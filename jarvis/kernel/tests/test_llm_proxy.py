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
from jarvis_kernel.llm_proxy import MAX_OUT_TOKENS, PER_MINUTE, make_handler as _make_handler
from jarvis_kernel.store import Kernel


def make_handler(*args, **kwargs):
    """The hold-and-retry on a short 429 rest sleeps up to 70 s three times; these tests have no use for the wait (the fake clock does not move either)."""
    kwargs.setdefault("sleep", lambda seconds: None)
    return _make_handler(*args, **kwargs)

TOOL_REPLY = {"id": "x", "object": "chat.completion", "choices": [{"index": 0, "finish_reason": "tool_calls", "message": {
    "role": "assistant", "content": None, "tool_calls": [{"id": "call_1", "type": "function", "function": {"name": "search", "arguments": "{\"q\": \"weather\"}"}}]}}]}
SSE = [b'data: {"choices":[{"delta":{"content":"Hel"}}]}\n\n', b'data: {"choices":[{"delta":{"content":"lo"}}]}\n\n', b"data: [DONE]\n\n"]


def upstream(name, seen, status=200, fail_first=0, err_body=b""):
    fails = [fail_first]
    class U(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.0"
        def log_message(self, *a): pass
        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            seen.append((name, body, self.headers.get("Authorization")))
            if fails[0] > 0:
                fails[0] -= 1
                self.send_response(500); self.send_header("Content-Length", "0"); self.end_headers(); return
            if status != 200:
                self.send_response(status); self.send_header("Content-Length", str(len(err_body))); self.end_headers(); self.wfile.write(err_body); return
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

    def test_only_standard_chat_fields_reach_the_provider_and_tool_text_is_scrubbed(self):
        # audit SEC-D6 (2026-10-18): `payload = dict(body, model=model)` forwarded models/plugins/n/provider unchanged, and a
        # key in a tool description reached the upstream unscrubbed. Synthetic keys, written in pieces.
        self.proxy([self.good()])
        aws, gh = "AKIA" + "QWERTYUIOPASDFGH", "ghp_" + "abcdefghijklmnopqrstuvwxyz0123456789"
        tools = [{"type": "function", "function": {"name": "lookup", "description": f"signs requests with {aws}",
                  "parameters": {"type": "object", "properties": {"q": {"type": "string", "description": f"use token {gh}",
                                                                        "enum": ["a", f"x {aws}"]}}}}}]
        self.call({"model": "x", "messages": [{"role": "user", "content": "hi"}], "tools": tools, "temperature": 0.2, "stop": ["\n\n"],
                   "models": ["paid-vendor/expensive-model"], "plugins": [{"id": "web"}], "n": 64, "provider": {"order": ["paid"]},
                   "user": "owner-synth@example.com", "n_predict": 99999, "response_format": {"type": "json_schema", "json_schema": {
                       "name": "r", "schema": {"type": "object", "description": f"key {aws}"}}}})
        body = self.seen[-1][1]
        for k in ("models", "plugins", "n", "provider", "user", "n_predict"):
            self.assertNotIn(k, body)
        self.assertEqual((body["temperature"], body["stop"], body["tools"][0]["function"]["name"]), (0.2, ["\n\n"], "lookup"))
        self.assertEqual(body["tools"][0]["function"]["parameters"]["properties"]["q"]["type"], "string")   # the schema still works
        sent = json.dumps(body)
        self.assertNotIn(aws, sent); self.assertNotIn(gh, sent)
        rows = " ".join(r[0] for r in self.k.db.execute("SELECT payload FROM audit"))
        self.assertIn('"dropped_fields": ["models", "n", "n_predict", "plugins", "provider", "user"]', rows)   # names only
        self.assertNotIn("owner-synth@example.com", rows); self.assertNotIn("paid-vendor", rows)

    def test_every_text_field_is_scrubbed_not_only_a_known_list(self):
        # audit M2 (2026-10-18): message `name`, legacy `function_call`, `reasoning_content`, `refusal`, tool names, extra keys on
        # content parts, `tool_choice` and `stop` reached the provider unscrubbed. A recorder at the provider's HTTP edge.
        self.proxy([self.good()])
        keys = {n: "AKIA" + n.upper().ljust(16, "Z")[:16] for n in
                ("name", "fcname", "fcargs", "reasoning", "refusal", "extra", "tcname", "tcid", "toolout", "toolname", "choice", "stop", "rfname")}
        self.call({"model": "x", "tool_choice": {"type": "function", "function": {"name": f"pick-{keys['choice']}"}}, "stop": [f"end {keys['stop']}"],
                   "messages": [
                       {"role": "user", "name": f"n-{keys['name']}", "content": [{"type": "text", "text": "hi", "note": f"extra {keys['extra']}"}]},
                       {"role": "assistant", "content": None, "reasoning_content": f"r {keys['reasoning']}", "refusal": f"no {keys['refusal']}",
                        "function_call": {"name": f"f-{keys['fcname']}", "arguments": json.dumps({"a": keys["fcargs"]})},
                        "tool_calls": [{"id": f"id-{keys['tcid']}", "type": "function", "function": {"name": f"t-{keys['tcname']}", "arguments": "{}"}}]},
                       {"role": "tool", "tool_call_id": "1", "content": f"out {keys['toolout']}"}],
                   "tools": [{"type": "function", "function": {"name": f"tool-{keys['toolname']}", "parameters": {"type": "object"}}}],
                   "response_format": {"type": "json_schema", "json_schema": {"name": f"rf-{keys['rfname']}", "schema": {"type": "object"}}}})
        sent = json.dumps(self.seen[-1][1])
        for tag, key in keys.items():
            self.assertNotIn(key, sent, tag)

    def test_media_reaches_a_local_model_only(self):
        """images/audio/files are not scrubbable text: a cloud provider of the private lane gets a note, a local model the part."""
        img = {"type": "image_url", "image_url": {"url": "data:image/png;base64,QUJDREVGRw=="}}
        body = {"model": "x", "messages": [{"role": "user", "content": [{"type": "text", "text": "what is this"}, img,
                                                                         {"type": "input_audio", "input_audio": {"data": "UklGRg", "format": "wav"}}]},
                                           {"role": "assistant", "content": "ok", "audio": {"id": "a1"}}]}
        self.proxy([self.good()])                                  # a private cloud provider
        self.call(body)
        cloud = json.dumps(self.seen[-1][1])
        for leak in ("QUJDREVGRw", "UklGRg", "image_url", "input_audio", '"audio"'):
            self.assertNotIn(leak, cloud, leak)
        self.assertIn("an image was withheld for privacy", cloud)
        self.assertEqual(self.seen[-1][1]["messages"][0]["content"][0]["text"], "what is this")   # the text still goes
        rows = " ".join(r[0] for r in self.k.db.execute("SELECT payload FROM audit"))
        self.assertIn('"media_withheld": 3', rows)
        self.servers[-1].shutdown()
        local = Provider("llama", "local", self.up_url, None, ["any"], default_model="local-vlm", key_optional=True)
        self.proxy([local])                                        # a local model may see them
        self.call(body)
        got = self.seen[-1][1]
        self.assertEqual(got["messages"][0]["content"][1], img)
        self.assertIn("QUJDREVGRw", json.dumps(got))

    def test_malformed_bodies_get_a_400_not_a_dropped_connection(self):
        self.proxy([self.good()])
        for body in ([{"role": "user", "content": "hi"}], {"messages": "hi"}, {"messages": ["a string, not a message"]},
                     {"messages": [], "max_tokens": "abc"}):
            with self.assertRaises(urllib.error.HTTPError) as cm:
                self.call(body)
            self.assertEqual(cm.exception.code, 400, body)
        self.assertEqual(self.seen, [])

    def test_deep_scan_runs_once_per_request_not_once_per_message(self):
        # gitleaks is a subprocess (~0.5 s each): per message, a 40-message agent call took 21 s before the provider was asked
        from jarvis_kernel import scrub as scrub_mod
        runs = []
        real = scrub_mod._gitleaks_secrets
        scrub_mod._gitleaks_secrets = lambda text: runs.append(text) or [("generic-api-key", "Zq9vLk2mPx7")]
        try:
            self.proxy([self.good()])
            msgs = [{"role": "user" if i % 2 == 0 else "assistant", "content": f"turn {i}"} for i in range(20)]
            msgs[3]["content"] = "the service secret is Zq9vLk2mPx7"
            msgs.append({"role": "assistant", "content": None, "tool_calls": [{"id": "c", "type": "function",
                         "function": {"name": "f", "arguments": "{\"k\": \"Zq9vLk2mPx7\"}"}}]})
            self.call({"messages": msgs})
        finally:
            scrub_mod._gitleaks_secrets = real
        self.assertEqual(len(runs), 1)
        sent = json.dumps(self.seen[-1][1])
        self.assertNotIn("Zq9vLk2mPx7", sent); self.assertEqual(sent.count("[REDACTED]"), 2)
        self.assertIn("turn 19", sent)

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
        # public never tried; paid model never sent; the failing provider gets one more try before the fallback
        self.assertEqual(used, [("bad", "default"), ("bad", "default"), ("good", "openai/gpt-oss-120b")])

    def test_bench_j_a_key_withdrawn_after_planning_falls_back_to_the_next_provider(self):
        class Withdrawn(Provider):
            def available(self):                    # it was there when the chain was planned; the file is gone now
                return True
        gone_up, gone_url = upstream("withdrawn", self.seen)
        self.servers.append(gone_up)
        self.proxy([Withdrawn("withdrawn", "private", gone_url, os.path.join(self.tmp.name, "gone.key"), ["any"], priority=1),
                    self.good(priority=2)])
        out, _ = self.call({"messages": [{"role": "user", "content": "hi"}]})
        self.assertEqual(json.loads(out), TOOL_REPLY)
        self.assertEqual([n for n, b, a in self.seen], ["good"])                 # the withdrawn provider was never asked
        rows = [json.loads(r[0]) for r in self.k.db.execute("SELECT payload FROM audit WHERE kind='agent_model_call_failed'")]
        self.assertTrue(any(r.get("provider") == "withdrawn" for r in rows))

    def test_bench_j_a_stream_that_stalls_mid_answer_ends_audited_without_a_fake_done(self):
        import time
        stalls = [1]
        class Stall(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.0"
            def log_message(self, *a): pass
            def do_POST(self):
                self.rfile.read(int(self.headers["Content-Length"]))
                if not stalls[0]:                   # the next request is answered normally
                    out = json.dumps(TOOL_REPLY).encode()
                    self.send_response(200); self.send_header("Content-Type", "application/json"); self.send_header("Content-Length", str(len(out)))
                    self.end_headers(); self.wfile.write(out); return
                stalls[0] = 0
                self.send_response(200); self.send_header("Content-Type", "text/event-stream"); self.end_headers()
                self.wfile.write(SSE[0]); self.wfile.flush()
                time.sleep(3)                       # longer than the provider's timeout_s
                try:
                    for c in SSE[1:]: self.wfile.write(c)
                except OSError:
                    pass
        st = ThreadingHTTPServer(("127.0.0.1", 0), Stall); threading.Thread(target=st.serve_forever, daemon=True).start()
        self.servers.append(st)
        self.proxy([Provider("stall", "private", f"http://127.0.0.1:{st.server_address[1]}/v1", None, ["any"], priority=1, timeout_s=1),
                    self.good(priority=2)])
        r = urllib.request.Request(self.url + "/v1/chat/completions", method="POST", headers={"Content-Type": "application/json",
                                   "Authorization": "Bearer " + self.tok},
                                   data=json.dumps({"messages": [{"role": "user", "content": "hi"}], "stream": True}).encode())
        got, t0 = b"", time.monotonic()
        try:
            with urllib.request.urlopen(r, timeout=10) as resp:
                while True:
                    c = resp.read1(65536)
                    if not c:
                        break
                    got += c
        except Exception:  # noqa: BLE001 - a cut connection is how a broken stream may look
            pass
        self.assertLess(time.monotonic() - t0, 2.9)
        self.assertEqual(got, SSE[0])                                               # no invented second token or [DONE]
        rows = [json.loads(r[0]) for r in self.k.db.execute("SELECT payload FROM audit WHERE kind='agent_model_call_failed'")]
        self.assertTrue(any(r.get("provider") == "stall" for r in rows), rows)
        self.assertEqual(json.loads(self.call({"messages": [{"role": "user", "content": "next"}]})[0]), TOOL_REPLY)   # still up

    def test_a_failed_sample_is_retried_once_on_the_same_provider_but_a_bad_request_is_not(self):
        flaky, flaky_url = upstream("flaky", self.seen, fail_first=1)
        refuses, refuses_url = upstream("refuses", self.seen, status=400)
        self.servers += [flaky, refuses]
        self.proxy([Provider("refuses", "private", refuses_url, None, ["any"], priority=1),
                    Provider("flaky", "private", flaky_url, None, ["any"], priority=2)])
        out, _ = self.call({"messages": [{"role": "user", "content": "hi"}]})
        self.assertEqual(json.loads(out), TOOL_REPLY)
        self.assertEqual([n for n, b, a in self.seen], ["refuses", "flaky", "flaky"])
        fails = [json.loads(r[0]) for r in self.k.db.execute("SELECT payload FROM audit WHERE kind='agent_model_call_failed'")]
        self.assertEqual([(f["provider"], f["error"], f["attempt"]) for f in fails], [("refuses", "HTTP 400", 1), ("flaky", "HTTP 500", 1)])

    def test_lane_wait_says_how_long_until_a_provider_of_the_lane_may_be_asked_again(self):
        limited, limited_url = upstream("limited", self.seen, status=429, err_body=b'{"error": {"message": "limit"}}')
        self.servers.append(limited)
        prov = Provider("groq", "private", limited_url, self.keyf, ["any"], default_model="openai/gpt-oss-120b")
        H, tokens = make_handler(self.k, [prov], clock=lambda: self.now[0])
        s = ThreadingHTTPServer(("127.0.0.1", 0), H); threading.Thread(target=s.serve_forever, daemon=True).start(); self.servers.append(s)
        self.url = f"http://127.0.0.1:{s.server_address[1]}"; self.tok = tokens.issue("hermes")
        wait = H.owner_plane.lane_wait
        self.assertEqual(wait("private"), 0)                                  # nothing has been refused yet
        with self.assertRaises(urllib.error.HTTPError):
            self.call({"model": "x", "messages": [{"role": "user", "content": "hi"}]})
        rest = wait("private")
        self.assertGreater(rest, 0)                                           # the only provider said 429: it rests
        self.assertEqual(wait("public"), 0)                                   # another lane is unaffected (no provider here, so nothing to wait for)
        self.now[0] += rest + 1
        self.assertEqual(wait("private"), 0)                                  # and it may be asked again after that

    def test_a_free_provider_too_small_for_the_agents_requests_does_not_count_as_available(self):
        limited, limited_url = upstream("limited", self.seen, status=429, err_body=b'{"error": {"message": "limit"}}')
        self.servers.append(limited)
        small = Provider("small", "private", self.up_url, self.keyf, ["any"], default_model="m", max_total_tokens=4000, priority=1)
        big = Provider("big", "private", limited_url, self.keyf, ["any"], default_model="m", priority=2)
        H, tokens = make_handler(self.k, [small, big], clock=lambda: self.now[0])
        s = ThreadingHTTPServer(("127.0.0.1", 0), H); threading.Thread(target=s.serve_forever, daemon=True).start(); self.servers.append(s)
        self.url = f"http://127.0.0.1:{s.server_address[1]}"; self.tok = tokens.issue("hermes")
        big_request = {"model": "x", "messages": [{"role": "user", "content": "word " * 9000}]}     # about 11k tokens: only `big` fits (`small` takes 4000 in all)
        with self.assertRaises(urllib.error.HTTPError):
            self.call(big_request)
        self.assertGreater(H.owner_plane.lane_wait("private"), 0)       # `small` is idle, but an owner command (about 6k tokens) cannot use it

    def test_a_groq_sized_provider_that_is_idle_means_an_owner_command_can_run(self):
        """2026-10-25: the gate assumed a 12k-token command, so the only provider it counted was OpenRouter's free model, which was always
        resting: every owner command was refused while Groq (8000 tokens in all) could take the ~5.7k-token request."""
        limited, limited_url = upstream("limited", self.seen, status=429, err_body=b'{"error": {"message": "limit"}}')
        self.servers.append(limited)
        groq = Provider("groq", "private", self.up_url, self.keyf, ["any"], default_model="m", max_total_tokens=8000, priority=1)
        big = Provider("big", "private", limited_url, self.keyf, ["any"], default_model="m", priority=2)
        H, tokens = make_handler(self.k, [groq, big], clock=lambda: self.now[0])
        s = ThreadingHTTPServer(("127.0.0.1", 0), H); threading.Thread(target=s.serve_forever, daemon=True).start(); self.servers.append(s)
        self.url = f"http://127.0.0.1:{s.server_address[1]}"; self.tok = tokens.issue("hermes")
        with self.assertRaises(urllib.error.HTTPError):
            self.call({"model": "x", "messages": [{"role": "user", "content": "word " * 9000}]})      # only `big` fits this one: it is now resting
        self.assertEqual(H.owner_plane.lane_wait("private"), 0)         # a normal owner command goes to `groq`, which is idle

    def test_kill_switch_and_rate_limit(self):
        self.proxy([self.good()])
        for i in range(PER_MINUTE): self.call({"messages": [{"role": "user", "content": str(i)}]})
        with self.assertRaises(urllib.error.HTTPError) as cm: self.call({"messages": [{"role": "user", "content": "one more"}]})
        self.assertEqual(cm.exception.code, 429)
        self.now[0] += 61; self.call({"messages": [{"role": "user", "content": "later"}]})
        self.k.kill("t")
        with self.assertRaises(urllib.error.HTTPError) as cm: self.call({"messages": [{"role": "user", "content": "x"}]})
        self.assertEqual(cm.exception.code, 503)

    def test_token_lane_decides_the_providers(self):
        priv = Provider("priv", "private", self.up_url, None, ["any"], priority=1)
        pub = Provider("pub", "public", self.bad_url, None, ["any"], priority=2)   # fails: proves no fallback into private
        H, tokens = make_handler(self.k, [priv, pub], clock=lambda: self.now[0])
        s = ThreadingHTTPServer(("127.0.0.1", 0), H); threading.Thread(target=s.serve_forever, daemon=True).start(); self.servers.append(s)
        self.url = f"http://127.0.0.1:{s.server_address[1]}"
        lab = tokens.issue("lab", "public"); owner = tokens.issue("hermes")
        with self.assertRaises(urllib.error.HTTPError) as cm: self.call({"messages": [{"role": "user", "content": "x"}]}, tok=lab)
        self.assertEqual(cm.exception.code, 502)
        self.assertEqual([n for n, b, a in self.seen], ["bad", "bad"])             # retried once, never reached the private provider
        self.seen.clear(); self.call({"messages": [{"role": "user", "content": "x"}]}, tok=owner)
        self.assertEqual([n for n, b, a in self.seen], ["good"])                   # the private token never reached the public one
        rows = [r[0] for r in self.k.db.execute("SELECT payload FROM audit WHERE kind='agent_model_call'")]
        self.assertIn('"lane": "private"', rows[-1])
        with self.assertRaises(ValueError): tokens.issue("x", "anything")

    def test_old_token_table_keeps_tokens_private(self):
        self.k.db.execute("CREATE TABLE agent_tokens(name TEXT PRIMARY KEY, token_h TEXT, created REAL)")
        self.k.db.execute("INSERT INTO agent_tokens VALUES ('old', 'h', 0)")
        from jarvis_kernel.llm_proxy import AgentTokens
        AgentTokens(self.k)
        self.assertEqual(self.k.db.execute("SELECT lane FROM agent_tokens WHERE name='old'").fetchone()[0], "private")

    def test_no_provider_available(self):
        self.proxy([Provider("pub", "public", self.up_url, None, ["any"])])
        with self.assertRaises(urllib.error.HTTPError) as cm: self.call({"messages": [{"role": "user", "content": "x"}]})
        self.assertEqual(cm.exception.code, 503)


if __name__ == "__main__":
    unittest.main()


def tripping_upstream(name, seen, trip, status=500, stream_after=None):
    """A provider that switches the kill switch on (`trip()`) while it handles its request, then answers with `status`, or, when
    `stream_after` is set, streams one event, trips, and streams the rest."""
    class U(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.0"
        def log_message(self, *a): pass
        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            seen.append((name, body, self.headers.get("Authorization")))
            if stream_after:
                self.send_response(200); self.send_header("Content-Type", "text/event-stream"); self.end_headers()
                self.wfile.write(SSE[0]); self.wfile.flush()
                trip()
                try:
                    for c in SSE[1:]: self.wfile.write(c); self.wfile.flush()
                except OSError:
                    pass
                return
            trip()
            self.send_response(status); self.send_header("Content-Length", "0"); self.end_headers()
    s = ThreadingHTTPServer(("127.0.0.1", 0), U); threading.Thread(target=s.serve_forever, daemon=True).start()
    return s, f"http://127.0.0.1:{s.server_address[1]}/v1"


class KillSwitchBetweenSteps(unittest.TestCase):
    """Audit M9: the kill switch is checked again between provider fallbacks, before a retry and between stream chunks (the real
    handler, real upstream servers; the switch is flipped from inside the first provider's request)."""
    setUp, tearDown, proxy, good, call = P.setUp, P.tearDown, P.proxy, P.good, P.call       # P's fixtures, without P's tests

    def failing_then_good(self, status, **kw):
        flaky, flaky_url = tripping_upstream("flaky", self.seen, self.k.kill, status=status, **kw)
        self.servers.append(flaky)
        self.proxy([Provider("flaky", "private", flaky_url, None, ["any"], priority=1), self.good(priority=2)])

    def post(self, body):
        with self.assertRaises(urllib.error.HTTPError) as cm:
            self.call(body)
        return cm.exception.code, cm.exception.read()

    def test_a_kill_between_the_first_provider_failing_and_the_fallback_calls_no_fallback(self):
        self.failing_then_good(422)                                      # not retried: the fallback would be next
        code, body = self.post({"messages": [{"role": "user", "content": "hi"}]})
        self.assertEqual(code, 503); self.assertIn(b"kill switch is on", body)
        self.assertEqual([n for n, b, a in self.seen], ["flaky"])         # the good provider was never asked
        rows = [r[0] for r in self.k.db.execute("SELECT kind FROM audit WHERE kind='agent_model_stopped_by_kill'")]
        self.assertEqual(rows, ["agent_model_stopped_by_kill"])

    def test_a_kill_after_a_5xx_does_not_retry_the_same_provider_either(self):
        self.failing_then_good(500)                                      # a 500 would be retried once, then fall back
        self.assertEqual(self.post({"messages": [{"role": "user", "content": "hi"}]})[0], 503)
        self.assertEqual([n for n, b, a in self.seen], ["flaky"])

    def test_without_the_kill_switch_the_same_chain_still_falls_back(self):
        flaky, flaky_url = tripping_upstream("flaky", self.seen, lambda: None, status=422)
        self.servers.append(flaky)
        self.proxy([Provider("flaky", "private", flaky_url, None, ["any"], priority=1), self.good(priority=2)])
        out, _ = self.call({"messages": [{"role": "user", "content": "hi"}]})
        self.assertEqual(json.loads(out), TOOL_REPLY)
        self.assertEqual([n for n, b, a in self.seen], ["flaky", "good"])

    def test_a_kill_during_a_stream_stops_the_relay_between_chunks(self):
        flaky, flaky_url = tripping_upstream("streamer", self.seen, self.k.kill, stream_after=True)
        self.servers.append(flaky)
        self.proxy([Provider("streamer", "private", flaky_url, None, ["any"], priority=1)])
        try:
            out, _ = self.call({"messages": [{"role": "user", "content": "hi"}], "stream": True})
        except (urllib.error.URLError, OSError, ConnectionError) as e:   # a cut-off stream may also surface as a read error
            out = b""
        self.assertNotIn(b"[DONE]", out)                                 # never a complete-looking answer
        self.assertNotIn(b'"lo"', out)                                   # nothing after the switch went on reached the agent
        import time as _t
        # Two correct stops, depending on how fast the proxy reads: the check between chunks (agent_model_call_failed, class
        # killed) or, when the proxy only looks after the whole answer was already sent, the check right after the provider
        # answered (agent_model_stopped_by_kill). Either way the stream is cut and nothing is passed on; a completed call is not.
        for _ in range(50):
            stopped = [json.loads(r[0]) for r in self.k.db.execute("SELECT payload FROM audit WHERE kind='agent_model_stopped_by_kill'")]
            failed = [json.loads(r[0]) for r in self.k.db.execute("SELECT payload FROM audit WHERE kind='agent_model_call_failed'")]
            if stopped or failed:
                break
            _t.sleep(0.1)
        self.assertEqual(len(stopped) + len(failed), 1)
        self.assertEqual([r.get("class") for r in failed], ["killed"] * len(failed))
        self.assertEqual(self.k.db.execute("SELECT count(*) FROM audit WHERE kind='agent_model_call'").fetchone()[0], 0)

    def test_the_request_start_check_still_refuses(self):
        self.proxy([self.good()])
        self.k.kill()
        self.assertEqual(self.post({"messages": [{"role": "user", "content": "hi"}]})[0], 503)
        self.assertEqual(self.seen, [])


class DeepScanCache(unittest.TestCase):
    """A conversation re-sent on every call is deep-scanned once per new text, and the cache never weakens redaction."""
    def setUp(self):
        from jarvis_kernel import llm_proxy, scrub as scrub_mod
        self.lp, self.sm = llm_proxy, scrub_mod
        self.real = scrub_mod._gitleaks_secrets
        llm_proxy._DEEP_CACHE.clear()
        self.runs = []
        def fake(text):
            self.runs.append(text)
            return [("generic-api-key", "Qx7FakeSecretValue9")] if "Qx7FakeSecretValue9" in text else []
        scrub_mod._gitleaks_secrets = fake

    def tearDown(self):
        self.sm._gitleaks_secrets = self.real
        self.lp._DEEP_CACHE.clear()

    def test_old_messages_are_not_rescanned_and_stay_redacted(self):
        hist = [{"role": "user", "content": "my token is Qx7FakeSecretValue9 please keep it"}]
        b1, f1, _ = self.lp._scrub_messages({"messages": [dict(m) for m in hist]})
        self.assertNotIn("Qx7FakeSecretValue9", json.dumps(b1)); self.assertIn("gitleaks:generic-api-key", f1)
        hist.append({"role": "assistant", "content": "ok"}); hist.append({"role": "user", "content": "next question about my token"})   # a text the gitleaks gate scans
        b2, f2, _ = self.lp._scrub_messages({"messages": [dict(m) for m in hist]})
        self.assertNotIn("Qx7FakeSecretValue9", json.dumps(b2)); self.assertIn("gitleaks:generic-api-key", f2)
        self.assertEqual(len(self.runs), 2)
        self.assertNotIn("Qx7FakeSecretValue9", self.runs[1])         # the second scan read only the new texts
        self.assertNotIn("Qx7FakeSecretValue9", json.dumps(list(self.lp._DEEP_CACHE.values())))

    def test_a_failed_scan_is_not_cached(self):
        self.sm._gitleaks_secrets = lambda text: self.runs.append(text) or None
        for _ in range(2):
            self.lp._scrub_messages({"messages": [{"role": "user", "content": "same token text"}]})
        self.assertEqual(len(self.runs), 2)


class JsonKeysAreScrubbed(unittest.TestCase):
    """A JSON key is text a provider reads: a token used as a property name was forwarded as it was (inventory pass F, 2026-10-19)."""
    def test_secret_shaped_keys_in_tools_and_schemas_are_redacted_and_values_keep_their_place(self):
        from jarvis_kernel import llm_proxy
        tok = "ghp_" + "A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8"
        body = {"messages": [{"role": "user", "content": "hi"}],
                "tools": [{"type": "function", "function": {"name": "t", "parameters": {"type": "object", "properties": {
                    tok: {"type": "string", "description": "plain"}, "ok_name": {"type": "string"}}}}}],
                "response_format": {"type": "json_schema", "json_schema": {"schema": {"properties": {f"key {tok}": {"type": "string"}}}}}}
        out, found, blocked = llm_proxy._scrub_messages(body)
        sent = json.dumps(out)
        self.assertNotIn(tok, sent)
        self.assertFalse(blocked)
        self.assertIn("github_token", found)
        self.assertIn("ok_name", sent)
        self.assertEqual(out["tools"][0]["function"]["parameters"]["properties"]["[REDACTED]"]["description"], "plain")

    def test_a_private_key_used_as_a_key_blocks_the_request(self):
        from jarvis_kernel import llm_proxy
        _, found, blocked = llm_proxy._scrub_messages({"messages": [], "tools": [{"x": {"-----BEGIN RSA PRIVATE KEY-----": 1}}]})
        self.assertTrue(blocked)
        self.assertIn("private_key", found)


class DeepScanBatch(unittest.TestCase):
    """Concurrent fresh requests share one gitleaks run, and each still loses only its own secret."""
    def test_concurrent_requests_share_a_scan_and_stay_redacted(self):
        from jarvis_kernel import llm_proxy, scrub as scrub_mod
        real, runs, lock = scrub_mod._gitleaks_secrets, [], threading.Lock()
        def fake(text):
            import time as _t
            _t.sleep(0.3)                                   # gitleaks' start-up cost
            with lock:
                runs.append(text)
            return [("generic-api-key", f"Zq{i:02d}FakeSecretValue") for i in range(12) if f"Zq{i:02d}FakeSecretValue" in text]
        scrub_mod._gitleaks_secrets = fake
        llm_proxy._DEEP_CACHE.clear()
        out = {}
        def one(i):
            b, f, _ = llm_proxy._scrub_messages({"messages": [{"role": "user", "content": f"request {i} key Zq{i:02d}FakeSecretValue"}]})
            out[i] = (json.dumps(b), f)
        try:
            th = [threading.Thread(target=one, args=(i,)) for i in range(12)]
            [t.start() for t in th]; [t.join(10) for t in th]
        finally:
            scrub_mod._gitleaks_secrets = real
            llm_proxy._DEEP_CACHE.clear()
        self.assertEqual(len(out), 12)
        self.assertLess(len(runs), 12)                      # shared, not one start-up each
        for i, (body, f) in out.items():
            self.assertNotIn(f"Zq{i:02d}FakeSecretValue", body)
            self.assertIn(f"request {i} key [REDACTED]", body)
            self.assertIn("gitleaks:generic-api-key", f)
            for j in range(12):                             # another request's secret never enters this body
                self.assertNotIn(f"Zq{j:02d}FakeSecretValue", body)

    def test_a_failed_shared_scan_fails_for_everyone_in_it(self):
        from jarvis_kernel import llm_proxy, scrub as scrub_mod
        real = scrub_mod._gitleaks_secrets
        scrub_mod._gitleaks_secrets = lambda text: (_ for _ in ()).throw(RuntimeError("boom"))
        llm_proxy._DEEP_CACHE.clear()
        try:
            b, f, blocked = llm_proxy._scrub_messages({"messages": [{"role": "user", "content": "plain token text"}]})
        finally:
            scrub_mod._gitleaks_secrets = real
        self.assertEqual((f, blocked), ([], False))
        self.assertEqual(llm_proxy._DEEP_CACHE, {})        # nothing cached after a failed scan


class ServerQueue(unittest.TestCase):
    """A burst of simultaneous connections is accepted at once, not refused and retried by TCP a second later."""
    def test_thirty_simultaneous_connections_are_served_without_tcp_retries(self):
        import socket
        import time as _t
        from jarvis_kernel import llm_proxy, nodes
        self.assertGreaterEqual(llm_proxy.ProxyServer.request_queue_size, 64)
        import inspect
        self.assertIn("ProxyServer(", inspect.getsource(llm_proxy.serve))
        self.assertIn("ProxyServer(", inspect.getsource(nodes))

        class Slow(BaseHTTPRequestHandler):
            def log_message(self, *a): pass
            def do_GET(self):
                _t.sleep(0.3)
                self.send_response(200); self.send_header("Content-Length", "2"); self.end_headers(); self.wfile.write(b"ok")
        s = llm_proxy.ProxyServer(("127.0.0.1", 0), Slow)
        # serve_forever accepts one connection per loop turn; let the backlog fill before it starts accepting
        socks = []
        for _ in range(30):
            c = socket.create_connection(("127.0.0.1", s.server_address[1]), timeout=5)
            c.sendall(b"GET / HTTP/1.0\r\n\r\n")
            socks.append(c)
        threading.Thread(target=s.serve_forever, daemon=True).start()
        t0 = _t.perf_counter()
        try:
            for c in socks:
                self.assertTrue(c.recv(64).startswith(b"HTTP/1.0 200"))
                c.close()
        finally:
            s.shutdown()
        self.assertLess(_t.perf_counter() - t0, 3.0)
