"""Reading and finding things on the screen with a CASCADE of three methods that check each other (D59, lever d).

    rung 1  UI Automation (the accessibility tree)   exact, instant, but empty for games, canvases, remote windows, some Electron panes
    rung 2  OCR                                      works on pixels, can misread small or low-contrast text
    rung 3  a small vision model                     understands layout, slowest, can invent

One rung alone is a guess with a label on it. Two rungs that AGREE are evidence. So the cascade asks for a second opinion whenever the
answer matters, and when they disagree it asks the third as a tie-breaker; if nobody agrees the answer says so (`conflict`) and is
low-confidence, and a click is not offered from pixels alone.

    read(rungs, cross_check=True)  -> Reading(text, source, confidence, agreement, conflict, trace)
    locate(label, rungs)           -> Located(hit, source, confidence, safe_to_click, ...)

The rungs are functions the caller lends (the desktop plugin's UIA read, `window_ocr`, the vision specialist), so nothing here touches
the screen and every path is testable with fakes. Confidence numbers are fixed by RULE (below), not by a model's self-report:

    two rungs agree                      0.95        (UIA + OCR or any pair)
    UIA alone, nothing to compare        0.80
    OCR alone                            0.65
    vision alone                         0.50
    rungs disagree, no majority          0.30        (conflict=True; the highest-priority rung's text is returned, flagged)

Standard library only.
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from typing import Any, Callable

UIA, OCR, VISION = "uia", "ocr", "vision"
PRIORITY = (UIA, OCR, VISION)
AGREE_AT = 0.6                 # share of the shorter text's words found in the other text
MIN_CHARS = 12                 # shorter than this is "nothing useful" for a reading
CONF_AGREE, CONF_UIA, CONF_OCR, CONF_VISION, CONF_CONFLICT = 0.95, 0.80, 0.65, 0.50, 0.30
ALONE = {UIA: CONF_UIA, OCR: CONF_OCR, VISION: CONF_VISION}

_WORD = re.compile(r"[^\W_]{2,}", re.U)


def words(text: str) -> set[str]:
    return {w.lower() for w in _WORD.findall(str(text or ""))}


def agreement(a: str, b: str) -> float:
    """How much of the SHORTER text's words the other one contains (0..1). OCR drops and splits words, so this is containment, not equality."""
    wa, wb = words(a), words(b)
    if not wa or not wb:
        return 0.0
    small, big = (wa, wb) if len(wa) <= len(wb) else (wb, wa)
    return len(small & big) / len(small)


@dataclass
class Trace:
    rung: str
    ok: bool
    chars: int = 0
    seconds: float = 0.0
    error: str = ""


@dataclass
class Reading:
    text: str
    source: str                          # the rung whose text is returned ("" if none gave any)
    confidence: float
    agreed_with: str = ""                # the other rung that agrees ("" if none)
    conflict: bool = False
    trace: list[Trace] = field(default_factory=list)
    texts: dict[str, str] = field(default_factory=dict)

    @property
    def usable(self) -> bool:
        return bool(self.text) and not self.conflict


def _try(name: str, fn: Callable[[], Any], trace: list[Trace], clock: Callable[[], float]) -> str:
    t0 = clock()
    try:
        out = fn()
        text = out if isinstance(out, str) else ("" if out is None else str(out))
        text = text.strip()
        trace.append(Trace(name, len(text) >= MIN_CHARS, len(text), round(clock() - t0, 3)))
        return text if len(text) >= MIN_CHARS else ""
    except Exception as e:  # noqa: BLE001 - a rung that fails is a rung with no answer
        trace.append(Trace(name, False, 0, round(clock() - t0, 3), type(e).__name__))
        return ""


def read(rungs: dict[str, Callable[[], Any]], *, cross_check: bool = True, clock: Callable[[], float] = time.monotonic) -> Reading:
    """Read a window's text. `rungs` maps "uia" / "ocr" / "vision" to a function returning text. `cross_check` False = take the first rung
    that gives anything (cheap reads); True = confirm it with a second rung, and break a tie with the third."""
    trace: list[Trace] = []
    got: dict[str, str] = {}
    order = [r for r in PRIORITY if r in rungs]
    for name in order:
        if name in got:
            continue
        text = _try(name, rungs[name], trace, clock)
        if text:
            got[name] = text
        have = [n for n in got]
        if not cross_check and have:
            break
        if len(have) >= 2:
            a, b = have[0], have[1]
            if agreement(got[a], got[b]) >= AGREE_AT:
                return Reading(got[a], a, CONF_AGREE, b, False, trace, dict(got))
            # two rungs and no agreement: the third one (if there is one) decides
    return _decide(got, trace, order)


def _decide(got: dict[str, str], trace: list[Trace], order: list[str]) -> Reading:
    names = [n for n in PRIORITY if n in got]
    if not names:
        return Reading("", "", 0.0, "", False, trace, {})
    if len(names) == 1:
        n = names[0]
        return Reading(got[n], n, ALONE[n], "", False, trace, dict(got))
    for i, a in enumerate(names):                               # any pair that agrees wins; the higher-priority rung's text is returned
        for b in names[i + 1:]:
            if agreement(got[a], got[b]) >= AGREE_AT:
                return Reading(got[a], a, CONF_AGREE, b, False, trace, dict(got))
    first = names[0]
    return Reading(got[first], first, CONF_CONFLICT, "", True, trace, dict(got))


# ----------------------------------------------------------------------------------------------------------------- locating
@dataclass(frozen=True)
class Hit:
    x: float                   # centre, screen pixels
    y: float
    w: float = 0.0             # size when the rung knows it (UIA and OCR do; a vision point has 0)
    h: float = 0.0
    text: str = ""


@dataclass
class Located:
    hit: Hit | None
    source: str
    confidence: float
    agreed_with: str = ""
    safe_to_click: bool = False
    conflict: bool = False
    trace: list[Trace] = field(default_factory=list)


def close(a: Hit, b: Hit) -> bool:
    """Two hits are the same place when one's centre lies inside the other's box (grown by 40%), or within 0.75 of the larger height."""
    for p, q in ((a, b), (b, a)):
        if q.w and q.h and abs(p.x - q.x) <= q.w * 0.7 and abs(p.y - q.y) <= q.h * 0.7:
            return True
    tol = 0.75 * max(a.h, b.h, 16.0)
    return abs(a.x - b.x) <= tol * 2 and abs(a.y - b.y) <= tol


def locate(label: str, rungs: dict[str, Callable[[str], Any]], *, clock: Callable[[], float] = time.monotonic) -> Located:
    """Find `label` on the screen. Each rung returns a Hit, a list of Hits or None. A UIA hit can be invoked without pixels; any click made
    from pixels (OCR, vision) needs a second rung to agree on the place (`safe_to_click`)."""
    trace: list[Trace] = []
    found: dict[str, Hit] = {}
    for name in [r for r in PRIORITY if r in rungs]:
        t0 = clock()
        try:
            out = rungs[name](label)
            hits = [out] if isinstance(out, Hit) else [h for h in (out or []) if isinstance(h, Hit)]
            trace.append(Trace(name, bool(hits), len(hits), round(clock() - t0, 3)))
        except Exception as e:  # noqa: BLE001
            hits = []
            trace.append(Trace(name, False, 0, round(clock() - t0, 3), type(e).__name__))
        if not hits:
            continue
        # keep the hit that the earlier rungs also saw, else the first
        prior = list(found.values())
        match = next((h for h in hits if any(close(h, p) for p in prior)), hits[0])
        found[name] = match
        if len(found) >= 2 and any(close(match, p) for p in prior):
            break
    names = [n for n in PRIORITY if n in found]
    if not names:
        return Located(None, "", 0.0, trace=trace)
    for i, a in enumerate(names):
        for b in names[i + 1:]:
            if close(found[a], found[b]):
                return Located(found[a], a, CONF_AGREE, b, True, False, trace)
    n = names[0]
    if len(names) == 1:
        # UIA alone is enough to INVOKE the control (no pixels); a lone OCR or vision hit is not enough to click on
        return Located(found[n], n, ALONE[n], "", n == UIA, False, trace)
    return Located(found[n], n, CONF_CONFLICT, "", False, True, trace)
