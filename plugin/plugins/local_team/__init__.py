"""Local Team: small specialist models on this PC (code, maths, general; the generalist also routes) that share a task, with Dahl's MiniMax as
a reasoning adviser reached only through the privacy gateway (it sees [PASSWORD_1], never the password).

The logic and its safety rule (no adviser call unless the agent model points at the gateway) are in core.py.
"""

from __future__ import annotations

import asyncio
import json
import urllib.request

from plugin.sdk.plugin import Err, NekoPluginBase, Ok, SdkError, lifecycle, neko_plugin, plugin_entry, tr

from . import core
from .core import TeamError


def _gateway_preview(text: str) -> dict:
    req = urllib.request.Request("http://127.0.0.1:48950/redact", data=json.dumps({"text": text}).encode("utf-8"),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode("utf-8"))


def _adviser(task: str) -> dict:
    from utils.config_manager import get_config_manager

    cfg = get_config_manager().get_model_api_config("agent")
    return core.advise(task, str(cfg.get("base_url") or "").strip(), str(cfg.get("model") or "").strip(),
                       str(cfg.get("api_key") or "").strip(), gateway_preview=_gateway_preview)


@neko_plugin
class LocalTeamPlugin(NekoPluginBase):
    @lifecycle(id="startup")
    async def startup(self, **_):
        return Ok({"status": "ready"})

    @lifecycle(id="shutdown")
    async def shutdown(self, **_):
        return Ok({"status": "stopped"})

    @plugin_entry(
        id="ask_local",
        name=tr("entry.ask_local.name", default="Ask a local model"),
        description=tr("entry.ask_local.description", default=(
            "Answer with a small model running on this PC; the text never leaves it, so use this for anything private "
            "(passwords, personal details, files). Specialists: code, math, general; auto picks one. Fast for short jobs.")),
        input_schema={"type": "object", "properties": {
            "task": {"type": "string", "description": "The full request, with any context it needs."},
            "specialist": {"type": "string", "enum": ["auto", "code", "math", "general"], "default": "auto"}},
            "required": ["task"]},
        timeout=320,
        llm_result_fields=["specialist", "answer"],
    )
    async def ask_local(self, task: str = "", specialist: str = "auto", **_):
        try:
            return Ok(await asyncio.to_thread(core.ask_local, task, specialist))
        except TeamError as exc:
            return Err(SdkError(str(exc)))

    @plugin_entry(
        id="solve_with_team",
        name=tr("entry.solve_with_team.name", default="Solve with the local team"),
        description=tr("entry.solve_with_team.description", default=(
            "Harder tasks: a public reasoning model (MiniMax) plans with private values hidden as placeholders, then a "
            "local specialist (code, math or general) writes the final answer on this PC with the real values. A fast "
            "decision model skips the public model when the task is simple (use_adviser=auto).")),
        input_schema={"type": "object", "properties": {
            "task": {"type": "string", "description": "The full request, with any context it needs."},
            "use_adviser": {"type": "string", "enum": ["auto", "always", "never"], "default": "auto"}},
            "required": ["task"]},
        timeout=600,
        llm_result_fields=["specialist", "answer", "adviser_used"],
    )
    async def solve_with_team(self, task: str = "", use_adviser: str = "auto", **_):
        try:
            return Ok(await asyncio.to_thread(core.solve, task, adviser=_adviser, use_adviser=use_adviser))
        except TeamError as exc:
            return Err(SdkError(str(exc)))

    @plugin_entry(
        id="team_status",
        name=tr("entry.team_status.name", default="Local team status"),
        description=tr("entry.team_status.description", default="Which local models are available or loaded, and whether the privacy gateway is up."),
        timeout=20,
        llm_result_fields=["local_models", "loaded", "privacy_gateway"],
    )
    async def team_status(self, **_):
        return Ok(await asyncio.to_thread(core.status))
