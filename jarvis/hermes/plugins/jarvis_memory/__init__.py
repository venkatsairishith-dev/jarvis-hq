"""JARVIS memory for Hermes: durable preferences with stable ids, and the kernel's task checkpoint, in every session.

A Hermes memory-provider plugin (Hermes's own extension point, agent/memory_provider.py; modelled on the bundled
holographic provider). The JARVIS kernel is the store of record (jarvis_kernel/memory.py, tasks.py); this plugin only
talks to it through the gateway with the agent's own token, so every read and change is authorized and audited there.

- system_prompt_block(): the owner's entries, each with its id and version, plus the checkpoint of open tasks (goal,
  criteria, state, steps done, pending, decisions, next action). Hermes rebuilds the system prompt at every
  compaction, so a compacted or fresh session starts from the current record, not from a lossy summary.
  The kernel's block is bounded (memory.render): when not every entry fits it keeps the newest, the corrections and
  lessons, marks similar entries (if they disagree, the newer one is current) and says how many it left out. The open
  tasks' goals go with the request as a query, so entries relevant to the current task are kept first (MEM-M1).
- on_pre_compress(): the same checkpoint goes into the summary prompt; the query also carries the owner's latest message.
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


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None                        # never re-send the kernel bearer token to another address


# No environment proxy (http_proxy/ALL_PROXY would receive the bearer token) and no redirects (completion H, 2026-10-19). Inline: this plugin
# is installed into Hermes and cannot import the JARVIS scripts package.
_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect())
MAX_QUERY = 600                                        # characters of task goals / latest owner message sent as the ranking query
GUIDE = ("Durable memory (JARVIS kernel): the owner's saved entries, each [id vN] (kind) {scope, added, updated} text, oldest "
         "first and newest last. Similar entries are marked: if they disagree, the newer one is current. If entries were left "
         "out, the last line says how many: jarvis_memory_list (with a query) shows them. Save: jarvis_memory_add. Correct: "
         "jarvis_memory_update(id, text, expected_version). Delete: jarvis_memory_forget(id); check the result says forgotten.")


def _text_of(content) -> str:
    """A message's text: a plain string, or the text parts of a list of content parts."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return " ".join(str(p.get("text", "")) for p in content if isinstance(p, dict) and p.get("type") in (None, "text"))
    return ""


def _query(*parts) -> str:
    """The ranking query: the given texts joined, whitespace collapsed, at most MAX_QUERY characters."""
    return " ".join(" ".join(str(p) for p in parts if p).split())[:MAX_QUERY]


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
            with _OPENER.open(req, timeout=TIMEOUT) as r:
                return json.loads(r.read())
        except urllib.error.HTTPError as e:
            try:
                return json.loads(e.read() or b"{}") | {"http_status": e.code}
            except ValueError:
                return {"status": "refused", "http_status": e.code}

    # ---- what every session starts from ----
    def _record(self, latest: str = "") -> str:
        """The memory block and the task checkpoint. The checkpoint is read first: the open tasks' goals (and, at a
        compaction, the owner's latest message) are the query the kernel ranks entries by; with neither, it uses recency."""
        try:
            cpr = self._act("task", "checkpoint")
            cp = cpr.get("text") or "(no open tasks)"
            goals = [t.get("goal") for t in cpr.get("tasks") or [] if isinstance(t, dict)
                     and t.get("state") not in ("completed", "failed", "cancelled")]
            q = _query(latest, *goals)
            mem = self._act("memory", "render", **({"query": q} if q else {})).get("text") or "(no saved entries)"
        except (OSError, ValueError) as e:
            log.warning("jarvis_memory: kernel not reachable (%s)", type(e).__name__)
            return ""
        return f"{GUIDE}\n{mem}\n\nOpen tasks (kernel checkpoint; the kernel's record is authoritative):\n{cp}"

    def system_prompt_block(self) -> str:
        return self._record() if self.primary else ""

    def on_pre_compress(self, messages: List[Dict[str, Any]]) -> str:
        latest = next((_text_of(m.get("content")) for m in reversed(messages or [])
                       if isinstance(m, dict) and m.get("role") == "user"), "")
        rec = self._record(latest)
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
