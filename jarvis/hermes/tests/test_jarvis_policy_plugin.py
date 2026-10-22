"""The JARVIS policy plugin for Hermes, against a real in-process kernel and gateway (no Hermes needed)."""
import importlib.util
import os
import sys
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "..", "kernel"))
from jarvis_kernel.llm_proxy import make_handler                   # noqa: E402
from jarvis_kernel.store import Kernel                             # noqa: E402

spec = importlib.util.spec_from_file_location("jarvis_policy", os.path.join(HERE, "..", "plugins", "jarvis_policy", "__init__.py"))
JP = importlib.util.module_from_spec(spec); spec.loader.exec_module(JP)


class Policy(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.k = Kernel(os.path.join(self.tmp.name, "d"))
        H, tokens = make_handler(self.k, [])
        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), H); threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.env = {k: os.environ.get(k) for k in ("JARVIS_ENDPOINT", "HERMES_HOME")}
        os.environ["JARVIS_ENDPOINT"] = f"http://127.0.0.1:{self.srv.server_address[1]}"
        os.environ["HERMES_HOME"] = self.tmp.name
        with open(os.path.join(self.tmp.name, "jarvis-memory.token"), "w", encoding="utf-8") as f:
            f.write(tokens.issue("hermes-policy-test"))
        JP._untrusted.clear()

    def tearDown(self):
        for k, v in self.env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        self.srv.shutdown(); self.k.db.close(); self.tmp.cleanup()

    def test_green_is_allowed_yellow_asks_red_and_deny_block(self):
        self.assertIsNone(JP.decide("read_file", {"path": "/work/notes.txt"}, "t1"))
        ask = JP.decide("write_file", {"path": "/work/notes.txt"}, "t1")
        self.assertEqual(ask["action"], "approve"); self.assertEqual(ask["rule_key"], "jarvis:fs.write")
        red = JP.decide("terminal", {"command": "ls"}, "t1")
        self.assertEqual(red["action"], "block"); self.assertIn("RED", red["message"])
        deny = JP.decide("read_file", {"path": "/home/u/.ssh/id_ed25519"}, "t1")
        self.assertEqual(deny["action"], "block"); self.assertIn("DENY", deny["message"])

    def test_unknown_tool_is_blocked_by_the_kernels_fail_closed_rule(self):
        out = JP.decide("some_new_tool", {}, "t1")
        self.assertEqual(out["action"], "block"); self.assertIn("fail closed", out["message"])

    def test_jarvis_safety_files_are_blocked(self):
        self.assertEqual(JP.decide("write_file", {"path": "/srv/x/cdp_relay.py"}, "t1")["action"], "block")

    def test_local_bookkeeping_tools_need_no_ruling_even_when_the_kernel_is_down(self):
        os.environ["JARVIS_ENDPOINT"] = "http://127.0.0.1:9"
        self.assertIsNone(JP.decide("todo_list", {}, "t1"))
        self.assertIsNone(JP.decide("skill_view", {"name": "x"}, "t1"))

    def test_fail_closed_when_the_kernel_is_unreachable_or_there_is_no_token(self):
        os.environ["JARVIS_ENDPOINT"] = "http://127.0.0.1:9"
        out = JP.decide("read_file", {"path": "/a"}, "t1")
        self.assertEqual(out["action"], "block"); self.assertIn("fail closed", out["message"])
        os.environ["JARVIS_ENDPOINT"] = f"http://127.0.0.1:{self.srv.server_address[1]}"
        os.remove(os.path.join(self.tmp.name, "jarvis-memory.token"))
        os.environ["HERMES_HOME"] = self.tmp.name
        home = os.path.expanduser("~/.jarvis-llm-token")
        if not os.path.exists(home):                            # a real token file on this machine would legitimately answer
            self.assertEqual(JP.decide("read_file", {"path": "/a"}, "t1")["action"], "block")

    def test_a_bad_token_is_blocked(self):
        with open(os.path.join(self.tmp.name, "jarvis-memory.token"), "w", encoding="utf-8") as f:
            f.write("not-a-real-token")
        self.assertEqual(JP.decide("read_file", {"path": "/a"}, "t1")["action"], "block")

    def test_an_exception_inside_the_plugin_blocks_instead_of_letting_the_tool_run(self):
        real = JP.ask_kernel
        JP.ask_kernel = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom"))
        try:
            out = JP.pre_tool_call("read_file", {"path": "/a"}, "t1")
        finally:
            JP.ask_kernel = real
        self.assertEqual(out["action"], "block")

    def test_a_web_read_marks_the_session_untrusted_for_the_next_call(self):
        seen = []
        real = JP.ask_kernel
        JP.ask_kernel = lambda tool, op, target, untrusted: (seen.append((tool, op, untrusted)) or {"status": "ok", "level": "GREEN", "reason": ""})
        try:
            JP.decide("web_search", {"query": "weather"}, "taskA")
            JP.decide("read_file", {"path": "/a"}, "taskA")
            JP.decide("read_file", {"path": "/a"}, "taskB")
        finally:
            JP.ask_kernel = real
        self.assertEqual(seen, [("browser", "open", False), ("fs", "read", True), ("fs", "read", False)])

    def test_register_installs_the_pre_tool_call_hook(self):
        got = []
        ctx = type("Ctx", (), {"register_hook": lambda self, name, fn: got.append((name, fn))})()
        JP.register(ctx)
        self.assertEqual([n for n, _ in got], ["pre_tool_call"])

    def test_asking_creates_no_approval_or_job(self):
        before = [self.k.db.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in ("approvals", "jobs")]
        JP.decide("write_file", {"path": "/a"}, "t1"); JP.decide("terminal", {"command": "x"}, "t1")
        self.assertEqual(before, [self.k.db.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in ("approvals", "jobs")])


if __name__ == "__main__":
    unittest.main()
