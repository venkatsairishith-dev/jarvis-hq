"""The Timeline (D61): what the chat model gets (labels, counts, flags: nothing else), what the panel gets, the owner's words for undo, the long poll, the
picture tokens, and the cleaner's journal line. A real journal in a temp folder; undo is a recording stand-in."""
import asyncio
import base64
import json
import threading
import time

import pytest

from jarvis.bridge import face_tools as F
from jarvis.bridge import timeline as T
from jarvis.node.jarvis_node import journal as J

JPEG = b"\xff\xd8\xff\xe0" + b"jpegbytes" * 20
PATH = r"C:\Users\me\Documents\Secret Plans\q3-layoffs.docx"
TYPED = "hunter2-MY-PASSWORD-ZZQ"


@pytest.fixture
def world(tmp_path):
    j = J.Journal(tmp_path / "journal", env={"JARVIS_JOURNAL_SCREENSHOTS": "1"})
    j.shots.save("job1-b.jpg", JPEG)
    j.shots.save("job1-a.jpg", JPEG + b"a")
    t = time.time()
    j.append(J.make_step(task_id="t1", goal="Open Calculator and tell me the weather", step_label="Open Calculator", tool="open_app", cls="B", target="Calculator",
                         result="a window for Calculator appeared", verified="verified", duration_ms=1800, ts=t - 30,
                         shots={"before": "job1-b.jpg", "after": "job1-a.jpg"}, undo={"kind": "close_window", "proc": "calculatorapp", "pid": 7, "opened_at": t - 31}))
    j.append(J.make_step(task_id="t1", goal="Open Calculator and tell me the weather", step_label=f"Type 23 characters into Notepad", tool="type_text", cls="B",
                         target="Notepad", result="the text was typed", verified="unconfirmed", ts=t - 20, undo={"kind": "cannot_undo", "why": "typed text cannot be taken back"}))
    j.append(J.make_step(task_id="t2", goal="Save my plans", step_label="Saved a .docx file", tool="save", cls="B", result="a new file appeared", verified="verified",
                         ts=t - 10, undo={"kind": "recycle_new", "path": PATH, "stamp": "abc", "size": 9}))
    j.append(J.make_step(task_id="t2", goal="Save my plans", step_label="Click in Chrome", tool="browser", cls="C", result="held for the owner", verified="failed", ts=t - 5))
    calls = []

    async def runner(params):
        calls.append(params)
        return {"ok": True, "undone": 1, "not_undone": 1, "results": [{"label": "Saved a .docx file", "status": "undone", "reason": "it is in the Recycle Bin", "id": "SECRETID"},
                                                                   {"label": "Open Calculator", "status": "refused", "reason": "the app did not close"}], "summary": "Undone: Saved a .docx file."}
    tl = T.Timeline(lambda: j, runner)
    return tl, j, calls


def run(coro):
    return asyncio.run(coro)


# --------------------------------------------------------------------------------------------------------------- the owner's words
def test_only_the_owners_own_undo_words_count():
    for yes in ("undo that", "Please undo the last 3", "take that back", "put it back", "revert it", "roll back what you did", "UNDO"):
        assert T.says_undo(yes), yes
    for no in ("", "what did you do", "don't undo that", "do not undo it", "never revert", "not yet, undo it later", "I might undo it tomorrow", "can't undo", "show me the timeline"):
        assert not T.says_undo(no), no


def test_the_number_the_owner_named_is_the_ceiling():
    assert T.count_named("undo the last 3") == 3 and T.count_named("undo the last three things") == 3 and T.count_named("undo two steps") == 2
    assert T.count_named("undo 50 steps") == T.MAX_UNDO and T.count_named("undo that") is None and T.count_named("undo the last one") is None
    assert T.whole_task("undo this task") and T.whole_task("undo the whole task") and T.whole_task("undo everything you just did") and not T.whole_task("undo that")


def test_undo_without_the_owners_words_is_refused_and_runs_nothing(world):
    tl, j, calls = world
    for say in ("", "what did you do?", "don't undo that"):
        out = run(tl.tool("undo", 5, say))
        assert out["ok"] is False and "only when the owner says undo" in out["message"]
    assert calls == []


def test_undo_runs_exactly_what_the_words_name(world):
    tl, j, calls = world
    run(tl.tool("undo", 0, "undo that"))
    run(tl.tool("undo", 9, "undo that"))                      # the model asks for more: the words name one
    run(tl.tool("undo", 9, "undo the last 3"))
    run(tl.tool("undo", 2, "undo the last 3"))                # the model asks for fewer: fewer
    run(tl.tool("undo", 0, "undo this task"))
    assert [c for c in calls[:4]] == [{"n": 1}, {"n": 1}, {"n": 3}, {"n": 2}]
    assert calls[4] == {"task": "t2"}, "the task of the newest undoable step"
    assert len(calls) == 5


def test_the_undo_answer_is_labels_and_fixed_words_not_ids(world):
    tl, j, calls = world
    out = run(tl.tool("undo", 0, "undo the last 2"))
    assert out["undone"] == 1 and out["not_undone"] == 1 and out["results"][0] == {"label": "Saved a .docx file", "status": "undone", "reason": "it is in the Recycle Bin"}
    assert "SECRETID" not in json.dumps(out)


def test_without_a_runner_or_journal_the_answers_are_plain(tmp_path):
    assert run(T.Timeline(lambda: None).tool("last"))["ok"] is False
    j = J.Journal(tmp_path)
    assert "not available" in run(T.Timeline(lambda: j, None).tool("undo", 1, "undo that"))["message"]
    assert run(T.Timeline(lambda: j).tool("wipe"))["ok"] is False


# ------------------------------------------------------------------------------------------------- what the chat model receives
def test_the_chat_model_gets_labels_counts_and_flags_and_nothing_else(world):
    tl, j, calls = world
    out = run(tl.tool("last", 10))
    blob = json.dumps(out)
    assert out["count"] == 4 and out["steps"][0]["label"] == "Click in Chrome" and out["steps"][0]["verified"] == "failed"
    assert out["steps"][2] == {"label": "Type 23 characters into Notepad", "class": "B", "verified": "unconfirmed", "tool": "type_text", "can_undo": False, "undone": False, "is_undo": False}
    assert out["steps"][1]["can_undo"] is True and out["steps"][3]["can_undo"] is True
    assert out["summary"]["steps"] == 4 and out["summary"]["failed"] == 1 and out["summary"]["unconfirmed"] == 1
    for forbidden in (PATH, "Secret Plans", "q3-layoffs", "jpeg", "job1", "shots", "\"undo\"", "recycle_new", "stamp", "calculatorapp", "Calculator appeared", "weather", "goal_label",
                      "hunter2", "token", "t1", "t2", "\"id\"", "target", "result"):
        assert forbidden not in blob, forbidden
    assert set(out["steps"][0]) == {"label", "class", "verified", "tool", "can_undo", "undone", "is_undo"}


def test_open_asks_the_panel_and_answers_in_one_sentence(world):
    tl, j, calls = world
    v0 = tl.version
    out = run(tl.tool("open"))
    assert out["ok"] and "Timeline panel" in out["message"] and "4 steps" in out["message"] and "1 failed" in out["message"]
    assert tl.open_seq == 1 and tl.version > v0
    assert PATH not in json.dumps(out)


def test_last_is_capped_and_an_undone_step_is_flagged(world):
    tl, j, calls = world
    s = j.undoable(1)[0]
    j.append(J.make_undo_entry(s, status="undone", detail="x"))
    out = run(tl.tool("last", 99))
    assert out["count"] <= T.CHAT_LAST_MAX
    flags = {x["label"]: x for x in out["steps"]}
    assert flags["Saved a .docx file"]["undone"] is True and flags["Saved a .docx file"]["can_undo"] is False and flags["Undo: Saved a .docx file"]["is_undo"] is True


# ----------------------------------------------------------------------------------------------------------------------- the panel
def test_the_panel_view_groups_tasks_and_offers_undo_text_pictures_and_reasons(world):
    tl, j, calls = world
    v = tl.view()
    assert v["available"] and [t["task_id"] for t in v["tasks"]] == ["t2", "t1"]
    t1 = v["tasks"][1]
    assert t1["goal_label"] == "Open Calculator and tell me the weather" and t1["failed"] == 0 and t1["undoable"] == 1 and len(t1["steps"]) == 2
    calc = next(s for s in t1["steps"] if s["step_label"] == "Open Calculator")
    assert calc["undoable"] and calc["undo_text"] == "Close the window JARVIS opened" and set(calc["shots"]) == {"before", "after"}
    typed = next(s for s in t1["steps"] if s["tool"] == "type_text")
    assert not typed["undoable"] and typed["cannot"].startswith("typed text") and typed["shots"] is None
    saved = next(s for s in v["tasks"][0]["steps"] if s["tool"] == "save")
    assert saved["undo_text"] == "Move q3-layoffs.docx to the Recycle Bin", "the owner's own panel may name the file"
    assert "recycle_new" not in json.dumps(v) and PATH not in json.dumps(v), "no recipe and no full path, even for the panel"
    assert v["tasks"][0]["failed"] == 1 and v["screenshots"] is True


def test_pictures_need_the_panel_token_and_the_picture_token(world):
    tl, j, calls = world
    calc = next(s for t in tl.view()["tasks"] for s in t["steps"] if s["step_label"] == "Open Calculator")
    ref, tok = calc["shots"]["before"]["ref"], calc["shots"]["before"]["token"]
    got = tl.shot(ref, tok, tl.panel_token)
    assert got["ok"] and base64.b64decode(got["data"]) == JPEG and got["mime"] == "image/jpeg"
    assert not tl.shot(ref, tok, "wrong")["ok"] and not tl.shot(ref, "0" * 16, tl.panel_token)["ok"] and not tl.shot(ref, "", tl.panel_token)["ok"]
    assert not tl.shot("../x.jpg", tl._shot_token("../x.jpg"), tl.panel_token)["ok"]
    (j.shots.path(ref)).unlink()
    assert "deleted" in tl.shot(ref, tok, tl.panel_token)["message"]
    assert tl.view()["tasks"][1]["steps"][1]["shots"] is None or "before" not in (tl.view()["tasks"][1]["steps"][1]["shots"] or {}), "a deleted picture is no longer offered"


def test_the_panel_click_needs_the_panel_token_and_runs_only_what_was_clicked(world):
    tl, j, calls = world
    assert not run(tl.undo_click("nope", ["x"]))["ok"] and calls == []
    run(tl.undo_click(tl.panel_token, ["a", "b"]))
    run(tl.undo_click(tl.panel_token, None, "t1"))
    run(tl.undo_click(tl.panel_token, None, "", 3))
    run(tl.undo_click(tl.panel_token, None, "", 500))
    assert calls == [{"ids": ["a", "b"]}, {"task": "t1"}, {"n": 3}, {"n": T.MAX_UNDO}]


def test_the_long_poll_returns_at_once_when_newer_waits_for_a_change_and_times_out(world):
    tl, j, calls = world
    first = tl.wanted(0, 0)
    assert first["version"] == tl.version and "view" in first and first["token"] == tl.panel_token
    t0 = time.monotonic()
    assert tl.wanted(first["version"], 0.3)["unchanged"] is True and time.monotonic() - t0 >= 0.25

    def later():
        time.sleep(0.15)
        j.append(J.make_step(task_id="t3", goal="x", step_label="Open Notepad", tool="open_app", cls="B"))
    threading.Thread(target=later).start()
    t0 = time.monotonic()
    got = tl.wanted(first["version"], 5)
    assert "view" in got and time.monotonic() - t0 < 2 and got["view"]["tasks"][0]["task_id"] == "t3", "a new step wakes the panel in well under a second"


def test_a_journal_written_by_another_process_is_noticed(world):
    tl, j, calls = world
    first = tl.wanted(0, 0)
    other = J.Journal(j.root)                                   # no listener of ours: another process
    threading.Timer(0.2, lambda: other.append(J.make_step(task_id="t9", goal="x", step_label="Open Paint", tool="open_app", cls="B"))).start()
    got = tl.wanted(first["version"], 4)
    assert "view" in got and got["view"]["tasks"][0]["task_id"] == "t9"


def test_without_a_journal_the_panel_is_told_why(tmp_path):
    v = T.Timeline(lambda: None).view()
    assert v["available"] is False and v["tasks"] == [] and "not available" in v["reason"]


# ----------------------------------------------------------------------------------------------------------------------- cleaner
def test_a_clean_becomes_one_step_with_a_restore_recipe_and_an_honest_note_about_caches(tmp_path):
    j = J.Journal(tmp_path)
    res = {"binned": 2, "deleted": 40, "freed_bytes": 9_000_000, "undo": [{"path": r"C:\Users\me\Downloads\a.iso", "bytes": 5, "label": "Downloads\\a.iso"},
                                                                      {"path": r"C:\Users\me\Downloads\b.exe", "bytes": 7, "label": "x"}], "undo_more": 3}
    T.record_cleanup(res, j)
    e = j.steps(1)[0]
    assert e["step_label"] == "Cleaned up the PC" and e["class"] == "C" and e["verified"] == "verified"
    assert e["undo"]["kind"] == "cleanup" and len(e["undo"]["items"]) == 2 and e["undo"]["more"] == 3 and e["undo"]["permanent"] == {"count": 40, "bytes": 9_000_000}
    assert "a.iso" not in json.dumps(T.Timeline.model_view(e)) and e["result"] == "2 to the Recycle Bin, 40 deleted for good"
    T.record_cleanup({"binned": 0, "deleted": 5, "freed_bytes": 10, "undo": []}, j)
    assert j.steps(1)[0]["undo"]["kind"] == "cannot_undo" and "caches" in j.steps(1)[0]["undo"]["why"]
    n = len(j.steps())
    T.record_cleanup({"binned": 0, "deleted": 0}, j)
    T.record_cleanup({"binned": 9, "deleted": 0, "dry_run": True, "undo": []}, j)
    T.record_cleanup("nonsense", j)
    assert len(j.steps()) == n


# ------------------------------------------------------------------------------------------------------------ through the tool server
def test_the_tools_are_registered_the_chat_one_is_listed_and_the_panel_ones_are_not(world):
    tl, j, calls = world

    class Client:
        pass

    async def go():
        tools = F.FaceTools(client_factory=lambda: Client(), console=type("C", (), {"BridgeError": Exception, "speak_full_reply": staticmethod(lambda: False)}),
                            clean=lambda v: v)
        server = F.build_server(tools, None, F.DEFAULT_PORT, None, None, None, None, "chat", timeline=tl)
        names = {t.name for t in await server.list_tools()}
        res = await server.call_tool("jarvis_timeline", {"action": "last", "n": 2})
        wanted = await server.call_tool("jarvis_timeline_wanted", {"seen": 0, "wait_s": 0})
        undone = await server.call_tool("jarvis_timeline", {"action": "undo", "say": "what did you do"})
        return names, str(res), str(wanted), str(undone)
    names, res, wanted, undone = asyncio.run(go())
    assert "jarvis_timeline" in names and not names & set(T.PANEL_TIMELINE_TOOLS)
    assert "Click in Chrome" in res and "q3-layoffs" not in res and "view" in wanted and "only when the owner says undo" in undone
    assert set(T.PANEL_TIMELINE_TOOLS) <= set(F.PANEL_TOOLS) and "jarvis_timeline" in F.CHAT_TOOLS


def test_the_chat_tool_description_tells_the_model_to_pass_the_owners_words():
    d = F.CHAT_TOOLS["jarvis_timeline"]
    assert "undo" in d and "owner said undo" in d and "say" in d


# --------------------------------------------------------------------------- the real cleaner on a fake PC writes its journal step
def test_the_cleaner_says_before_it_acts_that_caches_cannot_come_back_and_journals_what_it_did(tmp_path):
    from jarvis.bridge import ask as A
    from jarvis.bridge import cleaner_ui as U
    from jarvis.bridge.ask_spec import iter_nodes
    from jarvis.bridge.test_cleaner import World, make_review
    from jarvis.plugins.desktop import journal_client

    w = World(tmp_path / "pc")
    w.mk("Users/me/AppData/Local/Temp/a.tmp", 1000, 2)
    make_review(w)
    broker = A.AskBroker(home=w.env.home)
    svc = U.CleanerService(w.cleaner, broker, spawn=lambda fn: fn())
    j = J.Journal(tmp_path / "journal")
    journal_client.use(j)
    try:
        svc.journal = True
        svc.tool("open", "open the cleaner")
        warn = [n for n in iter_nodes(broker.wanted(0)["form"]["fields"]) if n.get("type") == "callout" and n.get("level") == "warn"]
        assert warn and "cannot be undone" in warn[0]["text"] and "Recycle Bin" in warn[0]["text"], "said in the confirmation text BEFORE cleaning"
        svc.tool("check", "old installers")
        svc.tool("clean", "clean now")
        e = j.steps(1)[0]
        assert e["step_label"] == "Cleaned up the PC" and e["undo"]["kind"] in ("cleanup", "cannot_undo") and e["class"] in ("B", "C")
        assert "a.tmp" not in json.dumps(T.Timeline.model_view(e))
    finally:
        journal_client.use(None)


def test_a_closed_panel_gets_no_view_until_it_is_asked_to_open(world):
    tl, j, calls = world
    first = tl.wanted(0, 0, full=False, seen_open=0)
    assert first["view"] is None and first["token"] == tl.panel_token
    run(tl.tool("open"))
    got = tl.wanted(first["version"], 2, full=False, seen_open=0)
    assert got["open_seq"] == 1 and got["view"] and got["view"]["available"], "asked to open: the view comes with the answer"
    again = tl.wanted(got["version"] - 1, 0, full=False, seen_open=1)
    assert again["view"] is None, "after it opened, a closed panel is told nothing but the version"
