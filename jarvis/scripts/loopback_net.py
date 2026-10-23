"""Loopback-only HTTP for JARVIS-authored programs that cannot import the plugin package (completion H, 2026-10-19). Standard library only.

Same rules as jarvis/plugins/_shared/loopback_http.py (re-review M13): urllib's urlopen() reads http_proxy / https_proxy / ALL_PROXY from the
environment, so with a proxy configured and 127.0.0.1 missing from no_proxy, text and tokens meant for a loopback service (the console,
the widget, the speech server, a local model) went to the PROXY. Here the proxy map is explicitly empty, the host must be exactly
127.0.0.1, ::1 or localhost with no user:password@, and a redirect is never followed. A genuine external call (a download, a web
search) must NOT use this module."""
from __future__ import annotations

import urllib.error
import urllib.parse
import urllib.request

LOOPBACK_HOSTS = ("127.0.0.1", "::1", "localhost")


class LoopbackError(ValueError):
    """The address is not a plain loopback address. The message never repeats the URL."""


def check_loopback(url, allow_hosts=()) -> str:
    """allow_hosts: exact extra host names (lower case) one caller may also reach, e.g. the kernel's configured address. Default: none."""
    try:
        parts = urllib.parse.urlsplit(str(url or "").strip())
        host = (parts.hostname or "").lower()
        parts.port          # noqa: B018 - raises ValueError for a bad port
    except ValueError:
        raise LoopbackError("the address could not be read, so nothing was sent") from None
    if parts.scheme not in ("http", "https") or (host not in LOOPBACK_HOSTS and host not in allow_hosts) or parts.username is not None or parts.password is not None:
        raise LoopbackError("this call may only go to this machine (127.0.0.1, ::1 or localhost, no credentials in the address), so nothing was sent")
    return str(url).strip()


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None                                   # urllib then raises HTTPError for the 3xx


def build_opener() -> urllib.request.OpenerDirector:
    return urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect())


def urlopen(req, timeout: float | None = None, allow_hosts=()):
    """urllib.request.urlopen for a loopback service: checked address, no proxy, no redirect. LoopbackError (a ValueError) before any socket."""
    check_loopback(req.full_url if isinstance(req, urllib.request.Request) else req, allow_hosts)
    return build_opener().open(req, timeout=timeout) if timeout is not None else build_opener().open(req)
