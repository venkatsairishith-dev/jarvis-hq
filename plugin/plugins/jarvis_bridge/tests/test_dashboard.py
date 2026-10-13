"""The data behind the JARVIS panel in N.E.K.O's plugin manager (fake console, no model)."""

from __future__ import annotations

import asyncio
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from plugin.plugins.jarvis_bridge import JarvisBridgePlugin


class Console:
    def __init__(self, killed=False, authorized=True):
        rows = [
            {"id": "c-1", "ts": 1700000000.0, "source": "neko", "text": "  find   my tax PDFs  ", "state": "done", "reply": "PRIVATE REPLY TEXT", "error": None},
            {"id": "c-2", "ts": 1700000100.0, "source": "text", "text": "typed in the JARVIS console", "state": "done", "reply": "x", "error": None},
            {"id": "c-3", "ts": 1700000200.0, "source": "neko", "text": "y" * 300, "state": "running", "reply": "", "error": None},
        ]
        health = {"kernel": {"reachable": True, "authorized": authorized, "killed": killed}}

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):
                if self.path.startswith("/api/owner/commands"):
                    body, code = {"commands": rows}, 200
                elif self.path == "/api/health":
                    body, code = health, 200
                elif self.path == "/api/owner/state":
                    body, code = {"busy": True}, 200
                else:
                    body, code = {"error": "nope"}, 404
                data = json.dumps(body).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()


def dash(monkeypatch, url):
    monkeypatch.setenv("JARVIS_CONSOLE_URL", url)
    plugin = object.__new__(JarvisBridgePlugin)
    return asyncio.run(JarvisBridgePlugin.dashboard(plugin))


def test_dashboard_shows_state_and_only_tasks_from_neko_never_reply_text(monkeypatch):
    c = Console()
    try:
        d = dash(monkeypatch, c.url)
    finally:
        c.close()
    assert d["ready"] is True and d["busy"] is True and d["kill_switch_on"] is False and d["message"] == "JARVIS is ready."
    assert [r["id"] for r in d["recent"]] == ["c-1", "c-3"]                      # the console-typed command is not N.E.K.O's
    assert d["recent"][0]["task"] == "find my tax PDFs"                            # whitespace collapsed
    assert len(d["recent"][1]["task"]) == 100                                       # long text is cut
    assert "PRIVATE REPLY TEXT" not in json.dumps(d)                                # replies stay in the JARVIS console
    assert d["console_url"] == c.url


def test_dashboard_reports_the_kill_switch(monkeypatch):
    c = Console(killed=True)
    try:
        d = dash(monkeypatch, c.url)
    finally:
        c.close()
    assert d["ready"] is False and d["kill_switch_on"] is True and "kill switch" in d["message"].lower()


def test_dashboard_when_the_console_is_not_running_says_so_instead_of_failing(monkeypatch):
    d = dash(monkeypatch, "http://127.0.0.1:9")
    assert d["ready"] is False and d["recent"] == [] and "console" in d["message"].lower()
