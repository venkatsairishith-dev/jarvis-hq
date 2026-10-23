from __future__ import annotations


from jarvis.plugins.main_agent.executor import StepResult
from jarvis.plugins.main_agent.planner import Plan, Step, effect_of
from jarvis.plugins.main_agent.store import RunStore, plan_from_dict, plan_to_dict, result_from_dict, result_to_dict


def sample_plan():
    return Plan("sum", [Step("s1", "think", "explain", effect="none"),
                        Step("s2", "plugin", "save", ref="learn_vault:save_note", args={"path": "a.md", "content": "{{s1}}"},
                             depends_on=["s1"], effect="non_idempotent")])


def test_plan_and_result_round_trip():
    p = plan_from_dict(plan_to_dict(sample_plan()))
    assert [s.id for s in p.steps] == ["s1", "s2"] and p.steps[1].args["content"] == "{{s1}}" and p.steps[1].effect == "non_idempotent"
    r = result_from_dict(result_to_dict(StepResult("s1", "unknown", "t", "e", 1.5, 2, True, 3.0)))
    assert (r.outcome, r.attempts, r.held, r.waited) == ("unknown", 2, True, 3.0)


def test_save_load_and_prune(tmp_path):
    s = RunStore(tmp_path / "runs", keep=3)
    for i in range(6):
        s.save({"run_id": f"r{i}", "state": "done"})
    s.prune()
    assert len(s.ids()) == 3
    assert s.load("r5")["state"] == "done" and s.load("r0") is None
    assert s.load("../../etc/passwd") is None                      # ids are flattened into the folder, never a path


def test_recover_marks_an_in_flight_step_unknown_and_held_only_when_it_has_side_effects(tmp_path):
    s = RunStore(tmp_path)
    s.save({"run_id": "r1", "state": "running", "plan": plan_to_dict(sample_plan()), "running": ["s2"],
            "results": {"s1": result_to_dict(StepResult("s1", "success", "done"))}})
    s.save({"run_id": "r2", "state": "running", "plan": plan_to_dict(sample_plan()), "running": ["s1"], "results": {}})
    s.save({"run_id": "r3", "state": "done", "plan": plan_to_dict(sample_plan()), "running": [], "results": {}})
    assert sorted(s.recover()) == ["r1", "r2"]
    r1, r2, r3 = s.load("r1"), s.load("r2"), s.load("r3")
    assert r1["state"] == "interrupted" and r1["results"]["s1"]["outcome"] == "success"
    assert r1["results"]["s2"]["outcome"] == "unknown" and r1["results"]["s2"]["held"] is True
    assert r2["results"]["s1"]["outcome"] == "unknown" and r2["results"]["s1"]["held"] is False
    assert r3["state"] == "done"
    assert s.recover() == []                                        # idempotent


def test_a_corrupt_file_is_ignored(tmp_path):
    s = RunStore(tmp_path)
    (tmp_path / "bad.json").write_text("{not json", encoding="utf-8")
    s.save({"run_id": "ok", "state": "done"})
    assert s.load("bad") is None and s.recover() == []


def test_effects_are_decided_by_what_the_step_is():
    assert effect_of("think") == "none"
    for kind in ("jarvis",):
        assert effect_of(kind) == "non_idempotent"
    for ref in ("doc_tools:write_document", "learn_vault:save_note", "app_launcher:launch_app", "memo_reminder:add_reminder",
                "web_search:search_and_send", "x:delete_item"):
        assert effect_of("plugin", ref) == "non_idempotent", ref
    for ref in ("web_search:search", "doc_tools:read_document", "learn_vault:list_notes", "lifekit:get_weather", "x:status"):
        assert effect_of("plugin", ref) == "none", ref
