"""Tests for jarvis.bridge.face_tools: the face's tool server hands tasks to the JARVIS console and decides nothing itself."""
from __future__ import annotations

import asyncio
import json
import os
import socket
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from jarvis.bridge import face_tools as F

CONSOLE = F._load_console_client()


class FakeClient:
    """Stands in for the console client: records calls, answers like the console does."""

    def __init__(self, rows=None, fail=None):
        self.calls, self.rows, self.fail = [], rows or [], fail

    def _maybe_fail(self):
        if self.fail:
            raise CONSOLE.BridgeError(self.fail, 503)

    async def delegate(self, task, source="neko"):
        self.calls.append(("delegate", task, source))
        self._maybe_fail()
        return {"id": "c1", "state": "running"}

    async def commands(self, limit=10):
        self._maybe_fail()
        return self.rows

    async def cancel(self, cid):
        self.calls.append(("cancel", cid))
        return {"id": cid, "cancelled": True, "tasks_stopped": ["t1"]}

    async def approvals(self):
        return [{"id": "a1", "level": "RED", "what": "email send", "seconds_left": 90, "reason": "rule"}]

    async def status(self):
        self._maybe_fail()
        return {"console": True, "kernel_reachable": True, "kernel_authorized": True, "kill_switch_on": False, "busy": False}


def tools(client):
    return F.FaceTools(client_factory=lambda: client, console=CONSOLE)


def test_a_task_goes_to_the_console_as_the_face():
    c = FakeClient()
    out = asyncio.run(tools(c).do("open my calendar"))
    assert c.calls == [("delegate", "open my calendar", "face")]
    assert out["ok"] is True and out["command_id"] == "c1" and "PIN" in out["message"]


def test_a_finished_reply_stays_private_unless_the_owner_allows_it(monkeypatch):
    rows = [{"id": "c1", "source": "face", "state": "done", "reply": "Your 3pm with Dr. Rao is moved."}]
    monkeypatch.delenv("JARVIS_BRIDGE_SPEAK_REPLY", raising=False)
    out = asyncio.run(tools(FakeClient(rows)).task_result(""))
    assert "reply" not in out and "may be private" in out["message"] and "reports it finished" in out["message"]
    monkeypatch.setenv("JARVIS_BRIDGE_SPEAK_REPLY", "1")
    out = asyncio.run(tools(FakeClient(rows)).task_result("c1"))
    assert out["reply"].startswith("Your 3pm")


def test_unknown_tasks_failures_and_a_down_console_are_said_plainly():
    assert asyncio.run(tools(FakeClient([])).task_result(""))["ok"] is False
    failed = [{"id": "c2", "source": "face", "state": "failed", "error": "approval expired"}]
    assert "approval expired" in asyncio.run(tools(FakeClient(failed)).task_result("c2"))["message"]
    down = FakeClient(fail="The JARVIS console is not running on this laptop.")
    assert asyncio.run(tools(down).do("x")) == {"ok": False, "message": "The JARVIS console is not running on this laptop."}
    assert asyncio.run(tools(down).ready())["ready"] is False


def test_waiting_shows_what_needs_the_pin_and_cancel_is_one_command():
    c = FakeClient()
    w = asyncio.run(tools(c).waiting())
    assert w["count"] == 1 and "PIN" in w["message"]
    out = asyncio.run(tools(c).cancel("c1"))
    assert c.calls == [("cancel", "c1")] and out["cancelled"] is True


def test_results_are_cleaned_before_a_model_sees_them():
    seen = []
    t = F.FaceTools(client_factory=lambda: FakeClient(), console=CONSOLE, clean=lambda v: seen.append(v) or {"cleaned": True})
    assert asyncio.run(t.do("x")) == {"cleaned": True} and seen


def test_no_tool_can_approve_or_change_policy():
    server = F.build_server(tools(FakeClient()))
    names = sorted(t.name for t in asyncio.run(server.list_tools()))
    assert names == ["jarvis_cancel", "jarvis_do", "jarvis_ready", "jarvis_task_result", "jarvis_waiting"]
    assert not any(w in n for n in names for w in ("approve", "deny", "kill", "policy", "pin"))


# ---- end to end: MCP client -> face tool server (real HTTP) -> fake console (real HTTP) -----------------------------------------

def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Console(BaseHTTPRequestHandler):
    seen: list = []

    def log_message(self, *a):
        pass

    def _send(self, code, body):
        raw = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])) or b"{}")
        Console.seen.append((self.path, body, self.headers.get("X-Jarvis-Console")))
        self._send(202, {"id": "cmd-7", "state": "running"})

    def do_GET(self):
        self._send(200, {"commands": []})


@pytest.fixture
def stack(tmp_path, monkeypatch):
    run = tmp_path / "run"
    run.mkdir()
    (run / "console.token").write_text("tok-123", encoding="ascii")
    os.chmod(run / "console.token", 0o600)
    monkeypatch.setenv("JARVIS_RUN_DIR", str(run))
    console = ThreadingHTTPServer(("127.0.0.1", 0), Console)
    threading.Thread(target=console.serve_forever, daemon=True).start()
    monkeypatch.setenv("JARVIS_CONSOLE_URL", f"http://127.0.0.1:{console.server_address[1]}")
    Console.seen = []

    import uvicorn
    port = free_port()
    app = F.build_server(F.FaceTools()).streamable_http_app(host="127.0.0.1", transport_security=F.transport_security(port))
    srv = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error"))
    th = threading.Thread(target=srv.run, daemon=True)
    th.start()
    for _ in range(100):
        if srv.started:
            break
        time.sleep(0.05)
    yield port
    srv.should_exit = True
    th.join(5)
    console.shutdown()


def test_end_to_end_the_face_hands_jarvis_a_task(stack):
    from mcp.client.session import ClientSession
    from mcp.client.streamable_http import streamable_http_client

    async def go():
        async with streamable_http_client(f"http://127.0.0.1:{stack}/mcp") as (r, w):
            async with ClientSession(r, w) as s:
                await s.initialize()
                names = sorted(t.name for t in (await s.list_tools()).tools)
                res = await s.call_tool("jarvis_do", {"task": "summarise my unread mail"})
                return names, res
    names, res = asyncio.run(go())
    assert "jarvis_do" in names
    payload = json.loads(res.content[0].text)
    assert payload["command_id"] == "cmd-7"
    assert Console.seen == [("/api/owner/command", {"text": "summarise my unread mail", "source": "face"}, "tok-123")]


def test_a_web_page_cannot_call_it(stack):
    def post(headers):
        req = urllib.request.Request(f"http://127.0.0.1:{stack}/mcp", data=b'{"jsonrpc":"2.0","id":1,"method":"tools/list"}',
                                     method="POST", headers={"Content-Type": "application/json",
                                                             "Accept": "application/json, text/event-stream", **headers})
        try:
            with urllib.request.urlopen(req, timeout=5) as resp:
                return resp.status
        except urllib.error.HTTPError as e:
            return e.code
    assert post({"Origin": "https://evil.example"}) == 403
    assert post({"Host": f"evil.example:{stack}"}) in (403, 421)
    assert Console.seen == []
