"""How a goal reaches JARVIS and what comes back (D59). Three drivers, one shape: `run(goal_text, timeout_s) -> Observation`.

  FaceDriver     the real thing: types the goal into the face chat (drive/reliability_drive.mjs through the face's debug port) and reads what
                 the owner would see. Needed for everything about the FINAL MESSAGE and about the chat model's behaviour. Slow (a goal takes as long
                 as the owner would wait).
  McpDriver      the face's tool server on 127.0.0.1:48970 (the same one the face uses): hands the goal to jarvis_do, follows jarvis_task_result,
                 watches jarvis_waiting for a PIN card. No chat model in the loop: it measures the PC side (planning, executing, verifying) fast.
  ScriptedDriver for tests: a function returns the Observation.

After every goal the runner cancels what is still running and NEVER approves anything: approving is the owner's PIN. In an attended run
(`--attended`) the driver waits for the owner to approve at the console and records `approved`.
Standard library only (loopback_http for the sockets).
"""
from __future__ import annotations

import json
import os
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Callable

from jarvis.plugins._shared import ask_form, loopback_http

from .records import Observation

HERE = Path(__file__).resolve().parent
DRIVE_JS = HERE / "drive" / "reliability_drive.mjs"
MCP_URL = "http://127.0.0.1:48970/mcp"
POLL_S = 2.0


class DriverError(RuntimeError):
    pass


# ------------------------------------------------------------------------------------------------------------------ MCP client
class McpClient:
    """The least of an MCP streamable-HTTP client that the face's tool server needs: initialize, tools/call. JSON or SSE answers."""

    def __init__(self, url: str = MCP_URL, timeout: float = 30.0, opener: Callable[..., Any] | None = None):
        self.url, self.timeout, self._open = url, timeout, opener or loopback_http.urlopen
        self.session = ""
        self._id = 0

    def _post(self, payload: dict, expect_reply: bool = True) -> dict | None:
        headers = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream"}
        if self.session:
            headers["Mcp-Session-Id"] = self.session
        req = urllib.request.Request(self.url, data=json.dumps(payload).encode(), headers=headers, method="POST")
        try:
            with self._open(req, timeout=self.timeout) as r:
                sid = r.headers.get("Mcp-Session-Id") or r.headers.get("mcp-session-id")
                if sid:
                    self.session = sid
                ctype = (r.headers.get("Content-Type") or "").lower()
                body = r.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            raise DriverError(f"the tool server answered HTTP {e.code}") from None
        except (urllib.error.URLError, OSError, ValueError) as e:
            raise DriverError(f"the tool server is not reachable ({type(e).__name__})") from None
        if not expect_reply or not body.strip():
            return None
        if "event-stream" in ctype:
            for line in body.splitlines():
                if line.startswith("data:"):
                    try:
                        msg = json.loads(line[5:].strip())
                    except ValueError:
                        continue
                    if msg.get("id") == payload.get("id"):
                        return msg
            raise DriverError("the tool server's stream held no answer")
        try:
            return json.loads(body)
        except ValueError:
            raise DriverError("the tool server answered something that is not JSON") from None

    def initialize(self) -> None:
        self._id += 1
        r = self._post({"jsonrpc": "2.0", "id": self._id, "method": "initialize", "params": {
            "protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "jarvis-reliability", "version": "1"}}})
        if not r or "error" in r:
            raise DriverError("the tool server refused to initialize")
        self._post({"jsonrpc": "2.0", "method": "notifications/initialized"}, expect_reply=False)

    def call(self, name: str, arguments: dict | None = None) -> dict:
        if not self.session:
            self.initialize()
        self._id += 1
        r = self._post({"jsonrpc": "2.0", "id": self._id, "method": "tools/call", "params": {"name": name, "arguments": arguments or {}}})
        if not r:
            raise DriverError("no answer to the tool call")
        if "error" in r:
            raise DriverError(f"tool {name}: {str((r['error'] or {}).get('message', ''))[:120]}")
        res = r.get("result") or {}
        if isinstance(res.get("structuredContent"), dict):
            return res["structuredContent"]
        for part in res.get("content") or []:
            if part.get("type") == "text":
                try:
                    v = json.loads(part.get("text", ""))
                    if isinstance(v, dict):
                        return v
                except ValueError:
                    return {"text": part.get("text", "")}
        return {}


# ------------------------------------------------------------------------------------------------------------------ drivers
class ScriptedDriver:
    """For tests and dry runs: `fn(goal_text) -> Observation | dict`."""
    name = "scripted"

    def __init__(self, fn: Callable[[str], Any]):
        self.fn = fn
        self.calls: list[str] = []

    def run(self, goal_text: str, timeout_s: float = 180.0, attended: bool = False) -> Observation:
        self.calls.append(goal_text)
        out = self.fn(goal_text)
        return out if isinstance(out, Observation) else Observation(**out)

    def cancel_all(self) -> None:
        return None


class McpDriver:
    name = "mcp"

    def __init__(self, client: McpClient | None = None, tool: str = "jarvis_do", clock: Callable[[], float] = time.monotonic,
                 sleep: Callable[[float], None] = time.sleep, approval_wait_s: float = 120.0):
        self.c = client or McpClient()
        self.tool, self.clock, self.sleep, self.approval_wait_s = tool, clock, sleep, approval_wait_s

    def _waiting(self) -> list[str]:
        try:
            rows = self.c.call("jarvis_waiting").get("approvals") or []
        except DriverError:
            return []
        return [str(r.get("id")) for r in rows if isinstance(r, dict) and r.get("id")]

    def run(self, goal_text: str, timeout_s: float = 180.0, attended: bool = False) -> Observation:
        t0 = self.clock()
        before = set(self._waiting())
        try:
            sent = self.c.call(self.tool, {"task": goal_text})
        except DriverError as e:
            return Observation(status="harness_error", harness_error=str(e), seconds=round(self.clock() - t0, 2))
        cid = str(sent.get("command_id") or "")
        if not cid:
            return Observation(status="failed", errors=[str(sent.get("message") or "no command id")[:160]], seconds=round(self.clock() - t0, 2),
                               tool_calls=[self.tool], requests=1)
        obs = Observation(tool_calls=[self.tool], command_id=cid, requests=1)
        deadline = t0 + timeout_s
        owner_deadline: float | None = None
        seen_card = False
        res: dict = {}
        while True:
            self.sleep(POLL_S)
            try:
                res = self.c.call("jarvis_task_result", {"command_id": cid})
            except DriverError as e:
                obs.errors.append(str(e)[:120])
                res = {}
            state = str(res.get("state") or "")
            waiting = [a for a in self._waiting() if a not in before]
            if waiting:
                seen_card = True
                obs.approval_ids = waiting
                if not attended:
                    break                                          # a PIN card is the end of an unattended run
                owner_deadline = owner_deadline or self.clock() + self.approval_wait_s
                if self.clock() > owner_deadline:
                    break                                          # the owner did not approve in time: the card is still waiting
                continue
            if state in ("done", "failed"):
                break
            if self.clock() > deadline:
                obs.status = "timeout"
                break
        still_waiting = bool(obs.approval_ids) and bool([a for a in self._waiting() if a in obs.approval_ids])
        obs.approval_pending = bool(waiting) or still_waiting
        if seen_card and attended and not obs.approval_pending and state == "done":
            obs.approved = True                                    # the card went away and the task finished: the owner approved it
        obs.final_text = str(res.get("reply") or res.get("summary") or res.get("message") or "")
        obs.ask_form = ask_form.find_form(res)
        if res.get("state") == "failed":
            obs.status = "failed"
            obs.errors.append(str(res.get("message") or "")[:160])
        obs.seconds = round(self.clock() - t0, 2)
        obs.raw = {k: res.get(k) for k in ("state", "message", "summary") if k in res}
        return obs

    def cancel_all(self) -> None:
        """Cancel what is still running after a goal; denies nothing and approves nothing."""
        try:
            rows = self.c.call("jarvis_task_result", {"command_id": ""})
            cid = rows.get("command_id")
            if cid and rows.get("state") == "running":
                self.c.call("jarvis_cancel", {"command_id": cid})
        except DriverError:
            pass


class FaceDriver:
    name = "face"

    def __init__(self, node: str = "node", script: Path | str = DRIVE_JS, client: McpClient | None = None, runner: Any = None,
                 reset_cmd: list[str] | None = None, cwd: str | None = None):
        self.node, self.script, self.c, self._run = node, str(script), client or McpClient(), runner or subprocess.run
        self.reset_cmd, self.cwd = reset_cmd, cwd or os.environ.get("JARVIS_FACE_DIR")

    def _waiting(self) -> list[str]:
        try:
            rows = self.c.call("jarvis_waiting").get("approvals") or []
        except DriverError:
            return []
        return [str(r.get("id")) for r in rows if isinstance(r, dict) and r.get("id")]

    def run(self, goal_text: str, timeout_s: float = 180.0, attended: bool = False) -> Observation:
        if self.reset_cmd:
            try:
                self._run(self.reset_cmd, capture_output=True, text=True, timeout=180)
            except (OSError, subprocess.TimeoutExpired) as e:
                return Observation(status="harness_error", harness_error=f"the face reset command failed ({type(e).__name__})")
        before = set(self._waiting())
        t0 = time.monotonic()
        try:
            proc = self._run([self.node, self.script, goal_text, str(int(timeout_s))], capture_output=True, text=True, timeout=timeout_s + 120,
                             encoding="utf-8", errors="replace")
        except (OSError, subprocess.TimeoutExpired) as e:
            return Observation(status="harness_error", harness_error=f"the face driver did not finish ({type(e).__name__})")
        lines = [ln for ln in (proc.stdout or "").splitlines() if ln.strip().startswith("{")]
        if not lines:
            return Observation(status="harness_error", harness_error=("the face driver printed nothing: " + (proc.stderr or "")[:160]))
        try:
            row = json.loads(lines[-1])
        except ValueError:
            return Observation(status="harness_error", harness_error="the face driver printed something that is not JSON")
        return observation_from_face_row(row, [a for a in self._waiting() if a not in before], time.monotonic() - t0)

    def cancel_all(self) -> None:
        McpDriver(self.c).cancel_all()


def observation_from_face_row(row: dict, new_approvals: list[str], wall_s: float) -> Observation:
    """Turn the face driver's JSON line into an Observation."""
    if row.get("harness_error"):
        return Observation(status="harness_error", harness_error=str(row["harness_error"])[:200])
    form = row.get("ask_form") if ask_form.is_form(row.get("ask_form")) else ask_form.find_form(row.get("chat_tail"))
    text = str(row.get("final_text") or "")
    errors = [str(e) for e in row.get("errors_on_screen") or []] + [f"HTTP {s}" for s in row.get("statuses") or [] if s != 200]
    prose = text.rstrip().endswith("?") and form is None
    status = "timeout" if row.get("timed_out") else "done"
    return Observation(final_text=text, tool_calls=[str(c) for c in row.get("calls") or []], ask_form=form, asked_in_prose=prose,
                       approval_pending=bool(new_approvals), approval_ids=new_approvals, status=status, seconds=float(row.get("seconds") or wall_s),
                       requests=int(row.get("requests") or 0), errors=errors, raw={"store_found": row.get("store_found"), "messages": row.get("messages")})
