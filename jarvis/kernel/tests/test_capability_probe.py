import json
import os
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from jarvis_kernel import capability_probe as cp
from jarvis_kernel import model_select as ms
from jarvis_kernel.gateway import Provider, RouteError

GOOD = {"model": "fake-tools-1", "choices": [{"message": {"tool_calls": [
    {"function": {"name": "get_weather", "arguments": json.dumps({"city": "Paris"})}}]}}]}
PLAIN = {"model": "fake-plain-1", "choices": [{"message": {"content": "It is sunny."}}]}


def server(reply, seen):
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a): pass
        def do_POST(self):
            seen.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
            out = json.dumps(reply).encode()
            self.send_response(200); self.send_header("Content-Length", str(len(out))); self.end_headers(); self.wfile.write(out)
    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"http://127.0.0.1:{srv.server_address[1]}/v1"


def prov(url, **kw):
    kw.setdefault("lane", "local")
    return Provider(name="loc", base_url=url, key_file=None, models=["any"], default_model="m1", **kw)


class TestProbe(unittest.TestCase):
    def test_check_reply_is_strict(self):
        self.assertTrue(cp.check_reply(GOOD))
        self.assertFalse(cp.check_reply(PLAIN))
        bad = json.loads(json.dumps(GOOD)); bad["choices"][0]["message"]["tool_calls"][0]["function"]["arguments"] = "{not json"
        self.assertFalse(cp.check_reply(bad))
        wrong = json.loads(json.dumps(GOOD)); wrong["choices"][0]["message"]["tool_calls"][0]["function"]["name"] = "rm_rf"
        self.assertFalse(cp.check_reply(wrong))
        self.assertFalse(cp.check_reply({}))

    def test_measures_pass_and_fail_and_sends_no_user_data(self):
        seen = []
        srv, url = server(GOOD, seen)
        try:
            r = cp.run(prov(url), runs=2)["tools"]
        finally:
            srv.shutdown()
        self.assertEqual((r["ok"], r["passed"], r["served_model"]), (True, 2, "fake-tools-1"))
        self.assertEqual(seen[0]["messages"][0]["content"], cp.PROMPT)
        self.assertEqual(seen[0]["tools"][0]["function"]["name"], "get_weather")
        srv, url = server(PLAIN, [])
        try:
            self.assertFalse(cp.run(prov(url), runs=2)["tools"]["ok"])
        finally:
            srv.shutdown()

    def test_model_alias_is_sent_and_errors_are_named_without_server_text(self):
        seen = []
        srv, url = server(GOOD, seen)
        try:
            r = cp.run(prov(url), runs=1, model="decider")["tools"]
        finally:
            srv.shutdown()
        self.assertEqual((seen[0]["model"], r["model"], r["error_kinds"]), ("decider", "decider", []))

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a): pass
            def do_POST(self):
                self.rfile.read(int(self.headers["Content-Length"]))
                self.send_response(404); self.end_headers(); self.wfile.write(b"model 'any' not found")
        bad = ThreadingHTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=bad.serve_forever, daemon=True).start()
        try:
            r = cp.run(prov(f"http://127.0.0.1:{bad.server_address[1]}/v1"), runs=2)["tools"]
        finally:
            bad.shutdown()
        self.assertEqual((r["ok"], r["errors"], r["error_kinds"]), (False, 2, ["HTTP 404"]))
        self.assertNotIn("not found", json.dumps(r))

    def test_server_down_is_a_failed_probe_not_a_crash(self):
        r = cp.run(prov("http://127.0.0.1:9/v1"), runs=1, timeout=2)["tools"]
        self.assertEqual((r["ok"], r["errors"]), (False, 1))

    def test_cloud_paid_and_remote_providers_are_refused_before_any_request(self):
        for p in (prov("https://api.groq.com/openai/v1", lane="private"), prov("http://10.0.0.5:8080/v1"),
                  prov("http://127.0.0.1:1/v1", paid=True), prov(None)):
            with self.assertRaises(RouteError):
                cp.run(p)
        self.assertTrue(cp.is_loopback("http://localhost:8080/v1")); self.assertFalse(cp.is_loopback("http://example.com/v1"))

    def test_save_is_atomic_and_selector_uses_the_measurement(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "m.json")
            cp.save("loc", {"tools": {"ok": True, "model": "m1"}}, path)
            cp.save("other", {"tools": {"ok": False, "model": "m1"}}, path)
            self.assertFalse(os.path.exists(path + ".part"))
            probes = ms.load_probes(path)
            self.assertEqual(sorted(probes), ["loc", "other"])
            req = ms.requirements("t", lane="private", tools=True)
            good = ms.capability_codes(None, req, False, probes["loc"], "m1")
            self.assertEqual(good, ([], ["NOT_IN_CATALOG", "TOOLS_MEASURED"]))
            bad = ms.capability_codes({"tool_call": True}, req, False, probes["other"], "m1")   # measured "no" beats declared "yes"
            self.assertIn("TOOLS_MEASURED_FAILED", bad[0])
            other_model = ms.capability_codes(None, req, False, probes["loc"], "m2")          # measured on another model: ignored
            self.assertIn("TOOLS_UNVERIFIED", other_model[0])
            alias = ms.capability_codes(None, req, False, {"tools": {"ok": True, "model": "decider"}}, "default")
            self.assertIn("TOOLS_MEASURED:decider", alias[1])                                  # unfixed local model: alias named
        self.assertEqual(ms.load_probes(os.path.join(tempfile.gettempdir(), "no-such-file.json")), {})

    def test_main_refuses_cloud_and_writes_nothing_without_flag(self):
        import io
        from contextlib import redirect_stdout
        with tempfile.TemporaryDirectory() as d:
            cfg = os.path.join(d, "p.json")
            with open(cfg, "w") as f:
                json.dump({"providers": [{"name": "groq", "lane": "private", "base_url": "https://api.groq.com/openai/v1",
                                          "key_file": None, "models": ["any"]}]}, f)
            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = cp.main(["--provider", "groq", "--providers", cfg])
            self.assertEqual(rc, 2); self.assertIn("refused", buf.getvalue())


if __name__ == "__main__":
    unittest.main()
