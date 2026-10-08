import json
import os
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(__file__)
sys.path.insert(0, os.path.join(HERE, ".."))
from jarvis_kernel.gateway import Provider
from jarvis_kernel.llm_proxy import MAX_OUT_TOKENS, PER_MINUTE, make_handler
from jarvis_kernel.store import Kernel

TOOL_REPLY = {"id": "x", "object": "chat.completion", "choices": [{"index": 0, "finish_reason": "tool_calls", "message": {
    "role": "assistant", "content": None, "tool_calls": [{"id": "call_1", "type": "function", "function": {"name": "search", "arguments": "{\"q\": \"weather\"}"}}]}}]}
SSE = [b'data: {"choices":[{"delta":{"content":"Hel"}}]}\n\n', b'data: {"choices":[{"delta":{"content":"lo"}}]}\n\n', b"data: [DONE]\n\n"]


def upstream(name, seen, status=200):
    class U(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.0"
        def log_message(self, *a): pass
        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            seen.append((name, body, self.headers.get("Authorization")))
            if status != 200:
                self.send_response(status); self.send_header("Content-Length", "0"); self.end_headers(); return
            if body.get("stream"):
                self.send_response(200); self.send_header("Content-Type", "text/event-stream"); self.end_headers()
                for c in SSE: self.wfile.write(c); self.wfile.flush()
                return
            out = json.dumps(TOOL_REPLY).encode()
            self.send_response(200); self.send_header("Content-Type", "application/json"); self.send_header("Content-Length", str(len(out))); self.end_headers(); self.wfile.write(out)
    s = ThreadingHTTPServer(("127.0.0.1", 0), U); threading.Thread(target=s.serve_forever, daemon=True).start()
    return s, f"http://127.0.0.1:{s.server_address[1]}/v1"


class P(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.k = Kernel(os.path.join(self.tmp.name, "d")); self.seen = []
        self.keyf = os.path.join(self.tmp.name, "groq.key"); open(self.keyf, "w").write("gsk_PROVIDERKEY_never_shown_1234567890")
        self.up, self.up_url = upstream("good", self.seen)
        self.bad, self.bad_url = upstream("bad", self.seen, status=500)
        self.now = [1000.0]
        self.servers = []

    def tearDown(self):
        for s in [self.up, self.bad] + self.servers: s.shutdown()
        self.k.db.close(); self.tmp.cleanup()

    def proxy(self, providers):
        H, tokens = make_handler(self.k, providers, clock=lambda: self.now[0])
        s = ThreadingHTTPServer(("127.0.0.1", 0), H); threading.Thread(target=s.serve_forever, daemon=True).start(); self.servers.append(s)
        self.url = f"http://127.0.0.1:{s.server_address[1]}"; self.tok = tokens.issue("hermes")
        return s

    def good(self, **kw):
        return Provider("groq", "private", self.up_url, self.keyf, ["any"], default_model="openai/gpt-oss-120b", **kw)

    def call(self, body, tok=None, path="/v1/chat/completions"):
        r = urllib.request.Request(self.url + path, data=json.dumps(body).encode(), method="POST", headers={"Content-Type": "application/json"})
        r.add_header("Authorization", "Bearer " + (tok if tok is not None else self.tok))
        resp = urllib.request.urlopen(r, timeout=10)
        return resp.read(), resp.headers.get("Content-Type")

    def test_token_required(self):
        self.proxy([self.good()])
        for tok in ("", "wrong"):
            with self.assertRaises(urllib.error.HTTPError) as cm: self.call({"messages": []}, tok=tok)
            self.assertEqual(cm.exception.code, 401)
        r = urllib.request.Request(self.url + "/v1/models", headers={"Authorization": "Bearer " + self.tok})
        self.assertEqual(json.loads(urllib.request.urlopen(r).read())["data"][0]["id"], "jarvis")

    def test_tool_calls_relayed_unchanged_and_the_key_is_added_by_the_proxy(self):
        self.proxy([self.good()])
        tools = [{"type": "function", "function": {"name": "search", "parameters": {"type": "object", "properties": {"q": {"type": "string"}}}}}]
        out, ctype = self.call({"model": "whatever-the-agent-asks", "messages": [{"role": "user", "content": "hi"}], "tools": tools})
        self.assertEqual(json.loads(out), TOOL_REPLY)
        name, body, auth = self.seen[-1]
        self.assertEqual(auth, "Bearer gsk_PROVIDERKEY_never_shown_1234567890")       # the agent never had this
        self.assertEqual(body["model"], "openai/gpt-oss-120b"); self.assertEqual(body["tools"], tools)
        self.assertNotIn(b"PROVIDERKEY", out)
        rows = " ".join(r[0] for r in self.k.db.execute("SELECT payload FROM audit"))
        self.assertNotIn("PROVIDERKEY", rows); self.assertNotIn(self.tok, rows)

    def test_secrets_scrubbed_in_messages_and_tool_arguments(self):
        self.proxy([self.good()])
        self.call({"messages": [{"role": "user", "content": "token gho_abcdefghijklmnopqrstuvwxyz0123"},
                                {"role": "user", "content": [{"type": "text", "text": "API_KEY=hunter2hunter2"}]},
                                {"role": "assistant", "content": None, "tool_calls": [{"id": "c", "type": "function", "function": {"name": "f", "arguments": "{\"k\": \"sk-abcdefghijklmnopqrstuv123\"}"}}]}]})
        sent = json.dumps(self.seen[-1][1])
        for bad in ("gho_abc", "hunter2", "sk-abcdefghij"): self.assertNotIn(bad, sent)

    def test_private_key_blocks_the_request(self):
        self.proxy([self.good()])
        with self.assertRaises(urllib.error.HTTPError) as cm: self.call({"messages": [{"role": "user", "content": "-----BEGIN OPENSSH PRIVATE KEY-----\nabc"}]})
        self.assertEqual(cm.exception.code, 400); self.assertEqual(self.seen, [])

    def test_agent_reasoning_settings_are_replaced_by_the_provider_entry(self):
        self.proxy([self.good(extra_body={"reasoning_effort": "low"})])
        self.call({"messages": [{"role": "user", "content": "x"}], "reasoning_effort": {"enabled": True, "effort": "medium"}, "reasoning": {"effort": "high"}})
        body = self.seen[-1][1]
        self.assertEqual(body["reasoning_effort"], "low"); self.assertNotIn("reasoning", body)
        rows = [r[0] for r in self.k.db.execute("SELECT payload FROM audit WHERE kind='agent_model_call'")]
        self.assertIn("prompt_chars", rows[-1])

    def test_output_tokens_capped(self):
        self.proxy([self.good()])
        self.call({"messages": [{"role": "user", "content": "x"}], "max_tokens": 100000}); self.assertEqual(self.seen[-1][1]["max_tokens"], MAX_OUT_TOKENS)
        self.call({"messages": [{"role": "user", "content": "x"}]}); self.assertEqual(self.seen[-1][1]["max_tokens"], MAX_OUT_TOKENS)
        self.call({"messages": [{"role": "user", "content": "x"}], "max_completion_tokens": 50}); self.assertEqual(self.seen[-1][1]["max_completion_tokens"], 50)

    def test_streaming_is_relayed_chunk_for_chunk(self):
        self.proxy([self.good()])
        out, ctype = self.call({"messages": [{"role": "user", "content": "hi"}], "stream": True})
        self.assertEqual(out, b"".join(SSE)); self.assertIn("text/event-stream", ctype)

    def test_fallback_stays_in_the_private_lane_and_honours_the_free_model_guard(self):
        bad = Provider("bad", "private", self.bad_url, None, ["any"], priority=1)
        paid = Provider("or", "private", self.up_url, None, ["any"], priority=2, default_model="anthropic/claude-expensive", model_pattern=":free$")
        pub = Provider("pub", "public", self.up_url, None, ["any"], priority=0)
        self.proxy([pub, bad, paid, self.good(priority=3)])
        out, _ = self.call({"messages": [{"role": "user", "content": "hi"}]})
        self.assertEqual(json.loads(out), TOOL_REPLY)
        used = [(n, b["model"]) for n, b, a in self.seen]
        self.assertEqual(used, [("bad", "default"), ("good", "openai/gpt-oss-120b")])   # public never tried; paid model never sent

    def test_kill_switch_and_rate_limit(self):
        self.proxy([self.good()])
        for i in range(PER_MINUTE): self.call({"messages": [{"role": "user", "content": str(i)}]})
        with self.assertRaises(urllib.error.HTTPError) as cm: self.call({"messages": [{"role": "user", "content": "one more"}]})
        self.assertEqual(cm.exception.code, 429)
        self.now[0] += 61; self.call({"messages": [{"role": "user", "content": "later"}]})
        self.k.kill("t")
        with self.assertRaises(urllib.error.HTTPError) as cm: self.call({"messages": [{"role": "user", "content": "x"}]})
        self.assertEqual(cm.exception.code, 503)

    def test_no_provider_available(self):
        self.proxy([Provider("pub", "public", self.up_url, None, ["any"])])
        with self.assertRaises(urllib.error.HTTPError) as cm: self.call({"messages": [{"role": "user", "content": "x"}]})
        self.assertEqual(cm.exception.code, 503)


if __name__ == "__main__":
    unittest.main()
