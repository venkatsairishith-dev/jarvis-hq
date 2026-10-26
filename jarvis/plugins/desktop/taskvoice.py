"""Talking to JARVIS about a PC task while it works (D65): the small hidden hook behind `desktop__quick_command`.

    "what are you doing" / "why did you stop" / "what went wrong"   answered from the live task state (taskstate.py: content-free), WITHOUT interrupting the task
    "try another way" / "skip this step" / "stop"                    controls the loop reads between two actions (stop also reaches every node job, as stop_pc_task does)
    "what have you learned about Excel" / "forget that"              the lessons store (lessons.py): viewable and clearable by the owner

Who may speak: the hook is reachable only through the hidden quick_command entry, which the face calls with the OWNER'S OWN latest message (plain text of his turn; never a
tool result, a page, a window's words or a model's output), and the chat model's tool list does not offer it (a test checks both). A WHOLE phrase is a control (taskstate.parse),
so "I will not stop the music" is not "stop". A message that carries a PIN, a password or a code is dropped here when a task is live: nothing is kept, nothing is forwarded, and
the reply says approvals are given in the console only (no PIN by voice, as D63). Standard library only.
"""
from __future__ import annotations

import time

from . import lessons as lessons_mod, taskstate

RECENT_LESSON_S = 1800.0


def handle(text: str, *, state_path=None, control_path=None, lessons: lessons_mod.Lessons | None = None, stop=None, now: float | None = None,
           ops_running=None) -> dict | None:
    """The reply for the owner's message, or None when it is not about the PC task or the lessons (then the message goes on as any other)."""
    kind, args = taskstate.parse(text)
    if not kind:
        return None
    now = time.time() if now is None else now
    d = taskstate.read(state_path)
    live = taskstate.live(d, now)
    running = bool(ops_running()) if ops_running else False
    out = lambda reply, label="One moment": {"handled": True, "status": "completed", "reply": reply, "planned_by": "rules", "label": label}      # noqa: E731
    if kind == "secret":
        if not (live or running):
            return None
        return out("PINs and approvals are only given in the JARVIS console, never by voice or in chat. I did not keep that.", "Not by voice")
    if kind in ("doing", "why", "wrong"):
        if kind == "doing" and (live or running) and not d:
            return out("I am working on the PC. I have no more detail yet.")
        if not taskstate.recent(d, now):
            return None
        return out(taskstate.answer(kind, d, now))
    if kind in ("try_another", "skip"):
        if not live:
            return None
        taskstate.push_control(kind, control_path)
        return out("Trying another way." if kind == "try_another" else "Skipping this step.", "On it")
    if kind == "stop":
        if not (live or running):
            return None                                             # the wake-word stop and stop_pc_task handle everything else
        taskstate.push_control("stop", control_path)
        if stop is not None:
            try:
                stop()
            except Exception:  # noqa: BLE001 - the flag file is read by the loop within a moment anyway
                pass
        return out("Stopping.", "Stopping")
    les = lessons if lessons is not None else lessons_mod.default()
    if kind == "learned":
        return out(les.describe(args.get("app") or None))
    if kind == "forget_app":
        n = les.forget(args.get("app"))
        if not n:
            return None                                             # "forget everything about Priya" is the names store's (D62), not an app's lessons
        return out(f"Forgot what I had learned about {lessons_mod.clean_app(args.get('app')) or 'that app'}.")
    if kind == "forget_all":
        n = les.forget(everything=True)
        return out("Forgot everything I had learned." if n else "I had not learned anything.")
    if kind == "forget_last":
        if not taskstate.recent(d, now) and not live:
            return None                                             # "forget that" with no PC task behind it is about something else (memory, chat)
        n = les.forget(last=True)
        return out("Forgot the last thing I learned." if n else "There is nothing recent to forget.")
    return None
