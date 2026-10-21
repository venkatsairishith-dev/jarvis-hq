"""Regression tests added after mutation testing of retention.py (2026-10-19). Direct, deterministic checks of the purge helpers and
counters that the end-to-end canary tests in test_retention.py did not pin."""
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from jarvis_kernel import retention as R
from jarvis_kernel.jobs import Jobs
from jarvis_kernel.store import Kernel
from jarvis_kernel.tasks import Tasks

DAY = 86400
CAN = "CANARY-7f3a-Priyanka-SYNTH"


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.k = Kernel(self.tmp.name)
        Jobs(self.k), Tasks(self.k)
        self.r = R.Retention(self.k, clock=lambda: 10_000_000.0)
        self.now = 10_000_000.0
        self.cfg = R.settings(self.k.db)

    def tearDown(self):
        self.k.db.close()
        self.tmp.cleanup()

    def old_approval(self, aid, reason, state="denied", card=None):
        self.k.db.execute("INSERT INTO approvals(id, action_hash, level, expires, state, action, reason, created, decided) VALUES (?,?,?,?,?,?,?,?,?)",
                          (aid, "ab" * 32, 1, self.now - 5 * DAY, state, json.dumps(card or {"tool": "pkg", "op": "install", "lane": "private",
                           "target": CAN + "-" + aid, "params": {"x": CAN * 2}}), reason, self.now - 6 * DAY, self.now - 5 * DAY))

    def stored(self, aid):
        return self.k.db.execute("SELECT action, reason FROM approvals WHERE id=?", (aid,)).fetchone()


class Helpers(Base):
    def test_text_is_never_wrapped_twice_and_empty_stays_empty(self):
        once = self.r._text(CAN * 3, "after use")
        self.assertTrue(once.startswith(R.MARK))
        self.assertNotIn(CAN, once)
        self.assertEqual(self.r._text(once, "later"), once)
        self.assertEqual(self.r._text("", "x"), "")
        self.assertEqual(self.r._text(None, "x"), "")

    def test_keep_scalars_cuts_key_names_at_40_and_stubs_text(self):
        out = self.r._keep_scalars({"k" * 60: 7, "t": CAN * 3, "ok": True, "id": "0123456789ab"})
        self.assertIn("k" * 40, out)
        self.assertNotIn("k" * 41, out)
        self.assertEqual(out["id"], "0123456789ab")
        self.assertEqual(out["t"]["_chars"], len(CAN * 3))
        self.assertTrue(out["_purged"])
        self.assertNotIn(CAN, json.dumps(out))


class Approvals(Base):
    def test_unsafe_reason_is_replaced_and_safe_reason_is_kept(self):
        self.old_approval("a1", "touches a protected resource of host evil.example")
        self.old_approval("a2", "rule fs.write")
        self.old_approval("a3", "no rule: fail closed")
        self.old_approval("a4", "withdrawn: task cancelled")
        self.assertEqual(self.r.approvals(self.now, self.cfg), 4)
        self.assertNotIn("evil.example", self.stored("a1")[1])
        self.assertTrue(self.stored("a1")[1].startswith(R.MARK))
        self.assertEqual(self.stored("a2")[1], "rule fs.write")
        self.assertEqual(self.stored("a3")[1], "no rule: fail closed")
        self.assertEqual(self.stored("a4")[1], "withdrawn: task cancelled")
        for a in ("a1", "a2", "a3", "a4"):
            self.assertNotIn(CAN, self.stored(a)[0])

    def test_reasons_pass_counts_every_stub_it_changes(self):
        for i in range(3):
            self.old_approval(f"b{i}", "x", card={"purged": True, "tool": "pkg"})
            self.k.db.execute("UPDATE approvals SET reason=? WHERE id=?", (f"quotes host h{i}.example", f"b{i}"))
        self.old_approval("b9", "rule fs.read", card={"purged": True})
        self.assertEqual(self.r.reasons(self.now, self.cfg), 3)
        for i in range(3):
            self.assertNotIn("example", self.stored(f"b{i}")[1])
        self.assertEqual(self.stored("b9")[1], "rule fs.read")
        self.assertEqual(self.r.reasons(self.now, self.cfg), 0)

    def test_a_backlog_larger_than_one_batch_is_purged_in_one_sweep(self):
        total = R.BATCH + 150
        with self.k.db.tx():
            for i in range(total):
                self.old_approval(f"c{i}", "rule fs.read")
        out = self.r.sweep("test", now=self.now)
        self.assertEqual(out["approvals"], total)
        left = self.k.db.execute("SELECT COUNT(*) FROM approvals WHERE action NOT LIKE '%\"purged\": true%'").fetchone()[0]
        self.assertEqual(left, 0)


class JobsAndTasks(Base):
    def job(self, jid, result):
        self.k.db.execute("INSERT INTO jobs(id, node, kind, params, state, created, expires, result, finished) VALUES (?,?,?,?,?,?,?,?,?)",
                          (jid, "n", "k", json.dumps({"text": CAN * 2, "n": 3}), "done", self.now - 40 * DAY, self.now - 40 * DAY,
                           result, self.now - 40 * DAY))

    def test_job_result_keeps_only_a_boolean_ok(self):
        cases = {"j1": (json.dumps({"ok": True, "text": CAN}), True), "j2": (json.dumps({"ok": "yes", "text": CAN}), None),
                 "j3": (json.dumps([CAN]), None), "j4": (json.dumps({"text": CAN}), None), "j5": ("not json " + CAN * 2, None),
                 "j6": (json.dumps({"ok": False}), False)}
        for jid, (res, _) in cases.items():
            self.job(jid, res)
        self.assertEqual(self.r.jobs(self.now, self.cfg), len(cases))
        for jid, (_, ok) in cases.items():
            params, result = self.k.db.execute("SELECT params, result FROM jobs WHERE id=?", (jid,)).fetchone()
            self.assertNotIn(CAN, params + result)
            got = json.loads(result)
            self.assertTrue(got["_purged"])
            if ok is None:
                self.assertNotIn("ok", got, jid)
            else:
                self.assertIs(got["ok"], ok)

    def test_task_verification_keeps_numbers_and_short_labels_only(self):
        ver = {"verifier": "separate-user", "assurance": "A" * 100, "outcome": "x" * 79, "method": "m" * 80, "passed": True, "score": 3,
               "note": CAN, "evidence": [CAN]}
        self.k.db.execute("INSERT INTO tasks(id, created, updated, owner, lane, source, request, goal, plan, criteria, state, step, attempts,"
                          " max_attempts, deadline, result, evidence, verification, parent) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                          ("t1", self.now - 90 * DAY, self.now - 90 * DAY, "o", "private", "s", CAN, CAN + "goal", "[]", "[]", "completed",
                           CAN, 1, 3, None, CAN, "[]", json.dumps(ver), None))
        self.assertEqual(self.r.tasks(self.now, self.cfg), 1)
        v = json.loads(self.k.db.execute("SELECT verification FROM tasks WHERE id='t1'").fetchone()[0])
        self.assertEqual(v, {"verifier": "separate-user", "outcome": "x" * 79, "passed": True, "score": 3, "purged": True})
        row = " ".join(str(x) for x in self.k.db.execute("SELECT request, goal, step, result FROM tasks WHERE id='t1'").fetchone())
        self.assertNotIn(CAN, row)


if __name__ == "__main__":
    unittest.main()
