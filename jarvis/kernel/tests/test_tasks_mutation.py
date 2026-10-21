"""Regression tests added after mutation testing of tasks.py (2026-10-19): every rule of isolation_problem pinned by the reason it
gives (so that one rule cannot hide behind another), the length limits of trusted_problem, and the clipped kernel_check text."""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from jarvis_kernel.tasks import assure_verdict, isolation_problem, trusted_problem

WRONG_FIELDS = "the isolation block is missing or has the wrong fields"
WRONG_TYPE = "has the wrong type"
UNKNOWN = "isolation.mode or backend is not a known value"
BOUNDS = "isolation.reasons or self_test is out of bounds"
NO_BACKEND = "isolation claims a boundary but has no backend or its self-test did not pass"
TOOLS = "isolation mode full without the verifier's tools isolated"
OWN_ID = "isolation mode full without a verifier identity of its own"
GRAN = "isolation mode full needs one process per test"
MISMATCH = "isolation.mode does not match its backend or the verifier-tools statement"
NO_SEP = "the sandbox identity is root, the verifier's or the runner's (no separation)"
CREDIBLE = "isolation.processes / cpu_seconds are not credible"


def iso(**over):
    return {"mode": "full", "backend": "identity", "self_test": "ok", "verifier_tools_isolated": True, "separate_verifier_identity": True,
            "sandbox_uid": 995, "verify_uid": 1001, "runner_uid": 0, "processes": 4, "granularity": "id", "cpu_seconds": 0.3, "reasons": [], **over}


class IsolationRules(unittest.TestCase):
    def check(self, over, expect):
        why = isolation_problem(iso(**over))
        if expect == "":
            self.assertEqual(why, "", over)
        else:
            self.assertIn(expect, why, over)

    def test_shape(self):
        self.assertEqual(isolation_problem(iso()), "")
        for bad in (None, "full", [], 3):
            self.assertEqual(isolation_problem(bad), WRONG_FIELDS)
        extra = iso()
        extra["x"] = 1
        self.assertEqual(isolation_problem(extra), WRONG_FIELDS)
        short = iso()
        del short["cpu_seconds"]
        self.assertEqual(isolation_problem(short), WRONG_FIELDS)
        renamed = iso()
        renamed["cpu"] = renamed.pop("cpu_seconds")
        self.assertEqual(isolation_problem(renamed), WRONG_FIELDS)

    def test_types(self):
        for over in ({"mode": 1}, {"backend": None}, {"self_test": 5}, {"verifier_tools_isolated": 1}, {"separate_verifier_identity": "yes"},
                     {"sandbox_uid": True}, {"sandbox_uid": 995.0}, {"sandbox_uid": "995"}, {"verify_uid": False}, {"runner_uid": None},
                     {"processes": 4.0}, {"processes": True}, {"granularity": 5}, {"cpu_seconds": True}, {"cpu_seconds": "0.3"},
                     {"reasons": ("a",)}, {"reasons": "a"}):
            with self.subTest(over=over):
                self.check(over, WRONG_TYPE)
        self.check({"cpu_seconds": 0}, "")                                  # a whole number is a fine number of seconds
        self.check({"cpu_seconds": 2}, "")
        self.check({"cpu_seconds": 0.0}, "")

    def test_known_values(self):
        for over in ({"mode": "partial"}, {"mode": "FULL"}, {"backend": "vm"}, {"backend": "Identity"}):
            self.check(over, UNKNOWN)
        self.check({"mode": "container", "backend": "container", "verifier_tools_isolated": False}, "")

    def test_bounds(self):
        self.check({"reasons": ["x" * 200] * 6}, "")
        self.check({"reasons": ["x" * 201]}, BOUNDS)
        self.check({"reasons": ["x"] * 7}, BOUNDS)
        self.check({"reasons": [3]}, BOUNDS)
        self.check({"reasons": ["ok", 3]}, BOUNDS)
        self.check({"self_test": "ok" + "x" * 118}, NO_BACKEND)             # 120 characters: in bounds, but not "ok"
        self.check({"self_test": "x" * 121}, BOUNDS)

    def test_mode_none_is_accepted_whatever_else_it_says(self):
        self.check({"mode": "none", "backend": "none", "self_test": "not run", "verifier_tools_isolated": False,
                    "separate_verifier_identity": False, "sandbox_uid": 0, "processes": 0, "granularity": "", "reasons": ["why"]}, "")
        self.check({"mode": "none", "backend": "identity", "self_test": "failed: x", "sandbox_uid": 0, "processes": 0}, "")
        self.check({"mode": "none", "reasons": ["x"] * 7}, BOUNDS)         # the bounds still apply to a block that claims nothing

    def test_a_claimed_boundary_needs_a_backend_and_a_passed_self_test(self):
        self.check({"backend": "none"}, NO_BACKEND)
        self.check({"self_test": "failed: x"}, NO_BACKEND)
        self.check({"self_test": ""}, NO_BACKEND)
        self.check({"mode": "identity", "backend": "none", "verifier_tools_isolated": False}, NO_BACKEND)

    def test_full_needs_isolated_tools_its_own_verifier_identity_and_one_process_per_test(self):
        self.check({"verifier_tools_isolated": False}, TOOLS)
        self.check({"separate_verifier_identity": False}, OWN_ID)
        self.check({"verify_uid": 995}, OWN_ID)
        self.check({"granularity": "group"}, GRAN)
        self.check({"granularity": ""}, GRAN)
        self.check({"granularity": "id"}, "")

    def test_identity_and_container_modes_must_match_their_backend_and_not_claim_isolated_tools(self):
        self.check({"mode": "identity", "verifier_tools_isolated": False}, "")
        self.check({"mode": "identity", "verifier_tools_isolated": True}, MISMATCH)
        self.check({"mode": "identity", "backend": "container", "verifier_tools_isolated": False}, MISMATCH)
        self.check({"mode": "container", "backend": "identity", "verifier_tools_isolated": False}, MISMATCH)
        self.check({"mode": "container", "backend": "container", "verifier_tools_isolated": True}, MISMATCH)

    def test_the_sandbox_identity_must_be_a_real_separate_user(self):
        for over in ({"sandbox_uid": 0}, {"sandbox_uid": -1}, {"sandbox_uid": 1001, "verify_uid": 1002, "runner_uid": 1001},
                     {"sandbox_uid": 7, "runner_uid": 7}):
            with self.subTest(over=over):
                self.check(dict(over, mode="identity", verifier_tools_isolated=False), NO_SEP)
        self.check({"sandbox_uid": 1, "mode": "identity", "verifier_tools_isolated": False}, "")
        self.check({"sandbox_uid": 0, "backend": "container", "mode": "container", "verifier_tools_isolated": False}, "")   # not an identity backend

    def test_the_run_must_be_credible(self):
        self.check({"processes": 2}, "")
        self.check({"processes": 1}, CREDIBLE)
        self.check({"processes": 0}, CREDIBLE)
        self.check({"processes": -3}, CREDIBLE)
        self.check({"cpu_seconds": -0.001}, CREDIBLE)
        self.check({"cpu_seconds": -1}, CREDIBLE)


def trusted_and_verdict(**over):
    art, harness_sha, ids_sha, chan = "a" * 64, "e" * 64, "f" * 64, "9" * 64
    t = {"ran_by": "lab-runner", "command": "python3 harness", "harness": "lab-harness/2", "harness_script_sha256": "d" * 64, "exit_code": 0,
         "collect_exit_code": 0, "tests_run": 3, "tests_collected": 3, "tests_passed": 3, "tests_failed": 0, "tests_errored": 0,
         "tests_skipped": 0, "tests_expected_failures": 0, "leftover_processes": 0, "collected_ids_sha256": ids_sha, "harness_sha256": harness_sha,
         "output_sha256": "b" * 64, "output_tail": "ok", "files_sha256": "c" * 64, "seconds": 0.5, "task": "t1", "job": "j1", "attempt": 1,
         "artifact_sha256": art, "isolation": iso()}
    t.update(over)
    v = {"passed": True, "task": "t1", "job": "j1", "attempt": 1, "artifact_sha256": art, "harness_sha256": harness_sha,
         "collected_ids_sha256": ids_sha, "verdict_channel_sha256": chan, "criteria": []}
    return t, v


class TrustedRules(unittest.TestCase):
    def why(self, **over):
        t, v = trusted_and_verdict(**over)
        return trusted_problem(t, v, "t1", "j1", 1)

    def test_the_good_record_passes(self):
        self.assertEqual(self.why(), "")

    def test_unknown_fields_are_named_and_the_list_is_cut_at_100_characters(self):
        why = self.why(evil=1)
        self.assertEqual(why, "trusted_execution has fields the kernel does not know: evil")
        many = {f"field_{i:03d}_" + "x" * 20: 1 for i in range(40)}
        why = self.why(**many)
        self.assertTrue(why.startswith("trusted_execution has fields the kernel does not know: field_000_"))
        self.assertEqual(len(why), len("trusted_execution has fields the kernel does not know: ") + 100)

    def test_length_limits_of_the_free_text_fields(self):
        for key, limit in (("command", 200), ("output_tail", 400), ("harness_script_sha256", 64)):
            with self.subTest(key=key):
                self.assertNotIn(f"trusted_execution.{key} has the wrong type or is too long", self.why(**{key: "d" * limit}))
                self.assertIn(f"trusted_execution.{key} has the wrong type or is too long", self.why(**{key: "d" * (limit + 1)}))
                self.assertIn(f"trusted_execution.{key} has the wrong type or is too long", self.why(**{key: 5}))
        self.assertNotIn("trusted_execution.harness has the wrong type", self.why(harness="x" * 40))
        self.assertIn("trusted_execution.harness has the wrong type or is too long", self.why(harness="x" * 41))
        self.assertIn("not produced by the runner's test harness", self.why(harness="x" * 40))


class KernelCheckText(unittest.TestCase):
    def test_the_reason_a_pass_is_not_verified_is_clipped_to_300_characters(self):
        reasons = ["r" * 150 + str(i) for i in range(3)]
        t, v = trusted_and_verdict(isolation=iso(mode="identity", verifier_tools_isolated=False, reasons=reasons))
        out, problem = assure_verdict(v, t, "t1", "j1", 1)
        self.assertEqual(problem, "")
        self.assertTrue(out["agent_assessed_pass"])
        self.assertNotIn("passed", out)
        self.assertEqual(len(out["kernel_check"]), 300)
        self.assertTrue(out["kernel_check"].startswith("not verified: rrrr"))
        self.assertEqual(out["assurance"], "isolated_tests_only")

    def test_a_demoted_pass_clips_its_reason_too(self):
        t, v = trusted_and_verdict()
        v["artifact_sha256"] = "0" * 64
        out, problem = assure_verdict(v, t, "t1", "j1", 1)
        self.assertTrue(problem)
        self.assertIs(out["passed"], False)
        self.assertLessEqual(len(out["kernel_check"]), 300)


if __name__ == "__main__":
    unittest.main()
