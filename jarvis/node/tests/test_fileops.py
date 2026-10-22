"""FileOps: every operation and its undo, the 20-operation undo drill, refusals, crash recovery. tmp_path only."""
import hashlib
import json
import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))

from jarvis_node import fileops as fo  # noqa: E402
from jarvis_node.fileops import FileOps  # noqa: E402


def sha(p):
    with open(p, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def snapshot(root):
    out = {}
    for base, dirs, files in os.walk(root):
        for d in dirs:
            out[os.path.relpath(os.path.join(base, d), root)] = "<dir>"
        for f in files:
            full = os.path.join(base, f)
            out[os.path.relpath(full, root)] = sha(full)
    return out


@pytest.fixture
def env(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    ops = FileOps([str(root)], str(tmp_path / "journal" / "ops.jsonl"), protected=("vault", ".ssh", ".env"))
    return ops, root, tmp_path


def test_create_folder_and_undo(env):
    ops, root, _ = env
    r = ops.create_folder(str(root / "a" / "b"))
    assert r["ok"] and r["op"] == "create_folder" and (root / "a" / "b").is_dir()
    again = ops.create_folder(str(root / "a" / "b"))
    assert again["op"] == "noop"
    ops.undo(again["op_id"])
    assert (root / "a" / "b").is_dir()
    ops.undo(r["op_id"])
    assert not (root / "a").exists()
    with pytest.raises(fo.AlreadyUndone):
        ops.undo(r["op_id"])


def test_create_folder_refuses_file(env):
    ops, root, _ = env
    (root / "f").write_text("x")
    with pytest.raises(fo.FileExists):
        ops.create_folder(str(root / "f"))


def test_create_file_and_undo(env):
    ops, root, _ = env
    r = ops.create_file(str(root / "n.txt"), "hello")
    assert sha(root / "n.txt") == hashlib.sha256(b"hello").hexdigest()
    assert not [p for p in os.listdir(root) if p.endswith(".tmp")]
    with pytest.raises(fo.FileExists):
        ops.create_file(str(root / "n.txt"), "other")
    assert (root / "n.txt").read_text() == "hello"
    ops.undo(r["op_id"])
    assert not (root / "n.txt").exists()


def test_edited_file_not_removed(env):
    ops, root, _ = env
    r = ops.create_file(str(root / "n.txt"), "hello")
    (root / "n.txt").write_text("edited")
    with pytest.raises(fo.Modified):
        ops.undo(r["op_id"])
    assert (root / "n.txt").read_text() == "edited"
    assert not any(h["status"] == "undone" for h in ops.history())
    c = ops.copy(str(root / "n.txt"), str(root / "c.txt"))
    (root / "c.txt").write_text("changed")
    with pytest.raises(fo.Modified):
        ops.undo(c["op_id"])
    assert (root / "c.txt").exists()


def test_move_rename_undo(env):
    ops, root, _ = env
    (root / "d").mkdir()
    (root / "a.txt").write_text("data")
    before = snapshot(root)
    h = sha(root / "a.txt")
    m = ops.move(str(root / "a.txt"), str(root / "d" / "a.txt"))
    assert not (root / "a.txt").exists() and sha(root / "d" / "a.txt") == h
    rn = ops.rename(str(root / "d" / "a.txt"), "b.txt")
    assert sha(root / "d" / "b.txt") == h
    ops.undo(rn["op_id"])
    ops.undo(m["op_id"])
    assert snapshot(root) == before
    (root / "x").write_text("1")
    with pytest.raises(fo.FileExists):
        ops.move(str(root / "a.txt"), str(root / "x"))
    for bad in ("a/b", "a\\b", "..", ""):
        with pytest.raises(ValueError):
            ops.rename(str(root / "a.txt"), bad)


def test_move_conflict_on_undo(env):
    ops, root, _ = env
    (root / "a.txt").write_text("1")
    m = ops.move(str(root / "a.txt"), str(root / "b.txt"))
    (root / "a.txt").write_text("someone new")
    with pytest.raises(fo.Conflict):
        ops.undo(m["op_id"])
    assert (root / "b.txt").exists() and (root / "a.txt").read_text() == "someone new"


def test_move_cross_volume_fallback(env, monkeypatch):
    ops, root, _ = env
    (root / "dir").mkdir()
    (root / "dir" / "f.txt").write_text("payload")
    (root / "dest").mkdir()
    before = snapshot(root)

    def boom(a, b):
        raise OSError("cross-device")
    monkeypatch.setattr(os, "rename", boom)
    m = ops.move(str(root / "dir"), str(root / "dest" / "dir"))
    assert not (root / "dir").exists() and (root / "dest" / "dir" / "f.txt").read_text() == "payload"
    ops.undo(m["op_id"])
    assert snapshot(root) == before


def test_copy_file_and_folder_undo(env):
    ops, root, _ = env
    (root / "src").mkdir()
    (root / "src" / "a.txt").write_text("A")
    (root / "src" / "sub").mkdir()
    (root / "src" / "sub" / "b.txt").write_text("B")
    before = snapshot(root)
    c = ops.copy(str(root / "src"), str(root / "dup"))
    assert snapshot(root / "dup") == snapshot(root / "src")
    with pytest.raises(fo.FileExists):
        ops.copy(str(root / "src"), str(root / "dup"))
    f = ops.copy(str(root / "src" / "a.txt"), str(root / "a2.txt"))
    assert sha(root / "a2.txt") == sha(root / "src" / "a.txt")
    ops.undo(f["op_id"])
    ops.undo(c["op_id"])
    assert snapshot(root) == before


def test_delete_goes_to_trash_and_restores(env):
    ops, root, _ = env
    (root / "f.txt").write_text("keep me")
    h = sha(root / "f.txt")
    before = snapshot(root)
    r = ops.delete(str(root / "f.txt"))
    assert not (root / "f.txt").exists()
    assert r["dst"].startswith(str(root / ".jarvis-trash")) and sha(r["dst"]) == h
    assert os.path.basename(r["dst"]) == "f.txt" and r["op_id"] in r["dst"]
    ops.undo(r["op_id"])
    assert sha(root / "f.txt") == h
    assert snapshot(root) == before


def test_journal_shape_and_no_contents(env):
    ops, root, tmp = env
    secret_text = "TOP-SECRET-CONTENT-12345"
    ops.create_file(str(root / "n.txt"), secret_text)
    ops.copy(str(root / "n.txt"), str(root / "m.txt"))
    ops.delete(str(root / "m.txt"))
    raw = open(ops.journal_path, encoding="utf-8").read()
    assert secret_text not in raw
    lines = [json.loads(x) for x in raw.splitlines()]
    assert len(lines) == 6
    for ln in lines:
        assert set(ln) == {"op_id", "t", "op", "src", "dst", "sha256", "size", "status"}
    assert [ln["status"] for ln in lines[:2]] == ["started", "done"]
    assert lines[0]["op_id"] == lines[1]["op_id"]
    hist = ops.history(limit=2)
    assert len(hist) == 2 and hist[0]["op"] == "delete" and hist[0]["status"] == "done"


def test_undo_last(env):
    ops, root, _ = env
    ops.create_file(str(root / "1.txt"), "1")
    ops.create_file(str(root / "2.txt"), "2")
    ops.create_file(str(root / "3.txt"), "3")
    res = ops.undo_last(2)
    assert len(res) == 2
    assert (root / "1.txt").exists() and not (root / "2.txt").exists() and not (root / "3.txt").exists()
    ops.undo_last()
    assert not (root / "1.txt").exists()


def test_refusals(env):
    ops, root, tmp = env
    (tmp / "outside.txt").write_text("o")
    (root / "ok.txt").write_text("ok")
    with pytest.raises(fo.OutsideRoots):
        ops.delete(str(root / ".." / "outside.txt"))
    with pytest.raises(fo.OutsideRoots):
        ops.create_file(str(root / ".." / "new.txt"), "x")
    with pytest.raises(fo.OutsideRoots):
        ops.move(str(root / "ok.txt"), str(tmp / "away.txt"))
    for name in ("vault", ".ssh", ".env", "VAULT"):
        with pytest.raises(fo.Protected):
            ops.create_folder(str(root / name))
    with pytest.raises(fo.Protected):
        ops.create_file(str(root / ".ENV"), "k=v")
    with pytest.raises(fo.Protected):
        ops.move(str(root / "ok.txt"), str(root / "vault" / "ok.txt"))
    with pytest.raises(fo.Protected):
        ops.create_file(str(root / "id_rsa"), "k")        # kernel SECRET_PATTERNS
    with pytest.raises(fo.Protected):
        ops.delete(str(root))
    with pytest.raises(fo.Protected):
        ops.move(str(root), str(root / "inner"))
    with pytest.raises(fo.Protected):
        ops.rename(str(root), "other")
    assert (tmp / "outside.txt").exists() and not os.path.exists(ops.journal_path)


def test_drive_root(tmp_path):
    anchor = os.path.splitdrive(str(tmp_path))[0] + os.sep if os.name == "nt" else "/"
    ops = FileOps([anchor], str(tmp_path / "j.jsonl"))
    with pytest.raises(fo.Protected):
        ops.delete(anchor)
    with pytest.raises(fo.Protected):
        ops.move(anchor, str(tmp_path / "x"))
    only = FileOps([str(tmp_path / "r")], str(tmp_path / "j2.jsonl"))
    with pytest.raises(fo.OutsideRoots):
        only.delete(anchor)


def test_symlink_escape(env):
    ops, root, tmp = env
    outside = tmp / "elsewhere"
    outside.mkdir()
    (outside / "f.txt").write_text("x")
    try:
        os.symlink(str(outside), str(root / "link"), target_is_directory=True)
    except (OSError, NotImplementedError, AttributeError):
        pytest.skip("cannot create symlinks here")
    with pytest.raises(fo.OutsideRoots):
        ops.delete(str(root / "link" / "f.txt"))
    with pytest.raises(fo.OutsideRoots):
        ops.create_file(str(root / "link" / "new.txt"), "x")
    assert (outside / "f.txt").exists()


def test_crash_pending_is_undone(env):
    ops, root, _ = env
    (root / "a.txt").write_text("data")
    (root / "d").mkdir()
    before = snapshot(root)
    h = sha(root / "a.txt")
    # crash 1: 'started' written, nothing moved
    ops._log("crash-1", "move", str(root / "a.txt"), str(root / "d" / "a.txt"), h, 4, "started")
    # crash 2: the file moved but no 'done' line
    os.rename(root / "a.txt", root / "d" / "a.txt")
    p = {x["op_id"] for x in ops.pending()}
    assert p == {"crash-1"}
    ops.undo("crash-1")
    assert snapshot(root) == before and ops.pending() == []
    # crash 3: create_file whose replace happened
    data = b"zz"
    ops._log("crash-2", "create_file", None, str(root / "z.txt"), hashlib.sha256(data).hexdigest(), 2, "started")
    (root / "z.txt").write_bytes(data)
    assert [x["op_id"] for x in ops.pending()] == ["crash-2"]
    ops.undo("crash-2")
    assert snapshot(root) == before


def test_twenty_undo_drills(env):
    ops, root, tmp = env
    for i in range(20):
        d = root / f"d{i % 4}" / f"s{i % 3}"
        d.mkdir(parents=True, exist_ok=True)
        (d / f"file{i}.txt").write_text(f"content {i}\n" * (i + 1))
    original = snapshot(root)
    R = lambda *p: str(root.joinpath(*p))  # noqa: E731
    script = [
        lambda: ops.create_folder(R("new", "deep", "er")),
        lambda: ops.create_file(R("new", "note.txt"), "note"),
        lambda: ops.move(R("d0", "s0", "file0.txt"), R("new", "deep", "file0.txt")),
        lambda: ops.rename(R("d1", "s1", "file1.txt"), "renamed1.txt"),
        lambda: ops.copy(R("d2", "s2", "file2.txt"), R("new", "copy2.txt")),
        lambda: ops.delete(R("d3", "s0", "file3.txt")),
        lambda: ops.copy(R("d0"), R("d0_backup")),
        lambda: ops.delete(R("d0_backup", "s1")),
        lambda: ops.rename(R("d2"), "d2_renamed"),
        lambda: ops.move(R("new", "note.txt"), R("d2_renamed", "note.txt")),
        lambda: ops.create_file(R("d1", "extra.txt"), "extra"),
        lambda: ops.delete(R("d1", "extra.txt")),
        lambda: ops.create_folder(R("d3", "empty")),
        lambda: ops.move(R("d3", "empty"), R("new", "empty")),
        lambda: ops.copy(R("new", "copy2.txt"), R("new", "deep", "er", "copy2b.txt")),
        lambda: ops.rename(R("new", "deep", "er", "copy2b.txt"), "final.txt"),
        lambda: ops.delete(R("new")),
        lambda: ops.create_file(R("fresh.txt"), "fresh"),
        lambda: ops.rename(R("fresh.txt"), "fresher.txt"),
        lambda: ops.delete(R("fresher.txt")),
    ]
    assert len(script) == 20
    ids = [step()["op_id"] for step in script]
    assert snapshot(root) != original
    for op_id in reversed(ids):
        ops.undo(op_id)
    assert snapshot(root) == original
    assert ops.pending() == []
    assert not (root / ".jarvis-trash").exists()
