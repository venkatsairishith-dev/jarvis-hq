r"""Undo recipes (D61): what the journal records about a step so that "undo that" can reverse it, and the node-side code that does it.

A recipe is plain data written to the journal when the step happens (jarvis/node/jarvis_node/journal.py, written by the desktop plugin). It names
the exact thing to reverse and how to know it is still that thing. Nothing here guesses: a recipe either runs and is checked by its EFFECT
(the file is gone from its folder and is in the bin; the window is gone; the setting reads back as before), or it refuses with a fixed reason.

  close_window   {proc, pid, opened_at}     a window JARVIS opened -> asked to close (WM_CLOSE, so the app can ask to save); refused if its title shows
                                            unsaved work, "still_open" if the app did not close it
  recycle_new    {path, stamp}              a NEW file (saved, copied, made) -> the Recycle Bin, only if size+mtime still match the record
  restore_bin    {path, size}               something JARVIS sent to the bin (a delete, a cleanup item) -> back to its original path through the
                                            shell; if that path is taken, restored under a numbered name ("name (1).txt"), never over anything
  move_back      {src, dst, stamp}          a move or rename -> back to where it was, only if dst is unchanged (size+mtime) and src is free
  setting        {name, previous, new}      dark mode, volume, brightness, wallpaper, do-not-disturb -> the value recorded before the change
  moves          {items: [{src, dst, stamp}], more}   a sort of Downloads (D54): one move_back per file, each only while that file is unchanged; the
                                            type folders the sort made stay (empty); `more` files beyond the first 100 are not recorded
  cannot_undo    {why}                      typed text, a permanent delete, safe-list caches: nothing to run; the panel and the answer say why

The node job is `pc_undo` (kernel: jobs.py, policy rule tool `undo`, the owner's own request = owner_direct = ordinary, so no PIN, and a secrets
path or a sensitive name is refused like anywhere else). Every path is made real (links resolved) and must be inside the owner's folders; secret
paths, password-manager, bank and wallet names, AppData, Windows, Program Files, the journal itself and D:\jarvis-secrets are refused again here.

Windows pieces (the shell's SHFileOperation, the shell's undelete verb, PostMessage, the registry, WMI) are behind small ports so the tests run on
a fake PC. They have never run on the laptop: D61 lists what to look at on screen.
"""
from __future__ import annotations

import os
import re
import shutil
import struct
import time
from pathlib import Path
from typing import Any, Callable

from . import journal
from .journal import stamp

KINDS = ("close_window", "recycle_new", "restore_bin", "move_back", "setting", "cleanup", "moves", "cannot_undo")
NODE_OPS = ("recycle", "restore_bin", "move", "close_window", "setting")
SETTINGS = ("dark_mode", "volume", "brightness", "wallpaper", "dnd")
UNSAVED_TITLE = re.compile(r"^\s*[*\u25cf\u2022]|\s\*\s*$|\bunsaved\b|\(modified\)", re.I)
CLOSE_WAIT_S = 4.0
PROTECTED_PARTS = {"appdata", "windows", "program files", "program files (x86)", "programdata", "$recycle.bin", "jarvis-secrets", "system volume information"}


class Refused(Exception):
    """A fixed reason code the answer can say in words. `code` is one of REASONS."""

    def __init__(self, code: str, detail: str = ""):
        super().__init__(code)
        self.code, self.detail = code, detail


REASONS = {
    "changed": "it has changed since JARVIS made it, so JARVIS left it alone",
    "missing": "it is not there any more",
    "taken": "something else is now at the place it would go back to",
    "not_in_bin": "it is not in the Recycle Bin any more (emptied or restored by hand)",
    "unsaved": "the window seems to have unsaved work, so JARVIS did not close it",
    "still_open": "the app did not close (it may be asking whether to save)",
    "outside": "it is outside your own folders",
    "protected": "it is a protected or private place",
    "link": "it is a link or junction",
    "failed": "Windows did not do it",
    "unsupported": "this kind of undo is not available here",
    "cannot": "this cannot be undone",
    "no_value": "the earlier value was not recorded",
}


def reason_text(code: str) -> str:
    return REASONS.get(code, "it could not be undone")


# ------------------------------------------------------------------------------------------------------------------------------ recipes
def recipe_close_window(proc: str, pid: int, opened_at: float) -> dict:
    return {"kind": "close_window", "proc": journal.text(proc, 80), "pid": int(pid or 0), "opened_at": round(float(opened_at), 1)}


def recipe_recycle_new(path: str) -> dict | None:
    """For a file that is new: None when it is not there to record."""
    st = stamp(path)
    return {"kind": "recycle_new", "path": str(path), "stamp": st["hash"], "size": st["size"]} if st else None


def recipe_restore_bin(path: str, size: int | None = None, at: float | None = None) -> dict:
    return {"kind": "restore_bin", "path": str(path), "size": size, "at": round(at if at is not None else time.time(), 1)}


def recipe_move_back(src: str, dst: str) -> dict | None:
    st = stamp(dst)
    return {"kind": "move_back", "src": str(src), "dst": str(dst), "stamp": st["hash"]} if st else None


def recipe_setting(name: str, previous: str, new: str) -> dict:
    return {"kind": "setting", "name": name if name in SETTINGS else "unknown", "previous": None if previous is None else str(previous)[:260], "new": str(new)[:260]}


MOVES_MAX = 100


def recipe_moves(pairs: list) -> "dict | None":
    """For a sort: [(original path, new path)]. Each file is stamped now; the first MOVES_MAX are kept and the rest only counted. None when none."""
    items = []
    for src, dst in pairs or []:
        rec = recipe_move_back(src, dst) if len(items) < MOVES_MAX else None
        if rec is not None:
            items.append({"src": rec["src"], "dst": rec["dst"], "stamp": rec["stamp"]})
    more = max(0, len(pairs or []) - len(items))
    return {"kind": "moves", "items": items, "more": more} if items else None


def recipe_cannot(why: str) -> dict:
    return {"kind": "cannot_undo", "why": journal.text(why, 160)}


def leaf(path: str) -> str:
    return journal.text(re.split(r"[\\/]", str(path).rstrip("\\/"))[-1], 80)


def describe(recipe: dict | None) -> str:
    """One sentence for the owner's panel (it may name a file: the panel is local). Never given to the chat model."""
    if not recipe:
        return ""
    k = recipe.get("kind")
    if k == "close_window":
        return "Close the window JARVIS opened"
    if k == "recycle_new":
        return f"Move {leaf(recipe.get('path', ''))} to the Recycle Bin"
    if k == "restore_bin":
        return f"Put {leaf(recipe.get('path', ''))} back from the Recycle Bin"
    if k == "move_back":
        return f"Move {leaf(recipe.get('dst', ''))} back to {leaf(os.path.dirname(str(recipe.get('src', ''))))}"
    if k == "setting":
        return f"Set {str(recipe.get('name', 'the setting')).replace('_', ' ')} back to how it was"
    if k == "cleanup":
        return f"Put {len(recipe.get('items') or [])} cleaned items back from the Recycle Bin"
    if k == "moves":
        return f"Move the {len(recipe.get('items') or [])} sorted files back"
    if k == "cannot_undo":
        return "Cannot be undone: " + str(recipe.get("why", ""))
    return ""


def word(recipe: dict | None) -> str:
    """The recipe's kind and nothing else (what a chat model may be told)."""
    return str((recipe or {}).get("kind") or "")


# ---------------------------------------------------------------------------------------------------------------------- the Recycle Bin
def parse_i(data: bytes) -> dict | None:
    """A Recycle Bin `$I` file: version 1 (Vista to 8: a 520-byte path) or 2 (Windows 10 and later: a length, then the path). Returns
    {path, size, deleted_at} or None."""
    try:
        ver, size, ft = struct.unpack_from("<Qqq", data, 0)
        if ver == 1:
            path = data[24:24 + 520].decode("utf-16-le", "replace").split("\0")[0]
        elif ver == 2:
            n = struct.unpack_from("<I", data, 24)[0]
            if n > 4096:
                return None
            path = data[28:28 + 2 * n].decode("utf-16-le", "replace").rstrip("\0")
        else:
            return None
    except struct.error:
        return None
    return {"path": path, "size": int(size), "deleted_at": ft / 1e7 - 11644473600} if path else None


def make_i(path: str, size: int, deleted_at: float, version: int = 2) -> bytes:
    """The bytes of a `$I` file (tests and the fake recycler)."""
    ft = int((deleted_at + 11644473600) * 1e7)
    raw = path.encode("utf-16-le") + b"\0\0"
    if version == 1:
        return struct.pack("<Qqq", 1, size, ft) + raw.ljust(520, b"\0")
    return struct.pack("<Qqq", 2, size, ft) + struct.pack("<I", len(path) + 1) + raw


def norm(path: str) -> str:
    return os.path.normcase(os.path.normpath(str(path))).replace("\\", "/")


def numbered_name(path: str, exists: Callable[[str], bool] = os.path.lexists) -> str:
    """`name (1).ext`, `name (2).ext`, ...: the first one that is free."""
    base, ext = os.path.splitext(path)
    n = 1
    while exists(f"{base} ({n}){ext}"):
        n += 1
    return f"{base} ({n}){ext}"


class Bin:
    """The Recycle Bin, as three small jobs: recycle(path), find(original path, size, after) -> item, restore(item, dest). `dirs` are the
    `$Recycle.Bin\\<SID>` folders; `recycler` and `exact` are the shell calls (fakes in tests)."""

    def __init__(self, dirs: Callable[[], list[str]], recycler: Callable[[str], None], exact: Callable[[dict, str], None] | None = None):
        self.dirs, self.recycler, self.exact = dirs, recycler, exact

    def recycle(self, path: str) -> None:
        self.recycler(path)

    def items(self) -> list[dict]:
        out = []
        for d in self.dirs():
            try:
                names = os.listdir(d)
            except OSError:
                continue
            for n in names:
                if not n.startswith("$I"):
                    continue
                try:
                    with open(os.path.join(d, n), "rb") as fh:
                        meta = parse_i(fh.read(1024))
                except OSError:
                    continue
                r = os.path.join(d, "$R" + n[2:])
                if meta and os.path.lexists(r):
                    out.append({**meta, "i": os.path.join(d, n), "r": r})
        return out

    def find(self, path: str, size: int | None = None, after: float | None = None) -> dict | None:
        """The newest bin item whose original path is `path` (and, when given, whose size matches and that was deleted at or after `after`)."""
        want = norm(path)
        hits = [it for it in self.items() if norm(it["path"]) == want and (size in (None, "") or it["size"] == int(size))
                and (after is None or it["deleted_at"] >= after - 5)]
        return max(hits, key=lambda it: it["deleted_at"]) if hits else None

    def restore(self, item: dict, dest: str, exact: bool) -> None:
        if exact and self.exact is not None:
            self.exact(item, dest)
            return
        shutil.move(item["r"], dest)                                    # a numbered name (or no shell): move the stored item out ...
        try:
            os.remove(item["i"])                                        # ... and drop its record, as Explorer does
        except OSError:
            pass


# ---------------------------------------------------------------------------------------------------------------------- ports
class Ports:
    """Everything an undo touches besides plain files. `roots` are the owner's folders; `bin`, `windows` and `settings` are the Windows pieces."""

    def __init__(self, roots: list[str], bin: Bin | None = None, windows: Any = None, settings: Any = None, protected: list[str] | None = None,
                 sleep: Callable[[float], None] = time.sleep, clock: Callable[[], float] = time.monotonic):
        self.roots = [os.path.realpath(r) for r in roots]
        self.bin, self.windows, self.settings = bin, windows, settings
        self.protected = [os.path.realpath(p) for p in (protected or [])]
        self.sleep, self.clock = sleep, clock


def check_path(path: str, ports: Ports, must_exist: bool = False) -> str:
    """The real path, or Refused. Links are refused; the place must be inside a root and not private."""
    if not path or "\0" in path:
        raise Refused("outside")
    p = os.path.abspath(os.path.expanduser(str(path)))
    if os.path.lexists(p) and (os.path.islink(p) or _is_junction(p)):
        raise Refused("link")
    real = os.path.realpath(p)
    rel = None
    for r in ports.roots:
        try:
            if os.path.commonpath([os.path.normcase(real), os.path.normcase(r)]) == os.path.normcase(r) and norm(real) != norm(r):
                rel = norm(real)[len(norm(r)):].strip("/")
                break
        except ValueError:
            continue
    if rel is None:
        raise Refused("outside")
    low = norm(real).lower()
    if set(rel.lower().split("/")) & PROTECTED_PARTS or any(low == norm(x).lower() or low.startswith(norm(x).lower() + "/") for x in ports.protected):
        raise Refused("protected")
    from . import sensitive
    if journal.SECRET_PATH.search(real) or sensitive.sensitive_target([real.replace("\\", " ").replace("/", " ")]):
        raise Refused("protected")
    try:
        from .fileops import _SECRET
        if _SECRET.search("/" + low.strip("/")):
            raise Refused("protected")
    except ImportError:
        pass
    if must_exist and not os.path.lexists(real):
        raise Refused("missing")
    return real


def _is_junction(p: str) -> bool:
    try:
        return bool(getattr(os.stat(p, follow_symlinks=False), "st_file_attributes", 0) & 0x400)      # FILE_ATTRIBUTE_REPARSE_POINT
    except OSError:
        return False


# ---------------------------------------------------------------------------------------------------------------------- the five jobs
def _recycle(p: dict, ports: Ports) -> dict:
    path = check_path(str(p.get("target", "")), ports, must_exist=True)
    st = stamp(path)
    want = str(p.get("expect") or "")
    if want and (st is None or st["hash"] != want):
        raise Refused("changed")
    if ports.bin is None:
        raise Refused("unsupported")
    ports.bin.recycle(path)
    if os.path.lexists(path):
        raise Refused("failed")                                        # the effect is checked: it must be gone from its folder
    return {"ok": True, "done": "recycle", "verified": True, "in_bin": ports.bin.find(path) is not None}


def _restore_bin(p: dict, ports: Ports) -> dict:
    path = check_path(str(p.get("target", "")), ports)
    if ports.bin is None:
        raise Refused("unsupported")
    size = str(p.get("expect") or "")
    item = ports.bin.find(path, int(size) if size.isdigit() else None)
    if item is None:
        raise Refused("not_in_bin")
    taken = os.path.lexists(path)
    dest = numbered_name(path) if taken else path
    if not os.path.isdir(os.path.dirname(dest)):
        raise Refused("missing")
    ports.bin.restore(item, dest, exact=not taken)
    if not os.path.lexists(dest):
        raise Refused("failed")
    return {"ok": True, "done": "restore_bin", "verified": True, "numbered": taken, "name": os.path.basename(dest)}


def _move(p: dict, ports: Ports) -> dict:
    dst = check_path(str(p.get("target", "")), ports, must_exist=True)
    src = check_path(str(p.get("path", "")), ports)
    st = stamp(dst)
    want = str(p.get("expect") or "")
    if want and (st is None or st["hash"] != want):
        raise Refused("changed")
    if os.path.lexists(src):
        raise Refused("taken")
    if not os.path.isdir(os.path.dirname(src)):
        raise Refused("missing")
    shutil.move(dst, src)
    back = stamp(src)
    if os.path.lexists(dst) or back is None or (want and back["hash"] != want):
        raise Refused("failed")
    return {"ok": True, "done": "move", "verified": True}


def _close_window(p: dict, ports: Ports) -> dict:
    if ports.windows is None:
        raise Refused("unsupported")
    proc = str(p.get("target", ""))
    try:
        pid = int(str(p.get("path") or "0"))
        opened = float(str(p.get("expect") or "0"))
    except ValueError:
        raise Refused("failed") from None
    from . import sensitive
    wins = ports.windows.find(proc, pid, opened)
    if not wins:
        return {"ok": True, "done": "close_window", "verified": True, "note": "already closed"}
    for w in wins:
        if sensitive.why(w.get("title", ""), proc):
            raise Refused("protected")
        if UNSAVED_TITLE.search(w.get("title", "")):
            raise Refused("unsaved")
    for w in wins:
        ports.windows.close(w["hwnd"])
    end = ports.clock() + CLOSE_WAIT_S
    while ports.clock() < end:
        if not any(ports.windows.alive(w["hwnd"]) for w in wins):
            return {"ok": True, "done": "close_window", "verified": True}
        ports.sleep(0.25)
    raise Refused("still_open")


def _setting(p: dict, ports: Ports) -> dict:
    name, previous, new = str(p.get("target", "")), p.get("path"), str(p.get("expect") or "")
    if name not in SETTINGS:
        raise Refused("unsupported")
    if previous in (None, ""):
        raise Refused("no_value")
    if ports.settings is None:
        raise Refused("unsupported")
    ports.settings.set(name, str(previous))
    now = ports.settings.get(name)
    if str(now) != str(previous):
        raise Refused("failed")                                         # read back: the value must be the earlier one
    return {"ok": True, "done": "setting", "verified": True}


JOBS = {"recycle": _recycle, "restore_bin": _restore_bin, "move": _move, "close_window": _close_window, "setting": _setting}


def run(p: dict, ports: Ports | None = None) -> dict:
    """One pc_undo job (already authorized by the kernel). {ok: True, done, verified} or {ok: False, refused: <code>, error: <sentence>}.
    The sentence is one of REASONS: it never holds a path or a name."""
    op = str(p.get("op", ""))
    if op not in JOBS:
        return {"ok": False, "refused": "unsupported", "error": reason_text("unsupported")}
    try:
        ports = ports or default_ports()
        return JOBS[op](p, ports)
    except Refused as e:
        return {"ok": False, "refused": e.code, "error": reason_text(e.code)}
    except OSError as e:
        return {"ok": False, "refused": "failed", "error": reason_text("failed"), "errno": getattr(e, "errno", None)}


# ---------------------------------------------------------------------------------------------------------------------- Windows ports
def default_roots(env: dict | None = None) -> list[str]:
    env = os.environ if env is None else env
    roots = [env.get("USERPROFILE") or os.path.expanduser("~")]
    for k in ("JARVIS_OUTPUTS", "DELIVERABLES_OUT_DIR"):
        if env.get(k):
            roots.append(env[k])
    try:
        from .paths import jarvis_home
        roots.append(os.path.join(jarvis_home(), "outputs"))
    except Exception:  # noqa: BLE001
        pass
    roots += [r for r in str(env.get("JARVIS_UNDO_ROOTS") or "").split(os.pathsep) if r]
    return [r for r in roots if r]


def default_ports() -> Ports:
    if os.name != "nt":
        raise Refused("unsupported")
    root, _ = journal.default_root()
    return Ports(default_roots(), bin=WinBin.make(), windows=WinWindows(), settings=WinSettings(), protected=[str(root)] if root else [])


class WinBin:
    @staticmethod
    def make() -> Bin:
        import ctypes
        import getpass  # noqa: F401

        def dirs() -> list[str]:
            out = []
            for letter in "CDEFGH":
                base = f"{letter}:\\$Recycle.Bin"
                if os.path.isdir(base):
                    try:
                        out += [os.path.join(base, d) for d in os.listdir(base) if d.startswith("S-1-5-21") and os.access(os.path.join(base, d), os.R_OK)]
                    except OSError:
                        pass
            return out

        def recycler(path: str) -> None:
            from ctypes import wintypes

            class OP(ctypes.Structure):
                _fields_ = [("hwnd", wintypes.HWND), ("func", wintypes.UINT), ("src", ctypes.c_wchar_p), ("dst", ctypes.c_wchar_p), ("flags", ctypes.c_ushort),
                            ("aborted", wintypes.BOOL), ("maps", ctypes.c_void_p), ("title", ctypes.c_wchar_p)]
            op = OP(None, 3, path + "\0", None, 0x40 | 0x10 | 0x04 | 0x400, False, None, None)          # FO_DELETE, ALLOWUNDO | NOCONFIRMATION | SILENT | NOERRORUI
            if ctypes.windll.shell32.SHFileOperationW(ctypes.byref(op)) != 0 or op.aborted:
                raise OSError("SHFileOperation failed")

        def exact(item: dict, dest: str) -> None:
            from . import runtime
            script = ("$s = New-Object -ComObject Shell.Application; $it = $s.NameSpace(10).Items() | "
                      "Where-Object { $_.Path -eq $env:JARVIS_BIN_ITEM } | Select-Object -First 1; if (-not $it) { throw 'not in bin' }; "
                      "$it.InvokeVerb('undelete'); Start-Sleep -Milliseconds 600")
            r = runtime.powershell(script, {"JARVIS_BIN_ITEM": item["r"]}, 30)
            if r.returncode:
                raise OSError("undelete failed")
        return Bin(dirs, recycler, exact)


class WinWindows:
    """The windows JARVIS opened (windowpick's registry) that are still on screen."""

    def find(self, proc: str, pid: int, opened_at: float) -> list[dict]:
        from . import windowpick, windows
        mine = [e for e in windowpick._load() if e.get("proc") == proc.lower() and (not pid or e.get("pid") == pid)
                and float(e.get("t", 0)) >= opened_at - 10]
        live = {w["hwnd"]: w for w in windows.list_windows()}
        return [{"hwnd": e["hwnd"], "title": live[e["hwnd"]]["title"]} for e in mine if e["hwnd"] in live and (not pid or live[e["hwnd"]]["pid"] == pid)]

    def close(self, hwnd: int) -> None:
        import ctypes
        ctypes.windll.user32.PostMessageW(hwnd, 0x0010, 0, 0)            # WM_CLOSE: the app may ask to save; nothing is killed

    def alive(self, hwnd: int) -> bool:
        import ctypes
        return bool(ctypes.windll.user32.IsWindow(hwnd) and ctypes.windll.user32.IsWindowVisible(hwnd))


class WinSettings:
    """Reads and writes five settings. Values are strings: dark_mode dark|light, volume 0-100, brightness 0-100, wallpaper a path, dnd on|off."""
    PERSONALIZE = r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize"
    NOTIFY = r"Software\Microsoft\Windows\CurrentVersion\PushNotifications"          # D54's `setting dnd` writes ToastEnabled here: 0 = toasts off = do-not-disturb on

    def get(self, name: str) -> str | None:
        if name == "dark_mode":
            import winreg
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, self.PERSONALIZE) as k:
                return "light" if winreg.QueryValueEx(k, "AppsUseLightTheme")[0] else "dark"
        if name == "dnd":
            import winreg
            try:
                with winreg.OpenKey(winreg.HKEY_CURRENT_USER, self.NOTIFY) as k:
                    return "off" if winreg.QueryValueEx(k, "ToastEnabled")[0] else "on"
            except OSError:
                return "off"
        if name == "wallpaper":
            import ctypes
            buf = ctypes.create_unicode_buffer(520)
            ctypes.windll.user32.SystemParametersInfoW(0x73, 520, buf, 0)
            return buf.value
        return self._ps(name, None)

    def set(self, name: str, value: str) -> None:
        if name == "dark_mode":
            import winreg
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, self.PERSONALIZE, 0, winreg.KEY_SET_VALUE) as k:
                for v in ("AppsUseLightTheme", "SystemUsesLightTheme"):
                    winreg.SetValueEx(k, v, 0, winreg.REG_DWORD, 1 if value == "light" else 0)
            return
        if name == "dnd":
            import winreg
            with winreg.CreateKey(winreg.HKEY_CURRENT_USER, self.NOTIFY) as k:
                winreg.SetValueEx(k, "ToastEnabled", 0, winreg.REG_DWORD, 0 if value == "on" else 1)
            return
        if name == "wallpaper":
            import ctypes
            if not ctypes.windll.user32.SystemParametersInfoW(0x14, 0, value, 3):
                raise OSError("wallpaper not set")
            return
        self._ps(name, value)

    def _ps(self, name: str, value: str | None) -> str | None:
        from . import runtime
        env = {"JARVIS_SET_VALUE": "" if value is None else str(int(float(value)))}
        if name == "brightness":
            script = ("if ($env:JARVIS_SET_VALUE -ne '') { Invoke-CimMethod -Namespace root/WMI -ClassName WmiMonitorBrightnessMethods -MethodName WmiSetBrightness "
                      "-Arguments @{Timeout=1; Brightness=[byte]$env:JARVIS_SET_VALUE} | Out-Null; Start-Sleep -Milliseconds 300 }; "
                      "(Get-CimInstance -Namespace root/WMI -ClassName WmiMonitorBrightness | Select-Object -First 1).CurrentBrightness")
        elif name == "volume":
            script = _VOLUME_PS
        else:
            raise Refused("unsupported")
        r = runtime.powershell(script, env, 20)
        if r.returncode:
            raise OSError("setting failed")
        return (r.stdout or "").strip().splitlines()[-1] if (r.stdout or "").strip() else None


_VOLUME_PS = r"""
Add-Type -TypeDefinition @'
using System; using System.Runtime.InteropServices;
[Guid("5CDF2C82-841E-4546-9722-0CF74078229A"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
interface IAudioEndpointVolume { int f(); int g(); int h(); int i(); int SetMasterVolumeLevelScalar(float l, Guid c); int k(); int GetMasterVolumeLevelScalar(out float l); }
[Guid("D666063F-1587-4E43-81F1-B948E807363F"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
interface IMMDevice { int Activate(ref Guid id, int ctx, IntPtr p, out IAudioEndpointVolume v); }
[Guid("A95664D2-9614-4F35-A746-DE8DB63617E6"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
interface IMMDeviceEnumerator { int f(); int GetDefaultAudioEndpoint(int flow, int role, out IMMDevice d); }
[ComImport, Guid("BCDE0395-E52F-49B9-9BF8-B6BEAD2C8F0E")] class MMDeviceEnumeratorComObject { }
public class Vol {
  static IAudioEndpointVolume V() { var e = (IMMDeviceEnumerator)(new MMDeviceEnumeratorComObject()); IMMDevice d; e.GetDefaultAudioEndpoint(0, 1, out d);
    var g = typeof(IAudioEndpointVolume).GUID; IAudioEndpointVolume v; d.Activate(ref g, 23, IntPtr.Zero, out v); return v; }
  public static int Get() { float l; V().GetMasterVolumeLevelScalar(out l); return (int)Math.Round(l * 100); }
  public static void Set(int p) { V().SetMasterVolumeLevelScalar(p / 100f, Guid.Empty); }
}
'@
if ($env:JARVIS_SET_VALUE -ne '') { [Vol]::Set([int]$env:JARVIS_SET_VALUE) }
[Vol]::Get()
"""
