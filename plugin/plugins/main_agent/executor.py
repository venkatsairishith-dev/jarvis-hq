"""Runs a validated plan: waves of independent steps in parallel, bounded time, failure isolation, cancellation.

The executor knows nothing about what a step *does*; the plugin passes in `dispatch(step, inputs)`. That keeps this
logic testable without a model, a browser or the network.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from .planner import Plan, Step

RESULT_CHARS = 4000


@dataclass
class StepResult:
    id: str
    ok: bool
    text: str = ""
    error: str = ""
    skipped: bool = False
    seconds: float = 0.0


Dispatch = Callable[[Step, dict[str, str]], Awaitable[str]]
Progress = Callable[[Step, StepResult], None]


class StepFailed(Exception):
    """Raised by a dispatcher with a message that is safe to show to the user."""


async def run_plan(plan: Plan, dispatch: Dispatch, *, parallel: int = 3, step_timeout: float = 240.0,
                   total_timeout: float = 600.0, cancel: asyncio.Event | None = None,
                   on_progress: Progress | None = None) -> list[StepResult]:
    results: dict[str, StepResult] = {}
    pending = list(plan.steps)
    deadline = time.monotonic() + total_timeout
    gate = asyncio.Semaphore(max(1, parallel))

    def emit(step: Step, res: StepResult) -> None:
        results[step.id] = res
        if on_progress:
            try:
                on_progress(step, res)
            except Exception:              # a reporting problem must never stop the work
                pass

    async def one(step: Step) -> None:
        inputs = {d: results[d].text for d in step.depends_on}
        started = time.monotonic()
        async with gate:
            remaining = deadline - time.monotonic()
            if cancel is not None and cancel.is_set():
                return emit(step, StepResult(step.id, False, error="cancelled", skipped=True))
            if remaining <= 0:
                return emit(step, StepResult(step.id, False, error="the overall time limit was reached", skipped=True))
            try:
                text = await asyncio.wait_for(dispatch(step, inputs), timeout=min(step_timeout, remaining))
                emit(step, StepResult(step.id, True, str(text or "")[:RESULT_CHARS], seconds=time.monotonic() - started))
            except asyncio.TimeoutError:
                emit(step, StepResult(step.id, False, error="timed out", seconds=time.monotonic() - started))
            except StepFailed as exc:
                emit(step, StepResult(step.id, False, error=str(exc)[:300], seconds=time.monotonic() - started))
            except asyncio.CancelledError:
                raise
            except Exception as exc:       # keep the class only: provider errors can carry URLs or keys
                emit(step, StepResult(step.id, False, error=f"{type(exc).__name__}", seconds=time.monotonic() - started))

    while pending:
        ready: list[Step] = []
        skipped_any = False
        for step in list(pending):
            deps = [results.get(d) for d in step.depends_on]
            if any(r is None for r in deps):
                continue
            if any(not r.ok for r in deps):
                pending.remove(step)
                emit(step, StepResult(step.id, False, error="a step it needed did not finish", skipped=True))
                skipped_any = True
                continue
            ready.append(step)
        if not ready:
            if skipped_any:
                continue                   # a skipped step may be what its own dependents were waiting on
            for s in pending:              # unreachable for validated plans; never loop forever
                emit(s, StepResult(s.id, False, error="could not be scheduled", skipped=True))
            break
        for s in ready:
            pending.remove(s)
        await asyncio.gather(*(one(s) for s in ready))
    return [results[s.id] for s in plan.steps if s.id in results]


def final_text(plan: Plan, results: list[StepResult]) -> str:
    """The answer to hand back: the output of the steps nothing else depends on, or an honest failure summary."""
    by_id = {r.id: r for r in results}
    needed = {d for s in plan.steps for d in s.depends_on}
    leaves = [s for s in plan.steps if s.id not in needed]
    parts = [by_id[s.id].text for s in leaves if s.id in by_id and by_id[s.id].ok and by_id[s.id].text.strip()]
    failed = [by_id[s.id] for s in plan.steps if s.id in by_id and not by_id[s.id].ok]
    if parts and not failed:
        return "\n\n".join(parts)
    if parts:
        names = ", ".join(f"{r.id} ({r.error or 'failed'})" for r in failed)
        return "\n\n".join(parts) + f"\n\nNot everything worked: {names}."
    names = ", ".join(f"{r.id} ({r.error or 'failed'})" for r in failed)
    return f"I could not finish this: {names or 'nothing produced a result'}."
