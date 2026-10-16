"""Repairs the owner approved (2026-10-16), each with a backup first and a check afterwards. Windows.

  edit_file    replace one exact piece of text in a file the owner's account may write (never Windows or Program Files,
               never the secrets folder); the old file is backed up first and can be put back with restore
  restore      put a backed-up file back (the current one is backed up too, so a restore can itself be undone)
  restart_app  ask a program to close (never forced: unsaved work could be lost), then start it again from the same path,
               and check that it runs and answers
  install      winget install of one exact package id; an installer that needs administrator rights shows the normal
               Windows UAC prompt to the owner (nothing here elevates); afterwards winget must list the package
Elevated commands, registry edits, services, uninstalling and anything that disables a protection are not offered.
"""
import hashlib
import json
import os
import re
import secrets
import shutil
import time

from . import runtime

BACKUPS = os.environ.get("NEKO_REPAIR_BACKUPS", r"D:\jarvis\N.E.K.O-data\repair-backups")
MAX_FILE = 2_000_000
PROTECTED_PROCS = {"system", "idle", "csrss", "lsass", "winlogon", "wininit", "services", "smss", "svchost", "dwm", "fontdrvhost",
                   "msmpeng", "securityhealthservice", "registry", "memcompression", "taskmgr", "jarvis_node", "python", "pythonw"}
SENSITIVE = re.compile(r"(?i)keepass|1password|bitwarden|lastpass|dashlane|enpass|wallet|metamask|exodus|ledger|banking|netbank|paypal")


def _norm(p):
    return os.path.normcase(os.path.realpath(p))


def writable_root_ok(path: str) -> bool:
    """Only places the owner's own account writes: the user profile and the D: workspace drive. Never Windows, Program
    Files, ProgramData or the secrets folder (the kernel refuses those too; this is the node's own check)."""
    p = _norm(path)
    banned = [_norm(x) for x in (os.environ.get("SystemRoot", r"C:\Windows"), r"C:\Program Files", r"C:\Program Files (x86)",
                                 r"C:\ProgramData", r"D:\jarvis-secrets", BACKUPS)]
    if any(p == b or p.startswith(b + os.sep) for b in banned):
        return False
    allowed = [_norm(os.path.expanduser("~")), _norm("D:\\")]
    return any(p.startswith(a.rstrip(os.sep) + os.sep) for a in allowed)


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _read(path: str) -> bytes:
    with open(path, "rb") as f:
        return f.read()


def backup(path: str, why: str) -> str:
    bid = time.strftime("%Y%m%d-%H%M%S-") + secrets.token_hex(3)
    d = os.path.join(BACKUPS, bid)
    os.makedirs(d)
    data = _read(path)
    with open(os.path.join(d, "file.bak"), "wb") as f:
        f.write(data)
    with open(os.path.join(d, "manifest.json"), "w", encoding="utf-8") as f:
        json.dump({"path": os.path.realpath(path), "sha256": _sha(data), "bytes": len(data), "time": time.strftime("%Y-%m-%d %H:%M:%S"),
                   "why": why}, f, indent=1)
    return bid


def _atomic_write(path: str, data: bytes):
    tmp = path + ".neko-tmp"
    with open(tmp, "wb") as f:
        f.write(data)
    os.replace(tmp, path)


def edit_file(path: str, find: str, replace: str) -> dict:
    if not writable_root_ok(path):
        return {"ok": False, "error": "refused: only files in your user folder or on D: (not Windows, Program Files or secrets)"}
    if not os.path.isfile(path):
        return {"ok": False, "error": "no such file"}
    raw = _read(path)
    if len(raw) > MAX_FILE:
        return {"ok": False, "error": "file too large for a text edit"}
    try:
        text = raw.decode("utf-8-sig" if raw.startswith(b"\xef\xbb\xbf") else "utf-8")
    except UnicodeDecodeError:
        return {"ok": False, "error": "not a UTF-8 text file"}
    n = text.count(find)
    if n != 1:
        return {"ok": False, "error": f"the text to replace occurs {n} times (it must occur exactly once); nothing changed"}
    bid = backup(path, "edit_file")
    new = text.replace(find, replace, 1)
    data = new.encode("utf-8")
    if raw.startswith(b"\xef\xbb\xbf"):
        data = b"\xef\xbb\xbf" + data
    _atomic_write(path, data)
    ok = _read(path) == data                                                  # verify what is on disk now
    return {"ok": ok, "changed": os.path.basename(path), "backup": bid, "lines_before": text.count("\n") + 1,
            "lines_after": new.count("\n") + 1, "verified_on_disk": ok, "undo": f"repair restore {bid}"}


def restore(bid: str) -> dict:
    if not re.fullmatch(r"\d{8}-\d{6}-[0-9a-f]{6}", bid or ""):
        return {"ok": False, "error": "bad backup id"}
    d = os.path.join(BACKUPS, bid)
    try:
        m = json.loads(_read(os.path.join(d, "manifest.json")).decode("utf-8"))
        data = _read(os.path.join(d, "file.bak"))
    except (OSError, ValueError):
        return {"ok": False, "error": "no such backup"}
    if _sha(data) != m.get("sha256"):
        return {"ok": False, "error": "the backup does not match its record; nothing changed"}
    path = m["path"]
    if not writable_root_ok(path):
        return {"ok": False, "error": "refused: the backed-up path is not in a writable place"}
    undo = backup(path, f"before restore {bid}") if os.path.isfile(path) else None
    _atomic_write(path, data)
    ok = _sha(_read(path)) == m["sha256"]
    return {"ok": ok, "restored": os.path.basename(path), "from": bid, "verified_on_disk": ok, "undo": f"repair restore {undo}" if undo else None}


CLOSE = r"""
$p = @(Get-Process -Name $env:JARVIS_APP -ErrorAction SilentlyContinue | Where-Object { $_.MainWindowHandle -ne 0 })
if (-not $p) { '{"running": false}'; exit }
$path = $p[0].Path
foreach ($x in $p) { [void]$x.CloseMainWindow() }
$deadline = (Get-Date).AddSeconds(8)
while ((Get-Date) -lt $deadline -and @(Get-Process -Id ($p | ForEach-Object { $_.Id }) -ErrorAction SilentlyContinue).Count) { Start-Sleep -Milliseconds 300 }
$left = @(Get-Process -Id ($p | ForEach-Object { $_.Id }) -ErrorAction SilentlyContinue).Count
[pscustomobject]@{ running = $true; path = $path; closed = ($left -eq 0); left = $left } | ConvertTo-Json -Compress
"""
CHECK = r"""
Start-Sleep -Seconds 4
$p = @(Get-Process -Name $env:JARVIS_APP -ErrorAction SilentlyContinue | Where-Object { $_.MainWindowHandle -ne 0 })
[pscustomobject]@{ running = ($p.Count -gt 0); responding = $(if ($p.Count) { $p[0].Responding } else { $false }); title = $(if ($p.Count) { $p[0].MainWindowTitle } else { '' }) } | ConvertTo-Json -Compress
"""


def restart_app(name: str) -> dict:
    if not re.fullmatch(r"[A-Za-z0-9_.-]{1,40}", name or "") or name.lower() in PROTECTED_PROCS or SENSITIVE.search(name):
        return {"ok": False, "error": "refused: a system, protected or sensitive program"}
    r = runtime.powershell(CLOSE, {"JARVIS_APP": name}, 30)
    try:
        st = json.loads(r.stdout.strip() or "{}")
    except ValueError:
        return {"ok": False, "error": "could not read the program's state"}
    if not st.get("running"):
        return {"ok": False, "error": f"{name} has no open window to restart"}
    if not st.get("closed"):
        return {"ok": False, "error": f"{name} did not close within 8 s (it may be asking to save); it was NOT forced, nothing lost"}
    path = st.get("path") or ""
    if not path or not os.path.isfile(path):
        return {"ok": False, "error": "closed, but its program file is not known, so it was not restarted; open it yourself"}
    runtime.popen([path], close_fds=True, creationflags=0)            # its own window, as the owner would start it
    chk = runtime.powershell(CHECK, {"JARVIS_APP": name}, 30)
    try:
        after = json.loads(chk.stdout.strip() or "{}")
    except ValueError:
        after = {}
    ok = bool(after.get("running") and after.get("responding"))
    return {"ok": ok, "restarted": name, "running": bool(after.get("running")), "responding": bool(after.get("responding")),
            "error": None if ok else "restarted, but it is not running and answering yet"}


def install(package: str) -> dict:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9.+_-]{1,80}", package or ""):
        return {"ok": False, "error": "bad package id"}
    winget = shutil.which("winget")
    if not winget:
        return {"ok": False, "error": "winget is not available on this PC"}
    base = [winget, "--disable-interactivity"]
    r = runtime.run(base[:1] + ["install", "--id", package, "--exact", "--source", "winget", "--accept-package-agreements",
                                "--accept-source-agreements", "--disable-interactivity"], timeout=1800)
    chk = runtime.run(base[:1] + ["list", "--id", package, "--exact", "--accept-source-agreements", "--disable-interactivity"], timeout=120)
    listed = package.lower() in (chk.stdout or "").lower()
    return {"ok": listed, "package": package, "installer_exit": r.returncode, "listed_after": listed,
            "error": None if listed else "winget does not list the package after the install (it may have been cancelled at the UAC prompt)"}


def run(p: dict) -> dict:
    op, target, args = str(p.get("op", "")), str(p.get("target", "")), p.get("args") or {}
    if op == "edit_file":
        return edit_file(target, str(args.get("find", "")), str(args.get("replace", "")))
    if op == "restore":
        return restore(target)
    if op == "restart_app":
        return restart_app(target)
    if op == "install":
        return install(target)
    return {"ok": False, "error": "unknown repair"}
