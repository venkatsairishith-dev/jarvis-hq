"""Privacy gateway: a local OpenAI-compatible proxy that sits in front of a public model service (Dahl).

    python gateway.py [--port 48950] [--upstream https://inference.dahl.global/v1]

Windows or macOS. Run it with D:\\jarvis\\tools\\privacy-venv (Presidio + spaCy). Listens on 127.0.0.1 only.

Every request to /v1/chat/completions (and /v1/completions) is redacted before it leaves the PC: passwords, PINs, keys,
tokens, card numbers, e-mail, phone, Aadhaar, PAN... become placeholders such as [PASSWORD_1]. The reply comes back with
the placeholders still in it and is restored here, so the caller sees the real values while the public service never does.
Streaming replies are restored too, including a placeholder split across chunks.

The Authorization header is passed through untouched and never stored or logged. Logs hold counts by kind, never values.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from redactor import Mapping, Redactor, StreamRestorer, restore  # noqa: E402

DEFAULT_UPSTREAM = os.environ.get("JARVIS_PRIVACY_UPSTREAM", "https://inference.dahl.global/v1")
PASS_HEADERS = ("authorization", "content-type", "accept", "user-agent", "openai-organization", "openai-project")
MAX_BODY = 32 * 1024 * 1024
_lock = threading.Lock()
STATS = {"requests": 0, "redacted": {}, "errors": 0, "started": time.time(), "inflight": 0, "last_call_end": 0.0}


def _count(mapping: Mapping):
    with _lock:
        STATS["requests"] += 1
        for kind, n in mapping.counts.items():
            STATS["redacted"][kind] = STATS["redacted"].get(kind, 0) + n


# ---------- outbound: redact ----------

DEEP_ROLES = ("user", "tool", "function")      # what the owner typed and what tools read: patterns + PII model + Jev check


def redact_content(content, red: Redactor, m: Mapping, deep: bool = True):
    if isinstance(content, str):
        return red.redact(content, m, deep)
    if isinstance(content, list):                               # [{"type":"text","text":...}, {"type":"image_url",...}]
        return [{**p, "text": red.redact(p["text"], m, deep)} if isinstance(p, dict) and isinstance(p.get("text"), str) else p
                for p in content]
    return content


def redact_body(body: dict, red: Redactor, m: Mapping) -> dict:
    body = dict(body)
    if isinstance(body.get("messages"), list):
        msgs = []
        for msg in body["messages"]:
            if not isinstance(msg, dict):
                msgs.append(msg)
                continue
            msg = dict(msg)
            if "content" in msg:
                msg["content"] = redact_content(msg["content"], red, m, deep=msg.get("role") in DEEP_ROLES)
            if isinstance(msg.get("tool_calls"), list):
                calls = []
                for c in msg["tool_calls"]:
                    fn = (c or {}).get("function") or {}
                    if isinstance(fn.get("arguments"), str):
                        c = {**c, "function": {**fn, "arguments": red.redact(fn["arguments"], m)}}
                    calls.append(c)
                msg["tool_calls"] = calls
            msgs.append(msg)
        body["messages"] = msgs
    if isinstance(body.get("prompt"), str):
        body["prompt"] = red.redact(body["prompt"], m)
    return body


# ---------- inbound: restore ----------

def restore_json(obj, m: Mapping):
    """Restore every string in a non-streamed reply (message content, tool-call arguments, reasoning fields)."""
    if isinstance(obj, str):
        return restore(obj, m)
    if isinstance(obj, list):
        return [restore_json(x, m) for x in obj]
    if isinstance(obj, dict):
        return {k: restore_json(v, m) for k, v in obj.items()}
    return obj


class SseRestorer:
    """Restores placeholders inside streamed chat deltas. One StreamRestorer per (choice, field)."""

    FIELDS = ("content", "reasoning_content", "reasoning")

    def __init__(self, m: Mapping):
        self.m, self.parts = m, {}

    def _r(self, key):
        if key not in self.parts:
            self.parts[key] = StreamRestorer(self.m)
        return self.parts[key]

    def event(self, data: str) -> str:
        if data.strip() == "[DONE]" or not self.m.to_value:
            return data
        try:
            ev = json.loads(data)
        except ValueError:
            return restore(data, self.m)
        for ch in ev.get("choices") or []:
            i = ch.get("index", 0)
            d = ch.get("delta") or {}
            for f in self.FIELDS:
                if isinstance(d.get(f), str):
                    d[f] = self._r((i, f)).feed(d[f])
            for tc in d.get("tool_calls") or []:
                fn = tc.get("function") or {}
                if isinstance(fn.get("arguments"), str):
                    fn["arguments"] = self._r((i, "tool", tc.get("index", 0))).feed(fn["arguments"])
            if ch.get("finish_reason"):                          # last chunk of this choice: release anything held
                for key, r in self.parts.items():
                    if key[0] != i:
                        continue
                    rest = r.flush()
                    if not rest:
                        continue
                    if key[1] == "tool":
                        calls = d.setdefault("tool_calls", [])
                        tc = next((t for t in calls if t.get("index", 0) == key[2]), None)
                        if tc is None:
                            tc = {"index": key[2], "function": {"arguments": ""}}
                            calls.append(tc)
                        fn = tc.setdefault("function", {})
                        fn["arguments"] = (fn.get("arguments") or "") + rest
                    else:
                        d[key[1]] = (d.get(key[1]) or "") + rest
                ch["delta"] = d
            if "message" in ch:
                ch["message"] = restore_json(ch["message"], self.m)
        return json.dumps(ev, ensure_ascii=False)

    def leftover(self) -> str:
        return "".join(r.flush() for r in self.parts.values())


# ---------- HTTP ----------

def make_handler(red: Redactor, upstream: str, timeout: float):
    upstream = upstream.rstrip("/")

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, fmt, *args):                     # never echo bodies or headers
            sys.stderr.write("%s %s\n" % (time.strftime("%H:%M:%S"), fmt % args))

        def _json(self, code, obj):
            raw = json.dumps(obj).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def _path(self):
            p = self.path.split("?", 1)[0]
            return p[3:] if p.startswith("/v1") else p

        def do_GET(self):
            if self.path in ("/health", "/v1/health"):
                with _lock:
                    last = STATS["last_call_end"]
                    return self._json(200, {"ok": True, "upstream": upstream, "requests": STATS["requests"],
                                            "redacted": dict(STATS["redacted"]), "errors": STATS["errors"],
                                            "pii_model": red.model is not None, "checks": dict(red.stats),
                                            "inflight": STATS["inflight"],          # model calls waiting right now (the icon's "thinking")
                                            "last_call_age_s": round(time.time() - last, 1) if last else None})
            self._forward(None)

        def do_POST(self):
            n = int(self.headers.get("Content-Length") or 0)
            if n > MAX_BODY:
                return self._json(413, {"error": {"message": "request too large"}})
            raw = self.rfile.read(n) if n else b""
            if self.path == "/redact":                         # local preview: what the public service would receive
                try:
                    text = json.loads(raw).get("text", "")
                except (ValueError, AttributeError):
                    return self._json(400, {"error": {"message": "send {\"text\": ...}"}})
                m = Mapping()
                return self._json(200, {"text": red.redact(str(text), m), "kinds": m.counts})
            self._forward(raw)

        def _forward(self, raw):
            with _lock:
                STATS["inflight"] += 1
            try:
                return self._forward_inner(raw)
            finally:
                with _lock:
                    STATS["inflight"] -= 1
                    STATS["last_call_end"] = time.time()

        def _forward_inner(self, raw):
            m = Mapping()
            body, stream = None, False
            if raw:
                try:
                    body = json.loads(raw)
                except ValueError:
                    return self._json(400, {"error": {"message": "the privacy gateway only forwards JSON"}})
                if self._path() in ("/chat/completions", "/completions"):
                    body = redact_body(body, red, m)
                    _count(m)
                stream = bool(body.get("stream"))
                raw = json.dumps(body, ensure_ascii=False).encode("utf-8")
            headers = {k: v for k, v in self.headers.items() if k.lower() in PASS_HEADERS}
            if raw is not None:
                headers["Content-Type"] = "application/json"
            req = urllib.request.Request(upstream + self._path(), data=raw, headers=headers,
                                         method="POST" if raw is not None else "GET")
            try:
                resp = urllib.request.urlopen(req, timeout=timeout)
            except urllib.error.HTTPError as e:
                payload = e.read()
                self.send_response(e.code)
                self.send_header("Content-Type", e.headers.get("Content-Type", "application/json"))
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)
                return
            except Exception as e:                               # noqa: BLE001 - report kind only
                with _lock:
                    STATS["errors"] += 1
                return self._json(502, {"error": {"message": f"upstream unreachable: {type(e).__name__}"}})
            with resp:
                ctype = resp.headers.get("Content-Type", "")
                if stream or "text/event-stream" in ctype:
                    self._relay_stream(resp, m)
                else:
                    data = resp.read()
                    if m.to_value and "json" in ctype:
                        try:
                            data = json.dumps(restore_json(json.loads(data), m), ensure_ascii=False).encode("utf-8")
                        except ValueError:
                            pass
                    self.send_response(resp.status)
                    self.send_header("Content-Type", ctype or "application/json")
                    self.send_header("Content-Length", str(len(data)))
                    self.end_headers()
                    self.wfile.write(data)

        def _relay_stream(self, resp, m):
            self.send_response(resp.status)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "close")
            self.end_headers()
            self.close_connection = True
            sse = SseRestorer(m)
            try:
                for line in resp:
                    text = line.decode("utf-8", "replace")
                    if text.startswith("data:"):
                        text = "data: " + sse.event(text[5:].strip()) + "\n"
                    self.wfile.write(text.encode("utf-8"))
                    self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError):
                return

    return Handler


def serve(port: int, upstream: str, timeout: float = 300.0, red: Redactor | None = None):
    srv = ThreadingHTTPServer(("127.0.0.1", port), make_handler(red or Redactor(), upstream, timeout))
    srv.daemon_threads = True
    return srv


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=int(os.environ.get("JARVIS_PRIVACY_PORT", "48950")))
    ap.add_argument("--upstream", default=DEFAULT_UPSTREAM)
    a = ap.parse_args()
    srv = serve(a.port, a.upstream)
    print(f"privacy gateway on http://127.0.0.1:{a.port}/v1 -> {a.upstream}", flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    main()
