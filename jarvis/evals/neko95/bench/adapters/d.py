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
os.environ.setdefault("NEKO_STORAGE_SELECTED_ROOT", _TMP)
ROOT = Path(__file__).resolve().parents[5]

from dbos import DBOS, SetWorkflowID  # noqa: E402

from plugin.plugins.desktop import coordinator as C  # noqa: E402
from plugin.plugins.desktop import missions as M  # noqa: E402
from plugin.plugins.main_agent.executor import (StepFailed, StepResult, StepUnknown, final_text, run_plan,  # noqa: E402
                                                run_state)
from plugin.plugins.main_agent.planner import Plan, Step  # noqa: E402
from plugin.plugins.main_agent.store import RunStore, plan_to_dict, result_from_dict, result_to_dict  # noqa: E402
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
from plugin.plugins.desktop import missions as M
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
    env = dict(os.environ, NEKO_STORAGE_SELECTED_ROOT=str(d))

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
from plugin.plugins.main_agent.executor import run_plan
from plugin.plugins.main_agent.planner import Plan, Step
from plugin.plugins.main_agent.store import RunStore, plan_to_dict, result_to_dict
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
