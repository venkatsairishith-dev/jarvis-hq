"""Completion H (2026-10-19): no GitHub/git credential is inherited by an agent, a sub-agent or a node job through its environment.
Synthetic names and values only; no real credential is read. Run from jarvis/lab/tests:  python -m unittest test_credential_env"""
import importlib.util
import os
import sys
import tempfile
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, ".."))
import test_jarvis_mcp as tm            # noqa: E402  its loader stubs the MCP SDK when it is not installed
import lab_runner as R                  # noqa: E402

SYNTH = "SYNTHETIC-not-a-token-0000"
NAMES = ("GH_TOKEN", "GITHUB_TOKEN", "GH_ENTERPRISE_TOKEN", "GITHUB_PERSONAL_ACCESS_TOKEN", "GH_CONFIG_DIR", "GH_HOST", "GITHUB_ACTIONS", "GIT_ASKPASS",
         "GIT_CONFIG_GLOBAL", "GIT_CONFIG_COUNT", "GIT_CONFIG_KEY_0", "GIT_SSH_COMMAND", "GIT_CREDENTIAL_HELPER", "SSH_AUTH_SOCK", "NETRC", "gh_token", "github_token")


FAKE_PW = type("PW", (), {"pw_uid": 4242, "pw_dir": "/home/agent", "pw_name": "agent"})()      # the lab box's agent user does not exist here


class Hermes(unittest.TestCase):
    def setUp(self):
        p = mock.patch.object(R, "PW", FAKE_PW)
        p.start()
        self.addCleanup(p.stop)

    def test_a_hermes_task_env_is_built_from_scratch(self):
        with mock.patch.dict(os.environ, {n: SYNTH for n in NAMES}):
            env = R.agent_env("hermes")
        self.assertEqual(set(NAMES) & set(env), set())
        self.assertNotIn(SYNTH, " ".join(env.values()))

    def test_the_goose_env_file_cannot_carry_a_github_credential(self):
        with tempfile.TemporaryDirectory() as home:
            with open(os.path.join(home, ".jarvis-goose.env"), "w") as fh:
                fh.write("GOOSE_PROVIDER=openai\n" + "".join(f"{n}={SYNTH}\n" for n in NAMES) + "# GH_TOKEN=x\n")
            with mock.patch.object(R, "HOME", home):
                env = R.agent_env("goose")
        self.assertEqual(env.get("GOOSE_PROVIDER"), "openai")          # ordinary settings still pass
        self.assertEqual(set(NAMES) & set(env), set())


class SubAgents(unittest.TestCase):
    def test_a_sub_agent_does_not_inherit_a_github_credential(self):
        with tempfile.TemporaryDirectory() as home:
            m = tm.load(home)
            env = m.agent_env({**{n: SYNTH for n in NAMES}, "PATH": "/usr/bin", "HOME": home, "LANG": "C"})
        self.assertEqual(set(NAMES) & set(env), set())
        self.assertEqual(env["LANG"], "C")

    def test_the_process_environment_is_filtered_too(self):
        with tempfile.TemporaryDirectory() as home:
            m = tm.load(home)
            with mock.patch.dict(os.environ, {n: SYNTH for n in NAMES}):
                env = m.agent_env()
        self.assertEqual(set(NAMES) & set(env), set())


class NodeJobs(unittest.TestCase):
    def test_node_check_jobs_drop_github_and_git_credential_variables(self):
        spec = importlib.util.spec_from_file_location("node_jobs_h", os.path.join(HERE, "..", "..", "node", "jarvis_node", "jobs.py"))
        sys.path.insert(0, os.path.join(HERE, "..", "..", "node"))
        try:
            mod = importlib.util.module_from_spec(spec)
            mod.__package__ = "jarvis_node"
            spec.loader.exec_module(mod)
        except ImportError:
            self.skipTest("jarvis_node.jobs needs its package imports")
        for n in NAMES:
            self.assertTrue(mod.SECRET_ENV.search(n), n)
        self.assertFalse(mod.SECRET_ENV.search("PATH"))
        self.assertFalse(mod.SECRET_ENV.search("PYTHONUTF8"))


class Sandbox(unittest.TestCase):
    def test_the_sandbox_identity_gets_only_the_allow_list(self):
        import lab_isolation as I
        env = I.sandbox_env("/tmp/x", {n: SYNTH for n in NAMES} | {"LANG": "C"})
        self.assertEqual(set(NAMES) & set(env), set())
        self.assertEqual(sorted(env), sorted(I.SANDBOX_ENV_ALLOW))


if __name__ == "__main__":
    unittest.main()
