"""The data behind the Main Agent panel."""

from __future__ import annotations

import asyncio

from plugin.plugins import main_agent as ma


def make():
    p = object.__new__(ma.MainAgentPlugin)
    p._runs, p._active, p._cancel = {}, None, None
    return p


def test_empty_dashboard():
    d = asyncio.run(ma.MainAgentPlugin.dashboard(make()))
    assert d == {"active": False, "current": None, "history": []}


def test_dashboard_shows_the_latest_run_with_each_step_and_a_short_history():
    p = make()
    for i in range(1, 8):
        p._runs[f"r{i}"] = {"task": f"t{i}", "summary": f"s{i}", "state": "done", "steps": {"s1": {"agent": "think", "goal": "g", "state": "done"}}}
    p._runs["r8"] = {"task": "now", "summary": "Look up then summarise", "state": "running",
                     "steps": {"s1": {"agent": "web_search:search", "goal": "find", "state": "done"},
                               "s2": {"agent": "think", "goal": "summarise", "state": "failed", "error": "timed out"}}}
    p._active = "r8"
    d = asyncio.run(ma.MainAgentPlugin.dashboard(p))
    assert d["active"] is True and d["current"]["id"] == "r8" and d["current"]["state"] == "running"
    assert [s["id"] for s in d["current"]["steps"]] == ["s1", "s2"] and d["current"]["steps"][1]["error"] == "timed out"
    assert [h["id"] for h in d["history"]] == ["r7", "r6", "r5", "r4", "r3"]        # newest first, five at most, not the current run
