"""speed: thinking off for decisions, prompt-cache friendly messages, parallel independent steps (D59, lever e)."""
import asyncio

import pytest

from jarvis.plugins._shared import speed as S
from jarvis.plugins._shared.speed import Task


def test_decision_body_turns_thinking_off_locally_without_overriding():
    body = {"model": "decider", "messages": []}
    out = S.decision_body(body, local=True)
    assert out["chat_template_kwargs"] == {"enable_thinking": False} and "chat_template_kwargs" not in body
    keep = S.decision_body({"chat_template_kwargs": {"enable_thinking": True, "x": 1}}, local=True)
    assert keep["chat_template_kwargs"] == {"enable_thinking": True, "x": 1}


def test_decision_body_online_only_for_providers_known_to_take_the_switch():
    assert S.decision_body({}, local=False, provider="Groq")["reasoning_effort"] == "low"
    assert "reasoning_effort" not in S.decision_body({}, local=False, provider="dahl")
    assert S.decision_body({"reasoning_effort": "high"}, local=False, provider="groq")["reasoning_effort"] == "high"


def test_prompt_cache_flag():
    assert S.with_prompt_cache({"a": 1}) == {"a": 1, "cache_prompt": True}
    assert S.with_prompt_cache({"cache_prompt": False})["cache_prompt"] is False


def test_static_first_keeps_one_identical_system_message():
    a = S.static_first("PERSONA", ["time: 10:00", "memory: likes tea"], "open notepad")
    b = S.static_first("PERSONA", ["time: 10:01", "memory: likes tea"], "open calculator")
    assert a[0] == b[0] and S.prefix_hash(a) == S.prefix_hash(b)
    assert a[1]["content"].endswith("open notepad") and "time: 10:00" in a[1]["content"]


def test_prefix_watch_finds_a_timestamp_in_the_static_prompt():
    w = S.PrefixWatch()
    reqs = [[{"role": "system", "content": f"You are JARVIS.\nNow: 10:0{i}\nRules."}, {"role": "user", "content": "x"}] for i in range(3)]
    assert [w.record(r) for r in reqs] == [True, False, False]
    assert w.changes == 2 and w.churn == 1.0 and w.first_difference == "Now: 10:01"
    ok = S.PrefixWatch()
    stable = S.static_first("You are JARVIS.", ["Now: 1"], "a"), S.static_first("You are JARVIS.", ["Now: 2"], "b")
    assert ok.record(stable[0]) and ok.record(stable[1]) and ok.churn == 0.0


def run(coro):
    return asyncio.run(coro)


def test_independent_tasks_overlap_but_pc_tasks_take_turns():
    log, active, peak_pc = [], {"pc": 0, "all": 0}, {"pc": 0, "all": 0}

    async def work(t, results):
        active["all"] += 1
        peak_pc["all"] = max(peak_pc["all"], active["all"])
        if t.pc:
            active["pc"] += 1
            peak_pc["pc"] = max(peak_pc["pc"], active["pc"])
        log.append(("start", t.id))
        await asyncio.sleep(0.02)
        log.append(("end", t.id))
        active["all"] -= 1
        if t.pc:
            active["pc"] -= 1
        return t.id.upper()
    tasks = [Task("open_calc", pc=True), Task("weather"), Task("open_notepad", pc=True), Task("answer", deps=("weather", "open_calc"))]
    res = run(S.run_dag(tasks, work))
    assert res == {"open_calc": "OPEN_CALC", "weather": "WEATHER", "open_notepad": "OPEN_NOTEPAD", "answer": "ANSWER"}
    assert peak_pc["pc"] == 1                                       # never two UI tasks at once
    assert peak_pc["all"] >= 2                                      # the lookup overlapped a UI task
    assert log.index(("start", "answer")) > log.index(("end", "weather")) and log.index(("start", "answer")) > log.index(("end", "open_calc"))


def test_a_failed_task_fails_only_its_dependents():
    async def work(t, results):
        if t.id == "weather":
            raise ValueError("no network")
        return "ok"
    res = run(S.run_dag([Task("weather"), Task("calc", pc=True), Task("say", deps=("weather",)), Task("say2", deps=("calc",))], work))
    assert isinstance(res["weather"], ValueError) and res["calc"] == "ok" and res["say2"] == "ok"
    assert isinstance(res["say"], RuntimeError) and "weather" in str(res["say"])


def test_bad_graphs_are_refused():
    async def work(t, r):
        return 1
    with pytest.raises(ValueError):
        run(S.run_dag([Task("a"), Task("a")], work))
    with pytest.raises(ValueError):
        run(S.run_dag([Task("a", deps=("zzz",))], work))
    assert S.cycle_free([Task("a"), Task("b", deps=("a",))]) and not S.cycle_free([Task("a", deps=("b",)), Task("b", deps=("a",))])
