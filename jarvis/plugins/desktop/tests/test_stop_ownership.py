"""Stop reaches every PC operation (review H3 / M14, 2026-10-18).

Every PC operation (a task, a mission, a single-action entry, a read-only check) is a coordinator.Operation while it runs.
Stop (stop_pc_task) sets its flag, cancels its node job, denies the approval it waits on, and reports it as stopped only
when its worker really ended; otherwise it is `still_running`, keeps the PC lock, and a second task is refused with the
holder named. The lock is released exactly once, by whoever ends last.

The real core.Pc and the real entries talk to a fake JARVIS console (no network); the synchronisation is threading.Events
the fake console sets (a job being polled, a cancel arriving, the node confirming), never sleeps.
"""
import asyncio
import json
import threading

import pytest

from jarvis.plugins.desktop import DesktopPlugin
from jarvis.plugins.desktop import coordinator as C
from jarvis.plugins.desktop import core, decide

WAIT = 10                       # an upper bound for waiting on an Event: a hang fails the test instead of freezing it
ENDED: list = []                # every Pc.run that returned (so a test can say what had ended when the lock was released)


class Console:
    """The JARVIS console's owner route as core.Pc sees it. Jobs stay `running` until the test lets the node finish them
    (release_job) or confirm a cancel (confirm); `needs_approval(body)` makes a request wait for the owner."""

    def __init__(self, needs_approval=lambda body: False, hold=True):
        self.needs_approval, self.hold = needs_approval, hold
        self.calls, self.executed, self.denied, self.cancels = [], [], [], []
        self.pending, self.jobs = set(), {}
        self.waiting, self.polled = threading.Event(), threading.Event()
        self.cancel_requested, self.confirm, self.release_job = threading.Event(), threading.Event(), threading.Event()
        self._lock = threading.Lock()

    def __call__(self, method, name, body=None, timeout=20.0):
        with self._lock:
            self.calls.append((method, name, body))
            if (method, name) == ("POST", "pc"):
                if body.get("approval_id"):
                    if body["approval_id"] in self.denied:
                        return 403, {"error": "approval denied"}
                    if body["approval_id"] in self.pending:
                        return 409, {"error": "approval pending"}
                elif self.needs_approval(body):
                    self.pending.add("ap1")
                    return 202, {"status": "needs_approval", "approval_id": "ap1", "level": "orange", "reason": "needs the owner"}
                jid = f"j{len(self.jobs) + 1}"
                self.jobs[jid] = {"body": body, "state": "running" if self.hold and body.get("op") != "windows" else "done"}
                self.executed.append(body)
                return 202, {"job": jid, "status": "queued"}
            if (method, name) == ("GET", "state"):
                self.waiting.set()
                return 200, {"approvals": [{"id": a} for a in sorted(self.pending)]}
            if (method, name) == ("POST", "deny"):
                if body["id"] in self.pending:
                    self.pending.discard(body["id"])
                    self.denied.append(body["id"])
                    return 200, {"denied": body["id"]}
                return 404, {"error": "unknown, used or expired approval"}
            if (method, name) == ("POST", "job/cancel"):
                self.cancels.append(body["id"])
                self.cancel_requested.set()
                return 200, {"id": body["id"], "state": "cancelling"}
            if method == "GET" and name.startswith("job/"):
                j = self.jobs[name[4:]]
                if j["state"] == "running":
                    self.polled.set()
                    if self.release_job.is_set():
                        j["state"] = "done"
                    elif self.cancels and self.confirm.is_set():
                        j["state"] = "cancelled"
                result = {"ok": True, "opened": True, "windows": [{"process": "JARVISSampleApp", "title": "JARVIS Sample App"}]
                          } if j["state"] == "done" else {"ok": False, "cancelled": True} if j["state"] == "cancelled" else None
                return 200, {"id": name[4:], "state": j["state"], "result": result}
        return 404, {"error": "not found"}


@pytest.fixture
def console(monkeypatch):
    c = Console()
    ENDED.clear()

    class TrackedPc(core.Pc):
        def __init__(self, **kw):
            kw.setdefault("sleep", lambda s: threading.Event().wait(0.005))     # a short poll; the approval wake is tested apart
            super().__init__(call=c, **kw)

        def run(self, *a, **k):
            try:
                return super().run(*a, **k)
            finally:
                ENDED.append(self)

    c.TrackedPc = TrackedPc
    monkeypatch.setattr(core, "Pc", TrackedPc)
    monkeypatch.setattr(core, "set_activity", lambda *a, **k: None)
    monkeypatch.setattr(C, "STOP_WAIT_S", 5.0)
    return c


class Host:
    """Just enough of the plugin object for the entries and the real step runners."""

    def __init__(self, loop=None, plugins=None):
        for name in ("_pc", "_run", "_one", "_play_youtube", "_stop_pc_tasks", "_agents"):
            setattr(self, name, getattr(DesktopPlugin, name).__get__(self))
        self._cmd_loop, self.plugins = loop, plugins

    async def _missions_ready(self):
        return True


def cur():
    return C.current_task(C.LOCK)


def holding():
    return (cur() or "").startswith("single-")


def run(coro):
    return asyncio.run(coro)


async def until(event):
    assert await asyncio.to_thread(event.wait, WAIT), "the awaited moment never came"


@pytest.fixture
def releases(monkeypatch):
    """Every effective release of a PC lock, with whether the Pc workers had ended by then."""
    seen, real = [], C.release

    def spy(task, path=C.LOCK):
        hold = task if isinstance(task, C.Hold) else None
        seen.append((hold, len(ENDED)))                       # the lock is being released: how many Pc.run had returned by now
        return real(task, path) if hold is None else real(task)
    monkeypatch.setattr(C, "release", spy)
    return seen


def released_once(releases) -> bool:
    holds = {id(h): h for h, _ in releases if h is not None}
    return bool(holds) and all(h.release_claims == 1 for h in holds.values())


ACTIONS = [
    ("run_pc_action", {"action": "focus", "target": "notepad|"}),
    ("run_browser_action", {"action": "read", "profile": "default"}),
    ("open_website", {"url": "https://example.com", "profile": "default"}),
    ("play_youtube", {"query": "a song", "profile": "default"}),
]


# ------------------------------------------------------------------ (1) Stop while awaiting an approval
@pytest.mark.parametrize("name,kw", ACTIONS[:2])
def test_stop_while_an_approval_is_awaited_denies_it_and_never_runs_the_action(name, kw, console, releases):
    console.needs_approval = lambda body: True

    async def main():
        h = Host()
        entry = asyncio.ensure_future(getattr(DesktopPlugin, name)(h, **kw))
        await until(console.waiting)                                  # the Pc is inside wait_approval
        assert holding(), "the exclusive lock must be held while the action waits for the owner"
        stop = await DesktopPlugin.stop_pc_task(h)
        return stop.value, (await asyncio.wait_for(entry, WAIT)).value
    stop, res = run(main())
    assert stop["stopped_jobs"] == 1 and stop["still_running"] == [] and stop["requested"] == 1
    assert console.denied == ["ap1"], "the pending kernel approval must be denied so it can never be used"
    assert console.executed == [], "the action must not run"
    assert res["state"] == "cancelled" and "nothing was done" in str(res.get("error"))
    assert released_once(releases)
    assert releases[0][1] >= 1, "the lock was released before the waiter (Pc.run) had ended"
    assert cur() is None and C.operations() == []


def test_the_approval_wait_wakes_at_once_on_stop_not_at_the_next_poll():
    """The wait polls every 3 s; Stop must end it at once (the stop event wakes the sleep)."""
    c = Console(needs_approval=lambda body: True)
    pc = core.Pc(call=c, on_state=lambda s, d: None)              # the real sleep: a 3 s poll
    out, done = {}, threading.Event()

    def worker():
        out["r"] = pc.run({"op": "ui", "action": "focus"}, 60, "focus")
        done.set()
    threading.Thread(target=worker, daemon=True).start()
    assert c.waiting.wait(WAIT) and pc.pending_approval == "ap1"
    pc.stop()
    assert done.wait(1.5), "Stop did not wake the approval wait (it would end only at the next 3 s poll)"
    assert out["r"]["state"] == "cancelled" and c.denied == ["ap1"] and c.executed == []
    assert pc.pending_approval is None


def test_an_approval_decided_at_the_moment_of_stop_is_not_used():
    """The owner approved just as Stop arrived: the wait ends 'not pending', but a stopped Pc submits nothing more."""
    c = Console(needs_approval=lambda body: True)
    pc = core.Pc(call=c, sleep=lambda s: None)
    real_pending = pc.approval_pending

    def pending_then_approved_and_stopped(aid):
        c.pending.discard(aid)                                   # the owner approved it ...
        pc.stop()                                                # ... and Stop landed in the same moment
        return real_pending(aid)
    pc.approval_pending = pending_then_approved_and_stopped
    r = pc.run({"op": "ui", "action": "focus"}, 60, "focus")
    assert r["state"] == "cancelled" and c.executed == []
    assert not [b for m, n, b in c.calls if n == "pc" and b.get("approval_id")], "the approval must not be used after Stop"
    assert r["result"]["approval_denied"] is False and "expires by itself" in r["result"]["error"]


# ------------------------------------------------------------------ (2)/(3) Stop during an executing action (PC and browser)
@pytest.mark.parametrize("name,kw", ACTIONS)
def test_stop_during_an_executing_action_cancels_the_job_and_frees_the_lock_after_the_node_confirms(name, kw, console, releases):
    async def main():
        h = Host()
        entry = asyncio.ensure_future(getattr(DesktopPlugin, name)(h, **kw))
        await until(console.polled)                                   # the node job is running
        stop = asyncio.ensure_future(DesktopPlugin.stop_pc_task(h))
        await until(console.cancel_requested)
        # the kernel has the cancel, the node has not confirmed the job ended: nothing may be called stopped yet
        assert holding() and not stop.done() and not entry.done()
        console.confirm.set()
        return (await asyncio.wait_for(stop, WAIT)).value, (await asyncio.wait_for(entry, WAIT)).value
    stop, res = run(main())
    assert stop["stopped_jobs"] == 1 and stop["still_running"] == []
    assert console.cancels == ["j1"], "the job is cancelled once, however many parties noticed the Stop"
    assert console.jobs["j1"]["state"] == "cancelled" and len(console.executed) == 1, "nothing further was sent"
    assert res["state"] == "cancelled"
    assert released_once(releases) and releases[0][1] >= 1
    assert cur() is None and C.operations() == []


# ------------------------------------------------------------------ (4) Stop during troubleshooting
def test_stop_during_the_troubleshooter_ends_the_flow_without_sending_anything_more(console, releases):
    async def main():
        h = Host()
        entry = asyncio.ensure_future(DesktopPlugin.run_troubleshooter(h, app="JARVISSampleApp", start_name="JARVIS Sample App"))
        await until(console.polled)
        assert holding()
        stop = asyncio.ensure_future(DesktopPlugin.stop_pc_task(h))
        await until(console.cancel_requested)
        assert holding() and not entry.done()
        console.confirm.set()
        return (await asyncio.wait_for(stop, WAIT)).value, (await asyncio.wait_for(entry, WAIT)).value
    stop, res = run(main())
    assert stop["stopped_jobs"] == 1 and stop["still_running"] == []
    assert res["outcome"].startswith("stopped by the owner") and "nothing further was done" in res["outcome"]
    assert len(console.executed) == 1, f"the flow went on after Stop: {console.executed}"
    assert cur() is None and C.operations() == []


# ------------------------------------------------------------------ (5) Stop during delegated execution (agent run)
class PlannedCoordinator(C.Coordinator):
    plan = None

    def __init__(self, *a, **k):
        super().__init__(*a, decide_fn=lambda r: PlannedCoordinator.plan, tasks_dir=C.LOCK.parent / "tasks", lock_path=C.LOCK, **k)


def _in_a_task(console, monkeypatch, steps, plugins=None):
    monkeypatch.setattr(C, "Coordinator", PlannedCoordinator)
    monkeypatch.setattr(core, "profiles", lambda: [])
    PlannedCoordinator.plan = decide.Plan(steps, "ok")


def test_stop_during_a_tasks_troubleshoot_step_reaches_the_flow_pc_and_waits_for_the_worker(console, monkeypatch, releases):
    """The step runs on the plugin loop, away from the task's thread: its Pc still belongs to the task's operation."""
    _in_a_task(console, monkeypatch, [{"tool": "troubleshoot", "app": "JARVISSampleApp"}])

    async def main():
        h = Host(loop=asyncio.get_running_loop())
        entry = asyncio.ensure_future(DesktopPlugin.run_pc_task(h, request="fix the sample app"))
        await until(console.polled)                                    # the troubleshooter's first node job
        op = [o for o in C.operations() if o.kind == "task"][0]
        assert op.hold is not None and cur() == op.hold.task
        stop = asyncio.ensure_future(DesktopPlugin.stop_pc_task(h))
        await until(console.cancel_requested)
        assert cur() == op.hold.task and not entry.done(), "the task's lock must be held until the node job ended"
        console.confirm.set()
        return (await asyncio.wait_for(stop, WAIT)).value, (await asyncio.wait_for(entry, WAIT)).value
    stop, res = run(main())
    assert stop["stopped_jobs"] == 1 and stop["still_running"] == []
    assert res["status"] == "cancelled"
    assert console.cancels == ["j2"] and len(console.executed) == 2        # j1 = the window lookup, j2 = the diagnosis job
    assert cur() is None and C.operations() == []


def test_stop_during_a_tasks_research_step_returns_only_after_the_delegated_call_really_ended(console, monkeypatch, releases):
    gone, started = threading.Event(), threading.Event()
    _in_a_task(console, monkeypatch, [{"tool": "research", "query": "home solar panels", "deliverable": "none"}])

    class Plugins:
        async def call_entry_json(self, entry, args, timeout=0):
            started.set()
            try:
                await asyncio.Event().wait()                           # the research plugin: never answers
            finally:
                await asyncio.sleep(0)                                 # its own cleanup takes a loop turn
                gone.set()

    async def main():
        h = Host(loop=asyncio.get_running_loop(), plugins=Plugins())
        entry = asyncio.ensure_future(DesktopPlugin.run_pc_task(h, request="research solar panels"))
        await until(started)
        stop = (await DesktopPlugin.stop_pc_task(h)).value
        assert gone.is_set(), "Stop answered while the delegated call was still being torn down"
        return stop, (await asyncio.wait_for(entry, WAIT)).value
    stop, res = run(main())
    assert stop["stopped_jobs"] == 1 and stop["still_running"] == []
    assert res["status"] == "cancelled" and cur() is None and C.operations() == []


def test_stop_reaches_a_mission_through_its_registered_operation():
    """A mission's lock holder is a registered operation whose stop hook is the mission's own cancel."""
    cancelled, hook_ran = [], threading.Event()
    hold = C.acquire("m-1", C.LOCK, 600, kind="mission")
    hold.op.on_stop.append(lambda: (cancelled.append("m-1"), hook_ran.set()))
    res = {}
    t = threading.Thread(target=lambda: res.update(r=C.stop_all("test", wait_s=WAIT)))
    t.start()
    assert hook_ran.wait(WAIT) and hold.op.stop_flag.is_set()          # the mission's own cancel ran and its flag is set ...
    assert cancelled == ["m-1"]
    C.release(hold)                                                   # ... and its worker ends
    t.join(WAIT)
    assert [d["id"] for d in res["r"]["stopped"]] == ["m-1"] and res["r"]["still_running"] == []
    assert cur() is None and C.operations() == []


# ------------------------------------------------------------------ (6) Stop after completion
def test_stop_after_completion_is_a_no_op_and_reports_nothing_as_stopped(console, releases):
    console.hold = False

    async def main():
        h = Host()
        done = (await DesktopPlugin.run_pc_action(h, action="focus", target="notepad|")).value
        calls_before = list(console.calls)
        stop = (await DesktopPlugin.stop_pc_task(h)).value
        return done, stop, calls_before
    done, stop, before = run(main())
    assert done["state"] == "done"
    assert stop["stopped_jobs"] == 0 and stop["stopped"] == [] and stop["still_running"] == [] and stop["requested"] == 0
    assert "Nothing was running" in stop["message"]
    assert console.cancels == [] and console.denied == [], "a finished operation must not be cancelled or denied again"
    assert console.calls == before, "Stop sent something to the console although nothing was running"


# ------------------------------------------------------------------ (7) a cancel racing with completion
def test_a_stop_racing_with_completion_records_exactly_one_outcome_and_releases_the_lock_once(console, releases):
    async def main():
        h = Host()
        entry = asyncio.ensure_future(DesktopPlugin.run_pc_action(h, action="focus", target="notepad|"))
        await until(console.polled)
        console.release_job.set()                                     # the node finishes the job ...
        stop = (await DesktopPlugin.stop_pc_task(h)).value            # ... as Stop arrives
        return stop, (await asyncio.wait_for(entry, WAIT)).value
    stop, res = run(main())
    assert res["state"] == "done", "the job completed: that is the one outcome, not 'cancelled'"
    assert stop["still_running"] == []
    assert released_once(releases), "the lock was released more than once"
    assert cur() is None and C.operations() == []


@pytest.mark.parametrize("round_", range(40))
def test_the_entry_and_the_last_worker_finishing_together_release_exactly_once(round_):
    hold = C.acquire(f"single-race-{round_}", C.LOCK, 60, op=C.begin("single:race", f"single-race-{round_}"))
    op = hold.op
    op.worker_start()
    barrier = threading.Barrier(2)

    def ends(fn):
        barrier.wait(WAIT)
        fn()
    ts = [threading.Thread(target=ends, args=(op.entry_exit,)), threading.Thread(target=ends, args=(op.worker_done,))]
    for t in ts:
        t.start()
    for t in ts:
        t.join(WAIT)
    assert op.done.is_set() and hold.release_claims == 1
    assert cur() is None and C.operations() == []


# ------------------------------------------------------------------ (8) a worker that does not end
class StuckPc:
    """A Pc whose worker ignores Stop until the test lets it go (a hung request)."""

    def __init__(self, **kw):
        self.stopped, self.current_job, self.submitting = False, None, False
        self.unconfirmed_jobs = set()
        self.entered, self.release = threading.Event(), threading.Event()
        self.final = False
        StuckPc.last = self

    def run(self, body, timeout=90.0, what=""):
        self.entered.set()
        self.release.wait(WAIT * 3)
        return {"state": "cancelled", "result": {"ok": False, "error": "stopped"}}

    def cancel(self, jid):
        return {"id": jid}

    def job_final(self, jid):
        return self.final


@pytest.fixture
def stuck(monkeypatch):
    StuckPc.last = None
    monkeypatch.setattr(core, "Pc", StuckPc)
    monkeypatch.setattr(core, "set_activity", lambda *a, **k: None)
    monkeypatch.setattr(C, "STOP_WAIT_S", 0.2)
    yield
    if StuckPc.last is not None:
        StuckPc.last.release.set()


def _start_hung_entry(loop_holder):
    h = Host()
    entry = asyncio.ensure_future(DesktopPlugin.run_pc_action(h, action="focus", target="notepad|"))
    return h, entry


def test_a_worker_that_does_not_end_is_reported_still_running_keeps_the_lock_and_blocks_a_second_task_until_it_ends(stuck):
    async def main():
        h, entry = _start_hung_entry(None)
        await until((await _pc_ready()).entered)
        stop = (await DesktopPlugin.stop_pc_task(h)).value
        # nothing is reported stopped; the lock is still held; the second task is refused and the answer names the holder
        busy = await DesktopPlugin.run_pc_action(Host(), action="focus", target="other|")
        still_held = holding()
        StuckPc.last.release.set()                                    # the worker finally ends
        first = (await asyncio.wait_for(entry, WAIT)).value
        await asyncio.to_thread(_wait_ops_empty)
        return stop, busy, still_held, first
    stop, busy, still_held, first = run(main())
    assert stop["stopped_jobs"] == 0 and stop["stopped"] == []
    [row] = stop["still_running"]
    assert row["kind"] == "single:run_pc_action" and row["state"] == "stopping" and row["stop_requested_s_ago"] >= 0
    assert "have not ended yet" in stop["message"]
    assert still_held, "the lock was released while the worker still ran"
    text = str(busy.error)
    assert type(busy).__name__ == "Err" and "another PC task is running" in text
    for part in ("single:run_pc_action", "running", "last sign of life", "stop requested", "has not ended yet"):
        assert part in text, (part, text)
    assert first["state"] == "cancelled" and cur() is None and C.operations() == []
    after = C.acquire("next-task", C.LOCK, 60)                         # admitted only now that the worker ended
    C.release(after)


async def _pc_ready():
    for _ in range(2000):
        if getattr(StuckPc, "last", None) is not None:
            return StuckPc.last
        await asyncio.sleep(0)
    raise AssertionError("the entry never created its Pc")


def _wait_ops_empty():
    for op in C.operations():
        op.done.wait(WAIT)


def test_recovery_frees_a_hung_holder_only_when_its_stop_is_confirmed_and_its_pc_can_send_nothing(stuck):
    async def main():
        h, entry = _start_hung_entry(None)
        pc = await _pc_ready()
        await until(pc.entered)
        results = {}
        results["not_stopped"] = C.recover(C.LOCK)                     # nobody asked it to stop
        await DesktopPlugin.stop_pc_task(h)
        pc.submitting = True
        results["sending"] = C.recover(C.LOCK)                         # a request is on the wire: its job id is unknown
        pc.submitting, pc.current_job, pc.final = False, "j9", False
        results["unconfirmed"] = C.recover(C.LOCK)                     # the kernel has not said the node job ended
        pc.final = True
        results["recovered"] = C.recover(C.LOCK)
        taken = C.acquire("second-task", C.LOCK, 60)                   # the second task is admitted ... while the old worker hangs
        results["pc_stopped"] = pc.stopped
        pc.release.set()                                               # ... and when that worker finally wakes it harms nothing
        await asyncio.wait_for(entry, WAIT)
        results["second_intact"] = cur() == "second-task"
        C.release(taken)
        return results
    r = run(main())
    assert not r["not_stopped"]["recovered"] and "not been asked to stop" in r["not_stopped"]["reason"]
    assert not r["sending"]["recovered"] and "not known yet" in r["sending"]["reason"]
    assert not r["unconfirmed"]["recovered"] and "has not confirmed" in r["unconfirmed"]["reason"]
    assert r["recovered"]["recovered"] and r["recovered"]["worker_still_running"] is True
    assert r["pc_stopped"] is True, "the abandoned worker's Pc must be stopped: it can send nothing new"
    assert r["second_intact"], "the old worker's late exit must not release the second task's lock"
    assert cur() is None


def test_recovery_refuses_a_lock_held_by_another_live_process():
    hold = C.acquire("single-x", C.LOCK, 60)
    for op in C.operations():                                          # as if the holder's registry lived in another process
        op.hold = None
    res = C.recover(C.LOCK)
    C.release(hold)
    assert res["recovered"] is False and "another live process" in res["reason"]


def test_a_live_heartbeat_with_a_hung_worker_becomes_visibly_stuck_with_a_stop_requested(monkeypatch):
    """The process is alive and the heartbeat fresh, yet no progress: the holder is marked stuck and asked to stop."""
    monkeypatch.setattr(C, "HEARTBEAT_S", 0.01)
    now = [1000.0]
    op = C.Operation("single:run_pc_action", "single-hung", clock=lambda: now[0])
    hold = C.acquire("single-hung", C.LOCK, 600, op=op)
    assert not op.stop_flag.is_set()
    now[0] += C.STUCK_AFTER_S + 1                                      # no progress for longer than the limit
    assert op.stop_flag.wait(WAIT), "a hung worker with a live heartbeat never became visible"
    assert op.stuck and op.state() == "stuck" and op.describe()["stop_requested_s_ago"] >= 0
    with pytest.raises(C.Busy) as busy:
        C.acquire("other", C.LOCK, 60)
    assert "STUCK" in str(busy.value) and "recover_pc_lock" in str(busy.value)
    C.release(hold)
    op.entry_exit()
    assert op.done.is_set() and C.operations() == []


def test_progress_keeps_a_working_operation_from_being_called_stuck():
    now = [0.0]
    op = C.Operation("task", "t", clock=lambda: now[0])
    now[0] += C.STUCK_AFTER_S - 1
    op.touch("step 2")
    now[0] += C.STUCK_AFTER_S - 1
    assert op.check_stuck() is False and not op.stop_flag.is_set()
    now[0] += 2
    assert op.check_stuck() is True and op.stop_flag.is_set()
    op.entry_exit()


def test_a_cancelled_job_the_node_never_confirms_keeps_the_lock_and_is_never_reported_stopped(monkeypatch):
    """The stop was sent, the node never says the job ended: the worker returns, but the operation (and its lock) stays."""
    monkeypatch.setattr(C, "REAP_POLL_S", 0.01)
    c = Console()
    pc = core.Pc(call=c, sleep=lambda s: None)
    pc.settle_s = 0                                                    # give up waiting for the confirmation at once
    op = C.begin("single:run_pc_action", "single-unconfirmed")
    C.acquire("single-unconfirmed", C.LOCK, 60, op=op)
    op.attach_pc(pc)
    op.worker_start()
    box = {}

    def worker():
        box["r"] = pc.run({"op": "ui", "action": "focus"}, 60, "focus")
        op.worker_done()
    t = threading.Thread(target=worker)
    t.start()
    assert c.polled.wait(WAIT)                                         # the job runs on the node
    op.request_stop("test")
    t.join(WAIT)
    op.entry_exit()
    assert box["r"]["state"] == "cancelled" and box["r"]["unconfirmed"] is True
    assert not op.done.is_set() and cur() == "single-unconfirmed", "released while the node job may still run"
    assert op.describe()["jobs"] == ["j1"]
    with pytest.raises(C.Busy):
        C.acquire("second", C.LOCK, 60)
    c.jobs["j1"]["state"] = "cancelled"                                # the node finally confirms
    assert op.done.wait(WAIT) and cur() is None


# ------------------------------------------------------------------ the lock file itself
def test_a_takeover_never_deletes_a_fresh_lock_created_between_the_check_and_the_removal():
    """The stale holder released and a new task created its lock just before the taker removed 'the stale one'. (The taker
    now re-reads the lock under the removal claim and finds a different file; the old test pinned the move-aside design.)"""
    stale = {"task": "old", "pid": 999999, "at": 1.0, "budget": 1.0, "token": "a" * 16}
    fresh = {"task": "new-task", "pid": 4242, "at": 2.0, "budget": 600.0, "token": "b" * 16}
    C.LOCK.write_text(json.dumps(fresh), encoding="utf-8")
    assert C._take_over(C.LOCK, stale, C.LOCK.stat().st_ino + 1) is True        # judged stale from an earlier observation
    assert C._held(C.LOCK) == fresh, "the takeover deleted a fresh lock"
    assert sorted(p.name for p in C.LOCK.parent.iterdir()) == [C.LOCK.name], "no leftover claim / grave files"


def test_acquire_waits_out_a_takeover_in_progress_and_a_holder_never_sees_its_lock_moved_aside(monkeypatch):
    """A takeover in progress is a removal claim on the stale lock: a second taker is told to try again in a moment (never a
    second removal, never a second holder). The lock file is never moved aside, so a holder that finds its file missing
    really lost it (the earlier design hid the file for a moment and needed a grace for that)."""
    stale = {"task": "old", "pid": 999999, "at": 1.0, "budget": 1.0, "token": "a" * 16}
    C.LOCK.write_text(json.dumps(stale), encoding="utf-8")
    ino = C.LOCK.stat().st_ino
    claim = C._claim(C.LOCK, stale["token"])
    assert claim is not None and C._claim(C.LOCK, stale["token"]) is None          # exactly one remover at a time
    monkeypatch.setattr(C, "ACQUIRE_WAIT_S", 0.2)
    with pytest.raises(C.Busy) as e:
        C.acquire("t", C.LOCK, 60)
    assert "being taken over" in str(e.value) and C._held(C.LOCK) == stale
    assert C._take_over(C.LOCK, stale, ino) is False                               # not a second removal
    hold = C.Hold("t", C.LOCK, "c" * 16, 60)
    claim.drop()
    C.LOCK.unlink()
    assert hold.still_held() is False and hold.lost is True                        # no file: really gone
    assert sorted(p.name for p in C.LOCK.parent.iterdir()) == []


def test_busy_names_the_holder_when_a_task_holds_the_pc():
    hold = C.acquire("task-9", C.LOCK, 600)
    try:
        with pytest.raises(C.Busy) as e:
            C.acquire("second", C.LOCK, 60)
    finally:
        C.release(hold)
    text = str(e.value)
    assert "another PC task is running (task-9" in text and "running" in text and "last sign of life" in text


def test_a_job_cancelled_because_it_ran_out_of_time_is_a_failure_with_the_timeout_as_its_reason_not_an_owner_stop():
    """Bench B-022: a timeout used to say `failed / no result within N s`; after the cancel-and-confirm change a job the node
    confirmed as cancelled came back as `cancelled`, which reads as 'the owner stopped it'. It stays a failure."""
    c = Console()
    c.confirm.set()                                                    # the node confirms the cancel at once
    now = [0.0]

    def clock():
        return now[0]
    pc = core.Pc(call=c, sleep=lambda s: now.__setitem__(0, now[0] + s), clock=clock)
    r = pc.run({"op": "diag", "check": "processes"}, 5)
    assert c.cancels == ["j1"]
    assert r["state"] == "failed" and "no result within 5 s" in r["result"]["error"], r
    assert not pc.stopped and "j1" not in pc.unconfirmed_jobs
