"""Takeover: JARVIS hands a screen to the owner for one step it must never do itself, then takes it back (D79, 2026-10-27).

Owner: "When I get a CAPTCHA, hand over the control of the VM to me, the whole display, I will complete it; same on my PC." One concept, two targets, raised by any engine:

    request  {target: pc | vm2, kind (a closed list), the site's host, one plain line made from the kind and the host}. NEVER page text, a title, a screenshot or an address.
    (a)      all JARVIS input to that target STOPS first. vm2: the lab's own `take control` (every process of the agent user is frozen, and the answer is CHECKED: mode owner and
             no agent task still running). pc: the D65 owner-takeover pause (the engine waits, `holding('pc')` is true, a browser action is refused meanwhile).
    (b)      the owner gets ONE card ("Needs you: solve the CAPTCHA on <host>") in the ask panel and ONE quiet notice in the inbox (no push sound while the face is hidden: queued).
    (c)      "Open VM2 desktop" opens the viewer address FROM THE CONFIGURATION FILE in his default browser. No password, token or key is ever in that address; the viewer's own
             password is his and JARVIS never reads it. Nothing a page, a model or a message says can change the address.
    (d)      he does the step and presses "Done, continue" (or says "done" / "continue": a voice word can only mean continue, never approve anything).
    (e)      JARVIS takes a FRESH look. Wall still there: it says so once and waits again, at most `max_reasks` (3) times, then the task pauses with a plain report. Wall gone: it
             takes the screen back (vm2: `continue task`) and goes on from the last verified milestone.
    (f)      not answered within `takeover_minutes` (30): the task pauses DURABLY (missions.resume) instead of failing.
    (g)      JARVIS never solves, skips or works around the wall, never uses a solving service, never fakes a human signal: there is no code path here that touches the page.
    (h)      one content-free journal line per takeover: kind, host class, target, how long, how it ended.

The broker knows nothing about Chrome, Firefox or Windows: engines give it a `look()` that says whether the wall is still there, and a target that can freeze and resume.
Standard library only; every effect (the card, the notice, the viewer, the journal, the clock) is injected so the tests run without a screen.
"""
from __future__ import annotations

import json
import os
import re
import secrets
import threading
import time
import webbrowser
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from .vm2 import Frozen, Vm2Config, Vm2Port

KINDS = {
    "captcha": "solve the CAPTCHA", "bot_check": "pass the bot check", "two_factor": "enter the verification code", "email_code": "confirm the e-mail code",
    "login": "sign in", "payment": "handle the payment page", "consent": "answer the cookie or consent box", "uac": "answer the Windows permission prompt",
    "dialog": "answer a dialog JARVIS cannot read", "other": "do one step",
}
WALL_KIND = {"captcha": "captcha", "two_factor": "two_factor", "email_code": "email_code", "payment": "payment", "signin": "login", "paywall": "other"}
HOST_RX = re.compile(r"^[a-z0-9]([a-z0-9.-]{0,78}[a-z0-9])?$")
CONTINUE_RX = re.compile(r"(?i)^\s*(?:ok(?:ay)?[, ]+)?(?:i(?:'m| am)? )?(?:done|continue|carry on|go on|go ahead and continue|it(?:'s| is) done|finished|all done)\s*[.!]?\s*$")
OUTCOMES = ("resolved", "stop", "paused", "exhausted")


@dataclass(frozen=True)
class Request:
    id: str
    target: str            # pc | vm2
    kind: str
    host: str
    reason: str

    @property
    def line(self) -> str:
        return "Needs you: " + KINDS[self.kind] + (f" on {self.host}" if self.host else "")

    @property
    def hud(self) -> str:
        """The strip's sentence: fixed words only (the HUD line allows letters, digits, spaces and . , ( ) ' -)."""
        return "I need you - " + KINDS[self.kind] + (" (VM2)" if self.target == "vm2" else "")


def clean_host(host: Any) -> str:
    h = str(host or "").strip().lower()
    return h if HOST_RX.match(h) else ""


def make_request(target: str, kind: str, host: Any = "", req_id: str = "") -> Request:
    """A request made only from a closed target, a closed kind and a plain host name. The one-line reason is built here from those: page text cannot reach it."""
    target = target if target in ("pc", "vm2") else "pc"
    kind = kind if kind in KINDS else "other"
    host = clean_host(host)
    where = "VM2" if target == "vm2" else "your PC"
    return Request(req_id or "tk-" + secrets.token_hex(4), target, kind, host, f"JARVIS needs you to {KINDS[kind]}" + (f" on {host}" if host else "") + f", on {where}")


def wall_kind(wall: Any) -> str:
    """The takeover kind for a browser-agent Wall (kind + why). A bot check is a captcha wall whose reason speaks of robots."""
    k = WALL_KIND.get(getattr(wall, "kind", ""), "other")
    why = str(getattr(wall, "why", "")).lower()
    if k == "captcha" and re.search(r"robot|bot check|human", why) and "captcha" not in why:
        return "bot_check"
    return k


# ---------------------------------------------------------------------------------------------------------------------- files
def _data() -> Path:
    from jarvis.pluginapi.paths import data_dir
    return data_dir() / "plugins"


class State:
    """`takeover_state.json`: whether a takeover waits, content-free (a closed kind, the target, the time). Read by the voice hook and the HUD."""

    def __init__(self, path: Path | None = None, clock: Callable[[], float] = time.time):
        self.path = Path(path) if path else _data() / "takeover_state.json"
        self.clock = clock

    def _write(self, d: dict) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(d), encoding="utf-8")
            os.replace(tmp, self.path)
        except OSError:
            pass

    def begin(self, req: Request) -> None:
        self._write({"v": 1, "pending": True, "id": req.id, "target": req.target, "kind": req.kind, "since": self.clock()})

    def end(self, outcome: str) -> None:
        self._write({"v": 1, "pending": False, "outcome": outcome if outcome in OUTCOMES else "", "at": self.clock()})

    def read(self) -> dict:
        try:
            d = json.loads(self.path.read_text(encoding="utf-8"))
            return d if isinstance(d, dict) else {}
        except (OSError, ValueError):
            return {}

    def pending(self) -> bool:
        return self.read().get("pending") is True


class Signal:
    """The owner's "continue" by voice, from the quick-command hook to the waiting broker (a small file; only the word 'continue' travels)."""

    def __init__(self, path: Path | None = None, clock: Callable[[], float] = time.time):
        self.path = Path(path) if path else _data() / "takeover_signal.json"
        self.clock = clock

    def post(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps({"v": 1, "continue_at": self.clock()}), encoding="utf-8")
        except OSError:
            pass

    def take(self) -> bool:
        try:
            self.path.read_text(encoding="utf-8")
        except OSError:
            return False
        try:
            self.path.unlink()
        except OSError:
            return False
        return True


def voice_continue(text: str, state: State | None = None, signal: Signal | None = None) -> str | None:
    """The owner said 'done' / 'continue' while a takeover waits: post the signal and answer. None when no takeover waits or the words are anything else (the message then goes on
    to the other hooks). A message that carries a PIN, a code, a password or the word approve is NEVER a continue: voice can only mean continue."""
    t = " ".join(str(text or "").split())[:80]
    if not CONTINUE_RX.match(t):
        return None
    if not (state or State()).pending():
        return None
    (signal or Signal()).post()
    return "Carrying on. I will look at the screen again first."


# ---------------------------------------------------------------------------------------------------------------------- targets
class Target:
    name = ""
    look_after_resume = False            # vm2: the agent has to run again to look at its own screen

    def freeze(self) -> Frozen:
        raise NotImplementedError

    def resume(self) -> bool:
        raise NotImplementedError

    def notes(self) -> list:
        return []


class Vm2Target(Target):
    name = "vm2"
    look_after_resume = True

    def __init__(self, port: Vm2Port):
        self.port = port

    def freeze(self) -> Frozen:
        return self.port.take()

    def resume(self) -> bool:
        return self.port.resume()


class PcTarget(Target):
    """The owner's own PC: the engine waits (D65 owner-takeover pause). `front()` brings the relevant window forward, but never while he is typing: `typing()` says so."""
    name = "pc"

    def __init__(self, state: Any = None, front: Callable[[], Any] | None = None, typing: Callable[[], bool] | None = None):
        self.state, self.front, self.typing = state, front, typing
        self.fronted = False

    def freeze(self) -> Frozen:
        if self.state is not None:
            try:
                self.state.update(state="needs_owner", phase="wait", why="takeover")
            except Exception:  # noqa: BLE001 - the state file is a courtesy
                pass
        self.fronted = False
        if self.front is not None:
            try:
                if self.typing is not None and self.typing():
                    return Frozen(True, 0, "he was typing, so the window was not brought forward")
                self.front()
                self.fronted = True
            except Exception:  # noqa: BLE001
                return Frozen(True, 0, "the window could not be brought forward")
        return Frozen(True, 0, "")

    def resume(self) -> bool:
        if self.state is not None:
            try:
                self.state.update(state="running", phase="perceive", why="")
            except Exception:  # noqa: BLE001
                pass
        return True


# ---------------------------------------------------------------------------------------------------------------------- the broker
@dataclass
class Outcome:
    status: str                     # resolved | stop | paused | exhausted
    reasks: int = 0
    seconds: float = 0.0
    detail: str = ""                # a plain sentence for the report
    request: Request | None = None
    frozen: bool = True             # is the target still held by the owner (vm2: still frozen)

    @property
    def ok(self) -> bool:
        return self.status == "resolved"


def default_opener(url: str) -> bool:
    return bool(webbrowser.open(url, new=1))


def default_notify(notice: dict, quiet: bool) -> None:
    """The existing inbox (muse/notify.py): the full line stays on this PC; a phone ping carries no content and is skipped while the face is hidden (it stays queued in the inbox)."""
    try:
        from jarvis.muse.notify import Inbox, Notifier, NtfyPush
        from jarvis.pluginapi.paths import data_dir
        topic = None
        if not quiet:
            from jarvis.tools.doctor import default_secrets_dir
            cand = default_secrets_dir() / "ntfy.topic"
            topic = cand if cand.exists() else None
        Notifier(Inbox(data_dir() / "muse" / "inbox.jsonl"), NtfyPush(topic) if topic else None).deliver([notice])
    except Exception:  # noqa: BLE001 - a notice that cannot be filed never stops the takeover; the card is the main road
        pass


def face_hidden() -> bool:
    """Is the face hidden (D78)? The hide switch is not in this branch; when it lands it writes `<data>/state/face_hidden.json` {"hidden": true}. A missing file is 'not hidden'."""
    try:
        from jarvis.pluginapi.paths import data_dir
        return json.loads((data_dir() / "state" / "face_hidden.json").read_text(encoding="utf-8")).get("hidden") is True
    except (OSError, ValueError, AttributeError):
        return False


class Takeover:
    def __init__(self, targets: dict[str, Target], *, ask: Callable[[dict], dict], config: Vm2Config | None = None, notify: Callable[[dict, bool], Any] | None = None,
                 opener: Callable[[str], Any] | None = None, journal: Callable[[dict], Any] | None = None, state: State | None = None, signal: Signal | None = None,
                 hidden: Callable[[], bool] | None = None, stop: Callable[[], bool] | None = None, clock: Callable[[], float] = time.monotonic,
                 sleep: Callable[[float], Any] = time.sleep, grants_host: Callable[[str], bool] | None = None):
        self.targets, self.ask = targets, ask
        self.cfg = config or Vm2Config()
        self.notify, self.opener, self.journal = notify, opener or default_opener, journal
        self.state, self.signal = state or State(), signal or Signal()
        self.hidden = hidden or (lambda: False)
        self.stop = stop or (lambda: False)
        self.clock, self.sleep = clock, sleep
        self.grants_host = grants_host
        self.holds: dict[str, bool] = {}
        self.history: list[Outcome] = []

    # ---- who is held right now
    def holding(self, target: str) -> bool:
        return bool(self.holds.get(target))

    def host_class(self, host: str) -> str:
        if not host:
            return "none"
        if self.grants_host is not None:
            try:
                return "granted" if self.grants_host(host) else "other"
            except Exception:  # noqa: BLE001
                return "other"
        return "other"

    def _log(self, req: Request, outcome: str, seconds: float, reasks: int) -> None:
        if self.journal is None:
            return
        try:
            self.journal({"kind": "takeover", "wall": req.kind, "target": req.target, "host_class": self.host_class(req.host), "ms": int(seconds * 1000),
                          "outcome": outcome, "reasks": reasks})
        except Exception:  # noqa: BLE001 - the recorder never breaks a takeover
            pass

    # ---- the card
    def card(self, req: Request, *, opened: bool, still: bool, frozen: Frozen, reasks: int) -> dict:
        where = "the VM2 desktop" if req.target == "vm2" else "your PC"
        if still:
            intro = f"It is still there. Please finish it on {where}, then press Done. JARVIS does not solve or skip it."
        else:
            intro = f"JARVIS stopped. Please {KINDS[req.kind]} on {where}, then press Done. JARVIS does not solve or skip it."
        rows = [["Where", where], ["Site", req.host or "-"], ["What", KINDS[req.kind]], ["JARVIS", "stopped: it will not click or type until you press Done"]]
        fields: list = [{"type": "info_table", "label": "What JARVIS sees", "rows": rows}]
        if not frozen.ok:
            fields.append({"type": "callout", "level": "warn", "text": "I could not pause the VM2 agent (" + (frozen.detail or "no answer") + "). Take the screen from the VM2 hub first."})
        elif frozen.detail:
            fields.append({"type": "callout", "level": "info", "text": frozen.detail})
        buttons = []
        if req.target == "vm2" and self.cfg.has_viewer:
            buttons.append({"id": "open_viewer", "label": "Open VM2 desktop", "style": "secondary" if opened else "primary"})
        buttons.append({"id": "done", "label": "Done, continue", "style": "primary" if (opened or req.target != "vm2" or not self.cfg.has_viewer) else "secondary"})
        buttons.append({"id": "later", "label": "Not now", "style": "secondary"})
        buttons.append({"id": "stop", "label": "Stop", "style": "secondary"})
        return {"title": "JARVIS needs you for one step", "intro": intro, "fields": fields, "buttons": buttons[:4]}

    def _wait_answer(self, spec: dict, timeout: float) -> dict:
        spec = dict(spec, timeout_s=int(min(150, max(5, timeout))))
        box: dict = {}

        def run() -> None:
            try:
                box["r"] = self.ask(spec)
            except Exception:  # noqa: BLE001
                box["r"] = {"answered": False, "reason": "error"}
        th = threading.Thread(target=run, daemon=True)
        th.start()
        while th.is_alive():
            th.join(0.05)
            if self.signal.take():
                return {"answered": True, "button": "done", "via": "voice"}
            if self.stop():
                return {"answered": True, "button": "stop"}
        r = box.get("r")
        return r if isinstance(r, dict) else {"answered": False, "reason": "error"}

    # ---- the whole handover
    def hand_over(self, req: Request, look: Callable[[], Any]) -> Outcome:
        """Freeze, ask, wait, look again. `look()` returns None when the wall is gone, or anything truthy when it is still there."""
        t0 = self.clock()
        target = self.targets[req.target]
        self.signal.take()                                        # a "continue" said before this takeover began is not an answer to it
        frozen = target.freeze()
        self.holds[req.target] = True
        self.state.begin(req)
        notice = {"kind": "takeover", "summary": req.line, "key": req.id, "reason": req.reason, "quiet": bool(self.hidden())}
        if self.notify is not None:
            try:
                self.notify(notice, bool(self.hidden()))
            except Exception:  # noqa: BLE001
                pass
        self._log(req, "raised", 0.0, 0)
        deadline = t0 + self.cfg.takeover_minutes * 60.0
        reasks, opened, still = 0, False, False
        out = Outcome("paused", request=req)
        spins = 0
        try:
            while True:
                spins += 1
                if spins > 20000:
                    out = Outcome("paused", reasks, detail="the wait loop gave up", request=req)
                    break
                left = deadline - self.clock()
                if left <= 0:
                    out = Outcome("paused", reasks, detail=f"you did not answer within {int(self.cfg.takeover_minutes)} minutes, so the task is paused and can be continued", request=req)
                    break
                ans = self._wait_answer(self.card(req, opened=opened, still=still, frozen=frozen, reasks=reasks), left)
                if not ans.get("answered"):
                    if ans.get("reason") == "unavailable":      # no panel to ask in: the notice is out, the task pauses now instead of waiting for nobody
                        out = Outcome("paused", reasks, detail="there was no panel to ask in, so the task is paused until you continue it", request=req)
                        break
                    if self.signal.take():
                        ans = {"answered": True, "button": "done"}
                    else:
                        self.sleep(min(2.0, max(0.0, left)))
                        continue
                btn = str(ans.get("button") or "")
                if btn == "open_viewer":
                    opened = self._open_viewer(req) or opened
                    continue
                if btn == "stop":
                    out = Outcome("stop", reasks, detail="you chose to stop", request=req)
                    break
                if btn == "later":
                    out = Outcome("paused", reasks, detail="you chose not now, so the task is paused and can be continued", request=req)
                    break
                if btn != "done":
                    continue
                # ---- the fresh look
                resumed = False
                if target.look_after_resume:
                    resumed = target.resume()
                    if not resumed:
                        out = Outcome("paused", reasks, detail="I could not hand the desktop back to the agents; check the VM2 hub and continue", request=req)
                        break
                    self.holds[req.target] = False
                wall = look()
                if wall is None:
                    if not target.look_after_resume and not target.resume():
                        out = Outcome("paused", reasks, detail="the step is done but I could not restart the work; continue the task", request=req)
                        break
                    self.holds[req.target] = False
                    out = Outcome("resolved", reasks, detail="the wall is gone; carrying on", request=req, frozen=False)
                    break
                reasks += 1
                if resumed:                                      # vm2: the agents ran to look and found it again: stop them again before asking
                    frozen = target.freeze()
                    self.holds[req.target] = True
                if reasks > self.cfg.max_reasks:
                    out = Outcome("exhausted", reasks - 1, detail=f"the {KINDS[req.kind].replace('solve the ', '').replace('pass the ', '')} is still there after {self.cfg.max_reasks} more tries, so the task is paused", request=req)
                    break
                still = True
        finally:
            secs = self.clock() - t0
            out.seconds = secs
            if req.target == "pc" or out.status == "resolved":
                self.holds[req.target] = False                   # the PC engine is not running any more; a resolved vm2 handover gave the desktop back
            out.frozen = bool(self.holds.get(req.target))        # vm2 after a pause or a stop: the agents stay frozen (the owner presses Continue task in the hub, or continues the task)
            self.state.end(out.status)
            self._log(req, out.status, secs, out.reasks)
            self.history.append(out)
        return out

    def _open_viewer(self, req: Request) -> bool:
        """Open the viewer for the owner. The address comes from the configuration file only (never from the request, a page or a model)."""
        if req.target != "vm2" or not self.cfg.has_viewer:
            return False
        url = self.cfg.viewer_open_url(view_only=False)
        try:
            self.opener(url)
        except Exception:  # noqa: BLE001
            return False
        return True

    def watch(self) -> bool:
        """Open the viewer to WATCH the agents (view only). Same rules; no takeover needed."""
        if not self.cfg.has_viewer:
            return False
        try:
            self.opener(self.cfg.viewer_open_url(view_only=True))
        except Exception:  # noqa: BLE001
            return False
        return True
