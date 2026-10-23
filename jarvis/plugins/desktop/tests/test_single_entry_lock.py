"""Single-action PC entries take the PC lock (remediation 2026-10-18, section 6: "Single-action PC entries do not take the
PC lock"). run_pc_action, run_browser_action, open_website, play_youtube and run_troubleshooter act on the PC, so they hold
the same lock as a task or mission for their duration and answer "another PC task is running" when it is busy. Read-only
entries (check_pc, list_browser_profiles, recent_pc_alerts, stop_pc_job, status) never take it. No servers, no network:
core.Pc is replaced by a recorder."""
import asyncio
import threading

import pytest

from jarvis.plugins.desktop import DesktopPlugin
from jarvis.plugins.desktop import coordinator as C
from jarvis.plugins.desktop import core, decide

BUSY = "another PC task is running"


def take(task):                       # acquire()'s default path is bound at import: always pass the test's own lock file
    return C.acquire(task, C.LOCK)


def cur():
    return C.current_task(C.LOCK)


class FakePc:
    """Stands in for core.Pc: records every job and what lock was held while it ran."""
    jobs: list = []
    lock_seen: list = []
    fail_with: Exception | None = None

    def __init__(self, *a, **k):
        self.stopped, self.current_job = False, None

    def run(self, body, timeout=90.0, what=""):
        FakePc.jobs.append(body)
        FakePc.lock_seen.append(cur())
        if FakePc.fail_with is not None:
            raise FakePc.fail_with
        op = body.get("op")
        if op == "windows":
            return {"state": "done", "result": {"ok": True, "windows": [{"process": "JARVISSampleApp", "title": "JARVIS Sample App"}]}}
        if op == "media":
            return {"state": "done", "result": {"ok": True, "sessions": []}}
        return {"state": "done", "result": {"ok": True, "opened": True}}

    def cancel(self, jid):
        return {"id": jid, "state": "cancelled"}


@pytest.fixture(autouse=True)
def fake_pc(monkeypatch):
    FakePc.jobs, FakePc.lock_seen, FakePc.fail_with = [], [], None
    monkeypatch.setattr(core, "Pc", FakePc)
    monkeypatch.setattr(core, "set_activity", lambda *a, **k: None)
    return FakePc


class Host:
    """Just enough of the plugin object for the entries and the real step runners."""

    def __init__(self):
        for name in ("_pc", "_run", "_one", "_play_youtube", "_stop_pc_tasks", "_agents", "_missions_ready"):
            if hasattr(DesktopPlugin, name):
                setattr(self, name, getattr(DesktopPlugin, name).__get__(self))
        self.run_troubleshooter = lambda **kw: DesktopPlugin.run_troubleshooter(self, **kw)     # the locking entry, as a task could call it

    _active_pcs: list = []


ENTRIES = [
    ("open_website", {"url": "https://example.com", "profile": "default"}),
    ("play_youtube", {"query": "a song", "profile": "default"}),
    ("run_browser_action", {"action": "read", "profile": "default"}),
    ("run_pc_action", {"action": "focus", "target": "notepad|"}),
    ("run_troubleshooter", {"app": "JARVISSampleApp"}),
]


def call(name, **kw):
    return asyncio.run(getattr(DesktopPlugin, name)(Host(), **kw))


def is_err(res):
    return type(res).__name__ == "Err"


@pytest.mark.parametrize("name,kw", ENTRIES)
def test_a_busy_pc_is_refused_honestly_and_nothing_is_sent(name, kw, fake_pc):
    hold = take("mission-1")                                     # a task or mission is running
    try:
        res = call(name, **kw)
    finally:
        C.release(hold)
    assert is_err(res) and BUSY in str(res.error)
    assert "mission-1" in str(res.error)                              # says which task holds the PC
    assert fake_pc.jobs == [], "the entry sent a job to the PC while another task held it"
    assert cur() is None                                   # the refused entry took and left nothing


@pytest.mark.parametrize("name,kw", ENTRIES)
def test_a_free_pc_runs_the_entry_while_it_holds_the_lock_and_frees_it_after(name, kw, fake_pc):
    res = call(name, **kw)
    assert fake_pc.jobs, f"{name} sent nothing"
    assert not is_err(res), res
    assert all(t and t.startswith(f"single-{name}-") for t in fake_pc.lock_seen), fake_pc.lock_seen    # held during every job
    assert cur() is None                                   # released afterwards
    C.release(take("next"))                                      # and a task can take it at once


@pytest.mark.parametrize("name,kw", ENTRIES)
def test_the_lock_is_released_when_the_job_fails(name, kw, fake_pc):
    fake_pc.fail_with = core.DesktopError("the JARVIS console is not answering")
    res = call(name, **kw)
    assert is_err(res)
    assert cur() is None
    C.release(take("next"))


@pytest.mark.parametrize("name,kw", ENTRIES)
def test_the_lock_is_released_when_something_unexpected_is_raised(name, kw, fake_pc):
    fake_pc.fail_with = RuntimeError("boom")
    try:
        call(name, **kw)
    except RuntimeError:
        pass                                                           # a few entries let it surface; the lock must not stay
    assert cur() is None
    C.release(take("next"))


def test_the_lock_is_released_when_the_entry_is_cancelled_and_only_after_its_job_has_stopped():
    """Cancelling the entry stops its job (the Pc is stopped, as the real one cancels its node job), and the lock is released
    when that worker has really ended, not merely when the entry returned (review M14: it used to be freed at once while the
    worker thread still acted)."""
    started, stop_seen = threading.Event(), threading.Event()
    seen = {}

    class Slow(FakePc):
        def __init__(self, *a, **k):
            self._stopped = False
            self.current_job = None

        @property
        def stopped(self):
            return self._stopped

        @stopped.setter
        def stopped(self, value):
            self._stopped = value
            if value:
                stop_seen.set()

        def run(self, body, timeout=90.0, what=""):
            started.set()
            seen["woken_by_stop"] = stop_seen.wait(10)
            seen["lock_when_the_job_ended"] = cur()                # the job is ending: the lock must still be held
            return {"state": "cancelled", "result": {"ok": False, "error": "stopped"}}
    core.Pc = Slow

    async def main():
        t = asyncio.ensure_future(DesktopPlugin.run_pc_action(Host(), action="focus", target="notepad|"))
        while not started.is_set():
            await asyncio.sleep(0.01)
        assert (cur() or "").startswith("single-run_pc_action-")
        t.cancel()
        try:
            await t
        except asyncio.CancelledError:
            pass
        seen["lock_after_cancel"] = cur()
    try:
        asyncio.run(main())
    finally:
        core.Pc = FakePc
    assert seen["woken_by_stop"] is True, "cancelling the entry did not stop its job"
    assert (seen["lock_when_the_job_ended"] or "").startswith("single-run_pc_action-"), "the lock was freed before the job ended"
    assert seen["lock_after_cancel"] is None and cur() is None            # and it is free once the worker has ended


def test_read_only_entries_stay_lock_free_while_a_task_holds_the_pc(fake_pc, tmp_path, monkeypatch):
    monkeypatch.setattr(core, "PROFILES", tmp_path / "none.json")
    monkeypatch.setattr(C, "DATA", tmp_path)
    hold = take("mission-1")
    try:
        for name, kw in (("check_pc", {"check": "disk"}), ("list_browser_profiles", {}), ("recent_pc_alerts", {}),
                         ("stop_pc_job", {"job": "j1"}), ("stop_pc_task", {})):
            res = call(name, **kw)
            assert not (is_err(res) and BUSY in str(res.error)), f"{name} must not need the PC lock"
        assert cur() == "mission-1"                        # none of them touched the holder's lock
    finally:
        C.release(hold)


def test_a_single_entry_during_a_running_task_is_refused_and_works_after_it(fake_pc):
    """The coordinator holds the lock for its whole run; a single entry in the middle is refused, not interleaved."""
    inside, go = threading.Event(), threading.Event()
    seen = {}

    def slow_step(step):
        inside.set()
        go.wait(10)
        return {"ok": True}
    plan = decide.Plan([{"tool": "check_pc", "check": "disk"}], "ok")
    co = C.Coordinator({"check_pc": slow_step}, [], decide_fn=lambda r: plan, lock_path=C.LOCK)
    t = threading.Thread(target=lambda: seen.setdefault("rec", co.run("check the disk")), daemon=True)
    t.start()
    assert inside.wait(10)
    mid = call("open_website", url="https://example.com", profile="default")
    go.set()
    t.join(10)
    assert is_err(mid) and BUSY in str(mid.error) and fake_pc.jobs == []
    assert seen["rec"]["status"] == "completed"
    assert not is_err(call("open_website", url="https://example.com", profile="default"))


def test_a_task_that_runs_the_troubleshooter_does_not_deadlock_on_its_own_lock(fake_pc):
    """run_pc_task's troubleshoot step already holds the PC lock; it must call the flow, not the locking entry."""
    loop = asyncio.new_event_loop()
    threading.Thread(target=loop.run_forever, daemon=True).start()
    h = Host()
    h._cmd_loop = loop
    plan = decide.Plan([{"tool": "troubleshoot", "app": "JARVISSampleApp"}], "ok")
    co = C.Coordinator(h._agents(loop), [], decide_fn=lambda r: plan, lock_path=C.LOCK)
    out = {}
    t = threading.Thread(target=lambda: out.setdefault("rec", co.run("fix the sample app")), daemon=True)
    t.start()
    t.join(30)
    loop.call_soon_threadsafe(loop.stop)
    assert not t.is_alive(), "the troubleshoot step waited on the lock its own task holds"
    rec = out["rec"]
    assert rec["steps"] and "outcome" in rec["steps"][0]["result"], rec     # the flow really ran (it diagnosed)
    assert cur() is None
