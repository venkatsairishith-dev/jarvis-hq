"""The console's HTTP server: static UI, kernel owner-plane proxy, voice, files, desktop control proxy.

Binds 127.0.0.1 only. Every request must carry an exact loopback Host (name AND port: DNS-rebinding defence). The owner token
stays in this process.

Authentication (audit SEC-D9 2026-10-18; re-review M3 2026-10-19). Version 1 wrote a per-launch secret into every `GET /` and
required it only on POST/DELETE; but any loopback client could `GET /` and read it, and every GET route (owner state, files,
memory, audit, desktop screen) needed nothing. Now (see scripts/control_auth.py for the full design and its limits):
  * public-harmless: `GET /api/health` WITHOUT credentials (liveness only: no kernel, voice or disk detail), static JS/CSS,
    the login page, `GET /auth/login.js`.
  * sensitive-read: every other GET under /api (owner state/tasks/approvals/audit/events/memory, files, desktop state and screen,
    config, voice, skills, models, bench, the event stream) answers 401 without a credential and sends no data.
  * state-changing: every POST/DELETE needs a credential and, for a browser session, the per-session double-submit token.
  * Credentials: the MASTER token (first-party programs send it in `X-Jarvis-Console`; it is published to the 0600 file
    `console.token` in the per-user run folder by `main()`; `srv.console_secret` in tests), or a BROWSER SESSION (HttpOnly,
    SameSite=Strict cookie bound to the exact Host:port) obtained by exchanging a ONE-TIME launch code (`POST /auth/code` with the
    master token, then `POST /auth/session` with the code from the URL fragment). A bare `GET /` without a session returns a login
    page that contains no secret; the signed-in page carries only the session's double-submit token in its <meta>.
WHAT THIS DOES NOT STOP, plainly: a process of the same OS user that can read the 0600 token file or the browser profile does what
the first-party client does; a web server on another loopback port that the owner browses to receives the cookie (cookies are not
port-scoped) but cannot read responses, pass the double-submit token or use the master token. It blocks other OS users (file
permissions), other sites (SameSite, no CORS, Origin check), DNS rebinding and programs that only know the port. The real
boundary for anything that matters stays the owner's PIN: approving needs the PIN, which no page and no process learns from
here, and the kernel's policy, lanes and kill switch are unchanged.
"""
import hmac
import json
import mimetypes
import os
import re
import secrets
import shutil
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import sys as _sys
_sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "scripts"))
import loopback_net                     # noqa: E402  loopback only, no proxy, no redirect (completion H)
import control_auth                     # noqa: E402  sessions, one-time codes and the master token (jarvis/scripts/control_auth.py)
from . import bench as benchmod
from . import files as filesmod
from .config import Config
from . import intent, scoring
from .kernel_client import Kernel, KernelError
from .speech_text import speakable
from .voice import Voice, VoiceError

MAX_BODY = 20 * 1024 * 1024
JSON_BODY = 256 * 1024
REFUSE_DRAIN = 1 << 20      # the most of a refused request's body that is read so the refusal can be delivered
def UI_PAGE_HEADERS(hub):
    return {"Content-Security-Policy": UI_CSP.format(hub=hub), "Referrer-Policy": "no-referrer", "X-Frame-Options": "DENY"}


UI_CSP = ("default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; media-src 'self' blob:; "
          "connect-src 'self'; frame-src 'self' {hub}; font-src 'self' data:; object-src 'none'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'")


class Server(ThreadingHTTPServer):
    daemon_threads = True
    # On Windows SO_REUSEADDR lets a second console bind the same port and both keep running; starting the console twice
    # then leaves duplicates that each hold models in memory. Exclusive use makes the second one fail at once instead.
    allow_reuse_address = os.name != "nt"

    def server_bind(self):
        if os.name == "nt":
            import socket
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()

    def handle_error(self, request, client_address):
        """A browser that navigates away aborts its own polls and streams: that is normal, not a server fault."""
        import sys
        if isinstance(sys.exc_info()[1], (ConnectionError, BrokenPipeError, TimeoutError)):
            return
        super().handle_error(request, client_address)


def make_server(cfg: Config = None, kernel=None, voice=None, bench=None):
    cfg = cfg or Config()
    gate = control_auth.Gate("console", "X-Jarvis-Console", "jarvis_console_session")
    console_secret = gate.master                      # this launch's MASTER token: never put in a page; for first-party programs and tests
    bench = bench or benchmod.Bench(cfg)
    kernel = kernel or Kernel(cfg)
    voice = voice or Voice(cfg.voice_dir, cfg.piper_python, cfg.voice_threads, cfg.voice_idle_unload_s)
    started = time.time()
    hub = cfg.hub_url                                    # what the BROWSER uses (Edge resolves *.localhost to loopback)
    _hu = urllib.parse.urlsplit(hub)
    hub_local = f"{_hu.scheme}://127.0.0.1:{_hu.port or 80}"   # what THIS process uses: Python's resolver does not know *.localhost,
    hub_host = _hu.netloc                                      # so connect to loopback and name the site in the Host header

    OPTIONAL_PROBE_TIMEOUT = 0.5      # a local model server that is up answers in milliseconds; do not wait 2 s for one that is not

    def probe(url, headers=None, timeout=2.0):
        t0 = time.time()
        try:
            req = urllib.request.Request(url, headers=headers or {})
            with loopback_net.urlopen(req, timeout=timeout) as r:
                body = r.read(2000)
                return {"ok": 200 <= r.status < 300, "status": r.status, "ms": round((time.time() - t0) * 1000), "body": body}
        except urllib.error.HTTPError as e:
            return {"ok": False, "status": e.code, "ms": round((time.time() - t0) * 1000), "body": b""}
        except (urllib.error.URLError, OSError, TimeoutError) as e:
            return {"ok": False, "status": 0, "ms": round((time.time() - t0) * 1000), "body": b"", "error": type(e).__name__}

    def with_secret(html: bytes, token: str) -> bytes:
        """Put the SESSION's double-submit token (never the master token) into an HTML page as a <meta> tag (before </head>, else
        before <body>, else at the end). Only a signed-in request gets here."""
        tag = ('<meta name="jarvis-console-secret" content="%s">' % token).encode()
        for pat in (rb"</head\s*>", rb"<body[\s>]"):
            m = re.search(pat, html, re.I)
            if m:
                return html[:m.start()] + tag + html[m.start():]
        return html + tag

    class H(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.0"    # one request per connection: refusals never leave an unread body behind
        server_version = "JarvisConsole"

        def log_message(self, *a):
            pass

        # ---- helpers
        def _allowed_host(self):
            """Only the console's own loopback names: a page on another site cannot reach it by DNS rebinding."""
            host = (control_auth.host_value(self.headers) or "").lower()
            name, _, port = host.rpartition(":") if ":" in host and not host.endswith("]") else (host, "", "")
            return name in ("127.0.0.1", "localhost") and port == str(self.server.server_address[1])

        def _origin_ok(self):
            """A POST or DELETE is accepted only with a credential: the master token in X-Jarvis-Console, or a signed-in session
            (cookie bound to this Host) PLUS its double-submit token in X-Jarvis-Console; and, when the client sends an Origin,
            that Origin is the console's own. The credential is required whatever the Origin says (any local program can write
            an Origin header)."""
            return gate.write_ok(self.headers, self.server.server_address[1])

        def _read_ok(self):
            """May this request read private state? The master token or a signed-in session, on the exact Host, no foreign Origin."""
            return gate.read_ok(self.headers, self.server.server_address[1])

        def _send(self, code, body=b"", ctype="application/json", extra=None):
            if isinstance(body, (dict, list)):
                body = json.dumps(body).encode()
            elif isinstance(body, str):
                body = body.encode()
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Cache-Control", "no-store")
            for k, v in (extra or {}).items():
                self.send_header(k, v)
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)

        def _json(self, code, obj):
            self._send(code, obj)

        def _refuse(self, code, obj):
            """Answer a request that is being turned away. Its body is read first (a bounded amount): closing a socket that
            still holds unread data resets the connection on Windows, and the client then sees an abort, not the reason."""
            try:
                n = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                n = 0
            if 0 < n <= REFUSE_DRAIN:
                try:
                    self.rfile.read(n)
                except OSError:
                    pass
            self._send(code, obj)

        def _body(self, limit=JSON_BODY):
            self._body_taken = True
            try:
                n = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                n = -1
            if n < 0 or n > limit:
                raise VoiceError("request body is too large", 413)
            return self.rfile.read(n) if n else b""

        def _jbody(self):
            raw = self._body()
            if not raw:
                return {}
            try:
                d = json.loads(raw)
            except ValueError:
                raise VoiceError("body is not JSON")
            if not isinstance(d, dict):
                raise VoiceError("body must be a JSON object")
            return d

        # ---- routing
        def do_GET(self):
            self._route("GET")

        def do_HEAD(self):
            self._route("GET")

        def do_POST(self):
            self._route("POST")

        def do_DELETE(self):
            self._route("DELETE")

        def _route(self, method):
            self._body_taken = False
            try:
                self._route_inner(method)
            finally:
                if method == "POST" and not self._body_taken:        # whatever turned the request away left its body unread
                    try:
                        n = int(self.headers.get("Content-Length", "0"))
                    except ValueError:
                        n = 0
                    if 0 < n <= REFUSE_DRAIN:
                        try:
                            self.rfile.read(n)
                        except OSError:
                            pass

        def _route_inner(self, method):
            if not self._allowed_host():
                return self._refuse(403, {"error": "this console answers only on its own loopback address"})
            u = urllib.parse.urlsplit(self.path)
            path, q = u.path, dict(urllib.parse.parse_qsl(u.query))
            try:
                if path.startswith("/auth/"):                   # sign-in: public by design, each route checks what it needs
                    return self._auth(method, path)
                if method in ("POST", "DELETE") and not self._origin_ok():
                    # `reload` tells the page its token is stale (the console restarted since it loaded): it re-reads it and retries
                    return self._refuse(403, {"error": "request refused: it did not come from this console's own signed-in page "
                                                       "(missing or old console credential; reload the page)", "reload": True})
                if path.startswith("/api/"):
                    if method == "GET" and not self._read_ok():
                        if path == "/api/health":                # liveness only: no kernel, voice, disk or path detail
                            return self._json(200, {"console": {"ok": True}, "time": time.time(), "minimal": True, "liveness_only": True})
                        return self._json(401, {"error": "not signed in: open the console from JARVIS (a one-time link) or send the "
                                                         "console token", "login": True})
                    return self._api(method, path[5:], q)
                if method != "GET":                              # the page is only ever fetched; a POST here is a mistake or a probe
                    return self._refuse(405, {"error": "method not allowed"})
                return self._static(path)
            except VoiceError as e:
                return self._json(e.code, {"error": str(e)})
            except KernelError as e:
                return self._json(e.code if e.code in (401, 403, 502, 503) else 502, {"error": str(e), "kernel_unreachable": e.code in (502, 503)})
            except (BrokenPipeError, ConnectionResetError):
                return
            except Exception as e:                               # never a traceback to the browser
                return self._json(500, {"error": f"console error: {type(e).__name__}"})

        # ---- sign-in
        def _auth(self, method, path):
            """GET /auth/login.js (public) and POST /auth/code (master token) / POST /auth/session (one-time code): a session cookie."""
            if method == "GET" and path == "/auth/login.js":
                return self._send(200, control_auth.LOGIN_JS, "application/javascript; charset=utf-8")
            body = b""
            if method == "POST":
                try:
                    body = self._body(4096)
                except VoiceError:
                    return self._refuse(413, {"error": "request body is too large"})
            res = control_auth.Gate.auth_route(gate, method, path, self.headers, body, self.server.server_address[1])
            if res is None:
                return self._json(404, {"error": "not found"})
            code, extra, reply = res
            return self._send(code, reply, "application/json", extra)

        # ---- static UI
        def _static(self, path):
            root = os.path.realpath(cfg.ui_dir)
            token = gate.page_token(self.headers)                 # the session's double-submit token, or None when not signed in
            signed_in = token is not None or gate.master_ok(self.headers)
            if not os.path.isdir(root):
                if not signed_in:
                    return self._send(401, control_auth.login_page("JARVIS console"), "text/html; charset=utf-8", UI_PAGE_HEADERS(hub))
                return self._send(200, with_secret("<!doctype html><meta charset=utf-8><title>JARVIS</title><body style='font:16px system-ui;padding:2rem'>"
                                                "<h1>JARVIS console is running</h1><p>The UI has not been built yet: run <code>npm run build</code> in "
                                                "<code>hq/ui</code>.</p>".encode(), token or ""), "text/html; charset=utf-8")
            rel = urllib.parse.unquote(path).lstrip("/") or "index.html"
            full = os.path.realpath(os.path.join(root, rel))
            if os.path.commonpath([root, full]) != root or not os.path.isfile(full):
                full = os.path.join(root, "index.html")           # single-page app: unknown paths are screens
            ctype = mimetypes.guess_type(full)[0] or "application/octet-stream"
            if ctype.startswith("text/") or ctype in ("application/javascript", "application/json"):
                ctype += "; charset=utf-8"
            if ctype.startswith("text/html") and not signed_in:
                # the app shell is a screen of private data: without a session, a login page that holds no secret
                return self._send(401, control_auth.login_page("JARVIS console"), "text/html; charset=utf-8", UI_PAGE_HEADERS(hub))
            with open(full, "rb") as fh:
                data = fh.read()
            if ctype.startswith("text/html"):
                data = with_secret(data, token or "")           # a signed-in page learns only its own double-submit token
            extra = {"Content-Security-Policy": UI_CSP.format(hub=hub), "Permissions-Policy": "microphone=(self), camera=(), geolocation=()",
                     "Referrer-Policy": "no-referrer", "X-Frame-Options": "DENY"}
            if "/assets/" in path:
                extra["Cache-Control"] = "public, max-age=31536000, immutable"
            self._send(200, data, ctype, extra)

        # ---- API
        def _api(self, method, name, q):
            if method == "DELETE" and not name.startswith("bench/"):       # only a saved challenge is ever deleted here
                return self._json(405, {"error": "method not allowed"})
            if name == "health":
                if q.get("minimal") == "1":                          # a fast liveness probe even for an authenticated caller
                    return self._json(200, {"console": {"ok": True}, "time": time.time(), "minimal": True, "liveness_only": True})
                return self._json(200, health())
            if name == "config":
                return self._json(200, cfg.public())
            if name.startswith("owner/"):
                return self._owner(method, name[6:], q)
            if name == "stream":
                return self._stream(q)
            if name.startswith("voice/"):
                return self._voice(method, name[6:], q)
            if name.startswith("files"):
                return self._files(name[5:].strip("/"), q)
            if name.startswith("desktop/"):
                return self._desktop(method, name[8:])
            if name == "classify" and method == "POST":
                text = str(self._jbody().get("text", ""))
                return self._json(200, intent.classify(text, cfg.intent_llm_url, cfg.intent_model))
            if name.startswith("connectors/"):
                return self._connectors(method, name[11:])
            if name == "skills":
                return self._json(200, skills())
            if name == "local-models":
                return self._json(200, local_models())
            if name.startswith("bench/"):
                return benchmod.route(self, bench, method, name[6:], q)
            return self._json(404, {"error": "not found"})

        def _connectors(self, method, name):
            """The laptop's local connector allowlist (the one place a command line is accepted) and the Add and Remove
            workflows that keep it and the kernel's registry together. Everything else about connectors goes through
            /api/owner/connectors/* to the kernel."""
            from . import connectorcfg as cc
            path = getattr(cfg, "connectors_file", None) or cc.default_path()
            secrets = getattr(cfg, "secrets_dir", None) or cc.secrets_dir()
            if method == "GET" and name == "local":
                return self._json(200, {"local": cc.describe(path, secrets), "file": os.path.basename(path)})
            if method == "GET" and name == "templates":
                with open(os.path.join(os.path.dirname(__file__), "connector_templates.json"), encoding="utf-8") as fh:
                    return self._json(200, json.load(fh))
            if method != "POST":
                return self._json(404, {"error": "not found"})
            d = self._jbody()
            try:
                if name == "local":
                    return self._json(200, {"saved": cc.save(str(d.get("name", "")), d.get("spec"), path, secrets)})
                if name == "local/remove":
                    return self._json(200, {"removed": cc.remove(str(d.get("name", "")), path)})
                if name == "add":
                    definition = d.get("definition") if isinstance(d.get("definition"), dict) else {}
                    cname = str(definition.get("name", ""))
                    previous = cc.load(path).get(cname)
                    if definition.get("transport") == "stdio":
                        if not isinstance(d.get("local"), dict):
                            return self._json(400, {"error": "a local program is needed for a stdio connector"})
                        clean = cc.save(cname, d["local"], path, secrets)
                        definition = dict(definition, env_refs=clean["env_refs"], timeout_s=clean["timeout_s"])
                    code, reply = kernel.request("POST", "connectors", {"definition": definition})
                    if code >= 400 and definition.get("transport") == "stdio":
                        if previous is None:
                            cc.remove(cname, path)              # the kernel refused it: leave no half-added program behind
                        else:
                            cc.save(cname, previous, path, secrets)
                    return self._json(code, reply)
                if name == "remove":
                    cname = str(d.get("name", ""))
                    code, reply = kernel.request("POST", "connectors/delete", {"name": cname, "confirm": str(d.get("confirm", ""))})
                    if code < 400:
                        cc.remove(cname, path)
                    return self._json(code, reply)
            except cc.ConfigError as e:
                return self._json(400, {"error": str(e)})
            return self._json(404, {"error": "not found"})

        def _owner(self, method, name, q):
            body = self._jbody() if method == "POST" else None
            qs = ("?" + urllib.parse.urlencode(q)) if q else ""
            code, reply = kernel.request(method, name + qs, body)
            return self._json(code, reply)

        def _stream(self, q):
            """Server-sent events from the kernel, passed through line by line; ends when the kernel closes it."""
            try:
                since = int(self.headers.get("Last-Event-ID") or q.get("since") or 0)
            except ValueError:
                since = 0
            up = kernel.stream(since)
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream"); self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff"); self.send_header("Connection", "close")
            self.end_headers()
            try:
                while True:
                    line = up.readline()
                    if not line:
                        break
                    self.wfile.write(line)
                    if line == b"\n":
                        self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError, OSError, TimeoutError):
                pass
            finally:
                up.close()
            self.close_connection = True

        def _voice(self, method, name, q):
            if name == "status":
                return self._json(200, voice.status())
            if name == "check/phrases":
                return self._json(200, {"phrases": voice_phrases(), "results": voice_check_history()})
            if method != "POST":
                return self._json(405, {"error": "use POST"})
            lang = q.get("lang", "en")
            if name == "warm":
                d = self._jbody()
                lang_ = str(d.get("lang") or lang)
                threading.Thread(target=lambda: _quiet(voice.warm, lang_), daemon=True).start()
                return self._json(202, {"warming": lang_})
            if name == "check/save":
                return self._json(200, voice_check_save(self._jbody()))
            if name == "check/score":
                d = self._jbody()
                return self._json(200, scoring.score(str(d.get("expected", "")), str(d.get("heard", ""))))
            if name == "transcribe":
                return self._json(200, voice.transcribe(self._body(MAX_BODY), lang))
            if name == "speak":
                d = self._jbody()
                text = speakable(str(d.get("text", ""))) if d.get("summarize", True) else str(d.get("text", ""))
                wav, meta = voice.speak(text, str(d.get("lang") or lang), float(d.get("speed") or 1.0))
                return self._send(200, wav, "audio/wav", {"X-Voice-Model": meta["model"], "X-Voice-Ms": str(meta["ms"]),
                                                           "X-Spoken-Text": urllib.parse.quote(text[:300])})
            return self._json(404, {"error": "not found"})

        def _files(self, name, q):
            if name == "":
                ws = filesmod.list_workspace() if q.get("workspace") == "1" else None
                return self._json(200, {"delivered": filesmod.list_delivered(cfg.outputs_dir), "root": cfg.outputs_dir, "workspace": ws})
            area = "workspace" if name == "workspace" else "delivered" if name == "get" else None
            if area is None:
                return self._json(404, {"error": "not found"})
            rel = q.get("path", "")
            try:
                data, fname = (filesmod.read_workspace(rel) if q.get("area") == "workspace" else filesmod.read_delivered(cfg.outputs_dir, rel))
            except FileNotFoundError:
                return self._json(404, {"error": "no such file"})
            except OverflowError as e:
                return self._json(413, {"error": str(e)})
            except ValueError:
                return self._json(400, {"error": "bad path"})
            ctype, hdr = filesmod.preview_headers(fname, q.get("source") == "1")
            if q.get("download") == "1":
                ctype = "application/octet-stream"
                hdr["Content-Disposition"] = "attachment; filename*=UTF-8''" + urllib.parse.quote(fname)
            return self._send(200, data, ctype, hdr)

        def _desktop(self, method, name):
            """The Agent PC (Linux, in the WSL box) through the hub's control socket: take control, continue, screen."""
            if name not in ("state", "take", "continue", "screen.jpg"):
                return self._json(404, {"error": "not found"})
            url = f"{hub_local}/control/{name}"
            hdr = {"X-Jarvis-Hub": "1", "Host": hub_host}
            try:
                if method == "POST":
                    req = urllib.request.Request(url, data=b"{}", method="POST", headers=dict(hdr, **{"Content-Type": "application/json"}))
                else:
                    req = urllib.request.Request(url, headers=hdr)
                with loopback_net.urlopen(req, timeout=8) as r:
                    data = r.read(8 * 1024 * 1024)
                    return self._send(r.status, data, r.headers.get("Content-Type", "application/octet-stream"))
            except urllib.error.HTTPError as e:
                return self._json(e.code, {"error": f"the Agent PC control answered HTTP {e.code}"})
            except (urllib.error.URLError, OSError, TimeoutError):
                return self._json(503, {"error": "the Agent PC control is not answering (is the lab box running?)", "unavailable": True})

    def health():
        out = {"console": {"ok": True, "uptime_s": round(time.time() - started), "port": cfg.port}, "time": time.time()}
        try:
            code, st = kernel.request("GET", "state", timeout=6)
            out["kernel"] = {"reachable": True, "authorized": code == 200, "status": code, "ms": getattr(kernel, "last_ms", None),
                             "killed": bool(st.get("killed")) if code == 200 else None, "pin_set": st.get("pin_set") if code == 200 else None,
                             "voice_command_ready": st.get("voice_command_ready") if code == 200 else None,
                             "error": None if code == 200 else (st.get("error") or f"HTTP {code}")}
        except KernelError as e:
            out["kernel"] = {"reachable": False, "authorized": False, "error": str(e)}
        out["voice"] = voice.status()
        p = probe(cfg.local_llm_url + "/health", timeout=OPTIONAL_PROBE_TIMEOUT)    # optional; a closed port takes ~2 s to refuse on this OS
        out["local_llm"] = {"ok": p["ok"], "ms": p["ms"], "url": cfg.local_llm_url, "error": p.get("error")}
        p = probe(hub_local + "/control/state", {"X-Jarvis-Hub": "1", "Host": hub_host})
        out["hub"] = {"ok": p["ok"], "ms": p["ms"], "url": hub, "error": p.get("error")}
        try:
            du = shutil.disk_usage(os.path.splitdrive(cfg.outputs_dir)[0] + "\\" if os.name == "nt" else "/")
            out["disk"] = {"free_gb": round(du.free / 1e9, 1), "total_gb": round(du.total / 1e9, 1)}
        except OSError:
            out["disk"] = None
        # honesty (completion H): `console.ok` only says this process answers. `ready` is true only when what the console needs works: the
        # kernel answers and accepts its token, the kill switch is off, and every voice model is present. The Agent PC (hub) and the
        # optional local model are listed under `optional_down`: they do not make the console unready but they are never reported as fine.
        k, problems = out["kernel"], []
        if not k.get("reachable"):
            problems.append("kernel_unreachable")
        elif not k.get("authorized"):
            problems.append("kernel_token_refused")
        elif k.get("killed"):
            problems.append("kill_switch_on")
        vs = out["voice"] if isinstance(out["voice"], dict) else {}
        if not vs.get("engine"):
            problems.append("voice_engine_missing")
        for part in ("stt", "tts"):
            problems += [f"voice_{part}_{lang}_missing" for lang, v in sorted((vs.get(part) or {}).items()) if not (v or {}).get("available")]
        out["problems"] = problems
        out["ready"] = not problems
        out["status"] = "ok" if not problems else "degraded"
        out["optional_down"] = [n for n in ("local_llm", "hub") if not (out.get(n) or {}).get("ok")]
        return out

    def _quiet(fn, *args):
        try:
            fn(*args)
        except Exception:
            pass

    def voice_phrases():
        path = os.path.join(os.path.dirname(__file__), "voice_phrases.json")
        try:
            with open(path, encoding="utf-8") as fh:
                d = json.load(fh)
        except (OSError, ValueError):
            return {}
        return {k: [str(x)[:200] for x in v][:20] for k, v in d.items() if k in ("en", "hi", "te") and isinstance(v, list)}

    VC_DIR = os.path.join(os.path.dirname(os.path.abspath(cfg.outputs_dir)), "cache", "voice-checks")

    def voice_check_history(limit=10):
        try:
            names = sorted(os.listdir(VC_DIR), reverse=True)[:limit]
        except OSError:
            return []
        out = []
        for n in names:
            try:
                with open(os.path.join(VC_DIR, n), encoding="utf-8") as fh:
                    d = json.load(fh)
                out.append({"file": n, "at": d.get("at"), "device": d.get("device"), "summary": d.get("summary"), "n": len(d.get("rows", []))})
            except (OSError, ValueError):
                continue
        return out

    def voice_check_save(d):
        """Keep one voice-check session on this computer (the cache folder beside the outputs folder): rows of expected text, what was
        heard, the scores and latencies, and the device label the browser reported. Nothing is sent anywhere, and the
        audio itself is not stored."""
        rows = []
        for r in (d.get("rows") or [])[:60]:
            if not isinstance(r, dict) or r.get("lang") not in ("en", "hi", "te"):
                continue
            sc = scoring.score(str(r.get("expected", ""))[:300], str(r.get("heard", ""))[:300])
            rows.append({"lang": r["lang"], "expected": str(r.get("expected", ""))[:300], "heard": str(r.get("heard", ""))[:300],
                         "wer": sc["wer"], "cer": sc["cer"], "exact": sc["exact"], "ms": r.get("ms") if isinstance(r.get("ms"), (int, float)) else None,
                         "speech": bool(r.get("speech", True)), "model": str(r.get("model", ""))[:60]})
        if not rows:
            raise VoiceError("no rows to save")
        rec = {"at": time.time(), "device": str(d.get("device", ""))[:120], "rows": rows, "summary": scoring.summarize(rows)}
        os.makedirs(VC_DIR, exist_ok=True)
        fn = time.strftime("check-%Y%m%d-%H%M%S.json")
        with open(os.path.join(VC_DIR, fn), "w", encoding="utf-8") as fh:
            json.dump(rec, fh, ensure_ascii=False, indent=1)
        return {"saved": fn, "summary": rec["summary"]}

    def skills():
        """Active and staged skills with their recorded risk, from the repo (read-only)."""
        root = cfg.skills_dir
        active, staged = {}, []
        try:
            with open(os.path.join(root, "active.json"), encoding="utf-8") as fh:
                active = json.load(fh)
        except (OSError, ValueError):
            pass
        sd = os.path.join(root, "staging")
        if os.path.isdir(sd):
            for n in sorted(os.listdir(sd)):
                prov = os.path.join(sd, n, "PROVENANCE.json")
                try:
                    with open(prov, encoding="utf-8") as fh:
                        pv = json.load(fh)
                except (OSError, ValueError):
                    pv = {}
                staged.append({"name": n, "risk": pv.get("risk") or (pv.get("review") or {}).get("risk"),
                               "source": pv.get("source") or pv.get("upstream"), "license": pv.get("license"),
                               "rejected": "content" not in pv and not os.path.exists(os.path.join(sd, n, "SKILL.md"))})
        return {"active": active, "staged": staged}

    def local_models():
        p = probe(cfg.local_llm_url + "/v1/models", timeout=OPTIONAL_PROBE_TIMEOUT)
        models = []
        if p["ok"]:
            try:
                models = [m.get("id") for m in json.loads(p["body"].decode() or "{}").get("data", [])]
            except ValueError:
                pass
        return {"llm": {"url": cfg.local_llm_url, "up": p["ok"], "ms": p["ms"], "models": models},
                "voice": voice.status()}

    srv = Server((cfg.host, cfg.port), H)
    srv.voice, srv.kernel, srv.cfg, srv.bench = voice, kernel, cfg, bench
    srv.console_secret = console_secret              # the MASTER token, for tests and the throwaway e2e servers; never logged or served
    srv.gate = gate

    def janitor():
        while True:
            time.sleep(60)
            try:
                voice.unload_if_idle()
            except Exception:
                pass
    threading.Thread(target=janitor, daemon=True).start()
    return srv


def main():
    cfg = Config()
    srv = make_server(cfg)
    try:
        control_auth.publish("console", srv.console_secret)      # 0600 file for first-party programs; sessions come from one-time codes
    except OSError as e:
        print(f"warning: could not publish the console token ({type(e).__name__}); first-party programs cannot use the console", flush=True)
    print(f"JARVIS console on http://{cfg.host}:{cfg.port}/  (kernel {cfg.kernel_url})", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        control_auth.unpublish("console")
