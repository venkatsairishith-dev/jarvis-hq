"""The console and the voice path in front of the language layer (D58): what the owner types or says in Telugu, Hindi or Tenglish reaches the kernel's
agent as English, and the speech server's transcript carries its English next to the owner's own words. LABELLED FAKE translator (translate.FakeBackend)."""
import http.client
import importlib.util
import json
import os
import re
import sys
import threading
import time
import unittest
from http.server import ThreadingHTTPServer

HERE = os.path.dirname(__file__)
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, ".."))
from test_console import Base                                                  # noqa: E402
from jarvis_console import lang_hook                                           # noqa: E402
from jarvis.plugins._shared import translate as T                              # noqa: E402

NATIVE = re.compile("[ऀ-ॿఀ-౿]")
TABLE = {"ఓపెన్": "open", "నోట్‌ప్యాడ్": "notepad", "చేయి": "do", "नोटपैड": "notepad", "खोलो": "open", "chesi": "and", "rayi": "write"}

spec = importlib.util.spec_from_file_location("local_speech_server_d58", os.path.join(HERE, "..", "..", "scripts", "local_speech_server.py"))
lss = importlib.util.module_from_spec(spec)
spec.loader.exec_module(lss)


def fake_translator(keep=False):
    return T.Translator([T.FakeBackend(table=TABLE)], {"keep_original": keep}, glossary=[])


class LangHook(unittest.TestCase):
    def setUp(self):
        T.set_translator(fake_translator())

    def tearDown(self):
        T.set_translator(None)

    def test_english_is_returned_untouched_and_nothing_is_asked_of_a_model(self):
        be = T.get_translator().backends[0]
        self.assertEqual(lang_hook.english("open notepad"), "open notepad")
        self.assertIsNone(lang_hook.english_info("open notepad"))
        self.assertEqual(be.calls, [])

    def test_telugu_hindi_and_tenglish_become_english(self):
        self.assertEqual(lang_hook.english("ఓపెన్ నోట్‌ప్యాడ్ చేయి"), "open notepad do")
        self.assertEqual(lang_hook.english("नोटपैड खोलो"), "notepad open")
        self.assertEqual(lang_hook.english("notepad chesi rayi"), "notepad and write")

    def test_a_missing_layer_changes_nothing(self):
        os.environ["JARVIS_TRANSLATE"] = "0"
        try:
            self.assertEqual(lang_hook.english("ఓపెన్ చేయి"), "ఓపెన్ చేయి")
        finally:
            del os.environ["JARVIS_TRANSLATE"]


class ConsoleCommandBox(Base):
    """The Command box posts to /api/owner/command; the console is the choke point on the laptop, before the text goes to the kernel on the server."""

    def setUp(self):
        super().setUp()
        T.set_translator(fake_translator(keep=True))
        self._env = os.environ.get("JARVIS_DATA_DIR")
        os.environ["JARVIS_DATA_DIR"] = self.tmp.name

    def tearDown(self):
        T.set_translator(None)
        if self._env is None:
            os.environ.pop("JARVIS_DATA_DIR", None)
        else:
            os.environ["JARVIS_DATA_DIR"] = self._env
        super().tearDown()

    def wait_said(self):
        for _ in range(100):
            if self.said:
                return
            time.sleep(0.05)

    def test_a_telugu_command_reaches_the_agent_as_english_and_the_original_stays_in_the_owners_record(self):
        code, body, _ = self.call("POST", "/api/owner/command", {"text": "ఓపెన్ నోట్‌ప్యాడ్ చేయి"})
        self.assertEqual(code, 202)
        self.wait_said()
        self.assertEqual(len(self.said), 1)
        self.assertIn("open notepad do", self.said[0])
        self.assertIsNone(NATIVE.search(self.said[0]))
        row = self.k.db.execute("SELECT text FROM owner_commands").fetchone()[0]
        self.assertEqual(row, "open notepad do")
        rec = os.path.join(self.tmp.name, "translate", "translations.jsonl")
        self.assertIn("ఓపెన్ నోట్‌ప్యాడ్ చేయి", open(rec, encoding="utf-8").read())

    def test_an_english_command_is_unchanged(self):
        self.call("POST", "/api/owner/command", {"text": "open my downloads folder"})
        self.wait_said()
        self.assertIn("open my downloads folder", self.said[0])

    def test_the_classifier_reads_english(self):
        code, body, _ = self.call("POST", "/api/classify", {"text": "ఓపెన్ నోట్‌ప్యాడ్"})
        self.assertEqual(code, 200)
        self.assertEqual(body["category"], "pc_control")


class SpeechServer(unittest.TestCase):
    def serve(self, **kw):
        handler = lss.make_handler(kw.pop("synth", lambda t, lang: b"RIFF"), kw.pop("transcribe", None), **kw)
        srv = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        self.addCleanup(lambda: (srv.shutdown(), srv.server_close()))
        return srv.server_address[1]

    def post(self, port, path, body, ctype="audio/wav"):
        c = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
        c.request("POST", path, body=body, headers={"Content-Type": ctype})
        r = c.getresponse()
        out = (r.status, r.read())
        c.close()
        return out

    def test_a_telugu_transcript_comes_back_with_its_english_and_the_owners_own_words_stay_in_text(self):
        T.set_translator(fake_translator())
        self.addCleanup(lambda: T.set_translator(None))
        port = self.serve(transcribe=lambda wav, lang: "ఓపెన్ నోట్‌ప్యాడ్ చేయి" if lang == "te" else "open notepad",
                          english=lambda text, lang: lang_hook.english_info(text, "voice") or {})
        code, raw = self.post(port, "/v1/audio/transcriptions?language=te", b"RIFF....WAVE")
        out = json.loads(raw)
        self.assertEqual(code, 200)
        self.assertEqual(out["text"], "ఓపెన్ నోట్‌ప్యాడ్ చేయి")
        self.assertEqual((out["language"], out["english"], out["translated"]), ("te", "open notepad do", True))

    def test_english_speech_has_no_language_fields_and_costs_nothing(self):
        called = []
        port = self.serve(transcribe=lambda wav, lang: "open notepad", english=lambda t, l: called.append(t))
        code, raw = self.post(port, "/v1/audio/transcriptions?language=en", b"RIFF....WAVE")
        self.assertEqual(json.loads(raw), {"text": "open notepad"})
        self.assertEqual(called, [])

    def test_a_translator_that_fails_never_loses_the_transcript(self):
        def boom(text, lang):
            raise RuntimeError("model down")
        port = self.serve(transcribe=lambda wav, lang: "ఓపెన్", english=boom)
        code, raw = self.post(port, "/v1/audio/transcriptions?language=te", b"RIFF....WAVE")
        out = json.loads(raw)
        self.assertEqual((code, out["text"], out["translated"]), (200, "ఓపెన్", False))
        self.assertNotIn("english", out)

    def test_a_telugu_voice_is_spoken_only_when_it_is_installed_and_verified(self):
        """build_engines' synthesize gate, driven through the same voice_plan the layer uses: installed but unverified means nothing is spoken and the answer says why."""
        mod = T
        plan = mod.voice_plan("te", {"te": True}, {"tts_verified": {"te": False}})
        self.assertFalse(plan["speak"])
        self.assertIn("not yet verified", plan["note"])
        self.assertTrue(mod.voice_plan("te", {"te": True}, {"tts_verified": {"te": True}})["speak"])


if __name__ == "__main__":
    unittest.main()
