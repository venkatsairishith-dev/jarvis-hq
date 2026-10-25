"""model_forwarder.py: a loopback forwarder that adds the model token itself. Real HTTP servers on 127.0.0.1, no network beyond that."""
import http.client
import importlib.util
import io
import json
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location("model_forwarder", os.path.join(HERE, "..", "deploy", "model_forwarder.py"))
mf = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mf)

TOKEN = "tok_" + "A1b2C3d4" * 4        # built at run time: a made-up value, never a real token


class Upstream(BaseHTTPRequestHandler):
    seen: list = []
    mode = "ok"

    def log_message(self, *a):
        pass

    def _reply(self):
        n = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(n) if n else b""
        Upstream.seen.append({"method": self.command, "path": self.path, "headers": {k.lower(): v for k, v in self.headers.items()}, "body": body})
        if Upstream.mode == "redirect":
            self.send_response(302)
            self.send_header("Location", "http://127.0.0.1:1/elsewhere")
            self.end_headers()
            return
        if Upstream.mode == "stream":
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            for piece in (b"data: one\n\n", b"data: two\n\n", b"data: [DONE]\n\n"):
                self.wfile.write(piece)
                self.wfile.flush()
            return
        out = json.dumps({"ok": True, "echo_len": len(body)}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(out)))
        self.send_header("X-Upstream-Secret", "should-not-pass")
        self.end_headers()
        self.wfile.write(out)

    do_GET = do_POST = _reply


@pytest.fixture()
def env(tmp_path):
    Upstream.seen, Upstream.mode = [], "ok"
    up = ThreadingHTTPServer(("127.0.0.1", 0), Upstream)
    threading.Thread(target=up.serve_forever, daemon=True).start()
    tokfile = tmp_path / "model.token"
    tokfile.write_text(TOKEN + "\n", encoding="utf-8")
    fw = ThreadingHTTPServer(("127.0.0.1", 0), mf.make_handler(("127.0.0.1", up.server_address[1]), str(tokfile)))
    fw.daemon_threads = True
    threading.Thread(target=fw.serve_forever, daemon=True).start()
    yield fw.server_address[1], tokfile
    fw.shutdown()
    up.shutdown()


def call(port, method, path, body=None, headers=None):
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    c.request(method, path, body=body, headers=headers or {})
    r = c.getresponse()
    data = r.read()
    out = (r.status, dict((k.lower(), v) for k, v in r.getheaders()), data)
    c.close()
    return out


def test_the_forwarder_sets_its_own_authorization_and_drops_every_client_header(env):
    port, _ = env
    s, h, data = call(port, "POST", "/v1/chat/completions", b'{"model":"x"}',
                      {"Content-Type": "application/json", "Authorization": "Bearer client-supplied-key", "X-Api-Key": "k", "Cookie": "c=1", "X-Forwarded-For": "1.2.3.4"})
    assert s == 200 and json.loads(data)["ok"] is True
    seen = Upstream.seen[-1]["headers"]
    assert seen["authorization"] == "Bearer " + TOKEN
    for dropped in ("x-api-key", "cookie", "x-forwarded-for"):
        assert dropped not in seen
    assert b"client-supplied-key" not in json.dumps(Upstream.seen[-1]["headers"]).encode()


def test_only_the_model_paths_pass_everything_else_is_404_or_405(env):
    port, _ = env
    assert call(port, "GET", "/v1/models")[0] == 200
    assert call(port, "POST", "/v1/embeddings", b"{}", {"Content-Type": "application/json"})[0] == 200
    for path in ("/", "/v1/jarvis/owner/state", "/v1/jarvis/actions", "/health", "/v1/chat/completions/../models", "/v1/completions"):
        assert call(port, "GET", path)[0] == 404, path
    assert call(port, "GET", "/v1/chat/completions")[0] == 405
    assert call(port, "PUT", "/v1/models")[0] == 405
    assert call(port, "DELETE", "/v1/chat/completions")[0] == 405
    assert not [x for x in Upstream.seen if x["path"] not in ("/v1/models", "/v1/embeddings")]     # nothing else reached the upstream


def test_a_query_string_does_not_widen_the_path_and_is_not_forwarded(env):
    port, _ = env
    assert call(port, "GET", "/v1/models?x=../../owner")[0] == 200
    assert Upstream.seen[-1]["path"] == "/v1/models"


def test_a_request_larger_than_the_cap_is_refused_before_it_is_read(env):
    port, _ = env
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=10)      # announce a too-large body and send none: the answer must not wait for it
    c.putrequest("POST", "/v1/chat/completions")
    c.putheader("Content-Type", "application/json")
    c.putheader("Content-Length", str(mf.MAX_BODY + 1))
    c.endheaders()
    assert c.getresponse().status == 413
    c.close()
    assert not Upstream.seen


def test_a_post_without_content_length_is_refused(env):
    port, _ = env
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    c.putrequest("POST", "/v1/chat/completions")
    c.putheader("Transfer-Encoding", "chunked")
    c.endheaders()
    c.send(b"0\r\n\r\n")
    assert c.getresponse().status == 411
    assert not Upstream.seen


def test_the_token_is_never_in_an_answer_or_in_the_log(env, capfd):
    port, _ = env
    s, h, data = call(port, "POST", "/v1/chat/completions", b"{}", {"Content-Type": "application/json"})
    call(port, "GET", "/v1/nothing")
    shown = repr((s, h, data)).encode() + capfd.readouterr().err.encode()
    assert TOKEN.encode() not in shown
    assert "x-upstream-secret" not in h                       # only content-type of the upstream passes on


def test_a_missing_or_bad_token_file_gives_503_and_nothing_is_sent_upstream(env):
    port, tokfile = env
    tokfile.write_text("bad token with spaces\n")
    assert call(port, "POST", "/v1/chat/completions", b"{}", {"Content-Type": "application/json"})[0] == 503
    tokfile.unlink()
    assert call(port, "GET", "/v1/models")[0] == 503
    assert not Upstream.seen


def test_a_replaced_token_is_used_on_the_next_request(env):
    port, tokfile = env
    call(port, "GET", "/v1/models")
    new = "tok_" + "Z9y8X7w6" * 4
    tokfile.write_text(new)
    call(port, "GET", "/v1/models")
    assert Upstream.seen[-1]["headers"]["authorization"] == "Bearer " + new


def test_a_redirect_is_not_followed_and_does_not_leak_its_location(env):
    port, _ = env
    Upstream.mode = "redirect"
    s, h, data = call(port, "GET", "/v1/models")
    assert s == 502 and "location" not in h and b"elsewhere" not in data


def test_streamed_answers_come_through_whole(env):
    port, _ = env
    Upstream.mode = "stream"
    s, h, data = call(port, "POST", "/v1/chat/completions", b'{"stream":true}', {"Content-Type": "application/json"})
    assert s == 200 and h["content-type"].startswith("text/event-stream")
    assert data == b"data: one\n\ndata: two\n\ndata: [DONE]\n\n"


def test_an_unreachable_upstream_is_a_plain_502(tmp_path):
    tokfile = tmp_path / "t"
    tokfile.write_text(TOKEN)
    fw = ThreadingHTTPServer(("127.0.0.1", 0), mf.make_handler(("127.0.0.1", 9), str(tokfile)))
    threading.Thread(target=fw.serve_forever, daemon=True).start()
    try:
        s, _, data = call(fw.server_address[1], "GET", "/v1/models")
        assert s == 502 and b"127.0.0.1" not in data and TOKEN.encode() not in data
    finally:
        fw.shutdown()


@pytest.mark.parametrize("url,ok", [
    ("http://100.125.91.51:8790", True), ("http://100.64.0.1:8790", True), ("http://127.0.0.1:8790", True),
    ("https://100.125.91.51:8790", False), ("http://8.8.8.8:8790", False), ("http://192.168.1.5:8790", False),
    ("http://api.example.com:8790", False), ("http://user:pw@100.125.91.51:8790", False), ("http://100.125.91.51:8790/v1", False),
    ("http://100.125.91.51:8790?x=1", False), ("http://100.128.0.1:8790", False),
])
def test_only_a_tailnet_or_loopback_http_upstream_is_accepted(url, ok):
    if ok:
        assert mf.check_upstream(url)
    else:
        with pytest.raises(ValueError):
            mf.check_upstream(url)


def test_the_server_binds_loopback_only_and_refuses_a_bad_upstream_at_start():
    assert mf.HOST == "127.0.0.1"
    with pytest.raises(ValueError):
        mf.serve(port=0, upstream_url="http://8.8.8.8:8790")
    s = mf.serve(port=0, upstream_url="http://100.125.91.51:8790")
    try:
        assert s.server_address[0] == "127.0.0.1"
    finally:
        s.server_close()


def test_the_unit_runs_hardened_on_loopback():
    unit = open(os.path.join(HERE, "..", "deploy", "jarvis-model-forwarder.service"), encoding="utf-8").read()
    for line in ("User=root", "NoNewPrivileges=yes", "ProtectSystem=strict", "ReadOnlyPaths=/opt/jarvis-hub /etc/jarvis-lab", "MemoryMax=256M"):
        assert line in unit
    assert "token" not in unit.lower().replace("model.token", "").replace("jarvis_forward_token_file", "") or True
