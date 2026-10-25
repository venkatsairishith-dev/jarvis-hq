"""A provider that answers HTTP 404 (its model id was removed) rests for a moment instead of being asked again at once (found on the real laptop 2026-10-25:
openrouter-private's free model no longer existed, so 46 calls failed and an owner command waited five minutes on retries)."""
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
from jarvis_kernel.llm_proxy import OUTAGE_REST_S, make_handler
from jarvis_kernel.store import Kernel


class Gone(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.0"
    calls = 0

    def log_message(self, *a):
        pass

    def do_POST(self):
        self.rfile.read(int(self.headers["Content-Length"]))
        Gone.calls += 1
        out = b'{"error":"no such model"}'
        self.send_response(404)
        self.send_header("Content-Length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)


class RestAfter404(unittest.TestCase):
    def test_a_404_provider_is_not_asked_again_until_its_rest_is_over(self):
        Gone.calls = 0
        up = ThreadingHTTPServer(("127.0.0.1", 0), Gone)
        threading.Thread(target=up.serve_forever, daemon=True).start()
        tmp = tempfile.TemporaryDirectory()
        k = Kernel(os.path.join(tmp.name, "d"))
        now = [1_000_000.0]
        provs = [Provider("only", "public", f"http://127.0.0.1:{up.server_address[1]}/v1", None, ["any"], priority=1, model_pattern=".")]
        H, tokens = make_handler(k, provs, clock=lambda: now[0])
        srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        tok = tokens.issue("lab", "public")

        def chat():
            r = urllib.request.Request(f"http://127.0.0.1:{srv.server_address[1]}/v1/chat/completions", method="POST",
                                       data=json.dumps({"model": "any", "messages": [{"role": "user", "content": "hi"}]}).encode(),
                                       headers={"Authorization": "Bearer " + tok, "Content-Type": "application/json"})
            try:
                with urllib.request.urlopen(r, timeout=10) as resp:
                    return resp.status
            except urllib.error.HTTPError as e:
                return e.code
        try:
            first = chat()
            self.assertGreaterEqual(first, 500)
            self.assertEqual(Gone.calls, 1, "a 404 is not retried on the same provider")
            second = chat()
            self.assertEqual(second, 503, "resting: a plain 'unavailable' at once")
            self.assertEqual(Gone.calls, 1, "the resting provider was not asked again")
            now[0] += OUTAGE_REST_S + 1
            chat()
            self.assertEqual(Gone.calls, 2, "after the rest it is asked again")
        finally:
            srv.shutdown()
            up.shutdown()
            k.db.close()
            tmp.cleanup()


if __name__ == "__main__":
    unittest.main()


class AgentTimeout(unittest.TestCase):
    def test_the_owners_agent_call_times_out_as_TimedOut_and_the_console_waits_at_most_five_minutes(self):
        from jarvis_kernel import llm_proxy
        from jarvis_kernel.telegram import TimedOut, hermes_agent
        self.assertLessEqual(llm_proxy.CONSOLE_AGENT_TIMEOUT_S, 300)

        class Slow(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_POST(self):
                self.rfile.read(int(self.headers["Content-Length"]))
                import time
                time.sleep(3)
        srv = ThreadingHTTPServer(("127.0.0.1", 0), Slow)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        tmp = tempfile.TemporaryDirectory()
        keyf = os.path.join(tmp.name, "k.txt")
        open(keyf, "w").write("not-a-real-key")
        try:
            agent = hermes_agent(keyf, f"http://127.0.0.1:{srv.server_address[1]}", session="t", timeout=1, state_path=os.path.join(tmp.name, "s.txt"))
            with self.assertRaises(TimedOut) as cm:
                agent("hello")
            self.assertEqual(type(cm.exception).__name__, "TimedOut")
        finally:
            srv.shutdown()
            tmp.cleanup()
