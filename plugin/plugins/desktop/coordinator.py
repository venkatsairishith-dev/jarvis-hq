"""Coordinator for PC tasks (2026-10-16): one owner request -> local decision -> bounded steps by the right agent ->
kernel-gated actions -> verified outcome, with one shared task record. Logic only (no N.E.K.O imports); the plugin wires
the real agents in.

    request -> decide.decide (local model + deterministic resolution)
            -> for each step: AGENT[tool] runs it (browser / desktop / troubleshooter / research), every action through
               the JARVIS kernel (Pc), which alone decides what is allowed
            -> VERIFY[tool] checks the observed state against that step's success criterion
            -> record: plan, actions, observations, verdicts, uncertainty (D:\\jarvis\\N.E.K.O-data\\neko\\pc_tasks)

Rules: one PC task at a time (execution lock, so two agents never type into the same window); at most MAX_STEPS steps,
TOTAL_BUDGET_S seconds and one retry of a step, and only for a transient failure (a timeout or a lost browser connection),
never for a refusal, a denial or a wrong result; a step that needs the owner (approval, clarification) ends the task with
that question instead of guessing; "success" is reported only for steps whose check passed.
"""
from __future__ import annotations

import contextlib
import contextvars
import json
import logging
import os
import re
import secrets
import threading
import time
from pathlib import Path
from typing import Callable

from . import decide

DATA = Path(os.environ.get("NEKO_STORAGE_SELECTED_ROOT", r"D:\jarvis\N.E.K.O-data"))
TASKS = DATA / "neko" / "pc_tasks"
LOCK = DATA / "neko" / "pc_task.lock"
TOTAL_BUDGET_S = 600
MAX_RETRIES = 1
AGENT = {"open_website": "browser", "play_media": "browser", "browser": "browser", "open_app": "desktop", "window": "desktop",
         "type_text": "desktop", "press_key": "desktop", "open_file": "desktop", "check_pc": "desktop",
         "troubleshoot": "troubleshooter", "research": "research", "open_output": "desktop", "stop": "coordinator"}
TRANSIENT = re.compile(r"(?i)timeout|timed out|connection failed|not answering|temporarily|busy")
# Steps that change nothing when run twice (open, focus, read, check). Typing, keys, clicks, playing, repairs and
# research are not: a failure reported as a timeout may come after they already acted, so they are never re-run blindly.
REPEAT_SAFE = {"open_website", "open_app", "window", "check_pc", "open_file"}
REPEAT_SAFE_BROWSER = {"read", "find", "tabs", "whoami"}


def repeat_safe(step: dict) -> bool:
    t = step.get("tool")
    return t in REPEAT_SAFE or (t == "browser" and step.get("action") in REPEAT_SAFE_BROWSER)


class Busy(Exception):
    pass


# ---------------------------------------------------------------- operations: ONE registry of what is acting on the PC
# Every PC operation (a task, a mission, a single-action entry, a read-only check, a delegated step's PC jobs) is an
# Operation while it runs: an id, a kind, its Pc objects (so its current node job can be cancelled), a stop flag, the workers
# (threads) that still do its work, and the PC lock it holds. Stop (stop_all) sets every flag, cancels every node job, denies
# every pending approval, and reports an operation as stopped only when its workers really ended; one that did not end within
# the bounded wait is reported `still_running` and keeps its lock (a second task is refused, naming it) until its worker has
# really ended or the owner's recovery (recover) finds the stop confirmed. The lock is released exactly once, by whoever
# ends last: the entry that started it or its last worker.
LOG = logging.getLogger("neko.desktop.pc")
STUCK_AFTER_S = 120.0           # a running operation whose workers show no sign of life for this long is "stuck": stop requested
STOP_WAIT_S = 20.0              # how long Stop waits for the workers to end before it says still_running
REAP_POLL_S = 2.0               # how often an operation waits for the node to confirm a cancelled job ended
REAP_MAX_S = 900.0              # ... and the longest it keeps the lock for that (then it says so and lets go)
RELEASE_RETRY_S = 5.0           # a lock file that could not be removed at release is tried again this often ...
RELEASE_RETRY_MAX_S = 600.0     # ... for this long (then it stays registered, visible, until the owner's recovery removes it)
_CURRENT: "contextvars.ContextVar[Operation | None]" = contextvars.ContextVar("pc_operation", default=None)
_OPS: "dict[int, Operation]" = {}
_OPS_LOCK = threading.Lock()
# Owner-visible escalations: a cancel the node never confirmed and the lock was nevertheless released (REAP_MAX_S passed). Kept
# (bounded, newest last) in this process and in the registry snapshot, and added to Stop's and the recovery's answers, so the
# owner is told "check the PC" instead of finding only a log line.
ALERTS: "list[dict]" = []
ALERT_KEEP = 10


OPS_FILE = DATA / "neko" / "pc_operations.json"      # the registry's snapshot for the widget and the wake listener (see publish)
PUBLISH_S = 5.0                 # the snapshot is rewritten this often while any operation lives
APPROVAL_TTL_S = 1800.0         # a tracked pending approval is forgotten after this long (the kernel's own card expires sooner)
_APPROVALS: "dict[str, dict]" = {}
_APPROVALS_LOCK = threading.Lock()
_PUB_WAKE = threading.Event()
_PUB_STATE = {"thread": None}
_PUB_LOCK = threading.Lock()


# ---- pending approvals that belong to PC operations (review L1/L2/L3 of M14): Stop and cancel DENY them
def track_approval(aid: str, pc, owner: str) -> None:
    """A kernel approval this operation's job is waiting on (or left pending when the operation ended waiting for the owner)."""
    if not aid:
        return
    now = time.monotonic()
    with _APPROVALS_LOCK:
        for k in [k for k, v in _APPROVALS.items() if now - v["at"] > APPROVAL_TTL_S]:
            _APPROVALS.pop(k, None)
        _APPROVALS[str(aid)] = {"pc": pc, "op": str(owner), "at": now}


def forget_approval(aid: str) -> None:
    with _APPROVALS_LOCK:
        _APPROVALS.pop(str(aid), None)


def pending_approvals(owner: "str | None" = None) -> list[str]:
    with _APPROVALS_LOCK:
        return [a for a, v in _APPROVALS.items() if owner is None or v["op"] == owner]


def deny_approval_id(aid: str, pc=None) -> str:
    """Deny ONE kernel approval by id (the owner route, no PIN: denying is the safe direction). "denied" (now or before),
    "gone" (already approved, used or expired: nothing to deny, tolerated) or "unreachable" (the console did not answer:
    it stays tracked and is tried again by the next Stop). The kernel never lets a denied approval be approved or used."""
    with _APPROVALS_LOCK:
        known = _APPROVALS.get(str(aid))
    pc = pc or (known or {}).get("pc")
    if pc is None:
        from . import core
        pc = core.Pc()
    try:
        res = pc.deny_result(str(aid))
    except Exception:  # noqa: BLE001
        res = "unreachable"
    if res != "unreachable":
        forget_approval(aid)
    return res


def deny_pending_approvals(owner: "str | None" = None) -> dict:
    """Deny every tracked pending approval (of one operation, or all): the approvals of operations that were cancelled or that
    ended waiting for the owner. Approvals nothing here tracked (other agents', the owner's own) are never touched."""
    out = {"denied": [], "gone": [], "unreachable": []}
    for aid in pending_approvals(owner):
        out[{"denied": "denied", "gone": "gone"}.get(deny_approval_id(aid), "unreachable")].append(aid)
    return out


def _approval_event(op: "Operation", pc, kind: str, aid: str) -> None:
    if kind == "pending":
        track_approval(aid, pc, op.name)
    elif kind == "resolved":
        forget_approval(aid)


# ---- the registry's snapshot, for the widget's "pc busy" and the wake listener's "stop" (review M14 / L1)
def snapshot(wall=time.time) -> dict:
    ops = [op.describe() for op in operations()]
    return {"at": wall(), "pid": os.getpid(), "busy": bool(ops), "operations": [
        {k: d.get(k) for k in ("id", "kind", "state", "age_s", "idle_s", "stop_requested_s_ago", "holds_lock")} for d in ops],
        "approvals_pending": len(pending_approvals()), "alerts": alerts()}


def publish(path: "Path | None" = None) -> bool:
    """Write the snapshot (atomically). The file is the authoritative 'is the PC being acted on' signal: it says busy exactly while
    an Operation is registered, and the publisher refreshes `at` every PUBLISH_S, whatever any step is doing; a reader treats it
    as stale only when `at` stops moving (the plugin process is gone), never because a long step wrote nothing."""
    path = OPS_FILE if path is None else path
    tmp = path.with_name(path.name + ".tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_text(json.dumps(snapshot()), encoding="utf-8")
        os.replace(tmp, path)
        return True
    except OSError:
        return False


def _publisher() -> None:
    while True:
        publish()
        _refresh_activity()
        if not operations():
            break
        _PUB_WAKE.wait(PUBLISH_S)
        _PUB_WAKE.clear()
    publish()                      # an operation that ended after the publish above must not leave a stale "busy" behind
    with _PUB_LOCK:
        _PUB_STATE["thread"] = None
        if operations():                                   # one registered between the check and now: keep publishing
            _start_publisher()


def _start_publisher() -> None:
    t = threading.Thread(target=_publisher, name="pc-ops-publisher", daemon=True)
    _PUB_STATE["thread"] = t
    t.start()


def _poke_publisher() -> None:
    """An operation was registered or ended: publish now, and keep publishing while any operation lives."""
    with _PUB_LOCK:
        if _PUB_STATE["thread"] is None:
            _start_publisher()
    _PUB_WAKE.set()


def _refresh_activity() -> None:
    """The Neko icon's activity file expires after 2 minutes (neko_state.ACTIVITY_FOR_S): while operations live and the last
    state is a working one, keep it fresh so a long step does not look idle."""
    try:
        from . import core
        path = core.ACTIVITY
        a = json.loads(path.read_text(encoding="utf-8"))
        if a.get("state") and a.get("state") != "idle" and operations():
            a["at"] = time.time()
            tmp = path.with_name(path.name + ".tmp")
            tmp.write_text(json.dumps(a), encoding="utf-8")
            os.replace(tmp, path)
    except (OSError, ValueError, TypeError, AttributeError):
        pass


class Operation:
    """What one PC operation is, for as long as it runs. Created by begin() (or by acquire() for a lock holder), registered at
    once, unregistered by finish()."""

    def __init__(self, kind: str, name: str = "", clock=time.monotonic, wall=time.time):
        self.kind, self.name, self.clock, self.wall = kind, name or kind, clock, wall
        self.started, self.started_wall = clock(), wall()
        self.stop_flag = threading.Event()
        self.stop_requested_at: float | None = None          # wall clock
        self.stop_reason = ""
        self.stop_errors: list[str] = []                # stop-path failures that were not swallowed (ST-5): reported with the stop answer
        self.done = threading.Event()                         # set when the operation is over and its lock is released
        self.last_progress, self.last_note = clock(), ""
        self.pcs: list = []
        self.on_stop: list = []                               # callables run once when the first stop arrives (a mission's cancel)
        self.hold: "Hold | None" = None
        self.auto = False                                     # created by acquire() itself: finishes when its lock is released
        self.stuck = False
        self.abandoned = False
        self.outcome: str | None = None
        self.events: list[str] = []
        self._lock = threading.RLock()
        self._workers = 0
        self._entry_open = True
        self._finished = False
        self._reaper: threading.Thread | None = None
        self.release_failed = ""                              # set when the lock file could not be removed at the end (review M9)
        self._release_retry: threading.Thread | None = None
        with _OPS_LOCK:
            _OPS[id(self)] = self
        self._event(f"started ({self.kind})", logging.DEBUG)
        _poke_publisher()

    # ---- observation
    def _event(self, text: str, level: int = logging.INFO) -> None:
        self.events = (self.events + [f"{time.strftime('%H:%M:%S')} {text}"])[-20:]
        LOG.log(level, "pc operation %s [%s]: %s", self.name, self.kind, text)

    def touch(self, note: str = "") -> None:
        self.last_progress = self.clock()
        if note:
            self.last_note = note[:80]

    def jobs(self) -> list[str]:
        out = []
        for pc in list(self.pcs):
            j = getattr(pc, "current_job", None)
            if j:
                out.append(str(j))
            out += [str(x) for x in getattr(pc, "unconfirmed_jobs", ()) or ()]
        return sorted(set(out))

    def state(self) -> str:
        if self.done.is_set():
            return "abandoned" if self.abandoned else "finished"
        if self.release_failed:
            return "release_failed"                           # its work is over; only its lock file is still there
        if self.stop_flag.is_set():
            return "stuck" if self.stuck else "stopping"
        return "stuck" if self.stuck else "running"

    def describe(self) -> dict:
        now = self.clock()
        d = {"id": self.name, "kind": self.kind, "state": self.state(), "age_s": round(now - self.started, 1),
             "idle_s": round(now - self.last_progress, 1), "jobs": self.jobs(), "holds_lock": self.hold is not None and not self.done.is_set(),
             "workers": self._workers}
        if self.last_note:
            d["last"] = self.last_note
        if self.release_failed:
            d["lock_release_failed"] = self.release_failed
        if self.stop_requested_at is not None:
            d["stop_requested_s_ago"] = round(self.wall() - self.stop_requested_at, 1)
            d["stop_reason"] = self.stop_reason
        return d

    # ---- pcs
    def attach_pc(self, pc):
        with self._lock:
            if pc not in self.pcs:
                self.pcs.append(pc)
            try:
                pc.on_progress = self.touch
                pc.on_approval = lambda kind, aid, _op=self, _pc=pc: _approval_event(_op, _pc, kind, aid)
                pc.fence, pc.on_job, pc.holder = self._fence, self._job_started, self._holder_info
            except Exception:  # noqa: BLE001 - a stand-in that refuses attributes
                pass
            if self.stop_flag.is_set():
                pc.stopped = True                             # created after Stop: it can send nothing
        return pc

    def _fence(self) -> bool:
        """Asked before every job request of this operation's Pcs: does this operation still hold the PC lock? False when its lock
        file names another holder or is gone (a takeover, the owner's recovery): the operation is stopped (visibly) and nothing is
        sent, so a stale executor never starts a job for a task that is no longer the PC's holder."""
        if self.abandoned:
            return False
        hold = self.hold
        if hold is None or hold.still_held():
            return True
        self._event("its PC lock is no longer this task's: nothing more is sent", logging.ERROR)
        self.request_stop("the PC lock is no longer this task's")
        return False

    def _holder_info(self) -> "dict | None":
        hold = self.hold
        return {"token": hold.token, "task": hold.task} if hold is not None else None

    def _job_started(self, jid: str) -> None:
        hold = self.hold
        if hold is not None:
            hold.record_job(jid)

    def detach_pc(self, pc) -> None:
        with self._lock:
            if pc in self.pcs and not getattr(pc, "unconfirmed_jobs", None):
                self.pcs.remove(pc)

    def new_pc(self, **kw):
        from . import core
        return self.attach_pc(core.Pc(**kw))

    # ---- stop
    def request_stop(self, reason: str = "stop requested") -> bool:
        """Set the stop flag and wake every wait of this operation. Never blocks. True = this call was the first stop."""
        with self._lock:
            first = not self.stop_flag.is_set()
            if first:
                self.stop_requested_at, self.stop_reason = self.wall(), reason[:160]
                self.stop_flag.set()
            pcs = list(self.pcs)
            callbacks = list(self.on_stop) if first else []
        for pc in pcs:
            try:
                pc.stopped = True
            except Exception as e:  # noqa: BLE001 - not silent: a worker that cannot be flagged may go on sending (ST-5)
                self.stop_errors.append(f"could not set the stop flag on a worker ({type(e).__name__})")
                LOG.error("pc operation %s: could not set pc.stopped (%s); the worker may not notice the stop", self.name, type(e).__name__)
        if first:
            self._event(f"stop requested: {reason}")
            for cb in callbacks:
                try:
                    cb()
                except Exception as e:  # noqa: BLE001 - one failing hook never blocks the stop
                    LOG.warning("pc operation %s: stop hook failed (%s)", self.name, type(e).__name__)
        return first

    def cancel_jobs(self) -> list[str]:
        """Blocking: tell the kernel to cancel every node job this operation has in flight and deny every approval it waits
        on (best effort; the worker does the same when it wakes). Returns the job ids a cancel was sent for."""
        sent = []
        for pc in list(self.pcs):
            aid = getattr(pc, "pending_approval", None)
            if aid and hasattr(pc, "deny_approval"):
                try:
                    pc.deny_approval(aid)
                except Exception as e:  # noqa: BLE001 - not silent (ST-5): the card stays tracked; the next Stop and the report say so
                    self.stop_errors.append(f"approval {aid} could not be denied ({type(e).__name__}); it may still be approvable")
                    LOG.warning("pc operation %s: denying approval %s failed (%s); it stays tracked", self.name, aid, type(e).__name__)
            jid = getattr(pc, "current_job", None)
            if jid:
                try:
                    getattr(pc, "cancel_once", pc.cancel)(jid)
                    sent.append(str(jid))
                except Exception as e:  # noqa: BLE001
                    self.stop_errors.append(f"cancelling job {jid} failed ({type(e).__name__}); it may still be running")
                    LOG.warning("pc operation %s: cancelling job %s failed (%s)", self.name, jid, type(e).__name__)
        deny_pending_approvals(self.name)             # approvals this operation's earlier jobs left pending (belong to it only)
        return sent

    def check_stuck(self) -> bool:
        """Progress-based staleness (the process being alive and the heartbeat fresh say nothing about the worker): no sign
        of life from the workers for STUCK_AFTER_S while the operation runs -> stuck, and a stop is requested, visibly."""
        if self.done.is_set() or self.stuck:
            return self.stuck
        if self.clock() - self.last_progress > STUCK_AFTER_S:
            self.stuck = True
            self._event(f"no progress for {self.clock() - self.last_progress:.0f} s: marked stuck")
            LOG.warning("pc operation %s [%s] is stuck: no progress for %.0f s; stop requested", self.name, self.kind,
                        self.clock() - self.last_progress)
            self.request_stop(f"no progress for {STUCK_AFTER_S:.0f} s (stuck)")
        return self.stuck

    # ---- ownership: the entry that started it and the workers that do its work
    def worker_start(self) -> None:
        with self._lock:
            self._workers += 1

    def worker_done(self) -> None:
        with self._lock:
            self._workers = max(0, self._workers - 1)
        self._maybe_finish()

    def entry_exit(self) -> None:
        """The entry that started this operation is over. The operation (and its lock) ends when its workers have too."""
        with self._lock:
            self._entry_open = False
        self._maybe_finish()

    def _unconfirmed(self) -> list:
        return [(pc, j) for pc in list(self.pcs) for j in list(getattr(pc, "unconfirmed_jobs", ()) or ())]

    def unconfirmed_jobs(self) -> list[str]:
        """The node jobs of this operation that nothing has confirmed ended. Each is asked of the kernel once; a confirmed
        one is forgotten. A task or mission starts no further action while this is not empty."""
        out = []
        for pc, j in self._unconfirmed():
            if _job_final(pc, j):
                pc.unconfirmed_jobs.discard(j)
            else:
                out.append(str(j))
        return sorted(set(out))

    def _maybe_finish(self) -> None:
        with self._lock:
            if self._entry_open or self._workers > 0 or self._finished:
                return
            pending = self._unconfirmed()
            if pending:
                if self._reaper is None:
                    self._reaper = threading.Thread(target=self._reap, name=f"pc-reaper-{self.name}", daemon=True)
                    self._reaper.start()
                return
        self.finish("ended")

    def _reap(self) -> None:
        """All workers are gone, but the node has not confirmed that a cancelled job ended: hold the lock until it does."""
        self._event("waiting for the node to confirm a cancelled job ended; the PC lock stays held")
        end = self.clock() + REAP_MAX_S
        while not self._finished and not self.abandoned:
            left = [(pc, j) for pc, j in self._unconfirmed() if not _job_final(pc, j)]
            for pc, j in list(self._unconfirmed()):
                if (pc, j) not in left:
                    pc.unconfirmed_jobs.discard(j)
            if not left:
                break
            if self.clock() > end:
                self._event(f"gave up waiting for the node to confirm job(s) {sorted(j for _, j in left)} ended")
                LOG.warning("pc operation %s: job(s) %s never confirmed ended; releasing the lock", self.name, [j for _, j in left])
                _alert(self, [j for _, j in left])
                break
            self.touch("waiting for the node to confirm the stop")
            time.sleep(REAP_POLL_S)
        self.finish("ended (node confirmation " + ("received" if not self._unconfirmed() else "missing") + ")")

    def finish(self, outcome: str = "ended") -> bool:
        """End the operation exactly once: release its lock, unregister it, release whoever waits on `done`. When the lock file
        cannot be removed the operation is NOT finished: it stays registered (state release_failed, visible in Busy answers and
        in Stop's still_running), keeps trying in the background, and the owner's recovery removes the lock."""
        with self._lock:
            if self._finished:
                return False
            self._finished = True
            self.outcome = outcome
        hold = self.hold
        if hold is not None and not release(hold):
            self._release_failed(hold)
            return True
        self._complete()
        return True

    def _complete(self) -> None:
        with _OPS_LOCK:
            _OPS.pop(id(self), None)
        self.release_failed = ""
        if not self.done.is_set():
            self._event(f"finished: {self.outcome}", logging.DEBUG)
        self.done.set()
        _poke_publisher()

    def _release_failed(self, hold: "Hold") -> None:
        self.release_failed = hold.release_failed or "the lock file could not be removed"
        self._event("its work has ended but its PC lock file could not be removed; the lock stays held and is retried "
                    "(or ask to recover the PC lock)", logging.ERROR)
        with self._lock:
            if self._release_retry is None:
                self._release_retry = threading.Thread(target=self._retry_release_loop, name=f"pc-release-{self.name}", daemon=True)
                self._release_retry.start()

    def retry_release(self) -> bool:
        """One more try to remove the lock file of an operation whose work is over. True = the lock is gone, op finished."""
        hold = self.hold
        if hold is None or release(hold):
            self.release_failed = ""
            if self._finished:
                self._complete()
            return True
        self.release_failed = hold.release_failed or self.release_failed
        return False

    def _retry_release_loop(self) -> None:
        end = self.clock() + RELEASE_RETRY_MAX_S
        while not self.done.is_set() and self.clock() < end:
            time.sleep(RELEASE_RETRY_S)
            if self.retry_release():
                return
        if not self.done.is_set():
            LOG.error("pc operation %s: its PC lock file still cannot be removed; the lock stays held until the owner recovers it",
                      self.name)

    def abandon(self, why: str) -> None:
        """Recovery: give up a worker that will not end. Every Pc is stopped (it can send nothing new), the lock is let go."""
        with self._lock:
            self.abandoned = True
        self.request_stop(why)
        self._event(f"abandoned: {why}")
        self.finish("abandoned: " + why)


def _alert(op: "Operation", jobs: list) -> None:
    ALERTS.append({"at": time.time(), "operation": op.name, "kind": op.kind, "jobs": sorted(str(j) for j in jobs),
                   "message": f"The PC lock of {op.name} was released although the node never confirmed that its job(s) "
                              f"{', '.join(sorted(str(j) for j in jobs))} ended after {REAP_MAX_S:.0f} s: it may still be acting on the "
                              "PC. Look at the PC, and stop the job in the JARVIS console (Jobs, Cancel) before trusting it."})
    del ALERTS[:-ALERT_KEEP]
    _poke_publisher()


ALERT_FOR_S = 3600.0            # an escalation is shown for an hour (the owner has looked at the PC by then)


def alerts() -> list:
    now = time.time()
    return [a for a in ALERTS if now - a["at"] <= ALERT_FOR_S]


def _job_final(pc, jid) -> bool:
    fn = getattr(pc, "job_final", None)
    try:
        return bool(fn(jid)) if fn else False
    except Exception:  # noqa: BLE001
        return False


def begin(kind: str, name: str = "") -> Operation:
    """Register a PC operation that holds no lock (a read-only check) or whose lock is taken later (acquire(op=...))."""
    return Operation(kind, name or f"{kind}-{secrets.token_hex(3)}")


def current_op() -> "Operation | None":
    return _CURRENT.get()


def bind(op: "Operation | None"):
    return _CURRENT.set(op)


def unbind(token) -> None:
    try:
        _CURRENT.reset(token)
    except (ValueError, RuntimeError):      # reset from another context (a worker thread): the binding dies with its thread
        pass


class operation:
    """`with coord.operation("check_pc"):` a lock-free PC operation, current for the block, finished when it ends."""

    def __init__(self, kind: str, name: str = ""):
        self.kind, self.name = kind, name

    def __enter__(self) -> Operation:
        self.op = begin(self.kind, self.name)
        self.tok = bind(self.op)
        return self.op

    def __exit__(self, *exc):
        unbind(self.tok)
        self.op.entry_exit()
        return False


def operations() -> list[Operation]:
    with _OPS_LOCK:
        return list(_OPS.values())


def request_stop_all(reason: str = "stop requested") -> int:
    """Flags only, never blocks (used from inside a running task's own stop step). Returns how many operations."""
    ops = operations()
    for op in ops:
        op.request_stop(reason)
    return len(ops)


def stop_all(reason: str = "stopped by the owner", wait_s: float = STOP_WAIT_S) -> dict:
    """The owner's Stop, for EVERY registered operation: set the flags, cancel the node jobs, deny the pending approvals, wait
    (bounded) for the workers to end. `stopped` lists only operations whose workers really ended; the others are
    `still_running` (observable here, in the log and in the operation's events), and they keep their lock."""
    ops = operations()
    for op in ops:
        op.request_stop(reason)
    cancelled: list[str] = []
    for op in ops:
        cancelled += op.cancel_jobs()
    deadline = time.monotonic() + max(0.0, wait_s)
    for op in ops:
        op.done.wait(max(0.0, deadline - time.monotonic()))
    stopped = [op.describe() for op in ops if op.done.is_set()]
    still = [op.describe() for op in ops if not op.done.is_set()]
    for d in still:
        LOG.warning("pc operation %s [%s] did not end within %.0f s of the stop (state %s, jobs %s): still running",
                    d["id"], d["kind"], wait_s, d["state"], d["jobs"])
    # Approvals left pending by operations that already ended waiting for the owner (a task or mission that said "approve it,
    # then continue") are stopped too: the card must not stay approvable after the owner said Stop. Only approvals desktop
    # operations created are tracked here.
    approvals = deny_pending_approvals()
    return {"requested": len(ops), "stopped": stopped, "still_running": still, "jobs_cancelled": cancelled,
            "approvals_denied": approvals["denied"], "approvals_gone": approvals["gone"], "approvals_unreachable": approvals["unreachable"],
            "stop_errors": [e for op in ops for e in op.stop_errors], "alerts": alerts()}


def _op_by_token(token: str) -> "Operation | None":
    if not token:
        return None
    for op in operations():
        if op.hold is not None and op.hold.token == token:
            return op
    return None


def recover(path: "Path | None" = None) -> dict:
    """The owner's explicit recovery of a PC lock whose holder will not end. It succeeds only when it cannot let a second
    task act while the first may still control the desktop: the holder's process is gone (the normal takeover), or the
    holder is in this process, its stop was requested, and every node job it had in flight is confirmed ended by the kernel
    (its Pc objects are stopped, so its hung worker can send nothing more). Anything else is refused, with the reason.

    Each Pc is stopped and read under its send gate (core.Pc.gate): a worker is either already sending (`submitting`, or
    its job id already named as `current_job`) and recovery is refused, or it can never send."""
    path = LOCK if path is None else path
    snap = _read_lock(path)
    if snap is not None and snap[0] is None and snap[1] == UNREADABLE:
        return {"recovered": False, "reason": "the PC lock file cannot be read right now, so it cannot be told who holds it; "
                                              "ask again in a moment"}
    held = (snap[0] or {}) if snap else {}
    if not held:
        return {"recovered": False, "reason": "the PC lock is not held"}
    if _stale(held, path):
        orphans = _orphan_jobs(held, path)
        left = reconcile_orphans(orphans) if orphans else []
        if left:                                              # the holder's process is gone; the node is not: do not free the PC
            return {"recovered": False, "reason": f"the previous holder ({held.get('task')}) is gone, but its node job(s) "
                                                  f"{', '.join(left)} are not confirmed ended; stop them in the JARVIS console, "
                                                  "then ask again"}
        if not _take_over(path, held, snap[1]):
            return {"recovered": False, "reason": "another process is removing this lock right now; ask again in a moment"}
        return {"recovered": True, "how": "its holder's process was gone or silent beyond its own budget"}
    op = _op_by_token(str(held.get("token") or ""))
    if op is None:
        return {"recovered": False, "reason": f"the PC lock is held by {held.get('task')} in another live process "
                                              f"(pid {held.get('pid')}); stop it there"}
    if op.release_failed:                                     # its work is over: only the lock file could not be removed
        if op.retry_release():
            return {"recovered": True, "how": f"{op.name} had ended; its lock file could not be removed before and now it was"}
        return {"recovered": False, "reason": f"{op.name} has ended, but its PC lock file still cannot be removed "
                                              f"({op.release_failed}); another program may hold it open; ask again in a moment"}
    if not op.stop_flag.is_set():
        return {"recovered": False, "reason": f"{op.name} has not been asked to stop; stop it first, then recover if it does not end"}
    snapshots = []
    for pc in list(op.pcs):
        gate = getattr(pc, "gate", None)
        with (gate if gate is not None else contextlib.nullcontext()):
            try:
                pc.stopped = True                             # from here on it can send nothing (checked under the same gate)
            except Exception:  # noqa: BLE001
                pass
            if getattr(pc, "submitting", False):
                return {"recovered": False, "reason": f"{op.name} is sending a PC job whose id is not known yet, so it cannot be "
                                                      "confirmed ended; not recovering"}
            jobs = set(getattr(pc, "unconfirmed_jobs", ()) or ())
            if getattr(pc, "current_job", None):
                jobs.add(pc.current_job)
        snapshots.append((pc, jobs))
    unconfirmed = [j for pc, jobs in snapshots for j in jobs if not _job_final(pc, j)]
    if unconfirmed:
        return {"recovered": False, "reason": f"the node has not confirmed that job(s) {sorted(unconfirmed)} ended, so {op.name} "
                                              "may still control the desktop; not recovering"}
    d = op.describe()
    op.abandon("recovered by the owner after its stop was confirmed")
    if op.release_failed:
        return {"recovered": False, "reason": f"{op.name} was abandoned, but its PC lock file could not be removed "
                                              f"({op.release_failed}); ask again in a moment", "operation": d}
    return {"recovered": True, "how": "its stop was requested and all its node jobs are confirmed ended; its worker was abandoned "
                                      "(it can send nothing more)", "worker_still_running": d.get("workers", 0) > 0, "operation": d}


def settle(hold: "Hold") -> None:
    """The task or mission that took `hold` is over with it. The PC lock is NOT necessarily released now: it is released
    exactly once, by the last of {the entry that started the operation, its workers, its node jobs the kernel has not
    confirmed ended}. A lock-only operation (made by acquire() for this holder) has the holder as its entry; an operation
    given to acquire() by a caller is ended by that caller (Operation.entry_exit). Use this, never release(), at the end of
    a task or mission (review H1: release() lets go even while a cancelled node job may still control the desktop)."""
    op = hold.op
    if op is None or op.hold is not hold:
        release(hold)
    elif op.auto:
        op.entry_exit()


# ---------------------------------------------------------------- execution lock
def _alive(pid: int) -> bool:
    if int(pid or 0) <= 0:
        return False
    if os.name != "nt":
        # Not Windows (tests in the Linux container): the Windows call below always failed here, so every lock owner
        # looked alive and a crashed task's lock was never taken over. A zombie has exited, so it is not alive.
        import psutil
        try:
            return psutil.Process(int(pid)).status() != psutil.STATUS_ZOMBIE
        except psutil.NoSuchProcess:
            return False
        except Exception:  # noqa: BLE001 - unsure: keep the lock (never take a live task's lock)
            return True
    try:
        import ctypes
        h = ctypes.windll.kernel32.OpenProcess(0x1000, False, int(pid))      # PROCESS_QUERY_LIMITED_INFORMATION
        if not h:
            return False
        code = ctypes.c_ulong()
        ctypes.windll.kernel32.GetExitCodeProcess(h, ctypes.byref(code))
        ctypes.windll.kernel32.CloseHandle(h)
        return code.value == 259                                                # STILL_ACTIVE
    except Exception:  # noqa: BLE001
        return True


ACQUIRE_WAIT_S = 3.0            # how long acquire() keeps looking while another process removes a stale lock
RELEASE_WAIT_S = 5.0            # how long release() waits for a remover that holds the claim on its lock
UNWRITTEN_GRACE_S = 30          # a lock file still empty after this long belongs to a writer that died mid-create
STALE_GRACE_S = 60              # added to the HOLDER's own budget before its silent lock counts as abandoned
HEARTBEAT_S = 10.0              # how often a holder refreshes its heartbeat while it runs
# A lock written before holders declared their own budget (the old format: task, pid, at) is judged by the single-task
# budget, as before (bench B-016). The current code always writes its budget, a token and a heartbeat.
LEGACY_BUDGET_S = TOTAL_BUDGET_S
_TOKEN = re.compile(r"^[0-9a-f]{8,64}$")


def _now() -> float:
    return time.time()


def _held(path: Path) -> dict:
    try:
        held = json.loads(path.read_text(encoding="utf-8"))
        return held if isinstance(held, dict) else {}
    except (OSError, ValueError):
        return {}


def _beat_path(path: Path, token: str) -> Path:
    return path.with_name(f"{path.name}.{token}.beat")


def _last_beat(held: dict, path: Path) -> float:
    """When the holder last said it is still working: its heartbeat file, or the time it took the lock."""
    try:
        at = float(held.get("at") or 0)
    except (TypeError, ValueError):
        at = 0.0
    tok = str(held.get("token") or "")
    if _TOKEN.match(tok):
        try:
            return max(at, float(json.loads(_beat_path(path, tok).read_text(encoding="utf-8")).get("beat") or 0))
        except (OSError, ValueError, TypeError, AttributeError):
            pass
    return at


def _holder_budget(held: dict) -> float:
    try:
        b = float(held.get("budget"))
    except (TypeError, ValueError):
        return LEGACY_BUDGET_S
    return b if b > 0 else LEGACY_BUDGET_S


def _stale(held: dict, path: Path) -> bool:
    """Is this lock abandoned? Decided only by what the HOLDER wrote: its process is gone, or it stopped refreshing its
    heartbeat for longer than its OWN declared budget plus grace. The caller's budget plays no part (PC-D2: a single
    task with a 600 s budget took over a live 30-min mission's lock after 660 s)."""
    if not held:
        # Empty or half-written: another task created the file a moment ago and is writing it (bench B-014: 8 threads,
        # 3 "won", because an empty lock read as pid 0 = dead). Only an old unwritten file is a dead writer's leftover.
        try:
            return time.time() - path.stat().st_mtime > UNWRITTEN_GRACE_S
        except OSError:
            return True
    if not _alive(held.get("pid", 0)):
        return True
    tok = str(held.get("token") or "")
    if tok:
        # A holder in THIS process that has not been stopped is alive by definition; its silence can only mean its heartbeat
        # thread died by accident. Restart that thread and do not judge it stale (no file is touched here: a release racing with
        # this look must not find a heartbeat file rewritten behind it).
        with _HOLDS_LOCK:
            mine = next((h for h in _HOLDS.values() if h.token == tok and h.released_at is None), None)
        if mine is not None and not mine._stop.is_set():
            mine._ensure_thread()
            return False
    return _now() - _last_beat(held, path) > _holder_budget(held) + STALE_GRACE_S


def _inode(p: Path) -> int:
    try:
        return p.stat().st_ino
    except OSError:
        return -1


def _unlink_inode(p: Path, ino: int) -> None:
    """Remove `p` only if it is still the file with this inode (a writer cleaning up its own failed create)."""
    if _inode(p) == ino:
        _unlink(p)


def _unlink(p: Path, tries: int = 5) -> bool:
    for i in range(tries):                       # Windows: a reader holding the file open makes the delete fail briefly
        try:
            p.unlink()
            return True
        except FileNotFoundError:
            return True
        except OSError:
            if i + 1 < tries:
                time.sleep(0.05)
    return False


def _seam(name: str) -> None:
    """A named point inside the lock's critical sections. Does nothing; the race tests replace it to hold one thread at the
    exact point between two steps while another thread runs through (deterministic interleavings, no sleeps)."""


UNREADABLE = -1      # the "inode" of a lock file that exists but could not be opened/read: nothing can be told about its holder


def _read_lock(path: Path) -> "tuple[dict | None, int] | None":
    """The lock file as ONE observation: (content, inode). None = no such file. (None, UNREADABLE) = it exists but cannot be read
    (a Windows share lock, permissions): CANNOT TELL, never judged stale (review M9). (None, inode) = not yet valid JSON (the
    writer is mid-write); {} = valid but not a lock record. Reading the inode from the
    same open file is what lets a remover prove later that it is looking at the very lock it judged."""
    try:
        with open(path, "rb") as f:
            ino, raw = os.fstat(f.fileno()).st_ino, f.read()
    except FileNotFoundError:
        return None
    except OSError:
        return None, UNREADABLE
    try:
        held = json.loads(raw.decode("utf-8"))
    except ValueError:
        return None, ino
    return (held if isinstance(held, dict) else {}), ino


def _lock_key(held: dict, ino: int) -> str:
    """Names ONE lock instance: its token (unique per holder), or, for an old/unwritten lock without one, its inode."""
    tok = str(held.get("token") or "")
    return tok if _TOKEN.match(tok) else f"i{ino}"


CLAIM_TTL_S = UNWRITTEN_GRACE_S      # a removal claim older than this belongs to a remover that died (a removal takes milliseconds)
CLAIM_GENERATIONS = 8


class _Claim:
    """The exclusive right to REMOVE one lock instance (a takeover of a stale lock, or its holder's release). Exactly one
    claim per instance exists at a time: it is an O_EXCL file named by the instance's key. The lock file is only ever removed
    while holding that claim, after re-reading it and proving it is still the instance (and, for a takeover, still stale), so a
    remove can never hit a fresh lock: a lock cannot be re-created while the old file exists (O_EXCL), and nobody else may
    remove the old file. The lock file is never moved aside, so a fresh lock is never hidden from another acquirer. A claim
    whose remover died is skipped after CLAIM_TTL_S by moving to the next generation of the file name (a name is never
    deleted to be reused while it may still be owned)."""

    def __init__(self, file: Path):
        self.file, self.t0 = file, time.monotonic()

    def fresh(self) -> bool:
        """Still safely inside its time budget: a remover suspended for long must not act on what it judged earlier."""
        return time.monotonic() - self.t0 < CLAIM_TTL_S / 2

    def drop(self) -> None:
        try:
            self.file.unlink()
        except OSError:
            pass


def _claim(path: Path, key: str) -> "_Claim | None":
    """Take the removal claim of the lock instance `key`; None = another remover holds it right now."""
    for gen in range(CLAIM_GENERATIONS):
        f = path.with_name(f"{path.name}.claim.{key}.{gen}")
        for _ in range(3):
            try:
                os.close(os.open(str(f), os.O_CREAT | os.O_EXCL | os.O_WRONLY))
                return _Claim(f)
            except FileExistsError:
                pass
            except OSError:
                return None
            try:
                age = time.time() - f.stat().st_mtime
            except OSError:
                continue                                            # released this very moment: try this name again
            if age <= CLAIM_TTL_S:
                return None                                         # a live remover: it will be done in a moment
            break                                                   # its remover died: the next generation
    return None


ORPHAN_WAIT_S = 10.0             # how long a new holder waits for the node to confirm a crashed holder's jobs ended


def _orphan_jobs(held: dict, path: Path) -> list[str]:
    """The node jobs a (crashed or silent) holder had started, from its heartbeat file."""
    tok = str(held.get("token") or "")
    if not _TOKEN.match(tok):
        return []
    try:
        jobs = json.loads(_beat_path(path, tok).read_text(encoding="utf-8")).get("jobs") or []
    except (OSError, ValueError, TypeError, AttributeError):
        return []
    return [str(j) for j in jobs if isinstance(j, str) and j][:20]


def reconcile_orphans(jobs: list[str], wait_s: "float | None" = None) -> list[str]:
    """Cancel the node jobs a crashed holder left behind and wait (bounded) for the kernel to say each one ended. Returns the
    ones NOT confirmed ended (an empty list = the PC is free of them). A job still running on the node would otherwise go on
    acting on the desktop while the next task starts: the plugin process died, the node did not."""
    if not jobs:
        return []
    wait_s = ORPHAN_WAIT_S if wait_s is None else wait_s
    from . import core
    pc = core.Pc()
    left = []
    for jid in jobs:
        if pc.job_final(jid):
            continue
        try:
            pc.cancel_once(jid)
        except Exception:  # noqa: BLE001 - the console may be down: then it is not confirmed
            pass
        end = time.monotonic() + max(0.0, wait_s)
        while not pc.job_final(jid):
            if time.monotonic() >= end:
                left.append(jid)
                break
            time.sleep(0.25)
    return left


def _take_over(path: Path, held: dict, ino: int) -> bool:
    """Remove a lock that was judged stale (`held`/`ino` is the observation that was judged), atomically with respect to every
    other acquirer and to its holder's own release: only under the removal claim of that very instance, and only after
    re-reading the file and finding it unchanged and still stale (the holder may have released it, a new task may have
    created a fresh lock, a silent holder may have resumed). True = the stale lock is gone (or what is there now is not the one
    judged and is left alone: the caller looks again); False = another remover holds the claim, or this one waited too long."""
    claim = _claim(path, _lock_key(held, ino))
    if claim is None:
        return False
    try:
        now = _read_lock(path)
        if now is None:
            return True                                             # already gone
        cur, cur_ino = now
        cur = {} if cur is None else cur                            # unreadable = not a lock record, like an empty file
        if cur != held or cur_ino != ino or not _stale(cur, path):
            return True                                             # not the lock that was judged stale: never touch it
        _seam("takeover:verified")
        if not claim.fresh() or not _unlink(path):
            return False
        _seam("takeover:removed")
        tok = str(held.get("token") or "")
        if _TOKEN.match(tok):                                       # the abandoned holder's heartbeat file
            _unlink(_beat_path(path, tok), 1)
        return True
    finally:
        claim.drop()


def _holder_text(held: dict, path: Path) -> str:
    """Who holds the PC and how it is doing, in words (the busy answer): kind, age, last sign of life, a requested stop."""
    op = _op_by_token(str(held.get("token") or ""))
    parts = []
    if op is not None:
        d = op.describe()
        parts.append(d["kind"])
        parts.append(f"running {d['age_s']:.0f} s")
        parts.append(f"last sign of life {d['idle_s']:.0f} s ago" + (f" ({d['last']})" if d.get("last") else ""))
        if d.get("stop_requested_s_ago") is not None:
            parts.append(f"stop requested {d['stop_requested_s_ago']:.0f} s ago and it has not ended yet")
        if d.get("lock_release_failed"):
            parts.append("its work has ended, but its lock file could not be removed (" + d["lock_release_failed"][:80] + "); it is "
                         "retried, or the owner can ask to recover the PC lock (recover_pc_lock)")
        if d["state"] == "stuck":
            parts.append("STUCK: its worker shows no progress; a stop is requested. Once the stop is confirmed the owner can "
                         "ask to recover the PC lock (recover_pc_lock)")
        return ", ".join(parts)
    kind = str(held.get("kind") or "")
    if kind:
        parts.append(kind)
    try:
        parts.append(f"running {max(0.0, _now() - float(held.get('at') or 0)):.0f} s")
    except (TypeError, ValueError):
        pass
    beat = _last_beat(held, path)
    if beat:
        parts.append(f"last heartbeat {max(0.0, _now() - beat):.0f} s ago")
    tok = str(held.get("token") or "")
    if _TOKEN.match(tok):
        try:
            info = json.loads(_beat_path(path, tok).read_text(encoding="utf-8"))
            if info.get("stop_requested_at"):
                parts.append(f"stop requested {max(0.0, _now() - float(info['stop_requested_at'])):.0f} s ago and it has not ended yet")
            if info.get("stuck"):
                parts.append("STUCK: no progress from its worker")
            if info.get("release_failed"):
                parts.append("its work has ended but its lock file could not be removed")
        except (OSError, ValueError, TypeError, AttributeError):
            pass
    return ", ".join(parts)


class Hold:
    """The PC-control lock held by one task. While the task runs, a heartbeat thread refreshes it, so no other caller
    can judge a live, working holder stale however long one step takes (a research step may take 16 min). The lock
    file itself is never rewritten after it is created (so a heartbeat can never write over another task's lock); the
    heartbeat goes to a file of its own, named by this holder's token. release() stops it."""

    def __init__(self, task_id: str, path: Path, token: str, budget: float, op: "Operation | None" = None):
        self.task, self.path, self.token, self.budget = task_id, path, token, float(budget)
        self.lost = False
        self.op = op                                   # the operation that owns this lock (stop flag, workers, progress)
        self.released_at: float | None = None
        self.release_claims = 0                        # how often a release was effective: exactly once
        self.release_failed = ""                       # why the lock file could not be removed at the last release (else empty)
        self._rel_lock = threading.Lock()
        self._release_mutex = threading.Lock()         # one release at a time; a second caller sees its outcome
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.jobs: list[str] = []                      # node jobs started under this lock (in the heartbeat file: crash recovery)
        self.beat_errors = 0

    def claim_release(self) -> bool:
        with self._rel_lock:
            if self.released_at is not None:
                return False
            self.released_at = _now()
            self.release_claims += 1
            return True

    def start(self) -> None:
        self._beat()
        t = threading.Thread(target=self._run, name=f"pc-lock-heartbeat-{self.task}", daemon=True)
        t.start()                                      # started BEFORE it is published: stop() must never join an unstarted thread
        self._thread = t

    def _run(self) -> None:
        while not self._stop.wait(HEARTBEAT_S):
            try:
                if self.op is not None:
                    self.op.check_stuck()              # a live heartbeat says nothing about the worker: judge its progress too
                if not self._beat():
                    return
            except Exception as e:  # noqa: BLE001 - the heartbeat must not die silently: a dead one makes a working holder look abandoned
                self.beat_errors += 1
                LOG.error("pc lock heartbeat of %s hit %s (error %d); it keeps going", self.task, type(e).__name__, self.beat_errors)

    def _ensure_thread(self) -> None:
        """A heartbeat thread that is gone while its holder still works would let another task judge the holder abandoned:
        start it again (and say so)."""
        t = self._thread
        if t is not None and not t.is_alive() and not self._stop.is_set() and self.released_at is None:
            LOG.error("pc lock heartbeat of %s had stopped; restarted", self.task)
            t2 = threading.Thread(target=self._run, name=f"pc-lock-heartbeat-{self.task}", daemon=True)
            t2.start()
            self._thread = t2

    def record_job(self, jid: str) -> None:
        """A node job now exists under this lock. It is written to the heartbeat file at once (not at the next beat), so that if
        this process dies, whoever takes the lock over can cancel it before the PC is used again."""
        if jid and jid not in self.jobs:
            self.jobs = (self.jobs + [str(jid)])[-20:]
        self._beat()

    def still_held(self) -> bool:
        """False once the lock file is gone or names another holder: this task must not act any more."""
        self._ensure_thread()
        try:
            raw = self.path.read_text(encoding="utf-8")
        except FileNotFoundError:
            self.lost = True                           # the lock file is never moved aside: no file means it is gone
            return False
        except OSError:
            return not self.lost                       # unreadable for a moment (a Windows share lock): ask again later
        try:
            held = json.loads(raw)
        except ValueError:
            return not self.lost
        if not isinstance(held, dict) or held.get("token") != self.token:
            self.lost = True
        return not self.lost

    def _beat(self) -> bool:
        if self._stop.is_set() or not self.still_held():
            return False
        beat = _beat_path(self.path, self.token)
        tmp = beat.with_name(beat.name + ".tmp")
        try:
            info = {"task": self.task, "beat": _now(), "jobs": list(self.jobs)}
            if self.op is not None:                    # what a refused caller (another process) can say about this holder
                info.update(kind=self.op.kind, stuck=self.op.stuck, release_failed=bool(self.op.release_failed or self.release_failed),
                            stop_requested_at=self.op.stop_requested_at, idle_s=round(self.op.clock() - self.op.last_progress, 1))
            tmp.write_text(json.dumps(info), encoding="utf-8")
            os.replace(tmp, beat)
        except OSError:
            pass                                       # the next beat tries again; the budget allows many misses
        return True

    def stop(self) -> None:
        self._stop.set()
        t = self._thread
        if t is not None and t is not threading.current_thread():
            try:
                t.join(timeout=5)
            except RuntimeError:                       # defensive: a thread that never started has nothing to wait for
                pass

    def resume(self) -> None:
        """A release that could not remove the lock file: the lock is still this holder's, so keep it alive (a live, visible
        lock the owner can recover) instead of letting it look abandoned."""
        self._stop = threading.Event()
        self.start()


_HOLDS: dict[tuple[str, str], Hold] = {}
_HOLDS_LOCK = threading.Lock()


def acquire(task_id: str, path: Path = LOCK, budget: float = TOTAL_BUDGET_S, op: "Operation | None" = None,
            kind: str = "task") -> Hold:
    """One PC task at a time: the one PC-control lock, for single tasks and missions alike. `budget` is THIS task's own
    time budget, written into the lock; it is never used to judge another task's lock. A lock is taken over only when
    its holder's process is gone or the holder stopped refreshing it for longer than its own budget plus grace (and the
    takeover is said so in the new lock). Release with release(hold) in a `finally`.

    `op` is the Operation that owns the lock (it keeps the stop flag, the workers and the progress); without one, the lock
    gets an Operation of its own (`kind`) that ends with the lock. A refused caller is told who holds the PC: kind, age,
    last sign of life, and whether a stop was requested."""
    path.parent.mkdir(parents=True, exist_ok=True)
    token = secrets.token_hex(8)
    give_up = time.monotonic() + ACQUIRE_WAIT_S
    while True:
        try:
            fd = os.open(str(path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            snap = _read_lock(path)
            if snap is None:                           # released this very moment: look again
                if time.monotonic() > give_up:
                    break
                continue
            if snap[0] is None and snap[1] == UNREADABLE:
                # exists but cannot be read: whether its holder is alive cannot be told, so it is NOT treated as an empty
                # (stale) lock; wait a moment, then say so (Busy)
                LOG.warning("pc lock busy: %s asked, but the lock file cannot be read right now; not judged stale", task_id)
                if time.monotonic() > give_up:
                    raise Busy("the PC lock file exists but cannot be read right now, so it cannot be told whether its holder is "
                               "still working; try again in a moment") from None
                time.sleep(0.05)
                continue
            held, ino = snap[0] or {}, snap[1]
            if not _stale(held, path):
                text = _holder_text(held, path)
                LOG.info("pc lock busy: %s asked, held by %s (%s)", task_id, held.get("task") or "starting", text)
                raise Busy(f"another PC task is running ({held.get('task') or 'starting'}{'; ' + text if text else ''}); "
                           "stop it first or wait") from None
            LOG.warning("pc lock of %s is stale (%s); taking it over for %s", held.get("task"), "process gone or silent", task_id)
            orphans = _orphan_jobs(held, path)
            if orphans:
                # The previous holder died (or went silent) with node jobs in flight; the node did not die with it. They are
                # cancelled and confirmed ended BEFORE the lock changes hands, or this task is refused, naming them.
                left = reconcile_orphans(orphans)
                if left:
                    LOG.error("pc lock of %s is stale, but its node job(s) %s are not confirmed ended; %s is refused", held.get("task"), left, task_id)
                    raise Busy(f"the previous PC task ({held.get('task')}) stopped without finishing and its PC job(s) {', '.join(left)} are "
                               "not confirmed ended (the JARVIS console did not confirm the cancel); try again in a moment, or stop "
                               "them in the JARVIS console")
                LOG.warning("pc lock of %s: its node job(s) %s were cancelled and confirmed ended before the takeover", held.get("task"), orphans)
            _seam("acquire:judged_stale")
            if not _take_over(path, held, ino):        # another taker is removing it right now: it will be gone in a moment
                if time.monotonic() > give_up:
                    raise Busy("the PC task lock is being taken over from a stale holder a moment ago; try again in a few seconds")
                time.sleep(0.005)
            continue
        mine = -1
        try:
            _seam("acquire:created")                   # our lock file exists and is still empty
            mine = os.fstat(fd).st_ino
            os.write(fd, json.dumps({"task": task_id, "pid": os.getpid(), "at": _now(), "budget": float(budget),
                                     "token": token, "kind": kind}).encode())
        except BaseException:
            os.close(fd)
            _unlink_inode(path, mine)                  # never leave a half-written lock behind (only our own file)
            raise
        os.close(fd)
        if _inode(path) != mine:                       # our still-empty lock was judged abandoned and removed: not the holder
            if time.monotonic() > give_up:
                break
            continue
        own = op is None
        if own:
            op = Operation(kind, task_id)
            op.auto = True
        hold = Hold(task_id, path, token, budget, op)
        op.hold = hold
        with _HOLDS_LOCK:
            _HOLDS[(str(path), task_id)] = hold
        try:
            hold.start()
        except BaseException:
            release(hold)
            raise
        op._event(f"holds the PC lock ({task_id})", logging.DEBUG)
        return hold
    raise Busy("the PC task lock could not be taken")


RELEASE_UNLINK_ROUNDS = 4        # a lock file that will not be deleted is tried this many rounds, with a growing pause


def _remove_own_lock(path: Path, hold: "Hold | None", task_id: str) -> bool:
    """Remove the lock file if it names this holder (its token, or for a lock-by-name release its task), under the removal
    claim of that lock instance, so a takeover that judged it stale cannot remove it from under us (nor a fresh lock created
    after ours): whoever holds the claim removes it; the other finds it gone or foreign and leaves it alone.

    True = the lock file no longer names this holder (removed, already gone, or another holder's). False = it is still there
    and still ours (or cannot be read to tell) after the retries: NOT released (review M9)."""
    def mine(held: dict) -> bool:
        return held.get("token") == hold.token if (hold is not None and held.get("token")) else held.get("task") == task_id
    for _ in range(5):
        snap = _read_lock(path)
        if snap is None:
            return True
        if snap[0] is not None:
            break
        time.sleep(0.05)                               # unreadable for a moment: try again, a stale lock is the worst outcome
    else:
        return False                                   # still unreadable: it is not known to be gone
    held, ino = snap
    if not mine(held):
        return True
    claim, give_up = None, time.monotonic() + RELEASE_WAIT_S
    while claim is None:
        claim = _claim(path, _lock_key(held, ino))
        if claim is None:
            if time.monotonic() > give_up:
                LOG.warning("pc lock %s: a removal claim on it did not end in %.0f s; releasing without it", task_id, RELEASE_WAIT_S)
                break
            time.sleep(0.005)
    try:
        _seam("release:claimed")
        now = _read_lock(path)
        if now is None:
            return True
        if now[0] is None:
            return False
        if now[1] != ino or not mine(now[0]):
            return True
        for rnd in range(RELEASE_UNLINK_ROUNDS):
            if _unlink(path):
                return True
            if rnd + 1 < RELEASE_UNLINK_ROUNDS:
                time.sleep(min(0.05 * (2 ** rnd), 0.5))
        return False
    finally:
        if claim is not None:
            claim.drop()


def release(task: "str | Hold", path: Path = LOCK) -> bool:
    """Free the lock, but only this holder's: stop its heartbeat, then remove the lock if it still names this holder.
    Effective once per Hold (a second call, e.g. from the entry and from its last worker, does nothing).

    Returns True when the lock is gone. False = the lock file could not be removed (a reader holds it open): the Hold stays
    registered and keeps its heartbeat, `hold.release_failed` says why, the owner's operation is told (Operation.finish) and
    the lock is NEVER reported released. A task or mission calls settle(hold), not this: see settle."""
    hold = task if isinstance(task, Hold) else None
    task_id = hold.task if hold else str(task)
    path = hold.path if hold else path
    with _HOLDS_LOCK:
        known = _HOLDS.get((str(path), task_id))
        hold = hold or known
    if hold is None:
        return _remove_own_lock(path, None, task_id)
    key = (str(path), task_id)
    with hold._release_mutex:
        if hold.released_at is not None:
            return True
        with _HOLDS_LOCK:
            if _HOLDS.get(key) is hold:
                del _HOLDS[key]
        hold.stop()
        if not _remove_own_lock(path, hold, task_id):
            hold.release_failed = "deleting the file failed"
            with _HOLDS_LOCK:
                _HOLDS[key] = hold                     # still ours and still on disk: stays registered
            hold.resume()
            LOG.error("pc lock %s: the lock file could not be removed; it stays held (retrying; recover_pc_lock removes it)", task_id)
            if hold.op is not None:
                hold.op._release_failed(hold)          # visible on the operation, retried in the background
            return False
        hold.release_failed = ""
        hold.claim_release()
        _unlink(_beat_path(path, hold.token), 3)
        _unlink(_beat_path(path, hold.token).with_name(_beat_path(path, hold.token).name + ".tmp"), 1)
    if hold.op is not None and hold.op.auto:
        hold.op.finish("released")                     # a lock-only operation ends with its lock
    return True


def current_task(path: Path = LOCK) -> str | None:
    try:
        return json.loads(path.read_text(encoding="utf-8")).get("task")
    except (OSError, ValueError):
        return None


# ---------------------------------------------------------------- verification: what counts as done, per tool
UNOBSERVED = "reported ok, not observed"       # the uncertainty of a step that passed on the node's "ok" alone (audit PC-D11)
WINDOW_STATES = {"minimize": "minimized", "maximize": "maximized", "restore": "normal"}


def unobserved(what: str) -> str:
    return f"{UNOBSERVED}: {what}"


def _verify_window(step: dict, r: dict) -> tuple[bool, str, str]:
    """A window step. The node checks the result itself for these and says so: minimize/maximize/restore read the window
    state back (`window_state`), focus confirms the target is the foreground window (`foreground`). A result without that
    observation still passes (the node reported ok) but is marked as not observed. `inspect` is a read."""
    act, app = step.get("action", "focus"), step.get("app") or r.get("process") or "the app"
    if act == "inspect":
        return True, f"{r.get('inspected') or app} window text read", ""
    want = WINDOW_STATES.get(act)
    state = r.get("window_state")
    if want and state is not None:
        if state != want:
            return False, f"the {app} window is {state}, not {want}", f"the window state read back after {act} was {state}"
        return True, f"the {app} window is {want} (read back from Windows)", ""
    if act == "focus" and r.get("foreground") is True:
        return True, f"the {app} window is in front (Windows says it is the foreground window)", ""
    return True, f"{act} was sent to the {app} window", unobserved(f"the {app} window's state after {act} was not read back")


def _verify_browser(step: dict, r: dict) -> tuple[bool, str, str]:
    """A browser step other than whoami. Page reads return the page itself; type/press echo what the node sent (compared
    with the request); a click has no result to read."""
    act = step.get("action", "")
    if act in ("read", "find", "tabs"):
        return True, f"the {act} result came back from the page", ""
    if act == "type":
        want, got = len(step.get("text") or ""), r.get("typed_chars")
        if got is not None and got != want:
            return False, f"the node typed {got} of {want} characters", f"typed_chars {got} does not match the request ({want})"
        return True, f"{want} characters were sent to the page element", unobserved(
            "the node counted the characters it sent; the field was not read back" if got is not None else
            "the node did not say how many characters it sent, and the field was not read back")
    if act == "press":
        said = (step.get("text") or "Enter").strip()
        got = r.get("pressed")
        if got is not None and str(got).strip().lower() != said.lower():
            return False, f"the node pressed {got}, not {said}", f"pressed {got} does not match the request ({said})"
        return True, f"the key {step.get('text') or 'Enter'} was sent to the page", unobserved("the page's reaction was not checked")
    if act == "click":
        return True, f"the click on {step.get('target') or 'the element'} was sent", unobserved("the page's reaction was not checked")
    return True, "the action reported success", unobserved("nothing about its effect came back")


def _open_site_doubt(r: dict) -> str:
    """What is and is not proven after open_website, in the owner's words (audit PC-D7). The browser window is the only
    witness without the extension: it proves a Chrome window changed after the launch and, if its title names the site,
    that the page is probably the one asked for. It never proves WHICH profile the window belongs to: the profile button shows
    a display name, and the account itself is checked by whoami. Empty when the profile button matched and the title names
    the site; an unproven profile still completes with this warning (bench L-004)."""
    matches, in_title = r.get("profile_name_matches"), r.get("site_in_title")
    if matches and in_title is not False:
        return ""
    host = r.get("host") or "the site"
    seen = [f"a Chrome window changed after the launch: {'yes' if r.get('verified') == 'window' else 'no'}",
            f"the window title names {host}: " + ("yes" if in_title is True else "no" if in_title is False else "not reported"),
            "the profile: " + ("the profile button shows the expected name" if matches else
                               "unproven (the profile button shows only a display name; the account itself is checked by whoami)")]
    text = "Observed: " + "; ".join(seen) + "."
    if in_title is False:
        text += " The address itself was not read (no browser extension), so the page may not be " + host + "."
    return text


def verify(step: dict, result: dict) -> tuple[bool, str, str]:
    """(passed, what was checked, uncertainty). Only the observed result is used, never the step's intent. A step that
    passes on the node's ok alone says so in its uncertainty ("reported ok, not observed: ..."), so the final summary
    never calls it checked (audit PC-D11); a step the node did observe has none."""
    r = result or {}
    t = step["tool"]
    if r.get("state") in ("waiting_for_permission", "denied"):
        return False, f"held for the owner ({r.get('state')})", ""
    if r.get("ok") is not True:                         # only boolean True: {"ok": "false"} is not success
        return False, "the action did not succeed", str(r.get("error") or r.get("state") or "")[:200]
    if t == "open_website":
        ok = r.get("profile") == step.get("profile") and r.get("verified") == "window"
        if not ok:
            return False, f"Chrome window appeared in profile {r.get('profile')}", "no Chrome window was seen"
        return True, f"Chrome window appeared in profile {r.get('profile')}", _open_site_doubt(r)
    if t == "play_media":
        ok = bool(r.get("playing")) and bool(r.get("same_video"))
        media = " ".join(r.get("windows_media") or [])
        unc = "" if (r.get("title") or "").replace(" - YouTube", "")[:30].lower() in media.lower() else "Windows' media session did not confirm the title"
        return ok, f"video '{(r.get('title') or '')[:60]}' playing, same id as chosen", unc
    if t == "open_app":
        return r.get("verified") == "verified", f"a window for {r.get('opened')} {'appeared' if r.get('verified') == 'verified' else 'was not seen'}", \
            "" if r.get("verified") == "verified" else str(r.get("note") or "")
    if t == "open_file":                                   # the node looks for a window showing the file or folder
        seen = r.get("verified") == "verified"
        return seen, f"{r.get('opened') or step.get('name')} " + ("opened (a window showing it appeared)" if seen else
                                                                "was sent to open, but no window showing it was seen"), \
            "" if seen else str(r.get("note") or "")
    if t == "browser" and step.get("action") == "whoami":
        seen = r.get("seen")
        seen = ", ".join(map(str, seen)) if isinstance(seen, (list, tuple)) else seen
        return bool(r.get("matches")), f"signed-in account {seen} vs expected {r.get('expected')}", ""
    if t == "browser":
        return _verify_browser(step, r)
    if t == "window":
        return _verify_window(step, r)
    if t == "press_key":
        if r.get("done") == "nothing to do":            # the media state was read and nothing needed pressing
            return True, str(r.get("note") or "nothing to do"), ""
        key = step.get("key") or "the key"
        return True, f"{key} was sent to {r.get('process') or step.get('app') or 'the PC'}", unobserved(
            "the key press has no result to read; its effect was not checked")
    if t == "check_pc":
        if any(k in r for k in ("rows", "count", "summary", "sessions", "playing")):
            return True, f"{step.get('check')} read", ""
        return True, f"{step.get('check')} check ran", unobserved("no data came back with it")
    if t == "type_text":                                   # the node reads the field back and fails if the text is absent
        return True, f"the text was typed into {r.get('process') or step.get('app')} and read back from the field", ""
    if t == "open_output":
        seen = r.get("opened_verified") is True
        return seen, f"{r.get('opened') or step.get('path')} " + (
            "opened (a window showing it appeared)" if seen else "was sent to open, but no window showing it was seen"), \
            "" if seen else str(r.get("open_note") or "")[:200]
    if t == "troubleshoot":
        ok = r.get("outcome") in ("fixed and verified", "diagnosed")
        return ok, f"troubleshooter outcome: {r.get('outcome')}", "" if r.get("verified") else "the cause is a hypothesis"
    if t == "research":
        made = step.get("deliverable", "none") != "none"
        if made and not r.get("file"):
            return False, f"no {step.get('deliverable')} file was made", "the research came back without the file"
        if made and r.get("qa_passed") is False:
            return False, f"the {step.get('deliverable')} failed its quality checks", str(r.get("file") or "")
        if step.get("open_after") and made and r.get("opened_verified") is not True:
            return False, f"{step.get('deliverable')} made but no window showing it appeared", \
                str(r.get("file") or "") + (f" ({r['open_note']})" if r.get("open_note") else "")
        what = f"; {step.get('deliverable')} {r.get('file')}" + (" opened" if r.get("opened_verified") else "") if made else ""
        return bool(r.get("summary")), "a report with checked citations came back" + what, ""
    return True, "the action reported success", ""


# ---------------------------------------------------------------- the run
FOLLOW_UP_S = 600
FOLLOW_CUE = re.compile(r"(?i)\b(instead|actually|again|also|too|as well|same|that one|the other|it|that|this|them|there|"
                        r"rather|switch|change it|not that|no,)\b")


def previous(tasks_dir: Path = TASKS, now: float | None = None, within_s: float = FOLLOW_UP_S) -> dict | None:
    """The owner's last PC task if it was recent: its request and what it produced (a made file, a page, an app), so a
    follow-up ("open it", "use the Satish profile instead") can refer to it. Only checked results count as produced."""
    now = time.time() if now is None else now
    files = sorted(tasks_dir.glob("*.json"))[-5:] if tasks_dir.is_dir() else []
    for f in files[-1:]:
        # only the NEWEST task counts: if its record is damaged, "it" is unknown, and an older task's file is not it
        try:
            rec = json.loads(f.read_text(encoding="utf-8"))
            at = float(rec.get("finished") or rec.get("started") or 0)
        except (OSError, ValueError, TypeError, AttributeError):
            try:
                recent = now - f.stat().st_mtime <= within_s
            except OSError:
                recent = True
            return {"request": "", "status": "unreadable"} if recent else None
        if now - at > within_s:
            return None
        out = {"request": rec.get("request", ""), "status": rec.get("status")}
        for s in rec.get("steps", []):
            r, a = s.get("result") or {}, s.get("args") or {}
            if not s.get("passed"):
                continue
            if r.get("file"):
                out["file"] = r["file"]
            if s["tool"] == "open_website" and a.get("url"):
                out.update(url=a["url"], profile=a.get("profile"))
            if s["tool"] == "open_app" and a.get("app"):
                out["app"] = a["app"]
        return out
    return None


class Coordinator:
    def __init__(self, agents: dict[str, Callable[[dict], dict]], profiles: list[dict], decide_fn=None, clock=time.time,
                 on_progress: Callable[[str, str], None] | None = None, tasks_dir: Path = TASKS, lock_path: Path = LOCK,
                 budget_s: float = TOTAL_BUDGET_S, cancelled: Callable[[], bool] = lambda: False,
                 op: "Operation | None" = None):
        self.agents, self.profiles, self.clock = agents, profiles, clock
        self.op = op                                  # the registered operation of this task (the plugin's Stop reaches it)

        def with_context(req):
            # the previous request is shown to the model only when this one reads like a follow-up: otherwise the model
            # mixes the two ("minimize notepad" after "type hello into notepad" came back with the typing again; live
            # run 2026-10-16 15:5x). "open it" is resolved without the model (decide.follow_up).
            prev = previous(tasks_dir, self.clock())
            ctx = (prev or {}).get("request", "") if FOLLOW_CUE.search(req or "") else ""
            return decide.decide(req, profiles, context=ctx, last=prev)
        self.decide_fn = decide_fn or with_context
        self.on_progress = on_progress or (lambda s, d: None)
        self.tasks_dir, self.lock_path, self.budget_s, self.cancelled = tasks_dir, lock_path, budget_s, cancelled

    def _save(self, rec: dict):
        self.tasks_dir.mkdir(parents=True, exist_ok=True)
        (self.tasks_dir / f"{rec['id']}.json").write_text(json.dumps(rec, indent=1, default=str), encoding="utf-8")

    def run(self, request: str, plan: decide.Plan | None = None) -> dict:
        tid = time.strftime("%Y%m%d-%H%M%S-") + secrets.token_hex(3)
        rec = {"id": tid, "request": request, "started": self.clock(), "status": "planning", "steps": [], "agents": []}
        self.on_progress("planning", "Deciding what to do")
        plan = plan or self.decide_fn(request)
        rec["plan"] = {"status": plan.status, "steps": plan.steps, "model": plan.model, "seconds": plan.seconds,
                       "problems": plan.problems, "question": plan.question}
        if plan.status == "clarify":
            rec.update(status="needs_clarification", question=plan.question, finished=self.clock())
            self._save(rec)
            return rec
        if plan.status != "ok":
            rec.update(status="not_understood", error="; ".join(plan.problems)[:300], finished=self.clock())
            self._save(rec)
            return rec
        if plan.steps and plan.steps[0]["tool"] == "answer":
            rec.update(status="not_a_pc_task", route="answer", finished=self.clock())
            self._save(rec)
            return rec
        if plan.steps and plan.steps[0]["tool"] == "stop":
            res = (self.agents.get("stop") or (lambda s: {"ok": True}))({"tool": "stop"})
            res = res if isinstance(res, dict) else {"ok": False, "error": f"the stop agent returned {type(res).__name__}"}
            rec.update(status="stopped" if res.get("ok") is True else "failed", steps=[{"tool": "stop", "result": res}], finished=self.clock())
            self._save(rec)
            return rec
        try:
            hold = acquire(tid, self.lock_path, self.budget_s, op=self.op, kind="task")
        except Busy as e:
            rec.update(status="busy", error=str(e), finished=self.clock())
            self._save(rec)
            return rec
        op = hold.op
        bound = bind(op)                               # the task's PC jobs belong to this operation: Stop reaches them
        cancelled = lambda: self.cancelled() or (op is not None and op.stop_flag.is_set())     # noqa: E731
        try:
            for i, step in enumerate(plan.steps, 1):
                op.touch(f"step {i}")
                if cancelled():
                    rec["status"] = "cancelled"
                    break
                if not hold.still_held():                  # never act once another task holds the PC
                    rec.update(status="failed", error=f"the PC lock was lost before step {i}; stopped so two tasks never act at once")
                    break
                if self.clock() - rec["started"] > self.budget_s:
                    rec.update(status="failed", error=f"time budget of {self.budget_s:.0f} s used up before step {i}")
                    break
                pending = op.unconfirmed_jobs()
                if pending:                                # a node job nothing has confirmed ended may still control the desktop
                    rec.update(status="failed", error=f"step {i} not started: the node has not confirmed that job(s) {pending} ended, "
                                                      "so one of them may still be acting on the PC")
                    break
                who = AGENT.get(step["tool"], "desktop")
                rec["agents"] = sorted(set(rec["agents"]) | {who})
                self.on_progress("working", f"{who} agent: step {i} of {len(plan.steps)} ({step['tool']})")
                entry = {"n": i, "tool": step["tool"], "agent": who, "args": {k: v for k, v in step.items() if k != "tool"},
                         "attempts": 0, "started": self.clock()}
                rec["steps"].append(entry)
                fn = self.agents.get(step["tool"])
                if fn is None:
                    entry.update(result={"ok": False, "error": "no agent for this step"}, passed=False, checked="", uncertainty="")
                    rec.update(status="failed", error=f"no agent can do {step['tool']}")
                    break
                while True:
                    entry["attempts"] += 1
                    try:
                        res = fn(step) or {}
                        if not isinstance(res, dict):          # an HTML error page, a list, a number: not a result
                            res = {"ok": False, "error": f"the agent returned a {type(res).__name__}, not a result object"}
                        passed, checked, unc = verify(step, res)
                    except Exception as e:  # noqa: BLE001 - a failed agent is a failed step, not a crashed coordinator
                        res = {"ok": False, "error": f"{type(e).__name__}: {str(e)[:160]}"}
                        passed, checked, unc = False, "the action did not succeed", res["error"]
                    entry.update(result=_slim(res), passed=passed, checked=checked, uncertainty=unc, finished=self.clock())
                    transient = not passed and TRANSIENT.search(str(res.get("error") or "")) and res.get("state") not in (
                        "waiting_for_permission", "denied", "cancelled")
                    if (transient and repeat_safe(step) and entry["attempts"] <= MAX_RETRIES and not cancelled()
                            and not op.unconfirmed_jobs()):
                        self.on_progress("working", f"{who} agent: retrying step {i} once ({str(res.get('error'))[:60]})")
                        continue
                    break
                if res.get("state") == "waiting_for_permission":
                    rec.update(status="waiting_for_permission", approval_id=res.get("approval_id"), how=res.get("how"))
                    break
                if not passed and (res.get("state") == "cancelled" or cancelled()):
                    rec.update(status="cancelled", error=f"stopped by the owner during step {i} ({step['tool']})")
                    break                                  # the owner's stop, not a failure of the step
                if not passed:
                    rec.update(status="failed" if res.get("state") != "denied" else "denied",
                               error=f"step {i} ({step['tool']}): {unc or checked}"[:300])
                    break
            else:
                rec["status"] = "completed"
        finally:
            unbind(bound)
            settle(hold)               # NOT release(): the operation keeps the lock while a node job is unconfirmed (review H1)
        rec["finished"] = self.clock()
        rec["seconds"] = round(rec["finished"] - rec["started"], 1)
        rec["uncertain"] = [f"step {s['n']}: {s['uncertainty']}" for s in rec["steps"] if s.get("uncertainty")]
        self.on_progress("idle", "")
        self._save(rec)
        return rec


def _slim(r: dict) -> dict:
    """What the record keeps of a result: no local file paths with screen or page text, nothing long."""
    out = {}
    for k, v in (r or {}).items():
        if k in ("local_result", "page"):
            continue
        out[k] = v if not isinstance(v, str) else v[:300]
    return out


def summary(rec: dict) -> str:
    """One honest sentence for the owner."""
    st = rec.get("status")
    if st == "completed":
        done = "; ".join(s["checked"] for s in rec["steps"] if s.get("passed"))
        unc = (" (not certain: " + "; ".join(rec.get("uncertain") or []) + ")") if rec.get("uncertain") else ""
        # a step that passed on the node's "ok" alone was reported, not observed: the owner must not read "Done" as "checked"
        guess = any(UNOBSERVED in str(u) for u in rec.get("uncertain") or [])
        # parts of the request the plan had to leave out (an unknown or unsupported step) were NOT done: say so
        dropped = [p for p in (rec.get("plan") or {}).get("problems") or [] if p]
        left = (" Not done: " + "; ".join(dropped)[:200] + ".") if dropped else ""
        return f"{'Done, but not everything was observed' if guess else 'Done'}: {done}{unc}.{left}"
    if st == "needs_clarification":
        return rec.get("question") or "Could you say a bit more?"
    if st == "stopped":
        return "Stopped, as you asked."
    if st == "waiting_for_permission":
        # nothing resumes the task by itself: name what waits and what the owner does next
        last = (rec.get("steps") or [{}])[-1]
        args = last.get("args") or {}
        what = " ".join(str(x) for x in (last.get("tool"), args.get("action"), args.get("target") or args.get("app") or args.get("site")) if x)
        return (f"This needs your approval first{': ' + what if what else ''}. Approve it in the JARVIS console with your PIN, "
                f"then ask me to continue" + (f" (approval {rec['approval_id']})" if rec.get("approval_id") else "") + ".")
    if st == "busy":
        return rec.get("error", "Another PC task is running.")
    if st == "not_a_pc_task":
        return "That is not a PC task; passing it on."
    if st == "cancelled":
        # what already happened stays on the PC (a window opened, a file saved): the owner must hear it
        before = "; ".join(s["checked"] for s in rec.get("steps") or [] if s.get("passed") and s.get("checked"))
        return "Stopped." + (f" Before the stop: {before}." if before else "")
    return f"I could not finish: {rec.get('error') or st}."
