"""HTTP to a service on THIS machine, and only to this machine (re-review M13, 2026-10-18).

urllib's urlopen() reads http_proxy / https_proxy / ALL_PROXY from the environment, so with a proxy configured and 127.0.0.1 not in
no_proxy, text meant for the loopback privacy gateway, the JARVIS console or a local model was sent to the PROXY instead (secret-scrubbed
at best, not yet name-redacted). Every call site that talks to a loopback service goes through this one module:

  * the proxy settings are ignored (ProxyHandler({}) - an explicit empty proxy map, not "whatever the environment says");
  * the host must be exactly 127.0.0.1, ::1 or localhost, over http or https, with no user:password@ in the URL; anything else is
    refused with LoopbackError BEFORE a socket is opened;
  * redirects are not followed (a 3xx is an HTTPError: a loopback service that redirects somewhere else is not trusted);
  * for httpx, loopback_client() is AsyncClient(trust_env=False, follow_redirects=False) and checks the base URL the same way.

Standard library (+ httpx only inside loopback_client). A genuine external call (a web search, a download) must NOT use this module.
"""
from __future__ import annotations

import urllib.error
import urllib.parse
import urllib.request

LOOPBACK_HOSTS = ("127.0.0.1", "::1", "localhost")


class LoopbackError(ValueError):
    """The address is not a plain loopback address. The message never repeats the URL."""


def check_loopback(url: str) -> str:
    """The URL when it is a plain loopback address, else LoopbackError. Never echoes the URL."""
    try:
        parts = urllib.parse.urlsplit(str(url or "").strip())
        host = (parts.hostname or "").lower()
        parts.port          # noqa: B018 - raises ValueError for a bad port
    except ValueError:
        raise LoopbackError("the address could not be read, so nothing was sent") from None
    if parts.scheme not in ("http", "https") or host not in LOOPBACK_HOSTS or parts.username is not None or parts.password is not None:
        raise LoopbackError("this call may only go to this machine (127.0.0.1, ::1 or localhost, no credentials in the address), "
                            "so nothing was sent")
    return str(url).strip()


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None                                   # urllib then raises HTTPError for the 3xx: it is not followed


def build_opener() -> urllib.request.OpenerDirector:
    """An opener that ignores proxy settings and never follows a redirect."""
    return urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect())


def urlopen(req, timeout: float | None = None):
    """urllib.request.urlopen for a loopback service: checked address, no proxy, no redirect. `req` is a URL or a Request."""
    url = req.full_url if isinstance(req, urllib.request.Request) else req
    check_loopback(url)
    return build_opener().open(req, timeout=timeout) if timeout is not None else build_opener().open(req)


def loopback_client(base_url: str = "", **kw):
    """An httpx.AsyncClient that ignores proxy settings and follows no redirect. base_url (when given) must be loopback."""
    import httpx
    if base_url:
        check_loopback(base_url)
    kw.pop("trust_env", None)
    kw.pop("follow_redirects", None)
    return httpx.AsyncClient(trust_env=False, follow_redirects=False, **kw)
