"""Neko's one visible state, decided only from real signals (no timers that fake progress).

Order (first match wins):
  error          a core server is down, the desktop plugin (it hosts the PC monitor) is not running, the kernel is
                 unreachable or stopped (kill switch), or the watchdog gave up
  needs_permission  the JARVIS kernel holds approvals waiting for the owner (the text says what is waiting)
  speaking       the local speech server synthesized a reply less than that reply's length ago (an estimate: the page
                 plays audio right after it is made)
  working        a main-agent task is running or planning (its current step is the detail)
  thinking       a model call through the privacy gateway is in progress (Neko waits for the model)
  listening      the local speech server transcribed speech in the last few seconds
  completed      a task finished, or a deliverable passed its checks, in the last few minutes (detail: what, and the file)
  attention      the same moment, but it did not end well: a task that ended without finishing (plan failed, stopped,
                 partly done, held) says so ("outcome"), and a deliverable that failed its QA checks (or whose result
                 could not be read) says that, never "Done" or "Ready"
  wake_listening the wake word is on and healthy (it is off until its runtime is built)
  sleeping       nothing of the above
"""
from __future__ import annotations

import json

CORE = ("N.E.K.O", "memory", "agent", "plugins", "speech", "console")
OPTIONAL_GIVE_UPS = ("stack:local_models",)         # llama-swap is optional: neko-start.ps1 starts it only when installed
COMPLETED_FOR_S = 180
LISTENING_FOR_S = 4.0
STATES = ("error", "needs_permission", "speaking", "working", "thinking", "listening", "attention", "completed", "wake_listening",
          "sleeping")
# Plugins whose absence is an error on the icon, not just a gap: the desktop plugin runs the 60 s PC monitor, and nothing
# else would say that the monitor stopped
WATCHED_PLUGINS = {"desktop": "the desktop plugin (PC monitor)"}
# The wake listener's own states (jarvis/wake/wake_listener.py) while it handles a command
WAKE_ACTIVE = {"recording": "Listening…", "transcribing": "Understanding what you said", "submitting": "Starting your task"}
# The desktop plugin's steps on the PC (plugin/plugins/desktop, activity.json); "completed"/"failed" show through the task
PC_STEPS = {"inspecting": "Inspecting", "diagnosing": "Diagnosing", "researching": "Researching", "applying_fix": "Applying the fix",
            "verifying": "Verifying", "waiting_for_permission": "Waiting for your permission", "working": "Working on the PC"}
ACTIVITY_FOR_S = 120
# Final main-agent states that are not "done": shown as they are, never as success and never hidden
UNFINISHED = {"failed": "Could not finish", "cancelled": "Stopped", "partial": "Partly done", "held": "Needs your check",
              "interrupted": "Interrupted"}


def _approval_text(a: dict) -> str:
    act = a.get("action") or {}
    what = act.get("summary") or act.get("op") or act.get("tool") or act.get("kind") or ""
    if not what:
        what = json.dumps(act, ensure_ascii=False)[:120]
    reason = f" — {a['reason']}" if a.get("reason") else ""
    return f"{a.get('level', '')}: {what}{reason}"[:200]


def _deliverable_item(d: dict) -> dict:
    """A deliverable is "Ready" only when its QA checks passed; a failed or unreadable QA result needs the owner's look."""
    base = {"file": d.get("file"), "folder": d.get("folder"), "passed": d.get("passed")}
    if d.get("passed") is True:
        return {**base, "detail": f"Ready: {d['name']}"}
    if d.get("passed") is False:
        failed = [str(c) for c in (d.get("failed_checks") or [])][:4]
        what = f" ({', '.join(failed)})" if failed else ""
        return {**base, "state": "attention", "outcome": "qa_failed",
                "detail": f"QA failed{what}: {d['name']} — open it to check, or make it again"[:200]}
    return {**base, "state": "attention", "outcome": "qa_unknown", "detail": f"Not checked: {d['name']} — its QA result could not be read"[:200]}


# The owner's voice-first vocabulary (master pass 2026-10-20). Added NEXT TO the icon state above, never instead of it:
# the same honesty rules, finer names. "listening" only while the wake listener records from a microphone that delivers
# audio; "completed" only where the icon would say completed (a finished task, or a deliverable whose checks passed).
PHASES = ("idle", "listening", "transcribing", "planning", "researching", "executing", "awaiting_approval", "waiting_for_input",
          "validating", "speaking", "completed", "needs_review", "cancelled", "failed")
_RESEARCH_AGENTS = ("research", "web_search", "browser")
_PC_PHASE = {"inspecting": "executing", "diagnosing": "executing", "researching": "researching", "applying_fix": "executing",
             "verifying": "validating", "waiting_for_permission": "awaiting_approval", "working": "executing"}


def mic_state(wake: dict | None) -> dict:
    """What the microphone really does: a stream only when the wake listener is healthy and not muted (paused)."""
    w = wake or {}
    muted = w.get("state") == "paused"
    stream = bool(w.get("enabled") and w.get("healthy") and not muted and w.get("state") in ("wake_listening", "recording"))
    return {"stream": stream, "muted": muted, "recording": stream and w.get("state") == "recording",
            "problem": (w.get("detail") or "") if w.get("enabled") and not w.get("healthy") and not muted else ""}


def phase_of(out: dict, task: dict, wake: dict | None, activity: dict | None) -> str:
    st = out.get("state")
    w = wake or {}
    if st == "error":
        return "failed"
    if st == "needs_permission":
        return "awaiting_approval"
    if st == "speaking":
        return "speaking"
    if st == "listening":
        if w.get("state") == "recording" and mic_state(wake)["stream"]:
            return "listening"
        return "transcribing"                       # transcribing / submitting, or speech just turned into text
    if st == "working":
        if out.get("step") in _PC_PHASE:
            return _PC_PHASE[out["step"]]
        if task.get("state") == "planning":
            return "planning"
        cur = next((x for x in task.get("steps") or [] if x.get("outcome") == "running"), None)
        if cur and any(k in str(cur.get("agent") or "").lower() for k in _RESEARCH_AGENTS):
            return "researching"
        return "executing"
    if st == "thinking":
        return "planning"
    if st == "completed":
        return "completed"
    if st == "attention" and task.get("state") == "held" and task.get("waiting_for_input"):
        return "waiting_for_input"                  # a question waits in the chat (main_agent pending_input)
    if st == "attention":
        outcome = out.get("outcome")
        return "cancelled" if outcome == "cancelled" else "failed" if outcome in ("failed", "qa_failed") else "needs_review"
    return "idle"


def voice_view(decided: dict, status: dict, wake: dict | None = None, activity: dict | None = None) -> dict:
    """The phase and microphone for a decided icon state (``decide``'s own output is unchanged)."""
    return {"phase": phase_of(decided, status.get("task") or {}, wake, activity), "mic": mic_state(wake)}


def decide(status: dict, approvals: list[dict], gateway: dict | None, speech: dict | None, deliverable: dict | None,
           wake: dict | None = None, activity: dict | None = None) -> dict:
    servers = status.get("servers") or {}
    down = [n for n in CORE if servers.get(n) is False]
    j = status.get("jarvis") or {}
    wd = dict(status.get("watchdog") or {})
    # A status file written before VOI-D11 may still say the watchdog gave up on a server that is simply not installed.
    wd["gave_up_on"] = [k for k in (wd.get("gave_up_on") or []) if k not in OPTIONAL_GIVE_UPS]
    plugins = status.get("plugins") or {}
    listed = servers.get("plugins") is not False and any(v not in (None, "", "unknown") for v in plugins.values())
    plugins_down = [p for p in WATCHED_PLUGINS if listed and plugins.get(p) != "running"]   # only when the list was read
    if down or plugins_down or (servers.get("console") and not j.get("reachable")) or j.get("killed") or wd.get("gave_up_on"):
        why = (f"not answering: {', '.join(down)}" if down else "JARVIS is stopped (kill switch)" if j.get("killed")
               else "the JARVIS kernel is not reachable" if servers.get("console") and not j.get("reachable")
               else f"self-repair gave up on {', '.join(wd['gave_up_on'])}" if wd.get("gave_up_on")
               else "not running: " + ", ".join(WATCHED_PLUGINS[p] for p in plugins_down))
        return {"state": "error", "detail": why, "recover": "The watchdog retries every 10 minutes; or run neko-start.ps1."}
    if approvals:
        return {"state": "needs_permission", "detail": _approval_text(approvals[0]), "count": len(approvals),
                "recover": "Approve or deny it with your PIN in the JARVIS console."}
    if speech and speech.get("speaking_estimate"):
        return {"state": "speaking", "detail": "Speaking"}
    if wake and wake.get("state") in WAKE_ACTIVE:       # "Hey Neko" was heard: that beats a running task (it may be "stop")
        return {"state": "listening", "detail": WAKE_ACTIVE[wake["state"]]}
    act = activity or {}
    if act.get("age_s", 1e9) <= ACTIVITY_FOR_S and act.get("state") in PC_STEPS:   # the desktop plugin's repair workflow
        label = PC_STEPS[act["state"]]
        if act["state"] == "waiting_for_permission":
            return {"state": "needs_permission", "detail": act.get("detail") or label,
                    "recover": "Approve or deny it with your PIN in the JARVIS console."}
        return {"state": "working", "detail": (f"{label}: {act['detail']}" if act.get("detail") else label)[:200], "step": act["state"]}
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
    if task.get("state") in UNFINISHED and task.get("age_s", 1e9) <= COMPLETED_FOR_S:
        why = f" — {task['error']}" if task.get("error") else ""
        recent.append({"age": task["age_s"], "state": "attention",
                       "detail": f"{UNFINISHED[task['state']]}: {task.get('summary') or 'your task'}{why}"[:200],
                       "run_id": task.get("id"), "outcome": task["state"]})
    if deliverable and deliverable.get("age_s", 1e9) <= COMPLETED_FOR_S:
        recent.append({"age": deliverable["age_s"], **_deliverable_item(deliverable)})
    if recent:
        best = min(recent, key=lambda r: r["age"])
        best.pop("age")
        return {"state": best.pop("state", "completed"), **best}
    if wake and wake.get("enabled") and wake.get("healthy") and wake.get("state") != "paused":
        return {"state": "wake_listening", "detail": "Say “Hey Neko”"}
    why = (" (wake word paused)" if wake and wake.get("state") == "paused" else
           f" (wake word not working: {wake.get('detail') or 'restarting'})" if wake and wake.get("enabled") else " (wake word off)")
    return {"state": "sleeping", "detail": "Click me or type a task" + why}
