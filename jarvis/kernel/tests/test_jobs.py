import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import winclean  # noqa: E402  retrying temp cleanup for Windows file locks
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

HERE = os.path.dirname(__file__)
sys.path.insert(0, os.path.join(HERE, ".."))
sys.path.insert(0, os.path.join(HERE, "..", "..", "node"))
from jarvis_kernel.analyze import _NUM, _flatten
from jarvis_kernel.jobs import JOB_TTL, JobError, Jobs, report_text
from jarvis_kernel.nodes import Nodes, make_handler
from jarvis_kernel.store import Killed, Kernel
from jarvis_kernel.telegram import Bridge
from jarvis_node import jobs as NJ

OWNER = 424242
GOOD = {"ok": True, "git": {"branch": "main", "dirty_files": 2, "ahead": 1, "behind": 0, "last_commits": ["abc1234 add feature", "def5678 fix bug"]},
        "tests": {"ran": True, "exit_code": 0, "seconds": 41, "summary": "Ran 182 tests in 40.2s. OK", "tail": []}}


class FakeAPI:
    def __init__(self): self.sent = []
    def send(self, chat_id, text): self.sent.append(text)
    def get_updates(self, offset, wait=25): return []


def msg(text):
    return {"update_id": 1, "message": {"text": text, "chat": {"id": OWNER, "type": "private"}, "from": {"id": OWNER}}}


class Queue(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.k = Kernel(os.path.join(self.tmp.name, "d")); self.j = Jobs(self.k)

    def tearDown(self): self.k.db.close(); winclean.cleanup(self.tmp)

    def test_create_validates_kind_and_parameters(self):
        for kind, params in (("rm_rf", {"project": "x"}), ("project_check", {}), ("project_check", {"project": "x", "cmd": "calc"}), ("project_check", {"project": "../x"}),
                             ("project_check", {"project": "a b"}), ("project_check", {"project": "x" * 41}), ("project_check", "x"), ("project_check", {"project": "x;calc"})):
            with self.assertRaises(JobError, msg=(kind, params)): self.j.create("laptop", kind, params)

    def test_kill_switch_blocks_new_jobs(self):
        self.k.kill("t")
        with self.assertRaises(Killed): self.j.create("laptop", "project_check", {"project": "jarvis-hq"})

    def test_job_is_handed_out_once_and_only_to_its_node(self):
        jid = self.j.create("laptop", "project_check", {"project": "jarvis-hq"})
        self.assertIsNone(self.j.next_for("other-node"))
        job = self.j.next_for("laptop")
        self.assertEqual((job["id"], job["kind"], job["params"]), (jid, "project_check", {"project": "jarvis-hq"}))
        self.assertIsNone(self.j.next_for("laptop"))

    def test_result_accepted_once_from_the_right_node_only(self):
        jid = self.j.create("laptop", "project_check", {"project": "jarvis-hq"}); self.j.next_for("laptop")
        self.assertFalse(self.j.finish("other-node", jid, GOOD))
        self.assertTrue(self.j.finish("laptop", jid, GOOD))
        self.assertFalse(self.j.finish("laptop", jid, {"ok": False}))          # no second answer
        self.assertFalse(self.j.finish("laptop", "nonexistent", GOOD))

    def test_a_node_that_says_it_is_idle_fails_the_job_it_no_longer_has(self):
        jid = self.j.create("laptop", "project_check", {"project": "jarvis-hq"}); self.j.next_for("laptop")
        now = time.time()
        self.assertEqual(self.j.fail_orphans("laptop", now + 5), 0)              # just handed over: within the grace period
        self.assertEqual(self.j.fail_orphans("other-node", now + 120), 0)        # another node's idle report says nothing about this job
        self.assertEqual(self.j.fail_orphans("laptop", now + 120), 1)
        rep = self.j.to_report()
        self.assertEqual([(r[0], r[2]) for r in rep], [(jid, "failed")])
        self.assertIn("restarted or lost", json.dumps(rep[0][4]))
        self.assertFalse(self.j.finish("laptop", jid, GOOD))                     # a late result for it is not accepted as a success

    def test_no_job_is_handed_out_while_the_kill_switch_is_on(self):
        # regression (bench D-075): the lab runner's /runner/next called next_for without checking the kill switch
        jid = self.j.create("lab", "project_check", {"project": "jarvis-hq"})
        self.k.kill("t")
        self.assertIsNone(self.j.next_for("lab"))
        self.assertEqual(self.k.db.execute("SELECT state FROM jobs WHERE id=?", (jid,)).fetchone()[0], "queued")
        self.k.resume()
        self.assertEqual(self.j.next_for("lab")["id"], jid)

    def test_only_a_real_true_ok_is_recorded_as_done(self):
        # regression (bench N-062): {"ok": "false"} was truthy and recorded as done
        for res in ({"ok": "false"}, {"ok": 1}, {"ok": "yes"}):
            jid = self.j.create("laptop", "project_check", {"project": "jarvis-hq"}); self.j.next_for("laptop")
            self.assertTrue(self.j.finish("laptop", jid, res))
            self.assertEqual(self.k.db.execute("SELECT state FROM jobs WHERE id=?", (jid,)).fetchone()[0], "failed", res)

    def test_unstarted_job_cannot_be_finished(self):
        jid = self.j.create("laptop", "project_check", {"project": "jarvis-hq"})
        self.assertFalse(self.j.finish("laptop", jid, GOOD))                    # never handed out

    def test_expiry_and_reporting_once(self):
        jid = self.j.create("laptop", "project_check", {"project": "jarvis-hq"})
        self.k.db.execute("UPDATE jobs SET expires=? WHERE id=?", (time.time() - 1, jid))
        self.assertIsNone(self.j.next_for("laptop"))                            # expired jobs are not handed out
        rep = self.j.to_report(); self.assertEqual([(r[0], r[2]) for r in rep], [(jid, "expired")])
        self.j.mark_notified(jid); self.assertEqual(self.j.to_report(), [])

    def test_a_job_that_never_reports_is_failed_and_reported(self):
        jid = self.j.create("laptop", "project_check", {"project": "jarvis-hq"}); self.j.next_for("laptop")
        self.assertEqual(self.j.to_report(), [])                                   # running normally: nothing to report yet
        self.k.db.execute("UPDATE jobs SET started=? WHERE id=?", (time.time() - 1801, jid))
        rep = self.j.to_report(); self.assertEqual([(r[0], r[2]) for r in rep], [(jid, "failed")]); self.assertIn("never reported", rep[0][4]["error"])
        self.assertFalse(self.j.finish("laptop", jid, GOOD))                       # a late answer after the failure is refused
        self.assertIn("job_stuck_failed", [r[0] for r in self.k.db.execute("SELECT kind FROM audit")])

    def test_old_databases_without_the_started_column_are_upgraded(self):
        import sqlite3
        db = sqlite3.connect(os.path.join(self.tmp.name, "old.db"))
        db.execute("CREATE TABLE jobs(id TEXT PRIMARY KEY, node TEXT, kind TEXT, params TEXT, state TEXT, created REAL, expires REAL, result TEXT, notified INT DEFAULT 0)")
        class K: pass
        k = K(); k.db = db
        Jobs(k)
        self.assertIn("started", [r[1] for r in db.execute("PRAGMA table_info(jobs)")]); db.close()

    def test_oversized_result_is_replaced(self):
        jid = self.j.create("laptop", "project_check", {"project": "jarvis-hq"}); self.j.next_for("laptop")
        self.j.finish("laptop", jid, {"ok": True, "blob": "x" * 70000})
        self.assertFalse(self.j.to_report()[0][4]["ok"])

    def test_report_numbers_come_from_the_result(self):
        text = report_text("jarvis-hq", GOOD); allowed = set(); _flatten(GOOD, allowed)
        self.assertEqual([m for m in _NUM.findall(text) if m not in allowed], [])
        self.assertIn("PASSED", text); self.assertIn("2 changed file(s)", text)
        bad = dict(GOOD, tests={"ran": True, "exit_code": 1, "seconds": 3, "summary": "FAILED (failures=2)", "tail": ["AssertionError: x"]})
        self.assertIn("FAILED", report_text("p", bad)); self.assertIn("AssertionError", report_text("p", bad))
        self.assertIn("failed to run", report_text("p", {"ok": False, "error": "git did not respond"}))


class Http(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.k = Kernel(os.path.join(self.tmp.name, "d"))
        H, self.nodes = make_handler(self.k)
        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), H); threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.srv.server_address[1]}"
        self.tok = self.nodes.pair(self.nodes.new_code(), "laptop")

    def tearDown(self): self.srv.shutdown(); self.k.db.close(); winclean.cleanup(self.tmp)

    def post(self, path, obj, tok=None):
        r = urllib.request.Request(self.url + path, data=json.dumps(obj).encode(), method="POST"); r.add_header("Authorization", "Bearer " + (tok or self.tok))
        return json.loads(urllib.request.urlopen(r, timeout=10).read())

    def test_endpoints_need_a_node_token(self):
        for path in ("/jobs/next", "/jobs/result"):
            with self.assertRaises(urllib.error.HTTPError) as cm: self.post(path, {}, tok="nope")
            self.assertEqual(cm.exception.code, 401)

    def test_full_round_trip_and_kill(self):
        jid = Jobs(self.k).create("laptop", "project_check", {"project": "jarvis-hq"})
        job = self.post("/jobs/next", {})["job"]; self.assertEqual(job["id"], jid)
        self.assertIsNone(self.post("/jobs/next", {})["job"])
        self.assertTrue(self.post("/jobs/result", {"id": jid, "result": GOOD})["accepted"])
        self.assertFalse(self.post("/jobs/result", {"id": jid, "result": GOOD})["accepted"])
        self.assertFalse(self.post("/jobs/result", {"id": 5, "result": "x"})["accepted"])
        Jobs(self.k).create("laptop", "project_check", {"project": "jarvis-hq"}); self.k.kill("t")
        r = self.post("/jobs/next", {}); self.assertEqual((r["job"], r.get("killed")), (None, True))


class Telegram(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.k = Kernel(os.path.join(self.tmp.name, "d")); self.api = FakeAPI()
        Jobs(self.k); self.b = Bridge(self.k, self.api, OWNER, [])

    def tearDown(self): self.k.db.close(); winclean.cleanup(self.tmp)

    def pair_laptop(self):
        n = Nodes(self.k); tok = n.pair(n.new_code(), "laptop")
        from test_nodes import STATE
        n.report(tok, STATE)

    def test_unknown_project_and_no_node(self):
        self.b.handle(msg("/check nonsense")); self.assertIn("Known projects: jarvis-hq", self.api.sent[-1])
        self.b.handle(msg("/check jarvis-hq")); self.assertIn("No laptop node", self.api.sent[-1])

    def test_check_queues_a_job_and_the_result_is_reported_once(self):
        self.pair_laptop()
        self.b.handle(msg("/check")); self.assertIn("Queued a check of jarvis-hq on laptop", self.api.sent[-1])
        self.assertEqual(self.k.db.execute("SELECT COUNT(*) FROM jobs WHERE state='queued'").fetchone()[0], 1)
        n = len(self.api.sent); self.b.flush_jobs(); self.assertEqual(len(self.api.sent), n)       # nothing finished yet
        job = Jobs(self.k).next_for("laptop"); Jobs(self.k).finish("laptop", job["id"], GOOD)
        self.b.flush_jobs(); self.assertIn("Tests PASSED", self.api.sent[-1]); self.assertIn("numbers not found in the result: none", self.api.sent[-1])
        n = len(self.api.sent); self.b.flush_jobs(); self.assertEqual(len(self.api.sent), n)       # reported exactly once

    def test_expired_job_is_reported(self):
        self.pair_laptop(); self.b.handle(msg("/check jarvis-hq"))
        self.k.db.execute("UPDATE jobs SET expires=?", (time.time() - 1,))
        self.b.flush_jobs(); self.assertIn("expired", self.api.sent[-1])

    def test_kill_switch_blocks_check(self):
        self.pair_laptop(); self.k.kill("t")
        self.b.handle(msg("/check jarvis-hq")); self.assertIn("Kill switch is on", self.api.sent[-1])
        self.assertEqual(self.k.db.execute("SELECT COUNT(*) FROM jobs").fetchone()[0], 0)

    def test_strangers_cannot_queue_jobs(self):
        self.pair_laptop()
        self.b.handle({"update_id": 2, "message": {"text": "/check jarvis-hq", "chat": {"id": 1, "type": "private"}, "from": {"id": 1}}})
        self.assertEqual(self.k.db.execute("SELECT COUNT(*) FROM jobs").fetchone()[0], 0)


def make_repo(root, test_body="import unittest\nclass T(unittest.TestCase):\n    def test_ok(self):\n        self.assertTrue(True)\n"):
    os.makedirs(os.path.join(root, "tests"))
    open(os.path.join(root, "tests", "test_a.py"), "w").write(test_body)
    g = lambda *a: subprocess.run(["git", "-C", root, "-c", "user.name=t", "-c", "user.email=t@t", *a], capture_output=True, text=True, check=True)
    g("init", "-q", "-b", "main"); g("add", "-A"); g("commit", "-q", "-m", "first commit"); return g


class NodeRunner(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.root = os.path.join(self.tmp.name, "proj")
        os.makedirs(self.root)

    def tearDown(self): winclean.cleanup(self.tmp)

    def projects(self, **test_over):
        t = {"cwd": ".", "cmd": ["{python}", "-B", "-W", "ignore", "-I", "-m", "unittest", "discover", "-s", "tests"], "timeout_s": 60}
        t.update(test_over)
        return {"demo": {"path": {os.name: self.root}, "test": t}}

    def test_passing_project(self):
        make_repo(self.root)
        r = NJ.project_check("demo", self.projects())
        self.assertTrue(r["ok"]); self.assertEqual((r["git"]["branch"], r["git"]["dirty_files"], r["git"]["ahead"], r["git"]["behind"]), ("main", 0, 0, 0))
        self.assertIn("first commit", r["git"]["last_commits"][0])
        self.assertEqual(r["tests"]["exit_code"], 0); self.assertIn("Ran 1 test", r["tests"]["summary"]); self.assertIn("OK", r["tests"]["summary"])

    def test_dirty_files_are_counted_and_nothing_is_changed(self):
        make_repo(self.root); open(os.path.join(self.root, "new.txt"), "w").write("x"); open(os.path.join(self.root, "tests", "test_a.py"), "a").write("\n# edit\n")
        before = subprocess.run(["git", "-C", self.root, "status", "--porcelain"], capture_output=True, text=True).stdout
        r = NJ.project_check("demo", self.projects())
        self.assertEqual(r["git"]["dirty_files"], 2); self.assertEqual(r["tests"]["files_changed_by_tests"], 0)     # no bytecode folders left behind
        self.assertEqual(subprocess.run(["git", "-C", self.root, "status", "--porcelain"], capture_output=True, text=True).stdout, before)

    def test_a_run_that_writes_files_is_reported_honestly(self):
        make_repo(self.root, "import unittest\nclass T(unittest.TestCase):\n    def test_w(self):\n        open(\"artifact.txt\", \"w\").write(\"x\")\n")
        r = NJ.project_check("demo", self.projects()); self.assertEqual(r["tests"]["files_changed_by_tests"], 1)
        text = report_text("demo", r); self.assertIn("added or changed 1 file(s)", text)
        clean = {"ok": True, "git": GOOD["git"], "tests": dict(GOOD["tests"], files_changed_by_tests=0)}
        self.assertIn("left the working tree unchanged", report_text("demo", clean)); self.assertNotIn("Nothing was changed", report_text("demo", clean))

    def test_failing_tests_report_the_tail(self):
        make_repo(self.root, "import unittest\nclass T(unittest.TestCase):\n    def test_bad(self):\n        self.assertEqual(1, 2)\n")
        t = NJ.project_check("demo", self.projects())["tests"]
        self.assertEqual(t["exit_code"], 1); self.assertIn("FAILED", t["summary"]); self.assertTrue(any("AssertionError" in ln or "FAILED" in ln for ln in t["tail"]))

    def test_unknown_unsafe_or_missing_projects_refused(self):
        make_repo(self.root); p = self.projects()
        for name in ("nope", "demo; calc", "../demo", "", "demo\n"):
            self.assertFalse(NJ.project_check(name, p)["ok"], name)
        p["demo"]["path"] = {os.name: os.path.join(self.tmp.name, "does-not-exist")}
        self.assertEqual(NJ.project_check("demo", p)["error"], "project folder not found on this machine")

    def test_not_a_git_repo(self):
        os.makedirs(os.path.join(self.root, "tests"))
        self.assertFalse(NJ.project_check("demo", self.projects())["ok"])

    def test_timeout_is_reported(self):
        make_repo(self.root, "import time, unittest\nclass T(unittest.TestCase):\n    def test_slow(self):\n        time.sleep(30)\n")
        t = NJ.project_check("demo", self.projects(timeout_s=2))["tests"]
        self.assertEqual((t["exit_code"], t["summary"]), (124, "timed out"))

    def test_secret_env_vars_are_not_given_to_project_code_and_output_is_scrubbed(self):
        body = ("import os, unittest\nclass T(unittest.TestCase):\n    def test_env(self):\n"
                "        print('SAW_SECRET=' + str('MY_API_TOKEN' in os.environ))\n        print('leak gho_abcdefghijklmnopqrstuvwxyz0123')\n        self.assertTrue(False)\n")
        make_repo(self.root, body); os.environ["MY_API_TOKEN"] = "should-not-be-visible"
        try:
            t = NJ.project_check("demo", self.projects())["tests"]
        finally:
            os.environ.pop("MY_API_TOKEN")
        joined = " ".join(t["tail"]) + t["summary"]
        self.assertNotIn("gho_abc", joined)
        out = subprocess.run([sys.executable, "-W", "ignore", "-I", "-m", "unittest", "discover", "-s", "tests"], cwd=self.root, capture_output=True, text=True,
                             env={k: v for k, v in os.environ.items() if "TOKEN" not in k}).stdout
        self.assertIn("SAW_SECRET=False", out)

    def test_run_job_refuses_unknown_kinds_and_bad_params(self):
        self.assertEqual(NJ.run_job({"kind": "shell", "params": {"cmd": "calc"}}, {})["error"], "unknown job kind")
        self.assertFalse(NJ.run_job({"kind": "project_check", "params": {"project": "x"}}, {})["ok"])


if __name__ == "__main__":
    unittest.main()
