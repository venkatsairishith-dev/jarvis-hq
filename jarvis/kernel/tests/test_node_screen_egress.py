"""The node never lets OCR text, window text, event messages or a screenshot leave in a job result or to a remote vision model
(audit 2026-10-18, Low: OCR / file-name egress). SYNTHETIC canaries only. The OCR engine and the window capture are Windows-only,
so they are replaced at their own seam (vision.capture / vision.ocr / the PowerShell runner); everything after that seam is real:
diag.run, vision.look, the local result file, the HTTP call to the vision model (a real local socket)."""
import json
import os
import sys
import tempfile
import threading
import unittest
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(__file__)
sys.path.insert(0, os.path.join(HERE, "..", "..", "node"))
from jarvis_node import diag, vision  # noqa: E402

C_OCR = "CANARY-OCR-9d41-Priyanka-SYNTH"
C_PATH = r"C:\Users\SYNTH-Owner\Salary-2026.xlsx"
C_EMAIL = "priyanka.synth9d41@example.invalid"
C_STREET = "CANARY-9d41-12-Elm-Street-SYNTH"
C_KEY = "sk-" + "CANARY9d41SYNTHKEY" + "0123456789abcdef"
LINES = [f"Dear {C_OCR},", f"file {C_PATH}", f"mail {C_EMAIL}", C_STREET, f"key {C_KEY}"]
CANARIES = (C_OCR, "Salary-2026", "SYNTH-Owner", C_EMAIL, C_STREET, C_KEY)


def leaked(blob) -> list:
    text = blob if isinstance(blob, str) else json.dumps(blob, ensure_ascii=False, default=str)
    return [c for c in CANARIES if c in text or c.replace("\\", "\\\\") in text]


class NodeScreenText(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["JARVIS_OUTPUTS"] = self.tmp.name
        from jarvis_node.paths import local_result_dir
        os.makedirs(local_result_dir(), exist_ok=True)                        # the real capture creates it
        self.png = os.path.join(self.tmp.name, "window.png")
        with open(self.png, "wb") as f:
            f.write(b"\x89PNG-synthetic")
        self.saved = (vision.capture, vision.ocr, vision.VISION_URL, vision.ask_vision)
        vision.capture = lambda proc, scale=2: (self.png, {"width": 400, "height": 200, "scale": 1})
        vision.ocr = lambda png, scale=1: [{"text": t, "words": [{"text": w, "x": 10 + 40 * i, "y": 20 * (n + 1), "w": 35, "h": 10}
                                                                  for i, w in enumerate(t.split())]} for n, t in enumerate(LINES)]

    def tearDown(self):
        vision.capture, vision.ocr, vision.VISION_URL, vision.ask_vision = self.saved
        os.environ.pop("JARVIS_OUTPUTS", None)
        self.tmp.cleanup()

    def local_file(self, result):
        with open(result["local_result"], encoding="utf-8") as f:
            return json.load(f)

    def test_window_ocr_result_holds_counts_only_and_the_text_is_in_the_local_file(self):
        r = diag.run({"op": "window_ocr", "target": "notepad"}, lister=lambda: [])
        self.assertTrue(r["ok"], r)
        self.assertEqual(leaked(r), [], r)                                   # not a character of the screen in the job result
        self.assertEqual(r["lines"], len(LINES))
        self.assertIn(C_OCR, "\n".join(self.local_file(r)["observed_lines"]))   # it is in the local file, which the plugin reads once
        self.assertTrue(r["local_result"].startswith(os.path.realpath(self.tmp.name)))

    def test_window_locate_returns_a_point_not_text_and_a_miss_does_not_echo_the_screen(self):
        hit = diag.run({"op": "window_locate", "target": "notepad|Dear"}, lister=lambda: [])
        self.assertTrue(hit["ok"] and hit["basis"] == "observed" and "point" in hit, hit)
        self.assertEqual(leaked(hit), [])
        vision.ask_vision = lambda *a, **k: "[-1,-1]"
        miss = diag.run({"op": "window_locate", "target": "notepad|nothing-like-this"}, lister=lambda: [])
        self.assertTrue(miss["ok"] and miss["point"] is None, miss)
        self.assertEqual(leaked(miss), [])

    def test_window_text_and_event_messages_stay_in_the_local_file(self):
        rows = [{"title": "Sample App", "dialog": True, "texts": [f"cannot open {C_PATH}", C_OCR, C_EMAIL], "buttons": ["OK"]}]
        run = lambda script, env, timeout: __import__("subprocess").CompletedProcess([], 0, json.dumps(rows), "")  # noqa: E731
        r = diag.run({"op": "window_text", "target": "sampleapp"}, runner=run)
        self.assertEqual(leaked(r), [])
        self.assertIn(C_OCR, json.dumps(self.local_file(r)))
        events = [{"time": "2026-10-18T10:00:00", "provider": "Application Error", "id": 1000, "level": "Error",
                   "message": f"faulting module in {C_PATH} for {C_OCR} {C_KEY}"}]
        run = lambda script, env, timeout: __import__("subprocess").CompletedProcess([], 0, json.dumps(events), "")  # noqa: E731
        r = diag.run({"op": "app_errors", "target": "sampleapp"}, runner=run)
        self.assertEqual(leaked(r), [])
        self.assertEqual(r["summary"]["event_ids"], [1000])
        self.assertIn(C_OCR, json.dumps(self.local_file(r)))

    def test_a_vision_model_that_is_not_on_this_pc_never_gets_the_screenshot(self):
        opened = []
        real = urllib.request.urlopen
        urllib.request.urlopen = lambda *a, **k: opened.append(a) or (_ for _ in ()).throw(AssertionError("a request was made"))
        try:
            for url in ("https://vision.example.invalid/v1/chat/completions", "http://10.1.2.3:8080/v1/chat/completions",
                        "http://127.0.0.1.evil.invalid/v1", "http://user:pw@127.0.0.1:8080/v1", "file:///etc/passwd", ""):
                vision.VISION_URL = url
                with self.assertRaises(vision.VisionError, msg=url) as cm:
                    vision.ask_vision("/nonexistent/never-read.png", "where is OK")       # the picture is not even opened
                self.assertIn("not on this PC", str(cm.exception))
        finally:
            urllib.request.urlopen = real
        self.assertEqual(opened, [])

    def test_a_vision_model_on_this_pc_still_works(self):
        got = []

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_POST(self):
                got.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
                raw = json.dumps({"choices": [{"message": {"content": "[500,250]"}}]}).encode()
                self.send_response(200)
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)
        srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        try:
            vision.VISION_URL = f"http://127.0.0.1:{srv.server_address[1]}/v1/chat/completions"
            self.assertEqual(vision.ask_vision(self.png, "where"), "[500,250]")
            self.assertEqual(len(got), 1)
        finally:
            srv.shutdown()
            srv.server_close()


if __name__ == "__main__":
    unittest.main()
