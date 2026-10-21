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
from jarvis_kernel.tasks import Tasks, TaskError, criteria_sha256
from jarvis_kernel.telegram import Bridge


ART = "a" * 64
HARNESS_SHA, IDS_SHA, CHAN_SHA = "e" * 64, "f" * 64, "9" * 64          # the runner's harness result, its collected test ids, the verifier's output line


def lab_verdict(tid, job, criteria, attempt=1, passed=True, rows=None, **over):
    """What the lab runner sends as `verification` (lab verdict audit, 2026-10-18): the identity the kernel dispatched and counted, the
    criteria hash and the artifact digest, plus one met row per criterion unless `rows` says otherwise."""
    rows = [{"criterion": c, "met": passed, "evidence": "ran it"} for c in criteria] if rows is None else rows
    out = {"passed": passed, "task": tid, "job": job, "attempt": attempt, "criteria_sha256": criteria_sha256(criteria),
           "artifact_sha256": ART, "harness_sha256": HARNESS_SHA, "collected_ids_sha256": IDS_SHA, "verdict_channel_sha256": CHAN_SHA, **over}
    if rows is not False:                                  # rows=False: no per-criterion report at all (a bare verdict)
        out["criteria"] = rows
    return out


def lab_isolation(**over):
    """The isolation block of a run that had a real boundary: the sandbox identity (uid 995), the verifier's (1001) and the runner (root)."""
    return {"mode": "full", "backend": "identity", "self_test": "ok", "verifier_tools_isolated": True, "separate_verifier_identity": True,
            "sandbox_uid": 995, "verify_uid": 1001, "runner_uid": 0, "processes": 4, "granularity": "id", "cpu_seconds": 0.3, "reasons": [], **over}


def lab_trusted(tid, job, attempt=1, **over):
    """What the runner records after running the delivered tests itself (`trusted_execution`)."""
    return {"ran_by": "lab-runner", "command": "python3 lab harness (unittest discover, test*.py)", "harness": "lab-harness/2", "exit_code": 0,
            "collect_exit_code": 0, "tests_run": 3, "tests_collected": 3, "tests_passed": 3, "tests_failed": 0, "tests_errored": 0,
            "tests_skipped": 0, "tests_expected_failures": 0, "leftover_processes": 0, "collected_ids_sha256": IDS_SHA,
            "harness_sha256": HARNESS_SHA, "output_sha256": "b" * 64, "files_sha256": "c" * 64, "artifact_sha256": ART, "task": tid,
            "job": job, "attempt": attempt, "isolation": lab_isolation(), **over}


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

    def test_an_expired_task_says_why_even_after_a_partial_result(self):
        # regression (bench D-068): the earlier partial result alone was kept, so "failed: draft 1 of 3 written" hid the reason
        t = self.T.create("hermes", "private", "", "Write 3 pages", budget_s=60)
        self.T.update("hermes", t, state="running", result="draft 1 of 3 pages written")
        self.T.expire(time.time() + 61)
        r = self.T.get(t)["result"]
        self.assertTrue(r.startswith("time budget used up"), r); self.assertIn("draft 1 of 3", r)

    def test_checkpoint_keeps_the_latest_correction_and_next_with_a_long_goal(self):
        # regression (bench A-080): the joined text was cut at 900 characters after an untrimmed goal line
        t = self.T.create("hermes", "private", "", "Plan the trip: " + "visit every museum and gallery on the list " * 25)
        self.T.update("hermes", t, state="running", step="booking", note="owner: use the train, not the plane")
        for i in range(2):
            o = self.T.create("hermes", "private", "", f"other task {i} " + "with a long goal " * 20, criteria=["c" * 250])
            self.T.update("hermes", o, state="running", note="x" * 200)
        self.T.update("hermes", t, note="owner correction: the budget is 500 euros")    # newest: listed first
        txt = self.T.checkpoint("hermes")["text"]
        self.assertLessEqual(len(txt), 900)
        self.assertIn("the budget is 500 euros", txt)
        self.assertIn("next: wait: the lab is working", txt)
        self.assertNotIn("museum and gallery on the list " * 5, txt)                  # the goal is what was trimmed

    def _lab_task(self, run=True):
        from jarvis_kernel.jobs import Jobs
        J = Jobs(self.k)
        t = self.T.create("hermes", "public", "", "A tool", criteria=["tests pass"])
        jid = J.create("lab", "lab_run", {"task": t, "agent": "hermes", "instructions": "build it", "criteria": ["tests pass"],
                                          "max_attempts": 2, "timeout_s": 600}, owner="hermes", lane="public")
        self.T.update("hermes", t, state="queued")
        if run:
            J.next_for("lab"); self.T.update(None, t, state="running", attempt=True)
        return J, t, jid

    def test_lab_work_that_ends_without_a_result_does_not_leave_the_task_open(self):
        # regression (bench D-072..D-074): the task kept saying running/queued until its deadline and the owner heard nothing
        J, t, jid = self._lab_task()
        J.finish("lab", jid, {"ok": False, "error": "runner error: KeyError"})        # the runner crashed before reporting
        g = self.T.get(t)
        self.assertEqual(g["state"], "failed"); self.assertIn("runner error: KeyError", g["result"])
        J, t, jid = self._lab_task(run=False)
        J.cancel(jid)                                                                 # the owner cancelled the queued lab job
        self.assertEqual(self.T.get(t)["state"], "cancelled")
        J, t, jid = self._lab_task(run=False)
        self.k.db.execute("UPDATE jobs SET expires=? WHERE id=?", (time.time() - 1, jid))
        J.sweep()                                                                     # the runner was offline: the job expired
        self.assertEqual(self.T.get(t)["state"], "failed"); self.assertIn("expired", self.T.get(t)["result"])
        J, t, jid = self._lab_task()
        self.T.update(None, t, state="verifying")
        self.T.update(None, t, state="completed", result="verified", verification={"passed": True})
        J.finish("lab", jid, {"ok": True})
        self.assertEqual(self.T.get(t)["state"], "completed")                         # a normal end is untouched


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.k = Kernel(os.path.join(self.tmp.name, "d"))
        H, tokens = make_handler(self.k, [])
        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), H); threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.srv.server_address[1]}"
        self.tokens = tokens
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

    def act(self, op, target, params, tok, approval_id=None):
        body = {"tool": "task", "op": op, "target": target, "params": params}
        if approval_id:
            body["approval_id"] = approval_id
        return self.post("/v1/jarvis/actions", body, tok)

    def approved_dispatch(self, tid, params, tok=None):
        """A private agent's dispatch: the kernel holds it for the owner's approval of exactly that text (audit SEC-D5); the
        owner approves at the console; the agent sends the approval id again."""
        tok = tok or self.owner
        code, r = self.act("dispatch", tid, params, tok)
        if code != 202:
            return code, r
        self.assertEqual((r["status"], r["level"]), ("needs_approval", "YELLOW_APPROVAL"))
        self.assertTrue(self.k.approve(r["approval_id"]))
        return self.act("dispatch", tid, {"public_ok": True}, tok, approval_id=r["approval_id"])


class RunnerStatus(Base):
    """What the lab runner learns about its running job each few seconds (audit SEC-D10: it used to learn only the task's state,
    so a lab job already running ignored the kill switch)."""

    def running_job(self):
        code, r = self.act("create", "Build a thing", {"goal": "Build a thing", "criteria": ["it works"], "request": "build a thing please"}, self.owner)
        tid = r["task"]
        code, r = self.approved_dispatch(tid, {"instructions": "Build it.", "public_ok": True})
        self.assertEqual(code, 200)
        job = self.post("/v1/jarvis/runner/next", {}, self.runner)[1]["job"]
        self.assertEqual(self.post("/v1/jarvis/runner/report", {"job": job["id"], "state": "running", "attempt": True}, self.runner)[0], 200)
        return tid, job["id"]

    def test_the_status_says_when_the_kill_switch_is_on_and_not_before(self):
        tid, jid = self.running_job()
        self.assertEqual(self.post("/v1/jarvis/runner/status", {"job": jid}, self.runner)[1], {"state": "running"})
        self.k.kill("test")
        code, r = self.post("/v1/jarvis/runner/status", {"job": jid}, self.runner)
        self.assertEqual((code, r["killed"], r["state"]), (200, True, "running"))
        self.assertEqual(self.post("/v1/jarvis/runner/next", {}, self.runner)[1]["job"], None)       # and nothing new starts
        self.k.resume()
        self.assertEqual(self.post("/v1/jarvis/runner/status", {"job": jid}, self.runner)[1], {"state": "running"})

    def test_the_status_is_answered_while_killed_even_for_a_job_the_kernel_no_longer_runs(self):
        tid, jid = self.running_job()
        self.k.kill("test")
        self.assertEqual(self.post("/v1/jarvis/runner/status", {"job": "nope"}, self.runner)[1].get("killed"), True)
        self.assertEqual(self.post("/v1/jarvis/runner/status", {"job": "nope"}, self.lab)[0], 403)   # only the runner's token

    def test_a_cancel_of_this_very_job_is_in_the_status(self):
        from jarvis_kernel.jobs import Jobs
        tid, jid = self.running_job()
        Jobs(self.k).cancel(jid)                      # the owner cancels a running job: marked, the node confirms later
        self.assertEqual(self.post("/v1/jarvis/runner/status", {"job": jid}, self.runner)[1], {"state": "running", "cancel_requested": True})

    def test_a_worker_stopped_by_the_kill_switch_ends_its_task_as_stopped_by_the_kill_switch(self):
        """What the runner reports after killing the worker (lab_runner.stopped): the task fails with the reason, the job too."""
        tid, jid = self.running_job()
        self.k.kill("test")
        self.assertEqual(self.post("/v1/jarvis/runner/report", {"job": jid, "state": "failed", "step": "stopped: the kill switch was switched on",
                                                                "result": "stopped by the kill switch: the lab worker was killed. Nothing was verified."}, self.runner)[0], 200)
        self.assertEqual(self.post("/v1/jarvis/runner/finish", {"job": jid, "result": {"ok": False, "summary": "stopped by the kill switch",
                                                                                      "error": "stopped by the kill switch"}}, self.runner)[0], 200)
        self.k.resume()                               # reading a task is an agent action: refused while the switch is on
        t = self.act("get", tid, {}, self.owner)[1]["task"]
        self.assertEqual(t["state"], "failed")
        self.assertIn("stopped by the kill switch", t["result"])
        self.assertNotEqual((t.get("verification") or {}).get("passed"), True)                       # never reported as verified


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
        code, r = self.approved_dispatch(tid, {"instructions": "Make the tests pass. token ghp_abcdefghijklmnopqrstuvwxyz0123456789",
                                               "public_ok": True, "agent": "opencode"})
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
                      # a bare {"passed": true} is no longer a verdict when the job has criteria (audit M8): one row per criterion;
                      # and it is "verified" only with the runner's own test run (trusted_execution)
                      "verification": lab_verdict(tid, job["id"], job["params"]["criteria"]),
                      "trusted_execution": lab_trusted(tid, job["id"])}):
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
        self.assertEqual(self.approved_dispatch(tid, {"instructions": "build it", "public_ok": True})[0], 200)
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
        full = lab_verdict(tid, job, ["tests pass", "delivered to the owner"])
        self.assertEqual(self.post("/v1/jarvis/runner/report", {"job": job, "state": "completed", "verification": full,
                                                                "trusted_execution": lab_trusted(tid, job)}, self.runner)[0], 200)
        self.assertEqual(deliver("late.py")[0], 409)                          # nothing after completion


class Race(Base):
    def test_simultaneous_dispatches_create_one_lab_job(self):
        tid = self.act("create", "t", {"goal": "A tool", "criteria": ["tests pass"]}, self.owner)[1]["task"]
        code, r = self.act("dispatch", tid, {"instructions": "build it", "public_ok": True}, self.owner)
        self.assertEqual(code, 202); aid = r["approval_id"]; self.k.approve(aid)      # the owner approves the text once
        codes, threads = [], []
        go = threading.Event()

        def send():
            go.wait(); codes.append(self.act("dispatch", tid, {"public_ok": True}, self.owner, approval_id=aid)[0])
        for _ in range(6):
            th = threading.Thread(target=send); th.start(); threads.append(th)
        go.set()
        for th in threads:
            th.join()
        self.assertEqual(sorted(codes), [200, 409, 409, 409, 409, 409])
        self.assertEqual(self.k.db.execute("SELECT COUNT(*) FROM jobs WHERE kind='lab_run'").fetchone()[0], 1)


    def test_the_same_request_sent_at_once_becomes_one_task(self):
        """Bench N-069 (2026-10-17): the duplicate check and the create were two steps; requests at once made two tasks."""
        from unittest import mock
        from jarvis_kernel import tasks as tasks_mod
        real = tasks_mod.Tasks.create

        def slow(self_, *a, **kw):                    # widen the window between the duplicate check and the insert
            time.sleep(0.2)
            return real(self_, *a, **kw)
        codes, threads, go = [], [], threading.Event()

        def send():
            go.wait(); codes.append(self.act("create", "Fix it", {"goal": "Fix it", "request": "please fix my failing build"}, self.owner)[0])
        with mock.patch.object(tasks_mod.Tasks, "create", slow):
            for _ in range(5):
                th = threading.Thread(target=send); th.start(); threads.append(th)
            go.set()
            for th in threads:
                th.join()
        self.assertEqual(sorted(codes), [200, 409, 409, 409, 409])
        self.assertEqual(self.k.db.execute("SELECT COUNT(*) FROM tasks").fetchone()[0], 1)

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
        self.k.db.execute("UPDATE jobs SET state='done', finished=? WHERE id=?", (time.time(), jid))
        # Audit SEC-D1 (2026-10-18): this used to complete the task. A file saved for no task is not proof that "tests pass".
        code, r = self.act("update", tid, {"state": "completed", "evidence": [f"saved job:{jid}"]}, self.owner)
        self.assertEqual(code, 409); self.assertIn(f"job:{jid} was not created for this task", r["problems"])
        # a job done FOR this task (linked by the kernel when it was created) is evidence; the kernel records that it judged
        # finished jobs, not the criteria, so the task is completed but never shown as "verified"
        code, r = self.post("/v1/jarvis/actions", {"tool": "output", "op": "save", "target": "report.md",
                                                   "params": {"content": "x", "task": tid}}, self.owner)
        jid2 = r["job"]
        self.k.db.execute("UPDATE jobs SET state='done', finished=? WHERE id=?", (time.time() + 1, jid2))
        code, r = self.act("update", tid, {"state": "completed", "evidence": [f"saved job:{jid2}"]}, self.owner)
        self.assertEqual((code, r["state"]), (200, "completed"))
        v = Tasks(self.k).get(tid)["verification"]
        self.assertEqual((v["verifier"], v["outcome"], v["evidence"], v["criteria_checked"]), ("kernel", "evidence accepted", [f"job:{jid2}"], False))
        self.assertNotIn("passed", v)

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


PII = "Owner SYNTH-Name lives at 12 Example Road, phone +91 90000 00000, email synth.owner@example.com"


class PrivateToPublic(Base):
    """Audit SEC-D5 / MEM-M8 (2026-10-18): the private agent's own public_ok flag was enough to send the owner's data to the
    public-lane lab. Now the owner approves exactly the shared text (PIN over HTTP), bound to its digest, used once."""

    def audit_text(self):
        return " ".join(r[0] for r in self.k.db.execute("SELECT payload FROM audit").fetchall())

    def lab_jobs(self):
        return self.k.db.execute("SELECT params FROM jobs WHERE kind='lab_run'").fetchall()

    def test_public_ok_alone_never_reaches_the_lab(self):
        tid = self.act("create", "t", {"goal": "summarise", "criteria": ["a summary exists"]}, self.owner)[1]["task"]
        code, r = self.act("dispatch", tid, {"instructions": "Summarise: " + PII, "public_ok": True}, self.owner)
        self.assertEqual((code, r["status"], r["level"]), (202, "needs_approval", "YELLOW_APPROVAL"))
        self.assertEqual(self.lab_jobs(), [])                                         # nothing queued for the lab
        self.assertEqual(self.post("/v1/jarvis/runner/next", {}, self.runner)[1]["job"], None)
        self.assertNotIn("synth.owner@example.com", json.dumps(r))                   # the reply carries counts only
        self.assertEqual(Tasks(self.k).get(tid)["state"], "waiting_approval")

    def test_the_owner_approves_exactly_the_shared_text_once(self):
        tid = self.act("create", "t", {"goal": "summarise", "criteria": ["a summary exists"]}, self.owner)[1]["task"]
        text = "Summarise: " + PII + " key ghp_" + "abcdefghijklmnopqrstuvwxyz0123456789"
        code, r = self.act("dispatch", tid, {"instructions": text, "public_ok": True, "agent": "opencode"}, self.owner)
        aid = r["approval_id"]
        card = next(a for a in self.k.pending_approvals() if a["id"] == aid)          # what the owner sees before the PIN
        self.assertEqual((card["action"]["tool"], card["action"]["op"], card["action"]["lane"]), ("lab", "share", "private"))
        self.assertIn("synth.owner@example.com", card["action"]["params"]["instructions"])
        self.assertNotIn("ghp_abcdef", json.dumps(card))                              # never a secret on the card
        self.assertEqual(card["action"]["params"]["to"], "public-lane lab models")
        audit = self.audit_text()
        for leak in ("synth.owner@example.com", "Example Road", "ghp_abcdef"):
            self.assertNotIn(leak, audit)                                             # audit rows: counts and digests only
        self.assertIn("_sha256", audit)
        code, r = self.act("dispatch", tid, {"public_ok": True}, self.owner, approval_id=aid)
        self.assertEqual((code, r["reason"]), (403, "approval pending"))              # not approved yet
        self.assertTrue(self.k.approve(aid))
        code, r = self.act("dispatch", tid, {"instructions": "Summarise something else entirely", "public_ok": True,
                                             "agent": "opencode"}, self.owner, approval_id=aid)
        self.assertEqual(code, 403); self.assertIn("different action", r["reason"])   # bound to the exact text
        code, r = self.act("dispatch", tid, {"public_ok": True}, self.owner, approval_id=aid)
        self.assertEqual((code, r["status"]), (200, "queued"))
        params = json.loads(self.lab_jobs()[0][0])
        self.assertEqual(params["agent"], "opencode"); self.assertIn("synth.owner@example.com", params["instructions"])
        self.assertNotIn("ghp_abcdef", params["instructions"])                        # secrets still scrubbed
        self.assertEqual(Tasks(self.k).get(tid)["state"], "queued")
        self.post("/v1/jarvis/actions", {"tool": "task", "op": "update", "target": tid, "params": {"state": "cancelled"}}, self.owner)
        tid2 = self.act("create", "t2", {"goal": "summarise again", "criteria": ["a summary exists"]}, self.owner)[1]["task"]
        self.assertEqual(self.act("dispatch", tid2, {"instructions": text, "public_ok": True, "agent": "opencode"}, self.owner,
                                  approval_id=aid)[0], 403)                           # used once: not for another task
        self.assertNotIn("synth.owner@example.com", self.audit_text())

    def test_the_kill_switch_stops_an_approved_dispatch(self):
        tid = self.act("create", "t", {"goal": "summarise", "criteria": ["a summary exists"]}, self.owner)[1]["task"]
        aid = self.act("dispatch", tid, {"instructions": "Summarise the notes", "public_ok": True}, self.owner)[1]["approval_id"]
        self.k.approve(aid); self.k.kill("test")
        self.assertEqual(self.act("dispatch", tid, {"public_ok": True}, self.owner, approval_id=aid)[0], 503)
        self.assertEqual(self.lab_jobs(), [])

    def test_the_public_lane_needs_no_approval(self):
        tid = self.act("create", "t", {"goal": "build it", "criteria": ["tests pass"]}, self.lab)[1]["task"]
        code, r = self.act("dispatch", tid, {"instructions": "build it"}, self.lab)
        self.assertEqual((code, r["status"]), (200, "queued"))

    def test_a_lab_subtask_from_the_private_lane_needs_approval_and_is_bound_to_its_prompt(self):
        lab = lambda tok, prompt, aid=None: self.post("/v1/jarvis/actions", dict({"tool": "lab", "op": "task", "target": "opencode",
                                                                                  "params": {"prompt": prompt}}, **({"approval_id": aid} if aid else {})), tok)
        code, r = lab(self.owner, "Summarise: " + PII)
        self.assertEqual((code, r["status"]), (202, "needs_approval")); aid = r["approval_id"]
        self.assertEqual(lab(self.owner, "Summarise: " + PII, aid)[0], 403)              # not approved yet
        self.k.approve(aid)
        self.assertEqual(lab(self.owner, "Something else", aid)[0], 403)                  # another prompt
        self.assertEqual(lab(self.owner, "Summarise: " + PII, aid), (200, {"status": "allowed"}))
        self.assertEqual(lab(self.owner, "Summarise: " + PII, aid)[0], 403)              # single use
        self.assertEqual(lab(self.lab, "write a haiku"), (200, {"status": "allowed"}))    # lab agent to lab agent: as before
        self.assertEqual(lab(self.lab, "-----BEGIN RSA PRIVATE KEY-----\nabc")[0], 400)
        self.assertNotIn("Example Road", self.audit_text())


class VerifiedCompletion(Base):
    """Audit SEC-D1 / PC-D4 (2026-10-18): completion is a kernel-checked transition. An agent never writes a verdict; the
    jobs it cites must have been done for this task, after it started; the lab runner's verdict must cover every criterion."""

    def task(self, tok=None, criteria=("tests pass",)):
        tid = self.act("create", "t", {"goal": "Deliver a tool " + str(time.time()), "criteria": list(criteria)}, tok or self.owner)[1]["task"]
        self.act("update", tid, {"state": "running"}, tok or self.owner)
        return tid

    def job(self, tok=None, task=None, done=True):
        params = {"content": "x", **({"task": task} if task else {})}
        code, r = self.post("/v1/jarvis/actions", {"tool": "output", "op": "save", "target": f"n{time.time()}.md", "params": params}, tok or self.owner)
        self.assertEqual(code, 200, r)
        if done:
            self.k.db.execute("UPDATE jobs SET state='done', finished=? WHERE id=?", (time.time() + 1, r["job"]))
        return r["job"]

    def complete(self, tid, jobs, tok=None):
        return self.act("update", tid, {"state": "completed", "evidence": [f"job:{j}" for j in jobs]}, tok or self.owner)

    def row(self, tid):
        return Tasks(self.k).get(tid)

    def test_an_agent_cannot_forge_a_verdict(self):
        tid = self.task()
        code, r = self.act("update", tid, {"verification": {"passed": True, "by": "me"}}, self.owner)
        self.assertEqual(code, 403); self.assertIn("never by an agent", r["reason"])
        code, r = self.act("update", tid, {"state": "verifying", "verification": {"passed": True}}, self.owner)
        self.assertEqual(code, 403)                                                    # not even alongside another change
        self.assertEqual(self.complete(tid, [])[0], 409)
        g = self.row(tid)
        self.assertEqual((g["state"], g["verification"]), ("running", {}))

    def test_an_unrelated_job_is_not_evidence(self):
        a, b = self.task(), self.task()
        jb = self.job(task=b)                                                          # done for task b
        code, r = self.complete(a, [jb])
        self.assertEqual(code, 409); self.assertEqual(r["problems"], [f"job:{jb} was not created for this task"])
        jo = self.job(tok=self.lab, task=self.task(self.lab))                          # another agent's job
        self.assertEqual(self.complete(a, [jo])[1]["problems"], [f"job:{jo} is not yours"])
        ja = self.job(task=a)
        self.assertEqual(self.complete(a, [ja, jb])[0], 409)                           # a false citation spoils the claim
        self.assertEqual(self.complete(a, [ja])[0], 200)

    def test_a_stale_or_unfinished_job_is_not_evidence(self):
        old = self.job()                                                               # done before the task existed
        tid = self.task()
        self.assertEqual(self.complete(tid, [old])[0], 409)
        jl = self.job(task=tid)
        t0 = self.row(tid)["created"]
        self.k.db.execute("UPDATE jobs SET created=?, finished=? WHERE id=?", (t0 - 60, t0 - 30, jl))   # older than the task
        self.assertEqual(self.complete(tid, [jl])[1]["problems"], [f"job:{jl} is older than this task"])
        jq = self.job(task=tid, done=False)
        self.assertEqual(self.complete(tid, [jq])[1]["problems"], [f"job:{jq} has not finished successfully (queued)"])
        self.assertEqual(self.complete(tid, ["job:abcdefabcdef"])[0], 409)
        self.assertEqual(self.row(tid)["state"], "running")

    def test_a_job_can_only_be_linked_to_an_open_task_of_ones_own(self):
        mine, theirs = self.task(), self.task(self.lab)
        code, r = self.post("/v1/jarvis/actions", {"tool": "output", "op": "save", "target": "a.md", "params": {"content": "x", "task": theirs}}, self.owner)
        self.assertEqual(code, 409)
        self.act("update", mine, {"state": "cancelled"}, self.owner)
        code, r = self.post("/v1/jarvis/actions", {"tool": "output", "op": "save", "target": "a.md", "params": {"content": "x", "task": mine}}, self.owner)
        self.assertEqual(code, 409)

    def test_a_repeated_completion_is_refused_cleanly(self):
        tid = self.task()
        j = self.job(task=tid)
        self.assertEqual(self.complete(tid, [j])[0], 200)
        events = len(self.row(tid)["events"])
        code, r = self.complete(tid, [j])
        self.assertEqual((code, r["reason"]), (409, "task is already completed"))
        self.assertEqual(len(self.row(tid)["events"]), events)                         # nothing written twice
        audit = [r[0] for r in self.k.db.execute("SELECT kind FROM audit WHERE kind='task_completed'").fetchall()]
        self.assertEqual(len(audit), 1)

    def test_a_failed_independent_check_cannot_be_overridden_with_evidence(self):
        tid = self.task()
        Tasks(self.k).update(None, tid, verification={"passed": False, "verifier": "lab-runner", "problems": ["tests fail"]})
        code, r = self.complete(tid, [self.job(task=tid)])
        self.assertEqual(code, 409); self.assertIn("unmet criteria", r["reason"])

    def runner_job(self, criteria):
        tid = self.act("create", "t", {"goal": "A tool", "criteria": criteria}, self.lab)[1]["task"]
        self.assertEqual(self.act("dispatch", tid, {"instructions": "build it"}, self.lab)[0], 200)
        job = self.post("/v1/jarvis/runner/next", {}, self.runner)[1]["job"]["id"]
        for body in ({"state": "running", "attempt": True}, {"state": "verifying"}):
            self.post("/v1/jarvis/runner/report", {"job": job, **body}, self.runner)
        return tid, job

    def test_the_runner_path_still_completes_and_records_the_verifier(self):
        crit = ["tests pass", "files delivered"]
        tid, job = self.runner_job(crit)
        code, r = self.post("/v1/jarvis/runner/report", {"job": job, "state": "completed", "verification": lab_verdict(tid, job, crit),
                                                          "trusted_execution": lab_trusted(tid, job)}, self.runner)
        self.assertEqual((code, r["state"]), (200, "completed"))
        v = self.row(tid)["verification"]
        self.assertEqual((v["passed"], v["verifier"], v["lab_job"], v["assurance"]), (True, "lab-runner", job, "runner_executed_tests"))
        self.assertEqual(v["trusted_execution"]["tests_run"], 3)

    def test_the_runner_cannot_complete_on_incomplete_criteria(self):
        crit = ["tests pass", "files delivered"]
        tid, job = self.runner_job(crit)
        half = lab_verdict(tid, job, crit, rows=[{"criterion": "tests pass", "met": True}, {"criterion": "files delivered", "met": False}])
        code, r = self.post("/v1/jarvis/runner/report", {"job": job, "state": "completed", "verification": half,
                                                          "trusted_execution": lab_trusted(tid, job)}, self.runner)
        self.assertEqual(code, 409); self.assertIn("files delivered", r["reason"])
        g = self.row(tid)
        self.assertEqual((g["state"], g["verification"]["passed"]), ("failed", False)); self.assertIn("not completed", g["result"])
        tid, job = self.runner_job(crit)
        one = lab_verdict(tid, job, crit, rows=[{"criterion": "tests pass", "met": True}])
        self.assertEqual(self.post("/v1/jarvis/runner/report", {"job": job, "state": "completed", "verification": one,
                                                                "trusted_execution": lab_trusted(tid, job)}, self.runner)[0], 409)
        self.assertEqual(self.row(tid)["state"], "failed")
        tid, job = self.runner_job(["tests pass"])
        self.assertEqual(self.post("/v1/jarvis/runner/report", {"job": job, "state": "completed"}, self.runner)[0], 409)   # no verdict
        self.assertEqual(self.post("/v1/jarvis/runner/report", {"job": job, "state": "completed",
                                                                "verification": lab_verdict(tid, job, ["tests pass"], passed=False)}, self.runner)[0], 409)
        self.assertEqual(self.row(tid)["state"], "verifying")

    # ---- audit M11 (2026-10-18): a job that only looks at or shows something is not evidence that the outcome exists
    def test_observation_only_jobs_are_not_evidence_of_an_outcome(self):
        tid = self.task()
        code, r = self.post("/v1/jarvis/actions", {"tool": "output", "op": "show", "target": f"n{time.time()}.md", "params": {"task": tid}}, self.owner)
        self.assertEqual(code, 200, r)
        shown = r["job"]
        self.k.db.execute("UPDATE jobs SET state='done', finished=? WHERE id=?", (time.time() + 1, shown))
        code, r = self.complete(tid, [shown])
        self.assertEqual(code, 409)
        self.assertTrue(any("only looks at or shows something" in p for p in r["problems"]), r["problems"])
        self.assertEqual(self.row(tid)["state"], "running")
        saved = self.job(task=tid)                                      # a saved output (the deliverable path) is still accepted ...
        self.assertEqual(self.complete(tid, [saved, shown])[0], 409)    # ... but a false citation spoils the claim, as before
        code, r = self.complete(tid, [saved])
        self.assertEqual(code, 200)
        v = self.row(tid)["verification"]
        self.assertNotIn("passed", v); self.assertIs(v["criteria_checked"], False)       # still "NOT verified yet", never a pass

    # ---- audit M8 (2026-10-18): a forged or stale verdict is not a pass; the JOB's criteria snapshot decides what is checked
    def rows(self, *criteria):
        return [{"criterion": c, "met": True, "evidence": "ran it"} for c in criteria]

    def report(self, job, **body):
        return self.post("/v1/jarvis/runner/report", {"job": job, **body}, self.runner)

    def ok(self, tid, job, criteria, **body):
        """A verified completion as the real runner sends it."""
        return self.report(job, state="completed", verification=lab_verdict(tid, job, criteria, **body.pop("v", {})),
                           trusted_execution=lab_trusted(tid, job), **body)

    def test_a_bare_passed_true_is_rejected_when_the_job_has_criteria(self):
        crit = ["tests pass", "files delivered"]
        tid, job = self.runner_job(crit)
        code, r = self.report(job, state="completed", verification=lab_verdict(tid, job, crit, rows=False),
                              trusted_execution=lab_trusted(tid, job))
        self.assertEqual(code, 409); self.assertIn("no per-criterion report", r["reason"])
        g = self.row(tid)
        self.assertEqual((g["state"], g["verification"]["passed"]), ("failed", False))
        # also when it is only RECORDED (no completion): it must not be stored as a pass an agent could later cite
        tid, job = self.runner_job(crit)
        self.assertEqual(self.report(job, verification=lab_verdict(tid, job, crit, rows=False), trusted_execution=lab_trusted(tid, job))[0], 200)
        v = self.row(tid)["verification"]
        self.assertIs(v["passed"], False); self.assertIn("no per-criterion report", v["kernel_check"])
        self.assertEqual(self.act("update", tid, {"state": "completed", "result": "done"}, self.lab)[0], 409)
        self.assertEqual(self.row(tid)["state"], "verifying")

    def test_duplicated_rows_do_not_stand_in_for_missing_criteria(self):
        crit = ["tests pass", "files delivered"]
        tid, job = self.runner_job(crit)
        code, r = self.report(job, state="completed", verification=lab_verdict(tid, job, crit, rows=self.rows("tests pass", "tests pass")),
                              trusted_execution=lab_trusted(tid, job))
        self.assertEqual(code, 409); self.assertIn("only 1 of 2", r["reason"])

    def test_rewriting_the_tasks_criteria_while_the_lab_runs_changes_nothing(self):
        crit = ["tests pass", "files delivered"]
        tid, job = self.runner_job(crit)
        self.assertEqual(self.act("update", tid, {"criteria": ["anything"]}, self.lab)[0], 200)      # the agent shortens them
        self.assertEqual(self.row(tid)["criteria"], ["anything"])
        code, r = self.report(job, state="completed", verification=lab_verdict(tid, job, crit, rows=self.rows("anything")),
                              trusted_execution=lab_trusted(tid, job))
        self.assertEqual(code, 409); self.assertIn("only 1 of 2", r["reason"])                     # the job's snapshot (2) decides
        self.assertEqual(self.row(tid)["state"], "failed")
        tid, job = self.runner_job(crit)
        self.act("update", tid, {"criteria": []}, self.lab)                                           # or empties them
        self.assertEqual(self.report(job, state="completed", verification=lab_verdict(tid, job, crit, rows=False),
                                     trusted_execution=lab_trusted(tid, job))[0], 409)
        tid, job = self.runner_job(crit)
        self.act("update", tid, {"criteria": ["anything", "else", "too"]}, self.lab)                  # or invents more: not required either
        code, r = self.ok(tid, job, crit)
        self.assertEqual((code, r["state"]), (200, "completed"))
        # a verdict made for other criteria than the job's (a hash that is not the snapshot's) is refused before anything is recorded
        tid, job = self.runner_job(crit)
        v = lab_verdict(tid, job, ["something else"])
        code, r = self.report(job, state="completed", verification=v, trusted_execution=lab_trusted(tid, job))
        self.assertEqual(code, 409); self.assertIn("criteria mismatch", r["reason"])
        self.assertEqual(self.row(tid)["state"], "verifying")

    def test_a_verdict_for_another_task_or_job_is_refused_and_recorded_nowhere(self):
        crit = ["tests pass"]
        tid, job = self.runner_job(crit)
        other, ojob = self.runner_job(crit)
        for wrong in ({"task": other}, {"job": ojob}, {"task": "0" * 12}, {"task": None}):
            code, r = self.report(job, state="completed", verification={**lab_verdict(tid, job, crit), **wrong}, trusted_execution=lab_trusted(tid, job))
            self.assertEqual(code, 409, wrong); self.assertIn("task identity mismatch", r["reason"])
        v = lab_verdict(tid, job, crit)
        v.pop("task")                                                    # an identity that is missing is not an identity
        self.assertEqual(self.report(job, state="completed", verification=v, trusted_execution=lab_trusted(tid, job))[0], 409)
        self.assertEqual((self.row(tid)["state"], self.row(tid)["verification"]), ("verifying", {}))
        self.assertEqual(self.row(other)["state"], "verifying")
        # the right ids are accepted
        self.assertEqual(self.ok(tid, job, crit)[0], 200)

    def test_a_late_verdict_for_a_closed_task_changes_nothing(self):
        crit = ["tests pass"]
        tid, job = self.runner_job(crit)
        self.assertEqual(self.act("update", tid, {"state": "cancelled"}, self.lab)[0], 200)
        before = self.row(tid)
        good = lab_verdict(tid, job, crit)
        self.assertEqual(self.report(job, state="completed", verification=good, trusted_execution=lab_trusted(tid, job))[0], 409)
        self.assertEqual(self.report(job, verification=good, trusted_execution=lab_trusted(tid, job))[0], 409)
        after = self.row(tid)
        self.assertEqual((after["state"], after["verification"]), (before["state"], before["verification"]))
        tid, job = self.runner_job(crit)                              # and after the task completed, a second verdict is refused
        self.assertEqual(self.ok(tid, job, crit)[0], 200)
        self.assertEqual(self.ok(tid, job, crit)[0], 409)
        self.assertEqual(self.post("/v1/jarvis/runner/finish", {"job": job, "result": {"ok": True}}, self.runner)[0], 200)
        self.assertEqual(self.ok(tid, job, crit)[0], 404)             # the job is over: no running lab job


if __name__ == "__main__":
    unittest.main()
