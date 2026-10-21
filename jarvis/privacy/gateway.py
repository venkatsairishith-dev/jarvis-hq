"""Privacy gateway: a local OpenAI-compatible proxy that sits in front of a public model service (Dahl).

    python gateway.py [--port 48950] [--upstream https://inference.dahl.global/v1]

Windows or macOS. Run it with D:\\jarvis\\tools\\privacy-venv (Presidio + spaCy). Listens on 127.0.0.1 only.

Every request to /v1/chat/completions (and /v1/completions) is redacted before it leaves the PC: passwords, PINs, keys,
tokens, card numbers, e-mail, phone, Aadhaar, PAN... become placeholders such as [PASSWORD_1]. Every message role gets the
same full pass (system prompts carry recalled memory). Images, audio and files are never forwarded: each becomes a short
"withheld for privacy" note. A POST to any other endpoint is refused, because it would not be redacted. The reply comes
back with the placeholders still in it and is restored here, so the caller sees the real values while the public service
never does. Streaming replies are restored too, including a placeholder split across chunks.

Whitelist (audit M2, 2026-10-18): only the fields below leave the PC. Every string in messages (content, name, tool_call_id,
tool_calls and function_call names and arguments, reasoning, refusal), tools, functions, tool_choice, response_format, stop,
prediction, suffix and prompt is redacted, parts of a message other than type+text are dropped, plain numbers and flags are
shape-checked, logit_bias keys must be token numbers, and everything else (user, metadata, store, a field nobody has seen yet)
is dropped and counted. A request the gateway cannot check (an unknown role, token-array prompts, nesting beyond 24 levels)
is refused with 400, not forwarded.

Fail closed (audit M3): with the PII model (GLiNER-PII) missing, serve()/main() refuse every model call with 503 and a plain
message, and /health says ready=false, public_egress=false, degraded=["pii_model_missing"]. A detector error also means 503 and
nothing is forwarded, and no request text is ever put in a log or an error. Set JARVIS_PRIVACY_REQUIRE_DEEP=0 to accept the
pattern-only pass (the owner's choice; /health still lists it). POST /redact {"text":..., "names": true} also hides people and
places for that call and answers with what was really active ("names", "deep", "ready").

The Authorization header is passed through untouched and never stored or logged. Logs hold counts by kind, never values.
"""
from __future__ import annotations

import argparse
import json
import os
import re
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

# Every role gets the full pass (patterns + PII model + Jev check), not only user/tool/function (audit MEM-M3, 2026-10-18):
# N.E.K.O puts recalled memory in the SYSTEM prompt and resends earlier ASSISTANT replies, and the memory server's own
# summary, correction and fact-extraction calls send the owner's conversation under whatever role they like. Placeholders
# in replies are restored as before. The redactor caches by sentence, so only new sentences cost model time.

# Anything that is not text is never forwarded to the public model (audit PRO-D9): an image, audio or file part cannot be
# redacted, and a screenshot or a photo of a document can hold anything. It is replaced by a short note so the
# conversation keeps working and the model can say it could not see it.
WITHHELD = {"image_url": "an image", "input_image": "an image", "image": "an image", "input_audio": "an audio clip",
            "audio": "an audio clip", "file": "a file", "input_file": "a file", "video_url": "a video"}


def _withheld_note(kind: str) -> dict:
    with _lock:
        STATS["withheld"] = STATS.get("withheld", 0) + 1
    return {"type": "text", "text": f"[{kind} was withheld for privacy: the privacy gateway never sends it to a public model. "
                                    "Tell the user you could not see it.]"}


class GatewayRefusal(Exception):
    """A request the gateway will not forward. The message is safe to show: it never repeats any part of the request."""


MAX_DEPTH = 24                                      # a deeper JSON value is refused, not walked (recursion bound)
ROLES = {"system", "user", "assistant", "tool", "function", "developer"}
MODEL_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/@+-]{0,127}")
LOGIT_KEY = re.compile(r"-?\d{1,12}")
# Fields that carry no text: forwarded as they are, only when they have the plain shape below.
SCALARS = {"max_tokens": (int,), "max_completion_tokens": (int,), "temperature": (int, float), "top_p": (int, float),
           "n": (int,), "seed": (int,), "presence_penalty": (int, float), "frequency_penalty": (int, float),
           "logprobs": (bool,), "top_logprobs": (int,), "parallel_tool_calls": (bool,), "stream": (bool,), "echo": (bool,),
           "best_of": (int,)}
# Provider switches N.E.K.O adds through extra_body (config/providers.py: enable_thinking, thinking.type, thinking_budget,
# reasoning_effort, chat_template_kwargs). They hold no free text, so they go on only in this exact shape (booleans, integers,
# and a fixed set of words); anything else under these names is refused.
THINK_WORDS = {"disabled", "enabled", "auto", "between_tools", "none", "minimal", "low", "medium", "high"}
CONTROL_KEY = re.compile(r"[a-z_]{1,40}")


def _control_ok(key: str, v) -> bool:
    if key in ("enable_thinking",):
        return isinstance(v, bool)
    if key == "thinking_budget":
        return isinstance(v, int) and not isinstance(v, bool)
    if key == "reasoning_effort":
        return isinstance(v, str) and v in THINK_WORDS
    if key == "thinking":
        return isinstance(v, dict) and all(k in ("type", "budget_tokens") and ((k == "type" and v[k] in THINK_WORDS) or
                                            (k == "budget_tokens" and isinstance(v[k], int) and not isinstance(v[k], bool))) for k in v)
    if key == "chat_template_kwargs":
        return isinstance(v, dict) and all(isinstance(k, str) and CONTROL_KEY.fullmatch(k) and isinstance(x, (bool, int)) for k, x in v.items())
    return False


CONTROLS = ("enable_thinking", "thinking_budget", "reasoning_effort", "thinking", "chat_template_kwargs")

# Fields whose strings are redacted (deep) wherever they sit in the JSON value. Keys stay (they are identifiers).
TEXT_FIELDS = {"tools", "functions", "function_call", "tool_choice", "response_format", "stop", "prediction", "suffix",
               "verbosity"}
# A string under one of these keys is a fixed word of the API, not the owner's text: redacting it could only break the call.
SCHEMA_WORDS = {"type", "$schema", "format", "$ref", "strict", "role"}
# Anything else at the top level (user, metadata, store, service_tier, audio, modalities, web_search_options, a field a
# future client invents...) is not forwarded: unknown means it was never checked (audit M2, 2026-10-18).
MESSAGE_TEXT_KEYS = ("name", "tool_call_id", "reasoning_content", "reasoning", "refusal", "reasoning_details", "function_call")


def _kept(red, text: str, m: Mapping, deep: bool = True) -> str:
    return red.redact(text, m, deep)


def _scalar_ok(v, types) -> bool:
    if isinstance(v, bool):                                     # True is an int in Python: a flag only where a flag is expected
        return bool in types
    return isinstance(v, tuple(t for t in types if t is not bool))


PLAIN_KEY = re.compile(r"[a-z][a-z0-9_]{0,40}")


def _key(k, red, m: Mapping):
    """A JSON object key. Plain lower-case schema words (type, properties, description, city_name ...) stay as they are; any other
    key (capitals, spaces, dashes, digits first, Indic script, a long value used as a name) is text a caller may have built from
    private data, so it gets the same deep pass as a value. The reply's placeholders are restored, keys in tool-call arguments too."""
    if not isinstance(k, str):
        raise GatewayRefusal("the request holds a value the gateway cannot check, so it was not forwarded")
    return k if PLAIN_KEY.fullmatch(k) else _kept(red, k, m)


def _tree(value, red, m: Mapping, depth: int = 0, key: str = ""):
    """Every string inside a JSON value redacted (deep); numbers, booleans and null kept; keys kept."""
    if depth > MAX_DEPTH:
        raise GatewayRefusal("the request is nested too deeply to be checked, so it was not forwarded")
    if isinstance(value, str):
        return value if key in SCHEMA_WORDS else _kept(red, value, m)
    if isinstance(value, list):
        return [_tree(v, red, m, depth + 1, key) for v in value]
    if isinstance(value, dict):
        return {_key(k, red, m): _tree(v, red, m, depth + 1, k if isinstance(k, str) else "") for k, v in value.items()}
    if value is None or isinstance(value, (bool, int, float)):
        return value
    raise GatewayRefusal("the request holds a value the gateway cannot check, so it was not forwarded")


def redact_content(content, red: Redactor, m: Mapping, deep: bool = True):
    if content is None:
        return None
    if isinstance(content, str):
        return red.redact(content, m, deep)
    if isinstance(content, list):                               # [{"type":"text","text":...}, {"type":"image_url",...}]
        out = []
        for p in content:
            if isinstance(p, str):
                out.append(red.redact(p, m, deep))
            elif isinstance(p, dict) and isinstance(p.get("text"), str) and p.get("type", "text") in ("text", "input_text"):
                # only type and text go on: any other key of a text part (a note, a cache hint, a future field) is unchecked text
                out.append({"type": p.get("type", "text"), "text": red.redact(p["text"], m, deep)})
            else:                                               # image, audio, file or a shape we do not know: fail closed
                kind = p.get("type") if isinstance(p, dict) else None
                out.append(_withheld_note(WITHHELD.get(kind, "an attachment")))
        return out
    return _withheld_note("content in an unknown format")["text"]


def _redact_message(msg, red: Redactor, m: Mapping):
    if isinstance(msg, str):
        return {"role": "user", "content": red.redact(msg, m, True)}
    if not isinstance(msg, dict):
        return {"role": "user", "content": _withheld_note("a message in an unknown format")["text"]}
    if msg.get("role") not in ROLES:
        raise GatewayRefusal("a message has an unknown role, so the request was not forwarded")
    out = {"role": msg["role"]}
    if "content" in msg:
        out["content"] = redact_content(msg["content"], red, m, deep=True)
    for k in MESSAGE_TEXT_KEYS:                                 # name, tool_call_id, reasoning text, refusal, legacy function_call
        if k in msg and msg[k] is not None:
            out[k] = _tree(msg[k], red, m, key=k)
    if isinstance(msg.get("tool_calls"), list):
        calls = []
        for c in msg["tool_calls"]:
            if not isinstance(c, dict):
                raise GatewayRefusal("a tool call has an unknown shape, so the request was not forwarded")
            fn = c.get("function")
            calls.append({"id": _tree(c.get("id"), red, m), "type": "function",
                          "function": _tree(fn if isinstance(fn, dict) else {}, red, m, key="function")})
        out["tool_calls"] = calls
    # anything else on the message (an audio reply, annotations, a field not known here) is not forwarded
    return out


def redact_body(body: dict, red: Redactor, m: Mapping) -> dict:
    """The body that may leave the PC: a WHITELIST. Every text-bearing field is redacted (deep, every role alike), the
    plain numbers and flags are checked for their shape, and everything else is dropped (names are counted in STATS)."""
    if not isinstance(body, dict):
        raise GatewayRefusal("the request must be a JSON object")
    out, dropped = {}, []
    for k, v in body.items():
        if k == "model":
            if not isinstance(v, str) or not MODEL_NAME.fullmatch(v):
                raise GatewayRefusal("the model name is not a plain model name, so the request was not forwarded")
            out[k] = v
        elif k == "messages":
            if not isinstance(v, list):
                raise GatewayRefusal("messages must be a list")
            out[k] = [_redact_message(x, red, m) for x in v]
        elif k == "prompt":
            if isinstance(v, str):
                out[k] = red.redact(v, m)
            elif isinstance(v, list) and all(isinstance(x, str) for x in v):    # the completions API takes a list of prompts
                out[k] = [red.redact(x, m) for x in v]
            else:                                               # token arrays cannot be read, so cannot be checked
                raise GatewayRefusal("this prompt format cannot be checked for private values, so it was not forwarded")
        elif k == "logit_bias":
            if not isinstance(v, dict) or not all(isinstance(a, str) and LOGIT_KEY.fullmatch(a) and isinstance(b, (int, float))
                                                  for a, b in v.items()):
                raise GatewayRefusal("logit_bias must map token numbers to numbers")
            out[k] = v
        elif k in CONTROLS:
            if not _control_ok(k, v):
                raise GatewayRefusal(f"{k} has an unexpected value, so the request was not forwarded")
            out[k] = v
        elif k in TEXT_FIELDS:
            out[k] = _tree(v, red, m, key=k)
        elif k == "stream_options":
            out[k] = {a: b for a, b in v.items() if a == "include_usage" and isinstance(b, bool)} if isinstance(v, dict) else {}
        elif k in SCALARS:
            if not _scalar_ok(v, SCALARS[k]):
                raise GatewayRefusal(f"{k} has an unexpected type, so the request was not forwarded")
            out[k] = v
        else:
            dropped.append(str(k)[:30])
    if dropped:
        with _lock:
            STATS["dropped_fields"] = STATS.get("dropped_fields", 0) + len(dropped)
    return out


# ---------- inbound: restore ----------

def restore_json(obj, m: Mapping):
    """Restore every string in a non-streamed reply (message content, tool-call arguments, reasoning fields)."""
    if isinstance(obj, str):
        return restore(obj, m)
    if isinstance(obj, list):
        return [restore_json(x, m) for x in obj]
    if isinstance(obj, dict):
        return {(restore(k, m) if isinstance(k, str) else k): restore_json(v, m) for k, v in obj.items()}
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


# ---------- providers (ordered fallback) ----------

PROVIDERS_FILE = os.environ.get("JARVIS_PRIVACY_PROVIDERS", os.path.join(os.path.dirname(os.path.abspath(__file__)), "providers.json"))
SECRET_ROOT = os.environ.get("NEKO_SECRET_REF_ROOTS", r"D:\jarvis-secrets").split(os.pathsep)[0]
RETRY_CODES = (429, 500, 502, 503, 504, 520, 522, 524)
_cooldown: dict[str, float] = {}
_keys: dict[str, str] = {}


def load_providers(path: str = PROVIDERS_FILE, upstream: str | None = None) -> tuple[list[dict], float]:
    """Enabled providers in order. The first always uses the caller's key and model; --upstream replaces its address."""
    try:
        with open(path, encoding="utf-8") as f:
            cfg = json.load(f)
        provs = [p for p in cfg.get("providers") or [] if p.get("enabled")]
        cool = float(cfg.get("cooldown_seconds", 60))
    except (OSError, ValueError):
        provs, cool = [], 60.0
    if not provs:
        provs = [{"name": "upstream", "base_url": DEFAULT_UPSTREAM, "key": "from_request"}]
    if upstream:
        provs[0] = {**provs[0], "base_url": upstream}
    usable = [provs[0]] + [p for p in provs[1:] if p.get("key_file") and p.get("model")]
    return [{**p, "base_url": p["base_url"].rstrip("/")} for p in usable], cool


def read_key(p: dict) -> str:
    """A fallback provider's key from its file under the secrets folder (never logged, kept only in memory)."""
    if p["name"] in _keys:
        return _keys[p["name"]]
    root = os.path.realpath(SECRET_ROOT)
    path = os.path.realpath(os.path.join(root, p.get("key_file") or ""))
    if not path.startswith(root + os.sep) or not os.path.isfile(path):
        return ""
    with open(path, encoding="utf-8") as f:
        key = f.read().strip()
    _keys[p["name"]] = key
    return key


_BEARER = re.compile(rb"(?i)(bearer\s+)[A-Za-z0-9._~+/=:-]{6,}")


def scrub_keys(payload: bytes, keys) -> bytes:
    """An error page relayed to the caller never carries a provider key: a careless proxy may echo the Authorization header."""
    for k in sorted({str(k) for k in keys if k and len(str(k)) >= 6}, key=len, reverse=True):
        payload = payload.replace(k.encode("utf-8"), b"[REDACTED_KEY]")
    return _BEARER.sub(rb"\1[REDACTED_KEY]", payload)


# ---------- HTTP ----------

def degraded_reasons(red, require_deep: bool) -> list[str]:
    """Why this gateway must not send anything to a public model right now ([] = ready). Names only, never values.
    The PII model (GLiNER-PII) is what finds secrets written without a cue word; without it the patterns alone ran on every
    message and nothing said so (audit M3, 2026-10-18). Now the gateway refuses to forward instead."""
    out = []
    if require_deep and getattr(red, "model", None) is None:
        out.append("pii_model_missing")
    return out


def make_handler(red: Redactor, upstream: str | list, timeout: float, cooldown: float = 60.0, require_deep: bool = False):
    """require_deep: refuse (503) every model call while the PII model is not loaded. `serve()` and `main()` turn it on
    unless the owner sets JARVIS_PRIVACY_REQUIRE_DEEP=0; it is off here only so a test or a bench can drive the handler
    with a stand-in detector that has no model."""
    providers = upstream if isinstance(upstream, list) else [{"name": "upstream", "base_url": upstream.rstrip("/"), "key": "from_request"}]
    upstream = providers[0]["base_url"]

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
                why = degraded_reasons(red, require_deep)
                with _lock:
                    last = STATS["last_call_end"]
                    return self._json(200, {"ok": True, "ready": not why, "degraded": why,   # ready=false: no model call is forwarded
                                            "public_egress": not why, "deep_required": require_deep,
                                            "names": bool(getattr(red, "names", False)), "upstream": upstream, "requests": STATS["requests"],
                                            "redacted": dict(STATS["redacted"]), "errors": STATS["errors"],
                                            "pii_model": getattr(red, "model", None) is not None, "checks": dict(getattr(red, "stats", {})),
                                            "withheld": STATS.get("withheld", 0),   # images/audio/files never forwarded
                                            "refused": STATS.get("refused", 0), "dropped_fields": STATS.get("dropped_fields", 0),
                                            "inflight": STATS["inflight"],          # model calls waiting right now (the icon's "thinking")
                                            "last_call_age_s": round(time.time() - last, 1) if last else None,
                                            "providers": [p["name"] for p in providers],
                                            "answered_by": dict(STATS.get("by_provider", {})),
                                            "cooling_down": [n for n, t in _cooldown.items() if t > time.time()]})
            self._forward(None)

        def do_POST(self):
            n = int(self.headers.get("Content-Length") or 0)
            if n > MAX_BODY:
                return self._json(413, {"error": {"message": "request too large"}})
            raw = self.rfile.read(n) if n else b""
            if self.path == "/redact":                         # local preview: what the public service would receive
                try:
                    req = json.loads(raw)
                    text = req.get("text", "")
                except (ValueError, AttributeError):
                    return self._json(400, {"error": {"message": "send {\"text\": ...}"}})
                # "names": true also hides people and places for this call (the research plugin asks for it); the answer says
                # what was really active, so the caller can refuse a preview that was weaker than it needed.
                want_names = isinstance(req, dict) and req.get("names") is True
                m = Mapping()
                try:
                    out = red.redact(str(text), m, True, names=True) if want_names else red.redact(str(text), m)
                except Exception:                                   # noqa: BLE001 - never echo the text or the error
                    with _lock:
                        STATS["errors"] += 1
                    return self._json(503, {"error": {"message": "the privacy gateway could not check the text"}})
                return self._json(200, {"text": out, "kinds": m.counts, "deep": getattr(red, "model", None) is not None,
                                        "names": want_names or bool(getattr(red, "names", False)),
                                        "ready": not degraded_reasons(red, require_deep)})
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
                    why = degraded_reasons(red, require_deep)
                    if why:                                    # fail closed and say so; the request is not even read further
                        with _lock:
                            STATS["refused"] = STATS.get("refused", 0) + 1
                        return self._json(503, {"error": {"message": "the privacy gateway is degraded (" + ", ".join(why) + "), so "
                                                                     "nothing was sent to the public model. Restore the PII model or "
                                                                     "use a local model."}})
                    try:
                        body = redact_body(body, red, m)
                    except GatewayRefusal as e:
                        with _lock:
                            STATS["refused"] = STATS.get("refused", 0) + 1
                        return self._json(400, {"error": {"message": str(e)}})
                    except Exception:                          # noqa: BLE001 - a detector failure: nothing leaves, nothing is echoed
                        with _lock:
                            STATS["errors"] += 1
                            STATS["refused"] = STATS.get("refused", 0) + 1
                        return self._json(503, {"error": {"message": "the privacy gateway could not check the request, so nothing "
                                                                     "was sent to the public model"}})
                    _count(m)
                else:                                          # fail closed: only calls it knows how to redact leave the PC
                    return self._json(403, {"error": {"message": "the privacy gateway forwards only chat and completion calls to "
                                                                 "the public model; this endpoint is not redacted, so it is refused"}})
                stream = bool(body.get("stream"))
                raw = json.dumps(body, ensure_ascii=False).encode("utf-8")
            headers = {k: v for k, v in self.headers.items() if k.lower() in PASS_HEADERS}
            if raw is not None:
                headers["Content-Type"] = "application/json"
            # Fallback only for model calls; the first provider always gets the caller's own key and model.
            chain = providers if (raw is not None and self._path() in ("/chat/completions", "/completions")) else providers[:1]
            now = time.time()
            chain = [chain[0]] + [p for p in chain[1:] if _cooldown.get(p["name"], 0) <= now]
            tried = []
            for i, prov in enumerate(chain):
                last = i == len(chain) - 1
                hdrs, data = dict(headers), raw
                if i > 0:
                    key = read_key(prov)
                    if not key:
                        tried.append(f"{prov['name']}: no key file")
                        continue
                    hdrs["Authorization"] = f"Bearer {key}"
                    # Groq's Cloudflare front refuses Python's default user agent (error 1010, seen live); a fallback
                    # must not depend on what the caller happened to send.
                    hdrs["User-Agent"] = "neko-privacy-gateway/1.0"
                    data = json.dumps({**body, "model": prov["model"]}, ensure_ascii=False).encode("utf-8")
                req = urllib.request.Request(prov["base_url"] + self._path(), data=data, headers=hdrs,
                                             method="POST" if data is not None else "GET")
                try:
                    resp = urllib.request.urlopen(req, timeout=timeout)
                except urllib.error.HTTPError as e:
                    if e.code == 429:
                        _cooldown[prov["name"]] = time.time() + cooldown
                    if e.code in RETRY_CODES and not last:
                        tried.append(f"{prov['name']}: HTTP {e.code}")
                        e.close()
                        continue
                    used = [read_key(q) for q in chain[1:i + 1] if q.get("key_file")]   # every fallback key read so far
                    payload = scrub_keys(e.read(), used)
                    self.send_response(e.code)
                    self.send_header("Content-Type", e.headers.get("Content-Type", "application/json"))
                    self.send_header("Content-Length", str(len(payload)))
                    self.send_header("X-Neko-Provider", prov["name"])
                    self.end_headers()
                    self.wfile.write(payload)
                    return
                except Exception as e:                           # noqa: BLE001 - report kind only
                    tried.append(f"{prov['name']}: {type(e).__name__}")
                    if not last:
                        continue
                    with _lock:
                        STATS["errors"] += 1
                    return self._json(502, {"error": {"message": "no model provider answered: " + "; ".join(tried)}})
                with _lock:
                    STATS.setdefault("by_provider", {})[prov["name"]] = STATS.setdefault("by_provider", {}).get(prov["name"], 0) + 1
                self._answer(resp, stream, m, prov["name"])
                return
            with _lock:
                STATS["errors"] += 1
            return self._json(502, {"error": {"message": "no model provider answered: " + "; ".join(tried)}})

        def _answer(self, resp, stream, m, provider):
            self._provider = provider
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
                    self.send_header("X-Neko-Provider", provider)
                    self.end_headers()
                    self.wfile.write(data)

        def _relay_stream(self, resp, m):
            self.send_response(resp.status)
            self.send_header("X-Neko-Provider", getattr(self, "_provider", ""))
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


def deep_required() -> bool:
    """On unless the owner sets JARVIS_PRIVACY_REQUIRE_DEEP=0 (then /health still lists the gap)."""
    return os.environ.get("JARVIS_PRIVACY_REQUIRE_DEEP", "1") != "0"


def serve(port: int, upstream, timeout: float = 300.0, red: Redactor | None = None, cooldown: float = 60.0,
          require_deep: bool | None = None):
    require_deep = deep_required() if require_deep is None else require_deep
    srv = ThreadingHTTPServer(("127.0.0.1", port), make_handler(red or Redactor(), upstream, timeout, cooldown, require_deep))
    srv.daemon_threads = True
    return srv


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=int(os.environ.get("JARVIS_PRIVACY_PORT", "48950")))
    ap.add_argument("--upstream", default=None, help="replace the first provider's address (testing)")
    a = ap.parse_args()
    providers, cooldown = load_providers(upstream=a.upstream)
    red = Redactor()
    srv = serve(a.port, providers, cooldown=cooldown, red=red)
    print(f"privacy gateway on http://127.0.0.1:{a.port}/v1 -> " + " then ".join(p["name"] for p in providers), flush=True)
    if degraded_reasons(red, deep_required()):
        print("WARNING: the PII model is not loaded: model calls are refused (503) until it is; see /health", flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    main()
