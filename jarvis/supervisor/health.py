"""Health checks (plan P1 step 4). Only ever against the component's own loopback/allowed bind address; no redirects, no proxy."""
from __future__ import annotations

import socket
import time
import urllib.error
import urllib.request


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):   # noqa: D401
        return None


_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect)


def tcp_ok(host: str, port: int, timeout: float = 1.0) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def http_ok(host: str, port: int, path: str = "/", timeout: float = 2.0) -> bool:
    """Healthy = the server answers with any HTTP status below 500 (a 401 from an auth-protected endpoint still means it is up)."""
    host_part = f"[{host}]" if ":" in host else host
    try:
        with _OPENER.open(f"http://{host_part}:{port}{path}", timeout=timeout) as r:
            return r.status < 500
    except urllib.error.HTTPError as e:
        return e.code < 500
    except (OSError, ValueError):
        return False


def wait_healthy(kind: str, host: str, port: int, path: str = "/", timeout_s: float = 30.0, poll_s: float = 0.25) -> bool:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if (tcp_ok(host, port) if kind == "tcp" else http_ok(host, port, path)):
            return True
        time.sleep(poll_s)
    return False
