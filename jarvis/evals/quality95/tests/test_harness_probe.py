"""The planner-prompt Propose-Probe-Compose: edits are chosen from observed failures, kept only when nothing that was right gets worse,
and the result is a proposal (hints text), never a change to the planner. A fake model stands in for the gateway."""
import importlib.util
import json
from pathlib import Path

SPEC = importlib.util.spec_from_file_location("harness_probe", Path(__file__).resolve().parents[1] / "bench" / "harness_probe.py")
P = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(P)
H = P.H


def plan(agent_ref):
    if agent_ref == "cannot":
        return json.dumps({"summary": "cannot", "steps": []})
    if agent_ref in ("think", "jarvis"):
        return json.dumps({"summary": "s", "steps": [{"id": "s1", "agent": agent_ref, "goal": "do it"}]})
    return json.dumps({"summary": "s", "steps": [{"id": "s1", "agent": "plugin", "plugin": agent_ref, "goal": "g", "args": {"query": "australia mars paris fasting"}}]})


def right_answer(req):
    for r, expect, _w in H.CASES:
        if r == req:
            return plan("cannot") if expect == "cannot" or "cannot" in expect else plan(sorted(expect)[0])


class FakeModel:
    """Answers every request right, except the pizza/flight ones, which it plans something for unless the 'cannot' rule is in the prompt.
    `hurt_rule`: when a given rule is present, it breaks the joke request."""
    def __init__(self, hurt_rule=None):
        self.hurt_rule, self.calls = hurt_rule, 0

    def __call__(self, base, model, messages, max_tokens):
        self.calls += 1
        system, req = messages[0]["content"], messages[1]["content"]
        if req.startswith(("Order me a pizza", "Book me a flight")):
            return (plan("cannot") if "not in the catalogue" in system else plan("web_search:search")), 10, 0.1, "stop"
        if self.hurt_rule and self.hurt_rule in system and req.startswith("Tell me a short joke"):
            return plan("jarvis"), 10, 0.1, "stop"
        return right_answer(req), 10, 0.1, "stop"


def test_a_failure_cue_selects_the_matching_edit_and_the_composite_fixes_it():
    rep = P.improve(FakeModel(), "b", "m", repeat=2)
    assert rep["candidates"] == ["cannot-when-not-listed"] and rep["chosen"] == ["cannot-when-not-listed"]
    assert rep["adopt"] and rep["final_correct"] == rep["base_correct"] + 4          # 2 impossible requests x 2 repeats
    assert "not in the catalogue" in rep["hints"] and rep["lessons"][0]["helped"]


def test_an_edit_that_breaks_a_good_case_is_not_composed():
    rep = P.improve(FakeModel(hurt_rule="not in the catalogue"), "b", "m", repeat=2)
    assert rep["chosen"] == [] and rep["adopt"] is False and rep["hints"] == ""
    assert rep["lessons"][0]["hurt"] == ["Tell me a short joke."]                      # the harm is recorded as a lesson


def test_nothing_to_fix_proposes_nothing_and_costs_only_the_base_run():
    class Perfect(FakeModel):
        def __call__(self, base, model, messages, max_tokens):
            self.calls += 1
            return right_answer(messages[1]["content"]), 10, 0.1, "stop"
    m = Perfect()
    rep = P.improve(m, "b", "m", repeat=2)
    assert rep["candidates"] == [] and rep["adopt"] is False and rep["rollouts"] == m.calls == 2 * len(H.CASES)


def test_rollouts_are_counted_like_harness2s_ledger():
    m = FakeModel()
    rep = P.improve(m, "b", "m", repeat=1)
    n = len(H.CASES)
    assert rep["rollouts"] == m.calls == n * (1 + 1 + 1)                                # base + 1 probe + final composite


def test_it_never_touches_the_planner():
    before = P.planner.planner_messages("x", H.CAPS, 4, "work")
    P.improve(FakeModel(), "b", "m", repeat=1)
    assert P.planner.planner_messages("x", H.CAPS, 4, "work") == before
