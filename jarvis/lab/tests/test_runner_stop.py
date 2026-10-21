"""How the lab runner decides to stop a running worker, and that the stop reaches every process the worker started.

These run anywhere with /proc (no `agent` user, no root, no lab box needed): the decision is the pure function
`follow_status` and the kill is `kill_tree`, both importable because lab_runner no longer fails at import when the lab's
`agent` user is missing (the rest of the runner is still tested on the lab box, see test_lab_runner.py).

Audit SEC-D10 (2026-10-18): the kill switch did not stop a lab job that was already running, because the status the runner
polls carried only the task's state.
"""
import os
import re
import signal
import subprocess
import sys
import tempfile
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))
import lab_runner as R

LINUX = os.path.isdir("/proc") and hasattr(os, "killpg")
REAL_POPEN = subprocess.Popen          # R.subprocess is the same module: patching R.subprocess.Popen patches it for everyone


class FollowStatus(unittest.TestCase):
    def test_the_kill_switch_stops_the_worker_whatever_the_task_says(self):
        for state in ("running", "verifying", "repairing", "paused", "queued"):
            self.assertEqual(R.follow_status(200, {"state": state, "killed": True}), ("stop", R.STOPPED_BY_KILL), state)
        self.assertEqual(R.follow_status(200, {"state": "running", "killed": True}, frozen=True), ("stop", "stopped by the kill switch"))

    def test_the_kernels_own_kill_refusal_stops_too(self):
        self.assertEqual(R.follow_status(503, {"status": "refused", "reason": "kill switch is on"}), ("stop", R.STOPPED_BY_KILL))

    def test_a_closed_or_cancelled_task_stops_it_as_before(self):
        self.assertEqual(R.follow_status(200, {"state": "cancelled"}), ("stop", "stopped by the owner"))
        self.assertEqual(R.follow_status(200, {"state": "failed"}), ("stop", "task closed"))
        self.assertEqual(R.follow_status(200, {"state": "completed"}), ("stop", "task closed"))
        self.assertEqual(R.follow_status(200, {"state": "running", "cancel_requested": True}), ("stop", "stopped by the owner"))

    def test_pause_and_resume(self):
        self.assertEqual(R.follow_status(200, {"state": "paused"}, frozen=False), ("freeze", ""))
        self.assertEqual(R.follow_status(200, {"state": "paused"}, frozen=True), ("none", ""))
        self.assertEqual(R.follow_status(200, {"state": "running"}, frozen=True), ("thaw", ""))
        self.assertEqual(R.follow_status(200, {"state": "running"}, frozen=False), ("none", ""))

    def test_a_blip_never_stops_the_work(self):
        for code, reply in ((0, {"error": "URLError"}), (500, {}), (502, {"reason": "bad gateway"}), (404, {"reason": "no running lab job"}),
                            (503, {"reason": "overloaded"}), (200, None), (200, "garbage"), (200, {}), (200, {"state": None, "killed": False})):
            self.assertEqual(R.follow_status(code, reply)[0], "none", (code, reply))

    def test_only_a_real_true_counts_as_killed(self):
        for v in ("true", 1, "yes", [], {}):
            self.assertNotEqual(R.follow_status(200, {"state": "running", "killed": v}), ("stop", R.STOPPED_BY_KILL), v)


def spawn_tree(marker_dir):
    """A worker (new session, like run_agent) that starts a child in its process group and a grandchild that starts its OWN
    session, as a daemonised helper would. Returns (worker Popen, [pids of the child, the escaped grandchild])."""
    code = (
        "import os, subprocess, sys, time\n"
        "d = sys.argv[1]\n"
        "esc = subprocess.Popen([sys.executable, '-c', 'import os,time;open(os.path.join(%r,\"esc\"),\"w\").write(str(os.getpid()));time.sleep(120)' % d],"
        " start_new_session=True)\n"
        "kid = subprocess.Popen([sys.executable, '-c', 'import os,time;open(os.path.join(%r,\"kid\"),\"w\").write(str(os.getpid()));time.sleep(120)' % d])\n"
        "time.sleep(120)\n")
    p = REAL_POPEN([sys.executable, "-c", code, marker_dir], start_new_session=True)
    for _ in range(100):
        if all(os.path.exists(os.path.join(marker_dir, n)) and os.path.getsize(os.path.join(marker_dir, n)) for n in ("esc", "kid")):
            break
        time.sleep(0.1)
    pids = []
    for n in ("kid", "esc"):
        with open(os.path.join(marker_dir, n)) as fh:
            pids.append(int(fh.read()))
    return p, pids


def dead(pid):
    return not R._alive(pid)


@unittest.skipUnless(LINUX, "needs /proc and process groups")
class KillTree(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.p, self.kids = spawn_tree(self.tmp.name)

    def tearDown(self):
        for pid in [self.p.pid, *self.kids]:
            try:
                os.kill(pid, signal.SIGKILL)
            except OSError:
                pass
        self.p.wait()
        self.tmp.cleanup()

    def test_the_tree_includes_a_helper_that_left_the_process_group(self):
        tree = R.process_tree(self.p.pid)
        for pid in [self.p.pid, *self.kids]:
            self.assertIn(pid, tree)
        self.assertNotEqual(os.getpgid(self.kids[1]), self.p.pid)         # the escaped one is NOT in the worker's group

    def test_a_kill_reaches_the_worker_its_children_and_the_helper_that_escaped_its_group(self):
        t0 = time.time()
        tree = R.kill_tree(self.p.pid, hard=True)
        self.p.wait(10)
        deadline = time.time() + 5
        while time.time() < deadline and not all(dead(x) for x in self.kids):
            time.sleep(0.1)
        self.assertLess(time.time() - t0, 5)                              # at once: no grace period for the kill switch
        self.assertTrue(dead(self.p.pid))
        for pid in self.kids:
            self.assertTrue(dead(pid), f"{pid} survived")
        self.assertGreaterEqual(len(tree), 3)

    def test_a_frozen_worker_is_still_killed(self):
        os.killpg(self.p.pid, signal.SIGSTOP)                              # the owner paused the task, then the kill switch went on
        R.kill_tree(self.p.pid, hard=True)
        self.p.wait(10)
        deadline = time.time() + 5
        while time.time() < deadline and not all(dead(x) for x in self.kids):
            time.sleep(0.1)
        self.assertTrue(dead(self.p.pid) and all(dead(x) for x in self.kids))

    def test_a_polite_stop_also_takes_the_whole_tree(self):
        R.kill_tree(self.p.pid, hard=False)                                # the owner's cancel: TERM first
        self.p.wait(20)
        deadline = time.time() + 20
        while time.time() < deadline and not all(dead(x) for x in self.kids):
            time.sleep(0.2)
        self.assertTrue(all(dead(x) for x in self.kids))


@unittest.skipUnless(LINUX, "needs /proc and process groups")
class RunAgentLoop(unittest.TestCase):
    """run_agent's own loop against a stand-in worker: the real polling, the real kill, a fake kernel."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.saved = (R.subprocess.Popen, R.api, R.agent_env, R.command, R.STATUS_EVERY_S)
        self.replies = []
        self.spawned = None

        def popen(argv, **kw):
            kw.pop("stdin", None)
            self.spawned = REAL_POPEN(["sh", "-c", "sleep 120 & sleep 120"], stdin=subprocess.DEVNULL, **kw)
            return self.spawned
        R.subprocess.Popen = popen
        R.api = lambda path, body=None: self.replies.pop(0) if len(self.replies) > 1 else self.replies[0]
        R.agent_env, R.command, R.STATUS_EVERY_S = (lambda kind: {}), (lambda *a, **k: ["x"]), 0.2

    def tearDown(self):
        R.subprocess.Popen, R.api, R.agent_env, R.command, R.STATUS_EVERY_S = self.saved
        if self.spawned is not None:
            try:
                os.killpg(self.spawned.pid, signal.SIGKILL)
            except OSError:
                pass
            self.spawned.wait()
        self.tmp.cleanup()

    def run_it(self, timeout=60):
        t0 = time.time()
        rc, secs, why = R.run_agent("job1", "hermes", "p", self.tmp.name, timeout, "w.log")
        return rc, why, time.time() - t0

    def test_the_kill_switch_stops_a_running_worker_and_says_so(self):
        self.replies = [(200, {"state": "running"}), (200, {"state": "running"}), (200, {"state": "running", "killed": True})]
        rc, why, took = self.run_it()
        self.assertEqual((rc, why), (None, "stopped by the kill switch"))
        self.assertLess(took, 15)
        pids = [self.spawned.pid]
        time.sleep(0.3)
        self.assertFalse(R._alive(pids[0]))
        self.assertEqual(R.process_tree(pids[0]), [pids[0]])               # nothing of the worker's is left under it

    def test_the_owner_cancelling_the_task_still_stops_it(self):
        self.replies = [(200, {"state": "cancelled"})]
        self.assertEqual(self.run_it()[:2], (None, "stopped by the owner"))

    def test_a_kernel_that_does_not_answer_does_not_stop_the_work(self):
        self.replies = [(0, {"error": "URLError"}), (0, {"error": "URLError"}), (0, {"error": "URLError"}), (200, {"state": "completed"})]
        self.assertEqual(self.run_it()[:2], (None, "task closed"))          # it only ended because the task did, after several blips


@unittest.skipUnless(LINUX, "needs /proc and process groups")
class RunAgentCapture(unittest.TestCase):
    """Re-review H2 (2026-10-18): run_agent captures the session's stdout for the runner (the verifier's final answer) and tells the runner
    the moment the session has exited, before anything is swept. Real process (runuser is a shim off the lab box), real files."""

    SCRIPT = (
        "import sys\n"
        "flags = [l.split()[1] for l in open('/proc/self/fdinfo/1') if l.startswith('flags:')][0]\n"
        "sys.stderr.write('stdout flags ' + flags + '\\n'); sys.stderr.flush()\n"
        "print('JARVIS-VERDICT: {\"passed\": false}', flush=True)\n")

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        shim = os.path.join(self.tmp.name, "bin")
        os.makedirs(shim)
        with open(os.path.join(shim, "runuser"), "w") as fh:
            fh.write("#!/bin/sh\nshift 3\nexec \"$@\"\n")
        os.chmod(os.path.join(shim, "runuser"), 0o755)
        self.path0 = os.environ["PATH"]
        os.environ["PATH"] = shim + os.pathsep + self.path0
        self.saved = (R.api, R.agent_env, R.command, R.PW)
        R.api, R.agent_env, R.PW = (lambda path, body=None: (200, {"state": "running"})), (lambda kind: {}), None
        R.command = lambda kind, prompt, folder, max_turns=60: [sys.executable, "-c", self.SCRIPT]
        self.work = os.path.join(self.tmp.name, "w")
        os.makedirs(self.work)

    def tearDown(self):
        os.environ["PATH"] = self.path0
        R.api, R.agent_env, R.command, R.PW = self.saved
        self.tmp.cleanup()

    def test_stdout_goes_to_the_runners_capture_stderr_to_the_log_and_on_exit_runs_after_the_exit(self):
        out, seen = R.VerifierOutput(), []
        try:
            rc, secs, why = R.run_agent("job1", "hermes", "p", self.work, 60, "v.log", capture=out, on_exit=lambda: seen.append(out.read()))
            self.assertEqual((rc, why), (0, ""))
            self.assertEqual(seen, [b'JARVIS-VERDICT: {"passed": false}\n'])            # already complete when the callback ran
            self.assertEqual(out.read(), seen[0])
            log = open(os.path.join(self.work, "v.log")).read()
            flags = int(re.search(r"stdout flags (\d+)", log).group(1), 8)
            self.assertTrue(flags & os.O_APPEND and (flags & 3) == os.O_WRONLY, oct(flags))   # the session's fd 1: write-only, append-only
            self.assertIn("--- stdout ---", log)
            self.assertIn("JARVIS-VERDICT", log.split("--- stdout ---")[1])
        finally:
            out.close()

    def test_without_a_capture_both_streams_still_go_to_the_log(self):
        rc, secs, why = R.run_agent("job1", "hermes", "p", self.work, 60, "v.log")
        log = open(os.path.join(self.work, "v.log")).read()
        self.assertEqual((rc, why), (0, ""))
        self.assertIn("JARVIS-VERDICT", log); self.assertIn("stdout flags", log)


if __name__ == "__main__":
    unittest.main()
