"""JARVIS model forwarder for the Agent PC (hermes-agent-vm-2): a root-owned loopback service that lets the firewalled `agent` account reach the
kernel's model endpoint WITHOUT holding any key.

    agent (Hermes, base_url http://127.0.0.1:18790/v1, dummy key)  ->  this service  ->  JARVIS_ENDPOINT (the kernel llm-proxy on the tailnet)

What it does, and nothing else:
  * listens on 127.0.0.1 only (the port is fixed in the unit; the agent account cannot reach the tailnet itself);
  * forwards exactly these requests: POST /v1/chat/completions, POST /v1/embeddings, GET /v1/models; any other path is 404, any other method on those paths is 405;
  * drops every header the client sent (including any Authorization) and sets its own: Authorization: Bearer <token from /etc/jarvis-lab/model.token>,
    Content-Type and Accept. The token file is root 600, read on every request (so a replaced token works at once), never logged, never echoed;
  * refuses a request body larger than MAX_BODY (2 MB) and any request without a Content-Length (no chunked uploads);
  * talks to ONE upstream (JARVIS_FORWARD_UPSTREAM, default http://100.125.91.51:8790), checked at start: http only, host a tailnet (100.64.0.0/10) or loopback address;
  * follows no redirect (a 3xx from the upstream becomes a 502) and never contacts another host;
  * streams the answer back as it arrives (server-sent events work); upstream errors become a plain 502 with no upstream text.
Standard library only. The audit log of what the agent asked for is the kernel's own (the token names the lane).
"""
from __future__ import annotations

import http.client
import ipaddress
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

HOST = "127.0.0.1"
DEFAULT_PORT = 18790
DEFAULT_UPSTREAM = "http://100.125.91.51:8790"
DEFAULT_TOKEN_FILE = "/etc/jarvis-lab/model.token"
MAX_BODY = 2 * 1024 * 1024
UPSTREAM_TIMEOUT_S = 300
ALLOWED = {("POST", "/v1/chat/completions"), ("POST", "/v1/embeddings"), ("GET", "/v1/models")}
ALLOWED_PATHS = {p for _, p in ALLOWED}


def check_upstream(url: str) -> tuple[str, int]:
    """(host, port) of the upstream, or ValueError: http only, no credentials/path/query, host in 100.64.0.0/10 or loopback."""
    u = urlsplit(url)
    if u.scheme != "http" or not u.hostname or u.username or u.password or u.query or u.fragment or u.path not in ("", "/"):
        raise ValueError("upstream must look like http://<tailnet address>:<port>")
    ip = ipaddress.ip_address(u.hostname)
    if not (ip.is_loopback or ip in ipaddress.ip_network("100.64.0.0/10")):
        raise ValueError("upstream must be a tailnet (100.64.0.0/10) or loopback address")
    return u.hostname, u.port or 80


def read_token(path: str) -> str:
    with open(path, "r", encoding="utf-8") as fh:
        tok = fh.read().strip()
    if not tok or any(c.isspace() or ord(c) < 33 or ord(c) > 126 for c in tok):
        raise ValueError("token file is empty or not a plain token")
    return tok


def make_handler(upstream: tuple[str, int], token_file: str):
    class Forwarder(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"
        server_version = "jarvis-model-forwarder"
        sys_version = ""

        def log_message(self, fmt, *args):                   # request lines only: method, path, status. Never headers, bodies or the token.
            sys.stderr.write("%s %s\n" % (self.address_string(), fmt % args))

        def _send(self, code: int, body: bytes = b"", ctype: str = "application/json"):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(body)
            self.close_connection = True

        def _forward(self, method: str):
            path = self.path.split("?", 1)[0]
            if path not in ALLOWED_PATHS:
                return self._send(404, b'{"error":"not found"}')
            if (method, path) not in ALLOWED:
                return self._send(405, b'{"error":"method not allowed"}')
            body = b""
            if method == "POST":
                if "chunked" in (self.headers.get("Transfer-Encoding") or "").lower() or self.headers.get("Content-Length") is None:
                    return self._send(411, b'{"error":"content-length required"}')
                try:
                    n = int(self.headers.get("Content-Length") or "")
                except ValueError:
                    return self._send(400, b'{"error":"bad content-length"}')
                if n < 0 or n > MAX_BODY:
                    return self._send(413, b'{"error":"request too large"}')
                body = self.rfile.read(n)
            try:
                token = read_token(token_file)
            except (OSError, ValueError):
                return self._send(503, b'{"error":"model access is not set up"}')
            headers = {"Authorization": "Bearer " + token, "Accept": self.headers.get("Accept") or "application/json", "Connection": "close"}
            if method == "POST":
                headers["Content-Type"] = "application/json"
                headers["Content-Length"] = str(len(body))
            conn = http.client.HTTPConnection(upstream[0], upstream[1], timeout=UPSTREAM_TIMEOUT_S)
            try:
                conn.request(method, path, body=body if method == "POST" else None, headers=headers)
                resp = conn.getresponse()
                if 300 <= resp.status < 400:                    # never follow, never pass a Location on
                    return self._send(502, b'{"error":"bad upstream answer"}')
                self.send_response(resp.status)
                self.send_header("Content-Type", resp.getheader("Content-Type") or "application/json")
                self.send_header("Connection", "close")
                self.end_headers()
                while True:
                    chunk = resp.read1(65536) if hasattr(resp, "read1") else resp.read(65536)
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    self.wfile.flush()
                self.close_connection = True
            except (OSError, http.client.HTTPException):
                if not self.wfile.closed and not getattr(self, "_headers_buffer", None):
                    try:
                        self._send(502, b'{"error":"model endpoint not reachable"}')
                    except OSError:
                        pass
                self.close_connection = True
            finally:
                conn.close()

        def do_GET(self):
            self._forward("GET")

        def do_POST(self):
            self._forward("POST")

        def do_PUT(self):
            self._send(405, b'{"error":"method not allowed"}')

        do_DELETE = do_PATCH = do_PUT

    return Forwarder


def serve(port: int | None = None, upstream_url: str | None = None, token_file: str | None = None) -> ThreadingHTTPServer:
    up = check_upstream(upstream_url or os.environ.get("JARVIS_FORWARD_UPSTREAM", DEFAULT_UPSTREAM))
    srv = ThreadingHTTPServer((HOST, DEFAULT_PORT if port is None else port), make_handler(up, token_file or os.environ.get("JARVIS_FORWARD_TOKEN_FILE", DEFAULT_TOKEN_FILE)))
    srv.daemon_threads = True
    return srv


def main() -> int:
    try:
        srv = serve()
    except (ValueError, OSError) as e:
        sys.stderr.write("model forwarder not started: %s\n" % e)
        return 2
    sys.stderr.write("model forwarder on http://%s:%d -> %s\n" % (HOST, srv.server_address[1], os.environ.get("JARVIS_FORWARD_UPSTREAM", DEFAULT_UPSTREAM)))
    threading.Thread(target=srv.serve_forever, daemon=False).start()
    return 0


if __name__ == "__main__":
    sys.exit(main())
