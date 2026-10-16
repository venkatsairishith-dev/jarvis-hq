"""Desktop: Neko's hands on this Windows PC — the owner's existing Chrome profiles, apps, keyboard and mouse, read-only
checks, and a repair workflow that backs up first and verifies after. Every action is a JARVIS kernel job (the kernel
decides; consequential actions wait for the owner's PIN in the JARVIS console). Logic in core.py.
"""

from __future__ import annotations

import asyncio
import json
import time

from plugin.sdk.plugin import Err, NekoPluginBase, Ok, SdkError, lifecycle, neko_plugin, plugin_entry, timer_interval, tr

from . import core, decide

PROFILE_HELP = "Chrome profile alias: 'default' (the owner's default account) or a name such as 'VenkatAMV'; see list_browser_profiles."


def _brief(res: dict) -> dict:
    """What the main agent sees of one action: its outcome and what the node observed (no page or screen text)."""
    r = dict(res.get("result") or {})
    r.pop("local_result", None)
    out = {"state": res.get("state"), "ok": bool(r.get("ok")) and res.get("state") == "done", **{k: v for k, v in r.items() if k != "ok"}}
    for k in ("approval_id", "how", "reason", "level"):
        if res.get(k):
            out[k] = res[k]
    return out


@neko_plugin
class DesktopPlugin(NekoPluginBase):
    def _pc(self):
        return core.Pc(on_state=core.set_activity)

    async def _run(self, pc: core.Pc, body: dict, timeout: float, what: str = ""):
        try:
            return await asyncio.to_thread(pc.run, body, timeout, what)
        except asyncio.CancelledError:                    # the owner stopped the task: stop the job on the PC too
            pc.stopped = True
            if pc.current_job:
                await asyncio.to_thread(pc.cancel, pc.current_job)
            raise

    async def _one(self, body: dict, timeout: float = 90.0, what: str = ""):
        try:
            res = await self._run(self._pc(), body, timeout, what)
        except core.DesktopError as e:
            return Err(SdkError(str(e)))
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
            missions.configure(coord.DATA / "neko" / "missions")
            missions.E.agents = self._agents(loop)
            missions.E.plan_fn = lambda goal, ctx: (lambda p: p.steps if p.status == "ok" else [])(
                decide.decide(goal, core.profiles(), context=ctx))
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
        timeout=15, llm_result_fields=["mission", "goal", "status", "steps", "replans", "error", "explain"],
    )
    async def mission_status(self, mission: str = "", **_):
        await self._missions_ready()
        from . import missions
        rec = await asyncio.to_thread(lambda: missions.load(mission) if mission else missions.latest())
        if not rec:
            return Ok({"status": "none"})
        return Ok({"mission": rec.get("id"), "goal": rec.get("goal"), "status": rec.get("status"), "replans": rec.get("replans"),
                   "steps": [{k: s.get(k) for k in ("n", "tool", "passed", "checked", "uncertainty")} for s in rec.get("steps", [])],
                   "error": rec.get("error"), "explain": rec.get("explain")})

    @plugin_entry(
        id="stop_mission",
        name=tr("entry.stop_mission.name", default="Stop the mission"),
        description=tr("entry.stop_mission.description", default="Stop the current (or a named) mission and the PC job it is running."),
        input_schema={"type": "object", "properties": {"mission": {"type": "string", "default": ""}}},
        timeout=30, llm_result_fields=["mission", "stopped"],
    )
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
        state_file = coord.DATA / "neko" / "monitor_state.json"
        st = monitor.State.load(state_file)
        alerts = monitor.Monitor(st, tasks=lambda: monitor.open_tasks(coord.TASKS), budget_s=coord.TOTAL_BUDGET_S).check()
        st.save(state_file)
        log = coord.DATA / "neko" / "monitor_alerts.jsonl"
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
    async def recent_pc_alerts(self, hours: float = 24, **_):
        from . import coordinator as coord
        log = coord.DATA / "neko" / "monitor_alerts.jsonl"
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
    async def open_website(self, url: str = "", profile: str = "default", **_):
        return await self._one({"op": "browser", "action": "open", "profile": profile, "url": url}, 90)

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
    async def play_youtube(self, query: str = "", profile: str = "default", **_):
        pc = self._pc()
        core.set_activity("working", f"YouTube: {query}"[:120])
        try:
            res = await self._run(pc, {"op": "browser", "action": "play", "profile": profile, "target": query[:300]}, 150)
            out = _brief(res)
            if out.get("needs") == "extension":
                # without the extension Neko can still open the search, so the owner picks and presses play
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
            return Err(SdkError(str(e)))
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
    async def run_browser_action(self, action: str = "read", profile: str = "default", target: str = "", text: str = "",
                                 approval_id: str = "", **_):
        body = {"op": "browser", "action": action, "profile": profile, "target": target, "text": text}
        if approval_id:
            body["approval_id"] = approval_id
        try:
            res = await self._run(self._pc(), body, 120, f"{action} {target}"[:120])
        except core.DesktopError as e:
            return Err(SdkError(str(e)))
        page = core.take_local((res.get("result") or {}).get("local_result"))      # read once, deleted at once
        out = _brief(res)
        if page and core.screen_text_allowed():                                    # the owner allowed page text to reach the model
            out["page"] = str(page)[:6000]
        elif page:
            out["page_note"] = "the page text stayed on this laptop (set NEKO_SCREEN_TEXT_TO_MODEL=1 to let the model read pages)"
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
        timeout=360, llm_result_fields=["state", "ok", "opened", "done", "verified", "window", "error", "approval_id", "how"],
    )
    async def run_pc_action(self, action: str = "", target: str = "", text: str = "", approval_id: str = "", **_):
        body = {"op": "open_app", "target": target} if action == "open_app" else {"op": "ui", "action": action, "target": target, "text": text}
        if approval_id:
            body["approval_id"] = approval_id
        return await self._one(body, 60, f"{action} {target}"[:120])

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
    async def check_pc(self, check: str = "disk", target: str = "", **_):
        body = {"op": "media"} if check == "media" else {"op": "diag", "check": check, "target": target}
        return await self._one(body, 90)

    @plugin_entry(
        id="run_troubleshooter",
        name=tr("entry.run_troubleshooter.name", default="Troubleshoot an app"),
        description=tr("entry.run_troubleshooter.description", default=(
            "Find out why a Windows app shows an error and fix it safely: read its error dialog, event log and crash records "
            "(on this laptop), check the cause against the file it names, back up, apply the fix after the owner approves, "
            "start the app again and verify the error is gone. Reports a verified cause or says it is only a hypothesis.")),
        input_schema={"type": "object", "properties": {
            "app": {"type": "string", "description": "The app's process name, e.g. NekoSampleApp"},
            "start_name": {"type": "string", "description": "Its Start-menu name, to start it again for the check, e.g. Neko Sample App"},
            "apply_fix": {"type": "boolean", "default": True}}, "required": ["app"]},
        timeout=900, llm_result_fields=["outcome", "cause", "verified", "evidence", "fix", "backup", "undo", "verification", "steps", "approval_id", "how"],
    )
    async def run_troubleshooter(self, app: str = "", start_name: str = "", apply_fix: bool = True, **_):
        steps = []

        def step(state, detail=""):
            steps.append({"state": state, "detail": detail[:160], "at": time.strftime("%H:%M:%S")})
            core.set_activity(state, detail)
        pc = core.Pc(on_state=step)                       # "waiting_for_permission" lands in the report too

        async def job(body, timeout=60, what=""):
            return _brief(await self._run(pc, body, timeout, what))

        try:
            step("inspecting", f"Looking at {app}")
            raw = await self._run(pc, {"op": "diag", "check": "window_text", "target": app}, 60)
            windows = core.take_local((raw.get("result") or {}).get("local_result")) or []
            if not windows and start_name:
                await job({"op": "open_app", "target": start_name}, 60)
                await asyncio.sleep(3)
                raw = await self._run(pc, {"op": "diag", "check": "window_text", "target": app}, 60)
                windows = core.take_local((raw.get("result") or {}).get("local_result")) or []
            ev = await self._run(pc, {"op": "diag", "check": "app_errors", "target": app}, 60)
            events = core.take_local((ev.get("result") or {}).get("local_result")) or []
            crashes = (await job({"op": "diag", "check": "crashes", "target": app})).get("rows") or []
            step("diagnosing", "Checking the cause")
            d = await asyncio.to_thread(core.diagnose, windows, events, crashes)
            report = {"cause": d["cause"], "verified": d["verified"], "evidence": d["evidence"], "fix": None, "steps": steps}
            if not d.get("fix") or not apply_fix:
                step("completed", "Diagnosis only (no fix asked)" if d.get("fix") else "No safe automatic fix")
                report.update(outcome="diagnosed" if d["verified"] else "hypothesis only", fix=d.get("fix"))
                return Ok(report)
            fix = d["fix"]
            report["fix"] = {"action": fix["action"], "file": fix["target"], "why": fix["why"]}
            body = {"op": "repair", "action": fix["action"], "target": fix["target"], "args": fix["args"]}
            res = await self._run(pc, body, 120, f"edit {fix['target']} ({fix['why']})")
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
                step("failed", str(r.get("error") or res.get("state"))[:160])
                report.update(outcome="the fix was not applied", error=r.get("error") or res.get("state"))
                return Ok(report)
            report.update(backup=r.get("backup"), undo=r.get("undo"))
            step("verifying", "Starting it again to check")
            ok_btn = [b for w in windows if w.get("dialog") for b in w.get("buttons") or [] if b.lower() in ("ok", "close")]
            if ok_btn:                                    # dismiss the old error dialog first, as the owner would
                await job({"op": "ui", "action": "click", "target": f"{app}|{ok_btn[0]}"}, 60)
                await asyncio.sleep(1)
            if start_name:
                await job({"op": "open_app", "target": start_name}, 60)
                await asyncio.sleep(4)
            raw = await self._run(pc, {"op": "diag", "check": "window_text", "target": app}, 60)
            after = core.take_local((raw.get("result") or {}).get("local_result")) or []
            dialogs = [w for w in after if w.get("dialog")]
            running = [w for w in after if not w.get("dialog")]
            fixed = bool(running) and not dialogs
            report["verification"] = {"windows_now": len(after), "error_dialogs_now": len(dialogs), "main_window": bool(running),
                                      "title": running[0].get("title", "")[:120] if running else ""}
            step("completed" if fixed else "failed", "The error is gone" if fixed else "The error is still there")
            report["outcome"] = "fixed and verified" if fixed else "fix applied, but the check failed (undo with the backup)"
            return Ok(report)
        except core.DesktopError as e:
            step("failed", str(e))
            return Err(SdkError(str(e)))
        finally:
            core.set_activity("idle")

    # ---------- the coordinator: one request -> local decision -> agents -> verified outcome ----------
    def _agents(self, loop) -> dict:
        """The step runners the coordinator uses. Every PC action is a kernel job (core.Pc); nothing here decides permission."""
        import re as _re
        active = self._active_pcs = getattr(self, "_active_pcs", [])

        def pc(body, timeout=90.0, what=""):
            p = core.Pc(on_state=core.set_activity)
            active.append(p)
            try:
                res = p.run(body, timeout, what)
            finally:
                active.remove(p)
            r = dict(res.get("result") or {})
            core.take_local(r.pop("local_result", None))            # screen/page text: read by nobody here, deleted
            r["state"] = res.get("state")
            r["ok"] = bool(r.get("ok")) and res.get("state") == "done"
            for k in ("approval_id", "how"):
                if res.get(k):
                    r[k] = res[k]
            return r

        def alias(folder):
            return "default" if folder == "Default" else _re.sub(r"[^a-z0-9]", "", str(folder).lower())

        def find_process(app):
            """The process of an open window whose title or process name fits the app the owner named. "(active window)":
            a window whose title reads like an error, else the window in front (never Neko's own or the editor)."""
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

        def check(step):
            if step.get("check") == "media":
                return pc({"op": "media"}, 30)
            return pc({"op": "diag", "check": step.get("check", "disk"), "target": step.get("app", "")}, 90)

        def call(coro, timeout):
            import asyncio as _a
            target = getattr(self, "_cmd_loop", None) or loop          # the entries' live loop (a mission may outlive the
            return _a.run_coroutine_threadsafe(coro, target).result(timeout)   # call that started it)

        def troubleshoot(step):
            proc = find_process(step["app"])
            start = step["app"] if step["app"] != decide.ACTIVE else ""
            if not proc and start:                               # not open: start it so its error can be seen
                pc({"op": "open_app", "target": start}, 60)
                time.sleep(3)
                proc = find_process(step["app"])
            if not proc:
                return {"ok": False, "error": f"'{step['app']}' has no window, even after starting it"}
            res = call(self.run_troubleshooter(app=proc, start_name=start), 900)
            data = res.value if isinstance(res, Ok) else {"error": str(getattr(res, "error", res))}
            return {"ok": data.get("outcome") in ("fixed and verified", "diagnosed"), **data}

        def research(step):
            want_open = bool(step.get("open_after")) and step.get("deliverable", "none") != "none"
            before = {w.get("hwnd") for w in pc({"op": "windows"}, 30).get("windows") or []} if want_open else set()
            res = call(self.plugins.call_entry_json("research:research_topic", {"question": step["query"], "deliverable": step.get("deliverable", "none"),
                                                                                 "open_after": want_open}, timeout=900.0), 960)
            data = res.value.get("data", res.value) if isinstance(res, Ok) and isinstance(res.value, dict) else {}
            out = {"ok": bool(data.get("summary")), "summary": str(data.get("summary") or "")[:600], "file": data.get("file"),
                   "qa_passed": data.get("qa_passed"), "sources": (data.get("sources") or [])[:8],
                   "error": None if data else str(getattr(res, "error", ""))[:200]}
            if want_open and data.get("file"):
                import os as _os
                seen = opened_window(_os.path.splitext(_os.path.basename(str(data["file"])))[0].lower()[:40], before)
                out.update(opened=seen or None, opened_verified=bool(seen))
            return out

        def opened_window(stem, before, tries=10):
            """A NEW window whose title shows the file's name: the evidence that a made file really opened."""
            for _ in range(tries):
                ws = pc({"op": "windows"}, 30).get("windows") or []
                hit = [w for w in ws if w.get("hwnd") not in before and stem and stem[:25] in str(w.get("title", "")).lower()]
                if hit:
                    return f"{hit[0].get('process')}: window for {stem}"
                time.sleep(2)
            return ""

        def open_output(step):
            """Re-open a file Neko made ("open it"): only through the deliverables plugin, which opens nothing outside its
            own output folder."""
            import os as _os
            before = {w.get("hwnd") for w in pc({"op": "windows"}, 30).get("windows") or []}
            res = call(self.plugins.call_entry_json("deliverables:open_output", {"path": step.get("path", "")}, timeout=30.0), 40)
            if not isinstance(res, Ok):
                return {"ok": False, "error": str(getattr(res, "error", res))[:200]}
            stem = _os.path.splitext(_os.path.basename(str(step.get("path", ""))))[0].lower()[:40]
            seen = opened_window(stem, before)
            return {"ok": True, "opened": seen or step.get("path"), "opened_verified": bool(seen)}

        def stop(step):
            for p in list(active):
                p.stopped = True
            self._cancel = True
            return {"ok": True, "stopped_jobs": len(active)}

        return {
            "open_website": lambda s: pc({"op": "browser", "action": "open", "profile": alias(s["profile"]), "url": s["url"]}, 90),
            "play_media": lambda s: (lambda r: dict(r, windows_media=[f"{m.get('title')} ({m.get('status')})" for m in
                                                                      pc({"op": "media"}, 30).get("sessions") or []]) if r.get("ok") else r)(
                pc({"op": "browser", "action": "play", "profile": alias(s["profile"]), "target": s["query"]}, 200)),
            "browser": lambda s: pc({"op": "browser", "action": s["action"], "profile": alias(s.get("profile", "Default")),
                                     "target": s.get("target", ""), "text": s.get("text", "")}, 120, f"{s['action']} {s.get('target', '')}"),
            "open_app": open_app,
            "window": window, "type_text": type_text, "press_key": press_key, "open_file": open_file, "check_pc": check,
            "troubleshoot": troubleshoot, "research": research, "open_output": open_output, "stop": stop,
        }

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
    async def run_pc_task(self, request: str = "", **_):
        from . import coordinator as coord
        loop = asyncio.get_running_loop()
        await self._missions_ready()                        # also resumes a mission a restart interrupted
        self._cancel = False
        c = coord.Coordinator(self._agents(loop), core.profiles(), on_progress=core.set_activity,
                              cancelled=lambda: bool(getattr(self, "_cancel", False)))
        try:
            rec = await asyncio.to_thread(c.run, request)
        except asyncio.CancelledError:
            self._cancel = True
            for p in list(getattr(self, "_active_pcs", [])):
                p.stopped = True
            raise
        finally:
            core.set_activity("idle")
        return Ok({"status": rec["status"], "summary": coord.summary(rec), "question": rec.get("question"), "task": rec["id"],
                   "plan_model": (rec.get("plan") or {}).get("model"), "plan_seconds": (rec.get("plan") or {}).get("seconds"),
                   "steps": [{k: s.get(k) for k in ("n", "tool", "agent", "attempts", "passed", "checked", "uncertainty")} for s in rec["steps"]],
                   "uncertain": rec.get("uncertain"), "approval_id": rec.get("approval_id"), "how": rec.get("how"),
                   "route": rec.get("route")})

    @plugin_entry(
        id="stop_pc_task",
        name=tr("entry.stop_pc_task.name", default="Stop the PC task"),
        description=tr("entry.stop_pc_task.description", default="Stop the running PC task and the job it is waiting on."),
        input_schema={"type": "object", "properties": {}}, timeout=30, llm_result_fields=["stopped_jobs"],
    )
    async def stop_pc_task(self, **_):
        self._cancel = True
        n = 0
        for p in list(getattr(self, "_active_pcs", [])):
            p.stopped = True
            if p.current_job:
                await asyncio.to_thread(p.cancel, p.current_job)
                n += 1
        return Ok({"stopped_jobs": n})

    @plugin_entry(
        id="stop_pc_job",
        name=tr("entry.stop_pc_job.name", default="Stop a PC job"),
        description=tr("entry.stop_pc_job.description", default="Stop a running PC job by its id; whatever it started is stopped."),
        input_schema={"type": "object", "properties": {"job": {"type": "string"}}, "required": ["job"]},
        timeout=30, llm_result_fields=["id", "state", "note", "error"],
    )
    async def stop_pc_job(self, job: str = "", **_):
        try:
            return Ok(await asyncio.to_thread(self._pc().cancel, job))
        except core.DesktopError as e:
            return Err(SdkError(str(e)))

    @plugin_entry(
        id="list_browser_profiles",
        name=tr("entry.list_browser_profiles.name", default="Chrome profiles"),
        description=tr("entry.list_browser_profiles.description", default=(
            "The owner's Chrome profiles Neko can use, with the names to say for each and a masked account. Ask the owner "
            "which one when a name fits more than one.")),
        input_schema={"type": "object", "properties": {}}, timeout=10, llm_result_fields=["profiles"],
    )
    async def list_browser_profiles(self, **_):
        return Ok({"profiles": core.profiles()})
