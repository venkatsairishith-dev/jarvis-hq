"""Egress proxy tests, loopback sockets only: a fake upstream on 127.0.0.1, an injected resolver and an injected connect().
Run: python -m pytest jarvis/egress -q -p no:cacheprovider"""
from __future__ import annotations

import json
import socket
import sys
import threading
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import proxy as p  # noqa: E402

PUBLIC = "93.184.216.34"


class Upstream:
    """Accepts connections; records what each received; replies to HTTP requests, echoes (uppercased) otherwise."""

    def __init__(self) -> None:
        self.sock = socket.socket()
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(8)
        self.port = self.sock.getsockname()[1]
        self.requests: list[bytes] = []
        threading.Thread(target=self._loop, daemon=True).start()

    def _loop(self) -> None:
        while True:
            try:
                c, _ = self.sock.accept()
            except OSError:
                return
            threading.Thread(target=self._one, args=(c,), daemon=True).start()

    def _one(self, c: socket.socket) -> None:
        try:
            c.settimeout(5)
            first = c.recv(65536)
            self.requests.append(first)
            if first.startswith(b"GET ") or first.startswith(b"POST "):
                c.sendall(b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\nConnection: close\r\n\r\nok")
                return
            c.sendall(first.upper())
            while True:
                d = c.recv(65536)
                if not d:
                    return
                c.sendall(d.upper())
        except OSError:
            pass
        finally:
            c.close()


@pytest.fixture
def upstream():
    u = Upstream()
    yield u
    u.sock.close()


def resolver_for(mapping, calls=None):
    def resolver(host, port):
        if calls is not None:
            calls.append(host)
        ips = mapping[host]
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, port)) for ip in ips]
    return resolver


class Rig:
    def __init__(self, upstream, mapping, **kw):
        self.connects: list[tuple[str, int]] = []
        self.logs: list[dict] = []
        self.resolve_calls: list[str] = []

        def connect(ip, port, timeout):
            self.connects.append((ip, port))
            return socket.create_connection(("127.0.0.1", upstream.port), timeout)
        kw.setdefault("log", self.logs.append)
        self.proxy = p.EgressProxy("127.0.0.1", 0, resolver=resolver_for(mapping, self.resolve_calls), connect=connect, timeout_s=5, **kw)
        self.proxy.start()

    def raw(self, data: bytes) -> bytes:
        s = socket.create_connection(("127.0.0.1", self.proxy.port), timeout=5)
        s.sendall(data)
        out = b""
        try:
            while True:
                d = s.recv(65536)
                if not d:
                    break
                out += d
        except OSError:
            pass
        s.close()
        return out


@pytest.fixture
def rig(upstream):
    r = Rig(upstream, {"good.test": [PUBLIC]})
    yield r
    r.proxy.close()


def test_connect_tunnel_relays_both_ways(rig, upstream):
    s = socket.create_connection(("127.0.0.1", rig.proxy.port), timeout=5)
    s.sendall(b"CONNECT good.test:443 HTTP/1.1\r\nHost: good.test:443\r\n\r\n")
    buf = b""
    while b"\r\n\r\n" not in buf:
        buf += s.recv(4096)
    assert buf.startswith(b"HTTP/1.1 200")
    s.sendall(b"hello")
    assert s.recv(100) == b"HELLO"
    s.sendall(b"again")
    assert s.recv(100) == b"AGAIN"
    s.close()
    assert rig.connects == [(PUBLIC, 443)]
    assert rig.logs[0]["verdict"] == "allow" and rig.logs[0]["ip"] == PUBLIC


def test_plain_http_get_is_rewritten_to_origin_form(rig, upstream):
    out = rig.raw(b"GET http://good.test/a/b?q=1 HTTP/1.1\r\nHost: good.test\r\nProxy-Connection: keep-alive\r\n"
                  b"Proxy-Authorization: Basic eDp5\r\nKeep-Alive: 5\r\nTE: trailers\r\nUpgrade: x\r\nConnection: keep-alive\r\n"
                  b"X-Keep: yes\r\n\r\n")
    assert out.startswith(b"HTTP/1.1 200") and out.endswith(b"ok")
    seen = upstream.requests[0].decode().lower()
    assert seen.startswith("get /a/b?q=1 http/1.1\r\n")
    for banned in ("proxy-connection", "proxy-authorization", "keep-alive:", "te:", "upgrade"):
        assert banned not in seen
    assert "connection: close" in seen and "x-keep: yes" in seen and "host: good.test" in seen
    assert rig.connects == [(PUBLIC, 80)]


@pytest.mark.parametrize("ips", [["169.254.169.254"], ["100.64.0.1"], ["127.0.0.1"], [PUBLIC, "169.254.169.254"]])
def test_non_public_resolutions_get_403_and_never_connect(upstream, ips):
    r = Rig(upstream, {"evil.test": ips})
    try:
        out = r.raw(b"CONNECT evil.test:443 HTTP/1.1\r\n\r\n")
        assert out.startswith(b"HTTP/1.1 403")
        assert "non-public" in json.loads(out.split(b"\r\n\r\n", 1)[1])["error"]
        out = r.raw(b"GET http://evil.test/ HTTP/1.1\r\n\r\n")
        assert out.startswith(b"HTTP/1.1 403")
        assert r.connects == []
        assert all(l["verdict"] == "deny" and l["ip"] == "" for l in r.logs)
    finally:
        r.proxy.close()


def test_resolver_is_called_once_per_request_even_if_it_would_flip(upstream):
    calls: list[str] = []
    answers = iter([[PUBLIC], ["169.254.169.254"]])

    def flipping(host, port):
        calls.append(host)
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, port)) for ip in next(answers)]
    connects: list[tuple[str, int]] = []

    def connect(ip, port, timeout):
        connects.append((ip, port))
        return socket.create_connection(("127.0.0.1", upstream.port), timeout)
    pr = p.EgressProxy("127.0.0.1", 0, resolver=flipping, connect=connect, timeout_s=5).start()
    try:
        s = socket.create_connection(("127.0.0.1", pr.port), timeout=5)
        s.sendall(b"CONNECT flip.test:443 HTTP/1.1\r\n\r\n")
        assert s.recv(4096).startswith(b"HTTP/1.1 200")
        s.close()
        assert calls == ["flip.test"] and connects == [(PUBLIC, 443)]
    finally:
        pr.close()


def test_port_22_is_refused(rig):
    out = rig.raw(b"CONNECT good.test:22 HTTP/1.1\r\n\r\n")
    assert out.startswith(b"HTTP/1.1 403") and rig.connects == []


def test_domain_allow_refuses_other_names_before_dns(upstream):
    r = Rig(upstream, {"good.test": [PUBLIC], "a.ok.test": [PUBLIC]}, domain_allow=["good.test", "*.ok.test"])
    try:
        assert r.raw(b"CONNECT other.test:443 HTTP/1.1\r\n\r\n").startswith(b"HTTP/1.1 403")
        assert r.raw(b"CONNECT ok.test:443 HTTP/1.1\r\n\r\n").startswith(b"HTTP/1.1 403")
        assert r.resolve_calls == [] and r.connects == []
        assert r.raw(b"GET http://a.ok.test/ HTTP/1.1\r\n\r\n").endswith(b"ok")
        assert r.resolve_calls == ["a.ok.test"]
    finally:
        r.proxy.close()


def test_bad_requests_get_400(rig):
    assert rig.raw(b"GET /relative HTTP/1.1\r\n\r\n").startswith(b"HTTP/1.1 400")
    assert rig.raw(b"garbage\r\n\r\n").startswith(b"HTTP/1.1 400")
    assert rig.raw(b"CONNECT nonsense HTTP/1.1\r\n\r\n").startswith(b"HTTP/1.1 400")
    assert rig.raw(b"GET http://good.test/ HTTP/1.1\r\nX: " + b"a" * 20000 + b"\r\n\r\n").startswith(b"HTTP/1.1 400")


def test_bind_to_all_interfaces_raises():
    with pytest.raises(ValueError):
        p.EgressProxy("0.0.0.0", 0)


def test_over_cap_connections_get_503(upstream):
    r = Rig(upstream, {"good.test": [PUBLIC]}, max_conns=1)
    try:
        held = socket.create_connection(("127.0.0.1", r.proxy.port), timeout=5)
        held.sendall(b"CONNECT good.test:443 HTTP/1.1\r\n\r\n")
        assert held.recv(4096).startswith(b"HTTP/1.1 200")
        assert r.raw(b"CONNECT good.test:443 HTTP/1.1\r\n\r\n").startswith(b"HTTP/1.1 503")
        held.close()
    finally:
        r.proxy.close()


def test_log_lines_hold_no_headers_bodies_or_query(upstream):
    r = Rig(upstream, {"good.test": [PUBLIC]})
    try:
        r.raw(b"GET http://good.test/p?token=SECRETQ HTTP/1.1\r\nAuthorization: Bearer SECRETH\r\nX-A: SECRETX\r\n\r\n")
        r.raw(b"POST http://good.test/p HTTP/1.1\r\nContent-Length: 10\r\n\r\nSECRETBODY")
        assert r.logs
        dumped = json.dumps(r.logs)
        for word in ("SECRET", "Bearer", "token"):
            assert word not in dumped
        assert set(r.logs[0]) == {"t", "method", "host", "port", "verdict", "reason", "ip"}
    finally:
        r.proxy.close()
