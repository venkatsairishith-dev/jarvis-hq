"""Review fixes for the egress proxy (found by reading the builder's code, 2026-10-22): header smuggling and chunked uploads."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_proxy import rig, upstream  # noqa: F401,E402  (pytest fixtures)


def test_bare_lf_in_a_header_is_refused_and_nothing_connects(rig):
    out = rig.raw(b"GET http://good.test/ HTTP/1.1\r\nHost: good.test\r\nX-A: b\nX-Smuggled: yes\r\n\r\n")
    assert out.startswith(b"HTTP/1.1 400") and rig.connects == []


def test_bare_cr_in_a_header_is_refused_and_nothing_connects(rig):
    out = rig.raw(b"GET http://good.test/ HTTP/1.1\r\nHost: good.test\r\nX-A: b\rX-Smuggled: yes\r\n\r\n")
    assert out.startswith(b"HTTP/1.1 400") and rig.connects == []


def test_chunked_upload_keeps_its_transfer_encoding_header(rig, upstream):
    rig.raw(b"POST http://good.test/up HTTP/1.1\r\nHost: good.test\r\nTransfer-Encoding: chunked\r\n\r\n5\r\nhello\r\n0\r\n\r\n")
    seen = upstream.requests[0].decode().lower()
    assert "transfer-encoding: chunked" in seen and "5\r\nhello" in seen
