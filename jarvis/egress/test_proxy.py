"""Egress proxy tests, no internet: names resolve through fake resolvers, and the pinned "public" address is routed by a fake
connector to an in-process upstream on loopback. Run: python -m pytest jarvis/egress -q -p no:cacheprovider"""
from __future__ import annotations

import asyncio
import base64
import json
import socket
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import proxy as egress  # noqa: E402
from binds import BindError  # noqa: E402

PUBLIC = "93.184.216.34"


def resolver_of(table):
    calls = []

    def resolve(host, port):
        calls.append(host)
        ips = table[host] if isinstance(table[host], list) else [table[host]]
        return [(socket.AF_INET6 if ":" in ip else socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, port)) for ip in ips]
    resolve.calls = calls
    return resolve


def no_sensitive(host):
    return "bank" if host.endswith("chase.com") else ""


class Upstream:
    """A loopback server standing in for the internet. It records what it received and answers or echoes."""

    def __init__(self, mode="http"):
        self.mode, self.received = mode, b""

    async def start(self):
        self.server = await asyncio.start_server(self._handle, "127.0.0.1", 0)
        self.port = self.server.sockets[0].getsockname()[1]
        return self

    async def _handle(self, r, w):
        if self.mode == "echo":
            data = await r.read(1024)
            self.received += data
            w.write(b"echo:" + data)
        else:
            self.received = await r.readuntil(b"\r\n\r\n")
            w.write(b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\nConnection: close\r\n\r\nok")
        await w.drain()
        w.close()

    async def close(self):
        self.server.close()
        await self.server.wait_closed()


async def make(table, upstream=None, acl=None, logs=None, **kw):
    connected = []

    async def connector(ip, port):
        connected.append((ip, port))
        if upstream is None:
            raise OSError("unreachable")
        return await asyncio.open_connection("127.0.0.1", upstream.port)
    p = egress.EgressProxy("127.0.0.1", 0, acl, resolver=resolver_of(table), connector=connector,
                           log=(logs.append if logs is not None else (lambda r: None)), sensitive=no_sensitive, **kw)
    await p.start()
    p.connected = connected
    return p


async def send(proxy, raw: bytes, then: bytes = b"") -> bytes:
    r, w = await asyncio.open_connection("127.0.0.1", proxy.port)
    w.write(raw)
    await w.drain()
    out = b""
    if then:
        out = await asyncio.wait_for(r.readuntil(b"\r\n\r\n"), 5)
        w.write(then)
        await w.drain()
    out += await asyncio.wait_for(r.read(), 5)
    w.close()
    return out


async def test_connect_to_a_public_host_tunnels_to_the_pinned_address():
    up = await Upstream("echo").start()
    p = await make({"example.test": PUBLIC}, up)
    out = await send(p, b"CONNECT example.test:443 HTTP/1.1\r\nHost: example.test:443\r\n\r\n", then=b"hello")
    assert out.startswith(b"HTTP/1.1 200") and out.endswith(b"echo:hello")
    assert p.connected == [(PUBLIC, 443)]
    await p.close(); await up.close()


@pytest.mark.parametrize("ip", ["169.254.169.254", "100.100.100.100", "127.0.0.1", "10.0.0.5", "::ffff:169.254.169.254"])
async def test_connect_to_internal_addresses_is_refused_without_connecting(ip):
    p = await make({"bad.test": ip})
    out = await send(p, b"CONNECT bad.test:443 HTTP/1.1\r\n\r\n")
    assert out.startswith(b"HTTP/1.1 403") and p.connected == []
    await p.close()


async def test_browser_self_test_metadata_request_gets_403():
    # jarvis_node/browser.py verify_egress() sends exactly this and refuses to browse unless it sees 403/407
    p = await make({"169.254.169.254": "169.254.169.254"})
    out = await send(p, b"GET http://169.254.169.254/latest/meta-data/ HTTP/1.1\r\nHost: 169.254.169.254\r\n\r\n")
    assert out.startswith(b"HTTP/1.1 403") and p.connected == []
    await p.close()


async def test_plain_http_is_forwarded_in_origin_form_without_proxy_headers():
    up = await Upstream("http").start()
    p = await make({"example.test": PUBLIC}, up)
    out = await send(p, b"GET http://example.test/a/b?q=1 HTTP/1.1\r\nHost: example.test\r\nProxy-Authorization: Basic eDp5\r\n"
                        b"Proxy-Connection: keep-alive\r\nUser-Agent: t\r\n\r\n")
    assert out.startswith(b"HTTP/1.1 200 OK") and out.endswith(b"ok")
    got = up.received.decode()
    assert got.startswith("GET /a/b?q=1 HTTP/1.1\r\n") and "Host: example.test" in got and "User-Agent: t" in got
    assert "Proxy-" not in got and "Connection: close" in got
    assert p.connected == [(PUBLIC, 80)]
    await p.close(); await up.close()


async def test_dns_is_resolved_once_so_a_rebinding_name_cannot_reach_loopback():
    up = await Upstream("echo").start()
    answers = iter([PUBLIC, "127.0.0.1"])
    calls = []

    def flipping(host, port):
        calls.append(host)
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (next(answers), port))]

    async def connector(ip, port):
        assert ip == PUBLIC          # the proxy connects to the address it checked, never to a second lookup
        return await asyncio.open_connection("127.0.0.1", up.port)
    p = egress.EgressProxy("127.0.0.1", 0, resolver=flipping, connector=connector, log=lambda r: None, sensitive=no_sensitive)
    await p.start()
    out = await send(p, b"CONNECT rebind.test:443 HTTP/1.1\r\n\r\n", then=b"x")
    assert out.startswith(b"HTTP/1.1 200") and calls == ["rebind.test"]
    await p.close(); await up.close()


async def test_sensitive_sites_are_refused_even_when_public():
    p = await make({"secure.chase.com": PUBLIC})
    out = await send(p, b"CONNECT secure.chase.com:443 HTTP/1.1\r\n\r\n")
    assert out.startswith(b"HTTP/1.1 403") and p.connected == []
    await p.close()


async def test_kernel_sensitive_floor_is_used_by_default():
    # without an injected checker the proxy uses jarvis_kernel.policy.sensitive_host (built-in floor + sensitive_sites.json)
    if egress._kernel_sensitive_host is None:
        pytest.skip("kernel policy not importable here")
    d = egress.decide("https://www.chase.com/", egress.Role("default"), resolver_of({"www.chase.com": PUBLIC}))
    assert not d.allow and d.reason == "sensitive site"


@pytest.mark.parametrize("req", [
    b"CONNECT example.test:8443 HTTP/1.1\r\n\r\n",           # CONNECT only to 443
    b"CONNECT example.test:80 HTTP/1.1\r\n\r\n",
    b"GET http://example.test:8080/ HTTP/1.1\r\n\r\n",       # plain http only to 80
    b"GET https://example.test/ HTTP/1.1\r\n\r\n",           # https must use CONNECT
    b"GET ftp://example.test/ HTTP/1.1\r\n\r\n",
    b"GET /relative HTTP/1.1\r\nHost: example.test\r\n\r\n",  # not a proxy request
    b"GET http://user:pw@example.test/ HTTP/1.1\r\n\r\n",     # credentials in the url
])
async def test_wrong_ports_schemes_and_forms_are_refused(req):
    p = await make({"example.test": PUBLIC})
    out = await send(p, req)
    assert out.startswith(b"HTTP/1.1 403") and p.connected == []
    await p.close()


async def test_oversized_headers_are_rejected():
    p = await make({"example.test": PUBLIC})
    out = await send(p, b"GET http://example.test/ HTTP/1.1\r\nX: " + b"a" * (egress.MAX_HEAD + 100) + b"\r\n\r\n")
    assert out.startswith(b"HTTP/1.1 431") and p.connected == []
    await p.close()


async def test_unreachable_upstream_answers_502():
    p = await make({"example.test": PUBLIC}, upstream=None)
    out = await send(p, b"CONNECT example.test:443 HTTP/1.1\r\n\r\n")
    assert out.startswith(b"HTTP/1.1 502")
    await p.close()


def test_bind_on_every_interface_or_a_public_address_is_refused():
    with pytest.raises(BindError):
        egress.EgressProxy("0.0.0.0", 0)
    with pytest.raises(BindError):
        egress.EgressProxy("8.8.8.8", 0)
    assert egress.EgressProxy("100.125.91.51", 0, allowed_binds=("100.125.91.51",)).host == "100.125.91.51"


async def test_decision_log_has_no_path_query_or_headers():
    logs = []
    p = await make({"169.254.169.254": "169.254.169.254"}, logs=logs)
    await send(p, b"GET http://169.254.169.254/secret/path?token=abc HTTP/1.1\r\nCookie: s=1\r\n\r\n")
    assert len(logs) == 1 and logs[0]["allow"] is False and logs[0]["host"] == "169.254.169.254"
    text = json.dumps(logs)
    assert "secret" not in text and "token" not in text and "Cookie" not in text
    await p.close()


def _acl(tmp_path, **roles):
    spec = {}
    for name, (token, extra) in roles.items():
        f = tmp_path / f"{name}.token"
        f.write_text(token, encoding="utf-8")
        spec[name] = {"token_file": str(f), **extra}
    path = tmp_path / "acl.json"
    path.write_text(json.dumps({"roles": spec}), encoding="utf-8")
    return egress.Acl.load(path)


def _auth(role, token):
    return b"Proxy-Authorization: Basic " + base64.b64encode(f"{role}:{token}".encode()) + b"\r\n"


async def test_roles_require_the_right_token(tmp_path):
    acl = _acl(tmp_path, research=("tok-123", {}))
    up = await Upstream("echo").start()
    p = await make({"example.test": PUBLIC}, up, acl=acl)
    assert (await send(p, b"CONNECT example.test:443 HTTP/1.1\r\n\r\n")).startswith(b"HTTP/1.1 407")
    assert (await send(p, b"CONNECT example.test:443 HTTP/1.1\r\n" + _auth("research", "wrong") + b"\r\n")).startswith(b"HTTP/1.1 407")
    ok = await send(p, b"CONNECT example.test:443 HTTP/1.1\r\n" + _auth("research", "tok-123") + b"\r\n", then=b"hi")
    assert ok.startswith(b"HTTP/1.1 200")
    await p.close(); await up.close()


async def test_role_deny_and_allow_only_lists(tmp_path):
    acl = _acl(tmp_path, research=("t1", {"deny_hosts": ["blocked.test"]}), narrow=("t2", {"allow_only": ["docs.test"]}))
    up = await Upstream("echo").start()
    p = await make({"blocked.test": PUBLIC, "a.docs.test": PUBLIC, "other.test": PUBLIC}, up, acl=acl)
    assert (await send(p, b"CONNECT blocked.test:443 HTTP/1.1\r\n" + _auth("research", "t1") + b"\r\n")).startswith(b"HTTP/1.1 403")
    assert (await send(p, b"CONNECT other.test:443 HTTP/1.1\r\n" + _auth("narrow", "t2") + b"\r\n")).startswith(b"HTTP/1.1 403")
    assert (await send(p, b"CONNECT a.docs.test:443 HTTP/1.1\r\n" + _auth("narrow", "t2") + b"\r\n", then=b"z")).startswith(b"HTTP/1.1 200")
    await p.close(); await up.close()


def test_acl_refuses_roles_without_tokens_and_empty_tokens(tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"roles": {"research": {}}}), encoding="utf-8")
    with pytest.raises(ValueError):
        egress.Acl.load(bad)
    empty = tmp_path / "e.token"
    empty.write_text("  ", encoding="utf-8")
    bad.write_text(json.dumps({"roles": {"research": {"token_file": str(empty)}}}), encoding="utf-8")
    with pytest.raises(ValueError):
        egress.Acl.load(bad)


async def test_full_proxy_answers_503():
    p = await make({"example.test": PUBLIC}, max_connections=1)
    r1, w1 = await asyncio.open_connection("127.0.0.1", p.port)   # holds the only slot (never sends a full head)
    w1.write(b"CONNECT ")
    await w1.drain()
    await asyncio.sleep(0.05)
    out = await send(p, b"CONNECT example.test:443 HTTP/1.1\r\n\r\n")
    assert out.startswith(b"HTTP/1.1 503")
    w1.close()
    await p.close()


def test_missing_sensitive_rules_fail_closed(monkeypatch):
    monkeypatch.setattr(egress, "_kernel_sensitive_host", None)
    d = egress.decide("https://example.test/", egress.Role("default"), resolver_of({"example.test": PUBLIC}))
    assert not d.allow and "fail closed" in d.reason
