"""What is actually on this Windows desktop, read with the Win32 API (ctypes only: no extra dependency).

Used to *observe* after an action instead of assuming it worked: list the visible top-level windows, wait for one to
appear, and take a real screenshot. Nothing here clicks or types, and nothing leaves the laptop on its own: window titles
are scrubbed for secret patterns before they go into a job result, and a screenshot is written to the owner's outputs
folder (never returned inline).
"""
import os
import struct
import sys
import time
import zlib

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "kernel"))

ON_WINDOWS = os.name == "nt"
SKIP_CLASSES = {"Progman", "WorkerW", "Shell_TrayWnd", "Shell_SecondaryTrayWnd", "Windows.UI.Core.CoreWindow", "ApplicationFrameInputSinkWindow"}
SKIP_TITLES = {"", "Program Manager", "Windows Input Experience", "Settings"}  # "Settings" is kept only when it has a real size (see below)

if ON_WINDOWS:
    import ctypes
    from ctypes import wintypes
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)      # real pixels on a scaled display
    except Exception:
        try:
            user32.SetProcessDPIAware()
        except Exception:
            pass


def _process_name(pid: int) -> str:
    h = kernel32.OpenProcess(0x1000, False, pid)            # PROCESS_QUERY_LIMITED_INFORMATION
    if not h:
        return ""
    try:
        buf = ctypes.create_unicode_buffer(520)
        n = wintypes.DWORD(520)
        if kernel32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(n)):
            return os.path.splitext(os.path.basename(buf.value))[0]
    finally:
        kernel32.CloseHandle(h)
    return ""


def list_windows(include_minimized=True) -> list:
    """Visible top-level windows with a title: [{hwnd, pid, process, title, class, foreground, minimized, x, y, w, h}]."""
    if not ON_WINDOWS:
        return []
    from jarvis_kernel.scrub import scrub
    out, fg = [], user32.GetForegroundWindow()
    cls = ctypes.create_unicode_buffer(256)
    title = ctypes.create_unicode_buffer(512)
    rect = wintypes.RECT()
    proto = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    @proto
    def cb(hwnd, _):
        if not user32.IsWindowVisible(hwnd):
            return True
        user32.GetWindowTextW(hwnd, title, 512)
        user32.GetClassNameW(hwnd, cls, 256)
        t, c = title.value, cls.value
        if not t or c in SKIP_CLASSES or t == "Program Manager":
            return True
        mini = bool(user32.IsIconic(hwnd))
        if mini and not include_minimized:
            return True
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        user32.GetWindowRect(hwnd, ctypes.byref(rect))
        w, h = rect.right - rect.left, rect.bottom - rect.top
        if not mini and (w < 40 or h < 20):
            return True
        out.append({"hwnd": int(hwnd), "pid": int(pid.value), "process": _process_name(pid.value), "title": t,
                    "class": c, "foreground": hwnd == fg, "minimized": mini, "x": rect.left, "y": rect.top, "w": w, "h": h,
                    "hung": _is_hung(hwnd), "cloaked": _is_cloaked(hwnd)})
        return True
    user32.EnumWindows(cb, 0)
    return _scrub_titles(out)


def _is_hung(hwnd) -> bool:
    """Windows' own verdict that the window's thread has not pumped messages for about 5 seconds (the "(Not Responding)" state)."""
    try:
        return bool(user32.IsHungAppWindow(hwnd))
    except Exception:  # noqa: BLE001 - an API that is missing is "not known to be hung"
        return False


def _is_cloaked(hwnd) -> bool:
    """True for a window on another virtual desktop (DWMWA_CLOAKED = 14 with the shell flag): it is in the list but not on this desktop."""
    try:
        val = wintypes.DWORD(0)
        if ctypes.windll.dwmapi.DwmGetWindowAttribute(wintypes.HWND(hwnd), 14, ctypes.byref(val), ctypes.sizeof(val)) == 0:
            return bool(val.value)
    except Exception:  # noqa: BLE001
        pass
    return False


def input_state() -> dict:
    """{idle_ms, cursor: [x, y]}: milliseconds since the last keyboard or mouse input of ANY kind (JARVIS's own synthetic input counts, so the
    plugin compares it with the time of its own last action), and where the mouse pointer is. {} off Windows or when Windows will not say."""
    if not ON_WINDOWS:
        return {}
    try:
        class LII(ctypes.Structure):
            _fields_ = [("cbSize", wintypes.UINT), ("dwTime", wintypes.DWORD)]
        info = LII(ctypes.sizeof(LII), 0)
        if not user32.GetLastInputInfo(ctypes.byref(info)):
            return {}
        idle = int((kernel32.GetTickCount() - info.dwTime) & 0xFFFFFFFF)       # the tick counter wraps every 49 days
        pt = wintypes.POINT()
        got = user32.GetCursorPos(ctypes.byref(pt))
        return {"idle_ms": idle, **({"cursor": [int(pt.x), int(pt.y)]} if got else {})}
    except Exception:  # noqa: BLE001
        return {}


def _scrub_titles(out: list) -> list:
    """The same scrub as scrub(t, deep=True) per title, but ONE gitleaks run over all titles instead of one per title
    (each run costs ~0.5 s: a 5-window list took 2.5 s, measured 2026-10-16). Built-in pass per title, then every
    secret the deep pass finds anywhere is redacted in every title it appears in."""
    from jarvis_kernel.scrub import _gitleaks_secrets, scrub
    for w in out:
        w["title"] = scrub(w["title"])[0]
    joined = "\n".join(w["title"] for w in out)
    secrets = [s for _, s in (_gitleaks_secrets(joined) or []) if len(s) >= 6] if joined else []
    for w in out:
        for s in secrets:
            w["title"] = w["title"].replace(s, "[REDACTED]")
        w["title"] = w["title"][:120]
    return out


def wait_for(match, timeout=6.0, interval=0.4, lister=None, before=None):
    """Poll the window list until match(window) is true. Returns the window dict, or None after `timeout` seconds.
    Windows already open in `before` (a set of hwnds) never count as the new one."""
    lister = lister or list_windows
    before = before or set()
    end = time.time() + timeout
    while True:
        for w in lister():
            if w["hwnd"] not in before and match(w):
                return w
        if time.time() >= end:
            return None
        time.sleep(interval)


def explorer_locations() -> list:
    """The folders that open File Explorer windows show (Shell.Application's LocationURL), lower-cased with forward slashes. Read only; [] off Windows."""
    if not ON_WINDOWS:
        return []
    import subprocess
    from urllib.parse import unquote, urlsplit
    try:
        r = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command",
                            "(New-Object -ComObject Shell.Application).Windows() | ForEach-Object { $_.LocationURL }"],
                           capture_output=True, text=True, encoding="utf-8", timeout=20, creationflags=0x08000000)
    except (OSError, subprocess.SubprocessError):
        return []
    out = []
    for line in (r.stdout or "").splitlines():
        u = urlsplit(line.strip())
        if u.scheme == "file" and u.path:
            out.append(normalize_path(unquote(u.path.lstrip("/"))))
    return out


def normalize_path(p: str) -> str:
    return str(p).replace("\\", "/").rstrip("/").lower()


def titles_match(expected: str, title: str) -> bool:
    e, t = expected.strip().lower(), title.strip().lower()
    return bool(e) and (e in t)


def screenshot_png(path: str) -> dict:
    """The whole virtual desktop as a PNG at `path` (GDI BitBlt; no dependency). Returns {width, height, bytes}."""
    if not ON_WINDOWS:
        raise OSError("screenshots need Windows")
    x, y = user32.GetSystemMetrics(76), user32.GetSystemMetrics(77)          # SM_XVIRTUALSCREEN, SM_YVIRTUALSCREEN
    w, h = user32.GetSystemMetrics(78), user32.GetSystemMetrics(79)          # SM_CXVIRTUALSCREEN, SM_CYVIRTUALSCREEN
    if w <= 0 or h <= 0:
        raise OSError("no screen")
    hdc = user32.GetDC(0)
    mem = gdi32.CreateCompatibleDC(hdc)
    bmp = gdi32.CreateCompatibleBitmap(hdc, w, h)
    old = gdi32.SelectObject(mem, bmp)
    try:
        if not gdi32.BitBlt(mem, 0, 0, w, h, hdc, x, y, 0x00CC0020 | 0x40000000):    # SRCCOPY | CAPTUREBLT
            raise OSError("BitBlt failed")

        class BMI(ctypes.Structure):
            _fields_ = [("biSize", wintypes.DWORD), ("biWidth", wintypes.LONG), ("biHeight", wintypes.LONG), ("biPlanes", wintypes.WORD),
                        ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD), ("biSizeImage", wintypes.DWORD),
                        ("biXPelsPerMeter", wintypes.LONG), ("biYPelsPerMeter", wintypes.LONG), ("biClrUsed", wintypes.DWORD),
                        ("biClrImportant", wintypes.DWORD)]
        bmi = BMI(ctypes.sizeof(BMI), w, -h, 1, 32, 0, 0, 0, 0, 0, 0)               # top-down BGRA
        buf = ctypes.create_string_buffer(w * h * 4)
        if not gdi32.GetDIBits(mem, bmp, 0, h, buf, ctypes.byref(bmi), 0):
            raise OSError("GetDIBits failed")
    finally:
        gdi32.SelectObject(mem, old)
        gdi32.DeleteObject(bmp)
        gdi32.DeleteDC(mem)
        user32.ReleaseDC(0, hdc)
    raw = bytearray(buf.raw)
    rgb = bytearray(w * h * 3)
    rgb[0::3], rgb[1::3], rgb[2::3] = raw[2::4], raw[1::4], raw[0::4]              # BGRA -> RGB
    png = encode_png(bytes(rgb), w, h)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "xb") as fh:
        fh.write(png)
    return {"width": w, "height": h, "bytes": len(png)}


def encode_png(rgb: bytes, w: int, h: int) -> bytes:
    """A valid 8-bit RGB PNG from raw pixels (zlib is in the standard library)."""
    stride = w * 3
    rows = b"".join(b"\x00" + rgb[r * stride:(r + 1) * stride] for r in range(h))

    def chunk(tag: bytes, data: bytes) -> bytes:
        c = struct.pack(">I", len(data)) + tag + data
        return c + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)) + chunk(b"IDAT", zlib.compress(rows, 3)) + chunk(b"IEND", b"")
