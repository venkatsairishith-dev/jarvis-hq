"""The JARVIS memory plugin for Hermes, against a real in-process kernel and gateway (no Hermes needed)."""
import importlib.util
import json
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

spec = importlib.util.spec_from_file_location("jarvis_memory", os.path.join(HERE, "..", "plugins", "jarvis_memory", "__init__.py"))
JM = importlib.util.module_from_spec(spec); spec.loader.exec_module(JM)


class Plugin(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.k = Kernel(os.path.join(self.tmp.name, "d"))
        H, tokens = make_handler(self.k, [])
        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), H); threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        url = f"http://127.0.0.1:{self.srv.server_address[1]}"
        self.files = {}
        for agent in ("hermes", "memtest"):
            f = os.path.join(self.tmp.name, agent + ".token"); open(f, "w").write(tokens.issue(agent)); self.files[agent] = f
        self.owner = JM.JarvisMemoryProvider(url, self.files["hermes"])
        self.test = JM.JarvisMemoryProvider(url, self.files["memtest"])
        for p in (self.owner, self.test):
            p.initialize("s1", hermes_home=self.tmp.name, platform="cli")
        from jarvis_kernel.tasks import Tasks
        self.tid = Tasks(self.k).create("hermes", "private", "", "Write the quarterly report", criteria=["has a summary table"])

    def tearDown(self):
        self.srv.shutdown(); self.srv.server_close(); self.k.db.close(); self.tmp.cleanup()

    def call(self, p, tool, **args):
        return json.loads(p.handle_tool_call(tool, args))

    def test_a_session_starts_with_ids_and_the_task_checkpoint(self):
        e = self.call(self.owner, "jarvis_memory_add", text="Owner prefers short answers")["entry"]
        block = self.owner.system_prompt_block()
        self.assertIn(f"[{e['id']} v1]", block); self.assertIn("Write the quarterly report", block); self.assertIn("next:", block)
        self.assertIn("Write the quarterly report", self.owner.on_pre_compress([]))   # survives a compaction summary
        self.assertNotIn("short answers", self.test.system_prompt_block())            # a test profile sees none of it
        self.assertNotIn("quarterly", self.test.system_prompt_block())

    def test_tools_update_with_version_and_forget_verified(self):
        e = self.call(self.test, "jarvis_memory_add", text="Use metric units in reports")["entry"]
        self.assertEqual(self.call(self.test, "jarvis_memory_update", id=e["id"], text="Use metric units and ISO dates", expected_version=1)["entry"]["version"], 2)
        self.assertEqual(self.call(self.test, "jarvis_memory_update", id=e["id"], text="x y z", expected_version=1)["http_status"], 409)
        r = self.call(self.test, "jarvis_memory_forget", id_or_text=e["id"])
        self.assertTrue(r["verified"]); self.assertEqual(self.call(self.test, "jarvis_memory_list")["entries"], [])
        self.assertNotIn(e["id"], self.test.system_prompt_block())

    def test_kernel_down_degrades_quietly(self):
        p = JM.JarvisMemoryProvider("http://127.0.0.1:9", self.files["hermes"])
        self.assertEqual(p.system_prompt_block(), "")


if __name__ == "__main__":
    unittest.main()
