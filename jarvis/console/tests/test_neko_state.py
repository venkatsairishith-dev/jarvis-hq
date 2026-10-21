"""Neko icon state: one test per state, priority between states, and the action guard. No servers needed."""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "..", "widget"))

import neko_state as ns  # noqa: E402
import widget_server as ws  # noqa: E402

OK_SERVERS = {n: True for n in ns.CORE}


def status(**kw):
    base = {"servers": dict(OK_SERVERS), "jarvis": {"reachable": True, "killed": False}, "watchdog": {"gave_up_on": []}, "task": None}
    base.update(kw)
    return base


def decide(st=None, approvals=None, gateway=None, speech=None, deliverable=None, wake=None):
    return ns.decide(st or status(), approvals or [], gateway, speech, deliverable, wake)


def test_sleeping_by_default_and_wake_listening():
    assert decide()["state"] == "sleeping"
    assert "wake word off" in decide()["detail"]
    assert decide(wake={"enabled": True, "healthy": True})["state"] == "wake_listening"
    assert "paused" in decide(wake={"enabled": True, "healthy": True, "state": "paused"})["detail"]
    assert "not working" in decide(wake={"enabled": True, "healthy": False, "state": "error", "detail": "WAKE_WORD_TIMEOUT"})["detail"]


def test_pc_repair_steps_show_on_the_icon():
    act = lambda s, age=5: {"state": s, "detail": "NekoSampleApp", "age_s": age}       # noqa: E731
    assert ns.decide(status(), [], None, None, None, None, act("diagnosing"))["detail"] == "Diagnosing: NekoSampleApp"
    assert ns.decide(status(), [], None, None, None, None, act("waiting_for_permission"))["state"] == "needs_permission"
    assert ns.decide(status(), [], None, None, None, None, act("verifying", age=500))["state"] == "sleeping"   # stale: ignored
    assert ns.decide(status(), [], None, None, None, None, act("idle"))["state"] == "sleeping"


def test_wake_word_heard_shows_listening_even_while_working():
    running = status(task={"state": "running", "steps": [], "summary": "x"})
    assert decide(running, wake={"enabled": True, "healthy": True, "state": "recording"}) == {"state": "listening", "detail": "Listening…"}
    assert decide(wake={"enabled": True, "healthy": True, "state": "transcribing"})["state"] == "listening"
    assert decide(running, wake={"enabled": True, "healthy": True, "state": "wake_listening"})["state"] == "working"


def test_error_beats_everything():
    st = status(servers={**OK_SERVERS, "memory": False})
    s = decide(st, approvals=[{"level": "RED", "action": {"op": "x"}}], gateway={"inflight": 2})
    assert s["state"] == "error" and "memory" in s["detail"]
    assert decide(status(jarvis={"reachable": True, "killed": True}))["state"] == "error"
    assert decide(status(watchdog={"gave_up_on": ["stack:speech"]}))["state"] == "error"


def test_needs_permission_shows_what_is_waiting():
    s = decide(approvals=[{"level": "YELLOW_APPROVAL", "action": {"summary": "click Play on youtube.com"}, "reason": "browser click"}])
    assert s["state"] == "needs_permission" and "click Play" in s["detail"] and "PIN" in s["recover"]


def test_speaking_working_thinking_listening_order():
    running = {"state": "running", "summary": "Research X", "steps": [{"outcome": "succeeded", "agent": "research"},
                                                                       {"outcome": "running", "agent": "deliverables"}], "id": "r1"}
    assert decide(speech={"speaking_estimate": True}, st=status(task=running))["state"] == "speaking"
    w = decide(st=status(task=running), gateway={"inflight": 1})
    assert w["state"] == "working" and "step 2 of 2 (deliverables)" in w["detail"]
    assert decide(gateway={"inflight": 1}, speech={"asr_age_s": 1.0})["state"] == "thinking"
    assert decide(speech={"asr_age_s": 2.0})["state"] == "listening"
    assert decide(speech={"asr_age_s": 30.0})["state"] == "sleeping"


def test_completed_offers_the_newest_file():
    d = {"age_s": 20, "name": "deck.pptx", "file": r"D:\out\x\deck.pptx", "folder": r"D:\out\x", "passed": True}
    s = decide(deliverable=d)
    assert s["state"] == "completed" and s["file"].endswith("deck.pptx")
    assert decide(deliverable={**d, "age_s": 999})["state"] == "sleeping"
    done = {"state": "done", "summary": "Plan a trip", "age_s": 10, "id": "r2"}
    assert decide(st=status(task=done))["detail"].startswith("Done: Plan a trip")


def test_feedback_is_validated_and_appended(tmp_path, monkeypatch):
    monkeypatch.setattr(ws, "DELIVERABLES_ROOT", str(tmp_path))
    rec, err = ws.validate_feedback({"rating": "up", "file": str(tmp_path / "j" / "a.pdf"), "note": "  great\n deck "})
    assert err is None and rec["note"] == "great deck"
    out = tmp_path / "fb.jsonl"
    ws.save_feedback(rec, str(out))
    ws.save_feedback({**rec, "rating": "down"}, str(out))
    assert len(out.read_text(encoding="utf-8").splitlines()) == 2
    assert ws.validate_feedback({"rating": "meh"})[1]
    assert ws.validate_feedback({"rating": "up", "file": r"C:\Windows\win.ini"})[1]


def test_action_guard(tmp_path, monkeypatch):
    monkeypatch.setattr(ws, "DELIVERABLES_ROOT", str(tmp_path))
    assert ws.validate_action({"action": "stop"}) == ({"action": "stop"}, None)
    ok, err = ws.validate_action({"action": "open", "path": str(tmp_path / "job" / "a.pdf")})
    assert err is None and ok["path"].endswith("a.pdf")
    _, err = ws.validate_action({"action": "open", "path": r"C:\Windows\system32\calc.exe"})
    assert err
    _, err = ws.validate_action({"action": "delete", "path": str(tmp_path / "a.pdf")})
    assert err


def test_a_task_that_ended_without_finishing_says_so_and_is_never_shown_as_done():
    """VOI-D4: a plan that failed after the widget stopped waiting, or a stopped task, is shown with its reason."""
    failed = {"state": "failed", "id": "r9", "summary": "", "error": "The planner did not return a plan.", "steps": [], "age_s": 3}
    out = decide(status(task=failed))
    assert out["state"] == "attention" and out["outcome"] == "failed" and "did not return a plan" in out["detail"]   # never "Done ✓"
    assert not out["detail"].startswith("Done")
    assert decide(status(task={**failed, "state": "cancelled", "error": ""}))["detail"].startswith("Stopped")
    assert decide(status(task={**failed, "age_s": 999}))["state"] == "sleeping"
    planning = {"state": "planning", "id": "r9", "summary": "Planning your task", "steps": [], "age_s": 1}
    assert decide(status(task=planning))["state"] == "working"


def test_a_deliverable_that_failed_qa_needs_attention_and_is_never_shown_as_done():
    """PRO-D5: qa.json with passed=False showed "Done ✓ Ready: deck.pptx"."""
    d = {"age_s": 5, "name": "deck.pptx", "file": "/x/deck.pptx", "folder": "/x", "passed": False, "failed_checks": ["fonts", "overflow"]}
    s = decide(deliverable=d)
    assert s["state"] == "attention" and s["outcome"] == "qa_failed" and s["passed"] is False
    assert s["detail"].startswith("QA failed (fonts, overflow): deck.pptx") and "Ready" not in s["detail"]
    assert s["file"] == "/x/deck.pptx"                                   # still offered: open it, or make it again
    unknown = decide(deliverable={**d, "passed": None})
    assert unknown["state"] == "attention" and unknown["outcome"] == "qa_unknown"
    assert decide(deliverable={**d, "passed": True})["state"] == "completed"
    assert "attention" in ns.STATES


def test_a_stopped_desktop_plugin_is_an_error_on_the_icon():
    """PRO-D6: the desktop plugin runs the 60 s PC monitor; when it stopped the icon still said "sleeping"."""
    plugins = {p: "running" for p in ws.PLUGINS}
    assert decide(status(plugins=plugins))["state"] == "sleeping"
    s = decide(status(plugins={**plugins, "desktop": "stopped"}))
    assert s["state"] == "error" and "desktop plugin" in s["detail"] and "neko-start" in s["recover"]
    assert decide(status(plugins={**plugins, "desktop": "unknown"}))["state"] == "error"            # not loaded at all
    assert decide(status(plugins={**plugins, "doc_tools": "stopped"}))["state"] == "sleeping"       # only the monitor host
    unread = {p: "unknown" for p in ws.PLUGINS}                                                     # the list could not be read
    assert decide(status(plugins=unread))["state"] == "sleeping"


def test_the_icon_page_draws_attention_without_a_check_mark():
    html = open(os.path.join(HERE, "..", "..", "widget", "neko.html"), encoding="utf-8").read()
    assert 'attention: "Needs your check"' in html and 'attention: "!"' in html
    assert 'body[data-state="attention"]' in html and '["completed", "attention"].includes(s.state)' in html


def test_a_server_that_is_not_installed_never_turns_the_icon_to_error():
    """VOI-D11: llama-swap (local_models, :8080) is optional. A watchdog status that gave up on it alone (an older status
    file) is not an error; giving up on anything else, or on it together with another server, still is."""
    assert decide(status(watchdog={"gave_up_on": ["stack:local_models"]}))["state"] == "sleeping"
    assert decide(status(watchdog={"gave_up_on": ["stack:local_models", "stack:speech"]}))["state"] == "error"
    assert decide(status(watchdog={"gave_up_on": ["stack:local_models,speech"]}))["state"] == "error"
    assert decide(status(watchdog={"gave_up_on": ["plugin:desktop"]}))["state"] == "error"
    assert "local_models" not in ns.CORE


# ---- the owner's voice-first phases (master pass 2026-10-20): additive, same honesty rules as the icon state
UP = {"servers": {n: True for n in ns.CORE}, "jarvis": {"reachable": True}, "plugins": {"desktop": "running"}}
WAKE_ON = {"enabled": True, "healthy": True, "state": "wake_listening"}


def _ph(status=None, approvals=(), gateway=None, speech=None, deliverable=None, wake=None, activity=None):
    st = dict(UP, **(status or {}))
    out = ns.decide(st, list(approvals), gateway, speech, deliverable, wake=wake, activity=activity)
    return {**out, **ns.voice_view(out, st, wake, activity)}


def test_phase_listening_only_with_a_real_microphone_stream():
    rec = _ph(wake={"enabled": True, "healthy": True, "state": "recording"})
    assert rec["state"] == "listening" and rec["phase"] == "listening" and rec["mic"]["stream"] and rec["mic"]["recording"]
    # the wake listener says recording but the microphone is failing: never "listening"
    broken = _ph(wake={"enabled": True, "healthy": False, "state": "recording", "detail": "microphone unplugged"})
    assert broken["phase"] != "listening" and not broken["mic"]["stream"] and broken["mic"]["problem"] == "microphone unplugged"
    assert _ph(wake={"enabled": True, "healthy": True, "state": "transcribing"})["phase"] == "transcribing"
    assert _ph(speech={"asr_age_s": 1.0})["phase"] == "transcribing"            # text heard, no live stream claimed
    muted = _ph(wake={"enabled": True, "healthy": True, "state": "paused"})
    assert muted["phase"] == "idle" and muted["mic"] == {"stream": False, "muted": True, "recording": False, "problem": ""}
    assert _ph(wake=WAKE_ON)["phase"] == "idle" and _ph(wake=WAKE_ON)["mic"]["stream"]
    assert _ph()["mic"]["stream"] is False                                        # no wake listener at all: no stream


def test_phase_planning_researching_executing_validating():
    assert _ph({"task": {"state": "planning"}})["phase"] == "planning"
    research = {"state": "running", "steps": [{"id": "s1", "agent": "research:research_topic", "outcome": "running"}]}
    assert _ph({"task": research})["phase"] == "researching"
    run = {"state": "running", "steps": [{"id": "s1", "agent": "doc_tools:write_document", "outcome": "running"}]}
    assert _ph({"task": run})["phase"] == "executing"
    assert _ph(activity={"state": "verifying", "age_s": 5})["phase"] == "validating"
    assert _ph(activity={"state": "researching", "age_s": 5})["phase"] == "researching"
    assert _ph(gateway={"inflight": 1})["phase"] == "planning"


def test_phase_approval_speaking_and_endings_are_never_completed_without_evidence():
    assert _ph(approvals=[{"level": "YELLOW", "action": {"summary": "send email"}}])["phase"] == "awaiting_approval"
    assert _ph(speech={"speaking_estimate": True})["phase"] == "speaking"
    assert _ph({"task": {"state": "done", "age_s": 10, "summary": "x"}})["phase"] == "completed"
    assert _ph({"task": {"state": "cancelled", "age_s": 10}})["phase"] == "cancelled"
    assert _ph({"task": {"state": "failed", "age_s": 10}})["phase"] == "failed"
    for unfinished in ("partial", "held", "interrupted"):
        got = _ph({"task": {"state": unfinished, "age_s": 10}})
        assert got["phase"] == "needs_review" and got["phase"] != "completed"
    bad_qa = _ph(deliverable={"age_s": 5, "name": "deck", "passed": False, "failed_checks": ["fonts"]})
    assert bad_qa["phase"] == "failed"
    assert _ph({"jarvis": {"reachable": True, "killed": True}})["phase"] == "failed"   # kill switch: nothing shows as working
    assert set(ns.PHASES) >= {"idle", "listening", "transcribing", "planning", "researching", "executing", "awaiting_approval",
                              "validating", "speaking", "cancelled", "failed"}


def test_phase_waiting_for_input_when_a_held_task_asks_a_question():
    held = {"state": "held", "age_s": 10, "summary": "configure", "waiting_for_input": True}
    got = _ph({"task": held})
    assert got["state"] == "attention" and got["phase"] == "waiting_for_input"     # icon state unchanged, phase precise
    assert _ph({"task": dict(held, waiting_for_input=False)})["phase"] == "needs_review"


def test_newest_run_says_only_that_a_question_waits(tmp_path):
    import json as _json
    import time as _time
    run = {"state": "held", "plan": {"summary": "s", "steps": [{"id": "s1", "agent": "plugin"}]}, "results": {},
           "pending_input": {"id": "q-1", "status": "open", "expires": _time.time() + 60, "prompt": "Paste the key",
                             "fields": [{"name": "api_key", "kind": "secret"}]}}
    (tmp_path / "r1.json").write_text(_json.dumps(run))
    got = ws.newest_run(str(tmp_path))
    assert got["waiting_for_input"] is True and "Paste the key" not in _json.dumps(got)
    run["pending_input"]["expires"] = _time.time() - 1
    (tmp_path / "r1.json").write_text(_json.dumps(run))
    assert "waiting_for_input" not in ws.newest_run(str(tmp_path))
