"""SSRF guard for JARVIS's egress proxy (plan P2 step 2; the policy core of the Smokescreen port).

check_url() decides whether an outbound request may be made and returns the ONE address to connect to. The caller must connect to that
pinned address (sending the original Host header / SNI), never resolve the name again: that is what defeats DNS rebinding.

Refused: any scheme but http/https, credentials in the URL, ports outside the allowed set, a host that does not resolve, and any host where
EVERY OR ANY resolved address is not globally routable: loopback, private (RFC 1918, ULA), link-local (169.254.0.0/16 including the
169.254.169.254 metadata address), carrier-grade NAT (100.64.0.0/10, which covers Tailscale), multicast, reserved, unspecified, and IPv4
addresses written inside IPv6 (::ffff:a.b.c.d, 6to4, Teredo, NAT64) when the embedded address is not global. Odd spellings of an IP
(decimal, hex, octal, short forms) are caught because the decision is made on what the resolver returns.
"""
from __future__ import annotations

import ipaddress
import socket
from dataclasses import dataclass
from typing import Callable
from urllib.parse import urlsplit

ALLOWED_PORTS = (80, 443)
ALLOWED_SCHEMES = ("http", "https")
CGNAT = ipaddress.ip_network("100.64.0.0/10")
EXTRA_BLOCKED_V4 = tuple(ipaddress.ip_network(n) for n in (
    "0.0.0.0/8", "100.64.0.0/10", "169.254.0.0/16", "192.0.0.0/24", "192.0.2.0/24", "198.18.0.0/15", "198.51.100.0/24",
    "203.0.113.0/24", "224.0.0.0/4", "240.0.0.0/4"))

Resolver = Callable[..., list]


@dataclass(frozen=True)
class Verdict:
    ok: bool
    reason: str = ""
    host: str = ""
    port: int = 0
    ip: str = ""            # the pinned address to connect to (empty when refused)


def _embedded_v4(ip: ipaddress.IPv6Address):
    """The IPv4 address hidden inside an IPv6 one (mapped, 6to4, Teredo, NAT64), or None."""
    if ip.ipv4_mapped:
        return ip.ipv4_mapped
    if ip.sixtofour:
        return ip.sixtofour
    if ip.teredo:
        return ip.teredo[1]
    packed = ip.packed
    if packed[:12] == bytes.fromhex("0064ff9b0000000000000000"):          # 64:ff9b::/96 (NAT64)
        return ipaddress.IPv4Address(packed[12:])
    return None


def ip_is_public(ip: ipaddress._BaseAddress) -> bool:
    if isinstance(ip, ipaddress.IPv6Address):
        inner = _embedded_v4(ip)
        if inner is not None:
            return ip_is_public(inner)
        return ip.is_global and not ip.is_multicast
    if any(ip in n for n in EXTRA_BLOCKED_V4):
        return False
    return ip.is_global


def _default_resolver(host: str, port: int) -> list:
    return socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)


def check_url(url: str, resolver: Resolver | None = None, allowed_ports: tuple[int, ...] = ALLOWED_PORTS) -> Verdict:
    resolver = resolver or _default_resolver
    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError:
        return Verdict(False, "unparsable url")
    if parts.scheme.lower() not in ALLOWED_SCHEMES:
        return Verdict(False, f"scheme {parts.scheme!r} not allowed")
    if parts.username is not None or parts.password is not None:
        return Verdict(False, "credentials in url")
    host = (parts.hostname or "").rstrip(".").lower()
    if not host:
        return Verdict(False, "no host")
    if port is None:
        port = 443 if parts.scheme.lower() == "https" else 80
    if port not in allowed_ports:
        return Verdict(False, f"port {port} not allowed", host, port)
    try:
        infos = resolver(host, port)
    except (OSError, UnicodeError):
        return Verdict(False, "host does not resolve", host, port)
    addrs: list[ipaddress._BaseAddress] = []
    for info in infos:
        raw = info[4][0].split("%")[0]
        try:
            addrs.append(ipaddress.ip_address(raw))
        except ValueError:
            return Verdict(False, "resolver returned a non-address", host, port)
    if not addrs:
        return Verdict(False, "host does not resolve", host, port)
    bad = [str(a) for a in addrs if not ip_is_public(a)]
    if bad:                                                   # one private answer is enough to refuse: a rebinding mix is hostile
        return Verdict(False, f"resolves to a non-public address ({bad[0]})", host, port)
    return Verdict(True, "", host, port, str(addrs[0]))
