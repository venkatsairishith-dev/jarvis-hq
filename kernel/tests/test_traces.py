"""Task traces: assembled read-only from the kernel record, redacted, bounded; the analyzer reports and changes nothing."""
import json
import os
import sqlite3
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from jarvis_kernel import traces
from jarvis_kernel.jobs import Jobs
from jarvis_kernel.store import Kernel
from jarvis_kernel.tasks import Tasks

SECRET = "ghp_abcdefghijklmnopqrstuvwxyz0123456789"


class T(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.dir = os.path.join(self.tmp.name, "d")
        self.k = Kernel(self.dir); self.T = Tasks(self.k); J = Jobs(self.k); self.conns = []
        self.tid = self.T.create("hermes", "private", "build it", "Build a Python CLI with tests", criteria=["tests pass", "delivered"])
        a = self.k.audit.append
        a("agent_model_call_failed", {"agent": "hermes", "provider": "groq", "error": "HTTP 413", "class": "too_large"})
        a("agent_model_call", {"agent": "hermes", "provider": "openrouter-private", "prompt_chars": 35000, "bytes_out": 900, "ms": 2400})
        a("agent_action", {"agent": "hermes", "tool": "task", "op": "dispatch", "status": "queued"})
        job = J.create("lab", "lab_run", {"task": self.tid, "agent": "hermes", "instructions": "x", "criteria": ["tests pass"],
                                          "max_attempts": 2, "timeout_s": 600}, owner="hermes")
        J.next_for("lab")
        for _ in range(6):
            a("agent_model_call", {"agent": "lab", "provider": "dahl-minimax", "prompt_chars": 7000, "bytes_out": 500})
        a("agent_model_call_failed", {"agent": "lab", "provider": "dahl-glm", "error": "HTTP 502"})
        self.T.update(None, self.tid, state="running", attempt=True)
        self.T.update(None, self.tid, note="skills used (attempt 1): adversarial-ux-test, grill-me")
        self.T.update(None, self.tid, state="verifying")
        self.T.update(None, self.tid, state="repairing", note=f"tests fail; token {SECRET} in config.py")
        self.T.update(None, self.tid, state="running", attempt=True)
        self.T.update(None, self.tid, state="verifying")
        self.T.update(None, self.tid, state="completed", result="verified after 2 attempt(s)",
                      evidence=["delivered: D:\\jarvis\\outputs\\task-x\\tool.py (9 chars) job:abc"],
                      verification={"passed": True, "criteria": [{"criterion": "tests pass", "met": True, "evidence": f"ran it {SECRET}"}],
                                    "history": [{"attempt": 1, "passed": False}, {"attempt": 2, "passed": True}]})
        J.finish("lab", job, {"ok": True, "summary": "verified"})

    def ro(self):
        db = traces.open_readonly(self.dir); self.conns.append(db); return db

    def tearDown(self):
        for c in self.conns:
            c.close()
        self.k.db.close(); self.tmp.cleanup()

    def test_trace_is_complete_redacted_and_bounded(self):
        db = self.ro()
        t = traces.build(db, self.tid)
        raw = json.dumps(t)
        self.assertNotIn(SECRET, raw)                                                # scrubbed in notes and evidence
        self.assertLess(len(raw), traces.MAX_TRACE_BYTES)
        self.assertEqual((t["category"], t["outcome"]["state"], t["outcome"]["verified"], t["outcome"]["attempts"]), ("software", "completed", True, 2))
        self.assertEqual(len(t["repairs"]), 1); self.assertEqual(t["environment"]["lab_agent"], "hermes")
        calls = {(c["role"], c["provider"]): c["n"] for c in t["models"]["calls"]}
        self.assertEqual(calls[("lab worker/verifier", "dahl-minimax")], 6); self.assertEqual(calls[("planner", "openrouter-private")], 1)
        self.assertIn({"role": "planner", "provider": "groq", "error": "HTTP 413", "class": "too_large", "n": 1}, t["models"]["failures"])
        self.assertEqual(t["delivery_evidence"][0][:10], "delivered:")
        self.assertEqual(t["skills"], ["adversarial-ux-test", "grill-me"])
        self.assertEqual(t["models"]["latency_ms"]["openrouter-private"]["p50"], 2400)

    def test_public_mode_drops_all_free_text(self):
        t = traces.build(self.ro(), self.tid, public=True)
        raw = json.dumps(t)
        for word in ("Build a Python", "tests fail", "config.py", "outputs"):
            self.assertNotIn(word, raw)
        self.assertEqual(t["outcome"]["attempts"], 2)                                # structure stays

    def test_the_connection_cannot_write(self):
        db = self.ro()
        with self.assertRaises(sqlite3.OperationalError):
            db.execute("DELETE FROM tasks")

    def test_analyzer_finds_recurring_failures_and_changes_nothing(self):
        before = self.k.db.execute("SELECT COUNT(*) FROM audit").fetchone()[0]
        for _ in range(6):
            self.k.audit.append("agent_model_call_failed", {"agent": "hermes", "provider": "groq", "error": "HTTP 413", "class": "too_large"})
        self.k.db.execute("UPDATE tasks SET updated=updated+60")                     # the failures fall inside the task's window
        r = traces.analyze(self.ro(), days=1)
        self.assertEqual(r["tasks"], 1); self.assertEqual(r["first_attempt_verified"], 0); self.assertEqual(r["avg_attempts"], 2)
        self.assertTrue(any("groq refused" in s for s in r["suggestions"]), r["suggestions"])
        self.assertEqual(r["skills_and_outcomes"]["grill-me"], {"tasks": 1, "verified": 1, "failed": 0, "corrected": 0})
        self.assertEqual(r["provider_latency"]["openrouter-private"]["p50_ms"], 2400)
        self.assertEqual(self.k.db.execute("SELECT COUNT(*) FROM audit").fetchone()[0], before + 6)   # only the test's own rows
        self.assertEqual(traces.analyze(self.ro(), days=1, public=True)["suggestions"], len(r["suggestions"]))

    def test_cli_trace_and_save(self):
        from jarvis_kernel.cli import main
        out = os.path.join(self.tmp.name, "tr")
        self.assertEqual(main(["--data", self.dir, "trace", self.tid, "--save", out]), 0)
        self.assertTrue(os.path.isfile(os.path.join(out, f"{self.tid}.json")))
        self.assertEqual(main(["--data", self.dir, "trace", "000000000000"]), 2)


if __name__ == "__main__":
    unittest.main()
