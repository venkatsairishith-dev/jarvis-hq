"""Runs a validated plan: waves of independent steps in parallel, bounded time, failure isolation, cancellation.

Robustness rules (design ideas from Cyrene-Agent's harness, MIT; written fresh for this stack):
  * Every step ends in one of four outcomes: success, failure (definitely did not work), unknown (it may or may not have
    happened) or not_executed (it never started).
  * An unknown result of a step with side effects (`effect == "non_idempotent"`) is HELD: it is never retried and nothing new
    starts after it, so a duplicated send, write or launch cannot happen behind the user's back.
  * Steps without side effects are retried once with a short jittered pause.
  * Two clocks: time a step spends waiting for the user (an approval) is credited with `ctx.add_waiting()` and does not count
    against the step or overall time limits.
  * State can be restored: `prior` results are kept as they are, so a run can resume after a crash.

The executor knows nothing about what a step *does*; the plugin passes in `dispatch(step, inputs[, ctx])`. That keeps this logic
testable without a model, a browser or the network.
"""

from __future__ import annotations

import asyncio
import inspect
import random
import time
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from plugin.plugins._shared.untrusted import clean_untrusted

from .planner import Plan, Step

RESULT_CHARS = 4000
OUTCOMES = ("success", "failure", "unknown", "not_executed")


@dataclass
class StepResult:
    id: str
    outcome: str = "success"
    text: str = ""
    error: str = ""
    seconds: float = 0.0
    attempts: int = 1
    held: bool = False          # unknown result of a step with side effects: not retried, later steps held back
    waited: float = 0.0         # seconds spent waiting for the user, not counted against the time limits
    screen: bool = False        # the text holds screen- or disk-derived text (OCR, a page, a window): every sink re-checks it
    untrusted: bool = False     # derived from outside the owner's own data (a page, the screen, another agent) or from a result that was:
                                # a plugin step that acts on it through its arguments waits for the owner (see MainAgentPlugin._untrusted_sink_problem)

    def __post_init__(self) -> None:
        # What a tool returned is DATA: invisible/bidi characters out and instruction-shaped constructs (a fake {{s1}}, a role marker,
        # tool-call tags, HTML, markdown images) defused, once, here - so the run file, a dependent step's input, a think step's
        # prompt, the final answer and the chat push never see them (tool inventory pass, 2026-10-19). Idempotent.
        self.text = clean_untrusted(self.text)
        self.error = clean_untrusted(self.error)

    @property
    def ok(self) -> bool:
        return self.outcome == "success"

    @property
    def skipped(self) -> bool:
        return self.outcome == "not_executed"


class StepFailed(Exception):
    """A step definitely did not work. The message is safe to show to the user."""


class StepUnknown(StepFailed):
    """A step may or may not have had its effect (it timed out after being sent, or is still going)."""


class StepNeedsApproval(StepUnknown):
    """The helper accepted the step but is waiting for the owner (an approval, an answer): it has not run yet. Held like an
    unknown result of a step with side effects (never retried, nothing new starts after it, so an approval is not asked twice),
    but said as what it is."""


NEEDS_YOU = "needs your approval or answer"      # starts the error text of a step waiting for the owner (final_text words it)

STOP_GRACE_S = 1.0      # after Stop, how long a running step may take to report its real outcome


class StepCancelled(Exception):
    """The run was stopped. `started` says whether the step's dispatcher had begun (it may then have had an effect)."""

    def __init__(self, started: bool):
        super().__init__("stopped while running" if started else "stopped before it started")
        self.started = started


class StepContext:
    """Handed to a dispatcher so it can report time spent waiting for the user, and see whether the run was stopped.

    `cancel` is the run's stop event (None when the run cannot be stopped). A dispatcher that is cancelled because the run
    was stopped can ask the helper to stop too, and say what it did in `cancel_note` (shown with the step's outcome).
    """

    def __init__(self, cancel: asyncio.Event | None = None) -> None:
        self.waited = 0.0
        self.cancel = cancel
        self.cancel_note = ""
        self.timed_out = False          # the executor ran out of the step's time and is cancelling the dispatcher (not the owner's Stop)
        self.screen = False                    # set by the dispatcher when what it returns holds screen-derived text
        self.screen_inputs: list[str] = []     # ids of the steps this step depends on whose result holds screen-derived text
        self.untrusted = False                 # set by the dispatcher when this step's own source is outside the owner's data
        self.untrusted_inputs: list[str] = []  # ids of the steps this step depends on whose result is untrusted

    @property
    def stopped(self) -> bool:
        return self.cancel is not None and self.cancel.is_set()

    def add_waiting(self, seconds: float) -> None:
        self.waited += max(0.0, float(seconds))


Dispatch = Callable[..., Awaitable[str]]
Progress = Callable[[Step, StepResult], None]


async def _call(dispatch: Dispatch, takes_ctx: bool, step: Step, inputs: dict[str, str], ctx: StepContext,
                step_timeout: float, hard_limit: Callable[[], float]) -> str:
    cancel = ctx.cancel
    began = [False]

    async def guarded() -> str:
        # Checked when the dispatcher actually begins, not when it was scheduled: a Stop that lands in between must not
        # let the step start.
        if cancel is not None and cancel.is_set():
            raise StepCancelled(False)
        began[0] = True
        return await (dispatch(step, inputs, ctx) if takes_ctx else dispatch(step, inputs))

    task = asyncio.ensure_future(guarded())
    stop = asyncio.ensure_future(cancel.wait()) if cancel is not None else None
    start = time.monotonic()
    try:
        while True:
            limit = min(start + step_timeout + ctx.waited, hard_limit() + ctx.waited)
            remaining = limit - time.monotonic()
            if remaining <= 0:
                ctx.timed_out = True              # the dispatcher is cancelled next: it may ask its helper to stop, as on a Stop
                raise asyncio.TimeoutError
            waiting = {task} if stop is None else {task, stop}
            done, _ = await asyncio.wait(waiting, timeout=min(remaining, 0.5), return_when=asyncio.FIRST_COMPLETED)
            if task in done:
                return task.result()
            if stop is not None and stop in done:
                # The run was stopped. A step that ends on its own within a moment reports what really happened (it
                # did finish); otherwise it is interrupted below, which also asks its helper to stop.
                late, _ = await asyncio.wait({task}, timeout=STOP_GRACE_S)
                if task in late:
                    return task.result()
                raise StepCancelled(began[0])
    finally:
        if stop is not None and not stop.done():
            stop.cancel()
        if not task.done():
            task.cancel()                         # the dispatcher sees CancelledError and can ask its helper to stop
            try:
                await task
            except BaseException:     # the cancelled dispatcher's own exception is not interesting
                pass


async def run_plan(plan: Plan, dispatch: Dispatch, *, parallel: int = 3, step_timeout: float = 240.0,
                   total_timeout: float = 600.0, cancel: asyncio.Event | None = None, on_progress: Progress | None = None,
                   on_start: Callable[[Step], None] | None = None, retries: int = 1,
                   prior: dict[str, StepResult] | None = None, backoff: tuple[float, float] = (0.3, 1.2)) -> list[StepResult]:
    results: dict[str, StepResult] = dict(prior or {})
    pending = [s for s in plan.steps if s.id not in results]
    started_at = time.monotonic()
    waited_total = [0.0]
    halt: list[str] = [f"{r.id} may or may not have happened" for r in results.values() if r.held]
    gate = asyncio.Semaphore(max(1, parallel))
    try:
        takes_ctx = len(inspect.signature(dispatch).parameters) >= 3
    except (TypeError, ValueError):
        takes_ctx = False

    def hard_limit() -> float:
        return started_at + total_timeout + waited_total[0]

    def emit(step: Step, res: StepResult) -> None:
        results[step.id] = res
        if on_progress:
            try:
                on_progress(step, res)
            except Exception:              # a reporting problem must never stop the work
                pass

    def not_executed(step: Step, why: str) -> None:
        emit(step, StepResult(step.id, "not_executed", error=why, attempts=0))

    async def one(step: Step) -> None:
        inputs = {d: results[d].text for d in step.depends_on}
        async with gate:
            if cancel is not None and cancel.is_set():
                return not_executed(step, "cancelled")
            if halt:
                return not_executed(step, f"held back: {halt[0]}")
            if time.monotonic() >= hard_limit():
                return not_executed(step, "the overall time limit was reached")
            if on_start:
                try:
                    on_start(step)
                except Exception:
                    pass
            attempts, began = 0, time.monotonic()
            while True:
                attempts += 1
                waiting = False
                ctx = StepContext(cancel)
                ctx.screen_inputs = [d for d in step.depends_on if d in results and results[d].screen]
                ctx.untrusted_inputs = [d for d in step.depends_on if d in results and results[d].untrusted]
                t0 = time.monotonic()
                try:
                    text = await _call(dispatch, takes_ctx, step, inputs, ctx, step_timeout, hard_limit)
                    waited_total[0] += ctx.waited
                    return emit(step, StepResult(step.id, "success", str(text or "")[:RESULT_CHARS],
                                                 seconds=time.monotonic() - began, attempts=attempts, waited=ctx.waited,
                                                 screen=bool(ctx.screen),
                                                 untrusted=bool(ctx.untrusted or ctx.untrusted_inputs or ctx.screen)))
                except StepCancelled as exc:
                    waited_total[0] += ctx.waited
                    if not exc.started:
                        return not_executed(step, "cancelled")
                    # Stopped part-way: a step that may change something may or may not have done it, so it is held
                    # (never re-run without the owner choosing it); a read or reasoning step simply did not finish.
                    held = step.effect == "non_idempotent"
                    note = f"; {ctx.cancel_note}" if ctx.cancel_note else ""
                    return emit(step, StepResult(step.id, "unknown" if held else "failure",
                                                 error=(f"stopped while running{note}"
                                                        + ("; whether it had any effect is not known" if held else ""))[:300],
                                                 seconds=time.monotonic() - began, attempts=attempts, held=held, waited=ctx.waited))
                except asyncio.TimeoutError:
                    kind, error = "unknown", "timed out; whether it had any effect is not known"
                    if ctx.cancel_note:                  # what the helper was asked to do about it (and whether it ended)
                        error = f"{error}; {ctx.cancel_note}"[:300]
                except StepUnknown as exc:
                    kind, error = "unknown", str(exc)[:300]
                    waiting = isinstance(exc, StepNeedsApproval)
                except StepFailed as exc:
                    kind, error = "failure", str(exc)[:300]
                except asyncio.CancelledError:
                    raise
                except Exception as exc:   # keep the class only: provider errors can carry URLs or keys
                    kind = "unknown" if step.effect == "non_idempotent" else "failure"
                    error = type(exc).__name__
                waited_total[0] += ctx.waited
                retryable = (attempts <= retries and step.effect != "non_idempotent" and not waiting and not (cancel and cancel.is_set())
                             and not halt and time.monotonic() + 2.0 < hard_limit())
                if retryable:
                    await asyncio.sleep(random.uniform(*backoff))
                    if not (cancel is not None and cancel.is_set()):
                        continue              # stopped during the pause: report this attempt, start no other
                held = kind == "unknown" and (step.effect == "non_idempotent" or waiting)
                if held:
                    halt.append(f"{step.id} is waiting for you" if waiting else f"{step.id} may or may not have happened")
                return emit(step, StepResult(step.id, kind, error=error, seconds=time.monotonic() - began,
                                             attempts=attempts, held=held, waited=ctx.waited))

    while pending:
        ready: list[Step] = []
        skipped_any = False
        for step in list(pending):
            deps = [results.get(d) for d in step.depends_on]
            if any(r is None for r in deps):
                continue
            bad = next((r for r in deps if not r.ok), None)
            if bad is not None:
                pending.remove(step)
                if cancel is not None and cancel.is_set():
                    why = "cancelled"                  # the run was stopped (its step in flight was interrupted)
                else:
                    if bad.held:
                        waiting = (bad.error or "").startswith(NEEDS_YOU)
                        why = f"held back: {bad.id} is waiting for you" if waiting else f"held back: {bad.id} may or may not have happened"
                    else:
                        why = "a step it needed did not finish"
                not_executed(step, why)
                skipped_any = True
                continue
            ready.append(step)
        if not ready:
            if skipped_any:
                continue                   # a skipped step may be what its own dependents were waiting on
            for s in pending:              # unreachable for validated plans; never loop forever
                not_executed(s, "could not be scheduled")
            break
        for s in ready:
            pending.remove(s)
        await asyncio.gather(*(one(s) for s in ready))
    return [results[s.id] for s in plan.steps if s.id in results]


def run_state(plan: Plan, results: list[StepResult], cancelled: bool = False) -> str:
    """One word for the whole run: done, partial, held, failed or cancelled."""
    if cancelled:
        return "cancelled"
    if any(r.held for r in results):
        return "held"
    if results and all(r.ok for r in results) and len(results) == len(plan.steps):
        return "done"
    if any(r.ok for r in results):
        return "partial"
    return "failed"


def final_text(plan: Plan, results: list[StepResult]) -> str:
    """The answer to hand back: the output of the steps nothing else depends on, and an honest account of what did not work."""
    by_id = {r.id: r for r in results}
    goals = {s.id: s.goal for s in plan.steps}
    needed = {d for s in plan.steps for d in s.depends_on}
    leaves = [s for s in plan.steps if s.id not in needed]
    parts = [by_id[s.id].text for s in leaves if s.id in by_id and by_id[s.id].ok and by_id[s.id].text.strip()]
    held = [r for r in results if r.held]
    others = [r for r in results if not r.ok and not r.held]
    lines = []
    if parts:
        lines.append("\n\n".join(parts))
    for r in held:
        if (r.error or "").startswith(NEEDS_YOU):
            lines.append(f"Waiting for you: {goals.get(r.id, r.id)[:140]} ({r.error}). It has not run yet and I did not go on. "
                         "Give the approval or answer, then tell me to continue.")
            continue
        lines.append(f"Needs your attention: I could not confirm whether this happened, so I did not retry it or go on: "
                     f"{goals.get(r.id, r.id)[:140]} ({r.error or 'result unknown'}). Please check, then tell me whether to run it again.")
    if others:
        names = ", ".join(f"{r.id} ({r.outcome.replace('_', ' ')}: {r.error or 'no reason given'})" for r in others)
        lines.append(("Not everything worked: " if parts or held else "I could not finish this: ") + names + ".")
    if not lines:
        lines.append("I could not finish this: nothing produced a result.")
    return "\n\n".join(lines)
