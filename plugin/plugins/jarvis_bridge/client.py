"""Small HTTP client for the JARVIS console's owner plane (loopback only).

The console holds the owner's credentials; this plugin never sees one. It only submits a command and reads the
command list back, exactly like the console's own Command box does. The JARVIS kernel stays the sole authority:
approvals, risk rules, lane separation and audit all happen there.
"""

from __future__ import annotations

import os
import re
from typing import Any
from urllib.parse import urlsplit

import httpx

DEFAULT_URL = "http://127.0.0.1:8799"
LOOPBACK = ("127.0.0.1", "localhost", "::1")
MAX_TASK_CHARS = 2000          # the kernel refuses longer commands; fail early with a clear message
SOURCE = "neko"                # shows as the origin of the command in the JARVIS console


_META = re.compile(r'<meta name="jarvis-console-secret" content="([^"]+)"')
_SECRETS: dict[str, str] = {}      # console base URL -> the secret of its current launch


def _read_token_file() -> str:
    """console.token from the per-user run folder (same rule as jarvis/scripts/control_auth.py); "" when absent or not private."""
    from pathlib import Path
    override = os.environ.get("JARVIS_RUN_DIR")
    if override:
        folder = Path(override)
    elif os.name == "nt":
        folder = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local") / "jarvis" / "run"
    else:
        xdg = os.environ.get("XDG_RUNTIME_DIR")
        folder = Path(xdg) / "jarvis" if xdg else Path.home() / ".jarvis" / "run"
    try:
        f = folder / "console.token"
        st = f.stat()
        if os.name != "nt" and (st.st_mode & 0o077 or st.st_uid != os.getuid()):
            return ""
        return f.read_text(encoding="ascii").strip()
    except (OSError, ValueError):
        return ""


def _says_reload(resp: httpx.Response) -> bool:
    try:
        data = resp.json()
    except ValueError:
        return False
    return isinstance(data, dict) and data.get("reload") is True


def _says_login(resp: httpx.Response) -> bool:
    try:
        data = resp.json()
    except ValueError:
        return False
    return isinstance(data, dict) and data.get("login") is True


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

    async def _console_secret(self, client: httpx.AsyncClient, refresh: bool = False) -> str:
        """The console's MASTER token (re-review M3, 2026-10-19). The console no longer puts any credential in `GET /`: it writes
        `console.token`, a 0600 file in the per-user run folder, and refuses every request (reads included) without it. A file that
        other users could read, or that belongs to someone else, is not used. A console that still serves its secret in its page
        (a test server, an older console) is read the old way. A program of the same OS user can read the file: this blocks other
        users, other sites and programs that cannot read it, not malware running as the owner (the PIN is the boundary)."""
        if refresh or self.base not in _SECRETS:
            token = _read_token_file()
            if not token:
                resp = await client.get(self.base + "/", headers={"Accept": "text/html"})
                m = _META.search(resp.text) if resp.status_code == 200 else None
                if not m:
                    raise BridgeError("The JARVIS console token is not available: is the console running (it writes console.token), "
                                      "and is this the JARVIS console's address?", 502)
                token = m.group(1)
            _SECRETS[self.base] = token
        return _SECRETS[self.base]

    async def _request(self, method: str, path: str, body: dict[str, Any] | None = None) -> Any:
        try:
            async with httpx.AsyncClient(timeout=self.timeout, trust_env=False, follow_redirects=False) as client:
                headers = {"Accept": "application/json"}
                write = method.upper() in ("POST", "DELETE")
                try:
                    headers["X-Jarvis-Console"] = await self._console_secret(client)    # every request: private reads need it too
                except BridgeError:
                    if write:
                        raise
                    # a read without a token still goes out (an older console allows it); the new console answers 401 and says so
                resp = await client.request(method, self.base + path, json=body, headers=headers)
                if ((write and resp.status_code == 403 and _says_reload(resp))
                        or (resp.status_code == 401 and _says_login(resp))):             # the console restarted since the token was read
                    headers["X-Jarvis-Console"] = await self._console_secret(client, refresh=True)
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

    async def cancel(self, command_id: str) -> dict[str, Any]:
        """Cancel ONE command this bridge handed over, and stop what it started: the kernel's command/cancel route with
        `stop_tasks` (the console's plain "Stop waiting" does not stop tasks; this explicit option does).

        The kernel stops waiting for the agent's answer, frees the command gate, and cancels the lab tasks that were created
        while this command was the running one, together with their queued or running jobs. Nothing else is touched: other
        commands' tasks, and any task the kernel could not tie to this command, are stopped with Stop all in the console.
        Never the kill switch.
        """
        cid = str(command_id or "").strip()
        if not cid or len(cid) > 40 or not all(c.isalnum() or c in "-_" for c in cid):
            raise BridgeError("A command id from delegate_task is needed to cancel a JARVIS command.", 400)
        data = await self._request("POST", "/api/owner/command/cancel", {"id": cid, "stop_tasks": True})
        data = data if isinstance(data, dict) else {}
        out: dict[str, Any] = {"id": cid, "cancelled": bool(data.get("cancelled"))}
        if "tasks_stopped" in data:         # an older console ignores stop_tasks and says nothing about tasks: nothing is claimed
            out["tasks_stopped"] = [str(t)[:20] for t in (data.get("tasks_stopped") or []) if isinstance(t, (str, int))][:20]
            out["jobs_cancelled"] = len(data.get("jobs_cancelled") or [])
            out["tasks_not_stopped"] = len(data.get("tasks_not_stopped") or [])
        return out

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

    async def approvals(self) -> list[dict[str, Any]]:
        """Approvals waiting for the owner, read only and without the action's parameters (they can hold private text).

        Approving or denying happens in JARVIS, with the owner's PIN. This only lets N.E.K.O show that something is waiting.
        """
        state = await self._request("GET", "/api/owner/state")
        rows = state.get("approvals") if isinstance(state, dict) else None
        out: list[dict[str, Any]] = []
        for a in rows if isinstance(rows, list) else []:
            if not isinstance(a, dict):
                continue
            act = a.get("action") if isinstance(a.get("action"), dict) else {}
            what = " ".join(str(x) for x in (act.get("tool"), act.get("op"), act.get("target")) if x)
            out.append({"id": str(a.get("id") or "")[:40], "level": str(a.get("level") or "")[:20],
                        "what": " ".join(what.split())[:140], "seconds_left": int(a.get("seconds_left") or 0),
                        "reason": " ".join(str(a.get("reason") or "").split())[:140]})
        return out[:10]

    async def connectors(self) -> list[dict[str, Any]]:
        """Connector names and states only: no endpoints, no secret references, no tool arguments."""
        data = await self._request("GET", "/api/owner/connectors")
        rows = data.get("connectors") if isinstance(data, dict) else None
        out: list[dict[str, Any]] = []
        for c in rows if isinstance(rows, list) else []:
            if not isinstance(c, dict):
                continue
            out.append({"name": str(c.get("name") or "")[:60], "label": str(c.get("label") or c.get("name") or "")[:80],
                        "status": str(c.get("status") or "")[:30], "enabled": bool(c.get("enabled")),
                        "transport": str(c.get("transport") or "")[:20], "lanes": [str(x)[:12] for x in (c.get("lanes") or [])][:3],
                        "tools_enabled": int(c.get("tools_enabled") or 0)})
        return out[:30]
