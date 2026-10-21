"""The build-failure scenario (projectfix, 2026-10-18), end to end on a disposable fixture project.

"Inspect my project, identify why the build is failing, fix the root cause, run the relevant tests, and report what was
verified." Real: the kernel (tasks, jobs, approvals, policy, audit, kill switch), its HTTP runner endpoints and
dispatch_to_lab with the owner's sharing approval, the node's project_check and project_apply (git, the project's own
unittest run, repair backups) and the kernel's verification. Not real: the lab. ScriptedLab below is a SCRIPTED STAND-IN
FOR THE LAB MODEL (and for lab_runner.py, which needs the WSL lab box): it applies a known edit instead of asking a model.
The fixture is a tiny git repository in a temporary folder with a real bug in calc.py; nothing else is touched. No network.
"""
import hashlib
import json
import os
import re
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
from jarvis_kernel.jobs import JOB_TTL, JobError, Jobs, action_for  # noqa: E402
from jarvis_kernel.llm_proxy import make_handler  # noqa: E402
from jarvis_kernel.policy import decide  # noqa: E402
from jarvis_kernel.projectfix import AGENT, ProjectFix, patch_files  # noqa: E402
from jarvis_kernel.store import Kernel  # noqa: E402
from jarvis_kernel.tasks import Tasks  # noqa: E402
from jarvis_node import jobs as NJ  # noqa: E402
from jarvis_node import repair  # noqa: E402

BUG = "def add(a, b):\n    return a - b\n\n\ndef mul(a, b):\n    return a * b\n"
TESTS = ("import unittest\n\nfrom calc import add, mul\n\n\nclass Calc(unittest.TestCase):\n"
         "    def test_add(self):\n        self.assertEqual(add(2, 3), 5)\n\n    def test_mul(self):\n        self.assertEqual(mul(2, 3), 6)\n")
CMD = ["{python}", "-B", "-I", "-m", "unittest", "discover", "-s", "tests", "-t", "."]


def git(root, *a, check=True):
    return subprocess.run(["git", "-C", root, "-c", "user.name=t", "-c", "user.email=t@t", *a], capture_output=True, text=True, check=check)


def make_project(root):
    os.makedirs(os.path.join(root, "tests"))
    files = {"calc.py": BUG, "util.py": "def greet(name):\n    return 'hi ' + name\n", "README.md": "# demo\n",
             "tests/__init__.py": "", "tests/test_calc.py": TESTS}
    for name, text in files.items():
        with open(os.path.join(root, name), "w", newline="\n") as fh:
            fh.write(text)
    git(root, "init", "-q", "-b", "main"); git(root, "add", "-A"); git(root, "commit", "-q", "-m", "calc with a bug")


def read(path):
    with open(path) as fh:
        return fh.read()


def edit(path, old, new):
    text = read(path)
    assert old in text, (path, old)
    with open(path, "w", newline="\n") as fh:
        fh.write(text.replace(old, new, 1))


def the_fix(work):                       # the known correct fix: the one line that is wrong
    edit(os.path.join(work, "calc.py"), "return a - b", "return a + b")


class ScriptedLab:
    """SCRIPTED STAND-IN FOR THE LAB MODEL (and the lab runner around it). It takes the next lab job from the real kernel
    with the runner's narrow token, clones the project at the commit the instructions name (the caretaker pattern),
    applies `change` (a known edit, no model), runs the project's tests in its clone, delivers deliver/projectfix.json
    through the runner's normal delivery and reports a verdict. `claim` overrides the verdict: a lying lab says "passed"."""

    def __init__(self, t, change, root_cause="calc.py: add() subtracts (a - b) instead of adding (a + b)", claim=None):
        self.t, self.change, self.root_cause, self.claim = t, change, root_cause, claim

    def run_one(self):
        t = self.t
        job = t.post("/v1/jarvis/runner/next", {}, t.runner)[1]["job"]
        if not job:
            return None
        jid, p = job["id"], job["params"]
        shown = re.search(r"(?m)^clone: (.+)$", p["instructions"]).group(1)
        if os.name == "nt":      # the product shows the lab no owner path: a Windows drive path is masked
            t.assertEqual(shown, "<path on the laptop>")
            clone = t.root       # the scripted stand-in knows where its clone is
        else:
            clone = shown
        base = re.search(r"(?m)^commit: ([0-9a-f]{40})$", p["instructions"]).group(1)

        def report(body):
            code, r = t.post("/v1/jarvis/runner/report", {"job": jid, **body}, t.runner)
            return code, r
        report({"state": "running", "attempt": True, "step": "attempt 1 (scripted stand-in for the lab model)"})
        work = os.path.join(t.tmp.name, "lab", jid)
        subprocess.run(["git", "clone", "-q", clone, work], check=True, capture_output=True)
        git(work, "checkout", "-q", base)
        self.change(work)
        patch = git(work, "diff").stdout
        files = [f for f in git(work, "diff", "--name-only").stdout.splitlines() if f]
        ran = subprocess.run([sys.executable, "-B", "-I", "-m", "unittest", "discover", "-s", "tests", "-t", "."], cwd=work,
                             capture_output=True, text=True)
        passed = ran.returncode == 0 if self.claim is None else self.claim
        report({"state": "verifying"})
        content = json.dumps({"base": base, "patch": patch, "files": files, "root_cause": self.root_cause})
        code, r = t.post("/v1/jarvis/runner/deliver", {"job": jid, "name": "projectfix.json", "content": content}, t.runner)
        assert code == 200, r
        t.pump()                                                         # the laptop saves the delivery (real node code)
        # what the real runner stamps on a verdict (lab verdict audit, 2026-10-18): the identity the kernel dispatched, the criteria hash,
        # the artifact digest, and the runner's OWN run of the tests. A lying lab (claim=True) fabricates a clean run on top of its claim:
        # the kernel's independent project check on the laptop has to catch it (the lab-side evidence cannot be re-run by the kernel).
        crit_hash = hashlib.sha256(json.dumps([str(c) for c in p["criteria"]], ensure_ascii=True, separators=(",", ":")).encode()).hexdigest()
        verdict = {"passed": passed, "criteria": [{"criterion": c, "met": passed} for c in p["criteria"]], "task": p["task"], "job": jid,
                   "attempt": 1, "criteria_sha256": crit_hash, "artifact_sha256": "a" * 64, "harness_sha256": "e" * 64,
                   "collected_ids_sha256": "f" * 64, "verdict_channel_sha256": "9" * 64}
        trusted = {"ran_by": "lab-runner", "command": "python3 lab harness (unittest discover, test*.py)", "harness": "lab-harness/2",
                   "exit_code": 0 if passed else ran.returncode, "collect_exit_code": 0, "tests_run": 2, "tests_collected": 2, "tests_passed": 2,
                   "tests_failed": 0, "tests_errored": 0, "tests_skipped": 0, "tests_expected_failures": 0, "leftover_processes": 0,
                   "collected_ids_sha256": "f" * 64, "harness_sha256": "e" * 64,
                   "output_sha256": "b" * 64, "files_sha256": "c" * 64, "artifact_sha256": "a" * 64, "task": p["task"], "job": jid, "attempt": 1,
                   "isolation": {"mode": "full", "backend": "identity", "self_test": "ok", "verifier_tools_isolated": True,
                                 "separate_verifier_identity": True, "sandbox_uid": 995, "verify_uid": 1001, "runner_uid": 0, "processes": 4,
                                 "granularity": "id", "cpu_seconds": 0.3, "reasons": []}}
        if passed:
            report({"state": "completed", "result": "fix delivered", "verification": verdict, "trusted_execution": trusted,
                    "evidence": ["MET: tests pass in the clone"]})
        else:
            report({"state": "failed", "result": "the tests still fail in the clone", "verification": verdict})
        t.post("/v1/jarvis/runner/finish", {"job": jid, "result": {"ok": bool(passed), "summary": "scripted"}}, t.runner)
        return jid


class _Fixture(unittest.TestCase):
    """The kernel with its real HTTP handler, the fixture project and the laptop node (no tests of its own)."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.k = Kernel(os.path.join(self.tmp.name, "kernel"))
        H, tokens = make_handler(self.k, [])
        self.H, self.tokens = H, tokens
        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.srv.server_address[1]}"
        self.runner = tokens.issue("lab-runner", "runner")
        self.T, self.J = Tasks(self.k), Jobs(self.k)
        self.pf = ProjectFix(self.k, self.T, self.J, H.owner_plane.automations.dispatch)     # the real dispatch_to_lab
        self.root = os.path.join(self.tmp.name, "demo")
        make_project(self.root)
        self.projects = {"demo": {"path": {os.name: self.root}, "test": {"cwd": ".", "cmd": CMD, "timeout_s": 60},
                                  "lab_clone": self.root}}
        self.env = {k: os.environ.get(k) for k in ("JARVIS_OUTPUTS", "HOME")}
        os.environ["JARVIS_OUTPUTS"] = os.path.join(self.tmp.name, "outputs")
        self.backups, repair.BACKUPS = repair.BACKUPS, os.path.join(self.tmp.name, "backups")

    def tearDown(self):
        repair.BACKUPS = self.backups
        for k, v in self.env.items():
            os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)
        self.srv.shutdown(); self.srv.server_close(); self.k.db.close(); winclean.cleanup(self.tmp)

    # ---- the pieces outside the kernel
    def post(self, path, body, tok):
        r = urllib.request.Request(self.url + path, data=json.dumps(body).encode(), method="POST",
                                   headers={"Content-Type": "application/json", "Authorization": "Bearer " + tok})
        try:
            with urllib.request.urlopen(r, timeout=30) as resp:
                return resp.status, json.loads(resp.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read() or b"{}")

    def get(self, path, tok):
        r = urllib.request.Request(self.url + path, headers={"Authorization": "Bearer " + tok})
        try:
            with urllib.request.urlopen(r, timeout=30) as resp:
                return resp.status, json.loads(resp.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read() or b"{}")

    def pump(self, node="laptop"):
        """The laptop node: every queued job runs through the REAL node implementation (run_job), as its poll loop does."""
        ran = []
        while True:
            j = self.J.next_for(node)
            if not j:
                return ran
            self.J.finish(node, j["id"], NJ.run_job(j, self.projects))
            ran.append(j["kind"])

    def calc(self):
        return read(os.path.join(self.root, "calc.py"))

    def changed(self):
        return sorted(NJ._porcelain(git(self.root, "status", "--porcelain").stdout))


class Scenario(_Fixture):

    def to_lab(self, lab):
        """start -> inspect on the laptop -> the owner approves sharing -> the lab works -> its fix is offered."""
        labs = self.k.db.execute("SELECT COUNT(*) FROM jobs WHERE kind='lab_run'").fetchone()[0]
        r = self.pf.start("demo", "Inspect my project, identify why the build is failing, fix the root cause, run the relevant "
                                  "tests, and report what was verified.")
        tid = r["task"]
        self.assertIn("waiting for the laptop", r["waiting"])
        self.assertEqual(self.pump(), ["project_check"])
        r = self.pf.advance(tid)
        aid = r["approvals"]["share_approval"]
        self.assertIn("waiting for your approval", r["waiting"])
        self.assertEqual(self.T.get(tid)["state"], "waiting_approval")
        self.assertEqual(self.k.db.execute("SELECT COUNT(*) FROM jobs WHERE kind='lab_run'").fetchone()[0], labs)   # nothing shared yet
        self.assertIn("waiting for your approval", self.pf.advance(tid)["waiting"])                              # no approval: waits
        self.assertTrue(self.k.approve(aid))
        r = self.pf.advance(tid)
        self.assertIn("the lab", r["waiting"]); self.assertEqual(r["stage"], "lab")
        self.assertIsNotNone(lab.run_one())
        return tid, self.pf.advance(tid)

    # ---- the scenario
    def test_the_build_is_fixed_verified_by_the_kernel_and_reported(self):
        tid, r = self.to_lab(ScriptedLab(self, the_fix))
        inspect = self.J.get_for(AGENT, r["jobs"]["inspect_job"])["result"]
        self.assertEqual(inspect["tests"]["exit_code"], 1)
        self.assertTrue(any("test_add" in ln for ln in inspect["tests"]["failed"]), inspect["tests"])  # real failing output
        self.assertEqual(r["stage"], "approve"); self.assertIn("calc.py (+1 -1 lines", r["waiting"])
        self.assertEqual(self.calc(), BUG)                                   # nothing applied before the owner approves
        aid = r["approvals"]["apply_approval"]
        card = next(a for a in self.k.pending_approvals() if a["id"] == aid)
        self.assertEqual((card["level"], card["action"]["tool"], card["action"]["op"]), ("YELLOW_APPROVAL", "project", "apply_patch"))
        self.assertEqual(card["action"]["params"]["files"], ["calc.py"])
        # review M6: the card shows the patch itself (what the digest binds) and the root cause, not only file names and a digest
        self.assertIn("-    return a - b", card["action"]["card"]["patch"]); self.assertIn("+    return a + b", card["action"]["card"]["patch"])
        self.assertIn("calc.py: add() subtracts", card["action"]["card"]["root_cause"])
        self.assertFalse(card["truncated"])
        self.assertIn("waiting for your approval", self.pf.advance(tid)["waiting"])      # still no approval: still waits
        self.assertTrue(self.k.approve(aid))
        r = self.pf.advance(tid)
        self.assertIn("scratch copy", r["waiting"])
        self.assertEqual(self.pump(), ["project_apply"])
        r = self.pf.advance(tid)
        self.assertIn("re-running the tests", r["waiting"])
        self.assertEqual(self.pump(), ["project_check"])
        r = self.pf.advance(tid)
        t = self.T.get(tid)
        self.assertEqual((t["state"], r["verified"], r["outcome"]), ("completed", True, "fixed and verified"))
        v = t["verification"]
        self.assertEqual((v["verifier"], v["passed"]), ("kernel", True))
        self.assertTrue(all(c["met"] for c in v["criteria"]) and len(v["criteria"]) == 4, v)
        self.assertIn("return a + b", self.calc()); self.assertEqual(self.changed(), ["calc.py"])
        for want in (r["jobs"]["inspect_job"], r["jobs"]["apply_job"], r["jobs"]["verify_job"], "Ran 2 tests", "OK",
                     "calc.py: add() subtracts", "+1 -1", "Undo: repair restore", "not committed"):
            self.assertIn(want, r["text"])
        # the undo really puts the old file back (repair restore; the owner's folder is under HOME here)
        os.environ["HOME"] = self.tmp.name
        bid = self.T.get(tid)["result"].split("repair restore ")[1].split(".")[0].split(";")[0].strip()
        self.assertTrue(repair.restore(bid)["ok"]); self.assertEqual(self.calc(), BUG)
        audit = " ".join(x[0] for x in self.k.db.execute("SELECT kind FROM audit").fetchall())
        self.assertIn("projectfix_verified", audit)

    def test_a_fix_that_does_not_make_the_tests_pass_is_not_completed(self):
        wrong = lambda w: edit(os.path.join(w, "calc.py"), "return a - b", "return a + b + 1")   # noqa: E731
        tid, r = self.to_lab(ScriptedLab(self, wrong, claim=True))           # the lab CLAIMS it passed
        self.assertTrue(self.k.approve(r["approvals"]["apply_approval"]))
        self.pf.advance(tid); self.pump()
        r = self.pf.advance(tid)
        t = self.T.get(tid)
        self.assertEqual(t["state"], "failed"); self.assertFalse(r["verified"])
        self.assertIn("does not make the tests pass", t["result"]); self.assertIn("nothing in your project was changed", t["result"])
        self.assertEqual(self.calc(), BUG); self.assertEqual(self.changed(), [])
        self.assertFalse(os.path.exists(repair.BACKUPS) and os.listdir(repair.BACKUPS))

    def test_an_honest_lab_that_cannot_fix_it_ends_the_task_failed(self):
        wrong = lambda w: edit(os.path.join(w, "calc.py"), "return a - b", "return a * b")   # noqa: E731
        tid, r = self.to_lab(ScriptedLab(self, wrong))
        self.assertEqual(self.T.get(tid)["state"], "failed"); self.assertIn("did not produce a verified fix", r["text"])
        self.assertEqual(self.calc(), BUG)

    def test_a_worker_that_changes_unrelated_files_is_refused(self):
        def also_util(w):
            the_fix(w); edit(os.path.join(w, "util.py"), "'hi '", "'hello '")
        # (a) a file its root cause does not even mention: refused before the owner is asked
        tid, r = self.to_lab(ScriptedLab(self, also_util))
        self.assertEqual(self.T.get(tid)["state"], "failed"); self.assertIn("does not mention (util.py)", r["text"])
        self.assertEqual(r["approvals"].get("apply_approval"), None)
        # (b) it names the file too: the scratch-copy proof shows the change is not needed; nothing is applied
        git(self.root, "status")
        tid, r = self.to_lab(ScriptedLab(self, also_util, root_cause="calc.py: add() subtracts; util.py: greeting tidied"))
        self.assertTrue(self.k.approve(r["approvals"]["apply_approval"]))
        self.pf.advance(tid); self.pump()
        r = self.pf.advance(tid)
        self.assertEqual(self.T.get(tid)["state"], "failed")
        self.assertIn("tests pass without the change to util.py", r["text"])
        self.assertEqual(self.calc(), BUG); self.assertEqual(self.changed(), [])
        # (c) it changes the tests to make them pass: refused, never offered
        weaken = lambda w: edit(os.path.join(w, "tests", "test_calc.py"), "add(2, 3), 5", "add(2, 3), -1")   # noqa: E731
        tid, r = self.to_lab(ScriptedLab(self, weaken, root_cause="tests/test_calc.py expected the wrong value"))
        self.assertEqual(self.T.get(tid)["state"], "failed"); self.assertIn("changes test files", r["text"])
        self.assertEqual(read(os.path.join(self.root, "tests", "test_calc.py")), TESTS)

    def test_the_owner_declining_either_approval_changes_nothing(self):
        r = self.pf.start("demo"); tid = r["task"]; self.pump()
        aid = self.pf.advance(tid)["approvals"]["share_approval"]
        self.assertTrue(self.k.deny(aid))
        r = self.pf.advance(tid)
        self.assertEqual(self.T.get(tid)["state"], "failed"); self.assertIn("you declined sharing", r["text"])
        self.assertEqual(self.k.db.execute("SELECT COUNT(*) FROM jobs WHERE kind='lab_run'").fetchone()[0], 0)
        tid, r = self.to_lab(ScriptedLab(self, the_fix))
        self.assertTrue(self.k.deny(r["approvals"]["apply_approval"]))
        r = self.pf.advance(tid)
        self.assertEqual(self.T.get(tid)["state"], "failed"); self.assertIn("you declined applying", r["text"])
        self.assertEqual(self.pump(), []); self.assertEqual(self.calc(), BUG)

    def test_a_project_that_is_not_allowlisted_is_refused(self):
        r = self.pf.start("secret-project")
        self.pump()                                                          # the laptop's allowlist has no such project
        r = self.pf.advance(r["task"])
        t = self.T.get(r["task"])
        self.assertEqual(t["state"], "failed"); self.assertIn("not in the laptop's allowlist", t["result"])
        self.assertIsNone(r["lab_task"]); self.assertEqual(self.k.pending_approvals(), [])

    def test_the_lab_unavailable_fails_honestly(self):
        r = self.pf.start("demo"); tid = r["task"]; self.pump()
        self.k.approve(self.pf.advance(tid)["approvals"]["share_approval"])
        r = self.pf.advance(tid)
        self.assertIn("has not picked it up yet", r["waiting"])            # nothing runs a lab here
        self.J.sweep(now=time.time() + JOB_TTL + 1)                          # 30 minutes later the job expires unclaimed
        r = self.pf.advance(tid)
        t = self.T.get(tid)
        self.assertEqual(t["state"], "failed"); self.assertIn("the lab is unavailable", t["result"])
        self.assertIn("WSL lab box", t["result"]); self.assertEqual(self.calc(), BUG)

    def test_a_project_the_lab_cannot_clone_fails_honestly(self):
        del self.projects["demo"]["lab_clone"]
        r = self.pf.start("demo"); self.pump()
        r = self.pf.advance(r["task"])
        self.assertEqual(self.T.get(r["task"])["state"], "failed"); self.assertIn("no lab_clone", r["text"])
        self.assertEqual(self.k.pending_approvals(), [])

    def test_passing_tests_need_no_fix(self):
        the_fix(self.root); git(self.root, "commit", "-qam", "fixed by hand")
        r = self.pf.start("demo"); self.pump()
        r = self.pf.advance(r["task"])
        self.assertEqual(self.T.get(r["task"])["state"], "completed"); self.assertIn("Nothing to fix", r["text"])

    def test_the_kill_switch_stops_the_scenario_where_it_is(self):
        r = self.pf.start("demo"); tid = r["task"]; self.pump()
        aid = self.pf.advance(tid)["approvals"]["share_approval"]
        self.k.approve(aid); self.k.kill("test")
        r = self.pf.advance(tid)
        self.assertIn("kill switch", r["waiting"])
        self.assertEqual(self.k.db.execute("SELECT COUNT(*) FROM jobs WHERE kind='lab_run'").fetchone()[0], 0)


OWNER = "/v1/jarvis/owner/"
PIN = "246810"


class OwnerRoutes(_Fixture):
    """The procedure as the owner reaches it in production (2026-10-18): make_handler builds ONE ProjectFix on the owner plane,
    the owner starts it, approves its steps (PIN) and reads its report over the real owner HTTP routes, and the owner's tick
    moves it on. The lab is ScriptedLab: a SCRIPTED STAND-IN FOR THE LAB MODEL (see above). Nothing else is faked."""

    def setUp(self):
        super().setUp()
        self.owner = self.tokens.issue("console", "owner")
        self.H.owner_plane.set_pin(PIN)
        self.pf = self.H.owner_plane.projectfix

    def approve(self, aid):
        return self.post(OWNER + "approve", {"id": aid, "confirm": aid, "pin": PIN}, self.owner)

    def report(self, tid):
        code, r = self.get(OWNER + f"project_fix?task={tid}", self.owner)
        self.assertEqual(code, 200, r)
        return r

    def tick(self):
        return self.H.owner_plane.tick()["projectfix"]

    def test_production_wiring(self):
        from jarvis_kernel.owner import Owner
        self.assertIsInstance(self.pf, ProjectFix)
        self.assertIs(self.pf.dispatch, self.H.owner_plane.automations.dispatch)           # the one guarded path to the lab
        self.assertEqual(self.pf.node, self.H.owner_plane.node)
        for route in (("POST", "project_fix"), ("POST", "project_fix/advance"), ("GET", "project_fix")):
            self.assertIn(route, self.H.owner_plane.routes)
        self.assertTrue(callable(getattr(Owner, "advance_projectfixes", None)))

    def test_the_build_is_fixed_through_the_owner_routes_and_the_tick(self):
        code, r = self.post(OWNER + "project_fix", {"project": "demo", "request": "Inspect my project, identify why the build is "
                            "failing, fix the root cause, run the relevant tests, and report what was verified."}, self.owner)
        self.assertEqual(code, 201, r)
        tid = r["task"]
        self.assertIn("waiting for the laptop", r["waiting"])
        self.assertEqual(self.pump(), ["project_check"])
        self.assertEqual(self.tick()["advanced"], 1)                                       # the tick moves it on
        r = self.report(tid)
        self.assertEqual(r["stage"], "share"); aid = r["approvals"]["share_approval"]
        self.assertEqual(self.k.db.execute("SELECT COUNT(*) FROM jobs WHERE kind='lab_run'").fetchone()[0], 0)   # nothing shared
        self.assertEqual(self.tick(), {"advanced": 0, "skipped": 1, "errors": 0})          # an unanswered approval is not re-asked
        self.assertEqual(self.post(OWNER + "approve", {"id": aid, "confirm": aid, "pin": "000000"}, self.owner)[0], 403)
        self.assertEqual(self.approve(aid), (200, {"approved": aid}))                     # the owner's PIN, over HTTP
        code, r = self.post(OWNER + "project_fix/advance", {"task": tid}, self.owner)
        self.assertEqual((code, r["stage"]), (200, "lab"), r)
        self.assertIsNotNone(ScriptedLab(self, the_fix).run_one())
        self.tick()
        r = self.report(tid)
        self.assertEqual(r["stage"], "approve"); aid = r["approvals"]["apply_approval"]
        card = next(a for a in self.k.pending_approvals() if a["id"] == aid)
        self.assertEqual((card["level"], card["action"]["tool"], card["action"]["op"]), ("YELLOW_APPROVAL", "project", "apply_patch"))
        self.tick(); self.assertEqual(self.pump(), []); self.assertEqual(self.calc(), BUG)  # nothing applied without the owner
        self.assertEqual(self.approve(aid)[0], 200)
        self.tick()
        self.assertEqual(self.pump(), ["project_apply"])
        self.tick()
        self.assertEqual(self.pump(), ["project_check"])
        self.tick()
        r = self.report(tid)
        self.assertEqual((r["state"], r["stage"], r["verified"], r["outcome"]), ("completed", "done", True, "fixed and verified"))
        self.assertIn("return a + b", self.calc()); self.assertEqual(self.changed(), ["calc.py"])
        self.assertEqual(self.tick()["advanced"], 0)                                       # a finished fix is left alone
        code, lst = self.get(OWNER + "project_fix", self.owner)
        self.assertEqual((code, [x["task"] for x in lst["project_fixes"]]), (200, [tid]))
        audit = [x[0] for x in self.k.db.execute("SELECT kind FROM audit").fetchall()]
        self.assertIn("owner_projectfix", audit); self.assertIn("projectfix_verified", audit)

    def test_only_the_owner_reaches_the_routes(self):
        agent, lab = self.tokens.issue("hermes", "private"), self.tokens.issue("lab", "public")
        for tok in (agent, lab, self.runner):
            self.assertEqual(self.post(OWNER + "project_fix", {"project": "demo"}, tok)[0], 403)
            self.assertEqual(self.get(OWNER + "project_fix", tok)[0], 403)
        self.assertEqual(self.k.db.execute("SELECT COUNT(*) FROM projectfix").fetchone()[0], 0)
        self.assertEqual(self.k.db.execute("SELECT COUNT(*) FROM jobs").fetchone()[0], 0)
        code, r = self.post("/v1/jarvis/actions", {"action": "project_fix", "project": "demo"}, agent)   # no agent action either
        self.assertNotEqual(code, 200)
        self.assertEqual(self.k.db.execute("SELECT COUNT(*) FROM projectfix").fetchone()[0], 0)

    def test_bad_requests_are_refused_plainly(self):
        self.assertEqual(self.post(OWNER + "project_fix", {"project": "../etc"}, self.owner)[0], 400)
        self.assertEqual(self.post(OWNER + "project_fix", {"project": "demo", "request": ["x"]}, self.owner)[0], 400)
        self.assertEqual(self.post(OWNER + "project_fix/advance", {"task": "nope"}, self.owner)[0], 400)
        self.assertEqual(self.post(OWNER + "project_fix/advance", {"task": "0" * 12}, self.owner)[0], 404)
        self.assertEqual(self.get(OWNER + "project_fix?task=" + "0" * 12, self.owner)[0], 404)
        self.k.kill("test")
        code, r = self.post(OWNER + "project_fix", {"project": "demo"}, self.owner)
        self.assertEqual(code, 503, r)
        states = [x[0] for x in self.k.db.execute("SELECT state FROM tasks WHERE owner='projectfix'").fetchall()]
        self.assertTrue(all(st == "failed" for st in states), states)                       # never left sitting in planning

    def test_one_broken_fix_does_not_stop_the_tick_and_the_tick_is_bounded(self):
        from jarvis_kernel import owner as owner_mod
        tids = [self.post(OWNER + "project_fix", {"project": "demo"}, self.owner)[1]["task"] for _ in range(owner_mod.PROJECTFIX_TICK + 2)]
        while True:          # the laptop's answer, written directly (12 real test runs would only make this slow): tests pass
            j = self.J.next_for("laptop")
            if not j:
                break
            self.J.finish("laptop", j["id"], {"ok": True, "git": {"head": "a" * 40, "branch": "main", "changed": [], "dirty_files": 0},
                                              "tests": {"ran": True, "exit_code": 0, "summary": "OK"}})
        self.k.db.execute("UPDATE projectfix SET stage='no-such-stage' WHERE task=?", (tids[0],))   # a broken record
        seen, real = [], self.pf.advance
        self.pf.advance = lambda tid: (seen.append(tid), real(tid))[1]
        out = self.tick()
        self.assertEqual(len(seen), owner_mod.PROJECTFIX_TICK, out)                         # bounded
        self.assertEqual((out["errors"], out["advanced"]), (1, owner_mod.PROJECTFIX_TICK - 1), out)
        self.assertEqual(self.report(tids[1])["stage"], "done")                             # the next one still moved on
        audit = [x[0] for x in self.k.db.execute("SELECT kind FROM audit").fetchall()]
        self.assertIn("projectfix_tick_error", audit)
        seen.clear(); self.tick()
        self.assertTrue(set(tids[-2:]) <= set(seen), seen)                                  # the rest get their turn next tick

    def test_a_fix_the_owner_cancels_or_pauses_is_not_moved_on(self):
        a = self.post(OWNER + "project_fix", {"project": "demo"}, self.owner)[1]["task"]
        b = self.post(OWNER + "project_fix", {"project": "demo"}, self.owner)[1]["task"]
        self.pump()
        self.assertEqual(self.post(OWNER + "task", {"id": a, "op": "cancel"}, self.owner)[0], 200)
        self.assertEqual(self.post(OWNER + "task", {"id": b, "op": "pause"}, self.owner)[0], 200)
        self.tick()
        self.assertEqual((self.report(a)["stage"], self.report(a)["state"]), ("failed", "cancelled"))
        self.assertEqual(self.report(b)["stage"], "inspect")                                # paused: it waits where it was
        self.assertEqual(self.k.pending_approvals(), [])                                   # nothing asked for either
        self.assertEqual(self.tick()["advanced"], 1)                                       # only b, which stays paused
        self.assertEqual(self.report(b)["stage"], "inspect")


class ApplyJob(unittest.TestCase):
    """The new job kind on its own: the kernel's parameter and policy checks, and the node's refusals."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.root = os.path.join(self.tmp.name, "demo"); make_project(self.root)
        self.projects = {"demo": {"path": {os.name: self.root}, "test": {"cwd": ".", "cmd": CMD, "timeout_s": 60}}}
        edit(os.path.join(self.root, "calc.py"), "return a - b", "return a + b")
        self.patch = git(self.root, "diff").stdout
        git(self.root, "checkout", "-q", "--", ".")
        self.head = git(self.root, "rev-parse", "HEAD").stdout.strip()
        self.backups, repair.BACKUPS = repair.BACKUPS, os.path.join(self.tmp.name, "backups")

    def tearDown(self):
        repair.BACKUPS = self.backups; winclean.cleanup(self.tmp)

    def params(self, **over):
        import hashlib
        p = {"project": "demo", "base": self.head, "patch": self.patch, "digest": hashlib.sha256(self.patch.encode()).hexdigest(),
             "files": ["calc.py"]}
        p.update(over)
        return p

    def test_kernel_parameters_and_policy(self):
        k = Kernel(os.path.join(self.tmp.name, "k"))
        try:
            J = Jobs(k)
            for bad in (self.params(digest="0" * 64), self.params(files=["../calc.py"]), self.params(files=["/etc/passwd"]),
                        self.params(files=[f"f{i}.py" for i in range(6)]), self.params(base="HEAD"), self.params(extra=1)):
                with self.assertRaises(JobError, msg=str(bad)[:80]):
                    J.create("laptop", "project_apply", bad, owner=AGENT)
            with self.assertRaises(JobError) as e:                                   # never without the owner's approval
                J.create("laptop", "project_apply", self.params(), owner=AGENT)
            self.assertIn("approval required", str(e.exception))
            levels = {f: decide(action_for("project_apply", self.params(files=[f]), "private", untrusted=True)).name
                      for f in ("calc.py", "src/.env", "jarvis/kernel/jarvis_kernel/policy.py")}
            self.assertEqual(levels, {"calc.py": "YELLOW_APPROVAL", "src/.env": "DENY", "jarvis/kernel/jarvis_kernel/policy.py": "RED"})
            a = action_for("project_apply", self.params(), "private", untrusted=True, requester=AGENT)   # as projectfix asks (SEC-D3)
            d, aid = k.request(a)
            k.approve(aid)
            other = self.params(patch=self.patch.replace("a + b", "a + b + 0"))
            import hashlib
            other["digest"] = hashlib.sha256(other["patch"].encode()).hexdigest()
            with self.assertRaises(JobError):                                        # the approval is for exactly that patch
                J.create("laptop", "project_apply", other, approval_id=aid, owner=AGENT)
            self.assertTrue(J.create("laptop", "project_apply", self.params(), approval_id=aid, owner=AGENT))
        finally:
            k.db.close()

    def test_apply_patch_rule_a_projects_own_files_versus_jarvis_itself(self):
        """project.apply_patch (2026-10-18): a project's own store.py, policy.py or settings.json is an ordinary approval (as
        fs.write it was RED, so a fix to such a file could never be approved from the console's PIN); secrets stay DENY;
        agent-rule files stay RED in any project; JARVIS's own safety files stay RED when the project is JARVIS itself."""
        from jarvis_kernel.jobs import KINDS
        self.assertEqual(KINDS["project_apply"], ("project", "apply_patch"))

        def level(project, f):
            return decide(action_for("project_apply", self.params(project=project, files=[f]), "private", untrusted=True)).name
        cases = {
            ("demo", "store.py"): "YELLOW_APPROVAL", ("demo", "src/policy.py"): "YELLOW_APPROVAL",
            ("demo", "audit.py"): "YELLOW_APPROVAL", ("demo", "config/settings.json"): "YELLOW_APPROVAL",
            ("demo", "providers.json"): "YELLOW_APPROVAL", ("demo", "calc.py"): "YELLOW_APPROVAL",
            ("demo", ".env"): "DENY", ("demo", "src/.env.local"): "DENY", ("demo", "config/secrets.json"): "DENY",
            ("demo", "deploy/key.pem"): "DENY",
            ("demo", "CLAUDE.md"): "RED", ("demo", ".claude/settings.json"): "RED", ("demo", "hooks.json"): "RED",
            ("demo", "logs/auth.log"): "RED",
            ("demo", "jarvis/kernel/jarvis_kernel/policy.py"): "RED", ("demo", "kernel/jarvis_kernel/store.py"): "RED",
            ("jarvis-hq", "kernel/jarvis_kernel/store.py"): "RED", ("jarvis-hq", "config/providers.json"): "RED",
            ("jarvis-hq", "config/sensitive_sites.json"): "RED", ("hq-neko", "plugin/x/settings.json"): "RED",
            ("jarvis-hq", "README.md"): "YELLOW_APPROVAL", ("jarvis-hq", ".env"): "DENY",
        }
        got = {c: level(*c) for c in cases}
        self.assertEqual(got, cases)
        # the rule binds the patch: one approval is for exactly this patch and these files (digest in the action)
        a = action_for("project_apply", self.params(), "private", untrusted=True)
        self.assertEqual((a.tool, a.op, a.params["digest"]), ("project", "apply_patch", self.params()["digest"]))

    def test_node_refusals_change_nothing(self):
        self.assertIn("digest", NJ.project_apply(self.params(digest="0" * 64), self.projects)["error"])
        self.assertIn("allowlist", NJ.project_apply(self.params(project="other"), self.projects)["error"])
        with open(os.path.join(self.root, "calc.py"), "a") as fh:
            fh.write("# my own edit\n")
        self.assertIn("uncommitted changes in calc.py", NJ.project_apply(self.params(), self.projects)["error"])
        git(self.root, "commit", "-qam", "owner moved on")
        self.assertIn("the project moved", NJ.project_apply(self.params(), self.projects)["error"])
        self.assertIn("refused", NJ.project_apply(self.params(files=["calc.py", "util.py"]), self.projects)["error"])
        self.assertFalse(os.path.exists(repair.BACKUPS))

    def test_patch_files_accepts_only_plain_changes_of_existing_files(self):
        self.assertEqual(patch_files(self.patch), (["calc.py"], ""))
        for bad in ("", "hello\n" + self.patch, self.patch.replace("diff --git a/calc.py b/calc.py", "diff --git a/calc.py b/other.py"),
                    self.patch.replace("index ", "new file mode 100644\nindex "), self.patch + self.patch):
            self.assertEqual(patch_files(bad)[0], [], bad[:60])

    def test_project_check_reports_the_commit_the_changed_files_and_the_failures(self):
        r = NJ.project_check("demo", dict(self.projects, demo=dict(self.projects["demo"], lab_clone="https://example.invalid/demo.git")))
        self.assertEqual((r["git"]["head"], r["git"]["changed"], r["lab_clone"]), (self.head, [], "https://example.invalid/demo.git"))
        self.assertEqual(r["tests"]["exit_code"], 1); self.assertTrue(r["tests"]["failed"][0].startswith("FAIL: test_add"))


if __name__ == "__main__":
    unittest.main()
