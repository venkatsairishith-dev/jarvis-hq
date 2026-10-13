import json
import os
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from jarvis_kernel import gateway
from jarvis_kernel.gateway import Provider, RouteError, plan, call, allowed, load
from jarvis_kernel.scrub import scrub
from jarvis_kernel.store import Kernel

SEEN = {}


def fake_server(name):
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a): pass
        def do_POST(self):
            body = self.rfile.read(int(self.headers["Content-Length"]))
            SEEN.setdefault(name, []).append((body.decode(), self.headers.get("Authorization")))
            out = json.dumps({"choices": [{"message": {"content": f"hello from {name}"}}]}).encode()
            self.send_response(200); self.send_header("Content-Length", str(len(out))); self.end_headers(); self.wfile.write(out)
    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"http://127.0.0.1:{srv.server_address[1]}/v1"


class G(unittest.TestCase):
    def setUp(self):
        SEEN.clear()
        self.tmp = tempfile.TemporaryDirectory()
        self.k = Kernel(os.path.join(self.tmp.name, "d"))
        self.keyf = os.path.join(self.tmp.name, "k.key")
        open(self.keyf, "w").write("sk-SECRETKEYVALUE1234567890abcdef")
        self.s_priv, self.u_priv = fake_server("priv")
        self.s_pub, self.u_pub = fake_server("pub")
        self.s_loc, self.u_loc = fake_server("loc")

    def tearDown(self):
        for s in (self.s_priv, self.s_pub, self.s_loc): s.shutdown()
        self.k.db.close(); self.tmp.cleanup()

    def provs(self):
        return [
            Provider("pub", "public", self.u_pub, None, ["any"]),
            Provider("priv", "private", self.u_priv, self.keyf, ["any"]),
            Provider("loc", "local", self.u_loc, None, ["any"]),
        ]

    # lanes
    def test_lane_rules(self):
        self.assertTrue(allowed("private", "local") and allowed("private", "private"))
        self.assertFalse(allowed("private", "public"))
        self.assertFalse(allowed("public", "private"))
        self.assertTrue(allowed("public", "public") and allowed("public", "local"))
        self.assertFalse(allowed("whatever", "local"))

    def test_private_task_never_planned_on_public(self):
        names = [p.name for p in plan(self.provs(), "private")]
        self.assertEqual(names, ["loc", "priv"])
        self.assertNotIn("pub", names)
        self.assertEqual([p.name for p in plan(self.provs(), "public")], ["loc", "pub"])

    def test_only_public_available_means_private_task_fails_closed(self):
        with self.assertRaises(RouteError):
            plan([Provider("pub", "public", self.u_pub, None, ["any"])], "private")

    def test_fallback_stays_in_lane(self):
        dead_priv = Provider("deadpriv", "private", "http://127.0.0.1:9/v1", None, ["any"])
        with self.assertRaises(RouteError):  # the live public provider must NOT be used as a fallback
            call(self.k, [dead_priv, Provider("pub", "public", self.u_pub, None, ["any"])], "private", "hi", timeout=2)
        self.assertNotIn("pub", SEEN)
        # same-lane fallback does work
        out = call(self.k, [dead_priv, Provider("priv", "private", self.u_priv, None, ["any"])], "private", "hi", timeout=2)
        self.assertEqual(out, "hello from priv")

    # paid / availability
    def test_paid_or_bad_lane_provider_never_used(self):
        paid = Provider("paid", "private", self.u_priv, None, ["any"], paid=True)
        weird = Provider("weird", "everyone", self.u_priv, None, ["any"])
        self.assertFalse(paid.available() or weird.available())
        with self.assertRaises(RouteError):
            plan([paid, weird], "private")

    def test_missing_key_file_or_disabled_is_skipped(self):
        nokey = Provider("nokey", "private", self.u_priv, os.path.join(self.tmp.name, "absent.key"), ["any"])
        off = Provider("off", "private", self.u_priv, None, ["any"], enabled=False)
        nourl = Provider("nourl", "private", None, None, ["any"])
        with self.assertRaises(RouteError):
            plan([nokey, off, nourl], "private")

    # scrubber and secrets
    def test_private_key_blocks_the_call(self):
        with self.assertRaises(RouteError):
            call(self.k, self.provs(), "private", "here -----BEGIN OPENSSH PRIVATE KEY----- abc")
        self.assertEqual(SEEN, {})

    def test_tokens_redacted_before_sending(self):
        text = "use gho_abcdefghijklmnopqrstuvwxyz0123 and API_KEY=supersecretvalue99 and sk-abcdefghijklmnopqrstuv123"
        call(self.k, self.provs(), "private", text)
        sent = SEEN["loc"][0][0]
        for bad in ["gho_abcdef", "supersecretvalue99", "sk-abcdefghij"]:
            self.assertNotIn(bad, sent)
        self.assertIn("REDACTED", sent)

    def test_key_sent_as_header_but_never_in_audit(self):
        call(self.k, [Provider("priv", "private", self.u_priv, self.keyf, ["any"])], "private", "hi")
        self.assertEqual(SEEN["priv"][0][1], "Bearer sk-SECRETKEYVALUE1234567890abcdef")
        rows = " ".join(r[0] for r in self.k.db.execute("SELECT payload FROM audit"))
        self.assertNotIn("SECRETKEYVALUE", rows)

    def test_scrub_findings_do_not_contain_values(self):
        _, f, blocked = scrub("TOKEN=abcdefghijklmnop")
        self.assertEqual((f, blocked), (["key_assignment"], False))

    # provider options added for real providers
    def test_relative_key_file_resolves_against_secrets_dir(self):
        d = os.path.join(self.tmp.name, "sec"); os.makedirs(os.path.join(d, "providers"))
        open(os.path.join(d, "providers", "x.key"), "w").write("k")
        old = os.environ.get("JARVIS_SECRETS"); os.environ["JARVIS_SECRETS"] = d
        try:
            p = Provider("x", "private", self.u_priv, "providers/x.key", ["any"])
            self.assertTrue(p.available())
            self.assertFalse(Provider("y", "private", self.u_priv, "providers/absent.key", ["any"]).available())
        finally:
            if old is None: os.environ.pop("JARVIS_SECRETS")
            else: os.environ["JARVIS_SECRETS"] = old

    def test_model_pattern_refuses_paid_model_before_any_request(self):
        p = Provider("or", "private", self.u_priv, None, ["any"], default_model="x/y:free", model_pattern=":free$")
        with self.assertRaises(RouteError):
            call(self.k, [p], "private", "hi", model="anthropic/claude-expensive")
        self.assertNotIn("priv", SEEN)
        self.assertEqual(call(self.k, [p], "private", "hi"), "hello from priv")  # default model passes the pattern
        self.assertIn("model_refused", [r[0] for r in self.k.db.execute("SELECT kind FROM audit")])

    def test_extra_body_and_default_model_sent(self):
        p = Provider("or", "private", self.u_priv, None, ["any"], default_model="a/b:free",
                     extra_body={"provider": {"data_collection": "deny"}})
        call(self.k, [p], "private", "hi")
        sent = json.loads(SEEN["priv"][0][0])
        self.assertEqual(sent["model"], "a/b:free")
        self.assertEqual(sent["provider"], {"data_collection": "deny"})
        self.assertIn("max_tokens", sent)

    def test_empty_reply_falls_through_to_next_provider_in_lane(self):
        class Empty(BaseHTTPRequestHandler):
            def log_message(self, *a): pass
            def do_POST(self):
                self.rfile.read(int(self.headers["Content-Length"]))
                out = json.dumps({"choices": [{"message": {"content": None}}]}).encode()
                self.send_response(200); self.send_header("Content-Length", str(len(out))); self.end_headers(); self.wfile.write(out)
        srv = ThreadingHTTPServer(("127.0.0.1", 0), Empty)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        empty = Provider("empty", "private", f"http://127.0.0.1:{srv.server_address[1]}/v1", None, ["any"])
        out = call(self.k, [empty, Provider("priv", "private", self.u_priv, None, ["any"])], "private", "hi")
        self.assertEqual(out, "hello from priv")
        srv.shutdown()

    def test_os_only_provider_is_unavailable_on_the_other_os(self):
        other = "posix" if os.name == "nt" else "nt"
        self.assertFalse(Provider("x", "local", self.u_loc, None, ["any"], os_only=other).available())
        self.assertTrue(Provider("x", "local", self.u_loc, None, ["any"], os_only=os.name).available())
        self.assertTrue(Provider("x", "local", self.u_loc, None, ["any"]).available())
        provs = load(os.path.join(os.path.dirname(__file__), "..", "..", "config", "providers.json"))
        self.assertEqual([p for p in provs if p.name == "local-llama-vulkan"][0].os_only, "nt")

    def test_priority_orders_providers_but_never_widens_the_lane(self):
        slow = Provider("slowlocal", "local", self.u_loc, None, ["any"], priority=90)
        fast = Provider("fastpriv", "private", self.u_priv, None, ["any"], priority=20)
        pub = Provider("pub", "public", self.u_pub, None, ["any"], priority=1)
        self.assertEqual([p.name for p in plan([slow, fast, pub], "private")], ["fastpriv", "slowlocal"])   # priority wins; public never appears
        self.assertEqual([p.name for p in plan([slow, fast, pub], "public")], ["pub", "slowlocal"])
        self.assertEqual(call(self.k, [slow, fast], "private", "hi"), "hello from priv")                  # the fast one answered
        # equal priority: local first (the original behaviour)
        self.assertEqual([p.name for p in plan(self.provs(), "private")], ["loc", "priv"])

    def test_provider_timeout_overrides_the_default(self):
        class Slow(BaseHTTPRequestHandler):
            def log_message(self, *a): pass
            def do_POST(self):
                import time; self.rfile.read(int(self.headers["Content-Length"])); time.sleep(2.5)
                out = json.dumps({"choices": [{"message": {"content": "late answer"}}]}).encode()
                self.send_response(200); self.send_header("Content-Length", str(len(out))); self.end_headers(); self.wfile.write(out)
        srv = ThreadingHTTPServer(("127.0.0.1", 0), Slow); threading.Thread(target=srv.serve_forever, daemon=True).start()
        url = f"http://127.0.0.1:{srv.server_address[1]}/v1"
        with self.assertRaises(RouteError):    # default timeout 1 s: too short for this provider
            call(self.k, [Provider("slow", "local", url, None, ["any"])], "private", "hi", timeout=1)
        self.assertEqual(call(self.k, [Provider("slow", "local", url, None, ["any"], timeout_s=10)], "private", "hi", timeout=1), "late answer")
        srv.shutdown()

    def test_loader_accepts_bom(self):
        p = os.path.join(self.tmp.name, "p.json")
        open(p, "wb").write(b"\xef\xbb\xbf" + json.dumps({"providers": [{"name": "a", "lane": "local", "base_url": "http://x", "key_file": None, "models": ["any"]}]}).encode())
        self.assertEqual(load(p)[0].name, "a")

    # shipped config
    def test_shipped_config_is_valid_and_safe(self):
        provs = load(os.path.join(os.path.dirname(__file__), "..", "..", "config", "providers.json"))
        for p in provs:
            self.assertEqual(p.problems(), [], p.name)
            self.assertFalse(p.paid, p.name)
        for p in provs:
            if p.enabled and p.lane != "local":      # an enabled cloud provider needs a URL and either a key or a model allow-list
                self.assertTrue(p.base_url and (p.key_file or p.model_pattern), p.name)
            if p.enabled and p.lane == "public":     # public lane (since 2026-10-09: lab agents with public-lane tokens): models pinned
                self.assertTrue(p.model_pattern, p.name)
            if "openrouter" in p.name:
                self.assertEqual(p.model_pattern, ":free$", p.name)        # OpenRouter may only use free models
        priv = [p for p in provs if p.name == "openrouter-private"][0]
        self.assertEqual(priv.extra_body, {"provider": {"data_collection": "deny"}})
        # tunnelled, free-for-all or decentralised (operators can read requests) routes are never private
        self.assertFalse([p for p in provs if p.lane != "public" and p.name.startswith(("colab", "kaggle", "omniroute", "dahl", "gonka"))])


if __name__ == "__main__":
    unittest.main()
