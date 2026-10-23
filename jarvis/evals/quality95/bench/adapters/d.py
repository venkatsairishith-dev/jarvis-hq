"""D: long-horizon tasks, against the real engines: desktop missions (DBOS Transact on a temporary SQLite store, fake
agents, a real killed-and-restarted child process), the main agent's executor and run store, and the kernel's durable
automations (a real kernel database in a temp folder, "restarted" by reopening it)."""
from __future__ import annotations

import asyncio
import json
import logging
import os
import subprocess
import sys
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path

_TMP = tempfile.mkdtemp(prefix="bench-d-")
os.environ.setdefault("JARVIS_DATA_DIR", _TMP)
ROOT = Path(__file__).resolve().parents[5]

from dbos import DBOS, SetWorkflowID  # noqa: E402

from jarvis.plugins.desktop import coordinator as C  # noqa: E402
from jarvis.plugins.desktop import missions as M  # noqa: E402
from jarvis.plugins.main_agent.executor import (StepFailed, StepResult, StepUnknown, final_text, run_plan,  # noqa: E402
                                                run_state)
from jarvis.plugins.main_agent.planner import Plan, Step  # noqa: E402
from jarvis.plugins.main_agent.store import RunStore, plan_to_dict, result_from_dict, result_to_dict  # noqa: E402
from jarvis_kernel.automations import CLAIM_GRACE, Automations  # noqa: E402
from jarvis_kernel.jobs import Jobs  # noqa: E402
from jarvis_kernel.store import Kernel  # noqa: E402
from jarvis_kernel.tasks import Tasks  # noqa: E402


def _dir() -> Path:
    return Path(tempfile.mkdtemp(dir=_TMP))


# ================================================================ missions (DBOS)
@contextmanager
def _engine(budget_s=None):
    d = _dir()
    M.configure(d)
    logging.getLogger("dbos").setLevel(logging.WARNING)
    M.E.lock_path, M.E.budget_s, M.E.on_cancel, M.E.agents, M.E.plan_fn = d / "pc_task.lock", budget_s or M.BUDGET_S, (lambda: None), {}, None
    DBOS.launch()
    try:
        yield M.E, d
    finally:
        DBOS.destroy(destroy_registry=False)


def _wait(mid, limit=30):
    end = time.time() + limit
    while time.time() < end:
        r = M.load(mid)
        if r and r.get("status") not in ("queued", "running"):
            return r
        time.sleep(0.05)
    raise TimeoutError(f"mission did not finish: {M.load(mid)}")


def _ok(log, tool):
    def fn(step):
        log.append((tool, step.get("app") or step.get("query") or step.get("text") or step.get("check") or step.get("action") or ""))
        if tool == "open_app":
            return {"ok": True, "verified": "verified", "opened": step.get("app")}
        if tool == "research":
            return {"ok": True, "summary": "three checked findings about solar", "file": "D:\\out\\solar.pptx", "qa_passed": True}
        return {"ok": True}
    return fn


def _short(r):
    return f"status {r.get('status')}, steps {[s['tool'] for s in r.get('steps', [])]}, replans {r.get('replans')}, error {str(r.get('error'))[:100]}"


def mission_phases(a):
    with _engine() as (e, d):
        log = []
        e.agents = {t: _ok(log, t) for t in ("research", "open_file", "check_pc")}
        e.plan_fn = lambda g, c: [{"tool": "check_pc", "check": "disk"}, {"tool": "research", "query": "solar", "deliverable": "presentation"}]
        r = _wait(M.start("check the disk, research solar and make slides"))
        ok = r["status"] == "completed" and "three checked findings" in r["explain"] and "solar.pptx" in r["explain"] \
            and r["explain"].startswith("2 of 2 steps") and C.current_task(e.lock_path) is None
        return ok, _short(r) + f"; explain '{r.get('explain', '')[:140]}'"


def mission_replan(a):
    with _engine() as (e, d):
        log, ctxs = [], []

        def open_app(step):
            log.append(step["app"])
            return {"ok": True, "opened": step["app"], "verified": "verified" if step["app"] == "wordpad" else "unconfirmed"}
        e.agents = {"open_app": open_app, "type_text": _ok(log, "type_text")}

        def plan(g, c):
            ctxs.append(c)
            app = "wordpad" if c else "word"
            return [{"tool": "open_app", "app": app}, {"tool": "type_text", "app": app, "text": "hi"}]
        e.plan_fn = plan
        r = _wait(M.start("write hi in a word processor"))
        ok = r["status"] == "completed" and r["replans"] == 1 and "failed" in ctxs[1] and log == ["word", "wordpad", ("type_text", "wordpad")]
        return ok, _short(r) + f"; actions {log}"


def mission_replans_bounded(a):
    with _engine() as (e, d):
        e.agents = {"open_app": lambda s: {"ok": True, "verified": "unconfirmed"}}
        e.plan_fn = lambda g, c: [{"tool": "open_app", "app": f"x{len(c)}"}]
        r = _wait(M.start("open something that never appears"))
        return r["status"] == "failed" and r["replans"] == M.MAX_REPLANS and len(r["steps"]) == M.MAX_REPLANS + 1, _short(r)


def mission_loop(a):
    with _engine() as (e, d):
        if a["kind"] == "repeat":
            e.agents = {"check_pc": lambda s: {"ok": True, "free_gb": 10}}
            e.plan_fn = lambda g, c: [{"tool": "check_pc", "check": "disk"}] * 8
            r = _wait(M.start("keep checking the disk"))
            return r["status"] == "stopped" and "same step gave the same result" in r["error"] and len(r["steps"]) == M.LOOP_REPEATS, _short(r)
        e.agents = {"window": lambda s: {"ok": True, "state": "done"}}
        e.plan_fn = lambda g, c: [{"tool": "window", "app": "notepad", "action": "maximize"}, {"tool": "window", "app": "notepad", "action": "minimize"}] * 4
        r = _wait(M.start("make notepad the right size"))
        return r["status"] == "stopped" and "undoing each other" in r["error"] and len(r["steps"]) == 4, _short(r)


def mission_step_budget(a):
    with _engine() as (e, d):
        calls = []

        def chk(s):
            calls.append(s["check"])
            return {"ok": s["check"] != "c19"}                       # the 20th step fails: a re-plan adds more steps
        e.agents = {"check_pc": chk}
        e.plan_fn = lambda g, c: [{"tool": "check_pc", "check": f"r{i}"} for i in range(10)] if c else [{"tool": "check_pc", "check": f"c{i}"} for i in range(30)]
        r = _wait(M.start("many checks"))
        ok = r["status"] == "stopped" and "step budget" in r["error"] and len(r["steps"]) == M.MAX_STEPS and len(calls) == M.MAX_STEPS
        return ok, _short(r)[:120] + f"; actions {len(calls)}"


def mission_time_budget(a):
    with _engine(budget_s=0.5) as (e, d):
        e.agents = {"check_pc": lambda s: (time.sleep(0.3), {"ok": True})[1]}
        e.plan_fn = lambda g, c: [{"tool": "check_pc", "check": x} for x in ("disk", "network", "memory", "cpu")]
        r = _wait(M.start("slow checks"))
        return r["status"] == "stopped" and "time budget" in r["error"] and len(r["steps"]) < 4, _short(r)


def mission_cancel(a):
    with _engine() as (e, d):
        stopped = []
        e.on_cancel = lambda: stopped.append(1)
        e.agents = {"check_pc": lambda s: (time.sleep(0.6), {"ok": True})[1]}
        e.plan_fn = lambda g, c: [{"tool": "check_pc", "check": x} for x in ("disk", "network", "memory", "cpu")]
        mid = M.start("check everything slowly")
        time.sleep(0.3)
        accepted = M.cancel(mid)
        unknown = M.cancel("m-no-such-mission")
        r = _wait(mid)
        ok = accepted and not unknown and stopped == [1] and r["status"] == "cancelled" and len(r["steps"]) == 1 \
            and C.current_task(e.lock_path) is None
        return ok, _short(r) + f"; running job stopped {bool(stopped)}, unknown id accepted {unknown}"


def mission_owner_states(a):
    """kind=permission: a step waits for the owner's approval; kind=denied: the owner refused. Either stops the mission,
    later steps never run, and the explanation says what is needed."""
    with _engine() as (e, d):
        later = []
        held = {"ok": False, "state": "waiting_for_permission", "approval_id": "ap-42", "how": "Approve with your PIN"} if a["kind"] == "permission" \
            else {"ok": False, "state": "denied", "error": "refused by policy: approval denied"}
        e.agents = {"open_app": lambda s: {"ok": True, "verified": "verified"}, "type_text": lambda s: dict(held),
                    "check_pc": lambda s: later.append(1) or {"ok": True}}
        e.plan_fn = lambda g, c: [{"tool": "open_app", "app": "notepad"}, {"tool": "type_text", "app": "notepad", "text": "x"},
                                  {"tool": "check_pc", "check": "disk"}]
        r = _wait(M.start("type into notepad then check"))
        if a["kind"] == "permission":
            ok = r["status"] == "waiting_for_permission" and r.get("approval_id") == "ap-42" and not later and "ap-42" in r["explain"]
        else:
            ok = r["status"] == "denied" and not later and "denied" in r["explain"] and r["replans"] == 0
        return ok, _short(r) + f"; later steps {len(later)}; explain '{r.get('explain', '')[:160]}'"


def mission_explain(a):
    """explain() must carry what was checked AND what stayed uncertain, and the reason it did not finish."""
    with _engine() as (e, d):
        e.agents = {"play_media": lambda s: {"ok": True, "playing": True, "same_video": True, "title": "Kalyani raga - YouTube",
                                             "windows_media": ["Another song (Playing)"]},
                    "research": lambda s: {"ok": False, "error": "search unavailable"}}
        e.plan_fn = lambda g, c: [] if c else [{"tool": "play_media", "query": "kalyani"}, {"tool": "research", "query": "raga kalyani", "deliverable": "report"}]
        r = _wait(M.start("play kalyani and write a report on it"))
        ex = r.get("explain", "")
        ok = r["status"] == "failed" and ex.startswith("1 of") and "media session did not confirm" in ex and "Not finished" in ex \
            and "Findings" not in ex
        return ok, _short(r) + f"; explain '{ex[:240]}'"


def mission_effects(a):
    """_act journal: a done effect replays its result instead of acting again; a second occurrence is a new effect;
    a failed one did not happen, so it may run again."""
    with _engine() as (e, d):
        calls = []
        e.agents = {"type_text": lambda s: (calls.append(s["text"]), {"ok": s["text"] != "flaky" or calls.count("flaky") > 1, "error": "x"})[1]}
        mid = "m-bench-effects"
        M._q("insert into missions (id, goal, status, created, updated, record) values (?,?,?,?,?,?)", (mid, "g", "x", 0, 0, "{}"))
        st = {"tool": "type_text", "app": "notepad", "text": "hello"}
        r1, r2, r3 = M._act(mid, st, 0), M._act(mid, st, 0), M._act(mid, st, 1)
        fl = {"tool": "type_text", "app": "notepad", "text": "flaky"}
        f1, f2 = M._act(mid, fl, 0), M._act(mid, fl, 0)
        ok = calls == ["hello", "hello", "flaky", "flaky"] and r2.get("replayed") and not r1.get("replayed") and not r3.get("replayed") \
            and not f1["ok"] and f2["ok"]
        return ok, f"agent calls {calls}, replayed {r2.get('replayed')}, failed-then-ok {f1['ok']}->{f2['ok']}"


def mission_interrupted(a):
    """A step found 'started' but never finished (the process died while it ran) in a full mission run."""
    with _engine() as (e, d):
        calls = []
        step = a["step"]
        e.agents = {step["tool"]: lambda s: calls.append(s["tool"]) or dict(a["result"]), "check_pc": lambda s: calls.append("check_pc") or {"ok": True}}
        e.plan_fn = lambda g, c: [dict(step), {"tool": "check_pc", "check": "disk"}]
        mid = f"m-bench-int-{step['tool']}-{step.get('action', '')}"
        M._q("insert into missions (id, goal, status, created, updated, record) values (?,?,?,?,?,?)", (mid, "g", "queued", time.time(), time.time(), "{}"))
        M._q("insert into effects (key, mission, tool, state, result, at) values (?,?,?,?,?,?)",
             (f"{mid}:{M._sig(step)}:0", mid, step["tool"], "started", "{}", 0))
        with SetWorkflowID(mid):
            DBOS.start_workflow(M.run_mission, mid, "resume test")
        r = _wait(mid)
        if a["repeat"]:
            ok = r["status"] == "completed" and calls == [step["tool"], "check_pc"]
        else:
            ok = r["status"] == "needs_owner" and calls == [] and "not repeated" in r["error"]
        return ok, _short(r) + f"; agent calls {calls}"


def mission_busy(a):
    with _engine() as (e, d):
        calls = []
        e.agents = {"check_pc": lambda s: calls.append(1) or {"ok": True}}
        e.plan_fn = lambda g, c: [{"tool": "check_pc", "check": "disk"}]
        C.acquire("a-pc-task", e.lock_path)
        try:
            r = _wait(M.start("check the disk"))
        finally:
            C.release("a-pc-task", e.lock_path)
        return r["status"] == "busy" and not calls and "a-pc-task" in r["error"], _short(r)


def mission_no_plan(a):
    with _engine() as (e, d):
        e.plan_fn = lambda g, c: []
        r = _wait(M.start("do the impossible"))
        return r["status"] == "not_understood" and not r.get("steps"), _short(r)


def mission_agent_crash(a):
    with _engine() as (e, d):
        def boom(s):
            raise RuntimeError("the agent crashed")
        e.agents = {"open_app": boom}
        n = []
        e.plan_fn = lambda g, c: (n.append(1), [{"tool": "open_app", "app": f"editor{len(n)}"}])[1]     # a new app each re-plan
        r = _wait(M.start("open notepad"))
        errs = [s["result"].get("error", "") for s in r["steps"]]
        ok = r["status"] == "failed" and all("RuntimeError" in x for x in errs) and C.current_task(e.lock_path) is None
        return ok, _short(r) + f"; step errors {errs[:1]}"


CHILD = r'''
import sys, time, json, logging
from pathlib import Path
sys.path.insert(0, @ROOT@)
from dbos import DBOS
from jarvis.plugins.desktop import missions as M
d, phase, slow = Path(@TMP@), @PHASE@, @SLOW@
M.configure(d)
logging.getLogger("dbos").setLevel(logging.WARNING)
M.E.lock_path = d / "pc_task.lock"
log = d / "acts.jsonl"
def agent(step):
    with log.open("a") as f:
        f.write(json.dumps(step) + "\n")
    if (step.get("check") or step.get("text")) == slow and phase == "first":
        time.sleep(60)                      # killed here, mid-step
    return {"ok": True}
M.E.agents = {"check_pc": agent, "type_text": agent}
M.E.plan_fn = lambda goal, ctx: @STEPS@
DBOS.launch()                               # in the second process this resumes the unfinished mission
if phase == "first":
    print(M.start("restart test"), flush=True)
    time.sleep(60)
else:
    for _ in range(300):
        r = M.latest()
        if r and r.get("status") not in ("queued", "running"):
            break
        time.sleep(0.1)
    print(json.dumps(M.latest()), flush=True)
DBOS.destroy()
'''


def mission_kill_restart(a):
    """A real process runs the mission and is killed (SIGKILL) inside step a.slow; a second process launches DBOS and
    resumes it. The journal decides whether the interrupted step may run again."""
    d = _dir()
    steps = a["steps"]
    env = dict(os.environ, JARVIS_DATA_DIR=str(d))

    def src(phase):
        return (CHILD.replace("@ROOT@", repr(str(ROOT))).replace("@TMP@", repr(str(d))).replace("@PHASE@", repr(phase))
                .replace("@SLOW@", repr(a["slow"])).replace("@STEPS@", repr(steps)))
    first = subprocess.Popen([sys.executable, "-c", src("first")], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, env=env)
    log = d / "acts.jsonl"
    end = time.time() + 60
    while time.time() < end and not (log.exists() and a["slow"] in log.read_text()):
        time.sleep(0.1)
    first.kill()
    first.wait()
    second = subprocess.run([sys.executable, "-c", src("second")], capture_output=True, text=True, timeout=120, env=env)
    rec = json.loads(second.stdout.strip().splitlines()[-1])
    acts = [(lambda s: s.get("text") or s.get("check"))(json.loads(x)) for x in log.read_text().splitlines()]
    ok = rec["status"] == a["want_status"] and acts == a["want_acts"]
    return ok, f"status {rec['status']}, actions in order {acts}, error {str(rec.get('error'))[:120]}"


# ================================================================ main agent executor and run store
def _plan(*steps):
    return Plan("bench", list(steps))


def S(id, deps=(), kind="think", effect="none"):
    return Step(id, kind, f"goal {id}", depends_on=list(deps), effect=effect)


FAST = (0.0, 0.01)


def _by(results):
    return {r.id: r for r in results}


def exec_case(a):
    m = a["mode"]
    if m == "waves":
        seen = {}

        async def dispatch(step, inputs):
            seen[step.id] = dict(inputs)
            await asyncio.sleep(0.3)
            return f"out-{step.id}"
        t0 = time.monotonic()
        res = asyncio.run(run_plan(_plan(S("a"), S("b"), S("c"), S("d", ["a", "b", "c"])), dispatch, parallel=3))
        sec = time.monotonic() - t0
        ok = sec < 0.9 and all(r.ok for r in res) and seen["d"] == {"a": "out-a", "b": "out-b", "c": "out-c"}
        return ok, f"{sec:.2f} s for two waves of 0.3 s (limit 0.9 s), inputs of d {seen.get('d')}"
    if m == "isolation":
        async def dispatch(step, inputs):
            if step.id == "a":
                raise StepFailed("the page would not load")
            return "ok"
        r = _by(asyncio.run(run_plan(_plan(S("a"), S("b"), S("c", ["a"]), S("d", ["c"]), S("e", ["b"])), dispatch, backoff=FAST)))
        ok = r["a"].outcome == "failure" and r["c"].outcome == r["d"].outcome == "not_executed" and r["b"].ok and r["e"].ok
        return ok, ", ".join(f"{k}:{v.outcome}" for k, v in r.items())
    if m == "held":
        calls = []

        async def dispatch(step, inputs):
            calls.append(step.id)
            if step.id == "send":
                await asyncio.sleep(5)
            return "ok"
        p = _plan(S("send", kind="plugin", effect="non_idempotent"), S("after", ["send"]), S("other"))
        r = _by(asyncio.run(run_plan(p, dispatch, step_timeout=0.2, parallel=1, backoff=FAST)))
        ok = r["send"].outcome == "unknown" and r["send"].held and calls.count("send") == 1 and r["after"].outcome == "not_executed" \
            and r["other"].outcome == "not_executed" and run_state(p, list(r.values())) == "held"
        return ok, ", ".join(f"{k}:{v.outcome}{'(held)' if v.held else ''}" for k, v in r.items()) + f"; send dispatched {calls.count('send')}x"
    if m == "retry_read":
        calls = []

        async def dispatch(step, inputs):
            calls.append(1)
            if len(calls) == 1:
                raise RuntimeError("transient")
            return "second time"
        r = asyncio.run(run_plan(_plan(S("a")), dispatch, backoff=FAST))[0]
        return r.ok and r.attempts == 2 and r.text == "second time", f"outcome {r.outcome}, attempts {r.attempts}"
    if m == "no_retry_effect":
        calls = []

        async def dispatch(step, inputs):
            calls.append(step.id)
            if step.id == "write":
                raise StepFailed("the disk is full")
            return "ok"
        r = _by(asyncio.run(run_plan(_plan(S("write", kind="plugin", effect="non_idempotent"), S("other")), dispatch, backoff=FAST)))
        ok = r["write"].outcome == "failure" and r["write"].attempts == 1 and calls.count("write") == 1 and not r["write"].held and r["other"].ok
        return ok, f"write {r['write'].outcome} after {r['write'].attempts} attempt(s), other {r['other'].outcome}"
    if m == "total_time":
        async def dispatch(step, inputs):
            await asyncio.sleep(0.3)
            return "ok"
        t0 = time.monotonic()
        r = _by(asyncio.run(run_plan(_plan(S("a"), S("b", ["a"]), S("c", ["b"]), S("d", ["c"])), dispatch, total_timeout=0.5, backoff=FAST)))
        sec = time.monotonic() - t0
        ok = r["a"].ok and r["b"].outcome == "unknown" and r["c"].outcome == r["d"].outcome == "not_executed" and sec < 1.0
        return ok, ", ".join(f"{k}:{v.outcome}" for k, v in r.items()) + f" in {sec:.2f} s"
    if m == "waiting":
        async def dispatch(step, inputs, ctx):
            for _ in range(6):                                # polls while the owner's approval is pending (0.6 s)
                await asyncio.sleep(0.1)
                ctx.add_waiting(0.1)
            return "approved and done"

        async def silent(step, inputs):
            await asyncio.sleep(0.6)                          # the same time, but working: over the 0.3 s limit
            return "late"
        p = _plan(S("a", kind="plugin", effect="non_idempotent"))
        r = asyncio.run(run_plan(p, dispatch, step_timeout=0.3, total_timeout=0.5, backoff=FAST))[0]
        neg = asyncio.run(run_plan(p, silent, step_timeout=0.3, total_timeout=0.5, backoff=FAST))[0]
        return r.ok and r.waited >= 0.59 and neg.outcome == "unknown" and neg.held, \
            f"waiting step {r.outcome} (waited {r.waited:.1f} s); working step {neg.outcome}, held {neg.held}"
    if m == "cancel":
        ev = None

        async def main():
            nonlocal ev
            ev = asyncio.Event()

            async def dispatch(step, inputs):
                if step.id == "a":
                    ev.set()                                   # the owner says stop while a runs
                await asyncio.sleep(0.05)
                return "ok"
            p = _plan(S("a"), S("b", ["a"]), S("c", ["b"]))
            res = await run_plan(p, dispatch, cancel=ev, parallel=1)
            return p, res
        p, res = asyncio.run(main())
        r = _by(res)
        ok = r["a"].ok and r["b"].outcome == "not_executed" and "cancelled" in r["b"].error and run_state(p, res, cancelled=True) == "cancelled"
        return ok, ", ".join(f"{k}:{v.outcome}({v.error})" for k, v in r.items())
    if m == "prior":
        calls = []

        async def dispatch(step, inputs):
            calls.append(step.id)
            return f"new-{step.id}"
        prior = {"a": StepResult("a", "success", "saved-a")}
        r = _by(asyncio.run(run_plan(_plan(S("a", kind="plugin", effect="non_idempotent"), S("b", ["a"])), dispatch, prior=prior)))
        return calls == ["b"] and r["a"].text == "saved-a" and r["b"].ok, f"dispatched {calls}, a kept '{r['a'].text}'"
    if m == "prior_held":
        calls = []

        async def dispatch(step, inputs):
            calls.append(step.id)
            return "ok"
        prior = {"send": StepResult("send", "unknown", error="interrupted", held=True)}
        r = _by(asyncio.run(run_plan(_plan(S("send", kind="plugin", effect="non_idempotent"), S("after", ["send"]), S("other")), dispatch, prior=prior)))
        ok = calls == [] and r["after"].outcome == r["other"].outcome == "not_executed"
        return ok, f"dispatched {calls}, after {r['after'].outcome}, other {r['other'].outcome} ({r['other'].error})"
    if m == "final_text":
        p = Plan("bench", [Step("send", "plugin", "send the weekly summary to the team", effect="non_idempotent"), Step("a", "think", "summarise"),
                           Step("b", "think", "translate")])
        res = [StepResult("send", "unknown", error="timed out", held=True), StepResult("a", "success", "Summary text."),
               StepResult("b", "failure", error="model refused")]
        t = final_text(p, res)
        ok = "Summary text." in t and "Needs your attention" in t and "did not retry" in t and "b (failure: model refused)" in t \
            and run_state(p, res) == "held"
        return ok, t[:300]
    if m == "error_class":
        async def dispatch(step, inputs):
            raise ValueError("401 for https://api.example/v1?key=sk-SYNTHETIC-TEST-KEY")
        r = asyncio.run(run_plan(_plan(S("a")), dispatch, backoff=FAST))[0]
        return r.outcome == "failure" and r.error == "ValueError" and "sk-" not in r.error, f"outcome {r.outcome}, error '{r.error}'"
    raise ValueError(m)


STORE_CHILD = r'''
import asyncio, json, sys
from pathlib import Path
sys.path.insert(0, @ROOT@)
from jarvis.plugins.main_agent.executor import run_plan
from jarvis.plugins.main_agent.planner import Plan, Step
from jarvis.plugins.main_agent.store import RunStore, plan_to_dict, result_to_dict
d = Path(@TMP@)
plan = Plan("bench", [Step("read", "think", "read the notes"),
                      Step("send", "plugin", "send the summary", ref="x:send_message", depends_on=["read"], effect="non_idempotent"),
                      Step("after", "think", "report", depends_on=["send"])])
store = RunStore(d / "runs")
run = {"run_id": "r1", "plan": plan_to_dict(plan), "results": {}, "running": [], "state": "running"}
store.save(run)
def on_start(step):
    run["running"].append(step.id); store.save(run)
def on_progress(step, res):
    run["results"][step.id] = result_to_dict(res)
    if step.id in run["running"]:
        run["running"].remove(step.id)
    store.save(run)
async def dispatch(step, inputs):
    with (d / "sent.log").open("a") as f:
        f.write(step.id + "\n")
    if step.id == "send":
        await asyncio.sleep(60)          # killed here: was the message sent? unknown
    return "ok-" + step.id
asyncio.run(run_plan(plan, dispatch, on_start=on_start, on_progress=on_progress))
'''


def store_kill_recover(a):
    """A real process runs a plan with checkpoints and is killed while a step with side effects runs. recover() must mark
    it unknown and held (finished steps kept), and resuming must not run it or what depends on it."""
    d = _dir()
    src = STORE_CHILD.replace("@ROOT@", repr(str(ROOT))).replace("@TMP@", repr(str(d)))
    p = subprocess.Popen([sys.executable, "-c", src], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    store = RunStore(d / "runs")
    end = time.time() + 60
    while time.time() < end and ((store.load("r1") or {}).get("running") != ["send"]):
        time.sleep(0.05)
    p.kill()
    p.wait()
    rec_ids = store.recover()
    rec = store.load("r1")
    res = rec["results"]
    plan = Plan("bench", [Step("read", "think", "read the notes"),
                          Step("send", "plugin", "send the summary", depends_on=["read"], effect="non_idempotent"),
                          Step("after", "think", "report", depends_on=["send"])])
    prior = {sid: result_from_dict({"id": sid, **rd}) for sid, rd in res.items()}
    calls = []

    async def dispatch(step, inputs):
        calls.append(step.id)
        return "ok"
    out = asyncio.run(run_plan(plan, dispatch, prior=prior))
    again = store.recover()
    ok = rec_ids == ["r1"] and rec["state"] == "interrupted" and res["read"]["outcome"] == "success" \
        and res["send"]["outcome"] == "unknown" and res["send"]["held"] is True and calls == [] \
        and run_state(plan, out) == "held" and again == []
    sent = (d / "sent.log").read_text().split()
    return ok, f"recovered {rec_ids}, read {res['read']['outcome']}, send {res['send']['outcome']} held {res['send'].get('held')}, " \
               f"resume dispatched {calls}, state {run_state(plan, out)}, sends attempted {sent.count('send')}"


def store_corrupt(a):
    d = _dir() / "runs"
    s = RunStore(d)
    s.save({"run_id": "good", "state": "done"})
    (d / "bad.json").write_text("{not json", encoding="utf-8")
    (d / "half.tmp").write_text('{"run_id": "half", "state": "running"', encoding="utf-8")     # a crash mid-write
    s.save({"run_id": "r2", "state": "running", "plan": plan_to_dict(_plan(S("a"))), "running": ["a"], "results": {}})
    rec = s.recover()
    ok = rec == ["r2"] and s.load("bad") is None and s.load("good")["state"] == "done" and s.load("half") is None \
        and s.load("../../etc/passwd") is None and s.load("r2")["results"]["a"]["outcome"] == "unknown" and not s.load("r2")["results"]["a"]["held"]
    return ok, f"recovered {rec}, corrupt ignored {s.load('bad') is None}, half-written ignored {s.load('half') is None}"


# ================================================================ kernel automations
JOB = {"name": "repo check", "action_kind": "job", "trigger_kind": "cron", "schedule": "0 8 * * *", "tz": "UTC",
       "action": {"kind": "project_check", "params": {"project": "jarvis-hq"}, "node": "laptop"}}
T0 = 1791676800.0          # 2026-10-11 00:00 UTC


def _kernel(path, now):
    k = Kernel(str(path))
    return k, Automations(k, Tasks(k), Jobs(k), owner=None, dispatch=None, clock=lambda: now[0])


def _jobs(k):
    return k.db.execute("SELECT COUNT(*) FROM jobs WHERE kind='project_check'").fetchone()[0]


def auto_case(a):
    m, d, now = a["mode"], _dir() / "k", [T0]
    k, A = _kernel(d, now)
    try:
        if m == "restart":
            aid = A.create(JOB)["id"]
            now[0] = T0 + 8 * 3600 + 5
            first = A.tick()["fired"]
            k.db.close()
            k, A = _kernel(d, now)                               # the kernel restarts; the same slot is still "now"
            second = A.tick()["fired"]
            third = A.tick()["fired"]
            n_runs, n_jobs = len(A.runs(aid)), _jobs(k)
            k.db.execute("UPDATE jobs SET state='done' WHERE kind='project_check'")    # the laptop finished the job
            A.settle()
            now[0] = T0 + 32 * 3600 + 5                          # the next day's 08:00 fires once more
            fourth = A.tick()["fired"]
            ok = len(first) == 1 and second == third == [] and n_runs == 1 and n_jobs == 1 and len(fourth) == 1 and _jobs(k) == 2
            return ok, f"fired before restart {len(first)}, after restart {len(second)}+{len(third)}, runs {n_runs}, jobs {n_jobs}, next day {len(fourth)}"
        if m in ("catchup_one", "catchup_skip"):
            spec = dict(JOB, trigger_kind="interval", schedule="600", catchup="one" if m == "catchup_one" else "skip", grace_s=60)
            aid = A.create(spec)["id"]
            now[0] += 5 * 600 + 300                              # off for 5 slots; the latest is 300 s old
            A.tick()
            st = [r["status"] for r in A.runs(aid)]
            if m == "catchup_one":
                ok = st.count("fired") == 1 and "skipped_missed" in st and _jobs(k) == 1
            else:
                ok = st.count("fired") == 0 and st == ["skipped_missed"] and _jobs(k) == 0
            return ok, f"run statuses {st}, jobs {_jobs(k)}"
        if m == "disabled":
            aid = A.create(dict(JOB, trigger_kind="interval", schedule="600"), enabled=False)["id"]
            now[0] += 3 * 600 + 10
            off = A.tick()["fired"]
            r = A.set_enabled(aid, True)
            same = A.tick()["fired"]
            now[0] = r["next_run"] + 1
            later = A.tick()["fired"]
            ok = off == [] and same == [] and r["next_run"] > T0 + 3 * 600 + 10 and len(later) == 1 and _jobs(k) == 1
            return ok, f"while disabled {len(off)}, right after enabling {len(same)} (missed slots not replayed), next slot {len(later)}"
        if m == "claimed_crash":
            aid = A.create(dict(JOB, trigger_kind="interval", schedule="3600"))["id"]
            a_ = A.get(aid)
            slot = a_["next_run"]
            A._claim(a_, f"{aid}:crash-slot", slot, "schedule")            # claimed, then the process died before firing
            k.db.close()
            k, A = _kernel(d, now)
            now[0] = slot + 60
            A.tick()
            mid = [r["status"] for r in A.runs(aid) if r["run_key"].endswith("crash-slot")]
            now[0] = slot + CLAIM_GRACE + 120
            A.tick()
            late = [r["status"] for r in A.runs(aid) if r["run_key"].endswith("crash-slot")]
            ok = mid == ["claimed"] and late == ["unknown"] and _jobs(k) == 1          # only the regular slot's job exists
            return ok, f"crashed run: {mid} within the grace, {late} after it; jobs {_jobs(k)}"
        if m == "once":
            when = T0 + 3600
            aid = A.create(dict(JOB, trigger_kind="once", schedule=str(when)))["id"]
            now[0] = when + 2
            A.tick()
            k.db.close()
            k, A = _kernel(d, now)
            A.tick()
            now[0] += 86400
            A.tick()
            ok = len(A.runs(aid)) == 1 and A.get(aid)["state"] == "completed" and _jobs(k) == 1
            return ok, f"runs {len(A.runs(aid))}, state {A.get(aid)['state']}, jobs {_jobs(k)}"
        if m == "auto_pause":
            aid = A.create(dict(JOB, trigger_kind="interval", schedule="600", pause_after_failures=2, overlap="queue", max_runs_per_day=50))["id"]
            for _ in range(2):
                now[0] += 610
                A.tick()
                k.db.execute("UPDATE jobs SET state='failed' WHERE id=?", (A.runs(aid)[0]["job_id"],))
                A.settle()
            cur = A.get(aid)
            now[0] += 5000
            after = A.tick()["fired"]
            ok = (cur["enabled"], cur["state"]) == (0, "paused") and "2 failed runs" in cur["paused_reason"] and after == []
            return ok, f"state {cur['state']} ({cur['paused_reason']}), fired after pause {len(after)}"
        raise ValueError(m)
    finally:
        k.db.close()


# ================================================================ 2026-10-17: dependencies, partial work, bounded retries, cancel and
# resume (D-044..D-083). Executor and run store: the real main_agent code with fake dispatchers. Kernel tasks, lab jobs and
# automations: a real kernel database in a temp folder, the real agent/runner HTTP endpoints on 127.0.0.1, fake lab runner.
def _arun(plan, dispatch, **kw):
    kw.setdefault("backoff", FAST)
    return asyncio.run(run_plan(plan, dispatch, **kw))


def _fmt(r):
    return ", ".join(f"{k}:{v.outcome}{'(held)' if v.held else ''}x{v.attempts}" for k, v in r.items())


def exec2(a):
    m = a["mode"]
    if m == "diamond":
        log = {}

        async def dispatch(step, inputs):
            log[step.id] = [time.monotonic(), None, dict(inputs)]
            await asyncio.sleep(0.25 if step.id == "c" else 0.1)
            log[step.id][1] = time.monotonic()
            return f"out-{step.id}"
        r = _by(_arun(_plan(S("a"), S("b", ["a"]), S("c", ["a"]), S("d", ["b", "c"])), dispatch, parallel=3))
        ok = all(v.ok for v in r.values()) and log["b"][0] >= log["a"][1] and log["c"][0] >= log["a"][1] \
            and log["d"][0] >= max(log["b"][1], log["c"][1]) and log["d"][2] == {"b": "out-b", "c": "out-c"} and abs(log["b"][0] - log["c"][0]) < 0.08
        return ok, (f"b started {log['b'][0] - log['a'][1]:+.3f}s after a ended, d started {log['d'][0] - max(log['b'][1], log['c'][1]):+.3f}s "
                    f"after b,c ended; b/c start gap {abs(log['b'][0] - log['c'][0]):.3f}s; inputs of d {log['d'][2]}")
    if m == "chain_fail":
        calls = []

        async def dispatch(step, inputs):
            calls.append(step.id)
            if step.id == "c":
                raise StepFailed("the page would not load")
            return f"out-{step.id}"
        p = _plan(S("a"), S("b", ["a"]), S("c", ["b"]), S("d", ["c"]), S("e", ["d"]), S("x"), S("y", ["x"]))
        res = _arun(p, dispatch)
        r, t = _by(res), final_text(p, res)
        ok = r["a"].ok and r["b"].ok and r["c"].outcome == "failure" and r["d"].outcome == r["e"].outcome == "not_executed" \
            and "d" not in calls and "e" not in calls and r["y"].ok and run_state(p, res) == "partial" and "out-y" in t \
            and "c (failure: the page would not load)" in t and "d (not executed: a step it needed did not finish)" in t and "e (not executed" in t
        return ok, _fmt(r) + f"; state {run_state(p, res)}; final '{t[:220]}'"
    if m == "explicit_unknown":
        calls = []

        async def dispatch(step, inputs):
            calls.append(step.id)
            if step.id == "send":
                raise StepUnknown("the server closed the connection after the request was sent")
            return "ok"
        p = _plan(S("send", kind="plugin", effect="non_idempotent"), S("after", ["send"]), S("other"))
        res = _arun(p, dispatch, parallel=1)
        r, t = _by(res), final_text(p, res)
        ok = calls == ["send"] and r["send"].outcome == "unknown" and r["send"].held and r["send"].attempts == 1 \
            and r["after"].outcome == r["other"].outcome == "not_executed" and "Needs your attention" in t \
            and "the server closed the connection" in t and run_state(p, res) == "held"
        return ok, _fmt(r) + f"; dispatched {calls}; final '{t[:200]}'"
    if m == "retry_limit":
        out = {}
        for n in (2, 0):
            calls = []

            async def dispatch(step, inputs):
                calls.append(1)
                raise StepFailed("upstream 503")
            r = _arun(_plan(S("a")), dispatch, retries=n)[0]
            out[n] = (len(calls), r.outcome, r.attempts, r.error)
        ok = out[2] == (3, "failure", 3, "upstream 503") and out[0] == (1, "failure", 1, "upstream 503")
        return ok, f"retries=2 -> (dispatches, outcome, attempts, error) {out[2]}; retries=0 -> {out[0]}"
    if m == "retry_no_time":
        out = {}
        for total in (1.5, 10.0):
            calls = []

            async def dispatch(step, inputs):
                calls.append(1)
                raise StepFailed("transient")
            r = _arun(_plan(S("a")), dispatch, total_timeout=total)[0]
            out[total] = (len(calls), r.attempts)
        return out[1.5] == (1, 1) and out[10.0] == (2, 2), f"1.5 s total limit -> (dispatches, attempts) {out[1.5]}; 10 s limit -> {out[10.0]}"
    if m == "cancel_no_retry":
        calls, holder = [], {}

        async def main():
            ev = asyncio.Event()

            async def dispatch(step, inputs):
                calls.append(step.id)
                if step.id == "a":
                    ev.set()                                   # the owner says stop while a is failing
                    raise RuntimeError("transient")
                return "ok"
            p = _plan(S("a"), S("b", ["a"]), S("c"))
            return p, await run_plan(p, dispatch, cancel=ev, parallel=1, retries=3, backoff=FAST)
        p, res = asyncio.run(main())
        r = _by(res)
        ok = calls == ["a"] and r["a"].attempts == 1 and r["b"].outcome == "not_executed" and r["c"].outcome == "not_executed" \
            and "cancelled" in r["c"].error and run_state(p, res, cancelled=True) == "cancelled"
        return ok, _fmt(r) + f"; dispatched {calls}; c '{r['c'].error}'"
    if m == "exc_kinds":
        calls = []

        async def dispatch(step, inputs):
            calls.append(step.id)
            raise RuntimeError("boom with detail")
        rr = _arun(_plan(S("read")), dispatch)[0]
        rw = _arun(_plan(S("write", kind="plugin", effect="non_idempotent")), dispatch)[0]
        ok = (rr.outcome, rr.attempts, rr.held, rr.error) == ("failure", 2, False, "RuntimeError") \
            and (rw.outcome, rw.attempts, rw.held, rw.error) == ("unknown", 1, True, "RuntimeError") and calls.count("write") == 1
        return ok, f"read-only: {rr.outcome} x{rr.attempts} held {rr.held}; with side effects: {rw.outcome} x{rw.attempts} held {rw.held}; errors '{rr.error}'/'{rw.error}'"
    if m == "parallel_cap":
        cur, peak = [0], [0]

        async def dispatch(step, inputs):
            cur[0] += 1
            peak[0] = max(peak[0], cur[0])
            await asyncio.sleep(0.1)
            cur[0] -= 1
            return "ok"
        t0 = time.monotonic()
        res = _arun(_plan(*[S(f"s{i}") for i in range(6)]), dispatch, parallel=2)
        sec = time.monotonic() - t0
        return peak[0] == 2 and all(x.ok for x in res) and sec >= 0.28, f"peak concurrency {peak[0]} (limit 2), {len(res)} steps in {sec:.2f} s"
    if m == "read_timeout":
        calls = []

        async def dispatch(step, inputs):
            calls.append(step.id)
            await asyncio.sleep(1.0)
            return "late"
        t0 = time.monotonic()
        r = _by(_arun(_plan(S("a"), S("b", ["a"])), dispatch, step_timeout=0.2))
        sec = time.monotonic() - t0
        ok = r["a"].outcome == "unknown" and not r["a"].held and calls == ["a", "a"] and r["b"].outcome == "not_executed" \
            and "did not finish" in r["b"].error and sec < 1.5
        return ok, _fmt(r) + f"; dispatched {calls} in {sec:.2f} s"
    if m == "cancel_before":
        calls = []

        async def main():
            ev = asyncio.Event()
            ev.set()

            async def dispatch(step, inputs):
                calls.append(step.id)
                return "ok"
            p = _plan(S("a"), S("b", ["a"]), S("c"))
            return p, await run_plan(p, dispatch, cancel=ev)
        p, res = asyncio.run(main())
        r = _by(res)
        ok = calls == [] and len(res) == 3 and all(x.outcome == "not_executed" for x in res) and run_state(p, res, cancelled=True) == "cancelled" \
            and all("cancelled" in x.error or "did not finish" in x.error for x in res)
        return ok, _fmt(r) + f"; dispatched {calls}; errors {[x.error for x in res]}"
    if m == "child_cancelled":
        flags = []

        async def dispatch(step, inputs):
            try:
                await asyncio.sleep(5)
                flags.append("finished")
            except asyncio.CancelledError:
                flags.append("cancelled")
                raise
            return "late"
        t0 = time.monotonic()
        r = _arun(_plan(S("a")), dispatch, total_timeout=0.4)[0]
        sec = time.monotonic() - t0
        return flags == ["cancelled"] and r.outcome == "unknown" and r.attempts == 1 and sec < 1.5, \
            f"child {flags}, outcome {r.outcome} x{r.attempts}, {sec:.2f} s (limit 0.4 s)"
    if m == "callbacks_raise":
        def boom(*x):
            raise RuntimeError("the UI went away")

        async def dispatch(step, inputs):
            return f"out-{step.id}"
        res = _arun(_plan(S("a"), S("b", ["a"])), dispatch, on_start=boom, on_progress=boom)
        return all(x.ok for x in res) and len(res) == 2, _fmt(_by(res))
    if m == "final_all_failed":
        async def dispatch(step, inputs):
            raise StepFailed(f"{step.id} could not reach the site")
        p = _plan(S("a"), S("b"))
        res = _arun(p, dispatch)
        t = final_text(p, res)
        ok = t.startswith("I could not finish this:") and "a (failure: a could not reach the site)" in t and "b (failure" in t \
            and run_state(p, res) == "failed" and "Not everything worked" not in t
        return ok, f"state {run_state(p, res)}; final '{t[:200]}'"
    if m == "state_lost_step":
        p = _plan(S("a"), S("b"), S("c"))
        two = [StepResult("a", "success", "x"), StepResult("b", "success", "y")]
        lost, full = run_state(p, two), run_state(p, two + [StepResult("c", "success", "z")])
        return lost == "partial" and full == "done", f"2 of 3 results, all ok -> '{lost}'; 3 of 3 -> '{full}'"
    if m == "prior_failure":
        calls = []

        async def dispatch(step, inputs):
            calls.append(step.id)
            return "ok"
        p = _plan(S("a", kind="plugin", effect="non_idempotent"), S("b", ["a"]), S("c"))
        res = _arun(p, dispatch, prior={"a": StepResult("a", "failure", error="the disk is full")})
        r = _by(res)
        ok = calls == ["c"] and r["a"].outcome == "failure" and r["a"].error == "the disk is full" and r["b"].outcome == "not_executed" \
            and r["c"].ok and run_state(p, res) == "partial"
        return ok, _fmt(r) + f"; dispatched {calls}"
    raise ValueError(m)


def store_roundtrip(a):
    """A run checkpointed after every step is restored exactly (outcome, attempts, waited, held) and only the missing step runs."""
    store = RunStore(_dir() / "runs")
    first = _plan(S("a"), S("b", ["a"]))
    run = {"run_id": "rt", "plan": plan_to_dict(first), "results": {}, "running": [], "state": "running"}
    store.save(run)
    seen = []

    async def dispatch(step, inputs, ctx):
        seen.append(step.id)
        if step.id == "a" and seen.count("a") == 1:
            raise RuntimeError("transient")                  # a needs 2 attempts: the count must survive the checkpoint
        ctx.add_waiting(0.05 if step.id == "b" else 0)
        return f"out-{step.id}"

    def save(step, res):
        run["results"][step.id] = result_to_dict(res)
        store.save(run)
    before = _by(_arun(first, dispatch, on_progress=save))
    rec = RunStore(store.dir).load("rt")                       # another process reads the checkpoint
    prior = {sid: result_from_dict({"id": sid, **rd}) for sid, rd in rec["results"].items()}
    seen.clear()
    full = _plan(S("a"), S("b", ["a"]), S("c", ["b"]))
    after = _by(_arun(full, dispatch, prior=prior))
    same = all(result_to_dict(prior[k]) == result_to_dict(before[k]) for k in ("a", "b"))
    ok = same and prior["a"].attempts == 2 and prior["b"].waited > 0 and seen == ["c"] and after["c"].ok and after["a"].text == "out-a"
    return ok, f"restored exactly {same} (a attempts {prior['a'].attempts}, b waited {prior['b'].waited:.2f}); resume dispatched {seen}"


STORE_CHILD2 = r'''
import asyncio, sys
from pathlib import Path
sys.path.insert(0, @ROOT@)
from jarvis.plugins.main_agent.executor import run_plan
from jarvis.plugins.main_agent.planner import Plan, Step
from jarvis.plugins.main_agent.store import RunStore, plan_to_dict, result_to_dict
d = Path(@TMP@)
plan = Plan("bench", [Step("send", "plugin", "send the summary", ref="x:send_message", effect="non_idempotent"),
                      Step("fetch", "think", "read the replies", depends_on=["send"]),
                      Step("report", "think", "report", depends_on=["fetch"])])
store = RunStore(d / "runs")
run = {"run_id": "r2", "plan": plan_to_dict(plan), "results": {}, "running": [], "state": "running"}
store.save(run)
def on_start(step):
    run["running"].append(step.id); store.save(run)
def on_progress(step, res):
    run["results"][step.id] = result_to_dict(res)
    if step.id in run["running"]:
        run["running"].remove(step.id)
    store.save(run)
async def dispatch(step, inputs):
    with (d / "acts.log").open("a") as f:
        f.write(step.id + "\n")
    if step.id == "fetch":
        await asyncio.sleep(60)          # killed here: a read, harmless to repeat
    return "ok-" + step.id
asyncio.run(run_plan(plan, dispatch, on_start=on_start, on_progress=on_progress))
'''


def store_kill_readonly(a):
    """Killed (SIGKILL) while a READ step runs after a send finished: recover marks the read unknown but not held; a resume
    that re-runs read-only unknowns (the plugin's resume rule, applied here) repeats the read, never the send."""
    d = _dir()
    p = subprocess.Popen([sys.executable, "-c", STORE_CHILD2.replace("@ROOT@", repr(str(ROOT))).replace("@TMP@", repr(str(d)))],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    store = RunStore(d / "runs")
    end = time.time() + 60
    while time.time() < end and (store.load("r2") or {}).get("running") != ["fetch"]:
        time.sleep(0.05)
    p.kill()
    p.wait()
    rec_ids = store.recover()
    res = store.load("r2")["results"]
    plan = Plan("bench", [Step("send", "plugin", "send", effect="non_idempotent"), Step("fetch", "think", "read", depends_on=["send"]),
                          Step("report", "think", "report", depends_on=["fetch"])])
    effects = {s.id: s.effect for s in plan.steps}
    prior = {}
    for sid, rd in res.items():
        r = result_from_dict({"id": sid, **rd})
        if r.outcome in ("unknown", "failure") and effects[sid] == "none":
            continue
        prior[sid] = r
    calls = []

    async def dispatch(step, inputs):
        calls.append(step.id)
        with (d / "acts.log").open("a") as f:
            f.write(step.id + "\n")
        return "ok"
    out = _arun(plan, dispatch, prior=prior)
    acts = (d / "acts.log").read_text().split()
    ok = rec_ids == ["r2"] and res["send"]["outcome"] == "success" and res["fetch"]["outcome"] == "unknown" and res["fetch"]["held"] is False \
        and calls == ["fetch", "report"] and acts.count("send") == 1 and run_state(plan, out) == "done"
    return ok, (f"recovered {rec_ids}; send {res['send']['outcome']}, fetch {res['fetch']['outcome']} held {res['fetch'].get('held')}; "
                f"resume dispatched {calls}; sends in total {acts.count('send')}; state {run_state(plan, out)}")


def store_recover_twice(a):
    """A step that finished but was still listed as running (crash between the two writes) stays a success; a second restart changes nothing."""
    s = RunStore(_dir() / "runs")
    s.save({"run_id": "r3", "state": "running", "plan": plan_to_dict(_plan(S("a", kind="plugin", effect="non_idempotent"), S("b"))),
            "running": ["a", "b"], "results": {"a": result_to_dict(StepResult("a", "success", "sent"))}})
    first = s.recover()
    snap = json.dumps(s.load("r3")["results"], sort_keys=True)
    second = s.recover()
    rec = s.load("r3")
    ok = first == ["r3"] and second == [] and rec["results"]["a"]["outcome"] == "success" and rec["results"]["b"]["outcome"] == "unknown" \
        and not rec["results"]["b"]["held"] and json.dumps(rec["results"], sort_keys=True) == snap and rec["state"] == "interrupted"
    return ok, f"first restart {first}, second {second}; a {rec['results']['a']['outcome']}, b {rec['results']['b']['outcome']}; unchanged by 2nd {json.dumps(rec['results'], sort_keys=True) == snap}"


def store_atomic_save(a):
    """The process dies in the middle of writing a checkpoint (fake: os.replace fails after the temp file is written): the
    previous checkpoint stays whole and readable, and the half-written temp file is never taken for a run."""
    from unittest import mock
    from jarvis.plugins.main_agent import store as ST
    s = RunStore(_dir() / "runs")
    s.save({"run_id": "r4", "state": "running", "step": 1, "results": {}, "running": [], "plan": plan_to_dict(_plan(S("a")))})
    crashed = False
    with mock.patch.object(ST.os, "replace", side_effect=OSError("power cut")):
        try:
            s.save({"run_id": "r4", "state": "running", "step": 2, "results": {}, "running": [], "plan": plan_to_dict(_plan(S("a")))})
        except OSError:
            crashed = True
    rec = s.load("r4")
    ok = crashed and rec is not None and rec["step"] == 1 and s.ids() == ["r4"]
    return ok, f"crash during save {crashed}; checkpoint after it: step {rec and rec.get('step')}; runs listed {s.ids()}"


# ---------------------------------------------------------------- missions (2026-10-17)
def mission_cancel_planning(a):
    with _engine() as (e, d):
        calls = []
        e.agents = {"check_pc": lambda s: calls.append(1) or {"ok": True}}
        e.plan_fn = lambda g, c: (time.sleep(0.6), [{"tool": "check_pc", "check": "disk"}, {"tool": "check_pc", "check": "cpu"}])[1]
        mid = M.start("check the disk and the cpu")
        time.sleep(0.2)
        accepted = M.cancel(mid)
        r = _wait(mid)
        ok = accepted and r["status"] == "cancelled" and not calls and not r["steps"] and "Not finished" in r.get("explain", "") \
            and C.current_task(e.lock_path) is None
        return ok, _short(r) + f"; agent calls {len(calls)}; explain '{r.get('explain', '')[:100]}'"


def mission_done_journal(a):
    """The process died after a step's effect was journaled done but before DBOS recorded the step: the resumed mission
    replays the journaled result instead of acting again."""
    with _engine() as (e, d):
        calls = []
        step = {"tool": "type_text", "app": "notepad", "text": "Dear sir"}
        e.agents = {"type_text": lambda s: calls.append("type") or {"ok": True}, "check_pc": lambda s: calls.append("check") or {"ok": True}}
        e.plan_fn = lambda g, c: [dict(step), {"tool": "check_pc", "check": "disk"}]
        mid = "m-bench-donejournal"
        M._q("insert into missions (id, goal, status, created, updated, record) values (?,?,?,?,?,?)", (mid, "g", "queued", time.time(), time.time(), "{}"))
        M._q("insert into effects (key, mission, tool, state, result, at) values (?,?,?,?,?,?)",
             (f"{mid}:{M._sig(step)}:0", mid, "type_text", "done", json.dumps({"ok": True, "typed": 8}), 0))
        with SetWorkflowID(mid):
            DBOS.start_workflow(M.run_mission, mid, "resume test")
        r = _wait(mid)
        rep = (r.get("steps") or [{}])[0].get("result", {}).get("replayed")
        return r["status"] == "completed" and calls == ["check"] and rep is True, _short(r) + f"; agent calls {calls}; first step replayed {rep}"


def mission_same_step_twice(a):
    """The plan types the same text twice on purpose: each occurrence is its own effect (no false de-duplication)."""
    with _engine() as (e, d):
        calls = []
        e.agents = {"type_text": lambda s: calls.append(s["text"]) or {"ok": True}, "check_pc": lambda s: calls.append(s["check"]) or {"ok": True}}
        e.plan_fn = lambda g, c: [{"tool": "type_text", "app": "notepad", "text": "-----"}, {"tool": "check_pc", "check": "disk"},
                                  {"tool": "type_text", "app": "notepad", "text": "-----"}]
        r = _wait(M.start("type a divider, check the disk, type another divider"))
        return r["status"] == "completed" and calls == ["-----", "disk", "-----"], _short(r) + f"; agent calls {calls}"


def mission_plan_crash(a):
    """The planner itself crashes: the mission must end in a final, explained state (never stay 'queued') and leave the PC free."""
    with _engine() as (e, d):
        logging.getLogger("dbos").setLevel(logging.CRITICAL)        # the expected workflow error is not bench output

        def bad(g, c):
            raise RuntimeError("planner model returned garbage")
        e.plan_fn = bad
        mid = M.start("do something")
        end, r = time.time() + 8, None
        while time.time() < end:
            r = M.load(mid)
            if r and r.get("status") not in ("queued", "running"):
                break
            time.sleep(0.1)
        st = (r or {}).get("status")
        ok = st not in (None, "queued", "running", "completed") and C.current_task(e.lock_path) is None
        return ok, f"status 8 s after the planner crashed: '{st}' (error {str((r or {}).get('error'))[:80]}); PC lock free {C.current_task(e.lock_path) is None}"


# ---------------------------------------------------------------- kernel outcome tasks and lab jobs (2026-10-17)
@contextmanager
def _proxy():
    """A real kernel and its agent/runner HTTP endpoints on 127.0.0.1 (the lab runner is played by this check)."""
    import threading
    import urllib.error
    import urllib.request
    from http.server import ThreadingHTTPServer
    from types import SimpleNamespace
    from jarvis_kernel.llm_proxy import make_handler
    d = _dir()
    k = Kernel(str(d / "k"))
    H, tokens = make_handler(k, [], owner_hermes=None)
    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{srv.server_address[1]}"

    def post(path, body, tok):
        r = urllib.request.Request(url + path, data=json.dumps(body).encode(), method="POST",
                                   headers={"Content-Type": "application/json", "Authorization": "Bearer " + tok})
        try:
            resp = urllib.request.urlopen(r, timeout=30)
            return resp.status, json.loads(resp.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read() or b"{}")
    P = SimpleNamespace(k=k, d=d, post=post, owner=tokens.issue("hermes"), runner=tokens.issue("lab-runner", "runner"), T=Tasks(k), J=Jobs(k))
    P.act = lambda op, target, params, tok=None: post("/v1/jarvis/actions", {"tool": "task", "op": op, "target": target, "params": params}, tok or P.owner)
    P.run = lambda path, body: post("/v1/jarvis/runner/" + path, body, P.runner)
    try:
        yield P
    finally:
        srv.shutdown()
        srv.server_close()
        k.db.close()


def _owner_approved_dispatch(P, tid, params):
    """Since 2026-10-18 (audit SEC-D5) a private agent's dispatch to the public-lane lab waits for the OWNER's approval of
    exactly the shared text. The cases below test the lab lifecycle after a dispatch, so they take the owner's step first,
    as the console would (Kernel.approve is the human step); what each case checks is unchanged."""
    code, r = P.act("dispatch", tid, params)
    if code == 202 and r.get("status") == "needs_approval":
        assert P.k.approve(r["approval_id"])
        code, r = P.post("/v1/jarvis/actions", {"tool": "task", "op": "dispatch", "target": tid, "params": {"public_ok": True},
                                                "approval_id": r["approval_id"]}, P.owner)
    return code, r


def _lab_task(P, attempts=2, start=True):
    tid = P.act("create", "t", {"goal": "A small tool", "criteria": ["tests pass"], "max_attempts": attempts,
                                "request": f"build me a small tool {time.time()}"})[1]["task"]
    code, r = _owner_approved_dispatch(P, tid, {"instructions": "build it and test it", "public_ok": True})
    job = None
    if start:
        job = P.run("next", {})[1]["job"]["id"]
        P.run("report", {"job": job, "state": "running", "attempt": True, "step": "attempt 1"})
    return tid, (job or r.get("job"))


def task_retry_restart(a):
    """The retry budget is kept in the kernel database: a restart between attempts does not reset it."""
    d = _dir() / "k"
    k = Kernel(str(d))
    T = Tasks(k)
    t = T.create("hermes", "public", "", "A tool", criteria=["tests pass"], max_attempts=2)
    T.update(None, t, state="running", attempt=True)
    T.update(None, t, state="verifying")
    k.db.close()
    k = Kernel(str(d))                                             # restart
    T = Tasks(k)
    T.update(None, t, state="repairing", note="tests fail")
    T.update(None, t, state="running", attempt=True)
    T.update(None, t, state="verifying")
    k.db.close()
    k = Kernel(str(d))                                             # restart again
    T = Tasks(k)
    refused = []
    for kw in ({"state": "repairing"}, {"attempt": True}):
        try:
            T.update(None, t, **kw)
        except Exception as e:  # noqa: BLE001
            refused.append(str(e)[:40])
    T.update(None, t, state="failed", result="not verified after 2 attempt(s): tests fail")
    g = T.get(t)
    k.db.close()
    ok = len(refused) == 2 and all("budget" in x for x in refused) and (g["state"], g["attempts"]) == ("failed", 2) and "2 attempt" in g["result"]
    return ok, f"after two restarts: extra attempt refused {refused}; final {g['state']} attempts {g['attempts']}/{g['max_attempts']}"


def task_expire_partial(a):
    d = _dir() / "k"
    k = Kernel(str(d))
    try:
        T = Tasks(k)
        t = T.create("hermes", "private", "", "Write three pages", budget_s=60)
        T.update("hermes", t, state="running", result="draft of page 1 of 3 written")
        gone = T.expire(time.time() + 61)
        g = T.get(t)
    finally:
        k.db.close()
    ok = gone == [t] and g["state"] == "failed" and "time budget" in g["result"] and "page 1 of 3" in g["result"]
    return ok, f"state {g['state']}; result '{g['result'][:120]}'"


def task_final_stays(a):
    d = _dir() / "k"
    k = Kernel(str(d))
    try:
        T = Tasks(k)
        out = {}
        for end in ("cancelled", "completed", "failed"):
            t = T.create("hermes", "public", "", f"goal {end}")
            T.update(None, t, state="running", attempt=True)
            if end == "completed":
                T.update(None, t, state="verifying")
            T.update(None, t, state=end, result=f"ended {end}")
            late = []
            for st in ("running", "completed", "failed", "verifying"):
                try:
                    T.update(None, t, state=st, result="late runner report")
                    late.append(st)
                except Exception:  # noqa: BLE001
                    pass
            g = T.get(t)
            finals = k.db.execute("SELECT COUNT(*) FROM audit WHERE kind IN ('task_completed','task_failed','task_cancelled') AND payload LIKE ?",
                                  (f'%{t}%',)).fetchone()[0]
            out[end] = (g["state"], g["result"], late, finals)
    finally:
        k.db.close()
    ok = all(v[0] == e and v[1] == f"ended {e}" and v[2] == [] and v[3] == 1 for e, v in out.items())
    return ok, "; ".join(f"{e}: state {v[0]}, result '{v[1]}', late reports accepted {v[2]}, final audit rows {v[3]}" for e, v in out.items())


def task_checkpoint_honest(a):
    d = _dir() / "k"
    k = Kernel(str(d))
    try:
        T = Tasks(k)
        f = T.create("hermes", "public", "", "Deploy the site", criteria=["site returns 200"])
        T.update("hermes", f, state="running", attempt=True, step="attempt 1: building")
        T.update("hermes", f, state="verifying")
        T.update("hermes", f, state="repairing", note="page 2 returns 500")
        T.update("hermes", f, state="running", attempt=True)
        T.update("hermes", f, state="failed", result="not verified after 2 attempt(s): page 2 returns 500")
        o = T.create("hermes", "public", "", "Write the report", criteria=["3 sections"])
        T.update("hermes", o, state="running", attempt=True)
        cp = T.checkpoint("hermes")
    finally:
        k.db.close()
    by = {c["id"]: c for c in cp["tasks"]}
    txt = cp["text"]
    ok = by[f]["state"] == "failed" and by[f]["attempts"] == "2/3" and "page 2 returns 500" in by[f]["result"] \
        and by[f]["next"].startswith("tell the owner the exact blocker") and by[o]["next"].startswith("wait: the lab is working") \
        and f"Task {f} [failed, attempts 2/3]" in txt and "not verified" in txt
    return ok, f"checkpoint text: '{txt[:260]}'"


def lab_cancel_midrun(a):
    with _proxy() as P:
        from jarvis_kernel.owner import Owner
        tid, job = _lab_task(P)
        Owner(P.k, P.T, P.J).task_control(tid, "cancel")
        status = P.run("status", {"job": job})[1].get("state")
        late = P.run("report", {"job": job, "state": "verifying", "step": "attempt 1: independent check"})[0]
        fin = P.run("finish", {"job": job, "result": {"ok": False, "summary": "stopped by the owner", "error": "stopped by the owner"}})[0]
        g = P.T.get(tid)
        js = P.k.db.execute("SELECT state FROM jobs WHERE id=?", (job,)).fetchone()[0]
    ok = status == "cancelled" and late == 409 and fin == 200 and g["state"] == "cancelled" and js == "failed"
    return ok, f"runner sees '{status}'; late report -> {late}; finish -> {fin}; task {g['state']}; lab job {js}"


def lab_runner_crash(a):
    """The lab runner crashes after starting attempt 1 and only manages to post its finish (ok false). The task must not keep
    saying 'running' until its deadline."""
    with _proxy() as P:
        tid, job = _lab_task(P)
        fin = P.run("finish", {"job": job, "result": {"ok": False, "summary": "runner error: KeyError", "error": "runner error: KeyError"}})[0]
        g = P.T.get(tid)
    ok = fin == 200 and g["state"] == "failed" and "runner error: KeyError" in g["result"]
    return ok, f"finish -> {fin}; task state '{g['state']}', result '{g['result'][:120]}'"


def lab_job_expired(a):
    """The lab runner is offline: the queued lab job expires. After the kernel's sweep the task must say so, not 'queued'."""
    with _proxy() as P:
        tid, job = _lab_task(P, start=False)
        P.k.db.execute("UPDATE jobs SET expires=? WHERE id=?", (time.time() - 1, job))
        sw = P.J.sweep()
        g = P.T.get(tid)
    ok = sw["expired"] == 1 and g["state"] == "failed" and "expired" in g["result"]
    return ok, f"sweep {sw}; task state '{g['state']}', result '{g['result'][:120]}'"


def lab_job_cancelled(a):
    with _proxy() as P:
        from jarvis_kernel.owner import Owner
        tid, job = _lab_task(P, start=False)
        out = Owner(P.k, P.T, P.J).cancel_job(job)
        g = P.T.get(tid)
        nxt = P.run("next", {})[1].get("job")
    ok = out["state"] == "cancelled" and g["state"] == "cancelled" and nxt is None
    return ok, f"cancel_job -> {out['state']}; task '{g['state']}'; runner then gets {nxt}"


def lab_runner_killed(a):
    """The kill switch is on: the lab runner must not be handed new work; after resume it is."""
    with _proxy() as P:
        tid, job = _lab_task(P, start=False)
        P.k.kill("bench")
        code, r = P.run("next", {})
        got = (r or {}).get("job")
        st = P.k.db.execute("SELECT state FROM jobs WHERE id=?", (job,)).fetchone()[0]
        P.k.resume()
        after = P.run("next", {})[1].get("job")
    ok = got is None and st == "queued" and after and after["id"] == job
    return ok, f"while killed: /runner/next -> {code} job {got and got['id']}, job state {st}; after resume -> {after and after['id']}"


def lab_dispatch_restart(a):
    """The same task is dispatched again after a kernel restart: the active lab job is found in the database, not in memory."""
    from http.server import ThreadingHTTPServer
    with _proxy() as P:
        tid, job = _lab_task(P, start=False)
        d = P.d
    import threading
    import urllib.error
    import urllib.request
    from jarvis_kernel.llm_proxy import make_handler
    k = Kernel(str(d / "k"))                                     # restart: a new kernel process on the same data
    try:
        H, tokens = make_handler(k, [], owner_hermes=None)
        tok = tokens.issue("hermes")
        srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        try:
            req = urllib.request.Request(f"http://127.0.0.1:{srv.server_address[1]}/v1/jarvis/actions", method="POST",
                                         data=json.dumps({"tool": "task", "op": "dispatch", "target": tid,
                                                          "params": {"instructions": "build it again", "public_ok": True}}).encode(),
                                         headers={"Content-Type": "application/json", "Authorization": "Bearer " + tok})
            try:
                code = urllib.request.urlopen(req, timeout=30).status
            except urllib.error.HTTPError as e:
                code = e.code
        finally:
            srv.shutdown()
            srv.server_close()
        n = k.db.execute("SELECT COUNT(*) FROM jobs WHERE kind='lab_run'").fetchone()[0]
    finally:
        k.db.close()
    return code == 409 and n == 1, f"second dispatch after the restart -> {code}; lab jobs {n}"


def lab_deadline_midrun(a):
    with _proxy() as P:
        tid, job = _lab_task(P)
        P.k.db.execute("UPDATE tasks SET deadline=? WHERE id=?", (time.time() - 1, tid))
        late = P.run("report", {"job": job, "state": "verifying"})[0]
        status = P.run("status", {"job": job})[1].get("state")
        fin = P.run("finish", {"job": job, "result": {"ok": False, "summary": "task closed", "error": "task closed"}})[0]
        g = P.T.get(tid)
    ok = late == 409 and status == "failed" and fin == 200 and g["state"] == "failed" and g["result"].startswith("time budget used up")
    return ok, f"report after the deadline -> {late}; runner sees '{status}'; task {g['state']} '{g['result'][:80]}'"


# ---------------------------------------------------------------- automations (2026-10-17)
LAB = {"name": "nightly build", "action_kind": "lab_task", "trigger_kind": "interval", "schedule": "86400",
       "action": {"goal": "Build the site", "instructions": "build the static site and run its checks", "criteria": ["the site builds"], "agent": "hermes"}}
CMD = {"name": "morning brief", "action_kind": "command", "trigger_kind": "interval", "schedule": "86400",
       "action": {"text": "summarise what changed overnight"}}


def auto2(a):
    m, d, now = a["mode"], _dir() / "k", [T0]
    k = Kernel(str(d))
    T, J = Tasks(k), Jobs(k)
    try:
        if m == "lab_refused":
            calls = []

            def dispatch(agent, lane, tid, p):                     # fake lab path: the lab is busy
                calls.append(tid)
                return 409, {"status": "refused", "reason": "the lab is busy with another task"}
            A = Automations(k, T, J, owner=None, dispatch=dispatch, clock=lambda: now[0])
            aid = A.create(LAB)["id"]
            now[0] = A.get(aid)["next_run"] + 5
            A.tick()
            run = A.runs(aid)[0]
            t = T.get(run["task_id"]) if run["task_id"] else {}
            ok = len(calls) == 1 and run["status"] == "refused" and "busy" in (run["detail"] or "") and t.get("state") == "failed" \
                and "not dispatched" in (t.get("result") or "")
            return ok, f"run {run['status']} ({run['detail']}); task {t.get('state')} '{t.get('result')}'"
        A = Automations(k, T, J, owner=None, dispatch=None, clock=lambda: now[0])
        if m == "cancelled_job":
            aid = A.create(dict(JOB, trigger_kind="interval", schedule="600"))["id"]
            now[0] += 610
            first = A.tick()["fired"]
            J.cancel(A.runs(aid)[0]["job_id"])                    # the owner stops the job before the laptop ran it
            A.tick()
            now[0] += 600
            second = A.tick()["fired"]
            st = [r["status"] for r in A.runs(aid)]
            ok = len(first) == 1 and len(second) == 1 and _jobs(k) == 2
            return ok, f"after the owner cancelled run 1's job: next slot fired {len(second)}; run statuses (newest first) {st}; jobs {_jobs(k)}"
        if m == "killed":
            from jarvis_kernel.automations import AutomationError
            aid = A.create(dict(JOB, trigger_kind="interval", schedule="600"))["id"]
            now[0] += 610
            k.kill("bench")
            try:
                A.run_now(aid)
                manual = "ran"
            except AutomationError as e:
                manual = e.code
            off = A.tick()["fired"]
            n_off = (len(A.runs(aid)), _jobs(k))
            k.resume()
            on = A.tick()["fired"]
            again = A.tick()["fired"]
            ok = manual == 503 and off == [] and n_off == (0, 0) and len(on) == 1 and again == [] and _jobs(k) == 1
            return ok, f"run now while killed -> {manual}; tick while killed fired {len(off)} (runs, jobs {n_off}); after resume {len(on)} then {len(again)}; jobs {_jobs(k)}"
        if m == "daily_limit":
            aid = A.create(dict(JOB, trigger_kind="interval", schedule="600", catchup="all", overlap="queue", max_runs_per_day=3))["id"]
            now[0] += 10 * 600 + 5
            A.tick()
            st = [r["status"] for r in A.runs(aid, 50)]
            ok = st.count("fired") == 3 and st.count("skipped_limit") == 7 and _jobs(k) == 3
            return ok, f"10 missed slots, limit 3 per day: fired {st.count('fired')}, skipped_limit {st.count('skipped_limit')}, jobs {_jobs(k)}"
        raise ValueError(m)
    finally:
        k.db.close()


def auto_command(a):
    """A command automation while JARVIS (fake agent) is busy waits, then fires exactly once; if JARVIS stays busy past the
    claim grace the run fails (bounded) instead of retrying forever."""
    from jarvis_kernel.automations import CLAIM_GRACE
    from jarvis_kernel.owner import Owner
    d, now = _dir() / "k", [T0]
    k = Kernel(str(d))
    try:
        sent = []
        o = Owner(k, Tasks(k), Jobs(k), hermes=lambda text: sent.append(text) or "done", clock=lambda: now[0], spawn=lambda fn: fn())
        A = Automations(k, o.tasks, o.jobs, owner=o, dispatch=None, clock=lambda: now[0])
        aid = A.create(CMD)["id"]
        slot = A.get(aid)["next_run"]
        o.busy.acquire()                                          # JARVIS is answering another command
        now[0] = slot + 5
        A.tick()
        now[0] = slot + 65
        A.tick()
        waiting = A.runs(aid)[0]["status"]
        if a["mode"] == "fires_once":
            o.busy.release()
            now[0] = slot + 125
            A.tick()
            now[0] = slot + 185
            A.tick()
            A.tick()
            n = k.db.execute("SELECT COUNT(*) FROM owner_commands").fetchone()[0]
            st = A.runs(aid)[0]["status"]
            ok = waiting == "claimed" and len(sent) == 1 and n == 1 and st in ("fired", "done")
            return ok, f"while busy: {waiting}; after: run {st}, agent calls {len(sent)}, command rows {n}"
        now[0] = slot + CLAIM_GRACE + 60
        A.tick()
        st, det = A.runs(aid)[0]["status"], A.runs(aid)[0]["detail"]
        o.busy.release()
        A.tick()
        streak = A.get(aid)["failure_streak"]
        ok = waiting == "claimed" and st == "failed" and "busy too long" in (det or "") and not sent and streak == 1
        return ok, f"while busy: {waiting}; after the grace: {st} ({det}); agent calls {len(sent)}; failure streak {streak}"
    finally:
        k.db.close()
