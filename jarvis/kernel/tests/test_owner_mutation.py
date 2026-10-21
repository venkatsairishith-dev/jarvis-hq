"""Regression tests added after mutation testing of owner.py (2026-10-19): PIN hashing, the PIN lockout and its persistence,
the refusals in Owner.approve (each with its own status code, independent of the kernel's own second check) and the helper
that withdraws a cancelled task's approvals."""
import json
import os
import sqlite3
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from jarvis_kernel.jobs import Jobs
from jarvis_kernel.owner import Owner, OwnerError, PIN_TRIES, PIN_WINDOW, _check_pin, _hash_pin, _withdraw_task_approvals
from jarvis_kernel.policy import Action
from jarvis_kernel.store import Kernel
from jarvis_kernel.tasks import Tasks

PIN = "correct horse battery"


class Rig(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.t = [1_000_000.0]
        self.k = Kernel(self.tmp.name)
        self.o = self.owner()
        self.o.set_pin(PIN)

    def owner(self):
        return Owner(self.k, Tasks(self.k), Jobs(self.k), clock=lambda: self.t[0])

    def tearDown(self):
        self.k.db.close()
        self.tmp.cleanup()

    def pending(self, target="requests"):
        d, aid = self.k.request(Action("pkg", "install", target))
        self.assertEqual(d.name, "YELLOW_APPROVAL")
        return aid

    def code(self, fn, *a):
        with self.assertRaises(OwnerError) as cm:
            fn(*a)
        return cm.exception.code, str(cm.exception)

    def audit(self, kind):
        return [json.loads(p) for (p,) in self.k.db.execute("SELECT payload FROM audit WHERE kind=? ORDER BY seq", (kind,)).fetchall()]


class PinHash(unittest.TestCase):
    def test_hash_format_is_strong_and_salted(self):
        a, b = _hash_pin("secret-pin"), _hash_pin("secret-pin")
        kind, iters, salt, h = a.split("$")
        self.assertEqual((kind, iters), ("pbkdf2", "200000"))
        self.assertEqual(len(salt), 32)                       # 16 random bytes
        self.assertEqual(len(h), 64)
        self.assertNotEqual(a, b)                             # a fresh salt every time

    def test_check_pin(self):
        stored = _hash_pin("secret-pin", iters=1000)
        self.assertTrue(_check_pin("secret-pin", stored))
        self.assertFalse(_check_pin("secret-pio", stored))
        self.assertFalse(_check_pin("", stored))
        for bad in ("", "garbage", "pbkdf2$x$y$z", "a$1$zz$00", None):
            self.assertFalse(_check_pin("secret-pin", bad))


class Pin(Rig):
    def test_set_pin_length_limits_and_audit(self):
        for bad in ("12345", "x" * 65, ""):
            self.assertEqual(self.code(self.o.set_pin, bad)[0], 400)
        self.o.set_pin("123456")
        self.o.set_pin("y" * 64)
        self.assertEqual(len(self.audit("owner_pin_set")), 3)             # the setUp one and these two
        self.assertTrue(self.o.pin_set())

    def test_lockout_survives_a_restart_and_fails_closed_on_a_damaged_record(self):
        aid = self.pending()
        for _ in range(PIN_TRIES):
            self.assertEqual(self.code(self.o.approve, aid, aid, "wrong wrong")[0], 403)
        self.assertEqual(len(json.loads(self.k.db.execute("SELECT value FROM owner_config WHERE key='pin_failures'").fetchone()[0])), PIN_TRIES)
        again = self.owner()                                              # the kernel restarted
        self.assertEqual(len(again.bad_pins), PIN_TRIES)
        self.assertEqual(self.code(again.approve, aid, aid, PIN)[0], 429)   # even the right PIN rests
        self.t[0] += PIN_WINDOW + 1                                       # the window passes
        self.assertEqual(again.approve(aid, aid, PIN), {"approved": aid})
        # a record that cannot be read counts as a full lockout for one window
        self.k.db.execute("INSERT OR REPLACE INTO owner_config(key, value) VALUES ('pin_failures', 'not json')")
        damaged = self.owner()
        self.assertEqual(len(damaged.bad_pins), PIN_TRIES)
        self.assertEqual(self.code(damaged.approve, "x", "x", PIN)[0], 429)

    def test_a_missing_pin_is_not_counted_as_a_wrong_one(self):
        self.k.db.execute("DELETE FROM owner_config WHERE key='approval_pin'")
        aid = self.pending()
        for _ in range(PIN_TRIES + 2):
            self.assertEqual(self.code(self.o.approve, aid, aid, "anything")[0], 403)
        self.assertEqual(self.o.bad_pins, [])
        self.assertEqual(self.audit("owner_approve_bad_pin"), [])

    def test_wrong_pin_is_audited_once_per_try(self):
        aid = self.pending()
        self.code(self.o.approve, aid, aid, "nope nope")
        self.assertEqual(self.audit("owner_approve_bad_pin"), [{"id": aid}])


class ApproveRefusals(Rig):
    def test_confirmation_must_repeat_the_id(self):
        aid = self.pending()
        for confirm in ("", "other", aid[:-1], None):
            self.assertEqual(self.code(self.o.approve, aid, confirm, PIN)[0], 400)
        self.assertEqual(self.k.approval_info(aid)["state"], "pending")
        self.assertEqual(self.o.bad_pins, [])                              # refused before the PIN was looked at

    def test_kill_switch_pauses_approving_with_409(self):
        aid = self.pending()
        self.k.kill("t")
        code, msg = self.code(self.o.approve, aid, aid, PIN)
        self.assertEqual(code, 409)
        self.assertIn("kill switch", msg)
        self.assertEqual(self.k.approval_info(aid)["state"], "pending")
        self.assertEqual(self.o.bad_pins, [])

    def test_red_action_is_console_only_and_audited(self):
        d, aid = self.k.request(Action("publish", "post", "blog"))
        self.assertEqual(d.name, "RED")
        code, msg = self.code(self.o.approve, aid, aid, PIN)
        self.assertEqual(code, 403)
        self.assertIn("console", msg)
        self.assertEqual(self.audit("owner_approve_refused"), [{"id": aid, "level": "RED"}])
        self.assertEqual(self.k.approval_info(aid)["state"], "pending")

    def test_card_with_invisible_text_is_409_and_stays_pending(self):
        aid = self.pending("re​quests")
        self.assertTrue(self.k.approval_info(aid)["blocked"])
        code, msg = self.code(self.o.approve, aid, aid, PIN)
        self.assertEqual(code, 409)
        self.assertIn("invisible", msg)
        self.assertEqual(self.k.approval_info(aid)["state"], "pending")

    def test_cut_off_card_is_409_and_stays_pending(self):
        aid = self.pending()
        self.k.db.execute("UPDATE approvals SET action=? WHERE id=?", (json.dumps({"target": "abc...[truncated]"}), aid))
        code, msg = self.code(self.o.approve, aid, aid, PIN)
        self.assertEqual(code, 409)
        self.assertIn("cut off", msg)
        self.assertEqual(self.k.approval_info(aid)["state"], "pending")

    def test_unknown_or_used_approval_is_404_and_good_one_is_approved_and_audited(self):
        self.assertEqual(self.code(self.o.approve, "nosuchid", "nosuchid", PIN)[0], 404)
        aid = self.pending()
        self.assertEqual(self.o.approve(aid, aid, PIN), {"approved": aid})
        self.assertEqual(self.audit("owner_approved_http"), [{"id": aid, "ok": True}])
        self.assertEqual(self.code(self.o.approve, aid, aid, PIN)[0], 404)


class FakeDb:
    def __init__(self, exc):
        self.exc = exc

    def execute(self, *a, **k):
        raise self.exc


class FakeKernel:
    def __init__(self, db=None):
        self.db = db
        self.rows = []

    class _A:
        def __init__(self, k): self.k = k
        def append(self, kind, payload): self.k.rows.append((kind, payload))


class Withdraw(Rig):
    def test_missing_table_means_nothing_to_withdraw_but_other_errors_are_raised(self):
        k = FakeKernel(FakeDb(sqlite3.OperationalError("no such table: lab_share_pending")))
        self.assertEqual(_withdraw_task_approvals(k, "t1"), {"approvals_withdrawn": [], "approval_failures": []})
        k = FakeKernel(FakeDb(sqlite3.OperationalError("database is locked")))
        with self.assertRaises(sqlite3.OperationalError):
            _withdraw_task_approvals(k, "t1")

    def test_one_failure_is_audited_and_the_others_are_still_withdrawn(self):
        self.k.db.execute("CREATE TABLE IF NOT EXISTS lab_share_pending(task TEXT, approval_id TEXT)")
        ids = [self.pending(f"pkg{i}") for i in range(3)]
        for a in ids:
            self.k.db.execute("INSERT INTO lab_share_pending(task, approval_id) VALUES ('t1', ?)", (a,))
        other = self.pending("other")
        self.k.db.execute("INSERT INTO lab_share_pending(task, approval_id) VALUES ('t2', ?)", (other,))
        real = self.k.withdraw

        def flaky(aid, why):
            if aid == ids[1]:
                raise RuntimeError("boom")
            return real(aid, why)
        self.k.withdraw = flaky
        res = _withdraw_task_approvals(self.k, "t1")
        self.assertEqual(sorted(res["approvals_withdrawn"]), sorted([ids[0], ids[2]]))
        self.assertEqual(res["approval_failures"], [ids[1]])
        self.assertEqual(self.audit("approval_withdraw_failed"), [{"id": ids[1], "task": "t1", "type": "RuntimeError"}])
        self.assertEqual(self.k.approval_info(ids[0])["state"], "denied")
        self.assertEqual(self.k.approval_info(ids[1])["state"], "pending")
        self.assertEqual(self.k.approval_info(other)["state"], "pending")      # another task's approval is never touched
        self.assertEqual(self.o._deny_task_approvals("t2"), [other])


if __name__ == "__main__":
    unittest.main()
