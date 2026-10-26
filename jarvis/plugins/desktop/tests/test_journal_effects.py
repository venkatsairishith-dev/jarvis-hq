"""D61 on top of D54: the file and setting jobs of the catalogue tasks are journaled by what they DID (one line per effect, class from the kernel's effect
classes), each with its undo recipe, and the recipe really puts things back. The node side is the real taskops on real temporary folders (a Recycle Bin
with the real `$I`/`$R` layout); the Windows pieces are the fakes of the undo tests. The journal never holds a file name."""
import json
import os
import sys
import time

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "..", "..", "node", "tests"))
sys.path.insert(0, os.path.join(HERE, "..", "..", "..", "node"))
sys.path.insert(0, os.path.join(HERE, "..", "..", "..", "kernel"))

from fakepc import FakeBinPc  # noqa: E402
from jarvis_kernel.jobs import action_for  # noqa: E402
from jarvis_kernel.policy import YELLOW_AUTO, decide  # noqa: E402
from jarvis_node import savefile as sf  # noqa: E402
from jarvis_node import taskops as T  # noqa: E402
from jarvis_node import undo as NU  # noqa: E402

from jarvis.node.jarvis_node import journal as J  # noqa: E402
from jarvis.plugins.desktop import journal_client as jc  # noqa: E402
from jarvis.plugins.desktop import undoer  # noqa: E402


class NodePc:
    """core.Pc for the undoer: `op: undo` jobs go through the kernel's real policy and the real node undo; `op: file` (a sort's own undo) runs taskops."""

    def __init__(self, pc, ctx):
        self.pc, self.ctx, self.jobs = pc, ctx, []
        self.stopped, self.current_job, self.approval_wait_s = False, None, 0.0

    def __call__(self, *a, **k):
        return self

    def run(self, body, timeout=90.0, what=""):
        self.jobs.append(body)
        if body["op"] == "file":
            return {"state": "done", "result": T.run("file_op", {"agent": "owner", "op": body["action"], "target": body["target"], "path": body["path"],
                                                                "dest": body["dest"], "text": body["text"]}, self.ctx)}
        params = {"agent": "owner", "op": body["action"], "target": body["target"], "path": body["path"], "expect": body["expect"]}
        assert decide(action_for("pc_undo", params, owner_direct=True)).level == YELLOW_AUTO
        return {"state": "done", "result": NU.run(params, self.pc.ports())}


@pytest.fixture
def w(tmp_path):
    pc = FakeBinPc(tmp_path)
    for n in ("Documents", "Downloads"):
        (pc.home / n).mkdir()
    roots = sf.folder_roots(known={"documents": str(pc.home / "Documents"), "downloads": str(pc.home / "Downloads")}, projects={}, outputs=str(tmp_path / "outputs"))
    (tmp_path / "outputs").mkdir()

    def lister():
        return [{"name": os.path.basename(i["path"]), "from": os.path.dirname(i["path"]), "size": i["size"]} for i in pc.bin.items()]
    ctx = T.Ctx(roots=roots, journal=str(tmp_path / "ops" / "journal.jsonl"), trasher=pc.recycle, bin_lister=lister, bin_emptier=lambda: None,
                env={"TEMP": str(tmp_path / "t")}, scan_only=True)
    j = J.Journal(tmp_path / "journal", env={"JARVIS_JOURNAL_SCREENSHOTS": "0"})
    jc.use(j)
    jc.use_roots(roots)
    jc.use_setting_reader(None)
    yield type("W", (), {"pc": pc, "ctx": ctx, "j": j, "docs": pc.home / "Documents", "dl": pc.home / "Downloads", "node": NodePc(pc, ctx)})
    jc.use(None)
    jc.use_roots(None)
    jc.use_setting_reader(None)


def fileop(w, act, target="Documents", path="", dest="", text=""):
    """The real node answers a file job; the plugin's hook writes the journal line."""
    body = {"op": "file", "action": act, "target": target, "path": path, "dest": dest, "text": text}
    r = T.run("file_op", {"agent": "owner", "op": act, "target": target, "path": path, "dest": dest, "text": text}, w.ctx)
    assert r.get("ok"), r
    jc.after_job(body, dict(r, state="done"), None, time.time())
    return r


def last(w):
    return w.j.steps(1)[0]


def no_names(entry, *names):
    """The line (without its recipe, which must keep the real path to undo) names no file."""
    text = json.dumps({k: v for k, v in entry.items() if k != "undo"})
    return all(n not in text for n in names)


def undo_last(w, n=1):
    return undoer.run(w.j.undoable(n), w.node, w.j)


# ------------------------------------------------------------------------------------------------------------------------------ new things: class A
def test_a_new_file_is_class_a_named_by_its_class_and_undone_into_the_bin(w):
    fileop(w, "create_file", "Documents", "weekend plans.txt", text="hi")
    e = last(w)
    assert (e["class"], e["step_label"], e["target"], e["tool"], e["verified"]) == ("A", "Create a .txt file in Documents", "Documents", "file", "verified")
    assert e["undo"]["kind"] == "recycle_new" and e["undo"]["path"] == str(w.docs / "weekend plans.txt") and no_names(e, "weekend", "plans")
    rep = undo_last(w)
    assert rep["ok"], rep
    assert not (w.docs / "weekend plans.txt").exists() and "weekend" not in rep["summary"]


def test_a_new_folder_and_an_unpacked_archive_are_new_things_to_undo(w):
    fileop(w, "mkdir", "Documents", "Taxes 2026")
    e = last(w)
    assert e["step_label"] == "Create a folder in Documents" and e["undo"]["kind"] == "recycle_new" and (w.docs / "Taxes 2026").is_dir() and no_names(e, "Taxes")
    assert undo_last(w)["ok"] and not (w.docs / "Taxes 2026").exists()
    import zipfile
    with zipfile.ZipFile(w.docs / "pack.zip", "w") as z:
        z.writestr("a.txt", "x")
    fileop(w, "unzip", "Documents", "pack.zip")
    assert last(w)["undo"]["kind"] == "recycle_new" and (w.docs / "pack").is_dir()
    assert undo_last(w)["ok"] and not (w.docs / "pack").exists() and (w.docs / "pack.zip").exists()


def test_a_copy_and_a_zip_make_new_files_and_the_original_is_never_touched(w):
    (w.docs / "a.txt").write_text("1")
    fileop(w, "copy", "Documents", "a.txt", "Downloads")
    e = last(w)
    assert (e["class"], e["step_label"]) == ("A", "Copy a .txt file to Downloads") and e["undo"]["path"] == str(w.dl / "a.txt")
    fileop(w, "zip", "Documents", "a.txt")
    assert last(w)["undo"] == dict(NU.recipe_recycle_new(str(w.docs / "a.zip")))
    assert undo_last(w, 2)["undone"] == 2
    assert not (w.dl / "a.txt").exists() and not (w.docs / "a.zip").exists() and (w.docs / "a.txt").read_text() == "1"


# ----------------------------------------------------------------------------------------------------------------------- changing what was there: class B
def test_a_rename_is_class_b_and_is_reversed_only_while_the_new_name_is_unchanged(w):
    (w.docs / "old.docx").write_text("v1")
    fileop(w, "rename", "Documents", "old.docx", "new")
    e = last(w)
    assert (e["class"], e["step_label"], e["undo"]["kind"]) == ("B", "Rename a .docx file in Documents", "move_back") and (w.docs / "new.docx").exists()
    assert undo_last(w)["ok"] and (w.docs / "old.docx").read_text() == "v1" and not (w.docs / "new.docx").exists()
    fileop(w, "rename", "Documents", "old.docx", "newer")
    (w.docs / "newer.docx").write_text("edited since")
    rep = undo_last(w)
    assert not rep["ok"] and rep["results"][0]["status"] == "refused" and "changed since" in rep["summary"] and (w.docs / "newer.docx").read_text() == "edited since"


def test_a_move_goes_back_unless_something_took_its_place_and_nothing_is_overwritten(w):
    (w.docs / "m.txt").write_text("m")
    fileop(w, "move", "Documents", "m.txt", "Downloads")
    e = last(w)
    assert (e["class"], e["step_label"]) == ("B", "Move a .txt file to Downloads") and (w.dl / "m.txt").exists()
    (w.docs / "m.txt").write_text("someone made this")
    rep = undo_last(w)
    assert rep["results"][0]["status"] == "refused" and "something else" in rep["summary"] and (w.dl / "m.txt").read_text() == "m"
    (w.docs / "m.txt").unlink()
    assert undo_last(w)["ok"] and (w.docs / "m.txt").read_text() == "m"


def test_a_sort_of_downloads_is_put_back_file_by_file_and_a_file_edited_since_stays_where_it_is(w):
    for n in ("a.pdf", "b.png", "c.docx"):
        p = w.dl / n
        p.write_text(n)
        os.utime(p, (time.time() - 7200, time.time() - 7200))
    r = fileop(w, "sort_downloads", "Downloads")
    e = last(w)
    assert r["moved"] == 3 and (e["class"], e["step_label"], e["undo"]["kind"]) == ("B", "Sort 3 files in Downloads", "moves") and len(e["undo"]["items"]) == 3
    assert not (w.dl / "a.pdf").exists() and no_names(e, "a.pdf", "b.png")
    rep = undo_last(w)
    assert rep["ok"] and rep["results"][0]["status"] == "undone" and (w.dl / "a.pdf").read_text() == "a.pdf" and (w.dl / "c.docx").exists() and "3 files" in rep["summary"]
    # sorted again, then one sorted file is edited: only that one stays, the others come back, and the answer says so
    for n in ("a.pdf", "b.png", "c.docx"):
        os.utime(w.dl / n, (time.time() - 7200, time.time() - 7200))
    fileop(w, "sort_downloads", "Downloads")
    sorted_pdf = next(p for p in w.dl.rglob("a.pdf"))
    sorted_pdf.write_text("edited after the sort")
    rep = undo_last(w)
    assert rep["results"][0]["status"] == "partly" and "2 of 3 files" in rep["summary"] and "changed since" in rep["summary"]
    assert sorted_pdf.read_text() == "edited after the sort" and (w.dl / "b.png").exists() and (w.dl / "c.docx").exists()


# -------------------------------------------------------------------------------------------------------------------------------- deleting: class C
def test_a_delete_to_the_bin_is_restored_exactly_and_a_taken_name_gets_a_number(w):
    (w.docs / "gone.txt").write_text("keep me")
    fileop(w, "trash", "Documents", "gone.txt")
    e = last(w)
    assert (e["class"], e["step_label"], e["undo"]["kind"]) == ("C", "Move a .txt file to the Recycle Bin", "restore_bin") and not (w.docs / "gone.txt").exists() and no_names(e, "gone")
    assert undo_last(w)["ok"] and (w.docs / "gone.txt").read_text() == "keep me"
    fileop(w, "trash", "Documents", "gone.txt")
    (w.docs / "gone.txt").write_text("a new file with the same name")
    assert undo_last(w)["ok"]
    assert (w.docs / "gone.txt").read_text() == "a new file with the same name" and (w.docs / "gone (1).txt").read_text() == "keep me"


def test_a_permanent_delete_a_bin_emptying_and_a_cleanup_are_class_c_and_say_they_cannot_be_undone(w):
    (w.docs / "x.txt").write_text("x")
    fileop(w, "delete_permanent", "Documents", "x.txt")
    e = last(w)
    assert (e["class"], e["step_label"], e["undo"]["kind"]) == ("C", "Delete a .txt file for good", "cannot_undo") and not (w.docs / "x.txt").exists()
    jc.after_job({"op": "file", "action": "empty_bin"}, {"ok": True, "state": "done", "items_before": 3, "verified": True}, None, time.time())
    assert last(w)["undo"]["kind"] == "cannot_undo" and last(w)["class"] == "C"
    jc.after_job({"op": "file", "action": "cleanup"}, {"ok": True, "state": "done", "freed_bytes": 10, "verified": True}, None, time.time())
    assert "caches" in last(w)["undo"]["why"] and last(w)["step_label"].startswith("Clean up the PC")
    rep = undo_last(w, 3)
    assert not rep["ok"] and [r["status"] for r in rep["results"]] == ["cannot"] * 3 and "for good" in rep["summary"]
    assert all(s["undo"]["kind"] == "cannot_undo" for s in w.j.undoable(3))


# ------------------------------------------------------------------------------------------------------------------------------------- settings
def setting_job(w, action, text="", read_back=True, ok=True):
    body = {"op": "setting", "action": action, "text": text}
    pre = jc.before_job(body)
    r = T.run("setting", {"agent": "owner", "op": action, "text": text}, w.ctx, run=lambda script, env: "JARVIS_SET:" + ("0,0" if action == "dark_mode" else "1"))
    jc.after_job(body, dict(r, state="done", ok=ok), pre, time.time())
    return pre, r


def test_a_setting_records_the_value_read_before_it_and_the_undo_reads_it_back(w):
    jc.use_setting_reader(lambda name: w.pc.settings.get(name))
    assert w.pc.settings.v["dark_mode"] == "dark"
    w.pc.settings.v["dark_mode"] = "light"
    pre, r = setting_job(w, "dark_mode")
    assert pre == {"name": "dark_mode", "previous": "light"}
    e = last(w)
    assert (e["class"], e["tool"], e["step_label"]) == ("B", "setting", "Change dark_mode") and e["undo"] == {"kind": "setting", "name": "dark_mode", "previous": "light", "new": "dark"}
    w.pc.settings.v["dark_mode"] = "dark"                              # (what the job did)
    assert undo_last(w)["ok"] and w.pc.settings.v["dark_mode"] == "light"


def test_a_setting_whose_earlier_value_could_not_be_read_cannot_be_undone_and_says_so(w):
    jc.use_setting_reader(lambda name: (_ for _ in ()).throw(OSError("no registry")))
    pre, _ = setting_job(w, "dark_mode")
    assert pre == {"name": "dark_mode", "previous": None}
    e = last(w)
    assert e["undo"]["kind"] == "cannot_undo" and "could not be read" in e["undo"]["why"]


def test_a_setting_that_does_not_read_back_is_unconfirmed_and_a_lock_is_not_undoable(w):
    jc.use_setting_reader(lambda name: "off")
    body = {"op": "setting", "action": "dnd", "text": "on"}
    jc.after_job(body, {"ok": True, "state": "done", "setting": "do not disturb on", "verified": False}, jc.before_job(body), time.time())
    e = last(w)
    assert e["verified"] == "unconfirmed" and e["undo"] == {"kind": "setting", "name": "dnd", "previous": "off", "new": "on"}
    jc.after_job({"op": "setting", "action": "lock", "text": ""}, {"ok": True, "state": "done", "setting": "screen lock", "verified": False}, None, time.time())
    assert last(w)["undo"] is None


def test_the_do_not_disturb_key_is_the_one_the_setting_job_writes():
    assert NU.WinSettings.NOTIFY.endswith("PushNotifications")


# ----------------------------------------------------------------------------------------------------------- a catalogue task is not journaled twice
def test_a_catalogue_task_writes_a_line_per_effect_and_no_extra_line_for_itself(w):
    (w.docs / "t.txt").write_text("t")

    def task(step):
        fileop(w, "mkdir", "Documents", "Backup")
        fileop(w, "move", "Documents", "t.txt", "Documents/Backup")
        return {"ok": True, "facts": ["done"], "unverified": [], "expected_missing": []}
    tok = jc.enter_task("make a backup folder and move t.txt into it")
    try:
        jc.wrap_agent("simple_task", task)({"tool": "simple_task", "task": "mkdir", "args": {}})
    finally:
        jc.exit_task(tok)
    steps = list(reversed(w.j.steps()))
    assert [s["step_label"] for s in steps] == ["Create a folder in Documents", "Move a .txt file to Documents/Backup"]
    assert len({s["task_id"] for s in steps}) == 1 and steps[0]["goal_label"].startswith("make a backup folder")
    rep = undoer.run(w.j.undoable(10, steps[0]["task_id"]), w.node, w.j)              # "undo this task": newest first
    assert rep["ok"] and rep["undone"] == 2 and (w.docs / "t.txt").exists() and not (w.docs / "Backup").exists()


def test_a_catalogue_task_that_changed_nothing_or_failed_still_writes_its_own_line(w):
    jc.wrap_agent("simple_task", lambda s: {"ok": True, "facts": ["2 matches"], "unverified": [], "expected_missing": []})({"tool": "simple_task", "task": "find_file", "args": {}})
    assert last(w)["tool"] == "simple_task" and last(w)["class"] == "A" and last(w)["undo"] is None
    jc.wrap_agent("simple_task", lambda s: {"ok": False, "error": "no such item", "facts": [], "unverified": [], "expected_missing": ["trash x"]})(
        {"tool": "simple_task", "task": "trash", "args": {}})
    e = last(w)
    assert (e["class"], e["verified"], e["step_label"]) == ("C", "failed", "Delete") and "no such item" in e["result"]
    n = len(w.j.steps())
    def half(step):
        fileop(w, "mkdir", "Documents", "Half")
        return {"ok": False, "error": "the second step failed", "facts": [], "unverified": [], "expected_missing": ["move"]}
    jc.wrap_agent("simple_task", half)({"tool": "simple_task", "task": "mkdir", "args": {}})
    assert len(w.j.steps()) == n + 2, "the effect that happened AND the failure of the step are both visible"


def test_a_job_that_failed_or_was_held_writes_no_effect_line(w):
    n = len(w.j.steps())
    body = {"op": "file", "action": "trash", "target": "Documents", "path": "x.txt", "dest": "", "text": ""}
    jc.after_job(body, {"ok": False, "state": "waiting_for_permission", "trashed": "x.txt"}, None, time.time())
    jc.after_job(body, {"ok": False, "state": "done", "trashed": "x.txt"}, None, time.time())
    jc.after_job(dict(body, action="find"), {"ok": True, "state": "done", "matches": []}, None, time.time())
    jc.after_job({"op": "clipboard", "action": "set"}, {"ok": True, "state": "done"}, None, time.time())
    assert len(w.j.steps()) == n


def test_a_save_as_is_a_new_file_in_a_known_folder(w):
    (w.docs / "n.txt").write_text("typed")
    res = {"ok": True, "observed": "the file was found on disk after Save", "saved": "n.txt", "folder": "Documents", "file_ok": True}
    jc.wrap_agent("save_as", lambda s: res)({"tool": "save_as", "folder": "Documents", "name": "n.txt", "app": "notepad"})
    e = last(w)
    assert (e["class"], e["step_label"], e["target"]) == ("A", "Save a .txt file in Documents", "Documents") and e["undo"]["path"] == str(w.docs / "n.txt")
    assert undo_last(w)["ok"] and not (w.docs / "n.txt").exists()


# ------------------------------------------------------------------------------------------------------------------------------------ classes
@pytest.mark.parametrize("tool,step,res,want", [
    ("open_app", {"app": "notepad"}, {}, "A"),
    ("open_website", {"url": "https://example.com/a"}, {}, "A"),
    ("window", {"action": "minimize", "app": "notepad"}, {}, "A"),
    ("type_text", {"app": "notepad", "text": "hi"}, {}, "A"),
    ("type_text", {"app": "powershell", "text": "dir"}, {}, "B"),
    ("type_in_editor", {"app": "code", "text": "x"}, {"typed_chars": 1}, "A"),
    ("press_key", {"app": "windowsterminal", "key": "enter"}, {}, "B"),
    ("browser", {"action": "read"}, {}, "A"),
    ("browser", {"action": "click"}, {}, "B"),
    ("drive", {"app": "calc", "query": "2+2"}, {}, "B"),
    ("troubleshoot", {"app": "word"}, {}, "C"),
    ("type_text", {"app": "notepad", "text": "x"}, {"state": "waiting_for_permission", "approval_id": "ap1"}, "C"),
    ("something_new", {}, {}, "B"),
])
def test_classes_follow_the_kernels_effect_classes(tool, step, res, want):
    assert jc.classify(tool, step, res)[0] == want


def test_terminal_typing_and_unknown_tools_default_to_the_careful_class(w):
    jc.wrap_agent("type_text", lambda s: {"ok": True, "process": "powershell"})({"tool": "type_text", "app": "powershell", "text": "rm"})
    assert last(w)["class"] == "B" and "rm" not in json.dumps(last(w))
