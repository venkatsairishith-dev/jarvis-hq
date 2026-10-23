"""JARVIS Bridge: hand a task the face heard (by voice or text) to JARVIS's main agent.

the face routes a request to one execution channel. JARVIS already has a main agent (Hermes) that plans a task with
acceptance criteria, hands parts to the lab sub-agents (Hermes, OpenCode, goose) and verifies the result, all under the
kernel's approvals and audit. This plugin is only the door between the two; it adds no agent logic of its own.
"""

from __future__ import annotations

import time
from typing import Any

from jarvis.plugins._shared import screen_text
from jarvis.pluginapi import Err, PluginBase, Ok, SdkError, lifecycle, jarvis_plugin, plugin_entry, tr, ui

from .client import BridgeError, JarvisClient, SOURCE, speak_full_reply

REPLY_CHARS = 1200


def _err(exc: BridgeError):
    return Err(SdkError(exc.message))


@jarvis_plugin
class JarvisBridgePlugin(PluginBase):
    @lifecycle(id="startup")
    async def startup(self, **_):
        # Nothing to open: every entry makes one short loopback request to the JARVIS console.
        return Ok({"status": "ready", "console": JarvisClient().base})

    @lifecycle(id="shutdown")
    async def shutdown(self, **_):
        return Ok({"status": "stopped"})

    @ui.context(id="dashboard")
    async def dashboard(self):
        """What the JARVIS panel in the face shows. State only: reply text stays in the JARVIS console."""
        client = JarvisClient()
        out: dict[str, Any] = {"console_url": client.base, "ready": False, "busy": None, "kill_switch_on": False,
                               "message": "", "recent": []}
        out["approvals"], out["connectors"] = [], []
        try:
            status = await client.status()
            rows = await client.commands(20)
        except BridgeError as exc:
            out["message"] = exc.message
            return out
        try:                                               # read-only extras: a failure here never hides the status
            out["approvals"] = await client.approvals()
            out["connectors"] = await client.connectors()
        except BridgeError:
            pass
        out.update(ready=status["kernel_reachable"] and status["kernel_authorized"] and not status["kill_switch_on"],
                   busy=status["busy"], kill_switch_on=status["kill_switch_on"])
        out["message"] = ("The kill switch is on." if status["kill_switch_on"] else "JARVIS is ready." if out["ready"]
                          else "The console is running but the kernel is not reachable or not authorized.")
        out["recent"] = [
            {"id": str(r.get("id") or ""), "state": str(r.get("state") or ""),
             "when": time.strftime("%H:%M:%S", time.localtime(float(r.get("ts") or 0))),
             "task": " ".join(str(r.get("text") or "").split())[:100]}
            for r in rows if r.get("source") == SOURCE][:8]
        return out

    @ui.action(id="delegate_task", label=tr("actions.delegate_task.label", default="Hand to JARVIS"), tone="primary", refresh_context=True)
    @plugin_entry(
        id="delegate_task",
        name=tr("entry.delegate_task.name", default="Hand a task to JARVIS"),
        description=tr(
            "entry.delegate_task.description",
            default=(
                "Hand a real task to JARVIS, the owner's main agent. Use it for work that needs planning, several steps "
                "or specialist helpers: research, writing or fixing code, working with files, using apps or the browser, "
                "or anything the user asks to 'get done'. JARVIS plans it, picks the right sub-agents and tools, asks the "
                "user for approval where needed and verifies the result. Do not use it for small talk or simple questions."
            ),
        ),
        input_schema={
            "type": "object",
            "properties": {
                "task": {
                    "type": "string",
                    "description": tr("entry.delegate_task.param.task", default="The task, in the user's own words, with any details they gave (at most 2000 characters)."),
                },
            },
            "required": ["task"],
        },
        timeout=20,
        llm_result_fields=["command_id", "state", "message"],
    )
    @screen_text.cleaned                  # every string in the result is cleaned by default (screen_text.clean_result)
    async def delegate_task(self, task: str = "", **_):
        try:
            sent = await JarvisClient().delegate(task)
        except BridgeError as exc:
            return _err(exc)
        return Ok({
            "command_id": sent["id"],
            "state": sent["state"],
            "message": "JARVIS has the task and is working on it. Ask for the result with task_result.",
        })

    @plugin_entry(
        id="task_result",
        name=tr("entry.task_result.name", default="Check a JARVIS task"),
        description=tr(
            "entry.task_result.description",
            default="Check whether a task handed to JARVIS is still running, finished or failed. Leave command_id empty for the latest one.",
        ),
        input_schema={
            "type": "object",
            "properties": {
                "command_id": {
                    "type": "string",
                    "description": tr("entry.task_result.param.command_id", default="The command_id returned by delegate_task (optional)."),
                },
            },
        },
        timeout=20,
        llm_result_fields=["command_id", "state", "message", "reply"],
    )
    @screen_text.cleaned                  # every string in the result is cleaned by default (screen_text.clean_result)
    async def task_result(self, command_id: str = "", **_):
        try:
            rows = await JarvisClient().commands(15)
        except BridgeError as exc:
            return _err(exc)
        wanted = str(command_id or "").strip()
        row = next((r for r in rows if r.get("id") == wanted), None) if wanted else next((r for r in rows if r.get("source") == SOURCE), None)
        if row is None:
            return Err(SdkError("JARVIS has no such task from the face." if wanted else "No task from the face has been handed to JARVIS yet."))
        state = str(row.get("state") or "unknown")
        out: dict[str, Any] = {"command_id": row.get("id"), "state": state}
        if state == "running":
            out["message"] = "JARVIS is still working on it."
        elif state == "done":
            if speak_full_reply():
                out["message"] = "JARVIS finished."
                out["reply"] = str(row.get("reply") or "")[:REPLY_CHARS]
            else:
                out["message"] = "JARVIS finished. The details are in the JARVIS console; they are not read out here because they may be private."
        elif state == "failed":
            out["message"] = "JARVIS could not complete it: " + str(row.get("error") or "no reason given")[:200]
        else:
            out["message"] = f"The task is {state}."
        return Ok(out)

    @plugin_entry(
        id="cancel_task",
        name=tr("entry.cancel_task.name", default="Cancel a task handed to JARVIS"),
        description=tr("entry.cancel_task.description", default=(
            "Cancel one command this bridge handed to JARVIS, by its command_id: JARVIS stops waiting for its answer and the lab "
            "tasks that command started are stopped too (with their queued or running jobs). Other commands' tasks are not "
            "touched. This is not the kill switch.")),
        input_schema={"type": "object", "properties": {
            "command_id": {"type": "string", "description": "The command_id returned by delegate_task."}},
            "required": ["command_id"]},
        timeout=20,
        llm_result_fields=["command_id", "cancelled", "tasks_stopped", "message"],
    )
    @screen_text.cleaned                  # every string in the result is cleaned by default (screen_text.clean_result)
    async def cancel_task(self, command_id: str = "", **_):
        try:
            out = await JarvisClient().cancel(command_id)
        except BridgeError as exc:
            return _err(exc)
        stopped = out.get("tasks_stopped", [])
        n = len(stopped)
        if out["cancelled"]:
            message = "JARVIS cancelled that command: its answer is ignored and JARVIS is free for the next one."
        elif n:
            message = "That command had already finished or been stopped."
        else:
            message = "That command was no longer running (it had finished, failed or was already stopped)."
        if n:
            message += f" {n} lab task{'s' if n != 1 else ''} it had started {'were' if n != 1 else 'was'} stopped."
        if out.get("tasks_not_stopped"):
            message += f" {out['tasks_not_stopped']} could not be stopped (already ended or changing); check the JARVIS console."
        message += " A task that cannot be tied to this command is stopped only by Stop all in the JARVIS console."
        return Ok({"command_id": out["id"], "cancelled": out["cancelled"], "tasks_stopped": stopped, "message": message})

    @ui.action(id="jarvis_status", label=tr("actions.jarvis_status.label", default="Check JARVIS"), tone="default", refresh_context=True)
    @plugin_entry(
        id="jarvis_status",
        name=tr("entry.jarvis_status.name", default="Is JARVIS ready?"),
        description=tr("entry.jarvis_status.description", default="Check that the JARVIS console and kernel are reachable and not busy or switched off."),
        timeout=20,
        llm_result_fields=["ready", "message"],
    )
    @screen_text.cleaned                  # every string in the result is cleaned by default (screen_text.clean_result)
    async def jarvis_status(self, **_):
        try:
            s = await JarvisClient().status()
        except BridgeError as exc:
            return Ok({"ready": False, "message": exc.message})
        ready = s["kernel_reachable"] and s["kernel_authorized"] and not s["kill_switch_on"]
        if s["kill_switch_on"]:
            message = "JARVIS's kill switch is on; it will refuse tasks until the owner lifts it."
        elif not s["kernel_reachable"]:
            message = "The JARVIS console is running but cannot reach the kernel."
        elif not s["kernel_authorized"]:
            message = "The JARVIS console is not authorized by the kernel."
        elif s["busy"]:
            message = "JARVIS is ready but still busy with the previous task."
        else:
            message = "JARVIS is ready."
        return Ok({"ready": ready, "busy": s["busy"], "message": message})

    @plugin_entry(
        id="pending_approvals",
        name=tr("entry.pending_approvals.name", default="JARVIS approvals waiting"),
        description=tr("entry.pending_approvals.description", default=(
            "Read-only: how many actions JARVIS is waiting for the owner to approve, and what they are. Approving or "
            "denying is done in the JARVIS console with the owner's PIN, never from here.")),
        timeout=20,
        llm_result_fields=["count", "approvals"],
    )
    @screen_text.cleaned                  # every string in the result is cleaned by default (screen_text.clean_result)
    async def pending_approvals(self, **_):
        try:
            rows = await JarvisClient().approvals()
        except BridgeError as exc:
            return Err(SdkError(exc.message))
        return Ok({"count": len(rows), "approvals": rows})

    @plugin_entry(
        id="connector_overview",
        name=tr("entry.connector_overview.name", default="JARVIS connectors"),
        description=tr("entry.connector_overview.description", default=(
            "Read-only: the external services JARVIS is connected to and whether each is working. No endpoints or "
            "credentials are shown.")),
        timeout=20,
        llm_result_fields=["connectors"],
    )
    @screen_text.cleaned                  # every string in the result is cleaned by default (screen_text.clean_result)
    async def connector_overview(self, **_):
        try:
            rows = await JarvisClient().connectors()
        except BridgeError as exc:
            return Err(SdkError(exc.message))
        return Ok({"connectors": rows})
