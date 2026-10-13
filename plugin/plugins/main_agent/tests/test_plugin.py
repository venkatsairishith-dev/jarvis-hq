"""The plugin class wired to fakes: no model, no network."""

from __future__ import annotations

import asyncio
import json
import logging
import types

import pytest

from plugin.plugins import main_agent as ma
from plugin.plugins.main_agent.planner import Capability

PLAN = {"summary": "Look up then summarise", "steps": [
    {"id": "s1", "agent": "plugin", "plugin": "web_search:search", "goal": "find", "args": {"query": "cats"}},
    {"id": "s2", "agent": "think", "goal": "summarise", "depends_on": ["s1"]}]}
CAPS = [Capability("think", "think", "t"),
        Capability("plugin", "web_search:search", "s", {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]})]


class Ok:
    def __init__(self, value):
        self.value = value


class Err:
    def __init__(self, error):
        self.error = error


def make(monkeypatch, llm_reply=None, plugin_result=None):
    p = object.__new__(ma.MainAgentPlugin)
    p._runs, p._active, p._cancel = {}, None, None
    p.logger = logging.getLogger("test")
    p.pushed = []
    p.push_message = lambda **kw: p.pushed.append(kw)

    async def catalogue():
        return CAPS

    async def llm(messages, **kw):
        is_planner = "planner" in messages[0]["content"]
        if is_planner:
            return json.dumps(PLAN) if llm_reply is None else llm_reply
        return "SUMMARY OF CATS"

    p._catalogue, p._llm = catalogue, llm
    calls = []

    async def call_entry(ref, params=None, timeout=10.0):
        calls.append((ref, params))
        return plugin_result if plugin_result is not None else Ok({"summary": "cats are animals"})

    monkeypatch.setattr(ma.MainAgentPlugin, "plugins", types.SimpleNamespace(call_entry=call_entry), raising=False)
    monkeypatch.setattr(ma, "Err", Err)
    p.calls = calls
    return p


def drive(p, coro):
    async def go():
        r = await coro
        # let the background run finish
        for _ in range(100):
            if p._active is None:
                break
            await asyncio.sleep(0.02)
        return r
    return asyncio.run(go())


def test_run_task_plans_runs_the_steps_and_speaks_one_combined_answer(monkeypatch):
    p = make(monkeypatch)
    res = drive(p, p.run_task(task="look up cats and summarise"))
    assert getattr(res, "value", None) is not None
    assert p.calls == [("web_search:search", {"query": "cats"})]            # the plugin step called through N.E.K.O, with validated args
    assert len(p.pushed) == 1
    msg = p.pushed[0]
    assert msg["ai_behavior"] == "respond" and msg["parts"][0]["text"] == "SUMMARY OF CATS" and msg["metadata"]["state"] == "done"
    assert p._active is None and p._cancel is None


def test_a_failing_step_is_reported_honestly_and_releases_the_lock(monkeypatch):
    p = make(monkeypatch, plugin_result=Err("backend down"))
    drive(p, p.run_task(task="look up cats"))
    text = p.pushed[0]["parts"][0]["text"]
    assert "could not finish" in text and p._active is None
    assert list(p._runs.values())[0]["state"] in ("failed", "partial")


def test_a_bad_plan_is_refused_and_nothing_runs(monkeypatch):
    p = make(monkeypatch, llm_reply='{"summary": "x", "steps": [{"id": "s1", "agent": "plugin", "plugin": "evil:run", "goal": "g", "args": {}}]}')
    res = asyncio.run(p.run_task(task="do it"))
    assert type(res).__name__ == "Err" and p.calls == [] and p.pushed == [] and p._active is None


def test_only_one_run_at_a_time(monkeypatch):
    p = make(monkeypatch)
    p._active = "busy"
    res = asyncio.run(p.run_task(task="another"))
    assert type(res).__name__ == "Err" and p.calls == []


def test_empty_task_is_refused(monkeypatch):
    p = make(monkeypatch)
    assert type(asyncio.run(p.run_task(task="  "))).__name__ == "Err"
    assert type(asyncio.run(p.plan_task(task=""))).__name__ == "Err"


def test_plan_task_previews_without_running_anything(monkeypatch):
    p = make(monkeypatch)
    res = asyncio.run(p.plan_task(task="look up cats"))
    assert [s["id"] for s in res.value["steps"]] == ["s1", "s2"] and p.calls == [] and p.pushed == []


def test_stop_task_sets_the_cancel_flag_only_when_something_runs(monkeypatch):
    p = make(monkeypatch)
    assert asyncio.run(p.stop_task()).value["stopped"] is False
    p._active, p._cancel = "r", asyncio.Event()
    assert asyncio.run(p.stop_task()).value["stopped"] is True and p._cancel.is_set()


def test_a_provider_error_becomes_a_clean_message_without_provider_text(monkeypatch):
    """The real _llm: a 401 from the provider must not surface its body (which can echo request details)."""
    import utils.config_manager as cm
    import utils.llm_client as lc

    class Cfg:
        def get_model_api_config(self, kind):
            return {"base_url": "https://provider.example/v1", "model": "m", "api_key": "", "provider_type": None}

    class Bad:
        async def ainvoke(self, messages):
            raise RuntimeError("Error code: 401 - {'error': {'message': 'You didn't provide an API key', 'request_id': 'abc'}}")

    async def fake_create(**kw):
        return Bad()

    monkeypatch.setattr(cm, "get_config_manager", lambda: Cfg())
    monkeypatch.setattr(lc, "create_chat_llm_async", fake_create)
    p = object.__new__(ma.MainAgentPlugin)
    from plugin.plugins.main_agent.executor import StepFailed
    with pytest.raises(StepFailed) as e:
        asyncio.run(p._llm([{"role": "user", "content": "hi"}]))
    assert "API key is missing or wrong" in str(e.value) and "request_id" not in str(e.value) and "abc" not in str(e.value)


def test_an_unconfigured_model_is_reported_before_any_call(monkeypatch):
    import utils.config_manager as cm

    class Cfg:
        def get_model_api_config(self, kind):
            return {"base_url": "", "model": ""}

    monkeypatch.setattr(cm, "get_config_manager", lambda: Cfg())
    p = object.__new__(ma.MainAgentPlugin)
    from plugin.plugins.main_agent.executor import StepFailed
    with pytest.raises(StepFailed) as e:
        asyncio.run(p._llm([{"role": "user", "content": "hi"}]))
    assert "not set up" in str(e.value)
