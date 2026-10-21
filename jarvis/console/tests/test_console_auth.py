"""Re-review M3 (2026-10-19): the console no longer hands a credential to whoever asks.

Every GET route that shows private state answers 401 without a credential; `GET /` without a session is a login page with no
secret; a browser signs in with a ONE-TIME launch code (fragment of the URL), gets an HttpOnly SameSite=Strict cookie bound to the
exact Host, and its page carries only a per-session double-submit token. Real HTTP against the real console handler."""
import http.client
import json
import os
import sys
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import test_console as tc

SENSITIVE_GET = (
    "/api/config", "/api/owner/state", "/api/owner/commands", "/api/owner/events?since=0&limit=5", "/api/owner/tasks", "/api/owner/approvals",
    "/api/files", "/api/files?workspace=1", "/api/files/get?path=task-abc123def456/report.md", "/api/files/workspace?path=x.txt",
    "/api/voice/status", "/api/voice/check/phrases", "/api/desktop/state", "/api/desktop/screen.jpg", "/api/skills", "/api/local-models",
    "/api/connectors/local", "/api/connectors/templates", "/api/bench/summary", "/api/bench/runs", "/api/bench/cases", "/api/bench/jobs",
    "/api/stream",
)
WRITES = (("POST", "/api/owner/command", {"text": "CANARY-7f3a-SYNTH hello"}), ("POST", "/api/owner/kill", {}), ("POST", "/api/classify", {"text": "x"}),
          ("DELETE", "/api/bench/challenge/ch-20261017-000000-abcdef", None))


class ConsoleAuth(unittest.TestCase):
    setUp, tearDown = tc.Server.setUp, tc.Server.tearDown                      # the same real console + kernel fixture, without its tests

    def raw(self, method, path, headers=None, body=None, host=None):
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=15)
        h = {"Host": host if host is not None else f"127.0.0.1:{self.port}"}
        h.update(headers or {})
        data = json.dumps(body).encode() if body is not None else None
        if data is not None:
            h["Content-Type"] = "application/json"
        c.request(method, path, body=data, headers=h)
        r = c.getresponse()
        out = (r.status, r.read(), dict(r.getheaders()))
        c.close()
        return out

    def sign_in(self, host=None):
        """What the launcher and the login page do: master token -> one-time code -> session cookie. Returns (cookie header, csrf)."""
        master = {"X-Jarvis-Console": self.srv.console_secret}
        code, body, _ = self.raw("POST", "/auth/code", master, {})
        self.assertEqual(code, 200)
        one_time = json.loads(body)["code"]
        code, body, h = self.raw("POST", "/auth/session", {}, {"code": one_time}, host=host)
        self.assertEqual(code, 200, body)
        cookie = h["Set-Cookie"].split(";")[0]
        sid = cookie.split("=", 1)[1]
        return cookie, self.srv.gate.csrf(sid), one_time, h["Set-Cookie"]

    # ---- the sensitive-read routes
    def test_every_sensitive_read_route_is_refused_without_a_credential_and_sends_no_data(self):
        for path in SENSITIVE_GET:
            code, body, h = self.raw("GET", path)
            self.assertEqual(code, 401, path)
            reply = json.loads(body)
            self.assertEqual(set(reply), {"error", "login"}, path)                 # a refusal, no data
            self.assertNotIn(self.srv.console_secret.encode(), body)
        for bad in ({"X-Jarvis-Console": "1"}, {"X-Jarvis-Console": ""}, {"X-Jarvis-Console": "x" * 43},
                    {"Cookie": "jarvis_console_session=forged"}, {"Cookie": "jarvis_console_session="}):
            self.assertEqual(self.raw("GET", "/api/owner/state", bad)[0], 401, bad)

    def test_every_sensitive_read_route_answers_with_the_master_token_or_a_session(self):
        master = {"X-Jarvis-Console": self.srv.console_secret}
        cookie, csrf, _, _ = self.sign_in()
        for path in SENSITIVE_GET:
            if path == "/api/stream":                                              # an endless stream: its start is enough
                continue
            for creds in (master, {"Cookie": cookie}):
                self.assertNotEqual(self.raw("GET", path, creds)[0], 401, (path, creds))
        for path in ("/api/config", "/api/owner/state", "/api/voice/status", "/api/skills"):
            self.assertEqual(self.raw("GET", path, master)[0], 200, path)
            self.assertEqual(self.raw("GET", path, {"Cookie": cookie})[0], 200, path)

    def test_health_is_liveness_only_without_a_credential(self):
        code, body, _ = self.raw("GET", "/api/health")
        self.assertEqual(code, 200)
        self.assertEqual(set(json.loads(body)), {"console", "time", "minimal", "liveness_only"})        # no kernel, voice, disk or url detail
        full = json.loads(self.raw("GET", "/api/health", {"X-Jarvis-Console": self.srv.console_secret})[1])
        self.assertIn("kernel", full)

    # ---- GET / and sign-in
    def test_get_root_without_proof_is_a_login_page_with_no_secret_anywhere(self):
        for path in ("/", "/index.html", "/tasks/abc", "/some/screen"):
            code, body, h = self.raw("GET", path)
            self.assertEqual(code, 401, path)
            blob = body + json.dumps(h).encode()
            self.assertNotIn(self.srv.console_secret.encode(), blob)
            self.assertNotIn(b"jarvis-console-secret", blob)
            self.assertNotIn("Set-Cookie", h)
            self.assertNotIn("Access-Control-Allow-Origin", h)                       # no CORS, ever
        # a static asset is public-harmless
        with open(os.path.join(self.ui, "assets", "app.js"), "w") as fh:
            fh.write("console.log(1)")
        self.assertEqual(self.raw("GET", "/assets/app.js")[0], 200)
        self.assertEqual(self.raw("GET", "/auth/login.js")[0], 200)

    def test_a_one_time_code_signs_in_once_and_the_session_cookie_is_locked_down(self):
        cookie, csrf, code, set_cookie = self.sign_in()
        self.assertIn("HttpOnly", set_cookie)
        self.assertIn("SameSite=Strict", set_cookie)
        self.assertNotIn("Domain", set_cookie)                                       # host-only
        self.assertNotIn(self.srv.console_secret, set_cookie)
        self.assertNotEqual(csrf, self.srv.console_secret)
        # replay of the used code is refused and signs nobody in
        before = len(self.srv.gate._sessions)
        code2, body, h = self.raw("POST", "/auth/session", {}, {"code": code})
        self.assertEqual(code2, 401)
        self.assertNotIn("Set-Cookie", h)
        self.assertEqual(len(self.srv.gate._sessions), before)
        # unknown and malformed codes
        for bad in ("A" * 32, "", None, 5, {"a": 1}, "x" * 500):
            self.assertEqual(self.raw("POST", "/auth/session", {}, {"code": bad})[0], 401, bad)
        self.assertEqual(self.raw("POST", "/auth/session", {}, None)[0], 401)

    def test_an_expired_code_is_refused(self):
        gate = self.srv.gate
        code = gate.issue_code()
        real = gate.clock
        gate.clock = lambda: real() + gate.code_ttl + 1
        try:
            self.assertEqual(self.raw("POST", "/auth/session", {}, {"code": code})[0], 401)
        finally:
            gate.clock = real

    def test_only_the_master_token_can_ask_for_a_code(self):
        n = len(self.srv.gate._codes)
        for headers in ({}, {"X-Jarvis-Console": "1"}, {"X-Jarvis-Console": "x" * 43}, {"Cookie": "jarvis_console_session=x"}):
            self.assertEqual(self.raw("POST", "/auth/code", headers, {})[0], 401, headers)
        self.assertEqual(len(self.srv.gate._codes), n)                              # none was issued
        self.assertEqual(self.raw("GET", "/auth/code", {"X-Jarvis-Console": self.srv.console_secret})[0], 405)
        # a signed-in browser session cannot mint codes either (only a program that holds the token file)
        cookie, csrf, _, _ = self.sign_in()
        self.assertEqual(self.raw("POST", "/auth/code", {"Cookie": cookie, "X-Jarvis-Console": csrf}, {})[0], 401)

    def test_a_cookie_is_bound_to_its_host_and_rebinding_hosts_are_refused(self):
        cookie, csrf, _, _ = self.sign_in()
        self.assertEqual(self.raw("GET", "/api/owner/state", {"Cookie": cookie})[0], 200)
        # same loopback machine, but another host string than the one the cookie was issued for: refused
        self.assertEqual(self.raw("GET", "/api/owner/state", {"Cookie": cookie}, host=f"localhost:{self.port}")[0], 401)
        # DNS rebinding: a name that is not loopback never gets an answer, cookie or not
        for host in ("evil.example", f"evil.example:{self.port}", "127.0.0.1", "127.0.0.1:1", ""):
            self.assertEqual(self.raw("GET", "/api/owner/state", {"Cookie": cookie}, host=host)[0], 403, host)
            self.assertEqual(self.raw("GET", "/", {"Cookie": cookie}, host=host)[0], 403, host)
            self.assertEqual(self.raw("POST", "/auth/code", {"X-Jarvis-Console": self.srv.console_secret}, {}, host=host)[0], 403, host)
        # a code exchanged under one Host cannot be used for a cookie of another
        self.assertEqual(self.raw("POST", "/auth/session", {"Origin": "http://evil.example"},
                                  {"code": self.srv.gate.issue_code()})[0], 403)

    def test_a_foreign_origin_never_reads_even_with_a_valid_cookie(self):
        cookie, csrf, _, _ = self.sign_in()
        for origin in ("http://evil.example", "null", "http://127.0.0.1:9", f"https://127.0.0.1:{self.port}"):
            self.assertEqual(self.raw("GET", "/api/owner/state", {"Cookie": cookie, "Origin": origin})[0], 401, origin)
        self.assertEqual(self.raw("GET", "/api/owner/state", {"Cookie": cookie, "Origin": f"http://127.0.0.1:{self.port}"})[0], 200)

    # ---- state-changing routes
    def test_a_session_needs_its_double_submit_token_to_write_and_the_master_token_works_alone(self):
        cookie, csrf, _, _ = self.sign_in()
        before = self.k.db.execute("SELECT COUNT(*) FROM owner_commands").fetchone()[0]
        body = {"text": "CANARY-7f3a-SYNTH open my downloads folder"}
        for headers in ({"Cookie": cookie}, {"Cookie": cookie, "X-Jarvis-Console": "1"}, {"Cookie": cookie, "X-Jarvis-Console": self.srv.console_secret[::-1]},
                        {"Cookie": cookie, "X-Jarvis-Console": csrf, "Origin": "http://evil.example"}, {"X-Jarvis-Console": csrf}):
            code, raw, _ = self.raw("POST", "/api/owner/command", headers, body)
            self.assertEqual(code, 403, headers)
            self.assertTrue(json.loads(raw).get("reload"))
        self.assertEqual(self.k.db.execute("SELECT COUNT(*) FROM owner_commands").fetchone()[0], before)
        self.assertEqual(self.raw("POST", "/api/owner/command", {"Cookie": cookie, "X-Jarvis-Console": csrf}, body)[0], 202)
        time.sleep(0.3)

    def test_unauthenticated_writes_change_nothing(self):
        for method, path, body in WRITES:
            for headers in ({}, {"X-Jarvis-Console": "1"}, {"Cookie": "jarvis_console_session=forged", "X-Jarvis-Console": "x"}):
                self.assertEqual(self.raw(method, path, headers, body)[0], 403, (method, path, headers))
        self.assertFalse(self.k.killed())

    def test_sessions_do_not_survive_a_restart_of_the_console(self):
        cookie, csrf, _, _ = self.sign_in()
        import copy
        from jarvis_console.server import make_server as make_again
        cfg2 = copy.copy(self.cfg)
        cfg2.port = 0
        other = make_again(cfg2, kernel=self.srv.kernel, voice=self.voice)
        try:
            self.assertIsNone(other.gate.session_of({"Cookie": cookie, "Host": f"127.0.0.1:{self.port}"}))
            self.assertFalse(other.gate.write_ok({"X-Jarvis-Console": csrf, "Cookie": cookie, "Host": f"127.0.0.1:{other.server_address[1]}"},
                                                 other.server_address[1]))
        finally:
            other.server_close()


if __name__ == "__main__":
    unittest.main()
