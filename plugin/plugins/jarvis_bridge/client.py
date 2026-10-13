"""Small HTTP client for the JARVIS console's owner plane (loopback only).

The console holds the owner's credentials; this plugin never sees one. It only submits a command and reads the
command list back, exactly like the console's own Command box does. The JARVIS kernel stays the sole authority:
approvals, risk rules, lane separation and audit all happen there.
"""

from __future__ import annotations

import os
from typing import Any
from urllib.parse import urlsplit

import httpx

DEFAULT_URL = "http://127.0.0.1:8799"
LOOPBACK = ("127.0.0.1", "localhost", "::1")
MAX_TASK_CHARS = 2000          # the kernel refuses longer commands; fail early with a clear message
SOURCE = "neko"                # shows as the origin of the command in the JARVIS console


class BridgeError(Exception):
    def __init__(self, message: str, status: int = 0):
        super().__init__(message)
        self.message = message
        self.status = status


def resolve_base_url(value: str | None = None) -> str:
    """The console URL from `JARVIS_CONSOLE_URL`, which must be plain http on a loopback name."""
    raw = (value or os.environ.get("JARVIS_CONSOLE_URL") or DEFAULT_URL).strip().rstrip("/")
    parts = urlsplit(raw)
    if parts.scheme != "http" or parts.hostname not in LOOPBACK or parts.username or parts.password or parts.path not in ("", "/"):
        raise BridgeError("JARVIS_CONSOLE_URL must be plain http on a loopback address, for example http://127.0.0.1:8799")
    return raw


def speak_full_reply() -> bool:
    """Whether JARVIS's full reply may be handed back to N.E.K.O (and so to the model provider chosen in N.E.K.O).

    Off by default: the reply can contain the owner's private data, which belongs to JARVIS's private lane.
    """
    return os.environ.get("JARVIS_BRIDGE_SPEAK_REPLY", "").strip().lower() in ("1", "true", "yes")


class JarvisClient:
    def __init__(self, base_url: str | None = None, timeout: float = 10.0):
        self.base = resolve_base_url(base_url)
        self.timeout = timeout

    async def _request(self, method: str, path: str, body: dict[str, Any] | None = None) -> Any:
        headers = {"X-Jarvis-Console": "1", "Accept": "application/json"}
        try:
            async with httpx.AsyncClient(timeout=self.timeout, trust_env=False, follow_redirects=False) as client:
                resp = await client.request(method, self.base + path, json=body, headers=headers)
        except httpx.ConnectError:
            raise BridgeError("The JARVIS console is not running on this laptop. Start it with start-console.ps1.", 503)
        except httpx.TimeoutException:
            raise BridgeError("The JARVIS console did not answer in time.", 504)
        except httpx.HTTPError as exc:
            raise BridgeError(f"Could not reach the JARVIS console ({type(exc).__name__}).", 502)
        try:
            data = resp.json()
        except ValueError:
            data = {}
        if resp.status_code >= 400:
            message = str(data.get("error") or f"JARVIS answered HTTP {resp.status_code}") if isinstance(data, dict) else f"JARVIS answered HTTP {resp.status_code}"
            raise BridgeError(message[:400], resp.status_code)
        return data

    async def delegate(self, task: str) -> dict[str, Any]:
        text = str(task or "").strip()
        if not text:
            raise BridgeError("There is no task to hand over.", 400)
        if len(text) > MAX_TASK_CHARS:
            raise BridgeError(f"The task is too long ({len(text)} characters; at most {MAX_TASK_CHARS}). Say it in fewer words.", 413)
        data = await self._request("POST", "/api/owner/command", {"text": text, "source": SOURCE})
        if not isinstance(data, dict) or not data.get("id"):
            raise BridgeError("JARVIS accepted the task but returned no id.", 502)
        return {"id": str(data["id"]), "state": str(data.get("state", "running"))}

    async def commands(self, limit: int = 10) -> list[dict[str, Any]]:
        data = await self._request("GET", f"/api/owner/commands?limit={max(1, min(int(limit), 50))}")
        rows = data.get("commands") if isinstance(data, dict) else None
        return [r for r in rows if isinstance(r, dict)] if isinstance(rows, list) else []

    async def status(self) -> dict[str, Any]:
        health = await self._request("GET", "/api/health")
        kernel = health.get("kernel", {}) if isinstance(health, dict) else {}
        busy = None
        try:
            state = await self._request("GET", "/api/owner/state")
            busy = bool(state.get("busy")) if isinstance(state, dict) else None
        except BridgeError:
            pass
        return {
            "console": True,
            "kernel_reachable": bool(kernel.get("reachable")),
            "kernel_authorized": bool(kernel.get("authorized")),
            "kill_switch_on": bool(kernel.get("killed")),
            "busy": busy,
        }
