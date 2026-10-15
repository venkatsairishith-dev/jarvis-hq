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


# ---------- conversation modes narrow the closed catalogue ----------
from plugin.plugins.main_agent.planner import MODES, normalize_mode  # noqa: E402


def mode_plugins():
    return plugins() + [
        {"id": "learn_vault", "status": "running", "entries": [{"id": "save_note", "description": "Save a note", "input_schema": {"type": "object", "properties": {"path": {"type": "string"}, "content": {"type": "string"}}, "required": ["path", "content"]}}]},
        {"id": "doc_tools", "status": "running", "entries": [{"id": "read_document", "description": "Read a document", "input_schema": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}}]},
    ]


def caps_for(mode, **kw):
    base = dict(browser_ready=True, jarvis_ready=True, computer_ready=True, allow_computer=True)
    base.update(kw)
    return build_catalogue(mode_plugins(), mode=mode, **base)


def test_chat_mode_exposes_no_tools():
    assert refs(caps_for("chat")) == {"think"}


def test_work_mode_has_the_tools_but_not_the_study_vault():
    r = refs(caps_for("work"))
    assert {"think", "browser", "jarvis", "computer", "web_search:search", "doc_tools:read_document"} <= r
    assert not any(x.startswith("learn_vault:") for x in r)


def test_code_mode_is_jarvis_only_so_every_action_goes_through_the_kernel():
    assert refs(caps_for("code")) == {"think", "jarvis"}
    assert refs(caps_for("code", jarvis_ready=False)) == {"think"}          # never falls back to anything else


def test_learn_mode_is_the_vault_and_documents_only():
    assert refs(caps_for("learn")) == {"think", "learn_vault:save_note", "doc_tools:read_document"}


def test_a_mode_never_adds_what_is_not_running_or_ready():
    p = [dict(x, status="stopped") if x["id"] == "learn_vault" else x for x in mode_plugins()]
    assert "learn_vault:save_note" not in refs(build_catalogue(p, browser_ready=False, jarvis_ready=False, computer_ready=False,
                                                              allow_computer=False, mode="learn"))


def test_plans_cannot_use_a_helper_the_mode_removed():
    work_plan = {"summary": "s", "steps": [{"id": "s1", "agent": "browser", "goal": "look it up"}]}
    parse_plan(json.dumps(work_plan), caps_for("work"), 6)                  # fine in work mode
    with pytest.raises(PlanError):
        parse_plan(json.dumps(work_plan), caps_for("code"), 6)              # the same plan is refused in code mode
    vault_plan = {"summary": "s", "steps": [{"id": "s1", "agent": "plugin", "plugin": "learn_vault:save_note", "goal": "save",
                                             "args": {"path": "notes/a.md", "content": "x"}}]}
    parse_plan(json.dumps(vault_plan), caps_for("learn"), 6)
    with pytest.raises(PlanError):
        parse_plan(json.dumps(vault_plan), caps_for("work"), 6)


def test_unknown_modes_are_refused_and_the_default_is_work():
    assert normalize_mode(None) == "work" and normalize_mode(" LEARN ") == "learn"
    with pytest.raises(PlanError):
        normalize_mode("root")
    assert set(MODES) == {"chat", "work", "code", "learn"}


def test_the_prompt_states_the_mode():
    for mode in MODES:
        m = planner_messages("task", caps_for(mode), 4, mode)
        assert f"Mode: {mode}." in m[0]["content"]


# ---------- reasoning text from a reasoning model ----------
from plugin.plugins.main_agent.planner import strip_reasoning  # noqa: E402


def test_reasoning_blocks_are_removed_before_parsing():
    good = json.dumps(GOOD)
    noisy = "<think>Let me plan {this} carefully, maybe {\"a\": 1} first.</think>\n" + good
    assert parse_plan(noisy, caps(), 6).steps
    assert strip_reasoning("<think>never closed {\"steps\": []}").strip() == ""
    assert strip_reasoning("plain") == "plain"
    assert parse_plan("<THINKING>x</THINKING>" + good, caps(), 6).steps


# ---------- passing an earlier result into a later plugin step ----------
from plugin.plugins.main_agent.planner import fill_args  # noqa: E402


def _save_caps():
    return [Capability("think", "think", "t"),
            Capability("plugin", "learn_vault:save_note", "save",
                       {"type": "object", "properties": {"path": {"type": "string"}, "content": {"type": "string"}}, "required": ["path", "content"]})]


def _plan(args, deps=("s1",)):
    return json.dumps({"summary": "s", "steps": [
        {"id": "s1", "agent": "think", "goal": "write exercises"},
        {"id": "s2", "agent": "plugin", "plugin": "learn_vault:save_note", "goal": "save", "args": args, "depends_on": list(deps)}]})


def test_a_result_may_fill_a_content_argument_of_a_dependent_step():
    plan = parse_plan(_plan({"path": "exercises/a.md", "content": "# Exercises\n\n{{s1}}"}), _save_caps(), 6)
    assert plan.steps[1].args["content"].endswith("{{s1}}")


def test_a_result_cannot_steer_other_arguments_or_a_step_it_does_not_depend_on():
    with pytest.raises(PlanError) as e:
        parse_plan(_plan({"path": "{{s1}}", "content": "x"}), _save_caps(), 6)         # the path must not come from data
    assert "may take one" in str(e.value)
    with pytest.raises(PlanError):
        parse_plan(_plan({"path": "a.md", "content": "{{s1}}"}, deps=()), _save_caps(), 6)


def test_fill_args_substitutes_truncates_and_leaves_other_values_alone():
    out = fill_args({"path": "a.md", "content": "Intro\n{{s1}}\nEnd {{ s2 }} {{nope}}", "n": 3},
                    {"s1": "X" * 50000, "s2": "two"})
    assert out["path"] == "a.md" and out["n"] == 3
    assert out["content"].startswith("Intro\n") and out["content"].count("X") == 20000 and out["content"].endswith("End two ")
    assert "{{" not in out["content"]


def test_work_mode_tells_the_planner_not_to_use_the_browser_for_general_knowledge():
    system = planner_messages("what is the capital of Italy", caps(), 4, "work")[0]["content"]
    assert "Use think for anything the model can answer from general knowledge" in system
    assert "Use the browser ONLY when" in system and "never for general knowledge" in system
    browser_line = next(l for l in system.splitlines() if l.startswith('- agent "browser"'))
    assert "Not for general knowledge".lower() in browser_line.lower()


def test_the_browser_helper_is_offered_only_when_the_request_points_at_the_web():
    from plugin.plugins.main_agent.planner import filter_for_task, needs_web
    full = caps()
    assert any(c.kind == "browser" for c in full)
    for plain in ("find the capital of Italy and save it to a document", "write a poem about cats", "summarise my notes"):
        assert not needs_web(plain), plain
        assert all(c.kind != "browser" for c in filter_for_task(full, plain)), plain
    for web in ("open https://example.com and read the title", "what is the latest news about electric cars",
                "check the weather today in Hyderabad", "fill in the form on that site", "search the web for flight prices"):
        assert needs_web(web), web
        assert any(c.kind == "browser" for c in filter_for_task(full, web)), web
    assert [c.kind for c in filter_for_task(full, "plain task")] == [c.kind for c in full if c.kind != "browser"]
