"""A fake desktop for the D65 control-loop tests: the `Port` of plugins/desktop/perceive.py, with a simulated clock and scriptable PROBLEMS.

Everything the ladder has to cope with can be switched on in a test: a window that is minimised, on another virtual desktop or behind another window, an app that starts
slowly, hangs for a while, closes itself, a dialog that appears mid-task, a control that is not there until the window is scrolled or a menu is opened, a typed
text that is lost the first time, a click that has no visible effect, an owner who grabs the mouse. Time is simulated (`sleep` moves the clock and fires scheduled
events), so a 20-second wait costs nothing. No Windows, no PowerShell, no kernel (the wiring tests use fakewin.py with the real kernel instead).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Callable


@dataclass
class Win:
    process: str
    title: str
    minimized: bool = False
    desktop: int = 1
    hung_until: float = 0.0
    hwnd: int = 0
    cls: str = ""


class App:
    def __init__(self, process: str, title: str):
        self.process, self.title = process, title
        self.screen: list[str] = []
        self.elements: dict[str, Callable[["App"], None]] = {}      # visible controls: name -> handler
        self.hidden: dict[str, Callable[["App"], None]] = {}        # appear after `reveal` (scroll or a menu)
        self.menus: dict[str, dict[str, Callable[["App"], None]]] = {}
        self.dialogs: list[dict] = []
        self.doc = ""
        self.keys: dict[str, Callable[["App"], None]] = {}
        self.log: list[tuple] = []
        self.lose_type = 0                  # the next N type calls "succeed" but the text does not land
        self.type_fails = 0                 # the next N type calls fail with 'text not in field'
        self.noop_clicks: set[str] = set()  # clicks that do nothing visible
        self.field_loses_focus = 0
        self.scrolled = 0
        self.menu_open = ""
        self.has_palette = False

    def dialog(self, title, texts, buttons):
        self.dialogs.append({"title": title, "texts": list(texts), "buttons": dict(buttons)})

    def lines(self) -> list[str]:
        return list(self.screen) + ([self.doc] if self.doc else [])


class FakeDesktop:
    def __init__(self):
        self.t = 0.0
        self.wins: list[Win] = []
        self.apps: dict[str, App] = {}
        self.factories: dict[str, Callable[["FakeDesktop"], None]] = {}
        self.start_delay: dict[str, float] = {}
        self.events: list[tuple[float, Callable[[], None]]] = []
        self.front: int | None = None
        self.prev_front: int | None = None
        self.cur_desktop = 1
        self.jobs: list[dict] = []
        self.last_input = -1000.0
        self.owner_until = -1.0          # the owner is using the mouse until this time
        self.cursor = (400, 300)
        self.focus_ok_on_other_desktop = False
        self.focus_blocked: set[str] = set()      # processes whose focus job 'works' but never brings the window forward (the foreground lock)
        self.ocr_text: dict[str, str] = {}
        self.vision_text: dict[str, str] = {}
        self.points: dict[str, dict] = {}     # label -> {"ocr": (x, y), "vision": (x, y)}
        self.next_hwnd = 100
        self.disk_full = False
        self.held: set[str] = set()      # (op, action, target) substrings the kernel "holds" for approval
        self.busy: dict[str, bool] = {}
        self.calls = {"uia": 0, "ocr": 0, "vision": 0, "windows": 0}

    # ---- time
    def clock(self) -> float:
        return self.t

    def sleep(self, s: float) -> None:
        self.t += max(0.0, s)
        due = [e for e in self.events if e[0] <= self.t]
        self.events = [e for e in self.events if e[0] > self.t]
        for _, fn in sorted(due, key=lambda e: e[0]):
            fn()

    def at(self, seconds_from_now: float, fn: Callable[[], None]) -> None:
        self.events.append((self.t + seconds_from_now, fn))

    # ---- building the scene
    def add_app(self, process: str, title: str, *, foreground: bool = True, minimized: bool = False, desktop: int | None = None) -> App:
        app = App(process, title)
        self.apps[process.lower()] = app
        self.next_hwnd += 1
        w = Win(process, title, minimized, desktop or self.cur_desktop, hwnd=self.next_hwnd)
        self.wins.append(w)
        if foreground and not minimized:
            self.prev_front, self.front = self.front, w.hwnd
        return app

    def win(self, process: str) -> Win | None:
        return next((w for w in self.wins if w.process.lower() == process.lower()), None)

    def remove_app(self, process: str) -> None:
        self.wins = [w for w in self.wins if w.process.lower() != process.lower()]
        self.apps.pop(process.lower(), None)
        if self.front not in {w.hwnd for w in self.wins}:
            self.front = None

    def owner_uses_mouse(self, seconds: float) -> None:
        self.owner_until = self.t + seconds

    # ---- the Port
    def windows(self) -> dict:
        self.calls["windows"] += 1
        idle = 0 if self.t < self.owner_until else int((self.t - self.last_input) * 1000)
        cursor = (self.cursor[0] + (37 if self.t < self.owner_until else 0), self.cursor[1])
        out = []
        for w in self.wins:
            out.append({"process": w.process, "title": w.title + (" (Not Responding)" if self.t < w.hung_until else ""), "foreground": w.hwnd == self.front and w.desktop == self.cur_desktop,
                        "minimized": w.minimized, "hung": self.t < w.hung_until, "cloaked": w.desktop != self.cur_desktop, "class": w.cls})
        return {"windows": out, "idle_ms": idle, "cursor": list(cursor)}

    def _app(self, name: str) -> App | None:
        n = re.sub(r"\.exe$", "", str(name).lower())
        if n in self.apps:
            return self.apps[n]
        for k, a in self.apps.items():
            if n in a.title.lower() or k in n:
                return a
        return None

    def uia(self, app: str) -> list[dict]:
        self.calls["uia"] += 1
        a = self._app(app)
        w = self.win(a.process) if a else None
        if a is None or w is None:
            return []
        if self.t < w.hung_until:
            raise TimeoutError("hung")
        shown = list(a.elements) + [k for k in a.hidden if a.scrolled]
        if a.menu_open:
            shown += list(a.menus.get(a.menu_open, {}))
        shown += list(a.menus)
        rows = [{"title": a.title, "dialog": False, "texts": a.lines(), "buttons": shown}]
        for d in a.dialogs:
            rows.append({"title": d["title"], "dialog": True, "texts": list(d["texts"]), "buttons": list(d["buttons"])})
        return rows

    def ocr(self, app: str) -> str:
        self.calls["ocr"] += 1
        a = self._app(app)
        return self.ocr_text.get(app.lower(), "\n".join(a.lines()) if a else "")

    def vision(self, app: str) -> str:
        self.calls["vision"] += 1
        a = self._app(app)
        return self.vision_text.get(app.lower(), "\n".join(a.lines()) if a else "")

    def locate(self, app: str, label: str) -> dict:
        return dict(self.points.get(label.lower(), {}))

    def app_info(self, app: str) -> dict:
        a = self._app(app)
        w = self.win(a.process) if a else None
        return {"responding": not (w and self.t < w.hung_until), "busy": bool(self.busy.get(app.lower()))}

    # ---- the kernel job
    def act(self, body: dict) -> dict:
        self.jobs.append(dict(body))
        key = f"{body.get('op')}|{body.get('action')}|{body.get('target')}|{body.get('text')}"
        for h in self.held:
            if h in key:
                return {"ok": False, "state": "waiting_for_permission", "approval_id": "apr-1", "how": "approve it"}
        op = body.get("op")
        if op != "diag":
            self.last_input = self.t
        if op == "open_app":
            out = self._open(str(body.get("target")))
        elif op == "ui":
            out = self._ui(body)
        else:
            out = {"ok": False, "state": "done", "error": "fake: " + str(op)}
        self.sleep(0.4)                                                    # a kernel job takes time
        return out

    def _open(self, name: str) -> dict:
        key = name.lower()
        if self.disk_full and key == "never":
            return {"ok": False, "state": "done", "error": "x"}
        if key not in self.factories:
            return {"ok": False, "state": "done", "error": "no such app in the Start list"}
        delay = self.start_delay.get(key, 0.0)
        if delay:
            self.at(delay, lambda: self.factories[key](self))
        else:
            self.factories[key](self)
        return {"ok": True, "state": "done", "verified": "verified" if not delay else "unconfirmed", "opened": name}

    def _fail(self, reason: str) -> dict:
        return {"ok": False, "state": "done", "error": "RuntimeError", "reason": reason}

    def _ui(self, b: dict) -> dict:
        action, target, text = b.get("action"), str(b.get("target") or ""), str(b.get("text") or "")
        proc, _, elem = target.partition("|")
        if proc.lower() == "desktop":
            return self._desktop_key(text)
        a = self._app(proc)
        w = self.win(a.process) if a else None
        if a is None or w is None:
            return self._fail("no window")
        if self.t < w.hung_until:
            return {"ok": False, "state": "done", "error": "TimeoutError"}
        if action == "focus":
            if a.process.lower() in self.focus_blocked:
                return self._fail("target is not the foreground window")
            if w.desktop != self.cur_desktop and not self.focus_ok_on_other_desktop:
                return self._fail("target is not the foreground window")
            w.minimized = False
            self.cur_desktop = w.desktop
            self.prev_front, self.front = self.front, w.hwnd
            return {"ok": True, "state": "done", "foreground": True, "observed": "the target is the foreground window"}
        if action == "restore":
            w.minimized = False
            return {"ok": True, "state": "done", "window_state": "normal", "observed": "window state read back"}
        if w.minimized or w.desktop != self.cur_desktop or self.front != w.hwnd:
            return self._fail("target is not the foreground window")        # input only reaches the window in front
        if action == "click":
            return self._click(a, elem)
        if action == "type":
            if a.type_fails:
                a.type_fails -= 1
                return self._fail("text not in field")
            if a.lose_type:
                a.lose_type -= 1
                return {"ok": True, "state": "done"}                       # claimed, but nothing landed (and nothing could be read back)
            a.doc = text
            return {"ok": True, "state": "done", "observed": "text read back from the field"}
        if action == "type_focused":
            if self.disk_full and False:
                pass
            a.doc += text
            return {"ok": True, "state": "done", "observed": "text read back from the editor"}
        if action == "key":
            if text == "esc":
                a.menu_open = ""
            fn = a.keys.get(text)
            if fn:
                fn(a)
            return {"ok": True, "state": "done"}
        if action == "scroll":
            a.scrolled += 1
            return {"ok": True, "state": "done"}
        if action == "click_at":
            a.log.append(("click_at", elem))
            return {"ok": True, "state": "done"}
        if action == "close":
            self.remove_app(a.process)
            return {"ok": True, "state": "done", "closed": True}
        return {"ok": True, "state": "done"}

    def _click(self, a: App, el: str) -> dict:
        n = re.sub(r"&", "", el).strip().lower()
        if a.dialogs:
            d = a.dialogs[-1]
            for name, fn in d["buttons"].items():
                if name.lower() == n:
                    a.dialogs.pop()
                    fn(a)
                    return {"ok": True, "state": "done"}
        for name, fn in list(a.elements.items()) + [(k, v) for k, v in a.hidden.items() if a.scrolled]:
            if name.lower() == n:
                if name in a.noop_clicks:
                    return {"ok": True, "state": "done"}
                fn(a)
                return {"ok": True, "state": "done"}
        if a.menu_open and n in {k.lower() for k in a.menus.get(a.menu_open, {})}:
            fn = next(v for k, v in a.menus[a.menu_open].items() if k.lower() == n)
            fn(a)
            a.menu_open = ""
            return {"ok": True, "state": "done"}
        for m in a.menus:
            if m.lower() == n:
                a.menu_open = m
                a.screen = [x for x in a.screen if not x.startswith("[menu]")] + [f"[menu] {m} open"]
                return {"ok": True, "state": "done"}
        return self._fail("no element")

    def _desktop_key(self, k: str) -> dict:
        if k == "alt+tab":
            if self.prev_front is not None:
                self.prev_front, self.front = self.front, self.prev_front
                w = next((x for x in self.wins if x.hwnd == self.front), None)
                if w is not None:
                    w.minimized = False
            return {"ok": True, "state": "done"}
        if k in ("ctrl+win+right", "ctrl+win+left"):
            self.cur_desktop = max(1, min(3, self.cur_desktop + (1 if k.endswith("right") else -1)))
            on = [w for w in self.wins if w.desktop == self.cur_desktop and not w.minimized]
            if on:
                self.front = on[0].hwnd
            return {"ok": True, "state": "done"}
        return {"ok": True, "state": "done"}


def classify_by_words(text: str, app: str = "") -> tuple[str, str]:
    """A stand-in for decider.classify_dialog that needs no model: the decision engine's own RULES, no model call."""
    from jarvis.plugins._shared import decider
    d = decider.classify_dialog(text, app, use_model=False)
    return d.choice or "B", d.source
