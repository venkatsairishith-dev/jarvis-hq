"""Agent-to-agent lab tasks and the owner's lab_work, as the tool server sends them (audit MEM-M8 / SEC-D5, 2026-10-18).

Runs anywhere: the kernel is a recorded fake, no agent is started (Popen is replaced), the token file is a made-up value
in a temp HOME. Run from jarvis/lab/tests:  python -m unittest test_jarvis_mcp"""
import importlib.util
import json
import os
import sys
import tempfile
import types
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
MODULE = os.path.join(HERE, "..", "jarvis_mcp.py")


def load(home):
    """Import jarvis_mcp with HOME pointing at a temp folder (it reads ~/.jarvis-llm-token on import)."""
    with open(os.path.join(home, ".jarvis-llm-token"), "w") as fh:
        fh.write("made-up-test-token")
    try:
        import mcp.server.mcpserver  # noqa: F401
    except ImportError:                                     # the tool server's SDK is not needed to test what it sends
        stub = types.ModuleType("mcp.server.mcpserver")

        class MCPServer:
            def __init__(self, *a, **k):
                pass

            def tool(self):
                return lambda fn: fn
        stub.MCPServer, stub.Image = MCPServer, object
        for name in ("mcp", "mcp.server"):
            sys.modules.setdefault(name, types.ModuleType(name))
        sys.modules["mcp.server.mcpserver"] = stub
    with mock.patch.dict(os.environ, {"HOME": home, "JARVIS_ACTIVITY": os.path.join(home, "activity.jsonl")}):
        spec = importlib.util.spec_from_file_location("jarvis_mcp_under_test", MODULE)
        m = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(m)
    m.TASKS = os.path.join(home, "work", "tasks")
    os.makedirs(m.TASKS, exist_ok=True)
    return m


class LabSubtasks(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = self.tmp.name
        self.m = load(self.home)
        self.sent = []
        self.reply = {"status": "allowed"}
        self.m._act = lambda tool, op, target="", params=None, approval_id="": self.sent.append(
            (tool, op, target, params, approval_id)) or dict(self.reply)
        self.started = []

        class FakePopen:
            pid = 4242

            def __init__(s, cmd, **kw):
                self.started.append((cmd, kw))
        self.popen = mock.patch.object(self.m.subprocess, "Popen", FakePopen)
        self.popen.start()

    def tearDown(self):
        self.popen.stop()
        self.tmp.cleanup()

    def activity(self):
        p = os.path.join(self.home, "activity.jsonl")
        return open(p).read() if os.path.exists(p) else ""

    def test_the_agent_runs_with_its_own_bin_last_on_path(self):
        # MEM-M8: start_lab_task put ~/.local/bin FIRST, undoing lab_runner's fix (a planted ~/.local/bin/python3, D14)
        local = os.path.join(self.home, ".local", "bin")
        with mock.patch.dict(os.environ, {"HOME": self.home, "PATH": f"{local}:/usr/local/bin:/usr/bin:/bin"}):
            env = self.m.agent_env()
            self.assertEqual(env["PATH"].split(os.pathsep), ["/usr/local/bin", "/usr/bin", "/bin", local])
            out = json.loads(self.m.start_lab_task("opencode", "write a haiku about rain"))
        self.assertEqual(out["status"], "started")
        path = self.started[0][1]["env"]["PATH"].split(os.pathsep)
        self.assertEqual(path[-1], local)
        self.assertEqual(path.count(local), 1)

    def test_the_kernel_decides_and_an_approval_is_sent_back_with_the_same_prompt(self):
        self.reply = {"status": "needs_approval", "approval_id": "a1b2c3d4e5f60718", "level": "YELLOW_APPROVAL"}
        prompt = "Summarise: Owner SYNTH-Name lives at 12 Example Road"
        out = json.loads(self.m.start_lab_task("opencode", prompt))
        self.assertEqual(out["status"], "needs_approval")
        self.assertEqual(self.started, [])                                   # nothing runs before the owner approves
        self.reply = {"status": "allowed"}
        json.loads(self.m.start_lab_task("opencode", prompt, approval_id="a1b2c3d4e5f60718"))
        self.assertEqual(self.sent[-1], ("lab", "task", "opencode", {"prompt": prompt}, "a1b2c3d4e5f60718"))
        self.assertEqual(len(self.started), 1)

    def test_the_activity_log_never_holds_the_prompt(self):
        prompt = "Summarise: Owner SYNTH-Name lives at 12 Example Road"
        self.m.start_lab_task("hermes", prompt)
        log = self.activity()
        self.assertIn("start_lab_task", log)
        self.assertNotIn("Example Road", log)
        self.assertIn(f"{len(prompt)} characters", log)

    def test_lab_work_and_outputs_carry_the_approval_and_the_task(self):
        self.m.lab_work("0123456789ab", approval_id="ffeeddccbbaa0099")
        self.assertEqual(self.sent[-1][:2], ("task", "dispatch"))
        self.assertEqual(self.sent[-1][4], "ffeeddccbbaa0099")
        self.reply = {"status": "refused"}
        self.m.save_output("notes.md", "x", task_id="0123456789ab")
        self.assertEqual(self.sent[-1][3], {"content": "x", "task": "0123456789ab"})
        self.m.pc_action("launch", "notepad", task_id="0123456789ab")
        self.assertEqual(self.sent[-1][3], {"task": "0123456789ab"})


if __name__ == "__main__":
    unittest.main()
