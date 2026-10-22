"""Review additions for FileOps: a directory junction (no admin rights needed on Windows) as the symlink-escape test, and the trash folder."""
import os
import subprocess
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))

from jarvis_node.fileops import FileOps, OutsideRoots, Protected  # noqa: E402


@pytest.fixture
def env(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    return FileOps([str(root)], str(tmp_path / "journal" / "ops.jsonl")), root, tmp_path


def make_link(link, target):
    """A directory link that needs no privilege: a junction on Windows, a symlink elsewhere. Skips if neither can be made."""
    if os.name == "nt":
        r = subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(target)], capture_output=True, text=True)
        if r.returncode != 0:
            pytest.skip("cannot create a junction here")
    else:
        try:
            os.symlink(str(target), str(link), target_is_directory=True)
        except OSError:
            pytest.skip("cannot create symlinks here")


def test_a_link_inside_the_root_that_points_outside_is_refused_for_every_operation(env):
    ops, root, tmp = env
    outside = tmp / "outside"
    outside.mkdir()
    (outside / "victim.txt").write_text("keep me", encoding="utf-8")
    make_link(root / "gate", outside)
    for call in (lambda: ops.create_file(str(root / "gate" / "new.txt"), "x"),
                 lambda: ops.delete(str(root / "gate" / "victim.txt")),
                 lambda: ops.move(str(root / "gate" / "victim.txt"), str(root / "taken.txt")),
                 lambda: ops.copy(str(root / "gate" / "victim.txt"), str(root / "copy.txt")),
                 lambda: ops.create_folder(str(root / "gate" / "sub"))):
        with pytest.raises(OutsideRoots):
            call()
    assert (outside / "victim.txt").read_text(encoding="utf-8") == "keep me" and not (outside / "new.txt").exists()


def test_the_trash_folder_cannot_be_deleted_or_moved(env):
    ops, root, _ = env
    (root / "a.txt").write_text("a", encoding="utf-8")
    ops.delete(str(root / "a.txt"))                                    # creates .jarvis-trash inside the root
    trash = root / ".jarvis-trash"
    assert trash.is_dir()
    with pytest.raises(Protected):
        ops.delete(str(trash))
    with pytest.raises(Protected):
        ops.move(str(trash), str(root / "elsewhere"))
    with pytest.raises(Protected):
        ops.rename(str(trash), "renamed")


def test_a_folder_that_holds_the_trash_cannot_be_deleted(tmp_path):
    parent = tmp_path / "parent"
    (parent / "child").mkdir(parents=True)
    ops = FileOps([str(parent)], str(tmp_path / "j.jsonl"), trash_dir=str(parent / "child" / "trash"))
    with pytest.raises(Protected):
        ops.delete(str(parent / "child"))
