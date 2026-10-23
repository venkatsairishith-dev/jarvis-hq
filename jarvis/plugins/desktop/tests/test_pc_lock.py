"""The one PC-control lock (coordinator.acquire/release), audit PC-D2 (2026-10-18): staleness is decided by the HOLDER's
own budget and heartbeat, never by the caller's budget; a dead holder is reclaimed; exactly one of many racers wins; the
lock is released on an exception and on a cancel, and the threaded work keeps it for as long as it can still act.
Time is moved forward by patching time.time (the lock's only clock); no servers, no network."""
import asyncio
import json
import os
import subprocess
import sys
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from unittest import mock

import pytest

from jarvis.plugins.desktop import coordinator as C
from jarvis.plugins.desktop import decide as D

ROOT = Path(__file__).resolve().parents[4]
REAL_TIME = time.time
OPEN_APP = {"tool": "open_app", "app": "notepad"}
OK_APP = {"ok": True, "verified": "verified", "opened": "notepad"}


@pytest.fixture(autouse=True)
def fast_heartbeat(monkeypatch):
    monkeypatch.setattr(C, "HEARTBEAT_S", 0.02, raising=False)      # a holder that is running beats often


@contextmanager
def later(seconds: float):
    """Pretend `seconds` have passed (for the holder's heartbeat too, as when time really passes while it runs)."""
    with mock.patch("time.time", lambda: REAL_TIME() + seconds):
        time.sleep(0.15)                                            # let a running holder's heartbeat see the new time
        yield


def plan(*steps):
    return D.Plan(list(steps), "ok", model="fake")


def beat_files(lock: Path) -> list:
    return sorted(p.name for p in lock.parent.glob(lock.name + ".*") if p.name.endswith((".beat", ".tmp")))


def heartbeats_alive() -> list:
    return [t.name for t in threading.enumerate() if t.name.startswith("pc-lock-heartbeat")]


def test_a_live_refreshing_holder_is_never_taken_over_whatever_the_callers_budget(tmp_path):
    lock = tmp_path / "pc_task.lock"
    C.acquire("m-mission", lock, budget=30 * 60)                     # a mission in the middle of a long step
    try:
        for passed in (700, 1900, 10_000):                           # PC-D2: a 600 s caller took it over after 660 s
            with later(passed):
                for caller_budget in (1, C.TOTAL_BUDGET_S, 30 * 60, 10 ** 9):
                    with pytest.raises(C.Busy, match="m-mission"):
                        C.acquire("t-single", lock, budget=caller_budget)
            assert C.current_task(lock) == "m-mission"
    finally:
        C.release("m-mission", lock)
    assert not lock.exists() and beat_files(lock) == []


def test_a_single_task_does_not_take_a_running_missions_lock_through_the_coordinator(tmp_path):
    lock = tmp_path / "pc_task.lock"
    C.acquire("m-mission", lock, budget=30 * 60)
    ran = []
    try:
        with later(700):
            rec = C.Coordinator({"open_app": lambda s: ran.append(1) or OK_APP}, [], tasks_dir=tmp_path / "tasks",
                                lock_path=lock).run("open notepad", plan(OPEN_APP))
        assert rec["status"] == "busy" and "m-mission" in rec["error"] and ran == []
        assert "another PC task is running" in C.summary(rec)       # the existing honest answer, no queueing
    finally:
        C.release("m-mission", lock)


def test_a_dead_holder_is_reclaimed_at_once(tmp_path):
    """A real child process takes the lock (heartbeat running) and is killed: its lock is free at once, whatever budget it
    declared, and its heartbeat file is cleaned up."""
    lock = tmp_path / "pc_task.lock"
    child = subprocess.Popen([sys.executable, "-c", (
        "import sys, time; sys.path.insert(0, {root!r}); from pathlib import Path\n"
        "from jarvis.plugins.desktop import coordinator as C\n"
        "C.acquire('crashed', Path({lock!r}), budget=10**6); print('held', flush=True); time.sleep(120)"
    ).format(root=str(ROOT), lock=str(lock))], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
    try:
        assert child.stdout.readline().strip() == "held"
        with pytest.raises(C.Busy, match="crashed"):                 # alive: refused
            C.acquire("next", lock)
    finally:
        child.kill()
        child.wait()
    C.acquire("next", lock)                                          # dead: reclaimed, long before its budget
    assert C.current_task(lock) == "next"
    C.release("next", lock)
    assert not lock.exists() and beat_files(lock) == []


def test_a_holder_whose_heartbeat_stopped_is_reclaimed_only_after_its_own_budget_plus_grace(tmp_path):
    lock = tmp_path / "pc_task.lock"
    hold = C.acquire("hung", lock, budget=1800)
    hold.stop()                                                      # alive process, but it stopped refreshing
    try:
        with later(700):                                             # beyond the CALLER's 600 + 60, within its own
            with pytest.raises(C.Busy, match="hung"):
                C.acquire("t-single", lock, budget=C.TOTAL_BUDGET_S)
        with later(1800 + C.STALE_GRACE_S - 10):
            with pytest.raises(C.Busy):
                C.acquire("t-single", lock, budget=1)
        with later(1800 + C.STALE_GRACE_S + 10):
            C.acquire("t-single", lock, budget=C.TOTAL_BUDGET_S)
        assert C.current_task(lock) == "t-single"
        assert hold.still_held() is False                             # the old holder knows it must not act any more
    finally:
        C.release("t-single", lock)
        C.release(hold)
    assert not lock.exists() and beat_files(lock) == []


def test_a_short_budget_holder_that_went_silent_is_reclaimed_by_a_long_budget_caller(tmp_path):
    lock = tmp_path / "pc_task.lock"
    hold = C.acquire("short", lock, budget=60)
    hold.stop()
    with later(60 + C.STALE_GRACE_S + 10):
        C.acquire("mission", lock, budget=30 * 60)                    # judged by the holder's 60 s, not the caller's 30 min
    assert C.current_task(lock) == "mission"
    C.release("mission", lock)
    C.release(hold)


@pytest.mark.parametrize("left", ["live", "dead", "silent"])
def test_eight_threads_race_one_winner(tmp_path, left):
    lock = tmp_path / "pc_task.lock"
    for _ in range(5):
        old = None
        if left == "live":
            old = C.acquire("holder", lock, budget=600)
        elif left == "dead":
            p = subprocess.Popen([sys.executable, "-c", "pass"])
            p.wait()
            lock.write_text(json.dumps({"task": "crashed", "pid": p.pid, "at": REAL_TIME(), "budget": 10 ** 6, "token": "ab" * 8}))
        else:
            lock.write_text(json.dumps({"task": "silent", "pid": os.getpid(), "at": REAL_TIME() - 5000, "budget": 600, "token": "cd" * 8}))
        won, gate = [], threading.Barrier(8)

        def go(i):
            gate.wait()
            try:
                C.acquire(f"t{i}", lock, budget=600)
                won.append(i)
            except C.Busy:
                pass
        th = [threading.Thread(target=go, args=(i,)) for i in range(8)]
        [t.start() for t in th]
        [t.join(10) for t in th]
        if left == "live":
            assert won == [] and C.current_task(lock) == "holder"
            C.release(old)
        else:
            assert len(won) == 1 and C.current_task(lock) == f"t{won[0]}"
            C.release(f"t{won[0]}", lock)
        assert not lock.exists() and beat_files(lock) == []


def test_released_on_an_exception_even_one_the_coordinator_does_not_catch(tmp_path):
    lock = tmp_path / "pc_task.lock"

    def interrupted(s):
        raise KeyboardInterrupt
    with pytest.raises(KeyboardInterrupt):
        C.Coordinator({"open_app": interrupted}, [], tasks_dir=tmp_path / "tasks", lock_path=lock).run("x", plan(OPEN_APP))
    assert not lock.exists() and beat_files(lock) == [] and heartbeats_alive() == []
    rec = C.Coordinator({"open_app": lambda s: OK_APP}, [], tasks_dir=tmp_path / "tasks", lock_path=lock).run("x", plan(OPEN_APP))
    assert rec["status"] == "completed"


def test_a_cancelled_task_keeps_the_lock_while_its_thread_can_act_and_frees_it_after(tmp_path):
    """run_pc_task runs the coordinator in a worker thread: cancelling the awaiting coroutine does not stop that thread at
    once, so the lock must stay held until the thread really ends, then be freed."""
    lock = tmp_path / "pc_task.lock"
    inside, go_on, stop = threading.Event(), threading.Event(), threading.Event()
    later_steps = []

    def blocking(s):
        inside.set()
        go_on.wait(10)
        return OK_APP
    c = C.Coordinator({"open_app": blocking, "window": lambda s: later_steps.append(1) or {"ok": True}}, [],
                      tasks_dir=tmp_path / "tasks", lock_path=lock, cancelled=stop.is_set)
    box = {}

    async def main():
        task = asyncio.ensure_future(asyncio.to_thread(lambda: box.setdefault("rec", c.run("x", plan(OPEN_APP, {"tool": "window", "app": "notepad", "action": "minimize"})))))
        while not inside.is_set():
            await asyncio.sleep(0.01)
        task.cancel()                                                 # the owner stops it / the entry times out
        stop.set()
        with pytest.raises(asyncio.CancelledError):
            await task
    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(main())
        assert C.current_task(lock) is not None                       # the step is still running: the PC is still taken
        with pytest.raises(C.Busy):
            C.acquire("other", lock)
        go_on.set()
        deadline = time.time() + 10
        while "rec" not in box and time.time() < deadline:
            time.sleep(0.02)
    finally:
        go_on.set()
        loop.close()
    assert box["rec"]["status"] == "cancelled" and later_steps == []
    assert not lock.exists() and beat_files(lock) == [] and heartbeats_alive() == []


def test_a_task_that_lost_its_lock_stops_before_its_next_step(tmp_path):
    lock = tmp_path / "pc_task.lock"
    second = []

    def first(s):                                                    # meanwhile the lock was judged stale and taken
        lock.unlink()
        lock.write_text(json.dumps({"task": "intruder", "pid": os.getpid(), "at": REAL_TIME(), "budget": 600, "token": "ef" * 8}))
        return OK_APP
    rec = C.Coordinator({"open_app": first, "window": lambda s: second.append(1) or {"ok": True}}, [],
                        tasks_dir=tmp_path / "tasks", lock_path=lock).run("x", plan(OPEN_APP, {"tool": "window", "app": "notepad", "action": "minimize"}))
    assert rec["status"] == "failed" and "lock" in rec["error"] and second == []
    assert C.current_task(lock) == "intruder"                         # never removes another task's lock
    lock.unlink()


def test_a_heartbeat_never_writes_over_another_holders_lock(tmp_path):
    lock = tmp_path / "pc_task.lock"
    hold = C.acquire("first", lock, budget=600)
    lock.unlink()
    other = {"task": "second", "pid": os.getpid(), "at": REAL_TIME(), "budget": 600, "token": "12" * 8}
    lock.write_text(json.dumps(other))
    time.sleep(0.15)                                                  # several heartbeats
    assert json.loads(lock.read_text()) == other and hold.lost
    C.release(hold)
    assert json.loads(lock.read_text()) == other                      # release leaves the other holder's lock alone


def test_a_busy_second_request_does_not_clear_the_owners_stop_of_the_running_task(monkeypatch):
    """run_pc_task used one shared flag and reset it at the start of every request: a second request (refused as busy)
    cleared the owner's stop of the task that was running. Each task now has its own stop flag."""
    from jarvis.plugins.desktop import DesktopPlugin
    seen, release = {}, threading.Event()

    class FakeCoordinator:
        def __init__(self, agents, profiles, cancelled=lambda: False, **kw):
            self.cancelled = cancelled

        def run(self, request):
            seen[request] = self.cancelled
            if request == "first":
                release.wait(10)
            return {"id": request, "status": "busy" if request == "second" else "cancelled", "steps": [], "error": "busy"}
    monkeypatch.setattr(C, "Coordinator", FakeCoordinator)

    class Host:
        _active_pcs = []

        async def _missions_ready(self):
            return True

        def _agents(self, loop, abort=None):
            return {}
    h = Host()
    for name in ("_stop_pc_tasks",):
        if hasattr(DesktopPlugin, name):
            setattr(h, name, getattr(DesktopPlugin, name).__get__(h))

    async def main():
        first = asyncio.ensure_future(DesktopPlugin.run_pc_task(h, request="first"))
        while "first" not in seen:
            await asyncio.sleep(0.01)
        await DesktopPlugin.stop_pc_task(h)                           # the owner says stop
        await DesktopPlugin.run_pc_task(h, request="second")          # another request arrives meanwhile
        stopped = seen["first"]()
        release.set()
        await first
        return stopped
    assert asyncio.run(main()) is True


# ------------------------------------------------------------------------------------------------------------------------
# The lock under every interleaving (review item 'TOCTOU in _take_over' and the intermittent two-winner failure of
# test_eight_threads_race_one_winner). The lock's critical sections call `C._seam(name)`; these tests replace it to stop a
# thread at an exact point while others run through, so the bad interleavings happen on every run, not one run in a dozen.
# Before the fix a taker that judged a stale lock earlier moved a FRESH lock aside (os.replace), a third acquirer's O_EXCL
# create succeeded in that gap, and the restore (os.link) failed: two holders. Invariants checked here: at most one holder at
# any time; a fresh lock is never moved, removed or hidden; a takeover is exclusive; a dead remover cannot block for ever.
# ------------------------------------------------------------------------------------------------------------------------
def dead_pid() -> int:
    p = subprocess.Popen([sys.executable, "-c", "pass"])
    p.wait()
    return p.pid


def write_stale(lock: Path, how: str, task: str = "stale", token: str = "ab" * 8) -> dict:
    rec = {"task": task, "pid": dead_pid() if how == "dead" else os.getpid(),
           "at": REAL_TIME() if how == "dead" else REAL_TIME() - 5000, "budget": 600, "token": token}
    lock.write_text(json.dumps(rec))
    return rec


@pytest.fixture
def seams(monkeypatch):
    """`seams({name: fn})` installs hooks; returns the list of seam names reached, in order. The hook of a name runs ONCE per
    thread (a seam inside a nested call made by a hook is not hooked again)."""
    class Reached(list):
        hooks: dict

    reached, hooks, busy = Reached(), {}, threading.local()

    def seam(name):
        reached.append(name)
        fn = hooks.get(name)
        if fn is not None and not getattr(busy, "in_hook", False):
            busy.in_hook = True
            try:
                fn()
            finally:
                busy.in_hook = False
    monkeypatch.setattr(C, "_seam", seam, raising=False)
    reached.hooks = hooks
    return reached


def forbid_moving_the_lock(monkeypatch, lock: Path):
    """The lock file must never be renamed or linked (that is how a fresh lock got hidden from other acquirers)."""
    moved = []
    for name in ("replace", "rename", "link"):
        real = getattr(os, name)

        def spy(src, dst, *a, _real=real, _name=name, **k):
            if Path(src) == lock or Path(dst) == lock:
                moved.append((_name, str(src), str(dst)))
            return _real(src, dst, *a, **k)
        monkeypatch.setattr(os, name, spy)
    return moved


def test_a_taker_that_judged_a_stale_lock_earlier_cannot_remove_or_hide_a_fresh_lock(tmp_path, monkeypatch, seams):
    """X reads stale lock S and judges it stale; before X acts, S's holder is gone and Y takes a FRESH lock. X's takeover must
    leave Y's lock exactly where and what it is, and X must be told the PC is busy (never two holders)."""
    lock = tmp_path / "pc_task.lock"
    stale = write_stale(lock, "dead")
    moved = forbid_moving_the_lock(monkeypatch, lock)
    swapped = {}

    def swap():
        lock.unlink()                                               # S's holder released it ...
        swapped["y"] = C.acquire("Y-fresh", lock, budget=600)       # ... and Y took the lock before X acted
        swapped["inode"] = lock.stat().st_ino
        swapped["content"] = lock.read_text()
        with pytest.raises(C.Busy, match="Y-fresh"):                # a third acquirer Z meets Y's lock
            C.acquire("Z", lock, budget=600)
    seams.hooks["acquire:judged_stale"] = swap
    with pytest.raises(C.Busy, match="Y-fresh"):
        C.acquire("X", lock, budget=600)
    assert "acquire:judged_stale" in seams, "the seam was not reached: the taker never judged the stale lock"
    assert C.current_task(lock) == "Y-fresh" and swapped["y"].still_held()
    assert lock.stat().st_ino == swapped["inode"] and lock.read_text() == swapped["content"], "Y's lock was replaced"
    assert moved == [], "the lock file was renamed or linked: a fresh lock was hidden from other acquirers"
    assert json.loads(swapped["content"])["token"] != stale["token"]
    C.release(swapped["y"])
    assert not lock.exists() and sorted(p.name for p in tmp_path.iterdir()) == []


@pytest.mark.parametrize("how", ["dead", "silent"])
def test_every_acquirer_judges_the_same_stale_lock_before_any_removes_it_and_exactly_one_wins(tmp_path, seams, how):
    """The worst start of the 8-thread race, forced: all 8 have read and judged the stale lock before any of them acts."""
    lock = tmp_path / "pc_task.lock"
    write_stale(lock, how)
    gate, once, won, busy = threading.Barrier(8), threading.local(), [], []

    def meet():
        if not getattr(once, "met", False):
            once.met = True
            gate.wait(10)
    seams.hooks["acquire:judged_stale"] = meet

    def go(i):
        try:
            won.append((i, C.acquire(f"t{i}", lock, budget=600)))
        except C.Busy:
            busy.append(i)
    th = [threading.Thread(target=go, args=(i,)) for i in range(8)]
    [t.start() for t in th]
    [t.join(20) for t in th]
    assert seams.count("acquire:judged_stale") >= 8, "not every thread judged the stale lock first"
    assert len(won) == 1 and len(busy) == 7, (won, busy)
    assert C.current_task(lock) == f"t{won[0][0]}" and won[0][1].still_held()
    C.release(won[0][1])
    assert not lock.exists() and sorted(p.name for p in tmp_path.iterdir()) == []


def test_a_takeover_is_exclusive_and_a_silent_holders_late_release_never_removes_the_new_lock(tmp_path, monkeypatch, seams):
    """X has verified that S is stale and is about to remove it. Meanwhile (a) another acquirer W must be refused (the removal is
    claimed), and (b) S's holder, alive but silent, wakes up and releases S: its release must wait for X's removal and then
    find a foreign lock (X's) and leave it alone."""
    lock = tmp_path / "pc_task.lock"
    stale = write_stale(lock, "silent", task="silent-holder", token="cd" * 8)
    old_holder = C.Hold("silent-holder", lock, stale["token"], 600)          # no heartbeat running: it is silent
    monkeypatch.setattr(C, "ACQUIRE_WAIT_S", 0.1)
    out = {}

    def during_the_takeover():
        with pytest.raises(C.Busy, match="being taken over"):
            C.acquire("W", lock, budget=600)
        out["thread"] = threading.Thread(target=C.release, args=(old_holder,))
        out["thread"].start()
    seams.hooks["takeover:verified"] = during_the_takeover
    x = C.acquire("X", lock, budget=600)
    out["thread"].join(10)
    assert not out["thread"].is_alive()
    assert C.current_task(lock) == "X" and x.still_held(), "the late release of the old holder removed the new lock"
    if "release:claimed" in seams:                                           # it waited for the removal to be over
        assert seams.index("release:claimed") > seams.index("takeover:removed")
    C.release(x)
    assert not lock.exists() and sorted(p.name for p in tmp_path.iterdir()) == []


def test_a_removal_claim_whose_remover_died_does_not_block_the_takeover_for_ever(tmp_path):
    lock = tmp_path / "pc_task.lock"
    stale = write_stale(lock, "dead")
    claim = tmp_path / f"{lock.name}.claim.{stale['token']}.0"
    claim.write_text("")
    live = C._claim(lock, stale["token"])                                    # a live claim on the same lock: refused
    assert live is None
    old = REAL_TIME() - C.CLAIM_TTL_S - 5
    os.utime(claim, (old, old))                                              # its remover died long ago
    h = C.acquire("next", lock, budget=600)
    assert C.current_task(lock) == "next" and h.still_held()
    assert sorted(p.name for p in tmp_path.iterdir() if ".claim." in p.name) == [claim.name]   # only the dead one's leftover
    C.release(h)


@pytest.mark.skipif(os.name != "posix", reason="needs unlinking a lock file its creator still has open, which Windows refuses (so that state cannot arise there)")
def test_a_lock_still_empty_when_it_is_removed_as_abandoned_does_not_make_its_writer_a_second_holder(tmp_path, seams):
    """A creator is suspended after O_EXCL and before writing; its empty lock is judged abandoned (a dead writer's leftover),
    removed, and another task takes the lock. When the creator wakes up it writes into an unlinked file: it must notice, and
    must not report itself as the holder."""
    lock = tmp_path / "pc_task.lock"
    out = {}

    def removed_and_retaken():
        old = REAL_TIME() - C.UNWRITTEN_GRACE_S - 5
        os.utime(lock, (old, old))                                           # looks like a dead writer's leftover
        ino = lock.stat().st_ino
        assert C._take_over(lock, {}, ino) is True and not lock.exists()
        out["other"] = C.acquire("other", lock, budget=600)
    seams.hooks["acquire:created"] = removed_and_retaken
    with pytest.raises(C.Busy, match="other"):
        C.acquire("slow-creator", lock, budget=600)
    assert C.current_task(lock) == "other" and out["other"].still_held()
    C.release(out["other"])
    assert not lock.exists()


@pytest.mark.parametrize("start", ["dead", "silent", "free"])
def test_never_two_holders_while_holders_come_and_go(tmp_path, start):
    """8 threads each take and release the lock 15 times, starting from a stale lock: the number of simultaneous holders is
    never above one (counted between 'acquire returned' and 'release called')."""
    lock = tmp_path / "pc_task.lock"
    if start != "free":
        write_stale(lock, start)
    gate, mu, state = threading.Barrier(8), threading.Lock(), {"now": 0, "max": 0, "got": 0}

    def go(i):
        gate.wait(10)
        for _ in range(15):
            try:
                h = C.acquire(f"t{i}", lock, budget=600)
            except C.Busy:
                continue
            with mu:
                state["now"] += 1
                state["max"] = max(state["max"], state["now"])
                state["got"] += 1
            with mu:
                state["now"] -= 1
            C.release(h)
    th = [threading.Thread(target=go, args=(i,)) for i in range(8)]
    [t.start() for t in th]
    [t.join(60) for t in th]
    assert state["max"] == 1 and state["got"] >= 1, state
    assert not lock.exists() and sorted(p.name for p in tmp_path.iterdir()) == []
