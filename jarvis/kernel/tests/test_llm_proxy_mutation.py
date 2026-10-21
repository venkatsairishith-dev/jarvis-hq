"""Regression tests added after mutation testing of llm_proxy.py (2026-10-19): what _scrub_messages leaves alone, how logit_bias is
filtered, and that an empty lane has no wait."""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from jarvis_kernel import llm_proxy
from jarvis_kernel.llm_proxy import make_handler
from jarvis_kernel.gateway import Provider
from jarvis_kernel.store import Kernel

TOK = "ghp_" + "A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8"


class ScrubMessages(unittest.TestCase):
    def test_role_and_audio_are_not_text_and_are_left_alone_while_everything_else_is_scrubbed(self):
        audio = {"data": "QUJD" + TOK, "format": "wav"}
        body = {"messages": [{"role": "user", "content": "key " + TOK, "audio": dict(audio), "name": "n " + TOK, "extra": {"k": TOK}}]}
        out, found, blocked = llm_proxy._scrub_messages(body)
        m = out["messages"][0]
        self.assertEqual(m["audio"], audio)                                  # media is withheld per provider, never edited here
        self.assertEqual(m["role"], "user")
        self.assertNotIn(TOK, m["content"])
        self.assertNotIn(TOK, m["name"])
        self.assertNotIn(TOK, str(m["extra"]))
        self.assertIn("github_token", found)
        self.assertFalse(blocked)

    def test_logit_bias_keeps_only_token_numbers_with_numeric_weights(self):
        body = {"messages": [{"role": "user", "content": "hi"}],
                "logit_bias": {"50256": -100, "-5": 2.5, "7": True, "8": "high", "abc": 1, "12": 0}}
        out, _, _ = llm_proxy._scrub_messages(body)
        self.assertEqual(out["logit_bias"], {"50256": -100, "-5": 2.5, "12": 0})


class LaneWait(unittest.TestCase):
    def test_a_lane_whose_only_provider_is_too_small_has_no_wait_and_an_unknown_lane_neither(self):
        with tempfile.TemporaryDirectory() as tmp:
            k = Kernel(os.path.join(tmp, "d"))
            keyf = os.path.join(tmp, "p.key")
            with open(keyf, "w") as f:
                f.write("gsk_PROVIDERKEY_never_shown_1234567890")
            small = Provider("small", "private", "http://127.0.0.1:9/v1", keyf, ["any"], default_model="m", max_total_tokens=8000)
            H, _ = make_handler(k, [small], clock=lambda: 1000.0)
            self.assertEqual(H.owner_plane.lane_wait("private"), 0)
            self.assertEqual(H.owner_plane.lane_wait("public"), 0)
            k.db.close()


class TooLarge(unittest.TestCase):
    def test_a_request_no_provider_can_take_is_413_and_audited_with_its_size(self):
        import json
        import threading
        import urllib.error
        import urllib.request
        from http.server import ThreadingHTTPServer
        with tempfile.TemporaryDirectory() as tmp:
            k = Kernel(os.path.join(tmp, "d"))
            keyf = os.path.join(tmp, "p.key")
            with open(keyf, "w") as f:
                f.write("gsk_PROVIDERKEY_never_shown_1234567890")
            small = Provider("small", "private", "http://127.0.0.1:9/v1", keyf, ["any"], default_model="m", max_total_tokens=8000)
            H, tokens = make_handler(k, [small], clock=lambda: 1000.0)
            srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
            threading.Thread(target=srv.serve_forever, daemon=True).start()
            try:
                body = json.dumps({"model": "x", "messages": [{"role": "user", "content": "word " * 9000}]}).encode()
                req = urllib.request.Request(f"http://127.0.0.1:{srv.server_address[1]}/v1/chat/completions", data=body, method="POST",
                                             headers={"Authorization": "Bearer " + tokens.issue("hermes"), "Content-Type": "application/json"})
                with self.assertRaises(urllib.error.HTTPError) as cm:
                    urllib.request.urlopen(req, timeout=10)
                self.assertEqual(cm.exception.code, 413)
                self.assertIn("context_length_exceeded", cm.exception.read().decode())
                rows = [json.loads(p) for (p,) in k.db.execute("SELECT payload FROM audit WHERE kind='agent_model_too_large'").fetchall()]
                self.assertEqual(len(rows), 1)
                self.assertEqual((rows[0]["agent"], rows[0]["lane"]), ("hermes", "private"))
                self.assertGreater(rows[0]["est_tokens"], 8000)
            finally:
                srv.shutdown()
                srv.server_close()
                k.db.close()


if __name__ == "__main__":
    unittest.main()
