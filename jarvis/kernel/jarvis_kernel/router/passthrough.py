"""Byte-identical passthrough of Anthropic-format requests (for coding CLIs such as Claude Code).

The request body is sent upstream exactly as received (never parsed or re-serialised); the response is relayed as raw
bytes in the chunks the upstream produced, so SSE `event: ping` blocks and every other event arrive in order, unchanged.
Only allow-listed headers cross in either direction. The real key is set here (x-api-key); whatever credentials the caller
sent are dropped. Redirects are never followed, no environment proxy is used, and nothing (body, headers, key) is logged
or stored. A non-2xx body is relayed with the key and anything shaped like an Anthropic key replaced by [KEY].
"""
import hashlib
import http.client
import re
import urllib.parse

REQUEST_HEADERS = ("anthropic-version", "anthropic-beta", "content-type", "accept", "user-agent")
RESPONSE_HEADERS = ("content-type", "request-id", "retry-after")
RESPONSE_HEADER_PREFIXES = ("anthropic-ratelimit-",)
CHUNK = 65536
MAX_ERROR_BODY = 1_000_000      # an error body is read whole to scrub the key out of it; a hostile upstream cannot make that unbounded
KEY_SHAPE = re.compile(rb"sk-ant-[A-Za-z0-9_\-]+")


class PassthroughError(Exception):
    """The upstream could not be reached. The message never carries the key, URL path or any request data."""


def _default_opener(method, url, body, headers, timeout_s):
    """One plain HTTP(S) request with http.client: no redirects, no environment proxy. Returns (response, connection)."""
    u = urllib.parse.urlsplit(url)
    cls = http.client.HTTPSConnection if u.scheme == "https" else http.client.HTTPConnection
    conn = cls(u.hostname, u.port, timeout=timeout_s)
    try:
        conn.request(method, u.path or "/", body=body, headers=headers)
        return conn.getresponse(), conn
    except BaseException:
        conn.close()
        raise


def _scrub(data: bytes, key: str) -> bytes:
    if key:
        data = data.replace(key.encode("utf-8", "replace"), b"[KEY]")
    return KEY_SHAPE.sub(b"[KEY]", data)


class Passthrough:
    """An open upstream response. .status, .headers (allow-listed), iter_body(), close()."""

    def __init__(self, base_url, key, resp, conn, sent_sha256):
        self._base_url, self._key, self._resp, self._conn = base_url, key, resp, conn
        self.sent_sha256 = sent_sha256
        self.status = resp.status
        self.headers = {}
        for k, v in resp.getheaders():
            lk = k.lower()
            if lk in RESPONSE_HEADERS or lk.startswith(RESPONSE_HEADER_PREFIXES):
                self.headers[lk] = _scrub(v.encode("latin-1", "replace"), key).decode("latin-1")
        self._used = False

    def __repr__(self):
        return f"Passthrough(base_url={self._base_url!r})"

    __str__ = __repr__

    def close(self):
        for c in (self._resp, self._conn):
            try:
                c.close()
            except Exception:  # noqa: BLE001 - closing is best effort
                pass

    def iter_body(self):
        """The upstream body as raw chunks. Closing this generator (caller disconnect) closes the upstream connection."""
        if self._used:
            return
        self._used = True
        try:
            if not 200 <= self.status < 300:
                yield _scrub(self._resp.read(MAX_ERROR_BODY), self._key)
                return
            while True:
                chunk = self._resp.read1(CHUNK)
                if not chunk:
                    return
                yield chunk
        finally:
            self.close()


def _forward(path, raw_body, headers, key, base_url, version_default, workspace_id, timeout_s, opener):
    if not isinstance(raw_body, (bytes, bytearray)):
        raise TypeError("raw_body must be bytes")
    raw_body = bytes(raw_body)
    if not key or any(c in key for c in "\r\n\0"):
        raise ValueError("no usable upstream key")
    out = {}
    for k, v in (headers or {}).items():
        if k.lower() in REQUEST_HEADERS and isinstance(v, str):
            out[k.lower()] = v
    out.setdefault("anthropic-version", version_default)
    out["x-api-key"] = key
    if workspace_id:
        out["anthropic-workspace-id"] = str(workspace_id)
    out["content-length"] = str(len(raw_body))
    base = base_url.rstrip("/")
    sent = hashlib.sha256(raw_body).hexdigest()
    try:
        resp, conn = (opener or _default_opener)("POST", base + path, raw_body, out, timeout_s)
    except Exception as e:  # noqa: BLE001 - report the kind only: a message can echo a header
        raise PassthroughError("upstream unreachable: " + type(e).__name__) from None
    return Passthrough(base, key, resp, conn, sent)


def forward_messages(raw_body: bytes, headers: dict, key: str, base_url="https://api.anthropic.com", version_default="2023-06-01",
                     workspace_id=None, timeout_s=300, opener=None):
    """POST the caller's exact bytes to {base_url}/v1/messages and return a Passthrough."""
    return _forward("/v1/messages", raw_body, headers, key, base_url, version_default, workspace_id, timeout_s, opener)


def forward_count_tokens(raw_body: bytes, headers: dict, key: str, base_url="https://api.anthropic.com", version_default="2023-06-01",
                         workspace_id=None, timeout_s=300, opener=None):
    """The same for POST /v1/messages/count_tokens."""
    return _forward("/v1/messages/count_tokens", raw_body, headers, key, base_url, version_default, workspace_id, timeout_s, opener)
