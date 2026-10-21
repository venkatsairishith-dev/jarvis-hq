"""Who can write a task's "verified" state, and under what condition (LAB-RT, 2026-10-19).

A task is "verified" when its stored verification has passed true (every screen, the owner's morning note, Telegram, memory promotion and the
agent's own "completed" check read exactly that). This file lists EVERY writer of that field and shows, through the real HTTP handler and sqlite,
that the only one a lab can reach needs all of: the runner's token, a verdict that names this task, job and attempt, the runner's own test run
(`trusted_execution`), an isolation block of mode "full" (which itself needs separated execution, a passed self-test, the owner's statement that
the verifier's tools are sandboxed AND a verifier identity of its own), and a per-criterion report that covers the job's criteria.

Writers found by reading the whole kernel (grep for `verification=`, `UPDATE tasks`, `INSERT INTO tasks`):
  1. llm_proxy runner/report  -> tasks.assure_verdict            the lab. Everything above.
  2. llm_proxy task_action update (completion by evidence)       the kernel's own verdict WITHOUT a passed flag ("evidence accepted").
  3. llm_proxy task_action update with `verification`            refused for every agent token (403, audited).
  4. projectfix                                                  the kernel's own re-run of the project's tests on the laptop; no lab involved.
  5. retention                                                   shrinks old rows, keeps booleans already there; never adds one.
  6. owner console / Telegram / CLI / automations / jobs         only change the STATE (cancel, pause, resume, fail); they never pass `verification`.
"""
import json
import os
import re
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))
from jarvis_kernel.tasks import Tasks, assure_verdict, isolation_problem, KERNEL_OWNED_KEYS      # noqa: E402
from tests.test_lab_report import Helpers, CRIT                                                  # noqa: E402
from tests.test_tasks import lab_trusted, lab_verdict, lab_isolation                             # noqa: E402

KERNEL_DIR = os.path.join(HERE, "..", "jarvis_kernel")


def iso(mode):
    """An isolation block as the runner would send it for each mode."""
    if mode == "none":
        return lab_isolation(mode="none", backend="none", self_test="failed: no backend", verifier_tools_isolated=False,
                             separate_verifier_identity=False, sandbox_uid=-1, verify_uid=-1, processes=0, granularity="", reasons=["no boundary"])
    if mode == "identity":
        return lab_isolation(mode="identity", verifier_tools_isolated=False, reasons=["the verifier's own tools are not known to run in a sandbox"])
    if mode == "container":
        return lab_isolation(mode="container", backend="container", verifier_tools_isolated=False)
    return lab_isolation()


class InventoryOfWriters(unittest.TestCase):
    def test_only_the_known_files_write_a_verification(self):
        found = {}
        for name in sorted(os.listdir(KERNEL_DIR)):
            if not name.endswith(".py"):
                continue
            with open(os.path.join(KERNEL_DIR, name), encoding="utf-8") as fh:
                text = fh.read()
            hits = [ln.strip() for ln in text.splitlines() if re.search(r"\bverification\s*=[^=]", ln) and not ln.strip().startswith(("#", "def ", '"""'))
                    and "verification=None" not in ln.replace(" ", "") and "verification=?" not in ln]       # (agents.py: a SQL column of agent definitions)
            hits += [ln.strip() for ln in text.splitlines() if re.search(r"(UPDATE|INSERT INTO) tasks", ln)]
            if hits:
                found[name] = hits
        self.assertEqual(sorted(found), ["llm_proxy.py", "projectfix.py", "retention.py", "tasks.py"],
                         "a new writer of a task's verification: review it against the table in this file's docstring\n" + json.dumps(found, indent=1))

    def test_no_route_outside_the_runner_and_projectfix_passes_a_verdict_dict(self):
        """owner.py, telegram.py, cli.py, jobs.py, automations.py, agents.py, overview.py, memory.py only call tasks.update(..., state=..., note=...)."""
        for name in ("owner.py", "telegram.py", "cli.py", "jobs.py", "automations.py", "agents.py", "memory.py", "overview.py", "traces.py"):
            path = os.path.join(KERNEL_DIR, name)
            if not os.path.exists(path):
                continue
            with open(path, encoding="utf-8") as fh:
                text = fh.read()
            for m in re.finditer(r"\.update\(([^)]*)\)", text):
                self.assertNotIn("verification", m.group(1), f"{name}: {m.group(0)[:100]}")


class WriterMatrix(Helpers):
    def mode_report(self, tid, job, mode, trusted=True, rows=None, **v):
        body = {"state": "completed", "verification": lab_verdict(tid, job, CRIT, rows=rows, **v)}
        if trusted:
            t = lab_trusted(tid, job, isolation=iso(mode))
            if mode == "none":
                t["harness"] = "lab-harness/1"
            body["trusted_execution"] = t
        return self.report(job, **body)

    def test_verified_needs_full_mode_the_runners_test_run_and_every_criterion(self):
        results = {}
        for mode in ("none", "identity", "container", "full"):
            for trusted in (True, False):
                for covered in (True, False):
                    tid, job = self.runner_job()
                    rows = None if covered else [{"criterion": CRIT[0], "met": True, "evidence": "x"}]       # one of two criteria reported
                    code, _ = self.mode_report(tid, job, mode, trusted=trusted, rows=rows)
                    v = self.row(tid)["verification"]
                    results[(mode, trusted, covered)] = (v.get("passed") is True and self.row(tid)["state"] == "completed", code)
        verified = sorted(k for k, (ok, _) in results.items() if ok)
        self.assertEqual(verified, [("full", True, True)], results)
        for key, (ok, code) in results.items():                               # nothing else even reaches 'completed' as a pass
            if key != ("full", True, True):
                self.assertFalse(ok, key)

    def test_the_other_modes_complete_only_as_not_verified_and_never_set_passed(self):
        for mode in ("identity", "container", "none"):
            tid, job = self.runner_job()
            code, r = self.mode_report(tid, job, mode)
            self.assertEqual((code, r.get("state")), (200, "completed"), mode)
            v = self.row(tid)["verification"]
            self.assertNotIn("passed", v)
            self.assertIs(v["agent_assessed_pass"], True)
            self.assertIs(v["criteria_checked"], False)

    def test_full_without_a_verifier_identity_of_its_own_is_not_verified(self):
        for over in ({"separate_verifier_identity": False}, {"verify_uid": 995}, {"verifier_tools_isolated": False}):
            tid, job = self.runner_job()
            t = lab_trusted(tid, job, isolation=lab_isolation(**over))
            code, _ = self.report(job, state="completed", verification=lab_verdict(tid, job, CRIT), trusted_execution=t)
            self.assertEqual(code, 409, over)
            v = self.row(tid)["verification"]
            self.assertIsNot(v.get("passed"), True, over)
            self.assertEqual(self.row(tid)["state"], "failed")

    def test_a_replayed_report_changes_nothing(self):
        tid, job = self.runner_job()
        self.assertEqual(self.mode_report(tid, job, "full")[0], 200)
        first = self.row(tid)
        for _ in range(2):
            code, r = self.mode_report(tid, job, "full")
            self.assertIn(code, (404, 409))                              # refused: the task is closed / the attempt already has its verdict
        self.assertEqual(self.row(tid)["verification"], first["verification"])
        self.assertEqual(self.row(tid)["state"], first["state"])

    def test_a_second_verdict_for_the_same_attempt_cannot_upgrade_a_demotion(self):
        tid, job = self.runner_job(attempts=2)
        self.assertEqual(self.mode_report(tid, job, "identity")[0], 200)
        self.assertIsNot(self.row(tid)["verification"].get("passed"), True)
        code, _ = self.report(job, state="verifying", verification=lab_verdict(tid, job, CRIT),
                                 trusted_execution=lab_trusted(tid, job, isolation=iso("full")))
        self.assertIn(code, (404, 409))
        self.assertIsNot(self.row(tid)["verification"].get("passed"), True)

    def test_a_failing_verdict_cannot_carry_the_kernels_own_assurance_words(self):
        """A report with passed false but agent_assessed_pass true (and the same for assurance / criteria_checked / isolation / kernel_check) used to be
        stored as it came and then closed the task as 'completed, NOT verified' with no test run behind it."""
        for key, value in (("agent_assessed_pass", True), ("assurance", "runner_executed_tests"), ("criteria_checked", True),
                           ("kernel_check", "all good"), ("isolation", iso("full"))):
            tid, job = self.runner_job()
            code, r = self.report(job, state="completed", verification=lab_verdict(tid, job, CRIT, passed=False,
                                                                          rows=[{"criterion": c, "met": True, "evidence": "x"} for c in CRIT], **{key: value}))
            self.assertEqual(code, 409, key)
            v = self.row(tid)["verification"]
            self.assertIsNot(v.get("passed"), True)
            self.assertIsNot(v.get("agent_assessed_pass"), True, key)
            self.assertNotEqual(self.row(tid)["state"], "completed", key)

    def test_assure_verdict_drops_every_kernel_owned_key_a_report_brings(self):
        tid = "t" * 12
        for key in KERNEL_OWNED_KEYS:
            out, _ = assure_verdict({"passed": False, key: "forged"}, None, tid, "j1", 1)
            self.assertNotIn(key, out)

    def test_an_agent_cannot_write_a_verdict_or_complete_without_proof(self):
        tid = self.act("create", "t", {"goal": "A tool x", "criteria": CRIT}, self.lab)[1]["task"]
        code, r = self.act("update", tid, {"verification": {"passed": True, "verifier": "lab-runner"}}, self.lab)
        self.assertEqual(code, 403)
        code, r = self.act("update", tid, {"state": "completed", "evidence": ["it works"]}, self.lab)
        self.assertEqual(code, 409)
        t = Tasks(self.k).get(tid)
        self.assertEqual(t["verification"], {})
        self.assertNotEqual(t["state"], "completed")

    def test_owner_controls_never_set_a_verdict(self):
        tid, job = self.runner_job()
        before = self.row(tid)["verification"]
        from jarvis_kernel.tasks import Tasks as T
        T(self.k).update(None, tid, state="paused", note="paused by the owner (console)")
        T(self.k).update(None, tid, state="cancelled", note="cancelled by the owner (console)")
        after = self.row(tid)
        self.assertEqual(after["verification"], before)
        self.assertNotIn("passed", after["verification"])


class IsolationBlockRules(unittest.TestCase):
    def test_full_needs_a_separate_verifier_identity(self):
        self.assertEqual(isolation_problem(lab_isolation()), "")
        self.assertIn("verifier identity of its own", isolation_problem(lab_isolation(separate_verifier_identity=False)))
        self.assertIn("verifier identity of its own", isolation_problem(lab_isolation(verify_uid=995)))
        self.assertEqual(isolation_problem(lab_isolation(mode="identity", verifier_tools_isolated=False, separate_verifier_identity=False)), "")


if __name__ == "__main__":
    unittest.main()
