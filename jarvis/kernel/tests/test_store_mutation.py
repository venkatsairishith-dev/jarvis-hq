"""Regression tests added after mutation testing of store.py (2026-10-19): each one pins a behaviour that a deliberate
code mutation survived, mostly the audit trail of the approval/kill paths and the stored-field limits."""
import json
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from jarvis_kernel.policy import Action
from jarvis_kernel.store import Kernel


class StoreMutation(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.k = Kernel(self.tmp.name)

    def tearDown(self):
        self.k.db.close()
        self.tmp.cleanup()

    def audit(self, kind):
        return [json.loads(p) for (p,) in self.k.db.execute("SELECT payload FROM audit WHERE kind=? ORDER BY seq", (kind,)).fetchall()]

    def pending(self, requester="agent-a", target="requests"):
        d, aid = self.k.request(Action("pkg", "install", target, requester=requester))
        self.assertIsNotNone(aid)
        return aid

    def test_kill_and_resume_are_audited(self):
        self.k.kill("because-canary")
        self.assertEqual(self.audit("kill"), [{"why": "because-canary"}])
        self.k.resume()
        self.assertEqual(len(self.audit("resume")), 1)
        self.assertFalse(self.k.killed())

    def test_request_is_audited_with_level_and_reason(self):
        self.pending()
        rows = self.audit("request")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["action"]["tool"], "pkg")
        self.assertTrue(rows[0]["reason"])

    def test_requester_stored_is_cut_at_80(self):
        aid = self.pending(requester="r" * 120)
        (stored,) = self.k.db.execute("SELECT requester FROM approvals WHERE id=?", (aid,)).fetchone()
        self.assertEqual(stored, "r" * 80)

    def test_approve_refused_under_kill_switch_is_audited(self):
        aid = self.pending()
        self.k.kill("t")
        self.assertFalse(self.k.approve(aid))
        self.assertEqual(self.audit("approve_refused"), [{"id": aid, "why": "kill switch"}])

    def test_incomplete_card_is_not_approved_and_is_audited(self):
        aid = self.pending()
        self.k.db.execute("UPDATE approvals SET action=? WHERE id=?", (json.dumps({"target": "x" * 10 + "...[truncated]"}), aid))
        self.assertFalse(self.k.approve(aid))
        self.assertEqual(self.audit("approve_refused"), [{"id": aid, "why": "card incomplete"}])
        self.assertEqual(self.k.db.execute("SELECT state FROM approvals WHERE id=?", (aid,)).fetchone()[0], "pending")

    def test_kill_landing_between_check_and_update_withdraws_the_approval(self):
        aid = self.pending()
        with mock.patch.object(self.k, "killed", side_effect=[False, True]):
            self.assertFalse(self.k.approve(aid))
        state, reason = self.k.db.execute("SELECT state, reason FROM approvals WHERE id=?", (aid,)).fetchone()
        self.assertEqual((state, reason), ("denied", "withdrawn: kill switch"))
        self.assertEqual(self.audit("approve_refused"), [{"id": aid, "why": "kill switch"}])
        self.assertEqual(self.audit("approved"), [])

    def test_withdraw_returns_true_audits_and_cuts_reason(self):
        aid = self.pending()
        self.assertTrue(self.k.withdraw(aid, "w" * 300))
        self.assertEqual(self.audit("approval_withdrawn"), [{"id": aid, "why": "w" * 60}])
        (reason,) = self.k.db.execute("SELECT reason FROM approvals WHERE id=?", (aid,)).fetchone()
        self.assertEqual(len(reason), 200)
        self.assertTrue(reason.startswith("withdrawn: w"))
        self.assertFalse(self.k.withdraw(aid))                   # already denied: nothing more to withdraw
        self.assertEqual(len(self.audit("approval_withdrawn")), 1)

    def test_deny_is_audited_once(self):
        aid = self.pending()
        self.assertTrue(self.k.deny(aid))
        self.assertFalse(self.k.deny(aid))
        self.assertEqual(self.audit("denied"), [{"id": aid}])
        self.assertEqual(self.audit("deny_refused"), [{"id": aid}])

    def test_wrong_reason_wording_for_other_agent_and_other_action(self):
        aid = self.pending(requester="agent-a", target="requests")
        self.assertTrue(self.k.approve(aid))
        # another agent, SAME action: the log says it is another agent's approval
        d = self.k.authorize(Action("pkg", "install", "requests", requester="agent-b"), approval_id=aid)
        self.assertFalse(d.allowed)
        self.assertEqual(d.reason, "approval was given for another agent's request")
        # another agent AND another action: it is a different action, never "another agent"
        d = self.k.authorize(Action("pkg", "install", "other-package", requester="agent-b"), approval_id=aid)
        self.assertFalse(d.allowed)
        self.assertEqual(d.reason, "approval is for a different action")
        # same agent, other action
        d = self.k.authorize(Action("pkg", "install", "other-package", requester="agent-a"), approval_id=aid)
        self.assertEqual(d.reason, "approval is for a different action")
        # the right one still works exactly once
        self.assertTrue(self.k.authorize(Action("pkg", "install", "requests", requester="agent-a"), approval_id=aid).allowed)
        self.assertFalse(self.k.authorize(Action("pkg", "install", "requests", requester="agent-a"), approval_id=aid).allowed)

    def test_text_len_counts_strings_keys_and_nesting(self):
        n = Kernel._text_len
        self.assertEqual(n("abc"), 3)
        self.assertEqual(n({"ab": "cde", "f": ["gh", ("i",)]}), 2 + 3 + 1 + 2 + 1)
        self.assertEqual(n(12345), 5)


if __name__ == "__main__":
    unittest.main()
