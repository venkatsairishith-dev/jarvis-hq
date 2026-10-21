"""A plugin step's outcome comes from what the entry SAID, not from the entry returning Ok (review M12), and a Stop that
lands while the last step finishes names that step (review M13). No model, no network: the plugin class wired to fakes."""
from __future__ import annotations

import asyncio
import json
import logging
import types

import pytest

from plugin.plugins import main_agent as ma
from plugin.plugins.main_agent.planner import effect_of
from plugin.plugins.main_agent.store import RunStore

PC = {"summary": "On the PC", "steps": [{"id": "s1", "agent": "plugin", "plugin": "desktop:run_pc_task", "goal": "open notepad",
                                         "args": {"request": "open notepad"}},
                                        {"id": "s2", "agent": "think", "goal": "report", "depends_on": ["s1"]}]}
PC_THEN_OTHER = {"summary": "Two PC things", "steps": [
    {"id": "s1", "agent": "plugin", "plugin": "desktop:run_pc_task", "goal": "open notepad", "args": {"request": "open notepad"}},
    {"id": "s2", "agent": "plugin", "plugin": "desktop:run_pc_task", "goal": "open calc", "args": {"request": "open calc"},
     "depends_on": ["s1"]}]}
CAPS_ARGS = {"type": "object", "properties": {"request": {"type": "string"}}, "required": ["request"]}


class Ok:
    def __init__(self, value):
        self.value = value


class Err:
    def __init__(self, error):
        self.error = error


def make(monkeypatch, tmp_path, plan, entry, json_entries=None):
    from plugin.plugins.main_agent.planner import Capability
    p = object.__new__(ma.MainAgentPlugin)
    p._runs, p._active, p._cancel = {}, None, None
    p._store = RunStore(tmp_path / "runs")
    p.logger = logging.getLogger("test")
    p.pushed, p.dispatched, p.entry_calls = [], [], []
    p.push_message = lambda **kw: p.pushed.append(kw)
    p._agent_base_url = lambda: ""

    async def catalogue(mode="work"):
        return [Capability("think", "think", "t"), Capability("plugin", "desktop:run_pc_task", "pc", CAPS_ARGS)]

    async def llm(messages, **kw):
        if "planner" in messages[0]["content"]:
            return json.dumps(plan)
        p.dispatched.append(messages[1]["content"].split("\n", 1)[0])
        return "TEXT"

    async def call_entry(ref, params=None, timeout=10.0):
        p.entry_calls.append(ref)
        return await entry(ref, params)

    async def call_entry_json(ref, params=None, timeout=10.0):
        fn = (json_entries or {}).get(ref)
        return Ok({"data": {}}) if fn is None else fn(params)
    p._catalogue, p._llm = catalogue, llm
    monkeypatch.setattr(ma.MainAgentPlugin, "plugins", types.SimpleNamespace(call_entry=call_entry, call_entry_json=call_entry_json),
                        raising=False)
    monkeypatch.setattr(ma, "Err", Err)
    return p


async def settle(p, limit=500):
    for _ in range(limit):
        if p._active is None:
            return
        await asyncio.sleep(0.01)
    raise AssertionError("the run never finished")


def run_with(monkeypatch, tmp_path, payload, plan=PC):
    async def go():
        async def entry(ref, params):
            return Ok(payload)
        p = make(monkeypatch, tmp_path, plan, entry)
        rid = (await p.run_task(task="open notepad on the PC")).value["run_id"]
        await asyncio.wait_for(settle(p), 5)
        return p, p._store.load(rid)
    return asyncio.run(go())


# ------------------------------------------------------------------ M12
@pytest.mark.parametrize("payload,reason", [
    ({"status": "failed", "summary": "I could not finish: step 1 (open_app): no window"}, "no window"),
    ({"status": "busy", "error": "another PC task is running (single-run_pc_action-ab12); stop it first or wait"}, "another PC task"),
    ({"status": "denied", "error": "the owner did not approve it"}, "did not approve"),
    ({"state": "failed", "ok": False, "error": "the node refused"}, "the node refused"),
    ({"state": "done", "ok": False, "error": "the window never appeared"}, "never appeared"),
    ({"status": "not_understood", "error": "no plan"}, "no plan"),
])
def test_an_ok_payload_that_says_failed_or_busy_is_a_failed_step_with_the_reason(monkeypatch, tmp_path, payload, reason):
    p, rec = run_with(monkeypatch, tmp_path, payload)
    s1 = rec["results"]["s1"]
    assert s1["outcome"] == "failure" and reason in s1["error"], s1
    assert rec["state"] == "failed", "the run must not be 'done' / 'partial' because the entry returned Ok"
    assert rec["results"]["s2"]["outcome"] == "not_executed" and p.dispatched == [], "a step that needed it must not run"


def test_a_payload_that_failed_after_part_of_it_ran_is_unknown_and_held_not_a_plain_failure(monkeypatch, tmp_path):
    p, rec = run_with(monkeypatch, tmp_path, {"status": "failed", "summary": "step 2 failed",
                                              "steps": [{"n": 1, "passed": True}, {"n": 2, "passed": False}]}, PC_THEN_OTHER)
    s1 = rec["results"]["s1"]
    assert s1["outcome"] == "unknown" and s1["held"] is True and "after part of it had run" in s1["error"]
    assert rec["results"]["s2"]["outcome"] == "not_executed" and rec["state"] == "held" and p.entry_calls == ["desktop:run_pc_task"]


def test_a_payload_waiting_for_the_owner_is_held_and_said_as_waiting_not_as_done(monkeypatch, tmp_path):
    p, rec = run_with(monkeypatch, tmp_path, {"status": "waiting_for_permission", "approval_id": "a1",
                                              "summary": "This needs your approval first: ui click. Approve it in the JARVIS console"},
                      PC_THEN_OTHER)
    s1 = rec["results"]["s1"]
    assert s1["outcome"] == "unknown" and s1["held"] is True and s1["error"].startswith("needs your approval or answer")
    assert "approve" in s1["error"].lower()
    assert rec["results"]["s2"]["outcome"] == "not_executed" and "waiting for you" in rec["results"]["s2"]["error"]
    assert p.entry_calls == ["desktop:run_pc_task"], "the approval must not be asked twice (no retry, no later PC step)"
    text = p.pushed[-1]["parts"][0]["text"]
    assert "Waiting for you" in text and "open notepad" in text and "could not confirm" not in text


def test_a_payload_that_says_cancelled_is_not_a_success(monkeypatch, tmp_path):
    p, rec = run_with(monkeypatch, tmp_path, {"status": "cancelled", "summary": "Stopped. Before the stop: Chrome window appeared"})
    s1 = rec["results"]["s1"]
    assert s1["outcome"] == "unknown" and s1["held"] is True and "reported that it was cancelled" in s1["error"]
    assert rec["state"] == "held"


@pytest.mark.parametrize("payload", [{"status": "completed", "summary": "Done: opened"}, {"state": "done", "ok": True},
                                     {"status": "done"}, {"ok": True}, {"data": {"status": "completed"}}])
def test_a_payload_that_reports_success_or_says_nothing_stays_a_success(monkeypatch, tmp_path, payload):
    p, rec = run_with(monkeypatch, tmp_path, payload)
    assert rec["results"]["s1"]["outcome"] == "success" and rec["state"] == "done"


def test_a_failed_payload_inside_the_data_wrapper_is_read_too(monkeypatch, tmp_path):
    p, rec = run_with(monkeypatch, tmp_path, {"data": {"status": "busy", "error": "another PC task is running (x)"}})
    assert rec["results"]["s1"]["outcome"] == "failure" and "another PC task" in rec["results"]["s1"]["error"]


# ------------------------------------------------------------------ M13
def test_a_stop_that_lands_while_the_last_step_finishes_names_the_step_that_did_complete(monkeypatch, tmp_path):
    async def go():
        running, go_on = asyncio.Event(), asyncio.Event()

        async def entry(ref, params):
            running.set()
            await go_on.wait()
            return Ok({"status": "completed", "summary": "Done: notepad opened"})
        p = make(monkeypatch, tmp_path, {"summary": "one", "steps": PC["steps"][:1]}, entry)
        rid = (await p.run_task(task="open notepad on the PC")).value["run_id"]
        await asyncio.wait_for(running.wait(), 2)
        async def finish_after_the_stop():
            while not p._cancel.is_set():
                await asyncio.sleep(0)
            go_on.set()                                         # ... as the step finishes (inside the stop grace)
        finisher = asyncio.ensure_future(finish_after_the_stop())
        await p.stop_task()                                     # Stop lands ...
        await finisher
        await asyncio.wait_for(settle(p), 5)
        return p, p._store.load(rid)
    p, rec = asyncio.run(go())
    assert rec["state"] == "cancelled" and rec["results"]["s1"]["outcome"] == "success"
    text = p.pushed[-1]["parts"][0]["text"]
    assert text.startswith("Stopped as you asked. No further step was started.")
    assert "already finished" in text and "not undone" in text and "open notepad" in text, text


def test_a_stop_with_only_reading_steps_finished_adds_no_warning(monkeypatch, tmp_path):
    from plugin.plugins.main_agent.executor import StepResult
    from plugin.plugins.main_agent.planner import Plan, Step
    plan = Plan("p", [Step("s1", "think", "read it", "", {}, [], "none")])
    assert ma._stopped_text(plan, [StepResult("s1", "success", "x")]) == "Stopped as you asked. No further step was started."


# ------------------------------------------------------------------ Stop reaches the PC even without a main-agent run, and says what ended
def test_stop_without_a_run_still_stops_a_single_pc_action_and_says_what_ended(monkeypatch, tmp_path):
    async def entry(ref, params):
        return Ok({})
    p = make(monkeypatch, tmp_path, PC, entry, {"desktop:stop_pc_task": lambda params: Ok({"data": {"stopped_jobs": 1, "still_running": []}})})
    res = asyncio.run(p.stop_task()).value
    assert res["stopped"] is True and "Stopped 1 PC operation(s); each has ended." == res["message"]
    p = make(monkeypatch, tmp_path, PC, entry, {"desktop:stop_pc_task": lambda params: Ok(
        {"data": {"stopped_jobs": 0, "still_running": [{"id": "single-x", "kind": "single:run_pc_action"}]}})})
    res = asyncio.run(p.stop_task()).value
    assert res["requested"] is True and res["stopped"] is False and res["still_running"] and "still running" in res["message"]
    p = make(monkeypatch, tmp_path, PC, entry, {"desktop:stop_pc_task": lambda params: Ok({"data": {"stopped_jobs": 0, "still_running": []}})})
    assert asyncio.run(p.stop_task()).value == {"requested": False, "stopped": False, "still_running": [], "message": "Nothing is running."}


def test_a_desktop_step_stopped_but_not_ended_is_said_so_in_the_step_note(monkeypatch, tmp_path):
    async def go():
        running = asyncio.Event()

        async def entry(ref, params):
            running.set()
            await asyncio.sleep(30)
            return Ok({"status": "completed"})
        stop_reply = lambda params: Ok({"data": {"stopped_jobs": 0, "still_running": [{"id": "task-ab", "kind": "task"}]}})  # noqa: E731
        p = make(monkeypatch, tmp_path, PC, entry, {"desktop:stop_pc_task": stop_reply})
        rid = (await p.run_task(task="open notepad on the PC")).value["run_id"]
        await asyncio.wait_for(running.wait(), 2)
        await p.stop_task()
        await asyncio.wait_for(settle(p), 5)
        return p._store.load(rid)
    s1 = asyncio.run(go())["results"]["s1"]
    assert s1["outcome"] == "unknown" and "have not ended yet (task-ab)" in s1["error"], s1


# ------------------------------------------------------------------ research_topic writes files
def test_research_topic_is_classed_as_able_to_change_something_but_plain_searches_are_not():
    assert effect_of("plugin", "research:research_topic") == "non_idempotent"
    assert effect_of("plugin", "web_search:search") == "none"
    assert effect_of("plugin", "research:get_status") == "none"


# ------------------------------------------------------------------ re-review M11: a strict outcome contract (outcome.judge)
from plugin.plugins.main_agent import outcome as oc  # noqa: E402

TROUBLE = "desktop:run_troubleshooter"
J = [  # (ref, payload, effect, expected kind) - every payload the re-review listed, and what must stay a success
    (TROUBLE, {"outcome": "the fix was not applied", "error": "x"}, "non_idempotent", oc.FAILED),
    (TROUBLE, {"outcome": "the fix was not applied"}, "non_idempotent", oc.FAILED),
    (TROUBLE, {"outcome": "fix applied, but the check failed (undo with the backup)", "backup": "b"}, "non_idempotent", oc.UNKNOWN),
    (TROUBLE, {"outcome": "fix applied, but the check could not run (undo with the backup)"}, "non_idempotent", oc.UNKNOWN),
    (TROUBLE, {"outcome": "stopped by the owner; nothing further was done"}, "non_idempotent", oc.CANCELLED),
    (TROUBLE, {"outcome": "not approved: nothing was changed"}, "non_idempotent", oc.FAILED),
    (TROUBLE, {"outcome": "waiting for your approval", "approval_id": "a"}, "non_idempotent", oc.WAITING),
    (TROUBLE, {"outcome": "an outcome nobody has ever seen"}, "non_idempotent", oc.UNKNOWN),
    (TROUBLE, {"outcome": "fixed and verified", "cause": "c"}, "non_idempotent", oc.SUCCESS),
    (TROUBLE, {"outcome": "diagnosed", "cause": "c"}, "non_idempotent", oc.SUCCESS),
    (TROUBLE, {"outcome": "hypothesis only"}, "non_idempotent", oc.SUCCESS),
    ("deliverables:make_report", {"file": "a.pdf", "qa": {"passed": False}}, "non_idempotent", oc.FAILED),
    ("deliverables:make_report", {"file": "a.pdf", "qa_passed": False}, "non_idempotent", oc.FAILED),
    ("deliverables:make_report", {"file": "a.pdf", "qa": {"passed": True}, "folder": "f"}, "non_idempotent", oc.SUCCESS),
    ("research:research_topic", {"summary": "s", "qa_passed": False}, "none", oc.FAILED),
    ("desktop:recover_pc_lock", {"recovered": False, "reason": "held by another live process"}, "non_idempotent", oc.FAILED),
    ("desktop:recover_pc_lock", {"recovered": True, "how": "gone"}, "non_idempotent", oc.SUCCESS),
    ("desktop:recover_pc_lock", {"how": "gone"}, "non_idempotent", oc.UNKNOWN),
    ("x:y", {"error": "it broke"}, "none", oc.FAILED),
    ("x:y", {"refused": True}, "non_idempotent", oc.FAILED),
    ("x:y", {"cancelled": True}, "non_idempotent", oc.CANCELLED),
    ("x:y", {"timed_out": True}, "non_idempotent", oc.UNKNOWN),
    ("x:y", {"partial": True, "ok": True}, "non_idempotent", oc.UNKNOWN),
    ("x:y", None, "non_idempotent", oc.UNKNOWN),
    ("x:y", {}, "non_idempotent", oc.UNKNOWN),
    ("x:y", [], "non_idempotent", oc.UNKNOWN),
    ("x:y", "", "none", oc.UNKNOWN),
    ("x:y", 42, "non_idempotent", oc.UNKNOWN),
    ("learn_vault:save_note", "saved it", "non_idempotent", oc.UNKNOWN),          # a registered write must show what it wrote
    ("x:y", {"data": {"data": {"status": "failed"}}}, "none", oc.FAILED),
    ("x:y", {"ok": True, "data": {"error": "x"}}, "non_idempotent", oc.FAILED),
    ("x:y", {"success": True, "result": {"ok": False}}, "none", oc.FAILED),
    ("x:y", {"data": None}, "non_idempotent", oc.UNKNOWN),
    ("desktop:run_pc_task", {"status": "completed", "steps": [{"passed": True}, {"passed": False}]}, "non_idempotent", oc.UNKNOWN),
    ("desktop:run_pc_task", {"status": "completed", "steps": [{"passed": True}]}, "non_idempotent", oc.SUCCESS),
    ("desktop:run_pc_task", {"count": 3}, "non_idempotent", oc.UNKNOWN),
    ("desktop:run_pc_task", {"status": "weird_new_state"}, "non_idempotent", oc.UNKNOWN),
    ("desktop:run_pc_task", {"status": "running"}, "non_idempotent", oc.UNKNOWN),
    ("desktop:run_pc_action", {"state": "done", "ok": True}, "non_idempotent", oc.SUCCESS),
    ("desktop:run_pc_action", {"state": "done", "ok": False}, "non_idempotent", oc.FAILED),
    ("desktop:run_pc_action", {"state": "cancelled", "ok": False}, "non_idempotent", oc.CANCELLED),
    ("learn_vault:save_note", {"path": "a.md", "mode": "create", "bytes": 3}, "non_idempotent", oc.SUCCESS),
    ("learn_vault:record_progress", {"topic": "t", "status": "mastered"}, "non_idempotent", oc.SUCCESS),       # status is data here
    ("doc_tools:write_document", {"path": "a.docx", "name": "a.docx", "format": "docx", "bytes": 9}, "non_idempotent", oc.SUCCESS),
    ("doc_tools:list_outputs", {"files": []}, "none", oc.SUCCESS),
    ("web_search:search", {"results": []}, "none", oc.SUCCESS),
    ("jarvis_bridge:task_result", {"command_id": "c", "state": "running"}, "none", oc.SUCCESS),
    ("jarvis_bridge:task_result", {"command_id": "c", "state": "failed", "message": "JARVIS could not complete it"}, "none", oc.FAILED),
    ("jarvis_bridge:delegate_task", {"command_id": "c", "state": "queued"}, "non_idempotent", oc.SUCCESS),
    ("jarvis_bridge:cancel_task", {"command_id": "c", "cancelled": True}, "non_idempotent", oc.SUCCESS),
    ("jarvis_bridge:cancel_task", {"command_id": "c", "cancelled": False}, "non_idempotent", oc.FAILED),
    ("desktop:start_mission", {"mission": "m", "status": "started"}, "non_idempotent", oc.SUCCESS),
    ("desktop:mission_status", {"mission": "m", "status": "running", "steps": [{"passed": False}]}, "none", oc.SUCCESS),
    ("desktop:mission_status", {"status": "none"}, "none", oc.SUCCESS),
    ("desktop:stop_pc_task", {"stopped_jobs": 0, "stopped": [], "still_running": [], "message": "Nothing was running on the PC."},
     "non_idempotent", oc.SUCCESS),
    ("desktop:stop_pc_task", {"stopped_jobs": 0, "still_running": [{"id": "x"}]}, "non_idempotent", oc.UNKNOWN),
    ("desktop:stop_mission", {"mission": "m", "stopped": False}, "non_idempotent", oc.FAILED),
    ("someone_else:add_thing", {"id": 5}, "non_idempotent", oc.UNKNOWN),         # no evidence either way for an unknown writer
    ("someone_else:add_thing", {"ok": True, "id": 5}, "non_idempotent", oc.SUCCESS),
    ("someone_else:list_things", {"things": [1]}, "none", oc.SUCCESS),
]


@pytest.mark.parametrize("ref,payload,effect,expected", J, ids=[f"{i}-{j[0]}" for i, j in enumerate(J)])
def test_outcome_contract_table(ref, payload, effect, expected):
    got = oc.judge(ref, payload, effect)
    assert got.kind == expected, (ref, payload, got)


MARKERS = {  # failure markers (and the kind each must reach) that must be found at every nesting depth
    "status failed": ({"status": "failed"}, oc.FAILED), "state denied": ({"state": "denied"}, oc.FAILED),
    "error text": ({"error": "e"}, oc.FAILED), "refused": ({"refused": True}, oc.FAILED), "ok false": ({"ok": False}, oc.FAILED),
    "recovered false": ({"recovered": False}, oc.FAILED), "qa false": ({"qa": {"passed": False}}, oc.FAILED),
    "cancelled": ({"cancelled": True}, oc.CANCELLED), "waiting": ({"status": "waiting_for_permission"}, oc.WAITING),
    "timed out": ({"timed_out": True}, oc.UNKNOWN),
}
WRAPS = {"flat": lambda d: d, "data": lambda d: {"data": d}, "data.data": lambda d: {"ok": True, "data": {"data": d}},
         "result": lambda d: {"result": d, "success": True}}


@pytest.mark.parametrize("marker", sorted(MARKERS))
@pytest.mark.parametrize("wrap", sorted(WRAPS))
def test_fuzz_a_failure_marker_is_found_at_every_depth_and_never_reads_as_success(marker, wrap):
    """10 markers x 4 wrappers = 40 shapes (the success flags around them must not outvote the failure)."""
    payload, expected = MARKERS[marker]
    for effect in ("none", "non_idempotent"):
        got = oc.judge("x:y", WRAPS[wrap](dict(payload)), effect)
        assert got.kind == expected, (marker, wrap, effect, got)


def test_judge_never_raises_on_hostile_shapes():
    class Boom:
        def __getitem__(self, k):
            raise RuntimeError

    cyc: dict = {}
    cyc["data"] = cyc
    for hostile in (cyc, {"status": object()}, {"steps": [None, 1, "x", {"passed": object()}]}, {"data": [1, 2]}, Boom(), b"bytes",
                    {"outcome": 5}, {"error": float("nan")}, {i: i for i in range(50)}, {"qa": "x"}):
        got = oc.judge("x:y", hostile, "non_idempotent")
        assert got.kind in (oc.SUCCESS, oc.FAILED, oc.UNKNOWN, oc.CANCELLED, oc.WAITING)


CAPS_X = [("plugin", "desktop:run_troubleshooter", "fix"), ("plugin", "learn_vault:read_note", "read")]


def _plan_for(ref, args=None):
    return {"summary": "one", "steps": [{"id": "s1", "agent": "plugin", "plugin": ref, "goal": "do it", "args": args or {"request": "x"}},
                                        {"id": "s2", "agent": "think", "goal": "report", "depends_on": ["s1"]}]}


def _run_ref(monkeypatch, tmp_path, ref, payload):
    from plugin.plugins.main_agent.planner import Capability

    async def go():
        calls = []

        async def entry(r, params):
            calls.append(r)
            return Ok(payload)
        p = make(monkeypatch, tmp_path, _plan_for(ref), entry)

        async def catalogue(mode="work"):
            return [Capability("think", "think", "t"), Capability("plugin", ref, "p", CAPS_ARGS)]
        p._catalogue = catalogue
        rid = (await p.run_task(task="do the thing")).value["run_id"]
        await asyncio.wait_for(settle(p), 8)
        return p, p._store.load(rid), calls
    return asyncio.run(go())


@pytest.mark.parametrize("payload", [{"outcome": "the fix was not applied"}, {}, None,
                                     {"outcome": "fix applied, but the check failed (undo with the backup)"}, {"timed_out": True}])
def test_a_changing_step_that_did_not_confirm_is_held_once_and_the_final_text_says_what_is_unknown(monkeypatch, tmp_path, payload):
    p, rec, calls = _run_ref(monkeypatch, tmp_path, TROUBLE, payload)
    s1 = rec["results"]["s1"]
    assert s1["outcome"] in ("failure", "unknown") and s1["outcome"] != "success"
    assert calls == [TROUBLE], "a step that changes something is never run a second time behind the owner's back"
    assert rec["results"]["s2"]["outcome"] == "not_executed" and p.dispatched == []
    text = p.pushed[-1]["parts"][0]["text"]
    if s1["outcome"] == "unknown":
        assert s1["held"] is True and rec["state"] == "held" and "could not confirm" in text and "did not retry" in text
    else:
        assert rec["state"] == "failed" and "could not finish" in text.lower() or "not everything" in text.lower()


def test_a_read_step_with_no_confirmation_is_retried_once_then_reported_not_succeeded(monkeypatch, tmp_path):
    p, rec, calls = _run_ref(monkeypatch, tmp_path, "learn_vault:read_note", None)
    assert calls == ["learn_vault:read_note"] * 2                   # a read may be repeated: one retry
    assert rec["results"]["s1"]["outcome"] == "unknown" and rec["state"] != "done"


def test_successes_still_succeed_end_to_end(monkeypatch, tmp_path):
    for ref, payload in ((TROUBLE, {"outcome": "fixed and verified"}), ("learn_vault:read_note", {"path": "a.md", "text": "t"}),
                         ("desktop:run_pc_task", {"status": "completed", "steps": [{"passed": True}]})):
        p, rec, calls = _run_ref(monkeypatch, tmp_path / ref.replace(":", "_"), ref, payload)
        assert rec["results"]["s1"]["outcome"] == "success" and rec["state"] == "done", ref
