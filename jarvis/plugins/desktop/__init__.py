"""Desktop: JARVIS's hands on this Windows PC — the owner's existing Chrome profiles, apps, keyboard and mouse, read-only
checks, and a repair workflow that backs up first and verifies after. Every action is a JARVIS kernel job (the kernel
decides; consequential actions wait for the owner's PIN in the JARVIS console). Logic in core.py.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import contextlib
import contextvars
import json
import os
import re
import secrets
import threading
import time

from jarvis.pluginapi import Err, PluginBase, Ok, SdkError, lifecycle, jarvis_plugin, plugin_entry, timer_interval, tr

from jarvis.plugins._shared import decider, screen_text, translate
from jarvis.plugins._shared import names as names_mod

from . import composite, coordinator as coord, core, decide, drive as drive_mod, gate as gate_mod, goal as goal_mod, journal_client as jc, lessons as lessons_mod, pcagent, pcport, quick as quick_mod, simple_tasks, taskstate, taskvoice, undoer

PROFILE_HELP = "Chrome profile alias: 'default' or a name such as 'VenkatAMV'; see list_browser_profiles."


class AgentSet(dict):
    """The step runners by tool name, plus (D65) `alternates`: tool -> fn(step, last result) -> [(route name, runner)], the other way to do the step that the coordinator's
    closed loop tries when the first route did not show its effect."""
    alternates: dict = {}


def _cascade_text(port, app: str, ocr_text: str):
    """Read one window with the three rungs and the agreement rule: the accessibility tree and the OCR text already in hand are compared; the vision model is asked only
    when they disagree or both are empty (it is the slowest and the one that can invent)."""
    from jarvis.plugins._shared import screen_cascade as sc
    from . import perceive
    rungs = {"uia": lambda: perceive._uia_text(port.uia(app)), "ocr": lambda: ocr_text}
    rd = sc.read(rungs, cross_check=True)
    if rd.conflict or not rd.text:
        rd = sc.read({**rungs, "vision": lambda: port.vision(app)}, cross_check=True)
    return rd


def _locate_cascade(port, app: str, label: str, first: dict) -> dict:
    """Where is the label, with the rungs checking each other: the control's NAME in the accessibility tree (a click by name needs no pixels), and the OCR line against
    the vision model's own point. `safe_to_click` is True for a named control or for two pixel rungs that agree; one pixel rung alone is a guess."""
    from jarvis.plugins._shared import screen_cascade as sc
    from . import ladder, perceive
    n = ladder._n(label)
    by_name = False
    try:
        rows = port.uia(app)
        by_name = bool(n) and any(ladder._n(b) == n for r in rows for b in (r.get("buttons") or []))
    except Exception:  # noqa: BLE001
        pass
    pt = first.get("x") is not None and {"x": first["x"], "y": first["y"]}
    both = {}
    try:
        both = port.locate(app, label) or {}
    except Exception:  # noqa: BLE001
        pass
    ocr_pt = (first["x"], first["y"]) if pt and first.get("found_by") == "ocr" else both.get("ocr")
    vis_pt = both.get("vision") or ((first["x"], first["y"]) if pt and first.get("found_by") != "ocr" else None)
    rungs = {}
    if ocr_pt:
        rungs["ocr"] = lambda _l, p=ocr_pt: sc.Hit(float(p[0]), float(p[1]), 60.0, 20.0, label)
    if vis_pt:
        rungs["vision"] = lambda _l, p=vis_pt: sc.Hit(float(p[0]), float(p[1]), 0.0, 0.0, label)
    loc = sc.locate(label, rungs) if rungs else sc.Located(None, "", 0.0)
    out = {"by_name": by_name, "agreement": "agreed" if loc.agreed_with else "disagreed" if loc.conflict else "single" if loc.hit else "none",
           "confidence": loc.confidence, "safe_to_click": bool(by_name or (loc.safe_to_click and loc.source != "uia"))}
    if by_name:
        out["note"] = (str(first.get("note") or "") + " The control is listed by name in the accessibility tree, so it can be clicked by name.").strip()
    elif loc.conflict:
        out["note"] = (str(first.get("note") or "") + " The OCR and the vision model put it in different places: do not click from pixels.").strip()
    return out


async def _no_sleep(_s: float) -> None:
    return None


async def _then(*coros):
    """Run several coroutines one after the other (a route that is two jobs: focus, then the key)."""
    for c in coros:
        await c


class StepUnavailable(RuntimeError):
    """A step could not run at all (not a failed check): a mission ends with this reason instead of re-planning."""
    unavailable = True


class StepCancelled(RuntimeError):
    """The owner stopped the mission or task while this step waited on another plugin."""


def call_on_loop(coro, loop, timeout: float, abort=None, poll_s: float = 1.0, teardown_s: float = 5.0):
    """Run a coroutine (another plugin's entry) on the plugin's long-lived entry loop from a worker thread and wait.

    Live missions 2026-10-16 15:57: the research step ran on the plugin-startup loop, which the host closes after startup;
    every call raised "Event loop is closed", and the mission re-planned that twice and stopped as a "loop". A loop that is
    closed or not running is now named at once (StepUnavailable) instead of failing obscurely or hanging for the whole step
    timeout; the owner's stop (abort) ends the wait within poll_s; a step that is stopped or runs out of time is cancelled
    on the loop, so it never keeps acting after the PC lock is released. The cancel is awaited (teardown_s): the call
    returns once the coroutine has really ended, or after teardown_s, when it is still being torn down (said in the error)."""
    if loop is None or loop.is_closed() or not loop.is_running():
        coro.close()
        raise StepUnavailable("the desktop plugin's event loop is closed or not running (the plugin host restarted?), so the "
                              "step could not run on the plugin bus")
    ended = threading.Event()

    async def tracked():
        try:
            return await coro
        finally:
            ended.set()
    try:
        fut = asyncio.run_coroutine_threadsafe(tracked(), loop)
    except RuntimeError as e:                          # closed between the check and the call
        coro.close()
        raise StepUnavailable(f"could not run on the plugin's event loop ({e})") from None
    end = time.monotonic() + timeout
    try:
        while True:
            left = end - time.monotonic()
            if left <= 0:
                raise StepUnavailable(f"the plugin call timed out after {timeout:.0f}s")
            try:
                return fut.result(min(poll_s, left))
            except concurrent.futures.TimeoutError:
                if fut.done():                         # the call itself raised a TimeoutError: that is its result
                    raise
            op = coord.current_op()
            if op is not None:
                op.touch("waiting for a plugin call")  # the wait is alive: progress for the stuck check
            if abort is not None and abort():
                raise StepCancelled("stopped by the owner while the step was waiting")
            if loop.is_closed():
                raise StepUnavailable("the desktop plugin's event loop is closed (it closed while the step was waiting)")
    except BaseException:
        fut.cancel()
        if loop.is_running() and not loop.is_closed():
            ended.wait(teardown_s)                     # the cancelled call really ended (its own cleanup ran)
        raise


class _PcLock:
    """The PC-control lock (coordinator.acquire) held for the duration of one single-action entry (run_pc_action,
    run_browser_action, open_website, play_youtube, run_troubleshooter). A task or mission holds the very same lock, so a
    single entry can no longer type, click or open things while one runs (audit PC lock, remediation section 6); a busy PC
    raises coordinator.Busy with the "another PC task is running" text, which names the holder (kind, age, last sign of
    life, a requested stop). The entry is a registered operation (coordinator.Operation): Stop reaches its Pc and node
    job. Leaving the block does NOT release the lock while a worker thread of the operation still runs (a cancelled entry's
    thread keeps acting until its job is cancelled): the lock is released exactly once, by the entry or by its last
    worker, whichever ends last. Read-only entries never take it."""

    def __init__(self, what: str, budget: float = 360.0):
        self.task = f"single-{what}-{secrets.token_hex(3)}"
        self.what, self.budget, self.hold, self.op, self._tok = what, float(budget), None, None, None

    def __enter__(self):
        self.op = coord.begin(f"single:{self.what}", self.task)
        try:
            self.hold = coord.acquire(self.task, coord.LOCK, self.budget, op=self.op)    # raises coord.Busy when another holder is live
        except BaseException:
            self.op.finish("not started: the PC was busy")
            raise
        self._tok = coord.bind(self.op)
        return self.hold

    def __exit__(self, *exc):
        coord.unbind(self._tok)
        op, self.hold, self.op = self.op, None, None
        if op is not None:
            if exc and exc[0] is not None:
                op.request_stop(f"the entry ended early ({exc[0].__name__})")
            op.entry_exit()
        return False

    async def __aenter__(self):
        return self.__enter__()

    async def __aexit__(self, *exc):
        return self.__exit__(*exc)


def _goal_progress_writer():
    """D60: the callback that GoalRunner and the rules runner call as a goal moves on; it writes core.GOAL_PROGRESS (content-free) for the face."""
    def write(note: dict) -> None:
        core.set_goal_progress(str(note.get("state") or "idle"), int(note.get("n") or 0), int(note.get("total") or 0), str(note.get("executor") or ""))
    return write


def _cancel_quietly(pc, jid) -> None:
    try:
        getattr(pc, "cancel_once", pc.cancel)(jid)
    except Exception:  # noqa: BLE001 - the worker repeats the cancel when it wakes; the lock stays until it ends
        pass


_HELD = threading.local()        # the owner's approval for the job a resumed mission step was waiting on (missions.resume)
APPROVAL_GONE = re.compile(r"(?i)approval (expired|used|required)")


def _with_approval(fn):
    """A resumed mission step carries the approval the owner gave for one of its jobs ("_approval": id and the job body).
    pc() hands it to exactly that job; the agent itself never sees it."""
    def run(step):
        held = step.get("_approval") if isinstance(step, dict) else None
        if not held:
            return fn(step)
        _HELD.approval = held
        try:
            return fn({k: v for k, v in step.items() if k != "_approval"})
        finally:
            _HELD.approval = None
    return run


def _brief(res: dict) -> dict:
    """What the main agent sees of one action: its outcome and what the node observed (no page or screen text). The strings in
    it are cleaned on the way out: a file or folder named in a title, a row or a note is reduced to its class ("a .xlsx file")
    and a secret pattern is removed, so what the node saw on disk or on the screen cannot ride along in a result field."""
    r = dict(res.get("result") or {})
    r.pop("local_result", None)
    out = {"state": res.get("state"), "ok": bool(r.get("ok")) and res.get("state") == "done", **{k: v for k, v in r.items() if k != "ok"}}
    for k in ("approval_id", "how", "reason", "level"):
        if res.get(k):
            out[k] = res[k]
    if out["ok"] and r.get("verified") == "unconfirmed":
        # owner, 2026-10-25: "Calculator is open on screen" was said while it was not. A launch whose window was not seen is not a success the model may report.
        out["ok"] = False
        out["error"] = str(r.get("note") or "launched, but no matching window appeared in time; it is NOT confirmed open")
    return screen_text.sanitize_meta(out)


def _err(e: BaseException) -> Err:
    """An error for the model or the owner: the message of a console or node refusal (DesktopError) or of a busy PC (Busy), which
    we author, with secrets and file names out. Never the text of an unknown exception's arguments: those can hold anything."""
    authored = isinstance(e, (core.DesktopError, coord.Busy))
    return Err(SdkError(screen_text.clean_message(e if authored else type(e).__name__, 600 if isinstance(e, coord.Busy) else 300)))


async def _release(text: str) -> "screen_text.Released":
    """Screen text on its way out of this plugin: the shared helper decides (flag, secret pass, privacy gateway with names and
    places hidden). The raw text is never the fallback, whatever goes wrong. The gateway call blocks, so it runs in a thread."""
    return await asyncio.to_thread(screen_text.release_safely, text)


def _release_sync(text: str) -> str:
    """The same for code that already runs in a worker thread: the cleared text, or "" when it may not leave."""
    got = screen_text.release_safely(text)
    return got.text if got.allowed else ""


async def _ocr_fallback(run, pc, app: str) -> tuple[list[str], str]:
    """OCR of the app's window when UI Automation gave no text: (its lines, a note when it gave none). Never raises: a
    window that cannot be read just stays unread. The node keeps the text in a local file, read once and deleted."""
    try:
        res = await run(pc, {"op": "diag", "check": "window_ocr", "target": app}, 90, f"read {app} by OCR")
        r = res.get("result") if isinstance(res, dict) and isinstance(res.get("result"), dict) else {}
        lines = core.ocr_lines(core.take_local(r.get("local_result")))
        if res.get("state") == "done" and r.get("ok") is True and lines:
            return lines, ""
        why = str(r.get("error") or "").strip() or ("no text found" if res.get("state") == "done" else str(res.get("state") or "no answer"))
        return [], f"OCR of the window was tried and gave nothing ({screen_text.clean_message(why, 120)})"
    except asyncio.CancelledError:
        raise
    except Exception as e:  # noqa: BLE001 - perception is optional: the diagnosis goes on without it
        return [], f"OCR of the window could not run ({type(e).__name__})"


def _ocr_error(got: dict, verb: str) -> str:
    """An honest, short reason for a failed OCR job: the node's own words (a fixed reason, never screen text) or the state."""
    why = str(got.get("error") or "").strip()
    if got.get("state") == "waiting_for_permission":
        return f"This needs the owner's approval in the JARVIS console first (approval {got.get('approval_id')})."
    if got.get("state") == "denied":
        return "The owner did not approve it; nothing was read."
    return screen_text.clean_message(f"Could not {verb} that window by OCR: {why}" if why else
                                     f"Could not {verb} that window by OCR ({got.get('state') or 'no answer'}).", 300)


@jarvis_plugin
class DesktopPlugin(PluginBase):
    def _pc(self):
        return core.Pc(on_state=core.set_activity)

    async def _run(self, pc: core.Pc, body: dict, timeout: float, what: str = ""):
        """One PC job in a worker thread the owning operation can join. If this call is cancelled, the operation is stopped
        (its Pc, its node job, its pending approval) and the call waits, bounded, for the worker to end before it re-raises;
        a worker that is still running keeps the operation's lock (the operation ends with its last worker)."""
        op = coord.current_op()
        if op is not None:
            op.attach_pc(pc)
            op.worker_start()
        ended = threading.Event()
        ctx = contextvars.copy_context()

        def work():
            try:
                res = pc.run(body, timeout, what)
                jc.note_job(res.get("job") if isinstance(res, dict) else None)      # D61: the journal finds the node's before/after pictures by job id
                return res
            finally:
                if op is not None:
                    op.worker_done()                      # the operation's lock may be released here ...
                ended.set()                               # ... so whoever waits on `ended` sees it released
        try:
            return await asyncio.get_running_loop().run_in_executor(None, ctx.run, work)
        except asyncio.CancelledError:                    # the owner stopped the task: stop the job on the PC too
            pc.stopped = True
            if op is not None:
                op.request_stop("the call was cancelled")
            if pc.current_job:
                await asyncio.to_thread(_cancel_quietly, pc, pc.current_job)
            if op is not None:
                await asyncio.to_thread(op.cancel_jobs)
            await asyncio.to_thread(ended.wait, coord.STOP_WAIT_S)
            raise

    async def _one(self, body: dict, timeout: float = 90.0, what: str = ""):
        scope = coord.operation("check", what or str(body.get("op"))) if coord.current_op() is None else contextlib.nullcontext()
        with scope:
            try:
                res = await self._run(self._pc(), body, timeout, what)
            except core.DesktopError as e:
                return _err(e)
            return Ok(_brief(res))

    @lifecycle(id="startup")
    async def startup(self, **_):
        # missions start on the first entry call, not here: the host runs startup in a temporary event loop that is
        # closed afterwards, and the mission agents must reach the long-lived loop the entries run on
        return Ok({"status": "ready", "profiles": len(core.profiles()), "missions": "start on first use"})

    async def _missions_ready(self) -> bool:
        """Remember the entries' event loop; start missions (and resume any a restart interrupted) once."""
        self._cmd_loop = asyncio.get_running_loop()
        if not getattr(self, "_missions_up", False):
            self._missions_note = await asyncio.to_thread(self._start_missions, self._cmd_loop)
        return getattr(self, "_missions_up", False)

    @lifecycle(id="shutdown")
    async def shutdown(self, **_):
        if getattr(self, "_missions_up", False):
            from dbos import DBOS
            await asyncio.to_thread(DBOS.destroy, destroy_registry=False)
            self._missions_up = False
        return Ok({"status": "stopped"})

    def _start_missions(self, loop) -> str:
        """Durable missions (missions.py): DBOS on a local SQLite file; launching resumes any mission a restart interrupted."""
        try:
            from dbos import DBOS
            from . import coordinator as coord, missions
            missions.configure(coord.DATA / "plugins" / "missions")
            missions.E.agents = self._agents(loop, abort=missions.cancel_requested)
            # the whole decision (ok, clarify, fallback): a question for the owner reaches the owner (audit PC-D5)
            missions.E.plan_fn = lambda goal, ctx: decide.decide(goal, core.profiles(), context=ctx)
            missions.E.on_cancel = lambda: [setattr(p, "stopped", True) or (p.current_job and p.cancel(p.current_job))
                                            for p in list(getattr(self, "_active_pcs", []))]
            DBOS.launch()
            self._missions_up = True
            return "ready"
        except Exception as e:  # noqa: BLE001 - single PC tasks still work without missions
            return f"unavailable ({type(e).__name__}: {str(e)[:120]})"

    # ---------- long missions: several phases, durable, re-planned on a failed check, stoppable ----------
    @plugin_entry(
        id="start_mission",
        name=tr("entry.start_mission.name", default="Start a long mission"),
        description=tr("entry.start_mission.description", default=(
            "Start a goal with several phases that may take minutes (for example 'research X, make slides, open them and "
            "explain'). It runs in the background, survives a restart, never repeats an action, re-plans when a check fails "
            "and stops on a loop or its budget. Use mission_status to follow it and stop_mission to stop it.")),
        input_schema={"type": "object", "properties": {"goal": {"type": "string", "description": "The goal, in the owner's words"}},
                      "required": ["goal"]},
        timeout=30, llm_result_fields=["mission", "status", "error"],
    )
    @screen_text.cleaned                  # every string in the result is cleaned by default (screen_text.clean_result)
    async def start_mission(self, goal: str = "", **_):
        if not await self._missions_ready():
            return Err(SdkError(f"missions are unavailable right now ({getattr(self, '_missions_note', '')}); "
                                "run_pc_task can still do it as one task"))
        from . import missions
        mid = await asyncio.to_thread(missions.start, goal)
        return Ok({"mission": mid, "status": "started"})

    @plugin_entry(
        id="mission_status",
        name=tr("entry.mission_status.name", default="Mission status"),
        description=tr("entry.mission_status.description", default="How the current or a named mission is going: steps done and checked, what failed, the explanation."),
        input_schema={"type": "object", "properties": {"mission": {"type": "string", "default": ""}}},
        timeout=15, llm_result_fields=["mission", "goal", "status", "steps", "replans", "error", "question", "approval_id", "how", "explain"],
    )
    @screen_text.cleaned                  # every string in the result is cleaned by default (screen_text.clean_result)
    async def mission_status(self, mission: str = "", **_):
        await self._missions_ready()
        from . import missions
        rec = await asyncio.to_thread(lambda: missions.load(mission) if mission else missions.latest())
        if not rec:
            return Ok({"status": "none"})
        return Ok({"mission": rec.get("id"), "goal": rec.get("goal"), "status": rec.get("status"), "replans": rec.get("replans"),
                   "steps": [{k: s.get(k) for k in ("n", "tool", "passed", "checked", "uncertainty")} for s in rec.get("steps", [])],
                   "error": screen_text.clean_message(rec.get("error"), 400) if rec.get("error") else rec.get("error"),
                   "question": rec.get("question"), "approval_id": rec.get("approval_id"),
                   "how": rec.get("how"), "explain": rec.get("explain")})

    @plugin_entry(
        id="resume_mission",
        name=tr("entry.resume_mission.name", default="Continue the mission"),
        description=tr("entry.resume_mission.description", default=(
            "Continue a mission that stopped for the owner, with the same mission id: after the owner approved the waiting "
            "action in the JARVIS console with the PIN, with the owner's answer to the mission's question, or after an "
            "interrupted step (answer 'done' if it already happened, 'redo' if it did not). Steps already done are never "
            "repeated. Use this for 'continue the mission', never start_mission again.")),
        input_schema={"type": "object", "properties": {
            "mission": {"type": "string", "default": "", "description": "The mission id; empty = the latest mission"},
            "answer": {"type": "string", "default": "", "description": "The owner's answer to the mission's question, or done/redo"}}},
        timeout=30, llm_result_fields=["ok", "mission", "status", "error"],
    )
    @screen_text.cleaned                  # every string in the result is cleaned by default (screen_text.clean_result)
    async def resume_mission(self, mission: str = "", answer: str = "", **_):
        if not await self._missions_ready():
            return Err(SdkError(f"missions are unavailable right now ({getattr(self, '_missions_note', '')})"))
        from . import missions
        mid = mission or ((await asyncio.to_thread(missions.latest)) or {}).get("id", "")
        if not mid:
            return Ok({"ok": False, "status": "none", "error": "there is no mission to continue"})
        return Ok(await asyncio.to_thread(missions.resume, mid, answer))

    @plugin_entry(
        id="stop_mission",
        name=tr("entry.stop_mission.name", default="Stop the mission"),
        description=tr("entry.stop_mission.description", default="Stop the current (or a named) mission and the PC job it is running."),
        input_schema={"type": "object", "properties": {"mission": {"type": "string", "default": ""}}},
        timeout=30, llm_result_fields=["mission", "stopped"],
    )
    @screen_text.cleaned                  # every string in the result is cleaned by default (screen_text.clean_result)
    async def stop_mission(self, mission: str = "", **_):
        await self._missions_ready()
        from . import missions
        mid = mission or ((await asyncio.to_thread(missions.latest)) or {}).get("id", "")
        ok = bool(mid) and await asyncio.to_thread(missions.cancel, mid)
        return Ok({"mission": mid, "stopped": ok})

    # ---------- proactive monitor: crashes, low disk, local services down, stuck PC tasks (read-only) ----------
    @timer_interval(id="pc_monitor", seconds=60, auto_start=True)
    async def pc_monitor(self, **_):
        # the host awaits what a timer returns: async, with the (blocking) checks in a worker thread
        return await asyncio.to_thread(self._monitor_tick)

    def _monitor_tick(self):
        from . import coordinator as coord, monitor
        state_file = coord.DATA / "plugins" / "monitor_state.json"
        st = monitor.State.load(state_file)
        alerts = monitor.Monitor(st, tasks=lambda: monitor.open_tasks(coord.TASKS), budget_s=coord.TOTAL_BUDGET_S).check()
        st.save(state_file)
        log = coord.DATA / "plugins" / "monitor_alerts.jsonl"
        for a in alerts:
            with log.open("a", encoding="utf-8") as f:
                f.write(json.dumps({"time": time.time(), "kind": a.kind, "subject": a.subject, "text": a.text}) + "\n")
            self.ctx.push_message(source="desktop", visibility=["chat"], ai_behavior="respond", priority=6,
                                  parts=[{"type": "text", "text": "PC monitor: " + a.text}],
                                  metadata={"event_type": "pc_alert", "kind": a.kind, "subject": a.subject},
                                  coalesce_key=f"pc_alert:{a.kind}:{a.subject}")
        return Ok({"alerts": len(alerts)})

    @plugin_entry(
        id="recent_pc_alerts",
        name=tr("entry.recent_pc_alerts.name", default="Recent PC alerts"),
        description=tr("entry.recent_pc_alerts.description", default=(
            "What the PC monitor noticed recently: program crashes, low disk space, a local service that stopped, a stuck "
            "PC task. Use for 'is anything wrong with my PC?' or 'what did you notice?'.")),
        input_schema={"type": "object", "properties": {"hours": {"type": "number", "default": 24}}},
        timeout=10, llm_result_fields=["alerts", "count"],
    )
    @screen_text.cleaned                  # every string in the result is cleaned by default (screen_text.clean_result)
    async def recent_pc_alerts(self, hours: float = 24, **_):
        from . import coordinator as coord
        log = coord.DATA / "plugins" / "monitor_alerts.jsonl"
        since = time.time() - float(hours) * 3600
        rows = []
        if log.exists():
            for line in log.read_text(encoding="utf-8").splitlines()[-500:]:
                try:
                    r = json.loads(line)
                except ValueError:
                    continue
                if r.get("time", 0) >= since:
                    rows.append({"when": time.strftime("%H:%M", time.localtime(r["time"])), "kind": r["kind"], "text": r["text"]})
        return Ok({"alerts": rows[-20:], "count": len(rows)})

    @plugin_entry(
        id="open_website",
        name=tr("entry.open_website.name", default="Open a website"),
        description=tr("entry.open_website.description", default=(
            "Open a web address in the owner's own Chrome, in one of the owner's existing profiles (logged in as the owner). "
            "Use for 'open YouTube', 'open my VenkatAMV account and go to …'.")),
        input_schema={"type": "object", "properties": {
            "url": {"type": "string", "description": "Full address starting with https://"},
            "profile": {"type": "string", "default": "default", "description": PROFILE_HELP}}, "required": ["url"]},
        timeout=120, llm_result_fields=["state", "ok", "opened", "profile", "account", "title", "verified", "profile_name_shown",
                                        "profile_name_matches", "note", "error"],
    )
    @screen_text.cleaned                  # every string in the result is cleaned by default (screen_text.clean_result)
    @jc.journaled("open_website")
    async def open_website(self, url: str = "", profile: str = "default", **_):
        try:
            with _PcLock("open_website", 120):
                return await self._one({"op": "browser", "action": "open", "profile": profile, "url": url}, 90)
        except coord.Busy as e:
            return _err(e)

    @plugin_entry(
        id="play_youtube",
        name=tr("entry.play_youtube.name", default="Play on YouTube"),
        description=tr("entry.play_youtube.description", default=(
            "Find a song or video on YouTube in the owner's Chrome and play it; reports the exact video and whether it is "
            "really playing (checked, not assumed). Example: 'play a Kalyani song'.")),
        input_schema={"type": "object", "properties": {
            "query": {"type": "string", "description": "What to search for on YouTube"},
            "profile": {"type": "string", "default": "default", "description": PROFILE_HELP}}, "required": ["query"]},
        timeout=180, llm_result_fields=["state", "ok", "chose", "title", "playing", "same_video", "position_s", "verified_by",
                                        "windows_media", "error", "needs"],
    )
    @screen_text.cleaned                  # every string in the result is cleaned by default (screen_text.clean_result)
    @jc.journaled("play_youtube")
    async def play_youtube(self, query: str = "", profile: str = "default", **_):
        try:
            with _PcLock("play_youtube", 180):
                return await self._play_youtube(query, profile)
        except coord.Busy as e:
            return _err(e)

    async def _play_youtube(self, query: str, profile: str):
        pc = self._pc()
        core.set_activity("working", f"YouTube: {query}"[:120])
        try:
            res = await self._run(pc, {"op": "browser", "action": "play", "profile": profile, "target": query[:300]}, 150)
            out = _brief(res)
            if out.get("needs") == "extension":
                # without the extension JARVIS can still open the search, so the owner picks and presses play
                url = "https://www.youtube.com/results?search_query=" + __import__("urllib.parse").parse.quote_plus(query)
                opened = _brief(await self._run(pc, {"op": "browser", "action": "open", "profile": profile, "url": url}, 60))
                out.update(opened_search=opened.get("ok"), playing=False,
                           error="Opened the YouTube search for you; choosing and playing needs the Playwright extension "
                                 "in this Chrome profile, so please press play on the video you want.")
            if out.get("ok"):                             # a second witness: what Windows itself says is playing
                media = _brief(await self._run(pc, {"op": "media"}, 30))
                out["windows_media"] = [f"{m.get('title')} ({m.get('status')})" for m in media.get("sessions") or []][:3]
            return Ok(out)
        except core.DesktopError as e:
            return _err(e)
        finally:
            core.set_activity("idle")

    @plugin_entry(
        id="run_browser_action",
        name=tr("entry.run_browser_action.name", default="Browser action"),
        description=tr("entry.run_browser_action.description", default=(
            "Do one thing in the owner's Chrome (needs the Playwright extension in that profile): read the page, find text, "
            "click or type into an element (use the element's visible name or a ref from read), press a key, list tabs, "
            "check which account is signed in (whoami), or check video playback (media). Paying, sending, deleting, "
            "signing out and similar clicks wait for the owner's approval.")),
        input_schema={"type": "object", "properties": {
            "action": {"type": "string", "enum": ["read", "find", "click", "type", "press", "tabs", "whoami", "media"]},
            "profile": {"type": "string", "default": "default", "description": PROFILE_HELP},
            "target": {"type": "string", "description": "Element name/ref, or the text to find"},
            "text": {"type": "string", "description": "Text to type, or the key to press"},
            "approval_id": {"type": "string", "description": "Only to continue after the owner approved"}}, "required": ["action"]},
        timeout=360, llm_result_fields=["state", "ok", "title", "url", "chars", "clicked", "typed_chars", "pressed", "seen", "expected",
                                        "matches", "first_is_expected", "tabs", "paused", "position_s", "error", "needs", "approval_id", "how",
                                        "page", "page_note"],
    )
    @screen_text.cleaned                  # every string in the result is cleaned by default (screen_text.clean_result)
    @jc.journaled("run_browser_action")
    async def run_browser_action(self, action: str = "read", profile: str = "default", target: str = "", text: str = "",
                                 approval_id: str = "", **_):
        body = {"op": "browser", "action": action, "profile": profile, "target": target, "text": text}
        if approval_id:
            body["approval_id"] = approval_id
        try:
            with _PcLock("run_browser_action", 360):
                res = await self._run(self._pc(), body, 120, f"{action} {target}"[:120])
        except coord.Busy as e:
            return _err(e)
        except core.DesktopError as e:
            return _err(e)
        page = core.take_local((res.get("result") or {}).get("local_result"))      # read once, deleted at once
        out = _brief(res)
        if page and core.screen_text_allowed():                                    # the owner allowed page text to reach the model ...
            rel = await _release(page if isinstance(page, str) else json.dumps(page, ensure_ascii=False))
            if rel.allowed:                                                        # ... after the gateway hid names, places, private details
                out["page"], out[screen_text.SCREEN_KEY], out["page_note"] = rel.text[:6000], True, rel.note
            else:
                out["page_note"] = rel.note
        elif page:
            out["page_note"] = "the page text stayed on this laptop (set JARVIS_SCREEN_TEXT_TO_MODEL=1 to let the model read pages)"
        return Ok(out)

    @plugin_entry(
        id="run_pc_action",
        name=tr("entry.run_pc_action.name", default="PC action"),
        description=tr("entry.run_pc_action.description", default=(
            "Operate a Windows app the owner names: open an app by its Start-menu name (open_app), bring a window to the "
            "front (focus), click a button or type into a field by its accessible name (target 'process|element'), press a "
            "key such as ctrl+s or playpause, scroll, or as a last resort click at a point ('process|x,y'). Structured "
            "accessibility first; risky keys and consequential clicks wait for the owner's approval.")),
        input_schema={"type": "object", "properties": {
            "action": {"type": "string", "enum": ["open_app", "focus", "click", "type", "key", "scroll", "click_at", "minimize", "maximize",
                                                  "restore"]},
            "target": {"type": "string", "description": "Start-menu app name for open_app; otherwise 'process|element' or 'process|x,y'"},
            "text": {"type": "string", "description": "Text to type, the key (e.g. ctrl+s, enter, playpause), or up/down"},
            "approval_id": {"type": "string"}}, "required": ["action", "target"]},
        timeout=360, llm_result_fields=["state", "ok", "opened", "done", "verified", "window", "note", "error", "approval_id", "how", "route", "ask"],
    )
    @screen_text.cleaned                  # every string in the result is cleaned by default (screen_text.clean_result)
    @jc.journaled("run_pc_action")
    async def run_pc_action(self, action: str = "", target: str = "", text: str = "", approval_id: str = "", **_):
        body = {"op": "open_app", "target": target} if action == "open_app" else {"op": "ui", "action": action, "target": target, "text": text}
        if approval_id:
            body["approval_id"] = approval_id
        try:
            with _PcLock("run_pc_action", 360):
                return await DesktopPlugin._action_closed_loop(self, action, target, text, body)      # (not self._...: tests and hosts lend a stand-in `self`)
        except coord.Busy as e:
            return _err(e)

    def _sync_port(self, loop) -> "pcport.PcPort":
        """A perceive.Port for code that runs in a worker thread (the cascade is plain code): each call is one kernel job run on the entries' loop, in this operation's context."""
        def pc(body, timeout=90.0, what="", keep_local=False):
            res = asyncio.run_coroutine_threadsafe(self._run(self._pc(), dict(body), timeout, what), loop).result(timeout + 30)
            r = dict(res.get("result") or {})
            local = core.take_local(r.pop("local_result", None))
            if keep_local:
                r["_local"] = local
            r["state"] = res.get("state")
            r["ok"] = bool(r.get("ok")) and res.get("state") == "done"
            for k in ("approval_id", "how"):
                if res.get(k):
                    r[k] = res[k]
            return r
        return pcport.PcPort(pc)

    async def _action_closed_loop(self, action: str, target: str, text: str, body: dict):
        """D59 lever a, wired (D65): ONE owner-named action is act -> check -> retry ONCE by the next route -> otherwise ask. Routes: the structured route (UIA invoke / set value), the
        keyboard (a shortcut or menu mnemonic, or focus + paste), and coordinates from the pixels (only where the OCR line and the vision model agree). Only a node that said the
        action did NOT work is retried; one that said ok is believed (a second click could do it twice); a hold, a denial or a stop ends it at once."""
        from jarvis.plugins._shared import closed_loop as cl
        from . import ladder, perceive
        proc, _, element = target.partition("|")
        box: dict = {"out": None, "stop": False}
        loop = asyncio.get_running_loop()

        def val() -> dict:
            o = box["out"]
            return dict(o.value) if isinstance(o, Ok) and isinstance(o.value, dict) else {"ok": False, "error": str(getattr(o, "error", "") or "no result")}

        async def send(b: dict, what: str = ""):
            box["out"] = await self._one(b, 60, what or f"{b.get('action') or b.get('op')} {target}"[:120])
            v = val()
            if v.get("state") in ("waiting_for_permission", "denied", "cancelled"):
                box["stop"] = True

        async def verify():
            v = val()
            if box["stop"]:
                return cl.Verification(cl.UNKNOWN, "held, denied or cancelled")
            return cl.Verification(cl.VERIFIED if v.get("ok") else cl.ABSENT, str(v.get("error") or "")[:80])

        def keyboard_key() -> str:
            if action == "click" and element:
                return ladder.key_for_element(element) or ladder.mnemonic(element, element.lower() in ladder.MENU_NAMES)
            return ""
        routes = [cl.Route("uia", lambda: send(body))]
        if action == "click" and element and keyboard_key():
            routes.append(cl.Route("keyboard", lambda: _then(send({"op": "ui", "action": "focus", "target": f"{proc}|", "text": ""}), send({"op": "ui", "action": "key", "target": f"{proc}|", "text": keyboard_key()})),
                                   guard=lambda: not box["stop"]))
        if action == "click" and element:
            located: dict = {}

            async def guard_pixels() -> bool:
                if box["stop"] or re.search(r"(?i)pay|delete|send|sign|install|buy|password", element):
                    return False
                loc = await asyncio.to_thread(perceive.locate, DesktopPlugin._sync_port(self, loop), proc, element)
                located["loc"] = loc
                return bool(loc.hit and loc.safe_to_click and loc.source != "uia")

            async def pixels():
                h = located["loc"].hit
                await send({"op": "ui", "action": "click_at", "target": f"{proc}|{int(h.x)},{int(h.y)}", "text": ""})
            routes.append(cl.Route("coordinates", pixels, guard=guard_pixels))
        if action == "type":
            routes.append(cl.Route("refocus", lambda: _then(send({"op": "ui", "action": "focus", "target": f"{proc}|", "text": ""}), send(body)), guard=lambda: not box["stop"]))
            routes.append(cl.Route("paste", lambda: _then(send({"op": "ui", "action": "focus", "target": f"{proc}|", "text": ""}),
                                                          send({"op": "ui", "action": "type_focused", "target": f"{proc}|", "text": text})), guard=lambda: not box["stop"] and bool(text)))
        if action == "key":
            from .ladder import menu_for_key
            menu = menu_for_key(text)
            if menu:
                routes.append(cl.Route("menu", lambda: _then(*[send({"op": "ui", "action": "click", "target": f"{proc}|{m}", "text": ""}) for m in menu]), guard=lambda: not box["stop"]))
        step = cl.Step("pc_action", f"{action} {proc}"[:60], routes, verify, idempotent=False, budget_s=120.0, settle_s=0.0, poll_s=0.0, max_attempts=3 if action in ("click", "type") else 2, keep_order=True)
        sr = await cl.arun_step(step, sleep=_no_sleep, cancelled=lambda: bool(coord.current_op() and coord.current_op().stop_flag.is_set()))
        out = box["out"]
        if isinstance(out, Ok) and isinstance(out.value, dict):
            v = dict(out.value)
            v["route"] = sr.route_used or (sr.attempts[-1].route if sr.attempts else "")
            if sr.retried:
                v["retried"] = True
            if sr.status == cl.ASKED and not v.get("ok") and not box["stop"]:
                v["ask"] = sr.ask
                v["note"] = "I tried " + " and ".join(a.route for a in sr.attempts if a.verification != "skipped") + " and it did not work. " + str(v.get("note") or "")
            return Ok(v)
        return out

    @plugin_entry(
        id="check_pc",
        name=tr("entry.check_pc.name", default="Check the PC"),
        description=tr("entry.check_pc.description", default=(
            "Read-only checks of this PC: busiest processes, disk space, network, a service's state, an app's version and "
            "whether it responds, recent crash records, or what is playing (media). Changes nothing.")),
        input_schema={"type": "object", "properties": {
            "check": {"type": "string", "enum": ["processes", "disk", "network", "services", "app_info", "crashes", "media"]},
            "target": {"type": "string", "description": "App process name or service name, when the check needs one"}}, "required": ["check"]},
        timeout=120, llm_result_fields=["state", "ok", "rows", "sessions", "playing", "error"],
    )
    @screen_text.cleaned                  # every string in the result is cleaned by default (screen_text.clean_result)
    @jc.journaled("check_pc")
    async def check_pc(self, check: str = "disk", target: str = "", **_):
        body = {"op": "media"} if check == "media" else {"op": "diag", "check": check, "target": target}
        return await self._one(body, 90)

    @plugin_entry(
        id="whats_on_screen",
        name=tr("entry.whats_on_screen.name", default="What is on the screen"),
        description=tr("entry.whats_on_screen.description", default=(
            "Which window is in front and which others are open right now (app name and window title only). Read-only. Use it when the "
            "owner says 'this', 'what is that error' or 'look at my screen': it tells you WHICH app to look at. To read the window's text "
            "call read_window_text with that app's process name; the text is released to the model only if the owner allowed screen text "
            "and the privacy gateway cleared it. Window titles are cleaned; refuses nothing because it reads no content.")),
        input_schema={"type": "object", "properties": {}},
        timeout=60, llm_result_fields=["ok", "in_front", "windows", "count", "hint"],
    )
    @screen_text.cleaned                  # every string in the result is cleaned by default (screen_text.clean_result)
    @jc.journaled("whats_on_screen")
    async def whats_on_screen(self, **_):
        res = await self._one({"op": "windows"}, 30, "list windows")
        if not isinstance(res, Ok):
            return res
        got = res.value
        if not got.get("ok"):
            return Err(SdkError("The PC could not list its windows right now."))
        ws = [w for w in (got.get("windows") or []) if isinstance(w, dict) and not w.get("minimized")]
        front = next((w for w in ws if w.get("foreground")), None)
        row = lambda w: {"app": str(w.get("process") or "")[:40], "title": str(w.get("title") or "")[:120]}     # noqa: E731
        others = [row(w) for w in ws if w is not front][:12]
        out = {"ok": True, "count": len(ws), "in_front": row(front) if front else None, "windows": others}
        out["hint"] = (f"To read what is in the front window, call read_window_text with app='{front.get('process')}'."
                       if front and core.APP_PROCESS.match(str(front.get("process") or "")) else
                       "No window is in front, or its app name is not usable; ask the owner which app they mean.")
        return Ok(out)

    # ---------- perception beyond the accessibility tree: OCR of one window, and finding a label in it ----------
    @plugin_entry(
        id="read_window_text",
        name=tr("entry.read_window_text.name", default="Read a window by OCR"),
        description=tr("entry.read_window_text.description", default=(
            "Read the text of one open window by OCR (Windows' built-in text recognition), for a window whose accessibility "
            "tree gives nothing: a game, a remote desktop, a picture of a form. Less reliable than the accessibility tree "
            "(OCR misreads digits and letters), so say so when you rely on it. Read-only; refuses password managers, wallets "
            "and bank windows. UNVERIFIED on real hardware: written and tested against a fake console only. The text goes "
            "to the model only when the owner allowed screen text (JARVIS_SCREEN_TEXT_TO_MODEL=1) AND the privacy gateway has hidden "
            "names, places and private details in it; secrets are removed first. Otherwise it is withheld.")),
        input_schema={"type": "object", "properties": {"app": {"type": "string", "description": "The app's process name, e.g. notepad"}},
                      "required": ["app"]},
        timeout=120, llm_result_fields=["ok", "app", "lines", "text", "text_note", "source", "removed", "error"],
    )
    @screen_text.cleaned                  # every string in the result is cleaned by default (screen_text.clean_result)
    @jc.journaled("read_window_text")
    async def read_window_text(self, app: str = "", **_):
        """OCR of one window through the kernel job diag/window_ocr (policy, approvals and audit apply).
        UNVERIFIED on real hardware. The node keeps the text in a local file; it is read once here, deleted, scrubbed, and returned only when the
        owner allowed screen text AND the privacy gateway cleared it (screen_text.release); otherwise it is withheld."""
        app = str(app or "").strip()
        if not core.APP_PROCESS.match(app):
            return Err(SdkError("Say which app's window to read, by its short process name (letters, digits, space, dot, dash; "
                                "at most 40 characters)."))
        try:
            with coord.operation("read_window_text", f"read-{app}"[:40]):        # a registered, stoppable (lock-free) operation
                res = await self._run(self._pc(), {"op": "diag", "check": "window_ocr", "target": app}, 90, f"read {app} by OCR")
        except core.DesktopError as e:
            return _err(e)
        got = _brief(res)
        try:
            lines = core.ocr_lines(core.take_local((res.get("result") or {}).get("local_result")))   # read once, deleted at once
        except core.DesktopError as e:
            return _err(e)
        if not got.get("ok"):
            return Err(SdkError(_ocr_error(got, "read")))
        out = {"ok": True, "app": app, "lines": len(lines), "source": core.OCR_LABEL}
        joined = "\n".join(lines)
        reading = await asyncio.to_thread(_cascade_text, DesktopPlugin._sync_port(self, asyncio.get_running_loop()), app, joined)      # D65: the cascade (tree, OCR, vision) with its agreement rule
        shown = reading.text if reading.text else joined
        _, removed = core.scrub_text(shown)
        label = {"ocr": core.OCR_LABEL, "uia": "from the accessibility tree", "vision": "from the vision model (a guess; check it)"}.get(reading.source, core.OCR_LABEL)
        out = {"ok": True, "app": app, "lines": len(lines) if reading.source in ("ocr", "") else len([x for x in shown.splitlines() if x.strip()]), "source": label,
               "confidence": reading.confidence, "read_by": [t.rung for t in reading.trace if t.ok]}
        if reading.agreed_with:
            out["agreed_with"] = reading.agreed_with
            out["source"] = f"{label}; confirmed by {reading.agreed_with}"
        note = core.window_prepared_note((res.get("result") or {}).get("prepared"), app)
        if note:
            out["window_note"] = note                                 # the owner's window was restored or enlarged to be read: said, not hidden
        if reading.conflict:
            out["conflict"] = True
        if removed:
            out["removed"] = removed                                  # rule names only
        if not shown.strip():
            out["text_note"] = "OCR found no text in the window"
        elif core.screen_text_allowed():
            rel = await _release(shown)                               # secrets out, then the privacy gateway hides names and places
            if rel.allowed:
                out["text"], out[screen_text.SCREEN_KEY], out["text_note"] = rel.text[:6000], True, rel.note
            else:
                out["text_note"] = rel.note                           # withheld: a short neutral note, none of the text
        else:
            out["text_note"] = "the window text stayed on this laptop (set JARVIS_SCREEN_TEXT_TO_MODEL=1 to let the model read it)"
        if reading.conflict and out.get("text_note"):
            out["text_note"] += " The readings of the window disagreed, so treat this as uncertain."
        return Ok(out)

    @plugin_entry(
        id="find_on_screen",
        name=tr("entry.find_on_screen.name", default="Find text in a window"),
        description=tr("entry.find_on_screen.description", default=(
            "Find where a piece of text is in one open window (OCR first; the local vision model only if OCR does not find it) "
            "and say where, as a point inside that window. Read-only: it does not click. Says whether the place was OBSERVED "
            "(OCR read it) or only INFERRED (the vision model guessed). Refuses password managers, wallets and bank windows. "
            "UNVERIFIED on real hardware: written and tested against a fake console only.")),
        input_schema={"type": "object", "properties": {
            "app": {"type": "string", "description": "The app's process name, e.g. notepad"},
            "text": {"type": "string", "description": "The text (a button label, a heading) to find in that window"}},
            "required": ["app", "text"]},
        timeout=180, llm_result_fields=["ok", "found", "app", "x", "y", "found_by", "basis", "window", "source", "note", "error"],
    )
    @screen_text.cleaned                  # every string in the result is cleaned by default (screen_text.clean_result)
    @jc.journaled("find_on_screen")
    async def find_on_screen(self, app: str = "", text: str = "", **_):
        """Locate a label in one window through the kernel job diag/window_locate (policy, approvals and audit apply).
        UNVERIFIED on real hardware. A point is in the window's own pixels; basis says observed (OCR) or inferred (model)."""
        app, text = str(app or "").strip(), str(text or "").strip()
        if not core.APP_PROCESS.match(app) or not text:
            return Err(SdkError("Say which app's window to look in (its short process name) and the text to find."))
        if re.search(r"[\r\n\0|]", text):
            return Err(SdkError("The text to find must be one plain line."))
        target = f"{app}|{text}"
        if len(target) > core.MAX_LOCATE_TARGET:
            return Err(SdkError(f"The app name and the text together are too long ({len(target)} characters; the limit is "
                                f"{core.MAX_LOCATE_TARGET}). Use a shorter piece of the text."))
        try:
            with coord.operation("find_on_screen", f"find-{app}"[:40]):
                res = await self._run(self._pc(), {"op": "diag", "check": "window_locate", "target": target}, 150, f"find {text[:40]} in {app}")
        except core.DesktopError as e:
            return _err(e)
        got = _brief(res)
        try:
            core.take_local((res.get("result") or {}).get("local_result"))   # the window's OCR text: deleted unread
        except core.DesktopError as e:
            return _err(e)
        if not got.get("ok"):
            return Err(SdkError(_ocr_error(got, "search")))
        pt = got.get("point")
        out = {"ok": True, "app": app, "found": isinstance(pt, dict) and "x" in pt and "y" in pt, "source": core.OCR_LABEL,
               "window": got.get("size")}
        if out["found"]:
            out.update(x=pt["x"], y=pt["y"], found_by=got.get("found_by"), basis=got.get("basis"))
            if got.get("basis") == "inferred":
                out["note"] = "INFERRED by the local vision model, not read from the screen: check before clicking there"
            else:
                out["note"] = "read by OCR (less reliable than the accessibility tree); the point is inside the window"
        else:
            out["note"] = str(got.get("note") or "not found by OCR or the vision model")[:200]
        try:                                                              # D65: the cascade's agreement rule for a place (and the control's name in the tree)
            out.update(await asyncio.to_thread(_locate_cascade, DesktopPlugin._sync_port(self, asyncio.get_running_loop()), app, text, out))
        except Exception:  # noqa: BLE001 - the second opinion is a help; the first answer stands
            pass
        return Ok(out)

    @plugin_entry(
        id="run_troubleshooter",
        name=tr("entry.run_troubleshooter.name", default="Troubleshoot an app"),
        description=tr("entry.run_troubleshooter.description", default=(
            "Find out why a Windows app shows an error and fix it safely: read its error dialog, event log and crash records "
            "(on this laptop), check the cause against the file it names, back up, apply the fix after the owner approves, "
            "start the app again and verify the error is gone. Reports a verified cause or says it is only a hypothesis.")),
        input_schema={"type": "object", "properties": {
            "app": {"type": "string", "description": "The app's process name, e.g. JARVISSampleApp"},
            "start_name": {"type": "string", "description": "Its Start-menu name, to start it again for the check, e.g. JARVIS Sample App"},
            "apply_fix": {"type": "boolean", "default": True}}, "required": ["app"]},
        timeout=900, llm_result_fields=["outcome", "cause", "verified", "evidence", "fix", "backup", "undo", "verification", "steps", "approval_id", "how"],
    )
    @screen_text.cleaned                  # every string in the result is cleaned by default (screen_text.clean_result)
    @jc.journaled("run_troubleshooter")
    async def run_troubleshooter(self, app: str = "", start_name: str = "", apply_fix: bool = True, **_):
        # It clicks, starts the app and edits a file: it takes the PC lock like a task. A task's own troubleshoot step
        # already holds that lock and calls the flow below directly (taking it again would wait on itself forever).
        try:
            with _PcLock("run_troubleshooter", 900):
                return await DesktopPlugin._troubleshooter_flow(self, app, start_name, apply_fix)
        except coord.Busy as e:
            return _err(e)

    async def _troubleshooter_flow(self, app: str = "", start_name: str = "", apply_fix: bool = True):
        steps = []

        def step(state, detail=""):
            # the steps are returned to the model and shown on the icon: no secret and no file name in them (a file is "a .json file")
            steps.append({"state": state, "detail": screen_text.clean_message(detail, 160), "at": time.strftime("%H:%M:%S")})
            core.set_activity(state, detail)

        cleared_any = []                                  # set when screen text (not a class) was released into the report

        def release_name(text):                           # for core.diagnose, which runs in a worker thread
            out = _release_sync(text)
            if out:
                cleared_any.append(True)
            return out
        pc = core.Pc(on_state=step)                       # "waiting_for_permission" lands in the report too

        class FlowStopped(BaseException):
            """The owner's Stop reached this flow: nothing further is sent."""

        async def run(body, timeout=60, what=""):
            res = await self._run(pc, body, timeout, what)
            if pc.stopped:                                # a stopped Pc sends nothing; the flow ends here, honestly
                raise FlowStopped()
            return res

        async def job(body, timeout=60, what=""):
            return _brief(await run(body, timeout, what))

        async def nap(seconds):                           # a pause that Stop does not have to wait out
            for _ in range(max(1, int(seconds * 4))):
                if pc.stopped:
                    raise FlowStopped()
                await asyncio.sleep(0.25)

        def local(res):                                   # the node's local result, read once and deleted; only its objects
            r = res.get("result")
            return core.rows(core.take_local(r.get("local_result") if isinstance(r, dict) else None))

        report = {}
        try:
            step("inspecting", f"Looking at {app}")
            raw = await run({"op": "diag", "check": "window_text", "target": app}, 60)
            windows = local(raw)
            if not windows and start_name:
                await job({"op": "open_app", "target": start_name}, 60)
                await nap(3)
                raw = await run({"op": "diag", "check": "window_text", "target": app}, 60)
                windows = local(raw)
            ocr_lines, ocr_note = [], ""
            if not windows and core.APP_PROCESS.match(app):
                # The accessibility tree gave nothing (a window drawn without it). Fall back to OCR of the window, clearly
                # labelled: OCR misreads characters, so it is only evidence for a hypothesis, never for an automatic fix.
                step("inspecting", f"No accessibility text from {app}; trying OCR")
                ocr_lines, ocr_note = await _ocr_fallback(lambda p, *a: run(*a), pc, app)
                if ocr_lines:
                    windows = [{"title": "", "texts": ocr_lines, "dialog": False, "ocr": True}]
            ev = await run({"op": "diag", "check": "app_errors", "target": app}, 60)
            events = local(ev)
            crashes = (await job({"op": "diag", "check": "crashes", "target": app})).get("rows") or []
            step("diagnosing", "Checking the cause")
            d = await asyncio.to_thread(core.diagnose, windows, events, crashes, None, release_name)
            if ocr_lines:
                n = len(ocr_lines)
                d["verified"], d["fix"] = False, None          # characters read by OCR can be wrong: hypothesis only, no fix
                d["evidence"].insert(0, f"window text {core.OCR_LABEL}: {n} line(s); the cause below is a hypothesis and no "
                                        "automatic fix is offered from it")
                d["cause"] = ("OCR read the window but nothing in it matched a known fault: the window text was " + core.OCR_LABEL
                              if d["cause"].startswith("no error dialog") else f"{d['cause']} ({core.OCR_LABEL})")
            elif ocr_note:
                d["evidence"].append(ocr_note)
            report = {"cause": d["cause"], "verified": d["verified"], "evidence": d["evidence"], "fix": None, "steps": steps}
            if cleared_any:
                report[screen_text.SCREEN_KEY] = True    # words from the window are in the report (cleared by the gateway): say so
            if not d.get("fix") or not apply_fix:
                step("completed", "Diagnosis only (no fix asked)" if d.get("fix") else "No safe automatic fix")
                report.update(outcome="diagnosed" if d["verified"] else "hypothesis only", fix=d.get("fix"))
                return Ok(report)
            fix = d["fix"]
            report["fix"] = {"action": fix["action"], "file": screen_text.file_class(fix["target"]), "why": fix["why"]}
            body = {"op": "repair", "action": fix["action"], "target": fix["target"], "args": fix["args"]}       # the real path: the kernel job only
            res = await run(body, 120, f"edit {screen_text.file_class(fix['target'])} ({fix['why']})")
            if res.get("state") == "waiting_for_permission":
                step("waiting_for_permission", "Approve the fix in the JARVIS console")
                report.update(outcome="waiting for your approval", approval_id=res.get("approval_id"), how=res.get("how"))
                return Ok(report)
            if res.get("state") == "denied":
                step("failed", "The fix was not approved; nothing was changed")
                report.update(outcome="not approved: nothing was changed", approval_id=res.get("approval_id"))
                return Ok(report)
            r = res.get("result") or {}
            step("applying_fix" if r.get("ok") else "failed", "Backed up and applied the fix" if r.get("ok") else "The fix did not apply")
            if not (res.get("state") == "done" and r.get("ok")):
                step("failed", str(r.get("error") or res.get("state")))
                report.update(outcome="the fix was not applied", error=screen_text.clean_message(r.get("error") or res.get("state"), 200))
                return Ok(report)
            report.update(backup=r.get("backup"), undo=r.get("undo"))
            step("verifying", "Starting it again to check")
            ok_btn = [b for w in windows if w.get("dialog") for b in (w.get("buttons") if isinstance(w.get("buttons"), list) else [])
                      if isinstance(b, str) and b.lower() in ("ok", "close")]
            if ok_btn:                                    # dismiss the old error dialog first, as the owner would
                await job({"op": "ui", "action": "click", "target": f"{app}|{ok_btn[0]}"}, 60)
                await nap(1)
            if start_name:
                await job({"op": "open_app", "target": start_name}, 60)
                await nap(4)
            raw = await run({"op": "diag", "check": "window_text", "target": app}, 60)
            after = local(raw)                            # an unreadable read counts as "not verified", never as fixed
            dialogs = [w for w in after if w.get("dialog")]
            running = [w for w in after if not w.get("dialog")]
            fixed = bool(running) and not dialogs
            title = await _release(str(running[0].get("title") or "")[:120]) if running and core.screen_text_allowed() else None
            report["verification"] = {"windows_now": len(after), "error_dialogs_now": len(dialogs), "main_window": bool(running),
                                      "title": title.text if title is not None and title.allowed else ""}
            if title is not None and title.allowed and title.text:
                report[screen_text.SCREEN_KEY] = True
            step("completed" if fixed else "failed", "The error is gone" if fixed else "The error is still there")
            report["outcome"] = "fixed and verified" if fixed else "fix applied, but the check failed (undo with the backup)"
            return Ok(report)
        except FlowStopped:
            step("failed", "Stopped by the owner")
            applied = "backup" in report                  # set once the fix was applied: the owner must still get its undo
            report.update(outcome="stopped by the owner" + (" after the fix was applied (undo with the backup)" if applied else
                                                            "; nothing further was done"), steps=steps)
            report.setdefault("cause", "stopped before a cause was found")
            report.setdefault("verified", False)
            report.setdefault("evidence", [])
            return Ok(report)
        except Exception as e:  # noqa: BLE001 - once a fix is applied, the owner must still get its backup and undo
            if "backup" in report:                        # set once the fix was applied
                step("failed", f"The check after the fix did not run: {type(e).__name__}")
                report.update(outcome="fix applied, but the check could not run (undo with the backup)",
                              error=screen_text.clean_message(e if isinstance(e, core.DesktopError) else type(e).__name__, 200))
                return Ok(report)
            if isinstance(e, core.DesktopError):
                step("failed", str(e))
                return _err(e)
            # any other failure: its class only. An exception's arguments can hold the text that was being read, and the host
            # would put them in its log and in the error the model sees.
            step("failed", f"the troubleshooter stopped ({type(e).__name__})")
            return Err(SdkError(f"the troubleshooter stopped ({type(e).__name__})"))
        finally:
            core.set_activity("idle")

    # ---------- the coordinator: one request -> local decision -> agents -> verified outcome ----------
    def _agents(self, loop, abort=None) -> dict:
        """The step runners the coordinator uses. Every PC action is a kernel job (core.Pc); nothing here decides permission.
        abort: the owner stopped the mission (missions.cancel_requested); a step waiting on another plugin stops waiting."""
        import re as _re
        active = self._active_pcs = getattr(self, "_active_pcs", [])

        def run_pc(p, body, timeout, what):
            """One job. A resumed mission step hands the owner's approval to the job it was asked for (same body), once;
            a still-pending approval keeps the step waiting, a denied one is denied, an expired one is asked for again."""
            held = getattr(_HELD, "approval", None)
            if not (held and held.get("id") and held.get("body") == body and not body.get("approval_id")):
                return p.run(body, timeout, what)
            _HELD.approval = None
            try:
                return p.run(dict(body, approval_id=held["id"]), timeout, what)
            except core.DesktopError as e:
                msg = str(e)
                if "approval pending" in msg:
                    return {"state": "waiting_for_permission", "approval_id": held["id"], "result": {"ok": False, "error": msg},
                            "how": f"Approve {held['id']} in the JARVIS console with your PIN, then tell me to continue the mission."}
                if "approval denied" in msg:
                    return {"state": "denied", "approval_id": held["id"], "result": {"ok": False, "error": msg}}
                if APPROVAL_GONE.search(msg):
                    return p.run(body, timeout, what)            # asked again: the owner approves the same action anew
                raise

        def pc(body, timeout=90.0, what="", keep_local=False):
            # The step's PC job belongs to the task or mission operation that is current on this thread (so Stop reaches
            # its node job and its pending approval); without one (an agent called outside a task) it is an operation of
            # its own for the length of the call.
            op = coord.current_op()
            own = coord.begin("agent-step", f"step-{body.get('op', 'job')}-{secrets.token_hex(2)}") if op is None else None
            op = op or own
            p = op.attach_pc(core.Pc(on_state=core.set_activity))
            active.append(p)
            pre, t_job = jc.before_job(body), time.time()                # D61: a setting job reads its current value first (what an undo puts back)
            try:
                res = run_pc(p, body, timeout, what)
                jc.note_job(res.get("job"))                              # D61: pictures of this step, found by job id
            finally:
                active.remove(p)
                op.detach_pc(p)
                if own is not None:
                    own.entry_exit()
            r = dict(res.get("result") or {})
            local = core.take_local(r.pop("local_result", None))    # screen/page text: read by nobody here, deleted (drive() keeps it, for the local model)
            if keep_local:
                r["_local"] = local
            r["state"] = res.get("state")
            r["ok"] = bool(r.get("ok")) and res.get("state") == "done"
            for k in ("approval_id", "how"):
                if res.get(k):
                    r[k] = res[k]
            if res.get("state") == "waiting_for_permission":
                r["approval_body"] = dict(body)                     # which job the approval is for (missions.resume)
            jc.after_job(body, r, pre, t_job)                       # D61: a file or setting job that worked is one journal line with its undo recipe
            return r

        def alias(folder):
            return "default" if folder == "Default" else _re.sub(r"[^a-z0-9]", "", str(folder).lower())

        def find_process(app):
            """The process of an open window whose title or process name fits the app the owner named. "(active window)":
            a window whose title reads like an error, else the window in front (never JARVIS's own or the editor)."""
            ws = pc({"op": "windows"}, 30).get("windows") or []
            if app == decide.ACTIVE:
                mine = ("code", "windowsterminal", "powershell", "python", "pythonw", "msedge", "explorer")
                cands = [w for w in ws if str(w.get("process", "")).lower() not in mine]
                err = [w for w in cands if _re.search(r"(?i)error|warning|failed|could not|cannot|exception|not found", str(w.get("title", "")))]
                front = [w for w in cands if w.get("foreground")]
                pick = (err or front or [None])[0]
                return pick.get("process") if pick else None
            words = [w for w in _re.split(r"\W+", app.lower()) if len(w) > 2 and w not in ("app", "the", "window")]
            words = words or [app.lower()]

            def name(w):                                         # a modern (UWP) app's window belongs to ApplicationFrameHost:
                if str(w.get("process", "")).lower() == "applicationframehost":   # name it by its title (the node finds
                    return _re.sub(r"[^A-Za-z0-9_.\- ]", "", str(w.get("title", "")))[:80] or None   # that one frame)
                return w.get("process")
            for w in ws:
                hay = (str(w.get("process", "")) + " " + str(w.get("title", ""))).lower()
                if all(x in hay for x in words) or str(w.get("process", "")).lower() == app.lower().replace(" ", ""):
                    return name(w)
            for w in ws:
                if any(x in (str(w.get("process", "")) + " " + str(w.get("title", ""))).lower() for x in words):
                    return name(w)
            return None

        def need_proc(step):
            """Precondition: a window of the app exists. If not, open the app once (a recorded recovery), then look again."""
            proc = find_process(step["app"])
            if not proc and step["app"] != decide.ACTIVE:
                opened = pc({"op": "open_app", "target": step["app"]}, 60)
                step.setdefault("recovered", []).append(f"opened {step['app']} first (precondition): {bool(opened.get('ok'))}")
                time.sleep(2)
                proc = find_process(step["app"])
            if not proc:
                raise RuntimeError(f"no open window for '{step['app']}'")
            return proc

        def open_app(step):
            """Already open: bring that window to the front instead of starting a second one (a second window makes it
            unclear where typing goes). The owner can still say 'open a new notepad window'."""
            if not _re.search(r"(?i)\bnew\b", step.get("app", "")):
                r = direct(step, {"op": "ui", "action": "focus", "target": "", "text": ""})
                if r is None:                                    # not found under its usual name: look the window up
                    proc = find_process(step["app"])
                    r = pc({"op": "ui", "action": "focus", "target": f"{proc}|", "text": ""}, 60) if proc else None
                if r is not None and r.get("ok"):
                    return dict(r, opened=step["app"], verified="verified", note="it was already open: brought to the front")
            return pc({"op": "open_app", "target": step["app"]}, 60)

        PROCESS_OF = {"calc": "Calculator", "calculator": "Calculator", "paint": "mspaint", "visual studio code": "Code",
                      "vs code": "Code", "file explorer": "explorer", "word": "WINWORD", "powerpoint": "POWERPNT"}

        def direct(step, body):
            """One kernel job instead of two: act on the app's likely process (or modern-app title) name first; only if
            the node finds no such window, look the window up properly (need_proc). Never guesses between windows: the
            node refuses when two frames match."""
            app = step.get("app", "")
            name = PROCESS_OF.get(app.lower(), decide.APPS.get(app.lower(), app))
            if app != decide.ACTIVE and _re.fullmatch(r"[A-Za-z0-9_.\- ]{1,40}", name or ""):
                r = pc(dict(body, target=f"{name}|{body['target']}"), 60)
                if r.get("ok") or "no window" not in str(r.get("error", "")):
                    return dict(r, process=name)
            return None

        def window(step):
            if step.get("action") != "inspect":
                r = direct(step, {"op": "ui", "action": step.get("action", "focus"), "target": "", "text": ""})
                if r is not None:
                    return r
            proc = need_proc(step)
            if step.get("action") == "inspect":
                r = pc({"op": "diag", "check": "window_text", "target": proc}, 60)
                r["inspected"] = proc
                return r
            return dict(pc({"op": "ui", "action": step.get("action", "focus"), "target": f"{proc}|", "text": ""}, 60), process=proc)

        def type_text(step):
            if not step.get("target"):                           # the first text field, found by the node in the same job
                r = direct(step, {"op": "ui", "action": "type", "target": "*", "text": step.get("text", "")})
                if r is not None:
                    return r
            proc = need_proc(step)
            target = step.get("target") or ""
            if not target:                                       # the window's first text field, by accessibility
                snap = pc({"op": "snapshot", "target": proc}, 60)
                fields = [e for e in snap.get("elements") or [] if str(e.get("type", "")).endswith(("Edit", "Document"))
                          or str(e.get("cls") or "").startswith(("Edit", "RichEdit"))]       # classic controls come through as Pane
                if not fields:
                    return {"ok": False, "error": f"no text field found in {proc}"}
                target = fields[0].get("id") or fields[0].get("name") or ""
            return pc({"op": "ui", "action": "type", "target": f"{proc}|{target}", "text": step.get("text", "")}, 60)

        def press_key(step):
            key = step.get("key", "")
            if key == "playpause" and step.get("intent") in ("pause", "play"):    # a toggle: look first, never start what should stop
                playing = [m for m in pc({"op": "media"}, 30).get("sessions") or [] if m.get("status") == "Playing"]
                if (step["intent"] == "pause") != bool(playing):
                    return {"ok": True, "done": "nothing to do", "note": "nothing was playing" if step["intent"] == "pause" else "already playing"}
            if key in decide.KEY_WORDS:
                return pc({"op": "ui", "action": "key", "target": "media|", "text": key}, 30)
            r = direct(step, {"op": "ui", "action": "key", "target": "", "text": key})
            return r if r is not None else pc({"op": "ui", "action": "key", "target": f"{need_proc(step)}|", "text": key}, 30)

        def open_file(step):
            name, folder = step.get("name", ""), step.get("folder", "")
            known = ("downloads", "documents", "desktop", "pictures", "videos", "music")
            if name.lower().strip() in known or _re.sub(r"(?i)\b(my|the|folder)\b", "", name).strip().lower() in known:
                return pc({"op": "open_folder", "target": _re.sub(r"(?i)\b(my|the|folder)\b", "", name).strip()}, 60)
            last = {}
            for f in ([folder] if folder.lower() in known else []) + ["Documents", "Desktop", "Downloads"]:
                last = pc({"op": "open_file", "target": f, "path": name}, 60)
                if last.get("ok"):
                    return last
            return last

        def editor_job(step, body):
            """One kernel job against the app's window: its likely process name first (one job), the looked-up window if the node found none."""
            r = direct(step, dict(body, target=""))
            if r is None:
                r = dict(pc(dict(body, target=f"{need_proc(step)}|"), 90))
            return r

        def open_in_vscode(step):
            """D54: VS Code on a folder inside Documents, Desktop, Downloads, outputs or a project (the node checks the folder and finds Code.exe)."""
            root, _, rel = str(step.get("folder", "")).partition("/")
            return pc({"op": "open_code", "target": root, "path": rel}, 90)

        def type_in_editor(step):
            """D54: paste the text into the app's editor (a new file first when asked). Several jobs for a long text; the first one the kernel
            holds or the node refuses ends the step, and the result says how much had been pasted."""
            text, done, chunks_n, read_back = str(step.get("text", "")), 0, 0, True
            if not text:
                return {"ok": False, "error": "there was no text to type"}
            if step.get("new_file") or composite.is_code_app(step.get("app", "")):
                # VS Code always gets a NEW untitled file first: the keyboard focus may be in its terminal, where a paste would run the lines, or in the Chat
                # panel (live 2026-10-26: a fresh window had the Chat box focused; ctrl+n and ctrl+1 both went to the chat and the code was pasted into it).
                # The Command Palette works from wherever the focus is: ctrl+shift+p, "File: New Untitled Text File", enter.
                if composite.is_code_app(step.get("app", "")):
                    for act in ({"op": "ui", "action": "key", "text": "ctrl+shift+p"}, {"op": "ui", "action": "type_focused", "text": "File: New Untitled Text File"},
                                {"op": "ui", "action": "key", "text": "enter"}):
                        r = editor_job(step, act)
                        if not r.get("ok"):
                            return r
                        time.sleep(0.8)
                else:
                    r = editor_job(step, {"op": "ui", "action": "key", "text": "ctrl+n"})        # a new, empty, focused editor
                if not r.get("ok"):
                    return r
                time.sleep(0.8)
            for piece in composite.chunks(text):
                r = editor_job(step, {"op": "ui", "action": "type_focused", "text": piece})
                if not r.get("ok"):
                    return dict(r, typed_chars=done, error=r.get("error") or ("only %d of %d characters were pasted" % (done, len(text))))
                done, chunks_n = done + len(piece), chunks_n + 1
                read_back = read_back and bool(r.get("observed"))
            return {"ok": True, "typed_chars": done, "chunks": chunks_n, "read_back": read_back, "process": r.get("process")}

        def save_as(step):
            """D54: Save As in the app's window; the node picks a free file name, saves and checks the disk. Never over an existing file."""
            text = f"{step.get('folder', '')}|{step.get('name', '')}" + (f"|{step['digest']}" if step.get("digest") else "")
            return editor_job(step, {"op": "ui", "action": "save_as", "text": text})

        OWN_PROCS = {"jarvis", "electron", "jarvisface"}

        def drive_observe(app_hint):
            """What the observe - decide - act loop looks at: the windows, the dialogs of the app being worked in (title, texts, buttons) and its
            text (accessibility tree first, OCR when that gives little). Local only; the text is shown to the local model by drive.propose_local."""
            ws = [w for w in (pc({"op": "windows"}, 30).get("windows") or []) if isinstance(w, dict) and not w.get("minimized")]
            front = next((w for w in ws if w.get("foreground") and _re.sub(r"[^a-z]", "", str(w.get("process", "")).lower()) not in OWN_PROCS), None)
            proc = (find_process(app_hint) if app_hint else None) or (front or {}).get("process")
            obs = {"windows": [{"process": w.get("process"), "title": w.get("title")} for w in ws[:10]], "dialogs": [], "text": "", "source": "none"}
            if not proc or not core.APP_PROCESS.match(str(proc)):
                return obs
            r = pc({"op": "diag", "check": "window_text", "target": str(proc)}, 60, keep_local=True)
            rows = core.rows(r.get("_local"))
            texts = []
            for w in rows:
                if w.get("dialog"):
                    obs["dialogs"].append({"process": proc, "title": str(w.get("title") or "")[:120],
                                           "texts": [str(t)[:200] for t in (w.get("texts") or [])[:12]], "buttons": [str(b)[:60] for b in (w.get("buttons") or [])[:8]]})
                else:
                    texts += [str(w.get("title") or "")] + [str(t) for t in (w.get("texts") or [])]
            text = "\n".join(t for t in texts if t)
            obs["source"] = "accessibility tree" if text else "none"
            if len(text.strip()) < 40:                                   # the accessibility tree gave little: the pixels
                o = pc({"op": "diag", "check": "window_ocr", "target": str(proc)}, 90, keep_local=True)
                lines = core.ocr_lines(o.get("_local"))
                if lines:
                    text, obs["source"] = "\n".join(lines), "OCR"
            obs["text"] = core.scrub_text(text)[0][:6000]
            return obs

        def drive(step):
            """D54, the general rule: any task as observe - decide - act with a step budget. Every act is one kernel job carrying the owner's goal
            (the Pc adds it) and, for a dialog, the dialog's text; what the kernel holds ends the loop with its approval id."""
            op = coord.current_op()
            goal = (op.goal if op is not None else "") or step.get("query", "")

            def act(body):
                r = pc(dict(body), 120)
                if body.get("op") == "list_folder":
                    r["items"] = (r.get("items") or [])[:200]
                return r
            def classify(ctx, app):
                d = decider.classify_dialog(ctx, app)
                return d.choice or "B", d.source
            if os.environ.get("JARVIS_PC_AGENT", "1").strip().lower() in ("0", "off", "no", "false"):        # the older observe - decide - act loop (D54), kept as a switch
                h = drive_mod.Hands(observe=drive_observe, act=act, propose=lambda task, obs, hist: drive_mod.propose_local(task, obs, hist), classify=classify)
                out = drive_mod.Driver(h).run(goal, task=step.get("query") or goal, app=step.get("app", ""))
            else:                                                       # D65: the human-like loop with the problem ladder (pcagent.py)
                agent = pcagent.PcAgent(pcport.PcPort(pc),
                                        lambda task, obs, hist: drive_mod.propose_local(task, obs, hist), classify=classify, lessons=lessons_mod.default(),
                                        state=taskstate.TaskState(), goal=goal, stop=(op.stop_flag.is_set if op is not None else None), task_id=getattr(op, "id", "") or "")
                out = agent.run(goal, task=step.get("query") or goal, app=step.get("app", ""))
            if out.get("status") == "waiting_for_permission":
                out["state"] = "waiting_for_permission"
            return out

        def front_process():
            ws = [w for w in (pc({"op": "windows"}, 30).get("windows") or []) if isinstance(w, dict) and not w.get("minimized")]
            front = next((w for w in ws if w.get("foreground") and _re.sub(r"[^a-z]", "", str(w.get("process", "")).lower()) not in OWN_PROCS), None)
            return (front or {}).get("process")

        def window_text_of(app):
            """One window's text, read locally with the accessibility tree (never sent anywhere): for 'copy the text to the clipboard' and answer checks."""
            proc = find_process(app) if app else front_process()
            if not proc or not core.APP_PROCESS.match(str(proc)):
                return ""
            r = pc({"op": "diag", "check": "window_text", "target": str(proc)}, 60, keep_local=True)
            rows = [w for w in core.rows(r.get("_local")) if not w.get("dialog")]
            text = "\n".join(t for w in rows for t in [str(w.get("title") or "")] + [str(x) for x in (w.get("texts") or [])] if t)
            return core.scrub_text(text)[0]

        def simple_task(step):
            """D54: a task from the catalogue (config/simple_tasks.json). Every effect is one kernel job carrying the owner's goal (the Pc adds it); the
            handler verifies by effect and returns facts, never the model's say-so."""
            import datetime as _dt
            import os as _os
            env = simple_tasks.Env(
                pc=lambda body, timeout=90.0, keep_local=False: pc(dict(body), timeout, keep_local=keep_local),
                agent=lambda tool, s: agents[tool](s),
                process=lambda app: find_process(app) if app else None,
                front=front_process, window_text=window_text_of,
                now=lambda: _dt.datetime.now().astimezone(),
                scheduler_create=lambda d: core.owner("POST", "automations", {"definition": d}),
                scheduler_list=lambda: (core.owner("GET", "automations")[1] or {}).get("automations") or [],
                vault_dir=lambda: _os.environ.get("JARVIS_VAULT_DIR") or r"D:\jarvis\vault",
                scrub=lambda t: core.scrub_text(t)[0])
            return simple_tasks.run(step, env)

        def check(step):
            if step.get("check") == "media":
                return pc({"op": "media"}, 30)
            return pc({"op": "diag", "check": step.get("check", "disk"), "target": step.get("app", "")}, 90)

        def call(coro, timeout):
            # the entries' live loop (a mission may outlive the call that started it); a closed loop, a timeout or the
            # owner's stop (the caller's abort, or the task operation's own stop flag) end the wait honestly, and the call
            # is cancelled on the loop (call_on_loop)
            op = coord.current_op()
            stopped = (lambda: bool(abort and abort()) or (op is not None and op.stop_flag.is_set())) if op else abort
            return call_on_loop(coro, getattr(self, "_cmd_loop", None) or loop, timeout, stopped)

        def troubleshoot(step):
            proc = find_process(step["app"])
            start = step["app"] if step["app"] != decide.ACTIVE else ""
            if not proc and start:                               # not open: start it so its error can be seen
                pc({"op": "open_app", "target": start}, 60)
                time.sleep(3)
                proc = find_process(step["app"])
            if not proc:
                return {"ok": False, "error": f"'{step['app']}' has no window, even after starting it"}
            res = call(DesktopPlugin._troubleshooter_flow(self, proc, start), 900)     # the task holds the PC lock already
            data = res.value if isinstance(res, Ok) else {"error": str(getattr(res, "error", res))}
            return {"ok": data.get("outcome") in ("fixed and verified", "diagnosed"), **data}

        def window_list():
            return pc({"op": "windows"}, 30)

        def research(step):
            want_open = bool(step.get("open_after")) and step.get("deliverable", "none") != "none"
            before = core.window_ids(window_list()) if want_open else None      # None: no new-window claim possible
            res = call(self.plugins.call_entry_json("research:research_topic", {"question": step["query"], "deliverable": step.get("deliverable", "none"),
                                                                                 "open_after": want_open}, timeout=900.0), 960)
            data = res.value.get("data", res.value) if isinstance(res, Ok) and isinstance(res.value, dict) else {}
            out = {"ok": bool(data.get("summary")), "summary": str(data.get("summary") or "")[:600], "file": data.get("file"),
                   "qa_passed": data.get("qa_passed"), "sources": (data.get("sources") or [])[:8],
                   "error": None if data else str(getattr(res, "error", ""))[:200]}
            if want_open and data.get("file"):
                import os as _os
                seen, why = opened_window(_os.path.splitext(_os.path.basename(str(data["file"]).replace("\\", "/")))[0].lower()[:40], before)
                out.update(opened=seen or None, opened_verified=bool(seen), open_note=why or None)
            return out

        def opened_window(stem, before, tries=10):
            """A NEW window whose title shows the file's name: the evidence that a made file really opened. New means
            its window id was not in `before` (core.new_window); without usable ids, or with two candidates, nothing is
            claimed. Returns (what was seen, why not)."""
            return core.wait_new_window(window_list, before, stem, tries=tries)

        def open_output(step):
            """Re-open a file JARVIS made ("open it"): only through the deliverables plugin, which opens nothing outside its
            own output folder."""
            import os as _os
            before = core.window_ids(window_list())
            res = call(self.plugins.call_entry_json("deliverables:open_output", {"path": step.get("path", "")}, timeout=30.0), 40)
            if not isinstance(res, Ok):
                return {"ok": False, "error": str(getattr(res, "error", res))[:200]}
            stem = _os.path.splitext(_os.path.basename(str(step.get("path", "")).replace("\\", "/")))[0].lower()[:40]
            seen, why = opened_window(stem, before)
            return {"ok": True, "opened": seen or step.get("path"), "opened_verified": bool(seen), "open_note": why or None}

        def pc_raw(body, timeout=120.0, what=""):
            """D64: the job runner of the browser agent. The same operation, lock, Stop and journal wiring as `pc`, but the node job's state and result come back
            as they are (the agent reads the page text itself, once, from the local file the node left)."""
            op = coord.current_op()
            own = coord.begin("agent-step", f"step-{body.get('op', 'job')}-{secrets.token_hex(2)}") if op is None else None
            op = op or own
            p = op.attach_pc(core.Pc(on_state=core.set_activity))
            active.append(p)
            try:
                op.touch("browser agent")
                res = run_pc(p, body, timeout, what)
                jc.note_job(res.get("job"))
                return res
            finally:
                active.remove(p)
                op.detach_pc(p)
                if own is not None:
                    own.entry_exit()

        def web_task(step):
            """D64: a deck (slides, a presentation) is made on a FREE web generator in the owner's own Chrome by the browser agent (web_runner.py): the shortlist,
            the site playbooks, the per-site grants. It passes only on a file that arrived in quarantine and was read (coordinator.verify)."""
            from jarvis.plugins._shared import owner_ask
            from . import web_runner
            op = coord.current_op()
            stopped = lambda: bool(abort and abort()) or (op is not None and op.stop_flag.is_set())      # noqa: E731
            res = web_runner.run_deck(step, pc_run=pc_raw, stop=stopped, ask=owner_ask.ask, profile=alias(step.get("profile", "Default")), journal=jc.record_web_step,
                                     touch=(op.touch if op is not None else None))
            if res.get("ok") and step.get("open_after") and res.get("file"):          # "... and open it": the verified copy, through the deliverables plugin's own open
                o = open_output({"path": res["file"]})
                res.update(opened=o.get("opened"), opened_verified=bool(o.get("opened_verified")), open_note=o.get("open_note") or o.get("error"))
            return res

        def stop(step):
            for p in list(active):
                p.stopped = True
            self._stop_pc_tasks()                # flags only: this step runs inside a task and must not wait for itself
            return {"ok": True, "stopped_jobs": len(active)}

        agents = {
            "open_website": lambda s: pc({"op": "browser", "action": "open", "profile": alias(s["profile"]), "url": s["url"]}, 90),
            "play_media": lambda s: (lambda r: dict(r, windows_media=[f"{m.get('title')} ({m.get('status')})" for m in
                                                                      pc({"op": "media"}, 30).get("sessions") or []]) if r.get("ok") else r)(
                pc({"op": "browser", "action": "play", "profile": alias(s["profile"]), "target": s["query"]}, 200)),
            "browser": lambda s: pc({"op": "browser", "action": s["action"], "profile": alias(s.get("profile", "Default")),
                                     "target": s.get("target", ""), "text": s.get("text", "")}, 120, f"{s['action']} {s.get('target', '')}"),
            "open_app": open_app,
            "window": window, "type_text": type_text, "press_key": press_key, "open_file": open_file, "check_pc": check,
            "troubleshoot": troubleshoot, "research": research, "open_output": open_output, "stop": stop,
            "open_in_vscode": open_in_vscode, "type_in_editor": type_in_editor, "save_as": save_as, "drive": drive, "simple_task": simple_task,
            "web_task": web_task,
        }
        wrapped = AgentSet({k: jc.wrap_agent(k, _with_approval(fn)) for k, fn in agents.items()})       # D61: one journal line per step run

        def other_way_type(step, last):
            """D59 lever a (D65): the other route for typing - bring the window forward and PASTE the text (the clipboard route), when the first route could not put it there."""
            if step.get("text") and step.get("app") and step.get("app") != decide.ACTIVE:
                return [("paste", lambda s: editor_job(s, {"op": "ui", "action": "type_focused", "text": str(s.get("text", ""))}))]
            return []

        def other_way_window(step, last):
            """The other route for a window action: restore it from minimised, then do the action again."""
            if step.get("action") in ("focus", "maximize") and step.get("app") not in (None, "", decide.ACTIVE):
                return [("restore", lambda s: (direct(s, {"op": "ui", "action": "restore", "target": "", "text": ""}), window(s))[1])]
            return []
        wrapped.alternates = {"type_text": other_way_type, "window": other_way_window}
        return wrapped

    def _stop_pc_tasks(self) -> None:
        """The owner's stop reaches every registered PC operation (each has its own flag: a second request that is
        refused as busy must not clear the stop of the one that runs). Flags and wake-ups only; never blocks."""
        coord.request_stop_all("stopped by the owner")

    @plugin_entry(
        id="run_pc_task",
        name=tr("entry.run_pc_task.name", default="Do a task on the PC"),
        description=tr("entry.run_pc_task.description", default=(
            "Do what the owner asked on this PC in one go: a small local model plans the steps (open sites or apps, play "
            "music, use the owner's Chrome accounts, type, press keys, check or troubleshoot the PC, research), each step "
            "runs through the JARVIS kernel and is checked afterwards. Reports what was verified and what was not.")),
        input_schema={"type": "object", "properties": {"request": {"type": "string", "description": "The owner's request, in their words"}},
                      "required": ["request"]},
        timeout=900, llm_result_fields=["status", "summary", "question", "steps", "uncertain", "approval_id", "how"],
    )
    @screen_text.cleaned                  # every string in the result is cleaned by default (screen_text.clean_result)
    async def run_pc_task(self, request: str = "", answers: dict | None = None, **_):
        # ``answers`` is never in the input schema (a plan cannot set it): only main_agent's re-run of this step after the owner
        # answered its clarification question passes it (pending-input contract, main_agent/input_request.py).
        # D74: ask, do not guess (config/ambiguity.json): a request that must be asked about stops here as a question; the owner's answer is put into this same request.
        gout = await gate_mod.gate_request(request, lambda need: DesktopPlugin._gate_fetch(self, need), DesktopPlugin._gate_ask(self), answers)
        if gout.result is not None:
            return Ok(gout.result)
        if gout.resolved:
            request = " and ".join(gout.texts)[:2000]
            answers = None                                  # his answer is in the request now: it is not added a second time
        if isinstance(answers, dict) and isinstance(answers.get("clarification"), str) and answers["clarification"].strip():
            request = f"{request}\n(The owner's clarification: {screen_text.clean_message(answers['clarification'], 300)})"
        # D58: the decider and every model below it understand English only. The owner's words are translated on this PC first (English costs nothing);
        # a doubtful translation is shown to him as a question instead of being acted on, unless he already answered one.
        request, tr_info = await asyncio.to_thread(translate.entry_text, request, confirmed=bool(isinstance(answers, dict) and answers.get("clarification")),
                                                    source="pc_task")
        if tr_info and tr_info.get("clarify"):
            return Ok(translate.clarification_result(tr_info["clarify"]))
        rec = await DesktopPlugin._run_task(self, request)         # (not self._run_task: tests and hosts lend a stand-in `self`)
        res = DesktopPlugin._task_result(rec)
        if tr_info and isinstance(getattr(res, "value", None), dict):
            # (the "note" key is the plugin's own words and passes the screen-text cleaner; an "english" field would be withheld as screen text)
            res.value["translation"] = {k: tr_info.get(k) for k in ("source_lang", "confidence", "translated")}
            res.value["note"] = translate.understood_note(tr_info)
            res.value["summary"] = translate.skipped_note(tr_info) + str(res.value.get("summary") or "")
        return res

    async def _run_task(self, request: str, plan: "decide.Plan | None" = None) -> dict:
        """One PC task as a registered operation (planning included): the coordinator's record. `plan`: a plan already made (D51: the
        local decider's plan for one step of an online plan); without it the coordinator plans with the local decider as before."""
        loop = asyncio.get_running_loop()
        await self._missions_ready()                        # also resumes a mission a restart interrupted
        # The task is a registered operation from the start (planning included): Stop sets its flag, cancels its node job,
        # denies its pending approval, and waits for its worker thread. abort: the owner's stop also ends a step waiting on
        # another plugin (research), as in missions.
        op = coord.begin("task", "task-" + secrets.token_hex(3))
        op.goal = core.OWNER_GOAL.get() or request          # the owner's own words (do_goal sets them); never a step an online plan wrote
        tok = coord.bind(op)
        jtok = jc.enter_task(core.OWNER_GOAL.get() or request)       # D61: the owner's request is the journal's task (inside a goal, the goal's task)
        c = coord.Coordinator(self._agents(loop, abort=op.stop_flag.is_set), core.profiles(), on_progress=core.set_activity,
                              cancelled=op.stop_flag.is_set, op=op)
        ctx = contextvars.copy_context()
        ended = threading.Event()
        op.worker_start()

        def work():
            try:
                return c.run(request) if plan is None else c.run(request, plan)
            finally:
                op.worker_done()
                ended.set()
        try:
            # the coordinator holds the PC lock in this worker thread until its last step really ends, even when this
            # call is cancelled first (the thread then stops at the next step boundary and releases the lock)
            rec = await loop.run_in_executor(None, ctx.run, work)
        except asyncio.CancelledError:
            op.request_stop("the call was cancelled")
            await asyncio.to_thread(op.cancel_jobs)
            await asyncio.to_thread(ended.wait, coord.STOP_WAIT_S)
            raise
        finally:
            jc.exit_task(jtok)
            coord.unbind(tok)
            op.entry_exit()
            core.set_activity("idle")
        return rec

    @staticmethod
    def _task_result(rec: dict):
        # the summary is built from what the steps observed (a resolved file name, a window title): file names out
        out_extra = {}
        if rec["status"] == "needs_clarification" and rec.get("question"):
            out_extra["input_request"] = {"title": "Clarification", "prompt": str(rec["question"])[:500], "answer_arg": "answers",
                                          "expires_s": 3600, "fields": [{"name": "clarification", "kind": "text",
                                                                         "label": "Your answer", "max_len": 300}]}
        return Ok({**out_extra, "status": rec["status"], "summary": screen_text.clean_message(coord.summary(rec), 1200), "question": rec.get("question"), "task": rec["id"],
                   "plan_model": (rec.get("plan") or {}).get("model"), "plan_seconds": (rec.get("plan") or {}).get("seconds"),
                   "steps": [{k: s.get(k) for k in ("n", "tool", "agent", "attempts", "passed", "checked", "uncertainty")} for s in rec["steps"]],
                   "uncertain": rec.get("uncertain"), "approval_id": rec.get("approval_id"), "how": rec.get("how"),
                   "route": rec.get("route"), "ask": rec.get("ask")})

    goal_readers: dict = {}                 # face-side read-only helpers the host may lend (jarvis/bridge/face_tools.py): {"recall": fn, "tasks": fn}

    async def _goal_pc(self, goal: str, plan) -> dict:
        return await self._run_task(goal, plan)

    # ---- the solve path (D54): the one window to read, and the editor to type into. Local work only; nothing here sends text anywhere.
    SOLVE_OWN = {"jarvis", "electron", "jarvisface", "python", "pythonw", "windowsterminal"}      # JARVIS's own windows are never "the window with the problem"
    EDITORS = {"code", "notepad", "notepad++", "wordpad", "sublimetext", "pycharm64", "idea64", "devenv", "idle", "gvim", "atom"}

    @staticmethod
    def _wname(w: dict) -> str:
        proc = str(w.get("process") or "")
        if proc.lower() == "applicationframehost":                        # a modern app's window belongs to the frame host: named by its title
            return re.sub(r"[^A-Za-z0-9_.\- ]", "", str(w.get("title") or ""))[:40]
        return proc

    @staticmethod
    def _wkey(name: str) -> str:
        return re.sub(r"[^a-z0-9+]", "", str(name).lower().removesuffix(".exe"))

    async def _goal_windows(self) -> list[dict]:
        res = await self._one({"op": "windows"}, 30, "list windows")
        got = res.value if isinstance(res, Ok) and isinstance(res.value, dict) else {}
        return [w for w in (got.get("windows") or []) if isinstance(w, dict) and not w.get("minimized")]

    async def _goal_read_window(self, app: str | None = None, avoid: str | None = None) -> dict:
        """The text of ONE window for a solve goal, read locally: the app the owner named, else the front window that is not JARVIS's own and not
        the editor he will type into. Rungs: the accessibility tree, then OCR (the local vision model only locates, it does not transcribe). The
        text is scrubbed of secret patterns and returned to the goal runner only; it is never logged, and it leaves the PC only through
        goal.GoalRunner._solve (the gateway)."""
        ws = await self._goal_windows()
        key, skip = self._wkey, self._wkey(avoid or "")
        cands = [w for w in ws if core.APP_PROCESS.match(self._wname(w) or "!") and key(self._wname(w)) not in {key(x) for x in self.SOLVE_OWN}
                 and not (skip and key(self._wname(w)) == skip)]
        if app:
            want = key(app)
            pick = next((w for w in cands if want and want in key(self._wname(w) + " " + str(w.get("title") or ""))), None)
            if pick is None:
                return {"ok": False, "error": f"no open window of {core.APP_PROCESS.match(app) and app or 'that app'} to read"}
        else:
            pick = next((w for w in cands if w.get("foreground")), cands[0] if cands else None)
            if pick is None:
                return {"ok": False, "error": "no window other than JARVIS's own is open, so there is nothing to read"}
        proc = self._wname(pick)
        ui_text, ocr_text = "", ""
        with coord.operation("solve_read", f"solve-{proc}"[:40]):
            res = await self._run(self._pc(), {"op": "diag", "check": "window_text", "target": proc}, 60, f"read {proc}")
            r = res.get("result") if isinstance(res.get("result"), dict) else {}
            try:
                wins = core.rows(core.take_local(r.get("local_result")))
            except core.DesktopError:
                wins = []
            ui_text = "\n".join(str(t) for w in wins for t in ([w.get("title")] if w.get("title") else []) + list(w.get("texts") or []) if isinstance(t, str))
            if len(ui_text.strip()) < 200:                                 # too little to be a problem statement: the pixels instead
                lines, _note = await _ocr_fallback(lambda p, b, t, w: self._run(p, b, t, w), self._pc(), proc)
                ocr_text = "\n".join(lines)
        text, source = (ocr_text, "OCR") if len(ocr_text.strip()) > len(ui_text.strip()) else (ui_text, "accessibility tree")
        if not text.strip():
            return {"ok": False, "app": proc, "error": f"nothing readable was found in the {proc} window (accessibility and OCR found no text)"}
        return {"ok": True, "app": proc, "text": core.scrub_text(text)[0][:screen_text.MAX_TEXT * 4], "source": source}

    async def _goal_pick_editor(self, named: str | None = None, read_app: str | None = None) -> dict:
        """The app to type the solution into: the one the owner named, else the front editor window (an editor other than the window that was read)."""
        if named:
            return {"ok": True, "app": named}
        ws = await self._goal_windows()
        skip = self._wkey(read_app or "")
        for w in ws:                                                       # the list is front to back
            name = self._wname(w)
            if self._wkey(name) in self.EDITORS and self._wkey(name) != skip and core.APP_PROCESS.match(name or "!"):
                return {"ok": True, "app": name}
        return {"ok": False, "error": "no editor window is open (VS Code, Notepad ...); say which app to type the solution in"}

    async def _goal_call(self, ref: str, args: dict) -> tuple[bool, dict, str]:
        """One local helper entry (another plugin's, or this one's) through the plugin router: (ok, value, error text)."""
        if self.plugins is None:
            return False, {}, "the plugin router is not available"
        t0 = time.time()
        res = await self.plugins.call_entry_json(ref, args, timeout=900.0 if ref.startswith("doc_reader:") else 180.0)
        if isinstance(res, Ok):
            value = res.value if isinstance(res.value, dict) else {}
            data = value["data"] if isinstance(value.get("data"), dict) else value
            jc.record_call(ref, args, True, data, "", int((time.time() - t0) * 1000))
            return True, data, ""
        jc.record_call(ref, args, False, {}, str(getattr(res, "error", res))[:200], int((time.time() - t0) * 1000))
        return False, {}, str(getattr(res, "error", res))[:300]

    @plugin_entry(
        id="do_goal",
        name=tr("entry.do_goal.name", default="Do a goal on the PC"),
        description=tr("entry.do_goal.description", default=(
            "Carry out a goal about this PC, its screen or a document. An online model only plans it (text, a few short steps, "
            "through the privacy gateway); small local models choose and run every action, and the JARVIS kernel still asks the "
            "owner's PIN for risky steps, then the goal waits. If no online plan is available the local model plans alone. "
            "Reports each step, which model ran it and what was observed.")),
        input_schema={"type": "object", "properties": {"goal": {"type": "string", "description": "What the owner wants done, in their words"}},
                      "required": ["goal"]},
        timeout=900, llm_result_fields=["status", "planned_by", "summary", "steps", "approval_id", "reason", "provider", "question", "ask"],
    )
    @screen_text.cleaned                  # every string in the result is cleaned by default (screen_text.clean_result)
    async def do_goal(self, goal: str = "", answers: dict | None = None, **_):
        # D74: ASK, DO NOT GUESS. Plain rules (config/ambiguity.json) run before any plan or action: "notepad or wordpad", "the file from yesterday", "delete that", "close the
        # window", a missing recipient. A request that must be asked about becomes ONE question; the owner's answer is put into the ORIGINAL goal, which then goes on.
        out = await gate_mod.gate_request(goal, lambda need: DesktopPlugin._gate_fetch(self, need), DesktopPlugin._gate_ask(self), answers)
        if out.result is not None:
            return Ok(out.result)
        results = []
        for text in (out.texts or [goal])[:8]:
            tok = core.OWNER_UTTERANCE.set(text) if out.resolved else None          # what he chose is his own word for the rest of the run
            try:
                results.append(await DesktopPlugin._goal_core(self, text))
            finally:
                if tok is not None:
                    core.OWNER_UTTERANCE.reset(tok)
        res = gate_mod.merge(results)
        if out.asked:
            res["asked"] = out.asked
        return Ok(res)

    async def _goal_core(self, goal: str) -> dict:
        """The goal as it always ran (translate, the rules-first router, the online plan with local executors); the decider's own 'ask' ends in the same form (D74)."""
        res = await DesktopPlugin._goal_run(self, goal)
        if isinstance(res, dict) and res.get("status") == "needs_clarification":
            async def again(text2: str) -> dict:
                return await DesktopPlugin._goal_run(self, text2)
            res = await gate_mod.finish(res, goal, DesktopPlugin._gate_ask(self), again)
        return res

    async def _goal_run(self, goal: str) -> dict:
        goal, tr_info = await asyncio.to_thread(translate.entry_text, goal, source="goal")       # D58: English only below this line
        if tr_info and tr_info.get("clarify"):
            return {**translate.clarification_result(tr_info["clarify"]), "planned_by": "translation"}
        if tr_info and tr_info.get("translated"):
            # D74: a Telugu or Hindi sentence in its own letters is only plain English from here on ("ఇది తెరవండి" is "Open this."): the rules read it again.
            again = await gate_mod.gate_request(goal, lambda need: DesktopPlugin._gate_fetch(self, need), DesktopPlugin._gate_ask(self))
            if again.result is not None:
                return again.result
            if again.resolved:
                goal = " and ".join(again.texts)[:1000]
        started = time.monotonic()
        progress = _goal_progress_writer()
        jtok = jc.enter_task(core.OWNER_UTTERANCE.get() or goal)       # D61: the goal is one task in the journal; its steps group under the owner's own words
        try:
            # D60: one plain command ("open notepad", "pause", "open notepad and write hello") needs no online plan: the rules plan it, the
            # coordinator and the kernel run it exactly as before. Anything else is planned online and run by the local models as before.
            quick = quick_mod.match(goal, core.profiles())
            if quick is not None:
                res = await quick_mod.run(quick, self._goal_pc, progress)
            else:
                hands = goal_mod.Hands(run_pc=self._goal_pc, call=self._goal_call, readers=dict(self.goal_readers or {}), on_progress=progress,
                                       read_window=getattr(self, "_goal_read_window", None), pick_editor=getattr(self, "_goal_pick_editor", None))      # a host without them never takes the solve path
                utterance = " ".join(str(core.OWNER_UTTERANCE.get() or goal or "").split())[:goal_mod.MAX_GOAL]     # his own words when the host has them, else the goal given
                token = core.OWNER_GOAL.set(utterance)
                try:
                    res = await goal_mod.GoalRunner(hands).run(goal, goal_mod.flag_on(), utterance=utterance)
                finally:
                    core.OWNER_GOAL.reset(token)
            res["seconds"] = round(time.monotonic() - started, 2)
            if tr_info and isinstance(res, dict):
                res["translation"] = {k: tr_info.get(k) for k in ("source_lang", "confidence", "translated")}
                res["note"] = translate.understood_note(tr_info)
            return res
        finally:
            jc.exit_task(jtok)
            core.set_goal_progress("idle")

    # ---- D74: the ambiguity gate's two hands (what the request cannot say by itself, and the owner's panel)
    async def _gate_fetch(self, need: set):
        """The context the rules asked for: the open windows (a 'close the window'), the windows JARVIS opened, what the last task produced (an 'open it'). Each part that
        cannot be had is simply missing: that never makes the gate less careful."""
        from jarvis.plugins._shared import ambiguity
        ctx = ambiguity.Context(apps=list(decide.APPS), sites=list(decide.SITES))
        if "windows" in need:
            try:
                ctx.windows = await self._goal_windows()                  # (self's own: tests and hosts lend a stand-in)
            except Exception:  # noqa: BLE001
                ctx.windows = None
        if "launched" in need:
            ctx.launched = DesktopPlugin._launched_procs()
        if "last" in need:
            try:
                ctx.last = coord.previous()
            except Exception:  # noqa: BLE001
                ctx.last = None
        return ctx

    @staticmethod
    def _launched_procs() -> list[str]:
        """Process names of windows JARVIS opened in the last half hour and has not undone (the journal's close_window recipes): 'close it' may mean exactly these."""
        try:
            j = jc.get_journal()
            if j is None:
                return []
            now = time.time()
            return [str((d.get("undo") or {}).get("proc")) for d in j.undoable(40)
                    if (d.get("undo") or {}).get("kind") == "close_window" and (d.get("undo") or {}).get("proc") and now - float(d.get("ts") or 0) < 1800]
        except Exception:  # noqa: BLE001
            return []

    def _gate_ask(self):
        """The function that shows a form on the owner's panel and waits (jarvis_ask, D55), or None when this plugin does not run inside the face's tool server."""
        from jarvis.plugins._shared import owner_ask
        if not owner_ask.available():
            return None

        async def ask(spec: dict, timeout_s: float):
            return await asyncio.to_thread(owner_ask.ask, spec, timeout_s)
        return ask

    @plugin_entry(
        id="quick_command",
        name=tr("entry.quick_command.name", default="Run a plain command at once"),
        description=tr("entry.quick_command.description", default=(
            "Rules-first fast path for the face: when the owner's whole message is ONE plain command (open an app or a page, pause, volume, "
            "minimize, play a song, type text into an app, what time or date it is) it is run at once with no model, through the same "
            "coordinator and JARVIS kernel as every PC task, and the reply is returned. Anything else answers handled=false immediately "
            "and costs nothing. Never used by a chat model; the face calls it before it asks one.")),
        input_schema={"type": "object", "properties": {"text": {"type": "string", "description": "The owner's message"}}, "required": ["text"]},
        timeout=300, llm_result_fields=["handled", "status", "reply", "planned_by", "approval_id", "label", "seconds"],
    )
    @screen_text.cleaned                  # every string in the result is cleaned by default (screen_text.clean_result)
    async def quick_command(self, text: str = "", **_):
        started = time.monotonic()
        spoken = await asyncio.to_thread(taskvoice.handle, text, stop=lambda: coord.stop_all("stopped by the owner by voice", 10.0), ops_running=coord.operations)      # D65: "what are you doing", "try another way", "what have you learned"
        if spoken is not None:
            return Ok({**spoken, "approval_id": None, "seconds": round(time.monotonic() - started, 2)})
        said = await asyncio.to_thread(names_mod.quick_answer, text)           # D62: "what names do you know", "forget Priya": a rule; the names never reach a model
        if said is not None:
            return Ok({"handled": True, "status": "completed", "reply": said, "planned_by": "rules", "label": "One moment", "approval_id": None,
                       "seconds": round(time.monotonic() - started, 2)})
        # D74: the face asks this first for every command-looking message, BEFORE any chat model: a request that must be asked about ("open notepad or wordpad",
        # "delete that", "close the window") is turned into the question here, whatever the chat model would have done with it. Without the owner's panel (this plugin
        # is not inside the face's tool server) the message goes on to the chat model, and do_goal asks there.
        ask = DesktopPlugin._gate_ask(self)
        if ask is not None and gate_mod.enabled():
            out = await gate_mod.gate_request(text, lambda need: DesktopPlugin._gate_fetch(self, need), ask)
            if out.asked or out.result is not None or out.resolved:
                if out.result is not None:
                    return Ok({"handled": True, "status": out.result["status"], "reply": out.result["summary"], "planned_by": gate_mod.PLANNED_BY, "label": "One question",
                               "approval_id": None, "seconds": round(time.monotonic() - started, 2)})
                results = []
                for t in out.texts[:8]:
                    tok = core.OWNER_UTTERANCE.set(t)
                    try:
                        results.append(await DesktopPlugin._goal_core(self, t))
                    finally:
                        core.OWNER_UTTERANCE.reset(tok)
                res = gate_mod.merge(results)
                return Ok({"handled": True, "status": res.get("status"), "reply": res.get("summary"), "planned_by": res.get("planned_by"), "label": "On it",
                           "approval_id": res.get("approval_id"), "seconds": round(time.monotonic() - started, 2)})
        elif gate_mod.enabled():
            pre = await gate_mod.gate_request(text, lambda need: DesktopPlugin._gate_fetch(self, need), None)
            if pre.result is not None:
                return Ok({"handled": False})                 # the chat model's do_goal call gets the question (with its form) and shows it
        quick = quick_mod.match(text, core.profiles())
        if quick is None:
            return Ok({"handled": False})
        progress = _goal_progress_writer()
        try:
            res = await quick_mod.run(quick, self._goal_pc, progress)
        finally:
            core.set_goal_progress("idle")
        return Ok({"handled": True, "status": res["status"], "reply": res["summary"], "planned_by": res["planned_by"], "label": quick.ack,
                   "approval_id": res.get("approval_id"), "seconds": round(time.monotonic() - started, 2)})

    @plugin_entry(
        id="undo_steps",
        name=tr("entry.undo_steps.name", default="Undo what JARVIS did"),
        description=tr("entry.undo_steps.description", default=(
            "Reverse steps from the journal (what JARVIS did on this PC), ONLY when the owner asked to undo: the last n steps that changed something, "
            "the steps of one task, or named steps. A new file goes to the Recycle Bin, a binned file comes back, a move is reversed, a window JARVIS opened is "
            "closed, a setting goes back. Each is checked first (it must be unchanged since) and afterwards; it says exactly what was and was not undone.")),
        input_schema={"type": "object", "properties": {
            "n": {"type": "integer", "description": "undo the newest n steps that changed something"},
            "task": {"type": "string", "description": "undo every undoable step of this task id"},
            "ids": {"type": "array", "items": {"type": "string"}, "description": "journal step ids"}}},
        timeout=300, llm_result_fields=["ok", "undone", "not_undone", "results", "summary"],
    )
    @screen_text.cleaned                  # every string in the result is cleaned by default (screen_text.clean_result)
    async def undo_steps(self, n: int = 0, task: str = "", ids: list | None = None, **_):
        j = jc.get_journal()
        if j is None:
            return Err(SdkError("the journal is not available here"))
        if ids:
            steps = [s for s in (j.get(str(i)) for i in list(ids)[:50]) if s and s.get("undo")]
        elif task:
            steps = j.undoable(task_id=str(task)[:60])[:50]
        else:
            steps = j.undoable(max(1, min(int(n or 1), 20)))
        steps.sort(key=lambda s: s.get("ts") or 0, reverse=True)           # newest first: a task is taken back in reverse order
        if not steps:
            return Ok({"ok": False, "undone": 0, "not_undone": 0, "results": [], "summary": "There was nothing to undo."})
        loop = asyncio.get_running_loop()
        pc = self._pc()
        pc.approval_wait_s = 0.0                            # an undo that would need the PIN says so and stops; it never waits for it

        class _Hand:
            def run(_self, body, timeout, what):
                return asyncio.run_coroutine_threadsafe(DesktopPlugin._run(self, pc, body, timeout, what), loop).result(timeout + 30)
        try:
            with _PcLock("undo_steps", 300):
                report = await asyncio.to_thread(undoer.run, steps, _Hand(), j)
        except coord.Busy as e:
            return _err(e)
        return Ok(report)

    @plugin_entry(
        id="stop_pc_task",
        name=tr("entry.stop_pc_task.name", default="Stop the PC task"),
        description=tr("entry.stop_pc_task.description", default=(
            "Stop everything that is acting on the PC: a task, a mission, a single action waiting for an approval or "
            "running, a troubleshooter. Cancels the node jobs and denies the approvals they wait on, and says honestly "
            "which operations really ended (stopped) and which did not yet (still_running).")),
        input_schema={"type": "object", "properties": {}}, timeout=45,
        llm_result_fields=["stopped_jobs", "stopped", "still_running", "requested", "approvals_denied", "message", "alerts", "stop_errors"],
    )
    @screen_text.cleaned                  # every string in the result is cleaned by default (screen_text.clean_result)
    async def stop_pc_task(self, **_):
        # Never "stopped" merely because this handler returned: an operation counts as stopped only once its worker ended.
        res = await asyncio.to_thread(coord.stop_all, "stopped by the owner", min(coord.STOP_WAIT_S, 20.0))
        stopped, still = res["stopped"], res["still_running"]
        out = {"stopped_jobs": len(stopped), "stopped": [d["id"] for d in stopped], "still_running": still,
               "requested": res["requested"], "approvals_denied": res.get("approvals_denied", []),
               "approvals_unreachable": res.get("approvals_unreachable", [])}
        if still:
            names = ", ".join(f"{d['id']} ({d['kind']}, {d['state']})" for d in still)
            out["message"] = (f"Stop was sent, but {len(still)} PC operation(s) have not ended yet: {names}. Their PC lock stays "
                              "held until they do; ask me to stop again or to recover the PC lock if it does not clear.")
        elif stopped:
            out["message"] = f"Stopped {len(stopped)} PC operation(s); each has ended."
        elif out["approvals_denied"]:
            out["message"] = f"Nothing was running on the PC; {len(out['approvals_denied'])} approval(s) it left waiting were denied."
        else:
            out["message"] = "Nothing was running on the PC."
        if out["approvals_denied"] and (stopped or still):
            out["message"] += f" {len(out['approvals_denied'])} pending approval(s) were denied."
        if out["approvals_unreachable"]:
            out["message"] += (f" {len(out['approvals_unreachable'])} approval(s) could not be denied (the console did not answer); "
                               "deny them in the JARVIS console.")
        if res.get("stop_errors"):                             # ST-5: a part of the stop itself failed; say so, do not imply it worked
            out["stop_errors"] = res["stop_errors"][:5]
            out["message"] += " Part of the stop failed: " + "; ".join(res["stop_errors"][:3]) + "."
        if res.get("alerts"):                                  # a lock was released although a node job never confirmed it ended
            out["alerts"] = res["alerts"]
            out["message"] += " WARNING: " + res["alerts"][-1]["message"]
        return Ok(out)

    @plugin_entry(
        id="recover_pc_lock",
        name=tr("entry.recover_pc_lock.name", default="Recover a stuck PC lock"),
        description=tr("entry.recover_pc_lock.description", default=(
            "ONLY when the owner asks, after Stop reported a PC operation still_running: free the PC lock of an operation that "
            "will not end. It succeeds only if that operation was asked to stop and the kernel confirms every PC job it had "
            "ended (or its process is gone); otherwise it refuses and says why. It never lets a second task act while the "
            "first may still control the desktop.")),
        input_schema={"type": "object", "properties": {}}, timeout=30,
        llm_result_fields=["recovered", "ok", "status", "how", "reason", "operation", "alerts"],
    )
    @screen_text.cleaned                  # every string in the result is cleaned by default (screen_text.clean_result)
    async def recover_pc_lock(self, **_):
        res = await asyncio.to_thread(coord.recover)
        coord.LOG.warning("pc lock recovery requested: %s", {k: v for k, v in res.items() if k != "operation"})
        recovered = bool(res.get("recovered"))
        # explicit outcome fields: a refusal is `ok: False` / status "refused", never a success the caller can mistake for one
        return Ok(dict(res, ok=recovered, status="recovered" if recovered else "refused", alerts=coord.alerts()))

    @plugin_entry(
        id="stop_pc_job",
        name=tr("entry.stop_pc_job.name", default="Stop a PC job"),
        description=tr("entry.stop_pc_job.description", default="Stop a running PC job by its id; whatever it started is stopped."),
        input_schema={"type": "object", "properties": {"job": {"type": "string"}}, "required": ["job"]},
        timeout=30, llm_result_fields=["id", "state", "note", "error"],
    )
    @screen_text.cleaned                  # every string in the result is cleaned by default (screen_text.clean_result)
    async def stop_pc_job(self, job: str = "", **_):
        try:
            return Ok(await asyncio.to_thread(self._pc().cancel, job))
        except core.DesktopError as e:
            return _err(e)

    @plugin_entry(
        id="list_browser_profiles",
        name=tr("entry.list_browser_profiles.name", default="Chrome profiles"),
        description=tr("entry.list_browser_profiles.description", default=(
            "The owner's Chrome profiles JARVIS can use, with the names to say for each and a masked account. Ask the owner "
            "which one when a name fits more than one.")),
        input_schema={"type": "object", "properties": {}}, timeout=10, llm_result_fields=["profiles"],
    )
    @screen_text.cleaned                  # every string in the result is cleaned by default (screen_text.clean_result)
    async def list_browser_profiles(self, **_):
        return Ok({"profiles": core.profiles()})
