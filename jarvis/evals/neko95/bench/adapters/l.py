"""L: what the owner is told — final status text, explanations, permission prompts, cancellation and progress.

Drives the real coordinator (plugin.plugins.desktop.coordinator: run + summary), core.Pc's approval wait, missions.explain,
the main agent's executor texts and plugin entries, and the kernel's approval record. The PC agents, the console HTTP
calls and the main agent's model are fixtures (fake results), so nothing acts on a real PC.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import tempfile
import time
from pathlib import Path

from plugin.plugins.desktop import coordinator as C
from plugin.plugins.desktop import decide as D

PROFILES = [{"profile": "Default", "name": "Venkat", "account": "ven***", "default": True, "say": ["default", "venkat"]},
            {"profile": "Profile 2", "name": "Satish", "account": "sat***", "default": False, "say": ["satish"]}]
OK = {"open_website": {"ok": True, "profile": "Default", "verified": "window", "profile_name_matches": True},
      "open_app": {"ok": True, "opened": "notepad", "verified": "verified"},
      "type_text": {"ok": True, "process": "notepad.exe"},
      "check_pc": {"ok": True, "free_gb": 40},
      "press_key": {"ok": True},
      "open_file": {"ok": True, "opened": "downloads", "verified": "verified"},
      "browser": {"ok": True, "matches": True, "seen": ["ven***"], "expected": "ven***"},
      "stop": {"ok": True}}


def _contains(text: str, a: dict) -> list[str]:
    low = (text or "").lower()
    bad = [f"lacks {w!r}" for w in a.get("summary_contains", []) if w.lower() not in low]
    bad += [f"says {w!r}" for w in a.get("summary_not_contains", []) if w.lower() in low]
    return bad


def coord_run(a):
    """A whole PC task through the real Coordinator with fake agents; the owner-facing text is coordinator.summary()."""
    if "plan" in a:
        plan = D.Plan([dict(s) for s in a["plan"]], a.get("plan_status", "ok"), question=a.get("question", ""), model="fixture",
                      problems=list(a.get("problems", [])))
    else:                                                     # a model plan (fixture) through the real validate()
        plan = D.validate(json.dumps({"steps": a["model_plan"]}), PROFILES, a["request"])
    results = {k: list(v) for k, v in (a.get("results") or {}).items()}
    calls, progress = [], []

    def agent(tool):
        def run(step):
            calls.append(tool)
            seq = results.get(tool)
            r = seq.pop(0) if seq else OK.get(tool, {"ok": True})
            if isinstance(r, str) and r == "raise":
                raise RuntimeError("agent crashed")
            return r
        return run

    agents = {t: agent(t) for t in list(D.TOOLS) + ["open_output"]}
    tick = [0.0]

    def clock():
        tick[0] += float(a.get("tick", 0.01))
        return 1_000_000.0 + tick[0]

    with tempfile.TemporaryDirectory() as t:
        lock = Path(t) / "lock"
        if a.get("lock_held"):
            lock.write_text(json.dumps({"task": "20261016-120000-bbbbbb", "pid": os.getpid(), "at": time.time()}), encoding="utf-8")
        cancel_after = a.get("cancel_after")
        c = C.Coordinator(agents, PROFILES, tasks_dir=Path(t) / "tasks", lock_path=lock, clock=clock,
                          budget_s=float(a.get("budget_s", 600)), on_progress=lambda s, d: progress.append(f"{s}: {d}"),
                          cancelled=(lambda: cancel_after is not None and len(calls) >= cancel_after))
        rec = c.run(a.get("request", "x"), plan=plan)
        lock_left = lock.exists() and not a.get("lock_held")
    text = C.summary(rec)
    bad = _contains(text, a)
    if "status" in a and rec["status"] != a["status"]:
        bad.append(f"status {rec['status']} != {a['status']}")
    if "calls" in a and calls != a["calls"]:
        bad.append(f"agents called {calls} != {a['calls']}")
    for w in a.get("progress_contains", []):
        if not any(w.lower() in p.lower() for p in progress):
            bad.append(f"no progress {w!r}")
    if a.get("progress_ends_idle") and (not progress or not progress[-1].startswith("idle")):
        bad.append(f"progress does not end idle: {progress[-1:]}")
    if lock_left:
        bad.append("the PC lock was left held")
    return not bad, ("; ".join(bad) + " | " if bad else "") + f"status {rec['status']}: {text!r}"[:400] + (f" progress={progress}" if a.get("show_progress") else "")


def pc_permission(a):
    """core.Pc meets an action the kernel wants approved; the console calls are fixtures."""
    from plugin.plugins.desktop import core
    states, aid = [], "a1b2c3d4e5f60718"

    def call(method, path, body):
        if method == "POST" and path == "pc":
            if body.get("approval_id"):
                return 200, {"job": "j1"}
            return 200, {"status": "needs_approval", "approval_id": aid, "level": "YELLOW_APPROVAL", "reason": "rule browser.click"}
        if path == "state":
            return 200, {"approvals": [{"id": aid}]}                 # still pending: the owner has not answered
        return 200, {}
    pc = core.Pc(call=call, sleep=lambda s: None, clock=time.time, on_state=lambda s, d: states.append((s, d)), approval_wait_s=0.0)
    if a.get("stop_first"):
        pc.stopped = True
    r = pc.run({"op": "browser", "action": "click", "target": "Buy now"}, 30, a.get("what", ""))
    shown = " | ".join(d for s, d in states if s == "waiting_for_permission")
    bad = []
    if r.get("state") != a["state"]:
        bad.append(f"state {r.get('state')} != {a['state']}")
    for w in a.get("shown_contains", []):
        if w.lower() not in shown.lower():
            bad.append(f"progress text lacks {w!r}")
    for w in a.get("how_contains", []):
        if w.lower() not in str(r.get("how") or "").lower():
            bad.append(f"'how' lacks {w!r}")
    for w in a.get("error_contains", []):
        if w.lower() not in str((r.get("result") or {}).get("error") or "").lower():
            bad.append(f"error lacks {w!r}")
    return not bad, ("; ".join(bad) + " | " if bad else "") + f"shown {shown!r}; result {json.dumps(r)[:250]}"


def kernel_approval(a):
    """The approval the owner sees in the console: what (tool, op, target), how risky, why, and how long it is valid."""
    from jarvis_kernel.policy import Action
    from jarvis_kernel.store import Kernel
    tmp = tempfile.TemporaryDirectory()
    k = Kernel(os.path.join(tmp.name, "d"))
    try:
        d, aid = k.request(Action(a["tool"], a["op"], a["target"], a.get("params") or {}))
        pend = k.pending_approvals()
        row = next((p for p in pend if p["id"] == aid), None)
        if not row:
            return False, f"no pending approval (level {d.name}, aid {aid})"
        act = row["action"]
        bad = []
        if act.get("target") != a["target"] or act.get("tool") != a["tool"] or act.get("op") != a["op"]:
            bad.append(f"action shown {act}")
        if not row.get("reason") or row.get("level") not in ("YELLOW_APPROVAL", "RED") or row.get("seconds_left", 0) <= 0 or not row.get("digest"):
            bad.append(f"reason/level/expiry/digest missing: {row}")
        if a.get("secret") and a["secret"] in json.dumps(row):
            bad.append("a secret is shown in the approval")
        return not bad, ("; ".join(bad) + " | " if bad else "") + f"{row['level']} {act.get('tool')}.{act.get('op')} {act.get('target')!r} why={row['reason']!r} {row['seconds_left']} s"
    finally:
        k.db.close()
        tmp.cleanup()


def mission_explain(a):
    from plugin.plugins.desktop import missions
    text = missions.explain(a["record"])
    bad = _contains(text, a)
    return not bad, ("; ".join(bad) + " | " if bad else "") + repr(text)[:400]


def _plan(steps):
    from plugin.plugins.main_agent.planner import Plan, Step, effect_of
    return Plan("fixture", [Step(s["id"], s.get("kind", "think"), s.get("goal", s["id"]), s.get("ref", ""), {}, s.get("deps", []),
                                 effect_of(s.get("kind", "think"), s.get("ref", ""))) for s in steps])


def main_final(a):
    from plugin.plugins.main_agent.executor import StepResult, final_text, run_state
    plan = _plan(a["steps"])
    results = [StepResult(r["id"], r.get("outcome", "success"), r.get("text", ""), r.get("error", ""), held=r.get("held", False)) for r in a["results"]]
    text, state = final_text(plan, results), run_state(plan, results, a.get("cancelled", False))
    bad = _contains(text, a)
    if state != a["state"]:
        bad.append(f"state {state} != {a['state']}")
    return not bad, ("; ".join(bad) + " | " if bad else "") + f"{state}: {text!r}"[:400]


def _agent_plugin(plan_json, dispatch):
    from plugin.plugins import main_agent as ma
    from plugin.plugins.main_agent.store import RunStore
    p = object.__new__(ma.MainAgentPlugin)
    p._runs, p._active, p._cancel = {}, None, None
    p._store = RunStore(Path(tempfile.mkdtemp()) / "runs")
    p.logger = logging.getLogger("bench")
    p.pushed = []
    p.push_message = lambda **kw: p.pushed.append(kw)

    async def catalogue(mode="work"):
        from plugin.plugins.main_agent.planner import Capability
        return [Capability("think", "think", "t"), Capability("plugin", "learn_vault:save_note", "save", None)]

    async def llm(messages, **kw):
        return json.dumps(plan_json)

    p._catalogue, p._llm, p._dispatch = catalogue, llm, dispatch
    return p


PLAN2 = {"summary": "Think then save", "steps": [{"id": "s1", "agent": "think", "goal": "write"},
                                                  {"id": "s2", "agent": "plugin", "plugin": "learn_vault:save_note", "goal": "save",
                                                   "args": {"content": "{{s1}}"}, "depends_on": ["s1"]}]}


async def _until_idle(p, limit=5.0):
    end = time.monotonic() + limit
    while p._active is not None and time.monotonic() < end:
        await asyncio.sleep(0.01)


def main_cancel(a):
    """The owner presses Stop while step 1 runs: the run, its steps and the message all say it was stopped."""
    async def go():
        started = asyncio.Event()

        async def dispatch(step, inputs, ctx=None):
            started.set()
            while not (p._cancel and p._cancel.is_set()):
                await asyncio.sleep(0.01)
            return "partial text"
        p = _agent_plugin(PLAN2, dispatch)
        r = await p.run_task(task="write and save")
        await asyncio.wait_for(started.wait(), 5)
        s = await p.stop_task()
        await _until_idle(p)
        st = await p.run_status()
        return p, r, s, st
    p, r, s, st = asyncio.run(go())
    v = st.value
    pushed = " ".join(part["text"] for m in p.pushed for part in m.get("parts", []))
    bad = []
    if not s.value.get("stopped"):
        bad.append("stop_task did not stop")
    if v["state"] != "cancelled":
        bad.append(f"state {v['state']}")
    if v["steps"]["s2"]["state"] != "not_executed" or "cancel" not in v["steps"]["s2"].get("error", ""):
        bad.append(f"s2 {v['steps']['s2']}")
    if "stopped" not in pushed.lower():
        bad.append(f"message {pushed!r}")
    return not bad, ("; ".join(bad) + " | " if bad else "") + f"state {v['state']}, s2 {v['steps']['s2']['state']} ({v['steps']['s2'].get('error')}), told {pushed!r}, resumable {v['resumable']}"


def main_status(a):
    """run_status after a finished or failed run: per-step state, attempts, effect, waited time and resumability."""
    fail = a.get("fail_step")

    async def go():
        from plugin.plugins.main_agent.executor import StepFailed

        async def dispatch(step, inputs, ctx=None):
            if step.id == fail:
                raise StepFailed("learn_vault:save_note failed: the vault folder is not bound")
            return f"result of {step.id}"
        p = _agent_plugin(PLAN2, dispatch)
        await p.run_task(task="write and save")
        await _until_idle(p)
        return p, await p.run_status()
    p, st = asyncio.run(go())
    v = st.value
    keys = {k for s in v["steps"].values() for k in s}
    pushed = " ".join(part["text"] for m in p.pushed for part in m.get("parts", []))
    bad = []
    need = {"agent", "goal", "state", "effect", "error", "attempts", "held", "waited"}
    if need - keys:
        bad.append(f"step fields missing {sorted(need - keys)}")
    if v["state"] != a["state"] or v["resumable"] != a["resumable"]:
        bad.append(f"state {v['state']} resumable {v['resumable']}")
    bad += _contains(pushed, a)
    return not bad, ("; ".join(bad) + " | " if bad else "") + f"state {v['state']}, steps {[(k, s['state'], s['effect']) for k, s in v['steps'].items()]}, told {pushed!r}"[:450]


def main_refusals(a):
    """Messages when the main agent cannot start: busy, empty task, unknown mode."""
    async def go():
        async def dispatch(step, inputs, ctx=None):
            return "x"
        p = _agent_plugin(PLAN2, dispatch)
        out = {"empty": await p.run_task(task="  "), "mode": await p.run_task(task="x", mode="root")}
        p._active = "abc"
        out["busy"] = await p.run_task(task="write and save")
        out["resume_busy"] = await p.resume_run()
        return out
    out = asyncio.run(go())
    msgs = {k: str(getattr(v, "error", "")) for k, v in out.items()}
    bad = [f"{k} not refused" for k, v in out.items() if type(v).__name__ != "Err"]
    for k, words in a["want"].items():
        if not all(w.lower() in msgs[k].lower() for w in words):
            bad.append(f"{k}: {msgs[k]!r} lacks {words}")
    return not bad, ("; ".join(bad) + " | " if bad else "") + json.dumps(msgs)[:400]
