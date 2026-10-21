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
        # Slack, Google and Stripe live keys moved into the built-in pass (audit SEC-D7, 2026-10-18): it no longer misses
        # them, so only SendGrid is still a "deep pass only" example. Every value must still be gone after the deep pass.
        builtin_now = {"slack": "slack_token", "gcp": "google_api_key", "stripe": "stripe_live_key"}
        for name, secret in SECRETS.items():
            text = f"here is a value: {secret} end"
            shallow = S.scrub(text)[0]
            deep, findings, blocked = S.scrub(text, deep=True)
            if name in builtin_now:
                self.assertNotIn(secret, shallow, name)                # the cheap pass catches it now
                self.assertIn(builtin_now[name], findings, name)
            else:
                self.assertIn(secret, shallow, name)                   # the cheap pass does not see it
                self.assertTrue(any(f.startswith("gitleaks:") for f in findings), name)
            self.assertNotIn(secret, deep, name)                       # the deep pass removes it
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


class BuiltinProviderKeys(unittest.TestCase):
    """Audit SEC-D7 (2026-10-18): Groq keys passed both scrub passes; hf_/AIza/xox*/sk_live_ passed the built-in one; a PGP
    private key block was not blocked. Synthetic values only, each written in two pieces so no scanner sees a whole one."""

    KEYS = {"groq_key": "gsk_" + rr(52), "huggingface_token": "hf_" + rr(34, string.ascii_letters),
            "google_api_key": "AIza" + rr(35, string.ascii_letters + string.digits + "-_"),
            "slack_token": "xoxb-" + rr(12, string.digits) + "-" + rr(13, string.digits) + "-" + rr(24),
            "stripe_live_key": "sk_" + "live_" + rr(24)}

    def setUp(self):
        reset(os.path.join(tempfile.gettempdir(), "no-such-gitleaks"))   # the built-in pass alone, as when gitleaks is missing

    def tearDown(self):
        reset()

    def test_each_provider_key_is_redacted_by_the_builtin_pass(self):
        for kind, key in self.KEYS.items():
            for text in (f"config value {key}", f"GROQ_KEY {key} end", f"see:{key}."):
                clean, findings, blocked = S.scrub(text, deep=True)
                self.assertNotIn(key, clean, (kind, text[:12]))
                self.assertNotIn(key[6:20], clean, kind)                # no piece of it is left either
                self.assertIn(kind, findings)
                self.assertFalse(blocked)
                self.assertFalse(any(key[:8] in f for f in findings))   # findings name kinds, never values

    def test_a_pgp_private_key_block_blocks_the_send(self):
        block = "-----BEGIN PGP " + "PRIVATE KEY BLOCK-----\n\n" + rr(64) + "\n=" + rr(4) + "\n-----END PGP PRIVATE KEY BLOCK-----"
        for deep in (False, True):
            clean, findings, blocked = S.scrub("here it is:\n" + block, deep=deep)
            self.assertTrue(blocked)
            self.assertEqual(clean, "")
            self.assertIn("pgp_private_key", findings)
        self.assertFalse(S.scrub("-----BEGIN PGP " + "PUBLIC KEY BLOCK-----\nabc")[2])   # a public key is not a secret

    def test_ordinary_words_with_the_same_letters_are_left_alone(self):
        t = "gsk_ is Groq's prefix; hf_ is Hugging Face's; AIza starts a Google key; sk_live_ is Stripe's."
        self.assertEqual(S.scrub(t), (t, [], False))


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


# ---- audit J-018 (2026-10-18): the gitleaks process costs ~0.5 s per new text; skip it only where it provably finds nothing
import base64
import binascii
import urllib.parse
from jarvis_kernel import gitleaks_gate as G

PROVIDER_KEYS = {   # every shape the built-in pass and gitleaks are expected to know, made up and random (written in two pieces)
    "github": "ghp_" + rr(36), "aws": "AKIA" + rr(16, string.ascii_uppercase + string.digits), "groq": "gsk_" + rr(52),
    "huggingface": "hf_" + rr(34, string.ascii_letters), "google": "AIza" + rr(35, string.ascii_letters + string.digits + "-_"),
    "slack": "xoxb-" + rr(12, string.digits) + "-" + rr(13, string.digits) + "-" + rr(24), "stripe": "sk_" + "live_" + rr(24),
    "sendgrid": "SG." + rr(22, string.ascii_letters + string.digits + "-_") + "." + rr(43, string.ascii_letters + string.digits + "-_"),
    "openai": "sk-" + rr(40), "jwt": "eyJ" + rr(20) + "." + "eyJ" + rr(20) + "." + rr(24),
    "assignment": "API_KEY=" + rr(24), "bearer": "Authorization: Bearer " + rr(30),
}


def encodings(t):
    b = t.encode()
    return {"raw": t, "base64": base64.b64encode(b).decode(), "base64url": base64.urlsafe_b64encode(b).decode().rstrip("="),
            "hex": binascii.hexlify(b).decode(), "percent": "".join("%%%02X" % c for c in b), "quoted": urllib.parse.quote(t, safe=""),
            "unicode": "".join("\\u%04x" % c for c in b), "base64_with_key": base64.b64encode(("key=" + t).encode()).decode()}


class GateOff:
    """Run the deep pass exactly as it was before the gate: every text goes to gitleaks."""
    def __enter__(self):
        self.real = G.must_scan
        G.must_scan = lambda text, exe: True
        return self

    def __exit__(self, *a):
        G.must_scan = self.real


@unittest.skipUnless(HAVE, "gitleaks not installed on this machine")
class GateMatchesTheBinary(unittest.TestCase):
    """The keyword table is only as good as its match with the gitleaks build it is pinned to."""

    def setUp(self): reset()

    def test_the_table_is_exactly_what_the_pinned_binary_embeds(self):
        exe = S.gitleaks_path()
        if G.gitleaks_version(exe) != G.VERSION:
            self.skipTest(f"this gitleaks is {G.gitleaks_version(exe)}, the table is for {G.VERSION}: the gate scans everything then (next test)")
        words, keywordless, n = G.extract_keywords(exe)
        self.assertEqual(sorted(G.KEYWORDS), words)
        self.assertEqual(keywordless, [])                               # every rule with a content pattern is gated by a keyword
        self.assertEqual(list(G.KEYWORDLESS), keywordless)
        self.assertGreaterEqual(n, 200)

    def test_a_different_gitleaks_version_is_always_scanned(self):
        exe = S.gitleaks_path()
        G._VERSION[exe] = "9.99.0"
        try:
            self.assertTrue(G.must_scan("hello", exe))
        finally:
            G._VERSION.pop(exe, None)
        G._VERSION[exe] = None                                          # a version that cannot be read
        try:
            self.assertTrue(G.must_scan("hello", exe))
        finally:
            G._VERSION.pop(exe, None)


class GateDecision(unittest.TestCase):
    """The pure part: which texts may skip the process."""

    def test_plain_short_text_may_skip_it(self):
        for t in ("hi", "say hello", "open the Downloads folder please", "what is 2+2", "the quick brown fox jumps over the lazy dog"):
            self.assertFalse(G.text_may_have_findings(t), t)

    def test_anything_that_could_hold_a_secret_must_be_scanned(self):
        for t in ("my token is here", "the password is", "ey", "a%20b", "C:\\users\\x", "&#65;", "U+0041", "caf\u00e9 hello",
                  "abcdefghijkl", "AKIA", "ghp_", "BEGIN -----BEGIN", "api"):
            self.assertTrue(G.text_may_have_findings(t), t)

    def test_every_keyword_trips_it_whatever_the_case(self):
        for k in G.KEYWORDS:
            self.assertTrue(G.text_may_have_findings(f"x {k.upper()} y"), k)
            self.assertTrue(G.text_may_have_findings(f"x {k} y"), k)

    def test_a_config_override_means_scan_everything(self):
        exe = S.gitleaks_path() or "gitleaks"
        G._VERSION[exe] = G.VERSION
        try:
            self.assertFalse(G.must_scan("hi", exe))
            for var in ("GITLEAKS_CONFIG", "GITLEAKS_CONFIG_TOML"):
                os.environ[var] = "x"
                try:
                    self.assertTrue(G.must_scan("hi", exe), var)
                finally:
                    del os.environ[var]
            with tempfile.TemporaryDirectory() as d:
                here = os.getcwd()
                os.chdir(d)
                try:
                    open(".gitleaks.toml", "w").close()
                    self.assertTrue(G.must_scan("hi", exe))
                finally:
                    os.chdir(here)
        finally:
            G._VERSION.pop(exe, None)

    def test_an_error_in_the_check_means_scan(self):
        real = G.text_may_have_findings
        G.text_may_have_findings = lambda t: 1 / 0
        exe = "gitleaks"
        G._VERSION[exe] = G.VERSION
        try:
            self.assertTrue(G.must_scan("hi", exe))
        finally:
            G.text_may_have_findings = real
            G._VERSION.pop(exe, None)


@unittest.skipUnless(HAVE, "gitleaks not installed on this machine")
class GateKeepsDetection(unittest.TestCase):
    """Detection with the gate is the detection without it, for every shape (including the provider keys added for SEC-D7)."""

    def setUp(self):
        reset()
        self.exe = S.gitleaks_path()
        if G.gitleaks_version(self.exe) != G.VERSION:
            self.skipTest("the gate is off for this gitleaks version (it scans everything)")

    def runs(self, fn):
        calls = []
        real = S.subprocess.run

        def spy(args, *a, **k):
            if "stdin" in list(args):
                calls.append(1)
            return real(args, *a, **k)
        S.subprocess.run = spy
        try:
            return fn(), len(calls)
        finally:
            S.subprocess.run = real

    def test_every_shape_gives_the_same_result_with_and_without_the_gate(self):
        from concurrent.futures import ThreadPoolExecutor
        texts = [(name, enc, f"value {form} end") for name, key in PROVIDER_KEYS.items()
                 for enc, form in encodings(key).items() if enc in ("raw", "base64", "percent")]
        with GateOff():                                                 # before: every text goes to gitleaks
            with ThreadPoolExecutor(4) as pool:
                want = list(pool.map(lambda t: S.scrub(t[2], deep=True), texts))
        with ThreadPoolExecutor(4) as pool:
            got = list(pool.map(lambda t: S.scrub(t[2], deep=True), texts))
        for (name, enc, text), g, w in zip(texts, got, want):
            self.assertEqual(g, w, (name, enc))
        self.assertEqual(len(texts), 36)
        self.assertTrue(all(not any(k[6:20] in g[0] for k in [PROVIDER_KEYS[n]]) for (n, e, _), g in zip(texts, got) if e == "raw"))   # and they are gone

    def test_whatever_gitleaks_finds_the_gate_says_scan(self):
        """The property that makes the skip safe, checked against the real binary: if gitleaks reports anything in a text, the
        gate must have answered 'scan'. Every secret shape in every encoding gitleaks decodes, inside prose."""
        from concurrent.futures import ThreadPoolExecutor
        r = random.Random(2026)
        words = "the owner asked about a report on the quarterly numbers and a list of tasks for next week please".split()
        samples = []
        for name, key in PROVIDER_KEYS.items():
            for enc, form in encodings(key).items():
                pre, post = " ".join(r.choice(words) for _ in range(r.randint(0, 5))), " ".join(r.choice(words) for _ in range(r.randint(0, 5)))
                samples.append((name, enc, f"{pre} {form} {post}".strip()))
        with GateOff():
            with ThreadPoolExecutor(4) as pool:
                raw = list(pool.map(lambda t: S._gitleaks_secrets(t[2]), samples))
        found = 0
        for (name, enc, text), hits in zip(samples, raw):
            if hits:
                found += 1
                self.assertTrue(G.text_may_have_findings(text), (name, enc, text[:40]))
        self.assertGreater(found, 50)                                   # the property was tested on plenty of real detections

    def test_plain_text_never_starts_the_process_and_gets_the_same_answer(self):
        for text in ("hi", "say hello", "Open the Downloads folder please", "what is 2+2?"):
            with GateOff():
                want = S.scrub(text, deep=True)
            (got, n) = self.runs(lambda: S.scrub(text, deep=True))
            self.assertEqual(got, want)
            self.assertEqual(n, 0, text)                                # no gitleaks process for it
            (_, n2) = self.runs(lambda: S.scrub(text + " token", deep=True))
            self.assertEqual(n2, 1, text)                               # one keyword and it is scanned again

    def test_measured_cost_of_a_plain_text_is_not_the_process_start(self):
        import time
        S.scrub("warm up", deep=True)
        t0 = time.perf_counter()
        for i in range(20):
            S.scrub(f"say hello number {i} to the owner", deep=True)
        per_text_ms = (time.perf_counter() - t0) * 1000 / 20
        self.assertLess(per_text_ms, 100)                               # was ~480 ms each (a gitleaks process per text)

    def test_the_proxy_scrub_skips_plain_texts_but_still_redacts_across_the_request(self):
        from jarvis_kernel import llm_proxy as L
        calls = []
        real = L._DEEP_BATCH.scan
        L._DEEP_BATCH.scan = lambda text, mod: calls.append(text) or real(text, mod)
        try:
            body = {"messages": [{"role": "user", "content": "just say hi " + rr(6)}]}
            L._scrub_messages(body)
            self.assertEqual(calls, [])                                 # a plain new text: gitleaks is not started
            # a text that needs the scan is scanned, and what is found in it is also removed from the plain text beside it
            secret = "ghp_" + rr(36)
            body = {"messages": [{"role": "user", "content": f"deploy with token {secret} now"},
                                 {"role": "assistant", "content": "ok " + secret[:36] + " done"}]}
            out, found, blocked = L._scrub_messages(body)
            self.assertEqual(len(calls), 1)
            self.assertNotIn(secret[:20], json.dumps(out))
            self.assertTrue(any("github" in f or "gitleaks" in f for f in found))
        finally:
            L._DEEP_BATCH.scan = real

    def test_provider_keys_in_a_request_are_still_removed(self):
        from jarvis_kernel import llm_proxy as L
        for name, key in PROVIDER_KEYS.items():
            for form in (key, f"please use {key} for the call"):
                out, found, blocked = L._scrub_messages({"messages": [{"role": "user", "content": form + " " + rr(4)}]})
                self.assertNotIn(key[6:24], json.dumps(out), name)


if __name__ == "__main__":
    unittest.main()
