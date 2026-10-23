"""A step that changes something is never retried unless the owner chooses it (tool inventory pass G, 2026-10-19): provider outage,
timeout, partial failure, malformed result, duplicate delivery. Each row runs the real plugin class against a fake plugin runtime and
counts how many times the entry was called."""
from __future__ import annotations

import asyncio

import pytest
from test_payload_outcome import PC, Err, Ok, make, run_with, settle  # noqa: E402

from jarvis.plugins.main_agent.planner import Capability

CYCLE: list = []
CYCLE.append(CYCLE)


class Hostile:
    def __str__(self):
        raise RuntimeError("no")


def raising(exc):
    async def entry(ref, params):
        raise exc
    return entry


ROWS = [
    ("provider outage (exception)", lambda: raising(ConnectionError("down CANARY-7f3a-x-SYNTH")), "held"),
    ("timeout reported as an error", lambda: (lambda ref, params: _const(Err("request timed out"))), "held"),
    ("definite refusal", lambda: (lambda ref, params: _const(Err("denied by policy"))), "failed"),
    ("partial failure", lambda: (lambda ref, params: _const(Ok({"status": "partial", "steps": [{"passed": True}, {"passed": False}]}))), "held"),
    ("failure after a step passed", lambda: (lambda ref, params: _const(Ok({"status": "failed", "steps": [{"passed": True}]}))), "held"),
    ("empty result", lambda: (lambda ref, params: _const(Ok(None))), "held"),
    ("empty dict", lambda: (lambda ref, params: _const(Ok({}))), "held"),
    ("bytes result", lambda: (lambda ref, params: _const(Ok(b"\x00\xff"))), "held"),
    ("object whose __str__ raises", lambda: (lambda ref, params: _const(Ok(Hostile()))), "held"),
    ("cyclic result", lambda: (lambda ref, params: _const(Ok(CYCLE))), "held"),
    ("status of a kind nobody registered", lambda: (lambda ref, params: _const(Ok({"status": "banana"}))), "held"),
]


async def _const(v):
    return v


@pytest.mark.parametrize("name,entry_factory,state", ROWS, ids=[r[0] for r in ROWS])
def test_a_writer_whose_result_is_bad_is_called_once_and_nothing_follows_it(monkeypatch, tmp_path, name, entry_factory, state):
    calls = []

    async def go():
        inner = entry_factory()

        async def entry(ref, params):
            calls.append(ref)
            r = inner(ref, params)
            return await r if asyncio.iscoroutine(r) else r
        p = make(monkeypatch, tmp_path, PC, entry)
        rid = (await p.run_task(task="open notepad on the PC")).value["run_id"]
        await asyncio.wait_for(settle(p), 10)
        return p, p._store.load(rid)
    p, rec = asyncio.run(go())
    assert calls == ["desktop:run_pc_task"], f"{name}: the writer was called {len(calls)} times"
    assert rec["state"] == state, (name, rec["state"])
    assert rec["results"]["s2"]["outcome"] == "not_executed"          # the think step after it never ran
    assert "CANARY-7f3a" not in str(rec) and "CANARY-7f3a" not in str(p.pushed)
    if state == "held":
        assert "could not confirm" in p.pushed[-1]["parts"][0]["text"] or "Needs your attention" in p.pushed[-1]["parts"][0]["text"]


def test_a_reader_that_fails_once_is_retried_once_and_a_reader_that_keeps_failing_gives_up(monkeypatch, tmp_path):
    plan = {"summary": "s", "steps": [{"id": "s1", "agent": "plugin", "plugin": "web_search:search", "goal": "g", "args": {"query": "q"}}]}
    for fail_times, want_calls, want_state in ((1, 2, "done"), (5, 2, "failed")):
        calls = []

        async def entry(ref, params):
            calls.append(ref)
            if len(calls) <= fail_times:
                raise ConnectionError("blip")
            return Ok({"results": [{"title": "t"}]})

        async def go():
            p = make(monkeypatch, tmp_path / str(fail_times), plan, entry)

            async def catalogue(mode="work"):
                return [Capability("think", "think", "t"), Capability("plugin", "web_search:search", "s", {"type": "object", "properties": {"query": {"type": "string"}}})]
            p._catalogue = catalogue
            import jarvis.plugins.main_agent.executor as ex
            monkeypatch.setattr(ex.random, "uniform", lambda a, b: 0.0)
            rid = (await p.run_task(task="look it up")).value["run_id"]
            await asyncio.wait_for(settle(p), 10)
            return p._store.load(rid)
        rec = asyncio.run(go())
        assert len(calls) == want_calls and rec["state"] == want_state, (fail_times, calls, rec["state"])


def test_duplicate_delivery_of_the_same_task_or_resume_runs_the_writer_once(monkeypatch, tmp_path):
    calls = []
    gate = {}

    async def go():
        async def entry(ref, params):
            calls.append(ref)
            await gate["open"].wait()
            return Ok({"status": "completed", "steps": [{"passed": True}]})
        p = make(monkeypatch, tmp_path, PC, entry)
        gate["open"] = asyncio.Event()
        first = await p.run_task(task="open notepad on the PC")
        second = await p.run_task(task="open notepad on the PC")             # the same request delivered again while it runs
        third = await p.resume_run(run_id=first.value["run_id"])              # and a resume of the run that is running
        gate["open"].set()
        await asyncio.wait_for(settle(p), 10)
        return first, second, third
    first, second, third = asyncio.run(go())
    assert calls == ["desktop:run_pc_task"]
    assert getattr(second, "value", None) and second.value.get("state") in ("busy", "failed") or hasattr(second, "error")
    assert getattr(third, "value", {}).get("state") in ("busy", "failed", None) or hasattr(third, "error")


def test_resuming_a_held_run_does_not_rerun_the_held_writer_unless_asked(monkeypatch, tmp_path):
    calls = []

    async def go():
        async def entry(ref, params):
            calls.append(ref)
            raise ConnectionError("down")
        p = make(monkeypatch, tmp_path, PC, entry)
        rid = (await p.run_task(task="open notepad on the PC")).value["run_id"]
        await asyncio.wait_for(settle(p), 10)
        assert p._store.load(rid)["state"] == "held"
        await p.resume_run(run_id=rid)                                       # no rerun list: the held step stays held
        await asyncio.wait_for(settle(p), 10)
        return p._store.load(rid)
    rec = asyncio.run(go())
    assert calls == ["desktop:run_pc_task"], calls
    assert rec["state"] == "held"


def test_duplicate_delivery_answers_are_busy_not_a_second_start(monkeypatch, tmp_path):
    out = {}

    async def go():
        gate = asyncio.Event()

        async def entry(ref, params):
            await gate.wait()
            return Ok({"status": "completed", "steps": [{"passed": True}]})
        p = make(monkeypatch, tmp_path, PC, entry)
        first = await p.run_task(task="open notepad on the PC")
        out["second"] = await p.run_task(task="open notepad on the PC")
        out["third"] = await p.resume_run(run_id=first.value["run_id"])
        gate.set()
        await asyncio.wait_for(settle(p), 10)
    asyncio.run(go())
    for k in ("second", "third"):
        shown = repr(getattr(out[k], "value", None) or getattr(out[k], "error", out[k]))
        assert "still working" in shown or "busy" in shown.lower(), (k, shown)


def test_a_successful_result_holding_an_unreadable_object_still_reaches_the_run_without_an_exception(monkeypatch, tmp_path):
    value = {"path": "C:\\out\\x.md", "extra": Hostile(), "again": CYCLE}
    p, rec = run_with(monkeypatch, tmp_path, value, plan={"summary": "s", "steps": [
        {"id": "s1", "agent": "plugin", "plugin": "desktop:run_pc_task", "goal": "g", "args": {"request": "r"}}]})
    assert rec["results"]["s1"]["outcome"] in ("success", "unknown", "failure")       # decided by the payload rules, never an unhandled error
    assert rec["state"] in ("done", "held", "failed")
