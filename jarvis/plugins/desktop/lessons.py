"""Lessons: what worked the last time an app gave JARVIS a problem (D65). Local, small, and CONTENT-FREE.

A lesson is `(app, problem class) -> route that worked` with a success and a failure count. The next run consults it BEFORE the problem ladder, so the
route that worked for this app is tried first ("Excel keeps its File menu behind Alt+F, not behind a button I can click" becomes `keyboard`).

What a lesson can hold, by construction (every field is checked on the way in and again on the way out of the file):
  app      a process name (letters, digits, dot, dash, underscore, space; at most 40), lower-cased: never a window title
  problem  one word of PROBLEMS (below)
  route    one word of ROUTES (below)
  counts   two small integers and a date
It never holds typed text, a file name, a window title, a path, a dialog's words or anything the screen said. The file is capped (MAX_ENTRIES apps x problems,
MAX_BYTES), the oldest lesson goes first, and the owner can see it and clear it by asking ("what have you learned about Excel", "forget that").
Standard library only.
"""
from __future__ import annotations

import json
import os
import re
import threading
import time
from pathlib import Path

DATA = Path(os.environ.get("JARVIS_DATA_DIR", r"D:\jarvis\data"))
FILE = DATA / "plugins" / "lessons.json"
MAX_ENTRIES = 300
MAX_BYTES = 64 * 1024

# the closed vocabularies: anything else is refused, so a lesson can never carry free text
PROBLEMS = ("wrong_window", "no_window", "minimized", "other_desktop", "dialog", "not_responding", "element_missing", "typed_not_landed", "no_effect",
            "slow_start", "window_vanished", "action")
ROUTES = ("uia", "keyboard", "coordinates", "menu", "search", "palette", "paste", "refocus", "restore", "focus_title", "alt_tab", "virtual_desktop",
          "launch", "wait", "scroll", "expand_menu", "vision", "answer", "relaunch", "replan")
APP = re.compile(r"^[a-z0-9][a-z0-9_. \-]{0,39}$")
_LOCK = threading.Lock()
LOG_LAST = "last"

PLAIN = {
    "wrong_window": "the wrong window was in front", "no_window": "the app had no window", "minimized": "the window was minimised",
    "other_desktop": "the window was on another desktop", "dialog": "a dialog came up", "not_responding": "the app stopped responding",
    "element_missing": "the control was not there yet", "typed_not_landed": "typed text did not land", "no_effect": "a click or key had no effect",
    "slow_start": "the app was slow to start", "window_vanished": "the window closed by itself", "action": "an action did not work",
}


def clean_app(app) -> str:
    """The app as a lesson key: a lower-case process name, or '' when it is not one (a title, a path, anything with other characters)."""
    a = re.sub(r"\.exe$", "", str(app or "").strip().lower())
    return a if APP.match(a) else ""


def _now() -> str:
    return time.strftime("%Y-%m-%d")


class Lessons:
    def __init__(self, path: Path | None = None, clock=_now):
        self.path = Path(path) if path else FILE
        self.clock = clock

    # ---- file
    def _load(self) -> dict:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {"v": 1, "entries": {}, LOG_LAST: ""}
        out: dict = {"v": 1, "entries": {}, LOG_LAST: ""}
        if not isinstance(raw, dict) or not isinstance(raw.get("entries"), dict):
            return out
        for key, e in raw["entries"].items():                  # re-check everything read: a hand-edited file never smuggles text in
            app, _, problem = str(key).partition("|")
            if not clean_app(app) or problem not in PROBLEMS or not isinstance(e, dict) or not isinstance(e.get("routes"), dict):
                continue
            routes = {}
            for r, c in e["routes"].items():
                if r in ROUTES and isinstance(c, list) and len(c) == 2 and all(isinstance(x, int) and not isinstance(x, bool) and 0 <= x <= 10**6 for x in c):
                    routes[r] = [c[0], c[1]]
            if routes:
                out["entries"][f"{clean_app(app)}|{problem}"] = {"routes": routes, "seen": str(e.get("seen") or "")[:10]}
        last = str(raw.get(LOG_LAST) or "")
        out[LOG_LAST] = last if last in out["entries"] else ""
        return out

    def _save(self, data: dict) -> None:
        entries = data["entries"]
        while len(entries) > MAX_ENTRIES:
            oldest = min(entries, key=lambda k: entries[k].get("seen", ""))
            entries.pop(oldest)
        while True:
            text = json.dumps(data, separators=(",", ":"), sort_keys=True)
            if len(text.encode()) <= MAX_BYTES or not entries:
                break
            entries.pop(min(entries, key=lambda k: entries[k].get("seen", "")))
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(text, encoding="utf-8")
            os.replace(tmp, self.path)
        except OSError:
            pass                                               # learning is a help: never a reason to fail a task

    # ---- learning
    def record(self, app, problem: str, route: str, worked: bool) -> bool:
        """Note that `route` did (or did not) solve `problem` in `app`. Returns False when any part is not in the closed vocabularies."""
        a = clean_app(app)
        if not a or problem not in PROBLEMS or route not in ROUTES:
            return False
        with _LOCK:
            data = self._load()
            key = f"{a}|{problem}"
            e = data["entries"].setdefault(key, {"routes": {}, "seen": ""})
            c = e["routes"].setdefault(route, [0, 0])
            c[0 if worked else 1] = min(c[0 if worked else 1] + 1, 10**6)
            e["seen"] = self.clock()
            if worked:
                data[LOG_LAST] = key
            self._save(data)
        return True

    def best_route(self, app, problem: str, available: list[str] | None = None) -> str:
        """The route that worked most often for this app and problem (it must have worked at least once, and more often than it failed), else ''."""
        a = clean_app(app)
        if not a or problem not in PROBLEMS:
            return ""
        e = self._load()["entries"].get(f"{a}|{problem}")
        if not e:
            return ""
        best, score = "", 0
        for r, (ok, bad) in e["routes"].items():
            if available is not None and r not in available:
                continue
            if ok > bad and ok - bad > score:
                best, score = r, ok - bad
        return best

    def order(self, app, problem: str, routes: list[str]) -> list[str]:
        """`routes` with the lesson's route first (the others keep their order). The lesson can only REORDER what the ladder already offers."""
        first = self.best_route(app, problem, routes)
        return ([first] + [r for r in routes if r != first]) if first else list(routes)

    # ---- the owner's view
    def about(self, app=None) -> list[dict]:
        a = clean_app(app) if app else ""
        rows = []
        for key, e in sorted(self._load()["entries"].items()):
            ap, _, problem = key.partition("|")
            if a and ap != a:
                continue
            for r, (ok, bad) in sorted(e["routes"].items(), key=lambda kv: -kv[1][0]):
                rows.append({"app": ap, "problem": problem, "route": r, "worked": ok, "failed": bad, "seen": e.get("seen", "")})
        return rows

    def describe(self, app=None) -> str:
        """The sentence for 'what have you learned about Excel'. Fixed words and process names only."""
        rows = [r for r in self.about(app) if r["worked"] > r["failed"]]
        who = clean_app(app) if app else ""
        if not rows:
            return f"I have not learned anything about {who} yet." if who else "I have not learned anything yet."
        parts = [f"{r['app']}: when {PLAIN[r['problem']]}, {r['route'].replace('_', ' ')} worked {r['worked']} time{'s' if r['worked'] != 1 else ''}" for r in rows[:6]]
        more = f" And {len(rows) - 6} more." if len(rows) > 6 else ""
        return "Here is what I have learned. " + "; ".join(parts) + "." + more + " Say forget that, or forget everything about an app, and I will clear it."

    def forget(self, app=None, *, last: bool = False, everything: bool = False) -> int:
        """Clear lessons: the last one learned, everything about one app, or all. Returns how many (app, problem) entries went."""
        with _LOCK:
            data = self._load()
            ents = data["entries"]
            if everything:
                gone = list(ents)
            elif last:
                gone = [data[LOG_LAST]] if data.get(LOG_LAST) in ents else []
            else:
                a = clean_app(app)
                gone = [k for k in ents if a and k.partition("|")[0] == a]
            for k in gone:
                ents.pop(k, None)
            if gone:
                if data.get(LOG_LAST) in gone:
                    data[LOG_LAST] = ""
                self._save(data)
        return len(gone)


_DEFAULT: Lessons | None = None


def default() -> Lessons:
    """The owner's lessons (the data dir file). Tests build their own `Lessons(path)`."""
    global _DEFAULT
    if _DEFAULT is None or _DEFAULT.path != FILE:
        _DEFAULT = Lessons(FILE)
    return _DEFAULT
