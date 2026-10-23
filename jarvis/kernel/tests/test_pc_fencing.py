"""Fencing for PC jobs (completion A, 2026-10-19): the kernel refuses a job request from a stale PC-lock holder, a request delivered
twice makes one job, and a cancel that reaches the node before the job begins is not lost (so the job does no side effect).
Real HTTP handler, real sqlite, real node runtime; no sleeps."""
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
from jarvis_kernel.llm_proxy import make_handler
from jarvis_kernel.store import Kernel

TOK_A, TOK_B = "a1" * 8, "b2" * 8


class Fence(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.k = Kernel(os.path.join(self.tmp.name, "d"))
        H, tokens = make_handler(self.k, [], owner_hermes=lambda t: "ok")
        self.plane = H.owner_plane
        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.url, self.tok = f"http://127.0.0.1:{self.srv.server_address[1]}", tokens.issue("console", "owner")
        self.lock = os.path.join(self.tmp.name, "pc_task.lock")
        self.plane.pc_lock_path = self.lock

    def tearDown(self):
        self.srv.shutdown()
        self.k.db.close()
        self.tmp.cleanup()

    def set_holder(self, token):
        with open(self.lock, "w", encoding="utf-8") as f:
            json.dump({"task": "t", "pid": 1, "token": token}, f)

    def pc(self, body):
        r = urllib.request.Request(self.url + "/v1/jarvis/owner/pc", data=json.dumps(body).encode(), method="POST",
                                   headers={"Authorization": "Bearer " + self.tok, "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(r, timeout=10) as resp:
                return resp.status, json.loads(resp.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read() or b"{}")

    def jobs(self):
        return self.k.db.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]

    def test_the_current_holder_gets_its_job(self):
        self.set_holder(TOK_A)
        code, r = self.pc({"op": "windows", "holder": {"token": TOK_A, "task": "t", "seq": 1}})
        self.assertEqual((code, r["status"]), (202, "queued"))

    def test_a_stale_executor_whose_lock_was_recovered_is_refused_and_no_job_exists(self):
        self.set_holder(TOK_B)                                    # the lock now belongs to a newer task
        code, r = self.pc({"op": "windows", "holder": {"token": TOK_A, "task": "old", "seq": 7}})
        self.assertEqual(code, 409)
        self.assertIn("no longer holds the PC lock", r["error"])
        self.assertEqual(self.jobs(), 0)

    def test_a_recovered_lock_with_no_file_refuses_every_holder(self):
        code, r = self.pc({"op": "windows", "holder": {"token": TOK_A, "task": "old", "seq": 1}})
        self.assertEqual((code, self.jobs()), (409, 0))

    def test_a_request_delivered_twice_makes_one_job(self):
        self.set_holder(TOK_A)
        h = {"token": TOK_A, "task": "t", "seq": 3}
        c1, r1 = self.pc({"op": "windows", "holder": h})
        c2, r2 = self.pc({"op": "windows", "holder": h})
        self.assertEqual((c1, c2), (202, 202))
        self.assertEqual(r1["job"], r2["job"])
        self.assertTrue(r2.get("duplicate"))
        self.assertEqual(self.jobs(), 1)
        c3, r3 = self.pc({"op": "windows", "holder": dict(h, seq=4)})        # the next request is a new job
        self.assertNotEqual(r3["job"], r1["job"])
        self.assertEqual(self.jobs(), 2)

    def test_a_malformed_holder_is_refused(self):
        for bad in ({"token": "x", "seq": 1}, {"token": TOK_A}, {"token": TOK_A, "seq": True}, "oops", {"token": TOK_A, "seq": -1}):
            self.assertEqual(self.pc({"op": "windows", "holder": bad})[0], 400, bad)
        self.assertEqual(self.jobs(), 0)

    def test_a_request_without_a_holder_is_unchanged(self):
        self.assertEqual(self.pc({"op": "windows"})[0], 202)

    def test_without_a_lock_path_only_the_duplicate_rule_applies(self):
        self.plane.pc_lock_path = None
        for k in ("JARVIS_PC_LOCK", "JARVIS_DATA_DIR"):
            os.environ.pop(k, None)
        self.assertEqual(self.pc({"op": "windows", "holder": {"token": TOK_A, "task": "t", "seq": 1}})[0], 202)


class ResultDelivery(Fence):
    """The node may deliver a job's result twice (a retried POST), or a result after the job was already ended another way."""

    def run_one(self):
        self.set_holder(TOK_A)
        code, r = self.pc({"op": "windows", "holder": {"token": TOK_A, "task": "t", "seq": 1}})
        self.assertEqual(code, 202)
        job = self.plane.jobs.next_for(self.plane.node)
        self.assertEqual(job["id"], r["job"])
        return r["job"]

    def state(self, jid):
        return self.k.db.execute("SELECT state, result FROM jobs WHERE id=?", (jid,)).fetchone()

    def test_a_result_delivered_twice_is_recorded_once_and_the_first_stands(self):
        jid = self.run_one()
        self.assertTrue(self.plane.jobs.finish(self.plane.node, jid, {"ok": True, "first": 1}))
        self.assertFalse(self.plane.jobs.finish(self.plane.node, jid, {"ok": False, "second": 1}))
        st, res = self.state(jid)
        self.assertEqual(st, "done")
        self.assertIn('"first"', res)
        self.assertNotIn("second", res)

    def test_a_late_done_cannot_overwrite_a_cancelled_job(self):
        jid = self.run_one()
        self.assertEqual(self.plane.cancel_job(jid)["state"], "cancelling")          # a request, not a completion
        self.assertEqual(self.state(jid)[0], "running", "cancel REQUEST was reported as a cancel COMPLETION")
        self.assertTrue(self.plane.jobs.finish(self.plane.node, jid, {"ok": False, "cancelled": True}))
        self.assertFalse(self.plane.jobs.finish(self.plane.node, jid, {"ok": True}))
        self.assertEqual(self.state(jid)[0], "cancelled")

    def test_a_job_cancelled_while_queued_never_runs(self):
        self.set_holder(TOK_A)
        _, r = self.pc({"op": "windows", "holder": {"token": TOK_A, "task": "t", "seq": 1}})
        self.assertEqual(self.plane.cancel_job(r["job"])["state"], "cancelled")
        self.assertIsNone(self.plane.jobs.next_for(self.plane.node))


class EarlyCancel(unittest.TestCase):
    def setUp(self):
        from jarvis_node import runtime
        self.rt = runtime
        runtime.end()
        runtime._early_cancels.clear()

    def test_a_cancel_before_the_job_begins_is_kept_and_the_job_does_no_side_effect(self):
        from jarvis_node import jobs
        self.assertFalse(self.rt.cancel("j9"))                  # nothing runs yet: not "stopped", but remembered
        ran = []
        orig = jobs._run_job
        jobs._run_job = lambda job, projects=None: ran.append(job) or {"ok": True}
        try:
            out = jobs.run_job({"id": "j9", "kind": "ui_windows", "params": {}})
        finally:
            jobs._run_job = orig
        self.assertEqual(ran, [], "the handler ran although the job was cancelled before it began")
        self.assertTrue(out.get("cancelled"))
        self.assertIsNone(self.rt.current())

    def test_a_cancel_for_another_job_does_not_cancel_this_one(self):
        from jarvis_node import jobs
        self.rt.cancel("other")
        orig = jobs._run_job
        jobs._run_job = lambda job, projects=None: {"ok": True}
        try:
            out = jobs.run_job({"id": "j10", "kind": "ui_windows", "params": {}})
        finally:
            jobs._run_job = orig
        self.assertTrue(out["ok"])

    def test_a_cancel_between_two_side_effects_stops_the_second(self):
        from jarvis_node import repair
        d = tempfile.mkdtemp()
        f = os.path.join(d, "a.txt")
        open(f, "w").write("one")
        repair.BACKUPS = os.path.join(d, "bk")
        repair.writable_root_ok = lambda p: True
        self.rt.begin("j11")
        orig = repair.backup

        def backup_then_cancel(path, why):
            bid = orig(path, why)
            self.rt.cancel("j11")                               # the owner's Stop arrives after the backup
            return bid
        repair.backup = backup_then_cancel
        try:
            with self.assertRaises(self.rt.Cancelled):
                repair.edit_file(f, "one", "two")
        finally:
            repair.backup = orig
            self.rt.end()
        self.assertEqual(open(f).read(), "one", "the file was changed after the cancel")


if __name__ == "__main__":
    unittest.main()
