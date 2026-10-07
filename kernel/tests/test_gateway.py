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

    # shipped config
    def test_shipped_config_is_valid_and_safe(self):
        provs = load(os.path.join(os.path.dirname(__file__), "..", "..", "config", "providers.json"))
        for p in provs:
            self.assertEqual(p.problems(), [], p.name)
            self.assertFalse(p.paid, p.name)
        cloud = [p for p in provs if p.lane != "local"]
        self.assertTrue(all(not p.enabled and p.base_url is None for p in cloud))  # nothing live until keys + verified URLs
        with self.assertRaises(RouteError):
            plan([p for p in provs if p.lane == "public"], "public")


if __name__ == "__main__":
    unittest.main()
