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
try:
    import presidio_analyzer  # noqa: F401
except ImportError:     # a clear skip, not an import error: these are the REAL-detector tests (the plumbing is test_gateway_plumbing.py)
    raise unittest.SkipTest("Presidio is not installed. Real-detector tests: install presidio-analyzer + spacy + the "
                            "en_core_web_sm model (the privacy venv on the laptop, or `pip install --target DIR ...` in a "
                            "throw-away dir with PYTHONPATH=DIR), then: cd jarvis/privacy && python -B -m unittest test_privacy -v")
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

    def test_groq_stripe_and_pgp_keys(self):
        # audit SEC-D7 (2026-10-18): made-up values, each written in two pieces so no scanner sees a whole one
        groq, stripe = "gsk_" + "Ab3dE6gH9jK2mN5pQ8sT1vW4yZ7bC0eF3hJ6kL9nP2rS5uX8z", "sk_" + "live_" + "Zx9Cv8Bn7Mq6Wr5Ty4Ui3Op2"
        pgp = "-----BEGIN PGP " + "PRIVATE KEY BLOCK-----\n\nlQOYBGXsecretbody\n=abcd\n-----END PGP PRIVATE KEY BLOCK-----"
        out, _ = self.r(f"keys: {groq} and {stripe}\n{pgp}")
        for v in (groq, stripe, "lQOYBGXsecretbody"):
            self.assertNotIn(v, out)

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


class DeepOnly:
    """Stands in for the PII model: hides the made-up secret SECRET only on a deep pass, as GLiNER + Jev would (no Presidio,
    no model files: these tests run anywhere)."""
    model, stats = object(), {}

    def __init__(self):
        self.calls = []

    def redact(self, text, m, deep=True, names=None):
        self.calls.append((text, deep))
        return text.replace(SECRET, m.tag("PASSWORD", SECRET)) if deep else text


SECRET = "Blue7Whale"                                # made up, with no cue word: only the model pass finds it


class GatewayRoles(unittest.TestCase):
    def test_every_role_gets_the_full_pass(self):
        # Audit MEM-M3 (2026-10-18): system and assistant text used to get the patterns only, and chat clients put recalled
        # memory in the system prompt. The old test asserted exactly that gap (system/assistant deep=False).
        spy = DeepOnly()
        gateway.redact_body({"messages": [{"role": "system", "content": "sys"}, {"role": "user", "content": "u"},
                                          {"role": "assistant", "content": "a", "reasoning_content": "r"},
                                          {"role": "tool", "content": "t"}, {"role": "developer", "content": "d"}]}, spy, Mapping())
        self.assertEqual(spy.calls, [("sys", True), ("u", True), ("a", True), ("r", True), ("t", True), ("d", True)])

    def test_recalled_memory_in_the_system_prompt_never_leaves_in_clear(self):
        m = Mapping()
        out = gateway.redact_body({"messages": [
            {"role": "system", "content": f"Memory about the owner: the office wifi passcode {SECRET}."},
            {"role": "assistant", "content": f"Noted, {SECRET}."},
            {"role": "user", "content": [{"type": "text", "text": f"remind me of {SECRET}"}]}]}, DeepOnly(), m)
        sent = json.dumps(out)
        self.assertNotIn(SECRET, sent)
        self.assertEqual(sent.count("[PASSWORD_1]"), 3)                       # one value, one placeholder everywhere
        self.assertEqual(restore("[PASSWORD_1]", m), SECRET)

    def test_images_audio_and_unknown_parts_are_withheld_with_a_note(self):
        # Audit PRO-D9: image_url parts were forwarded as they came. The text around them still goes, redacted.
        before = gateway.STATS.get("withheld", 0)
        out = gateway.redact_body({"messages": [
            {"role": "user", "content": [{"type": "text", "text": f"what is on my screen? {SECRET}"},
                                         {"type": "image_url", "image_url": {"url": "data:image/png;base64,iVBORw0KGgoAAAA"}},
                                         {"type": "input_audio", "input_audio": {"data": "UklGRgAAAA", "format": "wav"}},
                                         {"type": "something_new", "blob": "x"}]},
            {"role": "user", "content": {"unexpected": "shape"}},
            {"role": "assistant", "content": "ok", "audio": {"id": "a1"}}]}, DeepOnly(), Mapping())
        sent = json.dumps(out)
        for leak in ("base64", "iVBORw0KGgo", "UklGRg", "image_url", "input_audio", "blob", "unexpected", SECRET, '"audio"'):
            self.assertNotIn(leak, sent, leak)
        parts = out["messages"][0]["content"]
        self.assertEqual([p["type"] for p in parts], ["text"] * 4)
        self.assertIn("an image was withheld for privacy", parts[1]["text"])
        self.assertIn("an audio clip was withheld", parts[2]["text"])
        self.assertIn("an attachment was withheld", parts[3]["text"])
        self.assertIn("withheld for privacy", out["messages"][1]["content"])
        self.assertGreaterEqual(gateway.STATS["withheld"] - before, 4)

    def test_a_list_of_completion_prompts_is_redacted(self):
        out = gateway.redact_body({"prompt": [f"one {SECRET}", "two"]}, DeepOnly(), Mapping())
        self.assertNotIn(SECRET, json.dumps(out))


class GatewayFailsClosed(unittest.TestCase):
    """The real HTTP gateway with the deep-only stand-in: what would leave the PC (a fake upstream records it)."""

    @classmethod
    def setUpClass(cls):
        cls.up = ThreadingHTTPServer(("127.0.0.1", 0), FakeUpstream)
        threading.Thread(target=cls.up.serve_forever, daemon=True).start()
        cls.gw = gateway.serve(0, f"http://127.0.0.1:{cls.up.server_address[1]}/v1", timeout=10, red=DeepOnly())
        threading.Thread(target=cls.gw.serve_forever, daemon=True).start()
        cls.base = f"http://127.0.0.1:{cls.gw.server_address[1]}/v1"

    @classmethod
    def tearDownClass(cls):
        cls.gw.shutdown()
        cls.up.shutdown()

    def post(self, path, body):
        req = urllib.request.Request(self.base + path, data=json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json", "Authorization": "Bearer test-not-a-key"})
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read() or b"{}")

    def test_a_memory_summary_call_in_the_system_role_is_redacted_and_the_reply_restored(self):
        n = len(FakeUpstream.seen)
        code, out = self.post("/chat/completions", {"model": "m", "messages": [
            {"role": "system", "content": f"Summarise this conversation. The owner said the passcode is {SECRET} and password = x"},
            {"role": "assistant", "content": f"Summary so far: passcode {SECRET}"}]})
        self.assertEqual(code, 200)
        sent = json.dumps(FakeUpstream.seen[n:])
        self.assertNotIn(SECRET, sent)
        self.assertIn("I will keep " + SECRET + " safe.", out["choices"][0]["message"]["content"])   # restored locally

    def test_a_screenshot_reaches_the_public_model_only_as_a_note(self):
        n = len(FakeUpstream.seen)
        code, _ = self.post("/chat/completions", {"model": "m", "messages": [{"role": "user", "content": [
            {"type": "text", "text": "read this"}, {"type": "image_url", "image_url": {"url": "data:image/png;base64,QUJDREVGRw=="}}]}]})
        self.assertEqual(code, 200)                                        # the conversation keeps working
        sent = json.dumps(FakeUpstream.seen[n:])
        self.assertNotIn("QUJDREVGRw", sent)
        self.assertIn("withheld for privacy", sent)

    def test_other_endpoints_are_refused_not_forwarded_unredacted(self):
        n = len(FakeUpstream.seen)
        for path in ("/embeddings", "/responses", "/audio/speech"):
            code, out = self.post(path, {"model": "m", "input": f"the passcode is {SECRET}"})
            self.assertEqual(code, 403, path)
            self.assertIn("only chat and completion", out["error"]["message"])
        self.assertEqual(len(FakeUpstream.seen), n)                        # nothing left the PC


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
        cls.gw = gateway.serve(0, f"http://127.0.0.1:{cls.up.server_address[1]}/v1", timeout=10, red=RED,
                              require_deep=False)    # RED is patterns-only on purpose; the fail-closed default is tested below
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


class RealDetectorsOnEveryField(unittest.TestCase):
    """REAL Presidio + spaCy (patterns), no fake: a cue-word secret and a key placed in each field the audit (M2) listed must
    not reach the upstream recorder. GLiNER-PII is not needed for these (the model-dependent tests are ModelRedaction)."""

    @classmethod
    def setUpClass(cls):
        cls.up = ThreadingHTTPServer(("127.0.0.1", 0), FakeUpstream)
        threading.Thread(target=cls.up.serve_forever, daemon=True).start()
        cls.gw = gateway.serve(0, f"http://127.0.0.1:{cls.up.server_address[1]}/v1", timeout=10, red=RED, require_deep=False)
        threading.Thread(target=cls.gw.serve_forever, daemon=True).start()
        cls.base = f"http://127.0.0.1:{cls.gw.server_address[1]}"

    @classmethod
    def tearDownClass(cls):
        cls.gw.shutdown()
        cls.gw.server_close()
        cls.up.shutdown()
        cls.up.server_close()

    def post(self, body):
        req = urllib.request.Request(self.base + "/v1/chat/completions", data=json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json", "Authorization": "Bearer test-not-a-key"})
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status

    def test_a_secret_in_each_listed_field_never_reaches_the_upstream(self):
        sec = lambda tag: f"password = Zq{tag}9xW7vL"          # a cue word + a made-up value: the real pattern recogniser finds it
        body = {"model": "m", "messages": [
            {"role": "user", "name": sec("name"), "content": [{"type": "text", "text": "hi", "note": sec("extra")}]},
            {"role": "assistant", "content": None, "function_call": {"name": "f", "arguments": json.dumps({"a": sec("fcarg")})},
             "tool_calls": [{"id": "1", "type": "function", "function": {"name": "g", "arguments": json.dumps({"a": sec("tcarg")})}}]},
            {"role": "tool", "tool_call_id": "1", "content": sec("toolout")}],
            "tools": [{"type": "function", "function": {"name": "t", "description": sec("tooldesc"),
                                                       "parameters": {"type": "object", "properties": {"x": {"type": "string", "description": sec("param")}}}}}],
            "functions": [{"name": "h", "description": sec("functions"), "parameters": {"type": "object"}}],
            "response_format": {"type": "json_schema", "json_schema": {"name": "r", "schema": {"type": "object", "description": sec("rf")}}},
            "stop": [sec("stop")], "user": sec("user"), "metadata": {"k": sec("meta")}}
        n = len(FakeUpstream.seen)
        self.assertEqual(self.post(body), 200)
        sent = json.dumps(FakeUpstream.seen[n:])
        for tag in ("name", "extra", "fcarg", "tcarg", "toolout", "tooldesc", "param", "functions", "rf", "stop", "user", "meta"):
            self.assertNotIn(f"Zq{tag}9xW7vL", sent, tag)

    def test_names_for_one_call_are_hidden_with_real_spacy_and_off_by_default(self):
        """The research plugin asks /redact for names. John Smith / Paris are found by en_core_web_sm; it is a small model and
        misses some names and street addresses (see the report), so 'active' does not mean 'complete'."""
        def preview(**extra):
            req = urllib.request.Request(self.base + "/redact", data=json.dumps({"text": "John Smith flew to Paris", **extra}).encode(),
                                         headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.loads(r.read())
        default, named = preview(), preview(names=True)
        self.assertIn("John Smith", default["text"])                      # JARVIS_PRIVACY_NAMES is off: ordinary conversation stays
        self.assertFalse(default["names"])
        self.assertNotIn("John Smith", named["text"])
        self.assertNotIn("Paris", named["text"])
        self.assertTrue(named["names"])
        self.assertIn("John Smith", preview()["text"])                    # and the names=True answer was not cached for the default


class CachedRedactionWidening(unittest.TestCase):
    """D80: the gateway's per-message cache (D60) widened to hit on leading/trailing whitespace, against the REAL Presidio + spaCy
    engine (patterns + names), not the fake one test_latency_d60.py uses. Proves the widening is correct, and that the one widening
    considered and REJECTED (case-insensitive keys) would have been wrong, with the real engine's own behaviour."""

    def setUp(self):
        self.red = Redactor(names=True, model=None)        # patterns + spaCy names; no GLiNER files needed for this

    def test_leading_and_trailing_whitespace_still_get_the_same_correct_redaction(self):
        cached = gateway.CachedRedaction(self.red)
        core = "My password is Zq9xW7vLpad and my friend Turing came by."
        variants = [core, " " + core, core + "\n", "  \n\t" + core + "   ", "\n\n" + core]
        outs = [cached.redact(v, Mapping()) for v in variants]
        for v, out in zip(variants, outs):
            lead = v[:len(v) - len(v.lstrip())]
            trail = v[len(v.rstrip()):]
            self.assertEqual(out, lead + outs[0].strip() + trail, repr(v))
            self.assertNotIn("Zq9xW7vLpad", out)
            self.assertNotIn("Turing", out)
        snap = cached.snapshot()
        self.assertEqual(snap["misses"], 1, "one real scan for the core text")
        self.assertEqual(snap["hits"], len(variants) - 1, "every padded repeat of the same core text is a cache hit")

    def test_a_different_case_is_never_served_from_another_cases_cache_entry(self):
        """The widening does NOT fold case: 'Turing' is a name to the real engine, 'turing' and 'TURING' are not (spaCy is case-sensitive).
        Each must be redacted on its own, whether or not another case of the same text already primed the cache."""
        cached = gateway.CachedRedaction(self.red)
        proper, lower, upper = "My friend Turing came by.", "my friend turing came by.", "My friend TURING came by."
        self.assertIn("[PERSON_1]", cached.redact(proper, Mapping()))
        self.assertEqual(cached.redact(lower, Mapping()), lower, "no false positive, and no false NEGATIVE borrowed from the proper-case entry")
        self.assertEqual(cached.redact(upper, Mapping()), upper)
        self.assertEqual(cached.snapshot()["misses"], 3, "three different texts, three real scans: none shared a cache entry by case")


class _Scripted(BaseHTTPRequestHandler):
    """A fake provider: answers with the next status code from its script and records what it got."""
    def log_message(self, *a):
        pass

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        self.server.got.append({"auth": self.headers.get("Authorization"), "model": body.get("model"), "text": json.dumps(body)})
        code = self.server.script.pop(0) if self.server.script else 200
        raw = json.dumps({"choices": [{"index": 0, "message": {"role": "assistant", "content": f"from {self.server.name}"}}]}
                         if code == 200 else {"error": {"message": f"status {code}" + (f" (got Authorization: {self.headers.get('Authorization')})"
                                                                                       if getattr(self.server, "echo", False) else "")}}).encode()
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
        self.gw = gateway.serve(0, provs, timeout=10, red=RED, cooldown=60, require_deep=False)   # patterns-only on purpose
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
                return r.status, r.headers.get("X-Jarvis-Provider"), json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, e.headers.get("X-Jarvis-Provider"), json.loads(e.read())

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

    def test_bench_j_a_fallback_error_page_never_echoes_the_fallback_key(self):
        self.servers[0].script = [503]
        self.servers[1].script = [401]
        self.servers[1].echo = True                                # a careless proxy echoes the Authorization header
        req = urllib.request.Request(self.url, data=json.dumps({"model": "m", "messages": [{"role": "user", "content": "hi"}]}).encode(),
                                     headers={"Content-Type": "application/json", "Authorization": "Bearer caller-key"})
        with self.assertRaises(urllib.error.HTTPError) as cm:
            urllib.request.urlopen(req, timeout=10)
        body, hdrs = cm.exception.read().decode(), str(dict(cm.exception.headers))
        self.assertEqual(cm.exception.code, 401)
        self.assertEqual(self.servers[1].got[0]["auth"], "Bearer second-test-key")   # it was really sent upstream
        self.assertNotIn("second-test-key", body)
        self.assertNotIn("second-test-key", hdrs)
        self.assertIn("REDACTED", body)

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


class BenignAppNames(unittest.TestCase):
    """Live 2026-10-26: the deep model marked "Notepad" as a person, "WordPad" as a username and all of "Open Notepad" as an API key."""

    class _Model:
        def __init__(self, spans):
            self.spans = spans

        def predict_entities(self, sentence, labels, threshold=0.5):
            return [{"label": "api key", "start": s, "end": e, "score": 0.9} for s, e in self.spans]

    def _spans(self, sentence, word):
        from redactor import model_candidates
        i = sentence.index(word)
        return model_candidates(self._Model([(i, i + len(word))]), sentence)

    def test_an_app_name_or_a_command_with_one_is_not_a_secret(self):
        self.assertEqual(self._spans("Open Notepad", "Open Notepad"), [])
        self.assertEqual(self._spans("Ask me whether I mean Notepad or WordPad.", "WordPad."), [])

    def test_a_word_that_is_not_an_app_name_still_counts(self):
        self.assertTrue(self._spans("Open Notepad hunter2xyz", "Notepad hunter2xyz"))


class ScreenTextReversible(unittest.TestCase):
    """D73, with the REAL pattern detector (Presidio + spaCy; no PII model): what the screen-text path keeps, hides and never keeps."""

    def setUp(self):
        from redactor import ScreenVault, screen_redact
        self.vault, self.screen_redact = ScreenVault(), screen_redact
        self.red = Redactor(names=True, model=None, verifier=None)

    def run_screen(self, text):
        return self.screen_redact(self.red, self.vault, text, names=True)

    def test_what_the_owner_may_see_again_is_kept_and_secrets_are_not(self):
        out, kinds, hidden = self.run_screen("Wifi password: hunter2xyz9. Card 4111 1111 1111 1111. Mail a.b@example.com, call +91 98765 43210. "
                                             "PIN is 4821. Ravi wrote from Hyderabad.")
        for secret in ("hunter2xyz9", "4111 1111 1111 1111", "4821"):
            self.assertNotIn(secret, out)
            self.assertNotIn(secret, " ".join(self.vault.display().values()))
        self.assertIn("[REDACTED:password]", out)
        self.assertIn("[REDACTED:credit_card]", out)
        self.assertEqual(hidden, 3)
        shown = self.vault.display()
        self.assertTrue({"+91 98765 43210", "a.b@example.com"} <= set(shown.values()))          # a name or a place depends on spaCy's reading of the sentence
        self.assertTrue(set(shown.values()) <= {"+91 98765 43210", "Hyderabad", "Ravi", "a.b@example.com"})
        for tag in shown:
            self.assertIn(tag, out)

    def test_a_tag_on_the_screen_is_not_ours_and_a_marker_is_not_a_new_secret(self):
        out, _, _ = self.run_screen("The chat window says [PERSON_1] and password: hunter2xyz9")
        self.assertIn("(PERSON_1)", out)
        self.assertNotIn("[PERSON_1]", out)
        again, _, hidden = self.run_screen(out)
        self.assertEqual(hidden, 0, "'password: [REDACTED:password]' is not a second secret")
        self.assertIn("[REDACTED:password]", again)

    def test_the_gateway_pass_over_a_tool_result_keeps_our_marks_as_they_are(self):
        out, _, _ = self.run_screen("Wifi password: hunter2xyz9. Ravi is here.")
        m = Mapping()
        self.assertEqual(self.red.redact(out, m, True, True), out)
        self.assertEqual(m.to_value, {})

    def test_a_stream_restorer_holds_back_only_a_possible_tag(self):
        m = Mapping(to_value={"[PERSON_1]": "Ravi"})
        r = StreamRestorer(m)
        self.assertEqual(r.feed("a [b"), "a [b")
        self.assertEqual(r.feed(" [PER"), " ")
        self.assertEqual(r.feed("SON_1]!"), "Ravi!")


class FilePathsAreNotSecrets(BenignAppNames):
    """Live 2026-10-27 (reliability gate): "Rename <path>old.txt to new.txt" reached the chat model as "Rename [USERNAME_1] to new.txt" and
    "save it as note-r58ea3d.txt" as "save it as [PASSWORD_1]", so the file goals failed. A path hides only its profile name; a file name is left alone."""

    BS = chr(92)

    def test_a_bare_file_name_is_left_alone(self):
        self.assertEqual(self._spans("Save it as note-r58ea3d.txt please", "note-r58ea3d.txt"), [])

    def test_a_windows_path_hides_only_the_profile_name(self):
        b = self.BS
        path = "C:" + b + "Users" + b + "venkat" + b + "Documents" + b + "old.txt"
        sentence = "Rename " + path + " to new.txt."
        got = self._spans(sentence, path)
        self.assertEqual([sentence[s:e] for _k, s, e, _sc in got], ["venkat"])

    def test_a_path_without_a_profile_name_is_not_hidden(self):
        b = self.BS
        path = "D:" + b + "work" + b + "src-1.txt"
        self.assertEqual(self._spans("Copy " + path + " to dst-1.txt", path), [])

    def test_a_credential_that_looks_like_user_slash_password_still_counts(self):
        self.assertTrue(self._spans("login is admin/hunter2xyz ok", "admin/hunter2xyz"))
