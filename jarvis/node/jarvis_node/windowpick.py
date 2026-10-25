"""Which window does "notepad|..." mean? One shared answer for every action that names a window by its process.

The old rule was "the first process of that name that has a window". With two Notepads that is whichever the process list shows first, so JARVIS could type into
the OWNER's document (found on the real laptop, 2026-10-25). Now:

  1. one window of that process: that one;
  2. several, and JARVIS opened some of them (the node records the window it launched): its own, the one in front if it is one of them, else the newest;
  3. several, none opened by JARVIS: a READ (find, read back, screenshot, inspect) takes the one in front, else the newest of them, and says so;
     a CHANGE (focus, type, click, key, scroll, move, maximize, minimize, restore) is refused: "several <app> windows; say which one".
A window JARVIS did not open is never typed into, clicked, resized or closed unless it is the only one of its kind.

The choice is made here in Python (testable without Windows: `choose` is pure); PowerShell only lists windows (LIST) and then acts on the one handle it is given.
"""
from __future__ import annotations

import json
import os
import re
import time

_SAFE = re.compile(r"^[A-Za-z0-9_.\- ]{1,80}$")
KEEP_S = 12 * 3600                      # a recorded launch is forgotten after twelve hours


class SeveralWindowsSayWhich(RuntimeError):
    """The node reports only an exception's CLASS NAME to the kernel (its text could hold screen content), so this class name is the reason the plugin and the
    decider see; the message is for the node's own log: "several <app> windows; say which one"."""


WindowPickError = SeveralWindowsSayWhich


LIST = r"""
$ErrorActionPreference='Stop'
Add-Type -TypeDefinition @'
using System; using System.Collections.Generic; using System.Runtime.InteropServices;
public class PKW {
  public delegate bool EP(IntPtr h, IntPtr l);
  [DllImport("user32.dll")] static extern bool EnumWindows(EP p, IntPtr l);
  [DllImport("user32.dll")] static extern uint GetWindowThreadProcessId(IntPtr h, out uint pid);
  [DllImport("user32.dll")] static extern bool IsWindowVisible(IntPtr h);
  [DllImport("user32.dll")] static extern int GetWindowTextLength(IntPtr h);
  [DllImport("user32.dll")] static extern IntPtr GetWindow(IntPtr h, uint cmd);
  [DllImport("user32.dll")] static extern int GetWindowLong(IntPtr h, int i);
  [DllImport("user32.dll")] public static extern IntPtr GetForegroundWindow();
  public static List<long[]> For(HashSet<uint> ids) {
    var r = new List<long[]>();
    EnumWindows((h, l) => { uint pid; GetWindowThreadProcessId(h, out pid);
      if (ids.Contains(pid) && IsWindowVisible(h) && GetWindowTextLength(h) > 0 && GetWindow(h, 4) == IntPtr.Zero && (GetWindowLong(h, -20) & 0x80) == 0) r.Add(new long[]{ h.ToInt64(), pid });
      return true; }, IntPtr.Zero);
    return r;
  }
}
'@
$ids = New-Object 'System.Collections.Generic.HashSet[uint32]'; $start = @{}; $names = @{}
$all = if ($env:JARVIS_UI_PROC) { @(Get-Process -Name $env:JARVIS_UI_PROC -ErrorAction SilentlyContinue) } else { @(Get-Process -ErrorAction SilentlyContinue | Where-Object { $_.MainWindowHandle -ne 0 }) }
foreach ($q in $all) { [void]$ids.Add([uint32]$q.Id); $names[[string]$q.Id] = $q.ProcessName.ToLower(); try { $start[[string]$q.Id] = $q.StartTime.ToFileTimeUtc() } catch { $start[[string]$q.Id] = 0 } }
$w = @(); foreach ($x in [PKW]::For($ids)) { $w += @{ hwnd = $x[0]; pid = $x[1]; start = $start[[string]$x[1]]; name = $names[[string]$x[1]] } }
@{ fg = [PKW]::GetForegroundWindow().ToInt64(); windows = @($w) } | ConvertTo-Json -Compress -Depth 4
"""


def choose(windows: list[dict], fg: int, launched: set[int], mutating: bool, proc: str) -> tuple[dict, str] | None:
    """(window, note) or None when the process has no window; raises WindowPickError when a change would be a guess. Pure."""
    if not windows:
        return None
    if len(windows) == 1:
        return windows[0], ""
    mine = [w for w in windows if w["hwnd"] in launched]
    if mine:
        front = [w for w in mine if w["hwnd"] == fg]
        return (front or [max(mine, key=lambda w: (w.get("start") or 0, w["hwnd"]))])[0], "the window JARVIS opened"
    if not mutating:
        front = [w for w in windows if w["hwnd"] == fg]
        if front:
            return front[0], "the one in front"
        return max(windows, key=lambda w: (w.get("start") or 0, w["hwnd"])), f"picked the newest of {len(windows)}"
    raise WindowPickError(f"several {proc} windows; say which one")


def registry_path() -> str:
    base = os.environ.get("JARVIS_RUN_DIR") or os.path.join(os.environ.get("LOCALAPPDATA") or os.path.expanduser("~"), "jarvis", "run")
    return os.path.join(base, "launched-windows.json")


def _load() -> list[dict]:
    try:
        with open(registry_path(), encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return []
    now = time.time()
    return [e for e in data if isinstance(e, dict) and isinstance(e.get("hwnd"), int) and now - float(e.get("t", 0)) < KEEP_S]


def launched_hwnds(proc: str) -> set[int]:
    return {e["hwnd"] for e in _load() if e.get("proc") == proc.lower()}


def record(proc: str, hwnds: list[int], pid: int = 0) -> None:
    entries = _load() + [{"proc": proc.lower(), "hwnd": int(h), "pid": int(pid), "t": time.time()} for h in hwnds]
    path = registry_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(entries[-200:], fh)
    os.replace(tmp, path)


def list_windows(proc: str) -> tuple[list[dict], int]:
    from . import runtime
    if proc and not _SAFE.match(proc):
        raise ValueError("bad target")                                 # "" lists every app's windows (used to see what a launch opened)
    r = runtime.powershell(LIST, {"JARVIS_UI_PROC": proc}, 25)
    if r.returncode or not (r.stdout or "").strip():
        return [], 0
    data = json.loads(r.stdout.strip().splitlines()[-1])
    return list(data.get("windows") or []), int(data.get("fg") or 0)


def pick_env(proc: str, mutating: bool) -> dict:
    """The environment that tells a PowerShell script exactly which window to act on ({} when the process has no window of its own: the script then
    looks for a modern-app frame window, as before)."""
    windows, fg = list_windows(proc)
    got = choose(windows, fg, launched_hwnds(proc), mutating, proc)
    if got is None:
        return {}
    w, note = got
    return {"JARVIS_UI_HWND": str(w["hwnd"]), "JARVIS_UI_PID": str(w["pid"]), "JARVIS_UI_PICKNOTE": note}


def launch_and_record(proc: str, start) -> None:
    """Start the app (`start()` does it) and remember the window that appeared because of it."""
    try:
        before = {w["hwnd"] for w in list_windows(proc)[0]}
    except (OSError, ValueError, RuntimeError):
        before = set()
    start()
    for _ in range(20):
        time.sleep(0.5)
        try:
            now = [w for w in list_windows(proc)[0] if w["hwnd"] not in before]
        except (OSError, ValueError, RuntimeError):
            now = []
        if now:
            record(proc, [w["hwnd"] for w in now], now[0].get("pid", 0))
            return


def launch_and_record_any(start) -> list[str]:
    """Start something (an app, a folder, a file) and remember every NEW window of any app that appears because of it: the node does not know in advance
    which process a Start-menu entry, a folder or a document will open in. Returns the process names recorded."""
    try:
        before = {w["hwnd"] for w in list_windows("")[0]}
    except (OSError, ValueError, RuntimeError):
        before = set()
    start()
    for _ in range(20):
        time.sleep(0.5)
        try:
            new = [w for w in list_windows("")[0] if w["hwnd"] not in before]
        except (OSError, ValueError, RuntimeError):
            new = []
        if new:
            names = []
            for w in new:
                if w.get("name"):
                    record(w["name"], [w["hwnd"]], w.get("pid", 0))
                    names.append(w["name"])
            return names
    return []
