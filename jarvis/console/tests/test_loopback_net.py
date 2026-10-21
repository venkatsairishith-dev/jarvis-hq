"""Completion H (2026-10-19): the loopback-only / no-proxy / no-redirect opener (scripts/loopback_net.py) and the JARVIS programs that use it.
A RECORDING PROXY is configured through http_proxy/HTTP_PROXY/ALL_PROXY with no no_proxy: a plain urllib.request.urlopen goes to it (the bug),
the modules under test must never touch it. Synthetic canaries only."""
import importlib.util
import json
import os
import sys
import threading
import unittest
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPTS = os.path.join(HERE, "..", "..", "scripts")
sys.path.insert(0, SCRIPTS)
sys.path.insert(0, os.path.join(HERE, ".."))
import loopback_net  # noqa: E402

CANARY = "CANARY-7f3a-Priyanka-SYNTH"


def _serve(handler):
    srv = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


class Base(unittest.TestCase):
    def setUp(self):
        self.proxy_hits = []
        hits = self.proxy_hits

        class Proxy(BaseHTTPRequestHandler):
            def _any(self):
                n = int(self.headers.get("Content-Length") or 0)
                hits.append((self.command, self.path, self.rfile.read(n) if n else b"", dict(self.headers)))
                self.send_response(200)
                self.send_header("Content-Length", "2")
                self.end_headers()
                self.wfile.write(b"{}")
            do_GET = do_POST = do_PUT = _any

            def log_message(self, *a):
                pass
        self.proxy = _serve(Proxy)
        self.svc_hits = []
        svc_hits = self.svc_hits
        body = json.dumps({"choices": [{"message": {"content": "{\"category\": \"smalltalk_qa\"}"}}], "ok": True}).encode()

        class Svc(BaseHTTPRequestHandler):
            def _any(self):
                n = int(self.headers.get("Content-Length") or 0)
                svc_hits.append((self.command, self.path, self.rfile.read(n) if n else b""))
                if self.path.startswith("/redirect"):
                    self.send_response(302)
                    self.send_header("Location", "http://127.0.0.1:9/elsewhere")
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            do_GET = do_POST = _any

            def log_message(self, *a):
                pass
        self.svc = _serve(Svc)
        self.url = f"http://127.0.0.1:{self.svc.server_address[1]}"
        self._env = {k: os.environ.get(k) for k in ("http_proxy", "HTTP_PROXY", "all_proxy", "ALL_PROXY", "no_proxy", "NO_PROXY", "https_proxy", "HTTPS_PROXY")}
        p = f"http://127.0.0.1:{self.proxy.server_address[1]}"
        for k in self._env:
            os.environ.pop(k, None)
        for k in ("http_proxy", "HTTP_PROXY", "all_proxy", "ALL_PROXY", "https_proxy", "HTTPS_PROXY"):
            os.environ[k] = p

    def tearDown(self):
        for k, v in self._env.items():
            os.environ.pop(k, None)
            if v is not None:
                os.environ[k] = v
        self.proxy.shutdown()
        self.svc.shutdown()


class Opener(Base):
    def test_the_environment_proxy_really_captures_plain_urlopen(self):
        # urlopen() caches ONE global opener built from the environment at its first call anywhere in the process (other tests may have
        # called it before the proxy variables were set here); build_opener() reads the environment now and is what urlopen() uses.
        urllib.request.build_opener().open(urllib.request.Request(self.url + "/x", data=CANARY.encode()), timeout=5).read()
        self.assertEqual(len(self.proxy_hits), 1)                        # the premise: this is what the fix prevents
        self.assertEqual(self.svc_hits, [])

    def test_loopback_net_ignores_the_proxy_and_reaches_the_service(self):
        with loopback_net.urlopen(urllib.request.Request(self.url + "/x", data=CANARY.encode()), timeout=5) as r:
            self.assertEqual(r.status, 200)
        self.assertEqual(self.proxy_hits, [])
        self.assertEqual(self.svc_hits[0][2], CANARY.encode())

    def test_non_loopback_addresses_are_refused_before_a_socket_is_opened(self):
        for bad in ("http://example.com/", "http://127.0.0.1.nip.io/", "http://user:pw@127.0.0.1:1/", "http://0.0.0.0/", "http://[::ffff:127.0.0.1]/",
                    "http://localhost.evil.example/", "ftp://127.0.0.1/", "file:///etc/passwd", "http://127.0.0.1:99999/", "http://100.125.91.51:8790/", ""):
            with self.assertRaises(loopback_net.LoopbackError, msg=bad):
                loopback_net.urlopen(bad, timeout=1)
        self.assertEqual(self.proxy_hits, [])

    def test_a_redirect_is_not_followed(self):
        with self.assertRaises(urllib.error.HTTPError) as cm:
            loopback_net.urlopen(self.url + "/redirect", timeout=5)
        self.assertEqual(cm.exception.code, 302)
        self.assertEqual([h[1] for h in self.svc_hits], ["/redirect"])


class Callers(Base):
    def _load(self, name, rel):
        spec = importlib.util.spec_from_file_location(name, os.path.join(HERE, "..", "..", rel))
        mod = importlib.util.module_from_spec(spec)
        sys.modules[name] = mod
        spec.loader.exec_module(mod)
        return mod

    def test_intent_llm_predict_does_not_use_the_proxy(self):
        from jarvis_console import intent
        intent.llm_predict(self.url, "m", CANARY)
        self.assertEqual(self.proxy_hits, [])
        self.assertTrue(self.svc_hits)
        with self.assertRaises(ValueError):                              # a non-loopback model address is refused, not sent
            intent.llm_predict("http://example.com", "m", CANARY)
        self.assertEqual(self.proxy_hits, [])

    def test_watchdog_http_ok_does_not_use_the_proxy(self):
        wd = self._load("watchdog_h", "scripts/watchdog.py")
        ok, _ = wd.http_ok(self.url + "/health", timeout=3)
        self.assertTrue(ok)
        self.assertEqual(self.proxy_hits, [])
        self.assertEqual(wd.http_ok("http://example.com/health", timeout=1), (False, b""))
        self.assertEqual(self.proxy_hits, [])

    def test_live_check_http_does_not_use_the_proxy(self):
        lc = self._load("live_check_h", "scripts/live_check.py")
        code, _ = lc.http(self.url + "/x", method="POST", body={"c": CANARY})
        self.assertEqual(code, 200)
        self.assertEqual(self.proxy_hits, [])

    def test_open_console_asks_for_the_code_without_the_proxy(self):
        import control_auth
        import open_console
        d = os.path.join(os.environ.get("TMPDIR", "/tmp"), "lbn-run-%d" % os.getpid())
        control_auth.publish("console", "tok-" + CANARY, d)
        try:
            open_console.SERVERS["console"] = (self.svc.server_address[1], "/")
            url = open_console.launch_url("console", d)
        except RuntimeError:
            url = None                                                   # the stub answers without a code: the point is the proxy
        finally:
            control_auth.unpublish("console", d)
            open_console.SERVERS["console"] = (8799, "/")
        self.assertEqual(self.proxy_hits, [])
        self.assertTrue(any(h[1] == "/auth/code" for h in self.svc_hits))

    def test_bench_and_kernel_client_use_the_guarded_opener(self):
        for rel in ("jarvis_console/bench.py", "jarvis_console/kernel_client.py", "jarvis_console/intent.py", "jarvis_console/server.py"):
            src = open(os.path.join(HERE, "..", rel), encoding="utf-8").read()
            self.assertNotIn("urllib.request.urlopen(", src, rel)
            self.assertIn("loopback_net", src, rel)
        for rel in ("scripts/watchdog.py", "scripts/live_check.py", "scripts/open_console.py", "scripts/voice_chain_demo.py", "wake/eval_wake.py", "wake/loopback_test.py"):
            src = open(os.path.join(HERE, "..", "..", rel), encoding="utf-8").read()
            self.assertNotIn("urllib.request.urlopen(", src, rel)
            self.assertIn("loopback_net", src, rel)


if __name__ == "__main__":
    unittest.main()
