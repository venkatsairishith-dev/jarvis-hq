"""Re-review M12: the browser / computer / JARVIS helpers echo what they saw in their error strings; those reached the step error,
the run file and the chat push raw. Now cleaned at the source. HTTP edge: httpx MockTransport. Synthetic canaries only."""
import asyncio
import json
import logging
import types

import httpx
import pytest

from plugin.plugins import main_agent as ma
from plugin.plugins._shared.tests.fakes import ALL, C_EMAIL, C_KEY, C_PATH
from plugin.plugins.main_agent.executor import StepContext, StepFailed
from plugin.plugins.main_agent.planner import Step

BAD = f"could not open {C_PATH} with {C_KEY} for {C_EMAIL}"


def leaks(text):
    return [c for c in ALL if c in str(text)]


def plugin():
    p = object.__new__(ma.MainAgentPlugin)
    p._agent_base_url = lambda: ""
    p.logger = logging.getLogger("test")
    return p


@pytest.fixture
def agent(monkeypatch):
    state = {"run": {"success": False, "error": BAD}, "task": {"status": "failed", "error": BAD}}

    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.path in ("/browser_use/run",):
            return httpx.Response(200, json=state["run"])
        if request.url.path == "/computer_use/run":
            return httpx.Response(200, json=state["run"])
        if request.url.path.startswith("/tasks/"):
            return httpx.Response(200, json=state["task"])
        return httpx.Response(404, json={})
    real = httpx.AsyncClient
    monkeypatch.setattr(ma.httpx, "AsyncClient", lambda **kw: real(transport=httpx.MockTransport(handle)))
    monkeypatch.setattr(ma, "COMPUTER_POLL_S", 0.01)
    return state


def test_a_browser_helper_error_is_cleaned(agent):
    with pytest.raises(StepFailed) as e:
        asyncio.run(plugin()._dispatch(Step("s1", "browser", "look it up"), {}, StepContext()))
    assert leaks(e.value) == [] and "could not open" in str(e.value)


def test_a_computer_helper_error_is_cleaned_at_start_and_while_following(agent):
    with pytest.raises(StepFailed) as e:
        asyncio.run(plugin()._dispatch(Step("s1", "computer", "click it"), {}, StepContext()))
    assert leaks(e.value) == []
    agent["run"] = {"success": True, "task_id": "t-1"}
    with pytest.raises(StepFailed) as e:
        asyncio.run(plugin()._dispatch(Step("s1", "computer", "click it"), {}, StepContext()))
    assert leaks(e.value) == [] and "could not finish" in str(e.value)


def test_a_jarvis_failure_message_is_cleaned(monkeypatch):
    class Ok:
        def __init__(self, value):
            self.value = value

    class Plugins:
        async def call_entry_json(self, ref, params=None, timeout=0):
            if ref == "jarvis_bridge:delegate_task":
                return Ok({"data": {"command_id": "c1"}})
            if ref == "jarvis_bridge:task_result":
                return Ok({"data": {"state": "failed", "message": f"JARVIS could not complete it: {BAD}"}})
            return Ok({"data": {"count": 0}})
    monkeypatch.setattr(ma.MainAgentPlugin, "plugins", Plugins(), raising=False)

    async def no_sleep(_):
        return None
    monkeypatch.setattr(ma.asyncio, "sleep", no_sleep)
    with pytest.raises(StepFailed) as e:
        asyncio.run(plugin()._dispatch(Step("s1", "jarvis", "do it"), {}, StepContext()))
    assert leaks(e.value) == []
