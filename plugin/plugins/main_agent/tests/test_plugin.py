"""The plugin class wired to fakes: no model, no network."""

from __future__ import annotations

import asyncio
import json
import logging
import types

import pytest

from plugin.plugins import main_agent as ma
from plugin.plugins.main_agent.executor import StepResult
from plugin.plugins.main_agent.planner import Capability, Plan, Step
from plugin.plugins.main_agent.store import RunStore, plan_to_dict, result_to_dict

PLAN = {"summary": "Look up then summarise", "steps": [
    {"id": "s1", "agent": "plugin", "plugin": "web_search:search", "goal": "find", "args": {"query": "cats"}},
    {"id": "s2", "agent": "think", "goal": "summarise", "depends_on": ["s1"]}]}
WRITE_PLAN = {"summary": "Think then save", "steps": [
    {"id": "s1", "agent": "think", "goal": "write exercises"},
    {"id": "s2", "agent": "plugin", "plugin": "learn_vault:save_note", "goal": "save",
     "args": {"path": "exercises/a.md", "content": "{{s1}}"}, "depends_on": ["s1"]}]}
CAPS = [Capability("think", "think", "t"),
        Capability("plugin", "web_search:search", "s", {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]}),
        Capability("plugin", "learn_vault:save_note", "save", {"type": "object", "properties": {"path": {"type": "string"}, "content": {"type": "string"}},
                                                               "required": ["path", "content"]})]


class Ok:
    def __init__(self, value):
        self.value = value


class Err:
    def __init__(self, error):
        self.error = error


def make(monkeypatch, tmp_path, plan=PLAN, llm_reply=None, entry_result=None, json_entries=None):
    p = object.__new__(ma.MainAgentPlugin)
    p._runs, p._active, p._cancel = {}, None, None
    p._store = RunStore(tmp_path / "runs")
    p.logger = logging.getLogger("test")
    p.pushed = []
    p.push_message = lambda **kw: p.pushed.append(kw)

    async def catalogue(mode="work"):
        return CAPS

    async def llm(messages, **kw):
        if "planner" in messages[0]["content"]:
            return json.dumps(plan) if llm_reply is None else llm_reply
        return "SUMMARY TEXT"

    p._catalogue, p._llm = catalogue, llm
    calls = []

    async def call_entry(ref, params=None, timeout=10.0):
        calls.append((ref, params))
        if entry_result is None and ref == "learn_vault:save_note":      # the entry's real result shape (a write must show what it wrote)
            return Ok({"path": (params or {}).get("path"), "mode": "create", "bytes": 12})
        return entry_result if entry_result is not None else Ok({"summary": "cats are animals"})

    async def call_entry_json(ref, params=None, timeout=10.0):
        calls.append((ref, params))
        fn = (json_entries or {}).get(ref)
        return fn(params) if fn else Ok({"data": {}})

    monkeypatch.setattr(ma.MainAgentPlugin, "plugins", types.SimpleNamespace(call_entry=call_entry, call_entry_json=call_entry_json), raising=False)
    monkeypatch.setattr(ma, "Err", Err)
    p.calls = calls
    return p


def drive(p, coro):
    async def go():
        r = await coro
        for _ in range(300):
            if p._active is None:
                break
            await asyncio.sleep(0.02)
        return r
    return asyncio.run(go())


def stored(p, run_id):
    return p._store.load(run_id)


def test_run_task_plans_runs_the_steps_checkpoints_and_speaks_one_combined_answer(monkeypatch, tmp_path):
    p = make(monkeypatch, tmp_path)
    res = drive(p, p.run_task(task="look up cats and summarise"))
    rid = res.value["run_id"]
    assert ("web_search:search", {"query": "cats"}) in p.calls
    assert len(p.pushed) == 1
    msg = p.pushed[0]
    assert msg["ai_behavior"] == "respond" and msg["parts"][0]["text"] == "SUMMARY TEXT" and msg["metadata"]["state"] == "done"
    rec = stored(p, rid)                                               # the checkpoint on disk
    assert rec["state"] == "done" and rec["running"] == [] and rec["results"]["s2"]["outcome"] == "success"
    assert p._active is None and p._cancel is None


def test_a_failing_read_only_step_is_reported_honestly_and_releases_the_lock(monkeypatch, tmp_path):
    p = make(monkeypatch, tmp_path, entry_result=Err("backend down"))
    drive(p, p.run_task(task="look up cats"))
    text = p.pushed[0]["parts"][0]["text"]
    assert "could not finish" in text and p._active is None
    assert list(p._runs.values())[0]["state"] in ("failed", "partial")


def test_a_bad_plan_is_refused_and_nothing_runs(monkeypatch, tmp_path):
    p = make(monkeypatch, tmp_path, llm_reply='{"summary": "x", "steps": [{"id": "s1", "agent": "plugin", "plugin": "evil:run", "goal": "g", "args": {}}]}')
    res = asyncio.run(p.run_task(task="do it"))
    assert type(res).__name__ == "Err" and p.calls == [] and p.pushed == [] and p._active is None and p._store.ids() == []


def test_only_one_run_at_a_time_and_empty_or_unknown_input_is_refused(monkeypatch, tmp_path):
    p = make(monkeypatch, tmp_path)
    p._active = "busy"
    assert type(asyncio.run(p.run_task(task="another"))).__name__ == "Err" and p.calls == []
    p._active = None
    assert type(asyncio.run(p.run_task(task="  "))).__name__ == "Err"
    assert type(asyncio.run(p.plan_task(task=""))).__name__ == "Err"
    assert type(asyncio.run(p.run_task(task="x", mode="root"))).__name__ == "Err"


def test_plan_task_previews_with_side_effect_classes_and_runs_nothing(monkeypatch, tmp_path):
    p = make(monkeypatch, tmp_path, plan=WRITE_PLAN)
    res = asyncio.run(p.plan_task(task="explain and save"))
    effects = {s["id"]: s["effect"] for s in res.value["steps"]}
    assert effects == {"s1": "none", "s2": "non_idempotent"} and p.calls == [] and p.pushed == []


def test_stop_task_sets_the_cancel_flag_only_when_something_runs(monkeypatch, tmp_path):
    p = make(monkeypatch, tmp_path)
    monkeypatch.setattr(ma, "STOP_REPORT_WAIT_S", 0.05)
    assert asyncio.run(p.stop_task()).value["stopped"] is False
    p._active, p._cancel = "r", asyncio.Event()
    got = asyncio.run(p.stop_task()).value                         # requested; nothing ends the fake run, so it is not 'stopped'
    assert got["requested"] is True and got["stopped"] is False and got["still_running"] and p._cancel.is_set()


def test_a_refused_plan_gets_one_corrective_retry_and_a_second_refusal_is_final(monkeypatch, tmp_path):
    p = make(monkeypatch, tmp_path)
    replies = ['{"summary": "x", "steps": [{"id": "s1", "agent": "browser", "goal": "g"}]}', json.dumps(PLAN)]
    seen = []

    async def llm(messages, **kw):
        seen.append(messages)
        return replies.pop(0)

    p._llm = llm
    res = asyncio.run(p.plan_task(task="look it up"))
    assert len(seen) == 2 and "refused" in seen[1][-1]["content"] and "web_search:search" in seen[1][-1]["content"]
    assert [s["id"] for s in res.value["steps"]] == ["s1", "s2"]
    calls = []

    async def bad(messages, **kw):
        calls.append(1)
        return '{"summary": "x", "steps": [{"id": "s1", "agent": "browser", "goal": "g"}]}'

    p._llm = bad
    res = asyncio.run(p.plan_task(task="look it up"))
    assert type(res).__name__ == "Err" and len(calls) == 2


def test_a_dependent_plugin_step_receives_the_earlier_result_in_its_content(monkeypatch, tmp_path):
    p = make(monkeypatch, tmp_path)
    step = Step("s2", "plugin", "save", ref="learn_vault:save_note", args={"path": "exercises/a.md", "content": "# Exercises\n{{s1}}"}, depends_on=["s1"])
    asyncio.run(p._dispatch(step, {"s1": "1. What is chlorophyll?"}))
    assert p.calls[-1] == ("learn_vault:save_note", {"path": "exercises/a.md", "content": "# Exercises\n1. What is chlorophyll?"})


def test_a_write_that_times_out_is_held_not_retried_and_resume_needs_the_user_to_choose_it(monkeypatch, tmp_path):
    p = make(monkeypatch, tmp_path, plan=WRITE_PLAN, entry_result=Err("plugin call timed out"))
    res = drive(p, p.run_task(task="explain and save", mode="learn"))
    rid = res.value["run_id"]
    assert sum(1 for c in p.calls if c[0] == "learn_vault:save_note") == 1          # exactly one attempt: never blindly repeated
    assert stored(p, rid)["state"] == "held" and stored(p, rid)["results"]["s2"]["held"] is True
    assert "Needs your attention" in p.pushed[-1]["parts"][0]["text"] and "did not retry" in p.pushed[-1]["parts"][0]["text"]
    v = asyncio.run(p.run_status(run_id=rid)).value
    assert v["held"] == ["s2"] and v["resumable"] is True and v["steps"]["s2"]["state"] == "unknown"
    # resuming without choosing the held step runs nothing
    before = len(p.calls)
    r = drive(p, p.resume_run(run_id=rid))
    assert "already finished or held" in r.value["message"] and len(p.calls) == before
    # choosing it runs exactly that step again (now the entry works)
    monkeypatch.setattr(ma.MainAgentPlugin, "plugins", types.SimpleNamespace(
        call_entry=lambda ref, params=None, timeout=10.0: _ok(p, ref, params), call_entry_json=None), raising=False)
    r = drive(p, p.resume_run(run_id=rid, rerun=["s2"]))
    assert stored(p, rid)["state"] == "done" and stored(p, rid)["results"]["s1"]["text"] == "SUMMARY TEXT"


async def _ok(p, ref, params):
    p.calls.append((ref, params))
    return Ok({"saved": True})


def test_after_a_crash_the_run_is_interrupted_finished_steps_are_kept_and_nothing_re_runs_by_itself(monkeypatch, tmp_path):
    p = make(monkeypatch, tmp_path, plan=WRITE_PLAN)
    plan = Plan("Think then save", [Step("s1", "think", "write exercises"),
                                    Step("s2", "plugin", "save", ref="learn_vault:save_note", args={"path": "a.md", "content": "{{s1}}"},
                                         depends_on=["s1"], effect="non_idempotent")])
    p._store.save({"run_id": "crashed", "task": "t", "mode": "learn", "summary": "Think then save", "state": "running",
                   "plan": plan_to_dict(plan), "running": ["s2"],
                   "results": {"s1": result_to_dict(StepResult("s1", "success", "the exercises"))}})
    res = asyncio.run(p.startup())
    assert res.value["interrupted_runs"] == ["crashed"]
    assert p.calls == [] and p._active is None                                       # recovered, not re-run
    v = asyncio.run(p.run_status(run_id="crashed")).value
    assert v["state"] == "interrupted" and v["steps"]["s1"]["state"] == "success" and v["steps"]["s2"]["state"] == "unknown" and v["held"] == ["s2"]
    d = asyncio.run(ma.MainAgentPlugin.dashboard(p))
    assert d["current"]["id"] == "crashed" and d["current"]["resumable"] is True


def test_the_waiting_clock_credits_time_spent_waiting_for_an_approval(monkeypatch, tmp_path):
    real_sleep = asyncio.sleep

    async def fast(d, *a, **k):
        await real_sleep(0 if d == 5.0 else d)

    monkeypatch.setattr(asyncio, "sleep", fast)
    states = iter(["running", "running", "done"])
    plan = {"summary": "Open it", "steps": [{"id": "s1", "agent": "jarvis", "goal": "open the downloads folder"}]}
    p = make(monkeypatch, tmp_path, plan=plan, json_entries={
        "jarvis_bridge:delegate_task": lambda a: Ok({"data": {"command_id": "c-1"}}),
        "jarvis_bridge:task_result": lambda a: Ok({"data": {"state": next(states), "message": "JARVIS finished."}}),
        "jarvis_bridge:pending_approvals": lambda a: Ok({"data": {"count": 1}}),
        "jarvis_bridge:jarvis_status": lambda a: Ok({"data": {"ready": True}}),
    })
    p._catalogue = lambda mode="work": _caps_async([Capability("think", "think", "t"), Capability("jarvis", "jarvis", "j")])
    res = drive(p, p.run_task(task="open my downloads folder", mode="code"))
    rec = stored(p, res.value["run_id"])
    assert rec["state"] == "done" and rec["results"]["s1"]["waited"] > 0          # time waiting for the owner was credited


async def _caps_async(caps):
    return caps


def test_an_llm_provider_error_becomes_a_clean_message_without_provider_text(monkeypatch):
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

def test_resume_reruns_unknown_read_only_steps_by_itself_but_never_a_step_with_side_effects(monkeypatch, tmp_path):
    p = make(monkeypatch, tmp_path, plan=WRITE_PLAN)
    plan = Plan("Think then save", [Step("s1", "think", "write exercises"),
                                    Step("s2", "plugin", "save", ref="learn_vault:save_note", args={"path": "a.md", "content": "{{s1}}"},
                                         depends_on=["s1"], effect="non_idempotent")])
    p._store.save({"run_id": "crashed", "task": "t", "mode": "learn", "summary": "s", "state": "running", "plan": plan_to_dict(plan), "running": ["s1"],
                   "results": {}})
    asyncio.run(p.startup())                                          # s1 (reasoning) is now unknown, not held
    assert stored(p, "crashed")["results"]["s1"]["held"] is False
    monkeypatch.setattr(ma.MainAgentPlugin, "plugins", types.SimpleNamespace(
        call_entry=lambda ref, params=None, timeout=10.0: _ok(p, ref, params), call_entry_json=None), raising=False)
    r = drive(p, p.resume_run(run_id="crashed"))                      # no rerun list: the reasoning step is re-run on its own
    rec = stored(p, "crashed")
    assert rec["state"] == "done" and rec["results"]["s1"]["outcome"] == "success" and rec["results"]["s2"]["outcome"] == "success"
    assert [c for c in p.calls if c[0] == "learn_vault:save_note"] == [("learn_vault:save_note", {"path": "a.md", "content": "SUMMARY TEXT"})]


def _fake_llm(monkeypatch, errors, reply="ok"):
    """_llm with a model that raises each error in turn and then answers; returns the call counter and the waits."""
    import utils.config_manager as cm
    import utils.llm_client as lc

    class Cfg:
        def get_model_api_config(self, kind):
            return {"base_url": "https://provider.example/v1", "model": "m", "api_key": "", "provider_type": None}

    calls, waits = [], []

    class Model:
        async def ainvoke(self, messages):
            calls.append(1)
            if len(calls) <= len(errors):
                raise errors[len(calls) - 1]
            return types.SimpleNamespace(content=reply)

    async def fake_create(**kw):
        return Model()

    async def fake_sleep(s):
        waits.append(s)

    monkeypatch.setattr(cm, "get_config_manager", lambda: Cfg())
    monkeypatch.setattr(lc, "create_chat_llm_async", fake_create)
    monkeypatch.setattr(ma.asyncio, "sleep", fake_sleep)
    return calls, waits


class RateLimitError(Exception):
    pass


def test_a_rate_limited_model_call_is_asked_again_and_then_answers(monkeypatch):
    calls, waits = _fake_llm(monkeypatch, [RateLimitError("Error code: 429 - too many requests")] * 2, reply="plan")
    p = object.__new__(ma.MainAgentPlugin)
    assert asyncio.run(p._llm([{"role": "user", "content": "hi"}])) == "plan"
    assert (len(calls), waits) == (3, [6.0, 18.0])


def test_retry_after_is_honoured_and_capped(monkeypatch):
    class Slow(Exception):
        status_code = 429
        response = types.SimpleNamespace(headers={"retry-after": "500"}, status_code=429)
    calls, waits = _fake_llm(monkeypatch, [Slow("busy")], reply="x")
    asyncio.run(object.__new__(ma.MainAgentPlugin)._llm([{"role": "user", "content": "hi"}]))
    assert waits == [30.0]


def test_a_model_that_stays_rate_limited_fails_after_three_calls_without_provider_text(monkeypatch):
    from plugin.plugins.main_agent.executor import StepFailed
    calls, waits = _fake_llm(monkeypatch, [RateLimitError("429 secret-request-id-123")] * 5)
    with pytest.raises(StepFailed) as e:
        asyncio.run(object.__new__(ma.MainAgentPlugin)._llm([{"role": "user", "content": "hi"}]))
    assert len(calls) == 3 and "RateLimitError" in str(e.value) and "secret-request-id" not in str(e.value)


def test_a_wrong_key_or_a_bad_request_is_never_asked_again(monkeypatch):
    from plugin.plugins.main_agent.executor import StepFailed
    for err in (RuntimeError("Error code: 401 unauthorized"), ValueError("Error code: 400 - bad request")):
        calls, waits = _fake_llm(monkeypatch, [err] * 3)
        with pytest.raises(StepFailed):
            asyncio.run(object.__new__(ma.MainAgentPlugin)._llm([{"role": "user", "content": "hi"}]))
        assert (len(calls), waits) == (1, [])


def test_a_plan_failure_message_keeps_its_opening_and_adds_one_plain_hint():
    hint = ma.plan_failure_hint
    assert "busy" in hint("The agent model did not answer (RateLimitError).")
    assert "key" in hint("The agent model refused the request: its API key is missing or wrong.")
    assert "say it again" in hint("None of the available helpers can do that request.")
    assert "wrong format" in hint("The planner's plan was not valid JSON.")
    assert hint("some other reason") == "" and hint("") == ""
