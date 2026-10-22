"""File search for the laptop node, with pluggable backends (JARVIS plan P4 step 1, second half).

Backends: Everything's es.exe (fast, indexed), a plain os.scandir walk (always available, never leaves the roots), and a stub
for Windows Search (a later COM implementation). search() tries them in order and falls back when one raises, recording why.

Rules that hold for every backend: only names, sizes and times come back, never file contents; a hit outside the roots
(real path, so symlink and junction games do not count) or on a secret or sensitive path is dropped; es.exe is always run
with an argument list and the query text after "--", never through a shell.
"""

from __future__ import annotations

import fnmatch
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from typing import Callable, Iterator, Sequence

from . import sensitive

try:
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "kernel"))
    from jarvis_kernel.policy import SECRET_COMPONENTS as _K_COMPONENTS, SECRET_PATTERNS as _K_PATTERNS   # noqa: E402
except Exception:                                   # no kernel package: the node's own copy of the floor still applies
    _K_COMPONENTS = {".jarvis", ".jarvis-kernel", "secrets.env", "keys.json", "protected.json", "id_ed25519", "id_rsa"}
    _K_PATTERNS = re.compile(
        r"(^|/)\.env(\.[\w.-]+)?(/|$)|(^|/)\.(aws|ssh|gnupg|kube|azure)(/|$)|(^|/)\.(npmrc|pypirc|netrc|pgpass|git-credentials)$"
        r"|kubeconfig|terraform\.(tfstate|tfvars)|wallet\.dat|\.(p12|pfx|kdbx|jks|keystore)$|(^|/)id_(dsa|ecdsa|rsa|ed25519)(\.pub)?$",
        re.I)

_SECRETS_DIR = "jarvis" + "-secrets"                 # the kernel's secrets folder name
MAX_LIMIT = 1000
MAX_REGEX = 200
ES_TIMEOUT_S = 10.0
IGNORED_NAMES = frozenset(n.lower() for n in (
    "node_modules", ".git", "__pycache__", "$RECYCLE.BIN", "System Volume Information", ".jarvis-trash"))
IGNORED_SUFFIXES = ("appdata/local/temp",)
_REPARSE = 0x400


class BadQuery(ValueError):
    pass


class Unavailable(RuntimeError):
    """A backend that cannot run here (not installed, not implemented, failed)."""


@dataclass
class Query:
    text: str = ""
    mode: str = "substring"                 # substring | glob | regex
    extensions: tuple = ()
    min_size: int | None = None
    max_size: int | None = None
    modified_after: float | None = None
    modified_before: float | None = None
    kind: str = "any"                       # file | dir | any
    max_depth: int | None = None
    limit: int = 100
    case_sensitive: bool = False

    def __post_init__(self) -> None:
        if self.mode not in ("substring", "glob", "regex"):
            raise BadQuery(f"unknown mode {self.mode!r}")
        if self.kind not in ("file", "dir", "any"):
            raise BadQuery(f"unknown kind {self.kind!r}")
        if not isinstance(self.text, str) or "\x00" in self.text:
            raise BadQuery("text must be a string without NUL")
        self.limit = max(1, min(int(self.limit), MAX_LIMIT))
        self.extensions = tuple(str(e).lower().lstrip(".") for e in self.extensions)
        flags = 0 if self.case_sensitive else re.I
        if self.mode == "regex":
            if len(self.text) > MAX_REGEX:
                raise BadQuery(f"regex longer than {MAX_REGEX} characters")
            try:
                self._rx = re.compile(self.text, flags)
            except re.error as e:
                raise BadQuery(f"invalid regex: {e}") from None
        elif self.mode == "glob":
            self._rx = re.compile(fnmatch.translate(self.text or "*"), flags)
        else:
            self._rx = None

    def name_matches(self, name: str) -> bool:
        if self.mode == "substring":
            if not self.text:
                return True
            return self.text in name if self.case_sensitive else self.text.lower() in name.lower()
        return bool(self._rx.search(name) if self.mode == "regex" else self._rx.match(name))

    def matches(self, name: str, is_dir: bool, size: int, modified: float) -> bool:
        """Every filter except depth (the walker's business) and roots."""
        if (self.kind == "dir" and not is_dir) or (self.kind == "file" and is_dir):
            return False
        if not self.name_matches(name):
            return False
        if self.extensions and (is_dir or "." not in name or name.rsplit(".", 1)[-1].lower() not in self.extensions):
            return False
        if not is_dir:
            if self.min_size is not None and size < self.min_size:
                return False
            if self.max_size is not None and size > self.max_size:
                return False
        if self.modified_after is not None and modified < self.modified_after:
            return False
        if self.modified_before is not None and modified > self.modified_before:
            return False
        return True

    def quality(self, name: str) -> int:
        """0 exact, 1 prefix, 2 anything else."""
        t, n = (self.text, name) if self.case_sensitive else (self.text.lower(), name.lower())
        if not t:
            return 2
        if n == t:
            return 0
        return 1 if (self.mode == "substring" and n.startswith(t)) else 2


@dataclass
class Hit:
    path: str
    name: str
    kind: str                               # file | dir
    size: int
    modified: float
    backend: str


@dataclass
class SearchResult:
    hits: list = field(default_factory=list)
    truncated: bool = False
    backends_tried: list = field(default_factory=list)
    skipped: list = field(default_factory=list)       # (backend, reason)


def _raw_cap(query: "Query") -> int:
    """How many raw hits a backend may produce. search() drops secret and sensitive paths AFTER the backend yields, so a backend that stopped
    at limit+1 could return too few results (or a wrong 'truncated' flag) when many early matches are dropped."""
    return query.limit * 20 + 100


def _norm(p: str) -> str:
    return os.path.normcase(os.path.normpath(p))


def _inside(path: str, roots: Sequence[str]) -> bool:
    p = _norm(path)
    for r in roots:
        r = _norm(r)
        try:
            if os.path.commonpath([p, r]) == r:
                return True
        except ValueError:                  # different drives
            continue
    return False


def _is_reparse(entry: os.DirEntry) -> bool:
    try:
        if entry.is_symlink():
            return True
        st = entry.stat(follow_symlinks=False)
        return bool(getattr(st, "st_file_attributes", 0) & _REPARSE)
    except OSError:
        return True


def _ignored(name: str, path: str) -> bool:
    if name.lower() in IGNORED_NAMES:
        return True
    p = path.replace("\\", "/").lower().rstrip("/")
    return any(p.endswith("/" + s) for s in IGNORED_SUFFIXES)


class ScandirBackend:
    name = "scandir"

    def available(self) -> bool:
        return True

    def search(self, query: Query, roots: Sequence[str]) -> Iterator[Hit]:
        count = 0
        for root in roots:
            stack = [(root, 1)]
            while stack:
                folder, depth = stack.pop()
                try:
                    entries = sorted(os.scandir(folder), key=lambda e: e.name)
                except OSError:
                    continue
                subdirs = []
                for e in entries:
                    try:
                        reparse = _is_reparse(e)
                        is_dir = e.is_dir(follow_symlinks=False)
                        st = e.stat(follow_symlinks=False)
                    except OSError:
                        continue
                    if _ignored(e.name, e.path):
                        continue
                    if is_dir and not reparse and (query.max_depth is None or depth < query.max_depth):
                        subdirs.append((e.path, depth + 1))
                    if query.matches(e.name, is_dir, st.st_size, st.st_mtime):
                        yield Hit(e.path, e.name, "dir" if is_dir else "file", 0 if is_dir else st.st_size, st.st_mtime, self.name)
                        count += 1
                        if count > _raw_cap(query):
                            return
                stack.extend(reversed(subdirs))


class EsBackend:
    """Everything's es.exe. exe_path is a path or an argument list (tests: [sys.executable, fake_es.py])."""
    name = "es"

    def __init__(self, exe_path) -> None:
        self.exe = [str(x) for x in exe_path] if isinstance(exe_path, (list, tuple)) else [str(exe_path)]

    def available(self) -> bool:
        if not self.exe:
            return False
        probe = self.exe[-1] if len(self.exe) > 1 else self.exe[0]
        return os.path.isfile(probe) or (len(self.exe) == 1 and shutil.which(probe) is not None)

    def search(self, query: Query, roots: Sequence[str]) -> Iterator[Hit]:
        cmd = list(self.exe) + ["-n", str(MAX_LIMIT * 5)]
        if query.mode == "regex":
            cmd.append("-r")
        if query.case_sensitive:
            cmd.append("-i")
        # UNVERIFIED: that the real es.exe treats "--" as end-of-options (Everything's es.exe is not installed on this machine; only the fake one
        # in the tests was exercised). Check `es -- -x` on the laptop once Everything is installed; if "--" is taken as a search word, drop it
        # and instead refuse query text that starts with "-" before it reaches es.
        cmd += ["--", query.text]
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=ES_TIMEOUT_S, shell=False)
        except subprocess.TimeoutExpired:
            raise Unavailable(f"es timed out after {ES_TIMEOUT_S:g}s") from None
        except OSError as e:
            raise Unavailable(f"es could not run: {e}") from None
        if proc.returncode != 0:
            raise Unavailable(f"es exited with {proc.returncode}")
        count = 0
        for line in proc.stdout.splitlines():
            path = line.strip()
            if not path or not _inside(path, roots):
                continue
            try:
                st = os.stat(path)
            except OSError:
                continue                    # vanished since indexing
            is_dir = os.path.isdir(path)
            name = os.path.basename(path.rstrip("\\/")) or path
            depths = [len(_norm(path).split(os.sep)) - len(_norm(r).split(os.sep)) for r in roots if _inside(path, [r])]
            if query.max_depth is not None and min(depths, default=1) > query.max_depth:
                continue
            if query.matches(name, is_dir, st.st_size, st.st_mtime):
                yield Hit(path, name, "dir" if is_dir else "file", 0 if is_dir else st.st_size, st.st_mtime, self.name)
                count += 1
                if count > _raw_cap(query):
                    return


class WindowsSearchBackend:
    """Stub for a later COM (Windows Search index) implementation."""
    name = "windows-search"

    def available(self) -> bool:
        return False

    def search(self, query: Query, roots: Sequence[str]) -> Iterator[Hit]:
        raise Unavailable("Windows Search backend is not implemented in this build")


def is_secret_path(path: str) -> bool:
    """True for paths the kernel treats as secret (its SECRET_PATTERNS and SECRET_COMPONENTS)."""
    p = path.replace("\\", "/")
    if _SECRETS_DIR in p or _K_PATTERNS.search(p):
        return True
    for comp in re.split(r"[/\s]+", p):
        if comp in _K_COMPONENTS or comp.lower().endswith(".pem") or _SECRETS_DIR in comp:
            return True
    return False


def _default_backends() -> list:
    es = shutil.which("es") or shutil.which("es.exe")
    out = []
    if es:
        out.append(EsBackend(es))
    out.append(ScandirBackend())
    return out


def search(query: Query, roots: Sequence[str], backends: Sequence | None = None,
           allow: Callable[[str], bool] | None = None) -> SearchResult:
    real_roots = [os.path.realpath(os.path.abspath(r)) for r in roots]
    result = SearchResult()
    for backend in (list(backends) if backends is not None else _default_backends()):
        if not backend.available():
            result.skipped.append((backend.name, "not available"))
            continue
        result.backends_tried.append(backend.name)
        hits: list = []
        try:
            for hit in backend.search(query, real_roots):
                real = os.path.realpath(hit.path)
                if not _inside(real, real_roots) or is_secret_path(real) or is_secret_path(hit.path):
                    continue
                rel = next((os.path.relpath(real, r) for r in real_roots if _inside(real, [r])), real)
                if sensitive.why(rel):
                    continue
                if allow is not None and not allow(hit.path):
                    continue
                hits.append(hit)
                if len(hits) > query.limit:
                    break
        except Exception as e:              # this backend failed: note why, try the next
            result.skipped.append((backend.name, f"{type(e).__name__}: {e}"))
            continue
        result.truncated = len(hits) > query.limit
        hits = hits[:query.limit]
        hits.sort(key=lambda h: (query.quality(h.name), -h.modified, h.path))
        result.hits = hits
        return result
    return result
