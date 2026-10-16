import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from jarvis_kernel.analyze import build, unmatched_numbers
from jarvis_kernel.nodes import Nodes, make_handler, OFFLINE_AFTER
from jarvis_kernel.store import Kernel

STATE = {
    "collected_at": "2026-10-07T12:00:00", "collect_seconds": 4.4,
    "host": {"name": "X", "model": "MacBookPro16,1", "manufacturer": "Apple Inc."},
    "os": {"caption": "Microsoft Windows 10 Pro", "build": "19045", "uptime_hours": 7.5},
    "cpu": {"name": "Intel(R) Core(TM) i9-9980HK CPU @ 2.40GHz", "cores": 8, "threads": 16, "load_pct": 12},
    "ram": {"total_gb": 31.9, "free_gb": 19.4, "used_pct": 39.2},
    "gpu": [{"name": "AMD Radeon Pro 5500M", "vram_gb": 4.0, "driver": "30.0.13045.22003"}],
    "disks": [{"drive": "C:", "size_gb": 176.4, "free_gb": 20.0, "free_pct": 11.3}, {"drive": "D:", "size_gb": 75.0, "free_gb": 73.2, "free_pct": 97.6}],
    "processes_top": [{"name": "chrome", "mem_mb": 662}],
    "services": {"running": 118, "stopped_auto": ["svcA"]},
    "ports": [{"port": 22, "address": "0.0.0.0", "process": "sshd"}],
    "tools": {"git": {"present": True, "version": "git version 2.55.0"}, "ollama": {"present": False}},
    "env_paths": {"OLLAMA_MODELS": "D:/m", "HF_HOME": None},
}


class T(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.k = Kernel(self.tmp.name)
        self.n = Nodes(self.k)

    def tearDown(self):
        self.k.db.close(); self.tmp.cleanup()

    def test_pairing_single_use_and_bad_inputs(self):
        code = self.n.new_code()
        self.assertIsNone(self.n.pair("wrong", "laptop"))
        tok = self.n.pair(code, "laptop")
        self.assertTrue(tok)
        self.assertIsNone(self.n.pair(code, "other"))  # code is single use
        self.assertEqual(self.n.report(tok, STATE), "laptop")
        self.assertIsNone(self.n.report("bad-token", STATE))

    def test_one_pairing_code_raced_by_many_nodes_gives_one_token(self):
        # bench N-005: SELECT-then-UPDATE let several simultaneous pairings trade the same one-time code
        for trial in range(5):
            code = self.n.new_code()
            barrier, toks = threading.Barrier(10), []

            def go(i):
                barrier.wait()
                t = self.n.pair(code, f"n{trial}-{i}")
                if t:
                    toks.append(t)
            th = [threading.Thread(target=go, args=(i,)) for i in range(10)]
            [t.start() for t in th]; [t.join() for t in th]
            self.assertEqual(len(toks), 1)

    def test_a_bad_name_does_not_burn_the_code(self):
        code = self.n.new_code()
        self.assertIsNone(self.n.pair(code, ""))
        self.assertIsNone(self.n.pair(code, "x" * 65))
        self.assertTrue(self.n.pair(code, "laptop"))

    def test_expired_code_refused(self):
        code = self.n.new_code(ttl=-1)
        self.assertIsNone(self.n.pair(code, "laptop"))

    def test_offline_after_45s(self):
        tok = self.n.pair(self.n.new_code(), "laptop"); self.n.report(tok, STATE)
        self.assertTrue(self.n.list()[0]["online"])
        self.k.db.execute("UPDATE nodes SET last_seen=last_seen-?", (OFFLINE_AFTER + 1,))
        self.assertFalse(self.n.list()[0]["online"])

    def test_audit_has_summary_not_state(self):
        tok = self.n.pair(self.n.new_code(), "laptop"); self.n.report(tok, STATE)
        rows = " ".join(r[0] for r in self.k.db.execute("SELECT payload FROM audit"))
        self.assertNotIn("Intel i9", rows)
        self.assertNotIn(tok, rows)

    def test_report_numbers_all_come_from_state(self):
        tok = self.n.pair(self.n.new_code(), "laptop"); self.n.report(tok, STATE)
        node = self.n.list()[0]
        text = build(node)
        self.assertEqual(unmatched_numbers(text, node), [])
        self.assertIn("Disk C: is low", text)           # 11.3 percent free triggers the observation
        self.assertIn("Ollama is not installed", text)

    def test_unit_glued_and_dotted_numbers_match_whole(self):
        node = {"state": STATE, "seconds_since_report": 1.0}
        self.assertEqual(unmatched_numbers("CPU @ 2.40GHz, git 2.55.0, 8 cores", node), [])
        self.assertEqual(unmatched_numbers("clock 2 GHz", node), ["2"])      # a partial value is not accepted
        self.assertEqual(unmatched_numbers("version 2.55.1", node), ["2.55.1"])

    def test_smb_ports_noted(self):
        s = json.loads(json.dumps(STATE)); s["ports"].append({"port": 445, "address": "0.0.0.0", "process": "System"})
        node = {"name": "x", "online": True, "seconds_since_report": 1.0, "state": s}
        text = build(node)
        self.assertIn("SMB", text)
        self.assertEqual(unmatched_numbers(text, node), [])

    def test_invented_number_is_caught(self):
        tok = self.n.pair(self.n.new_code(), "laptop"); self.n.report(tok, STATE)
        node = self.n.list()[0]
        self.assertEqual(unmatched_numbers(build(node) + " Also 4242 errors.", node), ["4242"])

    def test_http_roundtrip_and_kill_flag(self):
        H, _ = make_handler(self.k)
        srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        url = f"http://127.0.0.1:{srv.server_address[1]}"
        def post(path, obj, tok=None):
            r = urllib.request.Request(url + path, data=json.dumps(obj).encode(), method="POST")
            if tok: r.add_header("Authorization", "Bearer " + tok)
            return json.loads(urllib.request.urlopen(r, timeout=10).read())
        tok = post("/pair", {"code": self.n.new_code(), "name": "laptop"})["token"]
        self.assertFalse(post("/report", {"state": STATE}, tok)["killed"])
        self.k.kill("test")
        self.assertTrue(post("/report", {"state": STATE}, tok)["killed"])
        with self.assertRaises(urllib.error.HTTPError) as cm:
            post("/report", {"state": STATE}, "nope")
        self.assertEqual(cm.exception.code, 401)
        with self.assertRaises(urllib.error.HTTPError) as cm:
            post("/pair", {"code": "x", "name": "y"})
        self.assertEqual(cm.exception.code, 403)
        srv.shutdown()

    def test_a_report_that_says_busy_false_fails_the_jobs_the_node_no_longer_holds(self):
        from jarvis_kernel.jobs import Jobs
        H, _ = make_handler(self.k)
        srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        url = f"http://127.0.0.1:{srv.server_address[1]}"
        def post(path, obj, tok=None):
            r = urllib.request.Request(url + path, data=json.dumps(obj).encode(), method="POST")
            if tok: r.add_header("Authorization", "Bearer " + tok)
            return json.loads(urllib.request.urlopen(r, timeout=10).read())
        try:
            tok = post("/pair", {"code": self.n.new_code(), "name": "laptop"})["token"]
            jobs = Jobs(self.k)
            jid = jobs.create("laptop", "project_check", {"project": "jarvis-hq"})
            self.assertEqual(post("/jobs/next", {}, tok)["job"]["id"], jid)
            self.k.db.execute("UPDATE jobs SET started=? WHERE id=?", (time.time() - 120, jid))      # handed over two minutes ago
            post("/report", {"state": STATE, "busy": True}, tok)                                   # still working: left alone
            self.assertEqual(self.k.db.execute("SELECT state FROM jobs WHERE id=?", (jid,)).fetchone()[0], "running")
            post("/report", {"state": STATE}, tok)                                                  # an older node that does not say: left alone
            self.assertEqual(self.k.db.execute("SELECT state FROM jobs WHERE id=?", (jid,)).fetchone()[0], "running")
            post("/report", {"state": STATE, "busy": False}, tok)                                  # idle: it cannot be running it
            self.assertEqual(self.k.db.execute("SELECT state FROM jobs WHERE id=?", (jid,)).fetchone()[0], "failed")
        finally:
            srv.shutdown()

    def test_serve_refuses_all_interfaces(self):
        r = subprocess.run([sys.executable, "-m", "jarvis_kernel", "--data", self.tmp.name, "serve", "--bind", "0.0.0.0"],
                           cwd=os.path.join(os.path.dirname(__file__), ".."), capture_output=True, text=True, timeout=30)
        self.assertEqual(r.returncode, 3)


if __name__ == "__main__":
    unittest.main()
