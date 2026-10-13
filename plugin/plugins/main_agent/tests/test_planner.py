from __future__ import annotations

import json

import pytest

from plugin.plugins.main_agent.planner import Capability, PlanError, build_catalogue, parse_plan, planner_messages


def plugins():
    return [
        {"id": "web_search", "status": "running", "description": "web", "entries": [
            {"id": "search", "description": "Search the web", "input_schema": {"type": "object", "properties": {"query": {"type": "string"}, "max_results": {"type": "integer"}}, "required": ["query"]}}]},
        {"id": "music_pusher", "status": "stopped", "entries": [{"id": "play", "description": "Play"}]},
        {"id": "main_agent", "status": "running", "entries": [{"id": "run_task", "description": "me"}]},
        {"id": "jarvis_bridge", "status": "running", "entries": [{"id": "delegate_task", "description": "bridge"}]},
    ]


def caps(**kw):
    base = dict(browser_ready=True, jarvis_ready=True, computer_ready=True, allow_computer=False)
    base.update(kw)
    return build_catalogue(plugins(), **base)


def refs(cs):
    return {c.ref for c in cs}


def test_catalogue_offers_only_what_is_usable():
    r = refs(caps())
    assert r == {"think", "browser", "jarvis", "web_search:search"}          # not stopped plugins, not itself, not the bridge, no desktop


def test_catalogue_respects_readiness_and_the_desktop_opt_in():
    assert "browser" not in refs(caps(browser_ready=False))
    assert "jarvis" not in refs(caps(jarvis_ready=False))
    assert "computer" not in refs(caps(computer_ready=True, allow_computer=False))
    assert "computer" not in refs(caps(computer_ready=False, allow_computer=True))
    assert "computer" in refs(caps(computer_ready=True, allow_computer=True))


def test_prompt_lists_the_catalogue_and_warns_about_untrusted_output():
    m = planner_messages("x" * 5000, caps(), 4)
    assert m[0]["role"] == "system" and "web_search:search" in m[0]["content"] and "untrusted" in m[0]["content"]
    assert "at most 4 steps" in m[0]["content"] and len(m[1]["content"]) == 2000


GOOD = {"summary": "Look it up and summarise", "steps": [
    {"id": "s1", "agent": "plugin", "plugin": "web_search:search", "goal": "find it", "args": {"query": "cats", "evil": 1}, "depends_on": []},
    {"id": "s2", "agent": "browser", "goal": "open the top page", "depends_on": []},
    {"id": "s3", "agent": "think", "goal": "summarise", "depends_on": ["s1", "s2"]}]}


def test_a_valid_plan_parses_and_drops_undeclared_arguments():
    plan = parse_plan(json.dumps(GOOD), caps(), 6)
    assert [s.id for s in plan.steps] == ["s1", "s2", "s3"]
    assert plan.steps[0].args == {"query": "cats"}                               # "evil" is not in the entry's schema
    assert plan.steps[2].depends_on == ["s1", "s2"]


def test_json_in_a_code_fence_or_surrounded_by_prose_is_accepted():
    assert parse_plan("```json\n" + json.dumps(GOOD) + "\n```", caps(), 6).steps
    assert parse_plan("Here is the plan: " + json.dumps(GOOD) + " Done.", caps(), 6).steps


@pytest.mark.parametrize("mutate,needle", [
    (lambda d: d.update(steps=[]), "None of the available helpers"),
    (lambda d: d.update(steps="nope"), "no steps list"),
    (lambda d: d["steps"].extend([{"id": f"x{i}", "agent": "think", "goal": "g"} for i in range(5)]), "at most"),
    (lambda d: d["steps"][1].update(agent="computer"), "not available"),
    (lambda d: d["steps"][0].update(plugin="music_pusher:play"), "not available"),
    (lambda d: d["steps"][0].update(args={}), "missing required"),
    (lambda d: d["steps"][0].update(depends_on=["s3"]), "does not come before"),
    (lambda d: d["steps"][1].update(id="s1"), "repeated id"),
    (lambda d: d["steps"][1].update(id="Bad Id!"), "missing or repeated id"),
    (lambda d: d["steps"][1].update(goal="  "), "no goal"),
])
def test_unsafe_or_malformed_plans_are_refused(mutate, needle):
    d = json.loads(json.dumps(GOOD))
    mutate(d)
    with pytest.raises(PlanError) as e:
        parse_plan(json.dumps(d), caps(), 6)
    assert needle in str(e.value)


@pytest.mark.parametrize("text", ["", "no json here", "{broken", "[1,2,3]"])
def test_non_plans_are_refused(text):
    with pytest.raises(PlanError):
        parse_plan(text, caps(), 6)
