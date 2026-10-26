"""Drivers: the MCP client against a fake tool server, the PIN-card watching, and the face driver's JSON line."""
import json
import subprocess
import urllib.error

import pytest

from jarvis.evals.reliability import drivers as D
from jarvis.plugins._shared import ask_form


class Resp:
    def __init__(self, body, ctype="application/json", sid=None):
        self.body, self.headers = body.encode(), {"Content-Type": ctype, **({"Mcp-Session-Id": sid} if sid else {})}

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read(self):
        return self.body


class FakeServer:
    """Speaks just enough MCP: initialize, notifications, tools/call, as JSON or as an SSE stream."""
    def __init__(self, tools, sse=False):
        self.tools, self.sse, self.log, self.session = tools, sse, [], "sess-1"

    def __call__(self, req, timeout=None):
        msg = json.loads(req.data)
        hdr = {k.lower(): v for k, v in req.header_items()}
        self.log.append((msg.get("method"), msg.get("params", {}).get("name"), hdr.get("mcp-session-id")))
        if msg["method"] == "initialize":
            return Resp(json.dumps({"jsonrpc": "2.0", "id": msg["id"], "result": {"protocolVersion": "2025-06-18"}}), sid=self.session)
        if msg["method"] == "notifications/initialized":
            return Resp("", "text/plain")
        name, args = msg["params"]["name"], msg["params"]["arguments"]
        out = self.tools[name](args) if callable(self.tools[name]) else self.tools[name]
        result = {"content": [{"type": "text", "text": json.dumps(out)}]} if not self.sse else {"structuredContent": out}
        body = {"jsonrpc": "2.0", "id": msg["id"], "result": result}
        if self.sse:
            return Resp("event: message\ndata: " + json.dumps(body) + "\n\n", "text/event-stream")
        return Resp(json.dumps(body))


def test_client_initializes_once_keeps_the_session_and_reads_json_or_sse():
    srv = FakeServer({"jarvis_ready": {"ready": True, "message": "JARVIS is ready."}})
    c = D.McpClient(opener=srv)
    assert c.call("jarvis_ready")["ready"] is True and c.call("jarvis_ready")["ready"] is True
    methods = [m for m, _, _ in srv.log]
    assert methods == ["initialize", "notifications/initialized", "tools/call", "tools/call"]
    assert all(s == "sess-1" for m, _, s in srv.log if m != "initialize")
    sse = D.McpClient(opener=FakeServer({"jarvis_ready": {"ready": False}}, sse=True))
    assert sse.call("jarvis_ready") == {"ready": False}


def test_client_errors_are_plain_and_never_leak_urls():
    def refuse(req, timeout=None):
        raise urllib.error.URLError("connection refused http://127.0.0.1:48970")
    with pytest.raises(D.DriverError) as e:
        D.McpClient(opener=refuse).call("jarvis_ready")
    assert "not reachable" in str(e.value) and "127.0.0.1" not in str(e.value)

    def http_err(req, timeout=None):
        raise urllib.error.HTTPError(req.full_url, 421, "x", {}, None)
    with pytest.raises(D.DriverError) as e2:
        D.McpClient(opener=http_err).call("x")
    assert "HTTP 421" in str(e2.value)


class Clock:
    def __init__(self):
        self.t = 0.0

    def now(self):
        return self.t

    def sleep(self, s):
        self.t += s


def mcp_driver(tools, **kw):
    clk = Clock()
    return D.McpDriver(D.McpClient(opener=FakeServer(tools)), clock=clk.now, sleep=clk.sleep, **kw), clk


def test_mcp_driver_follows_a_task_to_done():
    states = iter([{"state": "running"}, {"state": "running"}, {"state": "done", "reply": "Done: Calculator is open.", "summary": "Done."}])
    d, clk = mcp_driver({"jarvis_waiting": {"count": 0, "approvals": []}, "jarvis_do": {"ok": True, "command_id": "c1"}, "jarvis_task_result": lambda a: next(states)})
    obs = d.run("Open Calculator.", 60)
    assert obs.status == "done" and obs.final_text == "Done: Calculator is open." and obs.command_id == "c1" and not obs.approval_pending and obs.seconds == 6.0


def test_mcp_driver_ends_an_unattended_run_at_a_pin_card_and_never_approves():
    waiting = iter([[], [{"id": "ap-9"}], [{"id": "ap-9"}], [{"id": "ap-9"}]])
    tools = {"jarvis_waiting": lambda a: {"approvals": next(waiting)}, "jarvis_do": {"ok": True, "command_id": "c1"}, "jarvis_task_result": {"state": "running", "message": "waiting"}}
    d, clk = mcp_driver(tools)
    obs = d.run("Delete the file x.", 60)
    assert obs.approval_pending and obs.approval_ids == ["ap-9"] and not obs.approved and obs.status == "done"
    assert all(m != "approve" for m, n, s in d.c._open.log if n)                      # no approving tool exists, and none was called


def test_mcp_driver_attended_run_waits_for_the_owner_and_records_the_approval():
    waiting = iter([[], [{"id": "ap-1"}], [{"id": "ap-1"}], [], []])
    results = iter([{"state": "running"}, {"state": "running"}, {"state": "running"}, {"state": "done", "reply": "Moved to the Recycle Bin."}, {"state": "done", "reply": "Moved to the Recycle Bin."}])
    tools = {"jarvis_waiting": lambda a: {"approvals": next(waiting, [])}, "jarvis_do": {"ok": True, "command_id": "c1"}, "jarvis_task_result": lambda a: next(results, {"state": "done"})}
    d, clk = mcp_driver(tools, approval_wait_s=30)
    obs = d.run("Delete the file x.", 120, attended=True)
    assert obs.approved and not obs.approval_pending and obs.final_text.startswith("Moved")


def test_mcp_driver_timeout_and_unreachable_server():
    d, clk = mcp_driver({"jarvis_waiting": {"approvals": []}, "jarvis_do": {"ok": True, "command_id": "c1"}, "jarvis_task_result": {"state": "running"}})
    obs = d.run("Do a long thing.", 10)
    assert obs.status == "timeout" and obs.seconds >= 10

    def down(req, timeout=None):
        raise OSError("down")
    obs = D.McpDriver(D.McpClient(opener=down)).run("x", 5)
    assert obs.status == "harness_error" and "not reachable" in obs.harness_error


def test_mcp_driver_sees_an_ask_form_in_the_result():
    form = ask_form.did_you_mean(["Open Notepad", "Open WordPad"])
    tools = {"jarvis_waiting": {"approvals": []}, "jarvis_do": {"ok": True, "command_id": "c1"}, "jarvis_task_result": {"state": "done", "reply": "Which one?", "form": form}}
    d, _ = mcp_driver(tools)
    obs = d.run("Open it.", 30)
    assert ask_form.is_form(obs.ask_form, 2)


def test_face_row_becomes_an_observation():
    row = {"goal": "g", "seconds": 41.2, "timed_out": False, "requests": 3, "statuses": [200, 429, 200], "calls": ["builtIn_mcpCallTool -> jarvis::jarvis_do"],
           "final_text": "Calculator is open.", "ask_form": None, "errors_on_screen": ["Remote sent 400 response"], "store_found": True, "messages": 4}
    obs = D.observation_from_face_row(row, [], 41.0)
    assert obs.final_text == "Calculator is open." and obs.requests == 3 and obs.seconds == 41.2 and "HTTP 429" in obs.errors and "Remote sent 400 response" in obs.errors
    assert obs.tool_calls == ["builtIn_mcpCallTool -> jarvis::jarvis_do"] and obs.raw["store_found"] is True and not obs.asked_in_prose
    q = D.observation_from_face_row({"final_text": "Which file do you mean?", "ask_form": None}, ["ap-1"], 3.0)
    assert q.asked_in_prose and q.approval_pending and q.approval_ids == ["ap-1"]
    form = ask_form.did_you_mean(["a", "b"])
    f = D.observation_from_face_row({"final_text": "Which?", "ask_form": form}, [], 3.0)
    assert not f.asked_in_prose and ask_form.is_form(f.ask_form)
    assert D.observation_from_face_row({"harness_error": "cannot reach the face"}, [], 1).status == "harness_error"
    assert D.observation_from_face_row({"timed_out": True}, [], 1).status == "timeout"


def test_face_driver_runs_the_node_script_and_reads_the_last_json_line():
    calls = []

    def runner(cmd, **kw):
        calls.append(cmd)
        if cmd[0] == "node":
            return subprocess.CompletedProcess(cmd, 0, stdout='log line\n{"goal":"x","seconds":5,"final_text":"Hello there.","calls":[],"requests":1,"statuses":[200]}\n', stderr="")
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
    srv = FakeServer({"jarvis_waiting": {"approvals": []}})
    fd = D.FaceDriver(client=D.McpClient(opener=srv), runner=runner, reset_cmd=["bash", "reset.sh"])
    obs = fd.run("Say hello.", 90)
    assert obs.final_text == "Hello there." and calls[0] == ["bash", "reset.sh"] and calls[1][0] == "node" and calls[1][2] == "Say hello." and calls[1][3] == "90"

    def broken(cmd, **kw):
        return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="Error: playwright missing")
    obs = D.FaceDriver(client=D.McpClient(opener=srv), runner=broken).run("x", 5)
    assert obs.status == "harness_error" and "printed nothing" in obs.harness_error
