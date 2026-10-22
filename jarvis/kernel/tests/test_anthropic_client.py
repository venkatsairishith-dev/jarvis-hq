import json
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from jarvis_kernel.router.anthropic_client import (AnthropicClient, ClientError,
                                                   UpstreamHTTP)
from jarvis_kernel.router.translate import UpstreamError

KEY = "sk-ant-api03-SECRETKEYVALUE123"


def sse(*events):
    return "".join(f"event: {n}\ndata: {json.dumps(d)}\n\n" for n, d in events).encode()


TRANSCRIPT = sse(
    ("message_start", {"type": "message_start", "message": {"id": "msg_1", "model": "claude-x", "usage": {"input_tokens": 5, "output_tokens": 1}}}),
    ("content_block_start", {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}}),
    ("content_block_delta", {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "Hel"}}),
    ("ping", {"type": "ping"}),
    ("content_block_delta", {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "lo"}}),
    ("content_block_stop", {"type": "content_block_stop", "index": 0}),
    ("content_block_start", {"type": "content_block_start", "index": 1, "content_block": {"type": "tool_use", "id": "tu_1", "name": "get", "input": {}}}),
    ("content_block_delta", {"type": "content_block_delta", "index": 1, "delta": {"type": "input_json_delta", "partial_json": "{\"a\":"}}),
    ("content_block_delta", {"type": "content_block_delta", "index": 1, "delta": {"type": "input_json_delta", "partial_json": "1}"}}),
    ("content_block_stop", {"type": "content_block_stop", "index": 1}),
    ("message_delta", {"type": "message_delta", "delta": {"stop_reason": "tool_use"}, "usage": {"output_tokens": 9}}),
    ("message_stop", {"type": "message_stop"}),
)


class Fake:
    """Fake upstream; `respond` is set per test as fn(handler) -> None."""

    def __init__(self):
        self.requests = []
        outer = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_POST(self):
                n = int(self.headers.get("content-length") or 0)
                raw = self.rfile.read(n)
                outer.requests.append({"path": self.path, "headers": {k.lower(): v for k, v in self.headers.items()},
                                       "body": json.loads(raw or b"{}")})
                outer.respond(self)

        self.respond = lambda h: None
        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.url = f"http://127.0.0.1:{self.srv.server_address[1]}"
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def close(self):
        self.srv.shutdown()
        self.srv.server_close()


def send(h, status, body, headers=None, ctype="application/json"):
    h.send_response(status)
    h.send_header("content-type", ctype)
    h.send_header("content-length", str(len(body)))
    for k, v in (headers or {}).items():
        h.send_header(k, v)
    h.end_headers()
    h.wfile.write(body)


BODY = {"model": "claude-x", "messages": [{"role": "system", "content": "be nice"},
                                          {"role": "user", "content": "hi"}], "temperature": 0.5}


class T(unittest.TestCase):
    def setUp(self):
        self.f = Fake()
        self.addCleanup(self.f.close)
        self.c = AnthropicClient(base_url=self.f.url, timeout_s=5)

    def assertNoKey(self, e):
        for s in (str(e), repr(e), repr(e.args), repr(vars(e))):
            self.assertNotIn("SECRETKEY", s)

    def test_chat_headers_body_response(self):
        reply = {"id": "m1", "content": [{"type": "text", "text": "yo"}], "stop_reason": "end_turn",
                 "usage": {"input_tokens": 2, "output_tokens": 3}}
        self.f.respond = lambda h: send(h, 200, json.dumps(reply).encode())
        out = self.c.chat(BODY, KEY)
        r = self.f.requests[0]
        self.assertEqual(r["path"], "/v1/messages")
        self.assertEqual(r["headers"]["x-api-key"], KEY)
        self.assertNotIn("authorization", r["headers"])
        self.assertNotIn("anthropic-workspace-id", r["headers"])
        self.assertEqual(r["headers"]["anthropic-version"], "2023-06-01")
        self.assertEqual(r["body"]["system"], "be nice")
        self.assertEqual(r["body"]["messages"], [{"role": "user", "content": [{"type": "text", "text": "hi"}]}])
        self.assertNotIn("temperature", r["body"])
        self.assertNotIn("stream", r["body"])
        self.assertEqual(out["choices"][0]["message"]["content"], "yo")
        self.assertEqual(out["usage"]["total_tokens"], 5)

    def test_sampling_kept_when_requested(self):
        self.f.respond = lambda h: send(h, 200, b'{"content":[]}')
        self.c.chat(BODY, KEY, drop_sampling=False)
        self.assertEqual(self.f.requests[0]["body"]["temperature"], 0.5)

    def test_workspace_header(self):
        self.f.respond = lambda h: send(h, 200, b'{"content":[]}')
        AnthropicClient(self.f.url, workspace_id="wrk_1").chat(BODY, KEY)
        self.assertEqual(self.f.requests[0]["headers"]["anthropic-workspace-id"], "wrk_1")

    def test_429_retry_after(self):
        self.f.respond = lambda h: send(h, 429, b'{"error":{"type":"rate_limit_error","message":"slow"}}',
                                        {"retry-after": "7"})
        with self.assertRaises(UpstreamHTTP) as cm:
            self.c.chat(BODY, KEY)
        self.assertEqual((cm.exception.status, cm.exception.kind, cm.exception.retry_after),
                         (429, "rate_limit_error", 7.0))
        self.assertNoKey(cm.exception)

    def test_500(self):
        self.f.respond = lambda h: send(h, 500, b"boom")
        with self.assertRaises(UpstreamHTTP) as cm:
            self.c.chat(BODY, KEY)
        self.assertEqual(cm.exception.status, 500)
        self.assertIsNone(cm.exception.retry_after)

    def test_error_body_echoing_key_scrubbed(self):
        body = json.dumps({"error": {"type": "authentication_error " + KEY, "message": "bad " + KEY}}).encode()
        self.f.respond = lambda h: send(h, 401, body)
        with self.assertRaises(UpstreamHTTP) as cm:
            self.c.chat(BODY, KEY)
        self.assertNoKey(cm.exception)
        self.assertIn("[KEY]", cm.exception.kind)
        self.assertLessEqual(len(cm.exception.kind), 200)

    def test_redirect_not_followed(self):
        other = Fake()
        self.addCleanup(other.close)
        self.f.respond = lambda h: send(h, 301, b"", {"location": other.url + "/v1/messages"})
        with self.assertRaises(UpstreamHTTP) as cm:
            self.c.chat(BODY, KEY)
        self.assertEqual(cm.exception.status, 301)
        self.assertEqual(other.requests, [])

    def test_unsupported_refused_before_request(self):
        bad = dict(BODY, response_format={"type": "json_object"})
        with self.assertRaises(ClientError):
            self.c.chat(bad, KEY)
        with self.assertRaises(ClientError):
            self.c.chat_stream(bad, KEY)
        self.assertEqual(self.f.requests, [])

    def test_stream_transcript(self):
        self.f.respond = lambda h: send(h, 200, TRANSCRIPT, ctype="text/event-stream")
        chunks = list(self.c.chat_stream(dict(BODY, stream=True), KEY))
        self.assertTrue(self.f.requests[0]["body"]["stream"])
        self.assertEqual(chunks[-1], "data: [DONE]\n\n")
        objs = [json.loads(c[6:]) for c in chunks[:-1]]
        deltas = [o["choices"][0]["delta"] for o in objs]
        self.assertEqual(deltas[0], {"role": "assistant", "content": ""})
        self.assertEqual("".join(d.get("content", "") for d in deltas), "Hello")
        self.assertEqual(deltas[3]["tool_calls"][0]["id"], "tu_1")
        args = "".join(d["tool_calls"][0]["function"]["arguments"] for d in deltas if "tool_calls" in d)
        self.assertEqual(args, '{"a":1}')
        self.assertEqual(objs[-1]["choices"][0]["finish_reason"], "tool_calls")
        self.assertEqual(objs[-1]["usage"]["completion_tokens"], 9)

    def test_stream_error_event(self):
        ev = sse(("message_start", {"type": "message_start", "message": {"id": "m"}}),
                 ("error", {"type": "error", "error": {"type": "overloaded_error", "message": "busy " + KEY}}))
        self.f.respond = lambda h: send(h, 200, ev, ctype="text/event-stream")
        with self.assertRaises(UpstreamError) as cm:
            list(self.c.chat_stream(BODY, KEY))
        self.assertNoKey(cm.exception)

    def test_stream_closed_midway(self):
        def partial(h):
            h.send_response(200)
            h.send_header("content-type", "text/event-stream")
            h.send_header("content-length", "100000")
            h.end_headers()
            h.wfile.write(TRANSCRIPT[:300])
            h.wfile.flush()
            h.close_connection = True
        self.f.respond = partial
        with self.assertRaises(UpstreamError) as cm:
            list(self.c.chat_stream(BODY, KEY))
        self.assertNoKey(cm.exception)

    def test_stream_ends_without_stop(self):
        self.f.respond = lambda h: send(h, 200, TRANSCRIPT[:200].rsplit(b"\n\n", 1)[0] + b"\n\n",
                                        ctype="text/event-stream")
        with self.assertRaises(UpstreamError):
            list(self.c.chat_stream(BODY, KEY))

    def test_stream_http_error(self):
        self.f.respond = lambda h: send(h, 529, b'{"error":{"type":"overloaded_error"}}')
        with self.assertRaises(UpstreamHTTP) as cm:
            list(self.c.chat_stream(BODY, KEY))
        self.assertEqual(cm.exception.kind, "overloaded_error")

    def test_repr_has_no_key(self):
        self.assertEqual(repr(self.c), f"AnthropicClient(base_url={self.f.url!r})")
        self.assertNotIn("SECRETKEY", repr(vars(self.c)))

    def test_connection_refused(self):
        c = AnthropicClient("http://127.0.0.1:1", timeout_s=2)
        with self.assertRaises(UpstreamError) as cm:
            c.chat(BODY, KEY)
        self.assertNoKey(cm.exception)


if __name__ == "__main__":
    unittest.main()
