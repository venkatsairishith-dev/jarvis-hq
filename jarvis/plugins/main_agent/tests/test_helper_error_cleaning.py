"""Re-review M12: the JARVIS helper echoes what they saw in their error strings; those reached the step error,
the run file and the chat push raw. Now cleaned at the source. HTTP edge: httpx MockTransport. Synthetic canaries only."""
import asyncio

import pytest

from jarvis.pluginapi.log import BraceLogger
from jarvis.plugins import main_agent as ma
from jarvis.plugins._shared.tests.fakes import ALL, C_EMAIL, C_KEY, C_PATH
from jarvis.plugins.main_agent.executor import StepContext, StepFailed
from jarvis.plugins.main_agent.planner import Step

BAD = f"could not open {C_PATH} with {C_KEY} for {C_EMAIL}"


def leaks(text):
    return [c for c in ALL if c in str(text)]


def plugin():
    p = object.__new__(ma.MainAgentPlugin)
    p._agent_base_url = lambda: ""
    p.logger = BraceLogger("test")
    return p


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
