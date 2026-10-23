"""Regression tests for the holes found in review of jarvis.coding.worktrees (cloud review, 2026-10-23, plus the laptop's own reproduction):
git runs commands named in its config, and the worker can write its gitdir, so verify() must catch a changed git configuration WITHOUT running git."""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from jarvis.coding.test_worktrees import git, kinds, make, repo  # noqa: F401  (fixtures and helpers)
from jarvis.coding.worktrees import WorkerSpace, WorktreeError

FILTER = "\n[filter \"x\"]\n\tclean = sh -c 'touch PWNED && cat'\n"


def gitdir_of(sp) -> Path:
    line = (Path(sp.path) / ".git").read_text().strip()
    return Path(line.split(":", 1)[1].strip())


def test_a_filter_driver_in_the_repo_config_is_a_violation_and_does_not_run(repo, tmp_path):
    ws, sp = make(repo, tmp_path)
    with open(repo / ".git" / "config", "a") as f:
        f.write(FILTER)
    (Path(sp.path) / ".gitattributes").write_text("*.py filter=x\n")
    (Path(sp.path) / "src" / "app.py").write_text("x = 2\n")
    verdict = ws.verify(sp)
    assert not verdict.ok and "git_config" in {v.kind for v in verdict.violations}
    assert not (Path(sp.path) / "PWNED").exists()                     # the worker's command never ran
    with pytest.raises(WorktreeError):
        ws.collect_patch(sp)
    assert not (Path(sp.path) / "PWNED").exists()


def test_no_git_command_runs_at_all_when_the_git_configuration_changed(repo, tmp_path, monkeypatch):
    ws, sp = make(repo, tmp_path)
    with open(repo / ".git" / "config", "a") as f:
        f.write("\n[core]\n\tfsmonitor = true\n")
    def boom(*a, **k):
        raise AssertionError("git was run although its configuration changed")
    monkeypatch.setattr(ws, "_git", boom)
    assert "git_config" in kinds(ws, sp)


@pytest.mark.parametrize("label,relpath,content", [
    ("worktree config", "config.worktree", FILTER),
    ("worktree info/attributes", "info/attributes", "*.py filter=x\n"),
])
def test_per_worktree_git_files_are_guarded(repo, tmp_path, label, relpath, content):
    ws, sp = make(repo, tmp_path)
    target = gitdir_of(sp) / relpath
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content)
    verdict = ws.verify(sp)
    assert any(v.kind == "git_config" and v.path == label for v in verdict.violations)
    assert not (Path(sp.path) / "PWNED").exists()


@pytest.mark.parametrize("label,relpath,content", [
    ("info/attributes", "info/attributes", "*.py filter=x\n"),
    ("info/exclude", "info/exclude", "src/\n"),                       # would hide the worker's changes from `git add -A`
    ("objects/info/alternates", "objects/info/alternates", "/somewhere/else\n"),
])
def test_shared_git_files_are_guarded(repo, tmp_path, label, relpath, content):
    ws, sp = make(repo, tmp_path)
    target = repo / ".git" / relpath
    target.parent.mkdir(parents=True, exist_ok=True)
    with open(target, "a") as f:
        f.write(content)
    assert any(v.kind == "git_config" and v.path == label for v in ws.verify(sp).violations)


def test_an_unchanged_git_configuration_does_not_trip_the_guard(repo, tmp_path):
    ws, sp = make(repo, tmp_path)
    (Path(sp.path) / "src" / "app.py").write_text("x = 2\n")
    assert ws.verify(sp).ok


def test_gitattributes_gitignore_gitmodules_are_protected(repo, tmp_path):
    ws, sp = make(repo, tmp_path)
    for name in (".gitattributes", ".gitignore", ".gitmodules", "src/.gitattributes"):
        (Path(sp.path) / name).write_text("# worker\n")
    got = {(v.kind, v.path) for v in ws.verify(sp).violations}
    for name in (".gitattributes", ".gitignore", ".gitmodules", "src/.gitattributes"):
        assert ("new_protected", name) in got


@pytest.mark.parametrize("name", ["package.json", "jest.config.js", "vitest.config.ts", "playwright.config.ts", ".mocharc.json",
                                  "noxfile.py", "Makefile", "web/package.json", "web/jest.config.cjs"])
def test_test_selection_config_outside_python_is_protected(repo, tmp_path, name):
    ws, sp = make(repo, tmp_path)
    p = Path(sp.path) / name
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text('{"scripts": {"test": "exit 0"}}\n')
    assert ("new_protected", name) in {(v.kind, v.path) for v in ws.verify(sp).violations}


def test_installed_dependencies_do_not_trip_the_protected_scan(repo, tmp_path):
    ws, sp = make(repo, tmp_path)
    nm = Path(sp.path) / "node_modules" / "left-pad"
    nm.mkdir(parents=True)
    (nm / "package.json").write_text("{}")
    assert not any(v.kind == "new_protected" for v in ws.verify(sp).violations)


def test_the_manifest_cannot_live_where_the_worker_can_write(repo, tmp_path):
    base = tmp_path / "wt"
    with pytest.raises(WorktreeError):
        WorkerSpace(str(repo), str(base), ("tests/**",), state_dir=str(base / ".manifests"))
    with pytest.raises(WorktreeError):
        WorkerSpace(str(repo), str(base), ("tests/**",), state_dir=str(base))
    ws, sp = make(repo, tmp_path)
    assert Path(ws.base_dir) not in Path(ws.manifest_path(sp)).resolve().parents


def test_a_forged_manifest_dropped_into_base_dir_changes_nothing(repo, tmp_path):
    ws, sp = make(repo, tmp_path)
    t = Path(sp.path) / "tests" / "test_a.py"
    t.write_text("def test_a():\n    pass\n")
    forged = Path(ws.base_dir) / ".manifests"
    forged.mkdir(exist_ok=True)
    (forged / "w1.json").write_text('{"files": {}, "git_file": null, "base_commit": "%s"}' % sp.base_commit)
    assert "modified" in kinds(ws, sp)


def test_collect_patch_returns_bytes_and_keeps_non_utf8_text_intact(repo, tmp_path):
    ws, sp = make(repo, tmp_path)
    (Path(sp.path) / "src" / "latin.txt").write_bytes(b"caf\xe9\n")
    patch = ws.collect_patch(sp)
    assert isinstance(patch, bytes) and b"caf\xe9" in patch


def test_close_removes_the_temporary_folders(repo, tmp_path):
    ws = WorkerSpace(str(repo), str(tmp_path / "wt"), (), state_dir=str(tmp_path / "state"))
    hooks, scratch = ws._hooks_dir, ws._scratch
    assert os.path.isdir(hooks) and os.path.isdir(scratch)
    ws.close()
    assert not os.path.exists(hooks) and not os.path.exists(scratch)
    with WorkerSpace(str(repo), str(tmp_path / "wt2"), (), state_dir=str(tmp_path / "state2")) as w2:
        h2 = w2._hooks_dir
    assert not os.path.exists(h2)


def test_ordinary_work_still_passes_verification(repo, tmp_path):
    ws, sp = make(repo, tmp_path)
    (Path(sp.path) / "src" / "app.py").write_text("x = 3\n")
    (Path(sp.path) / "src" / "extra.py").write_text("y = 1\n")
    v = ws.verify(sp)
    assert v.ok and v.stats["changed_files"] == 2
