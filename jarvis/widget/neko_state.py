"""Neko's one visible state, decided only from real signals (no timers that fake progress).

Order (first match wins):
  error          a core server is down, the kernel is unreachable or stopped (kill switch), or the watchdog gave up
  needs_permission  the JARVIS kernel holds approvals waiting for the owner (the text says what is waiting)
  speaking       the local speech server synthesized a reply less than that reply's length ago (an estimate: the page
                 plays audio right after it is made)
  working        a main-agent task is running or planning (its current step is the detail)
  thinking       a model call through the privacy gateway is in progress (Neko waits for the model)
  listening      the local speech server transcribed speech in the last few seconds
  completed      a task or deliverable finished in the last few minutes (detail: what, and the file to open)
  wake_listening the wake word is on and healthy (it is off until its runtime is built)
  sleeping       nothing of the above
"""
from __future__ import annotations

import json

CORE = ("N.E.K.O", "memory", "agent", "plugins", "speech", "console")
COMPLETED_FOR_S = 180
LISTENING_FOR_S = 4.0
STATES = ("error", "needs_permission", "speaking", "working", "thinking", "listening", "completed", "wake_listening", "sleeping")


def _approval_text(a: dict) -> str:
    act = a.get("action") or {}
    what = act.get("summary") or act.get("op") or act.get("tool") or act.get("kind") or ""
    if not what:
        what = json.dumps(act, ensure_ascii=False)[:120]
    reason = f" — {a['reason']}" if a.get("reason") else ""
    return f"{a.get('level', '')}: {what}{reason}"[:200]


def decide(status: dict, approvals: list[dict], gateway: dict | None, speech: dict | None, deliverable: dict | None,
           wake: dict | None = None) -> dict:
    servers = status.get("servers") or {}
    down = [n for n in CORE if servers.get(n) is False]
    j = status.get("jarvis") or {}
    wd = status.get("watchdog") or {}
    if down or (servers.get("console") and not j.get("reachable")) or j.get("killed") or wd.get("gave_up_on"):
        why = (f"not answering: {', '.join(down)}" if down else "JARVIS is stopped (kill switch)" if j.get("killed")
               else "the JARVIS kernel is not reachable" if not j.get("reachable") else f"self-repair gave up on {', '.join(wd['gave_up_on'])}")
        return {"state": "error", "detail": why, "recover": "The watchdog retries every 10 minutes; or run neko-start.ps1."}
    if approvals:
        return {"state": "needs_permission", "detail": _approval_text(approvals[0]), "count": len(approvals),
                "recover": "Approve or deny it with your PIN in the JARVIS console."}
    if speech and speech.get("speaking_estimate"):
        return {"state": "speaking", "detail": "Speaking"}
    task = status.get("task") or {}
    if task.get("state") in ("running", "planning"):
        steps = task.get("steps") or []
        cur = next((s for s in steps if s.get("outcome") == "running"), None)
        done = sum(1 for s in steps if s.get("outcome") == "succeeded")
        detail = task.get("summary") or "Working on your task"
        if cur:
            detail += f" — step {done + 1} of {len(steps)} ({cur.get('agent')})"
        return {"state": "working", "detail": detail[:200], "run_id": task.get("id")}
    if gateway and (gateway.get("inflight") or 0) > 0:
        return {"state": "thinking", "detail": "Thinking"}
    if speech and speech.get("asr_age_s") is not None and speech["asr_age_s"] <= LISTENING_FOR_S:
        return {"state": "listening", "detail": "Heard you"}
    recent = []
    if task.get("state") in ("done", "succeeded", "finished", "completed") and task.get("age_s", 1e9) <= COMPLETED_FOR_S:
        recent.append({"age": task["age_s"], "detail": "Done: " + (task.get("summary") or "your task")[:160], "run_id": task.get("id")})
    if deliverable and deliverable.get("age_s", 1e9) <= COMPLETED_FOR_S:
        recent.append({"age": deliverable["age_s"], "detail": f"Ready: {deliverable['name']}", "file": deliverable["file"],
                       "folder": deliverable["folder"], "passed": deliverable.get("passed")})
    if recent:
        best = min(recent, key=lambda r: r["age"])
        best.pop("age")
        return {"state": "completed", **best}
    if wake and wake.get("enabled") and wake.get("healthy"):
        return {"state": "wake_listening", "detail": "Say “Hey Neko”"}
    return {"state": "sleeping", "detail": "Click me or type a task" + ("" if wake and wake.get("enabled") else " (wake word off)")}
