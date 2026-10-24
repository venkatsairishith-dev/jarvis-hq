"""The hosted-model comparison scores plans with the production parser. A fake OpenAI server stands in for the gateway."""
import importlib.util
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

SPEC = importlib.util.spec_from_file_location("hosted_model_compare", Path(__file__).resolve().parents[1] / "bench" / "hosted_model_compare.py")
mod = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(mod)

PLAN_SEARCH = {"summary": "s", "steps": [{"id": "s1", "agent": "plugin", "plugin": "web_search:search", "goal": "find it", "args": {"query": "capital of Australia"}}]}


def test_a_good_plan_is_valid_and_correct_even_with_think_text():
    text = "<think>hmm</think>" + json.dumps(PLAN_SEARCH)
    assert mod.score(text, {"web_search:search"}, "australia") == (True, True, "ok")


def test_wrong_first_agent_is_valid_but_not_correct():
    valid, correct, why = mod.score(json.dumps(PLAN_SEARCH), {"think"}, None)
    assert valid and not correct and "web_search:search" in why


def test_missing_expected_word_is_not_correct():
    plan = {"summary": "s", "steps": [{"id": "s1", "agent": "plugin", "plugin": "web_search:search", "goal": "x", "args": {"query": "weather"}}]}
    assert mod.score(json.dumps(plan), {"web_search:search"}, "australia")[1] is False


def test_an_impossible_request_must_come_back_as_cannot():
    assert mod.score(json.dumps({"summary": "cannot", "steps": []}), "cannot", None) == (True, True, "cannot")
    assert mod.score(json.dumps(PLAN_SEARCH), "cannot", None)[1] is False


def test_invented_plugins_and_garbage_are_invalid():
    bad = {"summary": "s", "steps": [{"id": "s1", "agent": "plugin", "plugin": "pizza:order", "goal": "x", "args": {}}]}
    assert mod.score(json.dumps(bad), {"jarvis"}, None)[0] is False
    assert mod.score("sorry, I cannot", {"jarvis"}, None)[0] is False


class Fake(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        raw = json.dumps({"data": [{"id": "m1"}]}).encode()
        self.send_response(200)
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_POST(self):
        self.rfile.read(int(self.headers["Content-Length"]))
        assert self.headers["Authorization"] == "Bearer jarvis-local"
        raw = json.dumps({"choices": [{"message": {"content": json.dumps({"summary": "cannot", "steps": []})}, "finish_reason": "stop"}],
                          "usage": {"completion_tokens": 12}}).encode()
        self.send_response(200)
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)


def test_the_run_uses_the_placeholder_key_and_counts_every_case():
    srv = ThreadingHTTPServer(("127.0.0.1", 0), Fake)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        base = f"http://127.0.0.1:{srv.server_address[1]}/v1"
        assert mod.models_of(base) == ["m1"]
        report = mod.run(base, ["m1"], 1, 100)["m1"]
        assert report["calls"] == len(mod.CASES)
        # a model that always answers "cannot" gets exactly the two impossible requests right
        assert report["correct"] == sum(1 for _, expect, _ in mod.CASES if expect == "cannot")
        assert report["p50_tokens"] == 12
    finally:
        srv.shutdown()
        srv.server_close()
