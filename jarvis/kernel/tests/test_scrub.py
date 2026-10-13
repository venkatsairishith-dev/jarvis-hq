import json
import os
import random
import string
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(__file__)
sys.path.insert(0, os.path.join(HERE, ".."))
from jarvis_kernel import scrub as S
from jarvis_kernel.gateway import Provider, call
from jarvis_kernel.store import Kernel
from jarvis_kernel.vault import write

rnd = random.Random(11)
def rr(n, alpha=string.ascii_letters + string.digits):
    return "".join(rnd.choice(alpha) for _ in range(n))

# made-up, random values in real formats: the built-in pass misses these, gitleaks catches them
SECRETS = {
    "slack": "xoxb-" + rr(11, string.digits) + "-" + rr(12, string.digits) + "-" + rr(24),
    "gcp": "key=AIza" + rr(35, string.ascii_letters + string.digits + "-_"),
    "stripe": "sk_live_" + rr(24),
    "sendgrid": "SG." + rr(22, string.ascii_letters + string.digits + "-_") + "." + rr(43, string.ascii_letters + string.digits + "-_"),
}
HAVE = S.gitleaks_path() is not None


def reset(env=None):
    S._GL["checked"] = False; S._GL["path"] = None
    if env is None: os.environ.pop("GITLEAKS_BIN", None)
    else: os.environ["GITLEAKS_BIN"] = env


@unittest.skipUnless(HAVE, "gitleaks not installed on this machine")
class Deep(unittest.TestCase):
    def setUp(self): reset()

    def test_builtin_alone_misses_them_deep_catches_them(self):
        for name, secret in SECRETS.items():
            text = f"here is a value: {secret} end"
            shallow = S.scrub(text)[0]
            deep, findings, blocked = S.scrub(text, deep=True)
            self.assertIn(secret, shallow, name)                       # the cheap pass does not see it
            self.assertNotIn(secret, deep, name)                       # the deep pass removes it
            self.assertTrue(any(f.startswith("gitleaks:") for f in findings), name)
            self.assertFalse(blocked)

    def test_findings_never_contain_values(self):
        _, findings, _ = S.scrub("x " + SECRETS["slack"], deep=True)
        self.assertFalse(any(SECRETS["slack"][:10] in f for f in findings))

    def test_plain_text_is_unchanged(self):
        t = "The quarterly report shows revenue up four percent; run pip install fastembed. Meeting at 5pm."
        self.assertEqual(S.scrub(t, deep=True), (t, [], False))

    def test_private_key_still_blocks(self):
        self.assertTrue(S.scrub("-----BEGIN OPENSSH PRIVATE KEY-----\nabc", deep=True)[2])

    def test_gateway_vault_and_telegram_paths_use_the_deep_pass(self):
        seen = []
        class H(BaseHTTPRequestHandler):
            def log_message(self, *a): pass
            def do_POST(self):
                seen.append(self.rfile.read(int(self.headers["Content-Length"])).decode())
                out = json.dumps({"choices": [{"message": {"content": "ok"}}]}).encode()
                self.send_response(200); self.send_header("Content-Length", str(len(out))); self.end_headers(); self.wfile.write(out)
        srv = ThreadingHTTPServer(("127.0.0.1", 0), H); threading.Thread(target=srv.serve_forever, daemon=True).start()
        with tempfile.TemporaryDirectory() as d:
            k = Kernel(os.path.join(d, "d"))
            call(k, [Provider("p", "private", f"http://127.0.0.1:{srv.server_address[1]}/v1", None, ["any"])], "private", "use " + SECRETS["slack"])
            self.assertNotIn(SECRETS["slack"], seen[0])
            root = os.path.join(d, "vault"); os.makedirs(os.path.join(root, "inbox"))
            p = write(root, "inbox/n.md", "note " + SECRETS["stripe"], kernel=k)
            self.assertNotIn(SECRETS["stripe"], open(p).read())
            from jarvis_kernel.telegram import Bridge
            sent = []
            class API:
                def send(self, c, t): sent.append(t)
            Bridge(k, API(), 1, []).say("answer " + SECRETS["sendgrid"])
            self.assertNotIn(SECRETS["sendgrid"], sent[0])
            k.db.close()
        srv.shutdown()


class Fallback(unittest.TestCase):
    def tearDown(self): reset()

    def test_missing_binary_falls_back_to_the_builtin_pass(self):
        reset(os.path.join(tempfile.gettempdir(), "no-such-gitleaks.exe"))
        text = "token ghp_" + rr(36) + " and " + SECRETS["slack"]
        clean, findings, blocked = S.scrub(text, deep=True)
        self.assertNotIn("ghp_", clean)                       # built-in still redacts what it knows
        self.assertFalse(blocked)

    def test_broken_binary_does_not_crash_or_leak_the_error(self):
        with tempfile.TemporaryDirectory() as d:
            fake = os.path.join(d, "fake-gitleaks.cmd" if os.name == "nt" else "fake-gitleaks")
            open(fake, "w").write("@echo off\r\nexit /b 3\r\n" if os.name == "nt" else "#!/bin/sh\nexit 3\n")
            if os.name != "nt": os.chmod(fake, 0o755)
            reset(fake)
            self.assertEqual(S.scrub("hello world", deep=True)[:2], ("hello world", []))


if __name__ == "__main__":
    unittest.main()
