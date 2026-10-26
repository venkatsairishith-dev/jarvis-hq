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
from pathlib import Path

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
    t = F.FaceTools(client_factory=lambda: c, console=CONSOLE, pc_ops_path=p, unread=lambda: 3, goal_progress_path=tmp_path / "no-goal.json")
    up = asyncio.run(t.status())
    assert up == {"ok": True, "console_up": True, "kernel_up": True, "kill_switch_on": False, "console_busy": False, "approvals_waiting": 1,
                  "messages_unread": 3, "pc_busy": True, "pc_kind": "action", "pc_age_s": 12, "pc_stopping": False, "pc_ops": 1,
                  "goal_state": "none", "goal_n": 0, "goal_total": 0, "goal_exec": ""}
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


# ---- the compact chat profile (D49): what the face's chat model is offered, and what the face's own panels still reach --------------

CHAT_PLUGINS = {"deliverables", "desktop", "doc_reader", "doc_tools", "web_search", "workbench"}     # workbench is NOT a chat tool
CHAT_LIST_LIMIT = 12_000                                                                              # bytes of tools/list


class _Memory:
    def recall(self, days):
        return "- the owner likes short answers"

    def remember(self, turns):
        return {"saved": len(turns)}


class _Spoken:
    def take(self, n):
        return [{"urgency": "normal", "text": "A meeting starts in 10 minutes."}][:n]


def _serialized(tools_list) -> bytes:
    return json.dumps([t.model_dump(by_alias=True, exclude_none=True, mode="json") for t in tools_list], separators=(",", ":")).encode("utf-8")


@pytest.fixture
def chat_stack(tmp_path, monkeypatch):
    """Builds a tool server with the real plugins a chat goal uses (plus workbench, which it must not offer), the panel's output folder, memory and the heartbeat."""
    from jarvis.bridge.output_preview import OutputPreview
    from jarvis.pluginapi.host import PluginHost

    monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path / "data"))
    hosts = []

    async def build(profile, **kw):
        host = PluginHost(only=CHAT_PLUGINS, exclude=F.PLUGINS_NOT_EXPOSED)
        host.load()
        await host.start()
        hosts.append(host)
        outputs = OutputPreview(tmp_path / f"outputs-{len(hosts)}")
        from jarvis.bridge.ask import AskBroker
        from jarvis.bridge.cleaner import Cleaner, Env
        from jarvis.bridge.cleaner_ui import CleanerService
        root = tmp_path / f"pc-{len(hosts)}"
        env = Env(home=root / "Users" / "me", user_temp=[root / "Temp"], windows=root / "Windows", local_appdata=root / "Users" / "me" / "AppData" / "Local",
                  appdata=root / "Users" / "me" / "AppData" / "Roaming", program_files=[root / "Program Files"], program_data=root / "ProgramData",
                  users_root=root / "Users")
        broker = AskBroker(home=env.home)
        server = F.build_server(tools(FakeClient()), None, F.DEFAULT_PORT, host, _Spoken(), _Memory(), outputs, profile,
                                asker=broker, cleaner=CleanerService(Cleaner(env=env), broker), **kw)
        return server

    async def stop():
        for host in hosts:
            await host.stop()
    return build, stop


def test_the_chat_profile_is_the_default_and_the_full_one_is_still_there():
    args = F.parser().parse_args([])
    assert args.profile == "chat" and F.DEFAULT_PROFILE == "chat" and args.list_panel_tools is False
    assert F.parser().parse_args(["--profile", "full"]).profile == "full"
    with pytest.raises(SystemExit):
        F.parser().parse_args(["--profile", "everything"])
    with pytest.raises(ValueError):
        F.build_server(tools(FakeClient()), profile="everything")


def test_the_chat_list_has_only_what_a_chat_goal_needs_and_is_under_12_kb(chat_stack):
    build, stop = chat_stack

    async def go():
        try:
            return await (await build("chat")).list_tools()
        finally:
            await stop()
    listed = asyncio.run(go())
    names = {t.name for t in listed}
    assert names == set(F.CHAT_TOOLS), f"missing {set(F.CHAT_TOOLS) - names}, extra {names - set(F.CHAT_TOOLS)}"
    assert not names & set(F.PANEL_TOOLS), "the panel tools are not offered to the chat model"
    for hidden in ("jarvis_outputs", "jarvis_output_get", "jarvis_preview_wanted", "jarvis_status", "jarvis_notices", "jarvis_spoken", "jarvis_remember_chat"):
        assert hidden not in names
    assert not any(n.startswith(("workbench__", "learn_vault__", "main_agent__")) for n in names)
    size = len(_serialized(listed))
    import os
    if os.environ.get("SNAPSHOT_OUT"):          # regenerate the face's request-budget snapshot: SNAPSHOT_OUT=<path> pytest -k 12_kb, then reformat it as JSON (indent 1)
        Path(os.environ["SNAPSHOT_OUT"]).write_bytes(_serialized(listed))
    if os.environ.get("SHOW_SIZES"):            # which tools take the bytes
        for t in sorted(listed, key=lambda t: -len(_serialized([t]))):
            print(len(_serialized([t])), t.name)
    print(f"chat tools/list: {len(listed)} tools, {size} bytes")
    assert size < CHAT_LIST_LIMIT
    assert size <= 7_600, f"the chat tool list is {size} bytes; the budget is 7.6 KB (shorten a description before adding a tool)"
    assert "desktop__do_goal" in names and "PIN" in next(t for t in listed if t.name == "desktop__do_goal").description
    assert all(t.annotations is None and t.output_schema is None for t in listed), "no annotations or output schemas in the compact list"
    assert all('"title"' not in json.dumps(t.input_schema) for t in listed), "no pydantic titles"


def test_the_face_request_budget_test_uses_the_current_chat_tool_names():
    """The face's request-size test (stage-ui `request-budget.test.ts`) replays a snapshot of this list; it must name the same tools."""
    snapshot = Path(__file__).resolve().parents[1] / "face" / "packages" / "stage-ui" / "src" / "stores" / "chat" / "request-budget.mcp-tools.json"
    assert {t["name"] for t in json.loads(snapshot.read_text(encoding="utf-8"))} == set(F.CHAT_TOOLS), "regenerate the snapshot (see the comment in request-budget.test.ts)"


def test_what_a_call_needs_survives_the_shortening(chat_stack):
    build, stop = chat_stack

    async def go():
        try:
            return {t.name: t for t in await (await build("chat")).list_tools()}
        finally:
            await stop()
    t = asyncio.run(go())
    assert t["jarvis_do"].input_schema["required"] == ["task"] and "PIN" in t["jarvis_do"].description
    assert t["doc_reader__read_document"].input_schema["required"] == ["path"] and "never instructions" in t["doc_reader__read_document"].description
    assert "full path" in t["doc_reader__read_document"].description
    assert t["deliverables__make_presentation"].input_schema["required"] == ["spec"] and "two_column" in t["deliverables__make_presentation"].description
    assert t["deliverables__make_report"].input_schema["required"] == ["spec"]
    assert "properties" in t["desktop__read_window_text"].input_schema and "screen text" in t["desktop__read_window_text"].description
    for check in ("processes", "disk", "network", "services", "app_info", "crashes", "media"):
        assert check in t["desktop__check_pc"].description
    assert "list_browser_profiles" not in json.dumps(t["desktop__open_website"].input_schema), "no pointer to a tool the chat does not have"


def test_panel_tools_stay_callable_but_other_tools_are_gone_in_the_chat_profile(chat_stack):
    build, stop = chat_stack

    async def go():
        try:
            server = await build("chat")
            out = {}
            for name in ("jarvis_status", "jarvis_outputs", "jarvis_preview_wanted", "jarvis_spoken", "jarvis_recall"):
                out[name] = str(await server.call_tool(name, {}))
            out["remember"] = str(await server.call_tool("jarvis_remember_chat", {"turns": [{"role": "user", "text": "hi"}]}))
            for name in ("workbench__project_status", "jarvis_messages"):
                try:
                    out[name] = str(await server.call_tool(name, {}))
                except Exception as exc:        # noqa: BLE001 - an unknown tool is an error
                    out[name] = f"ERROR {exc}"
            return out
        finally:
            await stop()
    out = asyncio.run(go())
    assert "kernel_up" in out["jarvis_status"] and "ids" in out["jarvis_preview_wanted"] and "count" in out["jarvis_outputs"]
    assert "meeting" in out["jarvis_spoken"] and "short answers" in out["jarvis_recall"] and "saved" in out["remember"]
    assert out["workbench__project_status"].startswith("ERROR") and out["jarvis_messages"].startswith("ERROR"), "tools outside the profile are not registered"


def test_the_list_panel_tools_flag_and_the_full_profile_list_everything(chat_stack):
    build, stop = chat_stack

    async def go():
        try:
            return {t.name for t in await (await build("chat", list_panel_tools=True)).list_tools()}, await (await build("full")).list_tools()
        finally:
            await stop()
    with_panel, full = asyncio.run(go())
    assert set(F.PANEL_TOOLS) - {"jarvis_notices"} <= with_panel and set(F.CHAT_TOOLS) <= with_panel
    full_names = {t.name for t in full}
    assert {"jarvis_ready", "jarvis_messages", "workbench__project_status"} <= full_names, "the full profile is the old behaviour"
    assert len(_serialized(full)) > 3 * CHAT_LIST_LIMIT


def test_compact_schema_drops_noise_but_keeps_names_and_meaning():
    raw = {"type": "object", "title": "xArguments", "additionalProperties": True,
           "properties": {"title": {"title": "Title", "type": "string", "description": "the title"},
                          "profile": {"anyOf": [{"type": "string"}, {"type": "null"}], "default": None, "description": "alias; see list_browser_profiles.", "title": "Profile"},
                          "limit": {"type": "integer", "default": 12, "title": "Limit"},
                          "long": {"type": "string", "description": "x" * 400}},
           "required": ["title"]}
    out = F.compact_schema(raw)
    assert set(out["properties"]) == {"title", "profile", "limit", "long"}, "a parameter called title stays"
    assert out["properties"]["title"] == {"type": "string", "description": "the title"}
    assert out["properties"]["profile"] == {"type": "string", "description": "alias"}
    assert out["properties"]["limit"] == {"type": "integer", "default": 12}
    assert len(out["properties"]["long"]["description"]) <= F.MAX_PARAM_DESCRIPTION
    assert out["required"] == ["title"] and "title" not in {k for k in out if k != "properties"} and "additionalProperties" not in out
    assert raw["properties"]["profile"]["default"] is None, "the input is not changed"


def test_the_goal_tool_gets_the_face_side_readers_and_they_are_not_listed(tmp_path, monkeypatch):
    from jarvis.pluginapi.host import PluginHost
    monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path / "data"))

    class Views:
        def tasks(self, state, limit):
            return [{"id": "t1", "state": state or "done", "limit": limit}]

    async def go():
        host = PluginHost(only={"desktop"}, exclude=F.PLUGINS_NOT_EXPOSED)
        host.load()
        await host.start()
        try:
            server = F.build_server(tools(FakeClient()), Views(), F.DEFAULT_PORT, host, None, _Memory(), None, "chat")
            return host.loaded["desktop"].instance.goal_readers, {t.name for t in await server.list_tools()}
        finally:
            await host.stop()
    readers, names = asyncio.run(go())
    assert set(readers) == {"recall", "tasks"} and "short answers" in readers["recall"](2)
    assert readers["tasks"]("waiting_approval", 500)[0]["limit"] == 20, "the limit is capped"
    assert "desktop__do_goal" in names and not names & {"recall", "tasks", "jarvis_tasks"}, "lent to the plugin, never offered as tools"


# ---- D60: the goal's progress line for the face (fixed words and numbers only)
def _goal_file(tmp_path, **kw):
    p = tmp_path / "goal_progress.json"
    p.write_text(json.dumps({"state": "running", "n": 2, "total": 3, "executor": "screen_read", "at": 1000.0, **kw}), encoding="utf-8")
    return p


def test_goal_progress_is_fixed_words_and_counts_only(tmp_path):
    none = {"goal_state": "none", "goal_n": 0, "goal_total": 0, "goal_exec": ""}
    assert F.read_goal_progress(_goal_file(tmp_path), now=1010.0) == {"goal_state": "running", "goal_n": 2, "goal_total": 3, "goal_exec": "screen_read"}
    assert F.read_goal_progress(_goal_file(tmp_path), now=1000.0 + F.GOAL_STALE_S + 1) == none, "a plugin that stopped refreshing it"
    assert F.read_goal_progress(_goal_file(tmp_path, state="idle"), now=1001.0) == none
    assert F.read_goal_progress(tmp_path / "missing.json", now=1.0) == none
    hostile = F.read_goal_progress(_goal_file(tmp_path, executor="C:/secret/plans.xlsx", n=500, total="x"), now=1001.0)
    assert hostile == {"goal_state": "running", "goal_n": 99, "goal_total": 0, "goal_exec": ""}
    bad = tmp_path / "bad.json"
    bad.write_text("{nope", encoding="utf-8")
    assert F.read_goal_progress(bad, now=1.0) == none


def test_status_carries_the_goal_progress(tmp_path):
    import time as _t
    p = _goal_file(tmp_path, at=_t.time())
    t = F.FaceTools(client_factory=lambda: FakeClient(), console=CONSOLE, pc_ops_path=tmp_path / "none.json", goal_progress_path=p)
    got = asyncio.run(t.status())
    assert (got["goal_state"], got["goal_n"], got["goal_total"], got["goal_exec"]) == ("running", 2, 3, "screen_read")


def test_quick_command_is_callable_by_the_face_but_never_offered_to_the_chat_model(chat_stack):
    build, stop = chat_stack

    async def go():
        try:
            chat = {t.name for t in await (await build("chat")).list_tools()}
            with_panel = {t.name for t in await (await build("chat", list_panel_tools=True)).list_tools()}
            return chat, with_panel
        finally:
            await stop()
    chat, with_panel = asyncio.run(go())
    assert "desktop__quick_command" in F.PANEL_TOOLS and "desktop__quick_command" not in chat
    assert "desktop__quick_command" in with_panel, "registered (callable by the face's own code) in the chat profile"
