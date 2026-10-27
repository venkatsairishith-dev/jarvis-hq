"""D76: open-ended tasks as REAL missions (missions.py on a temporary DBOS/SQLite store): durable, resumable, stoppable, re-planned once, with the hard budget kept across steps.
The milestones run on the D64 rig (fake Chrome over real HTML, the real kernel and node code)."""
import time

import pytest
from dbos import DBOS

from jarvis.plugins.desktop import missions as M
from jarvis.plugins.desktop import taskrun as TR
from jarvis.plugins.desktop.tests.taskharness import TaskHarness


class Rig:
    def __init__(self, h: TaskHarness):
        self.h = h
        self.over: dict = {}
        self.after_step = None
        self.calls: list = []

    def agent(self, step):
        mid = M.current_mission()
        self.calls.append(step["id"])
        goal = (M.load(mid) or {}).get("goal", "")
        res = TR.TaskRunner(self.h.hands(**self.over), self.h.boards, self.h.cfg).run_step(step, mid, goal)
        if self.after_step:
            self.after_step(mid, step, res)
        return res

    def run(self, goal: str, timeout: float = 90.0) -> dict:
        mid = M.start(goal, "task")
        deadline = time.time() + timeout
        while time.time() < deadline:
            r = M.load(mid)
            if r and r.get("status") not in ("queued", "running"):
                return r
            time.sleep(0.1)
        raise AssertionError(f"mission did not finish: {M.load(mid)}")

    def wait(self, mid: str, timeout: float = 90.0) -> dict:
        deadline = time.time() + timeout
        time.sleep(0.2)
        while time.time() < deadline:
            r = M.load(mid)
            if r and r.get("status") not in ("queued", "running"):
                return r
            time.sleep(0.1)
        raise AssertionError(f"mission did not finish: {M.load(mid)}")


@pytest.fixture
def rig(tmp_path, monkeypatch):
    h = TaskHarness(tmp_path, monkeypatch)
    M.configure(tmp_path / "missions")
    M.E.lock_path = tmp_path / "pc_task.lock"
    M.E.budget_s = M.BUDGET_S
    M.E.on_cancel = lambda: None
    r = Rig(h)
    M.E.agents = {"task_step": r.agent}
    M.E.task_plan_fn = lambda goal, ctx: TR.mission_plan(goal, ctx, ask_online=None, cfg=h.cfg)
    DBOS.launch()
    yield r
    DBOS.destroy(destroy_registry=False)
    h.close()


def test_a_task_is_a_mission_whose_answer_is_the_verified_facts(rig):
    r = rig.run("search searchtube for python decorators tutorial, open the best result and tell me its title and length")
    assert r["status"] == "completed" and r["kind"] == "task" and [s["tool"] for s in r["steps"]] == ["task_step"] * 3
    assert r["explain"].startswith("Done.") and "Python decorators tutorial: explained simply" in r["explain"] and "12:34" in r["explain"]
    assert all(s["passed"] for s in r["steps"])


def test_a_wall_hands_the_step_to_the_owner_and_redo_continues_without_repeating_done_steps(rig):
    h = rig.h
    good = h.web.page("college.test", "/exams.html")
    h.web.put("college.test", "/exams.html", "<html><head><title>Security check</title></head><body><h1>Please verify you are human</h1><p>We need to check that you are not a robot.</p></body></html>")
    h.answers = ["done", "done", "done"]                                   # he says "done" but the check is still on the page
    r = rig.run("go to college.test and find the exam timetable")
    assert r["status"] == "needs_owner" and [s["passed"] for s in r["steps"]] == [True, False]
    assert "CAPTCHA" in r["error"] or "robot" in r["error"] or "human" in r["error"]
    assert "Not finished" in r["explain"] or "I need you" in r["explain"] or "redo" in r["explain"]
    h.web.put("college.test", "/exams.html", good)                         # he cleared it by hand: the live tab shows the real page again
    h.world.open("https://college.test/exams.html")
    rig.calls.clear()
    assert M.resume(r["id"], "redo")["ok"]
    r2 = rig.wait(r["id"])
    assert r2["status"] == "completed", r2
    assert rig.calls[0] == "m2" and "m1" not in rig.calls                       # the first milestone (opening the site) was not done again
    assert "Exam Timetable" in r2["explain"]


def test_a_failed_milestone_is_replanned_once_and_then_reported_honestly(rig):
    r = rig.run("go to shop-a.test and find the laptop sleeve")
    assert r["status"] == "failed" and r["replans"] == 1 and "1 re-plan" in r["error"]
    assert r["plans"][1][0]["mode"] == "search" or r["plans"][1][0].get("id", "").endswith("s")      # the second try went in through the site's search
    text = r["explain"]
    assert text.startswith("What I did and checked:") and "opened shop-a.test" in text
    assert "Not done:" in text and "could not find it" in text
    assert "laptop sleeve" not in text.split("Not done:")[0]                                       # nothing about the sleeve is claimed


def test_when_the_page_is_not_found_and_no_search_exists_the_owner_is_asked_where_to_look(rig):
    r = rig.run("go to college.test and find the graduation ceremony")
    assert r["status"] == "needs_clarification" and "Where should I look" in r["question"]
    assert r["steps"][0]["passed"] and not r["steps"][-1]["passed"]


def test_the_hard_budget_stops_the_task_and_the_report_keeps_what_it_has(tmp_path, monkeypatch):
    h = TaskHarness(tmp_path, monkeypatch, limits={"pages": 3})
    M.configure(tmp_path / "missions")
    M.E.lock_path = tmp_path / "pc_task.lock"
    M.E.on_cancel = lambda: None
    r0 = Rig(h)
    M.E.agents = {"task_step": r0.agent}
    M.E.task_plan_fn = lambda goal, ctx: TR.mission_plan(goal, ctx, ask_online=None, cfg=h.cfg)
    DBOS.launch()
    try:
        r = r0.run("compare iPhone 15 and Pixel 8 on shop-a.test and shop-b.test and put it in a spreadsheet")
        assert r["status"] == "stopped" and "budget" in r["error"] and r["replans"] == 0          # not re-planned: a budget is not a reason to try another way
        assert "What I did and checked:" in r["explain"] and "shop-a.test" in r["explain"] and "budget" in r["explain"]
    finally:
        DBOS.destroy(destroy_registry=False)
        h.close()


def test_stop_ends_the_task_between_milestones_and_says_what_was_done(rig):
    rig.after_step = lambda mid, step, res: M.cancel(mid) if step["mode"] == "search" else None
    r = rig.run("search searchtube for python decorators tutorial, open the best result and tell me its title and length")
    assert r["status"] == "cancelled" and len(r["steps"]) == 1
    assert "Stopped, as you asked." in r["explain"] and "searched search.test" in r["explain"]
