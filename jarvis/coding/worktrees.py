"""Per-worker git worktrees with hashed, read-only tests, so a coding agent cannot "pass" by editing the tests.

    ws = WorkerSpace(repo, base_dir, protected_globs=("tests/**",))
    space = ws.create("worker-1"); ws.make_read_only(space)
    ... the worker edits files under space.path ...
    verdict = ws.verify(space)            # the guard; read-only bits are only a speed bump
    patch = ws.collect_patch(space)       # refuses unless verdict.ok

Rules enforced here:
- Protected files are hashed at create() into a manifest stored OUTSIDE the worktree (<base_dir>/.manifests/<id>.json).
- verify() reports modified / deleted / renamed / symlinked protected files, new files matching a protected glob, edits to
  `.git`, paths that resolve outside the worktree, and change sets over the size limits. Violations carry a kind and a
  relative path, never file contents.
- CI/hook/test-selection config (.github/, .githooks/, .pre-commit-config.yaml, conftest.py, pytest.ini, tox.ini,
  setup.cfg, pyproject.toml) is always protected: editing it can change which tests run.
- Every git call runs with an empty hooksPath, protocol.allow=never, no system config and no terminal prompts.
- Nothing follows a symlink out of base_dir; remove() refuses anything not under base_dir.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import tempfile
from dataclasses import dataclass, field

WORKER_ID_RE = re.compile(r"[a-z0-9][a-z0-9-]{0,40}")
DEFAULT_PROTECTED = (
    ".github/**", ".githooks/**", "**/.pre-commit-config.yaml", "**/conftest.py",
    "**/pytest.ini", "**/tox.ini", "**/setup.cfg", "**/pyproject.toml",
)


class WorktreeError(Exception):
    """A refused or failed operation."""


@dataclass(frozen=True)
class Space:
    path: str
    worker_id: str
    base_commit: str


@dataclass(frozen=True)
class Violation:
    kind: str   # modified deleted renamed symlink new_protected git_internal outside_worktree too_many_lines too_many_files manifest_missing
    path: str   # relative path ("" for whole-change-set limits)


@dataclass
class Verdict:
    ok: bool
    violations: list[Violation] = field(default_factory=list)
    stats: dict[str, int] = field(default_factory=dict)


def _glob_to_re(pattern: str) -> re.Pattern[str]:
    out, i = [], 0
    while i < len(pattern):
        if pattern.startswith("**/", i):
            out.append("(?:.*/)?")
            i += 3
        elif pattern.startswith("**", i):
            out.append(".*")
            i += 2
        elif pattern[i] == "*":
            out.append("[^/]*")
            i += 1
        elif pattern[i] == "?":
            out.append("[^/]")
            i += 1
        else:
            out.append(re.escape(pattern[i]))
            i += 1
    return re.compile("".join(out) + r"\Z")


def _sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _inside(child: str, parent: str) -> bool:
    child, parent = os.path.normcase(os.path.realpath(child)), os.path.normcase(os.path.realpath(parent))
    return child != parent and child.startswith(parent.rstrip(os.sep) + os.sep)


class WorkerSpace:
    def __init__(self, repo: str, base_dir: str, protected_globs: tuple[str, ...],
                 max_diff_lines: int = 2000, max_files: int = 50) -> None:
        self.repo = os.path.realpath(repo)
        os.makedirs(base_dir, exist_ok=True)
        self.base_dir = os.path.realpath(base_dir)
        self.protected_globs = tuple(protected_globs) + DEFAULT_PROTECTED
        self._res = [_glob_to_re(g) for g in self.protected_globs]
        self.max_diff_lines = max_diff_lines
        self.max_files = max_files
        self._hooks_dir = tempfile.mkdtemp(prefix="empty-hooks-")
        self._scratch = tempfile.mkdtemp(prefix="ws-scratch-")
        self.manifest_dir = os.path.join(self.base_dir, ".manifests")

    # -- helpers ----------------------------------------------------------------------------------------------
    def _git(self, cwd: str, *args: str, index: str | None = None) -> bytes:
        env = dict(os.environ, GIT_CONFIG_NOSYSTEM="1", GIT_TERMINAL_PROMPT="0")
        if index:
            env["GIT_INDEX_FILE"] = index
        cmd = ["git", "-c", f"core.hooksPath={self._hooks_dir}", "-c", "protocol.allow=never",
               "-c", "core.fsmonitor=false", *args]
        r = subprocess.run(cmd, cwd=cwd, env=env, capture_output=True, shell=False)
        if r.returncode != 0:
            raise WorktreeError(f"git {args[0]} failed: {r.stderr.decode('utf-8', 'replace').strip()[:300]}")
        return r.stdout

    def _protected(self, rel: str) -> bool:
        return any(r.match(rel) for r in self._res)

    def _manifest_path(self, worker_id: str) -> str:
        return os.path.join(self.manifest_dir, f"{worker_id}.json")

    def _scan(self, root: str) -> dict[str, str | None]:
        """Relative path -> sha256 for protected entries; None for a symlink. Never follows symlinks."""
        found: dict[str, str | None] = {}
        for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
            if dirpath == root and ".git" in dirnames:
                dirnames.remove(".git")
            entries = list(filenames) + [d for d in dirnames if os.path.islink(os.path.join(dirpath, d))]
            for name in entries:
                full = os.path.join(dirpath, name)
                rel = os.path.relpath(full, root).replace(os.sep, "/")
                if not self._protected(rel):
                    continue
                found[rel] = None if os.path.islink(full) else _sha256(full)
        return found

    def _checked_path(self, space: Space) -> str:
        p = os.path.abspath(space.path)
        if os.path.islink(p) or not _inside(p, self.base_dir) or os.path.dirname(os.path.realpath(p)) != self.base_dir:
            raise WorktreeError("space path is not a direct, non-symlink child of base_dir")
        return p

    # -- API --------------------------------------------------------------------------------------------------
    def create(self, worker_id: str, base_ref: str = "HEAD") -> Space:
        if not isinstance(worker_id, str) or not WORKER_ID_RE.fullmatch(worker_id):
            raise WorktreeError("bad worker id")
        if base_ref.startswith("-"):
            raise WorktreeError("bad base_ref")
        path = os.path.join(self.base_dir, worker_id)
        if os.path.lexists(path):
            raise WorktreeError("worktree path already exists")
        commit = self._git(self.repo, "rev-parse", "--verify", f"{base_ref}^{{commit}}").decode().strip()
        self._git(self.repo, "worktree", "add", "--detach", path, commit)
        files = self._scan(path)
        git_file = os.path.join(path, ".git")
        manifest = {"files": {k: v for k, v in files.items() if v},
                    "git_file": _sha256(git_file) if os.path.isfile(git_file) else None,
                    "base_commit": commit}
        os.makedirs(self.manifest_dir, exist_ok=True)
        mp = self._manifest_path(worker_id)
        with open(mp, "w", encoding="utf-8") as f:
            json.dump(manifest, f)
        try:
            os.chmod(mp, 0o600)
        except OSError:
            pass
        return Space(path=path, worker_id=worker_id, base_commit=commit)

    def manifest_path(self, space: Space) -> str:
        return self._manifest_path(space.worker_id)

    def make_read_only(self, space: Space) -> None:
        root = self._checked_path(space)
        for rel in self._scan(root):
            full = os.path.join(root, rel)
            if not os.path.islink(full) and os.path.isfile(full):
                os.chmod(full, stat.S_IREAD)

    def _changes(self, space: Space) -> list[tuple[int, str]]:
        """(changed lines, path) for everything differing from base, via a throwaway index; the real index is untouched."""
        index = os.path.join(self._scratch, f"{space.worker_id}.index")
        if os.path.exists(index):
            os.remove(index)
        try:
            self._git(space.path, "read-tree", space.base_commit, index=index)
            self._git(space.path, "add", "-A", "--", ".", index=index)
            raw = self._git(space.path, "diff", "--cached", "--numstat", "--no-renames", "-z", space.base_commit, index=index)
        finally:
            if os.path.exists(index):
                os.remove(index)
        out = []
        for item in raw.decode("utf-8", "surrogateescape").split("\0"):
            if not item:
                continue
            added, deleted, path = item.split("\t", 2)
            n = (int(added) + int(deleted)) if added.isdigit() and deleted.isdigit() else 0
            out.append((n, path))
        return out

    def verify(self, space: Space) -> Verdict:
        root = self._checked_path(space)
        v: list[Violation] = []
        try:
            with open(self._manifest_path(space.worker_id), encoding="utf-8") as f:
                manifest = json.load(f)
        except (OSError, ValueError):
            return Verdict(False, [Violation("manifest_missing", "")], {})
        want: dict[str, str] = manifest["files"]
        have = self._scan(root)
        changes = self._changes(Space(root, space.worker_id, manifest["base_commit"]))
        new_hashes = {h for p, h in have.items() if h and p not in want}
        for _, rel in changes:   # a protected file moved to an unprotected name shows up as a changed file with the same hash
            full = os.path.join(root, *rel.split("/"))
            if os.path.isfile(full) and not os.path.islink(full):
                new_hashes.add(_sha256(full))
        for rel, sha in want.items():
            if rel not in have:
                v.append(Violation("renamed" if sha in new_hashes else "deleted", rel))
            elif have[rel] is None:
                v.append(Violation("symlink", rel))
            elif have[rel] != sha:
                v.append(Violation("modified", rel))
        for rel in sorted(set(have) - set(want)):
            v.append(Violation("symlink" if have[rel] is None else "new_protected", rel))
        gitf = os.path.join(root, ".git")
        if os.path.islink(gitf) or not os.path.isfile(gitf) or _sha256(gitf) != manifest.get("git_file"):
            v.append(Violation("git_internal", ".git"))
        lines = sum(n for n, _ in changes)
        for _, rel in changes:
            parts = rel.split("/")
            if ".git" in (p.lower() for p in parts):
                v.append(Violation("git_internal", rel))
            elif not _inside(os.path.join(root, *parts), root):
                v.append(Violation("outside_worktree", rel))
        if lines > self.max_diff_lines:
            v.append(Violation("too_many_lines", ""))
        if len(changes) > self.max_files:
            v.append(Violation("too_many_files", ""))
        return Verdict(not v, v, {"changed_lines": lines, "changed_files": len(changes)})

    def collect_patch(self, space: Space) -> str:
        verdict = self.verify(space)
        if not verdict.ok:
            raise WorktreeError("refusing to collect patch: " + ", ".join(sorted({x.kind for x in verdict.violations})))
        index = os.path.join(self._scratch, f"{space.worker_id}.patch-index")
        try:
            self._git(space.path, "read-tree", space.base_commit, index=index)
            self._git(space.path, "add", "-A", "--", ".", index=index)
            raw = self._git(space.path, "diff", "--cached", "--binary", "--no-renames", space.base_commit, index=index)
        finally:
            if os.path.exists(index):
                os.remove(index)
        return raw.decode("utf-8", "replace")

    def remove(self, space: Space) -> None:
        path = self._checked_path(space)
        if os.path.isdir(path):
            for dirpath, _dirs, filenames in os.walk(path, followlinks=False):
                for name in filenames:
                    full = os.path.join(dirpath, name)
                    if not os.path.islink(full):
                        try:
                            os.chmod(full, stat.S_IWRITE | stat.S_IREAD)
                        except OSError:
                            pass
        try:
            self._git(self.repo, "worktree", "remove", "--force", path)
        except WorktreeError:
            if os.path.isdir(path) and not os.path.islink(path):
                shutil.rmtree(path, ignore_errors=True)
            self._git(self.repo, "worktree", "prune")
        try:
            os.remove(self._manifest_path(space.worker_id))
        except OSError:
            pass
