"""Completion H (2026-10-19): a health answer must not say ready/ok while a REQUIRED component is disabled or unavailable.

Console (kernel unreachable / token refused / kill switch / voice models absent), the speech server (models absent), the wake listener,
the widget's status (degraded gateway or speech, unauthorized kernel) and the watchdog (answers-but-broken components make the status
unhealthy without triggering a restart). Real HTTP against the real handlers where there is one."""
import contextlib
import http.client
import importlib.util
import io
import json
import os
import sys
import threading
import unittest
from http.server import ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "..", "..", "scripts"))
import test_console as tc


def load(name, rel):
    spec = importlib.util.spec_from_file_location(name, os.path.join(HERE, "..", "..", rel))
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def get(port, path, headers=None):
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=15)
    c.request("GET", path, headers={"Host": f"127.0.0.1:{port}", **(headers or {})})
    r = c.getresponse()
    out = (r.status, json.loads(r.read() or b"{}"))
    c.close()
    return out


class ConsoleHealth(unittest.TestCase):
    setUp, tearDown = tc.Server.setUp, tc.Server.tearDown

    def health(self):
        code, body = get(self.port, "/api/health", {"X-Jarvis-Console": self.srv.console_secret})
        self.assertEqual(code, 200)
        return body

    def test_ready_only_when_the_kernel_answers_and_voice_models_exist(self):
        self.voice.status = lambda: {"engine": "fake", "stt": {l: {"available": True} for l in ("en", "hi", "te")},
                                     "tts": {l: {"available": True} for l in ("en", "hi", "te")}}
        h = self.health()
        self.assertTrue(h["ready"], h["problems"])
        self.assertEqual((h["status"], h["problems"]), ("ok", []))

    def test_missing_voice_models_are_not_ready(self):
        self.voice.status = lambda: {"engine": None, "stt": {"en": {"available": False}, "hi": {"available": True}}, "tts": {"te": {"available": False}}}
        h = self.health()
        self.assertFalse(h["ready"])
        self.assertEqual(h["status"], "degraded")
        self.assertIn("voice_engine_missing", h["problems"])
        self.assertIn("voice_stt_en_missing", h["problems"])
        self.assertIn("voice_tts_te_missing", h["problems"])
        self.assertTrue(h["console"]["ok"])                                  # the process itself is alive: liveness is a different question

    def test_kill_switch_and_unreachable_kernel_are_not_ready(self):
        self.k.kill("test")
        h = self.health()
        self.assertFalse(h["ready"])
        self.assertIn("kill_switch_on", h["problems"])
        self.k.resume()
        self.ksrv.shutdown()
        self.ksrv.server_close()
        h = self.health()
        self.assertFalse(h["ready"])
        self.assertIn("kernel_unreachable", h["problems"])
        self.assertFalse(h["kernel"]["reachable"])

    def test_a_refused_kernel_token_is_not_ready(self):
        with open(self.tokfile, "w") as fh:
            fh.write("wrong-token")
        h = self.health()
        self.assertFalse(h["ready"])
        self.assertTrue({"kernel_token_refused", "kernel_unreachable"} & set(h["problems"]), h["problems"])

    def test_unauthenticated_health_says_it_is_liveness_only(self):
        code, body = get(self.port, "/api/health")
        self.assertEqual(code, 200)
        self.assertTrue(body.get("liveness_only"))
        self.assertNotIn("ready", body)                                      # it does not claim a readiness it did not check
        self.assertNotIn("kernel", body)


class SpeechHealth(unittest.TestCase):
    def serve(self, **kw):
        lss = load("lss_health", "scripts/local_speech_server.py")
        handler = lss.make_handler(lambda t, l: b"", **kw)
        srv = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        self.addCleanup(srv.shutdown)
        return srv.server_address[1]

    def test_without_recognition_it_is_degraded(self):
        code, body = get(self.serve(), "/health")
        self.assertEqual((code, body["status"], body["ready"]), (200, "degraded", False))
        self.assertIn("transcription_unavailable", body["problems"])

    def test_models_missing_is_degraded_and_complete_is_ok(self):
        port = self.serve(transcribe=lambda w, l: "", readiness=lambda: {"problems": ["stt_hi_missing", "vad_missing"]})
        code, body = get(port, "/health")
        self.assertEqual((body["status"], body["ready"], body["problems"]), ("degraded", False, ["stt_hi_missing", "vad_missing"]))
        self.assertNotIn("/", json.dumps(body))                              # names only: no path
        port = self.serve(transcribe=lambda w, l: "", readiness=lambda: {"problems": []})
        self.assertEqual(get(port, "/health")[1]["status"], "ok")

    def test_a_readiness_check_that_crashes_is_not_ready(self):
        def boom():
            raise RuntimeError("x")
        body = get(self.serve(transcribe=lambda w, l: "", readiness=boom), "/health")[1]
        self.assertEqual((body["status"], body["ready"]), ("degraded", False))

    def test_the_real_engine_readiness_names_what_is_missing(self):
        lss = load("lss_real", "scripts/local_speech_server.py")
        sys.path.insert(0, os.path.join(HERE, ".."))

        class V:
            def status(self):
                return {"engine": None, "stt": {"en": {"available": False}, "hi": {"available": True}}, "tts": {"en": {"available": True}}, "vad": False}
        import types
        mod = types.ModuleType("jarvis_console.voice")
        mod.Voice, mod.VoiceError = V, ValueError
        saved = sys.modules.get("jarvis_console.voice")
        sys.modules["jarvis_console.voice"] = mod
        try:
            _, transcribe = lss.build_engines()
        finally:
            if saved is not None:
                sys.modules["jarvis_console.voice"] = saved
            else:
                sys.modules.pop("jarvis_console.voice", None)
        self.assertEqual(transcribe.readiness()["problems"], ["engine_missing", "stt_en_missing", "vad_missing"])


class WidgetStatus(unittest.TestCase):
    def setUp(self):
        self.ws = load("widget_health", "widget/widget_server.py")
        import tempfile
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def status(self, **over):
        ws = self.ws
        table = {u: {"status": "ok", "ready": True} for u in ws.SERVERS.values()}
        table["http://127.0.0.1:8799/api/health"] = {"kernel": {"reachable": True, "authorized": True, "killed": False}}
        table["http://127.0.0.1:8799/api/owner/state"] = {"busy": False, "approvals": []}
        table["http://127.0.0.1:48916/plugins"] = {"plugins": [{"id": p, "status": "running"} for p in ws.PLUGINS]}
        table.update(over)
        return ws.build_status(lambda url, timeout=2.0, data=None: table.get(url), os.path.join(self.tmp.name, "r"), os.path.join(self.tmp.name, "w.json"))

    def test_everything_ready_is_healthy(self):
        s = self.status()
        self.assertTrue(s["healthy"], s["problems"])

    def test_a_degraded_gateway_or_speech_server_is_not_healthy(self):
        ws = self.ws
        for name, payload in (("privacy", {"ok": True, "ready": False, "degraded": ["pii_model_missing"]}), ("speech", {"status": "degraded", "ready": False, "problems": ["stt_hi_missing"]})):
            s = self.status(**{ws.SERVERS[name]: payload})
            self.assertFalse(s["healthy"], name)
            self.assertTrue(any(name in p for p in s["problems"]), s["problems"])

    def test_the_gateway_being_down_or_the_kernel_token_refused_is_not_healthy(self):
        ws = self.ws
        self.assertFalse(self.status(**{ws.SERVERS["privacy"]: None})["healthy"])
        s = self.status(**{"http://127.0.0.1:8799/api/health": {"kernel": {"reachable": True, "authorized": False, "killed": False}}})
        self.assertFalse(s["healthy"])
        self.assertIn("kernel (token refused)", s["problems"])
        s = self.status(**{"http://127.0.0.1:8799/api/health": {"kernel": {"reachable": False}}})
        self.assertFalse(s["healthy"])


class WatchdogHonesty(unittest.TestCase):
    def setUp(self):
        self.wd = load("watchdog_health", "scripts/watchdog.py")
        self.good = {"reachable": True, "authorized": True, "killed": False}

    def fetch(self, table):
        return lambda url, timeout=5.0: (True, json.dumps(table[url]).encode()) if table.get(url) is not None else (False, b"")

    def test_all_good_has_no_degraded_components(self):
        wd = self.wd
        t = {wd.SERVERS["privacy"][0]: {"ready": True}, wd.SERVERS["speech"][0]: {"ready": True}, wd.WAKE_URL: {"ready": True}}
        self.assertEqual(wd.degraded_components([], self.good, self.fetch(t), wake_up=True, wake_wanted_=True), [])

    def test_kernel_states(self):
        wd = self.wd
        t = {wd.SERVERS["privacy"][0]: {"ready": True}, wd.SERVERS["speech"][0]: {"ready": True}}
        for kernel, word in (({}, "unreadable"), ({"reachable": False}, "unreachable"), ({"reachable": True, "authorized": False}, "token refused")):
            bad = wd.degraded_components([], kernel, self.fetch(t))
            self.assertTrue(any(word in b for b in bad), (kernel, bad))
        self.assertEqual(wd.degraded_components(["console"], {}, self.fetch(t)), [])        # a down console is already a "down" problem

    def test_gateway_speech_and_wake_not_ready(self):
        wd = self.wd
        t = {wd.SERVERS["privacy"][0]: {"ok": True, "ready": False, "degraded": ["pii_model_missing"]},
             wd.SERVERS["speech"][0]: {"status": "degraded", "ready": False, "problems": ["stt_en_missing"]}, wd.WAKE_URL: {"ready": False}}
        bad = wd.degraded_components([], self.good, self.fetch(t), wake_up=True, wake_wanted_=True)
        self.assertEqual(len(bad), 3, bad)
        self.assertTrue(any("pii_model_missing" in b for b in bad))
        self.assertTrue(any("stt_en_missing" in b for b in bad))
        self.assertEqual(len(wd.degraded_components([], self.good, self.fetch(t), wake_up=True, wake_wanted_=False)), 2)    # wake not wanted: not judged

    def test_a_down_component_is_not_double_reported(self):
        wd = self.wd
        self.assertEqual(wd.degraded_components(["privacy", "speech"], self.good, self.fetch({})), [])


if __name__ == "__main__":
    unittest.main()
