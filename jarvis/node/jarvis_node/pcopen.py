"""Open things on the owner's Windows PC for JARVIS ("open Spotify", "open the invoices folder in Downloads").

Runs only after the kernel authorized the job (the owner's own agent: at once; a lab agent: after the owner approves).
Nothing here runs a command line an agent wrote:
- apps come from Windows' own Start list (Get-StartApps) and open through shell:AppsFolder\\<AppID>;
- folders are the owner's known folders (Downloads, Documents, Desktop, Pictures, Videos, Music), resolved by Windows,
  and anything inside one is matched by name and must stay inside it;
- files open with their default app only if they are documents or media, never programs or scripts.
Custom code, justified in docs/01_RESEARCH_AUDIT.md: Windows-MCP's App tool opens any name with a shell; this keeps the
kernel's job model (fixed kinds, approvals, audit) and adds the folder and file-type limits.
"""
import difflib
import json
import os
import re
import subprocess
import time

from .sensitive import sensitive_target  # (the kernel's list, config/sensitive_sites.json, with the node's floor)

KNOWN_FOLDERS = {   # name -> Windows KNOWNFOLDERID
    "downloads": "{374DE290-123F-4565-9164-39C4925E467B}", "documents": "{FDD39AD0-238F-46AF-ADB4-6C85480369C7}",
    "desktop": "{B4BFCC3A-DB2C-424C-B029-7FE99A87C641}", "pictures": "{33E28130-4E1E-4676-835A-98395C3BC3BB}",
    "videos": "{18989B1D-99B5-455B-841C-AB7C74E4DDFC}", "music": "{4BD8D571-6D19-48D3-BE97-422220080E43}"}
DOC_TYPES = {".pdf", ".txt", ".md", ".rtf", ".doc", ".docx", ".odt", ".xls", ".xlsx", ".ods", ".csv", ".ppt", ".pptx",
             ".odp", ".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".svg", ".mp3", ".wav", ".m4a", ".flac", ".mp4",
             ".mkv", ".mov", ".avi", ".webm", ".html", ".htm", ".json", ".xml", ".log", ".zip", ".epub"}
SENSITIVE = re.compile(r"1password|bitwarden|keepass|lastpass|dashlane|password|credential|metamask|wallet|paypal|bank|"
                       r"\bupi\b|broker|jarvis-secrets", re.I)
REFUSED = "refused: password managers, wallets, banking and secrets are never opened"


def _sensitive(*texts) -> bool:
    """The words above, plus the kernel's financial sites and app names (audit PC-D10: 'Zerodha Kite', 'PhonePe', 'YONO SBI'
    and 'ICICI iMobile' had none of these words and opened)."""
    return bool(SENSITIVE.search(" ".join(texts)) or sensitive_target(list(texts)))
MAX_SCAN = 5000            # entries looked at when matching a name inside a folder
MAX_LIST = 200
_apps_cache = {"t": 0.0, "apps": []}


def known_folder(name: str, roots: dict = None):
    """The real path of a known folder, or None. `roots` (tests) maps names to paths."""
    key = str(name).strip().lower()
    if key not in KNOWN_FOLDERS:
        return None
    if roots is not None:
        return os.path.realpath(roots[key]) if key in roots else None
    import ctypes
    from ctypes import wintypes

    class GUID(ctypes.Structure):
        _fields_ = [("d1", wintypes.DWORD), ("d2", wintypes.WORD), ("d3", wintypes.WORD), ("d4", ctypes.c_ubyte * 8)]
    guid, out = GUID(), ctypes.c_wchar_p()
    ctypes.oledll.ole32.CLSIDFromString(KNOWN_FOLDERS[key], ctypes.byref(guid))
    try:
        ctypes.oledll.shell32.SHGetKnownFolderPath(ctypes.byref(guid), 0, None, ctypes.byref(out))
        return os.path.realpath(out.value)
    finally:
        ctypes.windll.ole32.CoTaskMemFree(out)


def _entries(root, want_dir, depth=2):
    """Folders or files under root, at most `depth` levels down, skipping hidden ones."""
    out, seen = [], 0
    for dirpath, dirnames, filenames in os.walk(root):
        level = 0 if dirpath == root else os.path.relpath(dirpath, root).count(os.sep) + 1
        dirnames[:] = [d for d in dirnames if not d.startswith((".", "$"))]
        names = dirnames if want_dir else [f for f in filenames if not f.startswith((".", "$"))]
        out.extend(os.path.join(dirpath, n) for n in names)
        seen += len(dirnames) + len(filenames)
        if level + 1 >= depth:
            dirnames[:] = []                          # deep enough: do not descend further
        if seen > MAX_SCAN:
            break
    return out


def _score(query, name):
    q, n = query.lower(), name.lower()
    stem = os.path.splitext(n)[0]
    if q in (n, stem):
        return 1.0
    if n.startswith(q) or stem.startswith(q):
        return 0.95
    if q in n:
        return 0.9
    return 0.8 * difflib.SequenceMatcher(None, q, stem).ratio()


def resolve(root: str, rel: str, want_dir: bool):
    """(path, candidates): the entry `rel` names inside root. `rel` is a relative path or just a name to look for.
    Never leaves root; an unclear match returns no path and the closest names instead."""
    rel = str(rel or "").strip().strip("\\/")
    if not rel:
        return (root, []) if want_dir else (None, [])
    parts = re.split(r"[\\/]+", rel)
    if ".." in parts or os.path.isabs(rel) or re.match(r"^[A-Za-z]:", rel):
        return None, []
    exact = os.path.realpath(os.path.join(root, *parts))
    if exact.startswith(root + os.sep) and os.path.exists(exact) and os.path.isdir(exact) == want_dir:
        return exact, []
    scored = sorted(((_score(parts[-1], os.path.basename(c)), c) for c in _entries(root, want_dir)), reverse=True)
    names = [os.path.relpath(c, root) for _, c in scored[:5]]
    if not scored or scored[0][0] < 0.6:
        return None, names
    if len(scored) > 1 and scored[1][0] >= 0.9 and scored[1][0] >= scored[0][0] - 0.01:
        return None, names                              # two equally good matches: let the asker choose
    best = os.path.realpath(scored[0][1])
    return (best, []) if best.startswith(root + os.sep) else (None, [])


def _get_start_apps():
    r = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command",
                        "Get-StartApps | Select-Object Name, AppID | ConvertTo-Json -Compress"],
                       capture_output=True, text=True, encoding="utf-8", timeout=60,
                       creationflags=0x08000000 if os.name == "nt" else 0)   # CREATE_NO_WINDOW: no console flashes
    return r.stdout


def start_apps(runner=None):
    """Windows' own Start list [(name, AppID)]. The real list is cached for 10 minutes; `runner` (tests) is not."""
    cached = runner is None
    if cached and time.time() - _apps_cache["t"] < 600 and _apps_cache["apps"]:
        return _apps_cache["apps"]
    data = json.loads((runner or _get_start_apps)() or "[]")
    data = [data] if isinstance(data, dict) else data
    apps = [(str(d["Name"]), str(d["AppID"])) for d in data if isinstance(d, dict) and d.get("Name") and d.get("AppID")]
    if cached:
        _apps_cache.update(t=time.time(), apps=apps)
    return apps


def find_app(query: str, apps: list):
    """(name, AppID) of the best Start-list match, or (None, closest names). Uninstallers are never matched."""
    q = str(query).strip()
    usable = [(n, a) for n, a in apps if not n.lower().startswith("uninstall")]
    scored = sorted(((_score(q, n), n, a) for n, a in usable), reverse=True)
    if not q or not scored or scored[0][0] < 0.75:
        return None, [n for _, n, _ in scored[:5]]
    if len(scored) > 1 and scored[1][0] >= 0.9 and scored[1][0] >= scored[0][0] - 0.01 and scored[0][0] < 1.0:
        return None, [n for _, n, _ in scored[:5]]
    return (scored[0][1], scored[0][2]), []


_last: dict = {"path": None}


def take_path():
    """The folder the last open_folder resolved to (once), or None."""
    p, _last["path"] = _last["path"], None
    return p


def _opened(go, real: bool) -> None:
    """Run the opener and, for the real thing on Windows, remember the window it opened (windowpick.py: only a window JARVIS opened may be typed into later)."""
    if real and os.name == "nt":
        from . import windowpick
        windowpick.launch_and_record_any(go)
    else:
        go()


def pc_open(p: dict, roots: dict = None, apps: list = None, opener=None, launcher=None) -> dict:
    """Run one authorized pc_open job: open_app, open_folder, open_file or list_folder."""
    op, target, rel = str(p.get("op", "")), str(p.get("target", "")), str(p.get("path", ""))
    if _sensitive(target, rel):
        return {"ok": False, "error": REFUSED}
    opener = opener or os.startfile
    if op == "open_app":
        found, near = find_app(target, apps if apps is not None else start_apps())
        if not found:
            return {"ok": False, "error": "no such app in the Start list", "closest": near}
        name, appid = found
        if _sensitive(name):
            return {"ok": False, "error": REFUSED}
        go = launcher or (lambda a: subprocess.Popen(["explorer.exe", "shell:AppsFolder\\" + a], close_fds=True))
        _opened(lambda: go(appid), launcher is None)
        return {"ok": True, "opened": name}
    root = known_folder(target, roots)
    if not root or not os.path.isdir(root):
        return {"ok": False, "error": "folder must be one of: Downloads, Documents, Desktop, Pictures, Videos, Music"}
    if op not in ("open_folder", "list_folder", "open_file"):
        return {"ok": False, "error": "unsupported"}
    path, near = resolve(root, rel, want_dir=op != "open_file")
    if not path:
        return {"ok": False, "error": "no single " + ("file" if op == "open_file" else "folder") + " matches", "closest": near}
    inside = os.path.relpath(path, root) if path != root else ""
    if _sensitive(inside):
        return {"ok": False, "error": REFUSED}
    shown = os.path.join(target.strip().title(), inside) if inside else target.strip().title()
    if op == "open_file":
        if os.path.splitext(path)[1].lower() not in DOC_TYPES:
            return {"ok": False, "error": "refused: only documents and media open; programs and scripts never do"}
        _opened(lambda: opener(path), opener is os.startfile)
        return {"ok": True, "opened": shown}
    if op == "open_folder":
        _opened(lambda: opener(path), opener is os.startfile)
        _last["path"] = path                              # for jobs._verify_open: an Explorer window already showing this folder counts as open
        return {"ok": True, "opened": shown}
    items = []
    with os.scandir(path) as it:
        for e in it:
            if e.name.startswith((".", "$")):
                continue
            try:
                st, is_dir = e.stat(), e.is_dir()
            except OSError:
                continue
            items.append((st.st_mtime, e.name + ("\\" if is_dir else ""), 0 if is_dir else st.st_size))
    items.sort(reverse=True)                            # newest first: what was just downloaded comes first
    return {"ok": True, "folder": shown, "count": len(items),
            "items": [{"name": n, "bytes": b, "modified": time.strftime("%Y-%m-%d %H:%M", time.localtime(t))} for t, n, b in items[:MAX_LIST]]}
