"""A fake PC for the undo tests (node and desktop plugin): a real temp folder as the owner's folders, a Recycle Bin with the real `$I`/`$R` layout,
fake windows and settings. Not a test file."""
import os
import shutil
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))

from jarvis_node import undo as U  # noqa: E402


class FakeBinPc:
    """A PC in a folder: Users/me is the root, bin/S-1-5-21-1 is the Recycle Bin (real layout)."""

    def __init__(self, tmp):
        self.home = tmp / "Users" / "me"
        self.home.mkdir(parents=True)
        self.binroot = tmp / "bin" / "S-1-5-21-1"
        self.binroot.mkdir(parents=True)
        self.n = 0
        self.exact_calls = []
        self.fail_recycle = False
        self.windows = FakeWindows()
        self.settings = FakeSettings()
        self.bin = U.Bin(lambda: [str(self.binroot)], self.recycle, self.exact)

    def recycle(self, path):
        if self.fail_recycle:
            return                                          # the shell "did" nothing: the effect check must catch it
        self.n += 1
        size = os.path.getsize(path) if os.path.isfile(path) else 0
        ext = os.path.splitext(path)[1]
        (self.binroot / f"$I{self.n:05d}{ext}").write_bytes(U.make_i(path, size, time.time(), version=2 if self.n % 2 else 1))
        shutil.move(path, str(self.binroot / f"$R{self.n:05d}{ext}"))

    def exact(self, item, dest):
        self.exact_calls.append(dest)
        shutil.move(item["r"], dest)
        os.remove(item["i"])

    def ports(self, **kw):
        return U.Ports([str(self.home)], bin=self.bin, windows=self.windows, settings=self.settings, protected=kw.pop("protected", []),
                       sleep=lambda s: self.windows.tick(), clock=self.windows.clock, **kw)

    def file(self, rel, text="hello"):
        p = self.home / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
        return str(p)


class FakeWindows:
    def __init__(self):
        self.wins = {}
        self.t = 0.0
        self.closes = []
        self.refuse_close = False

    def add(self, hwnd, title, proc="calculatorapp", pid=77):
        self.wins[hwnd] = {"hwnd": hwnd, "title": title, "proc": proc, "pid": pid}

    def find(self, proc, pid, opened_at):
        return [dict(w) for w in self.wins.values() if w["proc"] == proc.lower() and (not pid or w["pid"] == pid)]

    def close(self, hwnd):
        self.closes.append(hwnd)
        if not self.refuse_close:
            self.wins.pop(hwnd, None)

    def alive(self, hwnd):
        return hwnd in self.wins

    def clock(self):
        return self.t

    def tick(self):
        self.t += 1.0


class FakeSettings:
    def __init__(self):
        self.v = {"dark_mode": "dark", "volume": "30", "brightness": "60", "wallpaper": "C:\\w\\a.jpg", "dnd": "off"}
        self.broken = False

    def get(self, name):
        return self.v[name]

    def set(self, name, value):
        if not self.broken:
            self.v[name] = value
