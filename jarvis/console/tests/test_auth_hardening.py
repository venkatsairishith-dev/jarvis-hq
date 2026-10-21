"""Completion H (2026-10-19): sign-in hardening of the console and the widget, over real HTTP: brute-force limit, one-time-code replay (also
concurrent), session fixation, CSRF on cookie-authenticated writes, Origin/Referer/Sec-Fetch variants, cookie flags, response headers on
sensitive answers, redirects, and no token/code/cookie in logs, error bodies or URLs. Synthetic canaries only."""
import contextlib
import http.client
import io
import json
import os
import sys
import threading
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import test_console as tc
import test_http_surface_matrix as mx


def call(port, method, path, headers=None, body=None, host=None):
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=15)
    h = {"Host": host if host is not None else f"127.0.0.1:{port}"}
    h.update(headers or {})
    data = json.dumps(body).encode() if body is not None else None
    if data is not None:
        h["Content-Type"] = "application/json"
    c.request(method, path, body=data, headers=h)
    r = c.getresponse()
    out = (r.status, r.read(), dict(r.getheaders()))
    c.close()
    return out


class Mixin:
    """Needs: self.port, self.master_header (dict), self.gate, self.sensitive (a GET path), self.write_path."""

    def sign_in(self):
        code = json.loads(call(self.port, "POST", "/auth/code", self.master_header, {})[1])["code"]
        status, body, h = call(self.port, "POST", "/auth/session", {}, {"code": code})
        self.assertEqual(status, 200, body)
        cookie = h["Set-Cookie"].split(";")[0]
        return cookie, self.gate.csrf(cookie.split("=", 1)[1]), code, h["Set-Cookie"]

    def test_cookie_flags_over_real_http(self):
        _, _, _, sc = self.sign_in()
        parts = [p.strip() for p in sc.split(";")]
        self.assertIn("HttpOnly", parts)
        self.assertIn("SameSite=Strict", parts)
        self.assertIn("Path=/", parts)
        self.assertTrue(any(p.startswith("Max-Age=") for p in parts))
        self.assertFalse(any(p.lower().startswith(("domain=", "secure")) for p in parts))     # no Secure on plain-http loopback: a Secure cookie would never be sent

    def test_concurrent_redemption_of_one_code_gives_exactly_one_session(self):
        code = json.loads(call(self.port, "POST", "/auth/code", self.master_header, {})[1])["code"]
        results = []
        barrier = threading.Barrier(8)

        def go():
            barrier.wait()
            results.append(call(self.port, "POST", "/auth/session", {}, {"code": code})[0])
        ts = [threading.Thread(target=go) for _ in range(8)]
        [t.start() for t in ts]
        [t.join() for t in ts]
        self.assertEqual(sorted(results), [200] + [401] * 7)

    def test_session_fixation_a_chosen_session_id_is_never_adopted(self):
        name = self.gate.cookie
        code = json.loads(call(self.port, "POST", "/auth/code", self.master_header, {})[1])["code"]
        status, _, h = call(self.port, "POST", "/auth/session", {"Cookie": f"{name}=ATTACKER-CHOSEN-SESSION-ID-0123456789"}, {"code": code})
        self.assertEqual(status, 200)
        self.assertNotIn("ATTACKER-CHOSEN", h["Set-Cookie"])
        self.assertEqual(call(self.port, "GET", self.sensitive, {"Cookie": f"{name}=ATTACKER-CHOSEN-SESSION-ID-0123456789"})[0], 401)

    def test_cookie_alone_cannot_write_and_cross_site_requests_cannot_use_the_cookie(self):
        cookie, csrf, _, _ = self.sign_in()
        self.assertEqual(call(self.port, "GET", self.sensitive, {"Cookie": cookie})[0], 200)
        for fetch_site in ("cross-site", "same-site", "bogus"):
            self.assertEqual(call(self.port, "GET", self.sensitive, {"Cookie": cookie, "Sec-Fetch-Site": fetch_site})[0], 401, fetch_site)
            status = call(self.port, "POST", self.write_path, {"Cookie": cookie, self.header: csrf, "Sec-Fetch-Site": fetch_site}, {})[0]
            self.assertIn(status, (401, 403), fetch_site)
        for method in ("POST", "DELETE", "PUT", "PATCH"):                         # cookie only: no double-submit token
            self.assertIn(call(self.port, method, self.write_path, {"Cookie": cookie}, {})[0], (401, 403, 404, 405, 501), method)
        self.assertEqual(self.changed(), [])

    def test_origin_and_referer_variants_with_a_valid_session(self):
        cookie, csrf, _, _ = self.sign_in()
        p = self.port
        bad = ("null", "http://evil.example", f"http://127.0.0.1:{p}.evil.example", f"http://127.0.0.1.evil.example:{p}", f"https://127.0.0.1:{p}",
               f"http://127.0.0.1:{p + 1}", "http://localhost", "http://[::1]:%d" % p, f"http://user@127.0.0.1:{p}", "file://", "")
        for origin in bad:
            self.assertEqual(call(p, "GET", self.sensitive, {"Cookie": cookie, "Origin": origin})[0], 401, origin)
            self.assertIn(call(p, "POST", self.write_path, {"Cookie": cookie, self.header: csrf, "Origin": origin}, {})[0], (401, 403), origin)
        for origin in (f"http://127.0.0.1:{p}", f"http://localhost:{p}"):
            self.assertEqual(call(p, "GET", self.sensitive, {"Cookie": cookie, "Origin": origin})[0], 200, origin)
        # Referer is never an authority: a hostile one changes nothing either way
        self.assertEqual(call(p, "GET", self.sensitive, {"Cookie": cookie, "Referer": "http://evil.example/"})[0], 200)
        self.assertEqual(call(p, "GET", self.sensitive, {"Referer": f"http://127.0.0.1:{p}/"})[0], 401)

    def test_brute_force_on_auth_locks_new_sign_ins_but_not_open_sessions(self):
        cookie, csrf, _, _ = self.sign_in()
        for i in range(self.gate.FAIL_LIMIT):
            self.assertEqual(call(self.port, "POST", "/auth/session", {}, {"code": f"guess-{i:04d}-xxxxxxxx"})[0], 401)
        status, body, h = call(self.port, "POST", "/auth/session", {}, {"code": "another-guess-0000"})
        self.assertEqual(status, 429)
        self.assertTrue(int(h["Retry-After"]) >= 1)
        # even a real code and the real master token do not open /auth while locked; the open session and the master token still READ
        self.assertEqual(call(self.port, "POST", "/auth/code", self.master_header, {})[0], 429)
        self.assertEqual(call(self.port, "GET", self.sensitive, {"Cookie": cookie})[0], 200)
        self.assertEqual(call(self.port, "GET", self.sensitive, self.master_header)[0], 200)
        # after the lockout the gate opens again (fake clock; no sleeping)
        real = self.gate.clock
        self.gate.clock = lambda: real() + self.gate.LOCKOUT_S + 1
        try:
            self.assertEqual(call(self.port, "POST", "/auth/code", self.master_header, {})[0], 200)
        finally:
            self.gate.clock = real

    def test_bad_master_tokens_on_auth_code_count_towards_the_limit(self):
        for i in range(self.gate.FAIL_LIMIT):
            self.assertEqual(call(self.port, "POST", "/auth/code", {self.header: f"wrong-{i}"}, {})[0], 401)
        self.assertEqual(call(self.port, "POST", "/auth/code", self.master_header, {})[0], 429)

    def test_sensitive_and_refused_answers_are_no_store_nosniff_and_never_redirect(self):
        cookie, _, _, _ = self.sign_in()
        probes = [("GET", self.sensitive, {"Cookie": cookie}), ("GET", self.sensitive, {}), ("POST", self.write_path, {}), ("GET", "/nope", {}),
                  ("GET", "/", {}), ("GET", "/", {"Cookie": cookie}), ("POST", "/auth/session", {}), ("GET", "/auth/session", {})]
        for method, path, hdr in probes:
            status, body, h = call(self.port, method, path, hdr, {} if method == "POST" else None)
            low = {k.lower(): v for k, v in h.items()}
            self.assertNotIn("location", low, (method, path))
            self.assertFalse(300 <= status < 400, (method, path, status))
            self.assertIn("no-store", low.get("cache-control", ""), (method, path, status))
            self.assertEqual(low.get("x-content-type-options"), "nosniff", (method, path, status))
            self.assertNotIn("access-control-allow-origin", low, (method, path))

    def test_nothing_secret_in_logs_error_bodies_or_the_login_script(self):
        code = json.loads(call(self.port, "POST", "/auth/code", self.master_header, {})[1])["code"]
        err, out = io.StringIO(), io.StringIO()
        with contextlib.redirect_stderr(err), contextlib.redirect_stdout(out):
            _, body1, _ = call(self.port, "POST", "/auth/session", {}, {"code": code})                 # ok
            _, body2, _ = call(self.port, "POST", "/auth/session", {}, {"code": code})                 # replay
            _, body3, _ = call(self.port, "POST", "/auth/session", {}, {"code": "x" * 40 + mx.CANARY})
            _, body4, _ = call(self.port, "GET", self.sensitive, {self.header: "wrong-" + mx.CANARY})
            _, body5, _ = call(self.port, "POST", self.write_path, {self.header: "wrong-" + mx.CANARY}, {"code": code})
            time.sleep(0.2)
        master = self.gate.master
        for text in (err.getvalue(), out.getvalue(), *(b.decode("utf-8", "replace") for b in (body2, body3, body4, body5))):
            self.assertNotIn(master, text)
            self.assertNotIn(code, text)
            self.assertNotIn(mx.CANARY, text)
        js = call(self.port, "GET", "/auth/login.js")[1].decode()
        self.assertIn("history.replaceState", js)                                            # the code leaves the address bar and the history entry
        self.assertLess(js.index("history.replaceState"), js.index("fetch("))                # before any network call
        self.assertNotIn("localStorage", js)


class ConsoleHardening(Mixin, unittest.TestCase):
    setUp, tearDown = tc.Server.setUp, tc.Server.tearDown

    def setUp(self):
        tc.Server.setUp(self)
        self.gate, self.header = self.srv.gate, "X-Jarvis-Console"
        self.master_header = {self.header: self.srv.console_secret}
        self.sensitive, self.write_path = "/api/owner/state", "/api/owner/command"
        before = self.k.db.execute("SELECT COUNT(*) FROM owner_commands").fetchone()[0]
        self.changed = lambda: [] if self.k.db.execute("SELECT COUNT(*) FROM owner_commands").fetchone()[0] == before else ["command"]


class WidgetHardening(Mixin, unittest.TestCase):
    def setUp(self):
        self.c = mx._Case()
        import tempfile
        self.c.tmp = tempfile.TemporaryDirectory()
        self.sf = mx.start_widget(self.c)
        self.port = self.sf.port
        self.gate = self.c.wsrv.RequestHandlerClass.gate
        self.header = "X-Jarvis-Control"
        self.master_header = {self.header: self.sf.master}
        self.sensitive, self.write_path = "/api/status", "/api/task"
        self.changed = lambda: [c for c in self.c.calls if c[1]]      # a signed-in session is expected here; only forwarded POST bodies would be a change

    def tearDown(self):
        self.c.wsrv.shutdown()
        self.c.tmp.cleanup()


if __name__ == "__main__":
    unittest.main()
