"""D65: the reliability levers of D59 are WIRED into the real code path. The coordinator's step runner is closed_loop (act -> verify -> one retry by the next route -> ask),
its sentence is checked by final_answer.guard, and a control loop that needs the owner ends the task as a question."""
from __future__ import annotations

import inspect

import pytest

from jarvis.plugins._shared import closed_loop, final_answer
from jarvis.plugins.desktop import DesktopPlugin, coordinator as C, decide as D

PROFILES = [{"profile": "Default", "name": "Venkat", "account": "ven***", "default": True, "say": ["default", "venkat"]}]


def plan(*steps):
    return D.Plan(list(steps), "ok", model="fake")


def coord(tmp_path, agents, **kw):
    return C.Coordinator(agents, PROFILES, tasks_dir=tmp_path / "tasks", lock_path=tmp_path / "lock", **kw)


class Agents(dict):
    alternates: dict = {}


TYPE = {"tool": "type_text", "app": "notepad", "text": "hello"}
OK_TYPE = {"ok": True, "process": "notepad"}


def test_the_step_runner_really_goes_through_closed_loop(monkeypatch, tmp_path):
    seen = []
    real = closed_loop.run_step
    monkeypatch.setattr(closed_loop, "run_step", lambda step, *a, **k: seen.append(step) or real(step, *a, **k))
    rec = coord(tmp_path, {"type_text": lambda s: OK_TYPE}).run("x", plan(TYPE))
    assert rec["status"] == "completed" and len(seen) == 1 and seen[0].keep_order
    src = inspect.getsource(C.Coordinator)
    assert "_closed_loop" in src and "closed_loop.run_step" in inspect.getsource(C.Coordinator._closed_loop)


def test_a_step_whose_effect_is_absent_is_retried_by_the_next_route_once(tmp_path):
    calls, alt_calls = [], []
    agents = Agents(type_text=lambda s: calls.append(1) or {"ok": False, "error": "text not in field"})
    agents.alternates = {"type_text": lambda step, last: [("paste", lambda s: alt_calls.append(1) or OK_TYPE)]}
    rec = coord(tmp_path, agents).run("x", plan(TYPE))
    assert rec["status"] == "completed" and calls == [1] and alt_calls == [1]
    e = rec["steps"][0]
    assert e["attempts"] == 2 and e["route"] == "paste" and e["retried_by"] == ["uia", "paste"] and e["passed"]


def test_without_an_alternative_a_failed_typing_step_is_not_repeated(tmp_path):
    calls = []
    rec = coord(tmp_path, {"type_text": lambda s: calls.append(1) or {"ok": False, "error": "text not in field"}}).run("x", plan(TYPE))
    assert calls == [1] and rec["status"] == "failed" and rec["steps"][0]["attempts"] == 1


def test_a_held_denied_or_cancelled_step_is_never_retried_by_any_route(tmp_path):
    for res in ({"ok": False, "state": "waiting_for_permission", "approval_id": "a1"}, {"ok": False, "state": "denied", "error": "no"}, {"ok": False, "state": "cancelled", "error": "stopped"}):
        calls, alt = [], []
        agents = Agents(type_text=lambda s, res=res: calls.append(1) or dict(res))
        agents.alternates = {"type_text": lambda step, last: [("paste", lambda s: alt.append(1) or OK_TYPE)]}
        rec = coord(tmp_path, agents).run("x", plan(TYPE))
        assert calls == [1] and alt == [] and rec["status"] in ("waiting_for_permission", "denied", "cancelled"), res


def test_a_transient_failure_of_a_repeat_safe_step_is_still_retried_once(tmp_path):
    calls = []
    rec = coord(tmp_path, {"open_app": lambda s: calls.append(1) or {"ok": False, "error": "timed out"}}).run("x", plan({"tool": "open_app", "app": "notepad"}))
    assert len(calls) == 2 and rec["steps"][0]["attempts"] == 2 and rec["steps"][0]["route"] != "" and rec["status"] == "failed"


def test_a_step_the_loop_could_not_finish_without_the_owner_becomes_his_question(tmp_path):
    ask = {"kind": "jarvis_ask", "question": "q", "options": []}
    agents = {"drive": lambda s: {"ok": False, "status": "stopped_to_ask", "question": "Windows is asking for administrator permission.", "ask": ask}}
    rec = coord(tmp_path, agents).run("x", plan({"tool": "drive", "app": "setup", "query": "install"}))
    assert rec["status"] == "needs_clarification" and rec["ask"] == ask
    assert C.summary(rec) == "Windows is asking for administrator permission."


def test_the_final_sentence_is_checked_against_what_was_verified(tmp_path):
    rec = {"status": "failed", "error": "step 1 (type_text): nothing", "steps": [{"n": 1, "tool": "type_text", "passed": False, "checked": "the text was typed", "uncertainty": "text not in field"}]}
    # the legacy sentence is already honest; the guard keeps it
    assert C.summary(rec).startswith("I could not finish")
    # a sentence that would claim success over a failed step is replaced by the one built from the results
    class Lie:
        pass
    got = final_answer.guard("Done! Everything is saved.", *final_answer.effects_from_record(rec))
    assert got.source == final_answer.TEMPLATE and "overclaim" in got.reasons
    done = {"status": "completed", "steps": [{"n": 1, "tool": "open_app", "passed": True, "checked": "a window for notepad appeared", "uncertainty": ""}], "uncertain": []}
    assert C.summary(done) == "Done: a window for notepad appeared."
    soft = {"status": "completed", "steps": [{"n": 1, "tool": "press_key", "passed": True, "checked": "ctrl+s was sent", "uncertainty": "reported ok, not observed: nothing"}],
            "uncertain": ["step 1: reported ok, not observed: nothing"]}
    assert "not everything was observed" in C.summary(soft)


def test_the_summary_function_uses_the_guard_in_the_real_code_path():
    assert "final_answer.guard" in inspect.getsource(C.summary)
    assert "final_answer" in inspect.getsource(C)


def test_the_desktop_plugin_offers_the_coordinator_its_alternative_routes():
    from jarvis.plugins.desktop import AgentSet
    assert isinstance(AgentSet(), dict) and hasattr(AgentSet, "alternates")
    src = inspect.getsource(DesktopPlugin._agents)
    assert "other_way_type" in src and "wrapped.alternates" in src
