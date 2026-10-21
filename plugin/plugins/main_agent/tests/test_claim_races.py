"""resume_run and run_status against the one authoritative claim (audit MEM-M7 follow-ups, 2026-10-18).

The claim is taken without an await between the check and the taking (run_task before planning, resume_run before it starts),
so two entries cannot both win and a reader never sees a claimed run without its record. These tests pin that down, and the
one gap found: a claim taken and then never released when something raised before the run really started (every later task
was then "busy" until the plugin restarted). Same fakes as test_run_control; no model, no network."""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from test_run_control import THINK, TWO, Err, make, settle  # noqa: E402

from plugin.plugins.main_agent.planner import Plan, Step  # noqa: E402
from plugin.plugins.main_agent.store import plan_to_dict  # noqa: E402


def save_resumable(p, rid="held1", n=1):
    steps = [Step(f"s{i + 1}", "think", f"goal {i + 1}") for i in range(n)]
    plan = Plan("Think", steps)
    p._store.save({"run_id": rid, "task": "t", "mode": "work", "summary": "s", "state": "interrupted",
                   "plan": plan_to_dict(plan), "running": [], "results": {}})
    return rid


def is_err(r):
    return isinstance(r, Err) or type(r).__name__ == "Err"


def test_run_status_during_planning_shows_the_claimed_run_not_nothing(monkeypatch, tmp_path):
    async def go():
        gate = asyncio.Event()
        p = make(monkeypatch, tmp_path, plan_gate=gate)
        first = asyncio.ensure_future(p.run_task(task="do it"))
        await asyncio.sleep(0.05)                                     # claimed, still planning
        st = (await p.run_status()).value
        assert st["state"] == "planning" and st["run_id"] == p._active and st["resumable"] is False
        by_id = (await p.run_status(run_id=st["run_id"])).value
        assert by_id["state"] == "planning"
        gate.set()
        await first
        await settle(p)
    asyncio.run(go())


def test_resume_while_a_run_is_planning_is_busy_and_leaves_that_run_alone(monkeypatch, tmp_path):
    async def go():
        gate = asyncio.Event()
        p = make(monkeypatch, tmp_path, plan_gate=gate)
        rid_held = save_resumable(p)
        first = asyncio.ensure_future(p.run_task(task="do it"))
        await asyncio.sleep(0.05)
        active, cancel = p._active, p._cancel
        res = await p.resume_run(run_id=rid_held)
        assert is_err(res) and "still working" in str(res.error)
        assert p._active == active and p._cancel is cancel and not cancel.is_set()       # the claim is untouched
        assert (await p.run_status(run_id=active)).value["state"] == "planning"
        gate.set()
        await first
        await settle(p)
    asyncio.run(go())


def test_two_simultaneous_resumes_of_one_run_start_it_once(monkeypatch, tmp_path):
    async def go():
        p = make(monkeypatch, tmp_path)
        rid = save_resumable(p)
        a, b = await asyncio.gather(p.resume_run(run_id=rid), p.resume_run(run_id=rid))
        assert sorted([is_err(a), is_err(b)]) == [False, True]
        await settle(p)
        assert p.dispatched == ["Goal: goal 1"]                             # the step ran exactly once
    asyncio.run(go())


def test_a_resume_and_a_new_task_at_the_same_time_only_one_wins(monkeypatch, tmp_path):
    async def go():
        p = make(monkeypatch, tmp_path)
        rid = save_resumable(p)
        a, b = await asyncio.gather(p.resume_run(run_id=rid), p.run_task(task="something else"))
        assert sorted([is_err(a), is_err(b)]) == [False, True]
        await settle(p)
        assert len(p.dispatched) == 1
    asyncio.run(go())


def test_a_new_task_and_a_resume_the_other_way_round(monkeypatch, tmp_path):
    async def go():
        p = make(monkeypatch, tmp_path)
        rid = save_resumable(p)
        b, a = await asyncio.gather(p.run_task(task="something else"), p.resume_run(run_id=rid))
        assert sorted([is_err(a), is_err(b)]) == [False, True]
        await settle(p)
    asyncio.run(go())


def test_run_status_right_after_a_resume_shows_the_resumed_run_running(monkeypatch, tmp_path):
    async def go():
        gate = asyncio.Event()

        async def think(goal):
            await gate.wait()
            return "x"
        p = make(monkeypatch, tmp_path, think=think)
        rid = save_resumable(p, n=2)
        res = await p.resume_run(run_id=rid)
        assert not is_err(res)
        st = (await p.run_status()).value                             # no id: the active run
        assert st["run_id"] == rid and st["state"] == "running"
        gate.set()
        await settle(p)
        assert (await p.run_status(run_id=rid)).value["state"] == "done"
    asyncio.run(go())


def test_stop_right_after_a_resume_stops_the_resumed_run(monkeypatch, tmp_path):
    async def go():
        async def think(goal):
            await asyncio.sleep(30)
            return "x"
        p = make(monkeypatch, tmp_path, think=think)
        rid = save_resumable(p)
        await p.resume_run(run_id=rid)
        await asyncio.sleep(0.05)
        out = (await p.stop_task()).value
        assert out["stopped"] is True and out["run_id"] == rid
        await asyncio.wait_for(settle(p), 3)
        assert p._store.load(rid)["state"] == "cancelled"
    asyncio.run(go())


def test_a_run_that_could_not_start_does_not_leave_the_claim_held(monkeypatch, tmp_path):
    """Before: _start raising (after planning) left _active set, so every later task was refused as busy until a restart."""
    async def go():
        p = make(monkeypatch, tmp_path)
        real_start = p._start
        p._start = lambda *a, **k: (_ for _ in ()).throw(OSError("disk"))
        res = await p.run_task(task="first")
        assert is_err(res) and "could not start" in str(res.error)
        assert p._active is None and p._cancel is None
        p._start = real_start
        ok = await p.run_task(task="second")                          # not "busy"
        assert not is_err(ok)
        await settle(p)
    asyncio.run(go())


def test_a_resume_that_could_not_start_does_not_leave_the_claim_held(monkeypatch, tmp_path):
    async def go():
        p = make(monkeypatch, tmp_path)
        rid = save_resumable(p)
        real_start = p._start
        p._start = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom"))
        res = await p.resume_run(run_id=rid)
        assert is_err(res) and "could not be resumed" in str(res.error)
        assert p._active is None
        p._start = real_start
        assert not is_err(await p.resume_run(run_id=rid))
        await settle(p)
    asyncio.run(go())


def test_the_planning_marker_and_the_claim_agree_at_every_step(monkeypatch, tmp_path):
    """run_status, the widget and "stop" all read the same claim: never a claimed run without a record or the reverse."""
    async def go():
        gate = asyncio.Event()
        p = make(monkeypatch, tmp_path, plan=THINK, plan_gate=gate)
        t = asyncio.ensure_future(p.run_task(task="do it"))
        await asyncio.sleep(0.05)
        assert p._active in p._runs and p._store.read_mark()["run_id"] == p._active
        gate.set()
        await t
        await settle(p)
        assert p._active is None and p._cancel is None
    asyncio.run(go())
