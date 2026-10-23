"""Self-correction (2026-10-22): after a DEFINITE failure the agent may plan once more, differently. These tests pin when it must NOT."""
from __future__ import annotations

import asyncio
import json

from test_run_control import Ok, make, settle  # noqa: E402 - the harness of the sibling test file

FAILS = {"status": "failed", "error": "I could not finish: step 1 (open_file): no single file matches."}
PC = {"summary": "Open it", "steps": [{"id": "s1", "agent": "plugin", "plugin": "desktop:run_pc_task", "goal": "open the report",
                                       "args": {"request": "open the report"}}]}
PC_THEN_PC = {"summary": "Two PC steps", "steps": [
    {"id": "s1", "agent": "plugin", "plugin": "desktop:run_pc_task", "goal": "open the folder", "args": {"request": "open the folder"}},
    {"id": "s2", "agent": "plugin", "plugin": "desktop:run_pc_task", "goal": "open the report", "args": {"request": "open the report"},
     "depends_on": ["s1"]}]}
THINK = {"summary": "Ask which", "steps": [{"id": "s1", "agent": "think", "goal": "say which report is meant"}]}
DONE = {"status": "completed", "task": "t", "steps": [{"n": 1, "passed": True}]}


def sequence(p, plans):
    """Planner answers the given plans in order (the last one repeats); returns the list of planning prompts it saw."""
    seen = []

    async def llm(messages, **kw):
        if "planner" in messages[0]["content"]:
            seen.append(messages)
            return json.dumps(plans[min(len(seen), len(plans)) - 1])
        p.dispatched.append(messages[1]["content"].split("\n", 1)[0])
        return "Which report do you mean: the sales one or the budget one?"
    p._llm = llm
    return seen


def entry(*answers):
    """call_entry answering each call in turn with Ok(answer) (an Exception instance becomes an Err); the last repeats."""
    calls = []

    async def call_entry(ref, params=None, timeout=10.0):
        calls.append(ref)
        a = answers[min(len(calls), len(answers)) - 1]
        return a if hasattr(a, "error") else Ok(a)
    call_entry.calls = calls
    return call_entry


def go(monkeypatch, tmp_path, plans, ce, task="open my report please"):
    async def run():
        p = make(monkeypatch, tmp_path, call_entry=ce)
        seen = sequence(p, plans)
        rid = (await p.run_task(task=task, mode="work")).value["run_id"]
        await settle(p)
        return p, seen, p._store.load(rid)
    return asyncio.run(run())


def test_a_definite_failure_gets_one_different_plan_and_is_rescued(monkeypatch, tmp_path):
    p, seen, rec = go(monkeypatch, tmp_path, [PC, THINK], entry(FAILS))
    assert len(seen) == 2 and rec["state"] == "done" and rec["replan"]["rescued"] is True
    ask = seen[1][-1]["content"]
    assert "already failed" in ask and "plugin:desktop:run_pc_task" in ask and "no_match" in ask and "DIFFERENT" in ask
    assert "report" not in ask.split("A first attempt")[1]                                   # what failed is told as shapes and classes only
    assert p.pushed[-1]["parts"][0]["text"].startswith("The first way did not work, so I tried another way.")
    rows = p._ep().all()
    assert [(r["attempt"], r["state"], r["shape"]) for r in rows] == [(1, "failed", ["plugin:desktop:run_pc_task"]), (2, "done", ["think"])]


def test_it_never_replans_after_something_that_changes_things_already_succeeded(monkeypatch, tmp_path):
    ce = entry(DONE, FAILS)                                          # s1 (changes things) succeeds, s2 fails definitely
    p, seen, rec = go(monkeypatch, tmp_path, [PC_THEN_PC, THINK], ce)
    assert len(seen) == 1 and "replan" not in rec and rec["state"] in ("partial", "failed")
    assert ce.calls == ["desktop:run_pc_task", "desktop:run_pc_task"]


def test_it_never_replans_when_a_step_may_have_happened(monkeypatch, tmp_path):
    from test_run_control import Err
    p, seen, rec = go(monkeypatch, tmp_path, [PC, THINK], entry(Err("timed out waiting for the plugin")))
    assert len(seen) == 1 and "replan" not in rec and rec["state"] == "held"


def test_it_never_replans_for_failures_a_different_plan_cannot_fix(monkeypatch, tmp_path):
    for bad in ("The agent model did not answer (RateLimitError).", "its API key is missing or wrong", "I cannot create folders or files yet"):
        p, seen, rec = go(monkeypatch, tmp_path / bad[:6].replace(" ", "_"), [PC, THINK], entry({"status": "failed", "error": bad}))
        assert len(seen) == 1 and "replan" not in rec, bad


def test_a_new_plan_with_the_same_shape_is_refused_and_nothing_runs_twice(monkeypatch, tmp_path):
    ce = entry(FAILS)
    p, seen, rec = go(monkeypatch, tmp_path, [PC, PC], ce)
    assert len(seen) == 2 and "replan" not in rec and rec["state"] == "failed" and ce.calls == ["desktop:run_pc_task"]


def test_it_replans_at_most_once(monkeypatch, tmp_path):
    ce = entry(FAILS)
    other = {"summary": "Another way", "steps": [{"id": "s1", "agent": "plugin", "plugin": "desktop:run_pc_task", "goal": "search for it",
                                                 "args": {"request": "search for it"}},
                                                {"id": "s2", "agent": "think", "goal": "report", "depends_on": ["s1"]}]}
    p, seen, rec = go(monkeypatch, tmp_path, [PC, other, THINK], ce)
    assert len(seen) == 2 and rec["replan"]["rescued"] is False and rec["state"] == "failed"      # no third plan
    assert p.pushed[-1]["parts"][0]["text"].startswith("I tried two different ways and neither worked.")


def test_the_owner_can_switch_it_off(monkeypatch, tmp_path):
    monkeypatch.setenv("MAIN_AGENT_REPLAN", "0")
    p, seen, rec = go(monkeypatch, tmp_path, [PC, THINK], entry(FAILS))
    assert len(seen) == 1 and "replan" not in rec


def test_stopping_the_task_stops_the_correction_too(monkeypatch, tmp_path):
    async def run():
        p = make(monkeypatch, tmp_path, call_entry=entry(FAILS))
        seen = sequence(p, [PC, THINK])
        orig = p._replan_once

        async def stopped_first(run_, plan, results, cancel):
            cancel.set()                                              # the owner pressed Stop while the first attempt ended
            return await orig(run_, plan, results, cancel)
        p._replan_once = stopped_first
        rid = (await p.run_task(task="open my report please", mode="work")).value["run_id"]
        await settle(p)
        return seen, p._store.load(rid)
    seen, rec = asyncio.run(run())
    assert len(seen) == 1 and "replan" not in rec


TWO_PARALLEL = {"summary": "Two independent PC steps", "steps": [
    {"id": "s1", "agent": "plugin", "plugin": "desktop:run_pc_task", "goal": "open the report", "args": {"request": "open the report"}},
    {"id": "s2", "agent": "plugin", "plugin": "desktop:run_pc_task", "goal": "open the budget", "args": {"request": "open the budget"}}]}


def test_one_definite_failure_next_to_a_step_that_may_have_happened_is_not_corrected(monkeypatch, tmp_path):
    """s1 failed for sure, but s2 timed out: s2 may have done something, so a new plan could do it twice. Nothing is re-planned."""
    from test_run_control import Err
    p, seen, rec = go(monkeypatch, tmp_path, [TWO_PARALLEL, THINK], entry(FAILS, Err("timed out waiting for the plugin")))
    assert len(seen) == 1 and "replan" not in rec and rec["state"] == "held"


def test_resuming_a_corrected_run_does_not_correct_it_a_second_time(monkeypatch, tmp_path):
    other = {"summary": "Another way", "steps": [{"id": "s1", "agent": "plugin", "plugin": "desktop:run_pc_task", "goal": "search for it",
                                                 "args": {"request": "search for it"}},
                                                {"id": "s2", "agent": "think", "goal": "report", "depends_on": ["s1"]}]}

    async def run():
        ce = entry(FAILS)
        p = make(monkeypatch, tmp_path, call_entry=ce)
        seen = sequence(p, [PC, other, THINK, THINK])
        rid = (await p.run_task(task="open my report please", mode="work")).value["run_id"]
        await settle(p)
        assert len(seen) == 2 and p._store.load(rid)["replan"]["rescued"] is False
        res = await p.resume_run(run_id=rid, rerun=["s1"])             # the owner asks for the failed step again
        assert res.value["run_id"] == rid
        await settle(p)
        return seen, p._store.load(rid)
    seen, rec = asyncio.run(run())
    assert len(seen) == 2                                              # no third planning call: one correction per run, ever
