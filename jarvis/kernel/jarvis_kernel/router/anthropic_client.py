"""Anthropic Messages transport for the router (standard library only).

The API key is only ever a call argument: it is never stored on the client and
is scrubbed from every piece of upstream text before it lands in an exception.
Redirects are refused and environment proxies ignored so the key cannot leak.
"""
from __future__ import annotations

import http.client
import json
import re
import urllib.error
import urllib.request
from typing import Iterator

from .translate import (StreamTranslator, Unsupported, UpstreamError,
                        anthropic_to_openai, openai_to_anthropic)

_KEY_LIKE = re.compile(r"sk-ant-[A-Za-z0-9_\-]*")
_MAX_TEXT = 200
MAX_RESPONSE = 32 * 1024 * 1024     # a whole non-streaming reply
MAX_LINE = 1024 * 1024              # one SSE line


class ClientError(ValueError):
    """The request cannot be sent (400-like); nothing went upstream."""
    status = 400


class UpstreamHTTP(RuntimeError):
    def __init__(self, status: int, kind: str = "", retry_after: float | None = None) -> None:
        super().__init__(f"upstream HTTP {status} {kind}".strip())
        self.status = status
        self.kind = kind
        self.retry_after = retry_after


def scrub(text, key: str | None = None, limit: int = _MAX_TEXT) -> str:
    s = text if isinstance(text, str) else str(text)
    if key:
        s = s.replace(key, "[KEY]")
    s = _KEY_LIKE.sub("[KEY]", s)
    s = s[:limit]
    # truncation may have cut a key in half; scrub again
    if key:
        s = s.replace(key, "[KEY]")
    return _KEY_LIKE.sub("[KEY]", s)


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _build_opener() -> urllib.request.OpenerDirector:
    return urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect())


def _retry_after(headers) -> float | None:
    raw = headers.get("retry-after") if headers is not None else None
    try:
        return float(raw) if raw is not None else None
    except (TypeError, ValueError):
        return None


class AnthropicClient:
    def __init__(self, base_url: str = "https://api.anthropic.com", version: str = "2023-06-01",
                 workspace_id: str | None = None, timeout_s: float = 120, opener=None) -> None:
        self.base_url = base_url.rstrip("/")
        self.version = version
        self.workspace_id = workspace_id
        self.timeout_s = timeout_s
        self._opener = opener if opener is not None else _build_opener()

    def __repr__(self) -> str:
        return f"AnthropicClient(base_url={self.base_url!r})"

    __str__ = __repr__

    # -- internals -------------------------------------------------------
    def _request(self, payload: dict, key: str, stream: bool):
        # A key with CR, LF or NUL would make http.client raise ValueError("Invalid header value <the key>"): refuse it first, without the text.
        if not isinstance(key, str) or not key or any(c in key for c in ("\r", "\n", "\0")) or not key.isascii():
            raise ClientError("no usable upstream key")
        headers = {"x-api-key": key, "anthropic-version": self.version,
                   "content-type": "application/json"}
        if stream:
            headers["accept"] = "text/event-stream"
        if self.workspace_id:
            headers["anthropic-workspace-id"] = self.workspace_id
        req = urllib.request.Request(self.base_url + "/v1/messages",
                                     data=json.dumps(payload).encode("utf-8"),
                                     headers=headers, method="POST")
        try:
            return self._opener.open(req, timeout=self.timeout_s)
        except urllib.error.HTTPError as e:
            raise self._http_error(e, key) from None
        except (urllib.error.URLError, OSError, http.client.HTTPException) as e:
            raise UpstreamError("upstream connection failed: " + scrub(type(e).__name__, key)) from None
        except ValueError as e:                                  # any other library complaint may quote a header value: say only the kind
            raise UpstreamError("request could not be sent: " + type(e).__name__) from None

    @staticmethod
    def _http_error(e: urllib.error.HTTPError, key: str) -> UpstreamHTTP:
        kind = ""
        try:
            data = json.loads(e.read(65536).decode("utf-8", "replace"))
            err = data.get("error") if isinstance(data, dict) else None
            t = err.get("type") if isinstance(err, dict) else None
            if isinstance(t, str):
                kind = scrub(t, key)
        except Exception:
            pass
        return UpstreamHTTP(e.code, kind, _retry_after(e.headers))

    def _translate(self, body: dict, drop_sampling: bool, stream: bool) -> dict:
        try:
            payload = openai_to_anthropic(body, drop_sampling=drop_sampling)
        except Unsupported as e:
            raise ClientError(str(e)) from None
        if stream:
            payload["stream"] = True
        else:
            payload.pop("stream", None)
        return payload

    # -- public ----------------------------------------------------------
    def chat(self, body: dict, key: str, drop_sampling: bool = True) -> dict:
        payload = self._translate(body, drop_sampling, False)
        resp = self._request(payload, key, False)
        try:
            with resp:
                raw = resp.read(MAX_RESPONSE + 1)
                if len(raw) > MAX_RESPONSE:
                    raise UpstreamError("upstream response too large")
                data = json.loads(raw.decode("utf-8"))
        except (ValueError, OSError, http.client.HTTPException):
            raise UpstreamError("malformed or interrupted upstream response") from None
        if not isinstance(data, dict):
            raise UpstreamError("malformed upstream response")
        return anthropic_to_openai(data, payload.get("model") or "")

    def chat_stream(self, body: dict, key: str, drop_sampling: bool = True) -> Iterator[str]:
        payload = self._translate(body, drop_sampling, True)
        return self._stream(payload, key)

    def _stream(self, payload: dict, key: str) -> Iterator[str]:
        resp = self._request(payload, key, True)
        tr = StreamTranslator(model=payload.get("model") or "")
        event, data = None, []
        try:
            with resp:
                while True:
                    try:
                        raw = resp.readline(MAX_LINE + 1)
                        if len(raw) > MAX_LINE:
                            raise UpstreamError("upstream stream line too long")
                    except (OSError, http.client.HTTPException):
                        raise UpstreamError("upstream stream interrupted") from None
                    if not raw:
                        break
                    line = raw.decode("utf-8", "replace").rstrip("\r\n")
                    if line == "":
                        if event is not None or data:
                            for chunk in self._dispatch(tr, event, data, key):
                                yield chunk
                                if chunk.startswith("data: [DONE]"):
                                    return
                        event, data = None, []
                    elif line.startswith(":"):
                        continue
                    elif line.startswith("event:"):
                        event = line[6:].strip()
                    elif line.startswith("data:"):
                        data.append(line[5:].lstrip())
                if event is not None or data:
                    for chunk in self._dispatch(tr, event, data, key):
                        yield chunk
                        if chunk.startswith("data: [DONE]"):
                            return
        finally:
            try:
                resp.close()
            except Exception:
                pass
        if not tr.finished:
            raise UpstreamError("upstream stream ended before message_stop")

    @staticmethod
    def _dispatch(tr: StreamTranslator, event, data, key) -> list[str]:
        text = "\n".join(data)
        if event is None:
            try:
                event = json.loads(text).get("type", "")
            except Exception:
                return []
        try:
            return tr.feed(event, text)
        except UpstreamError as e:
            raise UpstreamError(scrub(e, key)) from None
