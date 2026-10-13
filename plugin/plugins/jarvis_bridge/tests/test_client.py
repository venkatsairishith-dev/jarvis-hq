"""Tests for the JARVIS bridge against a fake console (no model, no network beyond loopback)."""

from __future__ import annotations

import asyncio
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from plugin.plugins.jarvis_bridge.client import BridgeError, JarvisClient, MAX_TASK_CHARS, resolve_base_url, speak_full_reply


class _Console:
    """A stand-in for the JARVIS console that records what it was sent and answers like the real one."""

    def __init__(self):
        self.seen: list[dict] = []
        self.commands = [{"id": "c-1", "ts": 1.0, "source": "neko", "text": "t", "state": "done", "reply": "secret details", "error": None, "finished": 2.0}]
        self.post_status = 202
        self.post_body: dict = {"id": "c-new", "state": "running"}
        console = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _send(self, code, obj):
                data = json.dumps(obj).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self):
                console.seen.append({"method": "GET", "path": self.path, "header": self.headers.get("X-Jarvis-Console"), "host": self.headers.get("Host")})
                if self.path.startswith("/api/owner/commands"):
                    return self._send(200, {"commands": console.commands})
                if self.path == "/api/health":
                    return self._send(200, {"kernel": {"reachable": True, "authorized": True, "killed": False}})
                if self.path == "/api/owner/state":
                    return self._send(200, {"busy": True})
                self._send(404, {"error": "not found"})

            def do_POST(self):
                n = int(self.headers.get("Content-Length", "0"))
                body = json.loads(self.rfile.read(n) or b"{}")
                console.seen.append({"method": "POST", "path": self.path, "body": body, "header": self.headers.get("X-Jarvis-Console"), "origin": self.headers.get("Origin")})
                self._send(console.post_status, console.post_body)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture()
def console():
    c = _Console()
    yield c
    c.close()


def run(coro):
    return asyncio.run(coro)


def test_delegate_sends_the_task_as_a_neko_command(console):
    out = run(JarvisClient(console.url).delegate("  find my tax PDFs and list them  "))
    assert out == {"id": "c-new", "state": "running"}
    post = console.seen[-1]
    assert post["path"] == "/api/owner/command"
    assert post["body"] == {"text": "find my tax PDFs and list them", "source": "neko"}
    assert post["header"] == "1" and post["origin"] is None       # the console's own cross-site check is satisfied without an Origin


def test_empty_and_oversized_tasks_are_refused_before_anything_is_sent(console):
    for task in ("", "   ", "x" * (MAX_TASK_CHARS + 1)):
        with pytest.raises(BridgeError):
            run(JarvisClient(console.url).delegate(task))
    assert console.seen == []


def test_the_kernels_own_refusal_text_is_passed_through(console):
    console.post_status, console.post_body = 429, {"error": "JARVIS's model providers are resting after hitting their free-tier limits"}
    with pytest.raises(BridgeError) as e:
        run(JarvisClient(console.url).delegate("do something"))
    assert e.value.status == 429 and "resting" in e.value.message
    console.post_status, console.post_body = 409, {"error": "JARVIS is still working on the previous command"}
    with pytest.raises(BridgeError) as e:
        run(JarvisClient(console.url).delegate("do something"))
    assert e.value.status == 409


def test_a_console_that_is_not_running_is_reported_plainly():
    with pytest.raises(BridgeError) as e:
        run(JarvisClient("http://127.0.0.1:9").delegate("hello"))     # nothing listens on the discard port
    assert e.value.status == 503 and "start-console" in e.value.message


def test_commands_and_status(console):
    rows = run(JarvisClient(console.url).commands(5))
    assert rows[0]["id"] == "c-1" and "limit=5" in console.seen[-1]["path"]
    s = run(JarvisClient(console.url).status())
    assert s == {"console": True, "kernel_reachable": True, "kernel_authorized": True, "kill_switch_on": False, "busy": True}


@pytest.mark.parametrize("url", ["https://127.0.0.1:8799", "http://example.com:8799", "http://user:pw@127.0.0.1:8799", "http://10.0.0.5:8799",
                                 "http://127.0.0.1:8799/api", "ftp://127.0.0.1"])
def test_only_a_plain_http_loopback_console_is_accepted(url):
    with pytest.raises(BridgeError):
        resolve_base_url(url)


def test_default_and_env_url(monkeypatch):
    monkeypatch.delenv("JARVIS_CONSOLE_URL", raising=False)
    assert resolve_base_url() == "http://127.0.0.1:8799"
    monkeypatch.setenv("JARVIS_CONSOLE_URL", "http://localhost:9000/")
    assert resolve_base_url() == "http://localhost:9000"


def test_full_reply_is_off_unless_the_owner_turns_it_on(monkeypatch):
    monkeypatch.delenv("JARVIS_BRIDGE_SPEAK_REPLY", raising=False)
    assert speak_full_reply() is False
    monkeypatch.setenv("JARVIS_BRIDGE_SPEAK_REPLY", "1")
    assert speak_full_reply() is True


def test_entries_are_registered_and_do_not_leak_a_private_reply(console, monkeypatch):
    """Load the plugin class through the real SDK decorators and call the entries on a bare instance."""
    from plugin.plugins.jarvis_bridge import JarvisBridgePlugin
    monkeypatch.setenv("JARVIS_CONSOLE_URL", console.url)
    monkeypatch.delenv("JARVIS_BRIDGE_SPEAK_REPLY", raising=False)
    plugin = object.__new__(JarvisBridgePlugin)
    res = run(JarvisBridgePlugin.task_result(plugin, command_id="c-1"))
    payload = getattr(res, "value", None) or getattr(res, "data", None) or res
    text = json.dumps(payload, default=str)
    assert "done" in text and "secret details" not in text
    monkeypatch.setenv("JARVIS_BRIDGE_SPEAK_REPLY", "1")
    res = run(JarvisBridgePlugin.task_result(plugin, command_id="c-1"))
    assert "secret details" in json.dumps(getattr(res, "value", None) or getattr(res, "data", None) or res, default=str)
