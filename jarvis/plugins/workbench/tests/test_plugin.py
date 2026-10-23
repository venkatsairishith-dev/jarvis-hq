"""The plugin wrapper: execution needs the owner's confirmation and a reachable JARVIS with the kill switch off, goes only
through jarvis_bridge / main_agent, and the planner can never put the execution entries in a plan."""
from __future__ import annotations

import asyncio
import logging
from pathlib import Path

import pytest

from jarvis.pluginapi.log import BraceLogger
from jarvis.plugins import workbench as wb
from jarvis.plugins.main_agent import planner
from jarvis.plugins.workbench import prompts, service
from jarvis.pluginapi import Err, Ok, SdkError


class Bus:
    def __init__(self, status=None, fail=None):
        self.calls: list[tuple[str, dict]] = []
        self.status = status if status is not None else {"ready": True, "busy": False, "message": "JARVIS is ready."}
        self.fail = fail or set()

    async def call_entry_json(self, ref, args, timeout=0):
        self.calls.append((ref, dict(args)))
        if ref in self.fail:
            return Err(SdkError("unreachable"))
        if ref == "jarvis_bridge:jarvis_status":
            return Ok(self.status)
        if ref == "jarvis_bridge:delegate_task":
            return Ok({"command_id": "cmd-7", "state": "queued"})
        if ref == "main_agent:run_task":
            return Ok({"run_id": "run-3"})
        return Ok({})

    async def list_ids(self, enabled=None):
        return Ok(["research", "doc_tools", "jarvis_bridge", "workbench"])

    def sent(self, ref):
        return [a for r, a in self.calls if r == ref]


def make(bus):
    p = object.__new__(wb.WorkbenchPlugin)
    p.__class__ = type("P", (wb.WorkbenchPlugin,), {"plugins": bus})
    p.logger = BraceLogger("test.workbench")
    return p


def run(coro):
    return asyncio.run(coro)


def value(res):
    assert isinstance(res, Ok), res
    return res.value


def _prompt():
    return service.generate_prompt({"objective": "Write a checklist for moving office", "kind": "single"})["id"]


def test_execute_needs_confirmation():
    bus = Bus()
    pid = _prompt()
    res = run(make(bus).execute_prompt(prompt_id=pid, confirm=False))
    assert isinstance(res, Err) and bus.calls == []
    res = run(make(bus).execute_prompt(prompt_id=pid, confirm="true"))       # only the boolean true counts
    assert isinstance(res, Err) and bus.calls == []


def test_execute_goes_through_the_authorised_path_and_stays_submitted():
    bus = Bus()
    pid = _prompt()
    out = value(run(make(bus).execute_prompt(prompt_id=pid, via="jarvis", confirm=True)))
    assert out["state"] == "submitted" and out["ref"] == "cmd-7"
    sent = bus.sent("jarvis_bridge:delegate_task")
    assert len(sent) == 1 and len(sent[0]["task"]) <= 2000 and sent[0]["task"].startswith("Objective:")
    got = service.get_prompt(pid)
    assert got["executions"][-1]["state"] == "submitted" and got["executions"][-1]["verified"] is False
    out = value(run(make(bus).execute_prompt(prompt_id=pid, via="main_agent", confirm=True)))
    assert out["ref"] == "run-3" and bus.sent("main_agent:run_task")[0]["mode"] == "work"


@pytest.mark.parametrize("status,fail", [({"ready": False, "message": "JARVIS's kill switch is on."}, None),
                                          (None, {"jarvis_bridge:jarvis_status"})])
def test_kill_switch_or_unknown_state_refuses(status, fail):
    bus = Bus(status=status, fail=fail)
    pid = _prompt()
    out = value(run(make(bus).execute_prompt(prompt_id=pid, confirm=True)))
    assert out["state"] == "refused" and "Nothing was run" in out["message"] or "nothing was run" in out["message"]
    assert not bus.sent("jarvis_bridge:delegate_task") and not bus.sent("main_agent:run_task")
    assert service.get_prompt(pid)["executions"][-1]["state"] == "refused"


def test_learned_workflow_runs_only_validated_and_through_jarvis():
    from .test_learning import CASES, LEDGER_WF, TASK
    out = service.learn_task(TASK, CASES, LEDGER_WF)
    bus = Bus()
    assert isinstance(run(make(bus).run_learned_workflow(name=out["workflow"], input_note="x", confirm=False)), Err)
    got = value(run(make(bus).run_learned_workflow(name=out["workflow"], input_note="the March ledger", confirm=True)))
    assert got["state"] == "submitted" and got["command_id"] == "cmd-7"
    assert "validated workflow" in bus.sent("jarvis_bridge:delegate_task")[0]["task"]
    assert isinstance(run(make(bus).run_learned_workflow(name="never-learned", input_note="x", confirm=True)), Err)
    off = Bus(status={"ready": False, "message": "JARVIS's kill switch is on."})
    got = value(run(make(off).run_learned_workflow(name=out["workflow"], input_note="x", confirm=True)))
    assert got["state"] == "refused" and not off.sent("jarvis_bridge:delegate_task")


def test_entries_work_through_the_wrapper(alpha):
    bus = Bus()
    p = make(bus)
    created = value(run(p.create_project(name="Wrapper test", client="Priya", owner_names=["Sam Rao"])))
    pid = created["project_id"]
    assert value(run(p.add_project_files(project_id=pid, paths=alpha)))["added"]
    analysed = value(run(p.analyze_project(project_id=pid, use_local_model=True)))
    assert analysed["stats"]["items"] > 5
    built = value(run(p.build_deliverables(project_id=pid)))             # no local model here: blocked, not "done"
    assert built["verified"] == 0 and {d["verdict"] for d in built["deliverables"]} == {"not made"}
    assert isinstance(run(p.project_status(project_id="no-such")), Err)
    assert isinstance(run(p.set_provider_policy(project_id=pid, mode="external_allowed", providers=["x"])), Err)


def test_planner_never_offers_execution_entries():
    listing = [{"id": "workbench", "status": "running", "entries": [
        {"id": e, "description": "d"} for e in ("generate_prompt", "execute_prompt", "run_learned_workflow",
                                                 "set_provider_policy", "analyze_project")]}]
    caps = planner.build_catalogue(listing, browser_ready=False, jarvis_ready=False, computer_ready=False,
                                   allow_computer=False, mode="work")
    refs = {c.ref for c in caps}
    assert "workbench:generate_prompt" in refs and "workbench:analyze_project" in refs
    assert not refs & {"workbench:execute_prompt", "workbench:run_learned_workflow", "workbench:set_provider_policy"}
    raw = ('{"steps": [{"id": "s1", "agent": "plugin", "plugin": "workbench:execute_prompt", "goal": "run it", '
           '"args": {"prompt_id": "P-x-abcdef", "confirm": true}}]}')
    with pytest.raises(planner.PlanError, match="not available"):
        planner.parse_plan(raw, caps, 5)
    assert set(wb.EXEC_ENTRIES) == {r.split(":")[1] for r in planner.PLANNER_EXCLUDED_ENTRIES}


def test_manifest_exists():
    root = Path(wb.__file__).resolve().parent
    text = (root / "plugin.toml").read_text(encoding="utf-8")
    assert 'id = "workbench"' in text and 'entry = "jarvis.plugins.workbench:WorkbenchPlugin"' in text
    assert prompts.EXEC_LIMIT == 2000
