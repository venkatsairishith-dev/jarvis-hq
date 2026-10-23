"""Per-worker git worktrees with hashed, read-only tests, so a coding agent cannot "pass" by editing the tests.

    ws = WorkerSpace(repo, base_dir, protected_globs=("tests/**",), state_dir=<a folder only the kernel can write>)
    space = ws.create("worker-1"); ws.make_read_only(space)
    ... the worker edits files under space.path; then EVERY process of the worker is stopped (kill its process group) ...
    verdict = ws.verify(space, worker_stopped=True)         # the guard; read-only bits are only a speed bump
    patch = ws.collect_patch(space, worker_stopped=True)    # bytes; refuses unless verdict.ok

verify() and collect_patch() REFUSE to run unless the caller asserts the worker has exited (worker_stopped=True, or a callable that returns
True once the worker's process group is gone): git configuration is checked in Python and git is run afterwards, and a still-running worker
could change that configuration in between. The stronger layer stays the kernel's own fixed test run on the patch in a fresh clone.

Rules enforced here:
- Protected files are hashed at create() into a manifest stored in `state_dir`, which must NOT be inside base_dir (the worker may write
  base_dir). Put it where only the kernel can write; the default is a per-user temp folder outside base_dir.
- GIT ITSELF IS PART OF THE ATTACK SURFACE. A worker needs write access to its own gitdir, and git runs commands named in config
  (filter drivers, diff.external, textconv, core.fsmonitor, ...). So at create() the files that configure git for this worktree are hashed too
  (the repository config, the worktree config, info/attributes, info/exclude, objects/info/alternates, commondir/gitdir, and the worktree's `.git`
  file), and verify() compares them in plain Python BEFORE any git command runs. If any changed, verify() returns a `git_config` violation and
  runs no git at all, so nothing the worker planted can execute. Git is also run with no global/system config, no replace objects,
  no external diff and no textconv, as a second layer.
- verify() reports modified / deleted / renamed / symlinked protected files, new files matching a protected glob, edits to `.git`, changed git
  configuration, paths that resolve outside the worktree, and change sets over the size limits. Violations carry a kind and a relative path,
  never file contents.
- Always protected (editing them can change which tests run, or make git run something): CI/hook files, pytest/tox/setup/pyproject config,
  conftest.py, .gitattributes/.gitignore/.gitmodules, package.json, jest/vitest/playwright/mocha/karma configs, noxfile.py, Makefile and friends.
  A worker therefore cannot add a dependency through package.json; that is a deliberate trade-off. The stronger design (the push gate's) is that
  the kernel runs its own fixed test command on the collected patch applied to a fresh clone.
- Every git call runs with an empty hooksPath, protocol.allow=never and no terminal prompts. Nothing follows a symlink out of base_dir;
  remove() refuses anything not under base_dir.
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
import weakref
from collections.abc import Callable
from dataclasses import dataclass, field

WORKER_ID_RE = re.compile(r"[a-z0-9][a-z0-9-]{0,40}")
DEFAULT_PROTECTED = (
    ".github/**", ".githooks/**", "**/.pre-commit-config.yaml", "**/conftest.py",
    "**/pytest.ini", "**/tox.ini", "**/setup.cfg", "**/pyproject.toml",
    # git reads these to decide what to run and what to ignore
    "**/.gitattributes", "**/.gitignore", "**/.gitmodules",
    # what "test" means outside Python
    "**/package.json", "**/jest.config.*", "**/vitest.config.*", "**/playwright.config.*", "**/.mocharc.*", "**/karma.conf.*",
    "**/noxfile.py", "**/Makefile", "**/makefile", "**/GNUmakefile", "**/Justfile", "**/justfile",
)
SKIP_DIRS = {"node_modules", ".venv", "venv", "__pycache__", ".tox", ".nox"}     # not walked when looking for protected files


class WorktreeError(Exception):
    """A refused or failed operation."""


@dataclass(frozen=True)
class Space:
    path: str
    worker_id: str
    base_commit: str


@dataclass(frozen=True)
class Violation:
    kind: str   # modified deleted renamed symlink new_protected git_internal git_config outside_worktree too_many_lines too_many_files manifest_missing
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


def _sha_or_absent(path: str) -> str:
    """sha256 of a regular file, 'absent' when it does not exist, 'not-a-file' for anything else (a link or folder where a file belongs)."""
    if not os.path.lexists(path):
        return "absent"
    if os.path.islink(path) or not os.path.isfile(path):
        return "not-a-file"
    return _sha256(path)


def _inside(child: str, parent: str) -> bool:
    child, parent = os.path.normcase(os.path.realpath(child)), os.path.normcase(os.path.realpath(parent))
    return child != parent and child.startswith(parent.rstrip(os.sep) + os.sep)


def _rm(path: str) -> None:
    shutil.rmtree(path, ignore_errors=True)


class WorkerSpace:
    def __init__(self, repo: str, base_dir: str, protected_globs: tuple[str, ...],
                 max_diff_lines: int = 2000, max_files: int = 50, state_dir: str | None = None) -> None:
        self.repo = os.path.realpath(repo)
        os.makedirs(base_dir, exist_ok=True)
        self.base_dir = os.path.realpath(base_dir)
        self.protected_globs = tuple(protected_globs) + DEFAULT_PROTECTED
        self._res = [_glob_to_re(g) for g in self.protected_globs]
        self.max_diff_lines = max_diff_lines
        self.max_files = max_files
        if state_dir is None:
            state_dir = os.path.join(tempfile.gettempdir(), "jarvis-workerspace-state", hashlib.sha256(self.repo.encode()).hexdigest()[:12])
        self.manifest_dir = os.path.realpath(state_dir)
        if self.manifest_dir == self.base_dir or _inside(self.manifest_dir, self.base_dir) or _inside(self.base_dir, self.manifest_dir):
            raise WorktreeError("state_dir must be outside base_dir (the worker can write base_dir) and must not contain it")
        self._hooks_dir = tempfile.mkdtemp(prefix="empty-hooks-")
        self._scratch = tempfile.mkdtemp(prefix="ws-scratch-")
        self._attrs = os.path.join(self._scratch, "empty-attributes")
        open(self._attrs, "w").close()
        self._cleanup = weakref.finalize(self, lambda a, b: (_rm(a), _rm(b)), self._hooks_dir, self._scratch)

    def close(self) -> None:
        """Remove the temporary folders this object made (also done when it is garbage-collected)."""
        self._cleanup()

    def __enter__(self) -> "WorkerSpace":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # -- helpers ----------------------------------------------------------------------------------------------
    def _git(self, cwd: str, *args: str, index: str | None = None) -> bytes:
        env = dict(os.environ, GIT_CONFIG_NOSYSTEM="1", GIT_TERMINAL_PROMPT="0", GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_SYSTEM=os.devnull,
                   GIT_ATTR_NOSYSTEM="1", GIT_NO_REPLACE_OBJECTS="1", GIT_OPTIONAL_LOCKS="0")
        for var in ("GIT_EXTERNAL_DIFF", "GIT_DIFF_OPTS", "GIT_PAGER", "GIT_SSH_COMMAND", "GIT_ASKPASS"):
            env.pop(var, None)
        if index:
            env["GIT_INDEX_FILE"] = index
        cmd = ["git", "-c", f"core.hooksPath={self._hooks_dir}", "-c", "protocol.allow=never", "-c", "core.fsmonitor=false",
               "-c", f"core.attributesFile={self._attrs}", "-c", "diff.external=", *args]
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
            dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS or os.path.islink(os.path.join(dirpath, d))]
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

    def _git_guard_files(self, worktree: str) -> dict[str, str]:
        """label -> absolute path of every file that configures git for this worktree. Resolved at create() time, before the worker runs."""
        gitfile = os.path.join(worktree, ".git")
        with open(gitfile, encoding="utf-8") as f:
            line = f.read().strip()
        if not line.startswith("gitdir:"):
            raise WorktreeError("unexpected .git file in the new worktree")
        gitdir = os.path.realpath(os.path.join(worktree, line.split(":", 1)[1].strip()))
        common = gitdir
        cd = os.path.join(gitdir, "commondir")
        if os.path.isfile(cd):
            with open(cd, encoding="utf-8") as f:
                common = os.path.realpath(os.path.join(gitdir, f.read().strip()))
        return {
            "repo config": os.path.join(common, "config"),
            "worktree config": os.path.join(gitdir, "config.worktree"),
            "info/attributes": os.path.join(common, "info", "attributes"),
            "worktree info/attributes": os.path.join(gitdir, "info", "attributes"),
            "info/exclude": os.path.join(common, "info", "exclude"),
            "objects/info/alternates": os.path.join(common, "objects", "info", "alternates"),
            "commondir": cd,
            "gitdir": os.path.join(gitdir, "gitdir"),
            "info/grafts": os.path.join(common, "info", "grafts"),
            "shallow": os.path.join(common, "shallow"),
        }

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
        guard = self._git_guard_files(path)
        manifest = {"files": {k: v for k, v in files.items() if v},
                    "git_file": _sha256(git_file) if os.path.isfile(git_file) else None,
                    "git_guard": {label: {"path": p, "sha256": _sha_or_absent(p)} for label, p in guard.items()},
                    "base_commit": commit}
        os.makedirs(self.manifest_dir, exist_ok=True)
        try:
            os.chmod(self.manifest_dir, 0o700)
        except OSError:
            pass
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
        """(changed lines, path) for everything differing from base, via a throwaway index; the real index is untouched.
        Only called after the git configuration was proved unchanged."""
        index = os.path.join(self._scratch, f"{space.worker_id}.index")
        if os.path.exists(index):
            os.remove(index)
        try:
            self._git(space.path, "read-tree", space.base_commit, index=index)
            self._git(space.path, "add", "-A", "--", ".", index=index)
            raw = self._git(space.path, "diff", "--cached", "--numstat", "--no-renames", "--no-ext-diff", "--no-textconv", "-z",
                            space.base_commit, index=index)
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

    def verify(self, space: Space, *, worker_stopped: "bool | Callable[[], bool]" = False) -> Verdict:
        """Check the worker's change against the manifest.

        CALL ONLY AFTER EVERY PROCESS OF THIS WORKER HAS EXITED (the lab runner kills the worker's process group first). The git files are
        checked in Python and git is run afterwards; a worker process that is still alive could rewrite `.git/config` between those two steps
        (the same attack, just timed). So the caller must assert that the worker is gone: pass worker_stopped=True, or a callable that returns True
        only once the worker's process group has exited. Without that assertion verify() raises and checks nothing."""
        stopped = worker_stopped() if callable(worker_stopped) else worker_stopped
        if stopped is not True:
            raise WorktreeError("verify() needs worker_stopped=True (or a callable confirming it): the worker must have exited, or it could change "
                                "the git configuration between the check and the git command")
        root = self._checked_path(space)
        v: list[Violation] = []
        try:
            with open(self._manifest_path(space.worker_id), encoding="utf-8") as f:
                manifest = json.load(f)
        except (OSError, ValueError):
            return Verdict(False, [Violation("manifest_missing", "")], {})
        # 1. The files that configure git: compared in plain Python, BEFORE any git command. If one changed, no git is run at all.
        for label, rec in sorted(manifest.get("git_guard", {}).items()):
            if _sha_or_absent(rec["path"]) != rec["sha256"]:
                v.append(Violation("git_config", label))
        gitf = os.path.join(root, ".git")
        git_file_bad = os.path.islink(gitf) or not os.path.isfile(gitf) or _sha256(gitf) != manifest.get("git_file")
        if git_file_bad:
            v.append(Violation("git_internal", ".git"))
        # 2. Protected files (also plain Python).
        want: dict[str, str] = manifest["files"]
        have = self._scan(root)
        git_safe = not any(x.kind == "git_config" for x in v) and not git_file_bad
        changes = self._changes(Space(root, space.worker_id, manifest["base_commit"])) if git_safe else []
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

    def collect_patch(self, space: Space, *, worker_stopped: "bool | Callable[[], bool]" = False) -> bytes:
        """The worker's change as a git binary patch, in BYTES (a diff can hold non-UTF-8 text and must not be altered). Refuses unless verify() is ok.
        Same rule as verify(): only after every process of the worker has exited, asserted with worker_stopped."""
        verdict = self.verify(space, worker_stopped=worker_stopped)
        if not verdict.ok:
            raise WorktreeError("refusing to collect patch: " + ", ".join(sorted({x.kind for x in verdict.violations})))
        index = os.path.join(self._scratch, f"{space.worker_id}.patch-index")
        try:
            self._git(space.path, "read-tree", space.base_commit, index=index)
            self._git(space.path, "add", "-A", "--", ".", index=index)
            return self._git(space.path, "diff", "--cached", "--binary", "--no-renames", "--no-ext-diff", "--no-textconv", space.base_commit, index=index)
        finally:
            if os.path.exists(index):
                os.remove(index)

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
