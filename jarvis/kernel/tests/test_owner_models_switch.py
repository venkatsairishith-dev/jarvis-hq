"""The owner's own "which provider am I on / switch to <provider>" (plan item 19): lane-checked, kernel-recorded, audited, and effective."""
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
from jarvis_kernel.llm_proxy import make_handler
from jarvis_kernel.store import Kernel


def upstream(name, seen):
    class U(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.0"

        def log_message(self, *a):
            pass

        def do_POST(self):
            self.rfile.read(int(self.headers["Content-Length"]))
            seen.append(name)
            out = json.dumps({"choices": [{"message": {"role": "assistant", "content": name}}]}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(out)))
            self.end_headers()
            self.wfile.write(out)
    s = ThreadingHTTPServer(("127.0.0.1", 0), U)
    threading.Thread(target=s.serve_forever, daemon=True).start()
    return s, f"http://127.0.0.1:{s.server_address[1]}/v1"


class OwnerSwitch(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.k = Kernel(os.path.join(self.tmp.name, "d"))
        self.seen = []
        self.ups = [upstream(n, self.seen) for n in ("pubA", "pubB", "priv")]
        provs = [Provider("pubA", "public", self.ups[0][1], None, ["any"], priority=1, model_pattern="."),
                 Provider("pubB", "public", self.ups[1][1], None, ["any"], priority=2, model_pattern="."),
                 Provider("priv", "private", self.ups[2][1], None, ["any"], priority=1)]
        H, tokens = make_handler(self.k, provs)
        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.srv.server_address[1]}"
        self.owner = tokens.issue("console", "owner")
        self.pub_agent = tokens.issue("lab", "public")
        self.priv_agent = tokens.issue("hermes", "private")

    def tearDown(self):
        for s, _ in self.ups:
            s.shutdown()
        self.srv.shutdown()
        self.k.db.close()
        self.tmp.cleanup()

    def call(self, method, path, body=None, tok=None):
        data = json.dumps(body).encode() if body is not None else None
        r = urllib.request.Request(self.url + path, data=data, method=method,
                                   headers={"Authorization": "Bearer " + (tok or self.owner), "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(r, timeout=10) as resp:
                return resp.status, json.loads(resp.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read() or b"{}")

    def chat(self, tok):
        return self.call("POST", "/v1/chat/completions", {"model": "any", "messages": [{"role": "user", "content": "hi"}]}, tok=tok)

    def lanes(self):
        return self.call("GET", "/v1/jarvis/owner/models/lanes")[1]["lanes"]

    def switch(self, lane, provider):
        return self.call("POST", "/v1/jarvis/owner/models/switch", {"lane": lane, "provider": provider})

    def test_lane_state_lists_each_lanes_chain_and_no_preference_at_first(self):
        lanes = self.lanes()
        self.assertEqual(lanes["public"]["chain"], ["pubA", "pubB"])
        self.assertEqual(lanes["private"]["chain"], ["priv"])
        self.assertIsNone(lanes["public"]["preferred"])

    def test_switching_reorders_the_chain_for_that_lane_and_a_call_goes_to_the_chosen_provider_first(self):
        self.assertEqual(self.chat(self.pub_agent)[0], 200)
        self.assertEqual(self.seen[-1], "pubA")                          # the normal order
        status, out = self.switch("public", "pubB")
        self.assertEqual((status, out["preferred"]), (200, "pubB"))
        self.assertEqual(self.lanes()["public"]["preferred"], "pubB")
        self.assertEqual(self.chat(self.pub_agent)[0], 200)
        self.assertEqual(self.seen[-1], "pubB")                          # now asked first
        self.switch("public", "auto")                                    # back to normal
        self.assertIsNone(self.lanes()["public"]["preferred"])
        self.chat(self.pub_agent)
        self.assertEqual(self.seen[-1], "pubA")

    def test_a_switch_never_crosses_lanes(self):
        self.assertEqual(self.switch("public", "priv")[0], 400)           # a private provider is not usable in the public lane
        self.assertEqual(self.switch("private", "pubA")[0], 400)          # a public provider is never reached from the private lane
        self.assertIsNone(self.lanes()["public"]["preferred"])
        self.assertIsNone(self.lanes()["private"]["preferred"])
        self.chat(self.priv_agent)
        self.assertEqual(self.seen[-1], "priv")

    def test_unknown_provider_bad_lane_and_missing_fields_are_refused(self):
        self.assertEqual(self.switch("public", "nope")[0], 400)
        self.assertEqual(self.switch("everywhere", "pubA")[0], 400)
        self.assertEqual(self.switch("", "pubA")[0], 400)

    def test_the_switch_is_recorded_in_the_audit_log_as_the_owner(self):
        self.switch("public", "pubB")
        self.switch("public", "auto")
        rows = self.k.db.execute("SELECT payload FROM audit WHERE kind='lane_preference_set' ORDER BY seq").fetchall()
        events = [json.loads(r[0]) for r in rows]
        self.assertEqual([(e["lane"], e["provider"], e["agent"]) for e in events], [("public", "pubB", "owner"), ("public", "auto", "owner")])
        ok, msg = self.k.audit.verify(None)
        self.assertTrue(ok, msg)

    def test_an_agent_token_cannot_use_the_owner_routes(self):
        for tok in (self.pub_agent, self.priv_agent):
            self.assertIn(self.call("POST", "/v1/jarvis/owner/models/switch", {"lane": "public", "provider": "pubB"}, tok=tok)[0], (401, 403))
            self.assertIn(self.call("GET", "/v1/jarvis/owner/models/lanes", tok=tok)[0], (401, 403))
        self.assertIsNone(self.lanes()["public"]["preferred"])

    def test_the_agents_own_prefer_action_and_the_owners_switch_share_one_preference(self):
        s, out = self.call("POST", "/v1/jarvis/actions", {"tool": "models", "op": "prefer", "target": "pubB"}, tok=self.pub_agent)
        self.assertEqual(s, 200)
        self.assertEqual(self.lanes()["public"]["preferred"], "pubB")
        self.switch("public", "pubA")
        s, out = self.call("POST", "/v1/jarvis/actions", {"tool": "models", "op": "status"}, tok=self.pub_agent)
        self.assertEqual(out["preferred"], "pubA")


if __name__ == "__main__":
    unittest.main()
