"""Tests for the JARVIS bridge against a fake console (no model, no network beyond loopback)."""

from __future__ import annotations

import os
import asyncio
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from jarvis.plugins.jarvis_bridge.client import BridgeError, JarvisClient, MAX_TASK_CHARS, resolve_base_url, speak_full_reply


class _Console:
    """A stand-in for the JARVIS console that records what it was sent and answers like the real one."""

    def __init__(self):
        self.seen: list[dict] = []
        self.commands = [{"id": "c-1", "ts": 1.0, "source": "plugin", "text": "t", "state": "done", "reply": "secret details", "error": None, "finished": 2.0}]
        self.post_status = 202
        self.post_body: dict = {"id": "c-new", "state": "running"}
        self.secret = "launch-secret-A-0123456789abcdefghijklmnopqrstuv"      # the real console makes a new one at every launch (SEC-D9)
        self.serve_secret = True
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
                if self.path == "/":
                    meta = f'<meta name="jarvis-console-secret" content="{console.secret}">' if console.serve_secret else ""
                    data = f"<!doctype html><html><head><title>JARVIS</title>{meta}</head><body></body></html>".encode()
                    self.send_response(200)
                    self.send_header("Content-Type", "text/html; charset=utf-8")
                    self.send_header("Content-Length", str(len(data)))
                    self.end_headers()
                    return self.wfile.write(data)
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
                if self.headers.get("X-Jarvis-Console") != console.secret:            # what the real console does with a missing or old secret
                    return self._send(403, {"error": "request refused: missing or old console secret; reload the page", "reload": True})
                self._send(console.post_status, console.post_body)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture(autouse=True)
def _no_real_token_file(monkeypatch, tmp_path):
    """The legacy tests below use a console that serves its secret in its page; never pick up a real console.token of the machine."""
    monkeypatch.setenv("JARVIS_RUN_DIR", str(tmp_path / "empty-run-dir"))


@pytest.fixture()
def console():
    c = _Console()
    yield c
    c.close()


def run(coro):
    return asyncio.run(coro)


def test_delegate_sends_the_task_as_a_plugin_command(console):
    out = run(JarvisClient(console.url).delegate("  find my tax PDFs and list them  "))
    assert out == {"id": "c-new", "state": "running"}
    post = console.seen[-1]
    assert post["path"] == "/api/owner/command"
    assert post["body"] == {"text": "find my tax PDFs and list them", "source": "plugin"}
    assert post["header"] == console.secret and post["origin"] is None       # the console's secret, read from its own page; no Origin needed


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
    from jarvis.plugins.jarvis_bridge import JarvisBridgePlugin
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


def test_cancel_sends_one_command_id_to_the_consoles_cancel_route(console):
    """main_agent's Stop withdraws the command it handed over: the console's "Stop waiting" route, for that id only."""
    console.post_status, console.post_body = 200, {"cancelled": True}
    out = run(JarvisClient(console.url).cancel("c-7"))
    assert out == {"id": "c-7", "cancelled": True}
    post = console.seen[-1]
    assert post["path"] == "/api/owner/command/cancel" and post["header"] == console.secret
    assert post["body"] == {"id": "c-7", "stop_tasks": True}      # the explicit option: also stop the lab tasks that command started
    console.post_body = {"cancelled": False}                     # already finished: reported, not claimed
    assert run(JarvisClient(console.url).cancel("c-7"))["cancelled"] is False
    assert not any(s["path"] in ("/api/owner/stop_all", "/api/owner/kill") for s in console.seen)


def test_cancel_needs_a_real_command_id(console):
    for bad in ("", "  ", "x" * 41, "c-1/../kill", "c 1"):
        with pytest.raises(BridgeError):
            run(JarvisClient(console.url).cancel(bad))
    assert console.seen == []


def test_the_cancel_entry_says_which_tasks_were_stopped_and_what_it_cannot_reach(console, monkeypatch):
    from jarvis.plugins.jarvis_bridge import JarvisBridgePlugin
    monkeypatch.setenv("JARVIS_CONSOLE_URL", console.url)
    console.post_status = 200
    plugin = object.__new__(JarvisBridgePlugin)
    payload = lambda r: getattr(r, "value", None) or getattr(r, "data", None) or r      # noqa: E731
    console.post_body = {"cancelled": True, "tasks_stopped": ["aaaaaaaaaaaa", "bbbbbbbbbbbb"], "jobs_cancelled": ["j1"], "tasks_not_stopped": []}
    p = payload(run(JarvisBridgePlugin.cancel_task(plugin, command_id="c-7")))
    assert p["cancelled"] is True and p["tasks_stopped"] == ["aaaaaaaaaaaa", "bbbbbbbbbbbb"]
    assert "2 lab tasks it had started were stopped" in p["message"]
    assert "keep running" not in p["message"]                                    # the old promise ("they keep running") is gone
    assert "Stop all" in p["message"]                                            # and what it cannot reach is still said
    console.post_body = {"cancelled": False, "tasks_stopped": ["cccccccccccc"], "jobs_cancelled": [], "tasks_not_stopped": ["dddddddddddd"]}
    p = payload(run(JarvisBridgePlugin.cancel_task(plugin, command_id="c-8")))
    assert p["cancelled"] is False and "already finished" in p["message"] and "1 lab task it had started was stopped" in p["message"]
    assert "1 could not be stopped" in p["message"]
    console.post_body = {"cancelled": False}                                     # an older console that ignores stop_tasks: nothing claimed
    p = payload(run(JarvisBridgePlugin.cancel_task(plugin, command_id="c-9")))
    assert p["tasks_stopped"] == [] and "lab task" not in p["message"].replace("A task that cannot be tied", "")
    res = run(JarvisBridgePlugin.cancel_task(plugin, command_id=""))
    assert type(res).__name__ == "Err"


# ---- the console's per-launch secret (audit SEC-D9)
def test_reads_and_writes_both_carry_the_token_and_it_is_fetched_once(console):
    """Re-review M3: the old test pinned "a read needs nothing". Private reads need the token now (legacy page path shown here)."""
    run(JarvisClient(console.url).commands(5))
    assert [(s["method"], s["path"]) for s in console.seen] == [("GET", "/"), ("GET", "/api/owner/commands?limit=5")]
    assert console.seen[-1]["header"] == console.secret
    run(JarvisClient(console.url).delegate("do a thing"))
    paths = [(s["method"], s["path"]) for s in console.seen]
    assert paths[-1] == ("POST", "/api/owner/command") and ("GET", "/") not in paths[2:]       # cached: the page is not fetched again
    assert console.seen[-1]["header"] == console.secret and console.seen[-1]["header"] != "1"


def test_a_restarted_console_has_a_new_secret_and_the_bridge_follows_it_once(console):
    run(JarvisClient(console.url).delegate("first"))
    old = console.secret
    console.secret = "launch-secret-B-0123456789abcdefghijklmnopqrstuv"                  # the console was restarted
    out = run(JarvisClient(console.url).delegate("second"))
    assert out["id"] == "c-new"
    posts = [s for s in console.seen if s["method"] == "POST"]
    assert [p["header"] for p in posts[-2:]] == [old, console.secret]                      # refused once, then sent again with the new one
    assert len(posts) == 3


def test_it_gives_up_instead_of_looping_when_the_console_keeps_refusing(console, monkeypatch):
    run(JarvisClient(console.url).delegate("first"))
    real = JarvisClient._console_secret
    async def wrong(self, client, refresh=False):
        await real(self, client, refresh)
        return "not-the-secret"
    monkeypatch.setattr(JarvisClient, "_console_secret", wrong)
    with pytest.raises(BridgeError) as e:
        run(JarvisClient(console.url).delegate("again"))
    assert e.value.status == 403
    assert len([s for s in console.seen if s["method"] == "POST"]) == 3                    # one good, one refused, one retry: no loop


def test_a_page_without_the_secret_is_not_taken_for_the_console(console):
    console.serve_secret = False
    import jarvis.plugins.jarvis_bridge.client as client_mod
    client_mod._SECRETS.pop(console.url, None)
    with pytest.raises(BridgeError) as e:
        run(JarvisClient(console.url).delegate("do a thing"))
    assert e.value.status == 502 and "console" in e.value.message
    assert not [s for s in console.seen if s["method"] == "POST"]                          # nothing was sent anywhere


def test_the_master_token_file_is_used_for_reads_and_writes_and_re_read_after_a_restart(console, monkeypatch, tmp_path):
    """Re-review M3 (2026-10-19): the real console serves no secret in `GET /`. The client reads console.token (0600) and sends it on
    every request, reads included; a file other users can read is ignored."""
    import jarvis.plugins.jarvis_bridge.client as client_mod
    run_dir = tmp_path / "run"
    run_dir.mkdir(mode=0o700)
    monkeypatch.setenv("JARVIS_RUN_DIR", str(run_dir))
    console.serve_secret = False                                                             # the page carries nothing
    client_mod._SECRETS.pop(console.url, None)
    token = run_dir / "console.token"
    token.write_text(console.secret)
    token.chmod(0o600)
    out = run(JarvisClient(console.url).delegate("first"))
    assert out["id"]
    assert not [s for s in console.seen if s["method"] == "GET" and s["path"] == "/"]       # the page was never asked
    assert [s["header"] for s in console.seen if s["method"] == "POST"] == [console.secret]
    console.secret = "launch-secret-C-0123456789abcdefghijklmnopqrstuv"                       # restarted: the file has the new token
    token.write_text(console.secret)
    out = run(JarvisClient(console.url).delegate("second"))
    assert out["id"] and [s["header"] for s in console.seen if s["method"] == "POST"][-1] == console.secret
    if os.name != "posix":                                                                     # Windows has no 0600/0644 file modes
        return
    token.chmod(0o644)                                                                         # readable by others: not trusted
    client_mod._SECRETS.pop(console.url, None)
    with pytest.raises(BridgeError) as e:
        run(JarvisClient(console.url).delegate("third"))
    assert e.value.status == 502 and "token" in e.value.message
