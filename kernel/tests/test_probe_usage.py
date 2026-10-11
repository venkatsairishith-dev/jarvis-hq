"""Provider identification and measured token usage: the probe reads the key inside the kernel and never returns it; the
usage a provider reports is recorded per call and summed; probes are rate limited."""
import json
import os
import re
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(__file__)
sys.path.insert(0, os.path.join(HERE, ".."))
from jarvis_kernel.gateway import Provider, probe
from jarvis_kernel.llm_proxy import _usage_from_tail, make_handler
from jarvis_kernel.store import Kernel

SECRET = "sk-live-NEVER-RETURNED-0123456789abcdef"  # gitleaks:allow (invented value: the test proves it is never returned)


class FakeProvider(BaseHTTPRequestHandler):
    seen_auth = []
    mode = {"models": 200, "chat": 200}

    def log_message(self, *a):
        pass

    def _send(self, code, obj, headers=None):
        b = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(b)))
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(b)

    def do_GET(self):
        FakeProvider.seen_auth.append(self.headers.get("Authorization"))
        if self.path.endswith("/models"):
            if FakeProvider.mode["models"] != 200:
                return self._send(FakeProvider.mode["models"], {"error": "nope"})
            return self._send(200, {"data": [{"id": "fake-1"}, {"id": "fake-2"}]}, {"x-ratelimit-remaining-tokens": "999000", "x-request-id": "r1"})
        self._send(404, {})

    def do_POST(self):
        n = int(self.headers.get("Content-Length", "0"))
        body = json.loads(self.rfile.read(n))
        FakeProvider.seen_auth.append(self.headers.get("Authorization"))
        if FakeProvider.mode["chat"] != 200:
            return self._send(FakeProvider.mode["chat"], {"error": {"message": "busy"}})
        out = {"id": "c1", "model": body.get("model"), "choices": [{"index": 0, "message": {"role": "assistant", "content": "ok"}, "finish_reason": "stop"}],
               "usage": {"prompt_tokens": 17, "completion_tokens": 3, "total_tokens": 20}}
        if body.get("stream"):
            data = ("data: " + json.dumps({"choices": [{"delta": {"content": "ok"}}]}) + "\n\n" +
                    "data: " + json.dumps({"choices": [{"delta": {}, "finish_reason": "stop"}], "usage": out["usage"]}) + "\n\ndata: [DONE]\n\n").encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            self.wfile.write(data)
            return
        self._send(200, out, {"x-ratelimit-remaining-requests": "99"})


class Probe(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        FakeProvider.seen_auth.clear()
        FakeProvider.mode.update(models=200, chat=200)
        self.up = ThreadingHTTPServer(("127.0.0.1", 0), FakeProvider)
        threading.Thread(target=self.up.serve_forever, daemon=True).start()
        self.key = os.path.join(self.tmp.name, "fake.key")
        with open(self.key, "w") as fh:
            fh.write(SECRET)
        self.prov = Provider(name="fake", lane="public", base_url=f"http://127.0.0.1:{self.up.server_address[1]}/v1", key_file=self.key,
                             models=["any"], default_model="fake-1", priority=5)
        self.k = Kernel(os.path.join(self.tmp.name, "d"))
        H, tokens = make_handler(self.k, [self.prov], owner_hermes=None)
        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.srv.server_address[1]}"
        self.owner, self.agent = tokens.issue("console", "owner"), tokens.issue("lab", "public")

    def tearDown(self):
        self.srv.shutdown(); self.up.shutdown(); self.k.db.close(); self.tmp.cleanup()

    def call(self, method, path, body, tok):
        r = urllib.request.Request(self.url + path, data=json.dumps(body).encode() if body is not None else None, method=method,
                                   headers={"Authorization": "Bearer " + tok, "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(r, timeout=20) as resp:
                return resp.status, json.loads(resp.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read() or b"{}")

    def test_the_probe_identifies_the_provider_without_returning_the_key(self):
        r = probe(self.prov, chat=True)
        self.assertTrue(r["ok"])
        self.assertEqual((r["models_count"], r["model_listed"], r["configured_model"]), (2, True, "fake-1"))
        self.assertEqual(r["chat"]["usage"], {"prompt_tokens": 17, "completion_tokens": 3, "total_tokens": 20})
        self.assertEqual(r["headers"]["x-ratelimit-remaining-tokens"], "999000")
        self.assertNotIn(SECRET, json.dumps(r))
        self.assertTrue(all(a == "Bearer " + SECRET for a in FakeProvider.seen_auth))     # the key was used, in the header only

    def test_a_missing_key_a_refusing_provider_and_a_missing_model_are_reported_by_class(self):
        os.unlink(self.key)
        self.assertIn("not available", probe(self.prov)["why"])
        with open(self.key, "w") as fh:
            fh.write(SECRET)
        FakeProvider.mode["models"] = 401
        r = probe(self.prov)
        self.assertFalse(r["ok"])
        self.assertIn("401", r["models_error"])
        FakeProvider.mode.update(models=200, chat=429)
        r = probe(self.prov, chat=True)
        self.assertFalse(r["chat"]["ok"])
        self.assertIn("429", r["chat"]["error"])
        self.assertNotIn(SECRET, json.dumps(r))

    def test_owner_route_probes_audits_and_rate_limits(self):
        code, r = self.call("POST", "/v1/jarvis/owner/models/probe", {"provider": "fake", "chat": True}, self.owner)
        self.assertEqual((code, r["ok"]), (200, True))
        self.assertEqual(self.call("POST", "/v1/jarvis/owner/models/probe", {"provider": "nope"}, self.owner)[0], 404)
        self.assertEqual(self.call("POST", "/v1/jarvis/owner/models/probe", {"provider": "fake"}, self.agent)[0], 403)      # an agent cannot
        ev = self.call("GET", "/v1/jarvis/owner/events?since=0&limit=200", None, self.owner)[1]["events"]
        pr = [e for e in ev if e["kind"] == "provider_probe"][0]["payload"]
        self.assertEqual((pr["provider"], pr["usage"]["total_tokens"]), ("fake", 20))
        self.assertNotIn(SECRET, json.dumps(ev))
        for _ in range(9):
            self.call("POST", "/v1/jarvis/owner/models/probe", {"provider": "fake"}, self.owner)
        self.assertEqual(self.call("POST", "/v1/jarvis/owner/models/probe", {"provider": "fake"}, self.owner)[0], 429)

    def test_provider_reported_usage_is_recorded_for_plain_and_streamed_calls_and_summed(self):
        body = {"model": "jarvis", "messages": [{"role": "user", "content": "hello"}]}
        self.assertEqual(self.call("POST", "/v1/chat/completions", body, self.agent)[0], 200)
        r = urllib.request.Request(self.url + "/v1/chat/completions", data=json.dumps(dict(body, stream=True)).encode(), method="POST",
                                   headers={"Authorization": "Bearer " + self.agent, "Content-Type": "application/json"})
        with urllib.request.urlopen(r, timeout=20) as resp:
            self.assertIn(b"[DONE]", resp.read())
        rows = self.k.db.execute("SELECT payload FROM audit WHERE kind='agent_model_call' ORDER BY seq").fetchall()
        usages = [json.loads(p[0]).get("usage") for p in rows]
        self.assertEqual(usages, [{"prompt_tokens": 17, "completion_tokens": 3, "total_tokens": 20}] * 2)
        m = self.call("GET", "/v1/jarvis/owner/models", None, self.owner)[1]
        self.assertEqual(m["kpis"]["measured_tokens_24h"], {"prompt": 34, "completion": 6, "calls": 2})
        self.assertEqual(m["kpis"]["measured_by_provider_24h"]["fake"]["calls"], 2)


class UsageParsing(unittest.TestCase):
    NL = bytes([10])

    def test_nested_usage_objects_from_real_providers_are_read(self):
        # the shape OpenRouter and OpenAI send as the last frame of a stream: details and cost nested inside usage
        frames = [b'data: {"id":"x","choices":[{"index":0,"delta":{"content":"hi"},"finish_reason":null}]}',
                  b'data: {"id":"x","choices":[],"usage":{"prompt_tokens":1234,"completion_tokens":56,"total_tokens":1290,'
                  b'"prompt_tokens_details":{"cached_tokens":1000},"completion_tokens_details":{"reasoning_tokens":8},"cost":0}}',
                  b'data: [DONE]']
        tail = (self.NL * 2).join(frames) + self.NL * 2
        self.assertEqual(_usage_from_tail(tail), {"prompt_tokens": 1234, "completion_tokens": 56, "total_tokens": 1290})

    def test_flat_usage_a_cut_off_start_and_no_usage(self):
        self.assertEqual(_usage_from_tail(b'{"usage":{"prompt_tokens":3,"completion_tokens":4,"total_tokens":7}}')["total_tokens"], 7)
        cut = b'ns":{"a":1}}}' + self.NL + b'data: {"usage":{"prompt_tokens":5,"completion_tokens":6}}'
        self.assertEqual(_usage_from_tail(cut)["prompt_tokens"], 5)
        none = b'data: {"choices":[{"delta":{"content":"x"}}]}' + self.NL * 2 + b"data: [DONE]"
        self.assertEqual(_usage_from_tail(none), {})
        self.assertEqual(_usage_from_tail(b'"usage":{"prompt_tokens":'), {})                    # truncated JSON is not a number


if __name__ == "__main__":
    unittest.main()
