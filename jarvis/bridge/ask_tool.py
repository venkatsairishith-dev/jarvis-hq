"""The MCP tools of the generic ask panel and the cleaner (D55), registered into the face's tool server by `register`.

  jarvis_ask(spec | pattern + data)   the chat model's tool: show a form, wait for the owner, return {answered, button, values} or
                                      {answered: false, reason}. `pattern` ids are in config/ui_patterns.json.
  jarvis_cleaner(action, items)       the chat model's tool for the cleaner panel (open, check, uncheck, list, clean).
  jarvis_ask_wanted / _submit / _close   the panel's own calls (listed neither to the chat model nor in its persona).

A form only collects choices. It never grants an approval: those stay in the JARVIS console with the PIN (D27).
"""
from __future__ import annotations

import asyncio
from typing import Any

from jarvis.bridge import ui_patterns
from jarvis.bridge.ask_spec import AskError

ASK_TOOLS = ("jarvis_ask", "jarvis_cleaner")
PANEL_ASK_TOOLS = ("jarvis_ask_wanted", "jarvis_ask_submit", "jarvis_ask_close")


async def ask(broker: Any, service: Any, spec: Any = None, pattern: str = "", data: Any = None) -> dict:
    """The body of jarvis_ask. Never raises: a refused spec comes back as {answered: false, reason: 'invalid', message}."""
    try:
        if pattern:
            pattern = str(pattern).strip().lower()
            if pattern == "cleanup":
                return await asyncio.to_thread(service.open, "", 25.0) if service is not None else {"answered": False, "reason": "unavailable"}
            raw = ui_patterns.build(pattern, data)
            if pattern in ui_patterns.NO_WAIT:                          # a job: shown and left open; the next call moves it forward
                cur = broker.current(pattern)
                clicked = cur.clicked if cur is not None else ""
                broker.show(raw, key=pattern)
                return {"shown": True, "answered": False, "reason": "not waiting", **({"owner_clicked": clicked} if clicked else {})}
        else:
            raw = spec
        return await broker.ask(raw)
    except AskError as exc:
        return {"answered": False, "reason": "invalid", "message": str(exc)}


def register(server: Any, broker: Any, service: Any, look: Any) -> None:
    """Adds the five tools. `look` is the read-only ToolAnnotations of the server."""
    from mcp_types import ToolAnnotations

    @server.tool(name="jarvis_ask", description=("Show the owner a form and wait for the answer. pattern (%s) + data, or a full spec. "
                                                 "Only collects choices; never for a PIN or approvals." % ui_patterns.id_line()),
                 annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=False))
    async def jarvis_ask(spec: dict | None = None, pattern: str | None = None, data: dict | None = None) -> dict:
        return await ask(broker, service, spec, pattern or "", data)

    if service is not None:
        @server.tool(name="jarvis_cleaner", description=("PC cleaner panel. action: open, check, uncheck, list, clean. items: row names in plain words "
                                                         "(temp, browser cache, recycle bin, old installers); for clean, the owner's own words. "
                                                         "Cleans only what is ticked, and only if the owner said clean."),
                     annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=True, idempotentHint=False, openWorldHint=False))
        async def jarvis_cleaner(action: str, items: str | None = None) -> dict:
            return await asyncio.to_thread(service.tool, action, items or "")

    @server.tool(name="jarvis_ask_wanted", description=("FOR THE FACE'S ASK PANEL ONLY, do not call: the form that is open, as a long poll "
                                                        "(returns when it changes)."), annotations=look)
    async def jarvis_ask_wanted(seen: int = 0, wait_s: float = 20.0) -> dict:
        return await asyncio.to_thread(broker.wanted, seen, wait_s)

    @server.tool(name="jarvis_ask_submit", description="FOR THE FACE'S ASK PANEL ONLY, do not call: the owner's click on a form.",
                 annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=False))
    async def jarvis_ask_submit(form_id: str, token: str, button: str, values: dict | None = None) -> dict:
        return await asyncio.to_thread(broker.submit, form_id, token, button, values or {})

    @server.tool(name="jarvis_ask_close", description="FOR THE FACE'S ASK PANEL ONLY, do not call: the owner closed the form.",
                 annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=True, openWorldHint=False))
    async def jarvis_ask_close(form_id: str, token: str) -> dict:
        return await asyncio.to_thread(broker.close, form_id, token)
