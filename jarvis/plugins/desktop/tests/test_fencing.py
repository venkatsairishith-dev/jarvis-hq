"""Fencing of PC jobs (completion A, 2026-10-19): lock ownership token + task id + request counter travel with every job request;
a stale executor (its lock was taken over or recovered) sends nothing; a holder that crashed with a node job in flight leaves
that job cancelled and confirmed ended before the next task (or the owner's recovery) may take the lock.

Real coordinator, real core.Pc, real lock files; the fake world of test_stop_ownership (no network); events, no sleeps."""
import asyncio
import json
import threading

import pytest

from jarvis.plugins.desktop import DesktopPlugin
from jarvis.plugins.desktop import coordinator as C
from jarvis.plugins.desktop import core
from jarvis.plugins.desktop.tests.test_lock_unconfirmed import PLAN, FlakyConsole, world  # noqa: F401  (fixture)
from jarvis.plugins.desktop.tests.test_stop_ownership import WAIT, Host

BODY = {"op": "windows"}


def lock_token():
    return json.loads(C.LOCK.read_text(encoding="utf-8"))["token"]


def test_every_job_request_carries_the_holders_token_task_and_a_counter(world):
    hold = C.acquire("task-A", C.LOCK)
    try:
        pc = hold.op.new_pc()
        pc.run(BODY)
        pc.run(BODY)
    finally:
        C.release(hold)
    sent = [b["holder"] for b in world.executed]
    assert [h["seq"] for h in sent] == [1, 2]
    assert {h["token"] for h in sent} == {hold.token} and {h["task"] for h in sent} == {"task-A"}
    assert hold.token == sent[0]["token"] and len(hold.token) == 16


def test_a_pc_without_a_lock_adds_no_holder(world):
    pc = core.Pc()
    pc.run(BODY)
    assert "holder" not in world.executed[0]


def test_a_stale_executor_whose_lock_was_recovered_sends_nothing(world):
    old = C.acquire("task-OLD", C.LOCK)
    pc = old.op.new_pc()
    C.LOCK.unlink()                                           # the owner's recovery / a takeover removed its lock ...
    new = C.acquire("task-NEW", C.LOCK)                               # ... and a newer task holds the PC
    try:
        r = pc.run(BODY)                                      # the stale executor issues a late job
        assert r["state"] == "cancelled" and "no longer holds the PC lock" in r["result"]["error"] and "nothing was done" in r["result"]["error"]
        assert world.executed == [], "a stale executor started a job under a newer task's lock"
        assert old.op.stop_flag.is_set(), "the stale operation must be stopped, visibly"
        assert lock_token() == new.token, "the newer holder's lock was not touched"
        newer = new.op.new_pc().run(BODY)                     # the newer task works
        assert newer["state"] == "done" and world.executed[0]["holder"]["token"] == new.token
    finally:
        C.release(new)
        C.release(old)
    assert C.LOCK.exists() is False


def test_the_approved_action_of_a_stale_executor_is_not_sent_and_its_approval_is_denied(world):
    c = world
    c.needs_approval = lambda body: True
    hold = C.acquire("task-OLD", C.LOCK)
    pc = hold.op.new_pc()

    def owner_approves_then_the_lock_is_recovered(method, name, body=None, timeout=20.0):
        if (method, name) == ("GET", "state"):
            c.pending.discard("ap1")                          # the owner approved while the task waited ...
            C.LOCK.unlink()                                   # ... and the lock was recovered meanwhile
            C.acquire("task-NEW", C.LOCK)
        return c(method, name, body, timeout)
    pc.call = owner_approves_then_the_lock_is_recovered
    try:
        r = pc.run(BODY)
    finally:
        for h in list(C._HOLDS.values()):
            C.release(h)
    assert r["state"] == "cancelled" and "no longer holds the PC lock" in r["result"]["error"]
    assert c.executed == [], "the approved action was sent by a task that no longer held the PC"


def test_a_lock_lost_between_two_jobs_of_one_task_stops_the_second(world):
    hold = C.acquire("task-A", C.LOCK)
    pc = hold.op.new_pc()
    assert pc.run(BODY)["state"] == "done"
    C.LOCK.unlink()
    assert pc.run(BODY)["state"] == "cancelled"
    assert len(world.executed) == 1
    C.release(hold)


# ---------------------------------------------------------------- plugin crash / restart with an orphaned node job
def crashed_holder(world, jobs, dead_pid=2_000_000_000):
    """A lock + heartbeat file the way a holder that died mid-task leaves them: its pid is gone, its job still runs on the node."""
    token = "ab" * 8
    C.LOCK.write_text(json.dumps({"task": "crashed", "pid": dead_pid, "at": 1.0, "budget": 600.0, "token": token, "kind": "task"}), encoding="utf-8")
    C._beat_path(C.LOCK, token).write_text(json.dumps({"task": "crashed", "beat": 1.0, "jobs": jobs}), encoding="utf-8")
    for j in jobs:
        world.jobs[j] = {"body": {}, "state": "running"}
    return token


def test_a_new_task_after_a_plugin_crash_cancels_the_orphaned_node_job_and_waits_for_the_node(world, monkeypatch):
    monkeypatch.setattr(C, "ORPHAN_WAIT_S", 0.2)
    crashed_holder(world, ["j7"])
    with pytest.raises(C.Busy) as e:                          # the node has not confirmed: the PC is not handed over
        C.acquire("task-B", C.LOCK)
    assert "j7" in str(e.value) and world.cancels == ["j7"]
    assert C.current_task(C.LOCK) == "crashed", "the lock changed hands while the orphaned job may still be running"
    world.confirm.set()                                     # the node confirms the cancel
    hold = C.acquire("task-B", C.LOCK)
    try:
        assert C.current_task(C.LOCK) == "task-B" and world.cancels == ["j7"], "the cancel is asked once"
    finally:
        C.release(hold)


def test_a_finished_orphan_job_does_not_block_the_next_task(world, monkeypatch):
    crashed_holder(world, ["j7"])
    world.jobs["j7"]["state"] = "done"
    hold = C.acquire("task-B", C.LOCK)
    C.release(hold)
    assert world.cancels == []


def test_the_owners_recovery_of_a_crashed_holder_also_waits_for_its_orphaned_job(world, monkeypatch):
    monkeypatch.setattr(C, "ORPHAN_WAIT_S", 0.1)
    crashed_holder(world, ["j7"])
    out = C.recover()
    assert out["recovered"] is False and "j7" in out["reason"] and world.cancels == ["j7"]
    assert C.current_task(C.LOCK) == "crashed", "recover() freed the PC while the orphaned job may still be running"
    world.confirm.set()
    assert C.recover()["recovered"] is True
    assert C.current_task(C.LOCK) is None


def test_a_crashed_holder_with_an_unreachable_console_keeps_the_pc_blocked(world, monkeypatch):
    crashed_holder(world, ["j7"])
    world.down = True                                         # the console does not answer polls or cancels
    monkeypatch.setattr(C, "ORPHAN_WAIT_S", 0.05)
    with pytest.raises(C.Busy):
        C.acquire("task-B", C.LOCK)
    assert C.current_task(C.LOCK) == "crashed"
    assert C.recover()["recovered"] is False


# ---------------------------------------------------------------- two tasks racing with a delayed node acknowledgement
def test_a_second_task_is_never_admitted_before_the_delayed_node_ack(world):
    from jarvis.plugins.desktop.tests.test_stop_ownership import until

    async def main():
        h = Host()
        a = asyncio.ensure_future(DesktopPlugin.run_pc_task(h, request="read the page"))
        await until(world.polled)
        await DesktopPlugin.stop_pc_task(h)
        first = (await asyncio.wait_for(a, WAIT)).value
        refused = []
        for _ in range(5):                                    # B keeps trying while the node's ack is late
            refused.append((await DesktopPlugin.run_pc_task(Host(), request="task B")).value["status"])
        jobs_before_ack = len(world.executed)
        world.confirm.set()                                   # the delayed ack arrives
        op = [o for o in C.operations() if o.kind == "task"][0]
        await asyncio.to_thread(op.done.wait, WAIT)
        world.release_job.set()
        b = (await DesktopPlugin.run_pc_task(Host(), request="task B")).value
        return first, refused, jobs_before_ack, b
    first, refused, before, b = asyncio.run(main())
    assert first["status"] == "cancelled" and refused == ["busy"] * 5
    assert before == 1, "task B started a job before the node had acknowledged the stop of task A"
    assert b["status"] == "completed" and len(world.executed) == 2
    tokens = [x["holder"]["token"] for x in world.executed]
    assert tokens[0] != tokens[1], "each task's jobs carry its own lock token"


# ---------------------------------------------------------------- a heartbeat thread that dies silently
def test_a_dead_heartbeat_thread_of_a_working_holder_does_not_make_it_stale(tmp_path, monkeypatch):
    from unittest import mock
    import time as _t
    real = _t.time
    hold = C.acquire("worker", C.LOCK, budget=600)
    try:
        hold._stop.set()                                       # simulate the thread dying: first stop it ...
        hold._thread.join(5)
        hold._stop = threading.Event()                         # ... but the holder itself is not stopped
        assert not hold._thread.is_alive()
        with mock.patch("time.time", lambda: real() + 5000):   # far beyond budget + grace: a silent holder would be taken over
            with pytest.raises(C.Busy):
                C.acquire("intruder", C.LOCK, budget=60)
        assert C.current_task(C.LOCK) == "worker" and hold._thread.is_alive(), "the heartbeat was restarted"
    finally:
        C.release(hold)


# ---------------------------------------------------------------- a cancel the node never confirms is escalated, not only logged
def test_a_job_the_node_never_confirms_is_escalated_to_the_owner_when_the_reaper_lets_the_lock_go(world, monkeypatch):
    monkeypatch.setattr(C, "REAP_MAX_S", 0.05)
    monkeypatch.setattr(C, "ALERTS", [])
    hold = C.acquire("task-A", C.LOCK)
    pc = hold.op.new_pc()
    pc.unconfirmed_jobs.add("j1")                             # a cancelled job the node never confirms
    world.jobs["j1"] = {"body": {}, "state": "running"}
    C.settle(hold)                                            # the task is over; the node is silent; the reaper gives up
    assert hold.op.done.wait(WAIT)
    [alert] = C.alerts()
    assert alert["jobs"] == ["j1"] and "may still be acting on the PC" in alert["message"] and "Cancel" in alert["message"]
    assert C.snapshot()["alerts"] == [alert], "the registry snapshot carries it"
    res = C.stop_all()
    assert res["alerts"] == [alert], "Stop's answer carries it"
    assert C.LOCK.exists() is False


def test_stopping_a_hold_whose_heartbeat_thread_never_started_does_not_raise(tmp_path):
    """Found by the 20x loop (test_pc_lock never-two-holders[dead]): a release racing the moment a heartbeat thread was created
    but not yet started raised 'cannot join thread before it is started' and left the lock held."""
    hold = C.acquire("w", C.LOCK)
    try:
        hold.stop()
        hold._thread = threading.Thread(target=lambda: None)   # created, never started
        hold.stop()                                            # must not raise
    finally:
        hold._stop = threading.Event()
        C.release(hold)
    assert C.LOCK.exists() is False


# ---------------------------------------------------------------- ST-5: the stop path does not swallow its own failures
def test_a_deny_that_raises_during_stop_is_reported_not_swallowed(world):
    op = C.begin("task", "st5")
    try:
        class BadPc:
            pending_approval, current_job, stopped = "ap9", None, False

            def deny_approval(self, aid):
                raise RuntimeError("console exploded")
        op.attach_pc(BadPc())
        res = C.stop_all("test", 0.05)
        assert any("ap9" in e and "could not be denied" in e for e in res["stop_errors"]), res["stop_errors"]
    finally:
        op.abandon("test teardown") if hasattr(op, "abandon") else None


def test_a_job_cancel_that_raises_during_stop_is_reported_not_swallowed(world):
    op = C.begin("task", "st5b")
    try:
        class BadPc:
            pending_approval, current_job, stopped = None, "jX", False

            def cancel(self, jid):
                raise core.DesktopError("no answer")
        op.attach_pc(BadPc())
        res = C.stop_all("test", 0.05)
        assert any("jX" in e and "may still be running" in e for e in res["stop_errors"]), res["stop_errors"]
    finally:
        op.abandon("test teardown") if hasattr(op, "abandon") else None
