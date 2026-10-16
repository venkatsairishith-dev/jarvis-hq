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
