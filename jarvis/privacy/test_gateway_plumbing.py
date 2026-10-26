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
import time
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

    def test_tool_and_argument_names_are_kept_so_the_model_can_call_them_but_a_speakers_name_and_descriptions_are_still_checked(self):
        """2026-10-25: the gateway rewrote builtIn_mcpListTools to [API_KEY_1] and a required argument to [USERNAME_2]: no tool call of the face could work."""
        det = LabelledFakeDetector()
        body = {"model": "m", "messages": [{"role": "user", "name": "Alice", "content": "hi"},
                                           {"role": "assistant", "content": None, "tool_calls": [{"id": "call_1", "type": "function", "function": {"name": "builtIn_mcpCallTool", "arguments": "{\"name\": \"x\"}"}}]}],
                "tools": [{"type": "function", "function": {"name": "builtIn_mcpListTools", "description": "List the tools of Bob Example",
                                                            "parameters": {"type": "object", "properties": {"call_summary": {"type": "string"}, "callIcon": {"type": "string"}},
                                                                           "required": ["call_summary", "callIcon", "bad name"]}}}],
                "tool_choice": {"type": "function", "function": {"name": "builtIn_mcpListTools"}}}
        out = gateway.redact_body(body, det, Mapping())
        fn = out["tools"][0]["function"]
        self.assertEqual(fn["name"], "builtIn_mcpListTools")
        self.assertEqual(sorted(fn["parameters"]["properties"]), ["callIcon", "call_summary"])
        self.assertEqual(fn["parameters"]["required"][:2], ["call_summary", "callIcon"])
        self.assertEqual(out["tool_choice"]["function"]["name"], "builtIn_mcpListTools")
        self.assertEqual(out["messages"][1]["tool_calls"][0]["function"]["name"], "builtIn_mcpCallTool")
        seen = [t for t, _ in det.seen]
        for must_check in ("Alice", "List the tools of Bob Example", "bad name"):
            self.assertIn(must_check, seen, must_check)                  # a speaker's name, a description and a non-identifier still go through the detector
        for kept in ("builtIn_mcpListTools", "call_summary", "builtIn_mcpCallTool"):
            self.assertNotIn(kept, seen, kept)

    def test_a_key_that_is_not_text_is_refused(self):
        with self.assertRaises(gateway.GatewayRefusal):
            gateway._tree({1: "x"}, LabelledFakeDetector(), Mapping())


class ShippedProvidersFile(unittest.TestCase):
    """2026-10-25: Cerebras joins as the second fallback (Dahl is often busy; Groq's 8K tokens a minute cannot take the face's big tool requests)."""

    def test_cerebras_follows_groq_and_never_holds_a_key(self):
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "providers.json")
        with open(path, encoding="utf-8") as fh:
            text = fh.read()
        names = [p["name"] for p in json.loads(text)["providers"]]
        self.assertEqual(names[:3], ["dahl", "groq", "cerebras"])
        c = [p for p in json.loads(text)["providers"] if p["name"] == "cerebras"][0]
        self.assertTrue(c["key_file"].startswith("providers/") and c["model"])     # a key FILE NAME and a model, as a fallback needs
        self.assertNotIn("api_key", c)
        self.assertFalse(re.search(r"csk-[A-Za-z0-9]{10,}", text))                  # no key text in the file


class LocalModelIsTheLastResort(unittest.TestCase):
    """2026-10-25 (owner: use the local uncensored MoE; the online providers are managed by the gateway): the last link of the chain is a model on this PC
    (llama-swap on loopback), marked "local": true, with no key. Every online provider resting or refusing means the answer comes from the laptop."""

    def start(self, local_flag=True, local_url=None, first_code=429, first_msg="busy", prefer=False, local_code=200):
        seen = {"first": 0, "local": 0, "local_auth": None}

        def handler_for(name, code):
            class H(BaseHTTPRequestHandler):
                def log_message(self, *a):
                    pass

                def do_POST(self):
                    self.rfile.read(int(self.headers["Content-Length"]))
                    seen[name] += 1
                    if name == "local":
                        seen["local_auth"] = self.headers.get("Authorization")
                    raw = json.dumps({"choices": [{"index": 0, "message": {"role": "assistant", "content": name}}]} if code == 200 else {"error": {"message": first_msg if name == "first" else "busy"}}).encode()
                    self.send_response(code)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(raw)))
                    self.end_headers()
                    self.wfile.write(raw)
            return H
        servers = []
        for name, code in (("first", first_code), ("local", local_code)):
            sv = ThreadingHTTPServer(("127.0.0.1", 0), handler_for(name, code))
            threading.Thread(target=sv.serve_forever, daemon=True).start()
            servers.append(sv)
        self.addCleanup(lambda: [(sv.shutdown(), sv.server_close()) for sv in servers])
        import tempfile
        gateway._keys.clear()
        secret_dir = tempfile.mkdtemp()
        os.makedirs(os.path.join(secret_dir, "providers"))
        with open(os.path.join(secret_dir, "providers", "first.key"), "w") as fh:
            fh.write("made-up-test-key")
        old_root, gateway.SECRET_ROOT = gateway.SECRET_ROOT, secret_dir
        self.addCleanup(lambda: setattr(gateway, "SECRET_ROOT", old_root))
        self.addCleanup(gateway._keys.clear)
        cfg = {"cooldown_seconds": 60, "providers": [
            {"name": "first", "enabled": True, "base_url": f"http://127.0.0.1:{servers[0].server_address[1]}/v1", "key": "from_request", "key_file": "providers/first.key"},
            {"name": "local", "enabled": True, "local": local_flag, "prefer_first": prefer, "model": "local-chat",
             "base_url": local_url or f"http://127.0.0.1:{servers[1].server_address[1]}/v1"}]}
        path = os.path.join(secret_dir, "providers.json")
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(cfg, fh)
        provs, cool = gateway.load_providers(path)
        handler = gateway.make_handler(LabelledFakeDetector(), provs, 10, cool)
        handler.log_message = lambda *a, **k: None
        gw = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        gw.daemon_threads = True
        threading.Thread(target=gw.serve_forever, daemon=True).start()
        self.addCleanup(lambda: (gw.shutdown(), gw.server_close()))
        gateway._cooldown.clear()
        return f"http://127.0.0.1:{gw.server_address[1]}", seen, [p["name"] for p in provs]

    def test_a_busy_online_provider_is_answered_by_the_local_model_without_a_key(self):
        base, seen, names = self.start()
        self.assertEqual(names, ["first", "local"])
        code, out = post(base, "/v1/chat/completions", {"model": "x", "messages": [{"role": "user", "content": "hi"}]})
        self.assertEqual(code, 200)
        self.assertEqual((seen["first"], seen["local"]), (1, 1))
        self.assertIsNone(seen["local_auth"])                                # no key and no caller placeholder went to the local server

    def test_a_400_about_bad_tool_json_moves_on_to_the_next_provider(self):
        base, seen, names = self.start(first_code=400, first_msg="Unterminated string starting at: line 1 column 812")
        code, out = post(base, "/v1/chat/completions", {"model": "x", "messages": [{"role": "user", "content": "hi"}]})
        self.assertEqual(code, 200)
        self.assertEqual((seen["first"], seen["local"]), (1, 1))

    def test_any_other_400_is_still_the_callers_answer(self):
        base, seen, names = self.start(first_code=400, first_msg="model not found")
        code, out = post(base, "/v1/chat/completions", {"model": "x", "messages": [{"role": "user", "content": "hi"}]})
        self.assertEqual(code, 400)
        self.assertEqual(seen["local"], 0)

    def test_a_preferred_local_model_answers_first_and_the_online_provider_is_not_touched(self):
        base, seen, names = self.start(first_code=200, prefer=True)
        code, out = post(base, "/v1/chat/completions", {"model": "x", "messages": [{"role": "user", "content": "hi"}]})
        self.assertEqual(code, 200)
        self.assertEqual((seen["first"], seen["local"]), (0, 1))

    def test_a_reasoning_caller_can_ask_for_the_online_chain_first(self):
        base, seen, names = self.start(first_code=200, prefer=True)
        req = urllib.request.Request(base + "/v1/chat/completions", data=json.dumps({"model": "x", "messages": [{"role": "user", "content": "hi"}]}).encode(),
                                     headers={"Content-Type": "application/json", "Authorization": "Bearer test-not-a-key", "X-Jarvis-Route": "online"})
        with urllib.request.urlopen(req, timeout=10) as r:
            self.assertEqual(r.status, 200)
        self.assertEqual((seen["first"], seen["local"]), (1, 0))             # the preferred local model was skipped for this caller

    def test_a_preferred_local_model_that_errors_falls_back_to_online(self):
        base, seen, names = self.start(first_code=200, prefer=True, local_code=404)
        code, out = post(base, "/v1/chat/completions", {"model": "x", "messages": [{"role": "user", "content": "hi"}]})
        self.assertEqual(code, 200)
        self.assertEqual((seen["local"], seen["first"]), (1, 1))

    def test_a_pin_file_sends_the_request_to_that_provider_only(self):
        base, seen, names = self.start()
        pin = os.path.join(tempfile_dir := __import__("tempfile").mkdtemp(), "pin.txt")
        with open(pin, "w") as fh:
            fh.write("local")
        os.environ["JARVIS_GATEWAY_PIN_FILE"] = pin
        self.addCleanup(lambda: os.environ.pop("JARVIS_GATEWAY_PIN_FILE", None))
        code, out = post(base, "/v1/chat/completions", {"model": "x", "messages": [{"role": "user", "content": "hi"}]})
        self.assertEqual(code, 200)
        self.assertEqual((seen["first"], seen["local"]), (0, 1))              # the busy first provider was not even tried

    def test_a_provider_not_on_loopback_is_never_treated_as_local(self):
        base, seen, names = self.start(local_url="http://203.0.113.5:8080/v1")
        self.assertEqual(names, ["first"])                                    # dropped: "local" only counts for 127.0.0.1, localhost and ::1

    def test_without_the_local_flag_a_keyless_fallback_is_still_dropped(self):
        base, seen, names = self.start(local_flag=False)
        self.assertEqual(names, ["first"])


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


class FallbackTooSmall(unittest.TestCase):
    """2026-10-25: Dahl said 429 (busy) to the face's 9k-token request, the gateway fell back to Groq (8000 tokens a minute in all), and Groq's 413 reached the face as
    "Remote sent 413 response". A fallback that declares a token limit is now skipped for a request it cannot take, so the caller gets the first provider's own answer."""

    def start(self, status):
        counts = {"first": 0, "small": 0}

        def handler_for(name, code):
            class H(BaseHTTPRequestHandler):
                def log_message(self, *a):
                    pass

                def do_POST(self):
                    self.rfile.read(int(self.headers["Content-Length"]))
                    counts[name] += 1
                    raw = json.dumps({"choices": [{"index": 0, "message": {"role": "assistant", "content": name}}]} if code == 200 else {"error": {"message": "busy"}}).encode()
                    self.send_response(code)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(raw)))
                    self.end_headers()
                    self.wfile.write(raw)
            return H
        servers = []
        for name, code in (("first", status), ("small", 200)):
            sv = ThreadingHTTPServer(("127.0.0.1", 0), handler_for(name, code))
            threading.Thread(target=sv.serve_forever, daemon=True).start()
            servers.append(sv)
        import tempfile
        gateway._keys.clear()
        secret_dir = tempfile.mkdtemp()
        os.makedirs(os.path.join(secret_dir, "providers"))
        for f in ("first.key", "small.key"):
            with open(os.path.join(secret_dir, "providers", f), "w") as fh:
                fh.write("made-up-test-key")
        old_root, gateway.SECRET_ROOT = gateway.SECRET_ROOT, secret_dir
        self.addCleanup(lambda: setattr(gateway, "SECRET_ROOT", old_root))
        self.addCleanup(gateway._keys.clear)
        self.addCleanup(lambda: [(sv.shutdown(), sv.server_close()) for sv in servers])
        provs = [{"name": "first", "base_url": f"http://127.0.0.1:{servers[0].server_address[1]}/v1", "key": "from_request", "key_file": "providers/first.key"},
                 {"name": "small", "base_url": f"http://127.0.0.1:{servers[1].server_address[1]}/v1", "key_file": "providers/small.key", "model": "m", "max_total_tokens": 2000}]
        handler = gateway.make_handler(LabelledFakeDetector(), provs, 10)
        handler.log_message = lambda *a, **k: None
        gw = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        gw.daemon_threads = True
        threading.Thread(target=gw.serve_forever, daemon=True).start()
        self.addCleanup(lambda: (gw.shutdown(), gw.server_close()))
        gateway._cooldown.clear()
        return f"http://127.0.0.1:{gw.server_address[1]}", counts

    def test_a_request_the_fallback_cannot_take_is_not_sent_there(self):
        base, counts = self.start(429)
        code, out = post(base, "/v1/chat/completions", {"model": "x", "messages": [{"role": "user", "content": "word " * 4000}]})     # ~5k tokens: over the fallback's 2000
        self.assertEqual(code, 429)                                       # the first provider's own answer, not a 413 from the fallback
        self.assertEqual(counts, {"first": 1, "small": 0})

    def test_a_small_request_still_falls_back(self):
        gateway._cooldown.clear()
        base, counts = self.start(429)
        code, out = post(base, "/v1/chat/completions", {"model": "x", "messages": [{"role": "user", "content": "hi"}]})
        self.assertEqual(code, 200)
        self.assertEqual(counts, {"first": 1, "small": 1})


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


def sse(*events):
    """A recorded-style provider stream: one `data:` line per event, a blank line after it, then [DONE]."""
    out = []
    for e in events:
        out.append("data: " + (e if isinstance(e, str) else json.dumps(e)) + "\n\n")
    out.append("data: [DONE]\n\n")
    return "".join(out)


def delta(content=None, reasoning=None, finish=None, role=None, **extra):
    d = {}
    if role:
        d["role"] = role
    if content is not None:
        d["content"] = content
    if reasoning is not None:
        d["reasoning_content"] = reasoning
    d.update(extra)
    return {"id": "x", "object": "chat.completion.chunk", "choices": [{"index": 0, "delta": d, "finish_reason": finish}]}


def run_events(text, hide=True, mapping=None):
    """What the face would read: the data payloads that survive SseRestorer, and the visible text they add up to."""
    sr = gateway.SseRestorer(mapping or Mapping(), hide_reasoning=hide)
    kept, visible = [], ""
    for block in text.split("\n\n"):
        if not block.startswith("data:"):
            continue
        out = sr.event(block[5:].strip())
        if out is None:
            continue
        kept.append(out)
        if out.strip() != "[DONE]":
            for ch in json.loads(out).get("choices") or []:
                visible += (ch.get("delta") or {}).get("content") or ""
    return kept, visible


class ThinkStripperCases(unittest.TestCase):
    def feed_all(self, *chunks):
        s = gateway.ThinkStripper()
        return "".join(s.feed(c) for c in chunks) + s.flush()

    def test_a_whole_thought_before_the_answer_is_removed_and_the_answer_starts_clean(self):
        self.assertEqual(self.feed_all("<think>The owner wants a deck.</think>\n\nHere is the deck."), "Here is the deck.")

    def test_tags_split_across_chunks_are_still_found(self):
        self.assertEqual(self.feed_all("<thi", "nk>plan", " it</th", "ink>", "\nDone."), "Done.")
        self.assertEqual(self.feed_all("Hello <", "think>x</think>there"), "Hello there")

    def test_an_opening_tag_that_never_closes_hides_everything_after_it(self):
        self.assertEqual(self.feed_all("<think>\nLet me think", " about this for a long time"), "")
        self.assertEqual(self.feed_all("Sure. <think>and then I wonder"), "Sure. ")

    def test_case_and_the_other_tag_names(self):
        self.assertEqual(self.feed_all("<THINK>a</THINK>b<thinking>c</thinking>d<thought>e</thought>f<reasoning>g</reasoning>h"), "bdfh")

    def test_a_stray_closing_tag_is_removed_but_the_text_before_it_stays(self):
        self.assertEqual(self.feed_all("answer</think> more"), "answer more")

    def test_text_that_only_looks_like_a_tag_is_kept(self):
        self.assertEqual(self.feed_all("if a <b and b> c then 1 < 2 and <thin air"), "if a <b and b> c then 1 < 2 and <thin air")
        self.assertEqual(self.feed_all("ends with <thi"), "ends with <thi")

    def test_a_normal_answer_passes_unchanged_chunk_by_chunk(self):
        s = gateway.ThinkStripper()
        self.assertEqual([s.feed(x) for x in ("Good ", "morning, ", "sir.")], ["Good ", "morning, ", "sir."])
        self.assertEqual(s.flush(), "")

    def test_two_thoughts_in_one_reply(self):
        self.assertEqual(self.feed_all("<think>a</think>one<think>b</think>two"), "onetwo")


class FaceReasoningStream(unittest.TestCase):
    """The shape GLM/Qwen style servers stream: reasoning_content deltas first, then content, then a finish chunk and usage."""

    def test_reasoning_deltas_are_dropped_and_the_answer_arrives_whole(self):
        stream = sse(delta(role="assistant", content=""),
                     delta(reasoning="The user wants "), delta(reasoning="a presentation. "), delta(reasoning="I should call a tool."),
                     delta(content="Your deck"), delta(content=" is ready."), delta(finish="stop"),
                     {"choices": [], "usage": {"prompt_tokens": 9, "completion_tokens": 40}})
        kept, visible = run_events(stream)
        self.assertEqual(visible, "Your deck is ready.")
        self.assertNotIn("reasoning", "".join(kept))
        self.assertFalse(any('"reasoning_content"' in k for k in kept))
        self.assertEqual(len(kept), 1 + 2 + 1 + 1 + 1, "role chunk, two content chunks, finish, usage and [DONE]; the three reasoning-only chunks are gone")
        self.assertTrue(any('"usage"' in k for k in kept) and kept[-1] == "[DONE]")

    def test_the_default_keeps_everything_for_callers_that_are_not_the_face(self):
        stream = sse(delta(reasoning="hidden?"), delta(content="<think>x</think>y"), delta(finish="stop"))
        kept, visible = run_events(stream, hide=False)
        self.assertIn("hidden?", "".join(kept))
        self.assertEqual(visible, "<think>x</think>y")

    def test_think_text_in_the_content_stream_is_removed_even_when_a_tag_is_split(self):
        stream = sse(delta(content="<thi"), delta(content="nk>\nThe owner asks "), delta(content="for a report</th"), delta(content="ink>\n\nI am "),
                     delta(content="making it."), delta(finish="stop"))
        kept, visible = run_events(stream)
        self.assertEqual(visible, "I am making it.")
        self.assertNotIn("think", "".join(kept))

    def test_a_stream_that_opens_think_and_never_closes_shows_nothing_and_still_finishes(self):
        stream = sse(delta(content="<think>\nHmm, "), delta(content="let me see"), delta(finish="length"))
        kept, visible = run_events(stream)
        self.assertEqual(visible, "")
        self.assertIn('"finish_reason": "length"', "".join(kept))
        self.assertEqual(kept[-1], "[DONE]")

    def test_a_held_back_partial_tag_is_released_with_the_finish_chunk(self):
        stream = sse(delta(content="a < b and <thi"), delta(finish="stop"))
        _, visible = run_events(stream)
        self.assertEqual(visible, "a < b and <thi")

    def test_tool_calls_survive_and_a_reasoning_only_event_that_also_has_tool_calls_is_kept(self):
        call = {"index": 0, "id": "c1", "type": "function", "function": {"name": "jarvis_do", "arguments": "{\"task\":\"x\"}"}}
        stream = sse(delta(reasoning="I will call jarvis_do", tool_calls=[call]), delta(finish="tool_calls"))
        kept, _ = run_events(stream)
        joined = "".join(kept)
        self.assertIn("jarvis_do", joined)
        self.assertNotIn("I will call", joined)

    def test_placeholders_are_still_restored_in_the_visible_answer(self):
        m = Mapping()
        tag = m.tag("CANARY", c("name"))
        stream = sse(delta(reasoning=f"think about {tag}"), delta(content=f"<think>{tag}</think>Hello {tag[:6]}"), delta(content=f"{tag[6:]}!"), delta(finish="stop"))
        kept, visible = run_events(stream, mapping=m)
        self.assertEqual(visible, f"Hello {c('name')}!")

    def test_non_streamed_reply_loses_reasoning_fields_and_think_text(self):
        obj = {"choices": [{"index": 0, "message": {"role": "assistant", "content": "<think>plan</think>\nDone.", "reasoning_content": "plan",
                                                    "reasoning": "plan", "tool_calls": [{"id": "1"}]}}]}
        out = gateway.hide_reasoning_json(obj)
        msg = out["choices"][0]["message"]
        self.assertEqual(msg["content"], "Done.")
        self.assertNotIn("reasoning_content", msg)
        self.assertNotIn("reasoning", msg)
        self.assertEqual(msg["tool_calls"], [{"id": "1"}])


class SseUpstream(BaseHTTPRequestHandler):
    """A fake provider that streams (or answers) with reasoning, and records the body and the key it was called with."""
    bodies: list = []
    keys: list = []
    stream_text = ""

    def log_message(self, *a):
        pass

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        SseUpstream.bodies.append(body)
        SseUpstream.keys.append(self.headers.get("Authorization"))
        if body.get("stream"):
            raw = SseUpstream.stream_text.encode()
            ctype = "text/event-stream"
        else:
            raw = json.dumps({"choices": [{"index": 0, "message": {"role": "assistant", "content": "<think>plan</think>Done.", "reasoning_content": "plan"}}]}).encode()
            ctype = "application/json"
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)


class FaceReasoningAtTheHttpEdge(unittest.TestCase):
    def setUp(self):
        import tempfile
        gateway._keys.clear()
        SseUpstream.bodies, SseUpstream.keys = [], []
        SseUpstream.stream_text = sse(delta(reasoning="secret plan"), delta(content="<think>more plan</think>Hello, sir."), delta(finish="stop"))
        self.up = ThreadingHTTPServer(("127.0.0.1", 0), SseUpstream)
        threading.Thread(target=self.up.serve_forever, daemon=True).start()
        self.secret_dir = tempfile.mkdtemp()
        os.makedirs(os.path.join(self.secret_dir, "providers"))
        for f in ("first.key", "second.key"):
            with open(os.path.join(self.secret_dir, "providers", f), "w") as fh:
                fh.write("made-up-test-key")
        self._old_root, gateway.SECRET_ROOT = gateway.SECRET_ROOT, self.secret_dir
        self.addCleanup(lambda: setattr(gateway, "SECRET_ROOT", self._old_root))
        self.addCleanup(gateway._keys.clear)
        self.addCleanup(lambda: (self.up.shutdown(), self.up.server_close()))

    def gateway(self, **first):
        url = f"http://127.0.0.1:{self.up.server_address[1]}/v1"
        provs = [{"name": "first", "base_url": url, "key": "from_request", "key_file": "providers/first.key", **first}]
        handler = gateway.make_handler(LabelledFakeDetector(), provs, 10)
        handler.log_message = lambda *a, **k: None
        gw = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        gw.daemon_threads = True
        threading.Thread(target=gw.serve_forever, daemon=True).start()
        self.addCleanup(lambda: (gw.shutdown(), gw.server_close()))
        return f"http://127.0.0.1:{gw.server_address[1]}"

    def call(self, base, key, stream):
        req = urllib.request.Request(base + "/v1/chat/completions", data=json.dumps({"model": "m", "stream": stream, "messages": [{"role": "user", "content": "hi"}]}).encode(),
                                     headers={"Content-Type": "application/json", "Authorization": f"Bearer {key}"})
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.read().decode()

    def test_the_face_gets_a_stream_without_reasoning_and_other_callers_get_it_as_sent(self):
        base = self.gateway()
        face = self.call(base, "jarvis-local", True)
        self.assertNotIn("secret plan", face)
        self.assertNotIn("think", face)
        self.assertIn("Hello, sir.", face)
        self.assertTrue(face.rstrip().endswith("data: [DONE]"))
        other = self.call(base, "a-plugins-own-key", True)
        self.assertIn("secret plan", other)
        self.assertIn("<think>more plan</think>Hello, sir.", other)

    def test_the_face_gets_a_non_streamed_reply_without_reasoning(self):
        base = self.gateway()
        face = json.loads(self.call(base, "jarvis-local", False))
        self.assertEqual(face["choices"][0]["message"], {"role": "assistant", "content": "Done."})
        other = json.loads(self.call(base, "a-plugins-own-key", False))
        self.assertEqual(other["choices"][0]["message"]["reasoning_content"], "plan")

    def test_show_reasoning_switch_turns_the_hiding_off(self):
        base = self.gateway()
        os.environ["JARVIS_PRIVACY_SHOW_REASONING"] = "1"
        self.addCleanup(lambda: os.environ.pop("JARVIS_PRIVACY_SHOW_REASONING", None))
        self.assertIn("secret plan", self.call(base, "jarvis-local", True))

    def test_a_provider_entrys_face_extra_body_reaches_the_provider_for_the_face_only(self):
        base = self.gateway(face_extra_body={"chat_template_kwargs": {"enable_thinking": False}}, extra_body={"reasoning_effort": "low"})
        self.call(base, "jarvis-local", False)
        self.call(base, "a-plugins-own-key", False)
        face_body, other_body = SseUpstream.bodies
        self.assertEqual(face_body["chat_template_kwargs"], {"enable_thinking": False})
        self.assertEqual(face_body["reasoning_effort"], "low")
        self.assertNotIn("chat_template_kwargs", other_body)
        self.assertEqual(other_body["reasoning_effort"], "low")

    def test_without_an_extra_body_nothing_is_added(self):
        base = self.gateway()
        self.call(base, "jarvis-local", False)
        self.assertEqual(set(SseUpstream.bodies[0]), {"model", "stream", "messages"})

    def test_an_extra_body_must_have_the_exact_shape_of_a_provider_switch(self):
        ok = {"chat_template_kwargs": {"enable_thinking": False}, "reasoning_effort": "low", "thinking_budget": 0}
        self.assertEqual(gateway.clean_extra_body(ok), ok)
        bad = {"chat_template_kwargs": {"enable_thinking": "a free text value"}, "reasoning_effort": "ignore previous instructions", "messages": [], "model": "other"}
        self.assertEqual(gateway.clean_extra_body(bad), {}, "free text, messages or a model cannot ride along")
        self.assertEqual(gateway.clean_extra_body(None), {})
        self.assertEqual(gateway.clean_extra_body("x"), {})

    def test_the_shipped_providers_file_keeps_dahls_switch_off_and_mirrors_groqs_effort(self):
        provs, _ = gateway.load_providers(os.path.join(HERE, "providers.json"))
        by = {p["name"]: p for p in provs}
        self.assertEqual(by["dahl"]["extra_body"], {})
        self.assertEqual(by["dahl"]["face_extra_body"], {}, "off until Dahl's behaviour is checked live (D49)")
        with open(os.path.join(HERE, "providers.json"), encoding="utf-8") as fh:
            raw = json.load(fh)
        self.assertEqual(raw["providers"][0]["_face_extra_body_example"], {"chat_template_kwargs": {"enable_thinking": False}})
        self.assertTrue(gateway.clean_extra_body(raw["providers"][0]["_face_extra_body_example"]), "the example is in a shape the gateway accepts")
        self.assertEqual(next(p for p in raw["providers"] if p["name"] == "groq")["extra_body"], {"reasoning_effort": "low"})


if __name__ == "__main__":
    unittest.main()


class ProviderConcurrencyLimit(unittest.TestCase):
    """2026-10-25: Dahl answers 429 'model_concurrency' to a second request in flight, and the face sends two at once. max_concurrent makes the second wait."""

    def test_two_requests_do_not_overlap_at_a_provider_with_max_concurrent_one(self):
        state = {"now": 0, "peak": 0}
        lock = threading.Lock()

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_POST(self):
                self.rfile.read(int(self.headers["Content-Length"]))
                with lock:
                    state["now"] += 1
                    state["peak"] = max(state["peak"], state["now"])
                import time as _t
                _t.sleep(0.3)
                with lock:
                    state["now"] -= 1
                raw = json.dumps({"choices": [{"index": 0, "message": {"role": "assistant", "content": "ok"}}]}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)
        sv = ThreadingHTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=sv.serve_forever, daemon=True).start()
        self.addCleanup(lambda: (sv.shutdown(), sv.server_close()))
        provs = [{"name": "one", "enabled": True, "base_url": f"http://127.0.0.1:{sv.server_address[1]}/v1", "max_concurrent": 1}]
        gateway._sems.clear()
        handler = gateway.make_handler(LabelledFakeDetector(), provs, 10, 60.0)
        handler.log_message = lambda *a, **k: None
        gw = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        gw.daemon_threads = True
        threading.Thread(target=gw.serve_forever, daemon=True).start()
        self.addCleanup(lambda: (gw.shutdown(), gw.server_close()))
        base = f"http://127.0.0.1:{gw.server_address[1]}"
        codes = []

        def one():
            codes.append(post(base, "/v1/chat/completions", {"model": "x", "messages": [{"role": "user", "content": "hi"}]})[0])
        ts = [threading.Thread(target=one) for _ in range(2)]
        [t.start() for t in ts]
        [t.join() for t in ts]
        self.assertEqual(codes, [200, 200])
        self.assertEqual(state["peak"], 1)


class StreamedAnswersAreJudgedBeforeTheCallerSeesThem(unittest.TestCase):
    """2026-10-26 (on-screen bench): Dahl delivered 'Unterminated string' as an error event inside a 200 stream, MiniMax a final answer of nothing.
    The gateway reads the start of the stream and moves on to the next provider instead of handing the caller a dead end."""

    GOOD = [b'data: {"choices":[{"index":0,"delta":{"content":"hello"}}]}\n', b"\n", b"data: [DONE]\n", b"\n"]
    ERROR = [b'data: {"error":{"message":"Unterminated string starting at: line 1 column 104"}}\n', b"\n"]
    EMPTY = [b'data: {"choices":[{"index":0,"delta":{"role":"assistant","content":""}}]}\n', b"\n", b"data: [DONE]\n", b"\n"]

    def run_case(self, first_lines, messages):
        seen = {"first": 0, "local": 0}

        def handler_for(name, lines):
            class H(BaseHTTPRequestHandler):
                def log_message(self, *a):
                    pass

                def do_POST(self):
                    self.rfile.read(int(self.headers["Content-Length"]))
                    seen[name] += 1
                    self.send_response(200)
                    self.send_header("Content-Type", "text/event-stream")
                    self.send_header("Connection", "close")
                    self.end_headers()
                    for ln in lines:
                        self.wfile.write(ln)
                    self.wfile.flush()
            return H
        servers = []
        for name, lines in (("first", first_lines), ("local", self.GOOD)):
            sv = ThreadingHTTPServer(("127.0.0.1", 0), handler_for(name, lines))
            threading.Thread(target=sv.serve_forever, daemon=True).start()
            servers.append(sv)
        self.addCleanup(lambda: [(sv.shutdown(), sv.server_close()) for sv in servers])
        provs = [{"name": "first", "enabled": True, "base_url": f"http://127.0.0.1:{servers[0].server_address[1]}/v1"},
                 {"name": "local", "enabled": True, "local": True, "model": "local-chat", "base_url": f"http://127.0.0.1:{servers[1].server_address[1]}/v1"}]
        gateway._cooldown.clear()
        handler = gateway.make_handler(LabelledFakeDetector(), provs, 10, 60.0)
        handler.log_message = lambda *a, **k: None
        gw = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        gw.daemon_threads = True
        threading.Thread(target=gw.serve_forever, daemon=True).start()
        self.addCleanup(lambda: (gw.shutdown(), gw.server_close()))
        req = urllib.request.Request(f"http://127.0.0.1:{gw.server_address[1]}/v1/chat/completions", method="POST", headers={"Content-Type": "application/json"},
                                     data=json.dumps({"model": "x", "stream": True, "messages": messages}).encode())
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.read().decode(), seen

    def test_an_error_event_inside_a_200_stream_moves_on_to_the_next_provider(self):
        body, seen = self.run_case(self.ERROR, [{"role": "user", "content": "hi"}])
        self.assertIn("hello", body)
        self.assertEqual((seen["first"], seen["local"]), (1, 1))

    def test_an_empty_final_answer_after_a_tool_result_moves_on(self):
        msgs = [{"role": "user", "content": "hi"}, {"role": "assistant", "content": None, "tool_calls": [{"id": "1", "type": "function", "function": {"name": "f", "arguments": "{}"}}]},
                {"role": "tool", "tool_call_id": "1", "content": "ok"}]
        body, seen = self.run_case(self.EMPTY, msgs)
        self.assertIn("hello", body)
        self.assertEqual((seen["first"], seen["local"]), (1, 1))

    def test_a_good_stream_is_relayed_untouched_and_the_next_provider_is_not_asked(self):
        body, seen = self.run_case(self.GOOD, [{"role": "user", "content": "hi"}])
        self.assertIn("hello", body)
        self.assertEqual((seen["first"], seen["local"]), (1, 0))


class PinFileAndPeekRobustness(unittest.TestCase):
    """Review 2026-10-26."""

    def test_the_pin_file_survives_a_bom_and_a_utf16_file(self):
        import tempfile
        d = tempfile.mkdtemp()
        p = os.path.join(d, "pin.txt")
        os.environ["JARVIS_GATEWAY_PIN_FILE"] = p
        self.addCleanup(lambda: os.environ.pop("JARVIS_GATEWAY_PIN_FILE", None))
        open(p, "wb").write(b"\xef\xbb\xbfgroq\r\n")
        self.assertEqual(gateway.read_pin(), ("groq", ""))
        open(p, "wb").write("groq".encode("utf-16"))                        # PowerShell 5.1 '>' redirection
        self.assertEqual(gateway.read_pin(), ("", ""))

    def test_peek_judges_message_shapes_whitespace_and_broken_streams(self):
        class R(list):
            pass

        def lines(*events):
            return R([("data: " + e + "\n").encode() for e in events])
        ok_msg = lines('{"choices":[{"message":{"content":"hi"}}]}')
        self.assertEqual(gateway.peek_stream(ok_msg, whole=True)[1], "ok")
        self.assertEqual(gateway.peek_stream(lines('{"choices":[{"text":"hi"}]}'), whole=True)[1], "ok")
        self.assertEqual(gateway.peek_stream(lines('{"choices":[{"delta":{"content":"\\n"}}]}', "[DONE]"), whole=True)[1], "empty")
        self.assertEqual(gateway.peek_stream(lines('{"choices":["bad"]}', "[DONE]"), whole=True)[1], "empty")

        class Broken:
            def __iter__(self):
                yield b'data: {"choices":[{"delta":{"role":"assistant"}}]}\n'
                raise __import__("http.client").client.IncompleteRead(b"")
        self.assertEqual(gateway.peek_stream(Broken())[1], "error")


class LatencyIsMeasuredPerProvider(unittest.TestCase):
    def test_health_reports_median_redaction_first_byte_and_total_ms(self):
        gateway.STATS.pop("latency", None)
        t = StreamedAnswersAreJudgedBeforeTheCallerSeesThem()
        t.setUp() if hasattr(t, "setUp") else None
        body, seen = t.run_case(t.GOOD, [{"role": "user", "content": "hi"}])
        self.assertIn("hello", body)
        lat = gateway.STATS.get("latency", {})
        self.assertTrue(lat, lat)
        row = next(iter(lat.values()))[-1]
        self.assertTrue(all(k in row for k in ("redact_ms", "first_ms", "total_ms")))


class AStalledProviderIsGivenUpOn(unittest.TestCase):
    """2026-10-27 (reliability quick run on the laptop): Groq and Dahl sometimes sent no first byte for 45 s to 315 s; the owner's goal ended with no answer.
    A streamed request waits for the first byte of a provider that is not the last link only `first_byte_timeout_s`, then asks the next one."""

    def test_a_provider_that_never_starts_is_skipped_for_the_next(self):
        seen = {"slow": 0, "local": 0}
        release = threading.Event()

        def handler_for(name, slow):
            class H(BaseHTTPRequestHandler):
                def log_message(self, *a):
                    pass

                def do_POST(self):
                    self.rfile.read(int(self.headers["Content-Length"]))
                    seen[name] += 1
                    if slow:
                        release.wait(8)                       # no first byte
                        return
                    self.send_response(200)
                    self.send_header("Content-Type", "text/event-stream")
                    self.send_header("Connection", "close")
                    self.end_headers()
                    self.wfile.write(b'data: {"choices":[{"index":0,"delta":{"content":"hello"}}]}\n\ndata: [DONE]\n\n')
                    self.wfile.flush()
            return H
        servers = []
        for name, slow in (("slow", True), ("local", False)):
            sv = ThreadingHTTPServer(("127.0.0.1", 0), handler_for(name, slow))
            threading.Thread(target=sv.serve_forever, daemon=True).start()
            servers.append(sv)
        self.addCleanup(lambda: (release.set(), [(sv.shutdown(), sv.server_close()) for sv in servers]))
        provs = [{"name": "slow", "enabled": True, "first_byte_timeout_s": 0.6, "base_url": f"http://127.0.0.1:{servers[0].server_address[1]}/v1"},
                 {"name": "local", "enabled": True, "local": True, "model": "local-chat", "base_url": f"http://127.0.0.1:{servers[1].server_address[1]}/v1"}]
        gateway._cooldown.clear()
        handler = gateway.make_handler(LabelledFakeDetector(), provs, 20, 60.0)
        handler.log_message = lambda *a, **k: None
        gw = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        gw.daemon_threads = True
        threading.Thread(target=gw.serve_forever, daemon=True).start()
        self.addCleanup(lambda: (gw.shutdown(), gw.server_close()))
        req = urllib.request.Request(f"http://127.0.0.1:{gw.server_address[1]}/v1/chat/completions", method="POST", headers={"Content-Type": "application/json"},
                                     data=json.dumps({"model": "x", "stream": True, "messages": [{"role": "user", "content": "hi"}]}).encode())
        t0 = time.time()
        with urllib.request.urlopen(req, timeout=20) as r:
            body = r.read().decode()
        self.assertIn("hello", body)
        self.assertLess(time.time() - t0, 5)
        self.assertEqual((seen["slow"], seen["local"]), (1, 1))
