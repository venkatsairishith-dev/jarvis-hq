"""Pending approvals after a cancel, and the registry as the authoritative "the PC is being acted on" signal (re-review M14, L1-L3).

* An approval an operation waits on, or left pending when it ended waiting for the owner, is DENIED when the operation is
  cancelled or Stop is pressed (kernel owner/deny); an approval nothing here created is never touched; an approval already
  decided or expired is tolerated; a denied approval cannot be used afterwards.
* The registry publishes a snapshot (coordinator.publish) that stays fresh while any operation lives, however long its current
  step takes, so the widget's pc.busy (and with it the spoken "stop") does not depend on a file that only a step's start wrote.
The real core.Pc and the real entries talk to the fake console of test_stop_ownership; no sleeps as synchronisation.
"""
import asyncio
import importlib.util
import json
import sys
import threading
from pathlib import Path

import pytest

from jarvis.plugins.desktop import DesktopPlugin
from jarvis.plugins.desktop import coordinator as C
from jarvis.plugins.desktop import core
from jarvis.plugins.desktop.tests.test_stop_ownership import WAIT, Console, Host, until  # noqa: F401  (console fixture below)

ROOT = Path(__file__).resolve().parents[4]


@pytest.fixture
def console(monkeypatch):
    c = Console()

    class Pc(core.Pc):
        def __init__(self, **kw):
            kw.setdefault("sleep", lambda s: threading.Event().wait(0.005))
            super().__init__(call=c, **kw)
    monkeypatch.setattr(core, "Pc", Pc)
    monkeypatch.setattr(core, "set_activity", lambda *a, **k: None)
    monkeypatch.setattr(C, "STOP_WAIT_S", 5.0)
    c.Pc = Pc
    return c


def leave_waiting(console, op_name="single-x"):
    """An operation whose job needs an approval, whose wait ran out: it ends 'waiting_for_permission', the card stays pending."""
    console.needs_approval = lambda body: True
    op = C.begin("single:run_pc_action", op_name)
    pc = op.attach_pc(console.Pc())
    pc.approval_wait_s = 0                                            # the owner did not decide in time
    res = pc.run({"op": "ui", "action": "click"}, 60, "click")
    op.entry_exit()
    assert res["state"] == "waiting_for_permission" and console.pending == {"ap1"}
    assert op.done.is_set(), "the operation must be over: only its approval is left behind"
    return pc


def stop(h=None):
    return asyncio.run(DesktopPlugin.stop_pc_task(h or Host())).value


# ------------------------------------------------------------------ Stop on an operation that already ended, card left pending
def test_stop_denies_the_approval_an_ended_operation_left_waiting_and_it_cannot_be_used_afterwards(console):
    leave_waiting(console)
    assert C.pending_approvals() == ["ap1"]
    out = stop()
    assert out["requested"] == 0 and out["stopped"] == [] and out["approvals_denied"] == ["ap1"], out
    assert "approval(s) it left waiting were denied" in out["message"]
    assert console.denied == ["ap1"] and console.pending == set() and C.pending_approvals() == []
    # unusable: the kernel refuses to run the action with that approval (the fake console answers like the kernel does)
    code, reply = console("POST", "pc", {"op": "ui", "action": "click", "approval_id": "ap1"})
    assert code == 403 and "denied" in reply["error"]


def test_stop_denies_only_approvals_that_belong_to_desktop_operations(console):
    leave_waiting(console)
    console.pending.add("someone-elses")                              # an approval another agent / the owner's own flow created
    out = stop()
    assert out["approvals_denied"] == ["ap1"]
    assert console.denied == ["ap1"] and "someone-elses" in console.pending, "an approval no PC operation owns was denied"


def test_an_approval_already_approved_or_expired_is_tolerated_not_an_error(console):
    leave_waiting(console)
    console.pending.discard("ap1")                                    # the owner approved it (or it expired) meanwhile
    out = stop()
    assert out["approvals_denied"] == [] and out["approvals_unreachable"] == []
    assert C.pending_approvals() == [], "a decided approval must be forgotten, not retried forever"
    assert console.denied == [], "nothing was denied: the card was already decided"


def test_a_console_that_does_not_answer_keeps_the_approval_tracked_and_says_so(console):
    leave_waiting(console)
    real = console.__call__

    pc = C._APPROVALS["ap1"]["pc"]
    pc.call = lambda *a, **k: (_ for _ in ()).throw(core.DesktopError("the JARVIS console is not answering"))
    out = stop()
    assert out["approvals_denied"] == [] and out["approvals_unreachable"] == ["ap1"]
    assert "could not be denied" in out["message"] and C.pending_approvals() == ["ap1"]
    pc.call = real                                                    # the console is back: the next Stop denies it
    assert stop()["approvals_denied"] == ["ap1"]


def test_cancelling_an_operation_with_no_approvals_denies_nothing(console):
    console.hold = True

    async def main():
        h = Host()
        entry = asyncio.ensure_future(DesktopPlugin.run_pc_action(h, action="focus", target="notepad|"))
        await until(console.polled)
        stop_ = asyncio.ensure_future(DesktopPlugin.stop_pc_task(h))
        await until(console.cancel_requested)
        console.confirm.set()
        return (await asyncio.wait_for(stop_, WAIT)).value, (await asyncio.wait_for(entry, WAIT)).value
    out, res = asyncio.run(main())
    assert out["stopped_jobs"] == 1 and out["approvals_denied"] == []
    assert not [c for c in console.calls if c[1] == "deny"], "a cancel with no approvals must not call deny"


# ------------------------------------------------------------------ a stop while the approval is awaited: the operation's own card
def test_cancel_denies_the_awaited_approval_of_that_operation_only(console):
    console.needs_approval = lambda body: True
    console.pending.add("someone-elses")

    async def main():
        h = Host()
        entry = asyncio.ensure_future(DesktopPlugin.run_pc_action(h, action="click", target="notepad|ok"))
        await until(console.waiting)
        out = (await DesktopPlugin.stop_pc_task(h)).value
        return out, (await asyncio.wait_for(entry, WAIT)).value
    out, res = asyncio.run(main())
    assert console.denied == ["ap1"] and "someone-elses" in console.pending and console.executed == []
    assert res["state"] == "cancelled" and C.pending_approvals() == []


def test_approving_first_then_cancelling_never_runs_the_action_after_the_cancel_flag_is_set(console):
    """The race: the owner approved, the Pc has not sent the approved action yet, Stop lands. The operation's stop flag is
    checked before the approved action is sent, so it is not; the approved card (already decided) is tolerated."""
    console.needs_approval = lambda body: True
    op = C.begin("single:run_pc_action", "single-race")
    pc = op.attach_pc(console.Pc())
    real = pc.approval_pending

    def approved_and_then_stopped(aid):
        console.pending.discard(aid)                                  # the owner approved it ...
        op.request_stop("Stop")                                       # ... and the cancel lands before the approved action is sent
        return real(aid)
    pc.approval_pending = approved_and_then_stopped
    res = pc.run({"op": "ui", "action": "click"}, 60, "click")
    op.entry_exit()
    assert res["state"] == "cancelled" and console.executed == []
    assert not [b for m, n, b in console.calls if n == "pc" and b.get("approval_id")], "the approved action was sent after the stop"
    assert C.pending_approvals() == [] and op.done.is_set()


def test_a_resumed_step_carrying_an_approval_is_not_sent_when_its_operation_was_stopped(console):
    """A resumed mission step hands its approval to the job (body + approval_id): a stopped operation sends nothing."""
    op = C.begin("mission", "m-1")
    pc = op.attach_pc(console.Pc())
    op.request_stop("Stop")
    res = pc.run({"op": "ui", "action": "click", "approval_id": "ap-held"}, 60, "click")
    op.entry_exit()
    assert res["state"] == "cancelled" and console.executed == [] and not [c for c in console.calls if c[1] == "pc"]


# ------------------------------------------------------------------ a mission waiting for an approval
def test_cancelling_a_waiting_mission_denies_its_approval_and_it_cannot_be_used(console, tmp_path, monkeypatch):
    from dbos import DBOS
    from jarvis.plugins.desktop import missions as M
    M.configure(tmp_path)
    M.E.lock_path = tmp_path / "pc_task.lock"
    M.E.on_cancel = lambda: None
    DBOS.launch()
    try:
        console.pending.add("ap-1")
        M.E.agents = {"type_text": lambda s: {"ok": False, "state": "waiting_for_permission", "approval_id": "ap-1"}}
        M.E.plan_fn = lambda g, c: [{"tool": "type_text", "app": "notepad", "text": "x"}]
        mid = M.start("type x")
        import time
        deadline = time.time() + 30
        while M.load(mid).get("status") != "waiting_for_permission" and time.time() < deadline:
            time.sleep(0.05)
        assert M.load(mid)["status"] == "waiting_for_permission" and console.pending == {"ap-1"}
        console.pending.add("someone-elses")
        assert M.cancel(mid)
        assert console.denied == ["ap-1"], "the cancelled mission's approval stayed approvable"
        assert "someone-elses" in console.pending, "an approval that is not the mission's was denied"
        assert M.load(mid)["status"] == "cancelled" and M.resume(mid)["ok"] is False
        code, reply = console("POST", "pc", {"op": "ui", "action": "type", "approval_id": "ap-1"})
        assert code == 403 and "denied" in reply["error"]
        before = len([c for c in console.calls if c[1] == "deny"])
        assert M.cancel(mid)                                          # cancelling again denies nothing new
        assert len([c for c in console.calls if c[1] == "deny"]) == before
    finally:
        DBOS.destroy(destroy_registry=False)


# ------------------------------------------------------------------ explicit outcomes of recover_pc_lock
def test_recover_pc_lock_says_refused_explicitly_when_it_recovered_nothing():
    out = asyncio.run(DesktopPlugin.recover_pc_lock(Host())).value
    assert out["recovered"] is False and out["ok"] is False and out["status"] == "refused" and out["reason"]


def test_recover_pc_lock_says_recovered_when_it_did(console):
    hold = C.acquire("single-gone", C.LOCK, 60)
    for op in C.operations():
        op.hold = None
    path = C.LOCK
    data = json.loads(path.read_text())
    data["pid"] = 999999                                              # its process is gone: the normal takeover
    path.write_text(json.dumps(data))
    out = asyncio.run(DesktopPlugin.recover_pc_lock(Host())).value
    C.release(hold)
    assert out["recovered"] is True and out["ok"] is True and out["status"] == "recovered"


# ------------------------------------------------------------------ the registry snapshot (busy while any operation lives)
def test_the_snapshot_says_busy_exactly_while_an_operation_is_registered(tmp_path):
    path = tmp_path / "ops.json"
    assert C.publish(path) and json.loads(path.read_text())["busy"] is False
    op = C.begin("single:run_pc_action", "single-live")
    assert C.publish(path)
    snap = json.loads(path.read_text())
    assert snap["busy"] is True and [o["id"] for o in snap["operations"]] == ["single-live"]
    assert snap["operations"][0]["kind"] == "single:run_pc_action" and snap["pid"] > 0
    op.entry_exit()
    C.publish(path)
    assert json.loads(path.read_text())["busy"] is False


def test_the_snapshot_keeps_being_refreshed_while_an_operation_lives_with_no_progress_at_all(monkeypatch):
    """A research step is silent for minutes: the file must not go stale. The publisher refreshes `at` on its own cadence."""
    monkeypatch.setattr(C, "PUBLISH_S", 0.01)
    seen, enough = [], threading.Event()
    real = C.publish

    def spy(path=None):
        ok = real(path)
        if C.operations():
            seen.append(json.loads(C.OPS_FILE.read_text())["at"])
            if len(set(seen)) >= 4:
                enough.set()
        return ok
    monkeypatch.setattr(C, "publish", spy)
    now = [0.0]
    op = C.Operation("task", "task-silent", clock=lambda: now[0])     # its clock says it has shown no progress for ten minutes
    now[0] = 600.0
    assert enough.wait(WAIT), "the snapshot was not refreshed while the operation lived"
    snap = json.loads(C.OPS_FILE.read_text())
    assert snap["busy"] is True and snap["operations"][0]["idle_s"] >= 600
    op.entry_exit()
    assert op.done.wait(WAIT)
    for t in [t for t in threading.enumerate() if t.name == "pc-ops-publisher"]:
        t.join(WAIT)
    assert json.loads(C.OPS_FILE.read_text())["busy"] is False, "the snapshot must say idle once the operation is gone"


def _widget():
    path = ROOT / "jarvis" / "widget" / "widget_server.py"
    sys.path.insert(0, str(path.parent))
    spec = importlib.util.spec_from_file_location("widget_server_for_pc_test", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_the_widgets_pc_busy_follows_the_registry_snapshot_even_after_two_minutes_of_silence(tmp_path):
    ws = _widget()
    ops_file = tmp_path / "ops.json"
    assert ws.pc_busy(str(tmp_path / "none.json"), str(ops_file))["busy"] is False
    op = C.begin("task", "task-long")
    C.publish(ops_file)
    got = ws.pc_busy(str(tmp_path / "activity-from-ten-minutes-ago.json"), str(ops_file))
    assert got["busy"] is True and got["source"] == "registry" and got["operations"][0]["id"] == "task-long"
    snap = json.loads(ops_file.read_text())                           # the snapshot is fresh, whatever the activity file says
    snap["operations"][0]["idle_s"] = 900
    ops_file.write_text(json.dumps(snap))
    assert ws.pc_busy(None, str(ops_file))["busy"] is True
    snap["at"] -= 3600                                                # the plugin process died: nobody refreshes it any more
    ops_file.write_text(json.dumps(snap))
    assert ws.pc_busy(None, str(ops_file))["busy"] is False
    op.entry_exit()
