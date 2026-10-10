"""Outcome tasks: state machine and budgets, dispatch to the lab, the runner's narrow token, overview and Telegram."""
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

HERE = os.path.dirname(__file__)
sys.path.insert(0, os.path.join(HERE, ".."))
from jarvis_kernel.llm_proxy import make_handler
from jarvis_kernel.store import Kernel
from jarvis_kernel.tasks import Tasks, TaskError
from jarvis_kernel.telegram import Bridge


class Store(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.k = Kernel(os.path.join(self.tmp.name, "d")); self.T = Tasks(self.k)

    def tearDown(self):
        self.k.db.close(); self.tmp.cleanup()

    def test_states_follow_the_rules_and_budgets_stop_retries(self):
        t = self.T.create("hermes", "private", "make a site", "A site with 3 pages", ["plan"], ["3 pages return 200"], max_attempts=2)
        self.assertEqual(self.T.get(t)["state"], "planning")
        with self.assertRaises(TaskError):
            self.T.update("hermes", t, state="verifying")                  # planning cannot jump to verifying
        self.T.update("hermes", t, state="running", attempt=True)
        self.T.update("hermes", t, state="verifying")
        self.T.update("hermes", t, state="repairing")
        self.T.update("hermes", t, state="running", attempt=True)        # attempt 2 of 2
        self.T.update("hermes", t, state="verifying")
        with self.assertRaises(TaskError):
            self.T.update("hermes", t, state="repairing")                 # budget used up: no third try
        self.T.update("hermes", t, state="failed", result="links broken on page 2")
        with self.assertRaises(TaskError):
            self.T.update("hermes", t, state="running")                   # final stays final
        g = self.T.get(t)
        self.assertEqual((g["state"], g["attempts"]), ("failed", 2))
        self.assertEqual([e["state"] for e in g["events"]][0], "failed")
        with self.assertRaises(TaskError):
            self.T.update("lab", t, step="x")                             # not the owner of the task

    def test_deadline_fails_open_tasks(self):
        t = self.T.create("hermes", "private", "", "goal", budget_s=60)
        self.assertEqual(self.T.expire(time.time() + 61), [t])
        self.assertEqual(self.T.get(t)["state"], "failed")
        with self.assertRaises(TaskError):
            self.T.create("hermes", "private", "", "goal", max_attempts=9)


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.k = Kernel(os.path.join(self.tmp.name, "d"))
        H, tokens = make_handler(self.k, [])
        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), H); threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.srv.server_address[1]}"
        self.owner, self.lab = tokens.issue("hermes"), tokens.issue("lab", "public")
        self.runner, self.hub = tokens.issue("lab-runner", "runner"), tokens.issue("hub", "dashboard")

    def tearDown(self):
        self.srv.shutdown(); self.k.db.close(); self.tmp.cleanup()

    def post(self, path, body, tok):
        r = urllib.request.Request(self.url + path, data=json.dumps(body).encode(), method="POST",
                                   headers={"Content-Type": "application/json", "Authorization": "Bearer " + tok})
        try:
            resp = urllib.request.urlopen(r, timeout=10); return resp.status, json.loads(resp.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read() or b"{}")

    def act(self, op, target, params, tok):
        return self.post("/v1/jarvis/actions", {"tool": "task", "op": op, "target": target, "params": params}, tok)


class API(Base):
    def test_owner_task_goes_to_the_lab_and_the_runner_closes_it_with_evidence(self):
        code, r = self.act("create", "Fix the failing tests", {"goal": "Fix the failing tests", "criteria": ["python -m unittest passes"],
                                                               "request": "my tests fail, fix them"}, self.owner)
        self.assertEqual(code, 200); tid = r["task"]
        self.assertEqual(self.act("get", tid, {}, self.lab)[0], 404)                       # other agents cannot read it
        code, r = self.act("dispatch", tid, {"instructions": "Repo is in ~/work/x. Make the tests pass."}, self.owner)
        self.assertEqual((code, "public_ok" in r["reason"]), (400, True))                  # private lane must say it may be shared
        code, r = self.act("dispatch", tid, {"instructions": "-----BEGIN OPENSSH PRIVATE KEY-----\nx", "public_ok": True}, self.owner)
        self.assertEqual(code, 400)
        for where in ("Save it in D:\\jarvis\\outputs\\hermes", "copy to /mnt/d/jarvis", "put it on C:/Users/me"):
            code, r = self.act("dispatch", tid, {"instructions": where, "public_ok": True}, self.owner)
            self.assertEqual(code, 400, where); self.assertIn("cannot see the owner's computer", r["reason"])
        code, r = self.act("dispatch", tid, {"instructions": "Make the tests pass. token ghp_abcdefghijklmnopqrstuvwxyz0123456789",
                                             "public_ok": True, "agent": "opencode"}, self.owner)
        self.assertEqual((code, r["status"]), (200, "queued")); self.assertIn("END your turn", r["next"])
        code, r = self.act("dispatch", tid, {"instructions": "again", "public_ok": True}, self.owner)
        self.assertEqual(code, 409)                                                        # no duplicate lab work
        self.assertEqual(self.k.db.execute("SELECT COUNT(*) FROM jobs WHERE kind='lab_run'").fetchone()[0], 1)
        self.assertEqual(self.act("update", tid, {"state": "repairing"}, self.owner)[0], 409)   # the lab owns the state now
        self.assertEqual(self.act("update", tid, {"note": "owner asked for a README too"}, self.owner)[0], 200)
        code, r = self.post("/v1/jarvis/actions", {"tool": "output", "op": "save", "target": "tool.py", "params": {"content": "x"}}, self.owner)
        self.assertEqual(code, 409); self.assertIn(f"task-{tid}", r["reason"])           # no copy racing the lab's delivery
        self.assertEqual(self.post("/v1/jarvis/actions", {"tool": "output", "op": "save", "target": "n.md", "params": {"content": "x"}},
                                   self.lab)[0], 200)                                     # other agents are not affected
        # the runner: narrow token, only runner endpoints
        self.assertEqual(self.post("/v1/jarvis/runner/next", {}, self.lab)[0], 403)
        self.assertEqual(self.post("/v1/chat/completions", {"messages": []}, self.runner)[0], 403)
        self.assertEqual(self.post("/v1/jarvis/actions", {"tool": "task", "op": "get", "target": tid}, self.runner)[0], 403)
        code, r = self.post("/v1/jarvis/runner/next", {}, self.runner)
        job = r["job"]; self.assertEqual(job["params"]["agent"], "opencode")
        self.assertNotIn("ghp_abc", job["params"]["instructions"])                        # scrubbed before the public lab
        self.assertEqual(self.post("/v1/jarvis/runner/report", {"job": "nope", "state": "running"}, self.runner)[0], 404)
        for body in ({"state": "running", "attempt": True, "step": "attempt 1"}, {"state": "verifying"},
                     {"state": "completed", "result": "verified after 1 attempt(s)", "evidence": ["MET: tests pass -- ran unittest: OK"],
                      "verification": {"passed": True}}):
            self.assertEqual(self.post("/v1/jarvis/runner/report", {"job": job["id"], **body}, self.runner)[0], 200)
        self.assertEqual(self.post("/v1/jarvis/runner/status", {"job": job["id"]}, self.runner)[1], {"state": "completed"})
        self.assertEqual(self.post("/v1/jarvis/runner/finish", {"job": job["id"], "result": {"ok": True, "summary": "verified"}}, self.runner)[0], 200)
        code, r = self.act("get", tid, {}, self.owner)
        t = r["task"]
        self.assertEqual((t["state"], t["attempts"], t["verification"]["passed"]), ("completed", 1, True))
        # the owner's dashboard sees it; Telegram hands the outcome to JARVIS once
        o = json.loads(urllib.request.urlopen(urllib.request.Request(self.url + "/v1/jarvis/overview",
                                                                     headers={"Authorization": "Bearer " + self.hub})).read())
        self.assertEqual((o["tasks"][0]["id"], o["tasks"][0]["state"]), (tid, "completed"))

        class Api:
            sent = []
            def send(self, chat, text): Api.sent.append(text)
        asked, pending = [], []
        b = Bridge(self.k, Api(), 1, [], agent=lambda p: asked.append(p) or "Done: your tests pass now.", spawn=pending.append)
        b.flush_tasks(); b.flush_tasks()
        self.assertEqual(len(pending), 1); pending[0]()
        self.assertIn("COMPLETED", asked[0]); self.assertIn(tid, asked[0])
        self.assertEqual(Api.sent, ["Done: your tests pass now."])


class Delivery(Base):
    def test_verified_files_reach_the_laptop_in_the_tasks_own_folder_and_only_then(self):
        code, r = self.act("create", "t", {"goal": "A tool", "criteria": ["tests pass", "delivered to the owner"]}, self.owner)
        tid = r["task"]
        self.act("dispatch", tid, {"instructions": "build it", "public_ok": True}, self.owner)
        job = self.post("/v1/jarvis/runner/next", {}, self.runner)[1]["job"]["id"]
        self.post("/v1/jarvis/runner/report", {"job": job, "state": "running", "attempt": True}, self.runner)
        deliver = lambda name, content="print(1)\n": self.post("/v1/jarvis/runner/deliver", {"job": job, "name": name, "content": content}, self.runner)
        self.assertEqual(deliver("tool.py")[0], 409)                          # not verified yet
        self.post("/v1/jarvis/runner/report", {"job": job, "state": "verifying"}, self.runner)
        self.assertEqual(deliver("../evil.py")[0], 403)                       # the output name rule holds
        self.assertEqual(deliver("key.txt", "-----BEGIN OPENSSH PRIVATE KEY-----\nx")[0], 400)
        self.assertEqual(self.post("/v1/jarvis/runner/deliver", {"job": job, "name": "a.py", "content": "x"}, self.lab)[0], 403)
        code, r = deliver("tool.py")
        self.assertEqual((code, r["status"]), (200, "queued")); out = r["job"]
        row = self.k.db.execute("SELECT node, kind, params, owner FROM jobs WHERE id=?", (out,)).fetchone()
        self.assertEqual((row[0], row[1], json.loads(row[2])["agent"], row[3]), ("laptop", "output_save", f"task-{tid}", f"runner-{tid}"))
        st = lambda: self.post("/v1/jarvis/runner/delivery", {"job": job, "delivery": out}, self.runner)
        self.assertEqual(st()[1]["state"], "queued")
        self.assertEqual(self.post("/v1/jarvis/runner/delivery", {"job": job, "delivery": job}, self.runner)[0], 404)
        from jarvis_kernel.jobs import Jobs
        J = Jobs(self.k); n = J.next_for("laptop")                            # the laptop node takes it and confirms
        self.assertEqual(n["id"], out)
        J.finish("laptop", out, {"ok": True, "saved": "tool.py", "folder": f"D:\\jarvis\\outputs\\task-{tid}", "chars": 9})
        code, r = st()
        self.assertEqual((code, r["state"], r["result"]["saved"]), (200, "done", "tool.py"))
        self.post("/v1/jarvis/runner/report", {"job": job, "state": "completed", "verification": {"passed": True}}, self.runner)
        self.assertEqual(deliver("late.py")[0], 409)                          # nothing after completion


class Race(Base):
    def test_simultaneous_dispatches_create_one_lab_job(self):
        tid = self.act("create", "t", {"goal": "A tool", "criteria": ["tests pass"]}, self.owner)[1]["task"]
        codes, threads = [], []
        go = threading.Event()

        def send():
            go.wait(); codes.append(self.act("dispatch", tid, {"instructions": "build it", "public_ok": True}, self.owner)[0])
        for _ in range(6):
            th = threading.Thread(target=send); th.start(); threads.append(th)
        go.set()
        for th in threads:
            th.join()
        self.assertEqual(sorted(codes), [200, 409, 409, 409, 409, 409])
        self.assertEqual(self.k.db.execute("SELECT COUNT(*) FROM jobs WHERE kind='lab_run'").fetchone()[0], 1)


class Skills(Base):
    def skill(self, op, name, risk, tok, aid=None, where="lab"):
        body = {"tool": "skill", "op": op, "target": name, "params": {"risk": risk, "where": where, "sha256": "ab" * 32}}
        if aid:
            body["approval_id"] = aid
        return self.post("/v1/jarvis/actions", body, tok)

    def test_activation_follows_the_recorded_risk_and_is_audited(self):
        self.assertEqual(self.skill("activate", "ux-test", "low", self.owner), (200, {"status": "allowed", "level": "YELLOW_AUTO"}))
        code, r = self.skill("activate", "grill-me", "medium", self.owner)
        self.assertEqual((code, r["status"]), (202, "needs_approval"))
        self.assertEqual(self.skill("activate", "grill-me", "medium", self.owner, aid=r["approval_id"])[0], 403)   # not yet approved
        self.k.approve(r["approval_id"])
        self.assertEqual(self.skill("activate", "grill-me", "medium", self.owner, aid=r["approval_id"])[0], 200)
        self.assertEqual(self.skill("activate", "grill-me", "medium", self.owner, aid=r["approval_id"])[0], 403)   # single use
        self.assertEqual(self.skill("activate", "x", "reject", self.owner)[0], 403)
        self.assertEqual(self.skill("activate", "x", "", self.owner)[0], 403)
        self.assertEqual(self.skill("activate", "ux-test", "low", self.lab)[0], 403)                            # a lab agent
        self.assertEqual(self.skill("activate", "ux-test", "low", self.owner, where="server")[0], 202)          # the owner's agent
        self.assertEqual(self.skill("deactivate", "ux-test", "low", self.owner)[0], 200)
        kinds = [r[0] for r in self.k.db.execute("SELECT kind FROM audit WHERE kind LIKE 'skill_%'").fetchall()]
        self.assertEqual(kinds, ["skill_activate", "skill_activate", "skill_deactivate"])


class Honesty(Base):
    def test_an_agent_cannot_mark_a_task_completed_without_proof(self):
        code, r = self.act("create", "x", {"goal": "Deliver a tool", "criteria": ["tests pass"]}, self.owner)
        tid = r["task"]; self.assertIn("lab_work", r["next"])
        self.act("update", tid, {"state": "running"}, self.owner)
        code, r = self.act("update", tid, {"state": "completed", "result": "all tests pass"}, self.owner)
        self.assertEqual(code, 409); self.assertIn("proof", r["reason"])
        code, r = self.act("update", tid, {"state": "completed", "evidence": ["job:abcdefabcdef"]}, self.owner)
        self.assertEqual(code, 409)                                            # a job id that does not exist / not done
        code, r = self.post("/v1/jarvis/actions", {"tool": "output", "op": "save", "target": "tool.py", "params": {"content": "x"}}, self.owner)
        self.assertEqual(code, 409); self.assertIn("lab_work", r["reason"])          # code it cannot run is never delivered
        code, r = self.post("/v1/jarvis/actions", {"tool": "output", "op": "save", "target": "notes.md", "params": {"content": "x"}}, self.owner)
        jid = r["job"]
        self.k.db.execute("UPDATE jobs SET state='done' WHERE id=?", (jid,))
        code, r = self.act("update", tid, {"state": "completed", "evidence": [f"saved job:{jid}"]}, self.owner)
        self.assertEqual((code, r["state"]), (200, "completed"))

    def test_the_owner_controls_tasks_from_telegram_without_a_model(self):
        class Api:
            sent = []
            def send(self, chat, text): Api.sent.append(text)
        T = Tasks(self.k); tid = T.create("hermes", "private", "", "Build the tool", criteria=["tests pass"])
        T.update("hermes", tid, state="running")
        b = Bridge(self.k, Api(), 1, [])
        say = lambda text: b.handle({"update_id": 1, "message": {"text": text, "chat": {"id": 1, "type": "private"}, "from": {"id": 1}}}) or Api.sent[-1]
        self.assertIn(tid, say("/tasks"))
        self.assertIn("Goal: Build the tool", say(f"/task {tid}"))
        self.assertIn("not paused", say(f"/task {tid} resume"))
        self.assertIn("paused", say(f"/task {tid} pause")); self.assertEqual(T.get(tid)["state"], "paused")
        self.assertIn("running", say(f"/task {tid} resume")); self.assertEqual(T.get(tid)["state"], "running")
        self.assertIn("cancelled", say(f"/task {tid} cancel")); self.assertEqual(T.get(tid)["state"], "cancelled")
        self.assertIn("Refused", say(f"/task {tid} pause"))                    # final stays final
        self.assertIn("Usage", say("/task nope"))
        b2 = Bridge(self.k, Api(), 1, [])
        b2.handle({"update_id": 2, "message": {"text": f"/task {tid} cancel", "chat": {"id": 9, "type": "private"}, "from": {"id": 9}}})
        self.assertEqual(len([s for s in Api.sent if "Refused" in s]), 1)      # strangers are ignored

    def test_telegram_reply_carries_the_kernel_record_of_the_task(self):
        class Api:
            sent = []
            def send(self, chat, text): Api.sent.append(text)
        k = self.k
        def agent(text):                                                       # claims success, never verifies
            k_tasks = Tasks(k); t = k_tasks.create("hermes", "private", text, "Build the tool", criteria=["tests pass"])
            return "Task completed. All tests pass."
        pending = []
        b = Bridge(self.k, Api(), 1, [], agent=agent, spawn=pending.append)
        b.handle({"update_id": 1, "message": {"text": "build me a tool", "chat": {"id": 1, "type": "private"}, "from": {"id": 1}}})
        pending[0]()
        self.assertIn("Task completed", Api.sent[-1]); self.assertIn("Kernel record", Api.sent[-1])
        self.assertIn("planning, NOT verified yet", Api.sent[-1])


if __name__ == "__main__":
    unittest.main()
