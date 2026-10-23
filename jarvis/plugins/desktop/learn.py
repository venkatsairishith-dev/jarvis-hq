"""Learning layer 3 (2026-10-22): turn PC tasks that WORKED, again and again, into proposed examples for the decider. Windows laptop, local only.

The decider (a small local model) turns a spoken request into a plan of tool steps. When it gets a request right the same way three times and every step was
OBSERVED to work on the real screen, that request and plan are worth teaching it: it becomes one of the "examples" in its prompt. This module finds those, and
STAGES them through the existing skill gate (skills.propose). It cannot do more than that:
  * a proposal is only ever {"examples": [...]}: skills.check refuses any tool the decider does not have and any field it does not know, so no permission, approval,
    safety rule or prompt rule can be expressed;
  * nothing is active until the OWNER promotes it from the command line, and only after the regression gate passed for that exact file hash (skills.gate / promote);
  * every version is immutable and can be rolled back (skills.rollback).

What counts as evidence (all of it, for the same plan): the task record says completed; the plan came from the model (a rule-made plan needs no teaching); every step
passed AND its check has no uncertainty (a step that passed on the node's "ok" alone, "reported ok, not observed", does not count); at least `min_runs` distinct task
records; the plan uses only tools that are safe to teach (no typing of text, no browser clicks, no repairs, no research, no stop/clarify/answer). What is left out of
the example itself: anything that looks like a secret (an address, a long number, the words password/token/key/secret/pin/otp/card).
Requests stay on this laptop: the only reader is the local decider, and the staged version file the owner can open.

Limits: at most 3 new examples per proposal, one proposal per day, never an example whose request is already taught or was already proposed.
CLI (owner):  python -m jarvis.plugins.desktop.skills candidates | propose-mined
"""
from __future__ import annotations

import hashlib
import json
import re
import time
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from . import skills

TEACHABLE = frozenset({"open_website", "play_media", "open_app", "window", "press_key", "open_file", "check_pc"})
MIN_RUNS = 3
PER_PROPOSAL = 3
SECRETISH = re.compile(r"@|\d{6,}|\b(password|passcode|token|secret|pin|otp|card|cvv|ssn|aadhaar|pan)\b|\bkey\b", re.I)
STATE_FILE = "mined.json"
CONTROL = re.compile("[\\x00-\\x1f\\x7f\\u2028\\u2029]")


def _norm(request: str) -> str:
    return " ".join(str(request or "").lower().split())


def _signature(steps: list[dict[str, Any]]) -> str:
    return json.dumps(steps, sort_keys=True, ensure_ascii=False)


def _usable(rec: dict[str, Any]) -> list[dict[str, Any]] | None:
    """The plan steps of a record that counts as evidence, or None."""
    plan = rec.get("plan") or {}
    steps = plan.get("steps")
    if rec.get("status") != "completed" or plan.get("status") != "ok" or not isinstance(steps, list) or not steps:
        return None
    if str(plan.get("model") or "") in ("", "rule"):
        return None
    ran = rec.get("steps") or []
    if len(ran) != len(steps) or not all(s.get("passed") is True and not str(s.get("uncertainty") or "").strip() for s in ran):
        return None
    if any(not isinstance(s, dict) or s.get("tool") not in TEACHABLE for s in steps):
        return None
    return steps


def mine(tasks_dir: Path, *, min_runs: int = MIN_RUNS) -> list[dict[str, Any]]:
    """Candidates, best first: [{"request", "plan", "runs", "requests"}]. Reads the task records only; changes nothing."""
    groups: dict[str, list[tuple[str, str]]] = defaultdict(list)       # plan signature -> [(task id, request)]
    plans: dict[str, list[dict[str, Any]]] = {}
    try:
        files = sorted(Path(tasks_dir).glob("*.json"))
    except OSError:
        return []
    for path in files:
        try:
            rec = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(rec, dict):
            continue
        raw = str(rec.get("request") or "")
        steps = _usable(rec)
        request = " ".join(raw.split())
        # a line break in the raw request would add a line to the decider's prompt: such a request is refused, not tidied
        if steps is None or not request or len(request) > 200 or SECRETISH.search(request) or CONTROL.search(raw):
            continue
        sig = _signature(steps)
        groups[sig].append((str(rec.get("id") or path.stem), request))
        plans[sig] = steps
    out = []
    for sig, runs in groups.items():
        if len({tid for tid, _ in runs}) < min_runs:
            continue
        common = Counter(_norm(r) for _, r in runs).most_common(1)[0][0]
        request = next(r for _, r in runs if _norm(r) == common)
        out.append({"request": request, "plan": {"steps": plans[sig]}, "runs": len(runs), "requests": len({_norm(r) for _, r in runs})})
    out.sort(key=lambda c: (-c["runs"], c["request"]))
    return out


def _state(root: Path) -> dict[str, Any]:
    try:
        d = json.loads((root / STATE_FILE).read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def _hash(c: dict[str, Any]) -> str:
    return hashlib.sha256((_norm(c["request"]) + _signature(c["plan"]["steps"])).encode("utf-8")).hexdigest()[:16]


def candidates(tasks_dir: Path, root: Path = skills.ROOT, *, min_runs: int = MIN_RUNS) -> list[dict[str, Any]]:
    """What could be proposed now: mined, minus what the active version already teaches and what was proposed before; each one checked by
    the gate's own validator, so a candidate that skills.propose would refuse never appears."""
    try:
        taught = {_norm(e.get("request")) for e in (skills.active(root).get("examples") or [])}
    except skills.SkillError:
        taught = set()
    done = set(_state(root).get("proposed") or [])
    from . import decide
    out = []
    for c in mine(tasks_dir, min_runs=min_runs):
        if _norm(c["request"]) in taught or _hash(c) in done:
            continue
        try:
            skills.check({"examples": [{"request": c["request"], "plan": c["plan"]}]}, decide.SITES)
        except skills.SkillError:
            continue
        out.append(c)
    return out


def propose_mined(tasks_dir: Path, root: Path = skills.ROOT, *, now: float | None = None, min_runs: int = MIN_RUNS, dry_run: bool = False) -> dict[str, Any]:
    """Stage ONE new version with up to PER_PROPOSAL examples. Never promotes. At most one proposal per day."""
    now = time.time() if now is None else now
    state = _state(root)
    found = candidates(tasks_dir, root, min_runs=min_runs)
    if not found:
        return {"proposed": None, "why": "nothing new has been seen working often enough"}
    if now - float(state.get("last") or 0) < 86400:
        return {"proposed": None, "why": "a proposal was already made in the last 24 hours", "waiting": len(found)}
    chosen = found[:PER_PROPOSAL]
    if dry_run:
        return {"proposed": None, "would_propose": [c["request"] for c in chosen]}
    reason = f"mined from {sum(c['runs'] for c in chosen)} verified runs of {len(chosen)} request(s); each step was observed on screen"
    ver = skills.propose({"examples": [{"request": c["request"], "plan": c["plan"]} for c in chosen]}, reason, root)
    state.update(last=now, proposed=sorted(set(state.get("proposed") or []) | {_hash(c) for c in chosen}))
    (root / STATE_FILE).write_text(json.dumps(state, indent=1), encoding="utf-8")
    return {"proposed": ver, "examples": len(chosen), "next": f"run the regression gate for {ver}, then the owner promotes it: python -m jarvis.plugins.desktop.skills promote {ver}"}
