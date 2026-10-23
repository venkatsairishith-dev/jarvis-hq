"""The PC lock stays held while a node job is unconfirmed (re-review H1, M7, M8, M9; 2026-10-19).

H1: a cancelled task or mission released the PC lock the moment its coordinator returned, although the node had not confirmed
that its job ended, so a second task could start while the first job could still control the desktop. The task and mission now
hand their lock to the Operation (coordinator.settle): it is released exactly once, by the last of {entry, workers, node jobs
the kernel has not confirmed ended}.
M7: a console that stops answering while a job is followed no longer makes the Pc forget the job.
M8: "not stopped" + "a request is about to be sent" is one step under a gate that recover() reads under too.
M9: an unreadable live lock is never judged stale; a lock file that cannot be deleted keeps the Hold registered.

The real plugin entries (run_pc_task / stop_pc_task), the real Coordinator, the real missions and the real core.Pc talk to the
fake console of test_stop_ownership (no network). Synchronisation is threading.Events the fake sets (a job being polled, the
node confirming), never a sleep.
"""
import asyncio
import os
import threading
import time

import pytest

from jarvis.plugins.desktop import DesktopPlugin
from jarvis.plugins.desktop import coordinator as C
from jarvis.plugins.desktop import core, decide
from jarvis.plugins.desktop.tests.test_stop_ownership import WAIT, Console, Host, cur, until

PLAN = decide.Plan(status="ok", steps=[{"tool": "browser", "action": "read", "profile": "default", "target": "", "text": ""}],
                   model="m", seconds=0, problems=[], question="")


class FlakyConsole(Console):
    """The console of test_stop_ownership, which can stop answering: `fail_polls` job polls raise, or `down` makes every job
    poll and cancel raise (the console is not running). `probe` is called on every failed call (what the lock looked like)."""

    def __init__(self, **kw):
        super().__init__(**kw)
        self._f = threading.Lock()
        self.fail_polls, self.down, self.probe = 0, False, None
        self.silent, self.records = 0, []
        self.outage_over, self.silent_enough, self.silent_target = threading.Event(), threading.Event(), 0
        self._was_silent = False

    def __call__(self, method, name, body=None, timeout=20.0):
        if (method == "GET" and name.startswith("job/")) or (method, name) == ("POST", "job/cancel"):
            fail = False
            with self._f:
                if self.down:
                    fail = True
                elif self.fail_polls > 0 and method == "GET":
                    self.fail_polls -= 1
                    fail = True
                if fail:
                    self.silent += 1
                    self._was_silent = True
                    if self.probe:
                        self.records.append(self.probe())
                    if self.silent_target and self.silent >= self.silent_target:
                        self.silent_enough.set()
                elif self._was_silent and method == "GET":
                    self.outage_over.set()
            if fail:
                raise core.DesktopError("the JARVIS console is not answering (URLError); is it running?")
        return super().__call__(method, name, body, timeout)


@pytest.fixture
def world(monkeypatch, tmp_path):
    c = FlakyConsole()

    class FastPc(core.Pc):
        def __init__(self, **kw):
            kw.setdefault("sleep", lambda s: threading.Event().wait(0.002))
            super().__init__(call=c, **kw)
            self.settle_s = 0.05                                  # a cancelled job the node does not confirm: unconfirmed fast
            self.outage_wait_s, self.outage_poll_s, self.outage_poll_max_s = 0.05, 0.005, 0.01   # (ignored by the old Pc)

    real_coordinator = C.Coordinator

    class PlannedCoordinator(real_coordinator):                   # the REAL coordinator, with a fixed plan and a private store
        def __init__(self, *a, **kw):
            kw.setdefault("decide_fn", lambda req: PLAN)
            kw["tasks_dir"], kw["lock_path"] = tmp_path / "tasks", C.LOCK     # (the default lock path was bound at import time)
            super().__init__(*a, **kw)

    monkeypatch.setattr(core, "Pc", FastPc)
    monkeypatch.setattr(core, "set_activity", lambda *a, **k: None)
    monkeypatch.setattr(C, "Coordinator", PlannedCoordinator)
    monkeypatch.setattr(C, "STOP_WAIT_S", 0.5)
    monkeypatch.setattr(C, "REAP_POLL_S", 0.005)
    return c


def task_op():
    ops = [op for op in C.operations() if op.kind == "task"]
    assert len(ops) == 1, [op.describe() for op in C.operations()]
    return ops[0]


# ------------------------------------------------------------------ (1) a cancelled task keeps the lock until the node confirms
def test_a_cancelled_task_keeps_the_pc_lock_until_the_node_confirms_its_job_ended(world):
    async def main():
        h = Host()
        entry = asyncio.ensure_future(DesktopPlugin.run_pc_task(h, request="read the page"))
        await until(world.polled)                                       # the node job runs
        stop = (await DesktopPlugin.stop_pc_task(h)).value
        first = (await asyncio.wait_for(entry, WAIT)).value             # the task has returned: cancelled, job unconfirmed
        out = {"stop": stop, "first": first, "holder": cur(), "op": task_op()}
        out["busy"] = (await DesktopPlugin.run_pc_task(Host(), request="a second task")).value
        out["stop2"] = (await DesktopPlugin.stop_pc_task(h)).value
        out["recover"] = (await DesktopPlugin.recover_pc_lock(h)).value
        out["still_held"], out["done_before_confirm"] = cur(), out["op"].done.is_set()
        world.confirm.set()                                             # the node finally confirms the end of the job
        await asyncio.to_thread(out["op"].done.wait, WAIT)
        out["after"] = cur()
        world.release_job.set()
        out["third"] = (await DesktopPlugin.run_pc_task(Host(), request="a third task")).value
        return out
    r = asyncio.run(main())
    first = r["first"]
    assert first["status"] == "cancelled" and r["holder"] == first["task"], "the lock was let go while the node job was unconfirmed"
    [row] = r["stop"]["still_running"]
    assert row["kind"] == "task" and r["stop"]["stopped"] == [], "Stop must say still_running, not stopped"
    assert r["busy"]["status"] == "busy" and "another PC task is running" in r["busy"]["summary"]
    assert first["task"].rsplit("-", 1)[1] in r["busy"]["summary"], "the busy answer must name the holder (the summary is cleaned of digit runs)"
    assert [d["id"] for d in r["stop2"]["still_running"]] == [row["id"]] and r["stop2"]["stopped"] == []
    assert r["recover"]["recovered"] is False and "has not confirmed" in r["recover"]["reason"]
    assert r["still_held"] == first["task"] and r["done_before_confirm"] is False
    assert r["after"] is None and r["op"].hold.release_claims == 1, "released exactly once, after the confirmation"
    assert r["third"]["status"] == "completed", "a second task is admitted once the first job is confirmed ended"
    assert world.cancels == ["j1"] and C.operations() == []


# ------------------------------------------------------------------ (2) the same for a mission
def test_a_cancelled_mission_keeps_the_pc_lock_until_the_node_confirms_its_job_ended(world, tmp_path, monkeypatch):
    from dbos import DBOS

    from jarvis.plugins.desktop import missions as M
    M.configure(tmp_path)
    M.E.lock_path, M.E.budget_s, M.E.on_cancel = C.LOCK, M.BUDGET_S, (lambda: None)
    M.E.agents = Host()._agents(None)
    M.E.plan_fn = lambda goal, ctx: [{"tool": "browser", "action": "read", "profile": "default"}]
    settled, real_settle = threading.Event(), C.settle

    def spy(hold):
        real_settle(hold)
        settled.set()
    monkeypatch.setattr(C, "settle", spy)
    DBOS.launch()
    try:
        mid = M.start("read the page")
        assert world.polled.wait(WAIT), "the mission's node job never ran"
        stop = C.stop_all("stopped by the owner", wait_s=0.5)
        assert settled.wait(WAIT), "the mission never ended"
        rec = M.load(mid)
        holder = cur()
        [op] = [o for o in C.operations() if o.kind == "mission"]
        with pytest.raises(C.Busy) as busy:
            C.acquire("a-second-task", C.LOCK, 60)
        stop2 = C.stop_all("stopped by the owner", wait_s=0.2)
        recovered = C.recover()
        done_before = op.done.is_set()
        world.confirm.set()
        assert op.done.wait(WAIT)
        after, claims = cur(), op.hold.release_claims
        second = C.acquire("a-second-task", C.LOCK, 60)                 # admitted now
        C.release(second)
    finally:
        DBOS.destroy(destroy_registry=False)
    assert rec["status"] == "cancelled" and holder == mid, "the mission let go of the lock while its job was unconfirmed"
    assert [d["id"] for d in stop["still_running"]] == [mid] and stop["stopped"] == []
    assert mid in str(busy.value) and "mission" in str(busy.value)
    assert [d["id"] for d in stop2["still_running"]] == [mid] and stop2["stopped"] == []
    assert recovered["recovered"] is False and "has not confirmed" in recovered["reason"] and done_before is False
    assert after is None and claims == 1 and world.cancels == ["j1"]


# ------------------------------------------------------------------ (3) the console stops answering while a job is followed
def test_a_console_that_stops_answering_for_a_while_does_not_lose_the_job_or_the_lock(world):
    probe_at = lambda: (cur(), sorted(j for o in C.operations() for pc in o.pcs for j in pc.unconfirmed_jobs))   # noqa: E731

    async def main():
        world.probe, world.fail_polls = probe_at, 3
        h = Host()
        entry = asyncio.ensure_future(DesktopPlugin.run_pc_task(h, request="read the page"))
        await until(world.outage_over)                                  # three polls failed, then the console answered again
        out = {"entry_done": entry.done(), "holder": cur()}
        world.release_job.set()
        out["rec"] = (await asyncio.wait_for(entry, WAIT)).value
        out["after"] = cur()
        return out
    r = asyncio.run(main())
    assert r["entry_done"] is False and r["holder"] is not None, "the job was abandoned during the outage"
    assert len(world.records) == 3 and all(holder == r["holder"] for holder, _ in world.records), "the lock was not held throughout"
    assert all(jobs == ["j1"] for _, jobs in world.records[1:]), "the job was not remembered as unconfirmed while the console was silent"
    assert r["rec"]["status"] == "completed" and r["after"] is None and world.cancels == []


def test_a_console_that_stays_silent_reports_the_job_unconfirmed_and_the_lock_stays_until_it_is_back(world):
    async def main():
        h = Host()
        entry = asyncio.ensure_future(DesktopPlugin.run_pc_task(h, request="read the page"))
        await until(world.polled)
        world.down = True                                               # the console goes away and does not come back (yet)
        rec = (await asyncio.wait_for(entry, WAIT)).value
        op = task_op()
        world.silent_target = world.silent + 4                          # ... and the reaper keeps asking
        await until(world.silent_enough)
        out = {"rec": rec, "holder": cur(), "op": op, "desc": op.describe(), "done": op.done.is_set()}
        out["busy"] = (await DesktopPlugin.run_pc_task(Host(), request="a second task")).value
        out["recover_before_stop"] = C.recover()
        out["stop"] = (await DesktopPlugin.stop_pc_task(h)).value
        out["recover"] = C.recover()
        world.down = False                                              # the console is back; the job has really ended
        world.release_job.set()
        await asyncio.to_thread(op.done.wait, WAIT)
        out["after"] = cur()
        return out
    r = asyncio.run(main())
    step = r["rec"]["steps"][0]
    assert r["rec"]["status"] == "failed" and step["passed"] is False, "an unreachable console must not look like success"
    assert world.cancels == [] and r["rec"]["status"] not in ("completed", "cancelled")
    assert r["holder"] is not None and r["done"] is False, "the lock was let go while the job's end was unknown"
    assert r["desc"]["jobs"] == ["j1"] and r["desc"]["holds_lock"] is True, r["desc"]
    assert r["busy"]["status"] == "busy" and "another PC task is running" in r["busy"]["summary"]
    assert r["recover_before_stop"]["recovered"] is False
    assert r["recover"]["recovered"] is False and "has not confirmed" in r["recover"]["reason"]
    assert [d["id"] for d in r["stop"]["still_running"]] == [r["desc"]["id"]]
    assert r["after"] is None and r["op"].hold.release_claims == 1, "released after the console confirmed the job ended"


def test_pc_wait_job_keeps_the_job_unconfirmed_while_the_console_is_silent_and_never_calls_it_done_or_stopped():
    """Unit level: no operation, no lock; just what Pc.wait_job answers."""
    gets, pauses = [], []

    def call(method, name, body=None):
        if method == "POST" and name == "pc":
            return 202, {"job": "jobU", "status": "queued"}
        if method == "GET" and name == "job/jobU":
            gets.append(name)
            raise core.DesktopError("the JARVIS console is not answering (URLError); is it running?")
        raise AssertionError((method, name))
    pc = core.Pc(call=call, sleep=pauses.append)
    pc.outage_wait_s, pc.outage_poll_s, pc.outage_poll_max_s = 1.0, 0.1, 0.4
    out = pc.run({"op": "ui"}, 30, "x")
    assert out["state"] == "failed" and out["unconfirmed"] is True, out
    assert out["state"] not in ("done", "cancelled") and out["result"]["ok"] is False
    assert pc.unconfirmed_jobs == {"jobU"}, "the job must stay in unconfirmed_jobs"
    assert pauses and pauses == sorted(pauses) and max(pauses) <= 0.4 and sum(pauses[:-1]) < 1.0 <= sum(pauses), pauses   # bounded backoff
    assert len(gets) == len(pauses) + 1


# ------------------------------------------------------------------ (4) the normal fast path is unchanged
def test_a_finished_task_releases_the_lock_at_once_and_exactly_once(world):
    world.release_job.set()                                             # the node finishes the job on the first poll

    async def main():
        rec = (await DesktopPlugin.run_pc_task(Host(), request="read the page")).value
        return rec, cur(), C.operations()
    rec, holder, ops = asyncio.run(main())
    assert rec["status"] == "completed" and holder is None and ops == []
    assert world.cancels == []


def test_settle_with_no_unconfirmed_job_releases_immediately():
    hold = C.acquire("quick", C.LOCK, 60)
    C.settle(hold)
    assert cur() is None and hold.release_claims == 1 and hold.op.done.is_set()
    C.settle(hold)                                                       # a second call does nothing
    assert hold.release_claims == 1


# ------------------------------------------------------------------ (5) no two holders under concurrent stop / recover / reaper / acquirer
@pytest.mark.parametrize("confirms", [True, False])
def test_no_double_holder_and_no_acquirer_while_a_job_is_unconfirmed_under_concurrent_stop_recover_reaper_and_acquirer(
        confirms, monkeypatch, tmp_path):
    """Each round is a REAL Coordinator task (as run_pc_task runs it: a worker thread, then the entry) whose node job is
    cancelled while five threads race: Stop, recover(), the entry's exit (which starts the reaper), the node's confirmation
    and a second task that keeps asking for the lock."""
    monkeypatch.setattr(C, "REAP_POLL_S", 0.002)
    rounds = 12
    live, violations, admitted_early = [0], [], []
    live_lock = threading.Lock()
    for n in range(rounds):
        console = Console()
        op = C.begin("task", f"race-{n}")
        pc = op.new_pc(call=console, sleep=lambda s: threading.Event().wait(0.001))
        pc.settle_s = 0.0

        def agent(step, pc=pc):
            res = pc.run({"op": "ui", "action": "focus"}, 60, "x")
            r = dict(res.get("result") or {})
            r["state"], r["ok"] = res.get("state"), bool(r.get("ok")) and res.get("state") == "done"
            return r
        coordinator = C.Coordinator({"browser": agent}, [], decide_fn=lambda req: PLAN, tasks_dir=tmp_path / f"t{n}",
                                    lock_path=C.LOCK, op=op, cancelled=op.stop_flag.is_set)
        op.worker_start()

        def worker():
            try:
                coordinator.run("x")
            finally:
                op.worker_done()
        w = threading.Thread(target=worker)
        w.start()
        assert console.polled.wait(WAIT)
        hold = op.hold
        barrier = threading.Barrier(5)
        result = {"acquired": 0}

        def stopper():
            barrier.wait(WAIT)
            op.request_stop("race")
            op.cancel_jobs()

        def recoverer():
            barrier.wait(WAIT)
            for _ in range(8):
                C.recover()

        def entry():
            barrier.wait(WAIT)
            w.join(WAIT)
            op.entry_exit()                                              # the reaper starts here when a job is unconfirmed

        def confirmer():
            barrier.wait(WAIT)
            if confirms:
                console.confirm.set()

        def acquirer():
            barrier.wait(WAIT)
            for _ in range(600 if confirms else 120):        # (a refused acquirer is not slow: it only checks the lock)
                try:
                    mine = C.acquire(f"new-{n}", C.LOCK, 60)
                except C.Busy:
                    threading.Event().wait(0.002)
                    continue
                with live_lock:
                    live[0] += 1
                    if live[0] != 1:
                        violations.append(("two holders", n))
                if console.jobs["j1"]["state"] == "running":
                    admitted_early.append(n)                             # admitted while the node job was not confirmed ended
                if not mine.still_held():
                    violations.append(("lock lost by the new holder", n))
                with live_lock:
                    live[0] -= 1
                C.release(mine)
                result["acquired"] += 1
                return
        ts = [threading.Thread(target=f) for f in (stopper, recoverer, entry, confirmer, acquirer)]
        for t in ts:
            t.start()
        for t in ts:
            t.join(WAIT * 2)
        assert not any(t.is_alive() for t in ts)
        if confirms:
            assert result["acquired"] == 1, f"round {n}: the lock was never freed after the node confirmed"
        else:
            assert result["acquired"] == 0, f"round {n}: a second task acquired while the node job was unconfirmed"
            assert cur() == hold.task and not op.done.is_set()
            console.confirm.set()                                        # end the round: the node confirms, the reaper frees it
        assert op.done.wait(WAIT) and hold.release_claims == 1 and cur() is None, f"round {n}"
    assert violations == [] and admitted_early == []


# ------------------------------------------------------------------ (6) M8: recover() against a worker about to send
def _op_with_pc(console, **kw):
    hold = C.acquire("m8-task", C.LOCK, 600, op=C.begin("task", "m8-task"))
    pc = hold.op.new_pc(call=console, sleep=lambda s: threading.Event().wait(0.001), **kw)
    pc.settle_s = 0.0
    hold.op.worker_start()
    return hold, pc


def test_recover_between_the_stopped_check_and_the_send_either_refuses_or_the_request_is_never_sent():
    console = Console()
    hold, pc = _op_with_pc(console)
    op = hold.op
    at_gate, go = threading.Event(), threading.Event()
    real_submit = pc.submit

    def held_submit(body):                                               # the worker has passed Pc.run's `if self.stopped`
        at_gate.set()
        assert go.wait(WAIT)
        return real_submit(body)
    pc.submit = held_submit
    out = {}
    t = threading.Thread(target=lambda: out.setdefault("r", pc.run({"op": "ui", "action": "focus"}, 5, "x")))
    t.start()
    assert at_gate.wait(WAIT)
    op.request_stop("test")
    rec = C.recover()
    second = None
    if rec["recovered"]:
        second = C.acquire("second", C.LOCK, 60)                         # the lock is free: a second task is admitted ...
    go.set()
    t.join(WAIT)
    posted = any(c[:2] == ("POST", "pc") for c in console.calls)
    if second is not None:
        C.release(second)
    assert rec["recovered"] is False or posted is False, "recover() freed the lock and the first task's POST /pc still went out"
    assert out["r"]["state"] == "cancelled"


def test_recover_while_a_request_is_on_the_wire_is_refused_and_then_the_job_id_must_be_confirmed():
    sent, answer = threading.Event(), threading.Event()
    inner = Console()

    def call(method, name, body=None, timeout=20.0):
        if (method, name) == ("POST", "pc"):
            sent.set()
            assert answer.wait(WAIT)                                     # the request is on the wire; the answer is not back
        return inner(method, name, body, timeout)
    hold, pc = _op_with_pc(call)
    op = hold.op
    t = threading.Thread(target=lambda: pc.run({"op": "ui", "action": "focus"}, 5, "x"))
    t.start()
    assert sent.wait(WAIT)
    op.request_stop("test")
    mid_send = C.recover()
    answer.set()
    assert inner.polled.wait(WAIT)
    t.join(WAIT)                                                         # the job was cancelled; the node has not confirmed
    unconfirmed = C.recover()
    inner.confirm.set()
    confirmed = C.recover()
    assert mid_send["recovered"] is False and "not known yet" in mid_send["reason"]
    assert unconfirmed["recovered"] is False and "has not confirmed" in unconfirmed["reason"]
    assert confirmed["recovered"] is True


def test_the_job_id_is_named_the_moment_the_request_returns_so_recover_never_sees_a_job_nobody_names():
    """Between `POST /pc` returning and wait_job starting, the job existed in neither `submitting` nor `current_job`."""
    console = Console()
    hold, pc = _op_with_pc(console)
    at_wait, go = threading.Event(), threading.Event()
    real_wait = pc.wait_job

    def held_wait(jid, timeout):
        at_wait.set()
        assert go.wait(WAIT)
        return real_wait(jid, timeout)
    pc.wait_job = held_wait
    t = threading.Thread(target=lambda: pc.run({"op": "ui", "action": "focus"}, 5, "x"))
    t.start()
    assert at_wait.wait(WAIT)
    hold.op.request_stop("test")
    rec = C.recover()                                                    # the job j1 runs on the node and nothing confirmed it ended
    go.set()
    t.join(WAIT)
    assert rec["recovered"] is False, "recover() freed the lock while a node job existed that nothing had confirmed ended"


# ------------------------------------------------------------------ (7) M9: unreadable lock, failed unlink
def _unreadable(path):
    os.chmod(path, 0)
    if os.access(path, os.R_OK):                                         # root reads anything
        os.chmod(path, 0o644)
        pytest.skip("this user can read a mode-000 file")


def test_an_unreadable_live_lock_is_not_judged_stale(monkeypatch):
    monkeypatch.setattr(C, "ACQUIRE_WAIT_S", 0.2)
    live = C.acquire("live-holder", C.LOCK, 600)
    old = time.time() - 100                                              # older than the unwritten-lock grace
    os.utime(C.LOCK, (old, old))
    _unreadable(C.LOCK)
    try:
        with pytest.raises(C.Busy) as e:
            C.acquire("intruder", C.LOCK, 600)
        rec = C.recover()
    finally:
        os.chmod(C.LOCK, 0o644)
    assert "cannot be read" in str(e.value)
    assert rec["recovered"] is False and "cannot be read" in rec["reason"]
    assert live.still_held() and C.current_task(C.LOCK) == "live-holder"
    C.release(live)
    assert cur() is None


class _Undeletable:
    """Windows: a scanner or reader holds the lock file open, so every delete fails, until `on` is turned off."""

    def __init__(self, monkeypatch, path):
        self.on, self.path, self.real = True, path, C._unlink
        monkeypatch.setattr(C, "_unlink", self)
        monkeypatch.setattr(C, "RELEASE_UNLINK_ROUNDS", 2, raising=False)

    def __call__(self, p, tries=5):
        if self.on and os.fspath(p) == os.fspath(self.path):
            return False
        return self.real(p, tries)


def test_a_failed_unlink_at_release_keeps_the_hold_registered_and_is_reported_not_released(monkeypatch):
    undeletable = _Undeletable(monkeypatch, C.LOCK)
    monkeypatch.setattr(C, "RELEASE_RETRY_S", 3600.0, raising=False)    # no background retry in this test: recovery does it
    hold = C.acquire("task-1", C.LOCK, 600)
    op = hold.op
    released = C.release(hold)
    assert released is False, "release() claimed success although the lock file is still there"
    assert C.LOCK.exists() and hold.released_at is None and hold.release_claims == 0
    assert C._HOLDS.get((str(C.LOCK), "task-1")) is hold, "the Hold was dropped from the registry"
    assert op.done.is_set() is False and op.state() == "release_failed" and "lock_release_failed" in op.describe()
    with pytest.raises(C.Busy) as busy:
        C.acquire("task-2", C.LOCK, 60)
    assert "could not be removed" in str(busy.value) and "recover_pc_lock" in str(busy.value)
    still = C.recover()
    assert still["recovered"] is False and "cannot be removed" in still["reason"] and C.LOCK.exists()
    stop = C.stop_all("stopped by the owner", wait_s=0.05)
    assert [d["id"] for d in stop["still_running"]] == ["task-1"] and stop["stopped"] == []
    undeletable.on = False                                               # the reader let go
    fixed = C.recover()
    assert fixed["recovered"] is True and not C.LOCK.exists()
    assert op.done.is_set() and hold.release_claims == 1 and C._HOLDS.get((str(C.LOCK), "task-1")) is None
    C.release(C.acquire("task-3", C.LOCK, 60))


def test_a_lock_file_that_could_not_be_removed_is_retried_in_the_background_until_it_is_gone(monkeypatch):
    undeletable = _Undeletable(monkeypatch, C.LOCK)
    monkeypatch.setattr(C, "RELEASE_RETRY_S", 0.01, raising=False)
    hold = C.acquire("task-1", C.LOCK, 600)
    assert C.release(hold) is False and C.LOCK.exists()
    undeletable.on = False
    assert hold.op.done.wait(WAIT), "the failed release was never retried"
    assert not C.LOCK.exists() and hold.release_claims == 1
