"""D54, simple tasks of any kind, on the node: files in the known folders, the delete family, a few reversible settings, the clipboard.

The kernel has already authorized the job (policy.py: effect classes A / B / C and the grant that comes from the owner's own goal words); this module only
does what the job names, and re-checks the rules that must hold even if the kernel were wrong:

  * Folders: only the known folders (Documents, Desktop, Downloads, Pictures, Videos, Music, outputs, the projects of config/projects.json) and folders that
    already exist inside them. A drive letter, "..", a wildcard, a link out of a root, a system folder, the user profile root, a drive root, the secrets
    folder or JARVIS's own code / vault / memory / audit folders: refused, for every operation (`protected`).
  * Nothing is overwritten. A new name that exists gets " (2)", " (3)" ... File operations go through FileOps (fileops.py): a journal written before each
    change and an exact undo.
  * Delete follows the owner's words (the kernel decides which op it is): `trash` = the Recycle Bin (send2trash's call, SHFileOperation with FOF_ALLOWUNDO),
    verified gone from its folder AND present in the bin; `delete_permanent` = a real deletion of the one named item; `cleanup` = ONLY the fixed safe list
    below, measured first and reported after, with other removable things LISTED and never deleted; `empty_bin` = the bin.
  * Settings and the clipboard are fixed PowerShell scripts with a read-back; nothing the caller writes is ever run.
Reused: send2trash (arsenetar, BSD) when installed, else the same Windows call it makes; FileOps and filesearch of this node. Custom: the rules around them.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import time
import zipfile

from . import filesearch, savefile as sf, sensitive
from .savefile import SafeError

NAME_PLAIN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 _.()\-]{0,79}$")
RECENT_S = 120                                   # a file touched in the last two minutes may still be downloading: sort_downloads leaves it
PARTIAL = re.compile(r"\.(crdownload|part|partial|tmp|download)$", re.I)
MAX_SORT = 300
MAX_ZIP_FILES, MAX_ZIP_BYTES = 5000, 500_000_000
MAX_UNZIP_FILES, MAX_UNZIP_BYTES = 20000, 1_000_000_000
TYPE_FOLDERS = (("Images", {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp", ".svg", ".heic", ".tif", ".tiff"}),
                ("Documents", {".pdf", ".doc", ".docx", ".txt", ".rtf", ".odt", ".md", ".xls", ".xlsx", ".csv", ".ppt", ".pptx", ".epub"}),
                ("Videos", {".mp4", ".mkv", ".mov", ".avi", ".webm", ".wmv"}),
                ("Music", {".mp3", ".wav", ".flac", ".m4a", ".ogg", ".aac"}),
                ("Archives", {".zip", ".7z", ".rar", ".tar", ".gz"}),
                ("Installers", {".exe", ".msi", ".msix", ".iso", ".dmg"}))
IMAGE_TYPES = {".png", ".jpg", ".jpeg", ".bmp", ".gif", ".webp"}
UNDO_NOTE = "undo list kept in the file operations journal (ask me to undo the last sort)"


def _fail(code: str, public: str, **extra) -> SafeError:
    return SafeError(code, public, **extra)


class Ctx:
    """Everything a file task needs from outside, with test seams: the roots, the journal, how to trash, how to see the Recycle Bin, the environment."""

    def __init__(self, roots: dict | None = None, journal: str | None = None, trasher=None, bin_lister=None, bin_emptier=None, env: dict | None = None,
                 now=time.time, launcher=None, protected_extra: tuple = (), scan_only: bool = False):
        self.roots = roots if roots is not None else sf.folder_roots()
        self.env = os.environ if env is None else env
        self.now = now
        self.trasher = trasher or _trash_real
        self.bin_lister = bin_lister or _bin_real
        self.bin_emptier = bin_emptier or _empty_bin_real
        self.launcher = launcher
        self.scan_only = scan_only                     # tests: a plain directory walk, never Everything's es.exe
        self.protected_extra = tuple(protected_extra)
        if journal is None:
            from .jobs import outputs_root
            journal = os.path.join(outputs_root(), "fileops", "journal.jsonl")
        from .fileops import FileOps
        self.fo = FileOps(list(self.roots.values()), journal, protected=(".git", ".ssh", ".gnupg", ".aws"), clock=now,
                          trash_dir=os.path.join(os.path.dirname(os.path.abspath(journal)), "trash"))


# ---------------------------------------------------------------------------------------------------------------- protected places
def _sys_dirs(env) -> list:
    out = []
    for k in ("SystemRoot", "windir", "ProgramFiles", "ProgramFiles(x86)", "ProgramData", "ProgramW6432"):
        if env.get(k):
            out.append(env[k])
    return [os.path.normcase(os.path.realpath(p)) for p in out]


def protected_real(path: str, ctx: Ctx) -> str:
    """Why this real path may never be deleted, moved or changed ("" when it may)."""
    from .paths import jarvis_home
    p = os.path.normcase(os.path.realpath(path))
    if os.path.dirname(p) == p:
        return "a drive root"
    if p in {os.path.normcase(os.path.realpath(r)) for r in ctx.roots.values()}:
        return "a known folder itself, not something in it"
    home = os.path.normcase(os.path.realpath(os.path.expanduser("~")))
    if p == home or os.path.normcase(os.path.dirname(home)) == p:
        return "the user profile root"
    for d in _sys_dirs(ctx.env):
        if p == d or p.startswith(d + os.sep):
            return "a system folder"
    jh = os.path.normcase(os.path.realpath(jarvis_home()))
    if p == jh or p.startswith(jh + os.sep) or "jarvis-secrets" in p:
        return "JARVIS's own folders or the secrets folder"
    if filesearch.is_secret_path(p) or sensitive.why(p):
        return "a secrets or sensitive place"
    if any(os.path.normcase(os.path.realpath(x)) in (p,) or p.startswith(os.path.normcase(os.path.realpath(x)) + os.sep) for x in ctx.protected_extra):
        return "a protected path"
    return ""


def _bare(name: str, what: str = "name") -> str:
    n = str(name or "").strip()
    if not NAME_PLAIN.match(n) or n.endswith((".", " ")) or ".." in n or sf.RESERVED.match(n) or n.startswith("."):
        raise _fail("name", f"that is not a plain {what} (letters, digits, spaces, dots, dashes; at most 80 characters)")
    return n


def _entry(base: str, rel: str) -> str:
    """The existing entry `rel` (one or more parts) inside `base`, matched by exact name (case does not matter). Never fuzzy: "delete the old one" is a question."""
    cur = base
    parts = [x for x in re.split(r"[\\/]+", rel) if x]
    if not parts:
        raise _fail("no_item", "no item was named")
    for part in parts:
        if part in (".", "..") or part.startswith("."):
            raise _fail("name", "that name is not one I may touch")
        try:
            hit = next((e for e in os.listdir(cur) if e.lower() == part.lower()), None)
        except OSError:
            hit = None
        if hit is None:
            raise _fail("no_item", "no item with that name is in that folder")
        cur = os.path.join(cur, hit)
    return cur


def _folder(spec: str, ctx: Ctx) -> tuple:
    return sf.resolve_folder(spec, ctx.roots)


def _item(p: dict, ctx: Ctx) -> tuple:
    """(real path of the named item, folder label). Inside a known folder, never the folder itself, never a protected place."""
    folder, label = _folder(p["target"], ctx)
    path = _entry(folder, p["path"])
    real = os.path.realpath(path) if not os.path.islink(path) else os.path.join(os.path.realpath(os.path.dirname(path)), os.path.basename(path))
    if not any(sf._inside(real, r) for r in ctx.roots.values()):
        raise _fail("outside", "that item is not inside the known folders")
    why = protected_real(real, ctx)
    if why:
        raise _fail("protected", f"I never touch {why}")
    return real, label


def _size(path: str, cap: int = 10**10) -> int:
    total = 0
    try:
        if os.path.isfile(path) or os.path.islink(path):
            return os.lstat(path).st_size
        for root, dirs, files in os.walk(path):
            for f in files:
                try:
                    total += os.lstat(os.path.join(root, f)).st_size
                except OSError:
                    pass
                if total > cap:
                    return total
    except OSError:
        pass
    return total


# ---------------------------------------------------------------------------------------------------------------- create, rename, move, copy
def mkdir(p, ctx):
    parent, label = _folder(p["target"], ctx)
    name, renamed = sf.free_name(parent, _bare(p["path"], "folder name"))
    full = os.path.join(parent, name)
    r = ctx.fo.create_folder(full)
    ok = os.path.isdir(full)
    return {"ok": ok, "created": name, "folder": label, "renamed": renamed, "verified": ok, "op_id": r["op_id"]}


def create_file(p, ctx):
    parent, label = _folder(p["target"], ctx)
    name, renamed = sf.free_name(parent, sf.safe_name(p["path"]))
    full = os.path.join(parent, name)
    r = ctx.fo.create_file(full, p["text"])
    got = sf.read_text(full)
    content_ok = got is not None and sf.normalize_text(got) == sf.normalize_text(p["text"])
    return {"ok": os.path.isfile(full) and content_ok, "created": name, "folder": label, "renamed": renamed, "file_ok": os.path.isfile(full),
            "content_ok": content_ok, "op_id": r["op_id"]}


def rename(p, ctx):
    real, label = _item(p, ctx)
    new = _bare(p["dest"], "name")
    if os.path.isfile(real) and not os.path.splitext(new)[1]:
        new += os.path.splitext(real)[1]                                       # "rename report.docx to final": the extension stays
    for n in (real, new):
        if os.path.splitext(n)[1].lower() in sf.BLOCKED_TYPES:
            raise _fail("type", "I do not rename programs or scripts, or to a program or script name")
    parent = os.path.dirname(real)
    final, renamed = sf.free_name(parent, new)
    r = ctx.fo.rename(real, final)
    ok = os.path.lexists(os.path.join(parent, final)) and not os.path.lexists(real)
    return {"ok": ok, "renamed_to": final, "folder": label, "numbered": renamed, "verified": ok, "op_id": r["op_id"]}


def _transfer(p, ctx, op):
    real, label = _item(p, ctx)
    dest, dlabel = _folder(p["dest"], ctx)
    name, numbered = sf.free_name(dest, os.path.basename(real))
    target = os.path.join(dest, name)
    r = getattr(ctx.fo, op)(real, target)
    ok = os.path.lexists(target) and (op == "copy" or not os.path.lexists(real))
    return {"ok": ok, {"copy": "copied", "move": "moved"}[op]: name, "from": label, "to": dlabel, "numbered": numbered, "verified": ok, "op_id": r["op_id"]}


def move(p, ctx):
    return _transfer(p, ctx, "move")


def copy(p, ctx):
    return _transfer(p, ctx, "copy")


# ---------------------------------------------------------------------------------------------------------------- zip, unzip, sort, find, reveal, undo
def zip_item(p, ctx):
    real, label = _item(p, ctx)
    parent = os.path.dirname(real)
    stem = os.path.splitext(os.path.basename(real))[0] if os.path.isfile(real) else os.path.basename(real)
    name, numbered = sf.free_name(parent, _bare(stem + ".zip", "archive name"))
    out = os.path.join(parent, name)
    files = []
    if os.path.isdir(real):
        for root, dirs, fs in os.walk(real):
            dirs[:] = [d for d in dirs if not d.startswith(".") and not os.path.islink(os.path.join(root, d))]
            for f in fs:
                fp = os.path.join(root, f)
                if not os.path.islink(fp) and not f.startswith(".") and not filesearch.is_secret_path(fp):
                    files.append((fp, os.path.relpath(fp, os.path.dirname(real))))
    else:
        files.append((real, os.path.basename(real)))
    if len(files) > MAX_ZIP_FILES or sum(os.path.getsize(f) for f, _ in files) > MAX_ZIP_BYTES:
        raise _fail("too_big", "that is too much to zip in one go")

    def act(_i):
        with zipfile.ZipFile(out, "x", zipfile.ZIP_DEFLATED) as z:
            for fp, arc in files:
                z.write(fp, arc.replace(os.sep, "/"))
        h = hashlib.sha256(open(out, "rb").read()).hexdigest()
        return {"sha": h, "size": os.path.getsize(out)}
    r = ctx.fo._run("create_file", None, out, None, 0, act)
    with zipfile.ZipFile(out) as z:
        ok = z.testzip() is None and len(z.namelist()) == len(files)
    return {"ok": ok, "archive": name, "folder": label, "files": len(files), "numbered": numbered, "verified": ok, "op_id": r["op_id"]}


def unzip(p, ctx):
    real, label = _item(p, ctx)
    if not (os.path.isfile(real) and real.lower().endswith(".zip") and zipfile.is_zipfile(real)):
        raise _fail("type", "that is not a zip archive")
    parent = os.path.dirname(real)
    name, numbered = sf.free_name(parent, _bare(os.path.splitext(os.path.basename(real))[0], "folder name"))
    out = os.path.join(parent, name)
    with zipfile.ZipFile(real) as z:
        infos = z.infolist()
        if len(infos) > MAX_UNZIP_FILES or sum(i.file_size for i in infos) > MAX_UNZIP_BYTES:
            raise _fail("too_big", "that archive is too big to unpack in one go")
        base = os.path.realpath(out)
        for i in infos:                                           # zip-slip: every member must land inside the new folder
            target = os.path.realpath(os.path.join(out, i.filename))
            if not (target == base or target.startswith(base + os.sep)) or i.filename.startswith(("/", "\\")) or ":" in i.filename.split("/")[0]:
                raise _fail("unsafe", "that archive has a path that would leave the new folder; I did not unpack it")
        os.makedirs(out)
        try:
            z.extractall(out)
        except BaseException:
            shutil.rmtree(out, ignore_errors=True)
            raise
        files = [i for i in infos if not i.is_dir()]
    ok = all(os.path.isfile(os.path.join(out, i.filename)) for i in files)
    return {"ok": ok, "unpacked_to": name, "folder": label, "files": len(files), "numbered": numbered, "verified": ok}


def _type_folder(name: str):
    ext = os.path.splitext(name)[1].lower()
    return next((f for f, exts in TYPE_FOLDERS if ext in exts), None)


def sort_downloads(p, ctx):
    folder, label = _folder(p["target"] or "Downloads", ctx)
    moved, by_type, undo, skipped = 0, {}, [], {"recent": 0, "other": 0, "hidden": 0}
    for e in sorted(os.scandir(folder), key=lambda e: e.name.lower()):
        if moved >= MAX_SORT:
            break
        if e.is_dir(follow_symlinks=False) or e.is_symlink():
            continue
        if e.name.startswith((".", "~$")) or PARTIAL.search(e.name):
            skipped["hidden"] += 1
            continue
        if ctx.now() - e.stat().st_mtime < RECENT_S:
            skipped["recent"] += 1
            continue
        sub = _type_folder(e.name)
        if not sub:
            skipped["other"] += 1
            continue
        if protected_real(e.path, ctx):
            continue
        dest = os.path.join(folder, sub)
        if not os.path.isdir(dest):
            undo.append(ctx.fo.create_folder(dest)["op_id"])
        name, _ = sf.free_name(dest, e.name)
        undo.append(ctx.fo.move(e.path, os.path.join(dest, name))["op_id"])
        moved += 1
        by_type[sub] = by_type.get(sub, 0) + 1
    record = os.path.join(os.path.dirname(ctx.fo.journal_path), f"sort-undo-{int(ctx.now())}.json")
    try:
        with open(record, "w", encoding="utf-8") as fh:
            json.dump({"folder": label, "ops": undo}, fh)
    except OSError:
        pass
    return {"ok": True, "folder": label, "moved": moved, "by_type": by_type, "skipped": skipped, "undo": undo[-50:], "note": UNDO_NOTE, "verified": True}


def _last_sort_record(ctx):
    """(path, op ids) of the newest sort's undo list when the newest operation in the journal belongs to it, else None."""
    d = os.path.dirname(ctx.fo.journal_path)
    try:
        names = sorted(f for f in os.listdir(d) if f.startswith("sort-undo-") and f.endswith(".json"))
        if not names:
            return None
        path = os.path.join(d, names[-1])
        with open(path, encoding="utf-8") as fh:
            ops = [str(x) for x in (json.load(fh).get("ops") or [])]
        newest = ctx.fo.history(1)
    except (OSError, ValueError):
        return None
    return (path, ops) if ops and newest and newest[0].get("op_id") in ops else None


def undo(p, ctx):
    ids = [x for x in re.split(r"[,\s]+", p["path"]) if x]
    done = []
    if not ids or ids == ["last"]:
        rec = _last_sort_record(ctx)
        if rec is not None:                                    # the newest thing done was a sort: all of it is put back, newest move first
            for oid in reversed(rec[1]):
                try:
                    done.append(ctx.fo.undo(oid))
                except Exception:                              # already undone or changed since: skipped, never forced
                    continue
            try:
                os.replace(rec[0], rec[0] + ".done")
            except OSError:
                pass
        else:
            done = ctx.fo.undo_last(1)
    else:
        for i in reversed(ids[:200]):
            done.append(ctx.fo.undo(i))
    return {"ok": True, "undone": len(done), "verified": True}


def _parse_filters(text: str) -> dict:
    q = {}
    for part in [x.strip() for x in str(text or "").split(";") if x.strip()]:
        m = re.fullmatch(r"size\s*([<>])\s*(\d+)\s*(kb|mb|gb)?", part, re.I)
        if m:
            n = int(m.group(2)) * {"kb": 1024, "mb": 1024**2, "gb": 1024**3, None: 1}[m.group(3).lower() if m.group(3) else None]
            q["min_size" if m.group(1) == ">" else "max_size"] = n
        m = re.fullmatch(r"(newer|older)\s*:\s*(\d+)\s*d", part, re.I)
        if m:
            q["modified_after" if m.group(1).lower() == "newer" else "modified_before"] = -int(m.group(2)) * 86400
        m = re.fullmatch(r"ext\s*:\s*([A-Za-z0-9]{1,8})", part, re.I)
        if m:
            q["extensions"] = (m.group(1),)
    return q


def find(p, ctx):
    spec = p["target"]
    roots = list(ctx.roots.values()) if spec.strip().lower() in ("all", "everywhere", "known folders") else [_folder(spec, ctx)[0]]
    f = _parse_filters(p["text"])
    for k in ("modified_after", "modified_before"):
        if k in f:
            f[k] = ctx.now() + f[k]
    q = filesearch.Query(text=p["path"], limit=20, max_depth=6, **f)
    backends = [filesearch.ScandirBackend()] if ctx.scan_only else None
    res = filesearch.search(q, roots, backends=backends)
    res.hits = [h for h in res.hits if not any(part.startswith(".") for part in os.path.relpath(
        h.path, next((r for r in ctx.roots.values() if sf._inside(h.path, r)), os.path.dirname(h.path))).split(os.sep))]       # hidden items are not listed
    hits = [{"name": h.name, "folder": next((lab for lab, r in ((k, v) for k, v in ctx.roots.items()) if sf._inside(h.path, r)), "?"),
             "path_rel": os.path.relpath(h.path, next((r for r in ctx.roots.values() if sf._inside(h.path, r)), os.path.dirname(h.path))),
             "bytes": h.size, "modified": time.strftime("%Y-%m-%d", time.localtime(h.modified)), "kind": h.kind} for h in res.hits]
    return {"ok": True, "count": len(hits), "matches": hits, "truncated": res.truncated, "verified": True}


def reveal(p, ctx):
    real, label = _item(p, ctx)
    args = ["explorer.exe", "/select," + real]
    if ctx.launcher is not None:
        ctx.launcher(args)
    else:
        from . import runtime
        runtime.popen(args, close_fds=True)
    return {"ok": True, "shown": os.path.basename(real), "folder": label, "verified": False,
            "note": "Explorer was asked to show it; whether the window opened is checked by the next view"}


# ---------------------------------------------------------------------------------------------------------------- the delete family
def _trash_real(path: str) -> None:
    try:
        from send2trash import send2trash                      # arsenetar/Send2Trash: the maintained library (reuse first)
    except ImportError:
        send2trash = None
    if send2trash is not None:
        send2trash(path)
        return
    import ctypes
    from ctypes import wintypes

    class SHFILEOPSTRUCTW(ctypes.Structure):
        _fields_ = [("hwnd", wintypes.HWND), ("wFunc", wintypes.UINT), ("pFrom", wintypes.LPCWSTR), ("pTo", wintypes.LPCWSTR),
                    ("fFlags", ctypes.c_ushort), ("fAnyOperationsAborted", wintypes.BOOL), ("hNameMappings", ctypes.c_void_p), ("lpszProgressTitle", wintypes.LPCWSTR)]
    FO_DELETE, FOF_SILENT, FOF_NOCONFIRMATION, FOF_ALLOWUNDO, FOF_NOERRORUI = 3, 0x4, 0x10, 0x40, 0x400
    op = SHFILEOPSTRUCTW(None, FO_DELETE, path + "\0", None, FOF_ALLOWUNDO | FOF_NOCONFIRMATION | FOF_NOERRORUI | FOF_SILENT, False, None, None)
    if ctypes.windll.shell32.SHFileOperationW(ctypes.byref(op)) != 0 or op.fAnyOperationsAborted:
        raise OSError("the Recycle Bin call failed")


BIN_LIST = r"""
$sh = New-Object -ComObject Shell.Application
$out = @(); foreach ($i in $sh.NameSpace(10).Items()) { $out += @{ name = $i.Name; from = [string]$i.ExtendedProperty('System.Recycle.DeletedFrom'); size = [int64]$i.Size } }
@($out) | ConvertTo-Json -Compress
"""


def _bin_real() -> list:
    from . import runtime
    r = runtime.powershell(BIN_LIST, {}, 60)
    out = (r.stdout or "").strip()
    data = json.loads(out) if out else []
    return data if isinstance(data, list) else [data]


def _empty_bin_real() -> None:
    import ctypes
    if ctypes.windll.shell32.SHEmptyRecycleBinW(None, None, 0x1 | 0x2 | 0x4) not in (0, -2147418113):       # NOCONFIRMATION | NOPROGRESSUI | NOSOUND (S_OK / already empty)
        raise OSError("the Recycle Bin could not be emptied")


def _in_bin(ctx: Ctx, name: str, folder: str) -> bool:
    try:
        items = ctx.bin_lister()
    except Exception:
        return False
    nf = os.path.normcase(folder).rstrip("\\/")
    return any(str(i.get("name", "")).lower() in (name.lower(), os.path.splitext(name)[0].lower()) and
               (not i.get("from") or os.path.normcase(str(i["from"])).rstrip("\\/") == nf) for i in items)


def trash(p, ctx):
    real, label = _item(p, ctx)
    name, folder = os.path.basename(real), os.path.dirname(real)
    size = _size(real)
    ctx.trasher(real)
    gone = not os.path.lexists(real)
    in_bin = _in_bin(ctx, name, folder)
    ok = gone and in_bin
    out = {"ok": ok, "trashed": name, "folder": label, "bytes": size, "gone": gone, "in_bin": in_bin, "reversible": True, "verified": ok}
    if gone and not in_bin:
        out["error"] = "it is gone from its folder, but I could not find it in the Recycle Bin; check the bin before relying on it"
    if not gone:
        out["error"] = "it is still in its folder"
    return out


def delete_permanent(p, ctx):
    real, label = _item(p, ctx)
    name, size = os.path.basename(real), _size(real)
    if os.path.isdir(real) and not os.path.islink(real):
        shutil.rmtree(real)
    else:
        os.remove(real)
    gone = not os.path.lexists(real)
    return {"ok": gone, "deleted": name, "folder": label, "bytes": size, "gone": gone, "permanent": True, "verified": gone}


def empty_bin(p, ctx):
    before = len(ctx.bin_lister())
    ctx.bin_emptier()
    after = len(ctx.bin_lister())
    return {"ok": after == 0, "items_before": before, "items_after": after, "permanent": True, "verified": after == 0}


# ---- the safe cleanup list: fixed, deterministic, never a known folder, never cookies / passwords / history / sessions / profiles
def cleanup_list(env) -> list:
    """[{id, label, dirs: [dirs whose CONTENTS go], files: [glob dirs, patterns]}] computed from the environment. Documents, Downloads, Desktop, Pictures,
    Videos, Music and everything of the browsers except their caches are not in it, and a test fails if one ever is."""
    g = lambda k: env.get(k) or ""            # noqa: E731
    local, roaming, temp, win = g("LOCALAPPDATA"), g("APPDATA"), g("TEMP") or g("TMP"), g("SystemRoot") or g("windir")
    j = lambda *a: os.path.join(*a) if all(a) else ""     # noqa: E731
    browsers = []
    for label, base in (("Chrome", j(local, "Google", "Chrome", "User Data")), ("Edge", j(local, "Microsoft", "Edge", "User Data")),
                        ("Brave", j(local, "BraveSoftware", "Brave-Browser", "User Data"))):
        if base and os.path.isdir(base):
            for prof in sorted(os.listdir(base)):
                if prof == "Default" or prof.startswith("Profile "):
                    for cache in ("Cache", "Code Cache", "GPUCache"):         # caches only: never Cookies, Login Data, History, Sessions or the profile itself
                        browsers.append(j(base, prof, cache))
    items = [
        {"id": "temp_user", "label": "your temporary files", "dirs": [temp]},
        {"id": "temp_windows", "label": "Windows' temporary files", "dirs": [j(win, "Temp")]},
        {"id": "browser_caches", "label": "browser caches (Chrome, Edge, Brave; not cookies, passwords, history or sessions)", "dirs": browsers},
        {"id": "inet_cache", "label": "the internet cache of Windows apps", "dirs": [j(local, "Microsoft", "Windows", "INetCache")]},
        {"id": "thumbnails", "label": "thumbnail and icon caches", "files": [(j(local, "Microsoft", "Windows", "Explorer"), r"^(thumbcache|iconcache).*\.db$")]},
        {"id": "crash_dumps", "label": "crash dumps and error reports", "dirs": [j(local, "CrashDumps"), j(local, "Microsoft", "Windows", "WER", "ReportArchive"),
                                                                              j(local, "Microsoft", "Windows", "WER", "ReportQueue")]},
        {"id": "old_logs", "label": "old Windows logs (older than 30 days)", "old_files": [(j(win, "Logs"), r"\.(log|etl)$", 30)]},
        {"id": "update_cache", "label": "the Windows Update download cache", "dirs": [j(win, "SoftwareDistribution", "Download")]},
        {"id": "pip_cache", "label": "the pip cache", "dirs": [j(local, "pip", "Cache")]},
        {"id": "npm_cache", "label": "the npm cache", "dirs": [j(local, "npm-cache"), j(roaming, "npm-cache")]},
        {"id": "nuget_cache", "label": "the NuGet cache", "dirs": [j(local, "NuGet", "v3-cache"), j(local, "NuGet", "Cache")]}]
    return items


def _cleanup_targets(item: dict, now: float) -> list:
    """The concrete paths (files and folders directly inside the cache dirs) that one list item would remove."""
    out = []
    for d in item.get("dirs", []):
        if d and os.path.isdir(d):
            out += [os.path.join(d, n) for n in os.listdir(d)]
    for d, rx in item.get("files", []):
        if d and os.path.isdir(d):
            out += [os.path.join(d, n) for n in os.listdir(d) if re.search(rx, n, re.I)]
    for d, rx, days in item.get("old_files", []):
        if d and os.path.isdir(d):
            for root, _dirs, files in os.walk(d):
                out += [os.path.join(root, n) for n in files if re.search(rx, n, re.I) and now - os.lstat(os.path.join(root, n)).st_mtime > days * 86400]
    return out


def _safe_cleanup_path(path: str, ctx: Ctx) -> bool:
    rp = os.path.realpath(path) if not os.path.islink(path) else os.path.join(os.path.realpath(os.path.dirname(path)), os.path.basename(path))
    if any(sf._inside(rp, r) for r in ctx.roots.values()):
        return False
    why = protected_real(rp, ctx)
    if why and not (why.startswith("JARVIS") and re.search(r"[\\/](tmp|temp|cache)([\\/]|$)", rp, re.I)):       # JARVIS's own tmp / cache may be cleaned; its code, vault, memory, audit may not
        return False
    return not any(rp.lower().endswith(x) for x in ("cookies", "login data", "history", "web data", "sessions"))


def suggestions(ctx: Ctx) -> list:
    """Things that LOOK removable but are the owner's: listed with sizes and asked about, never deleted on a broad phrase."""
    out = []
    dl = ctx.roots.get("downloads")
    if not dl or not os.path.isdir(dl):
        return out
    seen: dict = {}
    for e in os.scandir(dl):
        if not e.is_file(follow_symlinks=False) or e.name.startswith("."):
            continue
        st = e.stat()
        age = (ctx.now() - st.st_mtime) / 86400
        ext = os.path.splitext(e.name)[1].lower()
        if ext in (".exe", ".msi", ".msix", ".iso") and age > 30:
            out.append({"kind": "old installer", "name": e.name, "bytes": st.st_size, "age_days": int(age)})
        elif st.st_size > 200 * 1024 * 1024 and age > 60:
            out.append({"kind": "big old file", "name": e.name, "bytes": st.st_size, "age_days": int(age)})
        if st.st_size > 1024 * 1024:
            seen.setdefault(st.st_size, []).append(e.path)
    for size, paths in seen.items():
        if len(paths) > 1:
            digests: dict = {}
            for pth in paths[:6]:
                digests.setdefault(sf_hash(pth), []).append(os.path.basename(pth))
            out += [{"kind": "duplicate", "name": ", ".join(sorted(v)), "bytes": size * (len(v) - 1)} for v in digests.values() if len(v) > 1]
    return out[:30]


def sf_hash(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def cleanup(p, ctx):
    now = ctx.now()
    plan = []
    for item in cleanup_list(ctx.env):
        targets = [t for t in _cleanup_targets(item, now) if _safe_cleanup_path(t, ctx)]
        plan.append({"id": item["id"], "label": item["label"], "targets": targets, "bytes": sum(_size(t) for t in targets)})
    results, freed = [], 0
    for item in plan:                                                  # removed one list item at a time; a locked file is skipped, never forced
        got = 0
        for t in item["targets"]:
            before = _size(t)
            try:
                if os.path.islink(t) or os.path.isfile(t):
                    os.remove(t)
                else:
                    shutil.rmtree(t, ignore_errors=True)
            except OSError:
                pass
            got += before - (_size(t) if os.path.lexists(t) else 0)
        freed += got
        results.append({"id": item["id"], "label": item["label"], "planned_bytes": item["bytes"], "freed_bytes": got})
    bin_before = bin_after = None
    try:
        bin_before = len(ctx.bin_lister())
        ctx.bin_emptier()                                             # the Recycle Bin's contents are on the list
        bin_after = len(ctx.bin_lister())
    except Exception:
        pass
    return {"ok": True, "freed_bytes": freed, "results": results, "bin_items_before": bin_before, "bin_items_after": bin_after,
            "suggestions": suggestions(ctx), "verified": True,
            "note": "only the fixed safe list was removed; the suggestions are listed for you to decide and none was deleted"}


# ---------------------------------------------------------------------------------------------------------------- settings and the clipboard
def _ps(script: str, env: dict, run=None):
    if run is not None:
        return run(script, env)
    from . import runtime
    r = runtime.powershell(script, env, 30)
    if r.returncode:
        raise _fail("failed", "Windows did not accept that setting change")
    return r.stdout or ""


THEME = r"""
$k = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Themes\Personalize'; $v = [int]$env:JARVIS_SET_VALUE
Set-ItemProperty -Path $k -Name AppsUseLightTheme -Value $v; Set-ItemProperty -Path $k -Name SystemUsesLightTheme -Value $v
$a = (Get-ItemProperty -Path $k).AppsUseLightTheme; $s = (Get-ItemProperty -Path $k).SystemUsesLightTheme
"JARVIS_SET:" + $a + "," + $s
"""
DND = r"""
$k = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\PushNotifications'; if (-not (Test-Path $k)) { New-Item -Path $k -Force | Out-Null }
Set-ItemProperty -Path $k -Name ToastEnabled -Value ([int]$env:JARVIS_SET_VALUE) -Type DWord
"JARVIS_SET:" + (Get-ItemProperty -Path $k).ToastEnabled
"""
BRIGHTNESS = r"""
$m = Get-WmiObject -Namespace root/WMI -Class WmiMonitorBrightnessMethods; [void]$m.WmiSetBrightness(1, [byte][int]$env:JARVIS_SET_VALUE)
"JARVIS_SET:" + (Get-WmiObject -Namespace root/WMI -Class WmiMonitorBrightness).CurrentBrightness
"""
WALLPAPER = r"""
Add-Type -TypeDefinition 'using System.Runtime.InteropServices; public class WP { [DllImport("user32.dll", CharSet=CharSet.Unicode)] public static extern int SystemParametersInfo(int a, int b, string c, int d); }'
[void][WP]::SystemParametersInfo(20, 0, $env:JARVIS_SET_VALUE, 3)
"JARVIS_SET:" + (Get-ItemProperty 'HKCU:\Control Panel\Desktop').WallPaper
"""
LOCK = r"""
Add-Type -TypeDefinition 'using System.Runtime.InteropServices; public class LK { [DllImport("user32.dll")] public static extern bool LockWorkStation(); }'
"JARVIS_SET:" + [LK]::LockWorkStation()
"""
NIGHT = r"""
Start-Process 'ms-settings:nightlight'
"JARVIS_SET:page"
"""
RADIO = r"""
Add-Type -AssemblyName System.Runtime.WindowsRuntime
$null = [Windows.Devices.Radios.Radio, Windows.System.Devices, ContentType = WindowsRuntime]
$asTask = ([System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object { $_.Name -eq 'AsTask' -and $_.GetParameters().Count -eq 1 -and $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1' })[0]
function Await($op, [Type]$t) { $task = $asTask.MakeGenericMethod($t).Invoke($null, @($op)); $task.Wait(-1) | Out-Null; $task.Result }
$null = Await ([Windows.Devices.Radios.Radio]::RequestAccessAsync()) ([Windows.Devices.Radios.RadioAccessStatus])
$kind = $env:JARVIS_SET_KIND; $want = if ($env:JARVIS_SET_VALUE -eq '1') { [Windows.Devices.Radios.RadioState]::On } else { [Windows.Devices.Radios.RadioState]::Off }
$radios = Await ([Windows.Devices.Radios.Radio]::GetRadiosAsync()) ([System.Collections.Generic.IReadOnlyList[Windows.Devices.Radios.Radio]])
$seen = @(); foreach ($r in $radios) { if ([string]$r.Kind -eq $kind) { [void](Await ($r.SetStateAsync($want)) ([Windows.Devices.Radios.RadioAccessStatus])); $seen += [string]$r.State } }
"JARVIS_SET:" + ($seen -join ',')
"""
ONOFF = {"on": "1", "off": "0", "1": "1", "0": "0", "true": "1", "false": "0"}


def _read_back(out: str, expect) -> bool:
    m = re.search(r"JARVIS_SET:(.*)", out or "")
    return bool(m) and (expect is None or expect(m.group(1).strip()))


def setting(p, ctx, run=None):
    op, val = p["op"], p["text"].strip()
    if op in ("dark_mode", "light_mode"):
        v = "0" if op == "dark_mode" else "1"
        out = _ps(THEME, {"JARVIS_SET_VALUE": v}, run)
        ok = _read_back(out, lambda s: s == f"{v},{v}")
        return {"ok": ok, "setting": op, "read_back": ok, "verified": ok}
    if op == "dnd":
        on = ONOFF.get(val.lower(), "1")                                  # "do not disturb on" = toasts off
        out = _ps(DND, {"JARVIS_SET_VALUE": "0" if on == "1" else "1"}, run)
        ok = _read_back(out, lambda s: s == ("0" if on == "1" else "1"))
        return {"ok": ok, "setting": "do not disturb " + ("on" if on == "1" else "off"), "read_back": ok, "verified": ok}
    if op == "brightness":
        if not re.fullmatch(r"\d{1,3}", val) or not 0 <= int(val) <= 100:
            raise _fail("value", "brightness is a number from 0 to 100")
        out = _ps(BRIGHTNESS, {"JARVIS_SET_VALUE": val}, run)
        ok = _read_back(out, lambda s: s.isdigit() and abs(int(s) - int(val)) <= 3)
        return {"ok": ok, "setting": "brightness " + val, "read_back": ok, "verified": ok}
    if op == "wallpaper":
        folder_spec, _, name = val.rpartition("|")
        folder, _label = _folder(folder_spec or "Pictures", ctx)
        path = _entry(folder, name)
        if os.path.splitext(path)[1].lower() not in IMAGE_TYPES or not os.path.isfile(path) or protected_real(path, ctx):
            raise _fail("type", "the wallpaper must be a picture (png, jpg, bmp, gif, webp) in a known folder")
        out = _ps(WALLPAPER, {"JARVIS_SET_VALUE": path}, run)
        ok = _read_back(out, lambda s: os.path.normcase(s) == os.path.normcase(path))
        return {"ok": ok, "setting": "wallpaper", "read_back": ok, "verified": ok}
    if op == "lock":
        _ps(LOCK, {}, run)
        return {"ok": True, "setting": "screen lock", "verified": False, "note": "the lock was requested; Windows gives no read-back"}
    if op == "night_light":
        _ps(NIGHT, {}, run)
        return {"ok": True, "setting": "night light page opened", "verified": False, "partial": True,
                "note": "I opened the Night light page; flipping the switch itself is not built yet"}
    if op in ("wifi", "bluetooth", "airplane"):
        on = ONOFF.get(val.lower())
        if on is None:
            raise _fail("value", "say on or off")
        kind = {"wifi": "WiFi", "bluetooth": "Bluetooth", "airplane": "WiFi"}[op]
        out = _ps(RADIO, {"JARVIS_SET_KIND": kind, "JARVIS_SET_VALUE": on if op != "airplane" else ("0" if on == "1" else "1")}, run)
        ok = _read_back(out, lambda s: bool(s) and all(x.strip() == ("On" if (on == "1") != (op == "airplane") else "Off") for x in s.split(",")))
        return {"ok": ok, "setting": f"{op} {'on' if on == '1' else 'off'}", "read_back": ok, "verified": ok}
    raise _fail("unknown", "that setting is not one I change")


def clipboard(p, ctx, run=None):
    if p["op"] == "set":
        _ps(r"Set-Clipboard -Value $env:JARVIS_CLIP_TEXT; 'JARVIS_SET:ok'", {"JARVIS_CLIP_TEXT": p["text"]}, run)
        back = _ps(r"'JARVIS_SET:' + ((Get-Clipboard -Raw) -eq $env:JARVIS_CLIP_TEXT)", {"JARVIS_CLIP_TEXT": p["text"]}, run)
        ok = _read_back(back, lambda s: s.lower() == "true")
        return {"ok": ok, "chars": len(p["text"]), "read_back": ok, "verified": ok}
    out = _ps(r"$t = Get-Clipboard -Raw; if ($t -eq $null) { $t = '' }; 'JARVIS_CLIP:' + [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($t))", {}, run)
    m = re.search(r"JARVIS_CLIP:(\S*)", out or "")
    import base64
    text = base64.b64decode(m.group(1)).decode("utf-8", "replace") if m else ""
    from jarvis_kernel.scrub import scrub
    text = scrub(text[:4000])[0]
    from .diag import save_local
    return {"ok": True, "chars": len(text), "local_result": save_local("clipboard", {"text": text}),
            "note": "the clipboard text stayed on this laptop (local_result)", "verified": True}


# ---------------------------------------------------------------------------------------------------------------- the job entry
FILE_FUNCS = {"mkdir": mkdir, "create_file": create_file, "rename": rename, "move": move, "copy": copy, "zip": zip_item, "unzip": unzip,
              "sort_downloads": sort_downloads, "find": find, "reveal": reveal, "undo": undo, "trash": trash, "delete_permanent": delete_permanent,
              "cleanup": cleanup, "empty_bin": empty_bin}


def run(kind: str, p: dict, ctx: Ctx | None = None, **seams) -> dict:
    """The node's answer to a file_op / setting / clipboard job. A refusal or failure worded here (SafeError) comes back as {"ok": False, "error": ...}."""
    try:
        c = ctx or Ctx()
        if kind == "file_op":
            return FILE_FUNCS[p["op"]](p, c)
        if kind == "setting":
            return setting(p, c, **seams)
        if kind == "clipboard":
            return clipboard(p, c, **seams)
        return {"ok": False, "error": "unknown task"}
    except SafeError as e:
        return {"ok": False, "error": e.public, **{k: v for k, v in e.extra.items() if isinstance(v, (str, bool, int))}}
    except Exception as e:                       # noqa: BLE001 - the kind of failure only: its text could name a file
        name = type(e).__name__
        from .fileops import FileOpsError
        if isinstance(e, FileOpsError):
            return {"ok": False, "error": "the file operation was refused: " + name}
        return {"ok": False, "error": name}
