"""The coordinator with fake agents (no servers): lock, retries, verification, approvals, budgets, cancel, routing."""
import json
import os

import pytest

from plugin.plugins.desktop import coordinator as C
from plugin.plugins.desktop import decide as D

PROFILES = [{"profile": "Default", "name": "Venkat", "account": "ven***", "default": True, "say": ["default", "venkat"]},
            {"profile": "Profile 1", "name": "Venkat", "account": "ris***", "default": False, "say": ["venkat", "venkatamv"]}]


def plan(*steps, status="ok", question=""):
    return D.Plan(list(steps), status, question=question, model="fake")


def coord(tmp_path, agents, **kw):
    return C.Coordinator(agents, PROFILES, tasks_dir=tmp_path / "tasks", lock_path=tmp_path / "lock", **kw)


OPEN = {"tool": "open_website", "profile": "Default", "url": "https://example.com/"}
OK_OPEN = {"ok": True, "profile": "Default", "verified": "window", "profile_name_matches": True}


def test_completed_only_when_verified(tmp_path):
    rec = coord(tmp_path, {"open_website": lambda s: OK_OPEN}).run("x", plan(OPEN))
    assert rec["status"] == "completed" and rec["steps"][0]["passed"]
    # the agent says ok, but the window was opened in another profile: that is not success
    rec = coord(tmp_path, {"open_website": lambda s: dict(OK_OPEN, profile="Profile 1")}).run("x", plan(OPEN))
    assert rec["status"] == "failed" and not rec["steps"][0]["passed"]
    assert "Done" not in C.summary(rec)


def test_play_needs_the_chosen_video_playing(tmp_path):
    step = {"tool": "play_media", "profile": "Default", "query": "kalyani"}
    other = {"ok": True, "playing": True, "same_video": False, "title": "Charukesi - YouTube"}
    assert coord(tmp_path, {"play_media": lambda s: other}).run("x", plan(step))["status"] == "failed"
    good = {"ok": True, "playing": True, "same_video": True, "title": "Kalyani - YouTube", "windows_media": ["Kalyani (Playing)"]}
    rec = coord(tmp_path, {"play_media": lambda s: good}).run("x", plan(step))
    assert rec["status"] == "completed" and rec["uncertain"] == []


def test_transient_failure_is_retried_once_only(tmp_path):
    calls = []

    def flaky(s):
        calls.append(1)
        return {"ok": False, "error": "browser connection failed: McpError timeout"}
    rec = coord(tmp_path, {"open_website": flaky}).run("x", plan(OPEN))
    assert len(calls) == 2 and rec["status"] == "failed" and rec["steps"][0]["attempts"] == 2


def test_refusals_and_denials_are_not_retried(tmp_path):
    calls = []

    def denied(s):
        calls.append(1)
        return {"ok": False, "state": "denied", "error": "refused by policy: approval denied"}
    rec = coord(tmp_path, {"open_website": denied}).run("x", plan(OPEN))
    assert len(calls) == 1 and rec["status"] == "denied"


def test_waiting_for_permission_ends_the_task_with_the_question(tmp_path):
    second = []
    agents = {"open_website": lambda s: {"ok": False, "state": "waiting_for_permission", "approval_id": "a1", "how": "PIN"},
              "open_app": lambda s: second.append(1) or {"ok": True, "verified": "verified"}}
    rec = coord(tmp_path, agents).run("x", plan(OPEN, {"tool": "open_app", "app": "notepad"}))
    assert rec["status"] == "waiting_for_permission" and rec["approval_id"] == "a1" and second == []


def test_lock_one_task_at_a_time_and_stale_lock_taken_over(tmp_path):
    lock = tmp_path / "lock"
    C.acquire("t1", lock)
    with pytest.raises(C.Busy):
        C.acquire("t2", lock)
    rec = coord(tmp_path, {"open_website": lambda s: OK_OPEN}).run("x", plan(OPEN))
    assert rec["status"] == "busy"
    C.release("t1", lock)
    lock.write_text(json.dumps({"task": "dead", "pid": 999999, "at": 0}))     # owner process gone
    C.acquire("t3", lock)
    assert C.current_task(lock) == "t3"
    C.release("t3", lock)
    assert not lock.exists()


def test_cancel_between_steps_and_time_budget(tmp_path):
    flag = {"stop": False}

    def first(s):
        flag["stop"] = True
        return OK_OPEN
    rec = coord(tmp_path, {"open_website": first, "open_app": lambda s: {"ok": True, "verified": "verified"}},
                cancelled=lambda: flag["stop"]).run("x", plan(OPEN, {"tool": "open_app", "app": "notepad"}))
    assert rec["status"] == "cancelled" and len(rec["steps"]) == 1
    t = [0.0]

    def slow(s):
        t[0] += 700
        return OK_OPEN
    rec = coord(tmp_path, {"open_website": slow}, clock=lambda: t[0]).run("x", plan(OPEN, OPEN))
    assert rec["status"] == "failed" and "budget" in rec["error"]


def test_agent_crash_is_a_failed_step_and_the_lock_is_released(tmp_path):
    def boom(s):
        raise RuntimeError("no open window for 'paint'")
    rec = coord(tmp_path, {"window": boom}).run("x", plan({"tool": "window", "app": "paint", "action": "focus"}))
    assert rec["status"] == "failed" and "no open window" in rec["error"]
    assert not (tmp_path / "lock").exists()


def test_routing_without_actions(tmp_path):
    called = []
    agents = {"open_website": lambda s: called.append(1) or OK_OPEN}
    assert coord(tmp_path, agents).run("x", plan(status="clarify", question="Which account?"))["status"] == "needs_clarification"
    assert coord(tmp_path, agents).run("x", plan({"tool": "answer"}))["route"] == "answer"
    assert coord(tmp_path, agents).run("x", plan(status="fallback"))["status"] == "not_understood"
    assert coord(tmp_path, {"stop": lambda s: {"ok": True}}).run("x", plan({"tool": "stop"}))["status"] == "stopped"
    assert called == []


def test_record_keeps_no_screen_text(tmp_path):
    secret = "Dear Venkat, your OTP is 123456"
    agents = {"window": lambda s: {"ok": True, "local_result": r"D:\x\window_text.json", "page": secret}}
    rec = coord(tmp_path, agents).run("x", plan({"tool": "window", "app": "notepad", "action": "inspect"}))
    text = (tmp_path / "tasks" / f"{rec['id']}.json").read_text(encoding="utf-8")
    assert secret not in text and "window_text.json" not in text


def test_round3_resolver_rules():
    six = PROFILES + [{"profile": "Profile 2", "name": "Satish", "account": "sat***", "default": False, "say": ["satish", "satishk"]}]
    assert D.account_from_text("open gmail in Satish's profile", six) == "satish"
    assert D.account_from_text("open my Venkat AMV account and go to youtube", six) == "venkatamv"
    assert D.account_from_text("open the profile called zzzz and play music", six) == "zzzz"
    assert D.account_from_text("open youtube", six) is None
    # the account the owner named wins over a model that left it out
    p = D.validate(json.dumps({"steps": [{"tool": "open_website", "site": "youtube"}]}), six, "open my VenkatAMV account and go to youtube")
    assert p.steps[0]["profile"] == "Profile 1"
    assert D.validate(json.dumps({"steps": [{"tool": "open_website", "site": "youtube"}]}), six, "open the profile called zzzz").status == "clarify"
    # vague words ask instead of acting
    assert D.validate(json.dumps({"steps": [{"tool": "play_media", "query": "it"}]}), six, "play it").status == "clarify"
    assert D.validate(json.dumps({"steps": [{"tool": "open_app", "app": "it"}]}), six, "open it").status == "clarify"
    # "open youtube and play X": one play step; a port in an address is kept
    p = D.validate(json.dumps({"steps": [{"tool": "open_website", "site": "youtube"}, {"tool": "play_media", "query": "kalyani"}]}), six, "x")
    assert [s["tool"] for s in p.steps] == ["play_media"]
    assert D.resolve_site("127.0.0.1:8799") == "http://127.0.0.1:8799" and D.resolve_site("google maps").startswith("https://maps")
    # a report or slides asked for in the request is kept even if the model left it out
    p = D.validate(json.dumps({"steps": [{"tool": "research", "query": "solar"}]}), six, "make a presentation about solar energy")
    assert p.steps[0]["deliverable"] == "presentation"


def test_media_toggle_keeps_the_owners_intent():
    p = D.validate(json.dumps({"steps": [{"tool": "press_key", "key": "pause"}]}), PROFILES, "pause the music")
    assert p.steps[0]["key"] == "playpause" and p.steps[0]["intent"] == "pause"
    p = D.validate(json.dumps({"steps": [{"tool": "press_key", "key": "playpause"}]}), PROFILES, "resume the song")
    assert p.steps[0]["intent"] == "play"


def test_media_key_the_owner_named_wins():
    """Suite run 1 (2026-10-16): the model answered playpause for 'skip to the next song' / 'previous track' and 'm' for mute."""
    for req, model_key, want in (("skip to the next song", "playpause", "nexttrack"), ("go back to the previous track", "playpause", "prevtrack"),
                                 ("mute the sound", "m", "mute"), ("turn the volume up", "volumeup", "volumeup")):
        p = D.validate(json.dumps({"steps": [{"tool": "press_key", "key": model_key}]}), PROFILES, req)
        assert p.steps[0]["key"] == want, (req, p.steps)
    p = D.validate(json.dumps({"steps": [{"tool": "press_key", "app": "notepad", "key": "ctrl+s"}]}), PROFILES, "save the file in notepad")
    assert p.steps[0]["key"] == "ctrl+s"                       # an app shortcut is left alone


def test_a_trailing_answer_step_is_the_report_not_a_step():
    """Live 2026-10-16 (G07): "play a Kalyani song and tell me when it is playing" planned play_media + answer; the
    answer step had no agent and the whole task failed although the song played."""
    p = D.validate(json.dumps({"steps": [{"tool": "play_media", "query": "Kalyani song"}, {"tool": "answer"}]}), PROFILES,
                   "play a Kalyani song and tell me when it is playing")
    assert [s["tool"] for s in p.steps] == ["play_media"]
    assert [s["tool"] for s in D.validate(json.dumps({"steps": [{"tool": "answer"}]}), PROFILES, "what is 2+2").steps] == ["answer"]


def test_decision_resolution_rules():
    assert D.resolve_account("", PROFILES)[0] == "Default"
    assert D.resolve_account("VenkatAMV", PROFILES)[0] == "Profile 1"
    folder, why = D.resolve_account("my venkat account", PROFILES)
    assert folder is None and "ambiguous" in why
    assert D.normalize_key("Volume Up") == "volumeup" and D.normalize_key("Ctrl + S") == "ctrl+s" and D.normalize_key("pause") == "playpause"
    p = D.validate(json.dumps({"steps": [{"tool": "open_app", "app": "youtube"}]}), PROFILES)
    assert p.steps[0]["tool"] == "open_website" and "youtube" in p.steps[0]["url"]
    p = D.validate(json.dumps({"steps": [{"tool": "open_website", "site": "youtube", "account": "venkat"}]}), PROFILES)
    assert p.status == "clarify"
    assert D.validate("not json", PROFILES).status == "fallback"
    assert D.validate(json.dumps({"steps": [{"tool": "rm_rf"}]}), PROFILES).status == "fallback"
    assert D.decide("never mind", PROFILES, post=lambda b: 1 / 0).steps == [{"tool": "stop"}]     # no model needed
    assert D.decide("open notepad", PROFILES, post=lambda b: (_ for _ in ()).throw(D.DecisionError("down"))).status == "fallback"
