"""D77 (2026-10-27): JARVIS solves PRACTICE material; it does NOT complete a live graded or proctored exam for the owner. LOW FRICTION by design.

    looks_like_graded_exam(page_title, url_host, visible_text_snippets, folder_or_file_names) -> (bool, reasons)
    assess(...)  -> Verdict(level, reasons)       level: "none" | "ask" | "decline"

A pure, deterministic function (no model, no network, no file but its own rules). The rules are DATA in jarvis/config/exam_signals.json, console-only
(policy.SAFETY_COMPONENTS, push_gate.PROTECTED) so an agent cannot edit them. A missing or broken file falls back to the small built-in FLOOR below, key by
key, never to "nothing is an exam". The problem-set mission (plugins/desktop/problemset.py) calls it; the browser work can reuse it on the page it is looking at.

The owner's own command to solve the problems in a folder or file he names IS his statement that they are practice (the mission records "owner declared
practice"); an ordinary file or an ordinary practice page (a LeetCode problem page, a lab sheet, "exam-prep" notes) is never asked about. Only STRONG live-test
signals change that, in two tiers:

  decline   a proctoring, webcam or screen-share demand, lock-down browser text, a contest page, a proctoring vendor's host. Nothing the owner says overrides it:
            JARVIS does not complete the live test and offers to teach the concept with DIFFERENT examples instead.
  ask       an assessment host (HackerRank /test, Codility, CodeSignal, HackerEarth assessments), "honor code" / integrity-pledge text, an "online assessment" /
            "timed assessment" banner, or enough weak signals together (a file or folder named exam / midterm / final / assessment / quiz-attempt, an "exam" banner,
            a countdown, "submit test" / "final submission" / "finish attempt": each weak signal has a weight, the total must reach `ask_threshold`).
            ONE short question with the plain reason; only "it is practice" from the owner continues; no answer means nothing is done.

Words that say the material is practice (practice, mock, sample, previous year, revision, lab, homework, tutorial ...) take weight off the WEAK signals only.
The reasons are fixed sentences from the rules, never the page's own words (a page cannot put text into the owner's form). Nothing here approves anything, and
no model can switch it off: the only way on is the owner's own answer (a click on the form, recorded by the mission), never a flag in a plan.
"""
from __future__ import annotations

import json
import re
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

CONFIG_FILE = Path(__file__).resolve().parents[2] / "config" / "exam_signals.json"
MAX_SNIPPETS = 80
MAX_SNIPPET_CHARS = 6000
MAX_NAMES = 400
MAX_REASONS = 6
NONE, ASK, DECLINE = "none", "ask", "decline"

# The built-in floor: what is used for any key the file lacks (or has with the wrong type). Small on purpose.
FLOOR: dict[str, Any] = {
    "ask_threshold": 3,
    "hosts": [
        {"match": "hackerrank.com/test", "level": "ask", "why": "a HackerRank assessment page"},
        {"match": "app.codility.com", "level": "ask", "why": "a Codility test page"},
        {"match": "codesignal.com/assessment", "level": "ask", "why": "a CodeSignal assessment page"},
        {"match": "leetcode.com/contest", "level": "decline", "why": "a contest page"},
        {"match": "codeforces.com/contest", "level": "decline", "why": "a contest page"},
    ],
    "paths": [],
    "text": [
        {"rx": r"\bproctor(?:ed|ing|s)?\b", "level": "decline", "why": "the page speaks of proctoring"},
        {"rx": r"\bhonou?r code\b", "level": "ask", "why": "honor-code wording"},
        {"rx": r"\bsubmit (?:the )?(?:test|exam|assessment)\b|\bfinal submission\b|\bfinish attempt\b", "level": "weak", "weight": 2, "why": "a final submit button for a test"},
        {"rx": r"\b(?:time (?:left|remaining)|remaining time)\b[^\n]{0,12}\d|\b\d{1,2}:\d{2}(?::\d{2})?\s*(?:left|remaining)\b", "level": "weak", "weight": 1, "why": "a countdown"},
    ],
    "names": [
        {"rx": r"(?<![a-z])(?:exam|midterm|mid-term|assessment|quiz[ _-]?attempt)(?![a-z])", "weight": 1, "why": "a file or folder named like an exam or assessment"},
    ],
    "practice": {"rx": r"\b(?:practice|practise|mock|sample|samples|previous[ _-]?years?|past[ _-]?papers?|revision|revise|tutorial|exercises?|homework|lab|labs|"
                       r"worksheet|assignment|study|solved|examples?|model[ _-]?paper)\b", "discount": 1},
}

_LOCK = threading.Lock()
_CACHE: dict[str, Any] = {"mtime": None, "path": None, "rules": None}


def _int(v: Any, default: int, lo: int, hi: int) -> int:
    try:
        return max(lo, min(hi, int(v)))
    except (TypeError, ValueError):
        return default


def _plain(s: Any) -> str:
    return " ".join(str(s or "a live-test signal").split())[:120]


def _good(d: Any, key: str, rx: bool = False) -> bool:
    if not isinstance(d, dict) or not isinstance(d.get(key), str) or not d[key].strip():
        return False
    if rx:
        try:
            re.compile(d[key])
        except re.error:
            return False
    return True


def _level(v: Any, default: str) -> str:
    return v if v in ("ask", "decline", "weak") else default


def _norm_host_rule(match: str) -> tuple[str, str]:
    host, _, path = match.strip().lower().partition("/")
    return host.removeprefix("www."), ("/" + path.strip("/")) if path.strip("/") else ""


class Rules:
    def __init__(self, raw: dict):
        self.threshold = _int(raw.get("ask_threshold"), FLOOR["ask_threshold"], 2, 10)
        self.hosts = [(_norm_host_rule(h["match"]), _level(h.get("level"), "ask"), _plain(h.get("why"))) for h in raw["hosts"] if _good(h, "match")]
        self.paths = [(re.compile(p["rx"], re.I), _level(p.get("level"), "weak"), _int(p.get("weight"), 2, 1, 10), _plain(p.get("why")))
                      for p in raw.get("paths", []) if _good(p, "rx", rx=True)]
        self.text = [(re.compile(p["rx"], re.I), _level(p.get("level"), "weak"), _int(p.get("weight"), 1, 1, 10), _plain(p.get("why")), p.get("discountable", True) is not False)
                     for p in raw["text"] if _good(p, "rx", rx=True)]
        self.names = [(re.compile(p["rx"], re.I), _int(p.get("weight"), 1, 1, 10), _plain(p.get("why"))) for p in raw["names"] if _good(p, "rx", rx=True)]
        pr = raw["practice"] if isinstance(raw.get("practice"), dict) and _good(raw["practice"], "rx", rx=True) else FLOOR["practice"]
        self.practice = re.compile(pr["rx"], re.I)
        self.discount = _int(pr.get("discount"), 1, 0, 6)


def _merge(raw: Any) -> dict:
    """The file's keys where they have the right type, the floor's otherwise (key by key)."""
    out = dict(FLOOR)
    if isinstance(raw, dict):
        for k in ("hosts", "paths", "text", "names"):
            if isinstance(raw.get(k), list) and (raw[k] or k == "paths"):
                out[k] = raw[k]
        if isinstance(raw.get("practice"), dict):
            out["practice"] = raw["practice"]
        if raw.get("ask_threshold") is not None:
            out["ask_threshold"] = raw["ask_threshold"]
    return out


def load(path: Path | str | None = None) -> Rules:
    """config/exam_signals.json compiled (cached by modification time); a missing or broken file gives the built-in floor, never 'no rules'."""
    p = Path(path) if path else CONFIG_FILE
    try:
        mtime = p.stat().st_mtime
    except OSError:
        mtime = None
    with _LOCK:
        if _CACHE["rules"] is not None and _CACHE["path"] == str(p) and _CACHE["mtime"] == mtime:
            return _CACHE["rules"]
        raw: Any = None
        if mtime is not None:
            try:
                raw = json.loads(p.read_text(encoding="utf-8-sig"))
            except (OSError, ValueError):
                raw = None
        try:
            rules = Rules(_merge(raw))
        except Exception:  # noqa: BLE001 - a rule file that breaks the compile gives the floor
            rules = Rules(_merge(None))
        _CACHE.update(mtime=mtime, path=str(p), rules=rules)
        return rules


def _host_and_path(value: Any) -> tuple[str, str]:
    """(host, "/path") from a host, a host/path or a full URL; lower case, no scheme, port, query, fragment or leading www."""
    s = str(value or "").strip().lower()
    s = re.sub(r"^[a-z][a-z0-9+.-]*://", "", s)
    s = re.split(r"[?#]", s, maxsplit=1)[0]
    host, _, path = s.partition("/")
    host = host.rsplit("@", 1)[-1].split(":")[0].removeprefix("www.")
    return host, ("/" + path.strip("/")) if path.strip("/") else ""


# a host that is followed by a known suffix; file names such as "problems.txt" or "lab3.pdf" are not hosts
_URL_IN_TEXT = re.compile(r"(?i)\b(?:https?://)?((?:[a-z0-9-]+\.)+(?:com|org|net|io|edu|ac\.in|in|jp|cn|app|co|dev|ai|us|uk)(?:/[^\s\"'<>)]{0,120})?)")


def _urls_in(text: str) -> list[tuple[str, str]]:
    return [_host_and_path(m.group(1)) for m in _URL_IN_TEXT.finditer(text[:MAX_SNIPPET_CHARS])][:20]


@dataclass
class Verdict:
    level: str = NONE                                    # none | ask | decline
    reasons: list[str] = field(default_factory=list)     # fixed sentences from the rules

    @property
    def hit(self) -> bool:
        return self.level != NONE


def _strs(values: Iterable[Any] | Any, cap: int, chars: int) -> list[str]:
    if values is None:
        return []
    if isinstance(values, (str, bytes)):
        values = [values]
    out: list[str] = []
    try:
        for v in values:
            if isinstance(v, bytes):
                v = v.decode("utf-8", "replace")
            out.append(str(v)[:chars])
            if len(out) >= cap:
                break
    except TypeError:
        return []
    return out


def assess(page_title: Any = "", url_host: Any = "", visible_text_snippets: Iterable[Any] | Any = (),
           folder_or_file_names: Iterable[Any] | Any = (), rules: Rules | None = None) -> Verdict:
    """The tiered answer. Every argument is optional and may be empty; text is scanned only (never run, opened or sent). Never raises: a crash is a Verdict of
    "none" and the caller's other rules (the kernel, the owner's form) still stand. `url_host` may be a host, a host/path or a full URL."""
    try:
        r = rules or load()
        title = str(page_title or "")[:400]
        snippets = _strs(visible_text_snippets, MAX_SNIPPETS, MAX_SNIPPET_CHARS)
        names = _strs(folder_or_file_names, MAX_NAMES, 300)
        decline: dict[str, int] = {}
        ask: dict[str, int] = {}
        weak: dict[str, int] = {}
        host, path = _host_and_path(url_host)
        for h, p in [(host, path)] + [hp for s in [title, *snippets] for hp in _urls_in(s)]:
            if not h:
                continue
            for (rhost, rpath), level, why in r.hosts:
                if (h == rhost or h.endswith("." + rhost)) and (not rpath or p == rpath or p.startswith(rpath + "/")):
                    (decline if level == "decline" else ask)[why] = 1
            for rx, level, weight, why in r.paths:
                if rx.search(h + p):
                    {"decline": decline, "ask": ask}.get(level, weak).__setitem__(why, weight)
        body = "\n".join([title, *snippets])
        practice_text = bool(r.practice.search(body))
        for rx, level, weight, why, discountable in r.text:
            if not rx.search(body):
                continue
            if level == "decline":
                decline[why] = 1
            elif level == "ask":
                ask[why] = 1
            else:
                w = max(0, weight - r.discount) if (discountable and practice_text) else weight
                if w:
                    weak[why] = max(weak.get(why, 0), w)
        for n in names:                                     # the last three parts of each path: the drive and the user's folders are not signals
            for part in [x for x in re.split(r"[\\/]+", n) if x][-3:]:
                practice_name = bool(r.practice.search(part))
                for rx, weight, why in r.names:
                    if rx.search(part):
                        w = max(0, weight - r.discount) if practice_name else weight
                        if w:
                            weak[why] = max(weak.get(why, 0), w)
        if decline:
            return Verdict(DECLINE, list(decline)[:MAX_REASONS])
        if ask:
            return Verdict(ASK, list(ask)[:MAX_REASONS])
        if sum(weak.values()) >= r.threshold:
            return Verdict(ASK, [why for why, _ in sorted(weak.items(), key=lambda kv: -kv[1])][:MAX_REASONS])
        return Verdict()
    except Exception:  # noqa: BLE001
        return Verdict()


def looks_like_graded_exam(page_title: Any = "", url_host: Any = "", visible_text_snippets: Iterable[Any] | Any = (),
                           folder_or_file_names: Iterable[Any] | Any = (), rules: Rules | None = None) -> tuple[bool, list[str]]:
    """(True, reasons) when the material shows STRONG signs of a live graded or proctored exam, a timed assessment, an interview test or a contest (either tier of
    `assess`); (False, []) for ordinary practice material. `reasons` are fixed sentences from the rules."""
    v = assess(page_title, url_host, visible_text_snippets, folder_or_file_names, rules)
    return v.hit, v.reasons
