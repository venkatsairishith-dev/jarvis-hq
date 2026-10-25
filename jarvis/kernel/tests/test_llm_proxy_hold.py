"""A provider's short rest is waited out inside the request (2026-10-25): Groq's free 8000 tokens a minute allow the owner's agent about two calls a minute; a 429 at the
third sent the agent into its own back-off of minutes and the owner's command failed at once. The sleep is injected; nothing waits for real."""
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
from jarvis_kernel.llm_proxy import HOLD_MAX_S, make_handler
from jarvis_kernel.store import Kernel

REPLY = {"id": "x", "object": "chat.completion", "model": "m", "choices": [{"index": 0, "message": {"role": "assistant", "content": "ok"}, "finish_reason": "stop"}],
         "usage": {"prompt_tokens": 5, "completion_tokens": 1, "total_tokens": 6}}


def limited_once(retry_after):
    """An upstream that answers 429 (Retry-After) to its first request and 200 to every later one."""
    state = {"calls": 0}

    class U(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.0"

        def log_message(self, *a):
            pass

        def do_POST(self):
            self.rfile.read(int(self.headers["Content-Length"]))
            state["calls"] += 1
            if state["calls"] == 1:
                body = b'{"error": {"message": "Rate limit reached"}}'
                self.send_response(429)
                self.send_header("Retry-After", str(retry_after))
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            out = json.dumps(REPLY).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(out)))
            self.end_headers()
            self.wfile.write(out)
    srv = ThreadingHTTPServer(("127.0.0.1", 0), U)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, state


class Hold(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.k = Kernel(os.path.join(self.tmp.name, "d"))
        self.keyf = os.path.join(self.tmp.name, "groq.key")
        open(self.keyf, "w").write("made-up-test-key")
        self.now, self.slept, self.servers = [1000.0], [], []

    def tearDown(self):
        for s in self.servers:
            s.shutdown()
        self.k.db.close()
        self.tmp.cleanup()

    def setup_proxy(self, retry_after):
        up, state = limited_once(retry_after)
        self.servers.append(up)
        prov = Provider("groq", "private", f"http://127.0.0.1:{up.server_address[1]}/v1", self.keyf, ["any"], default_model="m", max_total_tokens=8000)

        self.on_sleep = lambda: None

        def fake_sleep(s):
            self.slept.append(s)
            self.now[0] += s
            self.on_sleep()
        H, tokens = make_handler(self.k, [prov], clock=lambda: self.now[0], sleep=fake_sleep)
        srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        self.servers.append(srv)
        self.url, self.tok = f"http://127.0.0.1:{srv.server_address[1]}", tokens.issue("hermes")
        return state

    def call(self):
        r = urllib.request.Request(self.url + "/v1/chat/completions", method="POST", data=json.dumps({"model": "x", "messages": [{"role": "user", "content": "hi"}]}).encode(),
                                   headers={"Content-Type": "application/json", "Authorization": "Bearer " + self.tok})
        try:
            with urllib.request.urlopen(r, timeout=10) as resp:
                return resp.status
        except urllib.error.HTTPError as e:
            return e.code

    def test_a_short_rest_is_waited_out_and_the_call_goes_through(self):
        state = self.setup_proxy(retry_after=10)
        self.assertEqual(self.call(), 200)                      # the provider says 429 for 10 s: the proxy waits them out and asks again, the agent sees only the answer
        self.assertEqual(len(self.slept), 1)
        self.assertTrue(0 < self.slept[0] <= HOLD_MAX_S, self.slept)
        self.assertEqual(state["calls"], 2)
        held = [json.loads(r[0]) for r in self.k.db.execute("SELECT payload FROM audit WHERE kind='agent_model_held'")]
        self.assertEqual([h["providers"] for h in held], [["groq"]])

    def test_a_provider_that_is_already_resting_for_a_few_seconds_is_waited_for_too(self):
        state = self.setup_proxy(retry_after=300)               # first: a long rest, answered at once (below); then a short one is put in place by hand
        self.assertEqual(self.call(), 429)
        self.now[0] += 290                                      # ten seconds of the rest are left
        self.assertEqual(self.call(), 200)
        self.assertEqual(len(self.slept), 1)
        self.assertLessEqual(self.slept[0], 10 + 1)
        self.assertEqual(state["calls"], 2)

    def test_a_long_rest_is_still_answered_at_once(self):
        state = self.setup_proxy(retry_after=300)
        self.assertEqual(self.call(), 429)
        self.assertEqual(self.call(), 429)                      # resting for minutes: not waited out here
        self.assertEqual(self.slept, [])
        self.assertEqual(state["calls"], 1)

    def test_the_kill_switch_ends_a_wait(self):
        state = self.setup_proxy(retry_after=10)
        self.on_sleep = lambda: self.k.kill("test")             # the owner pulls the kill switch while the proxy is waiting
        self.assertEqual(self.call(), 503)
        self.assertEqual(state["calls"], 1)                     # the provider was not asked again


if __name__ == "__main__":
    unittest.main()
