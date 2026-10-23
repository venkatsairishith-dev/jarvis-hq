"""Re-review M13: calls to a loopback service must not honour http_proxy / https_proxy / ALL_PROXY.

A RECORDING PROXY (a real local HTTP server) is put in every proxy variable, NO_PROXY is cleared, and each call site below talks to
a real loopback fake of its service. The proxy must see NOTHING; the service must see the request. Baseline: the same hazard is shown
with plain urllib.request.urlopen on this platform (the old code), so the test would fail on the old call sites. Synthetic data only."""
from __future__ import annotations

import importlib.util
import json
import sys
import threading
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from jarvis.plugins._shared import loopback_http as lh
from jarvis.plugins._shared import screen_text as st
from jarvis.plugins._shared.tests.fakes import FakeGateway

ROOT = Path(__file__).resolve().parents[4]
CANARY = "CANARY-7f3a-Priyanka-SYNTH"


class Recorder(BaseHTTPRequestHandler):
    log: list = []

    def log_message(self, *a):
        pass

    def _answer(self):
        n = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(n).decode("utf-8", "replace") if n else ""
        self.log.append((self.command, self.path, body, self.headers.get("Host")))
        raw = json.dumps({"ok": True, "text": "x", "choices": [{"message": {"content": "{}"}}]}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)
    do_GET = do_POST = _answer


def serve(handler_cls=Recorder):
    handler = type("H", (handler_cls,), {"log": []})
    srv = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, handler.log


@pytest.fixture
def proxy(monkeypatch):
    """Every proxy variable points at a recording proxy; nothing excuses loopback."""
    srv, log = serve()
    url = f"http://127.0.0.1:{srv.server_address[1]}"
    for k in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
        monkeypatch.setenv(k, url)
    for k in ("NO_PROXY", "no_proxy"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.delenv(st.FLAG, raising=False)
    yield log
    srv.shutdown()
    srv.server_close()


@pytest.fixture
def service():
    srv, log = serve()
    yield f"http://127.0.0.1:{srv.server_address[1]}", log
    srv.shutdown()
    srv.server_close()


def test_the_old_call_would_have_gone_to_the_proxy(proxy):
    """The hazard on this platform: plain urllib sends a loopback request to the proxy when no_proxy does not list it."""
    srv, log = serve()
    try:
        # a stock urllib opener built while the proxy variables are set: the PROXY gets the request (this is what every old call site
        # did; `urllib.request.urlopen` caches ONE global opener at its first use in the process, so a test that depends on the
        # environment must build a fresh opener instead of relying on whichever test called urlopen first)
        opener = urllib.request.build_opener(urllib.request.ProxyHandler())
        with opener.open(f"http://127.0.0.1:{srv.server_address[1]}/x?{CANARY}", timeout=5):
            pass
    finally:
        srv.shutdown()
        srv.server_close()
    assert any(CANARY in p for _, p, _, _ in proxy), "urllib ignored the proxy settings here, so this platform does not show the hazard"
    assert log == []


def test_the_helper_bypasses_the_proxy(proxy, service):
    base, log = service
    req = urllib.request.Request(base + "/redact", data=json.dumps({"text": CANARY}).encode(), headers={"Content-Type": "application/json"})
    with lh.urlopen(req, timeout=5) as r:
        assert json.loads(r.read())["ok"] is True
    assert proxy == [] and len(log) == 1 and CANARY in log[0][2]


@pytest.mark.parametrize("url", ["http://example.com/x", "https://127.0.0.2/x", "http://10.0.0.5:8080/x", "http://127.0.0.1.evil.example/x",
                                 "http://user:pw@127.0.0.1:8080/x", "http://127.0.0.1@evil.example/x", "ftp://127.0.0.1/x", "file:///etc/passwd",
                                 "http://[::2]/x", "http://localhost.evil.example/x", "127.0.0.1:8080/x", "", "http://", "http://127.0.0.1:99999999/x"])
def test_a_non_loopback_address_is_refused_before_any_socket_is_opened(proxy, url):
    with pytest.raises(lh.LoopbackError) as e:
        lh.urlopen(url, timeout=1)
    assert "example" not in str(e.value) and "evil" not in str(e.value)       # the message never repeats the address
    assert proxy == []


@pytest.mark.parametrize("url", ["http://127.0.0.1:1/x", "http://localhost:1/x", "http://[::1]:1/x", "https://127.0.0.1:1/x"])
def test_plain_loopback_addresses_pass_the_check(url):
    assert lh.check_loopback(url) == url


def test_a_redirect_is_not_followed(proxy):
    other, other_log = serve()

    class Redirector(Recorder):
        def _answer(self):
            self.send_response(302)
            self.send_header("Location", f"http://127.0.0.1:{other.server_address[1]}/stolen")
            self.send_header("Content-Length", "0")
            self.end_headers()
        do_GET = do_POST = _answer
    first, _ = serve(Redirector)
    try:
        with pytest.raises(urllib.error.HTTPError) as e:
            lh.urlopen(f"http://127.0.0.1:{first.server_address[1]}/x", timeout=5)
        assert e.value.code == 302
    finally:
        for s in (first, other):
            s.shutdown()
            s.server_close()
    assert other_log == [] and proxy == []


def test_the_httpx_client_ignores_proxy_settings_and_checks_its_base(proxy, service):
    import asyncio
    base, log = service

    async def go():
        async with lh.loopback_client(base) as c:
            assert (await c.post(base + "/x", json={"t": CANARY})).status_code == 200
    asyncio.run(go())
    assert proxy == [] and len(log) == 1
    with pytest.raises(lh.LoopbackError):
        lh.loopback_client("http://example.com")


# ------------------------------------------------------------------ every call site
def test_screen_text_gateway_calls(proxy, monkeypatch):
    g = FakeGateway()
    try:
        monkeypatch.setattr(st, "_gateway_base", lambda: f"http://127.0.0.1:{g.port}")
        assert st.gateway_state() == (True, "")
        assert st.gateway_redact(f"hello {CANARY}") == f"hello {CANARY}"
    finally:
        g.close()
    assert proxy == [] and g.redact_requests and g.health_requests


def test_research_gateway_calls(proxy, monkeypatch):
    from jarvis.plugins import research as r
    g = FakeGateway()
    try:
        monkeypatch.setattr(r, "GATEWAY_HEALTH", f"http://127.0.0.1:{g.port}/health")
        monkeypatch.setattr(r, "GATEWAY_REDACT", f"http://127.0.0.1:{g.port}/redact")
        assert r._gateway_state() == (True, "")
        assert r._redact(f"hello {CANARY}") == f"hello {CANARY}"
    finally:
        g.close()
    assert proxy == [] and g.redact_requests


def test_local_team_calls(proxy, service, monkeypatch):
    from jarvis.plugins import local_team as lt
    from jarvis.plugins.local_team import core
    base, log = service
    core.post_json(base + "/v1/chat/completions", {"messages": [{"role": "user", "content": CANARY}]})
    core.get_json(base + "/health")
    try:
        core.post_stream(base + "/v1/chat/completions", {"messages": []})
    except core.TeamError:
        pass                                              # the fake answers JSON, not SSE: only where the request went matters
    monkeypatch.setattr(lt, "GATEWAY_PREVIEW", base + "/redact")
    lt._gateway_preview(CANARY)
    assert proxy == [] and len(log) == 4
    with pytest.raises(core.TeamError):
        core.post_json("http://example.com/v1/chat/completions", {"x": CANARY})
    assert proxy == []


class FakeConsole(Recorder):
    """The console's page carries its secret in a meta tag (the old way), the rest answers JSON."""

    def _answer(self):
        if self.command == "GET" and self.path == "/":
            self.log.append(("GET", "/", "", self.headers.get("Host")))
            raw = b'<html><head><meta name="jarvis-console-secret" content="tok"></head></html>'
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)
            return
        super()._answer()
    do_GET = do_POST = _answer


def test_desktop_console_and_decider_calls(proxy, service, monkeypatch, tmp_path):
    from jarvis.plugins.desktop import core, decide
    console, clog = serve(FakeConsole)
    base, log = service
    try:
        monkeypatch.setattr(core, "CONSOLE", f"http://127.0.0.1:{console.server_address[1]}")
        monkeypatch.setenv("JARVIS_RUN_DIR", str(tmp_path))                  # no console.token file: the page is read
        core._SECRETS.clear()
        status, body = core.owner("GET", "state")
        assert status == 200
        try:
            decide.ask_model(f"open {CANARY}", url=base + "/v1/chat/completions")
        except Exception:  # noqa: BLE001 - the fake answers a bare JSON; only where the request went matters
            pass
    finally:
        console.shutdown()
        console.server_close()
        core._SECRETS.clear()
    assert proxy == [], proxy
    assert any(p == "/" for _, p, _, _ in clog) and any("/api/owner/state" in p for _, p, _, _ in clog)
    assert any(CANARY in b for _, _, b, _ in log)


def _load(name, rel, extra_path=None):
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    mod = importlib.util.module_from_spec(spec)
    if extra_path:
        sys.path.insert(0, str(ROOT / extra_path))
    try:
        spec.loader.exec_module(mod)
    finally:
        if extra_path:
            sys.path.remove(str(ROOT / extra_path))
    return mod


def test_node_vision_call(proxy, service, monkeypatch, tmp_path):
    base, log = service
    sys.path.insert(0, str(ROOT / "jarvis" / "node"))
    try:
        from jarvis_node import vision
    finally:
        sys.path.remove(str(ROOT / "jarvis" / "node"))
    monkeypatch.setattr(vision, "VISION_URL", base + "/v1/chat/completions")
    png = tmp_path / "s.png"
    png.write_bytes(b"\x89PNG\r\n\x1a\nCANARYPIXELS")
    vision.ask_vision(str(png), "what is this")
    assert proxy == [] and len(log) == 1 and "image_url" in log[0][2]


def test_the_bridge_client_and_main_agent_clients_use_trust_env_false(proxy, service, monkeypatch):
    """jarvis_bridge.client and main_agent already built their httpx clients with trust_env=False; this pins it with a real proxy."""
    import asyncio
    import httpx
    from jarvis.plugins.jarvis_bridge import client as bridge
    base, log = service
    monkeypatch.setattr(bridge, "_read_token_file", lambda *a, **k: "tok", raising=False)
    c = bridge.JarvisClient(base)

    async def go():
        try:
            await c.commands(1)
        except bridge.BridgeError:
            pass
    asyncio.run(go())
    assert proxy == [] and any("/api/owner/commands" in p for _, p, _, _ in log)
    src = (ROOT / "jarvis/plugins/main_agent/__init__.py").read_text(encoding="utf-8")
    assert src.count("httpx.AsyncClient(") == src.count("trust_env=False"), "every main_agent httpx client must ignore proxy settings"
    assert httpx  # imported for the transport assumption above
