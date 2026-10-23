"""Regression tests added after mutation testing of coordinator.py's lock functions (2026-10-19). Each one pins a branch that a
deliberate code mutation survived: settle() on a hold with no matching operation, _stale() looking up its own hold, the claim
retries, taking over a lock that is already gone, the heartbeat-file cleanup, the acquire() wait, Operation._reap() stopping when
the operation is over, and the single release-retry thread. No servers, no network, no sleeps beyond fractions of a second."""
import os
import subprocess
import sys
import threading
import time
from types import SimpleNamespace
from unittest import mock

import pytest

from jarvis.plugins.desktop import coordinator as C


@pytest.fixture(autouse=True)
def _no_registry_publisher(monkeypatch):
    """The registry snapshot thread is not under test, and the shared teardown waits up to 5 s for it each time it was started."""
    monkeypatch.setattr(C, "_poke_publisher", lambda: None)


@pytest.fixture(autouse=True)
def _end_the_operations_quietly():
    """End every operation this test made (finish, not the conftest's abandon, which waits for a stop to be confirmed)."""
    yield
    for op in C.operations():
        try:
            op.finish("test end")
            if not op.done.is_set():                       # a test that set _finished by hand: unregister it as finish() would have
                op._complete()
        except Exception:                                  # noqa: BLE001
            pass


def dead_pid() -> int:
    p = subprocess.Popen([sys.executable, "-c", "pass"])
    p.wait()
    return p.pid


def lock_record(**over) -> dict:
    rec = {"task": "t-dead", "pid": dead_pid(), "at": time.time(), "budget": 1.0, "token": "ab" * 8, "kind": "task"}
    rec.update(over)
    return rec


def test_settle_releases_a_hold_whose_operation_is_missing_or_is_not_the_one_that_holds_it(tmp_path):
    lock = tmp_path / "a.lock"
    hold = C.acquire("t-one", lock)
    assert lock.exists()
    hold.op = None                                    # no operation at all
    C.settle(hold)
    assert not lock.exists()

    lock2 = tmp_path / "b.lock"
    hold2 = C.acquire("t-two", lock2)
    hold2.op.hold = object()                          # the operation says another hold is its own
    C.settle(hold2)
    assert not lock2.exists()                         # released directly, not left to an operation that does not know it


def test_stale_looks_for_its_own_live_hold_by_token_AND_not_released(tmp_path):
    live = C.acquire("t-live", tmp_path / "live.lock")        # a live hold of this process, with some other token
    stray = tmp_path / "stray.lock"
    old = {"task": "t-stray", "pid": os.getpid(), "at": time.time() - 100_000, "budget": 1.0, "token": "cd" * 8, "kind": "task"}
    stray.write_text(__import__("json").dumps(old))
    assert live.token != old["token"]
    assert C._stale(old, stray) is True                       # same process, but no hold of that token: judged by its silence
    # a released hold with the same token does not keep it alive either
    gone = C.Hold("t-stray", stray, old["token"], 1.0)
    gone.released_at = time.time()
    with C._HOLDS_LOCK:
        C._HOLDS[(str(stray), "t-stray")] = gone
    try:
        assert C._stale(old, stray) is True
    finally:
        with C._HOLDS_LOCK:
            C._HOLDS.pop((str(stray), "t-stray"), None)
    # and a live hold with that token is never stale, however old the record
    mine = C.acquire("t-mine", tmp_path / "mine.lock")
    rec = {"task": "t-mine", "pid": os.getpid(), "at": time.time() - 100_000, "budget": 1.0, "token": mine.token, "kind": "task"}
    assert C._stale(rec, tmp_path / "mine.lock") is False


def test_a_dead_pid_or_a_missing_pid_makes_a_lock_stale_at_once(tmp_path):
    p = tmp_path / "x.lock"
    assert C._stale(lock_record(at=time.time()), p) is True
    rec = lock_record()
    del rec["pid"]
    assert C._stale(rec, p) is True


def test_claim_tries_a_name_three_times_before_moving_to_the_next_generation(tmp_path):
    lock = tmp_path / "c.lock"
    real, calls = os.open, []

    def flaky(path, flags, *a, **k):
        calls.append(path)
        if len(calls) < 3:
            raise FileExistsError(path)
        return real(path, flags, *a, **k)
    with mock.patch.object(C.os, "open", side_effect=flaky):
        claim = C._claim(lock, "key1")
    assert claim is not None
    assert claim.file.name.endswith(".claim.key1.0")          # still generation 0: the third try on the same name won
    assert len(calls) == 3
    claim.drop()


def test_a_live_claim_blocks_and_a_dead_removers_old_claim_is_skipped(tmp_path):
    lock = tmp_path / "d.lock"
    first = C._claim(lock, "k2")
    assert first is not None
    assert C._claim(lock, "k2") is None                       # a live remover holds it
    old = time.time() - C.CLAIM_TTL_S - 5
    os.utime(first.file, (old, old))
    second = C._claim(lock, "k2")
    assert second is not None and second.file.name.endswith(".claim.k2.1")      # next generation; the dead name is never reused
    second.drop()
    first.drop()


def test_take_over_of_a_lock_that_is_already_gone_is_a_success_and_leaves_no_claim(tmp_path):
    lock = tmp_path / "gone.lock"
    held = lock_record()
    assert C._take_over(lock, held, 12345) is True
    assert [p.name for p in tmp_path.iterdir()] == []


def test_take_over_leaves_a_lock_alone_when_it_is_not_the_one_judged(tmp_path):
    lock = tmp_path / "other.lock"
    import json
    lock.write_text(json.dumps(lock_record(token="ee" * 8)))
    ino = lock.stat().st_ino
    assert C._take_over(lock, lock_record(token="ff" * 8), ino) is True        # another instance than the one judged stale
    assert lock.exists()
    assert C._take_over(lock, json.loads(lock.read_text()), ino + 1) is True   # same record, other file
    assert lock.exists()


def test_take_over_removes_the_stale_lock_and_its_heartbeat_file_once(tmp_path):
    import json
    lock = tmp_path / "stale.lock"
    held = lock_record()
    lock.write_text(json.dumps(held))
    beat = C._beat_path(lock, held["token"])
    beat.write_text("{}")
    calls, real = [], C._unlink

    def spy(p, tries=5):
        calls.append((p.name, tries))
        return real(p, tries)
    with mock.patch.object(C, "_unlink", side_effect=spy):
        assert C._take_over(lock, held, lock.stat().st_ino) is True
    assert not lock.exists() and not beat.exists()
    assert (beat.name, 1) in calls                            # the heartbeat file gets one try, the lock itself the default
    assert (lock.name, 5) in calls
    assert not list(tmp_path.glob("*.claim.*"))


def test_acquire_keeps_looking_until_its_wait_is_over_when_the_lock_vanishes_each_time(tmp_path, monkeypatch):
    lock = tmp_path / "flap.lock"
    lock.write_text("{}")
    monkeypatch.setattr(C, "ACQUIRE_WAIT_S", 0.3)
    monkeypatch.setattr(C, "_read_lock", lambda p: None)      # it always looks "released this very moment"
    t0 = time.monotonic()
    with pytest.raises(C.Busy, match="could not be taken"):
        C.acquire("t-flap", lock)
    assert time.monotonic() - t0 >= 0.25


def test_acquire_waits_for_an_unreadable_lock_then_says_so(tmp_path, monkeypatch):
    lock = tmp_path / "unreadable.lock"
    lock.write_text("{}")
    monkeypatch.setattr(C, "ACQUIRE_WAIT_S", 0.3)
    monkeypatch.setattr(C, "_read_lock", lambda p: (None, C.UNREADABLE))
    t0 = time.monotonic()
    with pytest.raises(C.Busy, match="cannot be read right now"):
        C.acquire("t-unreadable", lock)
    assert time.monotonic() - t0 >= 0.25                      # it is never judged stale; it waits a moment first


@pytest.fixture
def fast_reap(monkeypatch):
    monkeypatch.setattr(C, "REAP_POLL_S", 0.01)
    monkeypatch.setattr(C, "REAP_MAX_S", 0.4)


def op_with_a_job_the_node_never_confirms():
    op = C.Operation("task", "t-reap")
    pc = SimpleNamespace(unconfirmed_jobs={"job-1"}, job_final=lambda j: False)
    op.pcs.append(pc)
    return op, pc


@pytest.mark.parametrize("how", ["finished", "abandoned"])
def test_reap_does_not_wait_once_the_operation_is_over(fast_reap, how):
    op, pc = op_with_a_job_the_node_never_confirms()
    if how == "finished":
        op._finished = True
    else:
        op.abandoned = True
    with mock.patch.object(C, "_alert") as alert:
        t0 = time.monotonic()
        op._reap()
    assert time.monotonic() - t0 < 0.2
    alert.assert_not_called()
    assert pc.unconfirmed_jobs == {"job-1"}


def test_reap_gives_up_after_its_limit_with_an_alert_and_releases(fast_reap):
    op, pc = op_with_a_job_the_node_never_confirms()
    with mock.patch.object(C, "_alert") as alert:
        op._reap()
    alert.assert_called_once()
    assert op.done.is_set()
    assert op.outcome.startswith("ended (node confirmation missing")


def test_reap_ends_at_once_when_the_node_confirms(fast_reap):
    op, pc = op_with_a_job_the_node_never_confirms()
    pc.job_final = lambda j: True
    with mock.patch.object(C, "_alert") as alert:
        op._reap()
    alert.assert_not_called()
    assert pc.unconfirmed_jobs == set()
    assert op.outcome == "ended (node confirmation received)"


def test_a_failed_release_starts_one_retry_thread_however_often_it_is_reported(monkeypatch):
    gate = threading.Event()
    monkeypatch.setattr(C.Operation, "_retry_release_loop", lambda self: gate.wait(5))
    op = C.Operation("task", "t-retry")
    hold = SimpleNamespace(release_failed="busy file")
    try:
        op._release_failed(hold)
        op._release_failed(hold)
        op._release_failed(hold)
        threads = [t for t in threading.enumerate() if t.name == "pc-release-t-retry"]
        assert len(threads) == 1
        assert threads[0].daemon is True                     # a stuck retry must never keep the process alive
        assert op.release_failed == "busy file"
    finally:
        gate.set()


def test_retry_release_keeps_the_last_known_reason_when_the_hold_gives_none(monkeypatch):
    op = C.Operation("task", "t-reason")
    hold = SimpleNamespace(release_failed="")
    op.hold = hold
    op.release_failed = "the earlier reason"
    monkeypatch.setattr(C, "release", lambda h: False)
    assert op.retry_release() is False
    assert op.release_failed == "the earlier reason"
    hold.release_failed = "a newer reason"
    assert op.retry_release() is False
    assert op.release_failed == "a newer reason"
    monkeypatch.setattr(C, "release", lambda h: True)
    op._finished = True
    assert op.retry_release() is True
    assert op.release_failed == "" and op.done.is_set()
