import hashlib
import json
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from jarvis_kernel.router import passthrough as pt

KEY = "sk-ant-api03-REALKEY_abc123-xyz"
SSE = [b"event: message_start\ndata: {\"type\":\"message_start\"}\n\n",
       b"event: ping\ndata: {\"type\": \"ping\"}\n\n",
       b"event: content_block_delta\ndata: {\"type\":\"content_block_delta\",\"delta\":{\"text\":\"h\\u00e9llo\"}}\n\n",
       b"event: ping\ndata: {\"type\": \"ping\"}\n\n",
       b"event: message_stop\ndata: {\"type\":\"message_stop\"}\n\n"]


class Upstream(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self):
        super().__init__(("127.0.0.1", 0), H)
        self.seen = []                      # (path, headers list, body bytes)
        self.disconnected = threading.Event()
        threading.Thread(target=self.serve_forever, daemon=True).start()

    @property
    def url(self):
        return f"http://127.0.0.1:{self.server_address[1]}"


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_POST(self):
        body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
        self.server.seen.append((self.path, [(k.lower(), v) for k, v in self.headers.items()], body))
        if self.path.startswith("/redir"):
            self.send_response(302)
            self.send_header("Location", "/elsewhere")
            self.send_header("Content-Length", "0")
            self.end_headers()
        elif self.path == "/v1/messages" and b"RATELIMIT" in body:
            msg = json.dumps({"error": {"message": "bad key " + KEY}}).encode()
            self.send_response(429)
            self.send_header("Content-Type", "application/json")
            self.send_header("retry-after", "7")
            self.send_header("anthropic-ratelimit-requests-remaining", "0")
            self.send_header("set-cookie", "x=1")
            self.send_header("Content-Length", str(len(msg)))
            self.end_headers()
            self.wfile.write(msg)
        elif b"FOREVER" in body:
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("request-id", "req_1")
            self.end_headers()
            try:
                for _ in range(200):
                    self.wfile.write(SSE[1])
                    self.wfile.flush()
                    time.sleep(0.05)
            except OSError:
                self.server.disconnected.set()
        else:
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("request-id", "req_1")
            self.end_headers()
            for ev in SSE:
                self.wfile.write(ev)
                self.wfile.flush()
                time.sleep(0.1)


class PassthroughTests(unittest.TestCase):
    def setUp(self):
        self.up = Upstream()
        self.addCleanup(self.up.server_close)
        self.addCleanup(self.up.shutdown)

    def fwd(self, body, headers=None, fn=pt.forward_messages, **kw):
        return fn(body, headers or {}, KEY, base_url=self.up.url, **kw)

    def test_body_bytes_identical(self):
        bodies = [b'{\n  "model" :  "claude-x",\t"messages":[ {"role":"user",\r\n"content":"hi"} ]  }\n\n',
                  '{"z":1,"a":{"y":2,"b":3},"text":"héllo ☃ \U0001F600 \\u00e9"}'.encode("utf-8"),
                  b'{"b":1,"a":2,"b":3}',
                  b"\xff\xfe not json at all \x00\x01{{{",
                  b""]
        for body in bodies:
            p = self.fwd(body)
            got = b"".join(p.iter_body())
            self.assertEqual(p.status, 200)
            path, _, recv = self.up.seen[-1]
            self.assertEqual(path, "/v1/messages")
            self.assertEqual(hashlib.sha256(recv).hexdigest(), hashlib.sha256(body).hexdigest())
            self.assertEqual(recv, body)
            self.assertEqual(p.sent_sha256, hashlib.sha256(body).hexdigest())
            self.assertTrue(got)

    def test_credentials_and_header_allow_list(self):
        hdrs = {"X-Api-Key": "caller-key", "Authorization": "Bearer caller-token", "Cookie": "s=1", "X-Forwarded-For": "9.9.9.9",
                "Host": "evil.example", "anthropic-version": "2024-01-01", "Anthropic-Beta": "tools-2024",
                "Content-Type": "application/json", "Accept": "text/event-stream", "User-Agent": "claude-cli/1"}
        b"".join(self.fwd(b"{}", hdrs, workspace_id="wrk_1").iter_body())
        names = dict(self.up.seen[-1][1])
        self.assertEqual(names["x-api-key"], KEY)
        for bad in ("authorization", "cookie", "x-forwarded-for"):
            self.assertNotIn(bad, names)
        self.assertTrue(names["host"].startswith("127.0.0.1:"))
        flat = json.dumps(self.up.seen[-1][1])
        for s in ("caller-key", "caller-token", "evil.example", "9.9.9.9"):
            self.assertNotIn(s, flat)
        self.assertEqual(names["anthropic-version"], "2024-01-01")
        self.assertEqual(names["anthropic-beta"], "tools-2024")
        self.assertEqual(names["content-type"], "application/json")
        self.assertEqual(names["accept"], "text/event-stream")
        self.assertEqual(names["user-agent"], "claude-cli/1")
        self.assertEqual(names["anthropic-workspace-id"], "wrk_1")

    def test_version_default_and_no_workspace(self):
        b"".join(self.fwd(b"{}").iter_body())
        names = dict(self.up.seen[-1][1])
        self.assertEqual(names["anthropic-version"], "2023-06-01")
        self.assertNotIn("anthropic-workspace-id", names)

    def test_sse_relayed_exactly_in_several_chunks(self):
        p = self.fwd(b"{}")
        chunks = list(p.iter_body())
        self.assertGreater(len(chunks), 1)
        self.assertEqual(b"".join(chunks), b"".join(SSE))
        self.assertEqual(b"".join(chunks).count(b"event: ping"), 2)
        self.assertEqual(p.headers["content-type"], "text/event-stream")
        self.assertEqual(p.headers["request-id"], "req_1")

    def test_error_body_scrubbed_and_headers_filtered(self):
        p = self.fwd(b'{"x":"RATELIMIT"}')
        body = b"".join(p.iter_body())
        self.assertEqual(p.status, 429)
        self.assertNotIn(KEY.encode(), body)
        self.assertNotIn(b"sk-ant-", body)
        self.assertIn(b"[KEY]", body)
        self.assertEqual(p.headers["retry-after"], "7")
        self.assertEqual(p.headers["anthropic-ratelimit-requests-remaining"], "0")
        self.assertNotIn("set-cookie", p.headers)

    def test_redirect_not_followed(self):
        p = pt.forward_messages(b"{}", {}, KEY, base_url=self.up.url + "/redir")
        b"".join(p.iter_body())
        self.assertEqual(p.status, 302)
        self.assertNotIn("location", p.headers)
        self.assertEqual([s[0] for s in self.up.seen], ["/redir/v1/messages"])

    def test_early_close_closes_upstream(self):
        p = self.fwd(b'{"x":"FOREVER"}')
        g = p.iter_body()
        self.assertTrue(next(g).startswith(b"event: ping"))
        g.close()
        self.assertTrue(self.up.disconnected.wait(5))

    def test_count_tokens(self):
        p = self.fwd(b'{ "a" : 1 }', fn=pt.forward_count_tokens)
        b"".join(p.iter_body())
        self.assertEqual(self.up.seen[-1][0], "/v1/messages/count_tokens")
        self.assertEqual(self.up.seen[-1][2], b'{ "a" : 1 }')
        self.assertEqual(dict(self.up.seen[-1][1])["x-api-key"], KEY)

    def test_key_never_in_repr_or_errors(self):
        p = self.fwd(b"{}")
        for s in (repr(p), str(p)):
            self.assertNotIn(KEY, s)
            self.assertNotIn("sk-ant", s)
            self.assertIn(self.up.url, s)
        b"".join(p.iter_body())
        with self.assertRaises(pt.PassthroughError) as cm:
            pt.forward_messages(b"{}", {}, KEY, base_url="http://127.0.0.1:1", timeout_s=2)
        self.assertNotIn(KEY, str(cm.exception) + repr(cm.exception))
        with self.assertRaises(ValueError) as cm:
            pt.forward_messages(b"{}", {}, KEY + "\nX: y", base_url=self.up.url)
        self.assertNotIn("REALKEY", str(cm.exception))


if __name__ == "__main__":
    unittest.main()


class ErrorBodyCap(unittest.TestCase):
    def test_a_huge_error_body_is_cut_at_the_cap_before_scrubbing(self):
        from jarvis_kernel.router import passthrough as P

        class Resp:
            status = 500

            def getheaders(self):
                return []

            def read(self, n=-1):
                return b"x" * (n if n > 0 else 50_000_000)

            def close(self):
                pass

        pt = P.Passthrough("https://x", "k" * 12, Resp(), Resp(), "0" * 64)
        body = b"".join(pt.iter_body())
        self.assertEqual(len(body), P.MAX_ERROR_BODY)
