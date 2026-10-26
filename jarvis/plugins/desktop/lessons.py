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

Playbook updates are INCREMENTAL DELTAS (D67; the method of "Agentic Context Engineering", arXiv 2510.04618: a playbook grown by small itemised
updates instead of being rewritten, so earlier knowledge is not lost to "context collapse". Method only, no code was taken). The store is a set
of items, one per (app, problem, route), id `app|problem|route`, each with a helpful and a harmful counter and a last_seen date. After a task a
REFLECT step looks at what was tried (a content-free trace of app, problem, route, worked) and PROPOSES deltas: `add`, `helpful`, `harmful`,
`retire`. A deterministic MERGE applies them one by one to the single item each names; every other item is left byte for byte as it was, and a
delta that is not made of closed words is ignored, so the proposer can be this code, a rule, or one day a small model: it can never write text.
Lessons still only REORDER routes the ladder already offers (`order`); they never add one.
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
OPS = ("add", "helpful", "harmful", "retire")           # the four delta kinds a reflect step may propose
MAX_DELTAS = 50                                         # one merge applies at most this many; the rest are ignored
RETIRE_MARGIN = 5                                       # a route that has failed this many times more than it worked is retired, not kept
COUNT_MAX = 10**6
DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
DELTA_KEYS = {"op", "app", "problem", "route", "id"}

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
                if r in ROUTES and isinstance(c, list) and len(c) in (2, 3) and all(isinstance(x, int) and not isinstance(x, bool) and 0 <= x <= COUNT_MAX for x in c[:2]):
                    if len(c) == 3 and not (isinstance(c[2], str) and DATE.match(c[2])):
                        continue                                       # a third field is a date and nothing else
                    routes[r] = list(c)                                # [helpful, harmful] or [helpful, harmful, last_seen]
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

    # ---- the incremental playbook: reflect proposes deltas, merge applies them (D67)
    @staticmethod
    def item_id(app, problem: str, route: str) -> str:
        a = clean_app(app)
        return f"{a}|{problem}|{route}" if a and problem in PROBLEMS and route in ROUTES else ""

    @staticmethod
    def _split_id(item_id) -> tuple[str, str, str]:
        parts = str(item_id or "").split("|")
        if len(parts) != 3:
            return "", "", ""
        a = clean_app(parts[0])
        return (a, parts[1], parts[2]) if a and parts[1] in PROBLEMS and parts[2] in ROUTES else ("", "", "")

    @staticmethod
    def _counts(data: dict, a: str, problem: str, route: str) -> tuple[int, int]:
        c = data["entries"].get(f"{a}|{problem}", {}).get("routes", {}).get(route)
        return (c[0], c[1]) if c else (-1, -1)                # (-1, -1): the item does not exist

    def _reflect(self, data: dict, trace) -> list[dict]:
        """The deltas that a task's trace asks for. Pure: it reads `data` and a local copy of the counters, and writes nothing."""
        deltas: list[dict] = []
        seen: dict[tuple[str, str, str], tuple[int, int]] = {}
        for att in list(trace or [])[:MAX_DELTAS]:
            if not isinstance(att, dict):
                continue
            a, problem, route = clean_app(att.get("app")), att.get("problem"), att.get("route")
            worked = att.get("worked")
            if not a or problem not in PROBLEMS or route not in ROUTES or not isinstance(worked, bool):
                continue
            key = (a, problem, route)
            ok, bad = seen.get(key) or self._counts(data, a, problem, route)
            base = {"app": a, "problem": problem, "route": route}
            if ok < 0:                                         # a route never seen for this app and problem: it becomes an item first
                deltas.append({"op": "add", **base})
                ok, bad = 0, 0
            if worked:
                deltas.append({"op": "helpful", **base})
                ok += 1
            elif bad + 1 - ok >= RETIRE_MARGIN:                # it has failed far more often than it worked: drop it, do not keep counting
                deltas.append({"op": "retire", **base})
                ok, bad = -1, -1
            else:
                deltas.append({"op": "harmful", **base})
                bad += 1
            seen[key] = (ok, bad)
        return deltas[:MAX_DELTAS]

    def reflect(self, trace) -> list[dict]:
        """After a task: from what was tried (a list of {"app", "problem", "route", "worked"}, closed words only) propose deltas
        ({"op": add|helpful|harmful|retire, "app", "problem", "route"}). Nothing is written; hand the result to `merge`."""
        with _LOCK:
            return self._reflect(self._load(), trace)

    def _valid_delta(self, d) -> tuple[str, str, str, str] | None:
        if not isinstance(d, dict) or not set(d) <= DELTA_KEYS or d.get("op") not in OPS:
            return None
        if "id" in d:
            a, problem, route = self._split_id(d["id"])
            if any(k in d and d[k] not in (a, problem, route) for k in ("app", "problem", "route")):
                return None
        else:
            a, problem, route = clean_app(d.get("app")), d.get("problem"), d.get("route")
        if not a or problem not in PROBLEMS or route not in ROUTES:
            return None
        return d["op"], a, problem, route

    def _merge(self, data: dict, deltas) -> dict:
        """Apply deltas one at a time, in order, each to the single item it names. Anything else in the store is not touched."""
        done = {"applied": 0, "ignored": 0, "retired": 0}
        today = self.clock()
        stamp = today if DATE.match(str(today)) else ""
        for i, d in enumerate(list(deltas or [])):
            ok = self._valid_delta(d) if i < MAX_DELTAS else None
            if ok is None:
                done["ignored"] += 1
                continue
            op, a, problem, route = ok
            key = f"{a}|{problem}"
            if op == "retire":
                e = data["entries"].get(key)
                if e and route in e["routes"]:
                    del e["routes"][route]
                    done["retired"] += 1
                    done["applied"] += 1
                    if not e["routes"]:
                        del data["entries"][key]
                        if data.get(LOG_LAST) == key:
                            data[LOG_LAST] = ""
                else:
                    done["ignored"] += 1
                continue
            e = data["entries"].setdefault(key, {"routes": {}, "seen": ""})
            c = e["routes"].get(route)
            if c is None:
                c = e["routes"][route] = [0, 0]
            if op == "helpful":
                c[0] = min(c[0] + 1, COUNT_MAX)
                data[LOG_LAST] = key
            elif op == "harmful":
                c[1] = min(c[1] + 1, COUNT_MAX)
            if stamp:
                if len(c) == 2:
                    c.append(stamp)
                else:
                    c[2] = stamp
            e["seen"] = str(today)[:10]
            done["applied"] += 1
        return done

    def merge(self, deltas) -> dict:
        """Merge proposed deltas into the store (deterministic; no rewrite of the store). Returns {"applied", "ignored", "retired"}."""
        with _LOCK:
            data = self._load()
            done = self._merge(data, deltas)
            if done["applied"]:
                self._save(data)
        return done

    def learn_from_task(self, trace) -> dict:
        """reflect + merge for a whole task, as one step under one lock."""
        with _LOCK:
            data = self._load()
            done = self._merge(data, self._reflect(data, trace))
            if done["applied"]:
                self._save(data)
        return done

    def items(self, app=None) -> list[dict]:
        """The playbook as items: {id, app, problem, route, outcome, helpful, harmful, last_seen}. `outcome` is one word: helpful, harmful, mixed or new."""
        a = clean_app(app) if app else ""
        rows = []
        for key, e in sorted(self._load()["entries"].items()):
            ap, _, problem = key.partition("|")
            if a and ap != a:
                continue
            for r, c in sorted(e["routes"].items()):
                ok, bad = c[0], c[1]
                outcome = "helpful" if ok > bad else "harmful" if bad > ok else "mixed" if ok else "new"
                rows.append({"id": f"{key}|{r}", "app": ap, "problem": problem, "route": r, "outcome": outcome, "helpful": ok, "harmful": bad,
                             "last_seen": c[2] if len(c) > 2 else e.get("seen", "")})
        return rows

    # ---- learning
    def record(self, app, problem: str, route: str, worked: bool) -> bool:
        """Note that `route` did (or did not) solve `problem` in `app`: one attempt as a one-item trace through reflect and merge.
        Returns False when any part is not in the closed vocabularies."""
        a = clean_app(app)
        if not a or problem not in PROBLEMS or route not in ROUTES:
            return False
        self.learn_from_task([{"app": a, "problem": problem, "route": route, "worked": bool(worked)}])
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
        for r, c in e["routes"].items():
            ok, bad = c[0], c[1]
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
            for r, c in sorted(e["routes"].items(), key=lambda kv: -kv[1][0]):
                ok, bad = c[0], c[1]
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
