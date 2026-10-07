import json
import os
import sys
import tempfile
import threading
import unittest
import urllib.request
from http.server import ThreadingHTTPServer

HERE = os.path.dirname(__file__)
sys.path.insert(0, os.path.join(HERE, ".."))
sys.path.insert(0, os.path.join(HERE, "..", "..", "node"))
from jarvis_kernel.nodes import Nodes, make_handler
from jarvis_kernel.store import Kernel
from jarvis_node.control import Controller, RepeatError, BudgetError


class C(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.k = Kernel(os.path.join(self.tmp.name, "d"))
        H, self.nodes = make_handler(self.k)
        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.srv.server_address[1]}"
        self.token = self.nodes.pair(self.nodes.new_code(), "laptop")
        self.calls = []
        self.state = {"text": "before"}
        self.ctl = Controller(self.ask, {"click": self.click, "type": lambda t, p: self.calls.append(("type", t))}, self.snap)

    def tearDown(self):
        self.srv.shutdown(); self.k.db.close(); self.tmp.cleanup()

    def ask(self, path, obj, token=None):
        req = urllib.request.Request(self.url + path, data=json.dumps(obj).encode(), method="POST")
        req.add_header("Authorization", "Bearer " + (token or self.token))
        return json.loads(urllib.request.urlopen(req, timeout=10).read())

    def click(self, target, params):
        self.calls.append(("click", target))
        if params.get("works", True):
            self.state["text"] = "after"

    def snap(self, process, **kw):
        return dict(self.state)

    def approved(self, op, target, params=None):
        r = self.ctl.request_approval(op, target, params)
        self.assertEqual(r.status, "pending")
        self.assertTrue(self.k.approve(r.approval_id))   # the test plays the human at the console
        return r.approval_id

    def test_observe_is_allowed_without_approval(self):
        self.assertEqual(self.ctl.observe("notepad"), {"text": "before"})

    def test_action_without_approval_is_refused_and_backend_not_called(self):
        r = self.ctl.do("click", "notepad|ok")
        self.assertEqual(r.status, "refused")
        self.assertEqual(self.calls, [])

    def test_approved_action_runs_once_and_is_verified(self):
        aid = self.approved("click", "notepad|ok")
        r = self.ctl.do("click", "notepad|ok", approval_id=aid, expect=lambda b, a: a["text"] == "after")
        self.assertEqual((r.status, self.calls), ("verified", [("click", "notepad|ok")]))
        again = Controller(self.ask, {"click": self.click}, self.snap).do("click", "notepad|ok", approval_id=aid)
        self.assertEqual(again.status, "refused")      # approval is single use: replay refused
        self.assertEqual(len(self.calls), 1)

    def test_approval_for_one_action_does_not_cover_another(self):
        aid = self.approved("click", "notepad|ok")
        r = self.ctl.do("click", "notepad|delete-everything", approval_id=aid)
        self.assertEqual(r.status, "refused"); self.assertEqual(self.calls, [])

    def test_mismatch_then_exact_repeat_is_blocked(self):
        aid = self.approved("click", "notepad|ok", {"works": False})
        r = self.ctl.do("click", "notepad|ok", {"works": False}, approval_id=aid, expect=lambda b, a: a["text"] == "after")
        self.assertEqual(r.status, "mismatch")
        with self.assertRaises(RepeatError):
            self.ctl.do("click", "notepad|ok", {"works": False}, approval_id=aid)

    def test_backend_error_reports_kind_not_text(self):
        def boom(t, p): raise RuntimeError("secret screen text")
        ctl = Controller(self.ask, {"click": boom}, self.snap)
        r = ctl.request_approval("click", "notepad|x"); self.k.approve(r.approval_id)
        out = ctl.do("click", "notepad|x", approval_id=r.approval_id)
        self.assertEqual((out.status, out.detail), ("error", "RuntimeError"))

    def test_budget_stops_runaway(self):
        ctl = Controller(self.ask, {"click": self.click}, self.snap, max_actions=2)
        for i in range(2):
            r = ctl.request_approval("click", f"notepad|b{i}"); self.k.approve(r.approval_id)
            ctl.do("click", f"notepad|b{i}", approval_id=r.approval_id)
        r = ctl.request_approval("click", "notepad|b3"); self.k.approve(r.approval_id)
        with self.assertRaises(BudgetError):
            ctl.do("click", "notepad|b3", approval_id=r.approval_id)

    def test_sensitive_app_gets_no_approval_and_cannot_run(self):
        r = self.ctl.request_approval("click", "1Password|unlock")
        self.assertEqual((r.status, r.level), ("refused", "DENY"))
        self.assertEqual(self.ctl.do("click", "1Password|unlock").status, "refused")
        self.assertEqual(self.ctl.observe("Bitwarden").status, "refused")
        self.assertEqual(self.calls, [])

    def test_kill_switch_blocks_even_approved_actions(self):
        aid = self.approved("click", "notepad|ok")
        self.k.kill("test")
        r = self.ctl.do("click", "notepad|ok", approval_id=aid)
        self.assertEqual((r.status, r.level), ("refused", "KILLED"))
        self.assertEqual(self.calls, [])

    def test_unknown_token_rejected(self):
        import urllib.error
        with self.assertRaises(urllib.error.HTTPError) as cm:
            self.ask("/authorize", {"action": {"tool": "ui", "op": "snapshot", "target": "x"}}, token="nope")
        self.assertEqual(cm.exception.code, 401)

    def test_node_cannot_lower_level_by_claiming_it(self):
        d = self.ask("/request", {"action": {"tool": "money", "op": "pay", "target": "1", "level": "GREEN", "suggested": 0}})
        self.assertEqual(d["level"], "RED")


if __name__ == "__main__":
    unittest.main()
