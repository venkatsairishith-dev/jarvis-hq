"""Explanations from real events only (plan item 3, P8): turn the kernel's audit rows into a timeline, a short spoken sentence, and a
partial answer to "why?". Nothing here invents progress, reasons or results.

Input: audit rows as the kernel stores them, {"seq", "ts", "kind", "payload"} (payload a dict or its JSON text).

  timeline(rows)  -> one entry per meaningful event, typed with AG-UI names (RUN_STARTED, STEP_FINISHED, CUSTOM ...), for the
                     RunTimeline in the console
  say(row)        -> one spoken sentence, or None. Voice speaks only when something needs the owner (an approval), something was
                     refused or stopped, or something ended. No "working on it...", no "almost done".
  why(rows, seq)  -> a partial explanation of one event (Google PAIR: the best explanation is a partial one, given on request):
                     what was asked, the policy level and the rule the kernel applied, whether and when the owner approved, what
                     happened after, and whether that result was CHECKED. Every field is read from the log; missing facts stay
                     missing ("not recorded"), never guessed.

Honesty rules the wording follows: a node or a lab reporting success is "reports it finished", not "done"; only a lab verdict that
passed is "checked"; a task closed as "completed" may still be "completed, not verified" in the kernel, so the sentence says to open
it rather than claiming it was checked. Targets are shown as the audit log already scrubbed them, shortened.
"""
from __future__ import annotations

import json
import time
from typing import Any, Iterable
from urllib.parse import urlsplit

MAX_TARGET = 60
NEEDS_OWNER = ("YELLOW_APPROVAL", "RED")
# kernel audit kind -> AG-UI event type (custom events keep their own name)
AGUI = {"task_created": "RUN_STARTED", "task_completed": "RUN_FINISHED", "task_failed": "RUN_ERROR", "task_cancelled": "RUN_FINISHED",
        "job_started": "STEP_STARTED", "job_finished": "STEP_FINISHED", "job_cancelled": "STEP_FINISHED", "model_call": "CUSTOM",
        "request": "CUSTOM", "authorize": "CUSTOM", "approved": "CUSTOM", "denied": "CUSTOM", "lab_verdict": "CUSTOM",
        "lane_preference_set": "CUSTOM", "kill": "CUSTOM", "resume": "CUSTOM", "job_queued": "CUSTOM"}


def _payload(row: dict) -> dict:
    p = row.get("payload")
    if isinstance(p, str):
        try:
            p = json.loads(p)
        except ValueError:
            return {}
    return p if isinstance(p, dict) else {}


def _short(x: Any) -> str:
    s = " ".join(str(x).split())
    return s if len(s) <= MAX_TARGET else s[:MAX_TARGET - 1] + "…"


def action_phrase(action: Any) -> str:
    """'open example.com in the browser' from the audit's action dict (already scrubbed by the kernel)."""
    if not isinstance(action, dict):
        return "an action"
    tool, op, target = str(action.get("tool", "")), str(action.get("op", "")), action.get("target", "")
    t = str(target or "")
    if tool == "browser" and t.startswith(("http://", "https://")):
        host = (urlsplit(t).hostname or "").removeprefix("www.")
        return f"{op} {host} in the browser" if op else f"use {host} in the browser"
    if "|" in t:                                          # "process|element" from UI actions
        app, _, element = t.partition("|")
        return f"{op} {_short(element)} in {app}" if element else f"{op} in {app}"
    if not t:
        return f"{op} ({tool})" if op else tool or "an action"
    return f"{op} {_short(t)} ({tool})" if op else f"{tool} {_short(t)}"


def timeline(rows: Iterable[dict]) -> list[dict]:
    out = []
    for row in rows:
        kind = str(row.get("kind", ""))
        if kind not in AGUI:
            continue
        p = _payload(row)
        if kind == "request" and p.get("level") == "GREEN":
            continue                                       # routine: the authorize/job rows that follow tell the story
        out.append({"seq": row.get("seq"), "ts": row.get("ts"), "type": AGUI[kind], "name": kind,
                    "text": _describe(kind, p), "needs_owner": kind == "request" and p.get("level") in NEEDS_OWNER})
    return out


def _describe(kind: str, p: dict) -> str:
    act = action_phrase(p.get("action"))
    if kind == "request":
        lvl = p.get("level")
        if lvl == "DENY":
            return f"Refused: {act}. {p.get('reason', '')}".strip()
        if lvl in NEEDS_OWNER:
            return f"Waiting for your approval ({lvl}): {act}"
        return f"Asked to {act}"
    if kind == "authorize":
        return f"Allowed: {act}" if p.get("allowed") else f"Stopped: {act}. {p.get('reason', '')}".strip()
    if kind == "approved":
        return "You approved it"
    if kind == "denied":
        return "You turned it down"
    if kind == "job_queued":
        return f"Queued a {p.get('kind', 'job')} job on {p.get('node', 'the PC')}"
    if kind == "job_started":
        return "A step started"
    if kind == "job_finished":
        return "The step reports it finished" if p.get("ok") else "The step failed"
    if kind == "job_cancelled":
        return "The step was cancelled"
    if kind == "model_call":
        return f"Asked {p.get('provider', 'a model')} ({p.get('lane', '?')} lane)"
    if kind == "lab_verdict":
        return "The lab's result was checked and passed" if p.get("passed") else "The lab's result was checked and did not pass"
    if kind == "task_created":
        return "Task started"
    if kind == "task_completed":
        return "Task marked completed (open it to see what was checked)"
    if kind == "task_failed":
        why = p.get("why")
        return "Task failed" + (f" ({why})" if why else "")
    if kind == "task_cancelled":
        return "Task cancelled"
    if kind == "lane_preference_set":
        prov = p.get("provider")
        return f"The {p.get('lane')} lane is back to automatic" if prov in (None, "auto") else f"The {p.get('lane')} lane now prefers {prov}"
    if kind == "kill":
        return "Everything stopped (kill switch)"
    if kind == "resume":
        return "Running again after the kill switch"
    return kind


def say(row: dict) -> str | None:
    """One sentence for voice, or None. Only approvals needed, refusals and stops, endings, and the owner's own switches."""
    kind, p = str(row.get("kind", "")), _payload(row)
    act = action_phrase(p.get("action"))
    if kind == "request":
        if p.get("level") in NEEDS_OWNER:
            return f"I need your approval to {act}. It's in the console; approve it with your PIN."
        if p.get("level") == "DENY":
            return f"I won't {act}: {p.get('reason', 'the policy refuses it')}."
        return None
    if kind == "authorize" and not p.get("allowed"):
        return f"I stopped before I could {act}: {p.get('reason', 'not allowed')}."
    if kind == "job_finished" and not p.get("ok"):
        return "That step failed. I'll say what I know when you ask why."
    if kind == "lab_verdict":
        return "Checked: the result passed." if p.get("passed") else "Checked: the result did not pass."
    if kind == "task_completed":
        return "That task is marked completed. Open it to see exactly what was checked."
    if kind == "task_failed":
        return "That task failed" + (f" ({p['why']})." if p.get("why") else ".")
    if kind == "lane_preference_set":
        return _describe(kind, p) + "."
    if kind == "kill":
        return "Everything is stopped."
    return None


def why(rows: list[dict], seq: int) -> dict:
    """A partial explanation of the event at `seq`, from the log only."""
    by_seq = {r.get("seq"): r for r in rows}
    row = by_seq.get(seq)
    if row is None:
        return {"error": "no such event"}
    kind, p = str(row.get("kind", "")), _payload(row)
    out: dict[str, Any] = {"event": kind, "when": _when(row.get("ts")), "what": _describe(kind, p)}
    before = [r for r in rows if (r.get("seq") or 0) <= seq]
    after = [r for r in rows if (r.get("seq") or 0) > seq]
    action = p.get("action")
    if kind in ("request", "authorize") and isinstance(action, dict):
        req = next((r for r in reversed(before) if r.get("kind") == "request" and _payload(r).get("action") == action), None)
        auth = next((r for r in (after if kind == "request" else [row]) if r.get("kind") == "authorize"
                     and _payload(r).get("action") == action), None)
        out["step"] = action_phrase(action)
        out["decided_by"] = "the kernel's policy (a model can suggest a level but never lower it)"
        out["level"] = _payload(req).get("level") if req else "not recorded"
        out["rule"] = (_payload(req).get("reason") if req else None) or "not recorded"
        if auth is not None:
            ap = _payload(auth)
            out["allowed"] = bool(ap.get("allowed"))
            out["how"] = ap.get("reason", "not recorded")
            out["owner_approved"] = ap.get("reason") == "approved, single use"
        else:
            out["allowed"] = "not decided yet"
    if kind.startswith("job_"):
        jid = p.get("id")
        q = next((r for r in before if r.get("kind") == "job_queued" and _payload(r).get("id") == jid), None)
        fin = next((r for r in rows if r.get("kind") == "job_finished" and _payload(r).get("id") == jid), None)
        out["step"] = f"{_payload(q).get('kind', 'a')} job" if q else "a job (queue record not found)"
        out["result"] = ("reported finished" if _payload(fin).get("ok") else "failed") if fin else "not finished yet"
        out["checked"] = "no: a node's own report is not a check"
    if kind == "lab_verdict":
        out["checked"] = "yes: the lab's verifier ran the criteria" if p.get("passed") else "yes, and it did not pass"
    if kind == "model_call":
        out["model"] = p.get("provider", "not recorded")
        out["lane"] = p.get("lane", "not recorded")
        out["private_values_hidden"] = p.get("scrubbed", "not recorded")
    if kind == "task_completed":
        out["checked"] = "not recorded in this event: open the task (it may be 'completed, NOT verified')"
    return out


def _when(ts: Any) -> str | None:
    try:
        return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(float(ts)))
    except (TypeError, ValueError):
        return None
