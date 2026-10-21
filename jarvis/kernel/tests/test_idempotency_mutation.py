"""Regression tests added after mutation testing of idempotency.py (2026-10-19): the audit rows of a refused replay, a mismatch
and a request still in flight."""
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from jarvis_kernel.idempotency import Idempotency
from jarvis_kernel.store import Kernel


class IdempotencyAudit(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.k = Kernel(self.tmp.name)
        self.I = Idempotency(self.k, clock=lambda: 1000.0)
        self.ran = 0

    def tearDown(self):
        self.k.db.close()
        self.tmp.cleanup()

    def act(self):
        self.ran += 1
        return 200, {"n": self.ran}

    def audit(self, kind):
        return [json.loads(p) for (p,) in self.k.db.execute("SELECT payload FROM audit WHERE kind=? ORDER BY seq", (kind,)).fetchall()]

    def test_a_replay_refused_by_the_recheck_is_audited_with_its_code_and_does_not_act(self):
        self.I.run("scope-x", "key-00001", {"a": 1}, self.act)
        out = self.I.run("scope-x", "key-00001", {"a": 1}, self.act, recheck=lambda: (503, {"error": "kill switch"}))
        self.assertEqual(out, (503, {"error": "kill switch"}))
        self.assertEqual(self.audit("idempotent_replay_refused"), [{"scope": "scope-x", "key": "key-00001", "code": 503}])
        self.assertEqual(self.audit("idempotent_replay"), [])
        self.assertEqual(self.ran, 1)

    def test_a_replay_that_passes_the_recheck_is_audited_with_the_stored_code(self):
        self.I.run("scope-x", "key-00002", {"a": 1}, lambda: (201, {"made": True}))
        out = self.I.run("scope-x", "key-00002", {"a": 1}, self.act, recheck=lambda: None)
        self.assertEqual(out, (201, {"made": True, "replayed": True}))
        self.assertEqual(self.audit("idempotent_replay"), [{"scope": "scope-x", "key": "key-00002", "code": 201}])

    def test_a_different_request_under_the_same_key_is_422_and_audited(self):
        self.I.run("scope-x", "key-00003", {"a": 1}, self.act)
        code, reply = self.I.run("scope-x", "key-00003", {"a": 2}, self.act)
        self.assertEqual(code, 422)
        self.assertEqual(self.audit("idempotency_mismatch"), [{"scope": "scope-x", "key": "key-00003"}])
        self.assertEqual(self.ran, 1)

    def test_a_repeat_while_running_is_409_and_audited(self):
        seen = []
        self.I.run("scope-x", "key-00004", {"a": 1}, lambda: (seen.append(self.I.run("scope-x", "key-00004", {"a": 1}, self.act)) or (200, {})))
        self.assertEqual(seen[0][0], 409)
        self.assertEqual(self.audit("idempotency_in_flight"), [{"scope": "scope-x", "key": "key-00004"}])
        self.assertEqual(self.ran, 0)


if __name__ == "__main__":
    unittest.main()
