"""Long missions for Neko (2026-10-16): a goal with several phases ("research X, make slides, open them and explain")
run as a durable workflow that survives a crash or restart, never repeats a side effect, re-plans when a check fails,
and stops on a loop, a budget or the owner's word. Windows; runs inside the desktop plugin.

Reused: DBOS Transact (MIT) on SQLite for durable execution: each finished step's result is recorded, so a resumed
mission skips it; DBOS.launch() resumes unfinished missions after a restart. What DBOS cannot know is whether a step that
was RUNNING at the crash already acted; that is the side-effect journal here (effects, keyed by mission + tool + args +
occurrence): a step found "started but not finished" is repeated only if repeating it is harmless (open, read, check);
otherwise the mission stops and asks the owner instead of typing, pressing or repairing twice.

Each step runs through the same agents and the same verify() as single PC tasks (coordinator.py); every PC action is
still a JARVIS kernel job that the kernel alone permits.
"""
from __future__ import annotations

import hashlib
import json
import secrets
import sqlite3
import threading
import time
from pathlib import Path
from typing import Callable

from dbos import DBOS, SetWorkflowID

from . import coordinator as coord

MISSIONS_DIR = coord.DATA / "neko" / "missions"
MAX_STEPS = 20
MAX_REPLANS = 2
BUDGET_S = 30 * 60
LOOP_REPEATS = 3
SAFE_TO_REPEAT = {"open_website", "open_app", "window", "check_pc", "open_file", "browser"}   # repeating changes nothing


class _Engine:
    agents: dict[str, Callable[[dict], dict]] = {}
    plan_fn: Callable[[str, str], list[dict]] | None = None          # (goal, context) -> steps; context empty = first plan
    on_cancel: Callable[[], None] = staticmethod(lambda: None)        # stop the PC job that is running right now
    db_path: Path = MISSIONS_DIR / "missions.sqlite"
    clock: Callable[[], float] = staticmethod(time.time)
    budget_s: float = BUDGET_S
    lock_path: Path = coord.LOCK
    _db_lock = threading.Lock()


E = _Engine()


# ---------------------------------------------------------------- the journal (our own; DBOS keeps its own tables)
def _db() -> sqlite3.Connection:
    E.db_path.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(str(E.db_path), timeout=10)
    c.execute("create table if not exists missions (id text primary key, goal text, status text, created real, "
              "updated real, cancel integer default 0, record text)")
    c.execute("create table if not exists effects (key text primary key, mission text, tool text, state text, "
              "result text, at real)")
    return c


def _q(sql: str, args=()):
    with E._db_lock, _db() as c:
        return c.execute(sql, args).fetchall()


def _save(mid: str, **kw):
    rec = load(mid) or {}
    rec.update(kw)
    _q("update missions set status=?, updated=?, record=? where id=?", (rec.get("status", ""), E.clock(), json.dumps(rec, default=str), mid))


def load(mid: str) -> dict | None:
    rows = _q("select record from missions where id=?", (mid,))
    return json.loads(rows[0][0]) if rows and rows[0][0] else None


def latest() -> dict | None:
    rows = _q("select record from missions order by created desc limit 1")
    return json.loads(rows[0][0]) if rows and rows[0][0] else None


def _sig(step: dict) -> str:
    args = {k: v for k, v in sorted(step.items()) if k not in ("post",)}
    return hashlib.sha256(json.dumps(args, sort_keys=True, default=str).encode()).hexdigest()[:20]


# ---------------------------------------------------------------- steps (DBOS records each result)
@DBOS.step()
def _now() -> float:
    return E.clock()


@DBOS.step()
def _plan(goal: str, context: str) -> list[dict]:
    return list(E.plan_fn(goal, context) if E.plan_fn else [])[:MAX_STEPS]


@DBOS.step()
def _act(mid: str, step: dict, occurrence: int) -> dict:
    key = f"{mid}:{_sig(step)}:{occurrence}"
    rows = _q("select state, result from effects where key=?", (key,))
    if rows and rows[0][0] == "done" and json.loads(rows[0][1]).get("ok"):
        return dict(json.loads(rows[0][1]), replayed=True)            # already happened: report it, do not act again
    # a recorded FAILURE did not happen: a re-plan may try it again (the loop check still stops endless retries)
    if rows and rows[0][0] == "started" and step["tool"] not in SAFE_TO_REPEAT:
        return {"ok": False, "interrupted": True,
                "error": f"the {step['tool']} step was interrupted mid-way and may already have acted; "
                         "not repeated so nothing happens twice. Check it and tell me to continue."}
    _q("insert or replace into effects (key, mission, tool, state, result, at) values (?,?,?,?,?,?)",
       (key, mid, step["tool"], "started", "{}", E.clock()))
    fn = E.agents.get(step["tool"])
    try:
        res = fn(step) if fn else {"ok": False, "error": f"no agent can do {step['tool']}"}
    except Exception as e:  # noqa: BLE001 - a failed agent is a failed step
        res = {"ok": False, "error": f"{type(e).__name__}: {str(e)[:160]}"}
    res = coord._slim(res or {})
    _q("update effects set state=?, result=?, at=? where key=?", ("done", json.dumps(res, default=str), E.clock(), key))
    return res


def _cancelled(mid: str) -> bool:
    rows = _q("select cancel from missions where id=?", (mid,))
    return bool(rows and rows[0][0])


def _loop(history: list[str]) -> str:
    """Same action with the same outcome LOOP_REPEATS times, or A->B->A->B ping-pong."""
    if len(history) >= LOOP_REPEATS and len(set(history[-LOOP_REPEATS:])) == 1:
        return f"the same step gave the same result {LOOP_REPEATS} times"
    if len(history) >= 4 and history[-1] == history[-3] and history[-2] == history[-4] and history[-1] != history[-2]:
        return "two steps keep undoing each other"
    return ""


# ---------------------------------------------------------------- the mission workflow
@DBOS.workflow()
def run_mission(mid: str, goal: str) -> dict:
    started = _now()
    steps = _plan(goal, "")
    rec = {"id": mid, "goal": goal, "status": "running", "started": started, "plans": [steps], "steps": [], "replans": 0}
    _save(mid, **rec)
    if not steps:
        rec.update(status="not_understood", error="no plan for this goal")
        _save(mid, **rec)
        return rec
    try:
        coord.acquire(mid, E.lock_path, E.budget_s)
    except coord.Busy as e:
        rec.update(status="busy", error=str(e))
        _save(mid, **rec)
        return rec
    history, seen, i = [], {}, 0
    try:
        while i < len(steps):
            step = steps[i]
            if _cancelled(mid):
                rec["status"] = "cancelled"
                break
            if len(rec["steps"]) >= MAX_STEPS:
                rec.update(status="stopped", error=f"step budget of {MAX_STEPS} used up")
                break
            if E.clock() - started > E.budget_s:
                rec.update(status="stopped", error=f"time budget of {E.budget_s / 60:.0f} min used up")
                break
            sig = _sig(step)
            occ = seen.get(sig, 0)
            res = _act(mid, step, occ)
            seen[sig] = occ + 1
            passed, checked, unc = coord.verify(step, res)
            rec["steps"].append({"n": len(rec["steps"]) + 1, "tool": step["tool"], "args": {k: v for k, v in step.items() if k != "tool"},
                                 "result": res, "passed": passed, "checked": checked, "uncertainty": unc})
            history.append(sig + ":" + hashlib.sha256(json.dumps(res, sort_keys=True, default=str).encode()).hexdigest()[:8])
            _save(mid, **rec)
            why = _loop(history)
            if why:
                rec.update(status="stopped", error=f"stopped a loop: {why}")
                break
            if res.get("state") == "waiting_for_permission":
                rec.update(status="waiting_for_permission", approval_id=res.get("approval_id"))
                break
            if res.get("state") == "denied" or res.get("interrupted"):
                rec.update(status="denied" if res.get("state") == "denied" else "needs_owner", error=str(res.get("error") or "denied")[:300])
                break
            if passed:
                i += 1
                continue
            if rec["replans"] >= MAX_REPLANS:
                rec.update(status="failed", error=f"step {step['tool']} failed after {MAX_REPLANS} re-plans: {unc or checked}"[:300])
                break
            rec["replans"] += 1
            done = "; ".join(f"{s['tool']} ok" for s in rec["steps"] if s["passed"]) or "nothing yet"
            ctx = f"Done so far: {done}. The step {step['tool']} {json.dumps(step.get('app') or step.get('url') or step.get('query') or '')} failed: {(unc or checked)[:160]}. Plan only the remaining steps, differently."
            steps = steps[:i] + _plan(goal, ctx)
            rec["plans"].append(steps[i:])
            _save(mid, **rec)
            if i >= len(steps):
                rec.update(status="failed", error="the re-plan had no further steps")
                break
        else:
            rec["status"] = "completed"
    finally:
        coord.release(mid, E.lock_path)
    rec["finished"] = E.clock()
    rec["explain"] = explain(rec)
    _save(mid, **rec)
    return rec


def explain(rec: dict) -> str:
    """The owner's answer: what was done and checked, and what the research found (from its summary, not invented)."""
    done = [s for s in rec.get("steps", []) if s.get("passed")]
    parts = [f"{len(done)} of {len(rec.get('steps', []))} steps done and checked"]
    for s in done:
        r = s.get("result") or {}
        if s["tool"] == "research" and r.get("summary"):
            parts.append("Findings: " + str(r["summary"])[:600])
        if r.get("path") or r.get("file"):
            parts.append(f"File: {r.get('path') or r.get('file')}")
    if rec.get("status") != "completed":
        parts.append(f"Not finished: {rec.get('error') or rec.get('status')}")
    return ". ".join(parts)


# ---------------------------------------------------------------- control
def start(goal: str) -> str:
    mid = time.strftime("m%Y%m%d-%H%M%S-") + secrets.token_hex(3)
    _q("insert into missions (id, goal, status, created, updated, record) values (?,?,?,?,?,?)",
       (mid, goal, "queued", E.clock(), E.clock(), json.dumps({"id": mid, "goal": goal, "status": "queued"})))
    with SetWorkflowID(mid):
        DBOS.start_workflow(run_mission, mid, goal)
    return mid


def cancel(mid: str) -> bool:
    """Stops at the next step boundary and stops the PC job running now; no further step starts."""
    if not _q("select id from missions where id=?", (mid,)):
        return False
    _q("update missions set cancel=1 where id=?", (mid,))
    try:
        E.on_cancel()
    except Exception:  # noqa: BLE001
        pass
    return True


def configure(db_dir: Path, app_version: str = "neko-missions-1"):
    """DBOS on a local SQLite file; nothing leaves the PC. Call once per process before launch()."""
    db_dir.mkdir(parents=True, exist_ok=True)
    E.db_path = db_dir / "missions.sqlite"
    DBOS(config={"name": "neko-missions", "system_database_url": "sqlite:///" + str(db_dir / "dbos.sqlite").replace("\\", "/"),
                 "application_version": app_version, "run_admin_server": False})
