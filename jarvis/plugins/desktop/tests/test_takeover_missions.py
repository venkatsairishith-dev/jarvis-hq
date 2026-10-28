"""D79: a mission whose step met a wall the owner did not clear in time is PAUSED durably (needs_owner, takeover) and continued with the same id; the record travels to the step.
Also the plan rule, the verifier, the voice hook and the HUD words. Fakes: DBOS on a temp SQLite, fake agents."""
import json
import time

import pytest
from dbos import DBOS

from jarvis.plugins.desktop import coordinator as coord
from jarvis.plugins.desktop import decide, missions as M, taskvoice

REC = {"id": "tk-1", "target": "pc", "kind": "captcha", "host": "decks.test", "status": "paused", "reasks": 0, "site": "decks.test", "milestones": ["open", "start"]}


@pytest.fixture
def eng(tmp_path):
    M.configure(tmp_path)
    M.E.lock_path = tmp_path / "pc_task.lock"
    M.E.budget_s = M.BUDGET_S
    M.E.on_cancel = lambda: None
    DBOS.launch()
    yield M.E
    M.E.on_takeover_resume = staticmethod(lambda rec: None)
    DBOS.destroy(destroy_registry=False)


def wait(mid, states=("queued", "running")):
    end = time.time() + 30
    while time.time() < end:
        r = M.load(mid)
        if r and r.get("status") not in states:
            return r
        time.sleep(0.1)
    raise AssertionError(M.load(mid))


def deck_ok():
    return {"ok": True, "summary": "Done", "site": "decks.test", "facts": ["pptx file of 9000 bytes with 3 slides"], "files": [{"name": "d.pptx", "slides": 3}], "verified_files": 1}


def test_a_mission_paused_at_a_wall_is_needs_owner_and_continues_with_the_same_id_and_the_record(eng):
    seen, resumed = [], []

    def web_task(step):
        seen.append({k: v for k, v in step.items() if k.startswith("_")})
        if not step.get("_takeover"):
            return {"ok": False, "summary": "Waiting for you", "error": "you did not answer within 30 minutes", "needs_owner": {"kind": "takeover", "site": "decks.test", "detail": "x", "takeover": REC}}
        return deck_ok()
    eng.agents = {"web_task": web_task}
    eng.plan_fn = lambda goal, ctx: [{"tool": "web_task", "topic": "tea", "slides": 3}]
    eng.on_takeover_resume = resumed.append
    mid = M.start("make a deck about tea")
    r = wait(mid)
    assert r["status"] == "needs_owner" and r["resume"]["kind"] == "takeover" and r["resume"]["takeover"] == REC
    assert r["replans"] == 0 and "captcha on decks.test" in r["error"] and "continue the mission" in r["explain"]
    assert M.resume(mid, "")["ok"] is True, "a takeover pause continues with no special word"
    r2 = wait(mid)
    assert r2["status"] == "completed" and r2["id"] == mid and seen[1]["_takeover"] == REC and resumed == [REC]
    assert [s["tool"] for s in r2["steps"] if not s.get("held")] == ["web_task"], "one step run counted; the held one is not a failed step"


def test_resume_accepts_continue_or_done_for_a_takeover_and_nothing_for_other_waits(eng):
    eng.agents = {"web_task": lambda s: {"ok": False, "needs_owner": {"kind": "takeover", "takeover": REC}} if not s.get("_takeover") else deck_ok()}
    eng.plan_fn = lambda goal, ctx: [{"tool": "web_task", "topic": "tea", "slides": 3}]
    mid = M.start("deck")
    wait(mid)
    out = M.resume(mid, "continue")
    assert out["ok"] and out["from"] == "needs_owner"
    assert wait(mid)["status"] == "completed"


def test_a_cancelled_mission_cannot_be_continued(eng):
    eng.agents = {"web_task": lambda s: {"ok": False, "needs_owner": {"kind": "takeover", "takeover": REC}}}
    eng.plan_fn = lambda goal, ctx: [{"tool": "web_task", "topic": "tea", "slides": 3}]
    mid = M.start("deck")
    assert wait(mid)["status"] == "needs_owner"
    assert M.cancel(mid) is True
    assert M.resume(mid, "continue")["ok"] is False


def test_a_pause_is_not_replanned_and_other_needs_owner_waits_keep_their_old_rules(eng):
    eng.agents = {"web_task": lambda s: {"ok": False, "state": "waiting_for_permission", "approval_id": "a1", "how": "approve"}}
    eng.plan_fn = lambda goal, ctx: [{"tool": "web_task", "topic": "tea", "slides": 3}]
    mid = M.start("deck")
    assert wait(mid)["status"] == "waiting_for_permission"
    assert M.takeover_of({"ok": False}) is None and M.takeover_of({"takeover": {"id": ""}}) is None and M.takeover_of({"takeover": REC}) == REC


# ------------------------------------------------------------------------------------------------------------------------------ the plan and the verifier
def plan(text):
    return decide.vm2_rule(text, [])


@pytest.mark.parametrize("text", ["download https://files.example.test/setup.exe", "on VM2 research https://shop.example", "download the installer for VLC"])
def test_a_vm2_task_is_planned_by_rule_as_one_step_in_the_owners_words(text):
    p = plan(text)
    assert p is not None and p.status == "ok" and [s["tool"] for s in p.steps] == ["vm2_task"] and p.steps[0]["query"] == text and p.model == "rule"


@pytest.mark.parametrize("text", ["make a 3 slide deck about tea", "open youtube", "Install 7-Zip.", "download notes.txt", "on my pc download https://x.example/a.zip"])
def test_everything_else_is_not_a_vm2_task(text):
    assert plan(text) is None


def test_the_whole_planner_routes_a_vm2_request_before_any_model():
    p = decide.decide("download https://files.example.test/setup.exe", [], context="")
    assert p is not None and [s["tool"] for s in p.steps] == ["vm2_task"]


def test_vm2_task_passes_the_verifier_only_with_facts_and_a_pause_is_not_a_pass():
    step = {"tool": "vm2_task", "query": "x"}
    ok, facts, _u = coord.verify(step, {"ok": True, "facts": ["the lab agent finished on VM2", "1 file(s) copied"]})[:3]
    assert ok is True and "copied" in facts
    bad = coord.verify(step, {"ok": False, "needs_owner": {"kind": "takeover", "detail": "waiting"}, "error": "waiting"})
    assert bad[0] is False
    assert coord.verify(step, {"ok": False, "stopped": True})[0] is False
    assert coord.verify(step, {"ok": True})[0] is False


# ------------------------------------------------------------------------------------------------------------------------------ voice
def test_the_quick_command_hook_turns_done_into_a_continue_only_while_a_takeover_waits(tmp_path, monkeypatch):
    from jarvis.plugins._shared import takeover as TK
    monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path))
    assert taskvoice.handle("done", state_path=tmp_path / "s.json", control_path=tmp_path / "c.json") is None
    TK.State().begin(TK.make_request("pc", "captcha", "x.example"))
    got = taskvoice.handle("Done", state_path=tmp_path / "s.json", control_path=tmp_path / "c.json")
    assert got and got["handled"] and "Carrying on" in got["reply"] and TK.Signal().take() is True
    assert taskvoice.handle("approve it", state_path=tmp_path / "s.json", control_path=tmp_path / "c.json") is None
    assert taskvoice.handle("continue, my pin is 1234", state_path=tmp_path / "s.json", control_path=tmp_path / "c.json") is None


def test_the_task_state_has_a_needs_owner_line_in_fixed_words(tmp_path):
    from jarvis.plugins.desktop import taskstate
    ts = taskstate.TaskState(tmp_path / "s.json", tmp_path / "c.json")
    ts.begin("t1")
    ts.update(state="needs_owner", phase="wait", why="takeover")
    d = taskstate.read(tmp_path / "s.json")
    assert d["state"] == "needs_owner" and d["why"] == "takeover" and "needs you at the screen" in d["line"] and taskstate.live(d)
    assert "CAPTCHA" in taskstate.answer("why", d) or "step needs you" in taskstate.answer("why", d)
    import re
    assert re.match(r"^[A-Za-z0-9 .,()'-]{1,140}$", d["line"])
