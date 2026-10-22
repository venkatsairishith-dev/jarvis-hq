"""Run: python -m pytest jarvis/supervisor/test_binds.py -q -p no:cacheprovider"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import binds  # noqa: E402


@pytest.mark.parametrize("host", ["127.0.0.1", "localhost", "::1", "127.0.0.2"])
def test_loopback_is_allowed(host):
    assert binds.assert_bind(host) == host


@pytest.mark.parametrize("host", ["0.0.0.0", "::", "[::]", "8.8.8.8", "192.168.1.5", "", "example.com"])
def test_wide_public_or_unknown_binds_fail(host):
    with pytest.raises(binds.BindError):
        binds.assert_bind(host)


def test_explicitly_allowed_address_passes_only_when_listed():
    assert binds.assert_bind("100.125.91.51", allowed=("100.125.91.51",)) == "100.125.91.51"
    with pytest.raises(binds.BindError):
        binds.assert_bind("100.125.91.52", allowed=("100.125.91.51",))
