"""Scoring of transcription accuracy, the Voice Check endpoints, and the resident Piper worker."""
import json
import os
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

HERE = os.path.dirname(__file__)
sys.path.insert(0, os.path.join(HERE, ".."))
sys.path.insert(0, os.path.join(HERE, "..", "..", "kernel"))
from jarvis_console import scoring
from jarvis_console.config import Config
from jarvis_console.server import make_server
from jarvis_console.voice import Voice


class Scoring(unittest.TestCase):
    def test_case_and_punctuation_do_not_count_as_errors(self):
        s = scoring.score("Open my downloads folder", "open my Downloads folder.")
        self.assertEqual((s["wer"], s["cer"], s["exact"]), (0.0, 0.0, True))

    def test_word_and_character_error_rates(self):
        self.assertEqual(scoring.wer("a b c d", "a x c"), 0.5)                         # one substitution + one deletion of four
        self.assertAlmostEqual(scoring.cer("abc", "abd"), 1 / 3, places=3)
        self.assertIsNone(scoring.wer("", ""))
        self.assertEqual(scoring.wer("", "something"), 1.0)

    def test_indic_vowel_signs_are_kept_so_spelling_errors_stay_visible(self):
        right = "मेरा डाउनलोड फ़ोल्डर खोलो"
        self.assertEqual(scoring.normalize(right).split()[0], "मेरा")                  # the matra is not stripped
        wrong = "मेरा डाउनलोड फ़ोल्डर खोलों"                                            # a wrong vowel sign on the last word
        s = scoring.score(right, wrong)
        self.assertFalse(s["exact"])
        self.assertEqual(s["wer"], 0.25)
        self.assertGreater(s["cer"], 0)
        tel = scoring.score("నా డౌన్\u200cలోడ్ ఫోల్డర్ తెరవండి", "నా డౌన్లోడ్ ఫోల్డర్ తెరవండి")
        self.assertTrue(tel["exact"])                                                  # a zero-width joiner alone is not an error

    def test_summary_per_language(self):
        rows = [{"lang": "hi", "wer": 0.0, "cer": 0.0, "exact": True, "ms": 300}, {"lang": "hi", "wer": 0.5, "cer": 0.2, "exact": False, "ms": 500},
                {"lang": "te", "wer": 1.0, "cer": 0.6, "exact": False, "ms": 100, "speech": False}]
        s = scoring.summarize(rows)
        self.assertEqual((s["hi"]["phrases"], s["hi"]["mean_wer"], s["hi"]["exact"]), (2, 0.25, 1))
        self.assertEqual(s["te"]["no_speech"], 1)


class Endpoints(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        os.makedirs(os.path.join(self.tmp.name, "outputs"))
        self.cfg = Config(port=0, outputs_dir=os.path.join(self.tmp.name, "outputs"), ui_dir=self.tmp.name, token_file=os.path.join(self.tmp.name, "none"))
        self.srv = make_server(self.cfg, voice=Voice(model_dir=os.path.join(self.tmp.name, "nomodels")))
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.port = self.srv.server_address[1]
        self.cfg.port = self.port
        self.base = f"http://127.0.0.1:{self.port}"

    def tearDown(self):
        self.srv.shutdown(); self.tmp.cleanup()

    def post(self, path, body):
        r = urllib.request.Request(self.base + path, data=json.dumps(body).encode(), method="POST",
                                   headers={"Origin": self.base, "X-Jarvis-Console": self.srv.console_secret, "Content-Type": "application/json"})
        with urllib.request.urlopen(r, timeout=10) as resp:
            return resp.status, json.loads(resp.read())

    def get(self, path):
        with urllib.request.urlopen(urllib.request.Request(self.base + path, headers={"Origin": self.base, "X-Jarvis-Console": self.srv.console_secret}), timeout=10) as resp:
            return json.loads(resp.read())

    def test_phrases_exist_for_all_three_languages(self):
        p = self.get("/api/voice/check/phrases")["phrases"]
        self.assertEqual(sorted(p), ["en", "hi", "te"])
        self.assertGreaterEqual(len(p["en"]), 5)

    def test_score_save_and_read_back_a_session(self):
        code, sc = self.post("/api/voice/check/score", {"expected": "Stop all running tasks", "heard": "stop all running task"})
        self.assertEqual(code, 200)
        self.assertEqual(sc["wer"], 0.25)
        rows = [{"lang": "en", "expected": "Open my downloads folder", "heard": "open my downloads folder", "ms": 480, "speech": True, "model": "parakeet"},
                {"lang": "hi", "expected": "नोटपैड खोलो", "heard": "नोटपैड खोलो", "ms": 270, "speech": True, "model": "indicconformer"},
                {"lang": "xx", "expected": "ignored", "heard": "ignored"}]
        code, saved = self.post("/api/voice/check/save", {"rows": rows, "device": "Test microphone"})
        self.assertEqual(code, 200)
        self.assertEqual(saved["summary"]["en"]["exact"], 1)
        self.assertNotIn("xx", saved["summary"])                                          # unknown languages are dropped
        hist = self.get("/api/voice/check/phrases")["results"]
        self.assertEqual((hist[0]["device"], hist[0]["n"]), ("Test microphone", 2))
        self.assertTrue(os.path.exists(os.path.join(self.tmp.name, "cache", "voice-checks", saved["saved"])))
        self.assertEqual(self.post_status("/api/voice/check/save", {"rows": []}), 400)    # nothing to save is refused

    def test_a_post_to_a_page_path_is_refused_not_answered_with_the_page(self):
        self.assertEqual(self.post_status("/v1/jarvis/actions", {"tool": "task", "op": "list"}), 405)
        self.assertEqual(self.post_status("/", {}), 405)

    def test_refusals_arrive_as_status_codes_even_when_the_body_is_large_and_unread(self):
        big = {"pad": "x" * 150_000}
        for _ in range(12):                                                                # a reset connection is intermittent: try several times
            self.assertEqual(self.post_status("/v1/jarvis/actions", big), 405)             # a page path
            r = urllib.request.Request(self.base + "/api/owner/command", data=json.dumps(big).encode(), method="POST",
                                       headers={"Origin": "http://evil.example", "X-Jarvis-Console": self.srv.console_secret, "Content-Type": "application/json"})
            with self.assertRaises(urllib.error.HTTPError) as cm:                           # a foreign origin
                urllib.request.urlopen(r, timeout=10)
            self.assertEqual(cm.exception.code, 403)
            self.assertEqual(self.post_status("/api/no-such-route", big), 404)             # an unknown API route
    def post_status(self, path, body):
        import urllib.error
        try:
            return self.post(path, body)[0]
        except urllib.error.HTTPError as e:
            return e.code


class OneInstance(unittest.TestCase):
    @unittest.skipUnless(os.name == "nt", "Windows port-sharing behaviour")
    def test_a_second_console_cannot_bind_the_same_port(self):
        tmp = tempfile.TemporaryDirectory()
        try:
            os.makedirs(os.path.join(tmp.name, "outputs"))
            cfg = Config(port=0, outputs_dir=os.path.join(tmp.name, "outputs"), ui_dir=tmp.name, token_file=os.path.join(tmp.name, "none"))
            first = make_server(cfg, voice=Voice(model_dir=os.path.join(tmp.name, "nomodels")))
            try:
                cfg2 = Config(port=first.server_address[1], outputs_dir=os.path.join(tmp.name, "outputs"), ui_dir=tmp.name,
                              token_file=os.path.join(tmp.name, "none"))
                with self.assertRaises(OSError):
                    make_server(cfg2, voice=Voice(model_dir=os.path.join(tmp.name, "nomodels")))
            finally:
                first.server_close()
        finally:
            tmp.cleanup()


PIPER_PY = r"D:\jarvis\tools\piper-venv\Scripts\python.exe"
HI = r"D:\jarvis\models\voice\tts\piper-voices\hi_IN-rohan-medium.onnx"


@unittest.skipUnless(os.path.exists(PIPER_PY) and os.path.exists(HI), "Piper voices not installed")
class ResidentPiper(unittest.TestCase):
    def test_the_worker_stays_loaded_answers_repeatedly_and_recovers_from_a_kill(self):
        v = Voice()
        try:
            a, _ = v.speak("नमस्ते", "hi")
            self.assertTrue(a.startswith(b"RIFF"))
            import time
            t = time.time()
            b, _ = v.speak("काम पूरा हुआ", "hi")
            self.assertLess(time.time() - t, 3.0)                                         # warm: not a process start per sentence
            self.assertTrue(v.status()["resident_piper"])
            v._worker.kill(); v._worker.wait()                                            # the worker dies
            c, _ = v.speak("फिर से", "hi")                                                 # it is replaced, the request still works
            self.assertTrue(c.startswith(b"RIFF"))
        finally:
            v._stop_worker()


if __name__ == "__main__":
    unittest.main()
