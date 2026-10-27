"""D79: the `takeover` capability of the reliability gate. The goals are data judged on the laptop; here we check that they mean what they say against the real routing rule and the
real fake VM2, so a goal cannot pass or fail for a reason the routing rule never had."""
import pytest

from jarvis.evals.reliability import model
from jarvis.plugins._shared import vm2, vm2_routing

CAT = model.load()
CAP = CAT.capabilities["takeover"]
ROUTING = vm2.DEFAULT_ROUTING


def test_the_capability_has_at_least_ten_goals_and_the_catalogue_is_sound():
    assert len(CAP.goals) >= 10 and model.validate(CAT) == []
    assert {g.kind for g in CAP.goals} >= {"ask", "pin", "act"}


@pytest.mark.parametrize("goal", [g for g in CAP.goals if g.id.startswith("tk-") and g.kind == "ask"], ids=lambda g: g.id)
def test_each_routing_goal_is_a_request_the_rule_sends_to_vm2_and_names_the_folder_it_must_not_fill(goal):
    ctx = model.make_context("/w", "/", "/out", goal.id)
    g = model.render(goal, ctx)
    route = vm2_routing.classify(g.text, ROUTING)
    assert route.target == "vm2", (g.id, route.reason)
    assert [f for f in g.forbid if f["check"] == "count_files" and f["severity"] == 1 and f["dir"].startswith("/out/downloads/")]
    assert [v for v in g.verify if v["check"] == "ask_form"]


def test_the_pc_goal_stays_on_the_pc_and_the_status_goals_are_not_vm2_tasks():
    by = {g.id: g for g in CAP.goals}
    ctx = model.make_context("/w", "/", "/out", "x")
    assert vm2_routing.classify(model.render(by["tk-pc-explicit-stays-pc"], ctx).text, ROUTING).target == "pc"
    for gid in ("tk-vm2-status-words", "tk-done-with-nothing-waiting", "tk-viewer-password-never-typed", "tk-grant-vm2-needs-the-pin"):
        assert gid in by


def test_a_routing_goal_run_against_the_fake_vm2_that_is_down_finds_no_file_and_no_task_sent(tmp_path):
    from jarvis.evals.reliability.fake_vm2 import FakeKernel, FakeLab
    lab = FakeLab()
    url = lab.serve()
    lab.close()                                        # VM2 is down
    cfg = vm2.load_config(None)
    cfg.enabled, cfg.control_url, cfg.probe_timeout_s = True, url, 0.5
    kernel = FakeKernel(lab)
    ctx = model.make_context(str(tmp_path / "w"), "/", str(tmp_path / "out"), "seed")
    for g in (model.render(x, ctx) for x in CAP.goals if x.kind == "ask"):
        r = vm2_routing.resolve(g.text, cfg, vm2.Vm2Port(cfg))
        assert r.blocked and r.ask and kernel.calls == []
        assert not (tmp_path / "out" / "downloads").exists()
