"""closed_loop: act -> verify by effect -> retry ONCE with an alternative route -> ask. Fake clock, fake routes (D59, lever a)."""
import asyncio

import pytest

from jarvis.plugins._shared import ask_form, closed_loop as cl


class Clock:
    def __init__(self):
        self.t = 0.0

    def now(self):
        return self.t

    def sleep(self, s):
        self.t += s


class World:
    """What the screen would show: routes change it, verify reads it."""
    def __init__(self, works=(), partial=False):
        self.done = False
        self.acts = []
        self.works = set(works)       # routes that really produce the effect
        self.partial = partial        # the check says "partial" instead of "absent" when nothing verified

    def route(self, name, raises=False):
        def act():
            self.acts.append(name)
            if raises:
                raise RuntimeError("boom with screen text SECRET-123")
            if name in self.works:
                self.done = True
        return cl.Route(name, act)

    def verify(self):
        if self.done:
            return cl.Verification(cl.VERIFIED, "file exists")
        return cl.Verification(cl.PARTIAL if self.partial else cl.ABSENT)


def run(step, clock=None):
    c = clock or Clock()
    return cl.run_step(step, clock=c.now, sleep=c.sleep)


def test_first_route_that_works_is_done_without_retry():
    w = World(works={"uia"})
    r = run(cl.Step("s", "save", [w.route("uia"), w.route("keyboard")], w.verify))
    assert r.verified and r.status == cl.OK and r.route_used == "uia" and not r.retried and w.acts == ["uia"]


def test_unverified_first_route_retries_once_with_the_next_route():
    w = World(works={"keyboard"})
    r = run(cl.Step("s", "save", [w.route("keyboard"), w.route("uia"), w.route("coordinates")], w.verify))
    assert w.acts == ["uia", "keyboard"]                 # order is UIA, keyboard, coordinates whatever order was listed
    assert r.verified and r.route_used == "keyboard" and r.retried


def test_default_is_one_retry_then_ask_not_the_whole_chain():
    w = World(works={"coordinates"})
    r = run(cl.Step("s", "click Save", [w.route("uia"), w.route("keyboard"), w.route("coordinates")], w.verify))
    assert w.acts == ["uia", "keyboard"] and not r.verified and r.status == cl.ASKED
    assert ask_form.is_form(r.ask, min_options=2) and "click Save" in r.ask["question"]


def test_max_attempts_three_walks_the_chain_to_coordinates():
    w = World(works={"coordinates"})
    r = run(cl.Step("s", "click Save", [w.route("uia"), w.route("keyboard"), w.route("coordinates")], w.verify, max_attempts=3))
    assert w.acts == ["uia", "keyboard", "coordinates"] and r.verified and r.route_used == "coordinates"


def test_a_route_that_raises_counts_as_unverified_and_never_leaks_the_message():
    w = World(works={"keyboard"})
    r = run(cl.Step("s", "save", [w.route("uia", raises=True), w.route("keyboard")], w.verify))
    assert r.verified and r.attempts[0].error == "RuntimeError" and "SECRET" not in repr(r.attempts)


def test_partial_effect_of_a_non_idempotent_step_is_not_repeated():
    w = World(works={"keyboard"}, partial=True)
    r = run(cl.Step("s", "type the note", [w.route("uia"), w.route("keyboard")], w.verify, idempotent=False))
    assert w.acts == ["uia"] and r.status == cl.ASKED and "twice" in r.reason     # a second route could type it twice


def test_partial_effect_of_an_idempotent_step_goes_on_to_the_next_route():
    w = World(works={"keyboard"}, partial=True)
    r = run(cl.Step("s", "open Notepad", [w.route("uia"), w.route("keyboard")], w.verify, idempotent=True))
    assert w.acts == ["uia", "keyboard"] and r.verified


def test_effect_that_shows_late_is_waited_for_not_retried():
    c = Clock()
    seen = {"n": 0}

    def verify():
        seen["n"] += 1
        return cl.Verification(cl.VERIFIED if c.now() >= 1.0 else cl.ABSENT)
    acts = []
    step = cl.Step("s", "save", [cl.Route("uia", lambda: acts.append("uia")), cl.Route("keyboard", lambda: acts.append("kb"))], verify,
                   settle_s=2.0, poll_s=0.25)
    r = cl.run_step(step, clock=c.now, sleep=c.sleep)
    assert r.verified and acts == ["uia"] and seen["n"] >= 4


def test_budget_ends_the_step_as_a_question():
    c = Clock()
    w = World(works=set())
    step = cl.Step("s", "save", [w.route("uia"), w.route("keyboard")], w.verify, budget_s=1.0, settle_s=2.0)
    r = cl.run_step(step, clock=c.now, sleep=c.sleep)
    assert r.status == cl.ASKED and w.acts == ["uia"]         # the budget (1 s) was gone after the first wait: no second route
    assert "budget" in r.reason


def test_guard_blocks_coordinates_until_confirmed():
    w = World(works={"coordinates"})
    blocked = cl.Route("coordinates", lambda: w.acts.append("coordinates"), guard=lambda: False)
    r = run(cl.Step("s", "click", [w.route("uia"), blocked], w.verify, max_attempts=3))
    assert "coordinates" not in w.acts and r.status == cl.ASKED
    assert any(a.verification == "skipped" for a in r.attempts)


def test_a_check_that_crashes_is_unknown_never_verified():
    def verify():
        raise ValueError("x")
    r = run(cl.Step("s", "save", [cl.Route("uia", lambda: None)], verify))
    assert not r.verified and r.status == cl.ASKED and r.attempts[0].verification == cl.UNKNOWN


def test_truthy_garbage_from_a_check_is_not_verified():
    assert cl.as_verification("yes").status == cl.UNKNOWN and cl.as_verification(1).status == cl.UNKNOWN
    assert cl.as_verification(True).ok and cl.as_verification((False, "no")).status == cl.ABSENT


def test_cancel_stops_before_the_next_attempt():
    w = World(works={"keyboard"})
    flag = {"on": False}

    def uia():
        w.acts.append("uia")
        flag["on"] = True
    c = Clock()
    r = cl.run_step(cl.Step("s", "save", [cl.Route("uia", uia), w.route("keyboard")], w.verify), clock=c.now, sleep=c.sleep,
                    cancelled=lambda: flag["on"])
    assert r.status == cl.CANCELLED and w.acts == ["uia"]


def test_no_routes_is_a_question():
    r = run(cl.Step("s", "save", [], lambda: True))
    assert r.status == cl.ASKED


def test_async_routes():
    w = World(works={"keyboard"})

    async def act_uia():
        w.acts.append("uia")

    async def act_kb():
        w.acts.append("keyboard")
        w.done = True

    async def verify():
        return w.verify()

    async def main():
        c = Clock()

        async def nap(s):
            c.sleep(s)
        return await cl.arun_step(cl.Step("s", "save", [cl.Route("uia", act_uia), cl.Route("keyboard", act_kb)], verify),
                                  clock=c.now, sleep=nap)
    r = asyncio.run(main())
    assert r.verified and w.acts == ["uia", "keyboard"]


def test_sync_runner_refuses_coroutine_routes():
    class Suspend:
        def __await__(self):
            yield

    async def act():
        await Suspend()
    with pytest.raises(TypeError):
        cl.run_step(cl.Step("s", "x", [cl.Route("uia", act)], lambda: True))


def test_summary_sentences():
    w = World(works={"keyboard"})
    r = run(cl.Step("s", "save the note", [w.route("uia"), w.route("keyboard")], w.verify))
    assert cl.summary(r) == "save the note: confirmed (via keyboard, after a retry)"
    r2 = run(cl.Step("s", "save the note", [w.route("uia")], lambda: False))
    assert "not confirmed" in cl.summary(r2)
