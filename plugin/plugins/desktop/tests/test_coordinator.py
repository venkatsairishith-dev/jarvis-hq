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


def test_plan_completion_rules_for_measured_omissions():
    six = PROFILES + [{"profile": "Profile 2", "name": "Satish", "account": "sat***", "default": False, "say": ["satish"]}]
    # H19: the model planned only open_app
    p = D.validate(json.dumps({"steps": [{"tool": "open_app", "app": "wordpad"}]}), six, "write dear sir into wordpad")
    assert [s["tool"] for s in p.steps] == ["open_app", "type_text"] and p.steps[1]["text"] == "dear sir" and p.steps[1]["app"] == "wordpad"
    # A07: the model planned a page read for "go to youtube" on a named account; and the account is checked first
    p = D.validate(json.dumps({"steps": [{"tool": "browser", "action": "read", "account": "VenkatAMV"}]}), six,
                   "open my VenkatAMV account and go to youtube")
    assert [s["tool"] for s in p.steps] == ["browser", "open_website"] and p.steps[0]["action"] == "whoami"
    assert p.steps[1]["profile"] == "Profile 1" and "youtube.com" in p.steps[1]["url"]
    # every step carries what must be observed after it
    assert all(s.get("post") for s in p.steps) and "account matches" in p.steps[0]["post"]
    # "find the error on the screen and fix it": the window showing the error, found at run time
    p = D.validate(json.dumps({"steps": [{"tool": "troubleshoot", "app": ""}]}), six, "find the error on the screen and fix it")
    assert p.status == "ok" and p.steps[0]["app"] == D.ACTIVE
    assert D.resolve_site("google account").startswith("https://myaccount.google.com")
    assert D.resolve_site("notes.txt") is None and D.resolve_site("report.pdf") is None and D.resolve_site("example.org")
    # model outputs observed in the 5x run (plans-20261016-152205), kept as regressions
    p = D.validate('{"steps": [{"tool": "open_website", "site": "youtube", "app": "chrome", "text": "VenkatAMV"}, '
                   '{"tool": "browser", "action": "type", "target": "search bar", "text": "VenkatAMV"}]}', six,
                   "open my VenkatAMV account and go to youtube")
    assert [(s["tool"], s.get("action")) for s in p.steps] == [("browser", "whoami"), ("open_website", None)]
    assert p.steps[1]["profile"] == "Profile 1" and "youtube.com" in p.steps[1]["url"]
    p = D.validate('{"steps": [{"tool": "open_website", "site": "google", "app": "google account"}]}', six, "open my google account")
    assert p.steps[0]["url"].startswith("https://myaccount.google.com")
    p = D.validate('{"steps": [{"tool": "window", "action": "inspect", "app": "unknown"}, {"tool": "troubleshoot", "app": "unknown"}]}',
                   six, "find the error on the screen and fix it")
    assert [s["tool"] for s in p.steps] == ["troubleshoot"] and p.steps[0]["app"] == D.ACTIVE
    p = D.validate('{"steps": [{"tool": "open_app", "app": "Microsoft Word"}, {"tool": "type_text", "app": "Word", "text": "My Document"}]}',
                   six, "create a pdf and open it")
    assert p.status == "clarify" and "about" in p.question
    p = D.validate('{"steps": [{"tool": "research", "query": "solar panels"}]}', six, "make a pdf report about solar panels and open it")
    assert p.status == "ok" and p.steps[0]["deliverable"] == "report"
    # A12 regression (dev run 15:43): "put ... on youtube" is music, not typing into an app called youtube
    p = D.validate('{"steps": [{"tool": "play_media", "query": "Ilaiyaraaja music"}]}', six, "put on some Ilaiyaraaja music on youtube")
    assert [s["tool"] for s in p.steps] == ["play_media"]
    p = D.validate('{"steps": [{"tool": "open_app", "app": "notepad"}]}', six, "put meeting at five into notepad")
    assert [s["tool"] for s in p.steps] == ["open_app", "type_text"] and p.steps[1]["text"] == "meeting at five"
    # G07 live: "tell me when it is playing" is answered by the play check, not by a (disk) status check
    p = D.validate('{"steps": [{"tool": "play_media", "query": "Kalyani song"}, {"tool": "check_pc", "action": "status"}]}', six,
                   "play a Kalyani song and tell me when it is playing")
    assert [s["tool"] for s in p.steps] == ["play_media"]
    p = D.validate('{"steps": [{"tool": "check_pc", "action": "nowplaying"}]}', six, "what song is playing right now")
    assert p.steps[0]["check"] == "media"
    # no account named: no extra check
    p = D.validate(json.dumps({"steps": [{"tool": "open_website", "site": "youtube"}]}), six, "open youtube")
    assert [s["tool"] for s in p.steps] == ["open_website"]


def test_follow_ups_refer_to_what_the_last_task_produced(tmp_path):
    deck = r"D:\jarvis\outputs\neko\deliverables\x\deck.pptx"
    p = D.decide("open it", PROFILES, last={"file": deck}, post=lambda b: (_ for _ in ()).throw(AssertionError("no model")))
    assert p.status == "ok" and p.model == "rule" and p.steps[0] == {"tool": "open_output", "path": deck, "post": D.POST["open_output"]}
    p = D.decide("show me that again", PROFILES, last={"url": "https://mail.google.com/", "profile": "Profile 2"}, post=lambda b: 1 / 0)
    assert p.steps[0]["tool"] == "open_website" and p.steps[0]["profile"] == "Profile 2"
    assert D.decide("open it", PROFILES, last=None, post=lambda b: 1 / 0).status == "clarify"
    # research that should be opened: the research step opens it; a separate open step is dropped
    p = D.validate(json.dumps({"steps": [{"tool": "research", "query": "solar panels"}, {"tool": "open_file", "name": "slides"}]}),
                   PROFILES, "research solar panels, make slides and open them")
    assert [s["tool"] for s in p.steps] == ["research"] and p.steps[0]["open_after"] and p.steps[0]["deliverable"] == "presentation"
    p = D.validate(json.dumps({"steps": [{"tool": "research", "query": "solar panels"}, {"tool": "browser", "action": "read"}]}),
                   PROFILES, "research solar panels, make slides, open them and explain what you found")
    assert [s["tool"] for s in p.steps] == ["research"]
    # the coordinator remembers only checked results, and only recent ones
    tasks = tmp_path / "tasks"
    tasks.mkdir()
    (tasks / "20261016-150000-aaaaaa.json").write_text(json.dumps({"request": "research x and make slides", "started": 1000, "finished": 1010,
        "steps": [{"tool": "research", "passed": True, "args": {}, "result": {"file": deck}}]}), encoding="utf-8")
    assert C.previous(tasks, now=1100) == {"request": "research x and make slides", "status": None, "file": deck}
    assert C.previous(tasks, now=1010 + C.FOLLOW_UP_S + 1) is None
    # research verification: a made file that should have opened but no window appeared is not done
    st = {"tool": "research", "deliverable": "presentation", "open_after": True}
    assert not C.verify(st, {"ok": True, "summary": "s", "file": deck, "qa_passed": True, "opened_verified": False})[0]
    assert C.verify(st, {"ok": True, "summary": "s", "file": deck, "qa_passed": True, "opened_verified": True})[0]
    assert not C.verify({"tool": "research", "deliverable": "report"}, {"ok": True, "summary": "s", "file": deck, "qa_passed": False})[0]


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
    # model down -> fallback (a two-part request: the one-command fast path below needs no model at all)
    assert D.decide("open notepad and type hi", PROFILES, post=lambda b: (_ for _ in ()).throw(D.DecisionError("down"))).status == "fallback"


def test_fast_path_handles_one_simple_command_and_nothing_else():
    no_model = lambda b: (_ for _ in ()).throw(AssertionError("the model was asked"))
    for req, tool, val in (("open notepad", "open_app", "notepad"), ("launch visual studio code", "open_app", "visual studio code"),
                           ("open youtube", "open_website", "youtube.com"), ("go to github.com", "open_website", "github.com"),
                           ("play a Kalyani song", "play_media", "a kalyani song"), ("pause the music", "press_key", "playpause"),
                           ("next song", "press_key", "nexttrack"), ("minimize notepad", "window", "minimize"),
                           ("how much disk space do I have left", "check_pc", "disk"), ("is my internet working", "check_pc", "network")):
        p = D.decide(req, PROFILES, post=no_model)
        s = p.steps[0]
        assert p.model == "rule" and s["tool"] == tool and any(val in str(v) for k, v in s.items() if k != "post"), (req, p.steps)
        assert all(x.get("post") for x in p.steps)
    assert D.decide("pause the music", PROFILES, post=no_model).steps[0]["intent"] == "pause"
    for req in ("open my VenkatAMV account and go to youtube", "open notepad and type hi", "open it", "play something",
                "open the budget spreadsheet in my documents", "what is the capital of Italy", "open youtube in Satish's profile",
                "play despacito on spotify", "open winamp"):
        assert D.fast_path(req, PROFILES) is None, req
    # a follow-up with context goes to the model even when its words look simple
    with pytest.raises(AssertionError, match="model was asked"):
        D.decide("open notepad", PROFILES, post=no_model, context="type hi")
