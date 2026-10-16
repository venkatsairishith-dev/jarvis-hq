import json
import os
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from jarvis_kernel.audit import AuditLog, GENESIS, _h
from jarvis_kernel.policy import Action, GREEN, YELLOW_APPROVAL, RED, DENY, decide
from jarvis_kernel.store import Kernel, Killed

GREEN_ACTIONS = [
    ("fs", "read", "C:/work/readme.md"), ("fs", "list", "C:/work"), ("state", "collect", ""), ("state", "read", "hardware"),
    ("git", "status", "repo"), ("git", "log", "repo"), ("git", "diff", "repo"), ("git", "branch_create", "feature-x"),
    ("tests", "run", "repo"), ("docs", "read", "https://docs.example.com"), ("fs", "read", "D:/jarvis/docs/a.md"),
    ("state", "list_models", ""),
]
NON_GREEN = [
    ("pkg", "install", "requests"), ("config", "write", "/etc/x"), ("service", "restart_noncritical", "cron"),
    ("message", "send_routine", "hi"), ("fs", "write", "a.txt"), ("money", "transfer", "100"), ("delete", "file", "a"),
    ("credential", "rotate", "k"), ("publish", "post", "site"), ("egress", "send", "host"), ("oci", "terminate", "vm"),
    ("unknown", "thing", ""),
]


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.k = Kernel(self.tmp.name)

    def tearDown(self):
        self.k.db.close()
        self.tmp.cleanup()


class TestClassification(Base):
    def test_green_actions_are_green(self):
        for t, o, g in GREEN_ACTIONS:
            self.assertEqual(decide(Action(t, o, g)).level, GREEN, (t, o))

    def test_non_green_never_green(self):
        for t, o, g in NON_GREEN:
            self.assertGreater(decide(Action(t, o, g)).level, GREEN, (t, o))

    def test_unknown_fails_closed(self):
        self.assertEqual(decide(Action("nothing", "here")).level, RED)

    def test_model_can_raise_not_lower(self):
        self.assertEqual(decide(Action("fs", "read", "a"), suggested=RED).level, RED)
        self.assertEqual(decide(Action("money", "pay", "1"), suggested=GREEN).level, RED)

    def test_secret_paths_denied(self):
        for tgt in ["D:\\jarvis-secrets\\keys.json", "~/.jarvis/secrets.env", "x/id_ed25519", "a/b.pem"]:
            self.assertEqual(decide(Action("fs", "read", tgt)).level, DENY, tgt)

    def test_protected_resource_denied(self):
        k = Kernel(self.tmp.name + "/p", protected={"ocid1.instance.oc1..PROTECTEDVM"})
        d = decide(Action("oci", "terminate", "ocid1.instance.oc1..PROTECTEDVM"), protected=k.protected)
        self.assertEqual(d.level, DENY)
        k.db.close()

    def test_egress_removed_after_untrusted_read(self):
        self.assertEqual(decide(Action("message", "send_routine", "x", untrusted_session=True)).level, DENY)
        self.assertEqual(decide(Action("fs", "read", "x", untrusted_session=True)).level, GREEN)

    def test_lane_must_be_known(self):
        self.assertEqual(decide(Action("fs", "read", "a", lane="whatever")).level, DENY)


class TestGrants(Base):
    def test_green_runs_without_approval(self):
        self.assertTrue(self.k.authorize(Action("fs", "read", "a")).allowed)

    def test_red_without_approval_refused(self):
        self.assertFalse(self.k.authorize(Action("money", "pay", "1")).allowed)

    def test_forged_approval_ids_refused(self):
        for a in (Action("pkg", "install", "x"), Action("money", "pay", "1")):
            self.assertFalse(self.k.authorize(a, approval_id="deadbeefdeadbeef").allowed)

    def test_pending_is_not_approved(self):
        a = Action("money", "pay", "1")
        d, aid = self.k.request(a)
        self.assertFalse(self.k.authorize(a, aid).allowed)

    def test_approved_single_use_and_replay_refused(self):
        a = Action("pkg", "install", "requests")
        d, aid = self.k.request(a)
        self.assertTrue(self.k.approve(aid))
        self.assertTrue(self.k.authorize(a, aid).allowed)
        self.assertFalse(self.k.authorize(a, aid).allowed)  # replay

    def test_approval_bound_to_exact_action(self):
        a, b = Action("pkg", "install", "requests"), Action("pkg", "install", "evil")
        d, aid = self.k.request(a)
        self.k.approve(aid)
        self.assertFalse(self.k.authorize(b, aid).allowed)

    def test_expired_approval_refused(self):
        a = Action("pkg", "install", "x")
        d, aid = self.k.request(a)
        self.k.approve(aid)
        self.k.db.execute("UPDATE approvals SET expires=0 WHERE id=?", (aid,))
        self.assertFalse(self.k.authorize(a, aid).allowed)

    def test_deny_never_gets_an_approval(self):
        a = Action("fs", "read", "D:/jarvis-secrets/keys.json")
        d, aid = self.k.request(a)
        self.assertIsNone(aid)
        self.assertFalse(self.k.authorize(a).allowed)

    def test_low_level_approval_cannot_cover_red(self):
        low, red = Action("pkg", "install", "x"), Action("money", "pay", "1")
        d, aid = self.k.request(low)
        self.k.approve(aid)
        self.k.db.execute("UPDATE approvals SET action_hash=? WHERE id=?", (red.digest(), aid))
        self.assertFalse(self.k.authorize(red, aid).allowed)

    def test_cli_approve_needs_terminal(self):
        a = Action("pkg", "install", "x")
        d, aid = self.k.request(a)
        headless = {"creationflags": 0x00000008} if os.name == "nt" else {"start_new_session": True}  # no console / no tty
        r = subprocess.run(
            [getattr(sys, "_base_executable", sys.executable), "-m", "jarvis_kernel", "--data", self.tmp.name, "approve", aid],
            cwd=os.path.join(os.path.dirname(__file__), ".."), input=aid + "\n", capture_output=True, text=True,
            timeout=30, **headless,
        )
        self.assertEqual(r.returncode, 3)
        self.assertFalse(self.k.authorize(a, aid).allowed)


class TestAudit(Base):
    def fill(self, n=8):
        for i in range(n):
            self.k.audit.append("t", {"i": i})

    def test_clean_log_verifies(self):
        self.fill()
        self.assertTrue(self.k.audit.verify(self.k.audit.anchor())[0])

    def test_edited_line_detected(self):
        self.fill()
        self.k.db.execute("UPDATE audit SET payload='{\"i\": 99}' WHERE seq=3")
        self.assertFalse(self.k.audit.verify()[0])

    def test_deleted_middle_row_detected(self):
        self.fill()
        self.k.db.execute("DELETE FROM audit WHERE seq=4")
        self.assertFalse(self.k.audit.verify()[0])

    def test_tail_truncation_needs_anchor_and_is_detected(self):
        self.fill()
        anchor = self.k.audit.anchor()
        self.k.db.execute("DELETE FROM audit WHERE seq>5")
        self.assertTrue(self.k.audit.verify()[0])  # chain alone cannot see it
        ok, msg = self.k.audit.verify(anchor)
        self.assertFalse(ok)
        self.assertIn("truncated", msg)

    def test_rewrite_from_genesis_detected_by_anchor(self):
        self.fill()
        anchor = self.k.audit.anchor()
        rows = self.k.db.execute("SELECT seq,ts,kind,payload FROM audit ORDER BY seq").fetchall()
        prev = GENESIS
        for seq, ts, kind, payload in rows:
            payload = '{"i": 0}' if seq == 2 else payload  # forge one event, re-chain everything
            h = _h(prev, ts, kind, payload)
            self.k.db.execute("UPDATE audit SET payload=?, prev=?, hash=? WHERE seq=?", (payload, prev, h, seq))
            prev = h
        self.assertTrue(self.k.audit.verify()[0])  # internally consistent
        ok, msg = self.k.audit.verify(anchor)
        self.assertFalse(ok)
        self.assertIn("rewritten", msg)


class TestKill(Base):
    def test_kill_blocks_everything_and_resume_restores(self):
        a = Action("fs", "read", "a")
        self.assertTrue(self.k.authorize(a).allowed)
        self.k.kill("test")
        with self.assertRaises(Killed):
            self.k.authorize(a)
        self.k.resume()
        self.assertTrue(self.k.authorize(a).allowed)

    def test_no_new_approvals_while_killed(self):
        self.k.kill("test")
        d, aid = self.k.request(Action("pkg", "install", "x"))
        self.assertIsNone(aid)


    def test_a_damaged_approval_row_does_not_hide_the_others(self):
        # bench N-015: one approval row with broken JSON made pending_approvals() (and the owner's state page) fail
        _, good = self.k.request(Action("fs", "write", "notes/ok.md"))
        _, bad = self.k.request(Action("fs", "write", "notes/bad.md"))
        self.k.db.execute("UPDATE approvals SET action='{half' WHERE id=?", (bad,))
        ids = {p["id"]: p for p in self.k.pending_approvals()}
        self.assertIn(good, ids)
        self.assertEqual(ids[bad]["action"], {"unreadable": True})
        self.assertEqual(self.k.approval_info(bad)["action"], {"unreadable": True})
        self.k.deny(bad)
        self.assertTrue(any(h["id"] == bad for h in self.k.approval_history()))


if __name__ == "__main__":
    unittest.main()
