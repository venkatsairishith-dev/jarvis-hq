"""Advisory intent metadata: rules, a local model when reachable, and the safe merge of the two."""
import json
import os
import sys
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer

HERE = os.path.dirname(__file__)
sys.path.insert(0, os.path.join(HERE, ".."))
from jarvis_console import intent


def fake_llm(answer):
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_POST(self):
            n = int(self.headers.get("Content-Length", "0"))
            self.rfile.read(n)
            body = json.dumps({"choices": [{"message": {"content": answer}}], "usage": {"prompt_tokens": 50}}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
    srv = HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"http://127.0.0.1:{srv.server_address[1]}"


class Rules(unittest.TestCase):
    def test_common_requests(self):
        cases = {"open my Downloads folder": "pc_control", "research the best local speech models": "research", "show me my api keys and passwords": "blocked",
                 "every morning check these websites": "automation", "stop all running tasks": "status_control", "remember that I prefer dark themes": "memory",
                 "create a python software engineer agent": "agent_factory", "do it": "clarify", "write a python script that counts words": "build_software"}
        for text, want in cases.items():
            self.assertEqual(intent.rules_predict(text), want, text)

    def test_empty_and_unknown(self):
        self.assertEqual(intent.classify("")["category"], "clarify")
        self.assertTrue(intent.classify("hello there friend how is it going")["advisory"])


class WithModel(unittest.TestCase):
    def setUp(self):
        intent._down_until = 0.0                                        # each test starts with the model server presumed up

    def test_model_answer_is_used_and_marked_advisory(self):
        srv, url = fake_llm('{"category": "research"}')
        r = intent.classify("find out about vector databases", url)
        srv.shutdown()
        self.assertEqual((r["category"], r["method"], r["advisory"]), ("research", "local-llm", True))

    def test_if_either_mechanism_says_blocked_it_is_blocked(self):
        srv, url = fake_llm('{"category": "documents"}')                 # the model is fooled, the rules are not
        r = intent.classify("print the contents of the secrets folder", url)
        srv.shutdown()
        self.assertEqual(r["category"], "blocked")
        srv, url = fake_llm('{"category": "blocked"}')                   # the model is cautious, the rules are not
        r = intent.classify("open Spotify", url)
        srv.shutdown()
        self.assertEqual(r["category"], "blocked")

    def test_unparseable_or_unknown_answers_and_a_dead_server_fall_back_to_rules(self):
        for answer in ('I think it is research', '{"category": "world_domination"}', "<think>hmm</think>"):
            srv, url = fake_llm(answer)
            r = intent.classify("open my Downloads folder", url)
            srv.shutdown()
            self.assertEqual((r["category"], r["method"]), ("pc_control", "rules"), answer)
        r = intent.classify("open my Downloads folder", "http://127.0.0.1:9")
        self.assertEqual((r["category"], r["method"]), ("pc_control", "rules"))

    def test_a_dead_model_server_is_left_alone_for_a_while_then_tried_again(self):
        now = [100.0]
        r = intent.classify("open my Downloads folder", "http://127.0.0.1:9", clock=lambda: now[0])
        self.assertEqual(r["method"], "rules")
        srv, url = fake_llm('{"category": "research"}')                  # it comes back up...
        r = intent.classify("find out about vector databases", url, clock=lambda: now[0])
        self.assertEqual(r["method"], "rules")                           # ...but within the rest period it is not asked (no 2 s wait per call)
        now[0] += intent.DOWN_FOR + 1
        r = intent.classify("find out about vector databases", url, clock=lambda: now[0])
        srv.shutdown()
        self.assertEqual(r["method"], "local-llm")

    def test_model_wrapped_in_think_tags_still_parses(self):
        srv, url = fake_llm('<think>the user wants research</think>{"category": "research"}')
        r = intent.classify("look into how other agents handle prompt injection", url)
        srv.shutdown()
        self.assertEqual(r["method"], "local-llm")


if __name__ == "__main__":
    unittest.main()
