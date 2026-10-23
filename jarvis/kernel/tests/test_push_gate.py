"""The push gate (plan P6 step 8): main moves only when the kernel's own checklist passes, on real git repositories."""
import os
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from jarvis_kernel import push_gate as G  # noqa: E402
from jarvis_kernel.policy import RED, Action, decide  # noqa: E402

ENV = {**os.environ, "GIT_AUTHOR_NAME": "agent", "GIT_AUTHOR_EMAIL": "a@example.invalid", "GIT_COMMITTER_NAME": "agent",
       "GIT_COMMITTER_EMAIL": "a@example.invalid", "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_SYSTEM": os.devnull}


class Repo:
    def __init__(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = self.tmp.name
        self.run("init", "-q", "-b", "main")
        self.commit({"app.py": "print('hi')\n", "tests/test_app.py": "def test_x():\n    assert True\n"}, "start")

    def run(self, *args):
        return subprocess.run(["git", "-C", self.path, *args], capture_output=True, text=True, check=True, env=ENV).stdout.strip()

    def commit(self, files, msg, trailer=True):
        for name, text in files.items():
            p = os.path.join(self.path, name)
            os.makedirs(os.path.dirname(p), exist_ok=True)
            with open(p, "w") as f:
                f.write(text)
        self.run("add", "-A")
        self.run("commit", "-q", "-m", msg + ("\n\nAgent-Run: run-1234" if trailer else ""))
        return self.run("rev-parse", "HEAD")

    def branch(self, name):
        self.run("checkout", "-q", "-b", name)


def ok_state(sha, **kw):
    return G.GateState(killed=False, enabled=True, test_runs={sha: True}, **kw)


class Gate(unittest.TestCase):
    def setUp(self):
        self.r = Repo()
        self.r.branch("agent/fix")
        self.git = G.make_git(self.r.path)

    def tearDown(self):
        self.r.tmp.cleanup()

    def eval(self, sha, state):
        return G.evaluate("demo", "main", sha, state, self.git)

    def failed(self, v):
        return [c.name for c in v.checks if not c.ok]

    def test_a_clean_small_fix_passes_every_check(self):
        sha = self.r.commit({"app.py": "print('hello')\n"}, "Fix greeting")
        v = self.eval(sha, ok_state(sha))
        self.assertTrue(v.ok, v.checklist())
        self.assertIn("ALL CHECKS PASS", v.checklist())
        self.assertEqual([c.name for c in v.checks], ["kill_switch", "enabled", "fast_forward", "size", "protected", "trailer",
                                                      "tests", "daily_cap", "one_at_a_time"])

    def test_kill_switch_and_gate_off(self):
        sha = self.r.commit({"app.py": "x\n"}, "Fix")
        self.assertEqual(self.failed(self.eval(sha, G.GateState(killed=True, enabled=False, test_runs={sha: True}))), ["kill_switch", "enabled"])

    def test_changing_tests_needs_red(self):
        sha = self.r.commit({"tests/test_app.py": "def test_x():\n    pass\n"}, "Make test pass")
        v = self.eval(sha, ok_state(sha))
        self.assertEqual(self.failed(v), ["protected"])
        self.assertIn("tests/test_app.py", v.checklist())
        base = self.r.run("rev-parse", "main")
        self.assertTrue(self.eval(sha, ok_state(sha, red_approved_range=f"{base}..{sha}")).ok)
        self.assertFalse(self.eval(sha, ok_state(sha, red_approved_range=f"{base}..{base}")).ok)

    def test_every_kind_of_protected_path(self):
        for path in (".github/workflows/ci.yml", ".claude/settings.json", "CLAUDE.md", ".mcp.json", "jarvis/kernel/jarvis_kernel/policy.py",
                     "config/thresholds.yaml", "evals/baseline.json", "src/conftest.py", "web/app.spec.ts", "fixtures/a.json",
                     "deploy/secret.txt", ".env.production", "jarvis/config/admin_verbs.json"):
            with self.subTest(path=path):
                r = Repo()
                r.branch("agent/x")
                sha = r.commit({path: "x\n"}, "change")
                v = G.evaluate("demo", "main", sha, ok_state(sha), G.make_git(r.path))
                self.assertIn("protected", self.failed(v), path)
                r.tmp.cleanup()

    def test_renaming_a_test_away_is_caught(self):
        self.r.run("mv", "tests/test_app.py", "app_notes.py")
        self.r.run("commit", "-q", "-m", "tidy\n\nAgent-Run: run-1234")
        sha = self.r.run("rev-parse", "HEAD")
        self.assertIn("protected", self.failed(self.eval(sha, ok_state(sha))))

    def test_size_limits(self):
        sha = self.r.commit({"big.py": "x = 1\n" * (G.MAX_LINES + 1)}, "Big change")
        self.assertEqual(self.failed(self.eval(sha, ok_state(sha))), ["size"])
        r = Repo()
        r.branch("agent/many")
        sha = r.commit({f"m{i}.py": "x\n" for i in range(G.MAX_FILES + 1)}, "Many files")
        self.assertEqual(self.failed(G.evaluate("demo", "main", sha, ok_state(sha), G.make_git(r.path))), ["size"])
        r.tmp.cleanup()

    def test_every_commit_needs_the_trailer(self):
        self.r.commit({"a.py": "1\n"}, "one")
        sha = self.r.commit({"b.py": "2\n"}, "two", trailer=False)
        v = self.eval(sha, ok_state(sha))
        self.assertEqual(self.failed(v), ["trailer"])
        self.assertIn(sha[:8], v.checklist())

    def test_tests_must_be_the_kernels_run_on_this_exact_commit(self):
        first = self.r.commit({"a.py": "1\n"}, "one")
        sha = self.r.commit({"a.py": "2\n"}, "two")
        self.assertEqual(self.failed(self.eval(sha, ok_state(first))), ["tests"])          # passed on an earlier commit only
        self.assertEqual(self.failed(self.eval(sha, G.GateState(False, True, test_runs={sha: False}))), ["tests"])

    def test_not_a_fast_forward(self):
        sha = self.r.commit({"a.py": "1\n"}, "work")
        self.r.run("checkout", "-q", "main")
        self.r.commit({"app.py": "moved on\n"}, "main moved")
        v = self.eval(sha, ok_state(sha))
        self.assertEqual(self.failed(v), ["fast_forward"])
        self.assertIn("not a fast-forward", v.checklist())

    def test_nothing_new_and_bad_ids(self):
        base = self.r.run("rev-parse", "main")
        self.assertIn("fast_forward", self.failed(self.eval(base, ok_state(base))))
        self.assertIn("candidate", self.failed(self.eval("main", ok_state("main"))))
        self.assertIn("repository", self.failed(self.eval("f" * 40, ok_state("f" * 40))))

    def test_daily_cap_and_one_at_a_time_from_the_ledger(self):
        sha = self.r.commit({"a.py": "1\n"}, "Fix")
        with tempfile.TemporaryDirectory() as d:
            led = G.Ledger(os.path.join(d, "gate.jsonl"), clock=lambda: 1_800_000_000.0)
            for i in range(G.DAILY_CAP):
                led.record("demo", f"c{i}", "merged")
            led.record("demo", "busy", "started")
            st = ok_state(sha, merges_today=led.merges_today("demo"), in_progress=led.in_progress("demo"))
            self.assertEqual(self.failed(self.eval(sha, st)), ["daily_cap", "one_at_a_time"])
            led.record("demo", "busy", "merged")
            self.assertFalse(led.in_progress("demo"))
            self.assertEqual(led.merges_today("other"), 0)

    def test_revert_plan(self):
        steps = G.revert_plan("a" * 40, "b" * 40)
        self.assertEqual(steps[0][:4], ["git", "revert", "--no-edit", "--no-commit"])
        self.assertIn("Agent-Run: kernel-revert", steps[1][-1])

    def test_the_gate_itself_is_console_only(self):
        self.assertEqual(decide(Action("fs", "write", "/opt/jarvis/hq/kernel/jarvis_kernel/push_gate.py", {}, "private", False)).level, RED)


if __name__ == "__main__":
    unittest.main()
