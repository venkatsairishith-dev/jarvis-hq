"""Bind assertion (plan P1 step 4): a supervised component may listen only on loopback or an explicitly allowed private address."""
from __future__ import annotations

import ipaddress

LOOPBACK_NAMES = {"localhost"}


class BindError(ValueError):
    pass


def assert_bind(host: str, allowed: tuple[str, ...] = ()) -> str:
    """Return host if it is loopback or in allowed (for example the Tailscale address); raise BindError for 0.0.0.0, :: and public addresses."""
    h = (host or "").strip().strip("[]")
    if h in LOOPBACK_NAMES:
        return host
    try:
        ip = ipaddress.ip_address(h)
    except ValueError as e:
        raise BindError(f"bind host {host!r} is not an IP address or localhost") from e
    if ip.is_unspecified:
        raise BindError(f"bind host {host!r} listens on every interface")
    if ip.is_loopback or h in allowed:
        return host
    raise BindError(f"bind host {host!r} is neither loopback nor in the allowed list")
