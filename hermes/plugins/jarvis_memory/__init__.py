"""JARVIS memory for Hermes: durable preferences with stable ids, and the kernel's task checkpoint, in every session.

A Hermes memory-provider plugin (Hermes's own extension point, agent/memory_provider.py; modelled on the bundled
holographic provider). The JARVIS kernel is the store of record (jarvis_kernel/memory.py, tasks.py); this plugin only
talks to it through the gateway with the agent's own token, so every read and change is authorized and audited there.

- system_prompt_block(): the owner's entries, each with its id and version, plus the checkpoint of open tasks (goal,
  criteria, state, steps done, pending, decisions, next action). Hermes rebuilds the system prompt at every
  compaction, so a compacted or fresh session starts from the current record, not from a lossy summary.
- on_pre_compress(): the same checkpoint goes into the summary prompt.
- tools: jarvis_memory_list / _add / _update (id + version) / _forget (id, or one clear match): verified results.
Token: <HERMES_HOME>/jarvis-memory.token if present (a test profile gets its own agent, so its memories never mix with
the owner's), else ~/.jarvis-llm-token. Endpoint: $JARVIS_ENDPOINT or http://127.0.0.1:8790.
"""
from __future__ import annotations

import json
import logging
import os
import urllib.error
import urllib.request
from typing import Any, Dict, List

try:
    from agent.memory_provider import MemoryProvider
except ImportError:                                    # tests outside Hermes
    from abc import ABC as MemoryProvider             # type: ignore

log = logging.getLogger(__name__)
TIMEOUT = 8
GUIDE = ("Durable memory (JARVIS kernel): the owner's saved entries, each [id vN]. Save: jarvis_memory_add. Correct: "
         "jarvis_memory_update(id, text, expected_version). Delete: jarvis_memory_forget(id); check the result says forgotten.")


class JarvisMemoryProvider(MemoryProvider):
    def __init__(self, endpoint: str | None = None, token_file: str | None = None):
        self.endpoint = (endpoint or os.environ.get("JARVIS_ENDPOINT") or "http://127.0.0.1:8790").rstrip("/")
        self._token_file = token_file
        self.hermes_home = None
        self.primary = True

    # ---- plumbing ----
    @property
    def name(self) -> str:
        return "jarvis_memory"

    def _token_path(self) -> str:
        if self._token_file:
            return self._token_file
        home = self.hermes_home or os.environ.get("HERMES_HOME")     # is_available() runs before initialize()
        if not home:
            try:
                from hermes_constants import get_hermes_home
                home = str(get_hermes_home())
            except Exception:
                home = None
        if home and os.path.isfile(os.path.join(home, "jarvis-memory.token")):
            return os.path.join(home, "jarvis-memory.token")
        return os.path.expanduser("~/.jarvis-llm-token")

    def is_available(self) -> bool:
        return os.path.isfile(self._token_path())

    def unavailable_reason(self) -> str:
        return "no JARVIS kernel token (jarvis-memory.token in the profile, or ~/.jarvis-llm-token)"

    def initialize(self, session_id: str, **kwargs) -> None:
        self.hermes_home = kwargs.get("hermes_home") or self.hermes_home
        self.primary = kwargs.get("agent_context", "primary") == "primary"

    def _act(self, tool: str, op: str, target: str = "", **params) -> Dict[str, Any]:
        with open(self._token_path(), encoding="utf-8-sig") as fh:
            tok = fh.read().strip()
        req = urllib.request.Request(self.endpoint + "/v1/jarvis/actions", method="POST",
                                     data=json.dumps({"tool": tool, "op": op, "target": target, "params": params}).encode(),
                                     headers={"Content-Type": "application/json", "Authorization": "Bearer " + tok})
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
                return json.loads(r.read())
        except urllib.error.HTTPError as e:
            try:
                return json.loads(e.read() or b"{}") | {"http_status": e.code}
            except ValueError:
                return {"status": "refused", "http_status": e.code}

    # ---- what every session starts from ----
    def _record(self) -> str:
        try:
            mem = self._act("memory", "render").get("text") or "(no saved entries)"
            cp = self._act("task", "checkpoint").get("text") or "(no open tasks)"
        except (OSError, ValueError) as e:
            log.warning("jarvis_memory: kernel not reachable (%s)", type(e).__name__)
            return ""
        return f"{GUIDE}\n{mem}\n\nOpen tasks (kernel checkpoint; the kernel's record is authoritative):\n{cp}"

    def system_prompt_block(self) -> str:
        return self._record() if self.primary else ""

    def on_pre_compress(self, messages: List[Dict[str, Any]]) -> str:
        rec = self._record()
        return ("Keep this record intact in the checkpoint (it is the durable state):\n" + rec) if rec else ""

    # ---- tools ----
    def get_tool_schemas(self) -> List[Dict[str, Any]]:
        s = lambda props, req: {"type": "object", "properties": props, "required": req}
        return [
            {"name": "jarvis_memory_list", "description": "List the owner's saved preferences/facts with ids and versions (optionally matching a query).",
             "parameters": s({"query": {"type": "string"}}, [])},
            {"name": "jarvis_memory_add", "description": "Save a lasting preference or fact the owner stated. Returns its id. Never store secrets.",
             "parameters": s({"text": {"type": "string"}, "kind": {"type": "string", "enum": ["preference", "fact", "instruction"]}}, ["text"])},
            {"name": "jarvis_memory_update", "description": "Correct a saved entry: its id, the new full text and the version you saw. "
                                                            "A version conflict means it changed: list it again and retry.",
             "parameters": s({"id": {"type": "string"}, "text": {"type": "string"}, "expected_version": {"type": "integer"}}, ["id", "text", "expected_version"])},
            {"name": "jarvis_memory_forget", "description": "Delete a saved entry by its id (best). Content is accepted only if exactly one "
                                                            "entry clearly matches; otherwise candidates are returned and nothing is deleted.",
             "parameters": s({"id_or_text": {"type": "string"}, "expected_version": {"type": "integer"}}, ["id_or_text"])},
        ]

    def handle_tool_call(self, tool_name: str, args: Dict[str, Any], **kwargs) -> str:
        if tool_name == "jarvis_memory_list":
            r = self._act("memory", "list", "", query=args.get("query") or None)
        elif tool_name == "jarvis_memory_add":
            r = self._act("memory", "add", str(args.get("text", "")), kind=args.get("kind") or "preference")
        elif tool_name == "jarvis_memory_update":
            r = self._act("memory", "update", str(args.get("id", "")), text=args.get("text"), expected_version=args.get("expected_version"))
        elif tool_name == "jarvis_memory_forget":
            extra = {"expected_version": args["expected_version"]} if args.get("expected_version") is not None else {}
            r = self._act("memory", "forget", str(args.get("id_or_text", "")), **extra)
        else:
            r = {"status": "refused", "reason": f"unknown tool {tool_name}"}
        return json.dumps(r)

    def get_config_schema(self) -> List[Dict[str, Any]]:
        return []


def register(ctx) -> None:
    ctx.register_memory_provider(JarvisMemoryProvider())
