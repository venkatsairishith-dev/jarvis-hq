"""Long missions for JARVIS (2026-10-16): a goal with several phases ("research X, make slides, open them and explain")
run as a durable workflow that survives a crash or restart, never repeats a side effect, re-plans when a check fails,
and stops on a loop, a budget or the owner's word. Windows; runs inside the desktop plugin.

Reused: DBOS Transact (MIT) on SQLite for durable execution: each finished step's result is recorded, so a resumed
mission skips it; DBOS.launch() resumes unfinished missions after a restart. What DBOS cannot know is whether a step that
was RUNNING at the crash already acted; that is the side-effect journal here (effects, keyed by mission + tool + args +
occurrence): a step found "started but not finished" is repeated only if repeating it is harmless (open, read, check);
otherwise the mission stops and asks the owner instead of typing, pressing or repairing twice.

Each step runs through the same agents and the same verify() as single PC tasks (coordinator.py); every PC action is
still a JARVIS kernel job that the kernel alone permits.

2026-10-18 (audit PC-D5, PC-D6, PC-D9 and the four failed live missions of 2026-10-16):
- A step that could not run at all (the plugin's event loop is closed, a plugin call timed out, the console is down) is
  "unavailable": the mission ends at once with that reason. Re-planning cannot fix it; the live missions re-planned an
  "Event loop is closed" research step twice and a 900 s research timeout once (another 15 minutes for nothing).
- A refusal by the JARVIS kernel (policy, kill switch) ends the mission as "refused". It is never re-planned: a re-plan
  "differently" would only look for another way to a target the kernel refused.
- The decider's clarification question reaches the owner (status needs_clarification) instead of "no plan".
- resume(): a mission that stopped for the owner (an approval, a question, an interrupted step) continues under the SAME
  mission id and effects journal from the step where it stopped; done steps are replayed, never repeated. The approval
  the owner gave is handed to exactly the job it was asked for.
- A research step that made its file but whose window was not seen is not run again: the file is opened (open_output).
- A step that can take long (research, troubleshoot) is not started when the time budget left cannot cover it.
"""
from __future__ import annotations

import hashlib
import json
import re
import secrets
import sqlite3
import threading
import time
from pathlib import Path
from typing import Callable

from dbos import DBOS, SetWorkflowID

from . import coordinator as coord

MISSIONS_DIR = coord.DATA / "plugins" / "missions"
MAX_STEPS = 20
MAX_REPLANS = 2
BUDGET_S = 30 * 60
LOOP_REPEATS = 3
SAFE_TO_REPEAT = coord.REPEAT_SAFE      # repeating changes nothing; browser steps only for read/find/tabs/whoami (coord.repeat_safe)
# the longest a step of these tools may take (the agents' own limits): one is not started when the budget left is shorter
STEP_WORST_S = {"research": 960, "troubleshoot": 900}
# a step that could not run at all: retrying or re-planning cannot help (the live missions of 2026-10-16 show each one)
UNAVAILABLE = re.compile(r"(?i)event loop is closed|timed out after|\bTimeoutError\b|console is not answering|no agent can do|"
                         r"could not run on the plugin")
# a result that says "I do not know whether it ran": no answer in time, the node never confirmed the end of the job
UNKNOWN_RESULT = re.compile(r"(?i)no result within|did not answer while|not confirmed|may still be running|has not (yet )?confirmed")
# the kernel said no: final. "approval pending/expired/denied" are approval states, handled as such, not refusals
REFUSED = re.compile(r"(?i)\brefused by policy\b|\bthe kill switch is on\b")
APPROVAL_STATE = re.compile(r"(?i)approval (pending|expired|denied|required|used|is for a different action)")
RESUMABLE = ("waiting_for_permission", "needs_clarification", "needs_owner")


class _Engine:
    agents: dict[str, Callable[[dict], dict]] = {}
    # (goal, context) -> steps, or a decide.Plan (status ok|clarify|fallback, steps, question, problems); context empty = first plan
    plan_fn: Callable[[str, str], object] | None = None
    on_cancel: Callable[[], None] = staticmethod(lambda: None)        # stop the PC job that is running right now
    db_path: Path = MISSIONS_DIR / "missions.sqlite"
    clock: Callable[[], float] = staticmethod(time.time)
    budget_s: float = BUDGET_S
    lock_path: Path = coord.LOCK
    _db_lock = threading.Lock()


E = _Engine()
_tls = threading.local()               # the mission whose step runs on this thread (an agent asks cancel_requested())


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


def _cas(mid: str, old: str, new: str) -> bool:
    """Compare-and-set of a mission's status column: two 'continue' requests at once start one run, not two."""
    with E._db_lock, _db() as c:
        return c.execute("update missions set status=?, updated=? where id=? and status=? and cancel=0",
                         (new, E.clock(), mid, old)).rowcount == 1


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
    # "_..." keys are run-time hand-overs (an approval for a resumed step), not part of what the step does
    args = {k: v for k, v in sorted(step.items()) if k != "post" and not k.startswith("_")}
    return hashlib.sha256(json.dumps(args, sort_keys=True, default=str).encode()).hexdigest()[:20]


def cancel_requested() -> bool:
    """For an agent waiting on something long (a research call): has the owner stopped the mission running this step?"""
    mid = getattr(_tls, "mid", None)
    return bool(mid) and _cancelled(mid)


def _classify(res: dict) -> dict:
    """Mark a failed step's result: refused (by the kernel: final), unavailable (could not run: not re-planned), or
    neither (an ordinary failed check, which a re-plan may fix)."""
    if res.get("ok") is True or res.get("state") in ("waiting_for_permission", "denied") or res.get("interrupted"):
        return res
    err = str(res.get("error") or "")
    if res.get("refused") or (REFUSED.search(err) and not APPROVAL_STATE.search(err)):
        return dict(res, refused=True)
    if res.get("unavailable") or UNAVAILABLE.search(err):
        return dict(res, unavailable=str(res.get("unavailable") or err)[:240])
    return res


def _as_plan(p) -> dict:
    """A planner answer as a plain record (DBOS stores it): a list of steps (tests, older records) or a decide.Plan."""
    if isinstance(p, dict) and "steps" in p:
        return {"status": str(p.get("status") or "ok"), "steps": list(p.get("steps") or [])[:MAX_STEPS],
                "question": str(p.get("question") or ""), "problems": [str(x) for x in (p.get("problems") or [])][:5]}
    if p is None or isinstance(p, (list, tuple)):
        return {"status": "ok", "steps": list(p or [])[:MAX_STEPS], "question": "", "problems": []}
    return {"status": str(getattr(p, "status", "ok") or "ok"), "steps": list(getattr(p, "steps", None) or [])[:MAX_STEPS],
            "question": str(getattr(p, "question", "") or ""), "problems": [str(x) for x in (getattr(p, "problems", None) or [])][:5]}


# ---------------------------------------------------------------- steps (DBOS records each result)
@DBOS.step()
def _now() -> float:
    return E.clock()


@DBOS.step()
def _plan(goal: str, context: str) -> dict:
    return _as_plan(E.plan_fn(goal, context) if E.plan_fn else [])


@DBOS.step()
def _resume_state(mid: str) -> dict:
    """What a resumed run continues from, read once and recorded (a crash during the resumed run replays this)."""
    return load(mid) or {}


@DBOS.step()
def _act(mid: str, step: dict, occurrence: int) -> dict:
    key = f"{mid}:{_sig(step)}:{occurrence}"
    rows = _q("select state, result from effects where key=?", (key,))
    if rows and rows[0][0] == "done" and json.loads(rows[0][1]).get("ok"):
        return dict(json.loads(rows[0][1]), replayed=True)            # already happened: report it, do not act again
    # a recorded FAILURE did not happen: a re-plan may try it again (the loop check still stops endless retries)
    if rows and rows[0][0] == "started" and not coord.repeat_safe(step):
        return {"ok": False, "interrupted": True, "effect": key,
                "error": f"the {step['tool']} step was interrupted mid-way and may already have acted; "
                         "not repeated so nothing happens twice. Check it and tell me to continue."}
    _q("insert or replace into effects (key, mission, tool, state, result, at) values (?,?,?,?,?,?)",
       (key, mid, step["tool"], "started", "{}", E.clock()))
    fn = E.agents.get(step["tool"])
    _tls.mid = mid
    try:
        res = fn(step) if fn else {"ok": False, "error": f"no agent can do {step['tool']}"}
        if not isinstance(res, dict):
            res = {"ok": False, "error": f"the {step['tool']} agent returned {type(res).__name__}"}
    except Exception as e:  # noqa: BLE001 - a failed agent is a failed step
        res = {"ok": False, "error": f"{type(e).__name__}: {str(e)[:160]}"}
        if getattr(e, "unavailable", False):                           # the agent layer says the step could not run
            res["unavailable"] = str(e)[:240]
    finally:
        _tls.mid = None
    res = _classify(coord._slim(res or {}))
    if (res.get("ok") is not True and not coord.repeat_safe(step) and not res.get("refused") and not res.get("unavailable")
            and res.get("state") not in ("waiting_for_permission", "denied", "cancelled")
            and (res.get("unconfirmed") or UNKNOWN_RESULT.search(str(res.get("error") or "")))):
        # The result is UNKNOWN (no answer in time, the node never confirmed): the step may have acted. A failure is normally
        # "did not happen, a re-plan may retry"; for a step that is not safe to repeat an unknown result is not a failure. The
        # effect stays `started` (a re-plan or a restart cannot repeat it) and the mission asks the owner: done or redo.
        return {"ok": False, "interrupted": True, "effect": key,
                "error": f"the {step['tool']} step got no confirmed result ({str(res.get('error') or 'unknown')[:120]}) and may already "
                         "have acted; not repeated so nothing happens twice. Check it and tell me to continue."}
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


def _made(rec: dict) -> list[str]:
    """Files the mission already made (a passed research, or one kept after only its window check failed)."""
    return [str(s["result"]["file"]) for s in rec.get("steps", []) if (s.get("passed") or s.get("kept"))
            and isinstance(s.get("result"), dict) and s["result"].get("file")]


def _replan_context(rec: dict, step: dict, why: str) -> str:
    done = "; ".join(f"{s['tool']} ok" for s in rec["steps"] if s.get("passed")) or "nothing yet"
    made = _made(rec)
    return (f"Done so far: {done}." + (f" Already made (do not make again): {', '.join(made)}." if made else "")
            + f" The step {step['tool']} {json.dumps(step.get('app') or step.get('url') or step.get('query') or '')} failed: "
              f"{why[:160]}. Plan only the remaining steps, differently.")


# ---------------------------------------------------------------- the mission workflow
@DBOS.workflow()
def run_mission(mid: str, goal: str, resume: bool = False) -> dict:
    started = _now()
    if resume:
        return _resumed(mid, goal, started)
    try:
        plan = _as_plan(_plan(goal, ""))
    except Exception as e:  # noqa: BLE001 - a planner crash ends the mission honestly; it never stays "queued"
        rec = {"id": mid, "goal": goal, "status": "failed", "error": f"the planner failed ({type(e).__name__})", "started": started,
               "plans": [], "steps": [], "replans": 0, "finished": E.clock()}
        rec["explain"] = explain(rec)
        _save(mid, **rec)
        return rec
    rec = {"id": mid, "goal": goal, "status": "running", "started": started, "plans": [plan["steps"]], "steps": [], "replans": 0}
    _save(mid, **rec)
    stop = _plan_stop(rec, plan, steps_so_far=[], seen={}, history=[])
    if stop:
        rec["finished"] = E.clock()
        rec["explain"] = explain(rec)
        _save(mid, **rec)
        return rec
    return _drive(mid, goal, rec, plan["steps"], 0, {}, [], started)


def _plan_stop(rec: dict, plan: dict, steps_so_far: list, seen: dict, history: list, context: str = "", replan: bool = False) -> bool:
    """A plan that is not a list of steps to run ends this run here: a question for the owner (kept, with what is needed
    to continue), a planner that could not plan, nothing to do, or a request that is not a PC task. True = stop."""
    if plan["status"] == "clarify":
        q = plan["question"] or "What exactly should I do?"
        rec.update(status="needs_clarification", question=q, asked=q,
                   resume={"kind": "answer", "cursor": len(steps_so_far), "steps": steps_so_far, "seen": seen,
                           "history": history, "context": context})
        return True
    if plan["status"] != "ok":
        rec.update(status="failed", error=("the planner could not make a plan: " + ("; ".join(plan["problems"]) or plan["status"]))[:300])
        return True
    if not plan["steps"]:
        if replan or steps_so_far:
            rec.update(status="failed", error="the re-plan had no further steps")
        else:
            rec.update(status="not_understood", error="no plan for this goal")
        return True
    if plan["steps"][0].get("tool") == "answer":
        rec.update(status="not_a_pc_task", error="this is a question to answer, not something to do on the PC")
        return True
    return False


def _resumed(mid: str, goal: str, started: float) -> dict:
    """Continue a mission that stopped for the owner, under the same id and effects journal (audit PC-D6)."""
    rec = _resume_state(mid)
    r = rec.get("resume") or {}
    steps, i = list(r.get("steps") or []), int(r.get("cursor") or 0)
    seen, history = dict(r.get("seen") or {}), list(r.get("history") or [])
    for s in rec.get("steps", []):
        if s.get("held"):
            s["held"] = "resumed"
    rec.update(status="running", error=None, question=None, explain=None, resume=None, approval_id=None)
    if r.get("kind") == "answer":                  # the owner answered the planner's question: plan the rest with it
        answer = str(rec.get("answer") or "")
        try:
            plan = _as_plan(_plan(f"{goal} (the owner's answer to '{rec.get('asked') or ''}': {answer})", r.get("context") or ""))
        except Exception as e:  # noqa: BLE001
            plan = {"status": "fallback", "steps": [], "question": "", "problems": [f"the planner failed ({type(e).__name__})"]}
        rec["plans"] = list(rec.get("plans") or []) + [plan["steps"]]
        if _plan_stop(rec, plan, steps_so_far=steps[:i], seen=seen, history=history, context=r.get("context") or ""):
            rec["finished"] = E.clock()
            rec["explain"] = explain(rec)
            _save(mid, **rec)
            return rec
        steps = steps[:i] + plan["steps"]
    elif r.get("approval") and i < len(steps):     # hand the owner's approval to exactly the job it was asked for
        steps[i] = dict(steps[i], _approval=r["approval"])
    _save(mid, **rec)
    return _drive(mid, goal, rec, steps, i, seen, history, started)


def _drive(mid: str, goal: str, rec: dict, steps: list, i: int, seen: dict, history: list, started: float) -> dict:
    try:
        hold = coord.acquire(mid, E.lock_path, E.budget_s, kind="mission")      # the same PC-control lock as single tasks,
    except coord.Busy as e:                                     # with this mission's own budget; refreshed while it runs
        rec.update(status="busy", error=str(e))
        rec["explain"] = explain(rec)
        _save(mid, **rec)
        return rec
    # The mission is a registered PC operation: the plugin's Stop (stop_pc_task) reaches it like stop_mission does, and its
    # steps' PC jobs belong to it (coordinator.bind).
    hold.op.on_stop.append(lambda: cancel(mid))
    bound = coord.bind(hold.op)
    steps_over = False
    try:
        while i < len(steps):
            step = steps[i]
            hold.op.touch(f"step {len(rec['steps']) + 1}")
            if _cancelled(mid):
                rec["status"] = "cancelled"
                break
            if len(rec["steps"]) >= MAX_STEPS:
                rec.update(status="stopped", error=f"step budget of {MAX_STEPS} used up")
                break
            if E.clock() - started > E.budget_s:
                rec.update(status="stopped", error=f"time budget of {E.budget_s / 60:.0f} min used up")
                break
            need = STEP_WORST_S.get(step.get("tool"), 0)
            if need and E.clock() - started + need > E.budget_s:
                rec.update(status="stopped", error=f"not started: a {step['tool']} step can take up to {need // 60} min and only "
                                                   f"{max(0, E.budget_s - (E.clock() - started)) / 60:.0f} min of the time budget are left")
                break
            if not hold.still_held():                   # never act once another task holds the PC
                rec.update(status="stopped", error="the PC lock was lost; stopped so two tasks never act at once")
                break
            pending = hold.op.unconfirmed_jobs()
            if pending:                                 # a node job nothing has confirmed ended may still control the desktop
                rec.update(status="stopped", error=f"not started: the node has not confirmed that job(s) {pending} ended, so one of "
                                                   "them may still be acting on the PC")
                break
            sig = _sig(step)
            occ = seen.get(sig, 0)
            res = _act(mid, step, occ)
            seen_before = dict(seen)
            seen[sig] = occ + 1
            passed, checked, unc = coord.verify(step, res)
            entry = {"n": len(rec["steps"]) + 1, "tool": step["tool"],
                     "args": {k: v for k, v in step.items() if k != "tool" and not k.startswith("_")},
                     "result": res, "passed": passed, "checked": checked, "uncertainty": unc}
            rec["steps"].append(entry)
            history.append(sig + ":" + hashlib.sha256(json.dumps(res, sort_keys=True, default=str).encode()).hexdigest()[:8])
            _save(mid, **rec)
            if _cancelled(mid) and not passed:          # stopped while this step ran: never re-planned after a stop
                rec["status"] = "cancelled"
                break
            if res.get("state") == "waiting_for_permission":
                entry["held"] = True                    # not a failed step: it continues with the owner's approval
                rec.update(status="waiting_for_permission", approval_id=res.get("approval_id"), how=res.get("how"),
                           resume={"kind": "approval", "cursor": i, "steps": steps, "seen": seen_before, "history": history[:-1],
                                   "approval": {"id": res.get("approval_id"), "body": res.get("approval_body")}})
                break
            if res.get("state") == "denied" or res.get("interrupted"):
                rec.update(status="denied" if res.get("state") == "denied" else "needs_owner", error=str(res.get("error") or "denied")[:300])
                if res.get("interrupted"):
                    entry["held"] = True
                    rec["resume"] = {"kind": "interrupted", "cursor": i, "steps": steps, "seen": seen_before,
                                     "history": history[:-1], "effect": res.get("effect")}
                break
            if res.get("refused"):                      # the kernel refused: final, never re-planned (audit PC-D9)
                rec.update(status="refused", error=f"the JARVIS kernel refused the {step['tool']} step: {str(res.get('error'))[:200]}. "
                                                   "Not re-planned: a refusal is final, not a reason to try another way")
                break
            if res.get("unavailable"):                  # it could not run at all: another plan cannot fix that
                rec.update(status="failed", error=f"the {step['tool']} step could not run: {str(res['unavailable'])[:200]}. "
                                                  "Not re-planned: trying another way cannot fix this")
                break
            why = _loop(history)
            if why:
                rec.update(status="stopped", error=f"stopped a loop: {why}")
                break
            if passed:
                i += 1
                continue
            if (step["tool"] == "research" and step.get("open_after") and res.get("file") and res.get("qa_passed") is not False
                    and res.get("opened_verified") is not True
                    and not any(s.get("tool") == "open_output" and s.get("path") == res["file"] for s in steps[i + 1:])):
                # the file was made and checked; only its window was not seen. Researching again would make a second file
                # (live mission 2026-10-16 18:01): open the one that exists instead.
                entry["kept"] = True
                steps = steps[:i + 1] + [{"tool": "open_output", "path": res["file"], "post": "a window showing the file appeared"}] + steps[i + 1:]
                rec["plans"].append(steps[i + 1:])
                i += 1
                continue
            if rec["replans"] >= MAX_REPLANS:
                rec.update(status="failed", error=f"step {step['tool']} failed after {MAX_REPLANS} re-plans: {unc or checked}"[:300])
                break
            rec["replans"] += 1
            ctx = _replan_context(rec, step, unc or checked)
            try:
                plan = _as_plan(_plan(goal, ctx))
            except Exception as e:  # noqa: BLE001 - the steps already done stay on record; the mission ends failed
                rec.update(status="failed", error=f"the re-plan failed ({type(e).__name__})")
                break
            if _plan_stop(rec, plan, steps_so_far=steps[:i], seen=seen, history=history, context=ctx, replan=True):
                break
            steps = steps[:i] + plan["steps"]
            rec["plans"].append(steps[i:])
            _save(mid, **rec)
        else:
            rec["status"] = "completed"
        steps_over = True
    finally:
        coord.unbind(bound)
        if not steps_over:                              # an exception ended the loop: never keep the lock (settle: only once
            coord.settle(hold)                          # no worker and no unconfirmed node job can still act, review H1)
    # The record is final before the lock is let go, so "the lock is free" (and Stop's "this mission ended") never comes
    # before the mission's record says how it ended.
    try:
        rec["finished"] = E.clock()
        rec["explain"] = explain(rec)
        _save(mid, **rec)
    finally:
        coord.settle(hold)
    return rec


def explain(rec: dict) -> str:
    """The owner's answer: what was done and checked, and what the research found (from its summary, not invented)."""
    steps = [s for s in rec.get("steps", []) if not s.get("held")]     # a step held for the owner is not a failed step
    done = [s for s in steps if s.get("passed")]
    guess = [s for s in done if coord.UNOBSERVED in str(s.get("uncertainty") or "")]
    parts = [f"{len(done)} of {len(steps)} steps done and checked" if not guess else
             f"{len(done)} of {len(steps)} steps done ({len(guess)} of them only reported ok, not observed)"]
    for s in [s for s in rec.get("steps", []) if s.get("passed") or s.get("kept")]:
        r = s.get("result") or {}
        if s["tool"] == "research" and r.get("summary"):
            parts.append("Findings: " + str(r["summary"])[:600])
        if r.get("path") or r.get("file"):
            parts.append(f"File: {r.get('path') or r.get('file')}")
    unsure = [f"step {s.get('n')} ({s['tool']}): {s['uncertainty']}" for s in done if s.get("uncertainty")]
    if unsure:                                   # passed, but not everything about it could be confirmed: say so
        parts.append("Not certain: " + "; ".join(unsure))
    mid = rec.get("id") or "the mission"
    if rec.get("status") == "waiting_for_permission":
        parts.append(f"Not finished: waiting for your approval in the JARVIS console (approval {rec.get('approval_id')}); "
                     f"approve it with your PIN, then tell me to continue the mission ({mid}): the steps already done are "
                     "not repeated")
    elif rec.get("status") == "needs_clarification":
        parts.append(f"Not finished: I need to know: {rec.get('question')} Answer it and I continue the mission ({mid})")
    elif rec.get("status") == "needs_owner":
        parts.append(f"Not finished: {rec.get('error')} Tell me 'done' if it already happened (I skip it) or 'redo' if it did "
                     f"not (I do it now), and I continue the mission ({mid})")
    elif rec.get("status") != "completed":
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


def resume(mid: str, answer: str = "") -> dict:
    """Continue a mission that stopped for the owner, with the same mission id and effects journal (audit PC-D6): after
    an approval (approve it in the console first), with the answer to the planner's question, or after an interrupted
    step ('done': it already happened, skip it; 'redo': it did not, do it now). Done steps are never repeated."""
    rec = load(mid)
    if not rec:
        return {"ok": False, "error": "no such mission"}
    st, answer = rec.get("status"), " ".join(str(answer or "").split())[:300]
    if st not in RESUMABLE or not rec.get("resume"):
        return {"ok": False, "mission": mid, "status": st,
                "error": f"the mission is {st}: only a mission waiting for your approval, an answer or a check can continue"}
    if st == "needs_clarification" and not answer:
        return {"ok": False, "mission": mid, "status": st, "error": f"answer the question first: {rec.get('question')}"}
    if st == "needs_owner" and answer.lower() not in ("done", "redo"):
        return {"ok": False, "mission": mid, "status": st, "error": "say whether the interrupted step already happened: 'done' "
                "(it did: skip it) or 'redo' (it did not: do it now)"}
    if not _cas(mid, st, "queued"):
        return {"ok": False, "mission": mid, "error": "the mission changed meanwhile (already continuing or stopped)"}
    if st == "needs_owner":
        key = rec["resume"].get("effect") or ""
        if answer.lower() == "done":                 # the owner checked: it happened. Recorded so it is never done again
            _q("update effects set state='done', result=?, at=? where key=?",
               (json.dumps({"ok": True, "owner_confirmed": True}), E.clock(), key))
        else:
            _q("delete from effects where key=?", (key,))
    n = int(rec.get("resumes") or 0) + 1
    _save(mid, status="queued", resumes=n, **({"answer": answer} if st == "needs_clarification" else {}))
    with SetWorkflowID(f"{mid}-r{n}"):
        DBOS.start_workflow(run_mission, mid, rec.get("goal", ""), True)
    return {"ok": True, "mission": mid, "status": "continuing", "from": st}


def cancel(mid: str) -> bool:
    """Stops at the next step boundary and stops the PC job running now; no further step starts. A mission that waits
    for the owner is cancelled at once (it can no longer be continued)."""
    if not _q("select id from missions where id=?", (mid,)):
        return False
    _q("update missions set cancel=1 where id=?", (mid,))
    rec = load(mid) or {}
    if rec.get("status") in RESUMABLE:
        _save(mid, status="cancelled", resume=None, explain=explain(dict(rec, status="cancelled", error="cancelled")))
        # A mission that waited for an approval can no longer be continued: its card must not stay approvable in the console.
        # Only this mission's own approval is denied (its record names it); an approval already decided or expired is tolerated.
        for aid in {str(a) for a in (rec.get("approval_id"), ((rec.get("resume") or {}).get("approval") or {}).get("id")) if a}:
            try:
                coord.deny_approval_id(aid)
            except Exception:  # noqa: BLE001 - denying is best effort; the cancel itself stands
                pass
    try:
        E.on_cancel()
    except Exception:  # noqa: BLE001
        pass
    return True


def configure(db_dir: Path, app_version: str = "jarvis-missions-1"):
    """DBOS on a local SQLite file; nothing leaves the PC. Call once per process before launch()."""
    db_dir.mkdir(parents=True, exist_ok=True)
    E.db_path = db_dir / "missions.sqlite"
    DBOS(config={"name": "jarvis-missions", "system_database_url": "sqlite:///" + str(db_dir / "dbos.sqlite").replace("\\", "/"),
                 "application_version": app_version, "run_admin_server": False})
