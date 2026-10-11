"""Console backend against a real kernel owner plane (in-process), with a fake voice engine for logic tests."""
import json
import os
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

HERE = os.path.dirname(__file__)
sys.path.insert(0, os.path.join(HERE, ".."))
sys.path.insert(0, os.path.join(HERE, "..", "..", "kernel"))
from jarvis_console import files as filesmod
from jarvis_console.config import Config
from jarvis_console.server import make_server
from jarvis_console.speech_text import speakable, sentences
from jarvis_console.voice import Voice, VoiceError, read_wav, write_wav
from jarvis_kernel.llm_proxy import make_handler
from jarvis_kernel.store import Kernel
from jarvis_kernel.tasks import Tasks

try:
    import numpy as np
except ImportError:
    np = None


class FakeVoice:
    def __init__(self):
        self.calls = []

    def status(self):
        return {"engine": "fake", "stt": {}, "tts": {}, "stats": {}}

    def transcribe(self, wav, lang="en"):
        self.calls.append(("stt", len(wav), lang))
        return {"text": "open my downloads folder", "speech": True, "language": lang, "model": "fake", "ms": 1}

    def speak(self, text, lang="en", speed=1.0):
        self.calls.append(("tts", text, lang))
        return b"RIFFfakewav", {"model": "fake", "ms": 1, "rate": 16000, "language": lang}

    def unload_if_idle(self):
        return False


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        t = self.tmp.name
        self.k = Kernel(os.path.join(t, "d"))
        self.said = []
        H, tokens = make_handler(self.k, [], owner_hermes=lambda text: self.said.append(text) or "Done: opened it.")
        self.plane = H.owner_plane
        self.ksrv = ThreadingHTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=self.ksrv.serve_forever, daemon=True).start()
        self.token = tokens.issue("console", "owner")
        self.tokfile = os.path.join(t, "owner.token")
        with open(self.tokfile, "w") as fh:
            fh.write(self.token)
        self.out = os.path.join(t, "outputs")
        os.makedirs(os.path.join(self.out, "task-abc123def456"))
        with open(os.path.join(self.out, "task-abc123def456", "report.md"), "w") as fh:
            fh.write("# Report\nhello")
        with open(os.path.join(self.out, "task-abc123def456", "page.html"), "w") as fh:
            fh.write("<h1>hi</h1><script>alert(1)</script>")
        self.ui = os.path.join(t, "ui")
        os.makedirs(os.path.join(self.ui, "assets"))
        with open(os.path.join(self.ui, "index.html"), "w") as fh:
            fh.write("<!doctype html><title>JARVIS</title><div id=root></div>")
        self.voice = FakeVoice()
        self.cfg = Config(port=0, kernel_url=f"http://127.0.0.1:{self.ksrv.server_address[1]}", token_file=self.tokfile,
                          outputs_dir=self.out, ui_dir=self.ui, hub_url="http://127.0.0.1:9", local_llm_url="http://127.0.0.1:9")
        self.srv = make_server(self.cfg, voice=self.voice)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.port = self.srv.server_address[1]
        self.cfg.port = self.port
        self.base = f"http://127.0.0.1:{self.port}"

    def tearDown(self):
        self.srv.shutdown(); self.ksrv.shutdown(); self.k.db.close(); self.tmp.cleanup()

    def call(self, method, path, body=None, headers=None, raw=None):
        h = {"Origin": self.base}
        h.update(headers or {})
        data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
        if data is not None and raw is None:
            h["Content-Type"] = "application/json"
        r = urllib.request.Request(self.base + path, data=data, method=method, headers=h)
        try:
            with urllib.request.urlopen(r, timeout=15) as resp:
                b = resp.read()
                return resp.status, (json.loads(b) if resp.headers.get("Content-Type", "").startswith("application/json") else b), resp.headers
        except urllib.error.HTTPError as e:
            b = e.read()
            try:
                return e.code, json.loads(b), e.headers
            except ValueError:
                return e.code, b, e.headers


class Server(Base):
    def test_ui_is_served_with_a_strict_policy_and_unknown_paths_are_screens(self):
        code, body, h = self.call("GET", "/")
        self.assertEqual(code, 200)
        self.assertIn(b"JARVIS", body)
        csp = h["Content-Security-Policy"]
        self.assertIn("script-src 'self'", csp)
        self.assertNotIn("unsafe-eval", csp)
        self.assertIn("microphone=(self)", h["Permissions-Policy"])
        self.assertEqual(self.call("GET", "/tasks/abc")[0], 200)                      # SPA route
        self.assertEqual(self.call("GET", "/../../etc/passwd")[0], 200)               # cannot leave the UI folder: index.html
        self.assertNotIn(b"root:", self.call("GET", "/..%2f..%2fWindows/win.ini")[1])

    def test_host_and_origin_checks(self):
        self.assertEqual(self.call("GET", "/api/health", headers={"Host": "evil.example"})[0], 403)      # DNS rebinding
        self.assertEqual(self.call("POST", "/api/owner/command", {"text": "hello there"}, headers={"Origin": "https://evil.example"})[0], 403)
        self.assertEqual(self.call("POST", "/api/owner/command", {"text": "hello there"}, headers={"Origin": "http://localhost:1"})[0], 403)
        r = urllib.request.Request(self.base + "/api/owner/command", data=b"{}", method="POST")      # no Origin, no custom header
        with self.assertRaises(urllib.error.HTTPError) as cm:
            urllib.request.urlopen(r, timeout=5)
        self.assertEqual(cm.exception.code, 403)

    def test_owner_passthrough_command_state_and_token_never_leaks(self):
        code, r, _ = self.call("POST", "/api/owner/command", {"text": "open my downloads folder", "source": "voice"})
        self.assertEqual(code, 202)
        for _ in range(100):
            c = self.call("GET", "/api/owner/commands")[1]["commands"][0]
            if c["state"] != "running":
                break
            time.sleep(0.05)
        self.assertEqual((c["state"], c["source"]), ("done", "voice"))
        self.assertIn("opened it", c["reply"])
        t = Tasks(self.k).create("hermes", "private", "r", "build site")
        st = self.call("GET", "/api/owner/state")[1]
        self.assertIn(t, [x["id"] for x in st["tasks"]])
        blob = json.dumps(self.call("GET", "/api/health")[1]) + json.dumps(self.call("GET", "/api/config")[1]) + json.dumps(st)
        self.assertNotIn(self.token, blob)

    def test_kernel_down_and_missing_token_are_clear_errors(self):
        self.ksrv.shutdown()
        self.ksrv.server_close()
        code, r, _ = self.call("GET", "/api/owner/state")
        self.assertEqual(code, 502)
        self.assertTrue(r["kernel_unreachable"])
        h = self.call("GET", "/api/health")[1]
        self.assertFalse(h["kernel"]["reachable"])
        os.unlink(self.tokfile)
        code, r, _ = self.call("GET", "/api/owner/state")
        self.assertEqual(code, 503)
        self.assertIn("owner token", r["error"])

    def test_event_stream_passthrough_delivers_new_audit_rows(self):
        t = Tasks(self.k).create("hermes", "private", "r", "stream me")
        r = urllib.request.Request(self.base + "/api/stream?since=0", headers={"Origin": self.base})
        got = []
        with urllib.request.urlopen(r, timeout=10) as resp:
            for _ in range(200):
                line = resp.readline().decode()
                if line.startswith("data:"):
                    got.append(json.loads(line[5:]))
                if any(e["kind"] == "task_created" for e in got):
                    break
        self.assertTrue(any(e["kind"] == "task_created" and e["payload"].get("id") == t for e in got))

    def test_voice_endpoints_use_the_engine_and_speak_a_short_version(self):
        code, r, _ = self.call("POST", "/api/voice/transcribe?lang=hi", raw=b"RIFF....")
        self.assertEqual((code, r["text"]), (200, "open my downloads folder"))
        self.assertEqual(self.voice.calls[0], ("stt", 8, "hi"))
        code, wav, h = self.call("POST", "/api/voice/speak", {"text": "Done. See `code` and https://example.com/x ```print(1)``` ok", "lang": "en"})
        self.assertEqual((code, wav), (200, b"RIFFfakewav"))
        spoken = self.voice.calls[-1][1]
        self.assertNotIn("http", spoken)
        self.assertNotIn("print", spoken)

    def test_files_list_and_preview_are_inert_and_confined(self):
        lst = self.call("GET", "/api/files")[1]
        names = {f["path"] for f in lst["delivered"]}
        self.assertEqual(names, {"task-abc123def456/report.md", "task-abc123def456/page.html"})
        code, body, h = self.call("GET", "/api/files/get?path=task-abc123def456/page.html")
        self.assertEqual(code, 200)
        self.assertIn("sandbox", h["Content-Security-Policy"])               # scripts cannot run in the preview
        self.assertEqual(h["X-Content-Type-Options"], "nosniff")
        code, body, h = self.call("GET", "/api/files/get?path=task-abc123def456/page.html&source=1")
        self.assertTrue(h["Content-Type"].startswith("text/plain"))
        for bad in ("../d/state.db", "..%2Fd%2Fstate.db", "task-abc123def456/../../d/state.db", "C:/Windows/win.ini", "a;b", "x'y", "nul"):
            self.assertIn(self.call("GET", "/api/files/get?path=" + bad)[0], (400, 404), bad)
        code, body, h = self.call("GET", "/api/files/get?path=task-abc123def456/report.md&download=1")
        self.assertIn("attachment", h["Content-Disposition"])

    def test_desktop_control_unavailable_is_reported_not_faked(self):
        code, r, _ = self.call("GET", "/api/desktop/state")
        self.assertEqual(code, 503)
        self.assertTrue(r["unavailable"])


class Text(unittest.TestCase):
    def test_speakable_never_reads_code_links_or_ids(self):
        t = speakable("## Done\nSee [the report](https://x.y/z) and `ls`.\n```python\nprint(1)\n```\nTask 9f98013ab16f finished.\n| a | b |")
        self.assertNotIn("http", t); self.assertNotIn("print", t); self.assertNotIn("9f98013ab16f", t); self.assertNotIn("|", t)
        self.assertIn("the report", t)
        long = speakable("Sentence one is here. " * 100)
        self.assertLess(len(long), 700)
        self.assertTrue(long.endswith("The rest is on screen."))
        self.assertEqual(sentences("One. Two! Three?"), ["One.", "Two!", "Three?"])


@unittest.skipUnless(np, "numpy not installed")
class WavAndRealVoice(unittest.TestCase):
    def test_wav_roundtrip_and_resample(self):
        x = (np.sin(np.arange(8000) / 20.0) * 0.5).astype("float32")
        y, rate = read_wav(write_wav(x, 8000))
        self.assertEqual(rate, 8000)
        self.assertAlmostEqual(len(y) / 16000, len(x) / 8000, places=2)          # resampled to 16 kHz
        with self.assertRaises(VoiceError):
            read_wav(b"not a wav")

    @unittest.skipUnless(os.path.exists(r"D:\jarvis\models\voice\stt\parakeet-tdt-0.6b-v3-int8\encoder.int8.onnx"), "voice models not installed")
    def test_real_english_roundtrip_and_silence(self):
        v = Voice()
        if not v.status()["engine"]:
            self.skipTest("sherpa-onnx not installed")
        wav, meta = v.speak("Open my downloads folder.", "en")
        r = v.transcribe(wav, "en")
        self.assertIn("downloads folder", r["text"].lower())
        s = v.transcribe(write_wav(np.zeros(32000, "float32"), 16000), "en")
        self.assertEqual((s["text"], s["speech"]), ("", False))                  # silence yields no words


if __name__ == "__main__":
    unittest.main()
