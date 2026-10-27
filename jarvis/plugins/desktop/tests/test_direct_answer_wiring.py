"""D75: a plain question is answered in the rules-first quick path, before any app, plan, model or kernel job; the Calculator opens only when the owner names it.
Fakes only: no model, no network, no Windows."""
import asyncio
import datetime
import json
import socket
import subprocess

import pytest

from jarvis.plugins.desktop import DesktopPlugin, core, goal as G, quick as Q, simple_tasks

from .test_goal import Fakes, _Host, gateway, plan_json  # noqa: F401  (the gateway fixture)

NOW = datetime.datetime(2026, 10, 27, 14, 5)
PROFILES = [{"profile": "Default", "name": "Venkat", "account": "ven***", "default": True, "say": ["default", "venkat"]}]


@pytest.fixture(autouse=True)
def _router_on(monkeypatch):
    monkeypatch.setenv("JARVIS_QUICK_PATH", "1")
    monkeypatch.delenv("JARVIS_DIRECT_ANSWERS", raising=False)
    Q.clear_normalisers()
    monkeypatch.setattr(core, "profiles", lambda: PROFILES)
    yield
    Q.clear_normalisers()


def m(text):
    return Q.match(text, PROFILES, NOW)


# --------------------------------------------------------------------------------------------------------------- the router answers
@pytest.mark.parametrize("text,reply", [
    ("23 times 47", "23 times 47 is 1,081."),
    ("what is 2+3", "2 plus 3 is 5."),
    ("what is 15% of 2400", "15% of 2,400 is 360."),
    ("Hey JARVIS, what's 1200 divided by 8?", "1,200 divided by 8 is 150."),
    ("square root of 144", "The square root of 144 is 12."),
    ("convert 100 celsius to fahrenheit", "100 °C is 212 °F (°F = °C x 9/5 + 32)."),
    ("how many cm in 2 inches", "2 inches is 5.08 cm (1 inch = 2.54 cm)."),
    ("what is the date after 45 days", "45 days from today (Tuesday, 27 October 2026) is Friday, 11 December 2026."),
    ("days until 25 December", "It is 59 days until Friday, 25 December 2026."),
    ("5 lakh plus 2 lakh", "5,00,000 plus 2,00,000 is 7,00,000 (7 lakh)."),
    ("23 guna 47", "23 times 47 is 1,081."),
    ("1200 ko 7 se bhaag karo", "1,200 divided by 7 is about 171.4286 (exactly 1200/7, rounded to 4 decimal places)."),
    ("add 18% gst to 2500", "GST at 18% on 2,500 is 450, so the total is 2,950."),
    ("what time is it", "It is 2:05 PM."),
    ("what is the date today", "Today is Tuesday, 27 October 2026."),
])
def test_a_plain_question_is_answered_with_no_plan_and_no_tool(text, reply):
    cmd = m(text)
    assert cmd is not None and cmd.kind == "answer" and cmd.plan is None and cmd.reply == reply, (text, cmd)
    out = asyncio.run(Q.run(cmd, run_pc=None))                  # an answer never needs the coordinator: run_pc is not even given
    assert out["status"] == "completed" and out["planned_by"] == "rules" and out["summary"] == reply and out["steps"] == []


def test_an_ordinary_pc_command_is_still_planned_by_rules():
    cmd = m("open notepad")
    assert cmd.kind == "pc" and cmd.plan.steps[0]["tool"] == "open_app"
    assert m("open calculator").kind == "pc" and m("open calculator").plan.steps[0]["app"] == "calculator"


@pytest.mark.parametrize("text", [
    "calculate 12 times 7 in calculator", "open calculator and do 2+3", "show me 23 times 47 on screen", "use the calculator to work out 15 plus 27",
    "calculate 5 plus 3 on the screen", "what is 5 plus 3 in calc", "open the calculator and add 4 and 5", "type 2+3 in notepad", "work out 15 plus 27 in the calculator app",
])
def test_naming_the_app_or_the_screen_is_not_answered_directly(text):
    assert Q.direct(text, NOW) is None
    cmd = m(text)
    assert cmd is None or cmd.kind != "answer", text


@pytest.mark.parametrize("text,entry,args", [
    ("calculate 12 times 7 in calculator", "calculate", {"text": "12 times 7"}),
    ("open calculator and work out 15 plus 27", "calculate", {"text": "15 plus 27"}),
    ("open calculator and do 2+3", "calculate", {"text": "2+3"}),
    ("show me 23 times 47 on screen", "calculate", {"text": "23 times 47"}),
    ("use the calculator to work out 15 plus 27", "calculate", {"text": "15 plus 27"}),
    ("what is 5 plus 3 in calc", "calculate", {"text": "5 plus 3"}),
])
def test_an_explicit_calculator_request_still_reaches_the_calculator_entry(text, entry, args):
    hit = simple_tasks.match(text)
    assert hit is not None and hit["id"] == entry and hit["args"] == args


def test_the_calculator_entry_keeps_its_class_and_its_check_but_a_bare_question_no_longer_matches_it():
    e = simple_tasks.get("calculate")
    assert e["class"] == "A" and e["how"] == {"handler": "drive", "app": "calculator"} and e["needs_laptop"] is True and "Calculator window" in e["verify"]
    assert simple_tasks.problems() == []
    for plain in ("what is 23 times 47", "calculate 12 times 7", "what is 2+3", "work out 15 plus 27"):
        assert simple_tasks.match(plain) is None, plain


def test_the_switches():
    assert Q.match("23 times 47", PROFILES, NOW, env={"JARVIS_QUICK_PATH": "0"}) is None
    assert Q.match("23 times 47", PROFILES, NOW, env={"JARVIS_DIRECT_ANSWERS": "0"}) is None
    assert Q.match("open notepad", PROFILES, NOW, env={"JARVIS_DIRECT_ANSWERS": "0"}) is not None, "only the direct answers go off"
    assert Q.match("23 times 47", PROFILES, NOW, env={}).kind == "answer"


def test_safe_mode_turns_the_direct_answers_off_with_the_other_shortcuts(monkeypatch):
    monkeypatch.setattr(Q.safe_mode, "active", lambda: True)
    assert m("23 times 47") is None


def test_the_tenglish_normaliser_hook_still_runs_before_the_answer():
    Q.register_normaliser(lambda t: t.replace("entha", "").strip())
    assert Q.match("23 times 47 entha", PROFILES, NOW).reply == "23 times 47 is 1,081."


# --------------------------------------------------------------------------------------------------------------- no network, no subprocess, no kernel job
def _boom(*a, **k):
    raise AssertionError("a direct answer must not use the network or start a process")


def test_an_answer_needs_no_network_and_no_subprocess(monkeypatch):
    monkeypatch.setattr(socket, "socket", _boom)
    monkeypatch.setattr(socket, "create_connection", _boom)
    monkeypatch.setattr(subprocess, "Popen", _boom)
    monkeypatch.setattr(G.loopback_http, "urlopen", _boom)
    for text in ("23 times 47", "15% of 2400", "convert 5 km to miles", "what time is it", "days until 25 december", "emi of 5 lakh at 9% for 5 years"):
        assert m(text).kind == "answer", text


def test_do_goal_answers_a_question_with_no_plan_no_job_and_no_model(gateway, monkeypatch):
    gw = gateway(plan_json(("pc", "open notepad", "open")))
    f = Fakes()
    res = asyncio.run(DesktopPlugin.do_goal(_Host(f), goal="what is 23 times 47"))
    assert gw.requests == [], "the online model was not asked"
    assert f.decided == [] and f.pc_runs == [], "no local model and no kernel job"
    assert res.value["summary"] == "23 times 47 is 1,081." and res.value["planned_by"] == "rules" and res.value["status"] == "completed" and res.value["steps"] == []


def test_do_goal_still_goes_the_normal_way_when_the_calculator_is_named(gateway, monkeypatch):
    gw = gateway(plan_json(("pc", "open the calculator", "open")))
    monkeypatch.setattr(G, "_profiles", lambda: [])
    f = Fakes()
    res = asyncio.run(DesktopPlugin.do_goal(_Host(f), goal="calculate 12 times 7 in calculator"))
    assert res.value.get("planned_by") != "rules", "not answered by the rules: the app request goes on"
    assert len(gw.requests) == 1, "the normal path planned it online, as before D75"


def test_quick_command_answers_before_the_ambiguity_gate_and_without_a_job():
    f = Fakes()
    for text, reply in (("what is 15% of 2400", "15% of 2,400 is 360."), ("23 guna 47", "23 times 47 is 1,081."), ("convert 5 km to miles", None)):
        res = asyncio.run(DesktopPlugin.quick_command(_Host(f), text=text))
        assert res.value["handled"] is True and res.value["planned_by"] == "rules" and res.value["status"] == "completed" and res.value["approval_id"] is None
        assert res.value["label"] == "One moment"
        if reply:
            assert res.value["reply"] == reply
    assert f.pc_runs == [] and f.decided == []


def test_quick_command_hands_the_calculator_request_on_unanswered():
    f = Fakes()
    res = asyncio.run(DesktopPlugin.quick_command(_Host(f), text="calculate 12 times 7 in calculator"))
    assert res.value == {"handled": False}, "the quick path does not answer it: the chat model's do_goal call opens the Calculator"
    assert f.pc_runs == []


def test_quick_command_hands_a_chat_question_on():
    f = Fakes()
    for text in ("what is the capital of France", "how are you", "what is on my screen"):
        res = asyncio.run(DesktopPlugin.quick_command(_Host(f), text=text))
        assert res.value == {"handled": False}, text


def test_the_result_has_the_shape_the_face_reads():
    f = Fakes()
    res = asyncio.run(DesktopPlugin.quick_command(_Host(f), text="what is 2+3"))
    assert set(res.value) >= {"handled", "status", "reply", "planned_by", "label", "approval_id", "seconds"}
    json.dumps(res.value)


# --------------------------------------------------------------------------------------------------------------- Telugu and Hindi in their own letters arrive as English
def test_a_telugu_or_hindi_question_is_answered_after_the_translation_layer(gateway, monkeypatch):
    from jarvis.plugins.desktop import translate

    def fake_entry_text(text, source="owner", **kw):
        english = {"23 గుణకారం 47 ఎంత": "what is 23 times 47", "२३ गुणा ४७ कितना": "what is 23 times 47"}[text]
        return english, {"source_lang": "te" if "ఎంత" in text else "hi", "english": english, "translated": True, "low_confidence": False, "confidence": 0.95}
    monkeypatch.setattr(translate, "entry_text", fake_entry_text)
    gw = gateway(plan_json(("pc", "open notepad", "open")))
    for goal in ("23 గుణకారం 47 ఎంత", "२३ गुणा ४७ कितना"):
        f = Fakes()
        res = asyncio.run(DesktopPlugin.do_goal(_Host(f), goal=goal))
        assert res.value["summary"] == "23 times 47 is 1,081." and res.value["planned_by"] == "rules", goal
        assert res.value["translation"]["translated"] is True and "message as: what is 23 times 47" in res.value["note"]
        assert f.pc_runs == [] and f.decided == []
    assert gw.requests == []
