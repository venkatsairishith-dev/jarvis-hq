"""The kernel's half of the lab-verdict defence (audit M8 residual, 2026-10-18), and the contract with the real lab runner.

The lab verifier shares its OS user with the code it runs, so its verdict FILE can be forged (see jarvis/lab/tests/test_verifier_forker.py
and jarvis/caretaker/README.md). The kernel therefore never lets a verdict make a task "verified" on the file's word:
  - the report must name the exact task, job and attempt the kernel dispatched and counted, the hash of the job's own criteria, and
    arrive while the task is in verification; each (job, attempt) takes ONE verdict (a replayed, duplicated or late report is refused);
  - "verified" (passed true) needs `trusted_execution`: the runner's own run of the delivered tests (exit 0, at least one test) on the
    artifact the verdict names, in a field the verifier cannot write. Without it a pass survives only as "completed, NOT verified"
    (`agent_assessed`) and only when the runner says no runnable tests exist.
Real: the kernel's HTTP handler, sqlite and tasks. The last class also runs the real lab_runner.handle against it.
"""
import json
import os
import pwd
import re
import shutil
import sys
import tempfile
import threading
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))
sys.path.insert(0, os.path.join(HERE, "..", "..", "lab"))
from jarvis_kernel.jobs import Jobs                                        # noqa: E402
from jarvis_kernel.tasks import Tasks, assure_verdict, criteria_sha256, trusted_problem   # noqa: E402
from tests.test_tasks import Base, lab_trusted, lab_verdict, ART, HARNESS_SHA, IDS_SHA, CHAN_SHA    # noqa: E402

EVIDENCE = {"artifact_sha256": ART, "harness_sha256": HARNESS_SHA, "collected_ids_sha256": IDS_SHA, "verdict_channel_sha256": CHAN_SHA}

CRIT = ["tests pass", "files delivered"]


class Pure(unittest.TestCase):
    def test_trusted_problem_names_each_way_the_record_can_be_wrong(self):
        ver = dict(EVIDENCE)
        good = lab_trusted("t" * 12, "j1", 1)
        self.assertEqual(trusted_problem(good, ver, "t" * 12, "j1", 1), "")
        bad = [({"ran_by": "verifier"}, "not recorded by the lab runner"), ({"task": "x"}, "another task"), ({"job": "j2"}, "another job"),
               ({"attempt": 2}, "another attempt"), ({"exit_code": 1}, "did not exit 0"), ({"exit_code": False}, "did not exit 0"),
               ({"exit_code": None}, "did not exit 0"), ({"tests_run": 0}, "executed no tests"), ({"tests_run": True}, "executed no tests"),
               ({"tests_run": "3"}, "executed no tests"), ({"files_sha256": "zz"}, "files_sha256"), ({"output_sha256": None}, "output_sha256"),
               ({"artifact_sha256": "d" * 64}, "other files"), ({"artifact_sha256": "nope"}, "artifact_sha256")]
        for over, word in bad:
            self.assertIn(word, trusted_problem({**good, **over}, ver, "t" * 12, "j1", 1), over)
        self.assertIn("not an object", trusted_problem("ok", ver, "t" * 12, "j1", 1))

    def test_trusted_problem_names_each_way_the_harness_evidence_can_be_wrong(self):
        """Re-review H2 (2026-10-18): the test count is the runner's own count from its harness, bound to the verdict that rests on it."""
        tid = "t" * 12
        ver = lab_verdict(tid, "j1", CRIT)
        good = lab_trusted(tid, "j1", 1)
        self.assertEqual(trusted_problem(good, ver, tid, "j1", 1), "")
        bad = [({"harness": None}, "test harness"), ({"harness": "lab-harness/0"}, "test harness"),
               ({"collect_exit_code": 1}, "collection step"), ({"collect_exit_code": None}, "collect_exit_code"),
               ({"tests_collected": 4}, "inconsistent"), ({"tests_passed": 2}, "inconsistent"),
               ({"tests_passed": 2, "tests_failed": 1}, "failures"), ({"tests_passed": 2, "tests_errored": 1}, "failures"),
               ({"tests_passed": 0, "tests_skipped": 3}, "passed no test"), ({"leftover_processes": 1}, "left processes"),
               ({"tests_run": 3, "tests_collected": "3"}, "tests_collected"), ({"tests_failed": True}, "tests_failed"),
               ({"harness_sha256": "zz"}, "harness_sha256"), ({"harness_sha256": "1" * 64}, "another harness_sha256"),
               ({"collected_ids_sha256": "1" * 64}, "another collected_ids_sha256"), ({"collected_ids_sha256": None}, "collected_ids_sha256")]
        for over, word in bad:
            self.assertIn(word, trusted_problem({**good, **over}, ver, tid, "j1", 1), over)
        for key in ("harness_sha256", "collected_ids_sha256", "verdict_channel_sha256"):                 # the verdict must name its evidence
            self.assertNotEqual(trusted_problem(good, {k: v for k, v in ver.items() if k != key}, tid, "j1", 1), "", key)
        self.assertIn("output channel", trusted_problem(good, dict(ver, verdict_channel_sha256="nope"), tid, "j1", 1))
        self.assertEqual(trusted_problem({**good, "tests_passed": 2, "tests_skipped": 1}, ver, tid, "j1", 1), "")     # skips are allowed, if one passed

    def test_assure_verdict_keeps_a_pass_only_with_the_runners_evidence(self):
        ver = {"passed": True, "criteria": [], **EVIDENCE}
        tid = "t" * 12
        out, why = assure_verdict(ver, lab_trusted(tid, "j1"), tid, "j1", 1)
        self.assertEqual((out["passed"], out["assurance"], why), (True, "runner_executed_tests", ""))
        out, why = assure_verdict(ver, None, tid, "j1", 1)                          # nothing from the runner: not a pass
        self.assertIs(out["passed"], False); self.assertTrue(why)
        out, why = assure_verdict(dict(ver, agent_assessed=True), None, tid, "j1", 1)    # the runner said no tests exist
        self.assertNotIn("passed", out); self.assertEqual((out["agent_assessed_pass"], out["assurance"], why), (True, "agent_assessed", ""))
        # whatever the verdict FILE carried as evidence is dropped
        out, why = assure_verdict(dict(ver, trusted_execution=lab_trusted(tid, "j1")), None, tid, "j1", 1)
        self.assertIs(out["passed"], False); self.assertNotIn("trusted_execution", out)
        out, why = assure_verdict({"passed": False, "agent_assessed": True}, None, tid, "j1", 1)   # a failing verdict needs no proof
        self.assertEqual(out, {"passed": False}); self.assertEqual(why, "")


class Helpers(Base):
    def runner_job(self, criteria=CRIT, attempts=1):
        tid = self.act("create", "t", {"goal": "A tool " + str(time.time()), "criteria": criteria, "max_attempts": attempts}, self.lab)[1]["task"]
        self.assertEqual(self.act("dispatch", tid, {"instructions": "build it"}, self.lab)[0], 200)
        job = self.post("/v1/jarvis/runner/next", {}, self.runner)[1]["job"]["id"]
        for body in ({"state": "running", "attempt": True}, {"state": "verifying"}):
            self.assertEqual(self.report(job, **body)[0], 200)
        return tid, job

    def report(self, job, **body):
        return self.post("/v1/jarvis/runner/report", {"job": job, **body}, self.runner)

    def row(self, tid):
        return Tasks(self.k).get(tid)

    def complete(self, tid, job, trusted=True, **v):
        body = {"state": "completed", "verification": lab_verdict(tid, job, CRIT, **v)}
        if trusted:
            body["trusted_execution"] = trusted if isinstance(trusted, dict) else lab_trusted(tid, job)
        return self.report(job, **body)



class Reports(Helpers):
    # ---- "verified" needs the runner's own execution
    def test_a_pass_without_the_runners_own_test_run_is_not_verified(self):
        tid, job = self.runner_job()
        code, r = self.complete(tid, job, trusted=False)
        self.assertEqual(code, 409); self.assertIn("trusted_execution", r["reason"])
        g = self.row(tid)
        self.assertEqual(g["state"], "failed")
        self.assertIs(g["verification"]["passed"], False)
        self.assertIn("needs the runner's own execution", g["verification"]["kernel_check"])

    def test_a_pass_the_runner_marks_agent_assessed_completes_as_not_verified(self):
        tid, job = self.runner_job()
        code, r = self.complete(tid, job, trusted=False, agent_assessed=True)
        self.assertEqual((code, r["state"]), (200, "completed"))
        v = self.row(tid)["verification"]
        self.assertNotIn("passed", v)                                   # every screen reads this as "NOT verified yet"
        self.assertEqual((v["assurance"], v["agent_assessed_pass"], v["criteria_checked"]), ("agent_assessed", True, False))
        self.assertNotIn("agent_assessed", v)

    def test_a_trusted_record_inside_the_verdict_file_does_not_count(self):
        tid, job = self.runner_job()
        code, r = self.report(job, state="completed", verification=lab_verdict(tid, job, CRIT, trusted_execution=lab_trusted(tid, job)))
        self.assertEqual(code, 409)
        self.assertIs(self.row(tid)["verification"]["passed"], False)

    def test_a_bad_trusted_record_fails_the_task_instead_of_verifying_it(self):
        for over in ({"exit_code": 1}, {"tests_run": 0}, {"job": "other"}, {"task": "other"}, {"attempt": 2}, {"artifact_sha256": "d" * 64},
                     {"ran_by": "verifier"}):
            with self.subTest(over=over):
                tid, job = self.runner_job()
                code, r = self.complete(tid, job, trusted={**lab_trusted(tid, job), **over})
                self.assertEqual(code, 409, over)
                g = self.row(tid)
                self.assertEqual(g["state"], "failed")
                self.assertIs(g["verification"]["passed"], False)
                self.assertNotIn("trusted_execution", g["verification"])

    def test_a_pass_whose_evidence_is_not_the_one_the_verdict_names_fails_the_task(self):
        for over in ({"harness_sha256": "1" * 64}, {"collected_ids_sha256": "1" * 64}, {"harness": "old"}, {"tests_collected": 9},
                     {"tests_failed": 1, "tests_passed": 2}, {"leftover_processes": 2}, {"collect_exit_code": 3}):
            with self.subTest(over=over):
                tid, job = self.runner_job()
                code, r = self.complete(tid, job, trusted={**lab_trusted(tid, job), **over})
                self.assertEqual(code, 409, over)
                g = self.row(tid)
                self.assertEqual(g["state"], "failed")
                self.assertIs(g["verification"]["passed"], False)
                self.assertTrue(g["verification"]["kernel_check"])

    def test_a_verdict_that_does_not_name_its_evidence_or_its_output_channel_is_not_verified(self):
        for drop in ("harness_sha256", "collected_ids_sha256", "verdict_channel_sha256"):
            with self.subTest(drop=drop):
                tid, job = self.runner_job()
                v = lab_verdict(tid, job, CRIT)
                v.pop(drop)
                code, r = self.report(job, state="completed", verification=v, trusted_execution=lab_trusted(tid, job))
                self.assertEqual(code, 409, drop)
                self.assertIs(self.row(tid)["verification"]["passed"], False)

    def test_an_older_runner_that_counted_tests_from_their_output_cannot_verify(self):
        tid, job = self.runner_job()
        old = {k: v for k, v in lab_trusted(tid, job).items() if k not in (
            "harness", "collect_exit_code", "tests_collected", "tests_passed", "tests_failed", "tests_errored", "tests_skipped",
            "tests_expected_failures", "leftover_processes", "collected_ids_sha256", "harness_sha256")}
        old["command"] = "python3 -m unittest"
        code, r = self.report(job, state="completed", verification=lab_verdict(tid, job, CRIT), trusted_execution=old)
        self.assertEqual(code, 409)
        self.assertIn("harness", self.row(tid)["verification"]["kernel_check"])
        self.assertEqual(self.row(tid)["state"], "failed")

    # ---- identity, attempt, order, one verdict
    def test_the_verdict_must_name_the_attempt_the_kernel_counted(self):
        for att in (2, 0, "1", True, None):
            with self.subTest(attempt=att):
                tid, job = self.runner_job()
                v = lab_verdict(tid, job, CRIT)
                v["attempt"] = att
                code, r = self.report(job, state="completed", verification=v, trusted_execution=lab_trusted(tid, job))
                self.assertEqual(code, 409); self.assertIn("attempt mismatch", r["reason"])
                self.assertEqual((self.row(tid)["state"], self.row(tid)["verification"]), ("verifying", {}))

    def test_a_verdict_is_refused_unless_the_task_is_being_verified(self):
        tid = self.act("create", "t", {"goal": "A tool x", "criteria": CRIT}, self.lab)[1]["task"]
        self.act("dispatch", tid, {"instructions": "build it"}, self.lab)
        job = self.post("/v1/jarvis/runner/next", {}, self.runner)[1]["job"]["id"]
        self.report(job, state="running", attempt=True)                # running, not verifying
        code, r = self.complete(tid, job)
        self.assertEqual(code, 409); self.assertIn("only while the task is being verified", r["reason"])
        self.assertEqual(self.row(tid)["state"], "running")

    def test_a_second_verdict_for_the_same_attempt_is_refused_whatever_it_says(self):
        tid, job = self.runner_job()
        first = lab_verdict(tid, job, CRIT, passed=False, rows=[{"criterion": c, "met": False} for c in CRIT])
        self.assertEqual(self.report(job, verification=first)[0], 200)          # recorded without a state change
        self.assertIs(self.row(tid)["verification"]["passed"], False)
        code, r = self.complete(tid, job)                                        # a replayed / different report for the same attempt
        self.assertEqual(code, 409); self.assertIn("already has a verdict", r["reason"])
        self.assertEqual(self.row(tid)["state"], "verifying")
        self.assertIs(self.row(tid)["verification"]["passed"], False)           # the first outcome stands
        n = self.k.db.execute("SELECT COUNT(*) FROM audit WHERE kind='lab_verdict_repeated'").fetchone()[0]
        self.assertEqual(n, 1)

    def test_two_reports_at_once_record_one_verdict(self):
        tid, job = self.runner_job()
        codes, gate = [], threading.Barrier(6)

        def send():
            gate.wait()
            codes.append(self.complete(tid, job)[0])
        threads = [threading.Thread(target=send) for _ in range(6)]
        [t.start() for t in threads]; [t.join() for t in threads]
        self.assertEqual(codes.count(200), 1, codes)
        self.assertEqual(self.k.db.execute("SELECT COUNT(*) FROM lab_verdicts WHERE job=?", (job,)).fetchone()[0], 1)
        self.assertEqual(self.row(tid)["state"], "completed")

    def test_an_earlier_attempts_valid_verdict_replayed_later_is_refused(self):
        tid, job = self.runner_job(attempts=2)
        first = lab_verdict(tid, job, CRIT, passed=False, rows=[{"criterion": c, "met": False} for c in CRIT])
        self.assertEqual(self.report(job, verification=first)[0], 200)           # attempt 1: an honest failure, recorded
        self.assertEqual(self.report(job, state="repairing", note="fix it")[0], 200)
        self.assertEqual(self.report(job, state="running", attempt=True)[0], 200)
        self.assertEqual(self.report(job, state="verifying")[0], 200)
        replay = lab_verdict(tid, job, CRIT, attempt=1)                          # the attempt-1 verdict, forged into a pass, sent again
        code, r = self.report(job, state="completed", verification=replay, trusted_execution=lab_trusted(tid, job, 1))
        self.assertEqual(code, 409); self.assertIn("attempt mismatch", r["reason"])
        self.assertEqual(self.row(tid)["state"], "verifying")
        code, r = self.complete(tid, job, trusted=lab_trusted(tid, job, 2), attempt=2)
        self.assertEqual(code, 200, r)                                           # attempt 2 has its own slot

    def test_an_earlier_attempts_trusted_record_replayed_into_a_later_attempt_is_refused(self):
        tid, job = self.runner_job(attempts=2)
        first = lab_verdict(tid, job, CRIT, passed=False, rows=[{"criterion": c, "met": False} for c in CRIT])
        self.assertEqual(self.report(job, verification=first)[0], 200)
        for body in ({"state": "repairing", "note": "fix it"}, {"state": "running", "attempt": True}, {"state": "verifying"}):
            self.assertEqual(self.report(job, **body)[0], 200)
        stale = lab_trusted(tid, job, 1)                                          # the evidence of attempt 1 (same hashes), sent in attempt 2
        code, r = self.report(job, state="completed", verification=lab_verdict(tid, job, CRIT, attempt=2), trusted_execution=stale)
        self.assertEqual(code, 409)
        g = self.row(tid)
        self.assertEqual(g["state"], "failed")
        self.assertIn("another attempt", g["verification"]["kernel_check"])

    def test_a_verdict_arriving_after_the_task_closed_is_refused_whatever_it_carries(self):
        tid, job = self.runner_job()
        self.assertEqual(self.complete(tid, job)[0], 200)
        self.assertEqual(self.row(tid)["state"], "completed")
        code, r = self.complete(tid, job)                                         # the same report again (a replay)
        self.assertEqual(code, 409)
        code, r = self.complete(tid, job, passed=False, rows=[{"criterion": c, "met": False} for c in CRIT])    # a different one
        self.assertEqual(code, 409)
        self.assertEqual(self.post("/v1/jarvis/runner/finish", {"job": job, "result": {"ok": True}}, self.runner)[0], 200)
        code, r = self.complete(tid, job)                                         # after the lab job itself has ended
        self.assertIn(code, (404, 409))
        g = self.row(tid)
        self.assertEqual(g["state"], "completed")
        self.assertIs(g["verification"]["passed"], True)

    def test_a_report_for_another_jobs_task_changes_nothing(self):
        tid, job = self.runner_job()
        other, ojob = self.runner_job()
        code, r = self.report(job, state="completed", verification=lab_verdict(other, ojob, CRIT), trusted_execution=lab_trusted(other, ojob))
        self.assertEqual(code, 409); self.assertIn("identity mismatch", r["reason"])
        self.assertEqual([self.row(tid)["state"], self.row(other)["state"]], ["verifying", "verifying"])

    def test_a_verdict_for_other_criteria_is_refused(self):
        tid, job = self.runner_job()
        v = lab_verdict(tid, job, CRIT)
        v["criteria_sha256"] = criteria_sha256(["only the easy one"])
        code, r = self.report(job, state="completed", verification=v, trusted_execution=lab_trusted(tid, job))
        self.assertEqual(code, 409); self.assertIn("criteria mismatch", r["reason"])
        v.pop("criteria_sha256")
        self.assertEqual(self.report(job, state="completed", verification=v, trusted_execution=lab_trusted(tid, job))[0], 409)

    def test_a_failing_verdict_needs_no_proof_and_is_recorded_once(self):
        tid, job = self.runner_job()
        v = lab_verdict(tid, job, CRIT, passed=False, rows=[{"criterion": c, "met": False} for c in CRIT], problems=["no"])
        self.assertEqual(self.report(job, state="failed", result="not verified", verification=v)[0], 200)
        g = self.row(tid)
        self.assertEqual((g["state"], g["verification"]["passed"]), ("failed", False))

    def test_the_audit_chain_records_who_decided_and_how(self):
        tid, job = self.runner_job()
        self.assertEqual(self.complete(tid, job)[0], 200)
        row = self.k.db.execute("SELECT payload FROM audit WHERE kind='lab_verdict'").fetchone()
        p = json.loads(row[0])
        self.assertEqual((p["assurance"], p["passed"], p["attempt"]), ("runner_executed_tests", True, 1))
        self.assertEqual((p["harness_sha256"], p["verdict_channel_sha256"]), (HARNESS_SHA[:16], CHAN_SHA[:16]))   # the evidence it rests on


@unittest.skipUnless(os.path.isdir("/proc"), "needs /proc")
class RunnerContract(Helpers):
    """The real lab_runner.handle talking to the real kernel over HTTP: what the runner sends is what the kernel accepts. Only the
    agent CLI sessions are scripted (in process); the runner's own run of the delivered tests is real (runuser is a shim off the lab box)."""

    def setUp(self):
        super().setUp()
        import lab_runner as R
        self.R = R
        self.saved = {n: getattr(R, n) for n in ("PW", "WORK", "api", "run_agent", "_fingerprint", "guard_restore", "skill_counts", "POLL_S")}
        self.path0 = os.environ["PATH"]
        self.work = tempfile.mkdtemp(prefix="contract-")
        os.chmod(self.work, 0o755)
        shim = os.path.join(self.work, "bin")
        os.makedirs(shim)
        with open(os.path.join(shim, "runuser"), "w") as fh:
            fh.write("#!/bin/sh\nshift 3\nexec \"$@\"\n")
        os.chmod(os.path.join(shim, "runuser"), 0o755)
        os.environ["PATH"] = shim + os.pathsep + self.path0
        R.PW, R.WORK, R.POLL_S = pwd.getpwuid(os.getuid()), self.work, 0.05
        import lab_isolation as ISO
        self.ISO = ISO
        self.iso_saved = (ISO.VERIFIER_TOOLS, ISO.REQUEST)
        ISO.REQUEST = "none"                                      # this arrangement has no separate identities, whatever machine it runs on
        R.api = lambda path, body=None: self.post(path, body or {}, self.runner)
        R._fingerprint, R.guard_restore, R.skill_counts = (lambda: {}), (lambda before, label: []), (lambda: {})
        R.run_agent = self.agent
        self.stop = threading.Event()
        threading.Thread(target=self.laptop, daemon=True).start()
        self.tests = "pass"          # what the worker delivers: "pass", "fail" or "none"

    def tearDown(self):
        self.stop.set()
        self.R_restore()
        shutil.rmtree(self.work, ignore_errors=True)
        super().tearDown()

    def R_restore(self):
        self.ISO.VERIFIER_TOOLS, self.ISO.REQUEST = self.iso_saved
        os.environ["PATH"] = self.path0
        for n, v in self.saved.items():
            setattr(self.R, n, v)

    def laptop(self):                                       # the owner's laptop confirms every delivery
        J = Jobs(self.k)
        while not self.stop.is_set():
            try:
                n = J.next_for("laptop")
            except Exception:
                n = None
            if n:
                J.finish("laptop", n["id"], {"ok": True, "saved": n["params"]["name"], "folder": "D:\\x", "chars": 1})
            else:
                time.sleep(0.02)

    def agent(self, job, kind, prompt, folder, timeout, log_name, capture=None, on_exit=None, user=None, cwd=None):
        if prompt.startswith("You are an independent verifier"):
            m = re.search(r'Then write (\S+) exactly like \{"nonce": "([0-9a-f]+)", "task": "([^"]+)", "job": "([^"]+)", "attempt": (\d+)', prompt)
            verdict = {"nonce": m.group(2), "task": m.group(3), "job": m.group(4), "attempt": int(m.group(5)), "passed": True,
                       "criteria": [{"criterion": c, "met": True, "evidence": "ran it"} for c in CRIT], "problems": []}
            with open(m.group(1), "w") as fh:
                fh.write(json.dumps(verdict))
            capture.feed(self.R.VERDICT_MARK + " " + json.dumps(verdict) + "\n")      # re-review H2: the final answer is the authority
            return 0, 0.1, ""
        if self.tests == "none":                              # a report only: nothing the runner can run
            os.makedirs(os.path.join(folder, "deliver"), exist_ok=True)
            open(os.path.join(folder, "deliver", "notes.md"), "w").write("# notes\nthe answer\n")
        else:
            os.makedirs(os.path.join(folder, "deliver"), exist_ok=True)
            open(os.path.join(folder, "deliver", "calc.py"), "w").write("def add(a, b):\n    return a + b\n")
            expect = 5 if self.tests == "pass" else 6
            open(os.path.join(folder, "deliver", "test_calc.py"), "w").write(
                f"import unittest\nimport calc\n\nclass T(unittest.TestCase):\n    def test_add(self):\n        self.assertEqual(calc.add(2, 3), {expect})\n")
        return 0, 0.1, ""

    def run_real(self):
        tid = self.act("create", "t", {"goal": "A tool " + str(time.time()), "criteria": CRIT, "max_attempts": 1}, self.lab)[1]["task"]
        self.assertEqual(self.act("dispatch", tid, {"instructions": "build it"}, self.lab)[0], 200)
        job = self.post("/v1/jarvis/runner/next", {}, self.runner)[1]["job"]
        self.R.handle(job)
        return tid, job["id"]

    def test_the_real_runner_without_a_separate_identity_completes_NOT_verified_with_the_reason(self):
        """Lab isolation (2026-10-19): this is the keep-id / non-root arrangement, with no sandbox identity. The delivered tests pass and the kernel
        keeps the pass, but as 'completed, NOT verified' (isolation none): it never verifies on a boundary that is not there."""
        tid, job = self.run_real()
        g = self.row(tid)
        self.assertEqual(g["state"], "completed", g["events"][:3])
        v = g["verification"]
        self.assertNotIn("passed", v)                                           # every screen reads this as "NOT verified yet"
        self.assertEqual((v["assurance"], v["agent_assessed_pass"], v["isolation"]["mode"]), ("unisolated_tests", True, "none"))
        self.assertTrue(v["isolation"]["reasons"])
        self.assertIn("NOT verified", g["result"])
        self.assertEqual((v["trusted_execution"]["exit_code"], v["trusted_execution"]["ran_by"]), (0, "lab-runner"))
        self.assertGreaterEqual(v["trusted_execution"]["tests_run"], 1)
        self.assertEqual(v["trusted_execution"]["artifact_sha256"], v["artifact_sha256"])
        self.assertNotIn("nonce", json.dumps(v))

    def test_the_real_runner_without_tests_completes_as_not_verified(self):
        self.tests = "none"
        tid, job = self.run_real()
        g = self.row(tid)
        self.assertEqual(g["state"], "completed", (g["result"], g["verification"]))
        self.assertNotIn("passed", g["verification"])
        self.assertEqual(g["verification"]["assurance"], "agent_assessed")
        self.assertIn("NOT verified", g["result"])

    def test_the_real_runner_with_failing_tests_fails_the_task_whatever_the_verifier_said(self):
        self.tests = "fail"
        tid, job = self.run_real()
        g = self.row(tid)
        self.assertEqual(g["state"], "failed")
        self.assertIs(g["verification"]["passed"], False)
        self.assertIn("delivered tests fail", " ".join(g["verification"]["problems"]))



def _real_users():
    try:
        return os.geteuid() == 0 and all(pwd.getpwnam(n) for n in ("agent", "jarvis-sandbox", "jarvis-verify"))
    except KeyError:
        return False


@unittest.skipUnless(os.path.isdir("/proc") and _real_users(), "needs root and the users agent, jarvis-sandbox, jarvis-verify (a throw-away container)")
class RunnerContractIsolated(RunnerContract):
    """Lab isolation (2026-10-19), the real arrangement: root runner, real users, real runuser. The runner's own run of the delivered tests is
    done by the sandbox identity, one process per test; the real kernel decides. Only the agent CLI sessions are scripted."""

    def setUp(self):
        super().setUp()
        self.ISO.REQUEST = "identity"
        os.environ["PATH"] = self.path0                          # the real runuser, not the shim
        self.R.PW = pwd.getpwnam("agent")
        os.chmod(self.work, 0o755)

    test_the_real_runner_without_a_separate_identity_completes_NOT_verified_with_the_reason = None      # the keep-id case; not this arrangement

    def agent(self, job, kind, prompt, folder, timeout, log_name, capture=None, on_exit=None, user=None, cwd=None):
        out = super().agent(job, kind, prompt, folder, timeout, log_name, capture=capture, on_exit=on_exit, user=user, cwd=cwd)
        if not prompt.startswith("You are an independent verifier"):
            for root, dirs, files in os.walk(folder):
                for n in dirs + files:
                    os.chown(os.path.join(root, n), self.R.PW.pw_uid, self.R.PW.pw_gid)
        return out

    def test_the_owner_states_the_verifiers_tools_are_sandboxed_and_the_kernel_verifies(self):
        self.ISO.VERIFIER_TOOLS = "sandboxed"
        tid, job = self.run_real()
        g = self.row(tid)
        self.assertEqual(g["state"], "completed", (g["result"], g["verification"]))
        v = g["verification"]
        self.assertIs(v["passed"], True)
        self.assertEqual((v["assurance"], v["isolation"]["mode"], v["isolation"]["backend"], v["isolation"]["self_test"]), ("runner_executed_tests", "full", "identity", "ok"))
        t = v["trusted_execution"]
        self.assertEqual((t["harness"], t["tests_run"], t["tests_passed"], t["leftover_processes"]), ("lab-harness/2", 1, 1, 0))
        self.assertGreaterEqual(v["isolation"]["processes"], 2)                  # a collection process and one process for the one test
        self.assertNotIn(v["isolation"]["sandbox_uid"], (0, v["isolation"]["verify_uid"], v["isolation"]["runner_uid"]))

    def test_without_that_statement_the_tests_pass_in_the_sandbox_but_the_task_is_NOT_verified(self):
        self.ISO.VERIFIER_TOOLS = ""
        tid, job = self.run_real()
        g = self.row(tid)
        self.assertEqual(g["state"], "completed", (g["result"], g["verification"]))
        v = g["verification"]
        self.assertNotIn("passed", v)
        self.assertEqual((v["assurance"], v["isolation"]["mode"]), ("isolated_tests_only", "identity"))
        self.assertIn("NOT verified", g["result"])
        self.assertIn("verifier's own tools", " ".join(v["isolation"]["reasons"]))

    def test_a_missing_sandbox_user_fails_closed_to_NOT_verified(self):
        self.ISO.VERIFIER_TOOLS = "sandboxed"
        saved = self.ISO.SANDBOX_USER
        self.ISO.SANDBOX_USER = "no-such-sandbox-user"
        try:
            tid, job = self.run_real()
        finally:
            self.ISO.SANDBOX_USER = saved
        v = self.row(tid)["verification"]
        self.assertNotIn("passed", v)
        self.assertEqual(v["isolation"]["mode"], "none")
        self.assertIn("does not exist", " ".join(v["isolation"]["reasons"]))

    def test_failing_tests_in_the_sandbox_fail_the_task_whatever_the_verifier_said(self):
        self.ISO.VERIFIER_TOOLS = "sandboxed"
        self.tests = "fail"
        tid, job = self.run_real()
        g = self.row(tid)
        self.assertEqual(g["state"], "failed")
        self.assertIn("delivered tests fail", " ".join(g["verification"]["problems"]))


if __name__ == "__main__":
    unittest.main()
