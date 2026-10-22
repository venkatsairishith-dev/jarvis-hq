"""Review additions: promotion records are checked against the audit log (when a verifier is given), and history is capped."""
import json
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", ".."))
from skills import curator as C  # noqa: E402

AUDIT = {"audit-17", "audit-18"}


def verifier(record):
    return record in AUDIT


class Clock:
    t = 1_000_000.0

    def __call__(self):
        return self.t


class PromotionVerification(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.tmp.name, "skills.json")
        self.clock = Clock()

    def tearDown(self):
        self.tmp.cleanup()

    def make(self, **kw):
        return C.Curator(self.path, clock=self.clock, promotion_verifier=verifier, **kw)

    def test_promote_refuses_a_record_that_is_not_in_the_audit_log(self):
        cur = self.make()
        cur.stage("tidy", "a" * 64)
        with self.assertRaises(C.CuratorError):
            cur.promote("tidy", "made-up-record")
        self.assertEqual(cur.skills["tidy"]["state"], "staged")
        cur.promote("tidy", "audit-17")
        self.assertEqual(cur.skills["tidy"]["state"], "active")

    def test_a_store_with_an_active_skill_whose_record_is_unknown_is_refused_on_load(self):
        cur = self.make()
        cur.stage("tidy", "a" * 64)
        cur.promote("tidy", "audit-17")
        data = json.load(open(self.path, encoding="utf-8"))
        data["skills"]["tidy"]["promotion_record"] = "forged-record"      # someone edits the file by hand
        json.dump(data, open(self.path, "w", encoding="utf-8"))
        with self.assertRaises(C.CuratorError) as cm:
            self.make()
        self.assertNotIn("forged-record", str(cm.exception))

    def test_without_a_verifier_only_presence_is_checked(self):
        cur = C.Curator(self.path, clock=self.clock)
        cur.stage("tidy", "a" * 64)
        cur.promote("tidy", "anything")
        self.assertEqual(cur.skills["tidy"]["state"], "active")

    def test_history_is_capped(self):
        cur = self.make()
        cur.stage("tidy", "a" * 64)
        cur.promote("tidy", "audit-17")
        for i in range(C.MAX_HISTORY + 50):
            cur.skills["tidy"]["state"] = "active"
            cur._transition(cur.skills["tidy"], "stale", f"r{i}", "curator", self.clock())
        self.assertEqual(len(cur.history("tidy")), C.MAX_HISTORY)


if __name__ == "__main__":
    unittest.main()
