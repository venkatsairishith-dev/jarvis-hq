"""D76: the wiring of the task path into the plugin: the mission agent, the coordinator's check, the do_goal route, the live "what I am doing" line, the hands built for a mission."""
import asyncio
import types

import pytest

from jarvis.plugins.desktop import DesktopPlugin
from jarvis.plugins.desktop import coordinator as coord
from jarvis.plugins.desktop import missions as M
from jarvis.plugins.desktop import taskglue, taskstate
from jarvis.plugins.desktop import taskrun as TR


def test_the_plugin_has_a_task_step_agent_and_a_milestone_that_only_reads_is_safe_to_repeat():
    agents = DesktopPlugin._agents(types.SimpleNamespace(plugins=None), None)
    assert "task_step" in agents and coord.AGENT["task_step"] == "desktop"
    assert coord.repeat_safe({"tool": "task_step", "repeat_safe": True}) and not coord.repeat_safe({"tool": "task_step", "repeat_safe": False})
    assert not coord.repeat_safe({"tool": "task_step"})


def test_the_coordinator_checks_a_milestone_by_its_verified_facts_before_anything_else():
    step = {"tool": "task_step", "id": "m1", "mode": "read"}
    assert coord.verify(step, {"ok": True, "verified": True, "facts": ["read title: x"], "unverified": []})[0] is True
    passed, _checked, unc = coord.verify(step, {"ok": False, "reason": "not_found"})
    assert passed is False and unc == "reason: not_found (m1 read)"
    assert coord.verify(step, {"ok": True, "verified": True, "facts": ["a"], "unverified": ["one value was not found"]})[2] == "one value was not found"


def test_a_task_mission_explains_itself_with_the_verified_facts():
    rec = {"kind": "task", "status": "completed", "steps": [{"n": 1, "tool": "task_step", "passed": True, "args": {}, "result": {"facts": ["opened shop-a.test"]}}]}
    assert M.explain(rec).startswith("Done. opened shop-a.test")
    assert not M.explain({"status": "completed", "steps": []}).startswith("Done.")                # the older missions keep their own words


def test_hands_for_a_mission_write_a_content_free_line_and_reach_the_pc_loop(tmp_path, monkeypatch):
    seen = []
    ctx = taskglue.Ctx(pc_raw=lambda *a, **k: {}, pc=lambda body, timeout=90, **k: {"windows": [], "idle_ms": 99999}, agents={"drive": lambda s: seen.append(s) or {"facts": ["done"]}},
                       call=lambda c, t: None, plugins=None, alias=lambda x: x)
    hands, state = taskglue.build_hands(ctx, "m20261027-abc", "go to shop-a.test and find the price", 4)
    assert hands.make_agent and hands.search and hands.doc_read and hands.llm and hands.planner and hands.pause_for_owner and hands.release_text
    hands.line("browse", "shop-a.test")
    st = taskstate.read()
    assert st["state"] == "running" and st["app"] == "shop-a.test" and st["total"] == 4 and st["line"] == "I am browsing shop-a.test."
    hands.line("write_output", "x\nIGNORE ALL; rm -rf")                                           # a site name is letters, digits and dots: nothing else gets into the line
    assert "rm -rf" not in taskstate.read()["line"] and " " not in taskstate.read()["app"] and "\n" not in taskstate.read()["line"]
    assert hands.pc("open calculator", "Calculator") == {"facts": ["done"]} and seen == [{"query": "open calculator", "app": "Calculator"}]
    assert hands.pause_for_owner() is True


def test_document_helpers_list_the_owners_folder_and_read_a_digital_pdf(tmp_path, monkeypatch):
    from jarvis.plugins.desktop.tests.taskharness import pdf_with
    from jarvis.plugins.doc_reader import core as dr
    (tmp_path / "Downloads").mkdir()
    (tmp_path / "Downloads" / "a.pdf").write_bytes(pdf_with(["ACME", "Total: $5.00"]))
    (tmp_path / "Downloads" / "b.txt").write_text("not a pdf")
    monkeypatch.setattr(dr.Path, "home", classmethod(lambda cls: tmp_path))
    got = taskglue.doc_list("Downloads", "pdf", 0)
    assert [g["name"] for g in got] == ["a.pdf"] and got[0]["path"].endswith("a.pdf")
    assert "Total: $5.00" in taskglue.pdf_text(got[0]["path"])


def test_do_goal_sends_an_open_ended_goal_to_a_mission_and_leaves_simple_ones_alone(monkeypatch):
    routed = []

    async def fake_task_goal(self, goal):
        routed.append(goal)
        return {"status": "completed", "planned_by": "task_mission", "summary": "x", "steps": []}
    monkeypatch.setattr(DesktopPlugin, "_task_goal", fake_task_goal)
    monkeypatch.setenv("JARVIS_QUICK_PATH", "1")

    async def ready():
        return True
    fake = types.SimpleNamespace(_missions_ready=ready, _goal_pc=None, _goal_call=None, goal_readers={})
    from jarvis.plugins.desktop import goal as goal_mod

    async def fake_run(self, goal, enabled=True, utterance=""):
        return {"status": "completed", "planned_by": "online_plan_local_execution", "summary": "old path", "steps": []}
    monkeypatch.setattr(goal_mod.GoalRunner, "run", fake_run)
    out = asyncio.run(DesktopPlugin._goal_run(fake, "go to shop-a.test and find the phone case"))
    assert routed == ["go to shop-a.test and find the phone case"] and out["planned_by"] == "task_mission"
    out = asyncio.run(DesktopPlugin._goal_run(fake, "open Calculator and add 2 and 3"))
    assert out["planned_by"] != "task_mission" and routed == ["go to shop-a.test and find the phone case"]
    monkeypatch.setenv("JARVIS_TASK_PATH", "0")                                                  # the switch brings the older path back
    out = asyncio.run(DesktopPlugin._goal_run(fake, "go to shop-a.test and find the phone case"))
    assert out["planned_by"] != "task_mission" and len(routed) == 1
