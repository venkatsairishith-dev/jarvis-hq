"""D60: the rules-first router, the `quick_command` entry, the goal progress file for the face, and the plan call's small limits.
Fakes only: no model, no network, no Windows."""
import asyncio
import datetime
import json

import pytest

from jarvis.plugins._shared import screen_text
from jarvis.plugins.desktop import DesktopPlugin, core, decide, goal as G, quick as Q

from .test_goal import Fakes, _Host, gateway, plan_json  # noqa: F401  (the gateway fixture)

PROFILES = [{"profile": "Default", "name": "Venkat", "account": "ven***", "default": True, "say": ["default", "venkat"]},
            {"profile": "Profile 1", "name": "Venkat", "account": "ris***", "default": False, "say": ["venkat", "venkatamv"]}]
NOW = datetime.datetime(2026, 10, 26, 14, 5)


@pytest.fixture(autouse=True)
def _router_on(monkeypatch):
    monkeypatch.setenv("JARVIS_QUICK_PATH", "1")
    Q.clear_normalisers()
    yield
    Q.clear_normalisers()


def m(text, now=NOW):
    return Q.match(text, PROFILES, now)


def tools(cmd):
    return [s["tool"] for s in cmd.plan.steps]


# --------------------------------------------------------------------------------------------------------------- what matches
@pytest.mark.parametrize("text,expected", [
    ("open notepad", ["open_app"]),
    ("Hey JARVIS, open Calculator please", ["open_app"]),
    ("pause", ["press_key"]),
    ("volume up", ["press_key"]),
    ("minimize excel", ["window"]),
    ("open youtube", ["open_website"]),
    ("play despacito", ["play_media"]),
    ("open notepad and write hello", ["open_app", "type_text"]),
    ("type hello into notepad", ["type_text"]),
])
def test_one_plain_command_is_planned_by_rules(text, expected):
    cmd = m(text)
    assert cmd is not None and cmd.kind == "pc" and tools(cmd) == expected, text
    assert cmd.plan.model == "rule" and cmd.ack


def test_the_notepad_goal_types_the_items_not_the_label():
    cmd = m("Open Notepad and write a shopping list: milk, eggs, bread, rice.")
    assert tools(cmd) == ["open_app", "type_text"]
    assert cmd.plan.steps[1]["text"] == "milk, eggs, bread, rice" and cmd.plan.steps[1]["app"] == "notepad"
    keep = m('open notepad and type "Dear Sir, Hello"')
    assert keep.plan.steps[1]["text"] == "Dear Sir, Hello", "the owner's own capitals and words are typed as they are"


@pytest.mark.parametrize("text", [
    "open notepad and save it",                      # a pronoun: the model plans
    "read the total in excel",                       # not a plain command
    "open an app i never heard of",                  # unknown app
    "open notepad, then open calculator",            # two commands
    "don't open notepad",                            # negated
    "open notepad in my work profile",               # an account
    "play despacito as venkatamv",
    "how much space is left on the disk",            # needs the data, so not a rules reply
    "take a screenshot",                             # no desktop-plugin step for it yet: never guessed
    "make me a deck about electric buses",
    "stop",                                          # stop is the stop entry's job
    "",
])
def test_everything_else_goes_the_normal_way(text):
    assert m(text) is None, text


def test_time_and_date_are_answered_without_any_tool():
    assert m("what time is it").reply == "It is 2:05 PM."
    assert m("What's the time?").reply == "It is 2:05 PM."
    assert m("what is the date today").reply == "Today is Monday, 26 October 2026."
    assert m("what time is it", datetime.datetime(2026, 10, 26, 0, 7)).reply == "It is 12:07 AM."
    assert m("what time is it", datetime.datetime(2026, 10, 26, 12, 30)).reply == "It is 12:30 PM."
    assert m("what time is it").kind == "answer" and m("what time is it").plan is None


def test_the_router_switch_and_the_tenglish_hook():
    assert Q.match("open notepad", PROFILES, env={"JARVIS_QUICK_PATH": "0"}) is None
    assert Q.match("open notepad", PROFILES, env={}) is not None
    assert m("notepad kholo") is None                                   # no lexicon on this base
    Q.register_normaliser(lambda t: t.replace("kholo", "").strip() and "open " + t.replace("kholo", "").strip())
    assert tools(m("notepad kholo")) == ["open_app"]

    def broken(_):
        raise RuntimeError("lexicon is down")
    Q.register_normaliser(broken)
    assert tools(m("notepad kholo")) == ["open_app"], "a broken lexicon is ignored"


# --------------------------------------------------------------------------------------------------------------- the reply is honest
def rec(status="completed", passed=True, **kw):
    return {"id": "t1", "status": status, "steps": [{"n": 1, "tool": "open_app", "passed": passed}], **kw}


def test_the_reply_says_done_only_when_every_check_passed():
    cmd = m("open notepad")
    assert Q.result_for(cmd, rec())["summary"] == "Opened Notepad."
    assert Q.result_for(m("pause"), rec())["summary"] == "Paused."
    assert Q.result_for(m("resume"), rec())["summary"] == "Resumed."
    assert Q.result_for(m("volume up"), rec())["summary"] == "Volume up."
    assert Q.result_for(m("minimize excel"), rec())["summary"] == "Minimized Excel."
    assert Q.result_for(m("open youtube"), rec())["summary"] == "Opened youtube."
    unverified = Q.result_for(cmd, rec(passed=False))
    assert unverified["status"] == "completed" and "could not confirm" in unverified["summary"] and unverified["steps"][0]["verified"] is False
    waiting = Q.result_for(cmd, rec("waiting_for_permission", approval_id="ap1"))
    assert waiting["status"] == "waiting_for_permission" and waiting["approval_id"] == "ap1" and "PIN" in waiting["summary"]
    failed = Q.result_for(cmd, rec("failed", error="no window appeared"))
    assert failed["status"] == "failed" and failed["summary"].startswith("I could not open Notepad: no window appeared")
    assert Q.result_for(cmd, rec("needs_clarification", question="Which one?"))["summary"] == "Which one?"


def test_run_hands_the_plan_to_the_coordinator_and_reports_progress():
    cmd, seen, notes = m("open notepad"), [], []

    async def run_pc(request, plan):
        seen.append((request, plan))
        return rec()
    out = asyncio.run(Q.run(cmd, run_pc, notes.append))
    assert seen == [("open notepad", cmd.plan)] and out["planned_by"] == "rules" and out["status"] == "completed"
    assert [n["state"] for n in notes] == ["running", "done"] and notes[0]["executor"] == "rules"

    async def boom(request, plan):
        raise RuntimeError("the node is gone")
    out = asyncio.run(Q.run(cmd, boom))
    assert out["status"] == "failed" and "node is gone" not in json.dumps(out), "no exception text in the reply"


# --------------------------------------------------------------------------------------------------------------- the entries
def read_progress():
    return json.loads(core.GOAL_PROGRESS.read_text(encoding="utf-8"))


def test_do_goal_runs_a_plain_command_with_no_online_plan(gateway, monkeypatch):
    gw = gateway(plan_json(("pc", "open notepad", "open")))
    monkeypatch.setattr(core, "profiles", lambda: PROFILES)
    f = Fakes()
    res = asyncio.run(DesktopPlugin.do_goal(_Host(f), goal="open notepad"))
    assert gw.requests == [], "the online model was not asked"
    assert f.decided == [] and len(f.pc_runs) == 1 and f.pc_runs[0][1].steps[0]["tool"] == "open_app", "the rules' plan went to the coordinator"
    assert res.value["planned_by"] == "rules" and res.value["status"] == "completed" and "seconds" in res.value
    assert res.value["summary"] == "Opened Notepad."
    assert read_progress()["state"] == "idle", "the progress line is cleared when the goal ends"


def test_do_goal_still_plans_online_for_anything_else(gateway, monkeypatch):
    gw = gateway(plan_json(("screen_read", "read the total in Excel", "a total is returned")))
    monkeypatch.setattr(core, "profiles", lambda: PROFILES)
    monkeypatch.setattr(G, "_profiles", lambda: [])
    f = Fakes()
    monkeypatch.setattr(G, "_local_chat", lambda body, post, timeout=0: json.dumps({"app": "excel"}))
    res = asyncio.run(DesktopPlugin.do_goal(_Host(f), goal="read the total in Excel"))
    assert len(gw.requests) == 1 and res.value["planned_by"] == G.ONLINE_PLAN


def test_quick_command_answers_handled_false_at_once_for_anything_else(monkeypatch):
    monkeypatch.setattr(core, "profiles", lambda: PROFILES)
    f = Fakes()
    res = asyncio.run(DesktopPlugin.quick_command(_Host(f), text="make me a deck about buses"))
    assert res.value == {"handled": False} and f.pc_runs == []


def test_quick_command_runs_a_plain_command_and_returns_the_reply(monkeypatch):
    monkeypatch.setattr(core, "profiles", lambda: PROFILES)
    f = Fakes()
    res = asyncio.run(DesktopPlugin.quick_command(_Host(f), text="open notepad"))
    assert res.value["handled"] is True and res.value["reply"] == "Opened Notepad." and res.value["label"] == "Opening notepad"
    assert f.pc_runs[0][0] == "open notepad"
    ans = asyncio.run(DesktopPlugin.quick_command(_Host(f), text="what time is it"))
    assert ans.value["handled"] is True and ans.value["reply"].startswith("It is ") and len(f.pc_runs) == 1


def test_quick_command_with_the_router_off_handles_nothing(monkeypatch):
    monkeypatch.setenv("JARVIS_QUICK_PATH", "0")
    f = Fakes()
    res = asyncio.run(DesktopPlugin.quick_command(_Host(f), text="open notepad"))
    assert res.value == {"handled": False} and f.pc_runs == []


# --------------------------------------------------------------------------------------------------------------- progress of an online plan
def test_the_runner_reports_each_step_with_fixed_words_only(gateway):
    gateway(plan_json(("pc", "open the secret-plans.xlsx workbook", "open"), ("screen_read", "read the total", "total"), summary="two steps"))
    notes = []
    f = Fakes()
    out = asyncio.run(G.GoalRunner(f.hands(on_progress=notes.append)).run("open the workbook and read the total", True))
    assert out["status"] == "completed"
    assert [(n["state"], n["n"], n["total"], n["executor"]) for n in notes] == [
        ("planning", 0, 0, ""), ("running", 1, 2, "pc"), ("running", 2, 2, "screen_read"), ("done", 2, 2, "")]
    assert "secret-plans" not in json.dumps(notes), "no step text in a progress note"


def test_a_broken_progress_callback_never_fails_the_goal(gateway):
    gateway(plan_json(("pc", "open notepad", "open")))

    def broken(_):
        raise RuntimeError("disk full")
    out = asyncio.run(G.GoalRunner(Fakes().hands(on_progress=broken)).run("open notepad", True))
    assert out["status"] == "completed"


def test_the_progress_file_holds_fixed_words_and_numbers_only(tmp_path):
    path = tmp_path / "gp.json"
    core.set_goal_progress("running", 2, 3, "screen_read", path)
    assert json.loads(path.read_text())["state"] == "running" and json.loads(path.read_text())["executor"] == "screen_read"
    core.set_goal_progress("open C:\\secret\\notes.txt", 500, -4, "C:\\secret\\notes.txt", path)
    got = json.loads(path.read_text())
    assert (got["state"], got["n"], got["total"], got["executor"]) == ("idle", 99, 0, "")


# --------------------------------------------------------------------------------------------------------------- the plan call
class _Resp:
    def __init__(self, obj):
        self.body, self.headers = json.dumps(obj).encode(), {"X-Jarvis-Provider": "fakeonline"}

    def read(self):
        return self.body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _gateway_returns(monkeypatch, *replies):
    sent = []

    def urlopen(req, timeout=0):
        sent.append(json.loads(req.data))
        return _Resp(replies[min(len(sent), len(replies)) - 1])
    monkeypatch.setattr(G.loopback_http, "urlopen", urlopen)
    monkeypatch.setattr(screen_text, "gateway_state", lambda: (True, ""))
    return sent


def reply(content, finish="stop"):
    return {"choices": [{"message": {"role": "assistant", "content": content}, "finish_reason": finish}]}


def test_the_plan_call_is_small_and_deterministic(monkeypatch):
    sent = _gateway_returns(monkeypatch, reply('{"steps": []}'))
    text, provider = G.ask_online([{"role": "user", "content": "x"}])
    assert (text, provider) == ('{"steps": []}', "fakeonline")
    assert sent[0]["temperature"] == 0 and sent[0]["max_tokens"] == G.PLAN_MAX_TOKENS == 500 and len(sent) == 1


def test_a_plan_cut_off_or_empty_is_asked_once_more_with_room(monkeypatch):
    sent = _gateway_returns(monkeypatch, reply("", "length"), reply('{"steps": []}'))
    assert G.ask_online([{"role": "user", "content": "x"}])[0] == '{"steps": []}'
    assert [s["max_tokens"] for s in sent] == [500, G.PLAN_RETRY_TOKENS]
    sent = _gateway_returns(monkeypatch, reply('{"summary": "cut', "length"), reply('{"steps": []}'))
    assert G.ask_online([{"role": "user", "content": "x"}])[0] == '{"steps": []}' and len(sent) == 2
    sent = _gateway_returns(monkeypatch, reply(""), reply(""))
    with pytest.raises(G.GoalError):
        G.ask_online([{"role": "user", "content": "x"}])
    assert len(sent) == 2, "asked once more, not forever"
