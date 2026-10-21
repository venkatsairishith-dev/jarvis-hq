"""Lab isolation (2026-10-19): the delivered code runs as its own identity, the verdict state belongs to others.

Follow-up to the re-review finding H2: with the verifier, the tested code and the harness in ONE OS user, nothing the runner did could
make the verdict trustworthy against the code being verified. These tests exercise the boundary that closes that: a sandbox identity that
runs all delivered code one process per test, a verify identity that owns the verdict state, a root runner that holds the channels, a
self-test run before every verification, resource limits, strict result validation, and fail-closed behaviour.

Two groups:
  - anything: pure helpers (configuration, container command line, strict JSON, record schema, judging) and the fail-closed paths;
  - REAL ISOLATION: needs a root process and three real users (agent, jarvis-sandbox, jarvis-verify), i.e. a throw-away container run with
    `-u 0` after `useradd`. There the attacks are real: the sandbox identity really is denied by the kernel. In the keep-id arrangement (one
    unprivileged user, no sudo on the server) separation is impossible, and these tests show it fails closed instead of pretending.
The container backend (rootless podman) is covered at command-line level only; the live container run is NOT RUN (podman is not installed
on the Linux test server).
"""
import hashlib
import json
import mmap
import os
import pwd
import re
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import threading
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))
sys.path.insert(0, os.path.join(HERE, "..", "..", "kernel"))
import lab_runner as R
import lab_isolation as ISO
from jarvis_kernel.tasks import isolation_problem, trusted_problem, assure_verdict

LINUX = os.path.isdir("/proc") and hasattr(os, "killpg")
REAL = LINUX and os.geteuid() == 0 and all(ISO._getpw(n) is not None for n in ("agent", ISO.SANDBOX_USER, ISO.VERIFY_USER))
NEEDS_REAL = unittest.skipUnless(REAL, "needs root and the users agent, jarvis-sandbox, jarvis-verify (a throw-away container; see the module docstring)")


def mod(*tests, header="import os, sys, unittest\n"):
    """Source of a test module with one class and the given test bodies (name -> statements)."""
    out = header + "class T(unittest.TestCase):\n"
    for name, body in tests:
        out += f"    def {name}(self):\n" + "".join("        " + ln + "\n" for ln in body.strip("\n").split("\n"))
    return out


def trusted_for(tid, job, iso, attempt=1):
    """(trusted_execution, verdict) as the runner would send them, with the given isolation block."""
    art, harness_sha, ids_sha, chan = "a" * 64, "e" * 64, "f" * 64, "9" * 64
    trusted = {"ran_by": "lab-runner", "command": "python3 lab harness (unittest discover, test*.py)",
               "harness": "lab-harness/1" if iso["mode"] == "none" else "lab-harness/2", "harness_script_sha256": "d" * 64, "exit_code": 0,
               "collect_exit_code": 0, "tests_run": 3, "tests_collected": 3, "tests_passed": 3, "tests_failed": 0, "tests_errored": 0,
               "tests_skipped": 0, "tests_expected_failures": 0, "leftover_processes": 0, "collected_ids_sha256": ids_sha, "harness_sha256": harness_sha,
               "output_sha256": "b" * 64, "output_tail": "ok", "files_sha256": "c" * 64, "seconds": 0.5, "task": tid, "job": job, "attempt": attempt,
               "artifact_sha256": art, "isolation": iso}
    ver = {"passed": True, "task": tid, "job": job, "attempt": attempt, "artifact_sha256": art, "harness_sha256": harness_sha,
           "collected_ids_sha256": ids_sha, "verdict_channel_sha256": chan, "criteria": []}
    return trusted, ver


PASS3 = mod(("test_a", "pass"), ("test_b", "self.assertEqual(2, 1 + 1)"), ("test_c", "pass"))


# ---------------------------------------------------------------------------------------------------------------- anything
class StrictJson(unittest.TestCase):
    def test_what_is_refused(self):
        for label, data in (("duplicate key", '{"passed": false, "passed": true}'), ("nan", '{"x": NaN}'), ("infinity", '{"x": Infinity}'),
                            ("not utf-8", b'{"x": "\xff"}'), ("trailing text", '{"a": 1} {"a": 2}'), ("too deep", "[" * 20 + "]" * 20),
                            ("nested duplicate", '{"a": {"b": 1, "b": 2}}'), ("too big", '{"a": "' + "x" * 300_000 + '"}')):
            with self.subTest(label):
                with self.assertRaises(ValueError):
                    ISO.strict_loads(data)

    def test_what_is_accepted(self):
        self.assertEqual(ISO.strict_loads('{"a": [1, 2, {"b": null}], "c": "é"}'), {"a": [1, 2, {"b": None}], "c": "é"})
        obj, end = ISO.strict_raw_decode('x {"a": 1} tail', 2)
        self.assertEqual((obj, end), ({"a": 1}, 10))
        with self.assertRaises(ValueError):
            ISO.strict_raw_decode('{"a": 1, "a": 2}')

    def test_the_verifiers_line_and_file_are_parsed_strictly(self):
        want = {"nonce": "ab" * 16, "task": "t", "job": "j", "attempt": 1}
        line = R.VERDICT_MARK + ' {"nonce": "%s", "task": "t", "job": "j", "attempt": 1, "passed": false, "passed": true}' % want["nonce"]
        v, why, _ = R.parse_channel(line.encode(), want)
        self.assertIsNone(v)
        self.assertIn("strict", why)


class RecordSchema(unittest.TestCase):
    """The harness channel: every record exact in keys and types; every damaged channel is a problem, never a pass."""
    GOOD = [{"e": "c", "id": "t.T.a", "seq": 0, "pid": 7}, {"e": "collected", "n": 1, "seq": 1, "pid": 7},
            {"e": "start", "id": "t.T.a", "seq": 2, "pid": 7}, {"e": "stop", "id": "t.T.a", "o": "ok", "seq": 3, "pid": 7},
            {"e": "done", "run": 1, "failures": 0, "errors": 0, "skipped": 0, "xfail": 0, "xpass": 0, "ok": True, "seq": 4, "pid": 7}]

    @staticmethod
    def wire(recs):
        return b"".join(json.dumps(r, separators=(",", ":")).encode() + b"\n" for r in recs)

    def test_a_good_channel_parses_and_every_record_matches_the_schema(self):
        recs, probs = R._parse_records(self.wire(self.GOOD))
        self.assertEqual((len(recs), probs), (5, []))

    def test_damaged_channels(self):
        good = self.wire(self.GOOD)
        cases = {
            "truncated record": good[:-7],
            "half a line at the end": good + b'{"e":"start","id":"x","seq":5',
            "oversized line": self.wire(self.GOOD[:1]) + b'{"e":"c","id":"' + b"x" * 5000 + b'","seq":1,"pid":7}\n',
            "duplicate key": b'{"e":"c","id":"a","id":"b","seq":0,"pid":7}\n',
            "not utf-8": b'{"e":"c","id":"\xff","seq":0,"pid":7}\n',
            "not json": b"Ran 7 tests in 0.001s\n",
            "out of sequence": self.wire([dict(self.GOOD[0], seq=3)]),
            "repeated sequence": self.wire([self.GOOD[0], dict(self.GOOD[1], seq=0)]),
            "extra field": self.wire([dict(self.GOOD[0], extra=1)]),
            "missing field": self.wire([{k: v for k, v in self.GOOD[0].items() if k != "pid"}]),
            "wrong type": self.wire([dict(self.GOOD[3], o=["ok"])]),
            "bool as int": self.wire([dict(self.GOOD[1], n=True)]),
            "unknown outcome": self.wire([dict(self.GOOD[3], o="passed-honest")]),
            "negative count": self.wire([dict(self.GOOD[4], run=-1)]),
            "huge id": self.wire([dict(self.GOOD[0], id="x" * 301)]),
            "empty id": self.wire([dict(self.GOOD[0], id="")]),
            "unknown event": self.wire([{"e": "ok", "seq": 0, "pid": 7}]),
            "orphan flag not true": self.wire([dict(self.GOOD[3], orphan=False)]),
        }
        for label, raw in cases.items():
            with self.subTest(label):
                recs, probs = R._parse_records(raw)
                self.assertTrue(probs, f"{label} was accepted: {recs}")

    def test_too_many_records_are_refused(self):
        recs, probs = R._parse_records(b'{"e":"c"}\n' * (R.RECORD_COUNT_MAX + 5))
        self.assertTrue(probs)


class JudgeProcess(unittest.TestCase):
    """judge_harness as the sandbox supervisor uses it for one process: the records must be exactly this process's ids and tell one story."""

    def step(self, recs, exit_code=0):
        return {"records": recs, "bad": [], "exit": exit_code, "timed_out": False, "output": ""}

    def one(self, outcome="ok", ids=("t.T.a",), exit_code=0, **done):
        recs, seq = [], 0

        def add(**r):
            nonlocal seq
            recs.append(dict(r, seq=seq, pid=9)); seq += 1
        for i in ids:
            add(e="c", id=i)
        add(e="collected", n=len(ids))
        for i in ids:
            add(e="start", id=i); add(e="stop", id=i, o=outcome)
        d = dict(run=len(ids), failures=int(outcome == "fail"), errors=int(outcome == "error"), skipped=int(outcome == "skip"), xfail=0, xpass=0,
                 ok=outcome in ("ok", "skip"))
        d.update(done)
        add(e="done", **d)
        return self.step(recs, exit_code)

    def test_an_honest_process_is_accepted(self):
        probs, facts = R.judge_harness(R.synth_collect(["t.T.a"]), self.one())
        self.assertEqual(probs, [])
        self.assertEqual((facts["tests_run"], facts["tests_passed"]), (1, 1))

    def test_ids_must_be_exactly_the_ones_the_process_was_given(self):
        for ids in (["t.T.b"], ["t.T.a", "t.T.b"], []):
            probs, facts = R.judge_harness(R.synth_collect(["t.T.a"]), self.one(ids=ids or ("t.T.a",)) if ids else self.step([]))
            if ids == ["t.T.b"] or ids == ["t.T.a", "t.T.b"]:
                self.assertTrue(probs, ids)
                self.assertIsNone(facts)

    def test_the_exit_status_must_be_the_one_its_own_records_imply(self):
        for outcome, exit_code in (("ok", 1), ("ok", 3), ("fail", 0), ("fail", 2), ("error", 0), ("ok", -9)):
            with self.subTest(outcome=outcome, exit=exit_code):
                probs, facts = R.judge_harness(R.synth_collect(["t.T.a"]), self.one(outcome, exit_code=exit_code))
                self.assertIsNone(facts)
                self.assertTrue(any("exit status" in p for p in probs), probs)
        self.assertIsNotNone(R.judge_harness(R.synth_collect(["t.T.a"]), self.one("fail", exit_code=1))[1])
        self.assertIsNotNone(R.judge_harness(R.synth_collect(["t.T.a"]), self.one("skip", exit_code=0))[1])

    def test_inconsistent_counts_are_void(self):
        for over in ({"run": 2}, {"skipped": 1}, {"xfail": 1}, {"failures": 0, "errors": 0, "ok": False}, {"ok": True, "failures": 1}):
            with self.subTest(over=over):
                run = self.one("ok" if "failures" not in over else "fail", **over)
                probs, facts = R.judge_harness(R.synth_collect(["t.T.a"]), run)
                if over.get("failures") == 1:
                    self.assertTrue(facts is None or facts["tests_failed"] == 1)
                else:
                    self.assertIsNone(facts, over)

    def test_records_after_the_final_one_and_duplicate_stops_are_void(self):
        run = self.one()
        run["records"].append({"e": "stop", "id": "t.T.a", "o": "ok", "seq": len(run["records"]), "pid": 9})
        self.assertIsNone(R.judge_harness(R.synth_collect(["t.T.a"]), run)[1])
        run = self.one()
        run["records"].insert(-1, dict(run["records"][-3], seq=0))
        for k, r in enumerate(run["records"]):
            r["seq"] = k
        self.assertIsNone(R.judge_harness(R.synth_collect(["t.T.a"]), run)[1])

    def test_records_from_two_processes_are_void(self):
        run = self.one()
        run["records"][-1]["pid"] = 10
        probs, facts = R.judge_harness(R.synth_collect(["t.T.a"]), run)
        self.assertIsNone(facts)
        self.assertTrue(any("more than one process" in p for p in probs))


class FailClosed(unittest.TestCase):
    """The boundary is never claimed that does not exist."""

    def setUp(self):
        self.saved = (ISO.REQUEST, ISO.SANDBOX_USER, ISO.VERIFY_USER, ISO.VERIFIER_TOOLS, ISO.CONTAINER_IMAGE)

    def tearDown(self):
        ISO.REQUEST, ISO.SANDBOX_USER, ISO.VERIFY_USER, ISO.VERIFIER_TOOLS, ISO.CONTAINER_IMAGE = self.saved

    AGENT = pwd.getpwuid(os.getuid())

    def test_switched_off_unknown_or_unavailable_means_none_with_a_reason(self):
        ISO.REQUEST = "none"
        b = ISO.resolve(self.AGENT)
        self.assertEqual((b.backend, b.mode(), b.active), ("none", "none", False))
        self.assertIn("switched off", " ".join(b.reasons))
        self.assertIn("unknown", " ".join(ISO.resolve(self.AGENT, "banana").reasons))
        ISO.CONTAINER_IMAGE = ""
        b = ISO.resolve(self.AGENT, "container")
        self.assertEqual(b.backend, "none")
        self.assertIn("podman", " ".join(b.reasons))

    def test_a_missing_sandbox_user_a_non_root_runner_or_a_shared_identity_is_none(self):
        ISO.SANDBOX_USER = "no-such-sandbox-user-anywhere"
        b = ISO.resolve(self.AGENT, "identity")
        self.assertEqual((b.backend, b.mode()), ("none", "none"))
        self.assertIn("does not exist", " ".join(b.reasons))
        other = pwd.getpwnam("nobody")
        ISO.SANDBOX_USER = other.pw_name                              # an existing, unprivileged user
        if os.geteuid() != 0:
            self.assertIn("not root", " ".join(ISO.resolve(self.AGENT, "identity").reasons))
        else:
            self.assertIn("no separation", " ".join(ISO.resolve(other, "identity").reasons))      # it IS the agent user here

    def test_a_backend_that_has_not_passed_its_self_test_is_not_active(self):
        me = self.AGENT
        b = ISO.Boundary("identity", [], sandbox=me, verify=None, agent=me)
        self.assertEqual((b.self_test, b.active, b.mode()), ("not run", False, "none"))
        b.self_test = "failed: x"
        self.assertEqual(b.mode(), "none")
        b.self_test = "ok"
        ISO.VERIFIER_TOOLS = ""
        b.verifier_tools = False
        self.assertEqual(b.mode(), "identity")                      # separated tests, but the verifier's own tools are not known to be sandboxed
        b.verifier_tools = True
        self.assertEqual(b.mode(), "identity")                      # LAB-RT: the owner's statement alone is not enough, the verifier needs an identity of its own
        b.verify = pwd.struct_passwd(("vf", "x", me.pw_uid + 1, me.pw_gid + 1, "", "/", "/"))
        b.sandbox = pwd.struct_passwd(("sb", "x", me.pw_uid + 2, me.pw_gid + 2, "", "/", "/"))
        self.assertEqual(b.mode(), "full")
        if os.geteuid() != 0:
            self.assertEqual(ISO.run_self_test(ISO.Boundary("none", ["x"]))[:6], "failed")
            self.assertEqual(ISO.run_self_test(ISO.Boundary("container", []))[:6], "failed")      # the container backend was not run: never ok

    def test_the_isolation_block_validates_in_the_kernel_and_only_full_can_verify(self):
        me = self.AGENT
        for backend, tools, mode in (("identity", True, "full"), ("identity", False, "identity"), ("container", True, "full")):
            b = ISO.Boundary(backend, [], sandbox=pwd.struct_passwd(("sb", "x", 995, 995, "", "/", "/")), verify=pwd.struct_passwd(("vf", "x", 1001, 1001, "", "/", "/")),
                             agent=me)
            b.self_test, b.verifier_tools = "ok", tools
            blk = b.describe(processes=4, granularity="id", cpu_seconds=0.3)
            blk["runner_uid"] = 0
            self.assertEqual(blk["mode"], mode)
            self.assertEqual(isolation_problem(blk), "", blk)
        none = ISO.Boundary("none", ["why"], agent=me).describe()
        self.assertEqual(isolation_problem(none), "")
        self.assertEqual(none["mode"], "none")

    def test_container_command_line_has_every_restriction(self):
        cmd = ISO.container_command(["/harness/lab_harness.py", "run", "/work", "3"], "/tmp/s", "/tmp/w", 3, "/tmp/h", image="img:1")
        joined = " ".join(cmd)
        for flag in ("--network=none", "--cap-drop=all", "--read-only", "--security-opt=no-new-privileges", "--userns=auto", "--pids-limit=",
                     "--memory=", "--cpus=1", "--ulimit=cpu=", "--ulimit=core=0", "--ulimit=nofile=", "--ulimit=fsize=", "--preserve-fds=1",
                     "--tmpfs=/scratch:rw", "noexec", "--volume=/tmp/w:/work:ro", "--volume=/tmp/h:/harness:ro", "--workdir=/scratch", "img:1"):
            self.assertIn(flag, joined)
        self.assertEqual(cmd[:2], ["podman", "run"])
        self.assertNotIn("--privileged", joined)
        self.assertNotIn("--network=host", joined)
        b = ISO.Boundary("container", [], agent=self.AGENT)
        with self.assertRaises(RuntimeError):
            ISO.spawn_sandboxed(b, ["true"], "/tmp", stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)     # NOT RUN: never silently succeeds

    def test_the_launcher_command_names_the_identity_the_limits_and_only_the_allowed_descriptors(self):
        pw = pwd.struct_passwd(("sb", "x", 995, 996, "", "/", "/"))
        cmd = ISO.launcher_command(pw, ["/usr/bin/python3", "x.py"], "/scratch/work", ISO.sandbox_env("/scratch"), keep_fds=(5, 3, 5))
        spec = json.loads(cmd[-1])
        self.assertEqual((spec["uid"], spec["gid"], spec["keep_fds"], spec["cwd"]), (995, 996, [3, 5], "/scratch/work"))
        self.assertEqual(spec["limits"], ISO.LIMITS)
        self.assertEqual(set(spec["env"]), set(ISO.SANDBOX_ENV_ALLOW) & set(spec["env"]))
        self.assertEqual(spec["env"]["HOME"], "/scratch")
        self.assertEqual(ISO.LIMITS["core"], 0)
        for needle in ("setgroups([])", "setresuid", "setuid(0)", "umask(0o077)", "prctl(38", "RLIMIT_NPROC", "RLIMIT_AS", "RLIMIT_CPU", "RLIMIT_FSIZE"):
            self.assertIn(needle, ISO.LAUNCHER_SRC)


# ---------------------------------------------------------------------------------------------------------------- kernel half
class KernelRules(unittest.TestCase):
    """The kernel's own checks of the isolation block (strict shape; full is the only way to verified; the rest keep the pass as NOT verified)."""

    @staticmethod
    def iso(**over):
        return {"mode": "full", "backend": "identity", "self_test": "ok", "verifier_tools_isolated": True, "separate_verifier_identity": True,
                "sandbox_uid": 995, "verify_uid": 1001, "runner_uid": 0, "processes": 4, "granularity": "id", "cpu_seconds": 0.3, "reasons": [], **over}

    def test_what_makes_the_block_invalid(self):
        bad = [{"mode": "partial"}, {"backend": "vm"}, {"self_test": "failed: x"}, {"sandbox_uid": 0}, {"sandbox_uid": 1001}, {"sandbox_uid": -1},
               {"verifier_tools_isolated": False}, {"processes": 1}, {"cpu_seconds": -1}, {"reasons": ["x"] * 7}, {"reasons": [3]},
               {"sandbox_uid": True}, {"mode": "identity"}, {"backend": "none"}, {"granularity": 5}, {"processes": "4"}, {"verifier_tools_isolated": 1}]
        for over in bad:
            with self.subTest(over=over):
                self.assertNotEqual(isolation_problem(self.iso(**over)), "", over)
        blk = self.iso()
        blk["extra"] = 1
        self.assertNotEqual(isolation_problem(blk), "")
        blk = self.iso()
        del blk["reasons"]
        self.assertNotEqual(isolation_problem(blk), "")
        self.assertNotEqual(isolation_problem("full"), "")
        self.assertNotEqual(isolation_problem(None), "")
        self.assertEqual(isolation_problem(self.iso()), "")
        self.assertEqual(isolation_problem(self.iso(mode="identity", verifier_tools_isolated=False)), "")

    def test_only_full_verifies_and_other_modes_keep_the_pass_as_not_verified(self):
        for blk, verified in ((self.iso(), True), (self.iso(mode="identity", verifier_tools_isolated=False, reasons=["the verifier's own tools"]), False),
                              (self.iso(mode="none", backend="none", self_test="not run", sandbox_uid=-1, verify_uid=-1, processes=0,
                                        reasons=["the runner is not root"], verifier_tools_isolated=False, separate_verifier_identity=False), False)):
            with self.subTest(mode=blk["mode"]):
                tid, job = "t" * 12, "j1"
                trusted, ver = trusted_for(tid, job, blk)
                out, why = assure_verdict(ver, trusted, tid, job, 1)
                self.assertEqual(why, "")
                self.assertEqual(out.get("passed") is True, verified, out)
                if not verified:
                    self.assertIs(out["agent_assessed_pass"], True)
                    self.assertIn("NOT verified".lower(), out["kernel_check"].lower())
                self.assertEqual(out["isolation"]["mode"], blk["mode"])

    def test_a_bad_isolation_block_inside_a_record_makes_the_whole_record_invalid(self):
        """trusted_problem itself (not only isolation_problem) refuses a record whose isolation block is missing, forged or inconsistent."""
        tid, job = "t" * 12, "j1"
        for over in ({"mode": "partial"}, {"sandbox_uid": 0}, {"sandbox_uid": 1001}, {"self_test": "failed: x"}, {"verifier_tools_isolated": False},
                     {"processes": 1}, {"extra": 1}):
            with self.subTest(over=over):
                trusted, ver = trusted_for(tid, job, self.iso(**over))
                self.assertNotEqual(trusted_problem(trusted, ver, tid, job, 1), "", over)
                out, why = assure_verdict(ver, trusted, tid, job, 1)
                self.assertTrue(why)
                self.assertIs(out["passed"], False)
        trusted, ver = trusted_for(tid, job, self.iso())
        del trusted["isolation"]
        self.assertIn("isolation block", trusted_problem(trusted, ver, tid, job, 1))

    def test_a_record_that_claims_a_boundary_with_the_wrong_harness_or_unknown_fields_is_refused(self):
        tid, job = "t" * 12, "j1"
        trusted, ver = trusted_for(tid, job, self.iso())
        self.assertEqual(trusted_problem(trusted, ver, tid, job, 1), "")
        for over, word in (({"harness": "lab-harness/1"}, "sandbox harness"), ({"surprise": 1}, "does not know"), ({"output_tail": "x" * 500}, "too long"),
                           ({"command": ["a"]}, "wrong type")):
            self.assertIn(word, trusted_problem({**trusted, **over}, ver, tid, job, 1), over)
        t2, v2 = trusted_for(tid, job, self.iso(mode="none", backend="none", self_test="not run", sandbox_uid=-1, verify_uid=-1, processes=0,
                                                 verifier_tools_isolated=False, separate_verifier_identity=False))
        self.assertIn("must say so", trusted_problem({**t2, "harness": "lab-harness/2"}, v2, tid, job, 1))


# ---------------------------------------------------------------------------------------------------------------- real isolation
class RealBase(unittest.TestCase):
    """A resolved, self-tested boundary with real users. (Runs only in a throw-away container as root.)"""

    @classmethod
    def setUpClass(cls):
        cls.saved = (ISO.REQUEST, ISO.VERIFIER_TOOLS)
        ISO.REQUEST = "identity"
        cls.b = ISO.resolve(R.PW)
        ISO.run_self_test(cls.b)

    @classmethod
    def tearDownClass(cls):
        ISO.REQUEST, ISO.VERIFIER_TOOLS = cls.saved

    def setUp(self):
        self.assertEqual(self.b.self_test, "ok", self.b.self_test)
        R.CURRENT_BOUNDARY = self.b
        self.timeouts = (R.SANDBOX_COLLECT_S, R.SANDBOX_PROC_S)

    def tearDown(self):
        R.CURRENT_BOUNDARY = None
        R.SANDBOX_COLLECT_S, R.SANDBOX_PROC_S = self.timeouts

    def run_tests(self, *tests, header="import os, sys, unittest\n", name="test_a.py"):
        return R.portable_run("/tmp", [(name, mod(*tests, header=header))])

    def run_src(self, src, name="test_a.py"):
        return R.portable_run("/tmp", [(name, src)])


@NEEDS_REAL
class SelfTest(RealBase):
    def test_the_real_boundary_passes_its_self_test(self):
        self.assertEqual((self.b.backend, self.b.self_test, self.b.mode()), ("identity", "ok", "identity"))
        blk = self.b.describe(4, "id", 0.2)
        self.assertEqual(isolation_problem(blk), "")
        self.assertNotIn(blk["sandbox_uid"], (0, blk["verify_uid"], blk["runner_uid"]))
        self.assertTrue(blk["separate_verifier_identity"])

    def test_a_boundary_without_real_separation_fails_the_self_test(self):
        vp, agent = ISO._getpw(ISO.VERIFY_USER), R.PW
        b = ISO.Boundary("identity", [], sandbox=vp, verify=vp, agent=agent)           # the "sandbox" is the verify identity itself
        self.assertTrue(ISO.run_self_test(b).startswith("failed"))
        self.assertEqual((b.active, b.mode()), (False, "none"))
        self.assertIn("list_state_dir", b.self_test)

    def test_the_self_test_cleans_up_after_itself(self):
        before = {n for n in os.listdir(tempfile.gettempdir()) if n.startswith("lab-selftest")}
        b = ISO.resolve(R.PW)
        ISO.run_self_test(b)
        self.assertEqual({n for n in os.listdir(tempfile.gettempdir()) if n.startswith("lab-selftest")}, before)
        self.assertEqual(ISO.uid_pids(b.verify.pw_uid), [])


ATTACK_STATE = r'''
import json, mmap, os, sys, ctypes
a = json.loads(sys.argv[1])
res = {}
def t(name, fn):
    try:
        fn(); res[name] = "ALLOWED"
    except PermissionError:
        res[name] = "denied"
    except OSError as e:
        res[name] = "denied" if e.errno in (1, 2, 13) else "errno %d" % e.errno
def mm():
    fd = os.open(a["slot_file"], os.O_RDWR); m = mmap.mmap(fd, 4); m[:] = b"HACK"
def pwrite():
    fd = os.open(a["slot_file"], os.O_WRONLY); os.pwrite(fd, b"HACK", 0)
def trunc():
    os.truncate(a["slot_file"], 0)
def trunc_open():
    os.close(os.open(a["slot_file"], os.O_WRONLY | os.O_TRUNC))
t("mmap_state_file", mm)
t("pwrite_state_file", pwrite)
t("truncate_state_file", trunc)
t("open_trunc_state_file", trunc_open)
t("chmod_state_file", lambda: os.chmod(a["slot_file"], 0o666))
t("symlink_over_state_file", lambda: os.symlink("/etc/passwd", a["slot_file"]))
t("symlink_new_in_state_dir", lambda: os.symlink("/etc/passwd", a["slot_dir"] + "/l"))
t("hardlink_state_file", lambda: os.link(a["slot_file"], a["own"] + "/hl"))
t("rename_state_file", lambda: os.rename(a["slot_file"], a["own"] + "/moved"))
t("rename_state_dir", lambda: os.rename(a["slot_dir"], a["parent"] + "/moved"))
t("rmdir_state_dir", lambda: os.rmdir(a["slot_dir"]))
t("unlink_state_file", lambda: os.unlink(a["slot_file"]))
t("create_in_state_dir", lambda: os.close(os.open(a["slot_dir"] + "/new", os.O_CREAT | os.O_WRONLY)))
t("list_state_dir", lambda: os.listdir(a["slot_dir"]))
t("read_state_file", lambda: open(a["slot_file"], "rb").read(1))
for i in (0, 1, 2):
    t("write_verifier_fd%d" % i, lambda i=i: os.close(os.open("/proc/%d/fd/%d" % (a["vpid"], i), os.O_WRONLY | os.O_APPEND)))
t("read_verifier_mem", lambda: os.close(os.open("/proc/%d/mem" % a["vpid"], os.O_RDWR)))
t("read_verifier_environ", lambda: open("/proc/%d/environ" % a["vpid"], "rb").read(1))
t("read_verifier_cmdline_is_public_but_not_secret_bearing", lambda: open("/proc/%d/cmdline" % a["vpid"], "rb").read(1))
def ptrace():
    libc = ctypes.CDLL(None, use_errno=True)
    if libc.ptrace(16, a["vpid"], 0, 0) != -1:
        return
    raise PermissionError
t("ptrace_verifier", ptrace)
t("signal_verifier", lambda: os.kill(a["vpid"], 9))
for name, p in a["runner_paths"].items():
    t("runner_" + name, lambda p=p: os.close(os.open(p, os.O_WRONLY | os.O_APPEND)))
t("runner_pid_fds", lambda: os.listdir("/proc/%d/fd" % a["rpid"]))
t("runner_mem", lambda: os.close(os.open("/proc/%d/mem" % a["rpid"], os.O_RDWR)))
print(json.dumps(res))
'''


@NEEDS_REAL
class StateAttacks(RealBase):
    """The sandbox identity against the verdict state: slot, capture, the verifier's descriptors and memory, the runner's descriptors."""

    def setUp(self):
        super().setUp()
        self.parent = tempfile.mkdtemp(prefix="vstate-")
        os.chmod(self.parent, 0o711)
        vpw = self.b.verifier_pw()
        self.slot = R.VerdictSlot(self.parent, vpw, closed=True)
        self.capture = R.VerifierOutput()
        with open(self.slot.verdict_path, "w") as fh:
            fh.write('{"passed": false}')
        # the verifier stand-in: the verify identity, stdout/stderr = the runner's capture, the verdict file open, a secret in its environment
        code = "import os,sys,time\nfd=os.open(sys.argv[1],os.O_RDWR)\nsys.stdout.write('ready\\n');sys.stdout.flush()\ntime.sleep(60)\n"
        cmd = ISO.launcher_command(vpw, [ISO.PYTHON3, "-I", "-B", "-c", code, self.slot.verdict_path], "/", {"PATH": "/usr/bin:/bin", "SECRET": "nonce-xyz"},
                                   keep_fds=(self.capture.wfd,))
        self.v = subprocess.Popen(cmd, stdout=self.capture.wfd, stderr=self.capture.wfd, stdin=subprocess.DEVNULL, start_new_session=True,
                                  pass_fds=(self.capture.wfd,))
        for _ in range(500):
            if b"ready" in self.capture.read():
                break
            time.sleep(0.01)
        self.own = tempfile.mkdtemp(prefix="own-")
        os.chown(self.own, self.b.sandbox.pw_uid, self.b.sandbox.pw_gid)
        self.honest_capture = self.capture.read()

    def tearDown(self):
        try:
            os.killpg(self.v.pid, signal.SIGKILL)
        except OSError:
            pass
        self.v.wait()
        self.slot.close(); self.capture.close()
        shutil.rmtree(self.parent, ignore_errors=True); shutil.rmtree(self.own, ignore_errors=True)
        super().tearDown()

    def attack(self):
        args = {"slot_file": self.slot.verdict_path, "slot_dir": self.slot.path, "parent": self.parent, "own": self.own, "vpid": self.v.pid,
                "rpid": os.getpid(), "runner_paths": {"capture_read_fd": f"/proc/{os.getpid()}/fd/{self.capture.rfd}",
                                                      "capture_write_fd": f"/proc/{os.getpid()}/fd/{self.capture.wfd}",
                                                      "slot_dir_fd": f"/proc/{os.getpid()}/fd/{self.slot.dirfd}",
                                                      "slot_read_fd": f"/proc/{os.getpid()}/fd/{self.slot.rfd}"}}
        r, w = os.pipe()
        p = ISO.spawn_sandboxed(self.b, [ISO.PYTHON3, "-I", "-B", "-c", ATTACK_STATE, json.dumps(args)], self.own, stdout=w, stderr=subprocess.DEVNULL)
        os.close(w)
        out = b""
        while True:
            chunk = os.read(r, 65536)
            if not chunk:
                break
            out += chunk
        os.close(r)
        p.wait(timeout=30)
        return json.loads(out)

    def test_every_attack_on_the_verdict_state_is_denied_and_nothing_changes(self):
        res = self.attack()
        allowed = {k: v for k, v in res.items() if v != "denied" and "cmdline" not in k}
        self.assertEqual(allowed, {}, allowed)
        self.assertGreaterEqual(len(res), 25)
        self.assertEqual(open(self.slot.verdict_path).read(), '{"passed": false}')                # symlink / hardlink / rename / mmap / pwrite / truncate: no effect
        self.assertEqual(os.listdir(self.slot.path), ["VERIFY.json"])
        self.assertEqual(os.stat(self.slot.verdict_path).st_uid, self.b.verify.pw_uid)
        self.assertEqual(stat.S_IMODE(os.stat(self.slot.verdict_path).st_mode), 0o600)
        self.assertEqual(self.capture.read(), self.honest_capture)                                 # nothing reached the verifier's captured stdout
        self.assertTrue(R._alive(self.v.pid))                                                       # and the verifier was not signalled
        self.assertEqual(os.listdir(self.own), [])

    def test_the_slot_is_closed_to_the_sandbox_identity_but_open_to_the_verify_identity(self):
        st = os.stat(self.slot.path)
        self.assertEqual((st.st_uid, st.st_gid, stat.S_IMODE(st.st_mode)), (0, self.b.verify.pw_gid, 0o750))
        self.assertEqual(os.stat(self.parent).st_uid, 0)

    def test_the_sealed_state_cannot_be_reopened_by_either_identity_after_the_exit(self):
        self.slot.freeze()
        for pw in (self.b.verify, self.b.sandbox):
            pid = os.fork()
            if pid == 0:
                try:
                    os.setgroups([]); os.setgid(pw.pw_gid); os.setuid(pw.pw_uid)
                    os.close(os.open(self.slot.verdict_path, os.O_RDWR))
                    os._exit(0)
                except BaseException:
                    os._exit(1)
            self.assertEqual(os.waitpid(pid, 0)[1] >> 8, 1, pw.pw_name)
        st = os.stat(self.slot.verdict_path)
        self.assertEqual((st.st_uid, stat.S_IMODE(st.st_mode)), (0, 0o400))


ATTACK_ENV = r'''
import json, os, resource, sys
res = {"uid": os.getuid(), "euid": os.geteuid(), "gid": os.getgid(), "groups": os.getgroups(), "cwd": os.getcwd(), "env": sorted(os.environ),
       "umask": os.umask(0o077)}
fds = {}
for n in os.listdir("/proc/self/fd"):
    try:
        fds[n] = os.readlink("/proc/self/fd/" + n)
    except OSError:
        pass
res["fds"] = fds
res["limits"] = {k: resource.getrlimit(getattr(resource, "RLIMIT_" + k)) for k in ("NPROC", "AS", "CPU", "NOFILE", "FSIZE", "CORE")}
res["no_new_privs"] = open("/proc/self/status").read().split("NoNewPrivs:")[1].split()[0]
res["setuid_root"] = "ALLOWED"
try:
    os.setuid(0)
except OSError:
    res["setuid_root"] = "denied"
res["sid_is_pid"] = os.getsid(0) == os.getpid()
print(json.dumps(res))
'''


@NEEDS_REAL
class ProcessShape(RealBase):
    def spawn(self, code):
        scratch = tempfile.mkdtemp(prefix="shape-")
        os.chown(scratch, self.b.sandbox.pw_uid, self.b.sandbox.pw_gid)
        try:
            r, w = os.pipe()
            extra_r, extra_w = os.pipe()                                  # runner descriptors that must NOT arrive (inheritable on purpose)
            os.set_inheritable(extra_r, True); os.set_inheritable(extra_w, True)
            p = ISO.spawn_sandboxed(self.b, [ISO.PYTHON3, "-I", "-B", "-c", code], scratch, stdout=w, stderr=subprocess.DEVNULL)
            os.close(w)
            out = b""
            while True:
                chunk = os.read(r, 65536)
                if not chunk:
                    break
                out += chunk
            os.close(r); os.close(extra_r); os.close(extra_w)
            p.wait(timeout=30)
            return json.loads(out), scratch
        finally:
            shutil.rmtree(scratch, ignore_errors=True)

    def test_the_sandboxed_process_has_nothing_it_should_not_have(self):
        res, scratch = self.spawn(ATTACK_ENV)
        self.assertEqual((res["uid"], res["euid"], res["gid"], res["groups"]), (self.b.sandbox.pw_uid, self.b.sandbox.pw_uid, self.b.sandbox.pw_gid, []))
        self.assertEqual(res["cwd"], scratch)
        self.assertEqual(res["umask"], 0o077)
        self.assertTrue(set(res["env"]) <= set(ISO.SANDBOX_ENV_ALLOW), res["env"])
        self.assertEqual(res["no_new_privs"], "1")
        self.assertEqual(res["setuid_root"], "denied")
        self.assertTrue(res["sid_is_pid"])                               # a session of its own
        others = [v for v in res["fds"].values() if not (v == "/dev/null" or v.startswith("pipe:") or v.startswith("/proc/"))]
        self.assertEqual(others, [], res["fds"])                          # no file, socket or runner descriptor
        self.assertLessEqual(len(res["fds"]), 4)                          # 0, 1, 2 and the listing itself
        L = res["limits"]
        for k, want in (("NPROC", ISO.LIMITS["nproc"]), ("AS", ISO.LIMITS["as_bytes"]), ("NOFILE", ISO.LIMITS["nofile"]), ("FSIZE", ISO.LIMITS["fsize"]),
                        ("CORE", 0), ("CPU", ISO.LIMITS["cpu_s"])):
            self.assertEqual(L[k][0], want, k)


@NEEDS_REAL
class SandboxedTests(RealBase):
    """portable_run through the sandbox supervisor: one fresh process per test id, structure checked by the runner."""

    def test_an_honest_suite_is_counted_per_process(self):
        problems, rec = self.run_src(PASS3)
        self.assertEqual(problems, [])
        self.assertEqual((rec["tests_run"], rec["tests_passed"], rec["harness"], rec["leftover_processes"]), (3, 3, "lab-harness/2", 0))
        iso = rec["isolation"]
        self.assertEqual((iso["mode"], iso["processes"], iso["granularity"]), ("identity", 4, "id"))      # a collection process + one per test
        self.assertGreaterEqual(iso["cpu_seconds"], 0)
        self.assertEqual(isolation_problem(iso), "")

    def test_failures_errors_and_import_errors_are_counted_not_hidden(self):
        problems, rec = self.run_tests(("test_a", "pass"), ("test_b", "self.fail('x')"), ("test_c", "raise RuntimeError('y')"))
        self.assertIn("tests fail", problems[0])
        self.assertEqual((rec["tests_run"], rec["tests_passed"], rec["tests_failed"], rec["tests_errored"]), (3, 1, 1, 1))
        problems, rec = self.run_src("import no_such_module_at_all\n")
        self.assertTrue(problems)
        self.assertEqual((rec["tests_errored"], rec["tests_passed"]), (1, 0))

    def test_each_test_runs_in_a_fresh_scratch_folder_and_cannot_prepare_another(self):
        problems, rec = self.run_tests(("test_a", "open('left-behind', 'w').write('x')"), ("test_b", "self.assertFalse(os.path.exists('left-behind'))"),
                                       ("test_c", "self.assertFalse(os.path.exists('/tmp/left-behind-tmp'))"))
        self.assertEqual(problems, [], problems)
        self.assertEqual(rec["tests_passed"], 3)

    def test_what_a_test_sees_from_inside(self):
        problems, rec = self.run_tests(("test_identity", f"self.assertEqual(os.getuid(), {self.b.sandbox.pw_uid})\nself.assertEqual(os.getgroups(), [])"),
                                       ("test_fds", "self.assertLessEqual(len(os.listdir('/proc/self/fd')), 6)"),
                                       ("test_env", "self.assertTrue(set(os.environ) <= {'PATH','HOME','TMPDIR','LANG','PYTHONDONTWRITEBYTECODE','PYTHONHASHSEED','TERM'})"),
                                       ("test_cannot_read_the_runner", f"self.assertRaises(OSError, open, '/proc/{os.getpid()}/environ', 'rb')\n"
                                                                       f"self.assertRaises(OSError, os.listdir, '/proc/{os.getpid()}/fd')"),
                                       ("test_cannot_write_elsewhere", "self.assertRaises(OSError, open, '/etc/lab-test-file', 'w')"))
        self.assertEqual(problems, [], problems)

    def test_a_forged_report_for_all_ids_from_inside_one_test_is_void(self):
        """test_b drives the harness's OWN emit function (so the sequence numbers and process id are right) and reports start, stop and a
        consistent done for ALL three ids, then exits 0. The supervisor gave that process exactly one id; a record for any other id is void, and the
        other two tests run for real in processes of their own."""
        src = mod(("test_a", "pass"),
                  ("test_b", r'''
import __main__
for x in ['test_a.T.test_b', 'test_a.T.test_a', 'test_a.T.test_c']:
    if x != 'test_a.T.test_b':
        __main__.emit({'e': 'start', 'id': x})
    __main__.emit({'e': 'stop', 'id': x, 'o': 'ok'})
__main__.emit({'e': 'done', 'run': 3, 'failures': 0, 'errors': 0, 'skipped': 0, 'xfail': 0, 'xpass': 0, 'ok': True})
os._exit(0)
'''), ("test_c", "self.fail('this test really fails')"))
        problems, rec = self.run_src(src)
        self.assertTrue(problems)
        self.assertLess(rec["tests_passed"], 3)
        self.assertIn("unexpected record", " ".join(problems))                # the extra start for an id the process was not given

    def test_a_test_cannot_vouch_for_a_later_failing_test(self):
        """The telling case: test_b reports 'ok' for itself AND for the later test_c, which really fails and is never run in that process. In the
        single-process harness (isolation none) that complete, consistent report is believed (pinned in test_verdict_integrity); here every id
        has a process of its own, so test_c fails for real and the forgery is void."""
        liar = r'''
import __main__
__main__.emit({'e': 'stop', 'id': 'test_a.T.test_b', 'o': 'ok'})
__main__.emit({'e': 'start', 'id': 'test_a.T.test_c'})
__main__.emit({'e': 'stop', 'id': 'test_a.T.test_c', 'o': 'ok'})
__main__.emit({'e': 'done', 'run': 3, 'failures': 0, 'errors': 0, 'skipped': 0, 'xfail': 0, 'xpass': 0, 'ok': True})
os._exit(0)
'''
        problems, rec = self.run_tests(("test_a", "pass"), ("test_b", liar), ("test_c", "self.fail('really fails')"))
        self.assertTrue(problems)
        self.assertEqual(rec["tests_passed"], 0)                    # no complete report: nothing is counted

    def test_RESIDUAL_a_test_can_claim_its_own_id_passed_but_nothing_more(self):
        """DOCUMENTED LIMIT: a test body that drives the harness's own emit for ITS OWN id ('ok', then a consistent done) and exits is believed for
        that id: it is indistinguishable from a test that asserts nothing. It cannot touch another id's process, record or exit status: the other
        tests here run for real and the one that fails still fails the run."""
        liar = r'''
import __main__
__main__.emit({'e': 'stop', 'id': 'test_a.T.test_b_liar', 'o': 'ok'})
__main__.emit({'e': 'done', 'run': 1, 'failures': 0, 'errors': 0, 'skipped': 0, 'xfail': 0, 'xpass': 0, 'ok': True})
os._exit(0)
'''
        problems, rec = self.run_tests(("test_a", "pass"), ("test_b_liar", liar), ("test_c", "pass"))
        self.assertEqual(problems, [], problems)                              # the liar's own claim stands ...
        self.assertEqual(rec["tests_passed"], 3)
        problems, rec = self.run_tests(("test_a", "pass"), ("test_b_liar", liar), ("test_c", "self.fail('real failure')"))
        self.assertTrue(problems)                                             # ... and cannot save a failing neighbour
        self.assertEqual((rec["tests_passed"], rec["tests_failed"]), (2, 1))

    def test_partial_oversized_duplicate_and_foreign_records_from_inside_are_void(self):
        head = ("args = open('/proc/self/cmdline','rb').read().rstrip(b'\\0').split(b'\\0')\n"
                "fd = int(args[args.index(b'run') + 2]) if b'run' in args else int(args[-1])\n")
        cases = {
            "half a line": head + "os.write(fd, b'{\"e\":\"c\",\"id\":\"x\",\"seq\":0,\"pi')\nos._exit(0)",
            "no final marker": head + "os.write(fd, b'{\"e\":\"c\",\"id\":\"test_a.T.test_x\",\"seq\":0,\"pid\":1}\\n')\nos._exit(0)",
            "oversized": head + "os.write(fd, b'{\"e\":\"c\",\"id\":\"' + b'x' * 5000 + b'\",\"seq\":0,\"pid\":1}\\n')\nos._exit(0)",
            "duplicate keys": head + "os.write(fd, b'{\"e\":\"c\",\"id\":\"a\",\"id\":\"b\",\"seq\":0,\"pid\":1}\\n')\nos._exit(0)",
            "text on the channel": head + "os.write(fd, b'Ran 1 test in 0.000s\\nOK\\n')\nos._exit(0)",
            "records of another attempt's ids": head + "import json\n"
                "for k, x in enumerate(['other.T.t1']):\n    os.write(fd, (json.dumps({'e':'c','id':x,'seq':k,'pid':os.getpid()})+'\\n').encode())\n"
                "os.write(fd, (json.dumps({'e':'collected','n':1,'seq':1,'pid':os.getpid()})+'\\n').encode())\nos._exit(0)",
        }
        for label, body in cases.items():
            with self.subTest(label):
                problems, rec = self.run_tests(("test_x", body))
                self.assertTrue(problems, label)
                self.assertEqual(rec["tests_passed"], 0, label)

    def test_early_exits_are_void_in_every_shape(self):
        cases = {
            "os._exit(0) in the test": mod(("test_x", "os._exit(0)")),
            "sys.exit(0) at import": "import sys\nsys.exit(0)\nimport unittest\nclass T(unittest.TestCase):\n    def test_x(self): pass\n",
            "kill -9 of itself": mod(("test_x", "import signal\nos.kill(os.getpid(), signal.SIGKILL)")),
            "fork and exit the parent": mod(("test_x", "if os.fork():\n    os._exit(0)\nimport time\ntime.sleep(0.5)")),
            "exit between collection and run": ("import os, unittest\nif b'run' in open('/proc/self/cmdline','rb').read().split(b'\\0'):\n    os._exit(0)\n"
                                                 "class T(unittest.TestCase):\n    def test_x(self): pass\n"),
            "exit 0 from an atexit handler after a failure": ("import atexit, os, unittest\natexit.register(lambda: os._exit(0))\n"
                                                              "class T(unittest.TestCase):\n    def test_x(self): self.fail('x')\n"),
            "segfault": mod(("test_x", "import ctypes\nctypes.string_at(0)")),
        }
        for label, src in cases.items():
            with self.subTest(label):
                problems, rec = self.run_src(src)
                self.assertTrue(problems, label)
                self.assertEqual(rec["tests_passed"], 0, label)

    def test_a_leftover_process_is_void_and_is_killed(self):
        problems, rec = self.run_tests(("test_x", "import subprocess, sys\nsubprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)  # lingering'], start_new_session=True)"))
        self.assertTrue(any("left" in p for p in problems), problems)
        self.assertGreaterEqual(rec["leftover_processes"], 1)
        self.assertEqual(ISO.uid_pids(self.b.sandbox.pw_uid), [])

    def test_timeouts_in_collect_and_in_run_are_void(self):
        R.SANDBOX_COLLECT_S = R.SANDBOX_PROC_S = 2
        t0 = time.time()
        problems, rec = self.run_src("import time\ntime.sleep(60)\n")
        self.assertTrue(any("out of time" in p for p in problems), problems)
        problems, rec = self.run_tests(("test_x", "import time\ntime.sleep(60)"))
        self.assertTrue(problems)
        self.assertLess(time.time() - t0, 20)
        self.assertEqual(ISO.uid_pids(self.b.sandbox.pw_uid), [])

    def test_resource_limits_a_fork_bomb_a_huge_allocation_a_huge_file_and_endless_output(self):
        bomb = mod(("test_x", "import time\nn = 0\ntry:\n    while n < 1000:\n        if os.fork() == 0:\n            time.sleep(30)\n            os._exit(0)\n        n += 1\n"
                              "except OSError:\n    pass\nopen('forks', 'w').write(str(n))\nassert n < 200, n"))
        problems, rec = self.run_src(bomb)
        self.assertTrue(problems)                                                    # the limit stopped it (and the leftovers were found and killed)
        self.assertEqual(ISO.uid_pids(self.b.sandbox.pw_uid), [])
        problems, rec = self.run_tests(("test_x", "x = bytearray(6 * 1024 ** 3)"))
        self.assertTrue(problems)
        self.assertEqual((rec["tests_passed"], rec["tests_errored"]), (0, 1))        # MemoryError, not a crash of the machine
        problems, rec = self.run_tests(("test_x", "f = open('big', 'wb')\nfor _ in range(200):\n    f.write(b'x' * 1024 * 1024)\n    f.flush()"))
        self.assertTrue(problems)                                                    # RLIMIT_FSIZE: SIGXFSZ or an error, never a pass
        self.assertEqual(rec["tests_passed"], 0)
        R.SANDBOX_PROC_S = 3
        problems, rec = self.run_tests(("test_x", "while True:\n    print('x' * 1000)"))
        self.assertTrue(problems)
        self.assertLessEqual(len(rec["output_tail"]), 300)

    def test_huge_output_is_bounded(self):
        problems, rec = self.run_tests(("test_x", "print('y' * 5_000_000)\nsys.stderr.write('z' * 5_000_000)"))
        self.assertEqual(problems, [])                                               # a lot of output is not a failure ...
        self.assertLessEqual(len(rec["output_tail"]), 300)                           # ... but only a tail is kept

    def test_a_hostile_filename_or_content_cannot_escape_the_scratch_folder(self):
        problems, rec = R.portable_run("/tmp", [("test_a.py", PASS3), ("notes.md", "# hi\n")])
        self.assertEqual(problems, [])
        self.assertEqual([n for n in os.listdir(tempfile.gettempdir()) if n.startswith("lab-sbx-")], [])      # the scratch folders are gone

    def test_attacks_on_the_runners_files_from_inside_a_test_fail(self):
        problems, rec = self.run_tests(
            ("test_a", f"self.assertRaises(OSError, open, '/proc/{os.getpid()}/mem', 'rb')"),
            ("test_b", f"self.assertRaises(OSError, os.listdir, '/proc/{os.getpid()}/fd')"),
            ("test_c", "self.assertRaises(OSError, open, '/etc/shadow', 'rb')"),
            ("test_d", "self.assertRaises(OSError, os.setuid, 0)"),
            ("test_e", "self.assertRaises(OSError, os.mkdir, '/usr/lib/evil')"))
        self.assertEqual(problems, [], problems)


@NEEDS_REAL
class HandleLoopIsolated(unittest.TestCase):
    """The real handle() with a real run_agent: the scripted verifier runs as the verify identity in the read-only copy; delivered tests run by
    the sandbox identity; fake kernel records what is reported."""
    CRIT = ["the tests pass", "the report is correct"]

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="hli-")
        os.chmod(self.tmp, 0o755)
        # LAB-RT: the stand-in verifier's notes must live OUTSIDE the world-writable folders (/tmp ...): the runner now deletes everything the verify and
        # sandbox identities own there after every session, so a note left in /tmp would be gone before the test reads it.
        self.coord = tempfile.mkdtemp(prefix="hli-coord-", dir="/opt")
        os.chmod(self.coord, 0o777)
        self.work = os.path.join(self.tmp, "jobs")
        os.makedirs(self.work); os.chmod(self.work, 0o755)
        self.reports = []
        self.saved = {n: getattr(R, n) for n in ("PW", "WORK", "api", "command", "_fingerprint", "guard_restore", "skill_counts", "POLL_S")}
        self.iso_saved = (ISO.REQUEST, ISO.VERIFIER_TOOLS)
        ISO.REQUEST = "identity"
        R.PW = pwd.getpwnam("agent")
        R.WORK, R.POLL_S = self.work, 0.05
        R.api, R.command = self.api, self.command
        R._fingerprint, R.guard_restore, R.skill_counts = (lambda: {}), (lambda before, label: []), (lambda: {})
        self.hooks = []
        self.verifier_script = None
        self.tests_src = "import unittest\nclass T(unittest.TestCase):\n    def test_a(self): self.assertEqual(1 + 1, 2)\n"

    def tearDown(self):
        ISO.REQUEST, ISO.VERIFIER_TOOLS = self.iso_saved
        for n, v in self.saved.items():
            setattr(R, n, v)
        ISO.kill_uid(ISO._getpw(ISO.VERIFY_USER).pw_uid); ISO.kill_uid(ISO._getpw(ISO.SANDBOX_USER).pw_uid)
        shutil.rmtree(self.tmp, ignore_errors=True)
        shutil.rmtree(self.coord, ignore_errors=True)

    def api(self, path, body=None):
        self.reports.append((path, body or {}))
        for h in self.hooks:
            h(path, body or {})
        if path.endswith("/deliver"):
            return 200, {"job": "d1", "redacted": []}
        if path.endswith("/delivery"):
            return 200, {"state": "done", "result": {"ok": True, "folder": "x", "saved": "y", "chars": 1}}
        return 200, {}

    WORKER = r'''
import os, sys
os.makedirs("deliver", exist_ok=True)
open("deliver/test_calc.py", "w").write(sys.argv[2])
open("EVIDENCE.md", "w").write("ran the tests\n")
'''
    VERIFIER = r'''
import json, os, re, subprocess, sys, time
prompt, coord = sys.argv[1], sys.argv[2]
m = re.search(r'Then write (\S+) exactly like \{"nonce": "([0-9a-f]+)", "task": "([^"]+)", "job": "([^"]+)", "attempt": (\d+)', prompt)
path, nonce, task, job, att = m.group(1), m.group(2), m.group(3), m.group(4), int(m.group(5))
def mark(name, text=""):
    open(os.path.join(coord, name), "w").write(text)
mark("verifier.json", json.dumps({"pid": os.getpid(), "uid": os.getuid(), "cwd": os.getcwd(), "path": path, "deliver": sorted(os.listdir("deliver"))}))
probe = {}
for name, fn in (() if not os.getcwd().startswith(%(vview)r) else (("write_view", lambda: open("deliver/test_calc.py", "w")), ("create_in_view", lambda: open("new.txt", "w")),
                 ("rename_in_view", lambda: os.rename("deliver/test_calc.py", "deliver/x.py")), ("write_evidence", lambda: open("EVIDENCE.md", "w")))):
    try:
        fn(); probe[name] = "ALLOWED"
    except OSError:
        probe[name] = "denied"
mark("view_probe.json", json.dumps(probe))
if os.path.exists(os.path.join(coord, "hang")):
    mark("verifier.hanging"); time.sleep(60)
if os.path.exists(os.path.join(coord, "inherit_forge")):
    # RESIDUAL: code the verifier runs (as the verify identity) that inherits its stdout rewrites both halves of the verdict
    verdict = {"nonce": nonce, "task": task, "job": job, "attempt": att, "passed": False,
               "criteria": [{"criterion": c, "met": False, "evidence": "honest"} for c in %(crit)r], "problems": ["the work is not done"]}
    open(path, "w").write(json.dumps(verdict))
    print("JARVIS-VERDICT: " + json.dumps(verdict), flush=True)
    forge = ("import mmap, os, sys\nfd = os.open(sys.argv[1], os.O_RDWR); m = mmap.mmap(fd, os.fstat(fd).st_size)\n"
             "flip = lambda b: b.replace(b'\"passed\": false', b'\"passed\": true ').replace(b'\"met\": false', b'\"met\": true ')\n"
             "data = bytes(m[:]); os.ftruncate(1, 0); os.write(1, b'JARVIS-VERDICT: ' + flip(data) + b'\\n'); m[:] = flip(data); m.flush()\n")
    subprocess.run([sys.executable, "-c", forge, path])
    sys.exit(0)
verdict = {"nonce": nonce, "task": task, "job": job, "attempt": att, "passed": True,
           "criteria": [{"criterion": c, "met": True, "evidence": "read the files"} for c in %(crit)r], "problems": []}
open(path, "w").write(json.dumps(verdict))
print("JARVIS-VERDICT: " + json.dumps(verdict), flush=True)
'''

    def command(self, kind, prompt, folder, max_turns=60):
        if prompt.startswith("You are an independent verifier"):
            return [sys.executable, "-c", self.VERIFIER % {"crit": self.CRIT, "vview": tempfile.gettempdir() + "/vview-"}, prompt, self.coord]
        return [sys.executable, "-c", self.WORKER, prompt, self.tests_src]

    def job(self, attempts=1):
        return {"id": "job0001", "params": {"task": "t1", "agent": "hermes", "instructions": "build it", "criteria": self.CRIT,
                                            "max_attempts": attempts, "timeout_s": 3000}}

    def posted(self, state):
        return [b for p, b in self.reports if p.endswith("/report") and b.get("state") == state]

    def last_verdict(self):
        for p, b in reversed(self.reports):
            if p.endswith("/report") and b.get("verification") is not None:
                return b["verification"]

    def test_the_verifier_runs_as_the_verify_identity_in_a_read_only_copy_and_the_tests_in_the_sandbox(self):
        ISO.VERIFIER_TOOLS = "sandboxed"
        R.handle(self.job())
        v = json.load(open(os.path.join(self.coord, "verifier.json")))
        self.assertEqual(v["uid"], ISO._getpw(ISO.VERIFY_USER).pw_uid)
        self.assertEqual(v["deliver"], ["test_calc.py"])
        self.assertNotEqual(v["uid"], R.PW.pw_uid)
        self.assertTrue(v["path"].startswith(tempfile.gettempdir() + "/vstate-"))
        self.assertEqual(json.load(open(os.path.join(self.coord, "view_probe.json"))), {"write_view": "denied", "create_in_view": "denied",
                                                                                       "rename_in_view": "denied", "write_evidence": "denied"})
        done = self.posted("completed")
        self.assertEqual(len(done), 1, self.reports)
        t = done[0]["trusted_execution"]
        self.assertEqual((t["harness"], t["isolation"]["mode"], t["tests_run"], t["isolation"]["processes"]), ("lab-harness/2", "full", 1, 2))
        self.assertIn("verified after 1", done[0]["result"])
        self.assertEqual(isolation_problem(t["isolation"]), "")
        self.assertEqual(done[0]["verification"]["harness_sha256"], t["harness_sha256"])
        self.assertEqual([n for n in os.listdir(tempfile.gettempdir()) if n.startswith(("vstate-", "vview-", "lab-sbx-", "harness-"))], [])

    def test_without_the_owners_statement_about_the_verifiers_tools_the_runner_says_NOT_verified(self):
        ISO.VERIFIER_TOOLS = ""
        R.handle(self.job())
        done = self.posted("completed")
        self.assertEqual(len(done), 1, self.reports)
        self.assertIn("NOT verified", done[0]["result"])
        self.assertEqual(done[0]["trusted_execution"]["isolation"]["mode"], "identity")

    def test_an_isolation_self_test_that_fails_means_none_and_NOT_verified(self):
        ISO.VERIFIER_TOOLS = "sandboxed"
        real = R.prepare_boundary

        def broken():
            vp = ISO._getpw(ISO.VERIFY_USER)
            b = ISO.Boundary("identity", [], sandbox=vp, verify=vp, agent=R.PW)       # a mis-resolved boundary: sandbox = verify identity
            ISO.run_self_test(b)
            return b
        R.prepare_boundary = broken
        try:
            R.handle(self.job())
        finally:
            R.prepare_boundary = real
        done = self.posted("completed")
        self.assertEqual(len(done), 1)
        iso = done[0]["trusted_execution"]["isolation"]
        self.assertEqual(iso["mode"], "none")
        self.assertIn("self-test", " ".join(iso["reasons"]))
        self.assertIn("NOT verified", done[0]["result"])

    def test_missing_identities_fail_closed(self):
        ISO.VERIFIER_TOOLS = "sandboxed"
        saved = ISO.SANDBOX_USER
        ISO.SANDBOX_USER = "jarvis-sandbox-missing"
        try:
            R.handle(self.job())
        finally:
            ISO.SANDBOX_USER = saved
        done = self.posted("completed")
        self.assertEqual(done[0]["trusted_execution"]["isolation"]["mode"], "none")
        self.assertIn("does not exist", " ".join(done[0]["trusted_execution"]["isolation"]["reasons"]))

    def test_a_verifier_killed_mid_run_ends_the_attempt_without_a_verdict(self):
        for sig in (signal.SIGTERM, signal.SIGKILL):
            with self.subTest(sig=sig.name):
                self.reports.clear()
                shutil.rmtree(os.path.join(self.work, "t1"), ignore_errors=True)
                for n in os.listdir(self.coord):
                    os.remove(os.path.join(self.coord, n))
                open(os.path.join(self.coord, "hang"), "w").close()
                ISO.VERIFIER_TOOLS = "sandboxed"

                def killer():
                    end = time.time() + 30
                    while not os.path.exists(os.path.join(self.coord, "verifier.hanging")) and time.time() < end:
                        time.sleep(0.01)
                    os.kill(json.load(open(os.path.join(self.coord, "verifier.json")))["pid"], sig)
                th = threading.Thread(target=killer)
                th.start()
                R.handle(self.job())
                th.join()
                self.assertEqual(self.posted("completed"), [], self.reports)
                self.assertIs(self.last_verdict()["passed"], False)
                self.assertEqual(ISO.uid_pids(ISO._getpw(ISO.VERIFY_USER).pw_uid), [])

    def test_RESIDUAL_code_the_verifier_runs_can_still_rewrite_the_verdict_so_it_is_never_verified_unless_the_owner_says_its_tools_are_sandboxed(self):
        """DOCUMENTED LIMIT: the verifier's own tools run delivered code as the verify identity, the same identity that owns the verdict state and
        the verifier's stdout. A child it starts that inherits fd 1 can truncate the captured answer and flip the file through a map: both halves
        then agree on a forged pass (the verifier's verdict was an honest FAIL). The runner cannot see that. What the boundary does is refuse to
        call it 'verified': the kernel keeps the pass as 'completed, NOT verified' unless the OWNER has stated the verifier's tools are sandboxed
        (then the forgery would be the owner's mis-statement). Either way the delivered tests passed in the sandbox for real."""
        open(os.path.join(self.coord, "inherit_forge"), "w").close()
        ISO.VERIFIER_TOOLS = ""
        R.handle(self.job())
        done = self.posted("completed")
        self.assertEqual(len(done), 1, self.reports)
        self.assertIs(done[0]["verification"]["passed"], True)                       # the forgery succeeded at the runner ...
        self.assertEqual(done[0]["trusted_execution"]["isolation"]["mode"], "identity")
        self.assertIn("NOT verified", done[0]["result"])                             # ... and is not a "verified" anywhere

    def test_a_verifier_that_hangs_is_stopped_at_its_deadline_and_the_attempt_fails(self):
        open(os.path.join(self.coord, "hang"), "w").close()
        ISO.VERIFIER_TOOLS = "sandboxed"
        old, R.VERIFY_TIMEOUT_S = R.VERIFY_TIMEOUT_S, 3
        t0 = time.time()
        try:
            R.handle(self.job())
        finally:
            R.VERIFY_TIMEOUT_S = old
        self.assertLess(time.time() - t0, 40)
        self.assertEqual(self.posted("completed"), [])
        self.assertIs(self.last_verdict()["passed"], False)
        self.assertEqual(ISO.uid_pids(ISO._getpw(ISO.VERIFY_USER).pw_uid), [])

    def test_the_kernel_not_answering_while_the_final_report_is_posted_is_retried_and_failure_is_never_called_success(self):
        ISO.VERIFIER_TOOLS = "sandboxed"
        for refuse, expect_ok in ((2, True), (99, False)):
            with self.subTest(refuse=refuse):
                self.reports.clear()
                shutil.rmtree(os.path.join(self.work, "t1"), ignore_errors=True)
                self.hooks.clear()
                seen = []
                real_api = self.api

                def flaky(path, body=None, seen=seen, refuse=refuse, real_api=real_api):
                    if path.endswith("/report") and (body or {}).get("state") == "completed":
                        seen.append(1)
                        if len(seen) <= refuse:
                            return 0, {"error": "URLError"}
                    return real_api(path, body)
                R.api = flaky
                R.handle(self.job())
                finishes = [b for p, b in self.reports if p.endswith("/finish")]
                self.assertEqual(finishes[-1]["result"]["ok"], expect_ok, finishes)
                self.assertEqual(len(seen), 3 if expect_ok else 5)

    def test_a_restart_kills_what_a_dead_runner_left_behind(self):
        """The runner is killed (-9) in the middle of a verification: its children are not. The next start sweeps the dedicated identities."""
        open(os.path.join(self.coord, "hang"), "w").close()
        ISO.VERIFIER_TOOLS = "sandboxed"
        driver = os.path.join(self.tmp, "driver.py")
        open(driver, "w").write(
            "import sys, json, os, pwd\nsys.path.insert(0, %r)\nimport lab_runner as R, lab_isolation as ISO\n"
            "R.PW = pwd.getpwnam('agent'); R.WORK = %r; R.POLL_S = 0.05\nISO.REQUEST = 'identity'; ISO.VERIFIER_TOOLS = 'sandboxed'\n"
            "R.api = lambda path, body=None: (200, {})\nR._fingerprint, R.guard_restore, R.skill_counts = (lambda: {}), (lambda b, l: []), (lambda: {})\n"
            "import runpy\nsrc = json.loads(sys.argv[1])\nR.command = lambda kind, prompt, folder, max_turns=60: ([sys.executable, '-c', src['verifier'], prompt, src['coord']] "
            "if prompt.startswith('You are an independent verifier') else [sys.executable, '-c', src['worker'], prompt, src['tests']])\n"
            "R.handle(%r)\n" % (os.path.join(HERE, ".."), self.work, self.job()))
        spec = json.dumps({"verifier": self.VERIFIER % {"crit": self.CRIT, "vview": tempfile.gettempdir() + "/vview-"}, "worker": self.WORKER, "tests": self.tests_src, "coord": self.coord})
        p = subprocess.Popen([sys.executable, driver, spec], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        end = time.time() + 40
        while not os.path.exists(os.path.join(self.coord, "verifier.hanging")) and time.time() < end:
            time.sleep(0.02)
        self.assertTrue(os.path.exists(os.path.join(self.coord, "verifier.hanging")))
        vuid = ISO._getpw(ISO.VERIFY_USER).pw_uid
        os.kill(p.pid, signal.SIGKILL)
        p.wait()
        self.assertTrue(ISO.uid_pids(vuid), "the verifier outlived its runner (that is the problem the start-up sweep solves)")
        R.sweep_dedicated()
        self.assertEqual(ISO.uid_pids(vuid), [])
        os.remove(os.path.join(self.coord, "hang"))                                  # and a fresh run on the same task still works
        shutil.rmtree(os.path.join(self.work, "t1"), ignore_errors=True)
        self.reports.clear()
        R.handle(self.job())
        self.assertEqual(len(self.posted("completed")), 1)


class KernelUnreachable(unittest.TestCase):
    """The final report: the kernel may not answer. The runner retries a bounded number of times and never claims success it did not deliver."""

    def test_post_final_retries_blips_stops_at_a_decision_and_gives_up(self):
        saved = (R.api, R.POLL_S)
        R.POLL_S = 0.01
        try:
            calls = []

            def flaky(path, body=None):
                calls.append(1)
                return (0, {}) if len(calls) < 3 else (200, {"status": "done"})
            R.api = flaky
            self.assertEqual(R.post_final({"x": 1})[0], 200)
            self.assertEqual(len(calls), 3)
            calls.clear()
            R.api = lambda path, body=None: (calls.append(1) or (409, {"reason": "no"}))
            self.assertEqual(R.post_final({"x": 1})[0], 409)
            self.assertEqual(len(calls), 1)                                          # a decision is not retried
            calls.clear()
            R.api = lambda path, body=None: (calls.append(1) or (0, {"error": "URLError"}))
            self.assertEqual(R.post_final({"x": 1}, tries=4)[0], 0)
            self.assertEqual(len(calls), 4)
        finally:
            R.api, R.POLL_S = saved


if __name__ == "__main__":
    unittest.main()
