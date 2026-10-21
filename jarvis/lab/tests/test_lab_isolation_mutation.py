"""Regression tests added after mutation testing of lab_isolation.py (2026-10-19). Plain functions only: no sandbox user is needed
(the process-killing and file-removing helpers are driven through patched /proc and os.kill, or through files in a temp folder)."""
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))
import lab_isolation as ISO


class FloatsInStrictJson(unittest.TestCase):
    def test_finite_numbers_keep_their_value(self):
        self.assertEqual(ISO.strict_loads('{"a": 1.5, "b": [0.25, -3.0], "c": 1e3}'), {"a": 1.5, "b": [0.25, -3.0], "c": 1000.0})
        self.assertEqual(ISO.strict_raw_decode('{"x": 2.5} tail')[0], {"x": 2.5})
        self.assertEqual(ISO._finite_float("0.1"), 0.1)

    def test_numbers_that_overflow_to_infinity_and_the_words_nan_infinity_are_refused(self):
        for text in ('{"a": 1e999}', '{"a": -1e999}', '[1e400]', '{"a": NaN}', '{"a": Infinity}', '{"a": -Infinity}'):
            with self.subTest(text=text):
                with self.assertRaises(ISO.StrictJSONError):
                    ISO.strict_loads(text)
        with self.assertRaises(ISO.StrictJSONError):
            ISO.strict_raw_decode('{"a": 1e999}')

    def test_error_text_does_not_echo_a_long_number(self):
        with self.assertRaises(ISO.StrictJSONError) as cm:
            ISO.strict_loads("[1" + "0" * 400 + "e999]")
        self.assertEqual(str(cm.exception), "the number '" + "1" + "0" * 19 + "' is not finite")        # the first 20 characters, no more


class SandboxEnv(unittest.TestCase):
    def test_only_allow_listed_names_reach_the_sandbox(self):
        env = ISO.sandbox_env("/scratch", {"LANG": "en_US.UTF-8", "TERM": "xterm", "LD_PRELOAD": "/evil.so", "PYTHONPATH": "/evil",
                                          "ANTHROPIC_API_KEY": "CANARY-SYNTH", "PATH": "/evil/bin"})
        self.assertEqual(env["LANG"], "en_US.UTF-8")
        self.assertEqual(env["TERM"], "xterm")
        self.assertEqual(env["PATH"], "/evil/bin")                 # PATH is on the allow list (the runner passes its own)
        for bad in ("LD_PRELOAD", "PYTHONPATH", "ANTHROPIC_API_KEY"):
            self.assertNotIn(bad, env)
        self.assertEqual(env["HOME"], "/scratch")
        self.assertEqual(set(ISO.sandbox_env("/s")), {"PATH", "HOME", "TMPDIR", "LANG", "PYTHONDONTWRITEBYTECODE", "PYTHONHASHSEED", "TERM"})


class KillUid(unittest.TestCase):
    def test_root_and_the_runner_itself_are_never_killed(self):
        sent = []
        with mock.patch.object(ISO, "uid_pids", return_value=[4242]), mock.patch.object(ISO.os, "kill", side_effect=lambda p, s: sent.append((p, s))):
            self.assertEqual(ISO.kill_uid(0, wait_s=0.05), ([], []))
            self.assertEqual(ISO.kill_uid(os.geteuid(), wait_s=0.05), ([], []))
        self.assertEqual(sent, [])

    def test_a_dedicated_identity_is_stopped_then_killed_and_survivors_are_reported_after_the_deadline(self):
        other = os.geteuid() + 1000
        sent = []
        t0 = time.monotonic()
        with mock.patch.object(ISO, "uid_pids", return_value=[4242, 4343]), mock.patch.object(ISO.os, "kill", side_effect=lambda p, s: sent.append((p, s))):
            killed, left = ISO.kill_uid(other, wait_s=0.15)
        self.assertGreaterEqual(time.monotonic() - t0, 0.14)         # it kept trying until its deadline, then reported
        self.assertEqual(killed, [4242, 4343])
        self.assertEqual(left, [4242, 4343])                        # they never went away: reported, not hidden
        import signal
        self.assertIn((4242, signal.SIGKILL), sent)
        self.assertIn((4343, signal.SIGCONT), sent)

    def test_returns_the_killed_pids_once_none_are_left(self):
        seq = iter([[11, 12], []])
        with mock.patch.object(ISO, "uid_pids", side_effect=lambda uid: next(seq)), mock.patch.object(ISO.os, "kill"):
            self.assertEqual(ISO.kill_uid(os.geteuid() + 1000, wait_s=5), ([11, 12], []))

    def test_an_already_gone_process_is_not_an_error(self):
        seq = iter([[11], []])
        with mock.patch.object(ISO, "uid_pids", side_effect=lambda uid: next(seq)), mock.patch.object(ISO.os, "kill", side_effect=ProcessLookupError):
            self.assertEqual(ISO.kill_uid(os.geteuid() + 1000, wait_s=5), ([11], []))


class PurgeFiles(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.d = self.tmp.name
        # files the identity under test owns: our own uid, or (as root) a made-up non-root uid
        self.uid = os.geteuid() if os.geteuid() != 0 else 54321

    def tearDown(self):
        self.tmp.cleanup()

    def make(self, rel, owner=None):
        p = os.path.join(self.d, rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w") as f:
            f.write("x")
        if os.geteuid() == 0:
            os.chown(p, self.uid if owner is None else owner, -1)
        return p

    def test_removes_exactly_what_the_identity_owns_and_nothing_else(self):
        a, b = self.make("a.txt"), self.make("sub/b.txt")
        sub = os.path.join(self.d, "sub")
        if os.geteuid() == 0:
            os.chown(sub, self.uid, -1)
            keep = self.make("root-owned.txt", owner=0)
            expect = 3
        else:
            keep = None
            expect = 3                                       # a, b and the folder, all ours
        with mock.patch.object(ISO, "purge_ipc", side_effect=AssertionError("not for an explicit folder list")):
            n = ISO.purge_uid_files(self.uid, dirs=[self.d])
        self.assertEqual(n, expect)
        self.assertFalse(os.path.exists(a))
        self.assertFalse(os.path.exists(b))
        self.assertFalse(os.path.exists(sub))
        if keep:
            self.assertTrue(os.path.exists(keep))

    def test_symlinks_are_removed_not_followed(self):
        target = tempfile.NamedTemporaryFile(delete=False)
        target.close()
        try:
            link = os.path.join(self.d, "link")
            os.symlink(target.name, link)
            if os.geteuid() == 0:
                os.lchown(link, self.uid, -1)
            self.assertEqual(ISO.purge_uid_files(self.uid, dirs=[self.d]), 1)
            self.assertFalse(os.path.lexists(link))
            self.assertTrue(os.path.exists(target.name))
        finally:
            os.unlink(target.name)

    def test_root_and_none_are_never_purged(self):
        p = self.make("mine.txt", owner=0 if os.geteuid() == 0 else None)
        self.assertEqual(ISO.purge_uid_files(0, dirs=[self.d]), 0)
        self.assertEqual(ISO.purge_uid_files(None, dirs=[self.d]), 0)
        self.assertTrue(os.path.exists(p))

    def test_the_shared_folders_and_the_ipc_objects_are_counted_when_no_folder_list_is_given(self):
        self.make("a.txt")
        with mock.patch.object(ISO, "SHARED_DIRS", (self.d,)), mock.patch.object(ISO, "purge_ipc", return_value=5) as ipc:
            self.assertEqual(ISO.purge_uid_files(self.uid), 1 + 5)
        ipc.assert_called_once_with(self.uid)

    def test_a_missing_folder_is_skipped(self):
        self.assertEqual(ISO.purge_uid_files(self.uid, dirs=[os.path.join(self.d, "nope")]), 0)


class PurgeIpc(unittest.TestCase):
    def run_with(self, **kw):
        with mock.patch.object(ISO.subprocess, "run", return_value=mock.Mock(**kw)) as run:
            return ISO.purge_ipc(os.getuid()), run

    def test_count_comes_from_the_helper_only_when_it_exits_zero(self):
        n, run = self.run_with(returncode=0, stdout=b"3\n")
        self.assertEqual(n, 3)
        self.assertEqual(run.call_args.kwargs["timeout"], 20)
        self.assertEqual(self.run_with(returncode=0, stdout=b"")[0], 0)
        self.assertEqual(self.run_with(returncode=1, stdout=b"7\n")[0], 0)

    def test_failures_to_start_the_helper_are_zero(self):
        for exc in (OSError("x"), subprocess.TimeoutExpired("c", 20)):
            with mock.patch.object(ISO.subprocess, "run", side_effect=exc):
                self.assertEqual(ISO.purge_ipc(os.getuid()), 0)
        self.assertEqual(ISO.purge_ipc(2 ** 31 - 5), 0)             # no such user


if __name__ == "__main__":
    unittest.main()
