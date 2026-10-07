"""Filtering forward proxy for the agent's browser: the hard network-level guard.

Chrome is forced through this proxy (including loopback). For every connection the proxy resolves the host itself,
refuses internal addresses (loopback, private, link-local, Tailscale/CGNAT, metadata, ...) and then connects to the exact
address it checked. That covers redirects, subresources, scripts and DNS names that point at private addresses,
which an in-page request hook cannot see reliably. Tests may exempt exact host names with allow_hosts.
"""
import os
import select
import socket
import socketserver
import sys
import threading
import urllib.parse

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "kernel"))
from jarvis_kernel.policy import internal_host  # noqa: E402

MAX_HEAD = 16 * 1024
IDLE = 60


def _resolve_public(host: str, port: int, allow_hosts):
    """Return one address to connect to, or None if the host is forbidden/unresolvable."""
    if host in allow_hosts:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
        return infos[0][4] if infos else None
    if internal_host(host):
        return None
    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except OSError:
        return None
    if not infos or any(internal_host(i[4][0]) for i in infos):
        return None
    return infos[0][4]


def _pipe(a: socket.socket, b: socket.socket):
    socks = [a, b]
    try:
        while True:
            r, _, _ = select.select(socks, [], [], IDLE)
            if not r:
                return
            for s in r:
                data = s.recv(65536)
                if not data:
                    return
                (b if s is a else a).sendall(data)
    except OSError:
        return
    finally:
        for s in socks:
            try:
                s.close()
            except OSError:
                pass


class _Handler(socketserver.BaseRequestHandler):
    def handle(self):
        c = self.request
        c.settimeout(IDLE)
        try:
            head = b""
            while b"\r\n\r\n" not in head and len(head) < MAX_HEAD:
                chunk = c.recv(4096)
                if not chunk:
                    return
                head += chunk
            first, _, rest = head.partition(b"\r\n")
            method, target, _ver = first.decode("latin-1").split(" ", 2)
            allow = self.server.allow_hosts
            if method.upper() == "CONNECT":
                host, _, port = target.rpartition(":")
                host, port = host.strip("[]"), int(port or 443)
                addr = _resolve_public(host, port, allow)
                if not addr:
                    return self._deny(c, host)
                up = socket.create_connection(addr[:2], timeout=10)
                c.sendall(b"HTTP/1.1 200 Connection Established\r\n\r\n")
                return _pipe(c, up)
            parts = urllib.parse.urlsplit(target)
            if parts.scheme.lower() != "http" or not parts.hostname:
                return self._deny(c, parts.hostname or "?")
            port = parts.port or 80
            addr = _resolve_public(parts.hostname, port, allow)
            if not addr:
                return self._deny(c, parts.hostname)
            path = (parts.path or "/") + ("?" + parts.query if parts.query else "")
            lines = [ln for ln in rest.split(b"\r\n") if ln and not ln.lower().startswith((b"proxy-", b"connection:"))]
            req = f"{method} {path} HTTP/1.1\r\n".encode() + b"\r\n".join(lines) + b"\r\nConnection: close\r\n\r\n"
            up = socket.create_connection(addr[:2], timeout=10)
            up.sendall(req)
            _pipe(c, up)
        except Exception:
            try:
                c.close()
            except OSError:
                pass

    def _deny(self, c, host):
        self.server.blocked.append(str(host)[:80])
        try:
            c.sendall(b"HTTP/1.1 403 Forbidden\r\nContent-Length: 0\r\nConnection: close\r\n\r\n")
        finally:
            c.close()


class GuardProxy(socketserver.ThreadingTCPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, allow_hosts=()):
        super().__init__(("127.0.0.1", 0), _Handler)
        self.allow_hosts, self.blocked = tuple(allow_hosts), []
        self.port = self.server_address[1]

    def start(self):
        threading.Thread(target=self.serve_forever, daemon=True).start()
        return self

    def stop(self):
        self.shutdown()
        self.server_close()
