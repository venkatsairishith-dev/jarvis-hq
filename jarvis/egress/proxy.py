"""Egress proxy for JARVIS's agents (plan P2 step 2; replaces Stripe Smokescreen, standard library only).

A small threaded forward proxy. Every request (CONNECT host:port, or a plain HTTP request with an absolute URI) is checked with
ssrf_guard.check_url(); a refused request gets HTTP 403 {"error": reason} and nothing is connected. An allowed one is connected to the
PINNED address that check_url returned, never resolved again (DNS rebinding). An optional domain allowlist is applied before any DNS
lookup. The proxy binds only through binds.assert_bind (loopback or an explicitly allowed address). One JSON line per decision goes to
log(); bodies, headers, query strings and credentials are never logged.

Run: python jarvis/egress/proxy.py --host 127.0.0.1 --port 4750 [--allow-host IP]... [--domain-allow PATTERN]...
"""
from __future__ import annotations

import argparse
import fnmatch
import json
import socket
import socketserver
import sys
import threading
import time
from pathlib import Path
from typing import Callable, Sequence
from urllib.parse import urlsplit

_HERE = Path(__file__).resolve().parent
for _p in (_HERE, _HERE.parent / "supervisor"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))
import binds  # noqa: E402
import ssrf_guard  # noqa: E402

MAX_HEADER_BYTES = 16 * 1024
HOP_BY_HOP = {"proxy-connection", "proxy-authorization", "connection", "keep-alive", "te", "trailer", "upgrade"}   # transfer-encoding is NOT dropped: the body is relayed as raw bytes, so a chunked upload must keep its framing header
CHUNK = 16 * 1024

Connect = Callable[[str, int, float], socket.socket]


def _default_connect(ip: str, port: int, timeout: float) -> socket.socket:
    return socket.create_connection((ip, port), timeout)


def _split_hostport(target: str) -> tuple[str, int] | None:
    """Parse 'host:port' or '[v6]:port'; None when malformed."""
    host, sep, port = target.rpartition(":")
    if not sep or not host or not port.isdigit() or not 0 < int(port) < 65536:
        return None
    if host.startswith("[") and host.endswith("]"):
        host = host[1:-1]
    if not host or any(c in host for c in "/?#@ []"):
        return None
    return host, int(port)


def _domain_matches(host: str, patterns: Sequence[str]) -> bool:
    host = host.rstrip(".").lower()
    for p in patterns:
        p = p.lower()
        if p.startswith("*."):
            if host.endswith(p[1:]) and len(host) > len(p) - 1:
                return True
        elif fnmatch.fnmatchcase(host, p):
            return True
    return False


def _pipe(src: socket.socket, dst: socket.socket) -> None:
    try:
        while True:
            data = src.recv(CHUNK)
            if not data:
                break
            dst.sendall(data)
    except OSError:
        pass
    finally:
        try:
            dst.shutdown(socket.SHUT_WR)
        except OSError:
            pass


class _Server(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True
    proxy: "EgressProxy"


class _Handler(socketserver.BaseRequestHandler):
    server: _Server

    def handle(self) -> None:
        proxy = self.server.proxy
        client = self.request
        if not proxy._slots.acquire(blocking=False):
            # Read the request head first: closing a socket that still holds unread data makes the OS reset the connection (seen on
            # Windows), and the client would lose this answer.
            try:
                client.settimeout(2)
                self._read_head(client)
            except OSError:
                pass
            self._reply(client, 503, {"error": "too many connections"})
            return
        upstream: socket.socket | None = None
        try:
            client.settimeout(proxy.timeout_s)
            upstream = self._serve(proxy, client)
        except OSError:
            pass
        finally:
            for s in (upstream, client):
                if s is not None:
                    try:
                        s.close()
                    except OSError:
                        pass
            proxy._slots.release()

    @staticmethod
    def _reply(sock: socket.socket, status: int, body: dict) -> None:
        reasons = {400: "Bad Request", 403: "Forbidden", 502: "Bad Gateway", 503: "Service Unavailable"}
        payload = json.dumps(body).encode()
        head = (f"HTTP/1.1 {status} {reasons.get(status, 'Error')}\r\nContent-Type: application/json\r\n"
                f"Content-Length: {len(payload)}\r\nConnection: close\r\n\r\n").encode()
        try:
            sock.sendall(head + payload)
        except OSError:
            pass

    @staticmethod
    def _read_head(sock: socket.socket) -> tuple[bytes, bytes] | None:
        buf = b""
        while b"\r\n\r\n" not in buf:
            if len(buf) > MAX_HEADER_BYTES:
                return None
            chunk = sock.recv(4096)
            if not chunk:
                return None
            buf += chunk
        head, _, rest = buf.partition(b"\r\n\r\n")
        if len(head) > MAX_HEADER_BYTES:
            return None
        return head, rest

    def _serve(self, proxy: "EgressProxy", client: socket.socket) -> socket.socket | None:
        got = self._read_head(client)
        if got is None:
            self._reply(client, 400, {"error": "bad or oversized request head"})
            return None
        head, rest = got
        try:
            lines = head.decode("latin-1").split("\r\n")
            method, target, version = lines[0].split(" ")
        except ValueError:
            self._reply(client, 400, {"error": "malformed request line"})
            return None
        if not version.startswith("HTTP/"):
            self._reply(client, 400, {"error": "malformed request line"})
            return None
        if any(("\n" in ln or "\r" in ln) for ln in lines):         # a bare CR or LF inside a line would reach the upstream as a smuggled header
            self._reply(client, 400, {"error": "bare CR or LF in request head"})
            return None
        method = method.upper()
        if method == "CONNECT":
            hp = _split_hostport(target)
            if hp is None:
                self._reply(client, 400, {"error": "bad CONNECT target"})
                return None
            host, port = hp
            url = f"https://{'[' + host + ']' if ':' in host else host}:{port}"
            path = ""
        else:
            try:
                parts = urlsplit(target)
                parts.port
            except ValueError:
                self._reply(client, 400, {"error": "bad request uri"})
                return None
            if parts.scheme.lower() != "http" or not parts.hostname:
                self._reply(client, 400, {"error": "absolute http URI required"})
                return None
            host, port = parts.hostname, parts.port or 80
            url = target
            path = (parts.path or "/") + (f"?{parts.query}" if parts.query else "")
        host = host.rstrip(".").lower()

        def decide(verdict: str, reason: str, ip: str = "") -> None:
            proxy._log({"t": time.time(), "method": method, "host": host, "port": port, "verdict": verdict, "reason": reason, "ip": ip})

        if proxy.domain_allow and not _domain_matches(host, proxy.domain_allow):
            decide("deny", "host not in domain allowlist")
            self._reply(client, 403, {"error": "host not in domain allowlist"})
            return None
        v = ssrf_guard.check_url(url, proxy.resolver)
        if not v.ok:
            decide("deny", v.reason)
            self._reply(client, 403, {"error": v.reason})
            return None
        try:
            upstream = proxy.connect(v.ip, v.port, proxy.timeout_s)
        except OSError:
            decide("deny", "upstream connect failed", v.ip)
            self._reply(client, 502, {"error": "upstream connect failed"})
            return None
        decide("allow", "", v.ip)
        upstream.settimeout(proxy.timeout_s)
        if method == "CONNECT":
            client.sendall(b"HTTP/1.1 200 Connection Established\r\n\r\n")
            if rest:
                upstream.sendall(rest)
        else:
            out = [f"{method} {path} {version}"]
            out.append(f"Host: {urlsplit(url).netloc}")
            for line in lines[1:]:
                name = line.split(":", 1)[0].strip().lower()
                if not name or ":" not in line or name in HOP_BY_HOP or name == "host":
                    continue
                out.append(line)
            out.append("Connection: close")
            upstream.sendall(("\r\n".join(out) + "\r\n\r\n").encode("latin-1") + rest)
        t = threading.Thread(target=_pipe, args=(client, upstream), daemon=True)
        t.start()
        _pipe(upstream, client)
        t.join(proxy.timeout_s)
        return upstream


class EgressProxy:
    def __init__(self, host: str, port: int, allowed_hosts: Sequence[str] = (), resolver: ssrf_guard.Resolver | None = None,
                 connect: Connect | None = None, domain_allow: Sequence[str] | None = None,
                 log: Callable[[dict], None] | None = None, max_conns: int = 64, timeout_s: float = 30) -> None:
        binds.assert_bind(host, tuple(allowed_hosts))
        self.resolver = resolver
        self.connect = connect or _default_connect
        self.domain_allow = list(domain_allow) if domain_allow else None
        self._log = log or (lambda rec: None)
        self.timeout_s = timeout_s
        self._slots = threading.BoundedSemaphore(max_conns)
        self._server = _Server((host.strip("[]"), port), _Handler)
        self._server.proxy = self
        self.host = host
        self.port: int = self._server.server_address[1]
        self._thread: threading.Thread | None = None

    def serve_forever(self) -> None:
        self._server.serve_forever()

    def start(self) -> "EgressProxy":
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        return self

    def close(self) -> None:
        if self._thread is not None:
            self._server.shutdown()
            self._thread.join(5)
        self._server.server_close()

    def __enter__(self) -> "EgressProxy":
        return self.start()

    def __exit__(self, *exc) -> None:
        self.close()


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="JARVIS egress proxy (SSRF-guarded forward proxy)")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=4750)
    ap.add_argument("--allow-host", action="append", default=[], metavar="IP", help="extra non-loopback bind address allowed (e.g. Tailscale)")
    ap.add_argument("--domain-allow", action="append", default=[], metavar="PATTERN", help="hostname or *.example.com; repeatable")
    a = ap.parse_args(argv)
    proxy = EgressProxy(a.host, a.port, a.allow_host, domain_allow=a.domain_allow or None,
                        log=lambda rec: print(json.dumps(rec), file=sys.stderr, flush=True))
    print(f"egress proxy listening on {a.host}:{proxy.port}", flush=True)
    try:
        proxy.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        proxy.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
