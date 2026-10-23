"""Tests for jarvis.coding.worktrees, using throwaway git repositories under tmp_path (no network, no remotes)."""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from jarvis.coding.worktrees import Space, WorkerSpace, WorktreeError

IDENT = ["-c", "user.name=t", "-c", "user.email=t@example.com"]


def git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", *IDENT, *args], cwd=cwd, check=True, capture_output=True)


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    r = tmp_path / "repo"
    (r / "tests").mkdir(parents=True)
    (r / "src").mkdir()
    (r / "tests" / "test_a.py").write_text("def test_a():\n    assert True\n")
    (r / "src" / "app.py").write_text("x = 1\n")
    (r / "conftest.py").write_text("# conf\n")
    (r / "pyproject.toml").write_text("[tool]\n")
    git(r, "init", "-q")
    git(r, "add", "-A")
    git(r, "commit", "-q", "-m", "init")
    return r


def make(repo: Path, tmp_path: Path, **kw) -> tuple[WorkerSpace, Space]:
    ws = WorkerSpace(str(repo), str(tmp_path / "wt"), ("tests/**",), state_dir=str(tmp_path / "state"), **kw)
    return ws, ws.create("w1")


def kinds(ws: WorkerSpace, sp: Space) -> set[str]:
    return {v.kind for v in ws.verify(sp).violations}


def test_clean_passes(repo, tmp_path):
    ws, sp = make(repo, tmp_path)
    assert ws.verify(sp).ok
    assert len(sp.base_commit) == 40


def test_modified_and_deleted(repo, tmp_path):
    ws, sp = make(repo, tmp_path)
    t = Path(sp.path) / "tests" / "test_a.py"
    t.write_text("def test_a():\n    pass\n")
    assert "modified" in kinds(ws, sp)
    t.unlink()
    assert "deleted" in kinds(ws, sp)


def test_rename(repo, tmp_path):
    ws, sp = make(repo, tmp_path)
    os.rename(Path(sp.path) / "tests" / "test_a.py", Path(sp.path) / "src" / "moved.py")
    assert "renamed" in kinds(ws, sp)


def test_new_protected_file(repo, tmp_path):
    ws, sp = make(repo, tmp_path)
    (Path(sp.path) / "tests" / "test_x.py").write_text("x\n")
    v = ws.verify(sp)
    assert not v.ok and ("new_protected", "tests/test_x.py") in [(x.kind, x.path) for x in v.violations]


@pytest.mark.parametrize("name", ["conftest.py", "pyproject.toml"])
def test_default_protected(repo, tmp_path, name):
    ws, sp = make(repo, tmp_path)
    with open(Path(sp.path) / name, "a") as f:
        f.write("# edit\n")
    assert "modified" in kinds(ws, sp)


def test_symlink_replacing_protected(repo, tmp_path):
    ws, sp = make(repo, tmp_path)
    t = Path(sp.path) / "tests" / "test_a.py"
    t.unlink()
    try:
        os.symlink(str(Path(sp.path) / "src" / "app.py"), str(t))
    except (OSError, NotImplementedError):
        pytest.skip("symlinks unavailable")
    assert "symlink" in kinds(ws, sp)


def test_limits(repo, tmp_path):
    ws, sp = make(repo, tmp_path, max_diff_lines=5)
    (Path(sp.path) / "src" / "big.py").write_text("a\n" * 10)
    assert "too_many_lines" in kinds(ws, sp)
    ws2 = WorkerSpace(str(repo), str(tmp_path / "wt2"), ("tests/**",), max_files=2, state_dir=str(tmp_path / "state2"))
    sp2 = ws2.create("w2")
    for i in range(3):
        (Path(sp2.path) / "src" / f"f{i}.py").write_text("a\n")
    assert "too_many_files" in kinds(ws2, sp2)


def test_git_file_tamper(repo, tmp_path):
    ws, sp = make(repo, tmp_path)
    with open(Path(sp.path) / ".git", "a") as f:
        f.write("\n")
    assert "git_internal" in kinds(ws, sp)


def test_manifest_outside_worktree(repo, tmp_path):
    ws, sp = make(repo, tmp_path)
    mp = Path(ws.manifest_path(sp)).resolve()
    assert mp.is_file()
    assert Path(sp.path).resolve() not in mp.parents


def test_read_only_blocks_write(repo, tmp_path):
    ws, sp = make(repo, tmp_path)
    ws.make_read_only(sp)
    target = Path(sp.path) / "tests" / "test_a.py"
    try:
        with open(target, "a") as f:
            f.write("")
        pytest.skip("platform ignores the read-only attribute (e.g. running as root)")
    except PermissionError:
        pass
    with pytest.raises(PermissionError):
        with open(target, "a") as f:
            f.write("x")
    assert ws.verify(sp).ok
    ws.remove(sp)
    assert not Path(sp.path).exists()


def test_collect_patch(repo, tmp_path):
    ws, sp = make(repo, tmp_path)
    ws.make_read_only(sp)
    (Path(sp.path) / "src" / "app.py").write_text("x = 2\n")
    (Path(sp.path) / "src" / "new.py").write_text("y = 1\n")
    patch = ws.collect_patch(sp)
    assert isinstance(patch, bytes) and b"+x = 2" in patch and b"src/new.py" in patch
    assert b"test_a.py" not in patch and b"conftest" not in patch
    status = subprocess.run(["git", "status", "--porcelain"], cwd=sp.path, capture_output=True, text=True).stdout
    assert "?? src/new.py" in status    # real index untouched: file still untracked


def test_collect_patch_refused_when_violating(repo, tmp_path):
    ws, sp = make(repo, tmp_path)
    (Path(sp.path) / "tests" / "test_a.py").write_text("")
    with pytest.raises(WorktreeError):
        ws.collect_patch(sp)


def test_hooks_do_not_run(repo, tmp_path):
    marker = tmp_path / "marker"
    for name in ("post-checkout", "pre-commit", "reference-transaction", "post-index-change"):
        h = repo / ".git" / "hooks" / name
        h.write_text(f"#!/bin/sh\necho ran > '{marker.as_posix()}'\n")
        h.chmod(0o755)
    ws, sp = make(repo, tmp_path)
    (Path(sp.path) / "src" / "n.py").write_text("n\n")
    ws.verify(sp)
    ws.collect_patch(sp)
    assert not marker.exists()


@pytest.mark.parametrize("bad", ["", "A", "-x", "a/b", "..", "a" * 42, "a b", "a.b"])
def test_bad_ids(repo, tmp_path, bad):
    ws = WorkerSpace(str(repo), str(tmp_path / "wt"), ())
    with pytest.raises(WorktreeError):
        ws.create(bad)


def test_existing_path_refused(repo, tmp_path):
    ws, _ = make(repo, tmp_path)
    with pytest.raises(WorktreeError):
        ws.create("w1")


def test_remove_refuses_outside(repo, tmp_path):
    ws, sp = make(repo, tmp_path)
    outside = tmp_path / "other"
    outside.mkdir()
    with pytest.raises(WorktreeError):
        ws.remove(Space(str(outside), "w1", sp.base_commit))
    with pytest.raises(WorktreeError):
        ws.remove(Space(ws.base_dir, "w1", sp.base_commit))
    assert outside.exists()
    ws.remove(sp)
    assert not Path(ws.manifest_path(sp)).exists()
