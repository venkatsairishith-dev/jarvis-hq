"""B: desktop automation, against the real desktop plugin logic (plugin/plugins/desktop: coordinator, core) and the node's
control/runtime/winbackend modules. The plugin targets Windows; here its logic runs with fake agents, a fake JARVIS
console and fake UI backends (no window is touched). Real Windows UI cases are deferred in the case file."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

_TMP = tempfile.mkdtemp(prefix="bench-b-")
os.environ.setdefault("NEKO_STORAGE_SELECTED_ROOT", _TMP)          # the plugin's data folder: never D:\ on this server

from plugin.plugins.desktop import coordinator as C  # noqa: E402
from plugin.plugins.desktop import core  # noqa: E402
from plugin.plugins.desktop import decide as D  # noqa: E402
from jarvis_node import control, runtime, winbackend  # noqa: E402


def _dir() -> Path:
    return Path(tempfile.mkdtemp(dir=_TMP))


def _plan(*steps):
    return D.Plan(list(steps), "ok", model="bench")


def _coord(agents, **kw):
    d = _dir()
    return C.Coordinator(agents, [], tasks_dir=d / "tasks", lock_path=d / "lock", **kw), d


def _seq(results, calls):
    """An agent that returns the given results in order (the last one repeats); {"raise": msg} raises DesktopError."""
    def fn(step):
        r = results[min(len(calls), len(results) - 1)]
        calls.append(step["tool"])
        if "raise" in r:
            raise core.DesktopError(r["raise"])
        return dict(r)
    return fn


# ---------------------------------------------------------------- verification per tool
def verify_step(a):
    """One step whose agent reports a.result; the task must end in a.want_status and say 'Done' only when completed."""
    step = a["step"]
    calls = []
    co, _ = _coord({step["tool"]: _seq([a["result"]], calls)})
    rec = co.run("bench", _plan(step))
    s = rec["steps"][0] if rec["steps"] else {}
    done_said = C.summary(rec).startswith("Done")
    ok = rec["status"] == a["want_status"] and bool(s.get("passed")) == (a["want_status"] == "completed") \
        and done_said == (rec["status"] == "completed")
    return ok, f"status {rec['status']}, passed {s.get('passed')}, checked '{s.get('checked')}', summary '{C.summary(rec)[:120]}'"


def retry_policy(a):
    """The agent returns a.results in order; the step must be attempted exactly a.want_attempts times."""
    step = a["step"]
    calls = []
    co, _ = _coord({step["tool"]: _seq(a["results"], calls)})
    rec = co.run("bench", _plan(step))
    att = rec["steps"][0]["attempts"]
    ok = att == a["want_attempts"] and len(calls) == a["want_attempts"] and rec["status"] == a["want_status"]
    return ok, f"attempts {att}, agent calls {len(calls)}, status {rec['status']}, error {str(rec.get('error'))[:120]}"


def held_for_owner(a):
    """A step that needs the owner's approval ends the task with the approval id; later steps never start."""
    later = []
    agents = {"open_app": lambda s: {"ok": False, "state": "waiting_for_permission", "approval_id": "ap-7", "how": "PIN in the console"},
              "type_text": lambda s: later.append(1) or {"ok": True}}
    co, _ = _coord(agents)
    rec = co.run("bench", _plan({"tool": "open_app", "app": "notepad"}, {"tool": "type_text", "app": "notepad", "text": "x"}))
    ok = rec["status"] == "waiting_for_permission" and rec.get("approval_id") == "ap-7" and not later and "approval" in C.summary(rec)
    return ok, f"status {rec['status']}, approval {rec.get('approval_id')}, later steps run {len(later)}, summary '{C.summary(rec)[:80]}'"


def no_agent(a):
    co, _ = _coord({})
    rec = co.run("bench", _plan({"tool": "open_app", "app": "notepad"}))
    return rec["status"] == "failed" and "no agent" in rec["error"], f"status {rec['status']}: {rec.get('error')}"


# ---------------------------------------------------------------- the PC-task lock
def lock_single(a):
    """While one task holds the lock, a second coordinator run is 'busy' and its agent never runs."""
    calls = []
    co, d = _coord({"open_app": lambda s: calls.append(1) or {"ok": True, "verified": "verified"}})
    C.acquire("first-task", d / "lock")
    try:
        rec = co.run("bench", _plan({"tool": "open_app", "app": "notepad"}))
    finally:
        C.release("first-task", d / "lock")
    return rec["status"] == "busy" and not calls and "first-task" in rec["error"], f"status {rec['status']}, calls {len(calls)}: {rec.get('error')}"


def lock_race(a):
    """Eight threads try to take the lock at the same instant: exactly one wins."""
    lock = _dir() / "lock"
    won, gate = [], threading.Barrier(8)

    def go(i):
        gate.wait()
        try:
            C.acquire(f"t{i}", lock)
            won.append(i)
        except C.Busy:
            pass
    th = [threading.Thread(target=go, args=(i,)) for i in range(8)]
    [t.start() for t in th]
    [t.join(10) for t in th]
    holder = C.current_task(lock)
    return len(won) == 1 and holder == f"t{won[0]}", f"winners {won}, holder {holder}"


def lock_stale(a):
    """A lock left by a dead process (a.kind=dead) or one older than its budget (a.kind=expired) is taken over; a fresh
    lock of a live process (a.kind=live) is not."""
    lock = _dir() / "lock"
    if a["kind"] == "dead":
        p = subprocess.Popen([sys.executable, "-c", "pass"])
        p.wait()
        held = {"task": "crashed", "pid": p.pid, "at": time.time()}
    elif a["kind"] == "expired":
        held = {"task": "hung", "pid": os.getpid(), "at": time.time() - C.TOTAL_BUDGET_S - 120}
    else:
        held = {"task": "running", "pid": os.getpid(), "at": time.time()}
    lock.write_text(json.dumps(held), encoding="utf-8")
    try:
        C.acquire("next", lock)
        took = True
    except C.Busy:
        took = False
    holder = C.current_task(lock)
    want = a["kind"] != "live"
    return took == want and holder == ("next" if want else held["task"]), f"taken over: {took}, holder {holder}"


def lock_released_after_crash(a):
    def boom(s):
        raise RuntimeError("agent crashed")
    co, d = _coord({"window": boom})
    rec = co.run("bench", _plan({"tool": "window", "app": "paint", "action": "focus"}))
    co2 = co.run("bench", _plan({"tool": "window", "app": "paint", "action": "focus"}))      # the next task is not blocked
    return rec["status"] == "failed" and not (d / "lock").exists() and co2["status"] == "failed" and co2["steps"], \
        f"status {rec['status']} ({rec.get('error')}), lock file left: {(d / 'lock').exists()}, next task {co2['status']}"


# ---------------------------------------------------------------- cancel, budgets
def cancel_between_steps(a):
    flag, later = {"stop": False}, []

    def first(s):
        flag["stop"] = True                                         # the owner says stop while step 1 runs
        return {"ok": True, "verified": "verified", "opened": "notepad"}
    co, _ = _coord({"open_app": first, "type_text": lambda s: later.append(1) or {"ok": True}}, cancelled=lambda: flag["stop"])
    rec = co.run("bench", _plan({"tool": "open_app", "app": "notepad"}, {"tool": "type_text", "app": "notepad", "text": "x"}))
    return rec["status"] == "cancelled" and not later and C.summary(rec) == "Stopped.", \
        f"status {rec['status']}, later steps {len(later)}, summary '{C.summary(rec)}'"


def cancel_during_step(a):
    """The owner stops the task while a step's job runs: the job is cancelled, the task reports 'cancelled', not 'failed'."""
    flag = {"stop": False}

    def running(s):
        flag["stop"] = True
        return {"ok": False, "state": "cancelled", "error": "the job was cancelled"}
    co, _ = _coord({"type_text": running}, cancelled=lambda: flag["stop"])
    rec = co.run("bench", _plan({"tool": "type_text", "app": "notepad", "text": "hello"}))
    return rec["status"] == "cancelled" and rec["steps"][0]["attempts"] == 1, \
        f"status {rec['status']}, attempts {rec['steps'][0]['attempts']}, summary '{C.summary(rec)}'"


def time_budget(a):
    t = [0.0]

    def slow(s):
        t[0] += 400
        return {"ok": True, "verified": "verified"}
    calls = []
    co, _ = _coord({"open_app": lambda s: calls.append(1) or slow(s)}, clock=lambda: t[0], budget_s=600)
    rec = co.run("bench", _plan(*[{"tool": "open_app", "app": f"a{i}"} for i in range(4)]))
    return rec["status"] == "failed" and "budget" in rec["error"] and len(calls) == 2, f"status {rec['status']}, steps run {len(calls)}: {rec.get('error')}"


def routing_stop(a):
    stopped = []
    co, d = _coord({"stop": lambda s: stopped.append(1) or {"ok": True}})
    C.acquire("busy-task", d / "lock")                               # 'stop' must work even while a task holds the lock
    try:
        rec = co.run("never mind", _plan({"tool": "stop"}))
    finally:
        C.release("busy-task", d / "lock")
    return rec["status"] == "stopped" and stopped == [1], f"status {rec['status']}"


# ---------------------------------------------------------------- the JARVIS console round trip (core.Pc)
class FakeConsole:
    """Stands in for the console's owner route: approvals, jobs and job cancel."""

    def __init__(self, needs_approval=False, owner_answers_after=1, deny=False, finish_after=2, stop_after_polls=None, pc=None):
        self.calls, self.pending, self.state_polls, self.job_polls = [], set(), 0, 0
        self.needs_approval, self.answer_after, self.deny, self.finish_after = needs_approval, owner_answers_after, deny, finish_after
        self.stop_after, self.pc, self.cancelled = stop_after_polls, pc, False

    def __call__(self, method, name, body=None, timeout=0):
        self.calls.append((method, name, json.loads(json.dumps(body)) if body else None))
        if name == "pc":
            if self.needs_approval and not body.get("approval_id"):
                self.pending.add("a1")
                return 202, {"status": "needs_approval", "approval_id": "a1", "level": "YELLOW_APPROVAL", "reason": "needs the owner"}
            if body.get("approval_id"):
                return (403, {"error": "refused by policy: approval denied"}) if self.deny else (202, {"job": "j2", "status": "queued"})
            return 202, {"job": "j1", "status": "queued"}
        if name == "state":
            self.state_polls += 1
            if self.state_polls >= self.answer_after:
                self.pending.clear()
            if self.stop_after and self.state_polls >= self.stop_after and self.pc:
                self.pc.stopped = True
            return 200, {"approvals": [{"id": x} for x in self.pending]}
        if name.startswith("job/") and method == "GET":
            self.job_polls += 1
            if self.stop_after and self.job_polls >= self.stop_after and self.pc:
                self.pc.stopped = True                               # the owner pressed stop while the job ran
            if self.cancelled:
                return 200, {"id": name[4:], "state": "cancelled", "result": {"ok": False, "cancelled": True}}
            done = self.job_polls >= self.finish_after
            return 200, {"id": name[4:], "state": "done" if done else "running", "result": {"ok": True} if done else None}
        if name == "job/cancel":
            self.cancelled = True
            return 200, {"id": body["id"], "state": "cancelling"}
        return 404, {}


def _pc(console, **kw):
    clock = [0.0]
    p = core.Pc(call=console, sleep=lambda s: clock.__setitem__(0, clock[0] + s), clock=lambda: clock[0], **kw)
    console.pc = p
    return p


def pc_approval(a):
    """mode=approved: the same request is sent again with the approval id; denied: no job runs; unanswered: the task
    says waiting_for_permission with how to approve; stopped: the owner stops while the approval is pending."""
    mode = a["mode"]
    c = FakeConsole(needs_approval=True, owner_answers_after={"approved": 2, "denied": 1, "unanswered": 10 ** 9, "stopped": 10 ** 9}[mode],
                    deny=mode == "denied", stop_after_polls=2 if mode == "stopped" else None)
    states = []
    p = _pc(c, on_state=lambda s, d: states.append(s), approval_wait_s=60)
    body = {"op": "repair", "action": "edit_file", "target": "cfg.json", "args": {"find": "a", "replace": "b"}}
    r = p.run(body, 30, "edit cfg.json")
    jobs = [n for m, n, b in c.calls if n.startswith("job/") and m == "GET"]
    resent = [b for m, n, b in c.calls if n == "pc" and b.get("approval_id") == "a1"]
    if mode == "approved":
        ok = r["state"] == "done" and resent and resent[0]["target"] == "cfg.json" and "waiting_for_permission" in states
    elif mode == "denied":
        ok = r["state"] == "denied" and not jobs
    elif mode == "unanswered":
        ok = r["state"] == "waiting_for_permission" and r["approval_id"] == "a1" and "PIN" in r["how"] and not jobs and not resent
    else:
        ok = r["state"] == "cancelled" and not jobs and not resent
    return ok, f"state {r['state']}, resent with approval {bool(resent)}, job polls {len(jobs)}, states {states}"


def pc_job_timeout(a):
    c = FakeConsole(finish_after=10 ** 9)
    r = _pc(c).run({"op": "diag", "check": "processes"}, 5)
    cancelled = ("POST", "job/cancel", {"id": "j1"}) in c.calls
    return r["state"] == "failed" and cancelled and "no result within 5 s" in r["result"]["error"], f"state {r['state']}, cancel sent {cancelled}: {r['result']}"


def pc_stop_reaches_job(a):
    """The owner stops the task while its job runs: the console receives job/cancel for that job and the result is cancelled."""
    c = FakeConsole(finish_after=10 ** 9, stop_after_polls=2)
    r = _pc(c).run({"op": "ui", "action": "type", "target": "notepad|*", "text": "x"}, 60)
    cancels = [b for m, n, b in c.calls if n == "job/cancel"]
    return r["state"] == "cancelled" and cancels and cancels[0]["id"] == "j1", f"state {r['state']}, cancels sent {len(cancels)}"


def node_cancel_kills_process(a):
    """runtime.cancel(job) kills the process tree a running job started; the handler sees Cancelled within seconds."""
    out = {}
    runtime.begin("bench-job")

    def work():
        t0 = time.time()
        try:
            runtime.run([sys.executable, "-c", "import time; time.sleep(60)"], timeout=90)
            out["r"] = "finished"
        except runtime.Cancelled:
            out["r"] = "cancelled"
        out["s"] = time.time() - t0
    th = threading.Thread(target=work)
    th.start()
    time.sleep(1.0)
    wrong = runtime.cancel("some-other-job")
    right = runtime.cancel("bench-job")
    th.join(20)
    runtime.end()
    return out.get("r") == "cancelled" and not wrong and right and out["s"] < 10, f"{out}, other-job cancel {wrong}, cancel {right}"


# ---------------------------------------------------------------- the node's gated controller (control.Controller)
def _controller(allowed=True, state=None, fail=False, max_actions=20):
    calls, st = [], state if state is not None else {"title": "Untitled - Notepad"}

    def ask(path, obj):
        return {"allowed": allowed, "level": "GREEN" if allowed else "DENY", "reason": "" if allowed else "policy: no"}

    def act(target, params):
        calls.append((target, params))
        if fail:
            raise RuntimeError("Dear Venkat, your OTP is 123456")       # an error carrying screen text
        st.update(params.get("after") or {})
    ctl = control.Controller(ask, {"click": act, "type": act}, lambda proc, **kw: dict(st), max_actions=max_actions)
    return ctl, calls, st


def controller_case(a):
    m = a["mode"]
    if m == "no_expectation":
        ctl, calls, _ = _controller()
        r = ctl.do("click", "notepad|Save", {})
        return r.status == "unverified" and calls, f"status {r.status}"
    if m == "verified":
        ctl, calls, _ = _controller()
        r = ctl.do("click", "notepad|Save", {"after": {"title": "notes.txt - Notepad"}},
                   expect=lambda b, af: af["title"].startswith("notes.txt"))
        return r.status == "verified", f"status {r.status}"
    if m == "unexpected_dialog":
        ctl, calls, _ = _controller()
        # clicking Save raised a 'Confirm Save As' dialog instead of saving: the state check fails, and the same click
        # is never repeated blindly (it raises RepeatError: a different strategy is needed)
        r = ctl.do("click", "notepad|Save", {"after": {"title": "Confirm Save As", "dialog": True}},
                   expect=lambda b, af: af["title"].startswith("notes.txt"))
        try:
            ctl.do("click", "notepad|Save", {"after": {"title": "Confirm Save As", "dialog": True}}, expect=lambda b, af: True)
            repeated = True
        except control.RepeatError:
            repeated = False
        return r.status == "mismatch" and not repeated and len(calls) == 1, f"status {r.status}, repeated {repeated}, clicks {len(calls)}"
    if m == "refused":
        ctl, calls, _ = _controller(allowed=False)
        r = ctl.do("type", "notepad|*", {"text": "x"})
        return r.status == "refused" and not calls, f"status {r.status}, backend calls {len(calls)}"
    if m == "budget":
        ctl, calls, _ = _controller(max_actions=3)
        for i in range(3):
            ctl.do("click", f"notepad|b{i}", {})
        try:
            ctl.do("click", "notepad|b9", {})
            stopped = False
        except control.BudgetError:
            stopped = True
        return stopped and len(calls) == 3, f"budget stop {stopped}, actions {len(calls)}"
    if m == "error_text":
        ctl, calls, _ = _controller(fail=True)
        r = ctl.do("type", "notepad|*", {"text": "x"})
        return r.status == "error" and "OTP" not in r.detail and r.detail == "RuntimeError", f"status {r.status}, detail '{r.detail}'"
    raise ValueError(m)


# ---------------------------------------------------------------- the Windows backend's own logic (scripts faked)
def typing_reads_back(a):
    """winbackend.type_text: focus, type, then read the field back; a read-back that misses the text fails the action."""
    seen, orig = [], winbackend._run
    readback_ok = a.get("readback_ok", True)

    def fake(script, env):
        kind = "READBACK" if script == winbackend.READBACK else "TYPE" if script == winbackend.TYPE else "FOCUS" if script == winbackend.FOCUS else "?"
        seen.append(kind)
        if kind == "READBACK" and not readback_ok:
            raise RuntimeError("ui-action-failed:text not in field")
    winbackend._run = fake
    try:
        try:
            winbackend.type_text("notepad|*", {"text": "dear sir"})
            err = ""
        except RuntimeError as e:
            err = str(e)
    finally:
        winbackend._run = orig
    if readback_ok:
        return seen == ["FOCUS", "TYPE", "READBACK"] and not err, f"scripts {seen}, error '{err}'"
    return seen == ["FOCUS", "TYPE", "READBACK"] and "text not in field" in err, f"scripts {seen}, error '{err}'"


def keys_and_targets_refused(a):
    """Raw SendKeys syntax, repeated modifiers, injected process names and unlisted programs are refused before any
    script runs."""
    bad_keys = ["{ENTER}", "ctrl+ctrl+x", "%{F4}", "win+r", "ctrl+shift+"]
    refused = []
    for k in bad_keys:
        try:
            winbackend.sendkeys(k)
        except ValueError:
            refused.append(k)
    good = winbackend.sendkeys("ctrl+shift+t") == "^+t" and winbackend.sendkeys("enter") == "{ENTER}"
    tgt = []
    for t in ("notepad;calc|x", "notepad|", "note$(x)|Save", "notepad|a\nb"):
        try:
            winbackend._split(t)
        except ValueError:
            tgt.append(t)
    try:
        winbackend.launch("cmd", {})
        launch_refused = False
    except ValueError:
        launch_refused = True
    ok = len(refused) == len(bad_keys) and good and len(tgt) == 4 and launch_refused
    return ok, f"keys refused {len(refused)}/{len(bad_keys)}, allowed keys mapped {good}, targets refused {len(tgt)}/4, cmd launch refused {launch_refused}"


def record_no_screen_text(a):
    secret = "Dear Venkat, your OTP is 123456"
    co, d = _coord({"window": lambda s: {"ok": True, "local_result": "/x/neko-local/window_text-1.json", "page": secret}})
    rec = co.run("bench", _plan({"tool": "window", "app": "notepad", "action": "inspect"}))
    text = (d / "tasks" / f"{rec['id']}.json").read_text(encoding="utf-8")
    return secret not in text and "window_text-1.json" not in text, f"record has screen text: {secret in text}, local path: {'window_text' in text}"


def uncertainty_reported(a):
    """A play step passed but Windows' media session did not confirm the title: 'Done' carries 'not certain'."""
    r = {"ok": True, "playing": True, "same_video": True, "title": "Kalyani raga - YouTube", "windows_media": ["Some other song (Playing)"]}
    co, _ = _coord({"play_media": lambda s: r})
    rec = co.run("bench", _plan({"tool": "play_media", "profile": "Default", "query": "kalyani"}))
    s = C.summary(rec)
    return rec["status"] == "completed" and "not certain" in s and rec["uncertain"], f"status {rec['status']}, summary '{s[:160]}'"


def speed_coordinator(a):
    """Coordinator overhead (decision given, instant fake agents): 5 verified steps, lock, record on disk."""
    co, _ = _coord({"open_app": lambda s: {"ok": True, "verified": "verified", "opened": s["app"]}})
    steps = [{"tool": "open_app", "app": f"app{i}"} for i in range(5)]
    co.run("warm-up", _plan(*steps))
    t0 = time.perf_counter()
    rec = co.run("bench", _plan(*steps))
    ms = (time.perf_counter() - t0) * 1000
    return rec["status"] == "completed" and ms < a["max_ms"], f"{ms:.1f} ms for 5 steps (limit {a['max_ms']} ms), status {rec['status']}"
