from __future__ import annotations

import asyncio
import time

from plugin.plugins.main_agent.executor import StepFailed, final_text, run_plan
from plugin.plugins.main_agent.planner import Plan, Step


def plan(*steps):
    return Plan("t", list(steps))


def S(id, deps=(), kind="think"):
    return Step(id, kind, f"goal {id}", depends_on=list(deps))


def run(coro):
    return asyncio.run(coro)


def test_independent_steps_run_in_parallel_and_dependents_get_their_inputs():
    seen = {}

    async def dispatch(step, inputs):
        seen[step.id] = dict(inputs)
        await asyncio.sleep(0.3)
        return f"out-{step.id}"

    t0 = time.monotonic()
    res = run(run_plan(plan(S("a"), S("b"), S("c", ["a", "b"])), dispatch, parallel=3))
    assert time.monotonic() - t0 < 0.8                                   # a and b together, then c (0.3 + 0.3, not 0.9)
    assert [r.id for r in res] == ["a", "b", "c"] and all(r.ok for r in res)
    assert seen["c"] == {"a": "out-a", "b": "out-b"}


def test_a_failure_skips_everything_that_needed_it_transitively_but_not_the_rest():
    async def dispatch(step, inputs):
        if step.id == "a":
            raise StepFailed("the page would not load")
        return "ok"

    res = {r.id: r for r in run(run_plan(plan(S("a"), S("b"), S("c", ["a"]), S("d", ["c"])), dispatch))}
    assert not res["a"].ok and res["a"].error == "the page would not load"
    assert res["b"].ok
    assert res["c"].skipped and res["d"].skipped                          # d never saw a result for c: it is skipped too


def test_a_slow_step_times_out_without_blocking_the_rest():
    async def dispatch(step, inputs):
        if step.id == "slow":
            await asyncio.sleep(5)
        return "ok"

    res = {r.id: r for r in run(run_plan(plan(S("slow"), S("fast")), dispatch, step_timeout=0.2))}
    assert res["slow"].error == "timed out" and res["fast"].ok


def test_unexpected_errors_report_only_the_class_not_the_message():
    async def dispatch(step, inputs):
        raise RuntimeError("https://api.example/v1?key=sk-SECRET failed")

    r = run(run_plan(plan(S("a")), dispatch))[0]
    assert r.error == "RuntimeError" and "SECRET" not in r.error


def test_cancel_before_a_step_starts_skips_it():
    async def go():
        cancel = asyncio.Event()
        cancel.set()

        async def dispatch(step, inputs):
            return "should not run"

        return await run_plan(plan(S("a")), dispatch, cancel=cancel)

    r = run(go())[0]
    assert r.skipped and r.error == "cancelled"


def test_the_overall_time_limit_stops_later_steps():
    async def dispatch(step, inputs):
        await asyncio.sleep(0.3)
        return "ok"

    res = run(run_plan(plan(S("a"), S("b", ["a"]), S("c", ["b"])), dispatch, step_timeout=5, total_timeout=0.4))
    assert res[0].ok and not all(r.ok for r in res)


def test_progress_callback_errors_do_not_stop_the_work():
    async def dispatch(step, inputs):
        return "ok"

    def boom(step, res):
        raise RuntimeError("ui exploded")

    assert all(r.ok for r in run(run_plan(plan(S("a"), S("b", ["a"])), dispatch, on_progress=boom)))


def test_results_are_capped():
    async def dispatch(step, inputs):
        return "x" * 100000

    assert len(run(run_plan(plan(S("a")), dispatch))[0].text) == 4000


def test_final_text_uses_leaf_steps_and_is_honest_about_failures():
    p = plan(S("a"), S("b"), S("c", ["a", "b"]))
    from plugin.plugins.main_agent.executor import StepResult as R
    ok = [R("a", True, "A"), R("b", True, "B"), R("c", True, "combined")]
    assert final_text(p, ok) == "combined"
    part = [R("a", True, "A"), R("b", False, error="timed out"), R("c", False, error="a step it needed did not finish", skipped=True)]
    assert "could not finish" in final_text(p, part) and "b (timed out)" in final_text(p, part)
    two = plan(S("a"), S("b"))
    mixed = [R("a", True, "A"), R("b", False, error="timed out")]
    assert final_text(two, mixed).startswith("A") and "Not everything worked" in final_text(two, mixed)
