"""Completion H (2026-10-19): a route-by-route matrix over the local servers (console, speech).

For every route the code defines, and for every HTTP method (GET, HEAD, OPTIONS, POST, PUT, PATCH, DELETE, TRACE, CONNECT, PROPFIND and an
unknown one), an UNAUTHENTICATED request must not get a 2xx answer unless the route is public by design, must not receive any canary
state, the master token or a session token, and must change nothing. The same goes for path-normalisation tricks, absolute-form
targets, Host-header confusion (also WITH a valid token: rebinding names are refused), forwarded-header tricks and oversized requests.
Real HTTP against the real handlers; raw sockets for the requests http.client would not let us write. Synthetic canaries only."""
import http.client
import importlib.util
import json
import os
import re
import socket
import sys
import threading
import unittest
from http.server import ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import test_console as tc

CANARY = "CANARY-7f3a-Priyanka-SYNTH"
METHODS = ("GET", "HEAD", "OPTIONS", "POST", "PUT", "PATCH", "DELETE", "TRACE", "CONNECT", "PROPFIND", "FOOBAR")


def raw(port, request: bytes, timeout=10):
    """Send raw bytes, return (status, headers dict lower-cased, body)."""
    s = socket.create_connection(("127.0.0.1", port), timeout=timeout)
    try:
        s.sendall(request)
        s.shutdown(socket.SHUT_WR)
        data = b""
        while True:
            chunk = s.recv(65536)
            if not chunk:
                break
            data += chunk
            if len(data) > 4_000_000:
                break
    finally:
        s.close()
    head, _, body = data.partition(b"\r\n\r\n")
    lines = head.split(b"\r\n")
    try:
        status = int(lines[0].split()[1])
    except (IndexError, ValueError):
        status = 0
    headers = {}
    for ln in lines[1:]:
        k, _, v = ln.partition(b":")
        headers.setdefault(k.strip().lower().decode("latin-1"), v.strip().decode("latin-1"))
    return status, headers, body


def req_bytes(method, target, host="", extra=(), body=b"", version="HTTP/1.0"):
    lines = [f"{method} {target} {version}"]
    if host is not None:
        for h in ([host] if isinstance(host, str) else host):
            lines.append(f"Host: {h}")
    lines += list(extra)
    if body:
        lines += ["Content-Type: application/json", f"Content-Length: {len(body)}"]
    return ("\r\n".join(lines) + "\r\n\r\n").encode("latin-1", "replace") + body


class Surface:
    """One server under test: its port, its master token, the header that carries it, its public routes and its leak canaries."""
    name = ""
    port = 0
    master = ""
    header = ""
    public = ()                 # (method set, exact path) answered 2xx without credentials by design
    routes_get = ()
    routes_write = ()
    leaks = ()                  # bytes that must never appear in an unauthenticated answer

    def changed(self) -> list:  # observable side effects since setUp (must stay empty)
        return []


class MatrixMixin:
    surfaces: list = []

    def check(self, surface, method, target, host=None, extra=(), body=b"", version="HTTP/1.0", label=""):
        host = f"127.0.0.1:{surface.port}" if host is None else host
        status, headers, rbody = raw(surface.port, req_bytes(method, target, host, extra, body, version))
        path = target.split("?", 1)[0]
        public = method in ("GET", "HEAD") and ("/" + path.lstrip("/")) in surface.public     # CPython folds a leading // into /
        blob = rbody + json.dumps(headers).encode()
        for leak in (surface.master.encode(), CANARY.encode(), *surface.leaks):
            self.assertNotIn(leak, blob, (surface.name, method, target, label))
        self.assertNotIn("location", headers, (surface.name, method, target, label))          # no redirect anywhere (no open redirect)
        if not public:
            self.assertFalse(200 <= status < 400, (surface.name, method, target, label, status, rbody[:120]))
        return status, headers, rbody

    # ---------------------------------------------------------------- the matrix proper
    def test_every_route_by_every_method_unauthenticated(self):
        count = 0
        for sf in self.surfaces:
            targets = list(sf.routes_get) + [r for r in sf.routes_write] + ["/", "/index.html", "/nope", "/api/nope"]
            for method in METHODS:
                for target in targets:
                    body = b"{}" if method in ("POST", "PUT", "PATCH", "DELETE") else b""
                    self.check(sf, method, target, body=body, label="matrix")
                    count += 1
            self.assertEqual(sf.changed(), [], sf.name)                        # nothing happened anywhere
        self.assertGreater(count, 800)       # the real number of (server, method, route) combinations is 814

    def test_unauthenticated_requests_with_forwarded_and_override_headers_get_nothing_more(self):
        tricks = (("X-Forwarded-For: 127.0.0.1",), ("X-Forwarded-Host: 127.0.0.1",), ("X-Forwarded-Proto: https",), ("X-Original-URL: /health",),
                  ("X-Rewrite-URL: /health",), ("X-HTTP-Method-Override: GET",), ("X-Real-IP: 127.0.0.1",), ("Forwarded: for=127.0.0.1;host=127.0.0.1",),
                  ("Referer: http://127.0.0.1/",), ("Origin: http://127.0.0.1",), ("Origin: null",), ("Authorization: Bearer x",),
                  ("Cookie: session=1; jarvis_console_session=x",), ("Sec-Fetch-Site: same-origin",))
        for sf in self.surfaces:
            for extra in tricks:
                for target in sf.routes_get[:6]:
                    self.check(sf, "GET", target, extra=extra, label=str(extra))
                for target in sf.routes_write[:3]:
                    self.check(sf, "POST", target, extra=extra, body=b"{}", label=str(extra))
            self.assertEqual(sf.changed(), [], sf.name)

    def test_path_tricks_never_reach_a_private_route(self):
        for sf in self.surfaces:
            private = sf.routes_get[0]
            tricks = ["//", "/./", "/%2e/", "/..%2f", "/../", "/..", "/%2e%2e/", "//" + private.lstrip("/"), "/." + private, private + "/", private + "//",
                      private + "/.", private + "%00", private + "\x00", private + "?x=1", private + "#frag", private.upper(), private.title(),
                      private.replace("/", "\\"), private.replace("/", "%2f"), "/%2e" + private, "/health/.." + private, "/api/health/.." + private,
                      "/api/health/../owner/state", "/api/..%2fowner/state", "/api/%2e%2e/owner/state", "/api/owner%2fstate", "/api%2fowner/state",
                      "/%252e%252e/%252e%252e/etc/passwd", "/..%2f..%2f..%2fetc%2fpasswd", "/%2e%2e/%2e%2e/etc/passwd", "/....//....//etc/passwd",
                      "/static/../../etc/passwd", "/assets/../../../etc/passwd", "/a" * 40000, "/" + "a" * 70000]
            for t in tricks:
                for method in ("GET", "HEAD", "POST", "DELETE"):
                    try:
                        self.check(sf, method, t, body=b"{}" if method in ("POST", "DELETE") else b"", label="path trick")
                    except (ConnectionError, OSError):
                        pass                                                    # a refused/closed connection is a refusal
            self.assertEqual(sf.changed(), [], sf.name)

    def test_absolute_form_and_http_versions(self):
        for sf in self.surfaces:
            for target in (f"http://evil.example{sf.routes_get[0]}", f"http://127.0.0.1:{sf.port}{sf.routes_get[0]}", f"http://user:pw@127.0.0.1:{sf.port}{sf.routes_get[0]}",
                           f"//evil.example{sf.routes_get[0]}", f"http://127.0.0.1:{sf.port}@evil.example{sf.routes_get[0]}"):
                for version in ("HTTP/1.0", "HTTP/1.1", "HTTP/0.9", "HTTP/2.0"):
                    for host in (None, "evil.example"):
                        self.check(sf, "GET", target, host=host, version=version, label="absolute-form")
            # HTTP/0.9 style request line without a version
            status, _, body = raw(sf.port, f"GET {sf.routes_get[0]}\r\n\r\n".encode())
            self.assertNotIn(sf.master.encode(), body)
            self.assertFalse(200 <= status < 300 and sf.routes_get[0] not in sf.public)

    def test_every_answer_has_nosniff_and_no_store(self):
        for sf in self.surfaces:
            for target in list(sf.routes_get)[:8] + ["/nope"]:
                _, headers, _ = self.check(sf, "GET", target, label="headers")
                self.assertEqual(headers.get("x-content-type-options"), "nosniff", (sf.name, target))
                self.assertIn("no-store", headers.get("cache-control", ""), (sf.name, target))

    # ---------------------------------------------------------------- Host confusion, with and without a valid token
    def test_host_confusion_is_refused_even_with_the_master_token(self):
        for sf in self.surfaces:
            good = f"127.0.0.1:{sf.port}"
            hosts = [None, "", "evil.example", f"evil.example:{sf.port}", "127.0.0.1", f"127.0.0.1:{sf.port + 1}", f"localhost.:{sf.port}",
                     f"127.0.0.1.:{sf.port}", f"127.0.0.1.nip.io:{sf.port}", f"localhost.evil.example:{sf.port}", f"user@127.0.0.1:{sf.port}",
                     f"127.0.0.1:{sf.port}@evil.example", f"127.0.0.1:{sf.port}:80", f"127.0.0.1%00:{sf.port}", f"127.0.0.1\x00:{sf.port}",
                     f"[::ffff:127.0.0.1]:{sf.port}", f"0x7f.0.0.1:{sf.port}", f"2130706433:{sf.port}",
                     f"127.1:{sf.port}", f"127.0.0.1:0{sf.port}", f"127.0.0.1:{sf.port}.evil.example", [good, "evil.example"], ["evil.example", good]]
            for host in hosts:
                for method, target, body in (("GET", sf.routes_get[0], b""), ("POST", sf.routes_write[0], b"{}")):
                    status, headers, rbody = raw(sf.port, req_bytes(method, target, host, [f"{sf.header}: {sf.master}"], body))
                    self.assertFalse(200 <= status < 400, (sf.name, method, repr(host), status))     # every confusing Host is refused, duplicates included
                    for leak in (sf.master.encode(), CANARY.encode()):
                        self.assertNotIn(leak, rbody, (sf.name, repr(host)))
            self.assertEqual(sf.changed(), [], sf.name)

    def test_the_login_page_is_the_only_html_a_bare_client_gets_and_it_holds_no_secret(self):
        for sf in self.surfaces:
            if not sf.has_pages:
                continue
            for target in sf.page_targets:
                status, headers, body = raw(sf.port, req_bytes("GET", target, f"127.0.0.1:{sf.port}"))
                self.assertEqual(status, 401, (sf.name, target))
                self.assertIn(b"/auth/login.js", body)
                self.assertNotIn(b"jarvis-console-secret", body)
                self.assertNotIn(b"jarvis-widget-secret", body)
                self.assertNotIn("set-cookie", headers)
                self.assertIn("no-store", headers.get("cache-control", ""))
                self.assertIn("frame-ancestors 'none'", headers.get("content-security-policy", ""), (sf.name, target))
                self.assertEqual(headers.get("x-frame-options", "DENY").upper(), "DENY", (sf.name, target))


def start_console(case):
    tc.Server.setUp(case)
    with open(os.path.join(case.out, "canary.md"), "w") as fh:
        fh.write(CANARY)
    from jarvis_kernel.tasks import Tasks
    Tasks(case.k).create("hermes", "private", CANARY, CANARY)
    sf = Surface()
    sf.name, sf.port, sf.master, sf.header = "console", case.port, case.srv.console_secret, "X-Jarvis-Console"
    sf.public = ("/api/health", "/auth/login.js", "/assets/app.js")
    sf.routes_get = ["/api/owner/state", "/api/owner/commands", "/api/owner/tasks", "/api/owner/approvals", "/api/owner/events?since=0&limit=5",
                     "/api/owner/memory", "/api/owner/audit/verify", "/api/config", "/api/files", "/api/files?workspace=1",
                     "/api/files/get?path=task-abc123def456/report.md", "/api/files/get?path=canary.md", "/api/files/workspace?path=x",
                     "/api/voice/status", "/api/voice/check/phrases", "/api/desktop/state", "/api/desktop/screen.jpg", "/api/skills",
                     "/api/local-models", "/api/connectors/local", "/api/connectors/templates", "/api/bench/summary", "/api/bench/runs",
                     "/api/bench/cases", "/api/bench/security", "/api/bench/jobs", "/api/bench/models", "/api/bench/challenges", "/api/stream",
                     "/api/health?full=1", "/api/health/", "/auth/session", "/auth/code"]
    sf.routes_write = ["/api/owner/command", "/api/owner/kill", "/api/owner/stop_all", "/api/owner/approve", "/api/owner/task",
                       "/api/voice/warm", "/api/voice/check/save", "/api/voice/check/score", "/api/voice/transcribe", "/api/voice/speak",
                       "/api/classify", "/api/providers/command", "/api/connectors/local", "/api/connectors/local/remove", "/api/connectors/add", "/api/connectors/remove",
                       "/api/bench/run", "/api/bench/rerun-failed", "/api/bench/jobs/j1/cancel", "/api/bench/challenge", "/api/bench/models/run",
                       "/api/desktop/take", "/api/desktop/continue", "/api/bench/challenge/ch-20261017-000000-abcdef", "/auth/session", "/auth/code"]
    sf.has_pages, sf.page_targets = True, ("/", "/index.html", "/tasks/abc", "/some/screen", "/INDEX.HTML")
    before = case.k.db.execute("SELECT COUNT(*) FROM owner_commands").fetchone()[0]
    sf.changed = lambda: ([] if (case.k.db.execute("SELECT COUNT(*) FROM owner_commands").fetchone()[0] == before and not case.k.killed()
                                  and not case.voice.calls and len(case.srv.gate._sessions) == 0 and not case.srv.gate._codes) else ["changed"])
    return sf


def start_speech(case):
    spec = importlib.util.spec_from_file_location("lss_matrix", os.path.join(HERE, "..", "..", "scripts", "local_speech_server.py"))
    lss = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(lss)
    case.lss = lss
    handler = lss.make_handler(lambda text, lang: (_ for _ in ()).throw(RuntimeError("never called")), None)
    case.ssrv = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=case.ssrv.serve_forever, daemon=True).start()
    epoch = lss._SYNTH["epoch"]
    sf = Surface()
    sf.name, sf.port, sf.master, sf.header = "speech", case.ssrv.server_address[1], handler.control_secret, "X-Jarvis-Control"
    sf.public = ("/health", "/", "/v1/models", "/models", "/activity")
    sf.routes_get = ["/interrupt", "/health", "/activity", "/v1/models"]
    sf.routes_write = ["/interrupt", "/interrupt/", "/v1/audio/speech", "/v1/audio/transcriptions"]
    sf.has_pages, sf.page_targets = False, ()
    sf.changed = lambda: ([] if lss._SYNTH["epoch"] == epoch else ["interrupted"])
    return sf


class AllServers(MatrixMixin, unittest.TestCase):
    def setUp(self):
        self.tmp_holder = None
        c = _Case()
        self.c = c
        sfs = [start_console(c), start_speech(c)]
        self.surfaces = sfs

    def tearDown(self):
        for fn in (lambda: self.c.srv.shutdown(), lambda: self.c.ssrv.shutdown(),
                   lambda: self.c.ksrv.shutdown(), lambda: self.c.k.db.close(), lambda: self.c.tmp.cleanup()):
            try:
                fn()
            except Exception:
                pass


class _Case:
    """A stand-in `self` for tc.Server.setUp (it only sets attributes)."""

    def __getattr__(self, name):
        raise AttributeError(name)


class RouteInventory(unittest.TestCase):
    """The matrix lists routes by hand; this pins the list to the code so a new route cannot slip in unlisted."""

    def test_every_console_api_route_in_the_code_is_in_the_matrix(self):
        src = open(os.path.join(HERE, "..", "jarvis_console", "server.py"), encoding="utf-8").read()
        api = src[src.index("        def _api(self, method, name, q):"):src.index("        def _connectors(self")]
        prefixes = set(re.findall(r'name\.startswith\("([^"]+)"\)', api)) | set(re.findall(r'name == "([^"]+)"', api))
        body = open(__file__, encoding="utf-8").read()
        text = body[body.index("def start_console"):body.index("def start_speech")]
        self.assertGreaterEqual(len(prefixes), 10)
        for p in prefixes:
            self.assertIn("/api/" + p.rstrip("/"), text, f"console route {p!r} is not in the matrix")

    def test_every_route_of_the_speech_server_in_the_code_is_in_the_matrix(self):
        base = os.path.join(HERE, "..", "..")
        for rel, start in (("scripts/local_speech_server.py", "def start_speech"),):
            src = open(os.path.join(base, rel), encoding="utf-8").read()
            routes = set(re.findall(r'(?:path|route|self\.path) (?:==|in) \(?"(/[a-z/_.-]+)"', src)) | set(re.findall(r'"(/(?:api|auth)/[a-z_/.-]+)"', src))
            body = open(__file__, encoding="utf-8").read()
            end = min(i for i in (body.find("\ndef ", body.index(start) + 5), body.find("\nclass ", body.index(start) + 5)) if i > 0)
            text = body[body.index(start):end]
            for r in routes:
                if r.endswith(".html") or r in ("/index.html",):
                    continue
                self.assertIn(r, text, f"{rel}: route {r!r} is not in the matrix")


if __name__ == "__main__":
    unittest.main()
