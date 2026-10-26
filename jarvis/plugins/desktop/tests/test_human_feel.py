"""D65: the human feel (waits by condition, the owner comes first, a what-I-am-doing line) and the last two places the final-answer guard is wired (goal.py, quick.py)."""
from __future__ import annotations

import json

import pytest

from jarvis.plugins._shared import final_answer
from jarvis.plugins.desktop import goal as goal_mod, humanfeel as H, quick as quick_mod, taskstate as T


class Clock:
    def __init__(self):
        self.t = 0.0

    def now(self):
        return self.t

    def sleep(self, s):
        self.t += s


def test_waiting_is_by_condition_not_by_a_fixed_sleep():
    c = Clock()
    got, waited = H.wait_for(lambda: c.t >= 1.5 and "here", timeout=10, clock=c.now, sleep=c.sleep)
    assert got == "here" and 1.5 <= waited < 3.0, "it looked again soon and stopped when the thing was there"
    c = Clock()
    got, waited = H.wait_for(lambda: False, timeout=5, clock=c.now, sleep=c.sleep)
    assert not got and waited == pytest.approx(5.0, abs=0.01), "never longer than the budget"
    polls = []
    c = Clock()
    H.wait_for(lambda: polls.append(c.t) or False, timeout=8, clock=c.now, sleep=c.sleep)
    gaps = [b - a for a, b in zip(polls, polls[1:])][:-1]            # (the last gap is cut by the budget)
    assert gaps[0] < gaps[-1] <= H.MAX_POLL_S + 1e-9, "soon at first, then less often"
    c = Clock()
    assert not H.wait_for(lambda: False, timeout=50, clock=c.now, sleep=c.sleep, stop=lambda: c.t > 1)[0] and c.t < 5, "Stop ends a wait"


def test_the_pause_for_the_owner_says_so_resumes_when_he_is_still_and_gives_up_when_he_never_is():
    c = Clock()
    said = []
    w = H.OwnerWatch(c.now)
    w.acted()
    readings = iter([H.OwnerReading(idle_ms=100), H.OwnerReading(idle_ms=800), H.OwnerReading(idle_ms=4000)])
    assert H.wait_for_owner(lambda: next(readings), w, say=said.append, clock=c.now, sleep=c.sleep) == "resumed"
    assert said == ["I will wait until you are done."] and c.t == 2.0
    c = Clock()
    assert H.wait_for_owner(lambda: H.OwnerReading(idle_ms=0), H.OwnerWatch(c.now), say=lambda t: None, clock=c.now, sleep=c.sleep, max_s=30) == "gave_up"
    c = Clock()
    assert H.wait_for_owner(lambda: H.OwnerReading(idle_ms=0), H.OwnerWatch(c.now), say=lambda t: None, clock=c.now, sleep=c.sleep, stop=lambda: True) == "stopped"


def test_two_readings_with_nothing_of_ours_between_them_see_the_owner():
    assert H.OwnerWatch.during(H.OwnerReading(idle_ms=100), H.OwnerReading(idle_ms=0), 0.35)
    assert not H.OwnerWatch.during(H.OwnerReading(idle_ms=100), H.OwnerReading(idle_ms=450), 0.35), "the idle time simply grew: nobody touched anything"
    assert not H.OwnerWatch.during(H.OwnerReading(idle_ms=100), H.OwnerReading(idle_ms=None), 0.35)


def test_the_hud_line_says_what_is_being_done_in_fixed_words_only(tmp_path):
    ts = T.TaskState(tmp_path / "s.json", tmp_path / "c.json")
    ts.begin("t1", "notepad")
    ts.update(act="type", step=2)
    raw = json.loads((tmp_path / "s.json").read_text())
    assert raw["line"] == "I am typing into notepad." and raw["state"] == "running"
    ts.update(state="paused_owner")
    assert json.loads((tmp_path / "s.json").read_text())["line"].startswith("I am waiting because you are using the PC")
    ts.end("done")
    assert json.loads((tmp_path / "s.json").read_text())["line"] == "", "no line once the task is over"


def test_the_status_tool_carries_the_line_and_nothing_else_of_the_state_file(tmp_path):
    from jarvis.bridge import face_tools as F
    ts = T.TaskState(tmp_path / "s.json", tmp_path / "c.json")
    ts.begin("t1", "excel")
    ts.update(act="click", step=1, problem="dialog")
    now = json.loads((tmp_path / "s.json").read_text())["updated"]
    got = F.read_pc_line(tmp_path / "s.json", now=now + 1)
    assert got == {"pc_line": "I hit a problem (an unexpected dialog came up) and I am working around it in excel."} or got.get("pc_line", "").startswith("I am")
    assert set(got) == {"pc_line"}
    assert F.read_pc_line(tmp_path / "s.json", now=now + 500) == {}, "a task that stopped refreshing it"
    assert F.read_pc_line(tmp_path / "nope.json") == {}
    (tmp_path / "bad.json").write_text(json.dumps({"state": "running", "updated": now, "line": "<script>x</script> C:\\Users\\me\\secret.docx"}))
    assert F.read_pc_line(tmp_path / "bad.json", now=now + 1) == {}


def test_goal_and_quick_results_go_through_the_final_answer_guard(monkeypatch):
    seen = []
    real = final_answer.guard
    monkeypatch.setattr(final_answer, "guard", lambda *a, **k: seen.append(a[1]) or real(*a, **k))
    runner = goal_mod.GoalRunner(goal_mod.Hands(run_pc=None, call=None, readers={}))
    steps = [{"n": 1, "id": "s1", "executor": "pc", "status": "completed", "result": "", "verified": True, "goal": "open notepad"}]
    out = runner._result(steps, "online", "Planned.", None)
    assert out["status"] == "completed" and seen == ["completed"]
    assert "final_answer.guard" in open(quick_mod.__file__, encoding="utf-8").read()
    assert "final_answer.guard" in open(goal_mod.__file__, encoding="utf-8").read()


def test_an_overclaim_in_a_goal_summary_is_replaced_by_the_verified_facts():
    runner = goal_mod.GoalRunner(goal_mod.Hands(run_pc=None, call=None, readers={}))
    steps = [{"n": 1, "id": "s1", "executor": "pc", "status": "completed", "result": "", "verified": True, "goal": "open notepad"},
             {"n": 2, "id": "s2", "executor": "pc", "status": "failed", "result": "the file was not saved", "verified": False, "goal": "save the note"}]
    out = runner._result(steps, "online", "Planned.", None)
    assert out["status"] == "failed" and out["summary"].startswith("Planned. I could not finish"), out["summary"]
