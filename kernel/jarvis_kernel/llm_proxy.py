"""OpenAI-compatible model endpoint for the agent runtime, so the agent NEVER holds a provider key.

Listens on 127.0.0.1 only. The agent authenticates with its own bearer token (hash stored in the kernel DB, created by
`jarvis agent-token`). Each request goes through the existing gateway rules: private lane only, free-model pattern,
provider priority and fallback (fallback only before any byte has been streamed back), secret scrubbing of every message
and tool-call argument, an output-token cap, a rate limit, and an audit entry with counts only. The provider's response,
including tool calls and streamed chunks, is relayed unchanged.
"""
import hashlib
import hmac
import json
import secrets
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import gateway
from .scrub import scrub

MAX_BODY = 2 * 1024 * 1024
MAX_OUT_TOKENS = 4096
PER_MINUTE = 30
PER_DAY = 2000


def _h(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()


class AgentTokens:
    def __init__(self, kernel):
        self.k = kernel
        kernel.db.execute("CREATE TABLE IF NOT EXISTS agent_tokens(name TEXT PRIMARY KEY, token_h TEXT, created REAL)")

    def issue(self, name: str) -> str:
        tok = secrets.token_urlsafe(32)
        self.k.db.execute("INSERT OR REPLACE INTO agent_tokens VALUES (?,?,?)", (name, _h(tok), time.time()))
        self.k.audit.append("agent_token_issued", {"name": name})
        return tok

    def identify(self, tok: str):
        th = _h(tok)
        for name, h in self.k.db.execute("SELECT name, token_h FROM agent_tokens"):
            if hmac.compare_digest(h, th):
                return name
        return None


def _scrub_messages(body: dict):
    """Scrub every text the agent sends to a model. Returns (body, findings, blocked)."""
    found, blocked = [], False

    def clean(s):
        nonlocal blocked
        c, f, b = scrub(s, deep=True)
        found.extend(f)
        blocked = blocked or b
        return c

    for m in body.get("messages") or []:
        if isinstance(m.get("content"), str):
            m["content"] = clean(m["content"])
        elif isinstance(m.get("content"), list):
            for part in m["content"]:
                if isinstance(part, dict) and isinstance(part.get("text"), str):
                    part["text"] = clean(part["text"])
        for tc in m.get("tool_calls") or []:
            fn = tc.get("function") or {}
            if isinstance(fn.get("arguments"), str):
                fn["arguments"] = clean(fn["arguments"])
    return body, found, blocked


def make_handler(kernel, providers, clock=time.time):
    tokens = AgentTokens(kernel)
    lock = threading.Lock()
    calls = {}   # agent -> list of timestamps

    class H(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.0"   # the connection closes after each response: a stream ends when the socket closes

        def log_message(self, *a):
            pass

        def _json(self, code, obj):
            b = json.dumps(obj).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(b)))
            self.end_headers()
            self.wfile.write(b)

        def _agent(self):
            auth = self.headers.get("Authorization", "")
            return tokens.identify(auth[7:]) if auth.startswith("Bearer ") else None

        def do_GET(self):
            if self.path.rstrip("/") == "/v1/models":
                if not self._agent():
                    return self._json(401, {"error": {"message": "unauthorized"}})
                return self._json(200, {"object": "list", "data": [{"id": "jarvis", "object": "model", "owned_by": "jarvis"}]})
            self._json(404, {"error": {"message": "not found"}})

        def do_POST(self):
            agent = self._agent()
            if not agent:
                kernel.audit.append("agent_model_refused", {"why": "bad token"})
                return self._json(401, {"error": {"message": "unauthorized"}})
            if self.path.rstrip("/") != "/v1/chat/completions":
                return self._json(404, {"error": {"message": "not found"}})
            if kernel.killed():
                return self._json(503, {"error": {"message": "kill switch is on"}})
            now = clock()
            with lock:
                ts = [t for t in calls.get(agent, []) if now - t < 86400]
                if len([t for t in ts if now - t < 60]) >= PER_MINUTE or len(ts) >= PER_DAY:
                    kernel.audit.append("agent_model_refused", {"agent": agent, "why": "rate limit"})
                    return self._json(429, {"error": {"message": "rate limit"}})
                ts.append(now)
                calls[agent] = ts
            try:
                n = int(self.headers.get("Content-Length", "0"))
                if n <= 0 or n > MAX_BODY:
                    return self._json(413, {"error": {"message": "bad size"}})
                body = json.loads(self.rfile.read(n))
            except (ValueError, OSError):
                return self._json(400, {"error": {"message": "bad request"}})
            body, findings, blocked = _scrub_messages(body)
            if blocked:
                kernel.audit.append("agent_model_refused", {"agent": agent, "why": "private key in prompt"})
                return self._json(400, {"error": {"message": "refused: the request contains a private key"}})
            for k in ("max_tokens", "max_completion_tokens"):
                if k in body:
                    body[k] = min(int(body[k] or MAX_OUT_TOKENS), MAX_OUT_TOKENS)
            if "max_tokens" not in body and "max_completion_tokens" not in body:
                body["max_tokens"] = MAX_OUT_TOKENS
            stream = bool(body.get("stream"))
            try:
                chain = gateway.plan(providers, "private")     # the agent works on the owner's data: private lane only
            except gateway.RouteError as e:
                return self._json(503, {"error": {"message": str(e)}})
            last = "none"
            for p in chain:
                model = p.default_model
                if not p.model_ok(model):
                    continue
                payload = dict(body, model=model)
                payload.update(p.extra_body)
                headers = {"Content-Type": "application/json", "User-Agent": "jarvis-llm-proxy/0.1"}
                if p.key_file:
                    with open(p.key_path(), encoding="utf-8-sig") as fh:
                        headers["Authorization"] = "Bearer " + fh.read().strip()
                req = urllib.request.Request(p.base_url.rstrip("/") + "/chat/completions", data=json.dumps(payload).encode(), headers=headers, method="POST")
                try:
                    resp = urllib.request.urlopen(req, timeout=p.timeout_s or 120)
                except urllib.error.HTTPError as e:
                    last = f"HTTP {e.code}"
                    kernel.audit.append("agent_model_call_failed", {"agent": agent, "provider": p.name, "error": last})
                    continue
                except Exception as e:
                    last = type(e).__name__
                    kernel.audit.append("agent_model_call_failed", {"agent": agent, "provider": p.name, "error": last})
                    continue
                with resp:   # from here on we are committed to this provider
                    self.send_response(200)
                    self.send_header("Content-Type", resp.headers.get("Content-Type", "application/json"))
                    if stream:
                        self.send_header("Cache-Control", "no-cache")
                    self.end_headers()
                    sent = 0
                    while True:
                        chunk = resp.read1(65536) if hasattr(resp, "read1") else resp.read(65536)
                        if not chunk:
                            break
                        self.wfile.write(chunk)
                        self.wfile.flush()
                        sent += len(chunk)
                kernel.audit.append("agent_model_call", {"agent": agent, "provider": p.name, "stream": stream, "bytes_out": sent,
                                                         "messages": len(body.get("messages") or []), "tools": len(body.get("tools") or []), "scrubbed": findings})
                return
            self._json(502, {"error": {"message": f"all providers failed ({last})"}})

    return H, tokens


def serve(kernel, providers, port=8790):
    H, _ = make_handler(kernel, providers)
    ThreadingHTTPServer(("127.0.0.1", port), H).serve_forever()
