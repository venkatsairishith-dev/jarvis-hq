"""The reliability gate's data model: capabilities and goals, loaded from goals.json, checked for mistakes, and filled in for one run (D59).

goals.json is DATA. Each capability has at least `min_goals` concrete goals; a goal says what to ask JARVIS, how the world is prepared
(`setup`), what must be true afterwards (`verify`, read from the PC itself, never from JARVIS's own report), what must NOT have happened
(`forbid`, with a severity), and which kind of behaviour is the right one:

    act          an ordinary task: the effect must be there. Stopping to ask is a FAIL.
    act_or_ask   a consequential but ordinary request (delete to the Recycle Bin): the effect, OR a correct stop for the owner's PIN
                 (counted as a graceful pass, reported separately from verified passes).
    ask          ambiguous: the right behaviour is a question (a jarvis_ask form) and NO action.
    pin          a class-C request: it must stop at the PIN card and nothing may have happened.
    probe        a responsiveness measurement with a threshold (see probes.py).

Template fields available in any string except `regex`/`pattern`: {work} the run's private folder, {sep} the path separator, {tok} a short
token unique to this run, {num} a 5-digit number derived from it, {date} {weekday} today, {outputs} JARVIS's output folder,
{plus45} the date 45 days from today ("11 December 2026") and {xmas_days} the days until the next 25 December (D75: the direct-answer goals).
"""
from __future__ import annotations

import copy
import datetime
import hashlib
import json
import os
import string
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
GOALS_FILE = HERE / "goals.json"

KINDS = ("act", "act_or_ask", "ask", "pin", "probe")
LANGS = ("en", "te", "hi", "te-rom", "hi-rom")
CHECKS = frozenset({
    "file_exists", "file_absent", "file_changed", "file_unchanged", "dir_exists", "count_files", "window_appeared", "window_closed",
    "window_text", "recycle_bin_has", "recycle_bin_lacks", "answer_contains", "answer_no_placeholders", "answer_nonempty", "ask_form", "approval_pending",
    "tool_called", "panel_open", "process_running", "output_made", "pptx", "window_absent", "model_requests",
    # D76 (practical_tasks): a table the task made (CSV / XLSX) and a brief (the research rules), both read from the file itself
    "table_rows", "brief_rules",
    "solutions_pass",                       # D77 (problem_sets): the harness RUNS the solution files itself on its own test cases
})
SETUP_OPS = frozenset({"mkdir", "write_file", "make_junk", "open_notepad_text", "open_image_text", "render_image", "open_app", "message_box", "recycle_file", "write_script", "write_pdf",
                       # D65 (problem_recovery): the problem is made on purpose
                       "minimize", "start_later", "message_box_later", "slow_form", "close_later", "type_unsaved"})
FIELDS = frozenset({"work", "sep", "tok", "num", "date", "weekday", "day", "month", "year", "outputs", "screenshots", "docs", "plus45", "xmas_days", "downloads"})
UNFORMATTED_KEYS = frozenset({"regex", "pattern"})


class GoalFileError(ValueError):
    """goals.json is wrong; the message says where."""


@dataclass
class Goal:
    id: str
    cap: str
    text: str
    kind: str = "act"
    lang: str = "en"
    twin_of: str = ""
    setup: list[dict] = field(default_factory=list)
    verify: list[dict] = field(default_factory=list)
    forbid: list[dict] = field(default_factory=list)
    timeout_s: float = 180.0
    require_answer: bool = True
    act_severity: int = 3
    probe: dict = field(default_factory=dict)
    tags: list[str] = field(default_factory=list)
    note: str = ""
    latency_class: str = ""          # overrides the capability's class for the per-goal latency budget
    cleanup: list[str] = field(default_factory=list)    # paths removed after the run: each must lie in Documents and carry the run's token (a folder made DIRECTLY in Documents)


@dataclass
class Capability:
    id: str
    name: str
    goals: list[Goal]
    estimate: float | None = None        # the honest guess made before measuring (2026-10-26), shown beside the measurement
    pin: str = ""                        # gateway pin for every goal of this capability ("local" = the local MoE is the only brain)
    needs_driver: str = ""               # "face" = only the face chat shows the thing being measured (the final message)
    latency_class: str = "pc_action"
    note: str = ""
    priority: str = "normal"             # D76: "low" = the old toy goals (open Calculator, type in Notepad): still measured, run after the practical ones


@dataclass
class Catalog:
    rules: dict
    defaults: dict
    capabilities: dict[str, Capability]
    probes: dict
    panels: dict
    latency: dict
    levers: dict

    def goals(self, cap: str | None = None) -> list[Goal]:
        caps = [self.capabilities[cap]] if cap else self.capabilities.values()
        return [g for c in caps for g in c.goals]

    def goal(self, goal_id: str) -> Goal:
        for g in self.goals():
            if g.id == goal_id:
                return g
        raise KeyError(goal_id)


def _goal(cap: str, raw: dict, defaults: dict) -> Goal:
    known = {f for f in Goal.__dataclass_fields__}
    extra = set(raw) - known
    if extra:
        raise GoalFileError(f"{cap}/{raw.get('id', '?')}: unknown field(s) {sorted(extra)}")
    d = {"timeout_s": defaults.get("timeout_s", 180.0), **raw}
    return Goal(cap=cap, **d)


def load(path: Path | str | None = None) -> Catalog:
    data = json.loads(Path(path or GOALS_FILE).read_text(encoding="utf-8"))
    defaults = data.get("defaults", {})
    caps: dict[str, Capability] = {}
    for cid, c in data["capabilities"].items():
        goals = [_goal(cid, g, defaults) for g in c.get("goals", [])]
        caps[cid] = Capability(cid, c["name"], goals, c.get("estimate"), c.get("pin", ""), c.get("needs_driver", ""),
                               c.get("latency_class", "pc_action"), c.get("note", ""), c.get("priority", "normal"))
    return Catalog(data.get("rules", {}), defaults, caps, data.get("probes", {}), data.get("panels", {}), data.get("latency", {}),
                   data.get("levers", {}))


# ------------------------------------------------------------------------------------------------------------- validation
def _fields_in(value: Any, key: str = "") -> set[str]:
    out: set[str] = set()
    if isinstance(value, str) and key not in UNFORMATTED_KEYS:
        for _, name, _, _ in string.Formatter().parse(value):
            if name:
                out.add(name.split(".")[0].split("[")[0])
    elif isinstance(value, dict):
        for k, v in value.items():
            out |= _fields_in(v, k)
    elif isinstance(value, list):
        for v in value:
            out |= _fields_in(v, key)
    return out


def validate(cat: Catalog) -> list[str]:
    """Every mistake in the catalogue, as plain sentences (empty list = sound). The tests fail on any."""
    bad: list[str] = []
    seen: dict[str, str] = {}
    min_goals = int(cat.rules.get("min_goals_per_capability", 10))
    for cid, cap in cat.capabilities.items():
        if len(cap.goals) < min_goals:
            bad.append(f"{cid}: {len(cap.goals)} goals, at least {min_goals} are required")
        for g in cap.goals:
            where = f"{cid}/{g.id}"
            if g.id in seen:
                bad.append(f"{where}: goal id also used in {seen[g.id]}")
            seen[g.id] = cid
            if g.kind not in KINDS:
                bad.append(f"{where}: kind {g.kind!r} is not one of {KINDS}")
            if g.lang not in LANGS:
                bad.append(f"{where}: lang {g.lang!r}")
            if not g.text.strip() and g.kind != "probe":
                bad.append(f"{where}: empty text")
            for section in ("setup", "verify", "forbid"):
                for item in getattr(g, section):
                    name = item.get("op") if section == "setup" else item.get("check")
                    allowed = SETUP_OPS if section == "setup" else CHECKS
                    if name not in allowed:
                        bad.append(f"{where}: {section} uses unknown {'op' if section == 'setup' else 'check'} {name!r}")
            for f in _fields_in([g.text, g.setup, g.verify, g.forbid, g.cleanup]) - FIELDS:
                bad.append(f"{where}: unknown template field {{{f}}}")
            for item in g.forbid:
                if int(item.get("severity", 0)) not in (1, 2, 3):
                    bad.append(f"{where}: forbid {item.get('check')} needs severity 1, 2 or 3")
            if g.kind == "act" and not g.verify:
                bad.append(f"{where}: an act goal needs verify checks")
            if g.kind == "act_or_ask" and not (g.verify and g.forbid):
                bad.append(f"{where}: an act_or_ask goal needs verify (the effect) and forbid (what a wrong action would touch)")
            if g.kind == "ask" and not g.forbid:
                bad.append(f"{where}: an ask goal needs forbid checks (what acting on a guess would change)")
            if g.kind == "pin" and not g.forbid:
                bad.append(f"{where}: a pin goal needs forbid checks (what must not happen without the PIN)")
            if g.kind == "probe" and not (g.probe.get("probe") and g.probe.get("limit") is not None):
                bad.append(f"{where}: a probe goal needs probe.probe and probe.limit")
            if g.twin_of and g.twin_of not in {x.id for c in cat.capabilities.values() for x in c.goals} and g.twin_of not in cat.rules.get("external_twins", []):
                bad.append(f"{where}: twin_of {g.twin_of!r} is not a goal")
    for cid, cap in cat.capabilities.items():
        langs = {g.lang for g in cap.goals}
        if cid == "lang_proper_script" and not {"te", "hi"} <= langs:
            bad.append(f"{cid}: needs both Telugu and Hindi goals")
        if cid == "lang_romanized" and not {"te-rom", "hi-rom"} <= langs:
            bad.append(f"{cid}: needs both Tenglish and Hinglish goals")
    return bad


# ------------------------------------------------------------------------------------------------------------- one run's values
@dataclass(frozen=True)
class Context:
    work: str
    sep: str
    tok: str
    num: str
    date: str
    weekday: str
    outputs: str
    day: str = ""
    month: str = ""
    year: str = ""
    screenshots: str = ""
    docs: str = ""
    plus45: str = ""
    xmas_days: str = ""
    downloads: str = ""

    def as_dict(self) -> dict:
        return {"work": self.work, "sep": self.sep, "tok": self.tok, "num": self.num, "date": self.date, "weekday": self.weekday, "outputs": self.outputs,
                "day": self.day, "month": self.month, "year": self.year, "screenshots": self.screenshots, "docs": self.docs, "plus45": self.plus45,
                "xmas_days": self.xmas_days, "downloads": self.downloads}


def make_context(work_root: str, sep: str, outputs: str, seed: str, now: float | None = None) -> Context:
    """A fresh context: `seed` (goal id + repeat + time) makes the token; the work folder is <work_root><sep><tok>."""
    t = time.localtime(now if now is not None else time.time())
    h = hashlib.sha256(seed.encode("utf-8")).hexdigest()
    tok = "r" + h[:6]
    num = str(10000 + int(h[6:12], 16) % 90000)
    shots = os.path.join(os.path.expanduser("~"), "Pictures", "Screenshots")
    today = datetime.date(t.tm_year, t.tm_mon, t.tm_mday)
    later = today + datetime.timedelta(days=45)
    xmas = datetime.date(today.year, 12, 25)
    if xmas < today:
        xmas = datetime.date(today.year + 1, 12, 25)
    return Context(f"{work_root.rstrip(sep)}{sep}{tok}", sep, tok, num, time.strftime("%Y-%m-%d", t), time.strftime("%A", t), outputs,
                   str(t.tm_mday), time.strftime("%B", t), time.strftime("%Y", t), shots, os.path.join(os.path.expanduser("~"), "Documents"),
                   f"{later.day} {later.strftime('%B %Y')}", str((xmas - today).days), os.path.join(os.path.expanduser("~"), "Downloads"))


def _fill(value: Any, ctx: dict, key: str = "") -> Any:
    if isinstance(value, str):
        return value if key in UNFORMATTED_KEYS else value.format_map(ctx)
    if isinstance(value, dict):
        return {k: _fill(v, ctx, k) for k, v in value.items()}
    if isinstance(value, list):
        return [_fill(v, ctx, key) for v in value]
    return value


def render(goal: Goal, ctx: Context) -> Goal:
    """The goal with every template field replaced for this run (a copy; the catalogue stays untouched)."""
    c = ctx.as_dict()
    g = copy.deepcopy(goal)
    g.text = _fill(g.text, c)
    g.setup = _fill(g.setup, c)
    g.verify = _fill(g.verify, c)
    g.forbid = _fill(g.forbid, c)
    g.cleanup = _fill(g.cleanup, c)
    return g


def summary(cat: Catalog) -> list[tuple[str, str, int, float | None]]:
    """(capability id, name, number of goals, the owner-facing estimate) for each capability."""
    return [(c.id, c.name, len(c.goals), c.estimate) for c in cat.capabilities.values()]
