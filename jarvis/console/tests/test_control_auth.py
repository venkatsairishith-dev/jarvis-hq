"""jarvis/scripts/control_auth.py: the per-launch secrets of the widget, wake status and speech control routes (audit M15)."""
import importlib.util
import os
import stat
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location("control_auth_t", os.path.join(HERE, "..", "..", "scripts", "control_auth.py"))
ca = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ca)


class Checks(unittest.TestCase):
    def test_secret_comparison(self):
        s = ca.new_secret()
        self.assertGreaterEqual(len(s), 32)
        self.assertNotEqual(s, ca.new_secret())
        self.assertTrue(ca.secret_ok(s, s))
        for bad in (None, "", "1", s[:-1], s + "x", 5, b"x"):
            self.assertFalse(ca.secret_ok(bad, s), bad)
        self.assertFalse(ca.secret_ok("", ""))                    # an unset secret never matches the empty header

    def test_exact_host(self):
        for h in ("127.0.0.1:48940", "localhost:48940", "LOCALHOST:48940", "[::1]:48940"):
            self.assertTrue(ca.host_exact(h, 48940), h)
        for h in ("127.0.0.1", "127.0.0.1:48941", "evil.example:48940", "127.0.0.1.evil.example:48940", "", None, "[::1]", "::1:48940"):
            self.assertFalse(ca.host_exact(h, 48940), h)

    def test_origin_allow_list(self):
        allowed = ca.loopback_origins(48940)
        self.assertTrue(ca.origin_ok(None, allowed))
        self.assertTrue(ca.origin_ok("http://127.0.0.1:48940/", allowed))
        self.assertTrue(ca.origin_ok("HTTP://LOCALHOST:48940", allowed))
        for o in ("null", "", "http://evil.example", "https://127.0.0.1:48940", "http://127.0.0.1:9999"):
            self.assertFalse(ca.origin_ok(o, allowed), o)


class TokenFiles(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = os.path.join(self.tmp.name, "run")

    def tearDown(self):
        self.tmp.cleanup()

    def test_publish_read_roundtrip_and_replace(self):
        self.assertIsNone(ca.read("widget", self.dir))
        path = ca.publish("widget", "tok-1", self.dir)
        self.assertEqual(ca.read("widget", self.dir), "tok-1")
        self.assertEqual(ca.headers_for("widget", self.dir), {"X-Jarvis-Control": "tok-1"})
        ca.publish("widget", "tok-2", self.dir)
        self.assertEqual(ca.read("widget", self.dir), "tok-2")
        self.assertEqual(os.listdir(self.dir), ["widget.token"])      # no temp file is left behind
        ca.unpublish("widget", self.dir)
        self.assertFalse(os.path.exists(path))
        self.assertEqual(ca.headers_for("widget", self.dir), {})

    @unittest.skipIf(os.name == "nt", "POSIX permission bits")
    def test_files_are_private_and_a_loosened_file_is_not_trusted(self):
        path = ca.publish("wake", "tok", self.dir)
        self.assertEqual(stat.S_IMODE(os.stat(path).st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(os.stat(self.dir).st_mode), 0o700)
        os.chmod(path, 0o644)                                          # readable by other users: do not trust or use it
        self.assertIsNone(ca.read("wake", self.dir))
        os.chmod(path, 0o666)
        self.assertIsNone(ca.read("wake", self.dir))

    def test_token_names_cannot_escape_the_folder(self):
        for bad in ("../x", "a/b", "", "a b", "..\\x"):
            with self.assertRaises(ValueError):
                ca.publish(bad, "t", self.dir)
            self.assertIsNone(ca.read(bad, self.dir))

    def test_run_dir_override(self):
        os.environ["JARVIS_RUN_DIR"] = self.dir
        try:
            self.assertEqual(ca.run_dir(), self.dir)
            ca.publish("speech", "s")
            self.assertEqual(ca.read("speech"), "s")
        finally:
            del os.environ["JARVIS_RUN_DIR"]

class GateTests(unittest.TestCase):
    def gate(self, now=None):
        t = [1000.0] if now is None else now
        return ca.Gate("t", "X-T", "t_session", clock=lambda: t[0]), t

    def test_codes_are_single_use_short_lived_and_capped(self):
        g, t = self.gate()
        code = g.issue_code()
        self.assertIsNotNone(g.redeem(code, "127.0.0.1:1"))
        self.assertIsNone(g.redeem(code, "127.0.0.1:1"))                         # spent
        code = g.issue_code()
        t[0] += g.code_ttl + 1
        self.assertIsNone(g.redeem(code, "127.0.0.1:1"))                         # expired
        codes = [g.issue_code() for _ in range(g.max_codes + 3)]
        self.assertLessEqual(len(g._codes), g.max_codes)
        self.assertIsNone(g.redeem(codes[0], "h:1"))                              # the oldest made room
        self.assertIsNotNone(g.redeem(codes[-1], "h:1"))
        for bad in (None, 5, "", "short", "x" * 300):
            self.assertIsNone(g.redeem(bad, "h:1"))
        self.assertIsNone(g.redeem(g.issue_code(), ""))                           # no Host to bind to

    def test_a_session_is_bound_to_its_host_expires_and_is_capped(self):
        g, t = self.gate()
        sid = g.redeem(g.issue_code(), "127.0.0.1:8799")
        ck = lambda host: {"Cookie": f"t_session={sid}", "Host": host}           # noqa: E731
        self.assertEqual(g.session_of(ck("127.0.0.1:8799")), sid)
        self.assertEqual(g.session_of(ck("127.0.0.1:8799".upper())), sid)
        self.assertIsNone(g.session_of(ck("localhost:8799")))
        self.assertIsNone(g.session_of({"Cookie": f"other={sid}", "Host": "127.0.0.1:8799"}))
        self.assertIsNone(g.session_of({"Cookie": "t_session=;;;", "Host": "127.0.0.1:8799"}))
        self.assertIsNone(g.session_of({"Host": "127.0.0.1:8799"}))
        t[0] += g.session_ttl + 1
        self.assertIsNone(g.session_of(ck("127.0.0.1:8799")))                    # expired
        for _ in range(g.max_sessions + 5):
            g.redeem(g.issue_code(), "h:1")
        self.assertLessEqual(len(g._sessions), g.max_sessions)

    def test_the_double_submit_token_is_per_session_and_is_not_the_master_token(self):
        g, _ = self.gate()
        a = g.redeem(g.issue_code(), "h:1")
        b = g.redeem(g.issue_code(), "h:1")
        self.assertNotEqual(g.csrf(a), g.csrf(b))
        self.assertNotIn(g.master, (g.csrf(a), g.csrf(b)))
        h = {"Host": "127.0.0.1:9", "Cookie": f"t_session={a}"}
        self.assertFalse(g.write_ok(h, 9))                                         # cookie alone (and Host not its bound one)
        g2 = ca.Gate("t", "X-T", "t_session")
        self.assertNotEqual(g2.master, g.master)

    def test_cookie_attributes(self):
        g, _ = self.gate()
        c = g.cookie_header("abc")
        self.assertTrue(c.startswith("t_session=abc;") and "HttpOnly" in c and "SameSite=Strict" in c and "Path=/" in c)
        self.assertNotIn("Domain", c)
        self.assertNotIn("Secure", c)                                              # plain http on loopback

    def test_login_page_holds_no_secret_and_loads_its_script_from_the_same_origin(self):
        page = ca.login_page('<script>"x"</script> JARVIS').decode()
        self.assertNotIn("<script>\"x\"", page)                                    # the product name is escaped to safe characters
        self.assertIn('src="/auth/login.js"', page)
        self.assertEqual(page.count("<script"), 1)
        self.assertNotIn("code=", page)


class OpenConsole(unittest.TestCase):
    def test_launch_url_uses_the_token_file_and_fails_plainly_without_it(self):
        import json as _json
        import urllib.error
        spec2 = importlib.util.spec_from_file_location("open_console_t", os.path.join(HERE, "..", "..", "scripts", "open_console.py"))
        oc = importlib.util.module_from_spec(spec2)
        spec2.loader.exec_module(oc)
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(RuntimeError) as cm:
                oc.launch_url("console", d)
            self.assertIn("console.token", str(cm.exception))
            ca.publish("console", "MASTER-T", d)
            seen = []

            class R:
                def __enter__(self): return self
                def __exit__(self, *a): return False
                def read(self): return _json.dumps({"code": "CODE-ONE-TIME"}).encode()
            url = oc.launch_url("console", d, opener=lambda req, timeout=None: seen.append((req.full_url, dict(req.header_items()))) or R())
            self.assertEqual(url, "http://127.0.0.1:8799/#code=CODE-ONE-TIME")
            self.assertEqual(seen[0][0], "http://127.0.0.1:8799/auth/code")
            self.assertEqual(seen[0][1]["X-jarvis-console"], "MASTER-T")

            def refuse(req, timeout=None):
                raise urllib.error.HTTPError(req.full_url, 401, "no", {}, None)
            with self.assertRaises(RuntimeError) as cm:
                oc.launch_url("console", d, opener=refuse)
            self.assertIn("refused the token", str(cm.exception))


if __name__ == "__main__":
    unittest.main()
