"""POST /v1/jarvis/policy: classify one action for an agent, with no side effects (plan P2 step 4)."""
import json
import os
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

HERE = os.path.dirname(__file__)
sys.path.insert(0, os.path.join(HERE, ".."))
sys.path.insert(0, os.path.join(HERE, "..", "..", "node"))
from jarvis_kernel.gateway import Provider
from jarvis_kernel.llm_proxy import make_handler
from jarvis_kernel.store import Kernel


class PolicyEndpoint(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.k = Kernel(os.path.join(self.tmp.name, "d"))
        H, tokens = make_handler(self.k, [Provider("priv", "private", "http://127.0.0.1:9/v1", None, ["any"], priority=1)])
        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.srv.server_address[1]}"
        self.agent = tokens.issue("hermes")
        self.dashboard = tokens.issue("dash", "dashboard") if hasattr(tokens, "issue") else None

    def tearDown(self):
        self.srv.shutdown()
        self.k.db.close()
        self.tmp.cleanup()

    def ask(self, body, tok=None, path="/v1/jarvis/policy"):
        req = urllib.request.Request(self.url + path, data=json.dumps(body).encode(), method="POST",
                                     headers={"Content-Type": "application/json", "Authorization": "Bearer " + (tok or self.agent)})
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read() or b"{}")

    def count(self, table):
        return self.k.db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]

    def test_levels_follow_the_kernel_rules_and_unknown_tools_fail_closed(self):
        self.assertEqual(self.ask({"tool": "fs", "op": "read", "target": "/work/a.txt"})[1]["level"], "GREEN")
        self.assertEqual(self.ask({"tool": "fs", "op": "write", "target": "/work/a.txt"})[1]["level"], "YELLOW_APPROVAL")
        status, out = self.ask({"tool": "shell", "op": "run", "target": "ls"})
        self.assertEqual((status, out["level"]), (200, "RED"))
        self.assertIn("fail closed", out["reason"])

    def test_safety_files_are_red_and_secret_paths_are_denied(self):
        self.assertEqual(self.ask({"tool": "fs", "op": "write", "target": "/srv/x/cdp_relay.py"})[1]["level"], "RED")
        self.assertEqual(self.ask({"tool": "fs", "op": "read", "target": "/home/u/.ssh/id_ed25519"})[1]["level"], "DENY")

    def test_asking_creates_no_approval_and_no_job(self):
        before = (self.count("approvals"), self.count("jobs"))
        for body in ({"tool": "fs", "op": "write", "target": "/a"}, {"tool": "shell", "op": "run", "target": "x"},
                     {"tool": "browser", "op": "download", "target": "https://example.test/f"}):
            self.assertEqual(self.ask(body)[0], 200)
        self.assertEqual((self.count("approvals"), self.count("jobs")), before)

    def test_bad_requests_and_bad_tokens(self):
        self.assertEqual(self.ask("not a dict")[0], 400)
        self.assertEqual(self.ask({"tool": "fs", "op": "read"}, tok="not-a-token")[0], 401)

    def test_untrusted_session_flag_is_passed_on(self):
        calm = self.ask({"tool": "browser", "op": "open", "target": "https://example.test"})[1]["level"]
        self.assertEqual(calm, "GREEN")
        self.assertIn(self.ask({"tool": "browser", "op": "open", "target": "https://example.test", "untrusted": True})[1]["level"],
                      ("GREEN", "YELLOW_AUTO", "YELLOW_APPROVAL", "RED"))


if __name__ == "__main__":
    unittest.main()
