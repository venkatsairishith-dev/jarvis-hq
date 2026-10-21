"""stop_task answers honestly, and a step TIMEOUT stops the helper like a Stop does (re-review M14 / L1-L3, findings c and d).

* stop_task: `requested` when there was something to stop; `stopped` only once the run (and the helper's worker) really ended;
  otherwise `still_running` names what has not. The widget shows that as "stop requested, N still running", not ok.
* A step that runs out of time is cancelled AND its plugin is asked to stop (the desktop plugin would otherwise run on after
  main_agent reported "timed out; unknown"); the result is unknown/held for a step with side effects, with what ended.
No model, no network."""
from __future__ import annotations

import asyncio
import importlib.util
import json
from pathlib import Path

from test_payload_outcome import PC, Ok, make, settle  # noqa: F401

from plugin.plugins import main_agent as ma
from plugin.plugins.main_agent.executor import StepContext, run_plan
from plugin.plugins.main_agent.planner import Plan, Step

ROOT = Path(__file__).resolve().parents[4]


def stop_reply(still=(), ended=1):
    return lambda params: Ok({"data": {"stopped_jobs": ended, "stopped": [], "still_running": list(still), "requested": 1}})


# ------------------------------------------------------------------ (c) stop_task is honest
def test_stop_while_the_helper_has_not_ended_is_requested_not_stopped(monkeypatch, tmp_path):
    monkeypatch.setattr(ma, "STOP_REPORT_WAIT_S", 3.0)

    async def go():
        running, release = asyncio.Event(), asyncio.Event()

        async def entry(ref, params):
            running.set()
            await release.wait()                                  # the desktop task: it ends only when the helper is released
            return Ok({"status": "completed"})
        still = [{"id": "task-ab12", "kind": "task", "state": "stopping"}]
        p = make(monkeypatch, tmp_path, PC, entry, {"desktop:stop_pc_task": stop_reply(still, ended=0)})
        rid = (await p.run_task(task="open notepad on the PC")).value["run_id"]
        await asyncio.wait_for(running.wait(), 2)
        got = (await p.stop_task()).value
        release.set()
        await asyncio.wait_for(settle(p), 5)
        return got, rid
    got, rid = asyncio.run(go())
    assert got["requested"] is True and got["stopped"] is False, got
    assert got["still_running"] and got["still_running"][0]["id"] == "task-ab12"
    assert "Stop requested, 1 still running" in got["message"] and got["run_id"] == rid


def test_stop_is_stopped_only_when_the_run_and_its_helper_have_ended(monkeypatch, tmp_path):
    async def go():
        running = asyncio.Event()

        async def entry(ref, params):
            running.set()
            await asyncio.sleep(30)
            return Ok({"status": "completed"})
        p = make(monkeypatch, tmp_path, PC, entry, {"desktop:stop_pc_task": stop_reply(ended=1)})
        await p.run_task(task="open notepad on the PC")
        await asyncio.wait_for(running.wait(), 2)
        got = (await p.stop_task()).value
        return got, p._active
    got, active = asyncio.run(go())
    assert got["requested"] is True and got["stopped"] is True and got["still_running"] == [] and active is None


def test_stop_that_the_run_does_not_finish_in_time_lists_the_run_as_still_running(monkeypatch, tmp_path):
    monkeypatch.setattr(ma, "STOP_REPORT_WAIT_S", 0.1)
    p = make(monkeypatch, tmp_path, PC, None)
    p._active, p._cancel = "r-1", asyncio.Event()
    got = asyncio.run(p.stop_task()).value
    assert got == {"requested": True, "stopped": False, "run_id": "r-1", "state": "", "message": got["message"],
                   "still_running": [{"id": "r-1", "kind": "main-agent run", "state": "stopping"}]}
    assert "1 still running" in got["message"]


def _widget():
    path = ROOT / "jarvis" / "widget" / "widget_server.py"
    import sys
    sys.path.insert(0, str(path.parent))
    spec = importlib.util.spec_from_file_location("widget_server_for_stop_report", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _widget_stop(ws, result, monkeypatch):
    monkeypatch.setattr(ws, "run_plugin_entry", lambda *a, **k: {"ok": True, "result": result})
    return ws.stop_main_task()


def test_the_widget_shows_a_requested_stop_with_helpers_still_running_as_not_ok(monkeypatch):
    ws = _widget()
    got = _widget_stop(ws, {"requested": True, "stopped": False, "still_running": [{"id": "a"}, {"id": "b"}], "message": "Stop requested"},
                       monkeypatch)
    assert got["ok"] is False and got["stopped"] is False and got["requested"] is True and got["still_running"] == 2
    assert got["error"] == "stop requested, 2 still running"
    done = _widget_stop(ws, {"requested": True, "stopped": True, "still_running": [], "message": "Stopped"}, monkeypatch)
    assert done["ok"] is True and done["stopped"] is True and done["still_running"] == 0
    nothing = _widget_stop(ws, {"requested": False, "stopped": False, "still_running": [], "message": "Nothing is running."}, monkeypatch)
    assert nothing["ok"] is False and nothing["error"] == "nothing to stop"
    old = _widget_stop(ws, {"stopped": True, "message": "Stopped"}, monkeypatch)             # an older main_agent: stopped only
    assert old["ok"] is True and old["stopped"] is True


# ------------------------------------------------------------------ (d) a step timeout stops the helper too
def _timeout_plan(effect="non_idempotent"):
    return Plan("pc", [Step("s1", "plugin", "open notepad", "desktop:run_pc_task", {"request": "open notepad"}, [], effect)])


def test_a_step_that_times_out_asks_the_desktop_plugin_to_stop_and_reports_unknown_and_held(monkeypatch, tmp_path):
    asked = []

    async def entry(ref, params):
        await asyncio.sleep(30)                                   # the desktop task: still running when the step runs out of time
        return Ok({"status": "completed"})

    def stop(params):
        asked.append(params)
        return Ok({"data": {"stopped_jobs": 1, "still_running": [], "requested": 1}})
    p = make(monkeypatch, tmp_path, PC, entry, {"desktop:stop_pc_task": stop})
    results = asyncio.run(run_plan(_timeout_plan(), p._dispatch, step_timeout=0.2, total_timeout=30, retries=0))
    [r] = results
    assert asked == [{}], "the plugin was not asked to stop when the step timed out"
    assert r.outcome == "unknown" and r.held is True
    assert "timed out" in r.error and "desktop:stop_pc_task was asked to stop it" in r.error


def test_a_timed_out_step_whose_helper_did_not_end_says_so(monkeypatch, tmp_path):
    async def entry(ref, params):
        await asyncio.sleep(30)

    still = [{"id": "task-9", "kind": "task", "state": "stopping"}]
    p = make(monkeypatch, tmp_path, PC, entry, {"desktop:stop_pc_task": stop_reply(still, ended=0)})
    [r] = asyncio.run(run_plan(_timeout_plan(), p._dispatch, step_timeout=0.2, total_timeout=30, retries=0))
    assert r.outcome == "unknown" and r.held is True and "have not ended yet (task-9)" in r.error


def test_a_plugin_that_answers_with_its_own_timeout_is_also_asked_to_stop(monkeypatch, tmp_path):
    asked = []

    async def entry(ref, params):
        return ma.Err("the call timed out after 300 s")

    def stop(params):
        asked.append(params)
        return Ok({"data": {"stopped_jobs": 1, "still_running": [], "requested": 1}})
    p = make(monkeypatch, tmp_path, PC, entry, {"desktop:stop_pc_task": stop})
    [r] = asyncio.run(run_plan(_timeout_plan(), p._dispatch, step_timeout=5, total_timeout=30, retries=0))
    assert asked == [{}] and r.outcome == "unknown" and r.held is True and "did not answer in time" in r.error


def test_a_step_that_finishes_in_time_is_not_stopped(monkeypatch, tmp_path):
    asked = []

    async def entry(ref, params):
        return Ok({"status": "completed", "summary": "Done"})
    p = make(monkeypatch, tmp_path, PC, entry, {"desktop:stop_pc_task": lambda params: (asked.append(params), Ok({}))[1]})
    [r] = asyncio.run(run_plan(_timeout_plan(), p._dispatch, step_timeout=5, total_timeout=30, retries=0))
    assert r.outcome == "success" and asked == []


def test_a_read_only_step_that_times_out_is_a_failure_not_held_but_its_helper_is_still_stopped(monkeypatch, tmp_path):
    asked = []

    async def entry(ref, params):
        await asyncio.sleep(30)
    p = make(monkeypatch, tmp_path, PC, entry, {"desktop:stop_pc_task": lambda params: (asked.append(params), Ok({"data": {}}))[1]})
    [r] = asyncio.run(run_plan(_timeout_plan("none"), p._dispatch, step_timeout=0.2, total_timeout=30, retries=0))
    assert asked == [{}] and r.held is False and r.outcome == "unknown"
