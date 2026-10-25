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

    async def delegate(self, task, source="jarvis"):
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
    assert names == ["jarvis_cancel", "jarvis_do", "jarvis_ready", "jarvis_status", "jarvis_task_result", "jarvis_waiting"]
    assert not any(w in n for n in names for w in ("approve", "deny", "kill", "policy", "pin"))


def test_jarvis_spoken_hands_out_one_cleaned_notice_and_is_only_there_when_the_heartbeat_is():
    class Spoken:
        def __init__(self):
            self.rows = [{"urgency": "normal", "text": "1 task is waiting for your approval in the console."}]

        def take(self, n):
            out, self.rows = self.rows[:n], self.rows[n:]
            return out
    sp = Spoken()
    server = F.build_server(tools(FakeClient()), spoken=sp)
    assert "jarvis_spoken" in {t.name for t in asyncio.run(server.list_tools())}
    assert "jarvis_spoken" not in {t.name for t in asyncio.run(F.build_server(tools(FakeClient())).list_tools())}
    first = asyncio.run(server.call_tool("jarvis_spoken", {}))
    assert "waiting for your approval" in str(first) and "1" in str(first)
    assert '"count": 0' in str(asyncio.run(server.call_tool("jarvis_spoken", {}))) or "'count': 0" in str(asyncio.run(server.call_tool("jarvis_spoken", {})))


def _ops_file(tmp_path, ops, busy=True, at=1000.0):
    p = tmp_path / "pc_operations.json"
    p.write_text(json.dumps({"at": at, "pid": 1, "busy": busy, "operations": ops, "approvals_pending": 0, "alerts": []}), encoding="utf-8")
    return p


def test_pc_operations_are_read_as_content_free_facts(tmp_path):
    ops = [{"id": "single:open notepad", "kind": "single:open notepad", "state": "running", "age_s": 4.2},
           {"id": "task-ab12", "kind": "task", "state": "running", "age_s": 71.9},
           {"id": "check_pc-1", "kind": "check_pc", "state": "running", "age_s": 300}]
    got = F.read_pc_operations(_ops_file(tmp_path, ops), now=1005.0)
    assert got == {"pc_busy": True, "pc_kind": "task", "pc_age_s": 71, "pc_stopping": False, "pc_ops": 3}
    assert "notepad" not in json.dumps(got), "the id of a single action can hold the task text and is never passed on"
    only_action = F.read_pc_operations(_ops_file(tmp_path, ops[:1]), now=1005.0)
    assert only_action["pc_kind"] == "action"
    stopping = F.read_pc_operations(_ops_file(tmp_path, [{**ops[1], "state": "stopping"}]), now=1005.0)
    assert stopping["pc_busy"] is True and stopping["pc_stopping"] is True


def test_pc_operations_missing_stale_idle_or_broken_all_mean_not_busy(tmp_path):
    idle = {"pc_busy": False, "pc_kind": "none", "pc_age_s": 0, "pc_stopping": False, "pc_ops": 0}
    busy_ops = [{"id": "t", "kind": "task", "state": "running", "age_s": 9}]
    assert F.read_pc_operations(tmp_path / "nope.json", now=1.0) == idle
    assert F.read_pc_operations(_ops_file(tmp_path, busy_ops), now=1000.0 + F.PC_OPS_STALE_S + 1) == idle, "the plugin stopped refreshing it"
    assert F.read_pc_operations(_ops_file(tmp_path, [], busy=False), now=1001.0) == idle
    assert F.read_pc_operations(_ops_file(tmp_path, [{"id": "t", "kind": "task", "state": "finished", "age_s": 9}]), now=1001.0) == idle
    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    assert F.read_pc_operations(bad, now=1.0) == idle
    bad.write_text('["a list"]', encoding="utf-8")
    assert F.read_pc_operations(bad, now=1.0) == idle


def test_jarvis_status_is_content_free_and_degrades_to_down(tmp_path):
    import time as _t
    p = _ops_file(tmp_path, [{"id": "single:send mail to bob@example.com", "kind": "single:send mail to bob@example.com", "state": "running", "age_s": 12}], at=_t.time())
    c = FakeClient()
    t = F.FaceTools(client_factory=lambda: c, console=CONSOLE, pc_ops_path=p, unread=lambda: 3)
    up = asyncio.run(t.status())
    assert up == {"ok": True, "console_up": True, "kernel_up": True, "kill_switch_on": False, "console_busy": False, "approvals_waiting": 1,
                  "messages_unread": 3, "pc_busy": True, "pc_kind": "action", "pc_age_s": 12, "pc_stopping": False, "pc_ops": 1}
    assert "bob" not in json.dumps(up) and "a1" not in json.dumps(up) and "email send" not in json.dumps(up), "no id, title or text"
    down = asyncio.run(F.FaceTools(client_factory=lambda: FakeClient(fail="console down"), console=CONSOLE, pc_ops_path=tmp_path / "none.json").status())
    assert down["ok"] is False and down["console_up"] is False and down["kernel_up"] is False and down["pc_busy"] is False
    assert "console down" not in json.dumps(down)

    class Killed(FakeClient):
        async def status(self):
            return {"console": True, "kernel_reachable": True, "kernel_authorized": True, "kill_switch_on": True, "busy": True}
    k = asyncio.run(F.FaceTools(client_factory=lambda: Killed(), console=CONSOLE, pc_ops_path=tmp_path / "none.json").status())
    assert k["kill_switch_on"] is True and k["kernel_up"] is True and k["console_busy"] is True


def test_jarvis_status_is_a_registered_read_only_tool(tmp_path):
    t = tools(FakeClient())
    t.pc_ops_path = tmp_path / "none.json"
    server = F.build_server(t)
    listed = {x.name: x for x in asyncio.run(server.list_tools())}
    assert "jarvis_status" in listed and listed["jarvis_status"].annotations.read_only_hint is True
    out = str(asyncio.run(server.call_tool("jarvis_status", {})))
    assert "kernel_up" in out and "messages_unread" in out


def test_memory_tools_exist_only_with_a_memory_and_pass_turns_and_days_through():
    class Mem:
        def __init__(self):
            self.seen = []

        def recall(self, days):
            self.seen.append(("recall", days))
            return "Remembered from earlier conversations: 2026-10-23: Exam on Friday."

        def remember(self, turns):
            self.seen.append(("remember", len(turns)))
            return {"saved": 2}
    mem = Mem()
    server = F.build_server(tools(FakeClient()), memory=mem)
    names = {t.name for t in asyncio.run(server.list_tools())}
    assert {"jarvis_recall", "jarvis_remember_chat"} <= names
    assert not {"jarvis_recall", "jarvis_remember_chat"} & {t.name for t in asyncio.run(F.build_server(tools(FakeClient())).list_tools())}
    assert "Exam on Friday" in str(asyncio.run(server.call_tool("jarvis_recall", {"days": 2})))
    assert "saved" in str(asyncio.run(server.call_tool("jarvis_remember_chat", {"turns": [{"role": "user", "text": "hi"}]})))
    assert mem.seen == [("recall", 2), ("remember", 1)]


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


# ---- end to end with the plugin host: the plugins' entries are tools, and what a plugin says on its own arrives once -------------

DEMO = '''
from jarvis.pluginapi import Ok, PluginBase, jarvis_plugin, plugin_entry


@jarvis_plugin
class Demo(PluginBase):
    @plugin_entry(id="echo", name="Echo", description="Gives the text back and tells the owner it did.",
                  input_schema={"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]})
    async def echo(self, text: str, **_):
        self.push_message(source="demo", parts=[{"type": "text", "text": "echoed " + text}])
        return Ok({"text": text, "path": "C:\\\\Users\\\\someone\\\\secret.txt"})
'''


@pytest.fixture
def plugin_stack(tmp_path, monkeypatch):
    import sys
    import textwrap

    import uvicorn
    from jarvis.pluginapi.host import PluginHost

    root = tmp_path / "ft_plugins"
    (root / "demo").mkdir(parents=True)
    (root / "__init__.py").write_text("")
    (root / "demo" / "__init__.py").write_text(textwrap.dedent(DEMO), encoding="utf-8")
    (root / "demo" / "plugin.toml").write_text('[plugin]\nid = "demo"\nname = "Demo"\ndescription = "d"\nentry = "ft_plugins.demo:Demo"\n'
                                              '[plugin_runtime]\nenabled = true\n', encoding="utf-8")
    sys.path.insert(0, str(tmp_path))
    port = free_port()
    state = {}

    async def serve():
        host = PluginHost(root)
        host.load()
        await host.start()
        app = F.build_server(F.FaceTools(client_factory=lambda: FakeClient(), console=CONSOLE), None, port, host).streamable_http_app(
            host="127.0.0.1", transport_security=F.transport_security(port))
        state["srv"] = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error"))
        try:
            await state["srv"].serve()
        finally:
            await host.stop()

    th = threading.Thread(target=lambda: asyncio.run(serve()), daemon=True)
    th.start()
    for _ in range(100):
        if state.get("srv") and state["srv"].started:
            break
        time.sleep(0.05)
    yield port
    state["srv"].should_exit = True
    th.join(5)
    sys.path.remove(str(tmp_path))
    for name in [m for m in sys.modules if m == "ft_plugins" or m.startswith("ft_plugins.")]:
        del sys.modules[name]


def test_end_to_end_a_plugin_entry_is_a_tool_and_its_own_message_arrives_once(plugin_stack):
    from mcp.client.session import ClientSession
    from mcp.client.streamable_http import streamable_http_client

    async def go():
        async with streamable_http_client(f"http://127.0.0.1:{plugin_stack}/mcp") as (r, w):
            async with ClientSession(r, w) as s:
                await s.initialize()
                names = sorted(t.name for t in (await s.list_tools()).tools)
                echoed = await s.call_tool("demo__echo", {"text": "hello"})
                first = await s.call_tool("jarvis_messages", {})
                second = await s.call_tool("jarvis_messages", {})
                return names, echoed, first, second
    names, echoed, first, second = asyncio.run(go())
    assert "demo__echo" in names and "jarvis_messages" in names and not any(n.startswith("jarvis_bridge__") for n in names)
    result = json.loads(echoed.content[0].text)
    assert result["result"]["text"] == "hello" and "someone" not in json.dumps(result), "results are cleaned before the face's model sees them"
    one, two = json.loads(first.content[0].text), json.loads(second.content[0].text)
    assert one["count"] == 1 and "echoed hello" in json.dumps(one) and two["count"] == 0


def test_the_developer_plugins_are_left_out_of_the_faces_tool_list_unless_asked_for(monkeypatch):
    """105 tools (60 KB) made every turn of the face's chat model slow and its next request 110 KB; workbench alone is 30 of them (2026-10-25)."""
    import importlib
    assert {"jarvis_bridge", "workbench", "provider_signup"} <= F.PLUGINS_NOT_EXPOSED
    try:
        monkeypatch.setenv("JARVIS_FACE_TOOLS_EXCLUDE", "")
        assert importlib.reload(F).PLUGINS_NOT_EXPOSED == {"jarvis_bridge"}
        monkeypatch.setenv("JARVIS_FACE_TOOLS_EXCLUDE", " workbench , local_team ")
        assert importlib.reload(F).PLUGINS_NOT_EXPOSED == {"jarvis_bridge", "workbench", "local_team"}
    finally:
        monkeypatch.delenv("JARVIS_FACE_TOOLS_EXCLUDE", raising=False)
        importlib.reload(F)
