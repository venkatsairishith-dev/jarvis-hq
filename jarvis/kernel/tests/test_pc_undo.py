"""Undo (D61) under the kernel's policy: the owner's own "undo that" is an ordinary owner-direct action (no PIN); an agent's is not; secrets, banks and
password managers are refused; the node's job accepts exactly its own fields."""
import os
import sys
import unittest

HERE = os.path.dirname(__file__)
sys.path.insert(0, os.path.join(HERE, ".."))
sys.path.insert(0, HERE)
from jarvis_kernel.jobs import JobError, _check_params, action_for  # noqa: E402
from jarvis_kernel.policy import Action, DENY, YELLOW_APPROVAL, YELLOW_AUTO, decide  # noqa: E402
import test_owner as _owner_tests  # noqa: E402

OPS = ("recycle", "restore_bin", "move", "close_window", "setting")


def owner(op, target, params=None, **kw):
    return Action("undo", op, target, params or {}, owner_direct=True, **kw)


class UndoPolicy(unittest.TestCase):
    def test_the_owners_own_undo_runs_at_once_and_everyone_else_needs_the_owner(self):
        for op in OPS:
            self.assertEqual(decide(owner(op, r"C:\Users\me\Documents\notes.txt")).level, YELLOW_AUTO, op)
            self.assertEqual(decide(Action("undo", op, r"C:\Users\me\Documents\notes.txt")).level, YELLOW_APPROVAL, op)       # an agent or a node
        self.assertEqual(decide(owner("recycle", r"C:\Users\me\x.txt", lane="public")).level, YELLOW_APPROVAL)
        self.assertEqual(decide(owner("recycle", r"C:\Users\me\x.txt", untrusted_session=True)).level, YELLOW_APPROVAL)

    def test_a_file_name_with_a_scary_word_is_still_an_ordinary_undo(self):
        self.assertEqual(decide(owner("restore_bin", r"C:\Users\me\Documents\Pay slip - delete after.pdf")).level, YELLOW_AUTO)

    def test_secrets_banks_and_password_managers_are_refused(self):
        for target in (r"D:\jarvis-secrets\keys.json", r"C:\Users\me\.ssh\id_rsa", r"C:\Users\me\Documents\ICICI Bank statement.pdf",
                       r"C:\Users\me\AppData\Roaming\KeePass\db.kdbx", r"C:\Users\me\.env"):
            self.assertEqual(decide(owner("recycle", target)).level, DENY, target)
        self.assertEqual(decide(owner("close_window", "1Password")).level, DENY)

    def test_the_unknown_op_fails_closed(self):
        self.assertGreater(decide(owner("format", "C:\\")).level, YELLOW_AUTO)


class UndoJob(unittest.TestCase):
    def ok(self, **kw):
        _check_params("pc_undo", dict({"agent": "owner", "op": "recycle", "target": r"C:\Users\me\a.txt", "path": "", "expect": ""}, **kw))

    def bad(self, **kw):
        with self.assertRaises(JobError):
            self.ok(**kw)

    def test_exact_fields_and_safe_values(self):
        for op in OPS:
            self.ok(op=op)
        self.bad(op="delete")
        self.bad(op="format")
        self.bad(target="")
        self.bad(target="a" * 261)
        self.bad(target="a\nb")
        self.bad(path="a\x00b")
        self.bad(expect="x" * 65)
        self.bad(agent="a b")
        with self.assertRaises(JobError):
            _check_params("pc_undo", {"agent": "owner", "op": "recycle", "target": "x"})
        with self.assertRaises(JobError):
            _check_params("pc_undo", {"agent": "owner", "op": "recycle", "target": "x", "path": "", "expect": "", "extra": "1"})

    def test_the_action_binds_the_exact_reversal(self):
        a = action_for("pc_undo", {"agent": "owner", "op": "move", "target": r"C:\b.txt", "path": r"C:\a.txt", "expect": "abc"}, owner_direct=True)
        b = action_for("pc_undo", {"agent": "owner", "op": "move", "target": r"C:\b.txt", "path": r"C:\other.txt", "expect": "abc"}, owner_direct=True)
        self.assertEqual((a.tool, a.op), ("undo", "move"))
        self.assertNotEqual(a.digest() if hasattr(a, "digest") else a.describe(600), b.digest() if hasattr(b, "digest") else b.describe(600))


class OwnerPlaneUndo(unittest.TestCase):
    setUp, tearDown, call = _owner_tests.Plane.setUp, _owner_tests.Plane.tearDown, _owner_tests.Plane.call

    def test_the_owners_undo_is_queued_at_once_as_a_pc_undo_job(self):
        code, r = self.call("POST", "/v1/jarvis/owner/pc", {"op": "undo", "action": "recycle", "target": r"C:\Users\me\Documents\notes.txt", "expect": "abc123"})
        self.assertEqual((code, r["status"], r["kind"]), (202, "queued", "pc_undo"))
        code, r = self.call("POST", "/v1/jarvis/owner/pc", {"op": "undo", "action": "move", "target": r"C:\b.txt", "path": r"C:\a.txt", "expect": "abc"})
        self.assertEqual((code, r["kind"]), (202, "pc_undo"))

    def test_secrets_are_refused_and_an_unknown_action_is_a_bad_request(self):
        self.assertEqual(self.call("POST", "/v1/jarvis/owner/pc", {"op": "undo", "action": "recycle", "target": r"D:\jarvis-secrets\k.json"})[0], 403)
        self.assertEqual(self.call("POST", "/v1/jarvis/owner/pc", {"op": "undo", "action": "wipe", "target": "C:\\"})[0], 400)
        self.assertIn(self.call("POST", "/v1/jarvis/owner/pc", {"op": "undo", "action": "recycle", "target": "C:\\a"}, tok=self.agent)[0], (401, 403))


if __name__ == "__main__":
    unittest.main()
