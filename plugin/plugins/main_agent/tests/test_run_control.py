"""One active run, Stop during planning and in flight, cancellation passed on to helpers (audit VOI-D1/D2/D4, MEM-M7).

The plugin class wired to fakes: no model, no network. The plugin runtime runs entries of one plugin concurrently on one
asyncio loop (plugin/core/host.py), which is what asyncio.gather reproduces here.
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
import logging
import sys
import types
from pathlib import Path

from plugin.plugins import main_agent as ma
from plugin.plugins.main_agent.executor import run_plan
from plugin.plugins.main_agent.planner import Capability, Plan, Step
from plugin.plugins.main_agent.store import ACTIVE_MARKER, RunStore, plan_to_dict

ROOT = Path(__file__).resolve().parents[4]
THINK = {"summary": "Think", "steps": [{"id": "s1", "agent": "think", "goal": "write"}]}
TWO = {"summary": "Two steps", "steps": [{"id": "s1", "agent": "think", "goal": "first"},
                                         {"id": "s2", "agent": "think", "goal": "second", "depends_on": ["s1"]}]}
PC = {"summary": "On the PC", "steps": [{"id": "s1", "agent": "plugin", "plugin": "desktop:run_pc_task", "goal": "open notepad",
                                         "args": {"request": "open notepad"}},
                                        {"id": "s2", "agent": "think", "goal": "report", "depends_on": ["s1"]}]}
JARVIS = {"summary": "Hand to JARVIS", "steps": [{"id": "s1", "agent": "jarvis", "goal": "fix the build"},
                                                 {"id": "s2", "agent": "think", "goal": "report", "depends_on": ["s1"]}]}
CAPS = [Capability("think", "think", "t"), Capability("jarvis", "jarvis", "j"),
        Capability("plugin", "desktop:run_pc_task", "pc", {"type": "object", "properties": {"request": {"type": "string"}},
                                                           "required": ["request"]})]


class Ok:
    def __init__(self, value):
        self.value = value


class Err:
    def __init__(self, error):
        self.error = error


def make(monkeypatch, tmp_path, plan=THINK, plan_delay=0.0, plan_gate=None, think=None, call_entry=None, json_entries=None):
    p = object.__new__(ma.MainAgentPlugin)
    p._runs, p._active, p._cancel = {}, None, None
    p._store = RunStore(tmp_path / "runs")
    p.logger = logging.getLogger("test")
    p.pushed = []
    p.push_message = lambda **kw: p.pushed.append(kw)
    p._agent_base_url = lambda: ""                         # no planner endpoint: memory is never looked up
    p.llm_calls = []
    p.dispatched = []

    async def catalogue(mode="work"):
        return CAPS

    async def llm(messages, **kw):
        if "planner" in messages[0]["content"]:
            p.llm_calls.append(messages)
            if plan_gate is not None:
                await plan_gate.wait()
            await asyncio.sleep(plan_delay)
            return json.dumps(plan)
        goal = messages[1]["content"]
        p.dispatched.append(goal.split("\n", 1)[0])
        if think is not None:
            return await think(goal)
        return "TEXT"

    p._catalogue, p._llm = catalogue, llm
    calls = []

    async def default_call_entry(ref, params=None, timeout=10.0):
        calls.append((ref, params))
        return Ok({"ok": True})

    async def call_entry_json(ref, params=None, timeout=10.0):
        calls.append((ref, params))
        fn = (json_entries or {}).get(ref)
        if fn is None:
            return Ok({"data": {}})
        out = fn(params)
        return await out if asyncio.iscoroutine(out) else out

    entry = call_entry or default_call_entry

    async def wrapped_entry(ref, params=None, timeout=10.0):
        if call_entry is not None:
            calls.append((ref, params))
        return await entry(ref, params, timeout)

    monkeypatch.setattr(ma.MainAgentPlugin, "plugins", types.SimpleNamespace(call_entry=wrapped_entry, call_entry_json=call_entry_json),
                        raising=False)
    monkeypatch.setattr(ma, "Err", Err)
    p.calls = calls
    return p


async def settle(p, limit=400):
    for _ in range(limit):
        if p._active is None:
            return
        await asyncio.sleep(0.01)
    raise AssertionError("the run never finished")


def marker(p):
    path = p._store.dir / ACTIVE_MARKER
    return json.loads(path.read_text()) if path.exists() else None


# ---------- one active run ----------

def test_two_requests_at_the_same_time_only_one_starts_and_the_other_gets_busy(monkeypatch, tmp_path):
    """Two "Hey Neko" commands (or a voice and a typed task) arriving together: the old code planned and started both."""
    async def go():
        gate = asyncio.Event()

        async def think(goal):
            await gate.wait()
            return "done"
        p = make(monkeypatch, tmp_path, plan_delay=0.05, think=think)
        a, b = await asyncio.gather(p.run_task(task="voice task"), p.run_task(task="typed task"))
        assert sorted(type(r).__name__ for r in (a, b)) == ["Err", "Ok"]       # old code: ["Ok", "Ok"], both started
        busy = next(r for r in (a, b) if type(r).__name__ == "Err")
        assert "still working on the previous task" in str(busy.error)
        assert len(p.llm_calls) == 1                                    # the refused one never reached the planner
        running = [r for r in p._runs.values() if r["state"] in ("running", "planning")]
        assert len(running) == 1 and p._active == running[0]["run_id"]
        stop = (await p.stop_task()).value                              # Stop reaches the one that runs
        assert stop["stopped"] is True and stop["run_id"] == running[0]["run_id"]
        gate.set()
        await settle(p)
        assert running[0]["state"] == "cancelled"
    asyncio.run(go())


def test_a_second_request_while_the_first_is_still_planning_is_busy(monkeypatch, tmp_path):
    async def go():
        gate = asyncio.Event()
        p = make(monkeypatch, tmp_path, plan_gate=gate)
        first = asyncio.ensure_future(p.run_task(task="first"))
        await asyncio.sleep(0.02)
        assert p._active is not None and p._runs[p._active]["state"] == "planning"
        second = await p.run_task(task="second")
        assert type(second).__name__ == "Err" and len(p.llm_calls) == 1
        gate.set()
        assert type(await first).__name__ == "Ok"
        await settle(p)
    asyncio.run(go())


def test_a_finishing_run_never_releases_a_claim_it_does_not_hold(monkeypatch, tmp_path):
    monkeypatch.setattr(ma, "STOP_REPORT_WAIT_S", 0.05)
    """The old _execute's finally cleared _active and _cancel unconditionally, so Stop then said "Nothing is running"."""
    async def go():
        p = make(monkeypatch, tmp_path)
        plan = Plan("Think", [Step("s1", "think", "old")])
        old = {"run_id": "old", "task": "t", "mode": "work", "plan": plan_to_dict(plan), "results": {}, "running": [], "state": "running"}
        rid = p._claim()                                               # another run holds the claim
        await p._execute(old, plan, None, asyncio.Event())             # the old run ends meanwhile
        assert old["state"] == "done" and p._active == rid and p._cancel is not None
        ma.STOP_REPORT_WAIT_S = 0.05
        got = (await p.stop_task()).value                              # the live claim is stopped, but nothing ends it here
        assert got["requested"] is True and got["stopped"] is False and got["still_running"][0]["id"] == rid
        assert p._release("old") is False and p._release(rid) is True and p._active is None
    asyncio.run(go())


def test_resume_respects_the_active_run(monkeypatch, tmp_path):
    async def go():
        p = make(monkeypatch, tmp_path)
        plan = Plan("Think", [Step("s1", "think", "x")])
        p._store.save({"run_id": "held1", "task": "t", "mode": "work", "summary": "s", "state": "interrupted",
                       "plan": plan_to_dict(plan), "running": [], "results": {}})
        p._claim()
        res = await p.resume_run(run_id="held1")
        assert type(res).__name__ == "Err" and p.dispatched == []
    asyncio.run(go())


# ---------- Stop while planning ----------

def test_stop_during_planning_cancels_the_plan_and_no_step_ever_starts(monkeypatch, tmp_path):
    """The old code answered "Nothing is running." while planning, and the plan then ran."""
    async def go():
        gate = asyncio.Event()
        p = make(monkeypatch, tmp_path, plan_gate=gate)
        task = asyncio.ensure_future(p.run_task(task="open notepad", source="widget"))
        await asyncio.sleep(0.02)
        assert marker(p)["state"] == "planning" and p._store.ids() == []   # the widget sees it; no run file yet
        stop = (await p.stop_task()).value
        assert stop["stopped"] is True and "planned" in stop["message"]
        res = await task
        assert res.value["state"] == "cancelled" and "Nothing was run" in res.value["message"]
        gate.set()                                                      # the model answers late: ignored
        await asyncio.sleep(0.05)
        assert p.dispatched == [] and p.calls == [] and p._active is None and p._store.ids() == []
        assert marker(p)["state"] == "cancelled"
        assert [m["metadata"]["state"] for m in p.pushed] == ["cancelled"]
        assert (await p.stop_task()).value["stopped"] is False
    asyncio.run(go())


def test_stop_arriving_just_as_planning_ends_still_starts_nothing(monkeypatch, tmp_path):
    async def go():
        p = make(monkeypatch, tmp_path)
        real = p._make_plan

        async def plan_then_stop(*a, **k):
            out = await real(*a, **k)
            await p.stop_task()                                        # Stop lands in the same moment
            return out
        p._make_plan = plan_then_stop
        res = await p.run_task(task="x")
        assert res.value["state"] == "cancelled" and p.dispatched == [] and p._store.ids() == []
    asyncio.run(go())


# ---------- the outcome is reported truthfully (D4) ----------

def test_a_plan_that_fails_after_the_widget_stopped_waiting_is_reported(monkeypatch, tmp_path):
    async def go():
        p = make(monkeypatch, tmp_path, plan={"summary": "x", "steps": [{"id": "s1", "agent": "plugin", "plugin": "evil:run", "goal": "g"}]})
        res = await p.run_task(task="do it", source="widget")
        assert type(res).__name__ == "Err" and p._active is None and p._store.ids() == []
        m = marker(p)
        assert m["state"] == "failed" and "not available" in m["error"]
        assert len(p.pushed) == 1 and p.pushed[0]["parts"][0]["text"].startswith("I could not plan that task")
        q = make(monkeypatch, tmp_path / "chat", plan={"summary": "x", "steps": []})
        await q.run_task(task="do it")                                 # a chat tool call gets the error as its answer
        assert q.pushed == [] and marker(q)["state"] == "failed"
    asyncio.run(go())


def test_the_marker_gives_way_to_the_run_file_once_the_run_starts(monkeypatch, tmp_path):
    async def go():
        p = make(monkeypatch, tmp_path)
        res = await p.run_task(task="x")
        await settle(p)
        assert marker(p) is None and p._store.load(res.value["run_id"])["state"] == "done"
    asyncio.run(go())


def test_a_restart_during_planning_leaves_a_failed_marker_not_a_planning_one(tmp_path):
    store = RunStore(tmp_path / "runs")
    store.mark({"run_id": "r1", "state": "planning"})
    store.recover()
    assert store.read_mark()["state"] == "failed" and store.ids() == []


# ---------- Stop while a step runs ----------

def test_repeated_stop_is_harmless_and_honest(monkeypatch, tmp_path):
    async def go():
        gate = asyncio.Event()

        async def think(goal):
            await gate.wait()
            return "x"
        p = make(monkeypatch, tmp_path, plan=TWO, think=think)
        await p.run_task(task="two things")
        await asyncio.sleep(0.05)
        # Stop is requested, then answered only once the run has really ended (the step in flight is cancelled after the grace)
        a, b = await asyncio.gather(p.stop_task(), p.stop_task())
        first, second = a.value, b.value
        assert first["requested"] is True and first["stopped"] is True and first["still_running"] == [] and "already" not in first
        assert second["requested"] is True and second["stopped"] is True and second["already"] is True
        await settle(p)
        assert (await p.stop_task()).value == {"requested": False, "stopped": False, "still_running": [], "message": "Nothing is running."}
    asyncio.run(go())


def test_delayed_stop_cancels_the_step_in_flight_and_no_further_step_starts(monkeypatch, tmp_path):
    """The old executor only checked Stop before a step: the step in flight ran on and its result was waited for."""
    async def go():
        started = asyncio.Event()

        async def think(goal):
            started.set()
            await asyncio.sleep(30)                                    # a long step
            return "x"
        p = make(monkeypatch, tmp_path, plan=TWO, think=think)
        rid = (await p.run_task(task="two things")).value["run_id"]
        await asyncio.wait_for(started.wait(), 2)
        await asyncio.sleep(0.2)                                       # Stop comes late, mid-step
        await p.stop_task()
        await asyncio.wait_for(settle(p), 3)                           # well before the 30 s step would have ended
        rec = p._store.load(rid)
        assert rec["state"] == "cancelled" and len(p.dispatched) == 1
        assert rec["results"]["s1"]["outcome"] == "failure" and "stopped while running" in rec["results"]["s1"]["error"]
        assert rec["results"]["s2"]["outcome"] == "not_executed" and rec["results"]["s2"]["error"] == "cancelled"
    asyncio.run(go())


def test_stop_during_a_desktop_step_asks_the_desktop_plugin_to_stop_its_job(monkeypatch, tmp_path):
    async def go():
        running = asyncio.Event()
        stopped = asyncio.Event()

        async def call_entry(ref, params=None, timeout=10.0):
            running.set()
            await asyncio.sleep(30)                                    # the PC job runs (the runtime does not pass a cancel on)
            return Ok({"status": "done"})

        def stop_pc(params):
            stopped.set()
            return Ok({"data": {"stopped_jobs": 1}})
        p = make(monkeypatch, tmp_path, plan=PC, call_entry=call_entry, json_entries={"desktop:stop_pc_task": stop_pc})
        rid = (await p.run_task(task="open notepad on the PC")).value["run_id"]
        await asyncio.wait_for(running.wait(), 2)
        await p.stop_task()
        await asyncio.wait_for(settle(p), 3)
        assert stopped.is_set()
        rec = p._store.load(rid)
        s1 = rec["results"]["s1"]
        assert rec["state"] == "cancelled" and s1["outcome"] == "unknown" and s1["held"] is True
        assert "desktop:stop_pc_task was asked to stop it" in s1["error"]
        assert rec["results"]["s2"]["outcome"] == "not_executed" and p.dispatched == []
        assert "may or may not have happened" in p.pushed[-1]["parts"][0]["text"]
    asyncio.run(go())


def test_stop_during_a_jarvis_step_cancels_that_command(monkeypatch, tmp_path):
    real_sleep = asyncio.sleep

    async def fast(d, *a, **k):
        await real_sleep(0.01 if d == 5.0 else d)
    monkeypatch.setattr(asyncio, "sleep", fast)

    async def go():
        polled = asyncio.Event()

        def result(params):
            polled.set()
            return Ok({"data": {"state": "running"}})
        p = make(monkeypatch, tmp_path, plan=JARVIS, json_entries={
            "jarvis_bridge:delegate_task": lambda a: Ok({"data": {"command_id": "c-7"}}),
            "jarvis_bridge:task_result": result,
            "jarvis_bridge:pending_approvals": lambda a: Ok({"data": {"count": 0}}),
            "jarvis_bridge:cancel_task": lambda a: Ok({"data": {"command_id": a["command_id"], "cancelled": True}})})
        rid = (await p.run_task(task="fix the build", mode="code")).value["run_id"]
        await asyncio.wait_for(polled.wait(), 2)
        await p.stop_task()
        await asyncio.wait_for(settle(p), 3)
        assert ("jarvis_bridge:cancel_task", {"command_id": "c-7"}) in p.calls
        s1 = p._store.load(rid)["results"]["s1"]
        assert s1["held"] is True and "JARVIS command was cancelled" in s1["error"] and "Stop all" in s1["error"]
        assert p.dispatched == []
    asyncio.run(go())


def test_stop_while_the_hand_over_to_jarvis_is_in_flight_withdraws_the_command_it_creates(monkeypatch, tmp_path):
    async def go():
        sending, release = asyncio.Event(), asyncio.Event()

        async def delegate(params):
            sending.set()
            await release.wait()
            return Ok({"data": {"command_id": "c-9"}})
        p = make(monkeypatch, tmp_path, plan=JARVIS, json_entries={
            "jarvis_bridge:delegate_task": delegate,
            "jarvis_bridge:cancel_task": lambda a: Ok({"data": {"cancelled": True}})})
        await p.run_task(task="fix the build", mode="code")
        await asyncio.wait_for(sending.wait(), 2)
        await p.stop_task()
        await asyncio.sleep(0.05)
        release.set()                                                  # the kernel answers with the id after the Stop
        await asyncio.wait_for(settle(p), 3)
        assert ("jarvis_bridge:cancel_task", {"command_id": "c-9"}) in p.calls
        assert not any(c[0] == "jarvis_bridge:task_result" for c in p.calls)
    asyncio.run(go())


def test_the_executor_never_starts_a_step_once_stop_is_set(monkeypatch):
    """Stop set at the last moment before a step begins (here: from on_start): the dispatcher is never entered."""
    async def go():
        cancel = asyncio.Event()
        called = []

        async def dispatch(step, inputs):
            called.append(step.id)
            return "x"
        plan = Plan("p", [Step("a", "think", "g"), Step("b", "think", "g")])
        res = await run_plan(plan, dispatch, cancel=cancel, on_start=lambda s: cancel.set(), parallel=2)
        assert called == [] and [r.outcome for r in res] == ["not_executed", "not_executed"]
    asyncio.run(go())


# ---------- "Hey Neko, stop" during planning, end to end with the real widget and wake loop ----------

def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_hey_neko_stop_during_planning_stops_it(monkeypatch, tmp_path):
    """The real widget newest_run() reads main_agent's planning marker, so the real wake loop sees a task running, and the
    stop reaches main_agent, which cancels the planning: the plan never runs."""
    ws = _load("widget_server_rc", ROOT / "jarvis" / "widget" / "widget_server.py")
    sys.path.insert(0, str(ROOT / "jarvis" / "wake"))
    import test_wake_listener as T
    import wake_listener as wl

    async def go():
        gate = asyncio.Event()
        p = make(monkeypatch, tmp_path, plan_gate=gate)
        loop = asyncio.get_running_loop()
        planning = asyncio.ensure_future(p.run_task(task="open notepad", source="widget"))
        await asyncio.sleep(0.02)

        def running():
            return (ws.newest_run(str(p._store.dir)) or {}).get("state") in ("running", "planning")

        def stop():
            return {"ok": asyncio.run_coroutine_threadsafe(p.stop_task(), loop).result(5).value["stopped"]}
        w = wl.WakeLoop(lambda: T.FakeDetector(), vad_factory=T.FakeVad, transcribe=lambda a: "Stop.", submit=lambda t: {"ok": True},
                        stop=stop, speaking=lambda: False, running=running, chime=lambda k: None)
        await w.start()
        await T.feed(w, [T.QUIET] * 5 + T.seconds(1, T.SPEECH) + T.seconds(1.2, T.QUIET))
        assert w.last["what"] == "stop the running task" and w.last["ok"] is True
        res = await planning
        gate.set()
        await asyncio.sleep(0.05)
        assert res.value["state"] == "cancelled" and p.dispatched == []
        assert ws.newest_run(str(p._store.dir))["state"] == "cancelled"
    asyncio.run(go())
