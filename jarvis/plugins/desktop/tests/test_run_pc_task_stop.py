"""stop_pc_task reaches a single task's research step (2026-10-18). run_pc_task built its step runners without the owner's
stop (abort), so a research step waiting on the research plugin kept waiting up to 960 s after the owner said stop, while
a mission's identical step stopped within a second. The coordinator here is a stand-in that runs one research step through
the REAL step runners (DesktopPlugin._agents); the research plugin is a coroutine that never answers. No network."""
import asyncio
import threading
import time

from jarvis.plugins.desktop import DesktopPlugin
from jarvis.plugins.desktop import coordinator as C


def _loop_that_closes_when_stopped():
    loop = asyncio.new_event_loop()

    def run():
        loop.run_forever()
        loop.close()                       # a waiting call then ends (call_on_loop sees the closed loop), old code too
    threading.Thread(target=run, daemon=True).start()
    return loop


class _Host:
    """Just enough of the plugin object for run_pc_task, stop_pc_task and the real _agents."""

    def __init__(self, loop, research):
        self._cmd_loop, self.calls, host = loop, [], self

        class Plugins:
            async def call_entry_json(self, entry, args, timeout=0):
                host.calls.append(entry)
                return await research()
        self.plugins = Plugins()
        for name in ("_agents", "_stop_pc_tasks"):
            setattr(self, name, getattr(DesktopPlugin, name).__get__(self))

    async def _missions_ready(self):
        return True


def test_stop_pc_task_ends_a_single_tasks_research_wait(monkeypatch):
    gone, seen = threading.Event(), {}

    async def research():                  # the research plugin: answers only after an hour
        try:
            await asyncio.sleep(3600)
        except asyncio.CancelledError:
            gone.set()
            raise

    class OneResearchStep:                 # stand-in for the coordinator: one research step through the real runner
        def __init__(self, agents, profiles, cancelled=lambda: False, **kw):
            self.agents, self.cancelled = agents, cancelled

        def run(self, request):
            try:
                out = self.agents["research"]({"tool": "research", "query": "home solar panels", "deliverable": "presentation"})
                seen["step"] = out
            except Exception as e:         # the real coordinator records a stopped step the same way: by its kind
                seen["step"] = type(e).__name__
            return {"id": request, "status": "stopped" if self.cancelled() else "failed", "steps": [], "error": str(seen["step"])}
    monkeypatch.setattr(C, "Coordinator", OneResearchStep)
    loop = _loop_that_closes_when_stopped()
    h = _Host(loop, research)

    async def main():
        task = asyncio.ensure_future(DesktopPlugin.run_pc_task(h, request="research solar panels and make slides"))
        deadline = time.time() + 10
        while not h.calls and time.time() < deadline:
            await asyncio.sleep(0.02)
        assert h.calls == ["research:research_topic"]
        t0 = time.time()
        await DesktopPlugin.stop_pc_task(h)                           # the owner says stop
        try:
            res = await asyncio.wait_for(asyncio.shield(task), 6)
        except asyncio.TimeoutError:
            res = None
        took = time.time() - t0
        if res is None:                                               # old code: still waiting; end the wait for teardown
            loop.call_soon_threadsafe(loop.stop)
            try:
                await asyncio.wait_for(task, 10)
            except Exception:
                pass
        return res, took
    try:
        res, took = asyncio.run(main())
    finally:
        try:
            loop.call_soon_threadsafe(loop.stop)
        except RuntimeError:                                          # already stopped and closed
            pass
    assert res is not None, "stop_pc_task did not reach the research step: it kept waiting on the research plugin"
    assert took < 5 and seen["step"] == "StepCancelled" and gone.wait(3), (took, seen)
    assert res.value["status"] == "stopped"
