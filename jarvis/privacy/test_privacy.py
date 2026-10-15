"""Tests for the privacy gateway. Run with the privacy venv:

    D:\\jarvis\\tools\\privacy-venv\\Scripts\\python.exe -m unittest jarvis/privacy/test_privacy.py -v

A fake upstream on 127.0.0.1 records exactly what it was sent, so the tests prove what would leave the PC.
All values below are made up.
"""
import json
import os
import sys
import threading
import unittest
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import gateway  # noqa: E402
from redactor import Mapping, Redactor, StreamRestorer, restore  # noqa: E402

RED = Redactor(names=False)
PW = "Tr0ub4dor&3xyz"
KEY = "sk-proj-AbCdEfGhIjKlMnOpQrStUvWxYz0123456789"


class Redaction(unittest.TestCase):
    def r(self, text):
        m = Mapping()
        return RED.redact(text, m), m

    def test_password_after_cue(self):
        out, m = self.r(f"my password = {PW} please remember it")
        self.assertNotIn(PW, out)
        self.assertIn("[PASSWORD_1]", out)
        self.assertIn("my password =", out)
        self.assertEqual(restore(out, m), f"my password = {PW} please remember it")
        out, m = self.r(f"my password = {PW}. Thanks")
        self.assertEqual(out, "my password = [PASSWORD_1]. Thanks")

    def test_cue_forms(self):
        for text, secret in [("pwd: hunter22", "hunter22"), ("PIN is 4821", "4821"), ("otp 482913", None),
                             ("the api key: zzTopSecret99", "zzTopSecret99"), ("password was 'q1w2e3r4'", "q1w2e3r4")]:
            out, _ = self.r(text)
            if secret:
                self.assertNotIn(secret, out, text)

    def test_keys_and_tokens(self):
        jwt = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dBjftJeZ4CVPmB92K27uhbUJU1p1r_wW1gFWFOEjXk"  # made-up test token  gitleaks:allow
        out, m = self.r(f"use {KEY} and token {jwt} and hf_abcdefghijklmnopqrstuvwxyz123456")
        for v in (KEY, jwt, "hf_abcdefghijklmnopqrstuvwxyz123456"):
            self.assertNotIn(v, out)
        self.assertIn("[API_KEY_1]", out)

    def test_contact_and_card(self):
        out, _ = self.r("mail me at ravi.k@example.com, card 4111 1111 1111 1111")
        self.assertNotIn("ravi.k@example.com", out)
        self.assertNotIn("4111 1111 1111 1111", out)

    def test_phone_numbers(self):
        for number in ("+91 98765 43210", "9876543210", "+1 415 555 0132", "+44 20 7946 0958"):
            out, _ = self.r(f"call me on {number} tomorrow")
            self.assertNotIn(number, out, number)
        out, _ = self.r("the meeting is at 10:30 on 15-10-2026, room 204, budget 12000")
        self.assertNotIn("[PHONE_NUMBER", out)

    def test_indian_ids(self):
        out, _ = self.r("my aadhaar number is 2345 6789 0123 and PAN ABCPE1234F")
        self.assertNotIn("2345 6789 0123", out)
        self.assertNotIn("ABCPE1234F", out)

    def test_ordinary_text_untouched(self):
        text = "What is the capital of France? Write a haiku about rain in 2026."
        out, m = self.r(text)
        self.assertEqual(out, text)
        self.assertEqual(m.to_value, {})

    def test_same_value_same_tag(self):
        m = Mapping()
        a = RED.redact(f"password: {PW}", m)
        b = RED.redact(f"again, password: {PW}", m)
        self.assertIn("[PASSWORD_1]", a)
        self.assertIn("[PASSWORD_1]", b)
        self.assertNotIn("[PASSWORD_2]", b)

    def test_stream_restorer_split_tag(self):
        m = Mapping()
        m.tag("PASSWORD", PW)
        s = StreamRestorer(m)
        got = "".join(s.feed(c) for c in ["Your pass", "word is [PASS", "WORD_", "1]. Keep it", " safe [x"]) + s.flush()
        self.assertEqual(got, f"Your password is {PW}. Keep it safe [x")


class FakeUpstream(BaseHTTPRequestHandler):
    seen = []
    headers_seen = []

    def log_message(self, *a):
        pass

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        FakeUpstream.seen.append(body)
        FakeUpstream.headers_seen.append(self.headers.get("Authorization"))
        text = json.dumps(body["messages"])
        echo = "I will keep [PASSWORD_1] safe." if "[PASSWORD_1]" in text else "ok"
        if body.get("stream"):
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            for piece in ["I will keep [PASS", "WORD_1] sa", "fe."]:
                ev = {"choices": [{"index": 0, "delta": {"content": piece}, "finish_reason": None}]}
                self.wfile.write(f"data: {json.dumps(ev)}\n\n".encode())
            ev = {"choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]}
            self.wfile.write(f"data: {json.dumps(ev)}\n\ndata: [DONE]\n\n".encode())
            return
        raw = json.dumps({"choices": [{"index": 0, "message": {"role": "assistant", "content": echo}}]}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)


class GatewayEndToEnd(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.up = ThreadingHTTPServer(("127.0.0.1", 0), FakeUpstream)
        threading.Thread(target=cls.up.serve_forever, daemon=True).start()
        cls.gw = gateway.serve(0, f"http://127.0.0.1:{cls.up.server_address[1]}/v1", timeout=10, red=RED)
        threading.Thread(target=cls.gw.serve_forever, daemon=True).start()
        cls.base = f"http://127.0.0.1:{cls.gw.server_address[1]}/v1"

    @classmethod
    def tearDownClass(cls):
        cls.gw.shutdown()
        cls.up.shutdown()

    def post(self, body):
        req = urllib.request.Request(self.base + "/chat/completions", data=json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json", "Authorization": "Bearer test-not-a-key"})
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.read().decode()

    def test_upstream_never_sees_the_password_and_reply_is_restored(self):
        out = json.loads(self.post({"model": "m", "messages": [
            {"role": "system", "content": "You are helpful."},
            {"role": "user", "content": f"my password = {PW}, remember it"}]}))
        sent = json.dumps(FakeUpstream.seen[-1])
        self.assertNotIn(PW, sent)
        self.assertIn("[PASSWORD_1]", sent)
        self.assertEqual(out["choices"][0]["message"]["content"], f"I will keep {PW} safe.")
        self.assertEqual(FakeUpstream.headers_seen[-1], "Bearer test-not-a-key")

    def test_streaming_restore(self):
        raw = self.post({"model": "m", "stream": True, "messages": [{"role": "user", "content": [
            {"type": "text", "text": f"password: {PW}"}]}]})
        self.assertNotIn(PW, json.dumps(FakeUpstream.seen[-1]))
        text = ""
        for line in raw.splitlines():
            if line.startswith("data:") and "[DONE]" not in line:
                for ch in json.loads(line[5:])["choices"]:
                    text += ch["delta"].get("content") or ""
        self.assertEqual(text, f"I will keep {PW} safe.")

    def test_tool_call_arguments_redacted(self):
        self.post({"model": "m", "messages": [
            {"role": "assistant", "content": None, "tool_calls": [
                {"id": "1", "type": "function", "function": {"name": "login", "arguments": json.dumps({"note": f"password: {PW}"})}}]},
            {"role": "tool", "tool_call_id": "1", "content": f"logged in with {KEY}"}]})
        sent = json.dumps(FakeUpstream.seen[-1])
        self.assertNotIn(PW, sent)
        self.assertNotIn(KEY, sent)

    def test_health_has_counts_not_values(self):
        with urllib.request.urlopen(self.base.replace("/v1", "") + "/health", timeout=5) as r:
            h = r.read().decode()
        self.assertIn('"ok": true', h)
        self.assertNotIn(PW, h)


if __name__ == "__main__":
    unittest.main()
