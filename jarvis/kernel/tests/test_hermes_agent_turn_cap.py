"""The owner console's Hermes conversation is replaced after a few turns, so its history cannot outgrow the private lane's smallest free model
(2026-10-25: a day-long session reached ~31k characters / 7.9k estimated tokens, Groq takes 6976, and a command waited five minutes for nothing)."""
import json
import os
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from jarvis_kernel import llm_proxy  # noqa: E402
from jarvis_kernel.telegram import hermes_agent  # noqa: E402


class TurnCap(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.seen = []
        seen = self.seen

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_POST(self):
                self.rfile.read(int(self.headers["Content-Length"]))
                seen.append(self.headers.get("X-Hermes-Session-Id"))
                out = json.dumps({"choices": [{"message": {"content": "done"}}]}).encode()
                self.send_response(200)
                self.send_header("Content-Length", str(len(out)))
                self.end_headers()
                self.wfile.write(out)
        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.key = os.path.join(self.tmp.name, "k.txt")
        open(self.key, "w").write("not-a-real-key")
        self.state = os.path.join(self.tmp.name, "s.txt")
        self.url = f"http://127.0.0.1:{self.srv.server_address[1]}"
        self.now = [1792928000.0]

    def tearDown(self):
        self.srv.shutdown()
        self.tmp.cleanup()

    def agent(self, **kw):
        return hermes_agent(self.key, self.url, session="console-owner", state_path=self.state, clock=lambda: self.now[0], **kw)

    def test_a_conversation_is_replaced_after_max_turns_and_the_count_survives_a_restart(self):
        call = self.agent(max_turns=3)
        for i in range(2):
            call(f"m{i}")
        call = self.agent(max_turns=3)                                   # the console restarted: the count is in the state file
        for i in range(2, 7):
            self.now[0] += 1                                             # a fresh id carries the clock second
            call(f"m{i}")
        self.assertEqual(len(set(self.seen)), 3, self.seen)              # 7 turns, 3 per conversation: 3 conversations
        self.assertEqual([self.seen.count(s) for s in dict.fromkeys(self.seen)], [3, 3, 1])
        self.assertEqual(self.seen[0], self.seen[2])
        self.assertNotEqual(self.seen[2], self.seen[3])

    def test_without_max_turns_the_conversation_is_kept_for_the_day_as_before(self):
        call = self.agent()
        for i in range(8):
            call(f"m{i}")
        self.assertEqual(len(set(self.seen)), 1)

    def test_an_old_state_file_with_only_the_session_id_still_works(self):
        open(self.state, "w").write("console-owner-20261025-1792900000")
        call = self.agent(max_turns=2)
        call("a"), call("b"), call("c")
        self.assertEqual(self.seen[0], "console-owner-20261025-1792900000")
        self.assertEqual(self.seen[0], self.seen[1])
        self.assertNotEqual(self.seen[1], self.seen[2])

    def test_the_owner_console_uses_the_cap(self):
        self.assertEqual(llm_proxy.CONSOLE_AGENT_MAX_TURNS, 1)
        src = open(os.path.join(os.path.dirname(llm_proxy.__file__), "llm_proxy.py"), encoding="utf-8").read()
        self.assertIn("max_turns=CONSOLE_AGENT_MAX_TURNS", src)


if __name__ == "__main__":
    unittest.main()
