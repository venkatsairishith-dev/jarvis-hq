"""D74 (2026-10-27): the desktop plugin asks instead of guessing. do_goal, quick_command and run_pc_task run the ambiguity rules BEFORE any plan or action.

Fakes only: no model, no network, no Windows. The owner's panel is a fake `owner_ask`; the PC and the online model must not be touched by a request that is asked about.
"""
import asyncio
import json

import pytest

from jarvis.plugins._shared import ambiguity, owner_ask
from jarvis.plugins.desktop import DesktopPlugin, core, decide, gate, goal as G, quick as Q

from .test_goal import Fakes, _Host, gateway, plan_json  # noqa: F401  (the gateway fixture)
from .test_quick import PROFILES


@pytest.fixture(autouse=True)
def _setup(monkeypatch):
    monkeypatch.setenv("JARVIS_QUICK_PATH", "1")
    monkeypatch.setattr(core, "profiles", lambda: PROFILES)
    monkeypatch.setattr(G, "_profiles", lambda: [])
    monkeypatch.delenv(gate.ENV_OFF, raising=False)
    monkeypatch.setattr(ambiguity, "recent_files", lambda *a, **k: [("old-notes.txt", 10), ("r.pdf", 20)])      # the newest files of the (pretend) known folders
    Q.clear_normalisers()
    yield
    owner_ask.uninstall()


class Panel:
    """A fake owner's panel: records the forms it was shown and answers them with `answers` in turn."""
    def __init__(self, monkeypatch, *answers):
        self.shown, self.answers = [], list(answers)
        monkeypatch.setattr(owner_ask, "available", lambda: True)

        def ask(spec, timeout_s=120.0):
            self.shown.append(spec)
            return self.answers.pop(0) if self.answers else {"answered": False, "reason": "timeout"}
        monkeypatch.setattr(owner_ask, "ask", ask)


def pick(choice=None, other=""):
    return {"answered": True, "button": "ok", "values": {"choice": choice, "other": other}}


class Host(_Host):
    def __init__(self, fakes, windows=None, **kw):
        super().__init__(fakes, **kw)
        self._wins = windows if windows is not None else []

    async def _goal_windows(self):
        return self._wins


# ------------------------------------------------------------------------------------------------------------------------ no panel: the question comes back
def test_do_goal_asks_instead_of_opening_notepad_on_a_guess(gateway):
    gw = gateway(plan_json(("pc", "open notepad", "open")))
    f = Fakes()
    res = asyncio.run(DesktopPlugin.do_goal(Host(f), goal="Open notepad or wordpad."))
    v = res.value
    assert v["status"] == "needs_clarification" and v["planned_by"] == gate.PLANNED_BY
    assert v["question"] == "Which one: Notepad or WordPad?" and ambiguity.ask_form.is_form(v["ask"], 2)
    assert [o["label"] for o in v["ask"]["options"]] == ["Notepad", "WordPad"] and v["ask"]["allow_other"] is True
    assert f.pc_runs == [] and f.decided == [] and gw.requests == [], "nothing was planned, nothing was done, the online model was not asked"
    assert "jarvis_ask" in v["summary"] and "Nothing was done" in v["summary"] and v["input_request"]["answer_arg"] == "answers"


def test_the_summary_tells_the_chat_model_what_to_do_with_the_form(gateway):
    f = Fakes()
    res = asyncio.run(DesktopPlugin.do_goal(Host(f), goal="Open the file I was working on yesterday."))
    assert res.value["status"] == "needs_clarification" and f.pc_runs == []
    assert "call desktop__do_goal again" in res.value["summary"]


def test_a_clear_request_is_not_touched_at_all(gateway):
    f = Fakes()
    res = asyncio.run(DesktopPlugin.do_goal(Host(f), goal="open notepad"))
    assert res.value["status"] == "completed" and res.value["planned_by"] == "rules" and len(f.pc_runs) == 1 and "ask" not in res.value


def test_the_gate_can_be_switched_off_for_one_run(gateway, monkeypatch):
    monkeypatch.setenv(gate.ENV_OFF, "0")
    f = Fakes()
    gateway(plan_json(("pc", "open notepad", "open")))
    res = asyncio.run(DesktopPlugin.do_goal(Host(f), goal="Open notepad or wordpad."))
    assert res.value.get("planned_by") != gate.PLANNED_BY


# ------------------------------------------------------------------------------------------------------------------------ with the panel: ask, then go on
def test_with_the_panel_the_question_is_asked_and_the_original_goal_goes_on_with_the_answer(gateway, monkeypatch):
    panel = Panel(monkeypatch, pick("o2"))
    f = Fakes()
    res = asyncio.run(DesktopPlugin.do_goal(Host(f), goal="Open notepad or wordpad."))
    assert len(panel.shown) == 1 and panel.shown[0]["intro"] == "Which one: Notepad or WordPad?"
    assert [f_["id"] for f_ in panel.shown[0]["fields"]] == ["choice", "other"] and panel.shown[0]["fields"][1]["label"] == "Something else"
    assert len(f.pc_runs) == 1 and f.pc_runs[0][0] == "Open WordPad." and f.pc_runs[0][1].steps[0]["app"] == "wordpad"
    assert res.value["status"] == "completed" and res.value["asked"] == 1


def test_a_typed_answer_works_like_a_click(gateway, monkeypatch):
    Panel(monkeypatch, pick(None, "Paint"))
    f = Fakes()
    asyncio.run(DesktopPlugin.do_goal(Host(f), goal="Open notepad or wordpad."))
    assert f.pc_runs[0][0] == "Open Paint."


def test_no_answer_means_nothing_is_done(gateway, monkeypatch):
    for answer in ({"answered": False, "reason": "timeout"}, {"answered": True, "button": "cancel", "values": {}}, pick(None, "")):
        Panel(monkeypatch, answer)
        f = Fakes()
        res = asyncio.run(DesktopPlugin.do_goal(Host(f), goal="Delete the old file."))
        assert res.value["status"] == "needs_clarification" and f.pc_runs == [] and "did nothing" in res.value["summary"], answer
        assert ambiguity.ask_form.is_form(res.value["ask"])


def test_the_chosen_file_is_the_owners_own_word_for_the_rest_of_the_run(gateway, monkeypatch):
    seen = {}
    Panel(monkeypatch, pick("C:\\Users\\me\\Downloads\\old-notes.txt"))

    async def fake_run(self, goal, enabled=True, utterance=""):
        seen.update(goal=goal, utterance=utterance, owner_goal=core.OWNER_GOAL.get())
        return {"status": "completed", "planned_by": "x", "summary": "ok", "steps": []}
    monkeypatch.setattr(G.GoalRunner, "run", fake_run)
    f = Fakes()
    asyncio.run(DesktopPlugin.do_goal(Host(f), goal="Delete the old file."))
    assert seen["goal"] == "Delete C:\\Users\\me\\Downloads\\old-notes.txt." and seen["utterance"] == seen["goal"] == seen["owner_goal"]


def test_a_second_ambiguity_in_the_answered_request_is_asked_too_but_never_forever(gateway, monkeypatch):
    panel = Panel(monkeypatch, pick("C:\\Users\\me\\Documents\\r.pdf"), pick(None, "Ravi"))
    f = Fakes()
    res = asyncio.run(DesktopPlugin.do_goal(Host(f), goal="Send it to him."))
    assert len(panel.shown) == 2 and panel.shown[1]["intro"] == "Who should I send it to?"
    assert f.pc_runs or res.value["status"] in ("needs_clarification", "completed", "failed")
    Panel(monkeypatch, *[pick(None, "it") for _ in range(10)])
    res = asyncio.run(DesktopPlugin.do_goal(Host(Fakes()), goal="open it"))
    assert res.value["status"] == "needs_clarification"                                        # the question budget ends the loop


# ------------------------------------------------------------------------------------------------------------------------ closing windows
WINS = [{"process": "jarvis", "title": "JARVIS", "foreground": True}, {"process": "notepad", "title": "screen-1.txt - Notepad"},
        {"process": "CalculatorApp", "title": "Calculator"}]


def test_close_the_window_asks_which_and_never_offers_jarvis_own_window(gateway, monkeypatch):
    panel = Panel(monkeypatch, pick("o1"))
    f = Fakes()
    asyncio.run(DesktopPlugin.do_goal(Host(f, windows=WINS), goal="Close the window."))
    opts = panel.shown[0]["fields"][0]["options"]
    assert [o["label"] for o in opts] == ["Notepad", "Calculator"] and all("jarvis" not in json.dumps(o).lower() for o in opts)
    assert f.pc_runs and f.pc_runs[0][0] == "Close Notepad"


def test_close_it_when_only_jarvis_window_is_in_front_asks_with_a_text_box(gateway):
    f = Fakes()
    only = [{"process": "jarvis", "title": "JARVIS", "foreground": True}]
    res = asyncio.run(DesktopPlugin.do_goal(Host(f, windows=only), goal="close this"))
    assert res.value["status"] == "needs_clarification" and res.value["question"] == "Which window should I close?" and f.pc_runs == []


def test_close_everything_runs_one_close_for_each_ticked_window(gateway, monkeypatch):
    Panel(monkeypatch, pick(["o1", "o2"]))
    f = Fakes()
    asyncio.run(DesktopPlugin.do_goal(Host(f, windows=WINS), goal="close everything"))
    assert [g for g, _p in f.pc_runs] == ["Close Notepad", "Close Calculator"]


def test_a_window_jarvis_opened_is_closed_without_a_question(gateway, monkeypatch):
    monkeypatch.setattr(DesktopPlugin, "_launched_procs", staticmethod(lambda: ["notepad"]))
    f = Fakes()
    asyncio.run(DesktopPlugin.do_goal(Host(f, windows=WINS), goal="close it"))
    assert f.pc_runs and f.pc_runs[0][0] == "Close Notepad"


# ------------------------------------------------------------------------------------------------------------------------ the quick door
def test_quick_command_with_the_panel_asks_before_any_model_and_runs_the_answer(monkeypatch):
    panel = Panel(monkeypatch, pick("o1"))
    f = Fakes()
    res = asyncio.run(DesktopPlugin.quick_command(Host(f), text="open notepad or wordpad"))
    assert res.value["handled"] is True and len(panel.shown) == 1 and f.pc_runs and f.pc_runs[0][0] == "open Notepad"


def test_quick_command_with_the_panel_and_no_answer_handles_it_with_the_question(monkeypatch):
    Panel(monkeypatch)
    f = Fakes()
    res = asyncio.run(DesktopPlugin.quick_command(Host(f), text="delete that"))
    assert res.value["handled"] is True and res.value["status"] == "needs_clarification" and "did nothing" in res.value["reply"] and f.pc_runs == []


def test_quick_command_without_a_panel_hands_an_ambiguous_message_to_the_chat_model(monkeypatch):
    f = Fakes()
    res = asyncio.run(DesktopPlugin.quick_command(Host(f), text="open notepad or wordpad"))
    assert res.value == {"handled": False} and f.pc_runs == []        # do_goal gets it next and returns the form


def test_quick_command_still_runs_a_plain_command_untouched(monkeypatch):
    Panel(monkeypatch)                                                # a panel is here, and is not used
    f = Fakes()
    res = asyncio.run(DesktopPlugin.quick_command(Host(f), text="open notepad"))
    assert res.value["handled"] is True and res.value["reply"] == "Opened Notepad." and f.pc_runs[0][0] == "open notepad"


# ------------------------------------------------------------------------------------------------------------------------ the decider's ask
def test_the_planners_clarification_ends_in_a_form_and_the_answer_runs_the_goal_again(gateway, monkeypatch):
    calls = []

    async def fake_run(self, goal, enabled=True, utterance=""):
        calls.append(goal)
        if len(calls) == 1:
            return {"status": "needs_clarification", "planned_by": "local_alone", "summary": "Which one: Alpha or Beta?", "steps": [
                {"n": 1, "status": "needs_clarification", "result": "Which one: Alpha or Beta?"}]}
        return {"status": "completed", "planned_by": "local_alone", "summary": "Done.", "steps": []}
    monkeypatch.setattr(G.GoalRunner, "run", fake_run)
    panel = Panel(monkeypatch, pick("o2"))
    res = asyncio.run(DesktopPlugin.do_goal(Host(Fakes()), goal="tidy the old file with that thing"))
    assert panel.shown and panel.shown[0]["intro"] == "Which one: Alpha or Beta?"
    assert calls[1].endswith("(The owner's clarification: Beta)") and res.value["status"] == "completed"


def test_the_planners_clarification_without_a_panel_is_a_form_in_the_result_not_an_empty_answer(gateway, monkeypatch):
    async def fake_run(self, goal, enabled=True, utterance=""):
        return {"status": "needs_clarification", "planned_by": "local_alone", "summary": "Which file?", "question": "Which file?", "steps": []}
    monkeypatch.setattr(G.GoalRunner, "run", fake_run)
    res = asyncio.run(DesktopPlugin.do_goal(Host(Fakes()), goal="tidy the old file with that thing"))
    assert res.value["status"] == "needs_clarification" and ambiguity.ask_form.is_form(res.value["ask"]) and res.value["ask"]["question"] == "Which file?"


def test_a_question_from_the_middle_of_a_task_is_never_asked_again_from_the_start(gateway, monkeypatch):
    calls = []

    async def fake_run(self, goal, enabled=True, utterance=""):
        calls.append(goal)
        return {"status": "needs_clarification", "planned_by": "online_plan_local_execution", "summary": "Step 2 needs you", "question": "Which dialog button?",
                "steps": [{"n": 1, "status": "completed", "result": "opened"}, {"n": 2, "status": "needs_clarification", "result": "Which dialog button?"}]}
    monkeypatch.setattr(G.GoalRunner, "run", fake_run)
    panel = Panel(monkeypatch, pick(None, "OK"))
    res = asyncio.run(DesktopPlugin.do_goal(Host(Fakes()), goal="tidy the old file with that thing"))
    assert len(calls) == 1 and not panel.shown and ambiguity.ask_form.is_form(res.value["ask"])


# ------------------------------------------------------------------------------------------------------------------------ run_pc_task
def test_run_pc_task_asks_too_and_does_not_plan(monkeypatch):
    ran = []

    async def fake_run_task(self, request, plan=None):
        ran.append(request)
        return {"id": "t", "status": "completed", "steps": [], "request": request}
    monkeypatch.setattr(DesktopPlugin, "_run_task", fake_run_task)

    class H:
        async def _missions_ready(self):
            return True
    res = asyncio.run(DesktopPlugin.run_pc_task(H(), request="open notepad or wordpad"))
    assert res.value["status"] == "needs_clarification" and ran == []
    asyncio.run(DesktopPlugin.run_pc_task(H(), request="open notepad or wordpad", answers={"clarification": "WordPad"}))
    assert len(ran) == 1 and "WordPad" in ran[0] and "wordpad or" not in ran[0].lower(), ran


def test_the_entries_expose_the_form_to_the_chat_model():
    from jarvis.pluginapi.decorators import ENTRY_META_ATTR
    assert "ask" in getattr(DesktopPlugin.do_goal, ENTRY_META_ATTR).llm_result_fields


# ------------------------------------------------------------------------------------------------------------------------ the pieces
def test_merge_takes_the_first_result_that_did_not_complete():
    a = {"status": "completed", "summary": "Closed Notepad.", "steps": [{"n": 1}]}
    b = {"status": "failed", "summary": "Calculator is still open.", "steps": []}
    m = gate.merge([a, b])
    assert m["status"] == "failed" and "Closed Notepad." in m["summary"] and "still open" in m["summary"]
    assert gate.merge([a]) is a and gate.merge([])["status"] == "failed"


def test_the_gate_never_raises_into_a_request(monkeypatch):
    async def boom(need):
        raise RuntimeError("the node is gone")
    out = asyncio.run(gate.gate_request("close the window", boom, None))
    assert out.result is not None or out.texts           # a failed lookup is 'not known': still a question, never an exception
    monkeypatch.setattr(ambiguity, "check", lambda *a, **k: (_ for _ in ()).throw(ValueError("x")))
    out = asyncio.run(gate.gate_request("open notepad", boom, None))
    assert out.texts == ["open notepad"] and out.result is None


def test_a_sentence_in_telugu_script_is_read_again_by_the_rules_once_it_is_english(monkeypatch):
    from jarvis.plugins._shared import translate
    monkeypatch.setattr(translate, "entry_text", lambda text, **kw: ("Open this.", {"translated": True, "source_lang": "te", "confidence": 0.9, "english": "Open this."}) if "తెర" in text else (text, None))
    f = Fakes()
    res = asyncio.run(DesktopPlugin.do_goal(Host(f), goal="ఇది తెరవండి"))
    assert res.value["status"] == "needs_clarification" and res.value["planned_by"] == gate.PLANNED_BY and f.pc_runs == [] and f.decided == []


def test_with_the_real_ask_broker_the_form_is_drawn_answered_and_continued(monkeypatch, tmp_path):
    """The whole round trip on the real AskBroker (what the face's tool server installs): the plugin shows the form, the panel's long poll sees it, the owner's click is
    validated and mapped (a radio id, a file path), and the original goal goes on with it. Only the owner's click is simulated."""
    import threading
    from jarvis.bridge.ask import AskBroker
    monkeypatch.setattr(ambiguity, "recent_files", lambda *a, **k: [("old-notes.txt", 10)])
    broker = AskBroker(home=tmp_path)
    for d in ("Downloads", "Documents", "Desktop"):
        (tmp_path / d).mkdir()
    (tmp_path / "Downloads" / "old-notes.txt").write_text("x")
    loop = asyncio.new_event_loop()
    t = threading.Thread(target=loop.run_forever, daemon=True)
    t.start()
    owner_ask.install(broker, loop)
    seen = []

    def panel_click(button, pick_value):
        before = broker.version                           # the panel's long poll: wait for something newer than what it has seen
        form = None
        for _ in range(20):
            w = broker.wanted(before, 1.0)
            form = w.get("form")
            if form:
                break
        seen.append(form)
        values = pick_value(form)
        assert broker.submit(form["id"], form["token"], button, values)["ok"]
    try:
        f = Fakes()
        th = threading.Thread(target=panel_click, args=("ok", lambda form: {"choice": form["fields"][0]["options"][1]["id"]}))
        th.start()
        res = asyncio.run(DesktopPlugin.do_goal(Host(f), goal="Open notepad or wordpad."))
        th.join(10)
        assert seen and seen[0]["intro"] == "Which one: Notepad or WordPad?" and "token" not in json.dumps(seen[0]["fields"])
        assert f.pc_runs and f.pc_runs[0][0] == "Open WordPad." and res.value["status"] == "completed"
        # a delete: the owner clicks a file in the panel's own list; the server turns the id into the path
        seen.clear()
        th = threading.Thread(target=panel_click, args=("ok", lambda form: {"choice": form["fields"][0]["options"][0]["id"]}))
        th.start()
        goals = []

        async def fake_run(self, goal, enabled=True, utterance=""):
            goals.append(goal)
            return {"status": "completed", "planned_by": "x", "summary": "ok", "steps": []}
        monkeypatch.setattr(G.GoalRunner, "run", fake_run)
        asyncio.run(DesktopPlugin.do_goal(Host(Fakes()), goal="Delete the old file."))
        th.join(10)
        assert seen[0]["fields"][0]["type"] == "file_choice" and [o["label"] for o in seen[0]["fields"][0]["options"]] == ["old-notes.txt"]
        assert "path" not in json.dumps(seen[0]) and goals == [f"Delete {tmp_path / 'Downloads' / 'old-notes.txt'}."]
        # Cancel: nothing is done
        seen.clear()
        th = threading.Thread(target=panel_click, args=("cancel", lambda form: {}))
        th.start()
        f2 = Fakes()
        res = asyncio.run(DesktopPlugin.do_goal(Host(f2), goal="Open notepad or wordpad."))
        th.join(10)
        assert f2.pc_runs == [] and res.value["status"] == "needs_clarification" and "did nothing" in res.value["summary"]
    finally:
        owner_ask.uninstall()
        loop.call_soon_threadsafe(loop.stop)


# ------------------------------------------------------------------------------------------------------------------------ the safety rule, at the hands
from .test_simple_tasks import done, pc, said  # noqa: E402,F401  (the fake-Windows fixture)


def test_close_never_reaches_jarvis_own_window_even_when_the_plan_names_it(pc):
    """Layer 3 (the plugin's catalogue handler); layer 2 is the kernel's policy, layer 1 the node's ownwin: each has its own test."""
    pc.add_app("jarvis", "JARVIS")
    pc.add_app("notepad", "Untitled - Notepad")
    rec = pc.ask("close jarvis")
    assert rec["status"] == "failed" and "own windows" in said(rec) and len(pc.windows) == 2
    assert not any(c.get("action") == "close" for c in pc.calls)


def test_close_the_window_as_a_catalogue_task_closes_nothing(pc):
    pc.add_app("jarvis", "JARVIS")
    pc.add_app("notepad", "Untitled - Notepad")
    rec = pc.ask("close the window")
    assert not done(rec) and len(pc.windows) == 2 and not any(c.get("action") == "close" for c in pc.calls)
    assert "which window" in said(rec).lower()


def test_close_notepad_by_name_still_works(pc):
    pc.add_app("jarvis", "JARVIS")
    pc.add_app("notepad", "Untitled - Notepad")
    rec = pc.ask("close notepad")
    assert done(rec) and [w["process"] for w in pc.windows] == ["jarvis"]
