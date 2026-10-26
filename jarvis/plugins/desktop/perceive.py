"""Perceiving the screen like a person does (D65): which windows are open and which one has the focus, what the active window's accessibility tree says, and only
when that is not enough what OCR and the small vision model read, with the AGREEMENT RULE of plugins/_shared/screen_cascade.py between the rungs.

    windows + focus  ->  UI Automation rows of the target window (text, buttons, dialogs)  ->  [OCR  ->  vision]  ->  Observation

`Port` is what the plugin lends (every call is one kernel job in the real thing, a scripted fake in the tests); nothing here knows Windows. An Observation is LOCAL: its
text stays on this PC (the loop shows it to the local model only through drive.propose_local's own egress check, and the state file / lessons / ask forms never get it).
Standard library only (plus the shared cascade).
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Protocol

from jarvis.plugins._shared import screen_cascade as cascade

from . import humanfeel, ladder

OWN = {"jarvis", "electron", "jarvisface", "msedgewebview2"}            # JARVIS's own windows are never "the window the task works in"
MIN_USEFUL = 40                                                         # characters of UIA text that make OCR unnecessary for an ordinary look
PROCESS_OF = {"calc": "calculator", "calculator": "calculator", "paint": "mspaint", "visual studio code": "code", "vs code": "code", "vscode": "code",
              "file explorer": "explorer", "word": "winword", "powerpoint": "powerpnt", "edge": "msedge", "terminal": "windowsterminal"}


class Port(Protocol):
    """What the plugin lends to the loop. Every method may raise; a raise reads as 'could not see / could not do', never as success."""

    def windows(self) -> dict: ...            # {"windows": [{process, title, foreground, minimized, hung?, cloaked?}], "idle_ms"?, "cursor"?}
    def uia(self, app: str) -> list[dict]: ...  # window_text rows of the app: {title, dialog, texts, buttons}
    def ocr(self, app: str) -> str: ...
    def vision(self, app: str) -> str: ...
    def locate(self, app: str, label: str) -> dict: ...   # {"ocr": (x, y) | None, "vision": (x, y) | None}
    def act(self, body: dict) -> dict: ...    # ONE kernel job: {"ok", "state", "error"?, "reason"?, "approval_id"?, ...node facts}
    def app_info(self, app: str) -> dict: ...  # {"responding": bool | None}


@dataclass
class Observation:
    windows: list[dict] = field(default_factory=list)
    front: dict | None = None
    target: dict | None = None
    app: str = ""                             # the process the task works in ('' when there is none yet)
    dialogs: list[dict] = field(default_factory=list)
    buttons: list[str] = field(default_factory=list)       # the target window's controls by name (from UIA), not the dialogs'
    text: str = ""
    source: str = "none"                      # uia | ocr | vision | none
    confidence: float = 0.0
    conflict: bool = False
    hung: bool = False
    owner: humanfeel.OwnerReading = field(default_factory=humanfeel.OwnerReading)
    error: str = ""

    def signature(self) -> str:
        """What the screen showed, as a fingerprint: window titles and state, the focus, the dialogs, the controls and the text. Two equal signatures = nothing changed."""
        wins = sorted((str(w.get("process") or "").lower(), str(w.get("title") or ""), bool(w.get("foreground")), bool(w.get("minimized"))) for w in self.windows)
        return ladder.sig(wins, [d.get("title") for d in self.dialogs], sorted(self.buttons), self.text)

    def has_element(self, name: str) -> bool:
        n = ladder._n(name)
        return bool(n) and (any(ladder._n(b) == n for b in self.buttons) or any(ladder._n(b) == n for d in self.dialogs for b in d.get("buttons") or []))

    def front_app(self) -> str:
        return str((self.front or {}).get("process") or "").lower()

    def target_in_front(self) -> bool:
        return bool(self.target and self.front and norm_app(self.front.get("process")) == norm_app(self.target.get("process")))


# ---------------------------------------------------------------------------------------------------------------------------------- matching
def norm_app(app: str) -> str:
    a = re.sub(r"\.exe$", "", str(app or "").strip().lower())
    return PROCESS_OF.get(a, a)


def window_matches(w: dict, app: str) -> bool:
    a = norm_app(app)
    if not a:
        return False
    proc = re.sub(r"\.exe$", "", str(w.get("process") or "").lower())
    title = str(w.get("title") or "").lower()
    if proc == a or proc.replace(" ", "") == a.replace(" ", ""):
        return True
    words = [x for x in re.split(r"\W+", a) if len(x) > 2]
    return bool(words) and all(x in title for x in words) or (proc == "applicationframehost" and a in title)


def pick_target(windows: list[dict], app: str) -> dict | None:
    """The window of the app the task works in: one in this desktop and not minimised first, then minimised, then one on another desktop. '' = the focus."""
    if not app:
        return next((w for w in windows if w.get("foreground") and re.sub(r"[^a-z]", "", str(w.get("process") or "").lower()) not in OWN), None)
    mine = [w for w in windows if window_matches(w, app) and not is_dialog_window(w, windows)]
    for ok in (lambda w: not w.get("minimized") and not w.get("cloaked"), lambda w: not w.get("cloaked"), lambda w: True):
        got = [w for w in mine if ok(w)]
        if got:
            return next((w for w in got if w.get("foreground")), got[0])
    return None


def is_dialog_window(w: dict, windows: list[dict]) -> bool:
    """A window that is a dialog of another window of the same process (the fake and the real list both show a dialog as its own entry in front)."""
    cls = str(w.get("class") or "")
    return cls == "#32770"


# ---------------------------------------------------------------------------------------------------------------------------------- reading
def _uia_text(rows: list[dict]) -> str:
    out = []
    for r in rows:
        if r.get("dialog"):
            continue
        out += [str(r.get("title") or "")] + [str(t) for t in r.get("texts") or []]
    return "\n".join(t for t in out if t)


def cascade_read(port: Port, app: str, uia_text: str = "", depth: str = "cheap", clock: Callable[[], float] = time.monotonic) -> cascade.Reading:
    """Read one window's text with the cascade. depth 'cheap': UIA alone when it gave enough, else OCR, else the vision model. depth 'check': UIA and OCR both,
    the vision model only when they disagree or both are empty (the agreement rule: two rungs that agree are evidence, one alone is a guess with a label)."""
    uia = uia_text.strip()
    if depth == "none":                                                  # a quick look to see whether something CHANGED: no OCR, no model
        return cascade.Reading(uia, cascade.UIA if uia else "", cascade.CONF_UIA if uia else 0.0)

    def rung_uia() -> str:
        return uia

    def rung_ocr() -> str:
        return port.ocr(app)

    def rung_vision() -> str:
        return port.vision(app)
    if depth == "cheap":
        if len(uia) >= MIN_USEFUL:
            return cascade.Reading(uia, cascade.UIA, cascade.CONF_UIA)
        rd = cascade.read({"uia": rung_uia, "ocr": rung_ocr}, cross_check=True, clock=clock)      # the tree is thin (often just the title): OCR gives the second opinion
        if rd.agreed_with or len(rd.text) >= MIN_USEFUL:
            return rd
        rd = cascade.read({"uia": rung_uia, "ocr": rung_ocr, "vision": rung_vision}, cross_check=True, clock=clock)
        if rd.conflict and len(rd.text) < MIN_USEFUL:                    # a thin tree against a real reading of the pixels: the pixels, flagged as one rung alone
            other = next(((n, t) for n, t in rd.texts.items() if n != cascade.UIA and len(t) >= MIN_USEFUL), None)
            if other:
                return cascade.Reading(other[1], other[0], cascade.ALONE[other[0]], "", False, rd.trace, rd.texts)
        return rd
    rd = cascade.read({"uia": rung_uia, "ocr": rung_ocr}, cross_check=True, clock=clock)
    if rd.conflict or not rd.text or (rd.confidence < cascade.CONF_AGREE and len(rd.texts) < 2 and not rd.agreed_with and len(rd.text) < MIN_USEFUL):
        rd = cascade.read({"uia": rung_uia, "ocr": rung_ocr, "vision": rung_vision}, cross_check=True, clock=clock)
    return rd


def owner_reading(port: Port) -> humanfeel.OwnerReading:
    """Only the owner's input state (one window listing): what the pause loop polls while the owner is using the PC."""
    try:
        got = port.windows() or {}
    except Exception:  # noqa: BLE001
        return humanfeel.OwnerReading()
    ws = [w for w in got.get("windows") or [] if isinstance(w, dict)]
    front = next((w for w in ws if w.get("foreground")), None)
    cur = got.get("cursor")
    return humanfeel.OwnerReading(got.get("idle_ms") if isinstance(got.get("idle_ms"), int) else None,
                                  (int(cur[0]), int(cur[1])) if isinstance(cur, (list, tuple)) and len(cur) == 2 else None, str((front or {}).get("process") or "").lower())


def observe(port: Port, app: str = "", depth: str = "cheap", clock: Callable[[], float] = time.monotonic) -> Observation:
    """One look: windows and focus, the owner's input state, the target window's UIA rows (dialogs and controls), its text through the cascade."""
    obs = Observation(app=norm_app(app))
    try:
        got = port.windows() or {}
    except Exception as e:  # noqa: BLE001 - a PC that cannot list its windows is an observation with an error, not a crash
        obs.error = type(e).__name__
        return obs
    ws = [w for w in got.get("windows") or [] if isinstance(w, dict)]
    obs.windows = ws
    obs.front = next((w for w in ws if w.get("foreground")), None)
    cur = got.get("cursor")
    obs.owner = humanfeel.OwnerReading(got.get("idle_ms") if isinstance(got.get("idle_ms"), int) else None,
                                       (int(cur[0]), int(cur[1])) if isinstance(cur, (list, tuple)) and len(cur) == 2 else None, obs.front_app())
    obs.target = pick_target(ws, app)
    if obs.target is None:
        # the other windows can still hold a dialog or an administrator prompt
        return obs
    proc = str(obs.target.get("process") or "")
    obs.app = norm_app(proc)
    obs.hung = bool(obs.target.get("hung")) or bool(ladder.NOT_RESPONDING.search(str(obs.target.get("title") or "")))
    if obs.hung:
        return obs                                   # a hung window cannot be read (UIA would hang too): the ladder handles it
    try:
        rows = [r for r in port.uia(proc) or [] if isinstance(r, dict)]
    except Exception as e:  # noqa: BLE001
        rows, obs.error = [], type(e).__name__
    for r in rows:
        if r.get("dialog"):
            obs.dialogs.append({"process": proc, "title": str(r.get("title") or "")[:120], "texts": [str(t)[:200] for t in (r.get("texts") or [])[:12]],
                                "buttons": [str(b)[:60] for b in (r.get("buttons") or [])[:8]]})
        else:
            obs.buttons += [str(b)[:60] for b in (r.get("buttons") or [])]
    for w in ws:                                      # a dialog the window list shows as its own entry (the app's thread owns it)
        if str(w.get("class") or "") == "#32770" and window_matches(w, proc) and not any(d["title"] == w.get("title") for d in obs.dialogs):
            obs.dialogs.append({"process": proc, "title": str(w.get("title") or "")[:120], "texts": [], "buttons": []})
    rd = cascade_read(port, proc, _uia_text(rows), depth, clock)
    obs.text, obs.source, obs.confidence, obs.conflict = rd.text, rd.source or "none", rd.confidence, rd.conflict
    return obs


# ---------------------------------------------------------------------------------------------------------------------------------- locating
def locate(port: Port, app: str, label: str, clock: Callable[[], float] = time.monotonic) -> cascade.Located:
    """Where is `label` on screen, from PIXELS: the OCR line and the vision model each give a point, and a click is offered only when they agree. (A control that UI
    Automation lists is clicked by NAME, with no pixels at all; that is the first route, not this one.)"""
    box: dict = {}

    def both() -> dict:
        if "r" not in box:
            box["r"] = port.locate(app, label) or {}
        return box["r"]

    def point(kind: str, w: float, h: float):
        def f(_label: str):
            p = both().get(kind)
            return cascade.Hit(float(p[0]), float(p[1]), w, h, label) if p else None
        return f
    return cascade.locate(label, {"ocr": point("ocr", 60.0, 20.0), "vision": point("vision", 0.0, 0.0)}, clock=clock)
