"""Self-correction, end to end, with no model in the loop: the REAL lab runner (`handle`), the REAL kernel (disposable) and
the REAL node delivery code, against a scripted worker that ships a real bug on attempt 1 and fixes it only when the
runner feeds the real failure back to it, and a verifier that really runs the delivered tests.

Runs inside the WSL lab box as root (the runner needs the `agent` user and `runuser`):
    wsl -d JarvisLab -u root -- bash -c 'cd /mnt/d/jarvis/hq/lab/tests && PYTHONPATH=/mnt/d/jarvis/hq/lab:/mnt/d/jarvis/hq/kernel:/mnt/d/jarvis/hq/node \
        python3 -m unittest test_repair_loop -v'
"""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
for p in (os.path.join(HERE, ".."), os.path.join(HERE, "..", "..", "kernel"), os.path.join(HERE, "..", "..", "node")):
    sys.path.insert(0, p)
try:
    import lab_runner as R
except (ImportError, KeyError):
    R = None

BUGGY = '''import re


def slugify(title):
    return re.sub(r"[^a-z0-9]+", "-", title).strip("-")
'''
FIXED = '''import re


def slugify(title):
    return re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
'''
TESTS = '''import unittest
from slugify import slugify


class T(unittest.TestCase):
    def test_lowercases(self):
        self.assertEqual(slugify("Hello World"), "hello-world")

    def test_punctuation(self):
        self.assertEqual(slugify("  A, b & C!  "), "a-b-c")


if __name__ == "__main__":
    unittest.main()
'''


@unittest.skipUnless(R is not None and R.PW is not None and hasattr(os, "geteuid") and os.geteuid() == 0, "lab box only, as root")
class RepairLoop(unittest.TestCase):
    CRITERIA = ["slugify lowercases and joins words with hyphens", "tests pass", "files are delivered to the owner"]

    def setUp(self):
        from jarvis_kernel.jobs import Jobs
        from jarvis_kernel.llm_proxy import make_handler
        from jarvis_kernel.store import Kernel
        self.tmp = tempfile.mkdtemp(prefix="repair-")
        os.chmod(self.tmp, 0o755)
        self.k = Kernel(os.path.join(self.tmp, "kernel"))
        H, tokens = make_handler(self.k, [], owner_hermes=None)
        self.plane = H.owner_plane
        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.srv.server_address[1]}"
        self.runner_tok, self.agent_tok = tokens.issue("runner", "runner"), tokens.issue("hermes", "private")
        self.jobs = Jobs(self.k)
        self.outputs = os.path.join(self.tmp, "outputs")
        os.makedirs(self.outputs)
        self.work = os.path.join(self.tmp, "work")
        os.makedirs(self.work)
        os.chown(self.work, R.PW.pw_uid, R.PW.pw_gid)
        self.calls = []                                   # (kind, attempt) of each scripted agent call
        self.prompts = []
        self.stop = threading.Event()
        self.laptop = threading.Thread(target=self._laptop, daemon=True)
        self.laptop.start()
        # point the real runner at the disposable kernel and a private work folder
        self.saved = (R.api, R.run_agent, R.WORK, R.POLL_S)
        R.api, R.run_agent, R.WORK, R.POLL_S = self._api, self._run_agent, self.work, 0.2
        # Lab isolation (2026-10-19): with the sandbox and verify users present the run is really separated; the OWNER's statement that the
        # verifier's tools are sandboxed is made here (this scripted verifier is test code), so the kernel may verify. Without those users the
        # run is not separated and the kernel keeps it as 'completed, NOT verified' (isolation none): the assertions below follow.
        import lab_isolation as ISO
        self.ISO, self.iso_saved = ISO, (ISO.REQUEST, ISO.VERIFIER_TOOLS)
        self.isolated = ISO._getpw(ISO.SANDBOX_USER) is not None and ISO._getpw(ISO.VERIFY_USER) is not None
        ISO.REQUEST, ISO.VERIFIER_TOOLS = ("identity" if self.isolated else "none"), "sandboxed"

    def tearDown(self):
        R.api, R.run_agent, R.WORK, R.POLL_S = self.saved
        self.ISO.REQUEST, self.ISO.VERIFIER_TOOLS = self.iso_saved
        self.stop.set()
        self.srv.shutdown()
        self.k.db.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    # ---- the disposable world
    def _api(self, path, body=None):
        r = urllib.request.Request(self.url + path, data=json.dumps(body or {}).encode(), method="POST",
                                   headers={"Content-Type": "application/json", "Authorization": "Bearer " + self.runner_tok})
        try:
            with urllib.request.urlopen(r, timeout=30) as resp:
                return resp.status, json.loads(resp.read() or b"{}")
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read() or b"{}")

    def _agent(self, body):
        r = urllib.request.Request(self.url + "/v1/jarvis/actions", data=json.dumps(body).encode(), method="POST",
                                   headers={"Content-Type": "application/json", "Authorization": "Bearer " + self.agent_tok})
        try:
            with urllib.request.urlopen(r, timeout=30) as resp:
                return resp.status, json.loads(resp.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())

    def _laptop(self):
        """The owner's laptop: the real node code saves each delivered file and answers the kernel."""
        from jarvis_node.jobs import run_job
        os.environ["JARVIS_OUTPUTS"] = self.outputs
        while not self.stop.is_set():
            j = self.jobs.next_for("laptop")
            if not j:
                time.sleep(0.1)
                continue
            self.jobs.finish("laptop", j["id"], run_job({"kind": j["kind"], "params": j["params"]}))

    def _own(self, path):
        os.chown(path, R.PW.pw_uid, R.PW.pw_gid)

    def _write(self, folder, rel, text):
        p = os.path.join(folder, rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w") as fh:
            fh.write(text)
        self._own(os.path.dirname(p)); self._own(p)

    def _run_agent(self, job, kind, prompt, folder, timeout, log_name, capture=None, on_exit=None, user=None, cwd=None):
        self.prompts.append(prompt)
        if prompt.startswith("You are an independent verifier"):
            self.calls.append(("verify", None))
            d = tempfile.mkdtemp(prefix="v-", dir=self.tmp)
            for n in os.listdir(os.path.join(folder, R.DELIVER)):
                shutil.copy(os.path.join(folder, R.DELIVER, n), d)
            r = subprocess.run([sys.executable, "-m", "unittest"], cwd=d, capture_output=True, text=True)    # a real check, as the verifier would
            tail = (r.stderr or r.stdout).strip().splitlines()[-4:]
            verdict = {"passed": r.returncode == 0, "failure_class": None if r.returncode == 0 else "tests",
                       # one row per acceptance criterion of the job (the kernel counts them; audit M8)
                       "criteria": [{"criterion": c, "met": r.returncode == 0, "evidence": " | ".join(tail)[:300]} for c in self.CRITERIA],
                       "problems": [] if r.returncode == 0 else ["the delivered tests fail: " + " | ".join(tail)[:280]]}
            # audit M8: the runner names a fresh, random verdict file and a nonce; the verdict counts only if it carries them
            m = re.search(r'Then write (\S+) exactly like \{"nonce": "([0-9a-f]+)", "task": "([^"]+)", "job": "([^"]+)", "attempt": (\d+)', prompt)
            verdict.update(nonce=m.group(2), task=m.group(3), job=m.group(4), attempt=int(m.group(5)))
            # the slot file is made by the runner for the agent user and must be written IN PLACE, once, without changing its owner or mode
            # (the runner's write audit voids any other change; audit M8, second pass)
            with open(m.group(1), "w") as fh:
                fh.write(json.dumps(verdict))
            capture.feed(R.VERDICT_MARK + " " + json.dumps(verdict) + "\n")     # re-review H2: the final answer is the authority
            return 0, 0.1, ""
        attempt = 1 + sum(1 for c in self.calls if c[0] == "work")
        self.calls.append(("work", attempt))
        seen_failure = "the delivered tests fail" in prompt or "AssertionError" in prompt
        fixes = getattr(self, "fixes", True)
        code = FIXED if (attempt > 1 and seen_failure and fixes) else BUGGY
        self._write(folder, f"{R.DELIVER}/slugify.py", code)
        self._write(folder, f"{R.DELIVER}/test_slugify.py", TESTS)
        self._write(folder, "EVIDENCE.md", "All tests pass (this claim is not trusted).\n")
        return 0, 0.1, ""

    def _task(self, max_attempts=3):
        code, r = self._agent({"tool": "task", "op": "create", "target": "t", "params": {
            "request": "Build slugify.py with tests and deliver the files", "goal": "Create slugify.py with tests",
            "criteria": ["slugify lowercases and joins words with hyphens", "tests pass", "files are delivered to the owner"], "max_attempts": max_attempts}})
        self.assertEqual(code, 200, r)
        tid = r["task"]
        code, r = self._agent({"tool": "task", "op": "dispatch", "target": tid, "params": {
            "instructions": "Write slugify.py and test_slugify.py, run the tests, and deliver the files.", "agent": "hermes", "public_ok": True,
            "criteria": ["slugify lowercases and joins words with hyphens", "tests pass", "files are delivered to the owner"]}})
        # from the private lane the owner approves exactly what is shared with the public lab first (audit SEC-D5, 2026-10-18)
        self.assertEqual((code, r.get("status")), (202, "needs_approval"), r)
        self.assertTrue(self.k.approve(r["approval_id"]))
        code, r = self._agent({"tool": "task", "op": "dispatch", "target": tid, "params": {"public_ok": True},
                               "approval_id": r["approval_id"]})
        self.assertEqual(code, 200, r)
        return tid, r["job"]

    def _run(self, jid, tid):
        code, r = self._api("/v1/jarvis/runner/next")
        self.assertEqual(r["job"]["id"], jid)
        R.handle(r["job"])
        return self.plane.tasks.get(tid)

    # ---- the cases
    def test_a_real_failure_is_observed_repaired_reverified_and_delivered(self):
        self.fixes = True
        tid, jid = self._task(3)
        t = self._run(jid, tid)
        self.assertEqual(t["state"], "completed", t["result"])
        self.assertEqual(t["attempts"], 2)                                            # one repair, not more
        if self.isolated:
            self.assertTrue(t["verification"]["passed"])
            self.assertEqual(t["verification"]["isolation"]["mode"], "full")
        else:
            self.assertNotIn("passed", t["verification"])
            self.assertEqual((t["verification"]["agent_assessed_pass"], t["verification"]["isolation"]["mode"]), (True, "none"))
        hist = t["verification"]["history"]
        self.assertEqual([h["passed"] for h in hist], [False, True])                  # the first check really failed
        states = [e["state"] for e in reversed(t["events"])]
        self.assertIn("repairing", states)
        self.assertLess(states.index("repairing"), states.index("completed"))
        # the real failure text reached the second attempt's prompt
        worker_prompts = [p for p in self.prompts if not p.startswith("You are an independent verifier")]
        self.assertIn("An independent check of the previous attempt found these problems", worker_prompts[1])
        self.assertIn("the delivered tests fail", worker_prompts[1])
        self.assertNotIn("previous attempt found", worker_prompts[0])
        # the corrected artifact (and only it) reached the laptop, confirmed
        delivered = os.path.join(self.outputs, f"task-{tid}")
        self.assertEqual(sorted(os.listdir(delivered)), ["slugify.py", "test_slugify.py"])
        self.assertIn("title.lower()", open(os.path.join(delivered, "slugify.py")).read())
        self.assertTrue(any("delivered:" in e for e in t["evidence"]))
        # the delivered files pass from a clean folder, run by this test and not by the lab
        d = tempfile.mkdtemp(dir=self.tmp)
        for n in os.listdir(delivered):
            shutil.copy(os.path.join(delivered, n), d)
        self.assertEqual(subprocess.run([sys.executable, "-m", "unittest"], cwd=d, capture_output=True).returncode, 0)
        if not self.isolated:
            return                                                                    # a 'NOT verified' pass teaches nothing (below needs a verified one)
        # a lesson is proposed from what the task recorded, and passes the gate
        made = self.plane.capture_lessons()
        self.assertEqual(len(made), 1)
        cand = self.plane.memories.candidates()[0]
        self.assertIn("the delivered tests fail", cand["text"])
        res = self.plane.memories.decide_lesson("hermes", cand["id"], self.plane.tasks.get(tid), True)
        self.assertEqual(res["state"], "active", res["checks"])

    def test_a_task_the_owner_later_corrects_cannot_teach(self):
        self.fixes = True
        tid, jid = self._task(3)
        t = self._run(jid, tid)
        self.assertEqual(t["state"], "completed")
        if not self.isolated:
            self.skipTest("a lesson needs a verified repair, which needs the sandbox and verify users")
        made = self.plane.capture_lessons()                                          # a candidate exists from the verified repair
        self.assertEqual(len(made), 1)
        self.plane.tasks._event(tid, "completed", "correction", "Owner-side check: criterion 3 NOT met after all")   # the owner's check disproves it
        res = self.plane.memories.decide_lesson("hermes", made[0], self.plane.tasks.get(tid), True)
        self.assertEqual(res["state"], "candidate")                                   # it stays out of every session
        self.assertIn("not_corrected", [c["check"] for c in res["checks"] if not c["ok"]])
        self.assertEqual(self.plane.memories.list("hermes"), [])

    def test_if_the_repair_never_works_the_task_fails_honestly_and_nothing_is_delivered(self):
        self.fixes = False
        tid, jid = self._task(2)
        t = self._run(jid, tid)
        self.assertEqual(t["state"], "failed")
        self.assertEqual(t["attempts"], 2)                                            # bounded: exactly the budget
        self.assertIn("not verified after 2 attempt(s)", t["result"])
        self.assertFalse(t["verification"]["passed"])
        self.assertFalse(os.path.exists(os.path.join(self.outputs, f"task-{tid}")))   # a failed task delivers nothing
        self.assertEqual(self.plane.capture_lessons(), [])                            # and teaches nothing
        self.assertEqual(sum(1 for c in self.calls if c[0] == "work"), 2)


if __name__ == "__main__":
    unittest.main()
