"""The console's HTTP server: static UI, kernel owner-plane proxy, voice, files, desktop control proxy.

Binds 127.0.0.1 only. Every request must carry a loopback Host header (DNS-rebinding defence); every POST must come from the
console's own origin (cross-site defence). The owner token stays in this process.
"""
import json
import mimetypes
import os
import shutil
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import files as filesmod
from .config import Config
from . import intent
from .kernel_client import Kernel, KernelError
from .speech_text import speakable
from .voice import Voice, VoiceError

MAX_BODY = 20 * 1024 * 1024
JSON_BODY = 256 * 1024
UI_CSP = ("default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; media-src 'self' blob:; "
          "connect-src 'self'; frame-src 'self' {hub}; font-src 'self' data:; object-src 'none'; base-uri 'none'; form-action 'self'")


class Server(ThreadingHTTPServer):
    daemon_threads = True

    def handle_error(self, request, client_address):
        """A browser that navigates away aborts its own polls and streams: that is normal, not a server fault."""
        import sys
        if isinstance(sys.exc_info()[1], (ConnectionError, BrokenPipeError, TimeoutError)):
            return
        super().handle_error(request, client_address)


def make_server(cfg: Config = None, kernel=None, voice=None):
    cfg = cfg or Config()
    kernel = kernel or Kernel(cfg)
    voice = voice or Voice(cfg.voice_dir, cfg.piper_python, cfg.voice_threads, cfg.voice_idle_unload_s)
    started = time.time()
    hub = cfg.hub_url                                    # what the BROWSER uses (Edge resolves *.localhost to loopback)
    _hu = urllib.parse.urlsplit(hub)
    hub_local = f"{_hu.scheme}://127.0.0.1:{_hu.port or 80}"   # what THIS process uses: Python's resolver does not know *.localhost,
    hub_host = _hu.netloc                                      # so connect to loopback and name the site in the Host header

    def probe(url, headers=None, timeout=2.0):
        t0 = time.time()
        try:
            req = urllib.request.Request(url, headers=headers or {})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                body = r.read(2000)
                return {"ok": 200 <= r.status < 300, "status": r.status, "ms": round((time.time() - t0) * 1000), "body": body}
        except urllib.error.HTTPError as e:
            return {"ok": False, "status": e.code, "ms": round((time.time() - t0) * 1000), "body": b""}
        except (urllib.error.URLError, OSError, TimeoutError) as e:
            return {"ok": False, "status": 0, "ms": round((time.time() - t0) * 1000), "body": b"", "error": type(e).__name__}

    class H(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.0"    # one request per connection: refusals never leave an unread body behind
        server_version = "JarvisConsole"

        def log_message(self, *a):
            pass

        # ---- helpers
        def _allowed_host(self):
            """Only the console's own loopback names: a page on another site cannot reach it by DNS rebinding."""
            host = (self.headers.get("Host") or "").lower()
            name, _, port = host.rpartition(":") if ":" in host and not host.endswith("]") else (host, "", "")
            return name in ("127.0.0.1", "localhost") and port == str(self.server.server_address[1])

        def _origin_ok(self):
            o = self.headers.get("Origin")
            if o is None:
                return self.headers.get("X-Jarvis-Console") == "1"
            try:
                u = urllib.parse.urlsplit(o)
                return u.hostname in ("127.0.0.1", "localhost") and u.port == self.server.server_address[1]
            except ValueError:
                return False

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

        def _body(self, limit=JSON_BODY):
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

        def _route(self, method):
            if not self._allowed_host():
                return self._json(403, {"error": "this console answers only on its own loopback address"})
            u = urllib.parse.urlsplit(self.path)
            path, q = u.path, dict(urllib.parse.parse_qsl(u.query))
            try:
                if method == "POST" and not self._origin_ok():
                    return self._json(403, {"error": "cross-site request refused"})
                if path.startswith("/api/"):
                    return self._api(method, path[5:], q)
                return self._static(path)
            except VoiceError as e:
                return self._json(e.code, {"error": str(e)})
            except KernelError as e:
                return self._json(e.code if e.code in (401, 403, 502, 503) else 502, {"error": str(e), "kernel_unreachable": e.code in (502, 503)})
            except (BrokenPipeError, ConnectionResetError):
                return
            except Exception as e:                               # never a traceback to the browser
                return self._json(500, {"error": f"console error: {type(e).__name__}"})

        # ---- static UI
        def _static(self, path):
            root = os.path.realpath(cfg.ui_dir)
            if not os.path.isdir(root):
                return self._send(200, "<!doctype html><meta charset=utf-8><title>JARVIS</title><body style='font:16px system-ui;padding:2rem'>"
                                       "<h1>JARVIS console is running</h1><p>The UI has not been built yet: run <code>npm run build</code> in "
                                       "<code>hq/ui</code>.</p>", "text/html; charset=utf-8")
            rel = urllib.parse.unquote(path).lstrip("/") or "index.html"
            full = os.path.realpath(os.path.join(root, rel))
            if os.path.commonpath([root, full]) != root or not os.path.isfile(full):
                full = os.path.join(root, "index.html")           # single-page app: unknown paths are screens
            ctype = mimetypes.guess_type(full)[0] or "application/octet-stream"
            if ctype.startswith("text/") or ctype in ("application/javascript", "application/json"):
                ctype += "; charset=utf-8"
            with open(full, "rb") as fh:
                data = fh.read()
            extra = {"Content-Security-Policy": UI_CSP.format(hub=hub), "Permissions-Policy": "microphone=(self), camera=(), geolocation=()",
                     "Referrer-Policy": "no-referrer", "X-Frame-Options": "DENY"}
            if "/assets/" in path:
                extra["Cache-Control"] = "public, max-age=31536000, immutable"
            self._send(200, data, ctype, extra)

        # ---- API
        def _api(self, method, name, q):
            if name == "health":
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
            if name == "skills":
                return self._json(200, skills())
            if name == "local-models":
                return self._json(200, local_models())
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
            if method != "POST":
                return self._json(405, {"error": "use POST"})
            lang = q.get("lang", "en")
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
                with urllib.request.urlopen(req, timeout=8) as r:
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
        p = probe(cfg.local_llm_url + "/health")
        out["local_llm"] = {"ok": p["ok"], "ms": p["ms"], "url": cfg.local_llm_url, "error": p.get("error")}
        p = probe(hub_local + "/control/state", {"X-Jarvis-Hub": "1", "Host": hub_host})
        out["hub"] = {"ok": p["ok"], "ms": p["ms"], "url": hub, "error": p.get("error")}
        try:
            du = shutil.disk_usage(os.path.splitdrive(cfg.outputs_dir)[0] + "\\" if os.name == "nt" else "/")
            out["disk"] = {"free_gb": round(du.free / 1e9, 1), "total_gb": round(du.total / 1e9, 1)}
        except OSError:
            out["disk"] = None
        return out

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
        p = probe(cfg.local_llm_url + "/v1/models", timeout=2.0)
        models = []
        if p["ok"]:
            try:
                models = [m.get("id") for m in json.loads(p["body"].decode() or "{}").get("data", [])]
            except ValueError:
                pass
        return {"llm": {"url": cfg.local_llm_url, "up": p["ok"], "ms": p["ms"], "models": models},
                "voice": voice.status()}

    srv = Server((cfg.host, cfg.port), H)
    srv.voice, srv.kernel, srv.cfg = voice, kernel, cfg

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
    print(f"JARVIS console on http://{cfg.host}:{cfg.port}/  (kernel {cfg.kernel_url})", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
