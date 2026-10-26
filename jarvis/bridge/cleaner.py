"""The PC cleaner's backend (D55): scan a fixed list of places, clean exactly what the owner chose. Windows; every folder, the clock and the
Recycle Bin are injected, so the tests run it on a fake tree with a fake recycler.

Two kinds of rows, and nothing else exists:

  SAFE (a fixed list, ticked by default): the user temp folder and Windows\\Temp, browser caches and app caches (cache folders only: never
  cookies, passwords, history, sessions or profiles), thumbnail and icon caches, crash dumps and logs older than 7 days, the Windows Update
  download cache, the Recycle Bin, and the pip / npm / NuGet caches. Their files are deleted for real (they are caches); the Recycle Bin row
  empties the bin.
  REVIEW (listed with the biggest items, NEVER ticked by default, removed only if the owner ticks them): duplicate files in Downloads and
  Documents (same size and same content; the newest copy stays), installers (.exe, .msi) in Downloads older than 30 days, files over 500 MB
  in the owner's folders not opened for 180 days, empty folders. These always go to the Recycle Bin, so they can be put back.

Hard blocks, checked when listing and again right before every removal (the path is resolved first, so a link cannot lead around them):
Windows, Program Files (both), ProgramData, the user profile folder itself and the owner's known folders themselves, the folder that holds the
users, any other user's folder, drive roots, D:\\jarvis and D:\\jarvis-secrets with the JARVIS code, vault, memory, audit and data folders, and
the project folders in config/projects.json. The only places inside Windows that a row may touch are the exact roots marked `exempt` below
(Windows\\Temp, SoftwareDistribution\\Download, Minidump, Logs, MEMORY.DMP). A link, junction or reparse point is never followed or removed.

Privacy: `scan()` and `clean()` return file names for the owner's panel. `model_view()` strips them: a chat model gets category ids, labels
that are fixed words, sizes and counts only.
"""
from __future__ import annotations

import fnmatch
import hashlib
import json
import os
import stat
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable, Iterator

from jarvis.bridge.output_preview import INVISIBLE, SECRET_NAME

DAY = 86400
OLD_LOG_DAYS = 7
OLD_INSTALLER_DAYS = 30
BIG_FILE_BYTES = 500 * 1024 * 1024
BIG_FILE_UNUSED_DAYS = 180
TEMP_GRACE_S = 3600                       # a temp file touched in the last hour may belong to a running installer
TOP_ITEMS = 8
LIST_ITEMS = 150                          # review items listed per row for the panel (the biggest first); only listed items can be ticked
HASH_CAP_BYTES = 2 * 1024 ** 3
HASH_BUDGET_S = 45.0
MAX_DEPTH = 12
UNDO_LIMIT = 200
SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv", "site-packages", ".svn", ".hg"}
REPARSE = 0x400                           # FILE_ATTRIBUTE_REPARSE_POINT: a junction, a symlink, a OneDrive placeholder


class CleanerError(RuntimeError):
    pass


def _label(text: Any, limit: int = 100) -> str:
    return " ".join(INVISIBLE.sub(" ", str(text)).split())[:limit]


# ---- where things are -------------------------------------------------------------------------------------------------------------------

@dataclass
class Env:
    home: Path
    user_temp: list[Path]
    windows: Path
    local_appdata: Path
    appdata: Path
    program_files: list[Path]
    program_data: Path
    users_root: Path
    protected: list[Path] = field(default_factory=list)       # JARVIS folders and the owner's project folders: nothing below is ever touched

    @property
    def known(self) -> dict[str, Path]:
        return {k: self.home / k for k in ("Downloads", "Documents", "Desktop", "Pictures", "Videos", "Music")}

    @classmethod
    def detect(cls, projects_file: Path | None = None) -> "Env":
        e = os.environ
        home = Path(e.get("USERPROFILE") or Path.home())
        win = Path(e.get("SystemRoot") or r"C:\Windows")
        local = Path(e.get("LOCALAPPDATA") or home / "AppData" / "Local")
        temps = [Path(e[k]) for k in ("TEMP", "TMP") if e.get(k)] or [local / "Temp"]
        protected = [Path(r"D:\jarvis"), Path(r"D:\jarvis-secrets")]
        for name in ("JARVIS_HOME", "JARVIS_SECRETS", "JARVIS_SECRETS_DIR", "JARVIS_DATA_ROOT", "JARVIS_DATA_DIR"):
            if e.get(name):
                protected.append(Path(e[name]))
        try:
            from jarvis.pluginapi.paths import data_dir
            protected.append(Path(data_dir()))
        except Exception:  # noqa: BLE001 - no plugin api: the other roots stand
            pass
        protected.append(Path(__file__).resolve().parents[2])
        protected += project_folders(projects_file)
        return cls(home=home, user_temp=temps, windows=win, local_appdata=local, appdata=Path(e.get("APPDATA") or home / "AppData" / "Roaming"),
                   program_files=[Path(e[k]) for k in ("ProgramFiles", "ProgramFiles(x86)", "ProgramW6432") if e.get(k)] or [Path(r"C:\Program Files")],
                   program_data=Path(e.get("ProgramData") or r"C:\ProgramData"), users_root=home.parent, protected=protected)


def project_folders(projects_file: Path | None = None) -> list[Path]:
    """The folders in config/projects.json (path.nt, or any path value), which the owner keeps code in."""
    path = projects_file or Path(__file__).resolve().parents[1] / "config" / "projects.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    out: list[Path] = []
    for proj in (data.get("projects") or {}).values():
        p = proj.get("path") if isinstance(proj, dict) else None
        if isinstance(p, dict):
            out += [Path(v) for v in p.values() if isinstance(v, str)]
        elif isinstance(p, str):
            out.append(Path(p))
    return out


def _norm(p: Any) -> str:
    try:
        return os.path.normcase(os.path.realpath(str(p)))
    except (OSError, ValueError):
        return os.path.normcase(os.path.abspath(str(p)))


def _within(child: str, parent: str) -> bool:
    return child == parent or child.startswith(parent.rstrip(os.sep) + os.sep)


class Guard:
    """Says why a path must not be touched, or None. `exempt` are the roots of the row being processed that may sit inside a blocked folder."""

    def __init__(self, env: Env):
        e = env
        self.blocked = [_norm(p) for p in [e.windows, *e.program_files, e.program_data, *e.protected]]
        self.exact = [_norm(p) for p in [e.home, e.users_root, e.local_appdata, e.appdata, *e.known.values(), *e.user_temp]]
        self.users = _norm(e.users_root)
        self.home = _norm(e.home)

    def reason(self, path: Any, exempt: Iterable[Any] = ()) -> str | None:
        p = _norm(path)
        if os.path.dirname(p) == p:
            return "a drive root"
        ex = [_norm(x) for x in exempt]
        for b in self.blocked:
            if _within(b, p):
                return "it holds a protected folder"
            if _within(p, b) and not any(_within(p, x) and _within(x, b) for x in ex):
                return "a protected folder"
        for x in self.exact:
            if _within(x, p):                                  # p is that folder, or a folder above it
                return "a known folder itself"
        if _within(p, self.users) and not _within(p, self.home):
            return "another user's folder"
        return None


# ---- the Recycle Bin ----------------------------------------------------------------------------------------------------------------------

class Recycler:
    """The Windows implementation: send2trash for moving to the bin, the shell for its size and for emptying it."""

    def trash(self, path: str) -> None:
        try:
            from send2trash import send2trash
        except ImportError:
            raise CleanerError("send2trash is not installed") from None
        send2trash(path)

    def bin_stats(self) -> tuple[int, int]:
        if os.name != "nt":
            return 0, 0
        import ctypes
        from ctypes import wintypes

        class Info(ctypes.Structure):
            _fields_ = [("cbSize", wintypes.DWORD), ("size", ctypes.c_int64), ("items", ctypes.c_int64)]
        info = Info(ctypes.sizeof(Info), 0, 0)
        if ctypes.windll.shell32.SHQueryRecycleBinW(None, ctypes.byref(info)) != 0:
            return 0, 0
        return int(info.size), int(info.items)

    def bin_empty(self) -> bool:
        if os.name != "nt":
            return False
        import ctypes
        return ctypes.windll.shell32.SHEmptyRecycleBinW(None, None, 0x7) in (0, -2147418113)    # S_OK; or E_UNEXPECTED when it was already empty


# ---- the rows -----------------------------------------------------------------------------------------------------------------------------

@dataclass
class Entry:
    path: str
    size: int
    mtime: float = 0.0
    is_dir: bool = False
    note: str = ""


@dataclass
class Root:
    path: Path
    exempt: bool = False                  # may sit inside a blocked folder (only the exact roots named in CATALOG)
    patterns: tuple[str, ...] = ()        # file name patterns (fnmatch, case-insensitive); empty = every file
    min_age_s: float = 0.0
    depth: int = MAX_DEPTH


@dataclass
class Category:
    id: str
    label: str
    what: str
    kind: str                             # safe | review
    disposal: str                         # delete | bin | bin_empty
    roots: list[Root] = field(default_factory=list)
    finder: Callable[["Cleaner", "Category", float], list[Entry]] | None = None


def _is_link(st: os.stat_result) -> bool:
    return stat.S_ISLNK(st.st_mode) or bool(getattr(st, "st_file_attributes", 0) & REPARSE)


def walk_files(root: Path, depth: int = MAX_DEPTH, skip: set = frozenset(), want_dirs: bool = False) -> Iterator[tuple[str, os.stat_result, bool]]:
    """(path, lstat, is_dir) for every plain file (and folder, if asked) below `root`. Links are neither listed nor entered."""
    stack = [(str(root), 0)]
    while stack:
        top, d = stack.pop()
        try:
            with os.scandir(top) as it:
                children = list(it)
        except OSError:
            continue
        for ch in children:
            try:
                st = ch.stat(follow_symlinks=False)
            except OSError:
                continue
            if _is_link(st):
                continue
            if stat.S_ISDIR(st.st_mode):
                if ch.name.lower() in skip:
                    continue
                if want_dirs:
                    yield ch.path, st, True
                if d < depth:
                    stack.append((ch.path, d + 1))
            elif stat.S_ISREG(st.st_mode):
                yield ch.path, st, False


def _glob(roots: Iterable[Path], pattern: str) -> list[Path]:
    out: list[Path] = []
    for r in roots:
        try:
            out += [p for p in r.glob(pattern) if p.is_dir()]
        except (OSError, ValueError):
            pass
    return out


def catalog(env: Env) -> list[Category]:
    L, A, W = env.local_appdata, env.appdata, env.windows
    chromium = [L / "Google" / "Chrome" / "User Data", L / "Microsoft" / "Edge" / "User Data", L / "BraveSoftware" / "Brave-Browser" / "User Data",
                A / "Opera Software" / "Opera Stable"]
    browser = []
    for base in chromium:
        for sub in ("Cache", "Code Cache", "GPUCache", "Service Worker/CacheStorage"):
            browser += [Root(p) for p in _glob([base], f"*/{sub}")]            # Default, Profile 1 ... : the cache folder only, never the profile
            browser.append(Root(base / sub))
    browser += [Root(p) for p in _glob([L / "Mozilla" / "Firefox" / "Profiles"], "*/cache2")]
    apps = [Root(A / app / sub) for app in ("discord", "Slack", "Code", "Microsoft/Teams") for sub in ("Cache", "Code Cache", "GPUCache", "CachedData")]
    dev = [Root(L / "pip" / "Cache"), Root(L / "npm-cache"), Root(A / "npm-cache"), Root(L / "NuGet" / "v3-cache"), Root(L / "NuGet" / "plugins-cache")]
    exp = L / "Microsoft" / "Windows" / "Explorer"
    old = OLD_LOG_DAYS * DAY
    return [
        Category("user_temp", "Temporary files", "Scratch files programs leave in your temp folder.", "safe", "delete",
                 [Root(p, min_age_s=TEMP_GRACE_S) for p in env.user_temp]),
        Category("windows_temp", "Windows temporary files", "Scratch files Windows leaves in Windows\\Temp (needs admin rights for some).", "safe", "delete",
                 [Root(W / "Temp", exempt=True, min_age_s=TEMP_GRACE_S)]),
        Category("browser_cache", "Browser cache", "Saved page pieces in Chrome, Edge, Brave, Opera and Firefox. Not cookies, passwords or history.", "safe", "delete", browser),
        Category("app_cache", "App cache", "Cache folders of Discord, Slack, VS Code and Teams. Not their accounts or settings.", "safe", "delete", apps),
        Category("thumbnail_cache", "Thumbnail and icon cache", "Pictures Windows made of your files; it rebuilds them.", "safe", "delete",
                 [Root(exp, patterns=("thumbcache_*.db", "iconcache_*.db"), depth=0), Root(L, patterns=("IconCache.db",), depth=0)]),
        Category("crash_logs", "Crash dumps and old logs", f"Crash reports and logs older than {OLD_LOG_DAYS} days.", "safe", "delete",
                 [Root(L / "CrashDumps", min_age_s=old), Root(W / "Minidump", exempt=True, min_age_s=old), Root(W / "MEMORY.DMP", exempt=True, min_age_s=old, depth=0),
                  Root(L / "Microsoft" / "Windows" / "WER" / "ReportArchive", min_age_s=old), Root(L / "Microsoft" / "Windows" / "WER" / "ReportQueue", min_age_s=old),
                  Root(W / "Logs", exempt=True, patterns=("*.log", "*.etl"), min_age_s=old)]),
        Category("update_cache", "Windows Update downloads", "Update files Windows already installed.", "safe", "delete",
                 [Root(W / "SoftwareDistribution" / "Download", exempt=True)]),
        Category("recycle_bin", "Recycle Bin", "Files you already deleted. This empties the bin for good.", "safe", "bin_empty"),
        Category("dev_caches", "pip, npm and NuGet caches", "Downloaded packages programs fetch again when needed.", "safe", "delete", dev),
        Category("duplicates", "Duplicate files", "Same content in Downloads or Documents; the newest copy stays.", "review", "bin", finder=Cleaner._find_duplicates),
        Category("old_installers", "Old installers", f"Setup files (.exe, .msi) in Downloads older than {OLD_INSTALLER_DAYS} days.", "review", "bin", finder=Cleaner._find_installers),
        Category("big_unused", "Big files not opened lately", f"Files over 500 MB not opened for {BIG_FILE_UNUSED_DAYS} days.", "review", "bin", finder=Cleaner._find_big),
        Category("empty_folders", "Empty folders", "Folders with nothing inside, in Downloads, Documents and Desktop.", "review", "bin", finder=Cleaner._find_empty),
    ]


# ---- the cleaner --------------------------------------------------------------------------------------------------------------------------

class Cleaner:
    def __init__(self, env: Env | None = None, recycler: Any = None, clock: Callable[[], float] = time.time, remove: Callable[[str], None] = os.remove,
                 hash_budget_s: float = HASH_BUDGET_S):
        self.env = env or Env.detect()
        self.recycler = recycler or Recycler()
        self.clock, self.remove, self.hash_budget_s = clock, remove, hash_budget_s
        self.guard = Guard(self.env)
        self.catalog = catalog(self.env)
        self.by_id = {c.id: c for c in self.catalog}
        self._entries: dict[str, list[Entry]] = {}

    # -- finding -----------------------------------------------------------------------------------------------------------------------
    def _ok(self, path: str, exempt: Iterable[Any] = ()) -> bool:
        return self.guard.reason(path, exempt) is None

    def _root_entries(self, cat: Category, now: float) -> list[Entry]:
        out: list[Entry] = []
        for r in cat.roots:
            ex = [r.path] if r.exempt else []
            if r.path.is_file():
                try:
                    st = os.lstat(r.path)
                except OSError:
                    continue
                if stat.S_ISREG(st.st_mode) and not _is_link(st) and now - st.st_mtime >= r.min_age_s and self._ok(str(r.path), ex):
                    out.append(Entry(str(r.path), st.st_size, st.st_mtime))
                continue
            for path, st, _ in walk_files(r.path, r.depth):
                if r.patterns and not any(fnmatch.fnmatch(os.path.basename(path).lower(), pat.lower()) for pat in r.patterns):
                    continue
                if now - st.st_mtime < r.min_age_s or not self._ok(path, ex):
                    continue
                out.append(Entry(path, st.st_size, st.st_mtime))
        seen: set = set()
        return [e for e in out if not (e.path in seen or seen.add(e.path))]

    def _roots_of(self, *names: str) -> list[Path]:
        return [self.env.known[n] for n in names if self.env.known[n].is_dir()]

    def _find_duplicates(self, cat: Category, now: float) -> list[Entry]:
        by_size: dict[int, list[tuple[str, os.stat_result]]] = {}
        for root in self._roots_of("Downloads", "Documents"):
            for path, st, _ in walk_files(root, 4, SKIP_DIRS):
                if st.st_size == 0 or st.st_size > HASH_CAP_BYTES or SECRET_NAME.search(os.path.basename(path)) or not self._ok(path):
                    continue
                by_size.setdefault(st.st_size, []).append((path, st))
        deadline = time.monotonic() + self.hash_budget_s
        out: list[Entry] = []
        for size, files in by_size.items():
            if len(files) < 2:
                continue
            groups: dict[str, list[tuple[str, os.stat_result]]] = {}
            for path, st in files:
                if time.monotonic() > deadline:
                    return out                                       # out of time: what was proven so far is still right
                digest = _sha256(path)
                if digest:
                    groups.setdefault(digest, []).append((path, st))
            for same in groups.values():
                if len(same) < 2:
                    continue
                same.sort(key=lambda f: (-f[1].st_mtime, len(f[0]), f[0]))        # newest first, then the shortest path
                keep = os.path.basename(same[0][0])
                out += [Entry(p, st.st_size, st.st_mtime, note=f"same as {_label(keep, 60)}") for p, st in same[1:]]
        return out

    def _find_installers(self, cat: Category, now: float) -> list[Entry]:
        out: list[Entry] = []
        for root in self._roots_of("Downloads"):
            for path, st, _ in walk_files(root, 2, SKIP_DIRS):
                if path.lower().endswith((".exe", ".msi")) and now - st.st_mtime >= OLD_INSTALLER_DAYS * DAY and self._ok(path):
                    out.append(Entry(path, st.st_size, st.st_mtime))
        return out

    def _find_big(self, cat: Category, now: float) -> list[Entry]:
        out: list[Entry] = []
        for root in self._roots_of("Downloads", "Documents", "Desktop", "Videos", "Pictures", "Music"):
            for path, st, _ in walk_files(root, 5, SKIP_DIRS):
                if st.st_size >= BIG_FILE_BYTES and now - max(st.st_atime, st.st_mtime) >= BIG_FILE_UNUSED_DAYS * DAY and self._ok(path):
                    out.append(Entry(path, st.st_size, st.st_mtime))
        return out

    def _find_empty(self, cat: Category, now: float) -> list[Entry]:
        out: list[Entry] = []
        for root in self._roots_of("Downloads", "Documents", "Desktop"):
            has_file: dict[str, bool] = {}
            dirs: list[str] = []
            for path, st, is_dir in walk_files(root, 6, SKIP_DIRS, want_dirs=True):
                if is_dir:
                    dirs.append(path)
                else:
                    parent = os.path.dirname(path)
                    while len(parent) > len(str(root)):               # every folder above a file is not empty
                        if has_file.get(parent):
                            break
                        has_file[parent] = True
                        parent = os.path.dirname(parent)
            empty = sorted(d for d in dirs if not has_file.get(d) and not os.path.basename(d).startswith(".") and self._ok(d))
            top: list[str] = []
            for d in empty:
                if not any(_within(_norm(d), _norm(t)) for t in top):
                    top.append(d)
            out += [Entry(d, 0, os.lstat(d).st_mtime, is_dir=True) for d in top if _safe_lstat(d)]
        return out

    def fresh(self, cat_id: str) -> Category:
        """The row with its folders looked up NOW (browser profiles come and go)."""
        return next(c for c in catalog(self.env) if c.id == cat_id)

    def entries(self, cat_id: str, now: float | None = None) -> list[Entry]:
        cat = self.fresh(cat_id)
        now = self.clock() if now is None else now
        if cat.disposal == "bin_empty":
            size, n = self.recycler.bin_stats()
            return [Entry("Recycle Bin", int(size), note=str(n))] if n else []
        return cat.finder(self, cat, now) if cat.finder else self._root_entries(cat, now)

    # -- scan ---------------------------------------------------------------------------------------------------------------------------
    def summary(self, cat: Category, entries: list[Entry]) -> dict:
        if cat.disposal == "bin_empty":
            files = int(entries[0].note) if entries else 0
        else:
            files = len(entries)
        row: dict[str, Any] = {"id": cat.id, "label": cat.label, "what_it_is": cat.what, "bytes": sum(e.size for e in entries), "files": files,
                               "kind": cat.kind, "default_checked": cat.kind == "safe"}
        if cat.kind == "review":
            biggest = sorted(entries, key=lambda e: e.size, reverse=True)
            row["top"] = [{"id": item_id(e.path), "label": self.display(e.path), "bytes": e.size, "note": e.note} for e in biggest[:TOP_ITEMS]]
            row["items"] = [{"id": item_id(e.path), "label": self.display(e.path), "name": _label(os.path.basename(e.path)), "folder": _label(self.display(os.path.dirname(e.path)), 80),
                             "bytes": e.size, "note": e.note} for e in biggest[:LIST_ITEMS]]
        return row

    def display(self, path: str) -> str:
        try:
            return _label(os.path.relpath(path, self.env.home)) if _within(_norm(path), _norm(self.env.home)) else _label(path)
        except ValueError:
            return _label(path)

    def scan_category(self, cat_id: str) -> dict:
        cat = self.by_id[cat_id]
        entries = self.entries(cat_id)
        self._entries[cat_id] = entries
        return self.summary(cat, entries)

    def scan(self, on_category: Callable[[dict, int, int], None] | None = None) -> dict:
        rows = []
        for i, cat in enumerate(self.catalog):
            row = self.scan_category(cat.id)
            rows.append(row)
            if on_category:
                on_category(row, i + 1, len(self.catalog))
        return {"categories": rows, "safe_bytes": sum(r["bytes"] for r in rows if r["kind"] == "safe"), "scanned_at": self.clock()}

    # -- clean --------------------------------------------------------------------------------------------------------------------------
    def clean(self, selection: dict, dry_run: bool = False, on_progress: Callable[[dict], None] | None = None, rescan: bool = True,
              should_stop: Callable[[], bool] | None = None) -> dict:
        """Remove exactly `selection`: {"categories": [ids], "items": {category id: [item ids]}}. A category in `items` only (not in
        `categories`) removes just those items. Locked, vanished and blocked files are skipped and counted; then everything is scanned again."""
        wanted: dict[str, set | None] = {}
        for cid in (selection.get("categories") or []):
            if cid in self.by_id:
                wanted[cid] = None
        for cid, ids in (selection.get("items") or {}).items():
            if cid in self.by_id and self.by_id[cid].kind == "review" and cid not in wanted:
                wanted[cid] = {str(i) for i in ids}
        now = self.clock()
        plan: list[tuple[Category, list[Entry]]] = []
        for cid, only in wanted.items():
            cat = self.fresh(cid)
            found = self.entries(cid, now)                              # fresh: what is there NOW, not what an old scan saw
            plan.append((cat, [e for e in found if only is None or item_id(e.path) in only] if cat.disposal != "bin_empty" else found))
        total_bytes = sum(e.size for _, es in plan for e in es)
        total_items = sum(len(es) for _, es in plan)
        res: dict[str, Any] = {"dry_run": dry_run, "freed_bytes": 0, "to_bin_bytes": 0, "deleted": 0, "binned": 0, "skipped": 0, "blocked": 0,
                               "undo": [], "undo_more": 0, "categories": {}, "bytes_planned": total_bytes, "items_planned": total_items}
        done_bytes = done_items = 0
        last = 0.0
        for cat, es in plan:
            part = {"freed_bytes": 0, "to_bin_bytes": 0, "deleted": 0, "binned": 0, "skipped": 0}
            exempt = [r.path for r in cat.roots if r.exempt]
            if cat.disposal == "bin_empty":
                if not dry_run and es:
                    if self.recycler.bin_empty():
                        part.update(freed_bytes=es[0].size, deleted=int(es[0].note or 0))
                    else:
                        part["skipped"] = int(es[0].note or 0)
                elif dry_run and es:
                    part.update(freed_bytes=es[0].size, deleted=int(es[0].note or 0))
                if es:                                                   # the bin counts as one step of the progress
                    done_bytes += es[0].size
                    done_items += 1
                    if on_progress:
                        on_progress({"label": cat.label, "done_items": done_items, "total_items": total_items, "done_bytes": done_bytes, "total_bytes": total_bytes})
                es = []
            for e in es:
                if should_stop and should_stop():
                    res["stopped"] = True
                    break
                why = self.guard.reason(e.path, exempt)
                if why:
                    part["skipped"] += 1
                    res["blocked"] += 1
                elif dry_run:
                    part["binned" if cat.disposal == "bin" else "deleted"] += 1
                    part["to_bin_bytes" if cat.disposal == "bin" else "freed_bytes"] += e.size
                else:
                    self._remove_one(cat, e, part, res)
                done_bytes += e.size
                done_items += 1
                if on_progress and (self.clock() - last >= 0.4 or done_items == total_items):
                    last = self.clock()
                    on_progress({"label": cat.label, "done_items": done_items, "total_items": total_items, "done_bytes": done_bytes, "total_bytes": total_bytes})
            if not dry_run and cat.disposal == "delete":
                self._prune_empty(cat)
            res["categories"][cat.id] = part
            for k in ("freed_bytes", "to_bin_bytes", "deleted", "binned", "skipped"):
                res[k] += part[k]
        if rescan and not dry_run:
            res["scan"] = self.scan()
        return res

    def _remove_one(self, cat: Category, e: Entry, part: dict, res: dict) -> None:
        try:
            st = os.lstat(e.path)
        except OSError:
            return                                                       # gone already: nothing to free, nothing to count
        if _is_link(st) or stat.S_ISDIR(st.st_mode) != e.is_dir:
            part["skipped"] += 1                                         # swapped for a link or another kind since the listing
            return
        try:
            if cat.disposal == "bin":
                self.recycler.trash(e.path)
                part["binned"] += 1
                part["to_bin_bytes"] += e.size
                if len(res["undo"]) < UNDO_LIMIT:
                    res["undo"].append({"label": self.display(e.path), "path": e.path, "bytes": e.size, "category": cat.id})
                else:
                    res["undo_more"] += 1
            else:
                try:
                    self.remove(e.path)
                except PermissionError:
                    os.chmod(e.path, stat.S_IWRITE)                     # a read-only cache file; a locked one fails again below
                    self.remove(e.path)
                part["deleted"] += 1
                part["freed_bytes"] += e.size
        except (OSError, CleanerError):
            part["skipped"] += 1                                         # in use, no rights, or no recycler

    def _prune_empty(self, cat: Category) -> None:
        for r in cat.roots:
            if r.depth == 0 or not r.path.is_dir():
                continue
            for dirpath, _dirs, _files in os.walk(r.path, topdown=False, followlinks=False):
                if dirpath == str(r.path) or not self._ok(dirpath, [r.path] if r.exempt else ()):
                    continue
                try:
                    st = os.lstat(dirpath)
                    if not _is_link(st):
                        os.rmdir(dirpath)                               # only succeeds when empty
                except OSError:
                    pass


def item_id(path: str) -> str:
    return hashlib.sha1(_norm(path).encode("utf-8", "replace")).hexdigest()[:12]


def _safe_lstat(path: str) -> bool:
    try:
        os.lstat(path)
        return True
    except OSError:
        return False


def _sha256(path: str) -> str:
    h = hashlib.sha256()
    try:
        with open(path, "rb") as f:
            for block in iter(lambda: f.read(1 << 20), b""):
                h.update(block)
    except OSError:
        return ""
    return h.hexdigest()


def model_view(value: Any) -> Any:
    """What an online chat model may see: category ids, fixed labels, kinds, sizes and counts. File and folder names, paths and notes are removed."""
    if isinstance(value, list):
        return [model_view(v) for v in value]
    if not isinstance(value, dict):
        return value
    drop = {"top", "items", "undo", "path", "note", "what_it_is"}
    return {k: model_view(v) for k, v in value.items() if k not in drop}
