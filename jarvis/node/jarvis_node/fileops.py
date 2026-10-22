r"""File operations for the owner's own folders, every one of them undoable (JARVIS plan P4, step 1).

Nothing here is ever unlinked: delete MOVES to a trash folder. Every operation writes a "started" line to a JSON Lines journal BEFORE
it changes anything and a "done" line after, so a crash leaves a visible, safely undoable "started"-only entry (pending()).
undo() reverses an operation exactly, and refuses (Modified, Conflict) rather than destroy something that changed since.

Paths are made absolute with realpath first (".." and symlinks resolved), then must be inside one of the roots. Secret paths
(the kernel's SECRET_PATTERNS, the node's sensitive check, the caller's `protected` fragments) are never touched. The journal holds
paths, sizes and hashes only, never file contents.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import sys
import threading
import time
import uuid

try:
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "kernel"))
    from jarvis_kernel.policy import SECRET_PATTERNS as _SECRET   # noqa: E402
except Exception:                                   # no kernel package: a local copy of the floor still refuses
    _SECRET = re.compile(
        r"(^|/)\.env(\.[\w.-]+)?(/|$)|(^|/)\.(aws|ssh|gnupg|kube|azure)(/|$)|kubeconfig|jarvis-" + "secrets"
        + r"|(^|/)(secrets?|credentials?)\.(ya?ml|json|db|txt|csv)$|\.(p12|pfx|kdbx|jks|keystore|pem)$"
        r"|(^|/)id_(dsa|ecdsa|rsa|ed25519)(\.pub)?$", re.I)
try:
    from .sensitive import sensitive_target as _sensitive
except Exception:
    _sensitive = None


class FileOpsError(Exception):
    pass


class OutsideRoots(FileOpsError):
    pass


class Protected(FileOpsError):
    pass


class FileExists(FileOpsError):
    pass


class Modified(FileOpsError):
    pass


class Conflict(FileOpsError):
    pass


class AlreadyUndone(FileOpsError):
    pass


class UnknownOperation(FileOpsError):
    pass


def _sha_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def _digest(path: str) -> tuple[str, int]:
    """(sha256, size) of a file; for a folder a tree digest of the sorted relative paths and file hashes."""
    if os.path.islink(path):
        return hashlib.sha256(("L" + os.readlink(path)).encode("utf-8", "surrogatepass")).hexdigest(), 0
    if not os.path.isdir(path):
        return _sha_file(path), os.path.getsize(path)
    entries, size = [], 0
    for base, dirs, files in os.walk(path):
        for name in dirs + files:
            full = os.path.join(base, name)
            rel = os.path.relpath(full, path).replace(os.sep, "/")
            if os.path.islink(full):
                entries.append((rel, "L" + os.readlink(full)))
            elif os.path.isdir(full):
                entries.append((rel, "D"))
            else:
                entries.append((rel, _sha_file(full)))
                size += os.path.getsize(full)
    h = hashlib.sha256()
    for rel, d in sorted(entries):
        h.update(f"{rel}\0{d}\n".encode("utf-8", "surrogatepass"))
    return h.hexdigest(), size


def _has_files(path: str) -> bool:
    return any(files for _b, _d, files in os.walk(path))


def _remove(path: str) -> None:
    if os.path.isdir(path) and not os.path.islink(path):
        shutil.rmtree(path)
    else:
        os.remove(path)


def _copy(src: str, dst: str) -> None:
    if os.path.isdir(src) and not os.path.islink(src):
        shutil.copytree(src, dst, symlinks=True)
    else:
        shutil.copy2(src, dst, follow_symlinks=False)


class FileOps:
    def __init__(self, roots, journal_path: str, protected: tuple = (), clock=time.time, trash_dir: str | None = None):
        self.roots = [os.path.realpath(r) for r in roots]
        if not self.roots:
            raise ValueError("at least one root is required")
        self.journal_path = os.path.abspath(journal_path)
        self.protected = tuple(str(p).lower() for p in protected if str(p))
        self.clock = clock
        self.trash_dir = os.path.realpath(trash_dir) if trash_dir else os.path.join(self.roots[0], ".jarvis-trash")
        self._lock = threading.RLock()
        os.makedirs(os.path.dirname(self.journal_path) or ".", exist_ok=True)

    # ---------------------------------------------------------------- safety
    def _root_of(self, p: str):
        n = os.path.normcase(p)
        for r in self.roots:
            nr = os.path.normcase(r)
            try:
                if os.path.commonpath([n, nr]) == nr:
                    return r
            except ValueError:
                continue
        return None

    def _is_root(self, p: str) -> bool:
        n = os.path.normcase(p)
        return os.path.dirname(p) == p or any(n == os.path.normcase(r) for r in self.roots)

    def _check(self, path, forbid_root: bool = False) -> str:
        p = os.path.realpath(os.path.abspath(os.path.expanduser(str(path))))
        root = self._root_of(p)
        if root is None:
            raise OutsideRoots(f"outside the allowed folders: {path}")
        fwd = "/" + p.replace("\\", "/").strip("/").lower()
        parts = fwd.strip("/").split("/")
        for frag in self.protected:
            if frag.replace("\\", "/").strip("/") in parts:
                raise Protected(f"protected: {frag}")
        if _SECRET.search(fwd):
            raise Protected(f"secret path: {path}")
        if _sensitive is not None:
            rel = os.path.relpath(p, root)
            if rel != "." and _sensitive([rel.replace("\\", "/")]):
                raise Protected(f"sensitive path: {path}")
        if forbid_root and self._is_root(p):
            raise Protected(f"a root folder cannot be moved or deleted: {path}")
        if forbid_root:                                  # moving the trash folder (or a folder holding it) into itself would copy forever
            tn, pn = os.path.normcase(self.trash_dir), os.path.normcase(p)
            if tn == pn or tn.startswith(pn + os.sep):
                raise Protected(f"the trash folder cannot be moved or deleted: {path}")
        return p

    # --------------------------------------------------------------- journal
    def _log(self, op_id, op, src, dst, sha, size, status) -> None:
        line = {"op_id": op_id, "t": self.clock(), "op": op, "src": src, "dst": dst, "sha256": sha, "size": size, "status": status}
        with self._lock, open(self.journal_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(line, ensure_ascii=False) + "\n")
            f.flush()
            os.fsync(f.fileno())

    def _lines(self) -> list:
        try:
            with open(self.journal_path, encoding="utf-8") as f:
                raw = f.read().splitlines()
        except FileNotFoundError:
            return []
        out = []
        for ln in raw:
            try:
                d = json.loads(ln)
            except ValueError:
                continue
            if isinstance(d, dict) and d.get("op_id"):
                out.append(d)
        return out

    def _records(self) -> dict:
        """op_id -> merged record (insertion order = first appearance); status is the last one; done_at the index of its done line."""
        recs: dict = {}
        for i, d in enumerate(self._lines()):
            r = recs.setdefault(d["op_id"], {"op_id": d["op_id"]})
            for k, v in d.items():
                if v is not None:
                    r[k] = v
            if d.get("status") == "done":
                r["done_at"] = i
        return recs

    def _new_id(self) -> str:
        return f"{int(self.clock() * 1000):x}-{uuid.uuid4().hex[:8]}"

    def _run(self, op, src, dst, sha, size, action, op_id=None):
        """Journal 'started', do action(op_id) (may return overrides), journal 'done'."""
        with self._lock:
            op_id = op_id or self._new_id()
            self._log(op_id, op, src, dst, sha, size, "started")
            try:
                res = action(op_id) or {}
            except BaseException:
                self._log(op_id, op, src, dst, sha, size, "failed")
                raise
            sha, size = res.get("sha", sha), res.get("size", size)
            self._log(op_id, op, src, dst, sha, size, "done")
            return {"op_id": op_id, "op": op, "src": src, "dst": dst, "ok": True}

    # ------------------------------------------------------------ operations
    def create_folder(self, path):
        p = self._check(path)
        if os.path.lexists(p) and not os.path.isdir(p):
            raise FileExists(f"a file is in the way: {p}")
        if os.path.isdir(p):
            return self._run("noop", None, p, None, 0, lambda _i: None)
        top = p
        while not os.path.lexists(os.path.dirname(top)) and os.path.dirname(top) != top:
            top = os.path.dirname(top)

        def act(_i):
            os.makedirs(p)
            sha, size = _digest(top)
            return {"sha": sha, "size": size}
        return self._run("create_folder", None, top, None, 0, act)

    def create_file(self, path, text):
        p = self._check(path)
        if os.path.lexists(p):
            raise FileExists(f"already exists: {p}")
        if not os.path.isdir(os.path.dirname(p)):
            raise FileNotFoundError(f"folder does not exist: {os.path.dirname(p)}")
        data = str(text).encode("utf-8")
        sha = hashlib.sha256(data).hexdigest()

        def act(op_id):
            tmp = os.path.join(os.path.dirname(p), f".{os.path.basename(p)}.{op_id}.tmp")
            try:
                with open(tmp, "wb") as f:
                    f.write(data)
                    f.flush()
                    os.fsync(f.fileno())
                os.replace(tmp, p)
            finally:
                if os.path.lexists(tmp):
                    os.remove(tmp)
        return self._run("create_file", None, p, sha, len(data), act)

    def _transfer(self, src: str, dst: str, sha: str) -> None:
        """Rename on the same volume, else copy, verify by hash, delete the original."""
        try:
            os.rename(src, dst)
            return
        except OSError:
            if not os.path.lexists(src) or os.path.lexists(dst):
                raise
        try:
            _copy(src, dst)
            if _digest(dst)[0] != sha:
                raise OSError(f"copy did not verify: {dst}")
        except BaseException:
            if os.path.lexists(dst):
                _remove(dst)
            raise
        _remove(src)

    def _plan(self, s: str, d: str, what: str) -> None:
        if not os.path.lexists(s):
            raise FileNotFoundError(f"not found: {s}")
        if os.path.lexists(d):
            raise FileExists(f"already exists: {d}")
        if os.path.normcase(d).startswith(os.path.normcase(s) + os.sep):
            raise Conflict(f"cannot {what} a folder into itself")
        if not os.path.isdir(os.path.dirname(d)):
            raise FileNotFoundError(f"folder does not exist: {os.path.dirname(d)}")

    def _move(self, op, s, d):
        self._plan(s, d, "move")
        sha, size = _digest(s)
        return self._run(op, s, d, sha, size, lambda _i: self._transfer(s, d, sha))

    def move(self, src, dst):
        return self._move("move", self._check(src, forbid_root=True), self._check(dst))

    def rename(self, src, new_name):
        name = str(new_name)
        if not name or name in (".", "..") or any(c in name for c in "/\\:") or name != name.strip():
            raise ValueError("new_name must be a bare name")
        s = self._check(src, forbid_root=True)
        return self._move("rename", s, self._check(os.path.join(os.path.dirname(s), name)))

    def copy(self, src, dst):
        s, d = self._check(src), self._check(dst)
        self._plan(s, d, "copy")
        sha, size = _digest(s)

        def act(_i):
            try:
                _copy(s, d)
                if _digest(d)[0] != sha:
                    raise OSError(f"copy did not verify: {d}")
            except BaseException:
                if os.path.lexists(d):
                    _remove(d)
                raise
        return self._run("copy", s, d, sha, size, act)

    def delete(self, path):
        s = self._check(path, forbid_root=True)
        if not os.path.lexists(s):
            raise FileNotFoundError(f"not found: {s}")
        sha, size = _digest(s)
        op_id = self._new_id()
        d = os.path.join(self.trash_dir, op_id, os.path.basename(s))

        def act(_i):
            os.makedirs(os.path.dirname(d))
            self._transfer(s, d, sha)
        return self._run("delete", s, d, sha, size, act, op_id=op_id)

    # ------------------------------------------------------------------ undo
    def pending(self) -> list:
        """Operations with a 'started' line and nothing after it (a crash): safe to undo."""
        return [self._public(r) for r in self._records().values() if r.get("status") == "started"]

    @staticmethod
    def _public(r: dict) -> dict:
        return {k: r.get(k) for k in ("op_id", "t", "op", "src", "dst", "sha256", "size", "status")}

    def history(self, limit: int = 50) -> list:
        """The newest operations first, from the journal alone (no file is opened)."""
        recs = list(self._records().values())
        return [self._public(r) for r in reversed(recs[-limit:])] if limit > 0 else []

    def _trash_ok(self, d: str) -> str:
        p = os.path.realpath(d)
        if not os.path.normcase(p).startswith(os.path.normcase(self.trash_dir) + os.sep):
            raise OutsideRoots(f"not in the trash folder: {d}")
        return p

    def _undo_move_back(self, r: dict, pending: bool, src: str, dst: str) -> None:
        """Put dst back at src. In a crashed (pending) operation both may exist, or nothing may have moved."""
        sha = r.get("sha256")
        if not os.path.lexists(dst):
            if pending and os.path.lexists(src):
                return                                  # the crash came before anything moved
            raise Conflict(f"nothing to move back at {dst}")
        if os.path.lexists(src):
            if not pending:
                raise Conflict(f"the original spot is occupied: {src}")
            if sha and _digest(src)[0] == sha:          # crash mid-copy: the original is whole, drop the partial copy
                _remove(dst)
                return
            if sha and _digest(dst)[0] == sha:          # crash mid-delete of the original: the copy is whole
                _remove(src)
            else:
                raise Conflict(f"cannot tell which of the two is whole: {src}")
        self._transfer(dst, src, sha or _digest(dst)[0])

    def undo(self, op_id: str) -> dict:
        with self._lock:
            r = self._records().get(op_id)
            if r is None:
                raise UnknownOperation(op_id)
            if r.get("status") == "undone":
                raise AlreadyUndone(op_id)
            if r.get("status") == "failed":
                raise UnknownOperation(f"{op_id} failed and changed nothing")
            pending = r.get("status") == "started"
            op, src, dst, sha = r.get("op"), r.get("src"), r.get("dst"), r.get("sha256")
            if op == "noop":
                pass
            elif op == "create_file":
                d = self._check(dst)
                if os.path.lexists(d):
                    if _digest(d)[0] != sha:
                        raise Modified(f"changed since it was created: {d}")
                    os.remove(d)
            elif op == "create_folder":
                d = self._check(dst)
                if os.path.lexists(d):
                    if _has_files(d) or (sha and not pending and _digest(d)[0] != sha):
                        raise Modified(f"changed since it was created: {d}")
                    shutil.rmtree(d)
            elif op == "copy":
                d = self._check(dst)
                if os.path.lexists(d):
                    if not pending and _digest(d)[0] != sha:
                        raise Modified(f"changed since it was copied: {d}")
                    _remove(d)
            elif op in ("move", "rename"):
                self._undo_move_back(r, pending, self._check(src, forbid_root=True), self._check(dst))
            elif op == "delete":
                t = self._trash_ok(dst)
                self._undo_move_back(r, pending, self._check(src), t)
                try:
                    os.rmdir(os.path.dirname(t))
                    os.rmdir(self.trash_dir)
                except OSError:
                    pass
            else:
                raise UnknownOperation(f"{op_id}: unknown operation {op}")
            self._log(op_id, op, src, dst, sha, r.get("size"), "undone")
            return {"op_id": op_id, "op": op, "src": src, "dst": dst, "ok": True}

    def undo_last(self, n: int = 1) -> list:
        """Undo the newest n done operations, newest first."""
        done = sorted((r for r in self._records().values() if r.get("status") == "done"), key=lambda r: r["done_at"], reverse=True)
        return [self.undo(r["op_id"]) for r in done[:max(0, n)]]
