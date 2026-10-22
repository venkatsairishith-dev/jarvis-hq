"""SSRF guard tests, no network: names resolve through fake resolvers.
Run: python -m pytest jarvis/egress -q -p no:cacheprovider"""
from __future__ import annotations

import socket
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ssrf_guard as g  # noqa: E402


def fixed(*ips):
    def resolver(host, port):
        return [(socket.AF_INET6 if ":" in ip else socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, port)) for ip in ips]
    return resolver


@pytest.mark.parametrize("ip", [
    "169.254.169.254", "169.254.0.1", "100.64.0.1", "100.100.100.100", "100.127.255.254", "127.0.0.1", "10.1.2.3", "172.16.0.9",
    "192.168.1.1", "0.0.0.0", "224.0.0.1", "240.0.0.1", "198.18.0.1", "::1", "::", "fe80::1", "fc00::1", "fd12::1", "ff02::1",
    "::ffff:127.0.0.1", "::ffff:169.254.169.254", "::ffff:100.64.0.1", "64:ff9b::a9fe:a9fe", "2002:a9fe:a9fe::1",
])
def test_non_public_addresses_are_refused(ip):
    v = g.check_url("https://example.test/x", fixed(ip))
    assert not v.ok and "non-public" in v.reason and v.ip == ""


@pytest.mark.parametrize("ip", ["93.184.216.34", "8.8.8.8", "1.1.1.1", "2606:4700:4700::1111", "::ffff:8.8.8.8"])
def test_public_addresses_pass_and_are_pinned(ip):
    v = g.check_url("https://example.test/x", fixed(ip))
    assert v.ok and v.ip == ip and v.port == 443 and v.host == "example.test"


def test_dns_rebinding_mix_is_refused_even_if_the_first_answer_is_public():
    assert not g.check_url("http://rebind.test/", fixed("93.184.216.34", "169.254.169.254")).ok


def test_dns_rebinding_two_step_fixture_is_defeated_by_pinning():
    """First lookup public, second lookup private. check_url resolves once and returns the public address to connect to; a caller that
    connects to the pin never asks DNS again, so the second (hostile) answer is never used."""
    answers = [["93.184.216.34"], ["127.0.0.1"]]
    calls = []

    def flaky(host, port):
        calls.append(host)
        return fixed(*answers[min(len(calls) - 1, 1)])(host, port)

    v = g.check_url("https://rebind.test/", flaky)
    assert v.ok and v.ip == "93.184.216.34" and len(calls) == 1
    # a naive client that resolves again would be sent to loopback; the guard would refuse that second lookup:
    assert not g.check_url("https://rebind.test/", flaky).ok


def test_scheme_credentials_port_and_host_rules():
    ok = fixed("93.184.216.34")
    assert not g.check_url("ftp://example.test/", ok).ok
    assert not g.check_url("file:///etc/passwd", ok).ok
    assert not g.check_url("gopher://example.test/", ok).ok
    assert not g.check_url("https://user:pw@example.test/", ok).ok
    assert not g.check_url("https://user@example.test/", ok).ok
    assert not g.check_url("http://example.test:22/", ok).ok
    assert not g.check_url("http://example.test:8080/", ok).ok
    assert g.check_url("http://example.test:8080/", ok, allowed_ports=(8080,)).ok
    assert not g.check_url("https:///nohost", ok).ok
    assert not g.check_url("http://[::1", ok).ok


def test_unresolvable_and_garbage_resolver_answers_are_refused():
    def boom(host, port):
        raise socket.gaierror("no such host")
    assert not g.check_url("https://nope.test/", boom).ok
    assert not g.check_url("https://nope.test/", lambda h, p: []).ok
    assert not g.check_url("https://nope.test/", lambda h, p: [(2, 1, 6, "", ("not-an-ip", p))]).ok


@pytest.mark.parametrize("spelling", ["2130706433", "0x7f000001", "0177.0.0.1", "127.1", "2852039166", "0xa9.0xfe.0xa9.0xfe"])
def test_odd_ip_spellings_are_judged_by_what_they_resolve_to(spelling):
    """The real resolver (inet_aton rules) turns these into loopback/metadata; the guard must refuse whatever it returns."""
    v = g.check_url(f"http://{spelling}/", g._default_resolver)
    assert not v.ok


def test_default_port_follows_scheme_and_trailing_dot_and_case_are_normalised():
    v = g.check_url("HTTP://Example.TEST./a", fixed("93.184.216.34"))
    assert v.ok and v.port == 80 and v.host == "example.test"
