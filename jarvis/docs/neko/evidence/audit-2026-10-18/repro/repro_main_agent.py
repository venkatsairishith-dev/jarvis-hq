"""Audit repro (read-only): two run_task calls overlapping during planning both start; stop then loses the first."""
import asyncio, json, logging, sys, tempfile
from pathlib import Path
sys.path.insert(0, "/src")
from plugin.plugins import main_agent as ma
from plugin.plugins.main_agent.store import RunStore
from plugin.plugins.main_agent.planner import Capability

PLAN = {"summary": "Think", "steps": [{"id": "s1", "agent": "think", "goal": "write"}]}

def plugin(gate):
    p = object.__new__(ma.MainAgentPlugin)
    p._runs, p._active, p._cancel = {}, None, None
    p._store = RunStore(Path(tempfile.mkdtemp()) / "runs")
    p.logger = logging.getLogger("audit"); p.pushed = []
    p.push_message = lambda **kw: p.pushed.append(kw)
    async def catalogue(mode="work"): return [Capability("think", "think", "t")]
    async def llm(messages, **kw):
        await asyncio.sleep(0.2)                     # planning takes time (Dahl: 10-60 s)
        return json.dumps(PLAN)
    async def dispatch(step, inputs, ctx=None):
        await gate.wait(); return "ok"
    p._catalogue, p._llm, p._dispatch = catalogue, llm, dispatch
    return p

async def go():
    gate = asyncio.Event()
    p = plugin(gate)
    a, b = await asyncio.gather(p.run_task(task="voice task"), p.run_task(task="typed task"))
    print("run_task #1:", type(a).__name__, getattr(a, "value", getattr(a, "error", None)))
    print("run_task #2:", type(b).__name__, getattr(b, "value", getattr(b, "error", None)))
    running = [rid for rid, r in p._runs.items() if r.get("state") == "running"]
    print("runs in state 'running' at once:", len(running), "| _active =", p._active)
    s = await p.stop_task(); print("stop_task:", s.value)
    gate.set(); await asyncio.sleep(0.3)
    print("final states:", {rid: r["state"] for rid, r in p._runs.items()})

asyncio.run(go())
