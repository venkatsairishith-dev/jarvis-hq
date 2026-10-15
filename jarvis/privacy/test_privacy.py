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

RED = Redactor(names=False, model=None)            # patterns only: deterministic, no model files or services needed
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


import redactor as _r  # noqa: E402

_MODEL = _r.load_pii_model()


@unittest.skipIf(_MODEL is None, "GLiNER-PII model not installed")
class ModelRedaction(unittest.TestCase):
    """Secrets with no cue word: the PII model proposes, the decision model (faked here) confirms. Values are made up.
    The real models' quality is measured by eval_pii.py, which needs the Jev service."""

    @classmethod
    def setUpClass(cls):
        cls.calls = []
        cls.yes = Redactor(names=False, model=_MODEL, verifier=lambda t, spans: cls.calls.append(spans) or [0.9] * len(spans))
        cls.no = Redactor(names=False, model=_MODEL, verifier=lambda t, spans: [0.1] * len(spans))

        def down(t, spans):
            raise OSError("decision model down")
        cls.down = Redactor(names=False, model=_MODEL, verifier=down)

    def test_confirmed_candidates_are_hidden(self):
        for text, secret in [("netflix login is ravi_k88 / Sunny#Day2024.", "Sunny#Day2024"),
                             ("use Mango!Tree77 for the office wifi", "Mango!Tree77"),
                             ("my SBI account 30012345678 ifsc SBIN0001234", "30012345678"),
                             ("ATM pin 9921, card in the drawer", "9921")]:
            out = self.yes.redact(text, Mapping())
            self.assertNotIn(secret, out, text)
        self.assertNotIn("!", self.yes.redact("use Mango!Tree77 for the office wifi", Mapping()))   # a split secret's middle

    def test_cue_words_stay_and_glued_symbols_go(self):
        out = self.yes.redact("the vpn login is k.rao and the passcode Blue7Whale#. ATM pin 9921", Mapping())
        for word in ("vpn", "passcode", "ATM pin"):
            self.assertIn(word, out)
        for leak in ("Blue7Whale", "#", "9921"):
            self.assertNotIn(leak, out)
        self.assertEqual(_r._widen("key Blue7Whale#.", 4, 14), (4, 15))   # takes the "#", leaves the full stop

    def test_short_words_are_never_model_secrets(self):
        text = "How can a beginner learn cybersecurity with AI, safely and legally?"
        self.assertEqual(self.yes.redact(text, Mapping()), text)

    def test_rejected_candidates_stay(self):
        text = "Remind me to call mom at 7 pm."
        self.assertEqual(self.no.redact(text, Mapping()), text)

    def test_pattern_hits_need_no_confirmation(self):
        out = self.no.redact(f"my password = {PW}", Mapping())
        self.assertNotIn(PW, out)

    def test_decision_model_down_hides_candidates(self):
        out = self.down.redact("ATM pin 9921, card in the drawer", Mapping())
        self.assertNotIn("9921", out)
        self.assertGreaterEqual(self.down.stats["jev_unreachable"], 1)

    def test_long_text_is_read_sentence_by_sentence(self):
        text = ("Yesterday I went to the market and bought vegetables. " * 40) + "ATM pin 9921, card in the drawer."
        self.assertNotIn("9921", self.yes.redact(text, Mapping()))

    def test_one_decision_call_per_sentence_and_sentences_are_cached(self):
        asked = []
        r = Redactor(names=False, model=_MODEL, verifier=lambda t, spans: asked.append(t) or [0.9] * len(spans))
        vpn = "the vpn login is k.rao and the passcode Blue7Whale#."
        r.redact(vpn, Mapping())
        self.assertEqual(asked, [vpn])                    # one call for the sentence, however many candidates it has
        r.redact(vpn + " Tell me a joke about cats.", Mapping())
        self.assertEqual(asked.count(vpn), 1)             # the repeated sentence was not asked again

    def test_shallow_mode_skips_the_model(self):
        text = "ATM pin 9921, card in the drawer"
        self.assertEqual(self.yes.redact(text, Mapping(), deep=False), text)


class GatewayRoles(unittest.TestCase):
    def test_only_user_and_tool_messages_are_deep(self):
        seen = []

        class Spy:
            def redact(self, text, m, deep=True):
                seen.append((text, deep))
                return text
        gateway.redact_body({"messages": [{"role": "system", "content": "sys"}, {"role": "user", "content": "u"},
                                          {"role": "assistant", "content": "a"}, {"role": "tool", "content": "t"}]}, Spy(), Mapping())
        self.assertEqual(seen, [("sys", False), ("u", True), ("a", False), ("t", True)])


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


class _Scripted(BaseHTTPRequestHandler):
    """A fake provider: answers with the next status code from its script and records what it got."""
    def log_message(self, *a):
        pass

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        self.server.got.append({"auth": self.headers.get("Authorization"), "model": body.get("model"), "text": json.dumps(body)})
        code = self.server.script.pop(0) if self.server.script else 200
        raw = json.dumps({"choices": [{"index": 0, "message": {"role": "assistant", "content": f"from {self.server.name}"}}]}
                         if code == 200 else {"error": {"message": f"status {code}"}}).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)


class ProviderFallback(unittest.TestCase):
    def setUp(self):
        import tempfile
        gateway._cooldown.clear()
        gateway._keys.clear()
        self.servers = []
        for name in ("first", "second"):
            s = ThreadingHTTPServer(("127.0.0.1", 0), _Scripted)
            s.name, s.script, s.got = name, [], []
            threading.Thread(target=s.serve_forever, daemon=True).start()
            self.servers.append(s)
        self.secret_dir = tempfile.mkdtemp()
        os.makedirs(os.path.join(self.secret_dir, "providers"))
        with open(os.path.join(self.secret_dir, "providers", "second.key"), "w") as f:
            f.write("second-test-key")                           # made-up test value
        self._old_root = gateway.SECRET_ROOT
        gateway.SECRET_ROOT = self.secret_dir
        provs = [{"name": "first", "base_url": f"http://127.0.0.1:{self.servers[0].server_address[1]}/v1", "key": "from_request"},
                 {"name": "second", "base_url": f"http://127.0.0.1:{self.servers[1].server_address[1]}/v1",
                  "key_file": "providers/second.key", "model": "fallback-model"}]
        self.gw = gateway.serve(0, provs, timeout=10, red=RED, cooldown=60)
        threading.Thread(target=self.gw.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.gw.server_address[1]}/v1/chat/completions"

    def tearDown(self):
        gateway.SECRET_ROOT = self._old_root
        self.gw.shutdown()
        self.gw.server_close()
        for s in self.servers:
            s.shutdown()
            s.server_close()

    def call(self):
        req = urllib.request.Request(self.url, data=json.dumps({"model": "m", "messages": [{"role": "user", "content": f"password: {PW}"}]}).encode(),
                                     headers={"Content-Type": "application/json", "Authorization": "Bearer caller-key"})
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                return r.status, r.headers.get("X-Neko-Provider"), json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, e.headers.get("X-Neko-Provider"), json.loads(e.read())

    def test_rate_limit_falls_back_with_the_fallbacks_own_key_and_model(self):
        self.servers[0].script = [429]
        code, prov, out = self.call()
        self.assertEqual((code, prov), (200, "second"))
        got = self.servers[1].got[0]
        self.assertEqual(got["auth"], "Bearer second-test-key")
        self.assertEqual(got["model"], "fallback-model")
        self.assertNotIn(PW, got["text"])                         # the fallback gets the redacted text too
        self.assertEqual(self.servers[0].got[0]["auth"], "Bearer caller-key")

    def test_cooling_down_provider_is_skipped_but_the_first_is_always_tried(self):
        self.servers[1].script = [429]
        self.servers[0].script = [503]
        code, _, _ = self.call()                                   # first 503 -> second 429 (last: passed back)
        self.assertEqual(code, 429)
        self.servers[0].script = [503]
        code, _, out = self.call()                                 # second cooling down: only the first is tried
        self.assertEqual(code, 503)
        self.assertEqual(len(self.servers[1].got), 1)

    def test_bad_request_is_not_retried(self):
        self.servers[0].script = [400]
        code, prov, _ = self.call()
        self.assertEqual((code, prov), (400, "first"))
        self.assertEqual(self.servers[1].got, [])

    def test_first_ok_needs_no_fallback(self):
        code, prov, out = self.call()
        self.assertEqual((code, prov), (200, "first"))
        self.assertEqual(self.servers[1].got, [])


if __name__ == "__main__":
    unittest.main()
