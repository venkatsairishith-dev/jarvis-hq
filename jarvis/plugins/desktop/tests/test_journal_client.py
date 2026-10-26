"""The desktop plugin's journal client (D61): what a step writes, which class and recipe it gets, that typed text never reaches the file, task grouping,
the new-file rule, pictures found by job id, and that a broken or missing journal never stops an action."""
import asyncio
import json
import os
import time

import pytest

from jarvis.node.jarvis_node import journal as J
from jarvis.plugins.desktop import coordinator as C
from jarvis.plugins.desktop import journal_client as jc
from jarvis.pluginapi import Err, Ok, SdkError


@pytest.fixture
def jr(tmp_path):
    j = J.Journal(tmp_path / "journal", env={"JARVIS_JOURNAL_SCREENSHOTS": "1"})
    jc.use(j)
    yield j
    jc.use(None)


def run(tool, step, res, task=None):
    """One step through the real wrapper with a fake agent."""
    agent = jc.wrap_agent(tool, lambda s: res)
    tok = jc.enter_task(task) if task else None
    try:
        return agent(dict(step, tool=tool))
    finally:
        jc.exit_task(tok)


def last(j):
    return j.steps(1)[0]


def test_an_opened_app_is_a_verified_class_b_step_with_a_close_recipe(jr):
    res = {"ok": True, "opened": "Calculator", "verified": "verified", "window": {"title": "Calculator", "process": "CalculatorApp"},
           "launched": [{"proc": "calculatorapp", "pid": 4242}]}
    out = run("open_app", {"app": "calculator"}, res, "Open Calculator and tell me the weather")
    assert out is res, "the agent's result passes through untouched"
    e = last(jr)
    assert (e["step_label"], e["tool"], e["class"], e["verified"], e["target"]) == ("Open Calculator", "open_app", "B", "verified", "Calculator")
    assert e["undo"]["kind"] == "close_window" and e["undo"]["proc"] == "calculatorapp" and e["undo"]["pid"] == 4242
    assert e["goal_label"].startswith("Open Calculator and tell me") and e["duration_ms"] >= 0 and e["task_id"]
    assert "a window for Calculator appeared" in e["result"]


def test_an_app_that_was_already_open_has_nothing_to_close(jr):
    run("open_app", {"app": "notepad"}, {"ok": True, "opened": "notepad", "verified": "verified", "note": "it was already open: brought to the front"})
    assert last(jr)["undo"] is None


def test_a_launch_whose_window_was_never_seen_is_unconfirmed_not_failed_and_has_no_recipe(jr):
    run("open_app", {"app": "paint"}, {"ok": True, "opened": "Paint", "verified": "unconfirmed", "note": "launched, no window"})
    e = last(jr)
    assert e["verified"] == "unconfirmed" and e["undo"] is None


def test_a_failed_step_is_failed_and_says_why(jr):
    run("open_website", {"url": "https://example.com/a/b?q=secret", "profile": "Default"}, {"ok": False, "error": "browser connection failed"})
    e = last(jr)
    assert e["verified"] == "failed" and e["step_label"] == "Open example.com" and "q=secret" not in json.dumps(e)
    assert "browser connection failed" in e["result"] and e["undo"] is None


def test_typed_text_is_never_in_the_journal_and_cannot_be_undone(jr):
    secret = "my password is hunter2-ZZQ and sk-abcdefghijklmnopqrstuvwxyz123456"
    run("type_text", {"app": "notepad", "target": "", "text": secret}, {"ok": True, "process": "notepad"})
    e = last(jr)
    assert e["step_label"] == f"Type {len(secret)} characters into Notepad" and e["undo"]["kind"] == "cannot_undo"
    raw = "".join(f.read_text(encoding="utf-8") for f in jr.root.glob("journal-*.jsonl"))
    assert "hunter2" not in raw and "sk-abc" not in raw and "password" not in raw


def test_a_single_character_key_is_not_named_but_a_chord_is(jr):
    run("press_key", {"app": "notepad", "key": "p"}, {"ok": True})
    assert last(jr)["step_label"] == "Press a key in Notepad"
    run("press_key", {"app": "notepad", "key": "ctrl+s"}, {"ok": True})
    assert last(jr)["step_label"] == "Press ctrl+s in Notepad"


def test_reads_are_class_a_and_a_held_step_is_class_c_and_failed(jr):
    run("check_pc", {"check": "disk"}, {"ok": True, "rows": [1]})
    e = last(jr)
    assert (e["class"], e["step_label"], e["undo"]) == ("A", "Check disk", None)
    run("browser", {"action": "read", "profile": "Default"}, {"ok": True})
    assert last(jr)["class"] == "A"
    run("browser", {"action": "click", "profile": "Default", "target": "Buy now"}, {"ok": False, "state": "waiting_for_permission", "approval_id": "ap1"})
    e = last(jr)
    assert (e["class"], e["verified"]) == ("C", "failed") and "approval" in e["result"] and "Buy now" not in json.dumps(e)


def test_a_step_that_raises_is_recorded_failed_and_the_error_still_propagates(jr):
    agent = jc.wrap_agent("open_app", lambda s: (_ for _ in ()).throw(RuntimeError("boom: secret text")))
    with pytest.raises(RuntimeError):
        agent({"tool": "open_app", "app": "notepad"})
    e = last(jr)
    assert e["verified"] == "failed" and "secret text" not in json.dumps(e) and "RuntimeError" in e["result"]


def test_a_retry_is_a_second_line(jr):
    calls = []
    agent = jc.wrap_agent("open_website", lambda s: calls.append(1) or {"ok": False, "error": "timed out"})
    agent({"tool": "open_website", "url": "https://example.com/", "profile": "Default"})
    agent({"tool": "open_website", "url": "https://example.com/", "profile": "Default"})
    assert len(jr.steps()) == 2


def test_steps_of_one_goal_share_one_task_and_the_owners_words(jr):
    tok = jc.enter_task("Open Calculator and tell me the weather")
    inner = jc.enter_task("open the calculator")                  # a task inside the goal: the goal keeps its scope
    assert inner is None
    for tool, step in (("open_app", {"app": "calculator"}), ("check_pc", {"check": "network"})):
        jc.wrap_agent(tool, lambda s: {"ok": True, "rows": [1], "verified": "verified"})(dict(step, tool=tool))
    jc.exit_task(tok)
    a, b = jr.steps()[1], jr.steps()[0]
    assert a["task_id"] == b["task_id"] and a["goal_label"] == b["goal_label"] == "Open Calculator and tell me the weather"
    assert a["goal_hash"] == J.goal_hash("open calculator and tell me the weather")
    jc.wrap_agent("check_pc", lambda s: {"ok": True, "rows": [1]})({"tool": "check_pc", "check": "disk"})
    assert jr.steps()[0]["task_id"] != a["task_id"], "outside a scope a step is a task of its own"
    assert len(jr.tasks()) == 2


def test_the_goal_text_is_cleaned_before_it_is_kept(jr):
    tok = jc.enter_task("open C:\\Users\\me\\Documents\\taxes.xlsx and use key sk-abcdefghijklmnopqrstuvwxyz123456")
    run("open_file", {"name": "taxes.xlsx"}, {"ok": True, "verified": "verified", "opened": "x"})
    jc.exit_task(tok)
    raw = "".join(f.read_text(encoding="utf-8") for f in jr.root.glob("journal-*.jsonl"))
    assert "taxes" not in raw and "sk-abc" not in raw and "a .xlsx file" in raw


def test_a_saved_new_file_gets_the_new_file_rule_and_a_file_that_was_there_does_not(jr, tmp_path, monkeypatch):
    desk = tmp_path / "Desktop"
    desk.mkdir()
    (desk / "keep.txt").write_text("kept")
    monkeypatch.setattr(jc, "watch_roots", lambda: [str(desk)])
    tok = jc.enter_task("write hello in notepad and save it")
    jc.wrap_agent("type_text", lambda s: {"ok": True})({"tool": "type_text", "app": "notepad", "text": "hello"})
    jc.wrap_agent("press_key", lambda s: (desk / "hello.txt").write_text("hi") or {"ok": True})({"tool": "press_key", "app": "notepad", "key": "ctrl+s"})
    jc.exit_task(tok)
    saved = [s for s in jr.steps() if s["tool"] == "save"]
    assert [s["step_label"] for s in saved] == ["Saved a .txt file"]
    rec = saved[0]["undo"]
    assert rec["kind"] == "recycle_new" and rec["path"].endswith("hello.txt") and "hello.txt" not in json.dumps(J.public_view(saved[0]))
    assert saved[0]["task_id"] == jr.steps()[-1]["task_id"], "it belongs to the task that saved it"


def test_a_task_that_never_pressed_save_looks_at_no_folder(jr, monkeypatch):
    monkeypatch.setattr(jc, "snapshot_files", lambda roots=None: (_ for _ in ()).throw(AssertionError("scanned")))
    tok = jc.enter_task("open notepad")
    jc.wrap_agent("open_app", lambda s: {"ok": True, "verified": "verified"})({"tool": "open_app", "app": "notepad"})
    jc.exit_task(tok)


def test_a_research_file_made_by_the_step_is_a_new_file_to_undo(jr, tmp_path):
    f = tmp_path / "deck.pptx"
    agent = jc.wrap_agent("research", lambda s: f.write_bytes(b"PK") and {} or {"ok": True, "summary": "done", "file": str(f)})
    agent({"tool": "research", "query": "q", "deliverable": "deck", "open_after": False})
    e = last(jr)
    assert e["undo"]["kind"] == "recycle_new" and e["step_label"] == "Research a topic and make a deck"
    gone = jc.wrap_agent("research", lambda s: {"ok": True, "summary": "x", "file": str(tmp_path / "nope.pptx")})
    gone({"tool": "research", "query": "q", "deliverable": "deck"})
    assert last(jr)["undo"] is None


def test_pictures_taken_by_the_node_are_found_by_the_jobs_of_the_step(jr):
    jr.shots.save("job1-b.jpg", b"\xff\xd8\xff" + b"1")
    jr.shots.save("job1-a.jpg", b"\xff\xd8\xff" + b"2")
    jr.shots.save("job3-a.jpg", b"\xff\xd8\xff" + b"3")

    def agent(step):
        jc.note_job("job1")
        jc.note_job("job2")           # a read: no pictures
        jc.note_job("job3")
        return {"ok": True, "process": "notepad"}
    jc.wrap_agent("type_text", agent)({"tool": "type_text", "app": "notepad", "text": "x"})
    assert last(jr)["shots"] == {"before": "job1-b.jpg", "after": "job3-a.jpg"}
    jc.wrap_agent("check_pc", lambda s: {"ok": True, "rows": [1]})({"tool": "check_pc", "check": "disk"})
    assert last(jr)["shots"] is None


def test_without_a_journal_every_call_is_a_no_op_and_the_action_still_runs(tmp_path):
    jc.use(None)
    res = {"ok": True}
    assert jc.wrap_agent("open_app", lambda s: res)({"tool": "open_app", "app": "x"}) is res
    assert jc.enter_task("x") is None
    jc.exit_task(None)
    jc.note_job("j")
    jc.record_call("doc_reader:read_document", {}, True, {}, "", 1)
    jc.record_setting_change("volume", "10", "20", True)


def test_a_journal_that_cannot_write_never_stops_the_action(jr, monkeypatch):
    monkeypatch.setattr(jr, "append", lambda e: (_ for _ in ()).throw(OSError("disk full")))
    res = {"ok": True, "verified": "verified", "launched": []}
    assert jc.wrap_agent("open_app", lambda s: res)({"tool": "open_app", "app": "x"}) is res


def test_helper_calls_of_a_goal_are_journaled_but_this_plugins_own_entries_are_not_twice(jr):
    jc.record_call("doc_reader:read_document", {"path": "C:\\scans\\invoice.pdf"}, True, {"text": "TOTAL 100"}, "", 900)
    e = last(jr)
    assert (e["class"], e["step_label"], e["target"]) == ("A", "Read a document", "a .pdf file") and "invoice" not in json.dumps(e) and "TOTAL" not in json.dumps(e)
    n = len(jr.steps())
    jc.record_call("desktop:read_window_text", {"app": "notepad"}, True, {}, "", 1)
    assert len(jr.steps()) == n
    jc.record_call("deliverables:open_output", {"path": "x"}, False, {}, "no such file", 5)
    assert last(jr)["verified"] == "failed"


def test_a_settings_change_records_the_value_before_it(jr):
    jc.record_setting_change("dark_mode", "light", "dark", True)
    e = last(jr)
    assert e["undo"] == {"kind": "setting", "name": "dark_mode", "previous": "light", "new": "dark"} and e["step_label"] == "Change dark mode"
    jc.record_setting_change("volume", None, "20", True)
    assert last(jr)["undo"] is None, "no earlier value, no recipe"


class Plugin:
    @jc.journaled("run_pc_action")
    async def run_pc_action(self, action="", target="", text="", **_):
        if action == "boom":
            raise RuntimeError("x")
        if action == "err":
            return Err(SdkError("the PC is busy"))
        return Ok({"ok": True, "opened": "Notepad", "verified": "verified", "launched": [{"proc": "notepad", "pid": 9}]} if action == "open_app" else {"ok": True})


def test_the_entry_decorator_journals_ok_err_and_exceptions_and_passes_the_value_through(jr):
    p = Plugin()
    out = asyncio.run(p.run_pc_action(action="open_app", target="Notepad"))
    assert isinstance(out, Ok) and last(jr)["undo"]["kind"] == "close_window" and last(jr)["step_label"] == "Open Notepad"
    asyncio.run(p.run_pc_action(action="type", target="notepad|Edit", text="hello there"))
    e = last(jr)
    assert e["step_label"] == "Type 11 characters into Notepad" and "hello there" not in json.dumps(e)
    assert isinstance(asyncio.run(p.run_pc_action(action="err", target="x")), Err) and last(jr)["verified"] == "failed"
    with pytest.raises(RuntimeError):
        asyncio.run(p.run_pc_action(action="boom"))
    assert last(jr)["verified"] == "failed"


def test_the_real_plugin_entries_carry_the_decorator():
    from jarvis.plugins.desktop import DesktopPlugin
    for name in ("run_pc_action", "open_website", "play_youtube", "run_browser_action", "run_troubleshooter", "check_pc", "whats_on_screen",
                 "read_window_text", "find_on_screen"):
        fn = getattr(DesktopPlugin, name)
        chain, seen = [], fn
        while seen is not None:
            chain.append(getattr(seen, "journaled", ""))
            seen = getattr(seen, "__wrapped__", None)
        assert name in chain, name


# ------------------------------------------------------------------------- through the real step runners and the coordinator
class _NodePc:
    """Stands in for core.Pc: answers the jobs one "open Calculator" task makes, with the job ids and the `launched` field the node adds."""
    jobs: list = []

    def __init__(self, *a, **k):
        self.stopped, self.current_job = False, None

    def run(self, body, timeout=90.0, what=""):
        _NodePc.jobs.append(body)
        jid = f"job{len(_NodePc.jobs)}"
        op = body.get("op")
        if op == "windows":
            return {"state": "done", "job": jid, "result": {"ok": True, "windows": []}}
        if op == "ui":
            return {"state": "done", "job": jid, "result": {"ok": False, "error": "no window"}}
        if op == "open_app":
            return {"state": "done", "job": jid, "result": {"ok": True, "opened": "Calculator", "verified": "verified", "window": {"title": "Calculator", "process": "CalculatorApp"},
                                                             "launched": [{"proc": "calculatorapp", "pid": 4242}]}}
        return {"state": "done", "job": jid, "result": {"ok": True, "rows": [1]}}

    def cancel(self, jid):
        return {"id": jid, "state": "cancelled"}


def test_a_whole_task_through_the_coordinator_writes_one_task_with_recipes_and_pictures(jr, tmp_path, monkeypatch):
    from jarvis.plugins.desktop import DesktopPlugin, core, decide

    _NodePc.jobs = []
    monkeypatch.setattr(core, "Pc", _NodePc)
    monkeypatch.setattr(core, "set_activity", lambda *a, **k: None)
    jr.shots.save("job3-a.jpg", b"\xff\xd8\xff" + b"after")             # the node took an after picture for its open_app job
    host = type("H", (), {"_active_pcs": []})()
    agents = DesktopPlugin._agents(host, None)
    profiles = [{"profile": "Default", "name": "Venkat", "account": "ven***", "default": True, "say": ["default"]}]
    plan = decide.Plan([{"tool": "open_app", "app": "calculator"}, {"tool": "check_pc", "check": "network", "app": ""}], "ok", model="fake")
    c = C.Coordinator(agents, profiles, tasks_dir=tmp_path / "tasks", lock_path=tmp_path / "lock")
    tok = jc.enter_task("Open Calculator and tell me the network")
    try:
        rec = c.run("Open Calculator and tell me the network", plan)
    finally:
        jc.exit_task(tok)
    assert rec["status"] == "completed", rec
    steps = list(reversed(jr.steps()))
    assert [s["step_label"] for s in steps] == ["Open Calculator", "Check network"]
    assert len({s["task_id"] for s in steps}) == 1 and steps[0]["goal_label"] == "Open Calculator and tell me the network"
    assert steps[0]["undo"] == {"kind": "close_window", "proc": "calculatorapp", "pid": 4242, "opened_at": steps[0]["undo"]["opened_at"]}
    assert steps[0]["verified"] == "verified" and steps[0]["class"] == "B" and steps[1]["class"] == "A" and steps[1]["undo"] is None
    assert steps[0]["shots"] == {"before": None, "after": "job3-a.jpg"}, "the job ids of the step led to the node's picture"
    assert jr.undoable(5)[0]["step_label"] == "Open Calculator"
