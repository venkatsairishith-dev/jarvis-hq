from __future__ import annotations

import asyncio
import time

from plugin.plugins.main_agent.executor import (StepContext, StepFailed, StepResult, StepUnknown, final_text, run_plan, run_state)
from plugin.plugins.main_agent.planner import Plan, Step

FAST = (0.0, 0.01)          # no real pause between retries in tests


def plan(*steps):
    return Plan("t", list(steps))


def S(id, deps=(), kind="think", effect="none"):
    return Step(id, kind, f"goal {id}", depends_on=list(deps), effect=effect)


def run(coro):
    return asyncio.run(coro)


def by_id(results):
    return {r.id: r for r in results}


def test_independent_steps_run_in_parallel_and_dependents_get_their_inputs():
    seen = {}

    async def dispatch(step, inputs):
        seen[step.id] = dict(inputs)
        await asyncio.sleep(0.3)
        return f"out-{step.id}"

    t0 = time.monotonic()
    res = run(run_plan(plan(S("a"), S("b"), S("c", ["a", "b"])), dispatch, parallel=3))
    assert time.monotonic() - t0 < 0.8                                   # a and b together, then c (0.3 + 0.3, not 0.9)
    assert [r.id for r in res] == ["a", "b", "c"] and all(r.outcome == "success" for r in res)
    assert seen["c"] == {"a": "out-a", "b": "out-b"}


def test_a_definite_failure_skips_everything_that_needed_it_transitively_but_not_the_rest():
    async def dispatch(step, inputs):
        if step.id == "a":
            raise StepFailed("the page would not load")
        return "ok"

    res = by_id(run(run_plan(plan(S("a"), S("b"), S("c", ["a"]), S("d", ["c"])), dispatch, backoff=FAST)))
    assert res["a"].outcome == "failure" and res["a"].error == "the page would not load" and res["a"].attempts == 2   # read-only: retried once
    assert res["b"].outcome == "success"
    assert res["c"].outcome == "not_executed" and res["d"].outcome == "not_executed"


def test_a_flaky_read_only_step_is_retried_once_and_can_succeed():
    calls = []

    async def dispatch(step, inputs):
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("transient")
        return "second time"

    r = run(run_plan(plan(S("a")), dispatch, backoff=FAST))[0]
    assert r.outcome == "success" and r.attempts == 2 and r.text == "second time"


def test_a_step_with_side_effects_that_times_out_is_held_never_retried_and_stops_what_follows():
    calls = []

    async def dispatch(step, inputs):
        calls.append(step.id)
        if step.id == "send":
            await asyncio.sleep(5)
        return "ok"

    p = plan(S("send", kind="plugin", effect="non_idempotent"), S("after", ["send"]), S("other"))
    res = by_id(run(run_plan(p, dispatch, step_timeout=0.2, parallel=1, backoff=FAST)))
    assert res["send"].outcome == "unknown" and res["send"].held is True and res["send"].attempts == 1
    assert calls.count("send") == 1                                       # not retried
    assert res["after"].outcome == "not_executed" and "may or may not have happened" in res["after"].error
    assert res["other"].outcome == "not_executed" and res["other"].error.startswith("held back")   # nothing new starts after a hold
    assert run_state(p, list(res.values())) == "held"


def test_a_definite_failure_of_a_step_with_side_effects_is_not_held_and_not_retried():
    calls = []

    async def dispatch(step, inputs):
        calls.append(1)
        raise StepFailed("the entry refused the arguments")

    r = run(run_plan(plan(S("w", kind="plugin", effect="non_idempotent")), dispatch, backoff=FAST))[0]
    assert r.outcome == "failure" and r.held is False and len(calls) == 1


def test_an_explicit_unknown_from_the_dispatcher_is_unknown_and_held_when_it_has_side_effects():
    async def dispatch(step, inputs):
        raise StepUnknown("JARVIS is still working; check its console")

    r = by_id(run(run_plan(plan(S("j", kind="jarvis", effect="non_idempotent"), S("r")), dispatch, parallel=1, backoff=FAST)))
    assert r["j"].outcome == "unknown" and r["j"].held


def test_unexpected_errors_report_only_the_class_not_the_message():
    async def dispatch(step, inputs):
        raise RuntimeError("https://api.example/v1?key=sk-SECRET failed")

    r = run(run_plan(plan(S("a")), dispatch, backoff=FAST))[0]
    assert r.error == "RuntimeError" and "SECRET" not in r.error


def test_waiting_for_the_user_is_a_separate_clock_and_does_not_count_against_the_limit():
    async def waits(step, inputs, ctx):
        ctx.add_waiting(2.0)                 # JARVIS is waiting for the owner to approve something
        await asyncio.sleep(0.8)
        return "approved and done"

    r = run(run_plan(plan(S("a", kind="jarvis", effect="non_idempotent")), waits, step_timeout=0.3, total_timeout=1.0))[0]
    assert r.outcome == "success" and r.waited == 2.0

    async def silent(step, inputs):
        await asyncio.sleep(0.8)
        return "late"

    r = run(run_plan(plan(S("a", kind="jarvis", effect="non_idempotent")), silent, step_timeout=0.3, total_timeout=1.0))[0]
    assert r.outcome == "unknown"            # the same delay without credited waiting runs out of time


def test_cancel_before_a_step_starts_means_not_executed():
    async def go():
        cancel = asyncio.Event()
        cancel.set()

        async def dispatch(step, inputs):
            return "should not run"

        return await run_plan(plan(S("a")), dispatch, cancel=cancel)

    r = run(go())[0]
    assert r.outcome == "not_executed" and r.error == "cancelled"


def test_the_overall_time_limit_stops_later_steps():
    async def dispatch(step, inputs):
        await asyncio.sleep(0.3)
        return "ok"

    res = run(run_plan(plan(S("a"), S("b", ["a"]), S("c", ["b"])), dispatch, step_timeout=5, total_timeout=0.4))
    assert res[0].outcome == "success" and not all(r.outcome == "success" for r in res)


def test_progress_and_start_callback_errors_do_not_stop_the_work():
    async def dispatch(step, inputs):
        return "ok"

    def boom(*a):
        raise RuntimeError("ui exploded")

    assert all(r.ok for r in run(run_plan(plan(S("a"), S("b", ["a"])), dispatch, on_progress=boom, on_start=boom)))


def test_results_are_capped():
    async def dispatch(step, inputs):
        return "x" * 100000

    assert len(run(run_plan(plan(S("a")), dispatch))[0].text) == 4000


def test_resuming_keeps_finished_steps_and_runs_only_the_rest():
    ran = []

    async def dispatch(step, inputs):
        ran.append(step.id)
        return f"out-{step.id}"

    p = plan(S("a"), S("b", ["a"]), S("c", ["b"]))
    prior = {"a": StepResult("a", "success", "kept result")}
    res = by_id(run(run_plan(p, dispatch, prior=prior)))
    assert ran == ["b", "c"] and res["a"].text == "kept result" and res["c"].outcome == "success"


def test_a_held_step_in_the_prior_results_still_halts_new_work_until_it_is_chosen_to_run_again():
    ran = []

    async def dispatch(step, inputs):
        ran.append(step.id)
        return "ok"

    p = plan(S("send", kind="plugin", effect="non_idempotent"), S("after", ["send"]), S("other"))
    prior = {"send": StepResult("send", "unknown", error="interrupted", held=True)}
    res = by_id(run(run_plan(p, dispatch, prior=prior)))
    assert ran == [] and res["after"].outcome == "not_executed" and res["other"].outcome == "not_executed"


def test_final_text_uses_leaf_steps_and_is_honest_about_every_kind_of_problem():
    p = plan(S("a"), S("b"), S("c", ["a", "b"]))
    R = StepResult
    assert final_text(p, [R("a", text="A"), R("b", text="B"), R("c", text="combined")]) == "combined"
    part = [R("a", text="A"), R("b", "failure", error="timed out"), R("c", "not_executed", error="a step it needed did not finish")]
    txt = final_text(p, part)
    assert "could not finish" in txt and "b (failure: timed out)" in txt
    two = plan(S("a"), S("b"))
    assert final_text(two, [R("a", text="A"), R("b", "failure", error="x")]).startswith("A")
    held = plan(S("send", kind="plugin", effect="non_idempotent"))
    txt = final_text(held, [R("send", "unknown", error="timed out", held=True)])
    assert "Needs your attention" in txt and "did not retry" in txt


def test_run_state_names():
    p = plan(S("a"), S("b"))
    R = StepResult
    assert run_state(p, [R("a"), R("b")]) == "done"
    assert run_state(p, [R("a"), R("b", "failure")]) == "partial"
    assert run_state(p, [R("a", "failure"), R("b", "not_executed")]) == "failed"
    assert run_state(p, [R("a", "unknown", held=True)]) == "held"
    assert run_state(p, [R("a")], cancelled=True) == "cancelled"
