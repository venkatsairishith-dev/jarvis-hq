"""Idempotency keys (2026-10-17): a request sent again after a lost reply, with the same Idempotency-Key, gets the first
reply back and the kernel does not act twice: no second owner command, no second PC job, no second agent action. Without
a key nothing changes; a refused request is not remembered; the same key for a different request is refused."""
import json
import os
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from jarvis_kernel.llm_proxy import make_handler
from jarvis_kernel.store import Kernel


class Http(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.k = Kernel(os.path.join(self.tmp.name, "d"))
        self.said = []

        def hermes(text):
            self.said.append(text)
            return "Opened Downloads."
        H, tokens = make_handler(self.k, [], owner_hermes=hermes)
        self.plane = H.owner_plane
        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), H); threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.srv.server_address[1]}"
        self.tok, self.agent = tokens.issue("console", "owner"), tokens.issue("hermes", "private")

    def tearDown(self):
        self.srv.shutdown(); self.k.db.close(); self.tmp.cleanup()

    def call(self, path, body, tok=None, key=None):
        h = {"Authorization": "Bearer " + (tok or self.tok), "Content-Type": "application/json"}
        if key:
            h["Idempotency-Key"] = key
        r = urllib.request.Request(self.url + path, data=json.dumps(body).encode(), method="POST", headers=h)
        try:
            with urllib.request.urlopen(r, timeout=10) as resp:
                return resp.status, json.loads(resp.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read() or b"{}")

    def jobs(self, kind):
        return self.k.db.execute("SELECT COUNT(*) FROM jobs WHERE kind=?", (kind,)).fetchone()[0]

    def wait_done(self, cid):
        end = time.time() + 5
        while time.time() < end:
            r = self.k.db.execute("SELECT state FROM owner_commands WHERE id=?", (cid,)).fetchone()
            if r and r[0] != "running":
                return r[0]
            time.sleep(0.02)
        self.fail("command did not finish")

    def audit_kinds(self):
        return [r[0] for r in self.k.db.execute("SELECT kind FROM audit ORDER BY seq").fetchall()]

    # ---- the owner plane (the phone, through the console)
    def test_owner_command_retried_with_the_same_key_runs_once(self):
        body = {"text": "open my Downloads folder"}
        c1, r1 = self.call("/v1/jarvis/owner/command", body, key="phone-cmd-0001")
        self.assertEqual(c1, 202)
        self.wait_done(r1["id"])                         # the first one finished; its reply never reached the phone
        c2, r2 = self.call("/v1/jarvis/owner/command", body, key="phone-cmd-0001")
        self.assertEqual((c2, r2["id"], r2.get("replayed")), (202, r1["id"], True))
        self.assertEqual(len(self.said), 1)              # JARVIS was asked once
        self.assertEqual(self.k.db.execute("SELECT COUNT(*) FROM owner_commands").fetchone()[0], 1)
        self.assertIn("idempotent_replay", self.audit_kinds())

    def test_owner_pc_job_retried_with_the_key_in_the_body_is_queued_once(self):
        body = {"op": "open_app", "target": "notepad", "idempotency_key": "pc-open-notepad-1"}
        c1, r1 = self.call("/v1/jarvis/owner/pc", dict(body))
        c2, r2 = self.call("/v1/jarvis/owner/pc", dict(body))
        self.assertEqual((c1, c2), (202, 202))
        self.assertEqual(r2["job"], r1["job"]); self.assertTrue(r2["replayed"])
        self.assertEqual(self.jobs("pc_open"), 1)
        # without a key, behaviour is unchanged: each request acts
        self.call("/v1/jarvis/owner/pc", {"op": "open_app", "target": "notepad"})
        self.assertEqual(self.jobs("pc_open"), 2)

    def test_same_key_for_a_different_request_is_refused_and_does_nothing(self):
        self.call("/v1/jarvis/owner/pc", {"op": "open_app", "target": "notepad"}, key="pc-key-shared-1")
        code, r = self.call("/v1/jarvis/owner/pc", {"op": "open_app", "target": "calculator"}, key="pc-key-shared-1")
        self.assertEqual(code, 422); self.assertIn("different request", r["error"])
        self.assertEqual(self.jobs("pc_open"), 1)
        self.assertIn("idempotency_mismatch", self.audit_kinds())

    def test_a_refused_request_is_not_remembered(self):
        self.k.kill("test")
        code, _ = self.call("/v1/jarvis/owner/pc", {"op": "open_app", "target": "notepad"}, key="pc-after-kill-1")
        self.assertEqual(code, 503)
        self.k.resume()
        code, r = self.call("/v1/jarvis/owner/pc", {"op": "open_app", "target": "notepad"}, key="pc-after-kill-1")
        self.assertEqual(code, 202); self.assertNotIn("replayed", r)
        self.assertEqual(self.jobs("pc_open"), 1)

    def test_a_bad_key_is_refused_before_anything_runs(self):
        code, _ = self.call("/v1/jarvis/owner/pc", {"op": "open_app", "target": "notepad"}, key="no spaces allowed")
        self.assertEqual(code, 400); self.assertEqual(self.jobs("pc_open"), 0)

    def test_approve_never_stores_a_body_with_a_pin(self):
        self.call("/v1/jarvis/owner/approve", {"id": "x" * 16, "confirm": "x" * 16, "pin": "123456"}, key="approve-key-01")
        self.assertEqual(self.k.db.execute("SELECT COUNT(*) FROM idempotency").fetchone()[0], 0)

    # ---- agent actions (MCP tool calls the agent's client may retry)
    def test_agent_action_retried_with_the_same_key_queues_one_job(self):
        body = {"tool": "output", "op": "save", "target": "notes.md", "params": {"content": "hello"}}
        c1, r1 = self.call("/v1/jarvis/actions", body, tok=self.agent, key="agent-save-notes-1")
        c2, r2 = self.call("/v1/jarvis/actions", body, tok=self.agent, key="agent-save-notes-1")
        self.assertEqual((c1, c2), (200, 200))
        self.assertEqual(r2["job"], r1["job"]); self.assertTrue(r2["replayed"])
        self.assertEqual(self.jobs("output_save"), 1)

    def test_lab_dispatch_retried_gets_its_first_reply_not_a_conflict(self):
        _, t = self.call("/v1/jarvis/actions", {"tool": "task", "op": "create", "target": "Build a tool",
                                                "params": {"goal": "Build a tool", "criteria": ["it runs"], "request": "build me a small tool"}},
                         tok=self.agent)
        d = {"tool": "task", "op": "dispatch", "target": t["task"], "params": {"instructions": "write it", "public_ok": True}}
        c1, r1 = self.call("/v1/jarvis/actions", d, tok=self.agent, key="dispatch-task-0001")
        c2, r2 = self.call("/v1/jarvis/actions", d, tok=self.agent, key="dispatch-task-0001")
        self.assertEqual((c1, c2, r2["job"], r2.get("replayed")), (200, 200, r1["job"], True))
        self.assertEqual(self.jobs("lab_run"), 1)


class Store(unittest.TestCase):
    """The key store itself: 24 h lifetime, a request still running, a crash in the handler."""
    def setUp(self):
        from jarvis_kernel.idempotency import Idempotency
        self.tmp = tempfile.TemporaryDirectory(); self.k = Kernel(os.path.join(self.tmp.name, "d"))
        self.now = [1000.0]
        self.I = Idempotency(self.k, clock=lambda: self.now[0])
        self.ran = 0

    def tearDown(self):
        self.k.db.close(); self.tmp.cleanup()

    def act(self):
        self.ran += 1
        return 200, {"n": self.ran}

    def test_kept_for_24_hours_then_acts_again(self):
        self.assertEqual(self.I.run("s", "key-00001", {"a": 1}, self.act), (200, {"n": 1}))
        self.now[0] += 23 * 3600
        self.assertEqual(self.I.run("s", "key-00001", {"a": 1}, self.act), (200, {"n": 1, "replayed": True}))
        self.now[0] += 2 * 3600
        self.assertEqual(self.I.run("s", "key-00001", {"a": 1}, self.act), (200, {"n": 2}))

    def test_a_repeat_while_the_first_still_runs_does_not_run_it_again(self):
        inner = []

        def slow():
            inner.append(self.I.run("s", "key-00002", {"a": 1}, self.act))      # the retry arrives meanwhile
            return self.act()
        self.assertEqual(self.I.run("s", "key-00002", {"a": 1}, slow)[0], 200)
        self.assertEqual(inner[0][0], 409); self.assertEqual(self.ran, 1)

    def test_a_crash_in_the_handler_frees_the_key(self):
        def boom():
            raise RuntimeError("x")
        with self.assertRaises(RuntimeError):
            self.I.run("s", "key-00003", {}, boom)
        self.assertEqual(self.I.run("s", "key-00003", {}, self.act), (200, {"n": 1}))

    def test_scopes_do_not_meet(self):
        self.I.run("agent:a", "key-00004", {}, self.act)
        self.assertEqual(self.I.run("agent:b", "key-00004", {}, self.act), (200, {"n": 2}))


if __name__ == "__main__":
    unittest.main()
