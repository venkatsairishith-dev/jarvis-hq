r"""Screen-reading specialists on windows this script opens itself (2026-10-26).

    python vision_compare.py vision-ground vision-qwen35 [--only calc,notepad]

For each of 10 windows (apps JARVIS opens for its own test: Calculator, a new Notepad, Paint, Settings, File Explorer on the outputs folder,
Character Map, Control Panel, Task Manager, Clock, Sticky-free Snipping Tool is left out) the window alone is captured (PrintWindow, saved in the
scratch folder, never the desktop) and each llama-swap alias is asked two things:
  describe  "What is on this screen? Answer in one or two plain sentences."          -> answer text, seconds (judged by a person from the saved picture)
  close     "Output the center point of the close (X) button ... as [x,y] in pixels"  -> a point; right when it lies in the window's real close button
The windows opened here are closed again at the end (only the ones this script started). Numbers and answers only; results/vision-<stamp>.json.
"""
from __future__ import annotations

import argparse
import base64
import ctypes
import json
import os
import re
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import win32con
import win32gui
import win32process
from PIL import Image

HERE = Path(__file__).resolve().parent
OUT = Path(os.environ.get("JARVIS_VISION_SHOTS", str(HERE / "results" / "vision-shots")))
ctypes.windll.user32.SetProcessDPIAware()
DPI = ctypes.windll.user32.GetDpiForSystem() / 96.0

# name -> (command to open, a regex the window title or class must match)
APPS = {
    "calc": (["cmd", "/c", "start", "", "calc.exe"], r"(?i)^calculator$"),
    "notepad": (["notepad.exe"], r"(?i)^(untitled|\*?untitled).*notepad$"),
    "paint": (["mspaint.exe"], r"(?i)paint$"),
    "settings": (["cmd", "/c", "start", "", "ms-settings:"], r"(?i)^settings$"),
    "explorer": (["explorer.exe", r"D:\jarvis\outputs"], r"(?i)^outputs$"),
    "charmap": (["charmap.exe"], r"(?i)character map"),
    "control": (["control.exe"], r"(?i)control panel"),
    "taskmgr": (["cmd", "/c", "start", "", "taskmgr.exe"], r"(?i)task manager"),
    "clock": (["cmd", "/c", "start", "", "ms-clock:"], r"(?i)^clock$"),
    "winver": (["winver.exe"], r"(?i)about windows"),
}


def find_window(pattern: str, known: set[int]) -> int | None:
    found: list[int] = []

    def cb(h, _):
        if h not in known and win32gui.IsWindowVisible(h) and re.search(pattern, win32gui.GetWindowText(h) or ""):
            found.append(h)
    win32gui.EnumWindows(cb, None)
    return found[0] if found else None


def all_windows() -> set[int]:
    out: set[int] = set()
    win32gui.EnumWindows(lambda h, _: out.add(h), None)
    return out


def capture(hwnd: int, path: Path) -> tuple[int, int, tuple[int, int, int, int]]:
    """PrintWindow of this window only; returns (w, h, frame rect on the screen)."""
    rect = ctypes.wintypes.RECT()
    ctypes.windll.dwmapi.DwmGetWindowAttribute(hwnd, 9, ctypes.byref(rect), ctypes.sizeof(rect))     # DWMWA_EXTENDED_FRAME_BOUNDS
    l, t, r, b = win32gui.GetWindowRect(hwnd)
    w, h = r - l, b - t
    import win32ui
    dc = win32gui.GetWindowDC(hwnd)
    src = win32ui.CreateDCFromHandle(dc)
    mem = src.CreateCompatibleDC()
    bmp = win32ui.CreateBitmap()
    bmp.CreateCompatibleBitmap(src, w, h)
    mem.SelectObject(bmp)
    ctypes.windll.user32.PrintWindow(hwnd, mem.GetSafeHdc(), 2)
    info = bmp.GetInfo()
    img = Image.frombuffer("RGB", (info["bmWidth"], info["bmHeight"]), bmp.GetBitmapBits(True), "raw", "BGRX", 0, 1)
    img.save(path)
    win32gui.DeleteObject(bmp.GetHandle())
    mem.DeleteDC()
    src.DeleteDC()
    win32gui.ReleaseDC(hwnd, dc)
    return w, h, (l, t, r, b)


def post(url: str, model: str, png: Path, prompt: str, max_tokens: int = 200) -> tuple[str, float]:
    b64 = base64.b64encode(png.read_bytes()).decode()
    body = {"model": model, "temperature": 0, "max_tokens": max_tokens, "chat_template_kwargs": {"enable_thinking": False},
            "messages": [{"role": "user", "content": [{"type": "image_url", "image_url": {"url": "data:image/png;base64," + b64}}, {"type": "text", "text": prompt}]}]}
    req = urllib.request.Request(url + "/v1/chat/completions", data=json.dumps(body).encode(), headers={"Content-Type": "application/json"})
    t = time.time()
    try:
        with urllib.request.urlopen(req, timeout=300) as r:
            out = json.loads(r.read())
        msg = out["choices"][0]["message"]
        return (msg.get("content") or msg.get("reasoning_content") or "").strip(), round(time.time() - t, 1)
    except Exception as e:                                                                # noqa: BLE001
        return f"[error {type(e).__name__}]", round(time.time() - t, 1)


def point(text: str, w: int, h: int) -> tuple[float, float] | None:
    m = re.search(r"\[?\(?\s*(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)\s*\)?\]?", text)
    if not m:
        return None
    x, y = float(m.group(1)), float(m.group(2))
    if x < 0:
        return None
    if x <= 1.0 and y <= 1.0:                                                             # relative 0..1
        return x * w, y * h
    if x <= 1000 and y <= 1000 and (w > 1000 or h > 1000) and max(x, y) <= 1000 and (x > w or y > h):
        return x / 1000 * w, y / 1000 * h                                                 # 0..1000 grid when it cannot be pixels
    return x, y


def in_close(pt: tuple[float, float] | None, w: int, h: int) -> bool:
    """The real close button: the 46 x 32 box (at 100% DPI) in the top-right corner of the window picture."""
    if pt is None:
        return False
    bw, bh = 46 * DPI, 34 * DPI
    return w - bw - 4 <= pt[0] <= w + 2 and -2 <= pt[1] <= bh + 2


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("aliases", nargs="+")
    ap.add_argument("--url", default="http://127.0.0.1:8080")
    ap.add_argument("--only", default="")
    a = ap.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    names = [n for n in APPS if not a.only or n in a.only.split(",")]
    opened: list[tuple[str, int]] = []
    shots: dict[str, dict] = {}
    for n in names:
        known = all_windows()
        subprocess.Popen(APPS[n][0], close_fds=True)
        hwnd = None
        for _ in range(30):
            time.sleep(1)
            hwnd = find_window(APPS[n][1], known)
            if hwnd:
                break
        if not hwnd:
            print(n, "no window", flush=True)
            continue
        time.sleep(2)
        opened.append((n, hwnd))
        png = OUT / f"{n}.png"
        w, h, rect = capture(hwnd, png)
        shots[n] = {"png": str(png), "w": w, "h": h, "title": win32gui.GetWindowText(hwnd)}
        print(n, w, h, shots[n]["title"], flush=True)
    rows = []
    for alias in a.aliases:
        for n, s in shots.items():
            d_text, d_s = post(a.url, alias, Path(s["png"]), "What is on this screen? Answer in one or two plain sentences.", 120)
            c_text, c_s = post(a.url, alias, Path(s["png"]), "Output the center point of the close (X) button of this window, in pixels of this image. "
                               "The output should just be the coordinates of a point, in the format [x,y]. If there is none, output [-1,-1].", 40)
            pt = point(c_text, s["w"], s["h"])
            row = {"alias": alias, "window": n, "title": s["title"], "describe": d_text[:400], "describe_s": d_s, "close_raw": c_text[:60], "close_point": pt,
                   "close_right": in_close(pt, s["w"], s["h"]), "close_s": c_s, "image": [s["w"], s["h"]]}
            rows.append(row)
            print(json.dumps({k: row[k] for k in ("alias", "window", "describe_s", "close_raw", "close_right", "close_s")}), flush=True)
    for n, hwnd in opened:                                                                # only what this script started
        try:
            win32gui.PostMessage(hwnd, win32con.WM_CLOSE, 0, 0)
        except Exception:                                                                 # noqa: BLE001
            pass
    time.sleep(2)
    res = HERE / "results"
    res.mkdir(exist_ok=True)
    path = res / f"vision-{time.strftime('%Y%m%d-%H%M%S')}.json"
    path.write_text(json.dumps({"time": time.strftime("%Y-%m-%d %H:%M:%S"), "dpi_scale": DPI, "rows": rows}, indent=1), encoding="utf-8")
    print("wrote", path)
    return 0


if __name__ == "__main__":
    import ctypes.wintypes  # noqa: F401
    raise SystemExit(main())
