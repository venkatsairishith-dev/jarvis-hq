"""JARVIS's hands on this PC, through the JARVIS kernel (logic; no the face imports, tested without servers).

Every action is one request to the JARVIS console's owner route (127.0.0.1:8799/api/owner/pc). The kernel decides: ordinary
actions run at once, consequential ones come back as "needs_approval" and wait for the owner's PIN in the console, refused
ones say why. The laptop node runs the job and reports what it actually observed. This module only asks and waits.

Privacy: screen text and page text are written by the node to outputs\\local-only on this laptop; take_local() reads such a
file once and deletes it. What goes back to the main agent (and so to its model) is a diagnosis written here, not the
screen text. Screen text itself leaves this plugin only when the owner set JARVIS_SCREEN_TEXT_TO_MODEL=1 AND the privacy gateway
confirmed it hid names, places and private details in it (jarvis/plugins/_shared/screen_text.py: release()); otherwise it is
withheld. File and directory names are never returned as names, only as a class ("a .json file").
"""
from __future__ import annotations

import json
import os
import re
import contextvars
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Callable

from jarvis.plugins._shared import screen_text
from jarvis.plugins._shared import loopback_http

CONSOLE = os.environ.get("JARVIS_CONSOLE_URL", "http://127.0.0.1:8799").rstrip("/")
DATA = Path(os.environ.get("JARVIS_DATA_DIR", r"D:\jarvis\data"))
ACTIVITY = DATA / "plugins" / "activity.json"
LOCAL_RESULTS = Path(os.environ.get("JARVIS_OUTPUTS", r"D:\jarvis\outputs")) / "local-only"
# The list the node builds from Chrome's profile list (browserext.REGISTRY, same default and same override). It was read from data\plugins\ while the node wrote
# data\jarvis\, so list_browser_profiles always answered "no profile" (found on the real laptop, 2026-10-25).
PROFILES = Path(os.environ.get("JARVIS_BROWSER_PROFILES") or DATA / "jarvis" / "browser_profiles.json")
FINAL = ("done", "failed", "cancelled", "expired")
STATES = ("inspecting", "diagnosing", "researching", "waiting_for_permission", "applying_fix", "verifying", "completed", "failed")


class DesktopError(Exception):
    pass


# D54 (task-scoped consent): the owner's OWN goal, as he said it. Every PC job of a task carries it (body["goal"]) and the kernel computes what the task
# may do without the PIN from these words alone (policy.grant_from_goal). A plan, a screen, a page or a file can never put words here: only the entry
# that received the owner's goal sets it, and the plan steps an online model wrote are not the goal.
OWNER_GOAL: "contextvars.ContextVar[str]" = contextvars.ContextVar("owner_goal", default="")
OWNER_UTTERANCE: "contextvars.ContextVar[str]" = contextvars.ContextVar("owner_utterance", default="")      # the owner's own sentence, when the host knows it (the face's model may have reworded the goal)


class NotSent(Exception):
    """Pc.submit: no request went out (not a failure of the console): the Pc was stopped, or its fence said the PC lock is no
    longer this task's. `reason` says which."""

    def __init__(self, reason: str = "stopped"):
        super().__init__(reason)
        self.reason = reason


def screen_text_allowed() -> bool:
    """The owner's switch. On does NOT mean "send the text": it means "the text may be offered to a model after it has been
    cleared" (see screen_text.release). Off (the default) means no screen text goes to any model."""
    return screen_text.flag_on()


# ---------------------------------------------------------------- the console's owner route
_META = re.compile(r'<meta\s+name="jarvis-console-secret"\s+content="([^"]+)"')
_SECRETS: dict[str, str] = {}


def _run_dir() -> Path:
    """The per-user folder where the JARVIS servers publish their 0600 token files (same rule as jarvis/scripts/control_auth.py)."""
    override = os.environ.get("JARVIS_RUN_DIR")
    if override:
        return Path(override)
    if os.name == "nt":
        return Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local") / "jarvis" / "run"
    xdg = os.environ.get("XDG_RUNTIME_DIR")
    return Path(xdg) / "jarvis" if xdg else Path.home() / ".jarvis" / "run"


def _console_secret(refresh: bool = False, timeout: float = 10.0) -> str:
    """The console's MASTER token (re-review M3, 2026-10-19). The console no longer puts any credential in `GET /`: it publishes the
    token to `console.token`, a 0600 file in the per-user run folder, and every request (reads too) sends it in X-Jarvis-Console.
    A file that other users could read, or that belongs to someone else, is not used. For a console that still serves its secret in
    its page (a test server, an older console) the page is read as before. A program of the same OS user can read the file: this
    blocks other users, other sites and programs that cannot read it, not malware running as the owner (the PIN is the boundary)."""
    if refresh or CONSOLE not in _SECRETS:
        token = ""
        try:
            f = _run_dir() / "console.token"
            st = f.stat()
            if os.name == "nt" or (not st.st_mode & 0o077 and st.st_uid == os.getuid()):
                token = f.read_text(encoding="ascii").strip()
        except (OSError, ValueError):
            token = ""
        if not token:
            req = urllib.request.Request(f"{CONSOLE}/", headers={"Accept": "text/html"})
            try:
                with loopback_http.urlopen(req, timeout=timeout) as r:
                    m = _META.search(r.read(200_000).decode("utf-8", "replace")) if r.status == 200 else None
            except urllib.error.HTTPError:
                m = None
            if not m:
                raise DesktopError("the JARVIS console token is not available: start the console (it writes console.token in the run folder), "
                                   "or this address is not the JARVIS console")
            token = m.group(1)
        _SECRETS[CONSOLE] = token
    return _SECRETS[CONSOLE]


def owner(method: str, name: str, body: dict | None = None, timeout: float = 20.0) -> tuple[int, dict]:
    if not re.match(r"^http://(127\.0\.0\.1|localhost)(:\d+)?$", CONSOLE):
        raise DesktopError("JARVIS_CONSOLE_URL must be plain http on a loopback address")
    data = json.dumps(body).encode() if body is not None else None
    write = method.upper() in ("POST", "DELETE")
    for attempt in (0, 1):
        # X-Jarvis-Console carries the console's master token: it refuses every request without it (writes AND private reads)
        try:
            secret = _console_secret(refresh=attempt == 1)
        except (urllib.error.URLError, OSError, TimeoutError) as e:
            raise DesktopError(f"the JARVIS console is not answering ({type(e).__name__}); is it running?") from None
        except DesktopError:
            if write:
                raise
            secret = ""                      # a read without a token still goes out (an older console allows it); a new one answers 401
        headers = {**({"X-Jarvis-Console": secret} if secret else {}), **({"Content-Type": "application/json"} if data else {})}
        req = urllib.request.Request(f"{CONSOLE}/api/owner/{name}", data=data, method=method, headers=headers)
        try:
            with loopback_http.urlopen(req, timeout=timeout) as r:
                status, text = r.status, r.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            try:
                reply = json.loads(e.read().decode("utf-8", "replace") or "{}")
            except ValueError:
                reply = None
            if attempt == 0 and ((e.code == 403 and write and isinstance(reply, dict) and reply.get("reload"))
                                 or (e.code == 401 and isinstance(reply, dict) and reply.get("login"))):
                continue                     # the console restarted since the token was read: read it again, once
            return e.code, reply if isinstance(reply, dict) else {"error": f"HTTP {e.code}"}
        except (urllib.error.URLError, OSError, TimeoutError) as e:
            raise DesktopError(f"the JARVIS console is not answering ({type(e).__name__}); is it running?") from None
        break
    try:                                     # something else on the console's port (a web server, a proxy) answers HTML
        reply = json.loads(text or "{}")
    except ValueError:
        reply = None
    if not isinstance(reply, dict):
        raise DesktopError(f"the JARVIS console is not answering properly (HTTP {status}, not a JSON object); "
                           "is something else using its port?")
    return status, reply


class Pc:
    """Ask, wait for approval when the kernel wants one, wait for the job, return what the node saw."""

    def __init__(self, call=owner, sleep=time.sleep, clock=time.time, on_state: Callable[[str, str], None] | None = None,
                 approval_wait_s: float = 300.0):
        self.call, self.sleep, self.clock = call, sleep, clock
        self.on_state = on_state or (lambda s, d: None)
        self.approval_wait_s = approval_wait_s
        self.current_job: str | None = None
        # Stop model (2026-10-18, review H3/M14): `stopped` is a flag AND an event, so a wait ends the moment Stop arrives
        # instead of at the next poll; a cancelled job the node has not confirmed is remembered in `unconfirmed_jobs`; the
        # approval being waited on is `pending_approval` (Stop denies it). `on_progress` lets the owning operation see that
        # this worker is alive (progress-based staleness).
        self._stop_evt = threading.Event()
        self._native_sleep = sleep is time.sleep
        self.on_progress: Callable[[str], None] | None = None
        # Fencing (completion A): called under the send gate before EVERY job request; False = this Pc's operation no longer holds
        # the PC lock (another task took it over, or the owner's recovery abandoned it), so nothing may be sent. A stale executor
        # can therefore never start a job for a task that is no longer the PC's one holder. on_job(job id) tells the owner of the
        # lock which job exists (it is written into the lock's heartbeat file, so a crashed holder's jobs can be cancelled).
        self.fence: Callable[[], bool] | None = None
        self.fence_reason = ""
        self.on_job: Callable[[str], None] | None = None
        # Fencing token (completion A): the lock holder's identity, sent with EVERY job request as body["holder"] =
        # {token, task, seq}. `seq` counts this Pc's requests, so a request delivered twice carries the same (token, seq). The
        # kernel refuses a request whose token is not the one in the PC lock file (when it can read that file) and answers a
        # repeated (token, seq) with the job it already made. None = no lock (read-only checks, tests): nothing is added.
        self.holder: Callable[[], "dict | None"] | None = None
        self._seq = 0
        self.on_approval: Callable[[str, str], None] | None = None     # ("pending"|"resolved", approval id): the owning operation tracks it
        self.goal = ""                                                 # the owner's goal for this task (OWNER_GOAL); sent with every job request
        self.pending_approval: str | None = None
        self.submitting = False
        self._cancel_lock = threading.Lock()
        self._cancel_sent: set[str] = set()
        self.unconfirmed_jobs: set[str] = set()
        self._denied: set[str] = set()
        self.settle_s = 30.0                  # how long a cancelled job may take to be confirmed ended by the node
        # The send gate (review M8): "not stopped" + "a request is about to go out" is ONE step under this lock, and recover()
        # reads `submitting` / `current_job` under the same lock, so a worker is either already sending (its job id is known
        # before the lock is let go, and must be confirmed) or can never send.
        self.gate = threading.Lock()
        # The console not answering while a job is followed (review M7): the job is NOT abandoned. It stays in
        # `unconfirmed_jobs` (nothing has confirmed it ended), the poll is retried with a growing pause for at most
        # `outage_wait_s` of pauses, and then the job is reported as unconfirmed (never done, never stopped).
        self.outage_wait_s, self.outage_poll_s, self.outage_poll_max_s = 60.0, 0.5, 10.0
        self.stopped = False

    @property
    def stopped(self) -> bool:
        return self._stop_evt.is_set()

    @stopped.setter
    def stopped(self, value: bool) -> None:
        if value:
            self._stop_evt.set()
        else:
            self._stop_evt.clear()

    def stop(self) -> None:
        self.stopped = True

    def touch(self, note: str = "") -> None:
        cb = self.on_progress
        if cb is not None:
            try:
                cb(note)
            except Exception:  # noqa: BLE001 - reporting progress never stops the work
                pass

    def _pause(self, seconds: float, wake_on_stop: bool = True) -> None:
        """Wait, but wake at once when Stop arrives (an injected sleep, as in tests, is called as it is). After Stop a poll
        loop that must go on (waiting for the node to confirm a cancel) uses wake_on_stop=False: a plain sleep."""
        if self._native_sleep and wake_on_stop and not self.stopped:
            self._stop_evt.wait(seconds)
        else:
            self.sleep(seconds)

    def submit(self, body: dict) -> dict:
        """Send one PC job. Raises NotSent when this Pc was stopped (nothing went out). The stop check and `submitting` are one
        step under `gate`; the job id is recorded as `current_job` before `submitting` is cleared, so there is no instant at
        which a job exists that neither flag names."""
        with self.gate:
            if self.stopped:
                raise NotSent()
            fence = self.fence
            if fence is not None:
                try:
                    holds = bool(fence())
                except Exception:  # noqa: BLE001 - a fence that cannot be asked is a fence that says no
                    holds = False
                if not holds:
                    self.fence_reason = "this task no longer holds the PC lock (another task took it over, or it was recovered)"
                    raise NotSent(self.fence_reason)
            if self.goal and "goal" not in body:
                body = dict(body, goal=self.goal[:1000])
            self.submitting = True        # while a request is on the wire the kernel may create a job whose id is not yet known
            hf = self.holder
            if hf is not None:
                try:
                    h = hf()
                except Exception:  # noqa: BLE001
                    h = None
                if h:
                    # an approved re-send of the same action is a new request (the first one asked for approval)
                    self._seq += 1
                    body = dict(body, holder=dict(h, seq=h.get("seq", self._seq)))      # the operation's own counter when it has one
        try:
            code, r = self.call("POST", "pc", body)
            if code < 400 and isinstance(r.get("job"), str) and r["job"]:
                with self.gate:
                    self.current_job = r["job"]
                cb = self.on_job
                if cb is not None:
                    try:
                        cb(r["job"])
                    except Exception:  # noqa: BLE001 - recording the job for crash recovery never stops the work
                        pass
        finally:
            with self.gate:
                self.submitting = False
        if code >= 400:
            raise DesktopError(str(r.get("error") or f"refused (HTTP {code})")[:300])
        return r

    def approval_pending(self, aid: str) -> bool:
        code, st = self.call("GET", "state", None)
        return code < 400 and any(a.get("id") == aid for a in st.get("approvals") or [])

    def wait_approval(self, aid: str, what: str) -> bool:
        """Wait (a few minutes at most) until the owner approved or denied it in the console. True = no longer pending.
        Stop ends the wait at once (the event wakes the sleep)."""
        self.on_state("waiting_for_permission", f"Approve in the JARVIS console with your PIN: {what}"[:200])
        end = self.clock() + self.approval_wait_s
        self.pending_approval = aid
        try:
            while self.clock() < end and not self.stopped:
                if not self.approval_pending(aid):
                    return True
                self.touch("waiting for the owner's approval")
                self._pause(3.0)
            return False
        finally:
            self.pending_approval = None

    def _approval(self, kind: str, aid: str) -> None:
        cb = self.on_approval
        if cb is not None:
            try:
                cb(kind, aid)
            except Exception:  # noqa: BLE001 - tracking never stops the work
                pass

    def deny_result(self, aid: str) -> str:
        """Deny the owner-route approval so it can never be approved or used (denying is the safe direction, no PIN).
        "denied" (now or before), "gone" (the kernel knows no pending approval of that id: already approved, used or expired;
        nothing to deny) or "unreachable" (the console did not answer)."""
        if aid in self._denied:
            return "denied"
        try:
            code, _ = self.call("POST", "deny", {"id": aid})
        except Exception:  # noqa: BLE001 - the console may be down: Stop still stops the wait
            return "unreachable"
        if code < 400:
            self._denied.add(aid)
            return "denied"
        return "gone" if code in (404, 409) else "unreachable"

    def deny_approval(self, aid: str) -> bool:
        """Best effort deny. True = it is denied (now or before); False = could not (already approved or used, expired, console
        down). Whatever the answer, the approval is no longer this Pc's to track unless the console did not answer."""
        res = self.deny_result(aid)
        if res != "unreachable":
            self._approval("resolved", aid)
        return res == "denied"

    def job_final(self, jid: str) -> bool:
        """Has the kernel recorded this job as ended (done, failed, cancelled, expired)? False when unknown."""
        try:
            code, j = self.call("GET", f"job/{jid}", None)
        except Exception:  # noqa: BLE001
            return False
        return code < 400 and j.get("state") in FINAL

    def cancel_once(self, jid: str) -> None:
        """Ask the kernel to cancel the job, once however many callers (the worker that noticed Stop, the Stop call itself)
        ask. A cancel that could not be sent leaves the job in `unconfirmed_jobs` and raises, so the next caller retries."""
        with self._cancel_lock:
            if jid in self._cancel_sent:
                return
            try:
                self.cancel(jid)
            except Exception:
                self.unconfirmed_jobs.add(jid)     # the cancel may not have reached the kernel
                raise
            self._cancel_sent.add(jid)

    _cancel_once = cancel_once

    def wait_job(self, jid: str, timeout: float) -> dict:
        """Follow a job to its end. After Stop or a timeout the job is cancelled ONCE, then followed until the kernel says
        it ended (up to settle_s): a job whose end is not confirmed is kept in `unconfirmed_jobs`, never reported as
        stopped, and the operation keeps the PC lock until it is.

        The console not answering (a DesktopError from the poll or from the cancel) does not end the job: nothing has been
        confirmed, so the job id stays in `unconfirmed_jobs`, the poll is retried with a growing pause, and when the console
        stays silent for `outage_wait_s` the job is reported as `unconfirmed` (state failed, never done and never stopped).
        When the console answers again the job is followed on (or, once the worker is gone, confirmed by the reaper)."""
        self.current_job = jid
        end = self.clock() + timeout
        cancelled = timed_out = False
        outage = {"waited": 0.0, "n": 0, "marked": False}

        def console_silent():
            """The console did not answer: remember the job as unconfirmed, pause (bounded), or give the unconfirmed result."""
            outage["n"] += 1
            outage["marked"] = True
            self.unconfirmed_jobs.add(jid)             # nothing confirms that it ended: never forget it
            self.touch("the JARVIS console did not answer; the PC job is not confirmed")
            if outage["waited"] >= self.outage_wait_s:
                return {"id": jid, "state": "failed", "unconfirmed": True, "result": {
                    "ok": False, "error": "the JARVIS console did not answer while this job ran, so it is not known whether "
                                          "it ended; it may still be running"}}
            pause = min(self.outage_poll_s * (2 ** (outage["n"] - 1)), self.outage_poll_max_s)
            outage["waited"] += pause
            self._pause(pause, wake_on_stop=False)
            return None

        try:
            while True:
                try:
                    if self.stopped and not cancelled:
                        self._cancel_once(jid)
                        cancelled = True
                        end = self.clock() + self.settle_s
                    code, j = self.call("GET", f"job/{jid}", None)
                except DesktopError:
                    gave_up = console_silent()
                    if gave_up is not None:
                        return gave_up
                    continue
                outage["n"], outage["waited"] = 0, 0.0              # the console answered: silence is counted from here again
                if code < 400 and j.get("state") in FINAL:
                    self.unconfirmed_jobs.discard(jid)
                    if timed_out and not self.stopped and j.get("state") == "cancelled":
                        # We cancelled it because it ran out of time (the owner did not stop it): that is a failure with the
                        # timeout as its reason, as it always was, not a "cancelled by the owner" result.
                        return {**j, "state": "failed", "result": {**(j.get("result") or {}), "ok": False,
                                                                   "error": f"no result within {int(timeout)} s; it was cancelled"}}
                    return j
                if outage["marked"] and jid not in self._cancel_sent:
                    self.unconfirmed_jobs.discard(jid)     # the console is back and the job is known to be running, not cancelled
                    outage["marked"] = False
                self.touch("waiting for the PC job")
                if self.clock() >= end:
                    if not cancelled:                           # out of time: cancel, then wait for the node to confirm
                        try:
                            self._cancel_once(jid)
                        except DesktopError:                    # the console went away: bounded retries, then unconfirmed
                            gave_up = console_silent()
                            if gave_up is not None:
                                return gave_up
                            continue
                        cancelled = timed_out = True
                        end = self.clock() + self.settle_s
                    else:                                       # cancel sent, never confirmed ended
                        self.unconfirmed_jobs.add(jid)
                        if timed_out and not self.stopped:
                            return {"id": jid, "state": "failed", "unconfirmed": True, "result": {
                                "ok": False, "error": f"no result within {int(timeout)} s; it was cancelled "
                                                      "(the node has not yet confirmed that it stopped)"}}
                        return {"id": jid, "state": "cancelled", "unconfirmed": True, "result": {
                            "ok": False, "error": "stopped; the node has not yet confirmed that the job ended"}}
                self._pause(0.5, wake_on_stop=not cancelled)    # most one-step actions finish within 2-4 s
        finally:
            self.current_job = None

    def cancel(self, jid: str) -> dict:
        code, r = self.call("POST", "job/cancel", {"id": jid})
        return r if code < 400 else {"error": r.get("error")}

    def _stopped_result(self, why: str, aid: str | None = None, **extra) -> dict:
        out = {"state": "cancelled", "result": {"ok": False, "error": why, **extra}}
        if aid:
            out["approval_id"] = aid
        return out

    def run(self, body: dict, timeout: float = 90.0, what: str = "") -> dict:
        """One action end to end. Returns {"state": done|failed|cancelled|waiting_for_permission|denied, "result": {...}}.
        A Pc that was stopped sends nothing; a stop while an approval is awaited denies the approval and does not run the
        action."""
        self.touch("sending the PC job")
        if self.stopped:
            return self._stopped_result("stopped by the owner before it was sent; nothing was done")
        try:
            r = self.submit(body)
        except NotSent as e:                           # Stop (or a lost PC lock) arrived between the check above and the send
            return self._stopped_result(f"not sent: {e.reason}; nothing was done" if self.fence_reason else
                                        "stopped by the owner before it was sent; nothing was done")
        if r.get("status") == "needs_approval":
            aid = r["approval_id"]
            self._approval("pending", aid)
            waited = self.wait_approval(aid, what or r.get("reason", ""))
            if self.stopped:                           # the owner said stop while it waited: stopped, not "still waiting"
                denied = self.deny_approval(aid)
                return self._stopped_result("stopped by the owner before the approval; nothing was done" if denied else
                                            "stopped by the owner before the approval was used; nothing was done (the approval "
                                            "could not be denied: it was already decided or the console did not answer, "
                                            "and it expires by itself)", aid, approval_denied=denied)
            if not waited:
                return {"state": "waiting_for_permission", "approval_id": aid, "level": r.get("level"), "reason": r.get("reason"),
                        "how": "Approve it in the JARVIS console (http://127.0.0.1:8799) with your PIN, then ask JARVIS to continue "
                               f"with approval_id {aid}."}
            self._approval("resolved", aid)            # decided by the owner: nothing left pending to deny
            try:
                r = self.submit(dict(body, approval_id=aid))
            except NotSent as e:                       # Stop (or a lost PC lock) arrived after the approval was seen, before the send
                denied = self.deny_approval(aid)
                return self._stopped_result(f"not sent: {e.reason}; nothing was done" if self.fence_reason else
                                            "stopped by the owner before the approved action was sent; nothing was done", aid,
                                            approval_denied=denied)
            except DesktopError as e:
                return {"state": "denied", "approval_id": aid, "result": {"ok": False, "error": str(e)}}
        j = self.wait_job(r["job"], timeout)
        out = {"state": j.get("state"), "job": j.get("id"), "result": j.get("result") or {}}
        if j.get("unconfirmed"):
            out["unconfirmed"] = True
        return out


# ---------------------------------------------------------------- local results and the icon
def take_local(path: str | None):
    """Read a node result that had to stay on this laptop, then delete it (not retained). Only inside local-only."""
    if not path:
        return None
    p = Path(path).resolve()
    if LOCAL_RESULTS.resolve() not in p.parents:
        raise DesktopError("a local result outside outputs\\local-only was refused")
    try:
        text = p.read_text(encoding="utf-8")
    finally:
        try:
            p.unlink()
        except OSError:
            pass
    try:
        return json.loads(text)
    except ValueError:
        return text


def set_activity(state: str, detail: str = "", path: Path = ACTIVITY):
    """What JARVIS is doing on the PC right now, for the icon (the widget reads it; old entries are ignored)."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        # the widget shows this line and the file is read by other programs: no secret and no file name in it, ever
        path.write_text(json.dumps({"state": state, "detail": screen_text.clean_message(detail, 200), "at": time.time()}), encoding="utf-8")
    except OSError:
        pass


GOAL_PROGRESS = DATA / "plugins" / "goal_progress.json"
GOAL_STATES = ("planning", "running", "done", "failed", "waiting", "idle")
GOAL_EXECUTORS = ("pc", "screen_read", "screen_locate", "doc_read", "deliverable_list", "deliverable_open", "recall", "tasks", "rules")


def set_goal_progress(state: str, n: int = 0, total: int = 0, executor: str = "", path: Path | None = None) -> None:
    """D60: which step of a goal runs now, for the face's progress line (the status tool reads this file). Content-free by construction: one
    of six state words, two small numbers and one of the fixed executor ids; never the goal's words, a step's goal, a file name or a result."""
    state = state if state in GOAL_STATES else "idle"
    executor = executor if executor in GOAL_EXECUTORS else ""
    path = path or GOAL_PROGRESS
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"state": state, "n": max(0, min(int(n), 99)), "total": max(0, min(int(total), 99)), "executor": executor,
                                    "at": time.time()}), encoding="utf-8")
    except (OSError, ValueError, TypeError):
        pass


def profiles(path: Path = PROFILES) -> list[dict]:
    try:
        reg = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    out = [{"profile": p["dir"], "name": p.get("name"), "account": p.get("account"), "default": p.get("default"),
            "say": sorted(a for a in p.get("aliases", []) if not a.startswith("profile"))[:4]} for p in reg.get("profiles", [])]
    if path == PROFILES:                                   # the node's real registry, not a file a test or a tool passed in
        _learn_profile_names(out)
    return out


def _learn_profile_names(profile_list: list[dict]) -> None:
    """D62: the Chrome profile names and accounts the node read from the owner's own browser are facts JARVIS already has: they feed the learned-names store
    (source "profile", the lowest rank). Best effort, unchanged registry costs one hash, never raises."""
    if not profile_list or os.environ.get("JARVIS_LEARNED_NAMES", "1") == "0":
        return
    try:
        from jarvis.plugins._shared import names
        names.learn_profiles(profile_list)
    except Exception:                                                  # noqa: BLE001 - learning names must never break listing profiles
        pass


# ---------------------------------------------------------------- OCR / vision rung (audit PRO-D10, 2026-10-18)
# The node can read one window with Windows' OCR and find a label in it (diag window_ocr / window_locate, kernel policy and
# audit apply like for every job). OCR is less reliable than the accessibility tree: it misreads digits and letters, so
# what it reads is always labelled, never used to apply a fix, and its text is scrubbed before it can reach a model.
OCR_LABEL = "from OCR (less reliable than the accessibility tree)"
APP_PROCESS = re.compile(r"^[A-Za-z0-9_.\- ]{1,40}$")        # a process name: what the node's window capture accepts
MAX_LOCATE_TARGET = 80                                       # the console's owner route keeps 80 characters of "app|label"

# The secret-pattern pass lives in the shared screen-text helper (one implementation for every sink); these names stay here
# because the plugin and its tests have always called core.scrub_text.
scrub_text = screen_text.scrub_text
_SECRET_RULES, _PRIVATE_KEY, _CARD, _luhn = screen_text._SECRET_RULES, screen_text._PRIVATE_KEY, screen_text._CARD, screen_text._luhn


def ocr_lines(value) -> list[str]:
    """The lines of a node OCR result read back from its local file ({"observed_lines": [...]}); anything else is no text."""
    lines = value.get("observed_lines") if isinstance(value, dict) else None
    return [str(x).strip()[:300] for x in lines if isinstance(x, str) and x.strip()][:300] if isinstance(lines, list) else []


# ---------------------------------------------------------------- "a new window showing it appeared"
# The node lists windows with an opaque id per window (a keyed hash of the window handle and process, valid for the life
# of the node process, named by id_epoch) and the ids of ALL windows, not only the 40 it describes. A window counts as
# new only when its id was not in the list taken before the action. Without trustworthy ids (an older node, a window with
# no id, a list cut short, a node restart in between) nothing is claimed: the step stays unverified (audit PC-D1: the
# node stripped the handle, so every window looked new and none was ever counted as new).
NEW_WINDOW_POLL_S = 2.0


def window_ids(listing) -> dict | None:
    """{"epoch", "ids"} of a node window listing, or None when that listing cannot tell windows apart."""
    if not isinstance(listing, dict) or listing.get("ok") is not True:
        return None
    epoch, ids, ws = listing.get("id_epoch"), listing.get("ids"), listing.get("windows")
    if not isinstance(epoch, str) or not epoch or not isinstance(ids, list) or not isinstance(ws, list):
        return None
    if listing.get("ids_complete") is not True or not all(isinstance(i, str) and i for i in ids):
        return None
    if not all(isinstance(w, dict) and isinstance(w.get("id"), str) and w["id"] in ids for w in ws):
        return None
    return {"epoch": epoch, "ids": frozenset(ids)}


def new_window(listing, before: dict | None, stem: str) -> tuple[dict | None, str]:
    """The ONE window that was not there before and whose title shows `stem` (the file's name): (window, "") or
    (None, why not). Two such new windows are ambiguous: which one shows the file is unknown, so nothing is claimed."""
    if before is None:
        return None, "the window list taken before opening had no usable window ids"
    now = window_ids(listing)
    if now is None:
        return None, "the window list has no usable window ids"
    if now["epoch"] != before["epoch"]:
        return None, "the PC node restarted in between, so new and old windows cannot be told apart"
    stem = (stem or "").strip().lower()[:25]
    if not stem:
        return None, "no file name to look for"
    hits = [w for w in listing["windows"] if w["id"] not in before["ids"] and stem in str(w.get("title") or "").lower()]
    if len(hits) == 1:
        return hits[0], ""
    if hits:
        return None, f"{len(hits)} new windows show '{stem}': which one shows the file is unclear"
    return None, f"no new window showing '{stem}' appeared"


def wait_new_window(list_fn: Callable[[], dict], before: dict | None, stem: str, tries: int = 10,
                    sleep: Callable[[float], None] | None = None) -> tuple[str, str]:
    """Poll the window list for new_window(); ("<process>: window for <stem>", "") or ("", why it was not seen)."""
    sleep = sleep or time.sleep
    why = "the window list was not read"
    for i in range(max(1, tries)):
        try:
            w, why = new_window(list_fn(), before, stem)
        except Exception as e:  # noqa: BLE001 - an unreadable list is no evidence
            w, why = None, f"the window list could not be read ({type(e).__name__})"
        if w is not None:
            return f"{w.get('process')}: window for {stem}", ""
        if before is None or "restarted" in why:
            break                                  # no later look can fix this
        if i + 1 < tries:
            sleep(NEW_WINDOW_POLL_S)
    return "", why


# ---------------------------------------------------------------- diagnosis (local)
# A Windows path (C:\...) or an absolute POSIX path (/...; not the path part of a URL such as http://x/a.json).
FILE_IN_TEXT = re.compile(r"((?:[A-Za-z]:\\|(?<![\w:/.~-])/)[^\s\"'<>|*?]+\.(?:json|ini|cfg|conf|config|xml|ya?ml|toml|txt))", re.I)
NUMBER_TYPO = re.compile(r"^(\d+)[^\d\s,}\]]+(\d*)$")


def rows(value) -> list[dict]:
    """The objects of a list the node sent (windows, events, crash records); anything else (an HTML page, one object, a
    null item, cut-off JSON read back as text) is dropped, so a malformed read yields fewer facts, never an exception."""
    return [x for x in value if isinstance(x, dict)] if isinstance(value, list) else []


def _str(value) -> str:
    return value if isinstance(value, str) else ""


def diagnose(windows: list, events: list, crashes: list, app_dir: str | None = None, release=None) -> dict:
    """What went wrong, from the error dialog, the event log and crash records, checked against the files it names.
    A cause is 'verified' only when the named file really has the fault; otherwise it is a hypothesis.
    Wrong-typed input (texts as one string, null items, an object instead of a list) is ignored, not trusted.

    What the diagnosis SAYS (cause, evidence) goes to a model, so it names no file: a file is "a .json file". Names and text
    taken from the window (a library, a module, the dialog's own words) appear only through `release`, a function that returns
    the cleared form of a string or None/"" when it may not leave (screen_text.release via the plugin); without it they
    are replaced by a class ("a .dll library") or "its text stayed on this laptop". The real file stays in out["file"] and
    out["fix"]["target"], for the local repair job only; the plugin never puts them in the report."""
    windows, events, crashes = rows(windows), rows(events), rows(crashes)

    def shown(text: str, fallback: str) -> str:
        try:
            got = release(text) if release is not None else None
        except Exception:  # noqa: BLE001 - a failing release means withheld
            got = None
        return got if isinstance(got, str) and got.strip() else fallback
    texts = [t for w in windows for t in (w.get("texts") if isinstance(w.get("texts"), list) else []) + [_str(w.get("title"))]
             if isinstance(t, str)]
    dialog = [w for w in windows if w.get("dialog")]
    out = {"dialog_seen": bool(dialog), "dialog_title": (_str(dialog[0].get("title")) if dialog else "")[:120],
           "verified": False, "cause": "", "evidence": [], "fix": None, "app_dir": app_dir}
    joined = " ".join(texts)
    m = FILE_IN_TEXT.search(joined)
    if m:
        path = m.group(1)
        out["file"] = path
        if path.lower().endswith(".json") and os.path.isfile(path):
            raw = _exact_text(path)
            try:
                json.loads(raw)
                out["evidence"].append("the file the dialog names is valid JSON now")
            except json.JSONDecodeError as e:
                line = raw.splitlines()[e.lineno - 1] if 0 < e.lineno <= len(raw.splitlines()) else ""
                out.update(verified=True, cause=f"{screen_text.file_class(path)} is not valid JSON at line {e.lineno}, column {e.colno} ({e.msg})",
                           line=e.lineno)
                out["evidence"].append(f"JSON check of {screen_text.file_class(path)}: {e.msg} at line {e.lineno}")
                fix = _number_typo_fix(line)
                if fix:
                    at = sum(len(x) for x in raw.splitlines(keepends=True)[:e.lineno - 1]) + line.find(fix[0])
                    fix = _unique_edit(raw, at, fix[0], fix[1])
                if fix:
                    out["fix"] = {"action": "edit_file", "target": path, "args": {"find": fix[0], "replace": fix[1]},
                                  "why": "a value that must be a number has a typo"}
    dll = re.search(r"(?i)\b([\w.-]+\.dll)\b was not found|(?:missing|cannot find|could not load)\D{0,20}\b([\w.-]+\.dll)\b", joined)
    mod = re.search(r"No module named '([\w.]+)'", joined)
    if not out["cause"] and dll:
        name = dll.group(1) or dll.group(2)
        found = _dll_present(name, out.get("app_dir"))
        out.update(cause=f"a required library is missing: {shown(name, 'a .dll library')}" + (" (a hypothesis: a copy is present, so the error may be about "
                                                                      "a different copy or version)" if found else ""),
                   verified=not found, dependency=name)
        out["evidence"].append(f"{shown(name, 'the library')} {'is present after all (the error may be about a different copy)' if found else 'is in neither the program folder nor the system path'}")
    elif not out["cause"] and mod:
        out.update(cause=f"a required Python module is missing: {shown(mod.group(1), 'a module whose name stayed on this laptop')} (a hypothesis until its environment is checked)",
                   dependency=mod.group(1))
    if not out["cause"]:
        _system_causes(joined, out)
    if not out["cause"] and dialog:
        out["cause"] = "the program showed an error dialog: " + shown("; ".join(texts)[:200], "its text stayed on this laptop")
    if crashes:
        c = crashes[0]
        out["evidence"].append(f"crash record: {c.get('app')} {c.get('kind')} in {c.get('module') or '?'} code {c.get('code') or '?'}")
        if not out["cause"]:
            out["cause"] = f"{c.get('app')} crashed in {c.get('module') or 'an unknown module'} (code {c.get('code') or '?'}): a hypothesis until reproduced"
    if events:
        out["evidence"].append(f"{len(events)} recent error event(s) from: " + ", ".join(sorted({str(e.get('provider')) for e in events}))[:160])
    if not out["cause"]:
        out["cause"] = "no error dialog, crash record or error event was found for it"
    return out


PORT_IN_USE = re.compile(r"(?i)\bport (\d{2,5}) is already in use|address already in use[^\d]{0,40}(\d{2,5})|"
                         r"only one usage of each socket address[^\d]{0,60}(\d{2,5})")
NO_SPACE = re.compile(r"(?i)not enough space on the disk|disk is full|there is not enough space")
NEEDS_SPACE = re.compile(r"(?i)needs (\d+) ?MB on (?:([A-Z]):|(/[^\s,;:]*))")
REFUSED = re.compile(r"(?i)(?:could not connect|connection refused|no connection could be made)[^\n]{0,80}?"
                     r"\b(127\.0\.0\.1|localhost):(\d{2,5})|\b(127\.0\.0\.1|localhost):(\d{2,5})\b[^\n]{0,40}connection refused")


def listener(port: int) -> dict | None:
    """Who listens on this local TCP port right now: {pid, name} or None. Read-only (psutil)."""
    import psutil
    for c in psutil.net_connections(kind="tcp"):
        if c.status == psutil.CONN_LISTEN and c.laddr and c.laddr.port == port:
            try:
                return {"pid": c.pid, "name": psutil.Process(c.pid).name() if c.pid else "a system process"}
            except (psutil.Error, OSError):
                return {"pid": c.pid, "name": "an unknown process"}
    return None


def free_port(start: int = 8100, end: int = 8999) -> int | None:
    import socket
    for p in range(start, end):
        if listener(p):
            continue
        with socket.socket() as s:
            try:
                s.bind(("127.0.0.1", p))
                return p
            except OSError:
                continue
    return None


def _system_causes(joined: str, out: dict):
    """Port taken, disk full, a service not running: each checked against the system as it is now, never assumed."""
    import psutil
    m = PORT_IN_USE.search(joined)
    if m:
        port = int(next(g for g in m.groups() if g))
        who = listener(port)
        if who:
            out.update(verified=True, cause=f"port {port} is already used by {who['name']} (process {who['pid']})", port=port)
            out["evidence"].append(f"listening check: {who['name']} holds 127.0.0.1:{port}")
            cfg = out.get("file")
            if cfg and cfg.lower().endswith(".json") and os.path.isfile(cfg):
                text = _exact_text(cfg)
                old = re.search(rf'"port"\s*:\s*{port}\b', text)
                new = free_port()
                edit = _unique_edit(text, old.start(), old.group(0), f'"port": {new}') if old and new else None
                if edit:
                    out["fix"] = {"action": "edit_file", "target": cfg, "args": {"find": edit[0], "replace": edit[1]},
                                  "why": f"move the program to free port {new} (the other program keeps {port})"}
        else:
            out.update(cause=f"the program says port {port} is in use, but nothing listens on it now (it may have been "
                             "released): a hypothesis", port=port)
        return
    if NO_SPACE.search(joined):
        n = NEEDS_SPACE.search(joined)
        if n and n.group(2):
            drive, label = n.group(2).upper() + ":\\", n.group(2).upper() + ":"
        elif n and n.group(3):
            drive = label = n.group(3)                       # a POSIX mount point or folder
        else:
            where = out.get("app_dir") or ("C:\\" if os.name == "nt" else "/")
            drive, label = (where[:3], where[:2]) if re.match(r"[A-Za-z]:", where) else (where, where)
        try:
            free_mb = psutil.disk_usage(drive).free // 2**20
        except OSError:
            free_mb = None
        need = int(n.group(1)) if n else None
        if free_mb is not None and need is not None:
            ok = free_mb < need
            out.update(verified=ok, cause=f"{label} has {free_mb} MB free and the program needs {need} MB" if ok else
                       f"the program reports low space, but {label} has {free_mb} MB free (it needs {need}): a hypothesis",
                       drive=label, free_mb=free_mb, needs_mb=need)
        else:
            have = f"{free_mb} MB free" if free_mb is not None else "free space that could not be checked"
            out.update(cause=f"the program reports low disk space; {label} has {have}: a hypothesis", drive=label)
        out["evidence"].append(f"free-space check of {label}: " + (f"{free_mb} MB" if free_mb is not None else "not possible (no such drive)"))
        out["advice"] = "free space on that drive (the owner decides what to delete) or move the program's data elsewhere"
        return
    m = REFUSED.search(joined)
    if m:
        port = int(m.group(2) or m.group(4))
        who = listener(port)
        if who is None:
            from .monitor import SERVICES
            known = next((n for n, p in SERVICES.items() if p == port), None)
            out.update(verified=True, port=port, cause=f"nothing is listening on 127.0.0.1:{port}: the service it needs is not running"
                       + (f" (that is JARVIS's {known})" if known else ""))
            out["advice"] = f"start {known or 'that service'}, then start the program again"
        else:
            out.update(port=port, cause=f"{who['name']} is listening on {port} now; the refusal may have been temporary: a hypothesis")
        out["evidence"].append(f"listening check on {port}: {who['name'] if who else 'nothing'}")


def _dll_present(name: str, app_dir: str | None = None) -> bool:
    dirs = ([app_dir] if app_dir else []) + [os.environ.get("SystemRoot", r"C:\Windows") + r"\System32"] + os.environ.get("PATH", "").split(os.pathsep)
    return any(d and os.path.isfile(os.path.join(d, name)) for d in dirs)


def _exact_text(path: str) -> str:
    """The file's text as repair.edit_file will see it (BOM dropped, line endings kept as they are on disk)."""
    with open(path, encoding="utf-8-sig", newline="") as f:
        return f.read()


def _unique_edit(text: str, at: int, find: str, replace: str, max_lines: int = 3):
    """repair.edit_file replaces only text that occurs exactly once. If `find` (at offset `at`) occurs more often, widen it
    to its whole line, then to neighbouring lines (at most `max_lines` each way), until it is unique. None if it never is."""
    if at < 0 or text[at:at + len(find)] != find:
        return None
    if text.count(find) == 1:
        return find, replace
    starts = [0]
    for ln in text.splitlines(keepends=True):
        starts.append(starts[-1] + len(ln))
    i = max(k for k in range(len(starts) - 1) if starts[k] <= at)     # the line that holds the fault
    for n in range(max_lines + 1):
        a, b = starts[max(i - n, 0)], starts[min(i + n + 1, len(starts) - 1)]
        anchor = text[a:b]
        if text.count(anchor) == 1:
            return anchor, text[a:at] + replace + text[at + len(find):b]
    return None


def _number_typo_fix(line: str):
    """'  "port": 80x80' -> ('"port": 80x80', '"port": 8080'): only when the value is digits with a stray character."""
    m = re.search(r'("[^"]+"\s*:\s*)([^,}\s]+)', line)
    if not m:
        return None
    t = NUMBER_TYPO.match(m.group(2))
    if not t:
        return None
    return m.group(0), m.group(1) + t.group(1) + t.group(2)
