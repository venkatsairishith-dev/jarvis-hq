"""Router shadow mode (plan item 19): it watches the live chain and records what it would have picked; it never changes a call."""
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
from jarvis_kernel import gateway
from jarvis_kernel.gateway import Provider
from jarvis_kernel.llm_proxy import make_handler
from jarvis_kernel.router import shadow as rs
from jarvis_kernel.store import Kernel


def upstream(name, seen, script):
    """script: list of HTTP statuses for this provider's successive calls (200 after it runs out)."""
    class U(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.0"

        def log_message(self, *a):
            pass

        def do_POST(self):
            self.rfile.read(int(self.headers["Content-Length"]))
            seen.append(name)
            status = script.pop(0) if script else 200
            out = json.dumps({"choices": [{"message": {"role": "assistant", "content": name}}]} if status == 200 else {"error": "x"}).encode()
            self.send_response(status)
            if status == 429:
                self.send_header("Retry-After", "300")
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(out)))
            self.end_headers()
            self.wfile.write(out)
    s = ThreadingHTTPServer(("127.0.0.1", 0), U)
    threading.Thread(target=s.serve_forever, daemon=True).start()
    return s, f"http://127.0.0.1:{s.server_address[1]}/v1"


class Unit(unittest.TestCase):
    def setUp(self):
        self.t = [1000.0]
        self.events = []
        provs = [Provider("a", "public", "http://x/v1", None, ["any"], priority=1, model_pattern="."),
                 Provider("b", "public", "http://x/v1", None, ["any"], priority=2, model_pattern=".")]
        self.sh = rs.Shadow(provs, lambda lane: gateway.plan(provs, lane), audit=lambda k, p: self.events.append((k, p)), clock=lambda: self.t[0])

    def test_mode_defaults_to_off_and_only_known_modes_are_accepted(self):
        for v in (None, "", "off", "OFF", "on", "yes", "decide", 3):
            self.assertEqual(rs.normalize_mode(v), "off")
        self.assertEqual(rs.normalize_mode(" Shadow "), "shadow")

    def test_agreement_is_counted_and_not_audited(self):
        self.sh.observe("public", ["a", "b"], 100)
        s = self.sh.snapshot()
        self.assertEqual((s["counts"]["calls"], s["counts"]["agree"], s["counts"]["differ"]), (1, 1, 0))
        self.assertEqual(self.events, [])

    def test_a_difference_is_recorded_with_the_router_reasons_and_audited_once_a_minute(self):
        for _ in range(5):
            self.sh.outcome("a", 500)                       # the router's breaker for a opens after five server errors
        self.sh.observe("public", ["a", "b"], 100)          # the live chain still asks a first
        self.sh.observe("public", ["a", "b"], 100)
        s = self.sh.snapshot()
        self.assertEqual((s["counts"]["agree"], s["counts"]["differ"]), (0, 2))
        self.assertEqual(s["recent_differences"][-1]["live"], "a")
        self.assertEqual(s["recent_differences"][-1]["router"], "b")
        self.assertEqual(s["recent_differences"][-1]["router_skipped"], [["a", "breaker open"]])
        self.assertEqual(len(self.events), 1)               # the second identical difference inside a minute is only counted
        self.assertEqual(self.events[0][0], "router_shadow_diff")
        self.t[0] += 61
        self.sh.outcome("a", 500)                           # the breaker had cooled to a probe; it fails again and opens
        self.sh.observe("public", ["a", "b"], 100)
        self.assertEqual(len(self.events), 2)

    def test_the_router_has_its_own_state_so_it_cannot_touch_the_live_path(self):
        self.sh.observe("public", ["a"], 10)
        self.assertEqual(self.sh.snapshot()["breakers"], {"a": "closed", "b": "closed"})

    def test_a_fault_inside_shadow_mode_is_counted_and_never_raised(self):
        def boom(*a, **k):
            raise RuntimeError("broken on purpose")
        self.sh.router.pick = self.sh.router.report = boom
        self.sh.observe("public", ["a"], 10)
        self.sh.outcome("a", 200)
        self.assertEqual(self.sh.snapshot()["counts"]["errors"], 2)

    def test_an_empty_live_chain_and_an_unknown_lane_do_not_raise(self):
        self.sh.observe("public", [], 10)
        self.sh.observe("private", ["x"], 10)
        self.assertEqual(self.sh.snapshot()["counts"]["calls"], 2)


class Live(unittest.TestCase):
    def build(self, mode, a_script):
        self.seen = []
        self.tmp = tempfile.TemporaryDirectory()
        self.k = Kernel(os.path.join(self.tmp.name, "d"))
        self.ups = [upstream("a", self.seen, list(a_script)), upstream("b", self.seen, [])]
        provs = [Provider("a", "public", self.ups[0][1], None, ["any"], priority=1, model_pattern="."),
                 Provider("b", "public", self.ups[1][1], None, ["any"], priority=2, model_pattern=".")]
        H, tokens = make_handler(self.k, provs, router_mode=mode)
        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.srv.server_address[1]}"
        self.owner, self.lab = tokens.issue("console", "owner"), tokens.issue("lab", "public")

    def tearDown(self):
        for s, _ in self.ups:
            s.shutdown()
        self.srv.shutdown()
        self.k.db.close()
        self.tmp.cleanup()

    def call(self, path, body=None, tok=None):
        r = urllib.request.Request(self.url + path, data=json.dumps(body).encode() if body is not None else None, method="POST" if body is not None else "GET",
                                   headers={"Authorization": "Bearer " + (tok or self.owner), "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(r, timeout=10) as resp:
                return resp.status, json.loads(resp.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read() or b"{}")

    def chat(self, n):
        return [self.call("/v1/chat/completions", {"model": "any", "messages": [{"role": "user", "content": "hi"}]}, tok=self.lab)[0] for _ in range(n)]

    def test_off_is_the_default_and_records_nothing(self):
        self.build(None, [429])
        self.assertEqual(self.get_router(), {"mode": "off"})
        self.chat(3)
        self.assertEqual(self.k.db.execute("SELECT COUNT(*) FROM audit WHERE kind='router_shadow_diff'").fetchone()[0], 0)

    def get_router(self):
        r = urllib.request.Request(self.url + "/v1/jarvis/owner/models/router", headers={"Authorization": "Bearer " + self.owner})
        with urllib.request.urlopen(r, timeout=10) as resp:
            return json.loads(resp.read())

    def test_shadow_notices_the_live_rest_the_router_does_not_have_yet_and_changes_no_answer(self):
        # the same script is replayed through both modes; the providers that answered must be identical
        self.build("shadow", [429])
        shadow_statuses = self.chat(3)
        shadow_served = list(self.seen)
        snap = self.get_router()
        self.tearDown()
        self.build("off", [429])
        off_statuses = self.chat(3)
        self.assertEqual((shadow_statuses, shadow_served), (off_statuses, list(self.seen)))
        self.assertEqual(shadow_served, ["a", "b", "b", "b"])      # a said 429 once, rests; then b serves
        self.assertEqual(snap["mode"], "shadow")
        c = snap["counts"]
        self.assertEqual((c["calls"], c["agree"], c["differ"]), (3, 1, 2))
        self.assertEqual(snap["recent_differences"][-1]["live"], "b")
        self.assertEqual(snap["recent_differences"][-1]["router"], "a")
        self.assertEqual(c["errors"], 0)

    def test_the_audit_log_still_verifies_and_the_snapshot_needs_the_owner_token(self):
        self.build("shadow", [429])
        self.chat(3)
        ok, msg = self.k.audit.verify(None)
        self.assertTrue(ok, msg)
        r = urllib.request.Request(self.url + "/v1/jarvis/owner/models/router", headers={"Authorization": "Bearer " + self.lab})
        with self.assertRaises(urllib.error.HTTPError) as cm:
            urllib.request.urlopen(r, timeout=10)
        self.assertIn(cm.exception.code, (401, 403))


if __name__ == "__main__":
    unittest.main()
