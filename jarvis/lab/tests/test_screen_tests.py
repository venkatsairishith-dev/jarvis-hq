"""On-screen test runs: a real virtual display and a real recording, kept only when the run fails."""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import screen_tests as S  # noqa: E402

HAVE_TOOLS = sys.platform == "linux" and shutil.which("Xvfb") and shutil.which("ffmpeg")
# A "test" that needs the display: it fails unless DISPLAY points at a live X socket, then sleeps so frames are recorded.
PROBE = ("import os,sys,time; d=os.environ.get('DISPLAY',''); "
         "ok=d.startswith(':') and os.path.exists('/tmp/.X11-unix/X'+d[1:]); print('display', d, ok); time.sleep(1.2); "
         "sys.exit(0 if ok and os.environ.get('WANT','pass')=='pass' else 1)")


def frames(path):
    r = subprocess.run(["ffprobe", "-v", "error", "-count_frames", "-select_streams", "v:0", "-show_entries",
                        "stream=nb_read_frames,width,height", "-of", "json", path], capture_output=True, text=True)
    return json.loads(r.stdout)["streams"][0]


@unittest.skipUnless(HAVE_TOOLS, "needs Linux with Xvfb and ffmpeg")
class Screen(unittest.TestCase):
    def setUp(self):
        self.out = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.out, ignore_errors=True)

    def go(self, want="pass", **kw):
        os.environ["WANT"] = want
        try:
            return S.run([sys.executable, "-c", PROBE], self.out, size="320x240", **kw)
        finally:
            os.environ.pop("WANT", None)

    def test_pass_keeps_no_video(self):
        r = self.go()
        self.assertTrue(r.passed)
        self.assertIsNone(r.video)
        self.assertIn("True", open(r.log).read())                      # the command really had the display
        self.assertFalse([f for f in os.listdir(os.path.dirname(r.log)) if f.endswith(".mp4")])

    def test_failure_keeps_a_playable_video(self):
        r = self.go("fail")
        self.assertFalse(r.passed)
        self.assertEqual(r.exit_code, 1)
        info = frames(r.video)
        self.assertEqual((info["width"], info["height"]), (320, 240))
        self.assertGreater(int(info["nb_read_frames"]), 3)
        saved = json.load(open(os.path.join(os.path.dirname(r.log), "result.json")))
        self.assertEqual(saved["video"], r.video)

    def test_keep_always_and_never(self):
        self.assertIsNotNone(self.go(keep="always").video)
        self.assertIsNone(self.go("fail", keep="never").video)

    def test_display_is_torn_down(self):
        r = self.go()
        n = [int(p[2:]) for p in os.listdir("/tmp/.X11-unix") if p.startswith("X") and p[1:].isdigit()]
        self.assertTrue(r.passed)
        self.assertEqual([x for x in n if x in S.DISPLAYS], [])

    def test_timeout_and_missing_command(self):
        r = S.run([sys.executable, "-c", "import time; time.sleep(30)"], self.out, size="320x240", timeout_s=2)
        self.assertTrue(r.timed_out and r.exit_code == 124 and r.video)
        r = S.run(["/nonexistent/tool"], self.out, size="320x240")
        self.assertEqual(r.exit_code, 127)

    def test_refusals(self):
        with self.assertRaises(S.ScreenError):
            S.run([], self.out)
        with self.assertRaises(S.ScreenError):
            S.run(["true"], self.out, keep="sometimes")

    def test_cli(self):
        code = S.main(["--out", self.out, "--name", "cli", "--size", "320x240", "--", sys.executable, "-c", "print('ok')"])
        self.assertEqual(code, 0)
        self.assertEqual(S.main(["--out", self.out]), 2)


if __name__ == "__main__":
    unittest.main()
