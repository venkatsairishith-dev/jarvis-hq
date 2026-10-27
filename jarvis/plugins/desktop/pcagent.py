"""The human-like PC control loop with a problem-solving ladder (D65). It runs ANY desktop task the way a careful person would:

    look      windows and focus, the active window's accessibility tree, OCR and the vision model only when that is not enough (perceive.py; the cascade's agreement rule)
    think     ONE next action from the decision engine's closed vocabulary (drive.py's propose, or a scripted one in the tests), after the lessons are consulted
    do        through the existing kernel jobs only (the Port lends them); every job carries the owner's goal, a dialog's text rides along as ctx: the kernel decides
    check     the EFFECT, not the reply (closed_loop.py: act -> verify -> one retry by the next route: UIA invoke -> keyboard -> coordinates from pixels -> ask)
    remember  a short working memory of this task (what was tried, what changed) that the model sees, and a content-free lesson file (lessons.py) for the next run

and when something goes wrong it climbs the ladder of ladder.py: (a) the wrong or no window in front, (b) an unexpected dialog (UAC is NEVER touched; only class A is
answered; B and C ask), (c) an element not found, (d) a slow or hung app, (e) typed text that did not land, (f) an action with no effect twice (another control, another
route, a re-plan by the decision engine, then a question), (g) the same step again and again (stop), (h) a resource problem (say it plainly, offer the next best option).
It never fights the owner: when he touches the mouse or keyboard it pauses ("I will wait until you are done"). Stop and the voice controls (taskstate.py) are polled
between every two jobs.

Nothing here decides permission. A step the kernel holds ends the loop with its approval id, exactly as drive.Driver did; text read from a screen can only add care.
The result has the same shape as drive.Driver.run (so coordinator.verify and the journal need no change) plus `problems`, `ask`, `lessons_used`.
"""
from __future__ import annotations

import json
import re
import time
from typing import Any, Callable

from jarvis.plugins._shared import ask_form, closed_loop as cl, screen_text

from . import drive, humanfeel, ladder, lessons as lessons_mod, perceive, taskstate

MAX_ACTIONS = 16               # actions the decision engine proposes
MAX_JOBS = 60                  # kernel jobs of one task, recovery included
DEADLINE_S = 540.0
HUNG_WAIT_S = 25.0
SLOW_START_S = 20.0
WAIT_ELEMENT_S = 6.0
WINDOW_ACTS = ("click", "click_at", "drag", "type", "paste", "key", "scroll", "save_as")
OPAQUE_KEYS = {"ctrl+c", "ctrl+a"}              # keys whose effect cannot be seen on screen: sent, not verified, never retried
NO_VERIFY = ("click_at", "drag", "scroll", "open_app", "save_as", "check_file")      # their own checks (the node's, the disk's) or none: not judged by the screen changing
FILE_DIALOG = re.compile(r"(?i)^\W*(?:save as|open|print|select (?:a )?(?:file|folder)|browse|choose|insert|import|export|page setup|find|go to|new)\b")


class _End(BaseException):
    """The loop is over (an ask, a stop, a hold): carries the result dict."""

    def __init__(self, result: dict):
        self.result = result


class PcAgent:
    def __init__(self, port: perceive.Port, propose: Callable[[str, dict, list], dict], *, classify: Callable[[str, str], tuple] | None = None,
                 lessons: lessons_mod.Lessons | None = None, state: taskstate.TaskState | None = None, goal: str = "", budget: int = MAX_ACTIONS,
                 clock: Callable[[], float] = time.monotonic, sleep: Callable[[float], Any] = time.sleep, stop: Callable[[], bool] | None = None,
                 deadline_s: float = DEADLINE_S, settle_s: float = 1.8, hung_wait_s: float = HUNG_WAIT_S, slow_start_s: float = SLOW_START_S,
                 owner_wait_s: float = humanfeel.OWNER_WAIT_MAX_S, human: bool = True, task_id: str = "",
                 takeover: Callable[[str, "perceive.Observation", Callable[[], bool]], Any] | None = None):
        self.port, self.propose, self.classify = port, propose, classify
        self.lessons = lessons
        self.state = state or taskstate.TaskState(state_file=None)
        self.goal, self.budget = goal, max(1, int(budget))
        self.clock, self._raw_sleep = clock, sleep
        self.stop = stop or (lambda: False)
        self.deadline_s, self.settle_s, self.hung_wait_s, self.slow_start_s, self.owner_wait_s = deadline_s, settle_s, hung_wait_s, slow_start_s, owner_wait_s
        self.human, self.task_id = human, task_id
        self.pending_takeover: dict | None = None
        self.takeover = takeover          # D79: takeover(kind, obs, look) -> plugins/_shared/takeover.Outcome; None = the old "ask and stop" behaviour
        self.watch = humanfeel.OwnerWatch(clock)
        self.t0 = clock()
        self.jobs = 0
        self.history: list[dict] = []
        self.facts: list[str] = []
        self.unverified: list[str] = []
        self.problems: list[str] = []
        self.lessons_used: list[str] = []
        self.done_effects: set = set()
        self.typed: dict = {}
        self.no_effect: dict[str, int] = {}
        self.failed_sig: dict[str, int] = {}
        self.seen: dict[str, int] = {}
        self.pending_ctl: list[str] = []
        self.force_alt = False
        self.replan_sig = ""
        self.interrupted = ""

    # ---------------------------------------------------------------------------------------------------------------------------- small helpers
    def _sleep(self, s: float) -> None:
        self._raw_sleep(s)
        self._poll_controls()

    def _poll_controls(self) -> None:
        try:
            self.pending_ctl += self.state.controls()
        except Exception:  # noqa: BLE001 - a broken control file is no control
            pass

    def _cancelled(self) -> bool:
        self._poll_controls()
        return bool(self.stop() or "stop" in self.pending_ctl)

    def _say(self, act: str = "", app: str = "", phase: str = "", problem: str | None = None, state: str | None = None) -> None:
        try:
            self.state.update(act=act or None, app=app or None, phase=phase or None, problem=problem, state=state, step=len([h for h in self.history if h.get("act")]))
        except Exception:  # noqa: BLE001
            pass

    def _lesson_order(self, app: str, problem: str, routes: list[str]) -> list[str]:
        if self.lessons is None:
            return list(routes)
        got = self.lessons.order(app, problem, routes)
        if got and got[0] != routes[0]:
            self.lessons_used.append(f"{problem}:{got[0]}")
        return got

    def _learn(self, app: str, problem: str, route: str, worked: bool) -> None:
        if self.lessons is not None:
            try:
                self.lessons.record(app, problem, route, worked)
            except Exception:  # noqa: BLE001
                pass
        if problem not in self.problems:
            self.problems.append(problem)
        try:
            self.state.tried(route, "ok" if worked else "failed")
        except Exception:  # noqa: BLE001
            pass

    def _job(self, body: dict) -> dict:
        """ONE kernel job. A hold or a denial is remembered; nothing else is decided here."""
        if self.jobs >= MAX_JOBS:
            raise _End(self._result("budget", False, error=f"the task used {MAX_JOBS} PC steps without finishing"))
        if self._cancelled():
            raise _End(self._cancel_result())
        self.jobs += 1
        res = self.port.act(dict(body))
        res = res if isinstance(res, dict) else {"ok": False, "error": "no result"}
        self.watch.acted()
        if res.get("state") == "waiting_for_permission":
            raise _End(self._result("waiting_for_permission", False, approval_id=res.get("approval_id"), how=res.get("how"), state="waiting_for_permission"))
        if res.get("state") == "cancelled":
            raise _End(self._cancel_result())
        return res

    def _result(self, status: str, ok: bool, **kw) -> dict:
        need = drive.expected_effects(self.goal)
        missing = sorted(need - self.done_effects)
        out = {"ok": ok, "status": status, "actions": self.history, "facts": self.facts, "unverified": self.unverified, "expected_missing": missing,
               "verified": bool(self.facts) and not missing and ok, "problems": list(self.problems), "lessons_used": list(self.lessons_used), **kw}
        if self.pending_takeover:
            out["takeover"] = self.pending_takeover
        why = {"completed": "done", "stopped_to_ask": "asked", "waiting_for_permission": "approval", "cancelled": "owner_stop", "failed": "error", "budget": "budget"}.get(status, "")
        try:
            self.state.end({"completed": "done", "cancelled": "stopped", "waiting_for_permission": "waiting_approval", "stopped_to_ask": "asking"}.get(status, "failed"),
                           kw.get("why") or why)
        except Exception:  # noqa: BLE001
            pass
        return out

    def _cancel_result(self) -> dict:
        if "stop" in self.pending_ctl:
            self.pending_ctl.remove("stop")
        r = self._result("cancelled", False, error="stopped by the owner", why="owner_stop")
        r["state"] = "cancelled"
        return r

    def _ask(self, question: str, options: list[str], *, why: str, reason: str = "", problem: str = "") -> _End:
        """Stop and ask the owner with a form that says what blocked it (the picture of the one window is in his Timeline, local)."""
        form = ask_form.build_form(question, options, reason=reason or "I could not go on by myself.", source="pcagent", default="o1")
        if problem:
            self.problems.append(problem) if problem not in self.problems else None
            try:
                self.state.update(problem=problem)
            except Exception:  # noqa: BLE001
                pass
        return _End(self._result("stopped_to_ask", False, question=question[:200], ask=form, why=why))

    def _hand_over(self, kind: str, obs: perceive.Observation, still: Callable[[perceive.Observation], bool]) -> str:
        """D79: the owner's screen is needed (UAC, a dialog JARVIS cannot read). Returns "resolved" (look again and carry on), or "" (no takeover wired, or it ended without a
        resolution: the caller asks as before). A stop by the owner ends the loop; a durable pause ends it with the takeover record in the result."""
        if self.takeover is None:
            return ""
        try:
            out = self.takeover(kind, obs, lambda: still(self.observe(obs.app or "", "none")) or None)
        except Exception:  # noqa: BLE001 - a broken takeover falls back to the plain question
            return ""
        status = getattr(out, "status", "")
        if status == "resolved":
            return "resolved"
        if status == "stop":
            raise _End(self._cancel_result())
        req = getattr(out, "request", None)
        rec = {"id": getattr(req, "id", ""), "target": "pc", "kind": kind, "host": "", "status": status, "reasks": getattr(out, "reasks", 0), "site": "", "milestones": []}
        self.pending_takeover = rec                      # the durable pause (missions.resume) needs the record; the caller asks its usual question, and the result carries the record
        return ""

    # ---------------------------------------------------------------------------------------------------------------------------- look
    def observe(self, app: str, depth: str = "cheap") -> perceive.Observation:
        return perceive.observe(self.port, app, depth, self.clock)

    def _view(self, obs: perceive.Observation) -> dict:
        return {"windows": [{"process": w.get("process"), "title": w.get("title")} for w in obs.windows[:10] if not w.get("minimized")],
                "dialogs": obs.dialogs, "text": screen_text.scrub_text(obs.text)[0][:6000], "source": {"uia": "accessibility tree", "ocr": "OCR", "vision": "vision model"}.get(obs.source, "none"),
                "front": (obs.front or {}).get("process") if obs.front else None}

    # ---------------------------------------------------------------------------------------------------------------------------- the owner comes first
    def _owner_gate(self, obs: perceive.Observation) -> perceive.Observation:
        if not self.human:
            return obs
        took = self.watch.took_over(obs.owner)
        if not took and self.watch.acted_at is not None and obs.owner.idle_ms is not None and obs.owner.idle_ms < 5000:
            t1 = self.clock()                                      # some input is recent: take the human beat, look again, and see whether it CONTINUED
            self._raw_sleep(humanfeel.BEAT_S)
            took = self.watch.during(obs.owner, perceive.owner_reading(self.port), self.clock() - t1)
        if not took:
            return obs
        self._say(phase="wait", state="paused_owner")
        self.problems.append("owner_active") if "owner_active" not in self.problems else None

        def read() -> humanfeel.OwnerReading:
            return perceive.owner_reading(self.port)
        got = humanfeel.wait_for_owner(read, self.watch, say=lambda t: self.unverified.append("paused: the owner was using the PC") if "paused: the owner was using the PC" not in self.unverified else None,
                                       clock=self.clock, sleep=self._raw_sleep, stop=self._cancelled, max_s=self.owner_wait_s)
        if got == "stopped":
            raise _End(self._cancel_result())
        if got == "gave_up":
            raise self._ask("You have been using the PC for a while, so I paused. Tell me when I should carry on.", ["Carry on now", "Stop here"], why="owner_active",
                            reason="I did not want to fight you for the mouse and keyboard.", problem="owner_active")
        self._say(phase="perceive", state="running")
        return self.observe(obs.app or "")

    # ---------------------------------------------------------------------------------------------------------------------------- ladder (a): the window
    def _front_ok(self, app: str, obs: perceive.Observation) -> bool:
        t = perceive.pick_target(obs.windows, app)
        return bool(t and not t.get("minimized") and not t.get("cloaked") and obs.front and perceive.norm_app(obs.front.get("process")) == perceive.norm_app(t.get("process"))
                    ) or bool(t and t.get("foreground") and not t.get("minimized") and not t.get("cloaked"))

    def _proc(self, w: dict) -> str:
        p = str(w.get("process") or "")
        if p.lower() == "applicationframehost":
            return re.sub(r"[^A-Za-z0-9_.\- ]", "", str(w.get("title") or ""))[:40] or p
        return p

    def ensure_window(self, app: str, obs: perceive.Observation) -> perceive.Observation:
        """Rung (a). Put a window of `app` in front and return the observation that shows it. Wrong or no window: focus it by title or process, bring it back from
        minimised, switch desktop, alt-tab, launch the app when it is not running. Ends the loop with a question when nothing works."""
        if not app or app.lower() in ("desktop", "media"):
            return obs
        if self._front_ok(app, obs):
            return obs
        tgt = perceive.pick_target(obs.windows, app)
        if tgt is None:
            return self._launch(app, obs)
        proc = self._proc(tgt)
        problem = "other_desktop" if tgt.get("cloaked") else "minimized" if tgt.get("minimized") else "wrong_window"
        base = {"other_desktop": ["focus_title", "virtual_desktop", "alt_tab"], "minimized": ["restore", "focus_title", "alt_tab"],
                "wrong_window": ["focus_title", "restore", "alt_tab", "virtual_desktop"]}[problem]
        self._say(app=proc, phase="recover", problem=problem)
        for route in self._lesson_order(proc, problem, base):
            if self.force_alt and route == base[0] and len(base) > 1:
                continue
            now = obs
            for body in self._window_route(route, proc):
                self._job(body)
                self._sleep(0.3)
                now = self.observe(app, "none")
                if self._front_ok(app, now):                         # (the desktop switch is checked after EACH key: it must not walk past the window)
                    break
            if self._front_ok(app, now):
                self._learn(proc, problem, route, True)
                return self.observe(app, "cheap")
            self._learn(proc, problem, route, False)
        raise self._ask(f"I could not bring the {proc} window to the front. Could you click it, or tell me what to do?", ["I brought it forward, carry on", "Open it again", "Stop here"],
                        why="no_window", reason="Focusing, restoring, switching desktop and alt-tab did not work.", problem=problem)

    def _window_route(self, route: str, proc: str) -> list[dict]:
        if route == "focus_title":
            return [{"op": "ui", "action": "focus", "target": f"{proc}|", "text": ""}]
        if route == "restore":
            return [{"op": "ui", "action": "restore", "target": f"{proc}|", "text": ""}, {"op": "ui", "action": "focus", "target": f"{proc}|", "text": ""}]
        if route == "alt_tab":
            return [{"op": "ui", "action": "key", "target": "desktop|", "text": "alt+tab"}]
        if route == "virtual_desktop":
            return [{"op": "ui", "action": "key", "target": "desktop|", "text": k} for k in ("ctrl+win+right", "ctrl+win+right", "ctrl+win+left", "ctrl+win+left", "ctrl+win+left")]
        return []

    def _launch(self, app: str, obs: perceive.Observation) -> perceive.Observation:
        """The app has no window. Start it (the kernel gates it as always), then wait BY CONDITION for its window: a slow-starting app is not a failed one."""
        self._say(app=app, phase="recover", problem="no_window")
        res = self._job({"op": "open_app", "target": app})
        if not res.get("ok"):
            text = str(res.get("error") or "")
            alts = ladder.alternatives_for(app)
            opts = [f"Use {alts[0]} instead"] if alts else []
            raise self._ask(f"I could not start {app}. It may not be installed on this PC.", opts + ["Install it (needs your approval)", "Stop here"], why="resource",
                            reason="Installing software is always your decision.", problem="resource")
        self._learn(app, "no_window", "launch", True)

        def seen():
            o = self.observe(app, "none")
            return o if self._front_ok(app, o) else None
        t0 = self.clock()
        got, waited = humanfeel.wait_for(seen, timeout=self.slow_start_s, clock=self.clock, sleep=self._sleep, stop=self._cancelled)
        if not got:
            seen_any = perceive.pick_target(self.observe(app, "none").windows, app)
            if seen_any is not None:                           # the window exists but is not in front: the rest of rung (a)
                return self.ensure_window(app, self.observe(app, "none"))
            self._learn(app, "slow_start", "wait", False)
            raise self._ask(f"{app} did not show a window after {int(waited)} seconds. Should I wait longer or try again?", ["Wait longer", "Try starting it again", "Stop here"],
                            why="no_window", reason="The app may be slow, stuck or missing.", problem="slow_start")
        if waited > 3.0:
            self._learn(app, "slow_start", "wait", True)
        return self.observe(app, "cheap")

    # ---------------------------------------------------------------------------------------------------------------------------- ladder (b), (d), (h): dialogs, hung apps, resources
    def preflight(self, obs: perceive.Observation) -> perceive.Observation:
        """What must be true before the next action is chosen. UAC, resource problems, a hung app, a dialog nobody asked for."""
        if ladder.is_uac(obs.windows, obs.dialogs):
            self.problems.append("uac") if "uac" not in self.problems else None
            if self._hand_over("uac", obs, lambda o: ladder.is_uac(o.windows, o.dialogs)) == "resolved":
                return self.observe(obs.app or "")
            raise self._ask("Windows is asking for administrator permission. That is only yours to give; I will not touch it. Tell me when it is done.", ["I dealt with it, carry on", "Stop here"],
                            why="uac", reason="I never answer a User Account Control prompt.", problem="uac")
        if obs.hung:
            return self._hung(obs)
        for d in list(obs.dialogs):
            words = ladder.dialog_words(d)
            res = ladder.resource_from_text(words)
            if res:
                self._resource(res, obs)
            if ladder.NOT_RESPONDING.search(words):
                return self._hung(obs)
        return self._dialogs(obs)

    def _resource(self, kind: str, obs: perceive.Observation) -> None:
        self.problems.append("resource") if "resource" not in self.problems else None
        if kind == "disk_full":
            raise self._ask("The disk is full, so this cannot be saved. What should I do?", ["Free up space (clean temporary files)", "Choose another folder", "Stop here"], why="resource",
                            reason="There is not enough free space.", problem="resource")
        raise self._ask("There is no network connection right now. What should I do?", ["Try again in a minute", "Work without the network", "Stop here"], why="resource",
                        reason="The PC cannot reach the network.", problem="resource")

    def _hung(self, obs: perceive.Observation) -> perceive.Observation:
        """Rung (d). A window that does not respond is waited for (a budget, and longer while it is busy), then the owner is OFFERED to close and restart it."""
        proc = self._proc(obs.target) if obs.target else obs.app
        self._say(app=proc, phase="recover", problem="not_responding")
        busy = False
        try:
            busy = bool((self.port.app_info(proc) or {}).get("busy"))
        except Exception:  # noqa: BLE001
            pass
        budget = self.hung_wait_s * (2 if busy else 1)

        def back():
            o = self.observe(obs.app, "none")
            return o if o.target is not None and not o.hung else None
        got, waited = humanfeel.wait_for(back, timeout=budget, clock=self.clock, sleep=self._sleep, stop=self._cancelled, first=1.0, cap=3.0)
        if got:
            self._learn(proc, "not_responding", "wait", True)
            return self.observe(obs.app, "cheap")
        self._learn(proc, "not_responding", "wait", False)
        raise self._ask(f"{proc} has not responded for {int(waited)} seconds. Closing it and starting it again may lose unsaved work. What should I do?",
                        ["Keep waiting", "Close it and start it again (may lose unsaved work)", "Stop here"], why="not_responding",
                        reason="Closing a stuck app can lose what was not saved, so it is your decision.", problem="not_responding")

    def _granted(self, words: str) -> bool:
        """Do the OWNER's own goal words cover what a class-B dialog is about? (replace / overwrite, run, settings, save.) Only his goal counts, never the screen."""
        g = (self.goal or "").lower()
        w = words.lower()
        return bool((re.search(r"replace|overwrite", w) and re.search(r"\b(replace|overwrite)\b", g)) or (re.search(r"save", w) and re.search(r"\bsave|saved\b", g))
                    or (re.search(r"\brun|execute\b", w) and re.search(r"\b(run|execute)\b", g)))

    def _classify(self, words: str, app: str) -> str:
        if self.classify is None:
            return "B" if ladder.UNREADABLE_RX.search(words) else ("C" if re.search(r"(?i)delete|install|pay|send|sign in|password|administrator", words) else "A")
        try:
            cls, _how = self.classify(words, app)
        except Exception:  # noqa: BLE001 - a broken classifier is a careful one
            cls = "B"
        return cls if cls in ("A", "B", "C") else "B"

    def _dialogs(self, obs: perceive.Observation) -> perceive.Observation:
        """Rung (b). A dialog nobody asked for is classified (raise-only engine) and answered only when it is class A and not a file dialog of the task; B and C
        ask the owner unless his own goal words cover a class-B one (then the model and the kernel go on as before)."""
        for d in list(obs.dialogs):
            words = ladder.dialog_words(d)
            clean = screen_text.scrub_text(words)[0]
            if not (d.get("texts") or d.get("title")):
                cls = "B"
            else:
                cls = self._classify(clean, obs.app)
            title = str(d.get("title") or "")
            if not (d.get("texts") or d.get("title")) and self._hand_over("dialog", obs, lambda o: any(not (x.get("texts") or x.get("title")) for x in o.dialogs)) == "resolved":
                return self.observe(obs.app or "", "cheap")           # D79: the owner answered a dialog JARVIS could not read; the screen is read again
            if FILE_DIALOG.search(title):
                continue                                              # the task's own dialog: the model handles it (the kernel still classifies each click)
            if cls == "A":
                btn = ladder.pick_button(d, "A")
                if not btn:
                    continue
                proc = self._proc(obs.target) if obs.target else obs.app
                self._say(app=proc, phase="recover", problem="dialog")
                ctx = drive.dialog_ctx({"dialogs": obs.dialogs}, proc, btn)
                body = {"op": "ui", "action": "click", "target": f"{proc}|{btn}", "text": ""}
                if ctx is not None:
                    body["ctx"] = ctx
                res = self._job(body)
                self._sleep(0.3)
                after = self.observe(obs.app, "none")
                gone = res.get("ok") and not any(x.get("title") == d.get("title") for x in after.dialogs)
                self._learn(proc, "dialog", "answer", bool(gone))
                self.history.append({"n": len(self.history) + 1, "act": "dialog", "app": proc[:40], "ok": bool(gone), "state": res.get("state"), "effect": "closed" if gone else "no_change"})
                if gone:
                    self.facts.append("a dialog nobody asked for was closed")
                    return self.observe(obs.app, "cheap")
                continue
            if cls == "B" and self._granted(words):
                continue
            kind = "C" if cls == "C" else "B"
            save = ladder.is_save_prompt(d)
            q = (f"{obs.app or 'An app'} shows a dialog that asks about unsaved work. I will not answer it for you."
                 if save else f"{obs.app or 'An app'} shows a dialog I will not answer for you (it could change, send or lose something: class {kind}).")
            raise self._ask(q, ["I answered it, carry on", "Cancel the dialog and carry on", "Stop here"], why="dialog_ask",
                            reason="I only answer ordinary (class A) dialogs by myself. A picture of the window is in your Timeline.", problem="dialog")
        return obs

    # ---------------------------------------------------------------------------------------------------------------------------- doing one action, closed loop
    def _key_body(self, proc: str, key: str, ctx: str | None) -> dict:
        b = {"op": "ui", "action": "key", "target": f"{proc}|", "text": key}
        if ctx is not None:
            b["ctx"] = ctx
        return b

    def _routes(self, action: dict, body: dict, obs: perceive.Observation, h: dict) -> list[cl.Route]:
        act, app = action["act"], str(action.get("app") or "")
        proc = self._proc(obs.target) if obs.target else (app or obs.app)
        element = str(action.get("element") or "")
        ctx = body.get("ctx")

        def send(b: dict) -> dict:
            res = self._job(b)
            h["res"], h["body"] = res, b
            return res

        def focus() -> None:
            self._job({"op": "ui", "action": "focus", "target": f"{proc}|", "text": ""})
        routes: list[cl.Route] = []
        if act == "click":
            def uia():
                res = send(dict(body))
                if not res.get("ok") and res.get("reason") == "no element":
                    self._find_element(action, body, obs, h, send)                 # rung (c): wait, scroll, expand menus, palette
            routes.append(cl.Route("uia", uia))
            key = ladder.key_for_element(element) or ladder.mnemonic(element, element.lower() in ladder.MENU_NAMES and not obs.dialogs)
            if key:
                routes.append(cl.Route("keyboard", lambda: (focus(), send(self._key_body(proc, key, ctx)))))

            def pixels():
                loc = h.get("located") or perceive.locate(self.port, proc, element, self.clock)
                x, y = int(loc.hit.x), int(loc.hit.y)
                send({"op": "ui", "action": "click_at", "target": f"{proc}|{x},{y}", "text": ""})
            routes.append(cl.Route("coordinates", pixels, guard=lambda: self._can_click_pixels(proc, element, h)))
        elif act in ("type", "paste"):
            if act == "type":
                routes.append(cl.Route("uia", lambda: send(dict(body))))
                routes.append(cl.Route("refocus", lambda: (focus(), send(dict(body)))))
            paste_body = {"op": "ui", "action": "type_focused", "target": f"{proc}|", "text": str(action.get("text") or "")}
            routes.append(cl.Route("paste", lambda: (focus(), send(paste_body))) if act == "type" else cl.Route("uia", lambda: send(dict(body))))
            if act == "paste":
                routes.append(cl.Route("refocus", lambda: (focus(), send(dict(body)))))
        elif act == "key":
            routes.append(cl.Route("uia", lambda: send(dict(body))))
            menu = ladder.menu_for_key(str(action.get("key") or ""))
            if menu:
                routes.append(cl.Route("menu", lambda: self._via_menu(proc, menu, ctx, send)))
        else:
            routes.append(cl.Route("uia", lambda: send(dict(body))))
        if self.force_alt and len(routes) > 1:
            routes = routes[1:] + routes[:1]
            self.force_alt = False
        problem = {"click": "element_missing", "type": "typed_not_landed", "paste": "typed_not_landed", "key": "no_effect"}.get(act, "action")
        order = self._lesson_order(proc, problem, [r.name for r in routes])
        by = {r.name: r for r in routes}
        return [by[n] for n in order if n in by]

    def _via_menu(self, proc: str, menu: tuple[str, str], ctx: str | None, send) -> None:
        """The same thing through the menu bar instead of the shortcut (File > Save instead of ctrl+s)."""
        for el in menu:
            send({"op": "ui", "action": "click", "target": f"{proc}|{el}", "text": ""})
            self._sleep(0.4)

    def _can_click_pixels(self, proc: str, element: str, h: dict) -> bool:
        """A click from pixels needs the OCR line and the vision model to agree on the place (the cascade's rule), and never for a sensitive-looking label."""
        if not element or re.search(r"(?i)pay|delete|send|sign|install|buy|password", element):
            return False
        try:
            loc = perceive.locate(self.port, proc, element, self.clock)
        except Exception:  # noqa: BLE001
            return False
        h["located"] = loc
        return bool(loc.hit and loc.safe_to_click and loc.source != "uia")

    def _find_element(self, action: dict, body: dict, obs: perceive.Observation, h: dict, send) -> None:
        """Rung (c). The control was not found: wait for it by condition, scroll, open the menus; a command palette where the app has one. Each step re-tries the click.
        The lesson for this app puts the step that worked last time first; 'try another way' by voice skips the step under way."""
        proc = self._proc(obs.target) if obs.target else obs.app
        el = str(action.get("element") or "")
        self._say(app=proc, phase="recover", problem="element_missing")

        def clicked() -> bool:
            return bool(send(dict(body)).get("ok"))
        for step in self._lesson_order(proc, "element_missing", ["wait", "scroll", "expand_menu", "palette"]):
            if self._cancelled() or "skip" in self.pending_ctl:        # Stop, or "skip this step" by voice: stop looking
                return
            if "try_another" in self.pending_ctl:
                self.pending_ctl.remove("try_another")
                continue
            ok = False
            if step == "wait":
                got, _ = humanfeel.wait_for(clicked, timeout=WAIT_ELEMENT_S, clock=self.clock, sleep=self._sleep, stop=lambda: self._cancelled() or "skip" in self.pending_ctl)
                ok = bool(got)
            elif step == "scroll":
                for d in ("down", "up"):
                    self._job({"op": "ui", "action": "scroll", "target": f"{proc}|", "text": d})
                    self._sleep(0.3)
                    if clicked():
                        ok = True
                        break
            elif step == "expand_menu":
                for m in ("File", "Edit", "View"):
                    if self._job({"op": "ui", "action": "click", "target": f"{proc}|{m}", "text": ""}).get("ok"):
                        self._sleep(0.4)
                        if clicked():
                            ok = True
                            break
                        self._job({"op": "ui", "action": "key", "target": f"{proc}|", "text": "esc"})
            elif step == "palette":
                key = ladder.PALETTE_APPS.get(proc.lower())
                if key:
                    jobs = [{"op": "ui", "action": "key", "target": f"{proc}|", "text": key}, {"op": "ui", "action": "type_focused", "target": f"{proc}|", "text": el},
                            {"op": "ui", "action": "key", "target": f"{proc}|", "text": "enter"}]
                    got = [self._job(j) for j in jobs]
                    ok = all(g.get("ok") for g in got)
                    if ok:
                        h["res"] = {"ok": True, "state": "done", "observed": "palette"}
            self._learn(proc, "element_missing", step, ok)
            if ok:
                return

    def _verify(self, action: dict, obs: perceive.Observation, h: dict) -> cl.Verification:
        res = h.get("res")
        act, app = action["act"], str(action.get("app") or "")
        if self.interrupted:
            return cl.Verification(cl.UNKNOWN, "interrupted")
        if not res:
            return cl.Verification(cl.ABSENT, "nothing was sent")
        if res.get("state") == "waiting_for_permission":
            return cl.Verification(cl.UNKNOWN, "held")
        if not res.get("ok"):
            h["reason"] = str(res.get("reason") or "")
            return cl.Verification(cl.ABSENT, str(res.get("reason") or res.get("error") or "failed")[:60])
        if act in NO_VERIFY or (act == "key" and str(action.get("key") or "").lower() in OPAQUE_KEYS):
            return cl.Verification(cl.VERIFIED, "sent")
        if act in ("type", "paste"):
            if res.get("observed"):
                return cl.Verification(cl.VERIFIED, "read back by the node")
            after = self.observe(app or obs.app, "check")
            snippet = " ".join(str(action.get("text") or "").split())[-40:]
            text = " ".join(after.text.split())
            h["after"] = after
            if not after.text:
                h["unobserved"] = True
                return cl.Verification(cl.VERIFIED, "the field cannot be read")
            if snippet and snippet in text:
                return cl.Verification(cl.VERIFIED, "the text is in the window")
            if snippet and snippet[:8] and snippet[:8] in text:
                return cl.Verification(cl.PARTIAL, "only part of the text is there")
            return cl.Verification(cl.ABSENT, "the text is not there")
        after = self.observe(app or obs.app, "none")
        h["after"] = after
        return cl.Verification(cl.VERIFIED, "the screen changed") if after.signature() != obs.signature() else cl.Verification(cl.UNKNOWN, "no visible change")

    def do_action(self, n: int, action: dict, body: dict, obs: perceive.Observation) -> tuple[str, dict]:
        """One action through the closed loop. Returns (outcome, details): verified | unconfirmed | failed | asked."""
        act = action["act"]
        h: dict = {}
        routes = self._routes(action, body, obs, h)
        step = cl.Step(f"a{n}", f"{act} in {action.get('app') or 'the window'}", routes, lambda: self._verify(action, obs, h), idempotent=act in ("scroll",),
                       budget_s=60.0, settle_s=0.0 if act in NO_VERIFY or act in ("type", "paste") else self.settle_s, poll_s=0.6,
                       max_attempts=3 if act in ("type", "paste", "click") else 2, keep_order=True)
        sr = cl.run_step(step, clock=self.clock, sleep=self._sleep, cancelled=self._cancelled)
        h["sr"], h["routes"] = sr, routes
        if sr.status == cl.CANCELLED:
            raise _End(self._cancel_result())
        proc = self._proc(obs.target) if obs.target else str(action.get("app") or obs.app)
        if sr.status == cl.OK:
            if sr.retried or (sr.route_used and sr.route_used != routes[0].name):
                problem = {"click": "element_missing", "type": "typed_not_landed", "paste": "typed_not_landed", "key": "no_effect"}.get(act, "action")
                self._learn(proc, problem, sr.route_used if sr.route_used in lessons_mod.ROUTES else "uia", True)
            return "verified", h
        last = sr.attempts[-1] if sr.attempts else None
        res = h.get("res") or {}
        if res.get("ok") and last is not None and last.verification in (cl.UNKNOWN, cl.PARTIAL) and act in ("click", "key"):
            return "unconfirmed", h                                      # the node did it; nothing on screen shows it
        for a in sr.attempts:
            if a.route in lessons_mod.ROUTES and a.verification == cl.ABSENT:
                self._learn(proc, {"click": "element_missing", "type": "typed_not_landed", "paste": "typed_not_landed", "key": "no_effect"}.get(act, "action"), a.route, False)
        return "failed", h

    # ---------------------------------------------------------------------------------------------------------------------------- the loop
    def run(self, goal: str, task: str = "", app: str = "") -> dict:
        self.goal = goal or self.goal
        task = task or goal
        self.state.begin(self.task_id, perceive.norm_app(app), 0)
        try:
            return self._run(task, app)
        except _End as e:
            return e.result
        except Exception as e:  # noqa: BLE001 - the loop never crashes the coordinator: a plain failed result, the class of the error only
            return self._result("failed", False, error=f"the control loop stopped unexpectedly ({type(e).__name__})")

    def _run(self, task: str, app_hint: str) -> dict:
        for n in range(1, self.budget + 1):
            if self.clock() - self.t0 > self.deadline_s:
                return self._result("budget", False, error="the task ran out of time")
            if self._cancelled():
                return self._cancel_result()
            skip_next = "skip" in self.pending_ctl                      # "skip this step" by voice: the step the loop would take next is not taken
            if skip_next:
                self.pending_ctl.remove("skip")
            self._say(phase="perceive")
            obs = self.observe(app_hint)
            obs = self._owner_gate(obs)
            obs = self.preflight(obs)
            self._say(phase="decide", app=obs.app)
            view = self._view(obs)
            hist = [dict(x) for x in self.history[-8:]]
            if self.force_alt or "try_another" in self.pending_ctl:
                if "try_another" in self.pending_ctl:
                    self.pending_ctl.remove("try_another")
                    self.force_alt = True
                hist.append({"note": "the owner asked for another way: choose a different control or route than the last attempt"})
            try:
                action = self.propose(task, view, hist)
            except drive.DriveError as e:
                return self._result("failed", False, error=f"the next step could not be chosen ({e})")
            if not isinstance(action, dict) or action.get("act") not in drive.ACTS:
                return self._result("failed", False, error="the model's next step was not an action I know")
            if action["act"] == "ask":
                raise self._ask(str(action.get("question") or "What should I do next?")[:200], ["Carry on", "Stop here"], why="asked", reason="I need your answer to go on.")
            if action["act"] == "done":
                return self._finish(action, obs)
            if skip_next:
                self.history.append({"n": n, "act": action["act"], "app": str(action.get("app") or "")[:40], "ok": True, "effect": "skipped_by_owner"})
                self.unverified.append("a step was skipped because the owner said so")
                continue
            try:
                body = drive.to_body(action, self.typed)
            except drive.DriveError as e:
                return self._result("failed", False, error=f"the model's next step was refused before it was sent ({e})")
            expect, said = body.pop("_expect", None), body.pop("_typed", None)
            sig = json.dumps([body.get("op"), body.get("action"), body.get("target"), body.get("text")], sort_keys=True)
            if self.replan_sig == sig:                                   # (f) the re-plan chose the very same step again: only the owner can help now
                raise self._ask("My last action had no visible effect, and another way did not help either. What should I do?", ["Try another way", "I did it myself, carry on", "Stop here"],
                                why="no_progress", reason="Another control, another route and a new plan did not help.", problem="no_effect")
            key = ladder.sig(sig, obs.signature())
            self.seen[key] = self.seen.get(key, 0) + 1
            if self.seen[key] >= 3:                                    # (g) the same step on the same screen, three times: stop
                self.problems.append("loop") if "loop" not in self.problems else None
                return self._result("failed", False, error="the same step on the same screen came up three times, so I stopped instead of repeating it", why="loop")
            aapp = str(action.get("app") or "")
            if action["act"] in WINDOW_ACTS and aapp:
                obs = self.ensure_window(aapp, obs)                       # (a)
            if body["op"] == "ui" and body.get("action") in ("click", "click_at", "key"):
                ctx = drive.dialog_ctx({"dialogs": obs.dialogs}, aapp, action.get("element", "") if action["act"] == "click" else "")
                if ctx is not None:
                    body["ctx"] = ctx
                    if self.classify is not None and ctx != drive.UNREADABLE:
                        cls = self._classify(ctx, aapp)
                        if cls in ("B", "C") and not self._granted(ctx):
                            raise self._ask(f"This dialog could be consequential (class {cls}). Should I go ahead with it?", ["Go ahead", "Stop here"], why="dialog_ask",
                                            reason="Only you decide class B and C dialogs.", problem="dialog")
            self._say(act=action["act"], app=aapp, phase="act")
            outcome, h = self.do_action(n, action, body, obs)
            res = h.get("res") or {}
            entry = {"n": n, "act": action["act"], "app": aapp[:40], "ok": bool(res.get("ok")), "state": res.get("state"),
                     "effect": {"verified": "changed", "unconfirmed": "no_visible_change", "failed": "failed"}.get(outcome, outcome)}
            if h.get("sr") is not None and h["sr"].route_used and h["sr"].route_used != "uia":
                entry["route"] = h["sr"].route_used
            self.history.append(entry)
            self.watch.acted()
            if res.get("state") == "waiting_for_permission":
                return self._result("waiting_for_permission", False, approval_id=res.get("approval_id"), how=res.get("how"), state="waiting_for_permission")
            if "skip" in self.pending_ctl and outcome == "failed":      # he said "skip this step" while it was struggling with it
                self.pending_ctl.remove("skip")
                self.history[-1]["effect"] = "skipped_by_owner"
                self.unverified.append("a step was skipped because the owner said so")
                continue
            if outcome == "failed":
                self._on_failure(n, action, sig, res, h)                  # (e)/(f)/(h): changes strategy, re-plans, asks; returns when the loop may go on
                continue
            self.failed_sig.pop(sig, None)
            if outcome == "unconfirmed":
                c = self.no_effect[sig] = self.no_effect.get(sig, 0) + 1
                self.unverified.append(f"{action['act']} was sent; nothing on screen showed its effect")
                if c >= 2:                                                # (f) no effect twice: another control or route, then a re-plan, then a question
                    self.problems.append("no_effect") if "no_effect" not in self.problems else None
                    if self._alternate_strategy(action, obs, h):
                        self.no_effect[sig] = 0
                        outcome = "verified"
                    else:
                        self.replan_sig = sig
                        self.force_alt = True
                        self.history.append({"n": n, "act": "note", "effect": "no_effect_twice", "note": "that had no visible effect twice, even another way; choose another way or another control"})
            else:
                self.replan_sig = ""
            self._account(action, body, res, outcome, said, expect, h)
        return self._result("budget", False, error=f"the task was not finished within {self.budget} steps")

    def _alternate_strategy(self, action: dict, obs: perceive.Observation, h: dict) -> bool:
        """(f) The action sent fine but showed no effect, twice: try a route not tried yet (the menu instead of the shortcut, the shortcut instead of the button, pixels that two
        rungs agree on). True when one of them changed the screen."""
        proc = self._proc(obs.target) if obs.target else obs.app
        done = {a.route for a in (h.get("sr").attempts if h.get("sr") else [])}
        for r in h.get("routes") or []:
            if r.name in done or self._cancelled():
                continue
            if r.guard is not None and not r.guard():
                continue
            self._say(phase="recover", problem="no_effect")
            try:
                r.act()
            except _End:
                raise
            except Exception:  # noqa: BLE001
                self._learn(proc, "no_effect", r.name if r.name in lessons_mod.ROUTES else "keyboard", False)
                continue
            v = self._verify(action, obs, h)
            ok = v.status == cl.VERIFIED
            self._learn(proc, "no_effect", r.name if r.name in lessons_mod.ROUTES else "keyboard", ok)
            if ok:
                return True
        return False

    def _on_failure(self, n: int, action: dict, sig: str, res: dict, h: dict) -> None:
        """An action failed on every route. Classify by the node's fixed reason code and respond; raises _End when only the owner can help."""
        reason = str(res.get("reason") or "")
        err = str(res.get("error") or "")
        act = action["act"]
        c = self.failed_sig[sig] = self.failed_sig.get(sig, 0) + 1
        problem = "typed_not_landed" if act in ("type", "paste") else "element_missing" if reason == "no element" else "wrong_window" if "foreground" in reason else \
            "no_window" if reason == "no window" else "action"
        if problem not in self.problems:
            self.problems.append(problem)
        self._say(phase="recover", problem=problem)
        if reason == "password field" or re.search(r"(?i)refused", err):
            raise self._ask(f"I may not do that step ({err[:80] or 'it is refused'}). What should I do?", ["Skip it", "Stop here"], why="error", reason="The PC refused it.")
        if re.search(r"(?i)app not in|no such app|launch allowlist", err):
            alts = ladder.alternatives_for(str(action.get("app") or ""))
            raise self._ask(f"{action.get('app')} is not available on this PC.", ([f"Use {alts[0]} instead"] if alts else []) + ["Install it (needs your approval)", "Stop here"], why="resource",
                            reason="Installing software is always your decision.", problem="resource")
        if c == 1:                                                         # strategy change already happened inside the closed loop; now the model chooses again
            self.force_alt = True
            self.history.append({"n": n, "act": "note", "effect": "failed_all_routes", "note": "that action failed by every route; choose a different control or another way"})
            return
        raise self._ask("I tried that step another way and it still did not work. What should I do?", ["Try another way", "Skip this step", "Stop here"], why="no_progress",
                        reason="Two routes and a new plan did not work.", problem=problem)

    def _account(self, action: dict, body: dict, res: dict, outcome: str, said: str | None, expect: str | None, h: dict) -> None:
        """What was VERIFIED goes into facts, the rest into unverified (as drive.Driver did)."""
        act = action["act"]
        if said is not None and (body.get("action") == "type_focused" or str(body.get("target", "")).endswith("|*")):
            k = str(action.get("app") or "").lower()
            self.typed[k] = (self.typed.get(k, "") + said) if body.get("action") == "type_focused" else said
        if act == "open_app":
            (self.facts if res.get("verified") == "verified" else self.unverified).append(
                f"a window for {action.get('app')} appeared" if res.get("verified") == "verified" else f"{action.get('app')} was started but its window was not seen")
        elif act in ("type", "paste"):
            ok = bool(res.get("observed")) or (outcome == "verified" and not h.get("unobserved"))
            (self.facts if ok else self.unverified).append("the typed text was read back from the field" if ok else "the typed text could not be read back")
        elif act == "save_as":
            if res.get("file_ok"):
                self.done_effects.add("saved")
                self.facts.append("the file was found on disk after Save" + (" and its text is what was typed" if res.get("content_ok") is True else ""))
                if res.get("title_ok") is False:
                    self.unverified.append("the window title did not show the file name")
            else:
                self.unverified.append("the save was not confirmed on disk")
        elif act == "check_file":
            names = [str(i.get("name", "")).rstrip("\\").lower() for i in res.get("items") or [] if isinstance(i, dict)]
            if expect and expect.lower() in names:
                self.done_effects.update(("file_in_folder", "saved"))
                self.facts.append("the file is in the folder (listed by the PC)")
            else:
                raise _End(self._result("failed", False, error="the file is not in the folder"))
        elif outcome == "verified" and act in ("click", "key") and h.get("sr") is not None and h["sr"].route_used:
            self.unverified.append(f"{act} was sent; the screen changed afterwards")
        else:
            self.unverified.append(f"{act} was sent; its effect is checked by what the next view shows")

    def _finish(self, action: dict, obs: perceive.Observation) -> dict:
        claim = str(action.get("result") or "").strip()
        if claim:
            if claim.lower() in obs.text.lower():
                self.facts.append(f"the window shows {claim[:60]}")
            else:
                self.unverified.append(f"the result {claim[:60]} was claimed but is not visible in the window")
        missing = sorted(drive.expected_effects(self.goal) - self.done_effects)
        if missing:
            return self._result("failed", False, error="the task was reported done, but the expected effect was not found: " + ", ".join(missing))
        if not self.facts:
            self.unverified.append("nothing could be checked on the PC; the steps were sent")
        return self._result("completed", True)
