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


# ---------------------------------------------------------------- 2026-10-17: the owner console's answers (jarvis_kernel/owner.py)
# Real kernel, real llm_proxy handler and owner plane over HTTP with an owner token; the owner's agent (Hermes) is a fake
# callable and model providers are fake local servers (adapters/j.py). What is checked is what the console shows.

OWNER = "/v1/jarvis/owner/"


class _Agent:
    """The owner's Hermes as the owner plane calls it: text in, reply out. Each call can be made to wait or fail."""

    def __init__(self, reply="ok", error=None, hold=False, on_call=None):
        self.reply, self.error, self.on_call, self.calls = reply, error, on_call, []
        self.gates = []
        self.hold = hold

    def __call__(self, text):
        import threading
        self.calls.append(text)
        if self.on_call:
            self.on_call(text)
        if self.hold:
            g = threading.Event()
            self.gates.append(g)
            g.wait(10)
        if self.error:
            raise self.error
        return self.reply


def _console(e, tok, method, name, body=None):
    if method == "GET":
        c, b = e.get(tok, OWNER + name)
        return c, json.loads(b or b"{}")
    c, _, b = e.post(tok, body or {}, path=OWNER + name)
    return c, json.loads(b or b"{}")


def _wait_cmd(e, tok, cid, limit=8.0, until=lambda r: r["state"] != "running"):
    end = time.monotonic() + limit
    while time.monotonic() < end:
        rows = {r["id"]: r for r in _console(e, tok, "GET", "commands")[1]["commands"]}
        if cid in rows and until(rows[cid]):
            return rows[cid]
        time.sleep(0.05)
    return {r["id"]: r for r in _console(e, tok, "GET", "commands")[1]["commands"]}.get(cid, {})


def owner_command_resting(a):
    """Every provider of the owner's lane is resting after a 429 (Retry-After 600): a new command is refused at once with
    when to try again, and nothing is sent to the agent."""
    from adapters import j as J
    agent = _Agent("should not be asked")
    with J.env() as e:
        groq = e.mock("groq", [{"status": 429, "body": b'{"error":"rate"}', "headers": {"Retry-After": "600"}}])
        toks = e.proxy([J.P("groq", "private", groq.url)], hermes=agent)
        hermes, me = toks.issue("hermes"), toks.issue("me", "owner")
        first = e.post(hermes, J.msg())[0]
        c, r = _console(e, me, "POST", "command", {"text": "open notepad"})
        rows = _console(e, me, "GET", "commands")[1]["commands"]
    msg = str(r.get("error", ""))
    bad = _contains(msg, a)
    if c != 429 or agent.calls or rows:
        bad.append(f"HTTP {c}, agent asked {len(agent.calls)}x, command rows {len(rows)}")
    return not bad, ("; ".join(bad) + " | " if bad else "") + f"agent call {first}; console {c}: {msg!r}"


def owner_command_agent_error(a):
    """The agent call fails (connection refused, the error naming an internal host): the command ends 'failed' with the
    error class, never 'done', with no reply text; the host is not shown (owner.py keeps the class only)."""
    from adapters import j as J
    agent = _Agent(error=ConnectionRefusedError("cannot reach http://10.20.30.40:8642/v1/runs"))
    with J.env() as e:
        toks = e.proxy([J.P("up", "private", e.mock("up").url)], hermes=agent)
        me = toks.issue("me", "owner")
        c, r = _console(e, me, "POST", "command", {"text": "open notepad"})
        row = _wait_cmd(e, me, r.get("id"))
    bad = []
    if c != 202 or row.get("state") != "failed" or row.get("reply") or not str(row.get("error") or "").startswith("ConnectionRefusedError"):
        bad.append(f"HTTP {c}, row {row}")
    if "10.20.30.40" in str(row.get("error")):
        bad.append("the internal host is shown")
    return not bad, ("; ".join(bad) + " | " if bad else "") + f"state {row.get('state')}, error {row.get('error')!r}"


def owner_command_claim_checked(a):
    """The agent replies 'Done' without anything having run (no job, no task in this turn); or (second command) a task it
    calls finished is still running and unverified. The console reply carries the kernel's own record either way."""
    from adapters import j as J
    with J.env() as e:
        holder = {}

        def make_task(text):
            if "weekly report" in text:
                T = holder["H"].owner_plane.tasks
                tid = T.create("hermes", "private", "write the weekly report", "weekly report written and saved")
                T.update(None, tid, state="running", step="writing")
        agent = _Agent(a.get("claim", "Done! Notepad is open now."), on_call=make_task)
        toks = e.proxy([J.P("up", "private", e.mock("up").url)], hermes=agent)
        holder["H"] = e.H
        me = toks.issue("me", "owner")
        _, r1 = _console(e, me, "POST", "command", {"text": "open notepad"})
        row1 = _wait_cmd(e, me, r1.get("id"))
        agent.reply = "All done, the weekly report is finished."
        _, r2 = _console(e, me, "POST", "command", {"text": "write the weekly report"})
        row2 = _wait_cmd(e, me, r2.get("id"))
    bad = []
    if row1.get("state") != "done" or "Kernel record:" not in str(row1.get("reply")) or "no action was run" not in str(row1.get("reply")):
        bad.append(f"1st reply {row1.get('reply')!r}")
    if "NOT verified yet" not in str(row2.get("reply")) or "running" not in str(row2.get("reply")):
        bad.append(f"2nd reply {row2.get('reply')!r}")
    return not bad, ("; ".join(bad) + " | " if bad else "") + f"1st: {str(row1.get('reply'))[-110:]!r}; 2nd: {str(row2.get('reply'))[-90:]!r}"


def owner_command_cancel_late(a):
    """The owner stops waiting on a slow command: it shows 'cancelled' at once, a new command is accepted at once (not 'still
    working'), and when the first answer arrives late it does not turn the cancelled command into 'done'."""
    from adapters import j as J
    agent = _Agent("late answer: done", hold=True)
    with J.env() as e:
        toks = e.proxy([J.P("up", "private", e.mock("up").url)], hermes=agent)
        me = toks.issue("me", "owner")
        _, r1 = _console(e, me, "POST", "command", {"text": "summarise my inbox"})
        end = time.monotonic() + 5
        while not agent.gates and time.monotonic() < end:
            time.sleep(0.02)
        cc, cr = _console(e, me, "POST", "command/cancel", {"id": r1.get("id")})
        c2, r2 = _console(e, me, "POST", "command", {"text": "what time is it"})
        while len(agent.gates) < 2 and time.monotonic() < end:
            time.sleep(0.02)
        agent.hold = False
        for g in list(agent.gates)[1:]:
            g.set()
        row2 = _wait_cmd(e, me, r2.get("id"))
        agent.gates[0].set()                                         # the first answer arrives late
        end = time.monotonic() + 8                                   # (its reply is scrubbed first: up to ~2 s here)
        while not e.audit("owner_command_late") and not e.audit("owner_command_done")[1:] and time.monotonic() < end:
            time.sleep(0.05)
        row1 = _wait_cmd(e, me, r1.get("id"), until=lambda r: True)
        late = e.audit("owner_command_late")
    bad = []
    if not cr.get("cancelled"):
        bad.append(f"cancel answered {cc} {cr}")
    if c2 != 202:
        bad.append(f"new command refused: {c2} {r2}")
    if row1.get("state") != "cancelled" or row1.get("reply"):
        bad.append(f"first command now {row1.get('state')} {row1.get('reply')!r}")
    if row2.get("state") != "done":
        bad.append(f"second command {row2.get('state')}")
    if not late:
        bad.append("late answer not audited")
    return not bad, ("; ".join(bad) + " | " if bad else "") + f"cancel {cr}; new command {c2}; first ends {row1.get('state')}; second {row2.get('state')}"


def owner_command_busy(a):
    """A second command while the first is still being answered: refused with a plain reason (409), and the first finishes."""
    from adapters import j as J
    agent = _Agent("first done", hold=True)
    with J.env() as e:
        toks = e.proxy([J.P("up", "private", e.mock("up").url)], hermes=agent)
        me = toks.issue("me", "owner")
        _, r1 = _console(e, me, "POST", "command", {"text": "check my calendar"})
        end = time.monotonic() + 5
        while not agent.gates and time.monotonic() < end:
            time.sleep(0.02)
        c2, r2 = _console(e, me, "POST", "command", {"text": "and open notepad"})
        agent.gates[0].set()
        row1 = _wait_cmd(e, me, r1.get("id"))
    bad = _contains(str(r2.get("error", "")), a)
    if c2 != 409 or row1.get("state") != "done" or len(agent.calls) != 1:
        bad.append(f"2nd HTTP {c2}; 1st {row1.get('state')}; agent calls {len(agent.calls)}")
    return not bad, ("; ".join(bad) + " | " if bad else "") + f"2nd: {c2} {r2.get('error')!r}; 1st: {row1.get('state')}"


def owner_job_cancel_states(a):
    """Stopping PC jobs from the console says what really happened: a queued job is 'cancelled'; a running one is
    'cancelling' (and still shows running) until the node confirms, then 'cancelled'; a finished one says nothing to cancel
    and stays 'done'."""
    from adapters import j as J
    with J.env() as e:
        toks = e.proxy([J.P("up", "private", e.mock("up").url)])
        me = toks.issue("me", "owner")
        jobs, node = e.H.owner_plane.jobs, e.H.owner_plane.node
        ids = []
        for _ in range(3):
            c, r = _console(e, me, "POST", "pc", {"op": "open_app", "target": "notepad"})
            ids.append(r.get("job"))
        if None in ids:
            return False, f"could not queue a job: {c} {r}"
        q_cancel = _console(e, me, "POST", "job/cancel", {"id": ids[0]})[1]
        got = []
        while True:                                   # the node takes the other two
            j = jobs.next_for(node)
            if not j:
                break
            got.append(j["id"])
        r_cancel = _console(e, me, "POST", "job/cancel", {"id": ids[1]})[1]
        r_view = _console(e, me, "GET", f"job/{ids[1]}")[1]
        jobs.finish(node, ids[1], {"ok": False, "cancelled": True, "error": "stopped by the owner"})
        r_after = _console(e, me, "GET", f"job/{ids[1]}")[1]
        jobs.finish(node, ids[2], {"ok": True, "opened": "notepad", "verified": "verified"})
        d_cancel = _console(e, me, "POST", "job/cancel", {"id": ids[2]})[1]
        d_view = _console(e, me, "GET", f"job/{ids[2]}")[1]
    got_states = [q_cancel.get("state"), r_cancel.get("state"), r_view.get("state"), r_after.get("state"), d_cancel.get("state"), d_view.get("state")]
    want = ["cancelled", "cancelling", "running", "cancelled", "done", "done"]
    ok = got_states == want and "nothing to cancel" in str(d_cancel.get("note"))
    return ok, f"states {got_states} (want {want}); finished-job note {d_cancel.get('note')!r}"


def owner_stop_all_counts(a):
    """'Stop all' answers with counts that match what it did: open tasks cancelled (a finished one untouched), pending
    approvals denied, queued PC jobs expired."""
    from adapters import j as J
    from jarvis_kernel.policy import Action
    with J.env() as e:
        toks = e.proxy([J.P("up", "private", e.mock("up").url)])
        me = toks.issue("me", "owner")
        op = e.H.owner_plane
        t_open = [op.tasks.create("hermes", "private", f"task {i}", f"goal {i}") for i in range(2)]
        t_done = op.tasks.create("hermes", "private", "done one", "goal done")
        op.tasks.update(None, t_done, state="cancelled")
        aid = e.k.request(Action("browser", "click", "https://shop.example/cart", {"selector": "Buy now"}))[1]
        jids = [_console(e, me, "POST", "pc", {"op": "open_app", "target": "notepad"})[1].get("job") for _ in range(2)]
        c, r = _console(e, me, "POST", "stop_all")
        states = {t: op.tasks.get(t)["state"] for t in t_open + [t_done]}
        jstates = [e.k.db.execute("SELECT state FROM jobs WHERE id=?", (j,)).fetchone()[0] for j in jids]
        pend = [p["id"] for p in e.k.pending_approvals()]
    ok = (c == 200 and sorted(r.get("cancelled_tasks", [])) == sorted(t_open) and r.get("denied_approvals") == (1 if aid else 0)
          and r.get("expired_jobs") == 2 and all(states[t] == "cancelled" for t in t_open) and jstates == ["expired", "expired"]
          and not pend and aid)
    return ok, f"reply {c} {json.dumps(r)[:200]}; tasks {list(states.values())}; jobs {jstates}; approvals pending after {pend} (one was {aid})"
