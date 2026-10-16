"""Desktop: Neko's hands on this Windows PC — the owner's existing Chrome profiles, apps, keyboard and mouse, read-only
checks, and a repair workflow that backs up first and verifies after. Every action is a JARVIS kernel job (the kernel
decides; consequential actions wait for the owner's PIN in the JARVIS console). Logic in core.py.
"""

from __future__ import annotations

import asyncio
import time

from plugin.sdk.plugin import Err, NekoPluginBase, Ok, SdkError, lifecycle, neko_plugin, plugin_entry, tr

from . import core

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
        return Ok({"status": "ready", "profiles": len(core.profiles())})

    @lifecycle(id="shutdown")
    async def shutdown(self, **_):
        return Ok({"status": "stopped"})

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
        timeout=180, llm_result_fields=["state", "ok", "chose", "title", "playing", "position_s", "verified_by", "error", "needs"],
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
                                        "matches", "tabs", "paused", "position_s", "error", "needs", "approval_id", "how"],
    )
    async def run_browser_action(self, action: str = "read", profile: str = "default", target: str = "", text: str = "",
                                 approval_id: str = "", **_):
        body = {"op": "browser", "action": action, "profile": profile, "target": target, "text": text}
        if approval_id:
            body["approval_id"] = approval_id
        return await self._one(body, 120, f"{action} {target}"[:120])

    @plugin_entry(
        id="run_pc_action",
        name=tr("entry.run_pc_action.name", default="PC action"),
        description=tr("entry.run_pc_action.description", default=(
            "Operate a Windows app the owner names: open an app by its Start-menu name (open_app), bring a window to the "
            "front (focus), click a button or type into a field by its accessible name (target 'process|element'), press a "
            "key such as ctrl+s or playpause, scroll, or as a last resort click at a point ('process|x,y'). Structured "
            "accessibility first; risky keys and consequential clicks wait for the owner's approval.")),
        input_schema={"type": "object", "properties": {
            "action": {"type": "string", "enum": ["open_app", "focus", "click", "type", "key", "scroll", "click_at"]},
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
