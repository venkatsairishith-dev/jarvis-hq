"""A pretend PC for rehearsing the gate without a laptop (and for the tests): LABELLED FAKES, not Windows.

`FakeEnv` keeps real files in a real folder (so the file checks are the real ones) and pretends windows, window text and the Recycle Bin.
`python -m jarvis.evals.reliability.runner` never uses it; the tests and a dry rehearsal of a new goal do.
"""
from __future__ import annotations

import os
from pathlib import Path

from .env import LocalEnv, Win

APPS = {"calc": ("ApplicationFrameHost", "Calculator"), "notepad": ("Notepad", "Untitled - Notepad"), "mspaint": ("mspaint", "Untitled - Paint")}


class FakeEnv(LocalEnv):
    sep = os.sep

    def __init__(self):
        self._wins: list[Win] = []
        self._text: dict[int, str] = {}
        self._next = 1000
        self.bin: list[tuple[str, str]] = []            # (name, from-folder)
        self.closed: list[Win] = []
        self.purged: list[str] = []

    # ---- windows
    def add_window(self, process: str, title: str, text: str = "") -> Win:
        self._next += 1
        w = Win(self._next, 4000 + self._next, process, title)
        self._wins.append(w)
        self._text[w.hwnd] = text
        return w

    def retitle(self, win: Win, title: str) -> Win:
        self._wins = [Win(w.hwnd, w.pid, w.process, title) if w.hwnd == win.hwnd else w for w in self._wins]
        return next(w for w in self._wins if w.hwnd == win.hwnd)

    def windows(self) -> list[Win]:
        return list(self._wins)

    def window_text(self, win: Win) -> str:
        return self._text.get(win.hwnd, "")

    def set_text(self, win: Win, text: str) -> None:
        self._text[win.hwnd] = text

    def close_window(self, win: Win) -> None:
        self.closed.append(win)
        self._wins = [w for w in self._wins if w.hwnd != win.hwnd]

    # ---- the bin
    def recycle_names(self) -> list[str]:
        return [f"{n} <{d}>" for n, d in self.bin]

    def recycle_file(self, path: str) -> None:
        p = Path(path)
        if p.is_dir():
            self.remove_tree(path)
        elif p.exists():
            p.unlink()
        self.bin.append((p.name, str(p.parent)))

    def recycle_purge(self, name_contains: str) -> int:
        keep = [(n, d) for n, d in self.bin if name_contains not in n]
        n = len(self.bin) - len(keep)
        self.bin = keep
        self.purged.append(name_contains)
        return n

    # ---- starting things
    def open_app(self, name: str) -> None:
        proc, title = APPS.get(name.lower().replace(".exe", ""), (name, name))
        self.add_window(proc, title)

    def open_notepad_text(self, path: str, text: str) -> None:
        self.write_file(path, text)
        self.add_window("Notepad", f"{Path(path).name} - Notepad", text)

    def render_image(self, path: str, text: str) -> None:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"\x89PNG" + b"0" * 2000)

    def open_image_text(self, path: str, text: str) -> None:
        self.render_image(path, text)
        self.add_window("mspaint", f"{Path(path).name} - Paint", "")

    def message_box(self, title: str, text: str) -> None:
        self.add_window("powershell", title, text)
