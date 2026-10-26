"""Closed-loop execution of ONE step, for any step (D59, lever a): act, then check the EFFECT, and only then call the step done.

    act (first route) -> verify by effect (wait a short settle time) -> verified?  done.
                                                                      -> not verified: retry ONCE with the next route
    routes, in this order:  UIA invoke  ->  keyboard  ->  screen coordinates  ->  ask the owner
    every step has a budget in seconds; when it is used up, or the routes are, the step ends as a QUESTION to the owner (an ask form),
    never as a guess and never as "done".

Why a step is not blindly repeated: a click that "did not show an effect" may still have done its work a moment later, and a second
route would then type the text twice or press the button twice. So a retry happens only when the check says the effect is ABSENT
(nothing changed). When the check says PARTIAL or UNKNOWN, a step that is not `idempotent` is NOT repeated: it goes to the owner.
(This is the same rule the coordinator applies with REPEAT_SAFE in plugins/desktop/coordinator.py, stated per step.)

The module knows nothing about Windows, the kernel or models: a Route is a function, a verify is a function. The caller (the desktop
coordinator, a mission step, the eval harness) lends them. Not yet wired into the coordinator: see D59 for the file and function.

    result = run_step(Step("save", "save the note", [Route("uia", invoke_save), Route("keyboard", press_ctrl_s)], verify=file_exists))
    result.verified, result.route_used, result.ask     # ask is an ask_form dict when the owner has to decide

`run_step` is plain code; `arun_step` is the same logic for async routes and checks. Standard library only.
"""
from __future__ import annotations

import asyncio
import inspect
import time
from dataclasses import dataclass, field
from typing import Any, Callable

from . import ask_form

VERIFIED, ABSENT, PARTIAL, UNKNOWN = "verified", "absent", "partial", "unknown"
ROUTE_ORDER = ("uia", "keyboard", "coordinates")     # the order alternatives are tried in; a caller lists the routes it has, in this order
COORDINATES = "coordinates"

# step results
OK, ASKED, FAILED, CANCELLED = "verified", "asked", "failed", "cancelled"


@dataclass
class Verification:
    status: str                    # verified | absent | partial | unknown
    evidence: str = ""

    @property
    def ok(self) -> bool:
        return self.status == VERIFIED


def as_verification(value: Any) -> Verification:
    """A verify function may return a Verification, a bool, (bool, text) or one of the status words. Anything else is UNKNOWN."""
    if isinstance(value, Verification):
        return value
    if value is True:
        return Verification(VERIFIED)
    if value is False:
        return Verification(ABSENT)
    if isinstance(value, tuple) and len(value) == 2 and isinstance(value[0], bool):
        return Verification(VERIFIED if value[0] else ABSENT, str(value[1] or "")[:200])
    if isinstance(value, str) and value in (VERIFIED, ABSENT, PARTIAL, UNKNOWN):
        return Verification(value)
    return Verification(UNKNOWN, "the check returned something it should not have")


@dataclass
class Route:
    name: str                                         # "uia" | "keyboard" | "coordinates" (any other name is allowed; it keeps its place)
    act: Callable[[], Any]                            # does the action; raising means the route failed to even send
    guard: Callable[[], bool] | None = None           # False = this route may not be used now (coordinates not confirmed, target gone)


@dataclass
class Step:
    id: str
    what: str                                         # plain words: "save the note", used in the question to the owner
    routes: list[Route]
    verify: Callable[[], Any]                         # reads the EFFECT (file exists, window appeared, text in the field), not the action's reply
    idempotent: bool = False                          # True: doing it twice changes nothing more (open, focus, read)
    budget_s: float = 20.0                            # all attempts together
    settle_s: float = 2.0                             # after an act, how long the effect may take to show
    poll_s: float = 0.25
    max_attempts: int = 2                             # the first route, and ONE alternative (3 walks the whole chain)


@dataclass
class Attempt:
    route: str
    error: str = ""
    verification: str = ""
    evidence: str = ""
    seconds: float = 0.0


@dataclass
class StepResult:
    step_id: str
    what: str
    status: str                                       # verified | asked | failed | cancelled
    verified: bool = False
    route_used: str = ""
    attempts: list[Attempt] = field(default_factory=list)
    seconds: float = 0.0
    reason: str = ""
    ask: dict | None = None

    @property
    def retried(self) -> bool:
        return sum(1 for a in self.attempts if a.verification != "skipped") > 1


def order_routes(routes: list[Route]) -> list[Route]:
    """Known route names in ROUTE_ORDER first (stable), unknown names after them in the order given."""
    rank = {n: i for i, n in enumerate(ROUTE_ORDER)}
    return sorted(routes, key=lambda r: rank.get(r.name, len(ROUTE_ORDER)))


def _question(step: Step, why: str) -> dict:
    return ask_form.build_form(
        f"I could not confirm: {step.what}. What should I do?",
        ["Try again", "Skip this step", "Stop here"], reason=why, source="closed_loop", default="o3")


async def _call(fn: Callable[[], Any]) -> Any:
    out = fn()
    if inspect.isawaitable(out):
        out = await out
    return out


async def _core(step: Step, clock: Callable[[], float], sleep: Callable[[float], Any], cancelled: Callable[[], bool] | None) -> StepResult:
    t0 = clock()
    res = StepResult(step.id, step.what, FAILED)
    routes = order_routes(step.routes)
    if not routes:
        res.reason = "the step has no route"
        res.ask = _question(step, res.reason)
        res.status = ASKED
        return res
    tried = 0
    for route in routes:
        if tried >= max(1, step.max_attempts):
            break
        if cancelled and cancelled():
            res.status, res.reason = CANCELLED, "stopped before the next attempt"
            break
        if clock() - t0 >= step.budget_s:
            res.reason = f"the {step.budget_s:.0f} s budget for this step was used up"
            break
        if route.guard is not None:
            try:
                allowed = bool(await _call(route.guard))
            except Exception:  # noqa: BLE001 - a broken guard means "not allowed"
                allowed = False
            if not allowed:
                res.attempts.append(Attempt(route.name, error="route not allowed now", verification="skipped"))
                continue
        tried += 1
        a = Attempt(route.name)
        a0 = clock()
        try:
            await _call(route.act)
        except Exception as e:  # noqa: BLE001 - a route that raised did not (surely) act; the check below decides
            a.error = type(e).__name__                       # the class only: a message may hold screen text
        v = await _wait_for_effect(step, clock, sleep, a0, t0)
        a.verification, a.evidence, a.seconds = v.status, v.evidence, round(clock() - a0, 3)
        res.attempts.append(a)
        if v.ok:
            res.status, res.verified, res.route_used = OK, True, route.name
            break
        if v.status in (PARTIAL, UNKNOWN) and not step.idempotent:
            # something may have happened: a second route could do it twice. The owner decides.
            res.reason = f"the effect is {v.status} after the {route.name} route and repeating could do it twice"
            break
        # ABSENT, or an idempotent step: the next route is safe to try
    if res.status == OK:
        res.seconds = round(clock() - t0, 3)
        return res
    if res.status != CANCELLED:
        if not res.reason:
            res.reason = "no route showed the effect" if tried else "no route could be used"
        res.status = ASKED
        res.ask = _question(step, res.reason)
    res.seconds = round(clock() - t0, 3)
    return res


async def _wait_for_effect(step: Step, clock, sleep, a0: float, t0: float) -> Verification:
    """Poll the check until it says verified, or the settle time (and the step budget) runs out. The LAST answer is returned."""
    last = Verification(UNKNOWN, "never checked")
    while True:
        try:
            last = as_verification(await _call(step.verify))
        except Exception as e:  # noqa: BLE001 - a check that crashes is UNKNOWN, never "verified"
            last = Verification(UNKNOWN, f"the check raised {type(e).__name__}")
        if last.ok:
            return last
        now = clock()
        if now - a0 >= step.settle_s or now - t0 >= step.budget_s:
            return last
        r = sleep(min(step.poll_s, max(0.0, step.settle_s - (now - a0))))
        if inspect.isawaitable(r):
            await r


def run_step(step: Step, clock: Callable[[], float] = time.monotonic, sleep: Callable[[float], Any] = time.sleep,
             cancelled: Callable[[], bool] | None = None) -> StepResult:
    """Run one step with plain (synchronous) routes and checks. Routes that are coroutines need `arun_step`."""
    async def nap(s: float) -> None:
        sleep(s)
    coro = _core(step, clock, nap, cancelled)
    try:
        coro.send(None)
    except StopIteration as done:
        return done.value
    coro.close()
    raise TypeError("a route or check was a coroutine: use arun_step")


async def arun_step(step: Step, clock: Callable[[], float] = time.monotonic, sleep: Callable[[float], Any] = asyncio.sleep,
                    cancelled: Callable[[], bool] | None = None) -> StepResult:
    """The same as run_step; routes and checks may be coroutine functions."""
    return await _core(step, clock, sleep, cancelled)


def summary(result: StepResult) -> str:
    """One plain sentence about a step result, for logs and for the templated final answer."""
    if result.status == OK:
        via = f" (via {result.route_used}{', after a retry' if result.retried else ''})"
        return f"{result.what}: confirmed{via}"
    if result.status == CANCELLED:
        return f"{result.what}: stopped before it finished"
    return f"{result.what}: not confirmed ({result.reason})"
