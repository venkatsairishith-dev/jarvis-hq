"""Every undo recipe on a fake PC: a real temp folder as the owner's folders, a fake Recycle Bin with the real `$I`/`$R` layout, fake windows and settings.
Each one is checked by its effect, and each refuses when the thing changed since."""
import os
import shutil
import sys
import time

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))

from jarvis_node import undo as U  # noqa: E402
from jarvis_node.journal import stamp  # noqa: E402


from fakepc import FakeBinPc  # noqa: E402


@pytest.fixture
def pc(tmp_path):
    return FakeBinPc(tmp_path)


def job(op, target, path="", expect=""):
    return {"agent": "owner", "op": op, "target": target, "path": path, "expect": expect}


# ------------------------------------------------------------------------------------------------------------ a NEW file -> the bin
def test_a_new_file_goes_to_the_bin_and_the_effect_is_checked(pc):
    p = pc.file("Documents/notes.txt")
    rec = U.recipe_recycle_new(p)
    r = U.run(job("recycle", rec["path"], expect=rec["stamp"]), pc.ports())
    assert r["ok"] and r["verified"] and r["in_bin"] and not os.path.exists(p)
    assert U.describe(rec) == "Move notes.txt to the Recycle Bin"


def test_a_new_file_that_was_edited_since_is_left_alone(pc):
    p = pc.file("Documents/notes.txt")
    rec = U.recipe_recycle_new(p)
    with open(p, "a") as fh:
        fh.write(" and the owner kept typing")
    r = U.run(job("recycle", p, expect=rec["stamp"]), pc.ports())
    assert not r["ok"] and r["refused"] == "changed" and os.path.exists(p)
    assert "changed" in r["error"] and "notes" not in r["error"], "the sentence names no file"


def test_if_the_shell_did_nothing_the_undo_says_it_failed(pc):
    p = pc.file("a.txt")
    pc.fail_recycle = True
    r = U.run(job("recycle", p, expect=U.recipe_recycle_new(p)["stamp"]), pc.ports())
    assert not r["ok"] and r["refused"] == "failed" and os.path.exists(p)


def test_a_file_that_is_gone_is_reported_missing(pc):
    r = U.run(job("recycle", str(pc.home / "gone.txt")), pc.ports())
    assert r["refused"] == "missing"


# ------------------------------------------------------------------------------------------------- delete to the bin -> restore
@pytest.mark.parametrize("n", [1, 2])        # $I version 1 and version 2
def test_a_file_in_the_bin_is_restored_to_its_original_path_through_the_shell(pc, n):
    pc.n = n - 1
    p = pc.file("Documents/report.pdf", "data")
    pc.recycle(p)
    assert not os.path.exists(p)
    r = U.run(job("restore_bin", p, expect="4"), pc.ports())
    assert r["ok"] and r["verified"] and not r["numbered"] and open(p).read() == "data"
    assert pc.exact_calls == [os.path.realpath(p)], "the original path goes through the shell's own restore"
    assert not os.listdir(pc.binroot), "the bin entry and its record are gone"


def test_if_the_original_path_is_taken_it_comes_back_with_a_numbered_name_and_nothing_is_overwritten(pc):
    p = pc.file("Documents/report.pdf", "old")
    pc.recycle(p)
    pc.file("Documents/report.pdf", "NEW WORK")
    r = U.run(job("restore_bin", p), pc.ports())
    assert r["ok"] and r["numbered"] and r["name"] == "report (1).pdf"
    assert open(p).read() == "NEW WORK" and open(os.path.join(os.path.dirname(p), "report (1).pdf")).read() == "old"
    assert pc.exact_calls == []
    pc.recycle(os.path.join(os.path.dirname(p), "report (1).pdf"))
    pc.file("Documents/report (1).pdf", "x")
    r = U.run(job("restore_bin", os.path.join(os.path.dirname(p), "report (1).pdf")), pc.ports())
    assert r["name"] == "report (1) (1).pdf" or r["name"].startswith("report (1) ("), "the number goes after the full name"


def test_the_newest_matching_bin_item_is_the_one_restored_and_a_wrong_size_is_not_taken(pc):
    p = pc.file("a.txt", "one")
    pc.recycle(p)
    time.sleep(0.01)
    pc.file("a.txt", "second!")
    pc.recycle(p)
    assert U.run(job("restore_bin", p, expect="99"), pc.ports())["refused"] == "not_in_bin"
    r = U.run(job("restore_bin", p), pc.ports())
    assert r["ok"] and open(p).read() == "second!"


def test_something_not_in_the_bin_any_more_is_said_plainly(pc):
    assert U.run(job("restore_bin", str(pc.home / "never.txt")), pc.ports())["refused"] == "not_in_bin"


def test_the_bin_item_parser_reads_both_versions_and_refuses_junk():
    for v in (1, 2):
        meta = U.parse_i(U.make_i("C:\\Users\\me\\Documents\\é file.txt", 123, 1_800_000_000.0, v))
        assert meta["path"] == "C:\\Users\\me\\Documents\\é file.txt" and meta["size"] == 123 and abs(meta["deleted_at"] - 1_800_000_000.0) < 1
    assert U.parse_i(b"") is None and U.parse_i(b"\x09" + b"\0" * 40) is None


# ---------------------------------------------------------------------------------------------------------------- move and rename
def test_a_move_is_reversed_when_the_destination_is_unchanged(pc):
    src = pc.file("Downloads/a.txt", "x")
    dst = str(pc.home / "Documents" / "a.txt")
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    shutil.move(src, dst)
    rec = U.recipe_move_back(src, dst)
    r = U.run(job("move", dst, src, rec["stamp"]), pc.ports())
    assert r["ok"] and os.path.exists(src) and not os.path.exists(dst)


def test_a_move_is_not_reversed_when_the_destination_changed(pc):
    src = pc.file("Downloads/a.txt", "x")
    dst = str(pc.home / "Documents" / "a.txt")
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    shutil.move(src, dst)
    rec = U.recipe_move_back(src, dst)
    with open(dst, "a") as fh:
        fh.write("edited")
    r = U.run(job("move", dst, src, rec["stamp"]), pc.ports())
    assert r["refused"] == "changed" and os.path.exists(dst) and not os.path.exists(src)


def test_a_move_is_not_reversed_onto_something_else_and_a_rename_works(pc):
    a = pc.file("a.txt", "x")
    b = str(pc.home / "b.txt")
    os.rename(a, b)
    rec = U.recipe_move_back(a, b)
    pc.file("a.txt", "someone else's")
    assert U.run(job("move", b, a, rec["stamp"]), pc.ports())["refused"] == "taken"
    os.remove(a)
    assert U.run(job("move", b, a, rec["stamp"]), pc.ports())["ok"] and os.path.exists(a)


def test_a_copy_is_undone_as_a_new_file(pc):
    a = pc.file("a.txt", "x")
    c = str(pc.home / "a - copy.txt")
    shutil.copy2(a, c)
    rec = U.recipe_recycle_new(c)
    assert U.run(job("recycle", c, expect=rec["stamp"]), pc.ports())["ok"] and os.path.exists(a) and not os.path.exists(c)


# ------------------------------------------------------------------------------------------------------------------- windows
def test_an_app_jarvis_opened_is_closed_and_the_window_is_gone(pc):
    pc.windows.add(10, "Calculator")
    r = U.run(job("close_window", "CalculatorApp", "77", "1000"), pc.ports())
    assert r["ok"] and pc.windows.closes == [10] and not pc.windows.alive(10)


def test_a_window_with_unsaved_work_is_not_closed(pc):
    pc.windows.add(11, "*Untitled - Notepad", "notepad", 5)
    r = U.run(job("close_window", "notepad", "5", "1000"), pc.ports())
    assert r["refused"] == "unsaved" and pc.windows.closes == [] and pc.windows.alive(11)


def test_an_app_that_asks_to_save_is_reported_still_open(pc):
    pc.windows.add(12, "Doc", "wordpad", 6)
    pc.windows.refuse_close = True
    r = U.run(job("close_window", "wordpad", "6", "1000"), pc.ports())
    assert r["refused"] == "still_open" and "save" in r["error"]


def test_an_already_closed_window_is_not_an_error(pc):
    r = U.run(job("close_window", "calculatorapp", "77", "1000"), pc.ports())
    assert r["ok"] and r["note"] == "already closed"


def test_another_window_of_the_same_app_is_not_touched(pc):
    pc.windows.add(10, "Calculator", "calculatorapp", 77)
    pc.windows.add(20, "Calculator", "calculatorapp", 88)
    U.run(job("close_window", "calculatorapp", "77", "1000"), pc.ports())
    assert pc.windows.closes == [10] and pc.windows.alive(20), "only the pid JARVIS opened"


def test_a_window_whose_title_shows_a_bank_is_refused(pc):
    pc.windows.add(13, "ICICI Bank - Chrome", "chrome", 9)
    assert U.run(job("close_window", "chrome", "9", "1000"), pc.ports())["refused"] == "protected"


# ------------------------------------------------------------------------------------------------------------------- settings
@pytest.mark.parametrize("name,previous,new", [("dark_mode", "light", "dark"), ("volume", "55", "30"), ("brightness", "80", "60"),
                                               ("wallpaper", "C:\\w\\old.jpg", "C:\\w\\a.jpg"), ("dnd", "on", "off")])
def test_a_setting_goes_back_to_the_recorded_value_and_is_read_back(pc, name, previous, new):
    pc.settings.v[name] = new
    r = U.run(job("setting", name, previous, new), pc.ports())
    assert r["ok"] and pc.settings.v[name] == previous


def test_a_setting_without_a_recorded_value_or_that_does_not_stick_is_refused(pc):
    assert U.run(job("setting", "volume", "", "30"), pc.ports())["refused"] == "no_value"
    assert U.run(job("setting", "nonsense", "1", "2"), pc.ports())["refused"] == "unsupported"
    pc.settings.broken = True
    assert U.run(job("setting", "volume", "10", "30"), pc.ports())["refused"] == "failed"


# --------------------------------------------------------------------------------------------------------------- the node's own rules
def test_paths_outside_the_owners_folders_or_private_are_refused_again_on_the_node(pc, tmp_path):
    outside = tmp_path / "elsewhere.txt"
    outside.write_text("x")
    assert U.run(job("recycle", str(outside)), pc.ports())["refused"] == "outside"
    for rel in ("AppData/Local/x.txt", "Documents/.env", "Documents/jarvis-secrets/k.json", "Documents/passwords.kdbx", "Documents/ICICI Bank statement.pdf",
                "Documents/id_rsa"):
        p = pc.file(rel)
        assert U.run(job("recycle", p), pc.ports())["refused"] == "protected", rel
        assert os.path.exists(p)
    assert U.run(job("recycle", str(pc.home)), pc.ports())["refused"] == "outside", "a root itself is never touched"
    p = pc.file("journal/x.txt")
    assert U.run(job("recycle", p), pc.ports(protected=[str(pc.home / "journal")]))["refused"] == "protected"


def test_links_are_refused(pc, tmp_path):
    target = pc.file("real.txt")
    link = pc.home / "link.txt"
    try:
        os.symlink(target, link)
    except (OSError, NotImplementedError):
        pytest.skip("no symlinks here")
    assert U.run(job("recycle", str(link)), pc.ports())["refused"] == "link"
    assert os.path.exists(target)


def test_an_unknown_op_and_a_missing_windows_port_are_refused_not_crashed(pc):
    assert U.run(job("format_disk", "C:\\"), pc.ports())["refused"] == "unsupported"
    ports = U.Ports([str(pc.home)])
    assert U.run(job("recycle", pc.file("a.txt")), ports)["refused"] == "unsupported"
    assert U.run(job("close_window", "x", "1", "1"), ports)["refused"] == "unsupported"
    assert U.run(job("setting", "volume", "1"), ports)["refused"] == "unsupported"


def test_describe_is_for_the_panel_and_word_is_for_a_model():
    r = U.recipe_cannot("typed text cannot be taken back")
    assert U.describe(r).startswith("Cannot be undone") and U.word(r) == "cannot_undo"
    assert U.word({"kind": "recycle_new", "path": "C:\\secret\\thing.txt"}) == "recycle_new", "a model gets the kind, never the path"
    assert U.describe(None) == "" and U.word(None) == ""
    assert U.numbered_name("C:\\a\\b.txt", lambda p: p == "C:\\a\\b (1).txt") == "C:\\a\\b (2).txt"
    assert U.recipe_recycle_new("/no/such/file") is None and U.recipe_move_back("/a", "/no/such") is None
