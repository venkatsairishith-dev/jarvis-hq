"""The live state of the PC task, and the owner's voice about it (D65).

While JARVIS works on the PC, the control loop keeps ONE small file up to date: what it is doing, which problem it met, what it tried, why it stopped. The file is
CONTENT-FREE by construction: every field is a number, a word of a closed list, or an app's process name. It never holds typed text, a file name, a window
title or a word the screen said. Two things read it:
  * the face's progress line (core.GOAL_PROGRESS keeps its own, older, fixed-word file; this one adds the detail), and
  * `answer()`: "what are you doing", "why did you stop", "what went wrong" are answered from it WITHOUT interrupting the task (the question is a read of this file).
The owner can also steer the task by voice: "try another way", "skip this step", "stop" are CONTROLS written to a second small file (`push_control`) that the loop
reads between two actions. Only the face's own call with the owner's message writes them (`quick_command`, never a chat model's tool list); a phrase that carries
a PIN, password or code is dropped before anything else (no PIN by voice, as in D63), and a control is a WHOLE phrase, so "I will not stop the music" is not "stop".
Standard library only.
"""
from __future__ import annotations

import json
import os
import re
import threading
import time
from pathlib import Path

DATA = Path(os.environ.get("JARVIS_DATA_DIR", r"D:\jarvis\data"))
STATE_FILE = DATA / "plugins" / "pc_task_state.json"
CONTROL_FILE = DATA / "plugins" / "pc_task_control.json"
LIVE_FRESH_S = 90.0              # a "running" file older than this belongs to a task that is gone
RECENT_S = 1800.0                # a finished task can still be asked about for half an hour

STATES = ("running", "paused_owner", "asking", "waiting_approval", "needs_owner", "stopped", "failed", "done", "idle")
PHASES = ("perceive", "decide", "act", "verify", "recover", "wait", "ask", "")
CONTROLS = ("stop", "try_another", "skip")
SAY = {    # fixed phrases for what the loop is doing (an app's process name is added after "in"/"on"); no typed text, ever
    "open_app": "opening", "click": "clicking in", "click_at": "clicking in", "type": "typing into", "paste": "typing into", "key": "pressing a key in",
    "scroll": "scrolling", "save_as": "saving the file from", "check_file": "checking the folder for the file", "focus": "bringing to the front",
    "restore": "bringing back", "drag": "dragging in", "wait": "waiting for", "read": "reading", "look": "looking at", "done": "finishing",
    # D76 (open-ended tasks): the words of the web, document and output milestones; the "app" is a site's host name (letters, digits, dots)
    "plan": "planning the task for", "browse": "browsing", "search": "searching", "download": "downloading from", "read_doc": "reading the files in",
    "write_output": "writing the result for", "ask_owner": "asking you about", "fill": "filling in a form on",
}
APP = re.compile(r"^[A-Za-z0-9_.\- ]{1,40}$")
_LOCK = threading.Lock()


def _app(app) -> str:
    a = str(app or "").strip()
    return a if APP.match(a) else ""


def _word(value, allowed, default="") -> str:
    return value if value in allowed else default


def _write(path: Path, data: dict) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data), encoding="utf-8")
        os.replace(tmp, path)
    except OSError:
        pass


def _read(path: Path) -> dict | None:
    try:
        d = json.loads(path.read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else None
    except (OSError, ValueError):
        return None


class TaskState:
    """The writer: one per running PC task. Cheap, never raises, and a failed write is a missing sign of life, not a failed task."""

    def __init__(self, state_file: Path | None = None, control_file: Path | None = None, clock=time.time):
        self.state_file = Path(state_file) if state_file else STATE_FILE
        self.control_file = Path(control_file) if control_file else CONTROL_FILE
        self.clock = clock
        self.d: dict = {}

    def begin(self, task_id: str = "", app: str = "", total: int = 0) -> None:
        self.d = {"v": 1, "task": re.sub(r"[^A-Za-z0-9_\-]", "", str(task_id))[:40], "state": "running", "phase": "perceive", "app": _app(app), "act": "", "step": 0,
                  "total": max(0, min(int(total or 0), 99)), "problem": "", "tried": [], "why": "", "started": self.clock(), "updated": self.clock()}
        with _LOCK:
            _write(self.control_file, {"v": 1, "controls": []})            # an old "stop" must not end the next task
        self._flush()

    def update(self, *, state: str | None = None, phase: str | None = None, app: str | None = None, act: str | None = None, step: int | None = None,
               problem: str | None = None, why: str | None = None, **_) -> None:
        d = self.d
        if state is not None:
            d["state"] = _word(state, STATES, d.get("state", "running"))
        if phase is not None:
            d["phase"] = _word(phase, PHASES, "")
        if app is not None:
            d["app"] = _app(app)
        if act is not None:
            d["act"] = _word(act, tuple(SAY), "")
        if step is not None:
            d["step"] = max(0, min(int(step), 99))
        if problem is not None:
            from .lessons import PROBLEMS
            d["problem"] = _word(problem, PROBLEMS + ("uac", "resource", "owner_active", "loop"), "")
        if why is not None:
            d["why"] = _word(why, tuple(WHY), "")
        d["updated"] = self.clock()
        self._flush()

    def tried(self, route: str, result: str) -> None:
        """One thing that was tried for the current problem: a route word and ok/failed. The last six are kept."""
        from .lessons import ROUTES
        if route in ROUTES and result in ("ok", "failed"):
            self.d.setdefault("tried", []).append({"route": route, "result": result})
            self.d["tried"] = self.d["tried"][-6:]
            self.d["updated"] = self.clock()
            self._flush()

    def clear_tried(self) -> None:
        self.d["tried"] = []

    def end(self, state: str, why: str = "") -> None:
        self.update(state=state, phase="", why=why)

    def _flush(self) -> None:
        self.d["line"] = (_doing(self.d) + ".") if self.d.get("state") in ("running", "paused_owner", "asking", "waiting_approval", "needs_owner") else ""      # the HUD strip's sentence: fixed phrases and a process name
        with _LOCK:
            _write(self.state_file, self.d)

    # ---- the controls the owner gave by voice
    def controls(self) -> list[str]:
        """Take (and clear) the pending controls, oldest first."""
        with _LOCK:
            raw = _read(self.control_file) or {}
            got = [c for c in (raw.get("controls") or []) if c in CONTROLS]
            if got:
                _write(self.control_file, {"v": 1, "controls": []})
        return got


WHY = {   # why a task stopped: fixed words the loop picks, and the sentence the owner hears
    "owner_stop": "you told me to stop", "owner_active": "you started using the PC, so I am waiting until you are done",
    "uac": "Windows asked for administrator permission, which only you can give", "dialog_ask": "a dialog needed your decision",
    "loop": "I kept repeating the same step, so I stopped", "budget": "the step budget was used up", "no_progress": "nothing I tried changed anything",
    "no_window": "I could not find or open the window", "not_responding": "the app stopped responding", "approval": "a step needed your approval",
    "resource": "the PC is missing something it needs (disk space, network or the app itself)", "skipped": "you told me to skip the step",
    "done": "the task is finished", "asked": "I need your answer to go on", "error": "a step failed",
    "takeover": "a step needs you at the screen (a CAPTCHA, a login or a code) and I do not do those",
}
PROBLEM_SAY = {
    "wrong_window": "the wrong window was in front", "no_window": "the app had no window", "minimized": "the window was minimised",
    "other_desktop": "the window was on another desktop", "dialog": "an unexpected dialog came up", "not_responding": "the app was not responding",
    "element_missing": "I could not find the control I needed", "typed_not_landed": "the typed text did not land", "no_effect": "my last action changed nothing",
    "slow_start": "the app was slow to start", "window_vanished": "the window closed by itself", "action": "an action did not work",
    "uac": "Windows asked for administrator permission", "resource": "the PC is missing disk space, network or the app", "owner_active": "you were using the PC",
    "loop": "I kept repeating the same step",
}


def read(path: Path | None = None) -> dict | None:
    return _read(Path(path) if path else STATE_FILE)


def live(d: dict | None, now: float | None = None) -> bool:
    """Is there a task under way right now? (running, paused, asking or waiting for an approval, and written recently)"""
    now = time.time() if now is None else now
    return bool(d) and d.get("state") in ("running", "paused_owner", "asking", "waiting_approval", "needs_owner") and now - float(d.get("updated") or 0) <= LIVE_FRESH_S


def recent(d: dict | None, now: float | None = None) -> bool:
    now = time.time() if now is None else now
    return bool(d) and now - float(d.get("updated") or 0) <= RECENT_S


def _doing(d: dict) -> str:
    app = d.get("app") or "the PC"
    act = SAY.get(d.get("act") or "", "")
    if d.get("state") == "paused_owner":
        return "I am waiting because you are using the PC. I will carry on when you are done"
    if d.get("state") == "waiting_approval":
        return "I am waiting for your approval in the JARVIS console"
    if d.get("state") == "needs_owner":
        return "I stopped because a step needs you at the screen. I will look again when you press Done"
    if d.get("state") == "asking":
        return "I am waiting for your answer"
    if d.get("phase") == "recover" and d.get("problem"):
        return f"I hit a problem ({PROBLEM_SAY.get(d['problem'], 'something went wrong')}) and I am working around it in {app}"
    if act:
        return f"I am {act} {app}" if not act.endswith(("in", "into")) else f"I am {act} {app}"
    return f"I am working in {app}" if d.get("app") else "I am working on it"


def _tried_words(d: dict) -> str:
    t = d.get("tried") or []
    if not t:
        return ""
    return "I tried " + ", then ".join(f"{x['route'].replace('_', ' ')} ({'it worked' if x['result'] == 'ok' else 'it did not'})" for x in t[-4:]) + ". "


def answer(kind: str, d: dict | None, now: float | None = None) -> str:
    """The reply to 'what are you doing' (kind 'doing'), 'why did you stop' ('why'), 'what went wrong' ('wrong'), from the state file only."""
    if not d or not recent(d, now):
        return "I am not doing anything on the PC right now." if kind == "doing" else "There is no recent PC task to talk about."
    isLive = live(d, now)
    step = f" (step {d['step']}{' of ' + str(d['total']) if d.get('total') else ''})" if d.get("step") else ""
    if kind == "doing":
        if isLive:
            return _doing(d) + step + "."
        return {"done": "I finished the last task.", "stopped": "I stopped the last task.", "failed": "The last task did not finish."}.get(d.get("state"), "I am not doing anything on the PC right now.")
    why = WHY.get(d.get("why") or "", "")
    prob = PROBLEM_SAY.get(d.get("problem") or "", "")
    if kind == "why":
        if isLive and d.get("state") == "running":
            return "I have not stopped. " + _doing(d) + step + "."
        if d.get("state") == "done":
            return "I did not stop early; the task is finished."
        return ("I stopped because " + why + "." if why else "I stopped, but I did not record why.") + (f" The last problem was that {prob}." if prob and d.get("why") != "done" else "")
    # wrong
    if not prob and d.get("state") not in ("failed", "stopped") and not d.get("why"):
        return "Nothing has gone wrong so far." + (" " + _doing(d) + "." if isLive else "")
    out = f"The problem was that {prob}. " if prob else "Something went wrong. "
    out += _tried_words(d)
    if why and d.get("why") != "done":
        out += f"I stopped because {why}."
    elif isLive:
        out += _doing(d) + "."
    return out.strip()


# ---------------------------------------------------------------------------------------------------------------- the owner's words
_FILLER = re.compile(r"(?i)^(?:(?:hey|ok|okay|hi)\s+jarvis[,\s]+|jarvis[,\s]+|please\s+|can you\s+|could you\s+|just\s+|now\s+|quickly\s+)+")
_TAIL = re.compile(r"(?i)[\s,.!?]*(?:please|thanks|thank you)?[\s,.!?]*$")
DOING_RX = re.compile(r"(?i)^(?:what(?:'s| is| are)? you(?:'re| are)? doing(?: right now| now)?|what are you up to|what(?:'s| is) (?:going on|happening)|what are you working on|where are you(?: now)?|status|what(?:'s| is) the status)$")
WHY_RX = re.compile(r"(?i)^(?:why (?:did you|have you|are you|did it) (?:stop|stopped|pause|paused|wait|waiting|waited|stuck)(?: just now| there)?|why (?:did you )?stop(?:ped)?|why are you waiting|why (?:is it|are you) (?:stuck|not moving)|why the pause)$")
WRONG_RX = re.compile(r"(?i)^(?:what(?:'s| is| has)? (?:went |gone |going )?wrong|what went wrong|what(?:'s| is) the problem|what(?:'s| is) the matter|what happened|what(?:'s| is) wrong|what is the issue|what(?:'s| is) blocking you|what blocked you)$")
TRY_RX = re.compile(r"(?i)^(?:try (?:it )?(?:another|a different|some other) way|try something else|do it (?:another|a different) way|use (?:another|a different) way|try (?:it )?differently)$")
SKIP_RX = re.compile(r"(?i)^(?:skip (?:this|that|the) (?:step|one)|skip (?:it|this|that)|move on|go to the next step|leave (?:it|that) and move on)$")
STOP_RX = re.compile(r"(?i)^(?:stop|stop (?:it|that|this|now|everything|the task|working|doing that)|halt|cancel(?: it| that| the task)?|abort|enough|that's enough|quit (?:it|that))$")
LEARNED_RX = re.compile(r"(?i)^(?:what have you learn(?:ed|t)(?: so far)?(?: about (?P<app>[\w. \-]{1,40}))?|what did you learn(?: about (?P<app2>[\w. \-]{1,40}))?|what do you know about (?P<app3>[\w. \-]{1,40}?)(?: lessons)?)$")
FORGET_LAST_RX = re.compile(r"(?i)^(?:forget (?:that|it|this|the last (?:one|lesson))|forget what you just learn(?:ed|t))$")
FORGET_APP_RX = re.compile(r"(?i)^forget (?:everything|all|what you(?:'ve| have) learn(?:ed|t)) (?:you(?:'ve| have) learn(?:ed|t) )?about (?P<app>[\w. \-]{1,40})$")
FORGET_ALL_RX = re.compile(r"(?i)^forget (?:all|everything)(?: (?:you(?:'ve| have) )?learn(?:ed|t))?(?: lessons)?$|^clear (?:all )?(?:your )?lessons$")
SECRET = re.compile(r"(?i)\b(?:pin|passcode|password|passwd|otp|one[- ]?time|verification code|cvv|token|secret)\b[^.]{0,20}?\d|\b\d{4,8}\b.{0,12}\b(?:pin|otp|code)\b|\b(?:my|the) (?:pin|password|otp)\b|\bapprove (?:it|that|this|the)\b|\bauthori[sz]e\b")


def parse(text: str) -> tuple[str, dict]:
    """(kind, args) for the owner's WHOLE message, or ('', {}) when it is not one of the task questions or controls. Kinds: doing, why, wrong, try_another, skip,
    stop, learned, forget_last, forget_app, forget_all, secret. A message with a PIN, password or code is 'secret' (dropped by the caller, nothing kept)."""
    t = " ".join(str(text or "").split())[:200]
    t = _TAIL.sub("", _FILLER.sub("", t)).strip()
    if not t:
        return "", {}
    if SECRET.search(t):
        return "secret", {}
    for kind, rx in (("doing", DOING_RX), ("why", WHY_RX), ("wrong", WRONG_RX), ("try_another", TRY_RX), ("skip", SKIP_RX), ("stop", STOP_RX), ("forget_last", FORGET_LAST_RX), ("forget_all", FORGET_ALL_RX)):
        if rx.match(t):
            return kind, {}
    m = FORGET_APP_RX.match(t)
    if m:
        return "forget_app", {"app": m.group("app").strip()}
    m = LEARNED_RX.match(t)
    if m:
        return "learned", {"app": (m.group("app") or m.group("app2") or m.group("app3") or "").strip()}
    return "", {}


def push_control(kind: str, path: Path | None = None, clock=time.time) -> bool:
    """Queue a control for the loop (stop, try_another, skip). Only for a task that is live; returns False when there is none."""
    if kind not in CONTROLS:
        return False
    cf = Path(path) if path else CONTROL_FILE
    with _LOCK:
        raw = _read(cf) or {}
        cur = [c for c in (raw.get("controls") or []) if c in CONTROLS]
        cur.append(kind)
        _write(cf, {"v": 1, "controls": cur[-5:], "at": clock()})
    return True
