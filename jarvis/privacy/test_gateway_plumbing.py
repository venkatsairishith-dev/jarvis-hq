"""Privacy gateway plumbing, with a LABELLED FAKE detector (audit M2/M3, 2026-10-18).

What this file proves, and what it does not:
  * PROVES: every text-bearing field of a chat/completions request is handed to the detector, nothing is forwarded that the
    detector did not see, unknown fields are dropped, media is withheld, and a gateway whose PII model is missing refuses to
    forward (503) and says so on /health. A recorder at the HTTP edge (a fake upstream) shows what would leave the PC.
  * DOES NOT PROVE that Presidio / spaCy / GLiNER find any particular value. That is test_privacy.py (real Presidio + spaCy;
    the GLiNER tests there skip without the model files). The fake detector below only swaps the made-up CANARY strings.

It runs in the stock image: if Presidio is not installed, its import is stubbed ONLY to be able to import the gateway module
(the real detector class is never constructed here) and the stubs are removed again afterwards.

    podman run --rm -v ~/hq-jarvis:/src:ro jarvis-test sh -c "cd /src/jarvis/privacy && python -B -m unittest test_gateway_plumbing -v"
"""
import json
import os
import re
import subprocess
import sys
import threading
import types
import unittest
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))


def _import_gateway():
    stubs = {}
    try:
        import presidio_analyzer  # noqa: F401
    except ImportError:
        pa = types.ModuleType("presidio_analyzer")
        for n in ("AnalyzerEngine", "Pattern", "PatternRecognizer", "RecognizerResult"):
            setattr(pa, n, type(n, (), {"__init__": lambda self, *a, **k: None}))
        ne = types.ModuleType("presidio_analyzer.nlp_engine")
        ne.NlpEngineProvider = type("NlpEngineProvider", (), {})
        stubs = {"presidio_analyzer": pa, "presidio_analyzer.nlp_engine": ne}
        sys.modules.update(stubs)
    sys.path.insert(0, HERE)
    try:
        import importlib
        red, gw = importlib.import_module("redactor"), importlib.import_module("gateway")
    finally:
        sys.path.remove(HERE)
        for n in stubs:
            sys.modules.pop(n, None)
        sys.modules.pop("redactor", None)             # these two generic names must not shadow test_privacy.py's real ones
        sys.modules.pop("gateway", None)
    return gw, red


gateway, redactor = _import_gateway()
Mapping = redactor.Mapping

CANARY = re.compile(r"CANARY-7f3a-[A-Za-z0-9_.]+-SYNTH")


class LabelledFakeDetector:
    """FAKE detector, not Presidio: swaps every made-up CANARY string for a placeholder and records each string it was given."""
    names = False
    stats = {}

    def __init__(self, model=object()):
        self.model = model                            # a non-None model means "the deep pass is available"
        self.seen = []

    def redact(self, text, mapping, deep=True, names=None):
        self.seen.append((text, deep))
        return CANARY.sub(lambda mm: mapping.tag("CANARY", mm.group(0)), text)


def c(field):
    return f"CANARY-7f3a-{field}-SYNTH"


def everything_body():
    """A request with a canary in every place a client can put text (the audit M2 list)."""
    return {
        "model": "dahl-model",
        "messages": [
            {"role": "system", "content": f"memory: {c('system')}"},
            {"role": "user", "content": [{"type": "text", "text": f"hi {c('part_text')}", "note": c("part_extra"),
                                          "cache_control": {"type": c("part_cache")}},
                                         {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{c('image')}"}},
                                         {"type": "input_audio", "input_audio": {"data": c("audio"), "format": "wav"}},
                                         {"type": "file", "file": {"file_data": c("file")}}]},
            {"role": "user", "name": c("msg_name"), "content": "named"},
            {"role": "assistant", "content": None, "refusal": c("refusal"), "reasoning": c("reasoning"),
             "reasoning_content": c("reasoning_content"), "audio": {"id": c("assistant_audio")},
             "function_call": {"name": c("fc_name"), "arguments": json.dumps({"a": c("fc_args")})},
             "tool_calls": [{"id": c("tc_id"), "type": "function",
                             "function": {"name": c("tc_name"), "arguments": json.dumps({"a": c("tc_args")})}}]},
            {"role": "tool", "tool_call_id": c("tool_call_id"), "content": f"output {c('tool_output')}"},
            {"role": "function", "name": c("fn_role_name"), "content": f"legacy {c('function_output')}"},
        ],
        "tools": [{"type": "function", "function": {
            "name": c("tool_name"), "description": f"does {c('tool_desc')}",
            "parameters": {"type": "object", "properties": {"x": {"type": "string", "description": c("tool_param_desc"),
                                                                  "enum": [c("tool_enum")]}}}}}],
        "functions": [{"name": c("functions_name"), "description": c("functions_desc"), "parameters": {"type": "object"}}],
        "tool_choice": {"type": "function", "function": {"name": c("tool_choice_name")}},
        "response_format": {"type": "json_schema", "json_schema": {"name": c("rf_name"), "schema": {
            "type": "object", "description": c("rf_desc"), "properties": {"q": {"type": "string", "description": c("rf_prop")}}}}},
        "stop": [c("stop1"), "END"],
        "logit_bias": {"50256": -100},
        "prediction": {"type": "content", "content": c("prediction")},
        "suffix": c("suffix"),
        "user": c("user"), "metadata": {"k": c("metadata")}, "safety_identifier": c("safety"), "brand_new_field": c("unknown"),
        "temperature": 0.2, "max_tokens": 50, "stream": False,
    }


class Recorder(BaseHTTPRequestHandler):
    seen: list = []

    def log_message(self, *a):
        pass

    def do_POST(self):
        Recorder.seen.append(self.rfile.read(int(self.headers["Content-Length"])).decode("utf-8"))
        raw = json.dumps({"choices": [{"index": 0, "message": {"role": "assistant", "content": "ok"}}]}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)


def start_gateway(red, **kw):
    up = ThreadingHTTPServer(("127.0.0.1", 0), Recorder)
    threading.Thread(target=up.serve_forever, daemon=True).start()
    handler = gateway.make_handler(red, f"http://127.0.0.1:{up.server_address[1]}/v1", 10, **kw)
    handler.log_message = lambda *a, **k: None
    gw = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    gw.daemon_threads = True
    threading.Thread(target=gw.serve_forever, daemon=True).start()
    return up, gw, f"http://127.0.0.1:{gw.server_address[1]}"


def stop(up, gw):
    for s in (gw, up):
        s.shutdown()
        s.server_close()


def post(base, path, body):
    req = urllib.request.Request(base + path, data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json", "Authorization": "Bearer test-not-a-key"})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")


class EveryFieldIsChecked(unittest.TestCase):
    def test_no_canary_leaves_through_any_field_and_the_detector_saw_each_text_field(self):
        det = LabelledFakeDetector()
        out = gateway.redact_body(everything_body(), det, Mapping())
        sent = json.dumps(out)
        self.assertNotIn("CANARY-7f3a", sent)
        seen = " ".join(t for t, _ in det.seen)
        for field in ("system", "part_text", "msg_name", "refusal", "reasoning", "reasoning_content", "fc_name", "fc_args",
                      "tc_id", "tc_name", "tc_args", "tool_call_id", "tool_output", "fn_role_name", "function_output",
                      "tool_name", "tool_desc", "tool_param_desc", "tool_enum", "functions_name", "functions_desc",
                      "tool_choice_name", "rf_name", "rf_desc", "rf_prop", "stop1", "prediction", "suffix"):
            self.assertIn(c(field), seen, f"the detector never saw the {field} field")
        self.assertTrue(all(deep for _, deep in det.seen), "every string gets the deep pass, whatever its field or role")

    def test_unknown_and_identity_fields_are_dropped_and_media_is_withheld(self):
        out = gateway.redact_body(everything_body(), LabelledFakeDetector(), Mapping())
        for gone in ("user", "metadata", "safety_identifier", "brand_new_field"):
            self.assertNotIn(gone, out)
        sent = json.dumps(out)
        for leak in ("base64", "image_url", "input_audio", "file_data", "assistant_audio", "cache_control", "part_extra"):
            self.assertNotIn(leak, sent, leak)
        self.assertEqual(out["messages"][1]["content"][0]["text"].startswith("hi [CANARY_"), True)
        self.assertEqual([p["type"] for p in out["messages"][1]["content"]], ["text"] * 4)
        self.assertEqual(out["logit_bias"], {"50256": -100})
        self.assertEqual((out["temperature"], out["max_tokens"], out["stream"], out["model"]), (0.2, 50, False, "dahl-model"))

    def test_shapes_that_cannot_be_checked_are_refused_not_forwarded(self):
        det = LabelledFakeDetector()
        for bad in ({"model": f"x {c('model')}", "messages": []}, {"model": "m", "messages": [{"role": "boss", "content": "x"}]},
                    {"model": "m", "prompt": [[1, 2, 3]]}, {"model": "m", "messages": [], "logit_bias": {c("lb"): 1}},
                    {"model": "m", "messages": [], "max_tokens": "50"}, {"model": "m", "messages": [], "stream": 1},
                    {"model": "m", "messages": [], "tools": [{"type": "function", "function": {"description": {"x": object}}}]}):
            with self.assertRaises((gateway.GatewayRefusal, TypeError), msg=str(bad)[:40]):
                gateway.redact_body(bad, det, Mapping())

    def test_provider_switches_pass_only_in_their_exact_shape(self):
        det = LabelledFakeDetector()
        ok = {"model": "m", "messages": [], "enable_thinking": False, "thinking_budget": 4096, "reasoning_effort": "low",
              "thinking": {"type": "disabled"}, "chat_template_kwargs": {"enable_thinking": False}}
        self.assertEqual(gateway.redact_body(ok, det, Mapping()), ok)
        for bad in ({"thinking": {"type": c("t")}}, {"reasoning_effort": c("r")}, {"enable_thinking": c("e")},
                    {"chat_template_kwargs": {"note": c("k")}}, {"chat_template_kwargs": {"x": c("v")}}, {"thinking_budget": "9"}):
            with self.assertRaises(gateway.GatewayRefusal, msg=str(bad)):
                gateway.redact_body({"model": "m", "messages": [], **bad}, det, Mapping())

    def test_deep_nesting_is_refused(self):
        deep = cur = {}
        for _ in range(60):
            cur["x"] = {}
            cur = cur["x"]
        with self.assertRaises(gateway.GatewayRefusal):
            gateway.redact_body({"model": "m", "messages": [], "tools": [deep]}, LabelledFakeDetector(), Mapping())

    def test_at_the_http_edge_the_upstream_recorder_sees_no_canary(self):
        det = LabelledFakeDetector()
        up, gw, base = start_gateway(det)
        try:
            Recorder.seen.clear()
            code, _ = post(base, "/v1/chat/completions", everything_body())
            self.assertEqual(code, 200)
            self.assertEqual(len(Recorder.seen), 1)
            self.assertNotIn("CANARY-7f3a", Recorder.seen[0])
            self.assertNotIn("brand_new_field", Recorder.seen[0])
        finally:
            stop(up, gw)

    def test_a_detector_that_raises_means_nothing_leaves_and_nothing_is_echoed(self):
        class Boom(LabelledFakeDetector):
            def redact(self, text, mapping, deep=True, names=None):
                raise RuntimeError(f"boom while reading {text}")
        up, gw, base = start_gateway(Boom())
        try:
            Recorder.seen.clear()
            code, out = post(base, "/v1/chat/completions", {"model": "m", "messages": [{"role": "user", "content": c("boom")}]})
            self.assertEqual(code, 503)
            self.assertEqual(Recorder.seen, [])
            self.assertNotIn("CANARY", json.dumps(out))
        finally:
            stop(up, gw)


class JsonKeysAreChecked(unittest.TestCase):
    """A JSON object KEY is text too (tool schema property names, response_format schema keys, nested objects): a key built from a
    private value used to be forwarded as it was (inventory pass F, 2026-10-19). Plain lower-case schema words stay."""
    def body(self):
        k = c("key")
        return {"model": "m", "messages": [{"role": "user", "content": "hi"}],
                "tools": [{"type": "function", "function": {"name": "lookup", "description": "d", "parameters": {
                    "type": "object", "properties": {k: {"type": "string", "description": "x"}, "city_name": {"type": "string"}},
                    "required": [k]}}}],
                "response_format": {"type": "json_schema", "json_schema": {"name": "out", "schema": {"type": "object", "properties": {
                    c("rf_key"): {"type": "string"}, "ಹೆಸರು": {"type": "string"}}}}},
                "tool_choice": {"type": "function", "function": {"name": "lookup", c("tc_key"): 1}}}

    def test_no_canary_key_leaves_and_plain_schema_words_stay(self):
        det = LabelledFakeDetector()
        out = gateway.redact_body(self.body(), det, Mapping())
        sent = json.dumps(out, ensure_ascii=False)
        self.assertNotIn("CANARY-7f3a", sent)
        props = out["tools"][0]["function"]["parameters"]["properties"]
        self.assertIn("city_name", props)
        self.assertEqual(sorted(props), ["[CANARY_1]", "city_name"])
        self.assertTrue(all(k in ("city_name", "type") or k.startswith("[CANARY_") for k in props), props)
        seen = " ".join(t for t, _ in det.seen)
        for f in ("key", "rf_key", "tc_key"):
            self.assertIn(c(f), seen, f"the detector never saw the {f} key")
        self.assertIn("ಹೆಸರು", [t for t, _ in det.seen])              # a non-ASCII key goes through the detector, never forwarded unchecked

    def test_at_the_http_edge_the_recorder_sees_no_canary_key_and_the_reply_keys_are_restored(self):
        det = LabelledFakeDetector()
        up, gw, base = start_gateway(det)
        try:
            Recorder.seen.clear()
            code, _ = post(base, "/v1/chat/completions", self.body())
            self.assertEqual(code, 200)
            self.assertNotIn("CANARY-7f3a", Recorder.seen[0])
        finally:
            stop(up, gw)
        m = Mapping()
        tag = m.tag("CANARY", c("key"))
        self.assertEqual(gateway.restore_json({tag: {tag: 1}}, m), {c("key"): {c("key"): 1}})

    def test_a_key_that_is_not_text_is_refused(self):
        with self.assertRaises(gateway.GatewayRefusal):
            gateway._tree({1: "x"}, LabelledFakeDetector(), Mapping())


class MissingPiiModelFailsClosed(unittest.TestCase):
    """audit M3: GLiNER missing used to turn the deep pass off for every role without a word."""

    def test_degraded_gateway_refuses_every_model_call_and_health_says_so(self):
        det = LabelledFakeDetector(model=None)               # the PII model is not loaded
        up, gw, base = start_gateway(det, require_deep=True)
        try:
            Recorder.seen.clear()
            for body in ({"model": "m", "messages": [{"role": "system", "content": c("sys")}]},
                         {"model": "m", "messages": [{"role": "user", "content": c("user")}]},
                         {"model": "m", "prompt": c("prompt")}):
                code, out = post(base, "/v1/chat/completions" if "messages" in body else "/v1/completions", body)
                self.assertEqual(code, 503)
                self.assertIn("degraded", out["error"]["message"])
                self.assertNotIn("CANARY", json.dumps(out))
            self.assertEqual(Recorder.seen, [], "nothing may reach the public model while the PII model is missing")
            self.assertEqual(det.seen, [], "the payload is not even read, let alone logged")
            with urllib.request.urlopen(base + "/health", timeout=5) as r:
                health = json.loads(r.read())
            self.assertEqual((health["ok"], health["ready"], health["public_egress"], health["pii_model"]), (True, False, False, False))
            self.assertEqual(health["degraded"], ["pii_model_missing"])
            self.assertGreaterEqual(health["refused"], 3)
        finally:
            stop(up, gw)

    def test_a_healthy_gateway_reports_ready(self):
        up, gw, base = start_gateway(LabelledFakeDetector(), require_deep=True)
        try:
            with urllib.request.urlopen(base + "/health", timeout=5) as r:
                health = json.loads(r.read())
            self.assertEqual((health["ready"], health["public_egress"], health["degraded"]), (True, True, []))
            code, _ = post(base, "/v1/chat/completions", {"model": "m", "messages": [{"role": "user", "content": "hi"}]})
            self.assertEqual(code, 200)
        finally:
            stop(up, gw)

    def test_serve_defaults_to_requiring_the_deep_pass_and_the_owner_can_switch_it_off(self):
        old = os.environ.pop("JARVIS_PRIVACY_REQUIRE_DEEP", None)
        try:
            self.assertTrue(gateway.deep_required())
            os.environ["JARVIS_PRIVACY_REQUIRE_DEEP"] = "0"
            self.assertFalse(gateway.deep_required())
        finally:
            os.environ.pop("JARVIS_PRIVACY_REQUIRE_DEEP", None)
            if old is not None:
                os.environ["JARVIS_PRIVACY_REQUIRE_DEEP"] = old

    def test_redact_preview_says_what_was_really_active(self):
        up, gw, base = start_gateway(LabelledFakeDetector(model=None), require_deep=True)
        try:
            code, out = post(base, "/redact", {"text": f"x {c('preview')}", "names": True})
            self.assertEqual(code, 200)
            self.assertEqual((out["deep"], out["names"], out["ready"]), (False, True, False))
        finally:
            stop(up, gw)

    def test_without_the_redaction_library_the_gateway_cannot_even_start(self):
        """No Presidio = no `redactor` module = the process exits at import; it never serves unredacted."""
        code = ("import sys; sys.modules['presidio_analyzer']=None; sys.modules['presidio_analyzer.nlp_engine']=None; "
                f"sys.path.insert(0, {HERE!r}); import gateway")
        proc = subprocess.run([sys.executable, "-I", "-c", code], capture_output=True, text=True, timeout=60)
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("ModuleNotFoundError", proc.stderr)


class AuthRecorder(BaseHTTPRequestHandler):
    seen: list = []

    def log_message(self, *a):
        pass

    def do_POST(self):
        self.rfile.read(int(self.headers["Content-Length"]))
        AuthRecorder.seen.append(self.headers.get("Authorization"))
        raw = json.dumps({"choices": [{"index": 0, "message": {"role": "assistant", "content": "ok"}}]}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)


class FirstProviderKeyFile(unittest.TestCase):
    """The face sends the placeholder "jarvis-local" (or nothing): the first provider's key file is used. A real key passes through."""

    def setUp(self):
        import tempfile
        gateway._keys.clear()
        AuthRecorder.seen = []
        self.up = ThreadingHTTPServer(("127.0.0.1", 0), AuthRecorder)
        threading.Thread(target=self.up.serve_forever, daemon=True).start()
        self.secret_dir = tempfile.mkdtemp()
        os.makedirs(os.path.join(self.secret_dir, "providers"))
        with open(os.path.join(self.secret_dir, "providers", "first.key"), "w") as f:
            f.write("first-test-key")                            # made-up test value
        self._old_root = gateway.SECRET_ROOT
        gateway.SECRET_ROOT = self.secret_dir
        provs = [{"name": "first", "base_url": f"http://127.0.0.1:{self.up.server_address[1]}/v1", "key": "from_request", "key_file": "providers/first.key"}]
        handler = gateway.make_handler(LabelledFakeDetector(), provs, 10)
        handler.log_message = lambda *a, **k: None
        self.gw = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self.gw.daemon_threads = True
        threading.Thread(target=self.gw.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.gw.server_address[1]}/v1/chat/completions"

    def tearDown(self):
        gateway.SECRET_ROOT = self._old_root
        gateway._keys.clear()
        for s in (self.gw, self.up):
            s.shutdown()
            s.server_close()

    def call(self, auth):
        headers = {"Content-Type": "application/json"}
        if auth is not None:
            headers["Authorization"] = auth
        req = urllib.request.Request(self.url, data=json.dumps({"model": "m", "messages": [{"role": "user", "content": "hello"}]}).encode(), headers=headers)
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status

    def test_the_placeholder_and_no_key_get_the_key_file(self):
        self.assertEqual(self.call("Bearer jarvis-local"), 200)
        self.assertEqual(self.call(None), 200)
        self.assertEqual(AuthRecorder.seen, ["Bearer first-test-key", "Bearer first-test-key"])

    def test_a_callers_own_key_passes_through_untouched(self):
        self.assertEqual(self.call("Bearer caller-key"), 200)
        self.assertEqual(AuthRecorder.seen, ["Bearer caller-key"])

    def test_a_missing_key_file_leaves_the_request_as_it_came(self):
        os.remove(os.path.join(self.secret_dir, "providers", "first.key"))
        self.assertEqual(self.call("Bearer jarvis-local"), 200)
        self.assertEqual(AuthRecorder.seen, ["Bearer jarvis-local"])


class ClaudeIsNeverAProvider(unittest.TestCase):
    """Owner, 2026-10-23 (D35): JARVIS never calls Claude. An 'anthropic' entry in a providers file is dropped, even a preferred one."""

    def test_anthropic_entries_are_dropped_and_dahl_is_first(self):
        import tempfile
        cfg = {"providers": [
            {"name": "claude", "api": "anthropic", "preferred": True, "enabled": True, "base_url": "https://api.anthropic.com",
             "key_file": "claude.key", "model": "claude-sonnet-5-5"},
            {"name": "dahl", "base_url": "https://inference.dahl.global/v1", "enabled": True, "key": "from_request"}]}
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as f:
            json.dump(cfg, f)
        try:
            provs, _ = gateway.load_providers(f.name)
        finally:
            os.unlink(f.name)
        self.assertEqual([p["name"] for p in provs], ["dahl"])
        self.assertFalse(any(p.get("api") == "anthropic" or "anthropic" in p["base_url"] for p in provs))

    def test_a_claude_only_file_falls_back_to_the_default_upstream(self):
        import tempfile
        cfg = {"providers": [{"name": "claude", "api": "anthropic", "enabled": True, "base_url": "https://api.anthropic.com",
                              "key_file": "claude.key", "model": "m"}]}
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as f:
            json.dump(cfg, f)
        try:
            provs, _ = gateway.load_providers(f.name)
        finally:
            os.unlink(f.name)
        self.assertEqual(len(provs), 1)
        self.assertNotIn("anthropic", provs[0]["base_url"])


if __name__ == "__main__":
    unittest.main()
