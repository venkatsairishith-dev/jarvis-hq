"""The computer-use step is followed and stopped as one task (audit "computer-use step has no stop API", 2026-10-18).

N.E.K.O's agent server has a per-task stop (POST /tasks/<id>/cancel) and answers /computer_use/run at once with the task id,
so the step used to report "success" with no result while the desktop was still being controlled, and Stop could not reach it.
Now the step polls that one task, cancels that one task on Stop or timeout, never calls the server's end_all/admin control,
and never starts desktop control while Stop is set. A fake agent server (httpx.MockTransport); no network, no model."""
from __future__ import annotations

import asyncio
import json
import logging
import types

import httpx
import pytest

from plugin.plugins import main_agent as ma
from plugin.plugins.main_agent.executor import StepCancelled, StepContext, StepFailed, StepUnknown
from plugin.plugins.main_agent.planner import Capability, Step
from plugin.plugins.main_agent.store import RunStore

COMPUTER = {"summary": "Use the desktop", "steps": [{"id": "s1", "agent": "computer", "goal": "open the calculator"},
                                                     {"id": "s2", "agent": "think", "goal": "report", "depends_on": ["s1"]}]}
CAPS = [Capability("think", "think", "t"), Capability("computer", "computer", "c")]
STEP = Step(id="s1", kind="computer", ref="computer", goal="open the calculator", args={}, depends_on=[], effect="non_idempotent")


class Ok:
    def __init__(self, value):
        self.value = value


class Err:
    def __init__(self, error):
        self.error = error


class AgentServer:
    """The parts of N.E.K.O's agent server the step talks to. Anything else it is asked for is recorded as a forbidden call."""

    def __init__(self, statuses=("queued", "running", "completed"), result="The calculator is open.", error=None, run_reply=None,
                 cancel_reply=None, fail_status_reads=0):
        self.statuses, self.result, self.error, self.run_reply, self.cancel_reply = list(statuses), result, error, run_reply, cancel_reply
        self.calls, self.fail_status_reads, self.reads = [], fail_status_reads, 0
        self.cancelled = []

    def handle(self, request: httpx.Request) -> httpx.Response:
        path, method = request.url.path, request.method
        self.calls.append((method, path))
        if (method, path) == ("POST", "/computer_use/run"):
            if self.run_reply is not None:
                return httpx.Response(self.run_reply[0], json=self.run_reply[1])
            return httpx.Response(200, json={"success": True, "task_id": "t-1", "status": "queued"})
        if method == "GET" and path == "/tasks/t-1":
            self.reads += 1
            if self.reads <= self.fail_status_reads:
                return httpx.Response(502, json={"error": "proxy"})
            st = self.statuses[0] if len(self.statuses) == 1 else self.statuses.pop(0)
            return httpx.Response(200, json={"id": "t-1", "status": st, "result": self.result if st == "completed" else None,
                                             "error": self.error})
        if (method, path) == ("POST", "/tasks/t-1/cancel"):
            self.cancelled.append("t-1")
            code, body = self.cancel_reply or (200, {"success": True, "task_id": "t-1", "status": "cancelled"})
            return httpx.Response(code, json=body)
        return httpx.Response(404, json={"error": "no such route"})

    @property
    def broad_calls(self):                       # calls that would stop or change more than this one task
        return [c for c in self.calls if "end_all" in c[1] or "admin" in c[1] or c[1] in ("/agent/command", "/agent/flags")]


@pytest.fixture
def server(monkeypatch):
    srv = AgentServer()
    real = httpx.AsyncClient
    monkeypatch.setattr(ma.httpx, "AsyncClient", lambda **kw: real(transport=httpx.MockTransport(srv.handle)))
    monkeypatch.setattr(ma, "COMPUTER_POLL_S", 0.01)
    return srv


def plugin(monkeypatch, tmp_path, plan=COMPUTER):
    p = object.__new__(ma.MainAgentPlugin)
    p._runs, p._active, p._cancel = {}, None, None
    p._store = RunStore(tmp_path / "runs")
    p.logger = logging.getLogger("test")
    p.pushed = []
    p.push_message = lambda **kw: p.pushed.append(kw)
    p._agent_base_url = lambda: ""
    p.dispatched = []

    async def catalogue(mode="work"):
        return CAPS

    async def llm(messages, **kw):
        if "planner" in messages[0]["content"]:
            return json.dumps(plan)
        p.dispatched.append(messages[1]["content"].split("\n", 1)[0])
        return "TEXT"
    p._catalogue, p._llm = catalogue, llm
    monkeypatch.setattr(ma.MainAgentPlugin, "plugins", types.SimpleNamespace(), raising=False)
    monkeypatch.setattr(ma, "Err", Err)
    return p


async def settle(p, limit=500):
    for _ in range(limit):
        if p._active is None:
            return
        await asyncio.sleep(0.01)
    raise AssertionError("the run never finished")


def dispatch(p, ctx=None, step=STEP):
    return p._dispatch(step, {}, ctx or StepContext(None))


# ------------------------------------------------------------------ following the task
def test_the_step_waits_for_the_task_and_returns_its_result(server, monkeypatch, tmp_path):
    p = plugin(monkeypatch, tmp_path)
    out = asyncio.run(dispatch(p))
    assert out == "The calculator is open."
    assert [c for c in server.calls if c[0] == "GET"] == [("GET", "/tasks/t-1")] * 3        # queued, running, completed
    assert server.cancelled == []


def test_a_failed_task_is_a_failed_step_with_its_reason(server, monkeypatch, tmp_path):
    server.statuses, server.error = ["running", "failed"], "no screen access"
    p = plugin(monkeypatch, tmp_path)
    with pytest.raises(StepFailed, match="no screen access"):
        asyncio.run(dispatch(p))


def test_a_task_cancelled_elsewhere_is_unknown_not_success(server, monkeypatch, tmp_path):
    server.statuses = ["running", "cancelled"]
    p = plugin(monkeypatch, tmp_path)
    with pytest.raises(StepUnknown, match="cancelled before it finished"):
        asyncio.run(dispatch(p))


def test_an_old_inline_answer_still_works(server, monkeypatch, tmp_path):
    server.run_reply = (200, {"success": True, "result": "done inline"})
    assert asyncio.run(dispatch(plugin(monkeypatch, tmp_path))) == "done inline"
    assert not [c for c in server.calls if c[0] == "GET"]


def test_a_start_with_no_task_id_is_said_to_be_unstoppable_not_success(server, monkeypatch, tmp_path):
    server.run_reply = (200, {"success": True})
    with pytest.raises(StepUnknown, match="neither followed nor stopped"):
        asyncio.run(dispatch(plugin(monkeypatch, tmp_path)))


def test_a_refused_or_unreachable_helper_is_a_failure_with_nothing_sent(server, monkeypatch, tmp_path):
    server.run_reply = (503, {"error": "ComputerUse not ready"})
    with pytest.raises(StepFailed, match="HTTP 503"):
        asyncio.run(dispatch(plugin(monkeypatch, tmp_path)))
    server.run_reply = (200, {"success": False, "error": "duplicate"})
    with pytest.raises(StepFailed, match="could not start"):
        asyncio.run(dispatch(plugin(monkeypatch, tmp_path)))


def test_losing_contact_cancels_the_one_task_and_says_so(server, monkeypatch, tmp_path):
    server.fail_status_reads = 99
    with pytest.raises(StepUnknown, match="lost contact"):
        asyncio.run(dispatch(plugin(monkeypatch, tmp_path)))
    assert server.cancelled == ["t-1"] and not server.broad_calls


def test_a_step_that_runs_too_long_cancels_its_one_task(server, monkeypatch, tmp_path):
    server.statuses = ["running"]
    p = plugin(monkeypatch, tmp_path)
    monkeypatch.setenv("MAIN_AGENT_STEP_TIMEOUT", "10")
    real = ma._int_env
    monkeypatch.setattr(ma, "_int_env", lambda name, d, lo, hi: 0.05 if name == "MAIN_AGENT_STEP_TIMEOUT" else real(name, d, lo, hi))
    with pytest.raises(StepUnknown, match="did not finish in time"):
        asyncio.run(dispatch(p))
    assert server.cancelled == ["t-1"] and not server.broad_calls


# ------------------------------------------------------------------ Stop
def test_stop_cancels_that_one_task_and_never_uses_the_broad_stop(server, monkeypatch, tmp_path):
    server.statuses = ["running"]                      # it would run for ever
    p = plugin(monkeypatch, tmp_path)

    async def go():
        rid = (await p.run_task(task="use the calculator")).value["run_id"]
        for _ in range(300):
            if ("GET", "/tasks/t-1") in server.calls:
                break
            await asyncio.sleep(0.01)
        await p.stop_task()
        await asyncio.wait_for(settle(p), 5)
        return rid
    rid = asyncio.run(go())
    assert server.cancelled == ["t-1"], server.calls
    assert not server.broad_calls                       # end_all would stop everything the agent server runs for everyone
    rec = p._store.load(rid)
    s1, s2 = rec["results"]["s1"], rec["results"]["s2"]
    assert rec["state"] == "cancelled" and s1["held"] is True and s1["outcome"] == "unknown"      # a click may have happened
    assert "that one task only" in s1["error"] and "cancelled" in s1["error"]
    assert s2["outcome"] == "not_executed" and p.dispatched == []


def test_a_cancel_the_server_does_not_confirm_is_reported_honestly(server, monkeypatch, tmp_path):
    server.statuses, server.cancel_reply = ["running"], (200, {"success": False, "error": "task is not active", "status": "running"})
    p = plugin(monkeypatch, tmp_path)

    async def go():
        rid = (await p.run_task(task="use the calculator")).value["run_id"]
        for _ in range(300):
            if ("GET", "/tasks/t-1") in server.calls:
                break
            await asyncio.sleep(0.01)
        await p.stop_task()
        await asyncio.wait_for(settle(p), 5)
        return rid
    s1 = p._store.load(asyncio.run(go()))["results"]["s1"]
    assert "did not confirm the cancel" in s1["error"] and "may still be controlling the desktop" in s1["error"]


def test_stop_while_the_start_request_is_in_flight_says_it_cannot_be_stopped(monkeypatch, tmp_path):
    started, release = asyncio.Event(), asyncio.Event()
    calls = []

    async def handler(request):
        calls.append((request.method, request.url.path))
        started.set()
        await release.wait()
        return httpx.Response(200, json={"success": True, "task_id": "t-1"})
    real = httpx.AsyncClient
    monkeypatch.setattr(ma.httpx, "AsyncClient", lambda **kw: real(transport=httpx.MockTransport(handler)))
    monkeypatch.setattr(ma, "COMPUTER_POLL_S", 0.01)
    p = plugin(monkeypatch, tmp_path)

    async def go():
        rid = (await p.run_task(task="use the calculator")).value["run_id"]
        await asyncio.wait_for(started.wait(), 2)
        await p.stop_task()
        await asyncio.wait_for(settle(p), 5)
        release.set()
        return rid
    s1 = p._store.load(asyncio.run(go()))["results"]["s1"]
    assert "cannot be stopped from here" in s1["error"] and s1["held"] is True
    assert ("POST", "/tasks/t-1/cancel") not in calls and not [c for c in calls if "end_all" in c[1] or "admin" in c[1]]


def test_a_computer_step_never_starts_while_stop_is_set(server, monkeypatch, tmp_path):
    p = plugin(monkeypatch, tmp_path)
    cancel = asyncio.Event()
    cancel.set()
    with pytest.raises(StepCancelled) as e:
        asyncio.run(dispatch(p, StepContext(cancel)))
    assert e.value.started is False
    assert server.calls == []                           # not even the start request was sent


def test_a_stop_before_the_run_starts_leaves_the_desktop_alone(server, monkeypatch, tmp_path):
    p = plugin(monkeypatch, tmp_path)
    gate = asyncio.Event()

    async def slow_catalogue(mode="work"):
        await gate.wait()
        return CAPS
    p._catalogue = slow_catalogue

    async def go():
        t = asyncio.ensure_future(p.run_task(task="use the calculator"))
        await asyncio.sleep(0.05)
        await p.stop_task()
        await t
        await settle(p)
    asyncio.run(go())
    assert server.calls == []
