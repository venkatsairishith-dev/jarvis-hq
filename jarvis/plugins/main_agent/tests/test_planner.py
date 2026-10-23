from __future__ import annotations

import json

import pytest

from jarvis.plugins.main_agent.planner import Capability, PlanError, build_catalogue, parse_plan, planner_messages


def plugins():
    return [
        {"id": "web_search", "status": "running", "description": "web", "entries": [
            {"id": "search", "description": "Search the web", "input_schema": {"type": "object", "properties": {"query": {"type": "string"}, "max_results": {"type": "integer"}}, "required": ["query"]}}]},
        {"id": "music_pusher", "status": "stopped", "entries": [{"id": "play", "description": "Play"}]},
        {"id": "main_agent", "status": "running", "entries": [{"id": "run_task", "description": "me"}]},
        {"id": "jarvis_bridge", "status": "running", "entries": [{"id": "delegate_task", "description": "bridge"}]},
    ]


def caps(**kw):
    base = dict(jarvis_ready=True)
    base.update(kw)
    return build_catalogue(plugins(), **base)


def refs(cs):
    return {c.ref for c in cs}


def test_catalogue_offers_only_what_is_usable():
    r = refs(caps())
    assert r == {"think", "jarvis", "web_search:search"}          # not stopped plugins, not itself, not the bridge, no desktop


def test_catalogue_respects_readiness_and_has_no_browser_or_computer_helper():
    assert "jarvis" not in refs(caps(jarvis_ready=False))
    assert not refs(caps()) & {"browser", "computer"}


def test_prompt_lists_the_catalogue_and_warns_about_untrusted_output():
    m = planner_messages("x" * 5000, caps(), 4)
    assert m[0]["role"] == "system" and "web_search:search" in m[0]["content"] and "untrusted" in m[0]["content"]
    assert "at most 4 steps" in m[0]["content"] and len(m[1]["content"]) == 2000


GOOD = {"summary": "Look it up and summarise", "steps": [
    {"id": "s1", "agent": "plugin", "plugin": "web_search:search", "goal": "find it", "args": {"query": "cats", "evil": 1}, "depends_on": []},
    {"id": "s2", "agent": "jarvis", "goal": "open the top page", "depends_on": []},
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
    (lambda d: d["steps"][1].update(agent="computer"), "not available"),     # the old computer helper is gone
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
from jarvis.plugins.main_agent.planner import MODES, normalize_mode  # noqa: E402


def mode_plugins():
    return plugins() + [
        {"id": "learn_vault", "status": "running", "entries": [{"id": "save_note", "description": "Save a note", "input_schema": {"type": "object", "properties": {"path": {"type": "string"}, "content": {"type": "string"}}, "required": ["path", "content"]}}]},
        {"id": "doc_tools", "status": "running", "entries": [{"id": "read_document", "description": "Read a document", "input_schema": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}}]},
    ]


def caps_for(mode, **kw):
    base = dict(jarvis_ready=True)
    base.update(kw)
    return build_catalogue(mode_plugins(), mode=mode, **base)


def test_chat_mode_exposes_no_tools():
    assert refs(caps_for("chat")) == {"think"}


def test_work_mode_has_the_tools_but_not_the_study_vault():
    r = refs(caps_for("work"))
    assert {"think", "jarvis", "web_search:search", "doc_tools:read_document"} <= r
    assert not any(x.startswith("learn_vault:") for x in r)


def test_code_mode_is_jarvis_only_so_every_action_goes_through_the_kernel():
    assert refs(caps_for("code")) == {"think", "jarvis"}
    assert refs(caps_for("code", jarvis_ready=False)) == {"think"}          # never falls back to anything else


def test_learn_mode_is_the_vault_and_documents_only():
    assert refs(caps_for("learn")) == {"think", "learn_vault:save_note", "doc_tools:read_document"}


def test_a_mode_never_adds_what_is_not_running_or_ready():
    p = [dict(x, status="stopped") if x["id"] == "learn_vault" else x for x in mode_plugins()]
    assert "learn_vault:save_note" not in refs(build_catalogue(p, jarvis_ready=False, mode="learn"))


def test_plans_cannot_use_a_helper_the_mode_removed():
    work_plan = {"summary": "s", "steps": [{"id": "s1", "agent": "plugin", "plugin": "web_search:search", "goal": "look it up",
                                            "args": {"query": "cats"}}]}
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
from jarvis.plugins.main_agent.planner import strip_reasoning  # noqa: E402


def test_reasoning_blocks_are_removed_before_parsing():
    good = json.dumps(GOOD)
    noisy = "<think>Let me plan {this} carefully, maybe {\"a\": 1} first.</think>\n" + good
    assert parse_plan(noisy, caps(), 6).steps
    assert strip_reasoning("<think>never closed {\"steps\": []}").strip() == ""
    assert strip_reasoning("plain") == "plain"
    assert parse_plan("<THINKING>x</THINKING>" + good, caps(), 6).steps


# ---------- passing an earlier result into a later plugin step ----------
from jarvis.plugins.main_agent.planner import fill_args  # noqa: E402


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


def test_work_mode_tells_the_planner_to_use_think_for_general_knowledge():
    system = planner_messages("what is the capital of Italy", caps(), 4, "work")[0]["content"]
    assert "Use think for anything the model can answer from general knowledge" in system
    assert "never use them for general knowledge" in system
    assert "browser" not in system.lower().replace("browsing", "")


def test_side_effects_of_real_entries_fail_closed():
    """Benchmark v2 A-066: entries whose names are not plain reads were classed as safe to retry (toggle_* would undo
    itself, call_tool/gateway_invoke run anything). Unknown verbs now count as side effects."""
    from jarvis.plugins.main_agent.planner import effect_of
    for ref in ("music_pusher:toggle_schedule_task", "mcp_adapter:call_tool", "mcp_adapter:gateway_invoke", "music_pusher:skip_current_track",
                "messenger_app:logout", "jukebox_controller:control_jukebox", "proactive_controller:command", "deliverables:make_presentation"):
        assert effect_of("plugin", ref) == "non_idempotent", ref
    for ref in ("web_search:search", "web_search:search_summary", "proactive_controller:get_state", "local_team:team_status",
                "jarvis_bridge:task_result", "learn_vault:search_notes"):
        assert effect_of("plugin", ref) == "none", ref


def test_an_argument_of_the_wrong_type_is_refused():  # bench A-094 (2026-10-17); before: accepted
    for q in (42, ["cats"], {"q": "cats"}, None, True):
        text = json.dumps({"summary": "x", "steps": [{"id": "s1", "agent": "plugin", "plugin": "web_search:search", "goal": "find",
                                                       "args": {"query": q}}]})
        with pytest.raises(PlanError, match="wrong type"):
            parse_plan(text, caps(), 6)
    for args in ({"query": "cats", "max_results": "5"}, {"query": "cats", "max_results": 2.5}, {"query": "cats", "max_results": False}):
        text = json.dumps({"summary": "x", "steps": [{"id": "s1", "agent": "plugin", "plugin": "web_search:search", "goal": "find", "args": args}]})
        with pytest.raises(PlanError, match="wrong type"):
            parse_plan(text, caps(), 6)
    text = json.dumps({"summary": "x", "steps": [{"id": "s1", "agent": "plugin", "plugin": "web_search:search", "goal": "find",
                                                   "args": {"query": "cats", "max_results": 5}}]})
    assert parse_plan(text, caps(), 6).steps[0].args == {"query": "cats", "max_results": 5}


def test_a_trailing_comma_is_forgiven_but_other_broken_json_is_not():
    text = json.dumps(GOOD)
    with_commas = text.replace("}]", "},]").replace("}}", "},}")           # a comma before a closing ] or }
    assert with_commas != text and parse_plan(with_commas, caps(), 6).steps[0].id == "s1"
    for bad in (text.replace('"id": "s1"', '"id" "s1"', 1), text[:40], "not json at all"):
        with pytest.raises(PlanError):
            parse_plan(bad, caps(), 6)


def test_the_planner_and_every_reasoning_step_are_told_the_time():
    import re as _re
    from jarvis.plugins.main_agent.planner import now_line
    line = now_line()
    assert _re.fullmatch(r"Current local date and time: \w+ \d{2} \w+ \d{4}, \d{2}:\d{2} \(UTC[+-]\d{2}:\d{2}\)\.", line), line
    m = planner_messages("what time is it", caps(), 4)
    assert "Current local date and time:" in m[0]["content"] and "what time is it" == m[1]["content"]
