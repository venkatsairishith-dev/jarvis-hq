"""POST /api/providers/command over real HTTP, with a real kernel behind the console (plan item 19): the words are mapped, the kernel decides."""
import json
import os
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "..", "..", "kernel"))
sys.path.insert(0, os.path.join(HERE, ".."))
from jarvis_console.config import Config  # noqa: E402
from jarvis_console.server import make_server  # noqa: E402
from jarvis_kernel.gateway import Provider  # noqa: E402
from jarvis_kernel.llm_proxy import make_handler  # noqa: E402
from jarvis_kernel.store import Kernel  # noqa: E402

DEAD = "http://127.0.0.1:9/v1"


class ProviderCommandApi(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        t = self.tmp.name
        self.k = Kernel(os.path.join(t, "d"))
        provs = [Provider("priv", "private", DEAD, None, ["any"], priority=1),
                 Provider("groq", "public", DEAD, None, ["any"], priority=1, model_pattern="."),
                 Provider("dahl", "public", DEAD, None, ["any"], priority=2, model_pattern=".")]
        H, tokens = make_handler(self.k, provs)
        self.ksrv = ThreadingHTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=self.ksrv.serve_forever, daemon=True).start()
        tokfile = os.path.join(t, "owner.token")
        with open(tokfile, "w") as fh:
            fh.write(tokens.issue("console", "owner"))
        ui = os.path.join(t, "ui")
        os.makedirs(ui)
        with open(os.path.join(ui, "index.html"), "w") as fh:
            fh.write("<!doctype html><title>JARVIS</title>")
        cfg = Config(port=0, kernel_url=f"http://127.0.0.1:{self.ksrv.server_address[1]}", token_file=tokfile, outputs_dir=os.path.join(t, "o"),
                     ui_dir=ui, hub_url="http://127.0.0.1:9", local_llm_url="http://127.0.0.1:9")
        self.srv = make_server(cfg)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.base = f"http://127.0.0.1:{self.srv.server_address[1]}"

    def tearDown(self):
        self.srv.shutdown()
        self.ksrv.shutdown()
        self.k.db.close()
        self.tmp.cleanup()

    def call(self, method, path, body=None, origin=True):
        h = {"Content-Type": "application/json"}
        if origin:
            h.update({"Origin": self.base, "X-Jarvis-Console": self.srv.console_secret})
        r = urllib.request.Request(self.base + path, data=json.dumps(body).encode() if body is not None else None, method=method, headers=h)
        try:
            with urllib.request.urlopen(r, timeout=15) as resp:
                return resp.status, json.loads(resp.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read() or b"{}")

    def say(self, text):
        code, out = self.call("POST", "/api/providers/command", {"text": text})
        self.assertEqual(code, 200, out)
        return out

    def lanes(self):
        return self.call("GET", "/api/owner/models/lanes")[1]["lanes"]

    def test_a_switch_phrase_changes_the_kernels_preference_and_the_answer_says_so(self):
        out = self.say("switch to dahl")
        self.assertTrue(out["handled"])
        self.assertEqual(out["command"], {"action": "switch", "provider": "dahl", "lane": "public"})
        self.assertIn("public lane is now set to dahl", out["say"])
        self.assertEqual(self.lanes()["public"]["preferred"], "dahl")

    def test_back_to_automatic_resets_both_lanes(self):
        self.say("use dahl")
        out = self.say("back to automatic")
        self.assertIn("back to automatic", out["say"])
        self.assertIsNone(self.lanes()["public"]["preferred"])

    def test_the_status_question_reports_each_lanes_choice_and_order(self):
        self.say("switch to dahl")
        say = self.say("which provider am I on")["say"]
        self.assertIn("public: dahl", say)
        self.assertIn("private: automatic", say)
        self.assertIn("order: groq, dahl", say)

    def test_the_kernel_not_the_parser_refuses_a_cross_lane_request(self):
        out = self.say("switch the private lane to groq")
        self.assertTrue(out["handled"])
        self.assertEqual(out["results"], [{"lane": "private", "code": 400}])
        self.assertIn("did not switch", out["say"])
        self.assertIsNone(self.lanes()["private"]["preferred"])

    def test_an_unknown_provider_is_reported_with_the_options(self):
        out = self.say("use the fancy model")
        self.assertTrue(out["handled"])
        self.assertIn("do not know a provider", out["say"])
        for name in ("groq", "dahl", "priv"):
            self.assertIn(name, out["say"])

    def test_ordinary_requests_are_not_handled_and_change_nothing(self):
        for text in ("switch to dark mode", "open notepad", "what is the weather"):
            self.assertEqual(self.say(text), {"handled": False})
        self.assertIsNone(self.lanes()["public"]["preferred"])

    def test_the_audit_log_records_the_owner_switch_made_by_words(self):
        self.say("switch to dahl")
        row = self.k.db.execute("SELECT payload FROM audit WHERE kind='lane_preference_set' ORDER BY seq DESC LIMIT 1").fetchone()
        self.assertEqual(json.loads(row[0]), {"lane": "public", "provider": "dahl", "agent": "owner"})

    def test_the_endpoint_needs_the_console_credentials_like_every_write(self):
        code, _ = self.call("POST", "/api/providers/command", {"text": "switch to dahl"}, origin=False)
        self.assertEqual(code, 403)
        self.assertIsNone(self.lanes()["public"]["preferred"])


if __name__ == "__main__":
    unittest.main()
