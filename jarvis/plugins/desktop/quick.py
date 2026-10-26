"""D60: the rules-first router. A short, plain PC command never waits for an online model.

    "open notepad", "pause", "volume up", "minimize excel", "open youtube", "play despacito", "what time is it",
    "open notepad and write hello", "type hello into notepad"
       --> decide.fast_path (the existing one-command rules) or the small rule table below --> a decide.Plan
       --> the same coordinator and the same JARVIS kernel jobs as every other PC task (policy.py decides, risky steps wait for the PIN)

What it is NOT: a second brain. The rules accept a whole message that is ONE command with words they know; everything else (a second part, a
pronoun, an account, an app they do not know, a question) returns None and goes the normal way (online plan, local executors). The plan a rule makes
goes through decide.validate exactly like a model's plan, so the account checks, the key names and the post-conditions are the same.

Two doors use it (plugins/desktop/__init__.py): `do_goal` (so the chat model's first and only tool call finishes in seconds instead of waiting for
an online plan) and `quick_command` (the face asks first, before it asks any chat model; if nothing matches it answers `handled: false` at once).

Interfaces left open on purpose:
  * `register_normaliser(fn)`: a function text -> English command text, run before the rules. The Tenglish/Hindi verb lexicon (the translate module of
    the language plan) plugs in here; this base has no such module, so nothing is registered and only English commands match.
  * `RULES` / `LOCAL_ANSWERS`: plain tables. A command that has no tool yet (a screenshot has a kernel job but no desktop-plugin step) is NOT
    guessed; add its row when the tool exists.

Switch: JARVIS_QUICK_PATH=0 (or off/false/no) turns the router off for one run; default on.
"""
from __future__ import annotations

import datetime
import os
import re
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from . import decide

ENV_FLAG = "JARVIS_QUICK_PATH"
PLANNED_BY = "rules"
MAX_TEXT = 300

_normalisers: list[Callable[[str], str]] = []


def enabled(env: dict | None = None) -> bool:
    env = os.environ if env is None else env
    return str(env.get(ENV_FLAG, "")).strip().lower() not in ("0", "false", "no", "off")


def register_normaliser(fn: Callable[[str], str]) -> None:
    """Add a text -> English command text function (Tenglish verb lexicon hook). It must be pure and fast; an error in it is ignored."""
    if fn not in _normalisers:
        _normalisers.append(fn)


def clear_normalisers() -> None:
    _normalisers.clear()


@dataclass
class Quick:
    kind: str                           # "pc": a plan for the coordinator; "answer": a reply the rules know without any tool
    request: str
    plan: "decide.Plan | None" = None
    reply: str = ""                     # for kind "answer"
    ack: str = ""                       # one short line for the face while it runs ("Opening notepad")


# ------------------------------------------------------------------------------------------------------------------- local answers
def _clock(now: datetime.datetime) -> str:
    hour = now.hour % 12 or 12
    return f"It is {hour}:{now.minute:02d} {'AM' if now.hour < 12 else 'PM'}."


def _date(now: datetime.datetime) -> str:
    return f"Today is {now.strftime('%A')}, {now.day} {now.strftime('%B %Y')}."


# (pattern over the lower-cased, cleaned message, function(now) -> reply)
LOCAL_ANSWERS: list[tuple[re.Pattern, Callable[[datetime.datetime], str]]] = [
    (re.compile(r"(?:what(?:'s| is)(?: the)? (?:current )?time(?: now| right now)?|what time is it(?: now| right now)?|tell me the time|current time|time now)"), _clock),
    (re.compile(r"(?:what(?:'s| is) (?:the )?(?:today'?s )?date(?: today)?|what day is it(?: today)?|today'?s date)"), _date),
]

# ------------------------------------------------------------------------------------------------------------------- compound rules
_APP = "|".join(sorted((re.escape(a) for a in decide.APPS), key=len, reverse=True))
OPEN_AND_TYPE = re.compile(rf"(?:open|launch|start)\s+(?:the\s+)?({_APP})(?:\s+app)?\s*(?:,|and then|and|then)\s*(?:type|write|enter)\s+(.+)")
TYPE_INTO = re.compile(rf"(?:type|write|enter)\s+(.+?)\s+(?:in|into|on)\s+(?:the\s+)?({_APP})(?:\s+app|\s+window)?")
_LABEL = re.compile(r"^(?:a|an|the|my)\s+[a-z][a-z ]{0,30}:\s*(.+)$", re.S)
_PREFIX = re.compile(r"(?i)^(?:hey jarvis[, ]*|jarvis[, ]*|please |can you |could you |would you )+")
_SUFFIX = re.compile(r"(?i)\s+(?:please|for me|now)$")


def _typed_text(raw: str) -> str:
    """What to type: the words after type/write, quotes dropped; 'a shopping list: milk, eggs' types the items, not the label."""
    text = raw.strip().strip(" .!").strip("\"'`").strip()
    label = _LABEL.match(text)
    if label:
        text = label.group(1).strip()
    return text


def _clean(request: str) -> str:
    r = " ".join(str(request or "").split())[:1000]
    r = _PREFIX.sub("", r.strip()).strip(" .!?")
    return _SUFFIX.sub("", r)


def _normalise(text: str) -> str:
    for fn in list(_normalisers):
        try:
            out = fn(text)
        except Exception:                                   # noqa: BLE001 - a broken lexicon must not break the router
            continue
        if isinstance(out, str) and out.strip():
            text = out
    return text


def _validated(steps: list[dict], request: str, profiles: list[dict]) -> "decide.Plan | None":
    import json
    plan = decide.validate(json.dumps({"steps": steps}), profiles, request)
    if plan.status != "ok":
        return None
    plan.model, plan.seconds = "rule", 0.0
    return plan


def _compound(r: str, request: str, profiles: list[dict]) -> "decide.Plan | None":
    low = r.lower()
    m = OPEN_AND_TYPE.fullmatch(low)
    steps: list[dict] | None = None
    if m and decide.APPS.get(m.group(1)) is not None:
        # the text keeps the owner's own capitals: take it from the same position in the cleaned (not lower-cased) message
        text = _typed_text(r[m.start(2):])
        if text and len(text) <= MAX_TEXT and "\n" not in text:
            steps = [{"tool": "open_app", "app": m.group(1)}, {"tool": "type_text", "app": m.group(1), "target": "", "text": text}]
    else:
        t = TYPE_INTO.fullmatch(low)
        if t:
            text = _typed_text(r[t.start(1):t.end(1)])
            if text and len(text) <= MAX_TEXT and "\n" not in text:
                steps = [{"tool": "type_text", "app": t.group(2), "target": "", "text": text}]
    return _validated(steps, request, profiles) if steps else None


# ------------------------------------------------------------------------------------------------------------------- the entry point
def _site(step: dict) -> str:
    """The page's short name: the site word the plan carries, else the host of its address without www and the ending."""
    if step.get("site"):
        return str(step["site"])[:40]
    host = re.sub(r"^https?://(?:www\.)?", "", str(step.get("url") or "")).split("/")[0]
    parts = host.split(".")
    return (parts[-2] if len(parts) >= 2 else host)[:40] or "the page"


def ack_for(plan: "decide.Plan") -> str:
    """One short line for the face while the plan runs. Never the owner's typed text or a search query."""
    s = plan.steps[0] if plan.steps else {}
    tool = s.get("tool")
    if tool == "open_app":
        return f"Opening {s.get('app') or 'the app'}"
    if tool == "open_website":
        return f"Opening {_site(s)}"
    if tool == "play_media":
        return "Finding it on YouTube"
    if tool == "window":
        word = {"minimize": "Minimizing", "maximize": "Maximizing", "restore": "Restoring", "focus": "Focusing"}.get(str(s.get("action")), "Working on")
        return f"{word} {s.get('app') or 'the window'}"
    if tool == "type_text":
        return f"Typing in {s.get('app') or 'the app'}"
    return "On it"


def match(request: str, profiles: list[dict] | None = None, now: datetime.datetime | None = None, env: dict | None = None) -> Quick | None:
    """The rules' answer for a whole message, or None. Pure and fast (no I/O, no model)."""
    if not enabled(env):
        return None
    text = _normalise(" ".join(str(request or "").split())[:1000])
    r = _clean(text)
    if not r or len(r) > 160:
        return None
    low = r.lower()
    for pattern, answer in LOCAL_ANSWERS:
        if pattern.fullmatch(low):
            return Quick("answer", text, reply=answer(now or datetime.datetime.now()), ack="One moment")
    profs = profiles or []
    plan = None
    if len(r) <= 60:
        plan = decide.fast_path(text, profs)
    if plan is None and not decide.NEG_RX.search(text):
        plan = _compound(r, text, profs)
    if plan is None or plan.status != "ok" or not plan.steps:
        return None
    tools = {s.get("tool") for s in plan.steps}
    if not tools <= {"open_app", "open_website", "play_media", "press_key", "window", "type_text", "browser"} or "stop" in tools:
        return None
    if any(s.get("tool") == "browser" for s in plan.steps) and not any(s.get("tool") in ("open_website", "play_media") for s in plan.steps):
        return None                                           # a page action on its own needs the model
    return Quick("pc", text, plan=plan, ack=ack_for(plan))


# ------------------------------------------------------------------------------------------------------------------- running it
_STATUS = {"completed": "completed", "waiting_for_permission": "waiting_for_permission", "needs_clarification": "needs_clarification",
           "cancelled": "cancelled", "stopped": "cancelled"}
_MEDIA = {"playpause": {"pause": "Paused.", "play": "Resumed."}, "nexttrack": "Skipped to the next track.", "prevtrack": "Went back a track.",
          "volumeup": "Volume up.", "volumedown": "Volume down.", "mute": "Toggled mute."}


def _titled(app: Any) -> str:
    app = " ".join(str(app or "").split())[:40]
    return app if any(ch.isupper() for ch in app) else app.title()


def _done_phrase(step: dict) -> str:
    tool = step.get("tool")
    if tool == "open_app":
        return f"Opened {_titled(step.get('app'))}."
    if tool == "open_website":
        return f"Opened {_site(step)}."
    if tool == "play_media":
        return "Playing it on YouTube."
    if tool == "type_text":
        return f"Typed it into {_titled(step.get('app'))}."
    if tool == "window":
        verb = {"minimize": "Minimized", "maximize": "Maximized", "restore": "Restored", "focus": "Focused"}.get(str(step.get("action")), "Done with")
        return f"{verb} {_titled(step.get('app'))}."
    if tool == "press_key":
        word = _MEDIA.get(str(step.get("key")))
        if isinstance(word, dict):
            return word.get(str(step.get("intent") or step.get("text")), "Done.")
        return word or "Pressed the key."
    return "Done."


def _verb_phrase(step: dict) -> str:
    tool = step.get("tool")
    return {"open_app": f"open {_titled(step.get('app'))}", "open_website": f"open {_site(step)}", "play_media": "play it",
            "type_text": f"type into {_titled(step.get('app'))}", "window": f"{step.get('action') or 'move'} {_titled(step.get('app'))}",
            "press_key": "press the key"}.get(str(tool), "do that")


def result_for(cmd: Quick, rec: dict | None) -> dict:
    """The do_goal-shaped result of a rules run: status, planned_by, a one-or-two-sentence summary, and the steps. Honest: 'opened' only
    when every checked step passed; otherwise it says it could not confirm."""
    if cmd.kind == "answer":
        return {"status": "completed", "planned_by": PLANNED_BY, "summary": cmd.reply, "steps": []}
    rec = rec or {}
    status = _STATUS.get(rec.get("status"), "failed")
    plan_steps = cmd.plan.steps if cmd.plan else []
    checked = rec.get("steps") or []
    verified = bool(checked) and all(x.get("passed") for x in checked) and not rec.get("uncertain")
    first = plan_steps[0] if plan_steps else {}
    if status == "completed" and verified:
        summary = " ".join(_done_phrase(s) for s in plan_steps)
    elif status == "completed":
        summary = f"I tried to {_verb_phrase(first)}, but I could not confirm that it worked."
    elif status == "waiting_for_permission":
        summary = "That step needs your approval first. Approve it in the JARVIS console with your PIN, then ask me again."
    elif status == "needs_clarification":
        summary = str(rec.get("question") or "I need one more detail first.")
    elif status == "cancelled":
        summary = "That was stopped."
    else:
        why = " ".join(str(rec.get("error") or rec.get("how") or rec.get("route") or "the step did not finish").split())[:160]
        summary = f"I could not {_verb_phrase(first)}: {why}"
    steps = [{"n": i, "id": f"s{i}", "executor": "pc", "kind": "ui_decide", "goal": _verb_phrase(s), "status": status if i == len(plan_steps) or status != "completed" else "completed",
              "model": "rule", "result": "", "verified": verified} for i, s in enumerate(plan_steps, 1)]
    out = {"status": status, "planned_by": PLANNED_BY, "summary": summary[:600], "steps": steps}
    if rec.get("approval_id"):
        out["approval_id"] = rec["approval_id"]
    return out


async def run(cmd: Quick, run_pc: Callable[[str, Any], Awaitable[dict]], progress: Callable[[dict], None] | None = None) -> dict:
    """Run a matched command: an answer needs nothing; a plan goes to the coordinator (`run_pc`), whose kernel jobs the policy decides as always."""
    def note(state: str, n: int = 0, total: int = 0) -> None:
        if progress is not None:
            try:
                progress({"state": state, "n": n, "total": total, "executor": "rules"})
            except Exception:                              # noqa: BLE001
                pass
    if cmd.kind == "answer":
        return result_for(cmd, None)
    note("running", 1, 1)
    try:
        rec = await run_pc(cmd.request, cmd.plan)
    except Exception:                                      # noqa: BLE001 - reported as a failed step, never as a crash of the entry
        note("failed", 1, 1)
        return result_for(cmd, {"status": "failed", "error": "the PC step could not run"})
    out = result_for(cmd, rec)
    note({"completed": "done", "waiting_for_permission": "waiting"}.get(out["status"], "failed"), 1, 1)
    return out
