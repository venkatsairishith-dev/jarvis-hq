"""Long missions (plugin/plugins/desktop/missions.py) with fake agents on a temporary DBOS/SQLite store. Windows.
The restart case runs a real second Python process that is killed mid-step, then resumes in a new process."""
import json
import os
import subprocess
import sys
import textwrap
import time
from pathlib import Path

import pytest
from dbos import DBOS

from plugin.plugins.desktop import missions as M

ROOT = Path(__file__).resolve().parents[4]


@pytest.fixture
def eng(tmp_path):
    M.configure(tmp_path)
    M.E.lock_path = tmp_path / "pc_task.lock"
    M.E.budget_s = M.BUDGET_S
    M.E.on_cancel = lambda: None
    DBOS.launch()
    yield M.E
    DBOS.destroy(destroy_registry=False)


def ok_agent(log, tool):
    def fn(step):
        log.append((tool, step.get("app") or step.get("query") or step.get("text") or ""))
        if tool == "open_app":
            return {"ok": True, "verified": "verified", "opened": step.get("app")}
        if tool == "research":
            return {"ok": True, "summary": "three checked findings", "file": "D:\\out\\deck.pptx", "qa_passed": True}
        return {"ok": True}
    return fn


def run(goal):
    mid = M.start(goal)
    deadline = time.time() + 30
    while time.time() < deadline:
        r = M.load(mid)
        if r and r.get("status") not in ("queued", "running"):
            return r
        time.sleep(0.1)
    raise AssertionError(f"mission did not finish: {M.load(mid)}")


def test_research_deck_open_explain_completes_and_explains(eng):
    log = []
    eng.agents = {t: ok_agent(log, t) for t in ("research", "open_file", "open_app")}
    eng.plan_fn = lambda goal, ctx: [{"tool": "research", "query": "solar panels", "deliverable": "presentation"},
                                     {"tool": "open_file", "name": "deck.pptx"}]
    r = run("research solar panels, make slides, open them and explain")
    assert r["status"] == "completed" and [s["tool"] for s in r["steps"]] == ["research", "open_file"]
    assert "three checked findings" in r["explain"] and "deck.pptx" in r["explain"]


def test_replan_after_a_failed_check_then_success(eng):
    log, plans = [], []
    def open_app(step):
        log.append(step["app"])
        return {"ok": True, "verified": "verified" if step["app"] == "wordpad" else "not seen", "opened": step["app"]}
    eng.agents = {"open_app": open_app, "type_text": ok_agent(log, "type_text")}
    def plan(goal, ctx):
        plans.append(ctx)
        if not ctx:
            return [{"tool": "open_app", "app": "word"}, {"tool": "type_text", "app": "word", "text": "hi"}]
        return [{"tool": "open_app", "app": "wordpad"}, {"tool": "type_text", "app": "wordpad", "text": "hi"}]
    eng.plan_fn = plan
    r = run("write hi in a word processor")
    assert r["status"] == "completed" and r["replans"] == 1
    assert "failed" in plans[1] and log == ["word", "wordpad", ("type_text", "wordpad")]


def test_replans_are_bounded(eng):
    eng.agents = {"open_app": lambda s: {"ok": True, "verified": "not seen"}}
    eng.plan_fn = lambda goal, ctx: [{"tool": "open_app", "app": f"x{len(ctx)}"}]
    r = run("open something that never appears")
    assert r["status"] == "failed" and r["replans"] == M.MAX_REPLANS and len(r["steps"]) == M.MAX_REPLANS + 1


def test_loop_is_detected(eng):
    eng.agents = {"check_pc": lambda s: {"ok": True, "same": 1}}
    eng.plan_fn = lambda goal, ctx: [{"tool": "check_pc", "check": "disk"}] * 6
    r = run("keep checking the disk")
    assert r["status"] == "stopped" and "loop" in r["error"] and len(r["steps"]) == M.LOOP_REPEATS


def test_cancel_stops_before_the_next_step_and_stops_the_running_job(eng):
    stopped = []
    eng.on_cancel = lambda: stopped.append(True)
    def slow(step):
        time.sleep(0.6)
        return {"ok": True}
    eng.agents = {"check_pc": slow}
    eng.plan_fn = lambda goal, ctx: [{"tool": "check_pc", "check": c} for c in ("disk", "network", "memory", "cpu")]
    mid = M.start("check everything slowly")
    time.sleep(0.3)
    assert M.cancel(mid) and stopped == [True]
    deadline = time.time() + 10
    while M.load(mid)["status"] in ("queued", "running") and time.time() < deadline:
        time.sleep(0.1)
    r = M.load(mid)
    assert r["status"] == "cancelled" and len(r["steps"]) == 1


def test_time_budget(eng):
    eng.budget_s = 0.5
    eng.agents = {"check_pc": lambda s: (time.sleep(0.3), {"ok": True})[1]}
    eng.plan_fn = lambda goal, ctx: [{"tool": "check_pc", "check": c} for c in ("disk", "network", "memory", "cpu")]
    r = run("slow checks")
    assert r["status"] == "stopped" and "time budget" in r["error"] and len(r["steps"]) < 4


def test_a_done_effect_is_never_repeated(eng):
    calls = []
    eng.agents = {"type_text": lambda s: (calls.append(s["text"]), {"ok": True})[1]}
    mid = "m-test-effects"
    M._q("insert into missions (id, goal, status, created, updated, record) values (?,?,?,?,?,?)", (mid, "g", "x", 0, 0, "{}"))
    step = {"tool": "type_text", "app": "notepad", "text": "hello"}
    a = M._act(mid, step, 0)
    b = M._act(mid, step, 0)                       # the same effect again (e.g. a re-plan produced the same step)
    c = M._act(mid, step, 1)                       # a genuinely second occurrence is a new effect
    assert calls == ["hello", "hello"] and b.get("replayed") and not a.get("replayed") and not c.get("replayed")


def test_a_failed_effect_did_not_happen_so_a_replan_may_retry_it(eng):
    calls = []
    eng.agents = {"research": lambda s: (calls.append(1), {"ok": len(calls) > 1, "summary": "s" if len(calls) > 1 else "",
                                                            "file": "D:\\o\\d.pptx", "qa_passed": True, "error": "flaky"})[1]}
    eng.plan_fn = lambda goal, ctx: [{"tool": "research", "query": "x", "deliverable": "presentation"}]
    r = run("research x and make slides")
    assert r["status"] == "completed" and len(calls) == 2 and r["replans"] == 1


def test_an_interrupted_unsafe_step_is_not_repeated_but_a_safe_one_is(eng):
    calls = []
    eng.agents = {"type_text": lambda s: (calls.append("type"), {"ok": True})[1],
                  "open_app": lambda s: (calls.append("open"), {"ok": True, "verified": "verified"})[1]}
    mid = "m-test-interrupted"
    for tool, step in (("type_text", {"tool": "type_text", "app": "notepad", "text": "x"}), ("open_app", {"tool": "open_app", "app": "notepad"})):
        M._q("insert into effects (key, mission, tool, state, result, at) values (?,?,?,?,?,?)",
             (f"{mid}:{M._sig(step)}:0", mid, tool, "started", "{}", 0))
    t = M._act(mid, {"tool": "type_text", "app": "notepad", "text": "x"}, 0)
    o = M._act(mid, {"tool": "open_app", "app": "notepad"}, 0)
    assert t["interrupted"] and not t["ok"] and calls == ["open"] and o["ok"]


CHILD = textwrap.dedent("""
    import sys, time, json
    from pathlib import Path
    sys.path.insert(0, {root!r})
    from dbos import DBOS
    from plugin.plugins.desktop import missions as M
    d = Path({tmp!r})
    M.configure(d)
    M.E.lock_path = d / "pc_task.lock"
    log = d / "acts.jsonl"
    def agent(step):
        with log.open("a") as f:
            f.write(json.dumps(step) + "\\n")
        if step.get("check") == "slow" and {phase!r} == "first":
            time.sleep(60)                         # killed here, mid-step
        return {{"ok": True}}
    M.E.agents = {{"check_pc": agent, "type_text": lambda s: (log.open("a").write(json.dumps(s) + "\\n"), {{"ok": True}})[1]}}
    M.E.plan_fn = lambda goal, ctx: [{{"tool": "type_text", "app": "notepad", "text": "once"}},
                                     {{"tool": "check_pc", "check": "slow"}}, {{"tool": "check_pc", "check": "disk"}}]
    DBOS.launch()                                  # in the second process this resumes the unfinished mission
    if {phase!r} == "first":
        print(M.start("restart test"), flush=True)
        time.sleep(60)
    else:
        for _ in range(200):
            r = M.latest()
            if r and r.get("status") not in ("queued", "running"):
                break
            time.sleep(0.1)
        print(json.dumps(M.latest()), flush=True)
    DBOS.destroy()
""")


def test_resume_after_restart_skips_done_steps(tmp_path):
    py = sys.executable
    first = subprocess.Popen([py, "-c", CHILD.format(root=str(ROOT), tmp=str(tmp_path), phase="first")],
                             stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
    log = tmp_path / "acts.jsonl"
    deadline = time.time() + 60
    while time.time() < deadline and not (log.exists() and "slow" in log.read_text()):
        time.sleep(0.2)
    first.kill()                                   # a crash in the middle of step 2
    first.wait()
    second = subprocess.run([py, "-c", CHILD.format(root=str(ROOT), tmp=str(tmp_path), phase="second")],
                            capture_output=True, text=True, timeout=120)
    rec = json.loads(second.stdout.strip().splitlines()[-1])
    acts = [json.loads(x) for x in log.read_text().splitlines()]
    assert rec["status"] == "completed", rec
    assert [a.get("text") or a.get("check") for a in acts] == ["once", "slow", "slow", "disk"]   # typing not repeated
