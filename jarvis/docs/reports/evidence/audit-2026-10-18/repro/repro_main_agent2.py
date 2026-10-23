"""Audit repro 2: after the first of two overlapping runs ends, the main agent forgets the second is still running."""
import asyncio, json, logging, sys, tempfile
from pathlib import Path
sys.path.insert(0, "/src")
from plugin.plugins import main_agent as ma
from plugin.plugins.main_agent.store import RunStore
from plugin.plugins.main_agent.planner import Capability

async def go():
    gates = {"voice task": asyncio.Event(), "typed task": asyncio.Event()}
    p = object.__new__(ma.MainAgentPlugin)
    p._runs, p._active, p._cancel = {}, None, None
    p._store = RunStore(Path(tempfile.mkdtemp()) / "runs")
    p.logger = logging.getLogger("audit"); p.push_message = lambda **kw: None
    async def catalogue(mode="work"): return [Capability("think", "think", "t")]
    async def llm(messages, **kw):
        await asyncio.sleep(0.2); return json.dumps({"summary": "Think", "steps": [{"id": "s1", "agent": "think", "goal": "g"}]})
    async def dispatch(step, inputs, ctx=None):
        task = next(r["task"] for r in p._runs.values() if r["state"] == "running" and r["running"] == ["s1"] and not r.get("_seen") and not r.__setitem__("_seen", 1))
        await gates[task].wait(); return "ok"
    p._catalogue, p._llm, p._dispatch = catalogue, llm, dispatch
    await asyncio.gather(p.run_task(task="voice task"), p.run_task(task="typed task"))
    await asyncio.sleep(0.1)
    gates["voice task"].set(); await asyncio.sleep(0.3)
    print("states:", {r["task"]: r["state"] for r in p._runs.values()}, "| _active:", p._active)
    print("stop_task now:", (await p.stop_task()).value)
    third = await p.run_task(task="third task")
    print("a third run_task while 'typed task' still runs:", type(third).__name__)
    for g in gates.values(): g.set()
    await asyncio.sleep(0.3)
asyncio.run(go())
