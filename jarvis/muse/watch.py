"""Durable page-watch logic (plan P7 step 2): conditions, backoff, diffs, atomic persistence and a small scheduler. Pure, no network.

Provenance: behaviour ported from openmuse (github.com/CopilotKit/openmuse, MIT, Copyright (c) 2026 OpenMuse contributors), commit 1ac68f3, apps/server/src/engine/service.ts (monitors) and apps/server/src/engine/page-diff.ts; reimplemented in Python.
"""
from __future__ import annotations

import difflib
import hashlib
import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

MAX_FAILURES = 5
MAX_BACKOFF_MIN = 60
MAX_HISTORY = 50          # entries kept per watch, so the saved file cannot grow without bound
MAX_DIFF_LINES = 200      # a page that rewrites itself completely does not put thousands of lines in the record
MAX_SNAPSHOT = 500_000    # characters of page text kept for the next diff

_NUM = r"\d{1,3}(?:,\d{2,3})+(?:\.\d+)?|\d+(?:\.\d+)?"
_PRE = r"(?P<pre>Rs\.?|₹|INR|\$|USD)\s*(?P<n1>" + _NUM + r")"
_POST = r"(?P<n2>" + _NUM + r")\s*(?P<post>INR|USD|Rs\.?)(?![A-Za-z])"
_PRICE_RE = re.compile(r"(?:(?<![A-Za-z])" + _PRE + r")|(?:(?<![\d,.])" + _POST + r")", re.IGNORECASE)
_CURRENCY = {"rs": "INR", "rs.": "INR", "₹": "INR", "inr": "INR", "$": "USD", "usd": "USD"}


def _norm(text: str) -> str:
    return " ".join(text.split())


def find_prices(text: str) -> list[tuple[str, float]]:
    """All (currency, amount) pairs found in text; currencies are never converted."""
    out: list[tuple[str, float]] = []
    for m in _PRICE_RE.finditer(text):
        marker = m.group("pre") or m.group("post")
        num = m.group("n1") or m.group("n2")
        out.append((_CURRENCY[marker.lower()], float(num.replace(",", ""))))
    return out


def compute_diff(old: str, new: str) -> list[dict[str, str]]:
    """Line diff ignoring whitespace-only changes and blank lines."""
    a = [ln for ln in old.splitlines() if ln.strip()]
    b = [ln for ln in new.splitlines() if ln.strip()]
    sm = difflib.SequenceMatcher(None, [_norm(x) for x in a], [_norm(x) for x in b], autojunk=False)
    diff: list[dict[str, str]] = []
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag in ("replace", "delete"):
            diff += [{"op": "del", "text": ln} for ln in a[i1:i2]]
        if tag in ("replace", "insert"):
            diff += [{"op": "add", "text": ln} for ln in b[j1:j2]]
    return diff


def _cap_diff(diff: list[dict[str, str]]) -> list[dict[str, str]]:
    if len(diff) <= MAX_DIFF_LINES:
        return diff
    return diff[:MAX_DIFF_LINES] + [{"op": "note", "text": f"... {len(diff) - MAX_DIFF_LINES} more changed line(s) not shown"}]


def summarize(diff: list[dict[str, str]]) -> str:
    adds = sum(1 for d in diff if d["op"] == "add")
    return f"{adds} line(s) added, {len(diff) - adds} removed" if diff else "no changes"


def condition_met(condition: dict[str, Any], text: str, diff: list[dict[str, str]]) -> bool:
    kind = condition.get("type")
    if kind == "contains":
        return _norm(str(condition["text"])).lower() in _norm(text).lower()
    if kind == "price_below":
        cur = str(condition["currency"]).upper()
        return any(c == cur and amt < float(condition["amount"]) for c, amt in find_prices(text))
    if kind == "changed":
        return bool(diff)
    return False


@dataclass
class Watch:
    id: str
    url: str
    condition: dict[str, Any]
    interval_s: float
    state: str = "active"
    failures: int = 0
    next_check: float = 0.0
    last_snapshot: str | None = None
    history: list[dict[str, Any]] = field(default_factory=list)
    last_hit_snapshot: str | None = None

    def resume(self, now: float = 0.0) -> None:
        self.state = "active"
        self.failures = 0
        self.next_check = now

    def to_dict(self) -> dict[str, Any]:
        return dict(self.__dict__)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Watch":
        return cls(**json.loads(json.dumps(d)))


def save(path: Path | str, watches: list[Watch]) -> None:
    """Atomic write: temp file in the same directory, then replace."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump([w.to_dict() for w in watches], f)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def load(path: Path | str) -> list[Watch]:
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return []
    return [Watch.from_dict(d) for d in raw]


class Scheduler:
    def __init__(self, watches: list[Watch] | None = None) -> None:
        self.watches: list[Watch] = watches if watches is not None else []

    def add(self, watch: Watch) -> None:
        self.watches.append(watch)

    def due(self, now: float) -> list[Watch]:
        return sorted((w for w in self.watches if w.state == "active" and w.next_check <= now),
                      key=lambda w: w.next_check)

    def run_check(self, watch: Watch, fetch: Callable[[str], str], now: float) -> list[dict[str, Any]]:
        notices: list[dict[str, Any]] = []
        try:
            text = fetch(watch.url)
            if not isinstance(text, str):
                raise TypeError("fetch must return str")
        except Exception as exc:  # a failed fetch is a failed check, never a crash
            watch.failures += 1
            watch.next_check = now + min(MAX_BACKOFF_MIN, 2 ** watch.failures) * 60
            watch.history.append({"at": now, "ok": False, "error": type(exc).__name__})    # the kind only: messages can carry URL tokens
            del watch.history[:-MAX_HISTORY]
            if watch.failures >= MAX_FAILURES:
                watch.state = "paused"
                notices.append({"kind": "paused", "watch": watch.id, "reason": "5 failed checks"})
            return notices
        watch.failures = 0
        watch.next_check = now + watch.interval_s
        diff: list[dict[str, str]] = []
        if watch.last_snapshot is not None:
            diff = compute_diff(watch.last_snapshot, text)
        summary = summarize(diff) if watch.last_snapshot is not None else "first check"
        diff = _cap_diff(diff)
        watch.history.append({"at": now, "ok": True, "summary": summary, "diff": diff})
        del watch.history[:-MAX_HISTORY]
        watch.last_snapshot = text[:MAX_SNAPSHOT]
        digest = hashlib.sha256(text.encode("utf-8", "replace")).hexdigest()      # a hash, not a second copy of the page
        if condition_met(watch.condition, text, diff) and watch.last_hit_snapshot != digest:
            watch.last_hit_snapshot = digest
            notices.append({"kind": "hit", "watch": watch.id, "summary": summary, "diff": diff})
        return notices
