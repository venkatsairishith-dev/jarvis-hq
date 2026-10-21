"""ST-2 (2026-10-19): runtime.cancel() used to swallow a failed kill and still return True, so a job whose program survived was reported
as stopped. A real process is started through runtime.popen; the kill is made to fail; cancel must say so."""
import os
import subprocess
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "node"))
from jarvis_node import runtime  # noqa: E402


class Cancel(unittest.TestCase):
    def tearDown(self):
        runtime.end()

    def start(self, jid="job-1"):
        runtime.begin(jid)
        return runtime.popen([sys.executable, "-c", "import time; time.sleep(60)"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def test_a_successful_cancel_kills_the_process_and_says_true(self):
        p = self.start()
        self.assertTrue(runtime.cancel("job-1"))
        self.assertIsNotNone(p.poll())
        self.assertTrue(runtime.cancelled())

    def test_a_failed_kill_is_reported_not_swallowed(self):
        p = self.start()
        try:
            with mock.patch.object(runtime, "_kill_tree", side_effect=PermissionError("access denied")):
                running, errors = runtime.cancel_detail("job-1")
                self.assertTrue(running)
                self.assertEqual(len(errors), 1)
                self.assertIn("PermissionError", errors[0])
                self.assertFalse(runtime.cancel("job-1"))                # was True before the fix
            self.assertTrue(runtime.cancelled())                          # the job is still flagged: its next step stops
            self.assertIsNone(p.poll())                                   # and the program really is still alive
        finally:
            p.kill()
            p.wait()

    def test_a_process_that_survives_the_kill_is_a_failure(self):
        p = self.start()
        try:
            with mock.patch.object(p, "wait", side_effect=subprocess.TimeoutExpired("x", 5)), mock.patch.object(p, "kill"):
                self.assertFalse(runtime.cancel("job-1"))
        finally:
            p.kill()

    def test_another_jobs_id_is_not_running_and_not_an_error(self):
        self.start()
        self.assertEqual(runtime.cancel_detail("job-2"), (False, []))
        self.assertFalse(runtime.cancel("job-2"))
        self.assertFalse(runtime.cancel(None))


class ChildEnvironment(unittest.TestCase):
    """Completion E (2026-10-19): children the node starts do not inherit a GitHub/git credential from the node's environment."""
    NAMES = ("GH_TOKEN", "GITHUB_TOKEN", "GH_CONFIG_DIR", "GITHUB_PERSONAL_ACCESS_TOKEN", "GIT_ASKPASS", "GIT_CONFIG_GLOBAL", "GIT_SSH_COMMAND",
             "SSH_AUTH_SOCK", "NETRC", "gh_enterprise_token")

    def test_safe_environ_and_powershell_drop_them_and_keep_the_rest(self):
        with mock.patch.dict(os.environ, {**{n: "CANARY-7f3a-Priyanka-SYNTH" for n in self.NAMES}, "JARVIS_KEEP_ME": "1"}):
            env = runtime.safe_environ()
            for n in self.NAMES:
                self.assertNotIn(n, env)
            self.assertEqual(env.get("JARVIS_KEEP_ME"), "1")
            with mock.patch.object(runtime, "run") as run:
                runtime.powershell("Write-Output 1", {"EXPLICIT": "x"})
            sent = run.call_args.kwargs["env"]
            self.assertEqual(sent["EXPLICIT"], "x")
            self.assertFalse([n for n in self.NAMES if n in sent])


if __name__ == "__main__":
    unittest.main()
