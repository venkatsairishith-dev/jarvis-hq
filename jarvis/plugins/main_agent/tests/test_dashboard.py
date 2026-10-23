"""The data behind the Main Agent panel."""

from __future__ import annotations

import asyncio

from jarvis.plugins import main_agent as ma
from jarvis.plugins.main_agent.planner import Plan, Step
from jarvis.plugins.main_agent.store import RunStore, plan_to_dict


def make(tmp_path):
    p = object.__new__(ma.MainAgentPlugin)
    p._runs, p._active, p._cancel = {}, None, None
    p._store = RunStore(tmp_path)
    return p


def record(run_id, state="done", mode="work", results=None, running=None):
    plan = Plan("sum", [Step("s1", "plugin", "find", ref="web_search:search"), Step("s2", "think", "summarise", depends_on=["s1"])])
    return {"run_id": run_id, "task": "t", "mode": mode, "summary": f"s-{run_id}", "state": state, "plan": plan_to_dict(plan),
            "results": results or {}, "running": running or []}


def test_empty_dashboard(tmp_path):
    d = asyncio.run(ma.MainAgentPlugin.dashboard(make(tmp_path)))
    assert d == {"active": False, "current": None, "history": []}


def test_dashboard_shows_the_latest_run_with_outcomes_and_a_short_history(tmp_path):
    p = make(tmp_path)
    for i in range(1, 8):
        p._runs[f"r{i}"] = record(f"r{i}")
    p._runs["r8"] = record("r8", state="running", running=["s2"], results={
        "s1": {"id": "s1", "outcome": "unknown", "error": "timed out", "attempts": 2, "held": True, "waited": 4.0}})
    p._active = "r8"
    d = asyncio.run(ma.MainAgentPlugin.dashboard(p))
    assert d["active"] is True and d["current"]["id"] == "r8" and d["current"]["state"] == "running"
    steps = {s["id"]: s for s in d["current"]["steps"]}
    assert steps["s1"]["state"] == "unknown" and steps["s1"]["held"] is True and steps["s1"]["attempts"] == 2
    assert steps["s2"]["state"] == "running" and d["current"]["held"] == ["s1"] and d["current"]["waited_s"] == 4.0
    assert [h["id"] for h in d["history"]] == ["r7", "r6", "r5", "r4", "r3"]        # newest first, five at most
