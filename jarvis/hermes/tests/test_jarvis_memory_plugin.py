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

    def test_the_newest_entries_reach_the_session_prompt(self):
        """MEM-M1 (2026-10-18): with many saved entries the block used to keep the 14 oldest and drop every newer one."""
        from jarvis_kernel import scrub as S
        from jarvis_kernel.memory import Memories
        gl = dict(S._GL); S._GL.update(path=None, checked=True)          # fixture filling only: skip the gitleaks subprocess
        try:
            M = Memories(self.k)
            for i in range(30):
                e = M.add("hermes", f"Topic{i}: x{i}alpha x{i}beta x{i}gamma x{i}delta for the owner")
                self.k.db.execute("UPDATE memories SET created=?, updated=? WHERE id=?", (1_760_000_000 + i, 1_760_000_000 + i, e["id"]))
        finally:
            S._GL.clear(); S._GL.update(gl)
        new = self.call(self.owner, "jarvis_memory_add", text="NEWEST: the owner now prefers aisle seats on every flight")["entry"]
        block = self.owner.system_prompt_block()
        self.assertIn(f"[{new['id']} v1]", block)
        self.assertRegex(block, r"\n\.\.\. \d+ more \(.*jarvis_memory_list")      # says how many were left out, and how to list them
        self.assertIn(new["id"], self.owner.on_pre_compress([{"role": "user", "content": "anything"}]))
        self.assertNotIn("NEWEST", self.test.system_prompt_block())

    def test_the_current_task_and_latest_message_are_the_ranking_query(self):
        sent = []
        real = self.owner._act

        def spy(tool, op, target="", **params):
            sent.append((tool, op, params))
            return real(tool, op, target, **params)
        self.owner._act = spy
        self.owner.system_prompt_block()
        self.assertEqual(sent[-1][:2], ("memory", "render"))
        self.assertIn("Write the quarterly report", sent[-1][2]["query"])               # the open task's goal
        self.owner.on_pre_compress([{"role": "user", "content": "old question"}, {"role": "assistant", "content": "x"},
                                    {"role": "user", "content": [{"type": "text", "text": "book my passport renewal slot"}]}])
        q = sent[-1][2]["query"]
        self.assertTrue(q.startswith("book my passport renewal slot"), q); self.assertNotIn("old question", q)
        self.assertLessEqual(len(q), JM.MAX_QUERY)
        sent.clear()
        self.test._act = lambda tool, op, target="", **params: sent.append((tool, op, params)) or {}
        self.test.system_prompt_block()                                                  # no open task, no message: recency only
        self.assertEqual(sent[-1], ("memory", "render", {}))

    def test_kernel_down_degrades_quietly(self):
        p = JM.JarvisMemoryProvider("http://127.0.0.1:9", self.files["hermes"])
        self.assertEqual(p.system_prompt_block(), "")


class NoProxy(unittest.TestCase):
    """Completion H (2026-10-19): the kernel bearer token never goes to an environment proxy and is not re-sent after a redirect."""

    def test_a_recording_proxy_sees_nothing(self):
        from http.server import BaseHTTPRequestHandler
        hits = []

        class Sink(BaseHTTPRequestHandler):
            def _any(self):
                hits.append(self.path)
                self.send_response(200)
                self.send_header("Content-Length", "2")
                self.end_headers()
                self.wfile.write(b"{}")
            do_GET = do_POST = _any

            def log_message(self, *a):
                pass
        proxy = ThreadingHTTPServer(("127.0.0.1", 0), Sink)
        threading.Thread(target=proxy.serve_forever, daemon=True).start()
        saved = {k: os.environ.get(k) for k in ("http_proxy", "HTTP_PROXY", "ALL_PROXY", "all_proxy", "no_proxy", "NO_PROXY")}
        tmp = tempfile.TemporaryDirectory()
        try:
            for k in saved:
                os.environ.pop(k, None)
            os.environ["http_proxy"] = os.environ["HTTP_PROXY"] = f"http://127.0.0.1:{proxy.server_address[1]}"
            tok = os.path.join(tmp.name, "t.token")
            open(tok, "w").write("CANARY-7f3a-Priyanka-SYNTH")
            p = JM.JarvisMemoryProvider("http://127.0.0.1:9", tok)         # nothing listens on :9: the call fails, but never via the proxy
            try:
                p._act("memory", "list")
            except OSError:
                pass
            self.assertEqual(hits, [])
        finally:
            for k, v in saved.items():
                os.environ.pop(k, None)
                if v is not None:
                    os.environ[k] = v
            proxy.shutdown(); tmp.cleanup()


if __name__ == "__main__":
    unittest.main()
