"""D76: the board of one open-ended task: what the milestones have found so far and what the task has spent. A small JSON file beside the mission (local only), written after
every change, so a milestone that runs after a restart or a resume sees what the earlier ones read and the hard budget (time, actions, pages, tokens) is kept ACROSS them.

What is kept: records (values read from pages, each with the address it came from), sources (a title, an address and a short excerpt of a page, for a brief), files (a
quarantine file's name, type, size and local path: the path never leaves the PC), verified facts (sentences the executors proved), outputs (the files the task made), notes of what was
NOT done. Page text is not kept beyond a source's short excerpt. Nothing here goes into a model-facing result except the facts and counts the final answer picks.
"""
from __future__ import annotations

import json
import os
import re
import tempfile
import time
from pathlib import Path

MAX_ITEMS = 400
EXCERPT_MAX = 4000
KEYS = ("actions", "pages", "tokens", "seconds")


class BudgetExceeded(Exception):
    def __init__(self, what: str):
        super().__init__(what)
        self.what = what


class Board:
    def __init__(self, path: "Path | str | None" = None, limits: "dict | None" = None, goal: str = "", clock=time.monotonic):
        self.path = Path(path) if path else None
        self.clock = clock
        self.limits = {"seconds": 1200, "actions": 120, "pages": 24, "tokens": 6000, **(limits or {})}
        self.d: dict = {"v": 1, "goal": goal[:1000], "hosts": [], "url": "", "records": [], "sources": [], "files": [], "facts": [], "outputs": [], "notes": [],
                        "milestones": {}, "spent": {k: 0 for k in KEYS}, "asked": 0, "filled": {}, "submit": {}}
        self._t0: "float | None" = None

    # ---- the file
    @classmethod
    def load(cls, path: "Path | str", limits: "dict | None" = None, goal: str = "", clock=time.monotonic) -> "Board":
        b = cls(path, limits, goal, clock)
        try:
            d = json.loads(Path(path).read_text(encoding="utf-8"))
            if isinstance(d, dict) and d.get("v") == 1:
                b.d.update({k: d[k] for k in b.d if k in d})
                b.d["spent"] = {k: float((d.get("spent") or {}).get(k, 0)) for k in KEYS}
        except (OSError, ValueError):
            pass
        return b

    def save(self) -> None:
        if self.path is None:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=str(self.path.parent), prefix=".board-", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(self.d, f, ensure_ascii=False, default=str)
            os.replace(tmp, self.path)
        except OSError:
            try:
                os.unlink(tmp)
            except OSError:
                pass

    # ---- the budget
    def begin(self) -> None:
        self._t0 = self.clock()

    def end(self) -> None:
        if self._t0 is not None:
            self.d["spent"]["seconds"] += max(0.0, self.clock() - self._t0)
            self._t0 = None
        self.save()

    def seconds(self) -> float:
        return self.d["spent"]["seconds"] + (max(0.0, self.clock() - self._t0) if self._t0 is not None else 0.0)

    def spend(self, key: str, n: float = 1) -> None:
        if key in KEYS and key != "seconds":
            self.d["spent"][key] = self.d["spent"].get(key, 0) + n

    def left(self, key: str) -> float:
        used = self.seconds() if key == "seconds" else self.d["spent"].get(key, 0)
        return float(self.limits.get(key, 0)) - used

    def over(self) -> str:
        """The first budget that is used up ('' when none is): the task then reports what it has instead of going on."""
        for k in ("seconds", "actions", "pages", "tokens"):
            if self.left(k) <= 0:
                return k
        return ""

    def check(self, *keys: str) -> None:
        for k in keys or KEYS:
            if self.left(k) <= 0:
                raise BudgetExceeded(k)

    # ---- what was found
    def add_host(self, host: str) -> None:
        h = str(host or "").lower()
        if h and h not in self.d["hosts"] and len(self.d["hosts"]) < 40:
            self.d["hosts"].append(h)

    def set_url(self, url: str) -> None:
        if str(url).startswith("https://"):
            self.d["url"] = str(url)[:400]

    def fact(self, sentence: str) -> None:
        s = " ".join(str(sentence).split())[:300]
        if s and s not in self.d["facts"] and len(self.d["facts"]) < MAX_ITEMS:
            self.d["facts"].append(s)

    def note(self, sentence: str) -> None:
        s = " ".join(str(sentence).split())[:300]
        if s and s not in self.d["notes"] and len(self.d["notes"]) < 60:
            self.d["notes"].append(s)

    def add_record(self, rec: dict) -> None:
        if len(self.d["records"]) < MAX_ITEMS:
            self.d["records"].append({k: (v if isinstance(v, (list, dict)) else str(v)[:500]) for k, v in rec.items()})

    def add_source(self, title: str, url: str, host: str, text: str) -> bool:
        """One page for a brief: False when the same address is already there."""
        if any(s["url"] == url for s in self.d["sources"]) or len(self.d["sources"]) >= 12:
            return False
        self.d["sources"].append({"title": " ".join(str(title).split())[:160], "url": str(url)[:400], "host": str(host)[:120], "text": re.sub(r"\s+", " ", str(text))[:EXCERPT_MAX]})
        return True

    def add_file(self, info: dict) -> None:
        self.d["files"].append({k: v for k, v in info.items() if k in ("name", "ext", "size", "sha256", "pages", "slides", "path", "host")})

    def add_output(self, info: dict) -> None:
        self.d["outputs"].append({k: v for k, v in info.items() if k in ("type", "file", "path", "rows", "columns", "name", "sources")})

    def mark(self, mid: str, status: str, **kw) -> None:
        self.d["milestones"][mid] = {"status": status, **{k: v for k, v in kw.items() if v not in (None, "", [], {})}}
        self.save()

    @property
    def records(self) -> list:
        return self.d["records"]

    @property
    def sources(self) -> list:
        return self.d["sources"]

    @property
    def files(self) -> list:
        return self.d["files"]

    @property
    def hosts(self) -> list:
        return self.d["hosts"]
