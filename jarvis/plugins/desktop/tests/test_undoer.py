"""Undo (D61) from the journal to the PC and back: recipes written by the client, run as kernel jobs under the real policy, executed by the real node code on a
fake PC, and journaled. The owner's words choose how many; nothing else is touched; the answer says what was and was not undone."""
import asyncio
import json
import os
import shutil
import sys
import time

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "..", "..", "node", "tests"))
sys.path.insert(0, os.path.join(HERE, "..", "..", "..", "node"))
sys.path.insert(0, os.path.join(HERE, "..", "..", "..", "kernel"))

from fakepc import FakeBinPc  # noqa: E402
from jarvis_kernel.jobs import action_for  # noqa: E402
from jarvis_kernel.policy import DENY, YELLOW_AUTO, decide  # noqa: E402
from jarvis_node import undo as NU  # noqa: E402

from jarvis.node.jarvis_node import journal as J  # noqa: E402
from jarvis.plugins.desktop import DesktopPlugin, core, undoer  # noqa: E402
from jarvis.plugins.desktop import journal_client as jc  # noqa: E402


class NodePc:
    """Stands in for core.Pc: the kernel's real policy decides, then the real node code runs on the fake PC."""

    def __init__(self, ports, approval=False):
        self.ports, self.jobs, self.approval = ports, [], approval
        self.stopped, self.current_job, self.approval_wait_s = False, None, 0.0
        self.levels = []

    def __call__(self, *a, **k):             # core.Pc(on_state=...) in the plugin
        return self

    def run(self, body, timeout=90.0, what=""):
        self.jobs.append(body)
        params = {"agent": "owner", "op": body["action"], "target": body["target"], "path": body["path"], "expect": body["expect"]}
        level = decide(action_for("pc_undo", params, owner_direct=True)).level
        self.levels.append(level)
        if level == DENY:
            raise core.DesktopError("refused by policy: touches a secrets path")
        if level != YELLOW_AUTO or self.approval:
            return {"state": "waiting_for_permission", "approval_id": "ap9", "result": {}}
        return {"state": "done", "job": "j" + str(len(self.jobs)), "result": NU.run(params, self.ports)}

    def cancel(self, jid):
        return {"id": jid, "state": "cancelled"}


@pytest.fixture
def world(tmp_path):
    pc = FakeBinPc(tmp_path)
    j = J.Journal(tmp_path / "journal", env={"JARVIS_JOURNAL_SCREENSHOTS": "0"})
    jc.use(j)
    node = NodePc(pc.ports())
    yield pc, j, node
    jc.use(None)


def step(j, label, undo, task="t1", ts=None, verified="verified"):
    return j.append(J.make_step(task_id=task, goal="the owner's words", step_label=label, tool="x", cls="B", undo=undo, verified=verified, ts=ts))


def test_a_new_file_the_task_saved_is_moved_to_the_bin_and_the_step_is_marked_undone(world):
    pc, j, node = world
    p = pc.file("Documents/notes.txt", "hello")
    s = step(j, "Saved a .txt file", NU.recipe_recycle_new(p))
    rep = undoer.run(j.undoable(1), node, j)
    assert rep["ok"] and rep["undone"] == 1 and rep["results"][0]["status"] == "undone" and not os.path.exists(p)
    assert j.get(s["id"])["undone"]["status"] == "undone"
    assert [x["step_label"] for x in j.steps(2)][0] == "Undo: Saved a .txt file", "the undo is a journal step of its own"
    assert j.undoable(5) == [] and "notes" not in rep["summary"]


def test_a_file_that_changed_since_is_refused_and_stays_undoable(world):
    pc, j, node = world
    p = pc.file("Documents/notes.txt", "hello")
    s = step(j, "Saved a .txt file", NU.recipe_recycle_new(p))
    open(p, "a").write(" and more")
    rep = undoer.run(j.undoable(1), node, j)
    assert not rep["ok"] and rep["results"][0]["status"] == "refused" and "changed" in rep["results"][0]["reason"] and os.path.exists(p)
    assert "Not undone" in rep["summary"] and "Undone:" not in rep["summary"]
    assert [x["id"] for x in j.undoable(1)] == [s["id"]], "a refused undo does not use the step up"
    assert j.get(s["id"]).get("undo_tried", {}).get("status") == "refused"


def test_a_task_is_taken_back_in_reverse_order_and_each_result_is_named(world):
    pc, j, node = world
    pc.windows.add(10, "Notepad", "notepad", 5)
    p = pc.file("Desktop/hello.txt", "hi")
    t = time.time()
    step(j, "Open Notepad", NU.recipe_close_window("notepad", 5, t), ts=t + 1)
    step(j, "Type 2 characters into Notepad", NU.recipe_cannot("typed text cannot be taken back"), ts=t + 2)
    step(j, "Saved a .txt file", NU.recipe_recycle_new(p), ts=t + 3)
    step(j, "Check disk", None, ts=t + 4)
    steps = j.undoable(task_id="t1")
    assert [s["step_label"] for s in steps] == ["Saved a .txt file", "Type 2 characters into Notepad", "Open Notepad"]
    rep = undoer.run(steps, node, j)
    assert [r["label"] for r in rep["results"]] == ["Saved a .txt file", "Type 2 characters into Notepad", "Open Notepad"]
    assert [r["status"] for r in rep["results"]] == ["undone", "cannot", "undone"]
    assert rep["undone"] == 2 and rep["not_undone"] == 1 and "cannot be taken back" in rep["summary"]
    assert [b["action"] for b in node.jobs] == ["recycle", "close_window"], "the typed text ran no job"
    assert not os.path.exists(p) and pc.windows.closes == [10]


def test_undo_the_last_n_never_touches_more_than_n(world):
    pc, j, node = world
    files = [pc.file(f"Documents/f{i}.txt", str(i)) for i in range(4)]
    t = time.time()
    for i, f in enumerate(files):
        step(j, f"Saved file {i}", NU.recipe_recycle_new(f), ts=t + i)
    rep = undoer.run(j.undoable(2), node, j)
    assert [r["label"] for r in rep["results"]] == ["Saved file 3", "Saved file 2"]
    assert [os.path.exists(f) for f in files] == [True, True, False, False]
    assert len(node.jobs) == 2


def test_an_app_with_unsaved_work_is_not_closed_and_the_answer_says_why(world):
    pc, j, node = world
    pc.windows.add(11, "*Untitled - Notepad", "notepad", 6)
    step(j, "Open Notepad", NU.recipe_close_window("notepad", 6, time.time() - 5))
    rep = undoer.run(j.undoable(1), node, j)
    assert rep["results"][0]["status"] == "refused" and "unsaved" in rep["results"][0]["reason"] and pc.windows.closes == []


def test_a_binned_file_comes_back_even_to_a_taken_name_and_a_cleanup_says_what_cannot_return(world):
    pc, j, node = world
    keep = pc.file("Downloads/setup.exe", "installer")
    pc.recycle(keep)
    taken = pc.file("Downloads/dup.iso", "dup")
    pc.recycle(taken)
    pc.file("Downloads/dup.iso", "someone put a new one")
    gone = str(pc.home / "Downloads" / "emptied.bin")
    recipe = {"kind": "cleanup", "items": [{"path": keep, "size": 9}, {"path": taken, "size": 3}, {"path": gone, "size": 1}], "more": 4,
              "permanent": {"count": 120, "bytes": 5_000_000}}
    s = step(j, "Cleaned up the PC", recipe)
    rep = undoer.run([s], node, j)
    r = rep["results"][0]
    assert r["status"] == "partly" and "2 of 3 items are back" in r["reason"] and "cannot be restored" in r["reason"] and "4 more items" in r["reason"]
    assert open(keep).read() == "installer" and open(taken).read() == "someone put a new one"
    assert os.path.exists(str(pc.home / "Downloads" / "dup (1).iso")), "restored under a numbered name, nothing overwritten"
    assert j.get(s["id"])["undone"]["status"] == "partly"


def test_a_move_a_copy_and_a_setting_are_undone_through_the_same_path(world):
    pc, j, node = world
    src = pc.file("Downloads/a.txt", "x")
    dst = str(pc.home / "Documents" / "a.txt")
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    shutil.move(src, dst)
    cp = str(pc.home / "Documents" / "a copy.txt")
    shutil.copy2(dst, cp)
    pc.settings.v["volume"] = "20"
    t = time.time()
    step(j, "Move a file", NU.recipe_move_back(src, dst), ts=t + 1)
    step(j, "Copy a file", NU.recipe_recycle_new(cp), ts=t + 2)
    jc.record_setting_change("volume", "55", "20", True)
    rep = undoer.run(j.undoable(3), node, j)
    assert [r["status"] for r in rep["results"]] == ["undone", "undone", "undone"], rep
    assert os.path.exists(src) and not os.path.exists(dst) and not os.path.exists(cp) and pc.settings.v["volume"] == "55"


def test_the_kernel_policy_still_applies_a_secret_path_is_refused_by_the_kernel_not_run(world):
    pc, j, node = world
    p = pc.file("Documents/ICICI Bank statement.pdf")
    s = step(j, "Saved a .pdf file", NU.recipe_recycle_new(p))
    rep = undoer.run([s], node, j)
    assert rep["results"][0]["status"] == "refused" and os.path.exists(p) and node.levels == [DENY]
    s2 = step(j, "Saved a .json file", NU.recipe_recycle_new(pc.file("Documents/jarvis-secrets/keys.json")))
    assert undoer.run([s2], node, j)["results"][0]["status"] == "refused"


def test_an_undo_that_would_need_the_pin_is_reported_and_left_alone(world):
    pc, j, node = world
    p = pc.file("Documents/a.txt")
    node.approval = True
    s = step(j, "Saved a .txt file", NU.recipe_recycle_new(p))
    rep = undoer.run([s], node, j)
    assert rep["results"][0]["status"] == "approval" and "PIN" in rep["results"][0]["reason"] and os.path.exists(p)
    assert j.undoable(1)[0]["id"] == s["id"]


def test_an_already_undone_step_is_not_undone_twice_and_not_journaled_again(world):
    pc, j, node = world
    p = pc.file("Documents/a.txt")
    s = step(j, "Saved a .txt file", NU.recipe_recycle_new(p))
    undoer.run([s], node, j)
    n = len(j.steps())
    again = undoer.run([j.get(s["id"])], node, j)
    assert again["results"][0]["status"] == "already" and len(j.steps()) == n and len(node.jobs) == 1


def test_a_failed_step_and_a_step_without_a_recipe_are_not_offered(world):
    pc, j, node = world
    step(j, "Open Calculator", NU.recipe_close_window("calculatorapp", 1, 0), verified="failed")
    step(j, "Check disk", None)
    assert [s["step_label"] for s in j.undoable(5)] == ["Open Calculator"], "the journal offers it; the client never writes a recipe for a step that failed"
    from jarvis.plugins.desktop import journal_client as c
    assert c.recipe_for("open_app", {}, {"ok": False}, False, 0) is None


def test_the_report_holds_labels_and_fixed_words_only_no_paths(world):
    pc, j, node = world
    p = pc.file("Documents/my secret plan.txt")
    s = step(j, "Saved a .txt file", NU.recipe_recycle_new(p))
    open(p, "a").write("!")
    rep = undoer.run([s], node, j)
    assert "secret plan" not in json.dumps(rep) and str(pc.home) not in json.dumps(rep)
    undo_line = j.steps(1)[0]
    assert undo_line["tool"] == "undo" and "secret plan" not in json.dumps(undo_line) and undo_line["undo_status"] == "refused"
    assert undo_line["verified"] == "failed"


def test_nothing_to_undo_says_so(world):
    pc, j, node = world
    assert undoer.run([], node, j)["summary"] == "There was nothing to undo."


def test_bodies_for_every_recipe_kind_match_what_the_kernel_accepts():
    from jarvis_kernel.jobs import _check_params
    recipes = [NU.recipe_close_window("notepad", 5, 1.0), {"kind": "recycle_new", "path": "C:\\a\\b.txt", "stamp": "abc"}, NU.recipe_restore_bin("C:\\a\\b.txt", 5),
               {"kind": "move_back", "src": "C:\\a", "dst": "C:\\b", "stamp": "x"}, NU.recipe_setting("volume", "5", "9"),
               {"kind": "cleanup", "items": [{"path": "C:\\x", "size": 1}]}]
    for r in recipes:
        for b in undoer.bodies(r):
            _check_params("pc_undo", {"agent": "owner", "op": b["action"], "target": b["target"], "path": b["path"], "expect": b["expect"]})
    assert undoer.bodies(NU.recipe_cannot("x")) == [] and undoer.bodies(None) == []


# ---------------------------------------------------------------------------------------------------- the plugin entry
class Host:
    def __init__(self):
        for name in ("_pc", "_run", "_one", "_stop_pc_tasks", "_agents", "_missions_ready"):
            if hasattr(DesktopPlugin, name):
                setattr(self, name, getattr(DesktopPlugin, name).__get__(self))

    _active_pcs: list = []


def test_the_entry_chooses_exactly_what_the_owner_named_and_holds_the_pc_lock(world, monkeypatch):
    pc, j, node = world
    monkeypatch.setattr(core, "Pc", node)
    monkeypatch.setattr(core, "set_activity", lambda *a, **k: None)
    files = [pc.file(f"Documents/f{i}.txt", str(i)) for i in range(3)]
    t = time.time()
    ids = [step(j, f"Saved file {i}", NU.recipe_recycle_new(f), task="t" + str(i % 2), ts=t + i)["id"] for i, f in enumerate(files)]
    host = Host()
    out = asyncio.run(DesktopPlugin.undo_steps(host, n=1))
    assert out.value["undone"] == 1 and [os.path.exists(f) for f in files] == [True, True, False]
    out = asyncio.run(DesktopPlugin.undo_steps(host, task="t0"))                       # files 0 (and 2 is already undone)
    assert out.value["undone"] == 1 and [os.path.exists(f) for f in files] == [False, True, False]
    out = asyncio.run(DesktopPlugin.undo_steps(host, ids=[ids[1]]))
    assert out.value["undone"] == 1 and not any(os.path.exists(f) for f in files)
    out = asyncio.run(DesktopPlugin.undo_steps(host, n=3))
    assert out.value["summary"] == "There was nothing to undo."
    assert node.approval_wait_s == 0.0, "an undo never waits for the PIN"


def test_the_entry_is_not_a_chat_tool_and_says_it_is_for_an_explicit_request():
    from jarvis.bridge import face_tools
    assert "desktop__undo_steps" not in face_tools.CHAT_TOOLS, "the chat model reaches undo only through jarvis_timeline, which checks the owner's words"
