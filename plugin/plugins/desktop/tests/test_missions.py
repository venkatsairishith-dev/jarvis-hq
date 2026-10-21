"""Long missions (plugin/plugins/desktop/missions.py) with fake agents on a temporary DBOS/SQLite store. Windows.
The restart case runs a real second Python process that is killed mid-step, then resumes in a new process."""
import json
import os
import subprocess
import sys
import textwrap
import threading
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
        if tool in ("open_app", "open_file"):              # the node reports the window it saw for an open
            return {"ok": True, "verified": "verified", "opened": step.get("app") or step.get("name")}
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


def test_a_planner_crash_ends_the_mission_honestly(eng):
    """Bench D-066 (2026-10-17): the planner raised and the mission stayed 'queued' forever."""
    def boom(goal, ctx):
        raise RuntimeError("planner down")
    eng.agents = {}
    eng.plan_fn = boom
    r = run("anything")
    assert r["status"] == "failed" and "planner failed" in r["error"] and not r["steps"]
    assert not os.path.exists(eng.lock_path)


def test_a_replan_crash_keeps_the_done_steps_and_ends_failed(eng):
    calls = []

    def plan(goal, ctx):
        calls.append(ctx)
        if ctx:
            raise RuntimeError("planner down")
        return [{"tool": "check_pc", "check": "disk"}, {"tool": "open_app", "app": "never"}]
    eng.agents = {"check_pc": lambda s: {"ok": True}, "open_app": lambda s: {"ok": True, "verified": "not seen"}}
    eng.plan_fn = plan
    r = run("check the disk then open something")
    assert r["status"] == "failed" and "re-plan failed" in r["error"] and r["steps"][0]["passed"] and len(calls) == 2


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


def test_the_plugins_stop_reaches_a_running_mission_through_the_pc_operation_registry(eng):
    """stop_pc_task (coordinator.stop_all) stops a mission like stop_mission does: the mission's lock holder is a registered
    operation whose stop hook is the mission's own cancel; the mission ends 'cancelled' after its current step, and its lock
    is released only when its worker has ended (the stop waits for that)."""
    from plugin.plugins.desktop import coordinator as C
    inside, go, ran, cancelled = threading.Event(), threading.Event(), [], threading.Event()
    eng.on_cancel = cancelled.set                                  # missions.cancel() runs this after it recorded the cancel

    def step(s):
        ran.append(s["check"])
        inside.set()
        go.wait(15)
        return {"ok": True}
    eng.agents = {"check_pc": step}
    eng.plan_fn = lambda goal, ctx: [{"tool": "check_pc", "check": c} for c in ("disk", "network", "memory")]
    mid = M.start("check everything")
    assert inside.wait(15)
    ops = [o for o in C.operations() if o.kind == "mission"]
    assert len(ops) == 1 and ops[0].name == mid
    out = {}
    t = threading.Thread(target=lambda: out.update(r=C.stop_all("test", wait_s=20)))
    t.start()
    assert cancelled.wait(15) and M._cancelled(mid), "the mission's own cancel was not triggered by the registry stop"
    assert C.current_task(M.E.lock_path) == mid, "the lock must stay held while the mission's step still runs"
    go.set()                                                       # the step finishes; the mission ends at the step boundary
    t.join(20)
    assert [d["id"] for d in out["r"]["stopped"]] == [mid] and out["r"]["still_running"] == []
    r = M.load(mid)
    assert r["status"] == "cancelled" and ran == ["disk"] and C.current_task(M.E.lock_path) is None


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


def test_an_interrupted_browser_click_is_not_repeated_but_a_read_is(eng):
    """Bench D-015 (2026-10-16): every browser step counted as safe to repeat, so a 'click Send' that was running when the
    process died would have been clicked again after the restart."""
    calls = []
    eng.agents = {"browser": lambda s: (calls.append(s["action"]), {"ok": True, "matches": True})[1]}
    mid = "m-test-browser"
    click = {"tool": "browser", "action": "click", "target": "Send", "profile": "Default"}
    who = {"tool": "browser", "action": "whoami", "profile": "Default"}
    for step in (click, who):
        M._q("insert into effects (key, mission, tool, state, result, at) values (?,?,?,?,?,?)",
             (f"{mid}:{M._sig(step)}:0", mid, "browser", "started", "{}", 0))
    c, w = M._act(mid, click, 0), M._act(mid, who, 0)
    assert c["interrupted"] and not c["ok"] and w["ok"] and calls == ["whoami"]


def test_explain_tells_what_stayed_uncertain_and_how_to_approve(eng):
    """Bench D-009/D-011 (2026-10-16): explain() dropped the uncertainty of passed steps and, for a mission waiting for
    the owner, gave no approval id ('Not finished: waiting_for_permission')."""
    rec = {"status": "waiting_for_permission", "approval_id": "ap-42", "steps": [
        {"n": 1, "tool": "play_media", "passed": True, "result": {}, "uncertainty": "Windows' media session did not confirm the title"},
        {"n": 2, "tool": "type_text", "passed": False, "result": {}, "uncertainty": ""}]}
    ex = M.explain(rec)
    assert ex.startswith("1 of 2 steps") and "did not confirm the title" in ex and "ap-42" in ex and "PIN" in ex
    ex = M.explain(dict(rec, status="failed", error="the re-plan had no further steps"))
    assert "Not finished: the re-plan had no further steps" in ex and "Not certain" in ex


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


def _wait_status(mid, timeout=20):
    deadline = time.time() + timeout
    while time.time() < deadline:
        r = M.load(mid)
        if r and r.get("status") not in ("queued", "running"):
            return r
        time.sleep(0.05)
    raise AssertionError(f"mission did not finish: {M.load(mid)}")


def test_a_mission_and_a_single_task_never_hold_the_pc_at_once(eng, monkeypatch):
    """Audit PC-D2: one PC-control lock for missions and single tasks. While a mission is inside a step, a run_pc_task is
    refused with the honest 'another PC task is running' (even 700 s later, beyond the single task's own budget); while a
    single task runs, a mission is refused the same way. Each frees the lock when it ends or is cancelled."""
    from unittest import mock
    from plugin.plugins.desktop import coordinator as C, decide as D
    monkeypatch.setattr(C, "HEARTBEAT_S", 0.02, raising=False)
    inside, go_on = __import__("threading").Event(), __import__("threading").Event()

    def blocking(step):
        inside.set()
        go_on.wait(15)
        return {"ok": True}
    eng.agents = {"check_pc": blocking}
    eng.plan_fn = lambda goal, ctx: [{"tool": "check_pc", "check": "disk"}]
    mid = M.start("check the disk")
    assert inside.wait(15)
    ran = []
    single = C.Coordinator({"open_app": lambda s: ran.append(1) or {"ok": True, "verified": "verified"}}, [],
                           tasks_dir=eng.db_path.parent / "tasks", lock_path=eng.lock_path)
    one = D.Plan([{"tool": "open_app", "app": "notepad"}], "ok", model="fake")
    rec = single.run("open notepad", one)
    assert rec["status"] == "busy" and mid in rec["error"] and ran == []
    real = time.time
    with mock.patch("time.time", lambda: real() + 700):
        time.sleep(0.15)
        rec = single.run("open notepad", one)
    assert rec["status"] == "busy" and mid in rec["error"] and ran == []
    go_on.set()
    assert _wait_status(mid)["status"] == "completed"
    assert not eng.lock_path.exists()

    # the other way round: a single task holds the PC, a mission is refused and never acts
    inside.clear(), go_on.clear()
    acted = []
    eng.agents = {"check_pc": lambda s: acted.append(1) or {"ok": True}}
    worker = __import__("threading").Thread(target=lambda: C.Coordinator({"open_app": lambda s: blocking(s) and {"ok": True, "verified": "verified"}},
                                                                         [], tasks_dir=eng.db_path.parent / "tasks",
                                                                         lock_path=eng.lock_path).run("open notepad", one))
    worker.start()
    assert inside.wait(15)
    r = _wait_status(M.start("check the disk"))
    assert r["status"] == "busy" and acted == []
    go_on.set()
    worker.join(15)
    assert not eng.lock_path.exists()


def test_a_cancelled_mission_frees_the_pc_lock(eng):
    eng.agents = {"check_pc": lambda s: (time.sleep(0.4), {"ok": True})[1]}
    eng.plan_fn = lambda goal, ctx: [{"tool": "check_pc", "check": c} for c in ("disk", "network", "memory", "cpu")]
    mid = M.start("check everything slowly")
    time.sleep(0.2)
    assert eng.lock_path.exists()
    assert M.cancel(mid)
    assert _wait_status(mid)["status"] == "cancelled"
    assert not eng.lock_path.exists()
    assert not [p for p in eng.lock_path.parent.iterdir() if p.name.endswith(".beat")]


# ---------------------------------------------------------------- 2026-10-18: the four failed live missions, PC-D5/D6/D9
# The live results (jarvis/evals/neko95/results/mission-20261016-*.json) each reproduced with fakes. Every case below
# failed (or hung for the step's whole timeout) on the code before this change.
import asyncio
import threading

from plugin.plugins.desktop import DesktopPlugin, call_on_loop, StepCancelled, StepUnavailable, core
from plugin.plugins.desktop import decide as D
from plugin.sdk.plugin import Ok

RESEARCH_DECK = {"tool": "research", "query": "home solar panels", "deliverable": "presentation"}


class _Host:
    """Just enough of the plugin object for DesktopPlugin._agents: an entry loop and a fake plugin bus."""

    def __init__(self, loop, research=None):
        self._cmd_loop, self.calls = loop, []
        host = self

        class Plugins:
            async def call_entry_json(self, entry, args, timeout=0):
                host.calls.append(entry)
                if research:
                    return await research()
                return Ok({"summary": "three checked findings", "file": "D:\\out\\solar.pptx", "qa_passed": True})
        self.plugins = Plugins()


def _live_loop():
    loop = asyncio.new_event_loop()
    threading.Thread(target=loop.run_forever, daemon=True).start()
    return loop


def _finished_thread_loop():
    """The loop the plugin host ran startup on: run in its own thread, then closed when startup returned."""
    box = {}

    def startup():
        async def capture():
            box["loop"] = asyncio.get_running_loop()
        asyncio.run(capture())
    t = threading.Thread(target=startup)
    t.start()
    t.join()
    assert box["loop"].is_closed()
    return box["loop"]


def test_call_on_loop_runs_on_a_live_loop_and_names_a_closed_one_at_once():
    loop = _live_loop()

    async def value():
        return 42
    assert call_on_loop(value(), loop, 5) == 42
    dead = _finished_thread_loop()
    t0 = time.time()
    with pytest.raises(StepUnavailable, match="event loop is closed"):
        call_on_loop(value(), dead, 960)
    assert time.time() - t0 < 1                                      # named at once, not after the 960 s step timeout
    loop.call_soon_threadsafe(loop.stop)


def test_call_on_loop_stops_waiting_when_the_owner_stops_and_cancels_the_call():
    loop, stop, cancelled = _live_loop(), threading.Event(), threading.Event()

    async def forever():
        try:
            await asyncio.sleep(3600)
        except asyncio.CancelledError:
            cancelled.set()
            raise
    threading.Timer(0.3, stop.set).start()
    t0 = time.time()
    with pytest.raises(StepCancelled):
        call_on_loop(forever(), loop, 960, abort=stop.is_set, poll_s=0.1)
    assert time.time() - t0 < 2 and cancelled.wait(2)              # the research call itself is stopped on the loop
    with pytest.raises(StepUnavailable, match="timed out after"):
        call_on_loop(forever(), loop, 0.3, poll_s=0.1)
    loop.call_soon_threadsafe(loop.stop)


def test_live_mission_155724_event_loop_closed_ends_honestly_without_replans(eng):
    """mission-20261016-155724: three research steps, each 'RuntimeError: Event loop is closed', two re-plans, then
    'stopped a loop'. The research agent ran its plugin call on a loop owned by a finished thread. Now: one step, no
    re-plan, status failed with the real reason."""
    plans = []
    host = _Host(_finished_thread_loop())
    eng.agents = DesktopPlugin._agents(host, host._cmd_loop, abort=M.cancel_requested)
    eng.plan_fn = lambda goal, ctx: plans.append(ctx) or [dict(RESEARCH_DECK)]
    r = run("research how home solar panels work and what they cost, make slides, open them and explain what you found")
    assert r["status"] == "failed" and r["replans"] == 0 and len(r["steps"]) == 1 and len(plans) == 1, r
    assert "could not run" in r["error"] and "event loop is closed" in r["error"] and "Not re-planned" in r["error"]
    assert host.calls == [] and "loop" not in r["error"].split(":")[0]


def test_live_missions_162004_and_171703_a_research_timeout_is_not_researched_again(eng):
    """mission-20261016-162004/171703: 'research_topic timed out after 900.0s', then a re-plan started a second
    15-minute research (still 'running' at 1506 s). A step that could not run is not re-planned."""
    plans, calls = [], []
    err = "plugin call failed: Plugin research event plugin_entry.research_topic timed out after 900.0s"    # the recorded error
    eng.agents = {"research": lambda s: calls.append(1) or {"ok": False, "summary": "", "error": err}}
    eng.plan_fn = lambda goal, ctx: plans.append(ctx) or [dict(RESEARCH_DECK)]
    r = run("research how home solar panels work and make slides")
    assert r["status"] == "failed" and r["replans"] == 0 and calls == [1] and len(plans) == 1, r
    assert "timed out after 900" in r["error"] and "Not finished" in r["explain"]


def test_a_long_step_is_not_started_when_the_budget_left_cannot_cover_it(eng):
    calls = []
    eng.budget_s = 600                                                 # less than one research step may take (960 s)
    eng.agents = {"research": lambda s: calls.append(1) or {"ok": True, "summary": "s"}}
    eng.plan_fn = lambda goal, ctx: [dict(RESEARCH_DECK)]
    r = run("research solar panels")
    assert r["status"] == "stopped" and "not started" in r["error"] and calls == []


def test_stopping_a_mission_reaches_a_research_step_waiting_on_the_research_plugin(eng):
    """mission-20261016-162004 was cancelled at 948 s: the stop waited for the 900 s research call to time out."""
    gone = threading.Event()

    async def research():
        try:
            await asyncio.sleep(3600)
        except asyncio.CancelledError:
            gone.set()
            raise
    host = _Host(_live_loop(), research)
    eng.agents = DesktopPlugin._agents(host, host._cmd_loop, abort=M.cancel_requested)
    eng.plan_fn = lambda goal, ctx: [dict(RESEARCH_DECK), {"tool": "check_pc", "check": "disk"}]
    mid = M.start("research solar panels, make slides")
    deadline = time.time() + 10
    while not host.calls and time.time() < deadline:
        time.sleep(0.05)
    t0 = time.time()
    assert M.cancel(mid)
    r = _wait_status(mid, 10)
    assert r["status"] == "cancelled" and r["replans"] == 0 and time.time() - t0 < 5 and gone.wait(3), r
    host._cmd_loop.call_soon_threadsafe(host._cmd_loop.stop)


def test_live_mission_180106_a_made_deck_whose_window_was_not_seen_is_opened_not_researched_again(eng):
    """mission-20261016-180106: step 2 made the .pptx but 'no window showing it appeared'; the re-plan researched again
    and made a second deck (which then failed QA). Now the existing file is opened instead (open_output)."""
    calls = []
    deck = "D:\\jarvis\\outputs\\neko\\deliverables\\20261016-175625-solar\\solar.pptx"
    eng.agents = {"research": lambda s: calls.append("research") or {"ok": True, "summary": "three checked findings", "file": deck,
                                                                       "qa_passed": True, "opened_verified": False,
                                                                       "open_note": "no new window showed the file"},
                  "open_output": lambda s: calls.append(("open", s["path"])) or {"ok": True, "opened": "POWERPNT: solar",
                                                                                 "opened_verified": True}}
    eng.plan_fn = lambda goal, ctx: [dict(RESEARCH_DECK, open_after=True)]
    r = run("research solar panels, make slides and open them")
    assert r["status"] == "completed" and r["replans"] == 0 and calls == ["research", ("open", deck)], r
    assert "three checked findings" in r["explain"] and deck in r["explain"]


def test_pc_d9_a_kernel_refusal_ends_the_mission_and_is_never_replanned(eng):
    plans = []

    def refused(step):
        raise core.DesktopError("refused by policy: UI or browser on a password manager, wallet or banking app")
    eng.agents = {"open_website": refused}
    eng.plan_fn = lambda goal, ctx: plans.append(ctx) or [{"tool": "open_website", "url": f"https://bank{len(plans)}.example", "profile": "Default"}]
    r = run("open my bank and pay the bill")
    assert r["status"] == "refused" and r["replans"] == 0 and len(plans) == 1 and len(r["steps"]) == 1, r
    assert "refused" in r["explain"] and "Not re-planned" in r["error"]
    eng.agents = {"check_pc": lambda s: (_ for _ in ()).throw(core.DesktopError("the kill switch is on"))}
    eng.plan_fn = lambda goal, ctx: [{"tool": "check_pc", "check": "disk"}]
    assert run("check the disk")["status"] == "refused"


def test_pc_d5_the_deciders_question_reaches_the_owner_and_the_answer_continues_the_same_mission(eng):
    asked, calls = [], []

    def plan(goal, ctx):
        asked.append(goal)
        if "answer" not in goal:
            return D.Plan([], "clarify", question="Open what? Name the app or file.", model="fake")
        return D.Plan([{"tool": "open_app", "app": "notepad"}], "ok", model="fake")
    eng.plan_fn = plan
    eng.agents = {"open_app": lambda s: calls.append(s["app"]) or {"ok": True, "verified": "verified", "opened": s["app"]}}
    mid = M.start("open it")
    r = _wait_status(mid)
    assert r["status"] == "needs_clarification" and r["question"].startswith("Open what?") and calls == []
    assert "Open what?" in r["explain"] and mid in r["explain"]
    assert M.resume(mid)["ok"] is False                                 # no answer: nothing runs
    assert M.resume(mid, "notepad")["ok"] is True
    r = _wait_status(mid)
    assert r["status"] == "completed" and calls == ["notepad"] and "notepad" in asked[-1] and r["id"] == mid


def test_pc_d6_resume_after_approval_keeps_the_mission_id_and_never_repeats_done_steps(eng):
    calls = []

    def type_text(step):
        calls.append(("type", bool(step.get("_approval"))))
        if not step.get("_approval"):
            return {"ok": False, "state": "waiting_for_permission", "approval_id": "ap-7", "how": "Approve with your PIN",
                    "approval_body": {"op": "ui", "action": "type", "target": "notepad|*", "text": "hello"}}
        return {"ok": True}
    eng.agents = {"open_app": lambda s: calls.append("open") or {"ok": True, "verified": "verified", "opened": "notepad"},
                  "type_text": type_text, "check_pc": lambda s: calls.append("check") or {"ok": True}}
    eng.plan_fn = lambda goal, ctx: [{"tool": "open_app", "app": "notepad"}, {"tool": "type_text", "app": "notepad", "text": "hello"},
                                     {"tool": "check_pc", "check": "disk"}]
    mid = M.start("open notepad, type hello, then check the disk")
    r = _wait_status(mid)
    assert r["status"] == "waiting_for_permission" and r["approval_id"] == "ap-7" and "continue the mission" in r["explain"]
    assert r["explain"].startswith("1 of 1 steps")                     # a step held for the owner is not a failed step
    assert M.resume("m-no-such")["ok"] is False
    first, second = M.resume(mid), M.resume(mid)                       # two "continue"s at once: one run
    assert first["ok"] and not second["ok"]
    r = _wait_status(mid)
    assert r["status"] == "completed" and r["id"] == mid and r["explain"].startswith("3 of 3 steps"), r
    assert calls == ["open", ("type", False), ("type", True), "check"]          # open_app not repeated, typing once
    assert M.resume(mid)["ok"] is False                                 # a completed mission does not continue


def test_pc_d6_the_owners_approval_goes_to_exactly_the_job_it_was_asked_for(eng, monkeypatch):
    """Through the plugin's own agents: the resumed type step sends the approval id with the same job body, once."""
    sent = []

    class FakePc:
        def __init__(self, *a, **k):
            self.current_job, self.stopped = None, False

        def run(self, body, timeout=90.0, what=""):
            sent.append(dict(body))
            if body.get("op") == "ui" and body.get("action") == "type":
                if body.get("approval_id") == "ap-9":
                    return {"state": "done", "result": {"ok": True, "typed_chars": 5}}
                return {"state": "waiting_for_permission", "approval_id": "ap-9", "result": {}, "how": "approve"}
            return {"state": "done", "result": {"ok": True}}
    monkeypatch.setattr(core, "Pc", FakePc)
    host = _Host(_live_loop())
    eng.agents = DesktopPlugin._agents(host, host._cmd_loop, abort=M.cancel_requested)
    eng.plan_fn = lambda goal, ctx: [{"tool": "type_text", "app": "notepad", "text": "hello"}]
    mid = M.start("type hello into notepad")
    assert _wait_status(mid)["status"] == "waiting_for_permission"
    assert M.resume(mid)["ok"]
    r = _wait_status(mid)
    assert r["status"] == "completed", r
    with_id = [b for b in sent if b.get("approval_id")]
    assert with_id == [{"op": "ui", "action": "type", "target": "notepad|*", "text": "hello", "approval_id": "ap-9"}]
    host._cmd_loop.call_soon_threadsafe(host._cmd_loop.stop)


def test_an_interrupted_step_continues_only_on_the_owners_word(eng):
    calls = []
    step = {"tool": "type_text", "app": "notepad", "text": "x"}
    eng.agents = {"type_text": lambda s: calls.append("type") or {"ok": True}, "check_pc": lambda s: calls.append("check") or {"ok": True}}
    eng.plan_fn = lambda g, c: [dict(step), {"tool": "check_pc", "check": "disk"}]
    mid = "m-test-interrupted-resume"
    M._q("insert into missions (id, goal, status, created, updated, record) values (?,?,?,?,?,?)", (mid, "g", "queued", time.time(), time.time(), "{}"))
    M._q("insert into effects (key, mission, tool, state, result, at) values (?,?,?,?,?,?)", (f"{mid}:{M._sig(step)}:0", mid, "type_text", "started", "{}", 0))
    from dbos import SetWorkflowID
    with SetWorkflowID(mid):
        DBOS.start_workflow(M.run_mission, mid, "type then check")
    r = _wait_status(mid)
    assert r["status"] == "needs_owner" and calls == [] and "'done'" in r["explain"]
    assert M.resume(mid, "maybe")["ok"] is False
    assert M.resume(mid, "done")["ok"]
    r = _wait_status(mid)
    assert r["status"] == "completed" and calls == ["check"], r       # the owner said it happened: never typed twice


def test_a_waiting_mission_can_be_cancelled_and_then_not_continued(eng):
    eng.agents = {"type_text": lambda s: {"ok": False, "state": "waiting_for_permission", "approval_id": "ap-1"}}
    eng.plan_fn = lambda g, c: [{"tool": "type_text", "app": "notepad", "text": "x"}]
    mid = M.start("type x")
    assert _wait_status(mid)["status"] == "waiting_for_permission"
    assert M.cancel(mid) and M.load(mid)["status"] == "cancelled" and M.resume(mid)["ok"] is False


def test_every_desktop_entry_is_an_async_function_with_its_own_name():
    """2026-10-18: a helper put between @plugin_entry(id="run_pc_task") and its function took the entry: the host refuses
    sync entries ("must be 'async def'"), so run_pc_task was unreachable."""
    import inspect
    entries = {}
    for name, fn in vars(DesktopPlugin).items():
        meta = getattr(fn, "__neko_event_meta__", None)
        if meta is not None and getattr(meta, "event_type", "") == "plugin_entry":
            entries[meta.id] = (name, inspect.iscoroutinefunction(fn))
    assert entries["run_pc_task"] == ("run_pc_task", True) and entries["resume_mission"] == ("resume_mission", True)
    assert all(is_async and name == eid for eid, (name, is_async) in entries.items()), entries


def test_an_unconfirmed_result_of_an_unsafe_step_is_held_for_the_owner_not_retried_by_a_replan(eng):
    """J: the node never confirmed that a typing job ended ("may have acted"). A re-plan used to treat that as a plain failure
    and could type the text a second time; now the mission stops with needs_owner and the effect stays `started`."""
    calls = []
    eng.agents = {"type_text": lambda s: (calls.append(1), {"ok": False, "state": "failed", "unconfirmed": True,
                                                            "error": "no result within 90 s; it was cancelled (the node has not yet confirmed that it stopped)"})[1]}
    eng.plan_fn = lambda goal, ctx: [{"tool": "type_text", "app": "notepad", "text": "hello"}]
    r = run("type hello")
    assert r["status"] == "needs_owner" and calls == [1], "an unknown result was retried (the text would be typed twice)"
    key = r["resume"]["effect"]
    assert M._q("select state from effects where key=?", (key,))[0][0] == "started"
    assert M.resume(r["id"], "")["ok"] is False                     # it needs the owner's word
    assert M.resume(r["id"], "done")["ok"] is True                  # the owner checked: it happened, never typed again


def test_a_timeout_of_a_safe_step_is_still_retried(eng):
    calls = []
    eng.agents = {"open_app": lambda s: (calls.append(1), {"ok": len(calls) > 1, "verified": "verified", "opened": "notepad",
                                                           "error": "no result within 90 s"})[1]}
    eng.plan_fn = lambda goal, ctx: [{"tool": "open_app", "app": "notepad"}]
    r = run("open notepad")
    assert r["status"] == "completed" and len(calls) == 2


def test_resume_with_an_expired_approval_asks_the_owner_again_and_never_runs_the_action(eng, monkeypatch):
    """J: the owner approved, the card expired before the mission continued. The step must go back to waiting_for_permission
    with a NEW approval; the action itself must not be sent."""
    sent = []

    class FakePc:
        def __init__(self, *a, **k):
            self.current_job, self.stopped = None, False

        def run(self, body, timeout=90.0, what=""):
            sent.append(dict(body))
            if body.get("op") == "ui" and body.get("action") == "type":
                if body.get("approval_id") == "ap-1":
                    raise core.DesktopError("approval expired")             # the kernel's answer for an expired approval
                return {"state": "waiting_for_permission", "approval_id": "ap-2", "result": {}, "how": "approve"}
            return {"state": "done", "result": {"ok": True}}
    monkeypatch.setattr(core, "Pc", FakePc)
    host = _Host(_live_loop())
    eng.agents = DesktopPlugin._agents(host, host._cmd_loop, abort=M.cancel_requested)
    eng.plan_fn = lambda goal, ctx: [{"tool": "type_text", "app": "notepad", "text": "hello"}]
    mid = "m-test-expired-approval"
    M._q("insert into missions (id, goal, status, created, updated, record) values (?,?,?,?,?,?)", (mid, "g", "queued", time.time(), time.time(), "{}"))
    step = {"tool": "type_text", "app": "notepad", "text": "hello"}
    body = {"op": "ui", "action": "type", "target": "notepad|*", "text": "hello"}
    rec = {"id": mid, "goal": "type hello", "status": "waiting_for_permission", "plans": [[step]], "steps": [], "replans": 0, "started": 0,
           "approval_id": "ap-1", "resume": {"kind": "approval", "cursor": 0, "steps": [step], "seen": {}, "history": [],
                                             "approval": {"id": "ap-1", "body": body}}}
    M._save(mid, **rec)
    assert M.resume(mid)["ok"]
    r = _wait_status(mid)
    assert r["status"] == "waiting_for_permission" and r["approval_id"] == "ap-2", r
    assert [b for b in sent if b.get("approval_id") == "ap-1"] and not any(b.get("approval_id") == "ap-2" for b in sent), \
        "the expired approval was retried or the action sent without a fresh approval"
    host._cmd_loop.call_soon_threadsafe(host._cmd_loop.stop)
