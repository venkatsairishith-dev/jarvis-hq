"""The 4-step cap is enforced honestly (audit PC-D8, 2026-10-18). complete_plan used to return steps[:MAX_STEPS + 1] after
the code inserted its own steps, so a plan could have 5 steps although the cap, the guide and the schema say 4, and a
step the code added could push a full plan over without a word. Now: only the code's own whoami (verification) step may
exceed the cap, by exactly that one; any other overflow is a question to the owner, never a silent cut."""
import json

from jarvis.plugins.desktop import coordinator as C
from jarvis.plugins.desktop import decide as D

PROFILES = [{"profile": "Default", "name": "Venkat", "account": "v***@x", "default": True, "say": ["default", "venkat"]},
            {"profile": "Profile 1", "name": "VenkatAMV", "account": "r***@x", "default": False, "say": ["venkatamv"]}]
YT = {"tool": "open_website", "site": "youtube", "account": "VenkatAMV"}


def plan(req, *steps):
    return D.validate(json.dumps({"steps": list(steps)}), PROFILES, req)


def test_the_audit_repro_exceeds_the_cap_only_by_the_inserted_verification_step():
    """repro_steps.py of the audit: four model steps and a named account -> the code inserts a whoami check -> 5 steps."""
    p = plan("open youtube in my VenkatAMV account, open notepad, type hi into notepad and minimize calculator",
             YT, {"tool": "open_app", "app": "notepad"}, {"tool": "type_text", "app": "notepad", "text": "hi"},
             {"tool": "window", "action": "minimize", "app": "calculator"})
    assert p.status == "ok" and len(p.steps) == D.MAX_STEPS + 1
    assert [s["tool"] for s in p.steps] == ["browser", "open_website", "open_app", "type_text", "window"]
    assert p.steps[0]["action"] == "whoami"                                   # the one step over the cap is the check
    assert sum(1 for s in p.steps if not (s["tool"] == "browser" and s.get("action") == "whoami")) == D.MAX_STEPS


def test_a_typing_step_the_code_adds_to_a_full_plan_is_asked_about_not_added_over_the_cap():
    """Four model steps, none typing, but the owner said "type hi into notepad": the completed plan would be 5 real steps."""
    req = "open notepad, open calculator, minimize paint, play some music and type hi into notepad"
    p = plan(req, {"tool": "open_app", "app": "notepad"}, {"tool": "open_app", "app": "calculator"},
             {"tool": "window", "action": "minimize", "app": "paint"}, {"tool": "play_media", "query": "some music", "account": "default"})
    assert p.status == "clarify", [s["tool"] for s in p.steps]
    assert "5 steps" in p.question and f"at most {D.MAX_STEPS}" in p.question and "first part" in p.question
    assert "type text" in p.question                                           # names the step that did not fit
    assert any("more steps than the cap" in x for x in p.problems)


def test_a_typing_step_plus_the_check_that_fit_the_cap_without_the_check_is_allowed():
    req = "open youtube in my VenkatAMV account, open notepad and type hi into notepad"
    p = plan(req, YT, {"tool": "open_app", "app": "notepad"})                   # +type_text +whoami = 4 real steps + the check
    assert p.status == "ok"
    tools = [(s["tool"], s.get("action")) for s in p.steps]
    assert len(p.steps) == 4 and ("browser", "whoami") in tools and ("type_text", None) in tools


def test_the_typing_step_and_the_check_on_a_full_plan_are_asked_about():
    req = "open youtube in my VenkatAMV account, open notepad, open calculator, minimize paint and type hi into notepad"
    p = plan(req, YT, {"tool": "open_app", "app": "notepad"}, {"tool": "open_app", "app": "calculator"},
             {"tool": "window", "action": "minimize", "app": "paint"})
    assert p.status == "clarify" and "steps" in p.question
    assert p.steps and len(p.steps) > D.MAX_STEPS                                 # the full list stays in the record, nothing ran


def test_a_model_plan_longer_than_the_cap_is_still_disclosed_not_hidden():
    steps = [{"tool": "open_app", "app": a} for a in ("notepad", "calculator", "paint", "wordpad", "excel")]
    p = plan("open notepad, calculator, paint, wordpad and excel", *steps)
    assert p.status == "ok" and len(p.steps) == D.MAX_STEPS
    assert any("only the first" in x for x in p.problems)                         # the old disclosure still stands
    rec = {"status": "completed", "steps": [{"passed": True, "checked": "a window appeared"}], "uncertain": [],
           "plan": {"problems": p.problems}}
    assert "Not done" in C.summary(rec)                                           # and the owner hears it


def test_over_cap_unit_rules():
    who = {"tool": "browser", "action": "whoami"}
    four = [{"tool": "open_app", "app": str(i)} for i in range(4)]
    assert D.over_cap(four, four) == ""
    assert D.over_cap(four, [who] + four) == ""                                   # only the inserted check over the cap
    assert D.over_cap(four + [who], four + [who]) != ""                           # the model's own fifth step is not "inserted"
    assert D.over_cap(four, four + [{"tool": "type_text", "app": "x", "text": "y"}]) != ""
    assert D.over_cap(four[:3], four[:3] + [who, {"tool": "type_text", "app": "x", "text": "y"}]) == ""    # 4 real + check
    assert D.over_cap(four, [who] + four + [{"tool": "type_text", "app": "x", "text": "y"}]) != ""


def test_a_clarify_plan_runs_nothing(tmp_path):
    req = "open notepad, open calculator, minimize paint, play some music and type hi into notepad"
    p = plan(req, {"tool": "open_app", "app": "notepad"}, {"tool": "open_app", "app": "calculator"},
             {"tool": "window", "action": "minimize", "app": "paint"}, {"tool": "play_media", "query": "some music", "account": "default"})
    ran = []
    co = C.Coordinator({k: (lambda s: ran.append(s) or {"ok": True}) for k in ("open_app", "window", "play_media", "type_text")},
                       PROFILES, decide_fn=lambda r: p, tasks_dir=tmp_path / "t", lock_path=tmp_path / "l")
    rec = co.run(req)
    assert rec["status"] == "needs_clarification" and not ran and "first part" in C.summary(rec)
