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
places for that call ("names": "people": people only) and answers with what was really active ("names", "deep", "ready").

The face's reasoning stays out of its chat (D49): a request that carries the face's placeholder key ("jarvis-local") gets a reply
without `reasoning_content` / `reasoning` deltas and without <think>...</think> text (also one that opens and never closes), because
the face would show it to the owner, sometimes for minutes. Other callers get the reply as the provider sent it. A provider entry in
providers.json may carry `extra_body` (switches added to every request it receives) and `face_extra_body` (added only for the face),
for example {"chat_template_kwargs": {"enable_thinking": false}}; they must have the exact shape of the provider switches above.

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
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from redactor import Mapping, Redactor, StreamRestorer, restore  # noqa: E402

DEFAULT_UPSTREAM = os.environ.get("JARVIS_PRIVACY_UPSTREAM", "https://inference.dahl.global/v1")
# The face sends this placeholder instead of a key; the gateway then uses the first provider's key file (set "key_file" on it).
LOCAL_KEY_TOKEN = "jarvis-local"
PASS_HEADERS = ("authorization", "content-type", "accept", "user-agent", "openai-organization", "openai-project")
MAX_BODY = 32 * 1024 * 1024
STREAM_KEEPALIVE_S = 15.0           # while only reasoning arrives (and is hidden), an SSE comment goes out at this interval
_lock = threading.Lock()
STATS = {"requests": 0, "redacted": {}, "errors": 0, "started": time.time(), "inflight": 0, "last_call_end": 0.0}


def _count(mapping: Mapping):
    with _lock:
        STATS["requests"] += 1
        for kind, n in mapping.counts.items():
            STATS["redacted"][kind] = STATS["redacted"].get(kind, 0) + n


# ---------- outbound: redact ----------

# Every role gets the full pass (patterns + PII model + Jev check), not only user/tool/function (audit MEM-M3, 2026-10-18):
# chat clients put recalled memory in the SYSTEM prompt and resend earlier ASSISTANT replies, and memory summary, correction
# and fact-extraction calls send the owner's conversation under whatever role they like. Placeholders
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
# Provider switches that chat clients add through extra_body (enable_thinking, thinking.type, thinking_budget,
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


# Tool-definition identifiers (2026-10-25). The names of tools and of their arguments, and the argument names listed under "required", are written by the
# app that offers the tool, not by the owner. Redacting them broke every tool call of the face: builtIn_mcpListTools became [API_KEY_1] (a mixed-case
# word looks like a key), call_icon became [USERNAME_2], location became [API_KEY_5], and the model could not name a tool it was given. A name is kept
# only inside tools / functions / tool_choice / function_call / a tool call, and only when it is a plain identifier (no space, "@", digit first);
# a message's own "name" (a speaker) and every description, default and example are still redacted.
IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,63}")      # letters, digits, underscore only: no hyphen, dot, space or "@", so a value built from private text does not pass
IDENT_KEYS = {"name", "required"}


def _key(k, red, m: Mapping, ident: bool = False):
    """A JSON object key. Plain lower-case schema words (type, properties, description, city_name ...) stay as they are; any other
    key (capitals, spaces, dashes, digits first, Indic script, a long value used as a name) is text a caller may have built from
    private data, so it gets the same deep pass as a value. The reply's placeholders are restored, keys in tool-call arguments too."""
    if not isinstance(k, str):
        raise GatewayRefusal("the request holds a value the gateway cannot check, so it was not forwarded")
    return k if (PLAIN_KEY.fullmatch(k) or (ident and IDENTIFIER.fullmatch(k))) else _kept(red, k, m)


def _tree(value, red, m: Mapping, depth: int = 0, key: str = "", ident: bool = False):
    """Every string inside a JSON value redacted (deep); numbers, booleans and null kept; keys kept."""
    if depth > MAX_DEPTH:
        raise GatewayRefusal("the request is nested too deeply to be checked, so it was not forwarded")
    if isinstance(value, str):
        if key in SCHEMA_WORDS or (ident and key in IDENT_KEYS and IDENTIFIER.fullmatch(value)):
            return value
        return _kept(red, value, m)
    if isinstance(value, list):
        return [_tree(v, red, m, depth + 1, key, ident) for v in value]
    if isinstance(value, dict):
        return {_key(k, red, m, ident): _tree(v, red, m, depth + 1, k if isinstance(k, str) else "", ident) for k, v in value.items()}
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
            out[k] = _tree(msg[k], red, m, key=k, ident=(k == "function_call"))
    if isinstance(msg.get("tool_calls"), list):
        calls = []
        for c in msg["tool_calls"]:
            if not isinstance(c, dict):
                raise GatewayRefusal("a tool call has an unknown shape, so the request was not forwarded")
            fn = c.get("function")
            calls.append({"id": _tree(c.get("id"), red, m), "type": "function",
                          "function": _tree(fn if isinstance(fn, dict) else {}, red, m, key="function", ident=True)})
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
            out[k] = _tree(v, red, m, key=k, ident=k in ("tools", "functions", "function_call", "tool_choice"))
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


HIDDEN_FIELDS = ("reasoning_content", "reasoning", "reasoning_details")
_THINK_NAMES = ("think", "thinking", "thought", "reasoning")
_THINK_OPEN = re.compile(r"<(?:think|thinking|thought|reasoning)>", re.I)
_THINK_CLOSE = re.compile(r"</(?:think|thinking|thought|reasoning)>", re.I)
_OPEN_TAGS = tuple(f"<{n}>" for n in _THINK_NAMES)
_CLOSE_TAGS = tuple(f"</{n}>" for n in _THINK_NAMES)


class ThinkStripper:
    """Removes <think>...</think> (also <thinking>, <thought>, <reasoning>) from streamed text, wherever the tags are split.

    Text inside a tag pair is dropped, and so is everything after an opening tag that never closes (the stream ended inside it).
    A stray closing tag is removed on its own. Spaces and newlines right after a closing tag are dropped, so the answer starts clean.
    A text that holds only the end of the thought (the model's template already opened it) cannot be told apart from an answer and is
    left alone.
    """

    def __init__(self):
        self.buf, self.inside, self.trim = "", False, False

    def _held(self, text: str, tags: tuple) -> int:
        """Length of the end of `text` that could still become one of `tags` (so it is kept back, not emitted)."""
        cut = text.rfind("<")
        if cut < 0:
            return 0
        tail = text[cut:].lower()
        return len(tail) if any(t.startswith(tail) and t != tail for t in tags) else 0

    def _visible(self, text: str) -> str:
        if self.trim and text:
            text = text.lstrip()
            if text:
                self.trim = False
        return text

    def feed(self, text: str) -> str:
        self.buf += text
        out = []
        while True:
            if self.inside:
                m = _THINK_CLOSE.search(self.buf)
                if m:
                    self.buf, self.inside, self.trim = self.buf[m.end():], False, True
                    continue
                keep = self._held(self.buf, _CLOSE_TAGS)
                self.buf = self.buf[len(self.buf) - keep:] if keep else ""
                break
            o, c = _THINK_OPEN.search(self.buf), _THINK_CLOSE.search(self.buf)
            hit = min((x for x in (o, c) if x), key=lambda x: x.start(), default=None)
            if hit is None:
                keep = self._held(self.buf, _OPEN_TAGS + _CLOSE_TAGS)
                out.append(self._visible(self.buf[:len(self.buf) - keep]))
                self.buf = self.buf[len(self.buf) - keep:] if keep else ""
                break
            out.append(self._visible(self.buf[:hit.start()]))
            self.buf = self.buf[hit.end():]
            if hit is o:
                self.inside = True            # a stray closing tag is just removed
        return "".join(out)

    def flush(self) -> str:
        rest, self.buf = ("" if self.inside else self.buf), ""
        return self._visible(rest)


def strip_think(text: str) -> str:
    s = ThinkStripper()
    return s.feed(text) + s.flush()


def hide_reasoning_json(obj):
    """A non-streamed reply without the model's reasoning: the reasoning fields and any <think> text in the content."""
    if isinstance(obj, dict):
        for ch in obj.get("choices") or []:
            if not isinstance(ch, dict):
                continue
            for part in (ch.get("message"), ch.get("delta")):
                if isinstance(part, dict):
                    for k in HIDDEN_FIELDS:
                        part.pop(k, None)
                    if isinstance(part.get("content"), str):
                        part["content"] = strip_think(part["content"])
            if isinstance(ch.get("text"), str):                       # the completions API
                ch["text"] = strip_think(ch["text"])
    return obj


class SseRestorer:
    """Restores placeholders inside streamed chat deltas. One StreamRestorer per (choice, field).

    With `hide_reasoning` (the face's requests) the reasoning fields are dropped and <think> text is stripped from the content before
    it is restored; an event that carried only reasoning is dropped (`event` returns None)."""

    FIELDS = ("content", "reasoning_content", "reasoning")

    def __init__(self, m: Mapping, hide_reasoning: bool = False):
        self.m, self.parts, self.hide, self.strippers = m, {}, hide_reasoning, {}

    def _r(self, key):
        if key not in self.parts:
            self.parts[key] = StreamRestorer(self.m)
        return self.parts[key]

    def _s(self, i) -> ThinkStripper:
        if i not in self.strippers:
            self.strippers[i] = ThinkStripper()
        return self.strippers[i]

    def event(self, data: str):
        if data.strip() == "[DONE]" or (not self.m.to_value and not self.hide):
            return data
        try:
            ev = json.loads(data)
        except ValueError:
            return restore(data, self.m) if self.m.to_value else data
        if not isinstance(ev, dict):
            return data
        removed = False
        for ch in ev.get("choices") or []:
            i = ch.get("index", 0)
            d = ch.get("delta") or {}
            if self.hide:
                for f in HIDDEN_FIELDS:
                    if f in d:
                        d.pop(f)
                        removed = True
                if isinstance(d.get("content"), str):
                    before = d["content"]
                    d["content"] = self._s(i).feed(before)
                    removed = removed or d["content"] != before
                if "message" in ch:
                    hide_reasoning_json({"choices": [ch]})
            if self.m.to_value:
                for f in self.FIELDS:
                    if isinstance(d.get(f), str):
                        d[f] = self._r((i, f)).feed(d[f])
                for tc in d.get("tool_calls") or []:
                    fn = tc.get("function") or {}
                    if isinstance(fn.get("arguments"), str):
                        fn["arguments"] = self._r((i, "tool", tc.get("index", 0))).feed(fn["arguments"])
            if ch.get("finish_reason"):                          # last chunk of this choice: release anything held
                if self.hide:
                    tail = self._s(i).flush()
                    if tail:
                        d["content"] = (d.get("content") or "") + (self._r((i, "content")).feed(tail) if self.m.to_value else tail)
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
            if "message" in ch and self.m.to_value:
                ch["message"] = restore_json(ch["message"], self.m)
        if removed and ev.get("choices") and "usage" not in ev and all(
                not (c.get("delta") or {}).get("tool_calls") and not c.get("finish_reason") and not (c.get("delta") or {}).get("content")
                and not (c.get("delta") or {}).get("role") for c in ev["choices"]):
            return None                                          # it carried only the model's reasoning
        return json.dumps(ev, ensure_ascii=False)

    def leftover(self) -> str:
        return "".join(r.flush() for r in self.parts.values())


# ---------- providers (ordered fallback) ----------

PROVIDERS_FILE = os.environ.get("JARVIS_PRIVACY_PROVIDERS", os.path.join(os.path.dirname(os.path.abspath(__file__)), "providers.json"))
SECRET_ROOT = os.environ.get("JARVIS_SECRET_REF_ROOTS", r"D:\jarvis-secrets").split(os.pathsep)[0]
RETRY_CODES = (429, 500, 502, 503, 504, 520, 522, 524)
BAD_TOOL_JSON = re.compile(r"unterminated string|invalid json|json.{0,40}(parse|decode)|tool_calls?", re.I)   # a provider's 400 about a tool-call body
_cooldown: dict[str, float] = {}
_keys: dict[str, str] = {}


def read_pin() -> tuple[str, str]:
    """Benchmark switch (owner 2026-10-25: test every model on screen): a one-line file `name` or `name model` in the secrets root
    ('gateway-pin.txt', not a secret) pins the chain to that one provider. Absent or empty means the normal chain."""
    path = os.environ.get("JARVIS_GATEWAY_PIN_FILE") or os.path.join(SECRET_ROOT, "gateway-pin.txt")
    try:
        with open(path, encoding="utf-8") as fh:
            parts = fh.readline().split(None, 1)
    except OSError:
        return "", ""
    return (parts[0], parts[1].strip() if len(parts) > 1 else "") if parts else ("", "")


def load_providers(path: str = PROVIDERS_FILE, upstream: str | None = None) -> tuple[list[dict], float]:
    """Enabled providers in order. The first always uses the caller's key and model; --upstream replaces its address."""
    try:
        with open(path, encoding="utf-8") as f:
            cfg = json.load(f)
        # Claude is never a runtime provider (owner, 2026-10-23, D35): an "anthropic" entry is dropped, whatever the file says.
        provs = [p for p in cfg.get("providers") or [] if p.get("enabled") and p.get("api") != "anthropic"]
        cool = float(cfg.get("cooldown_seconds", 60))
    except (OSError, ValueError):
        provs, cool = [], 60.0
    if not provs:
        provs = [{"name": "upstream", "base_url": DEFAULT_UPSTREAM, "key": "from_request"}]
    if upstream:
        provs[0] = {**provs[0], "base_url": upstream}
    usable = [provs[0]] + [p for p in provs[1:] if p.get("model") and (p.get("key_file") or is_local_provider(p))]
    return [{**p, "base_url": p["base_url"].rstrip("/"), "extra_body": clean_extra_body(p.get("extra_body")),
             "face_extra_body": clean_extra_body(p.get("face_extra_body"))} for p in usable], cool


def is_local_provider(p: dict) -> bool:
    """A fallback that is a model on THIS PC (llama-swap): marked "local": true, address on loopback only, needs no key. It is the last link
    of the chain, so a request is answered on the laptop (nothing leaves it) when every online provider is resting or refuses."""
    if not p.get("local"):
        return False
    try:
        host = (urllib.parse.urlsplit(str(p.get("base_url") or "")).hostname or "").lower()
    except ValueError:
        return False
    return host in ("127.0.0.1", "localhost", "::1")


def clean_extra_body(value) -> dict:
    """A provider entry's switches: only the provider controls, only in their exact shape (the same rule as for a caller's request)."""
    if not isinstance(value, dict):
        return {}
    return {k: v for k, v in value.items() if k in CONTROLS and _control_ok(k, v)}


def is_face(headers) -> bool:
    """The face sends the placeholder key; nobody else does. Its replies are shown to the owner, so its reasoning is hidden (D49)."""
    if os.environ.get("JARVIS_PRIVACY_SHOW_REASONING") == "1":
        return False
    sent = next((v for k, v in headers.items() if k.lower() == "authorization"), "") or ""
    return sent.strip().split()[-1:] == [LOCAL_KEY_TOKEN]


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
    upstream = providers[0]["base_url"]   # the caller's-key provider

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
                # "names": "people" hides people only, so a public search query may keep a city or a country (private values and
                # addresses are still caught by the patterns and the PII model).
                want_names = (req.get("names") if isinstance(req, dict) and req.get("names") in (True, "people") else False)
                m = Mapping()
                try:
                    out = red.redact(str(text), m, True, names=want_names) if want_names else red.redact(str(text), m)
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
            face = is_face(headers)
            if raw is not None:
                headers["Content-Type"] = "application/json"
            # Fallback only for model calls; the first provider always gets the caller's own key and model.
            first_own = providers[0]
            # A caller that sends no key, or the placeholder "jarvis-local" (the face does), gets the first provider's key file.
            # The gateway listens on loopback only, and the key never leaves it except to the provider.
            if first_own.get("key_file"):
                auth_name = next((k for k in headers if k.lower() == "authorization"), None)
                sent = (headers.get(auth_name) or "").strip() if auth_name else ""
                if not sent or sent.split()[-1] == LOCAL_KEY_TOKEN:
                    own_key = read_key(first_own)
                    if own_key:
                        headers[auth_name or "Authorization"] = f"Bearer {own_key}"
            chain = providers if (raw is not None and self._path() in ("/chat/completions", "/completions")) else [first_own]
            now = time.time()
            chain = [p for p in chain if p is first_own or _cooldown.get(p["name"], 0) <= now]
            # A fallback with a token limit of its own (Groq's free tier: 8000 tokens a minute for input and answer) is skipped for a request it cannot take: its
            # "413 request too large" would hide the first provider's real answer (busy), and the face showed "Remote sent 413 response" (2026-10-25).
            est = (len(raw) // 4 + 1024) if raw is not None else 0
            chain = [p for p in chain if p is first_own or not p.get("max_total_tokens") or est <= int(p["max_total_tokens"])]
            # "prefer_first": true on a provider (the local model, owner 2026-10-25: "use the local MoE model") puts it at the front; the online ones stay the failover.
            # Only for a SIMPLE request (owner: "for the simple tasks"): short, and at most prefer_max_tools tools. Big or tool-heavy requests go online first.
            n_tools = len(body.get("tools") or []) if isinstance(body, dict) else 0
            chain = sorted(chain, key=lambda p: not (p.get("prefer_first") and is_local_provider(p) and est <= int(p.get("prefer_max_tokens") or 3000)
                                                       and n_tools <= int(p.get("prefer_max_tools") or 3)))
            pin_name, pin_model = read_pin()
            if pin_name and raw is not None and self._path() in ("/chat/completions", "/completions"):
                pinned = next((p for p in providers if p["name"] == pin_name), None)
                if pinned is not None:                       # a benchmark run: this one provider only, no failover, so the result is honest
                    chain = [pinned]
            tried = []
            for i, prov in enumerate(chain):
                last = i == len(chain) - 1
                hdrs, data = dict(headers), raw
                if pin_model and prov is first_own and len(chain) == 1 and body is not None:
                    data = json.dumps({**body, "model": pin_model}, ensure_ascii=False).encode("utf-8")
                extra = {**clean_extra_body(prov.get("extra_body")), **(clean_extra_body(prov.get("face_extra_body")) if face else {})}
                if extra and body is not None:
                    data = json.dumps({**body, **extra}, ensure_ascii=False).encode("utf-8")
                if prov is not first_own:
                    local = is_local_provider(prov)
                    key = "" if local else read_key(prov)
                    if not key and not local:
                        tried.append(f"{prov['name']}: no key file")
                        continue
                    if local:
                        hdrs.pop("Authorization", None)      # the caller's placeholder key is not for the local server
                    else:
                        hdrs["Authorization"] = f"Bearer {key}"
                    # Groq's Cloudflare front refuses Python's default user agent (error 1010, seen live); a fallback
                    # must not depend on what the caller happened to send.
                    hdrs["User-Agent"] = "jarvis-privacy-gateway/1.0"
                    data = json.dumps({**body, **extra, "model": (pin_model if len(chain) == 1 and pin_name else "") or prov["model"]}, ensure_ascii=False).encode("utf-8")
                req = urllib.request.Request(prov["base_url"] + self._path(), data=data, headers=hdrs,
                                             method="POST" if data is not None else "GET")
                try:
                    resp = urllib.request.urlopen(req, timeout=timeout)
                except urllib.error.HTTPError as e:
                    if e.code == 429:
                        _cooldown[prov["name"]] = time.time() + cooldown
                    if (e.code in RETRY_CODES or is_local_provider(prov)) and not last:     # a local model that is missing or erroring never blocks the online chain
                        tried.append(f"{prov['name']}: HTTP {e.code}")
                        e.close()
                        continue
                    err_body = None
                    if e.code == 400 and not last:
                        # Dahl answered 400 "Unterminated string" to a tool-call body (face goal d, 2026-10-25): the next provider reads the same request fine.
                        err_body = e.read()
                        if BAD_TOOL_JSON.search(err_body[:2000].decode("utf-8", "replace")):
                            tried.append(f"{prov['name']}: HTTP 400 bad tool JSON")
                            e.close()
                            continue
                    used = [read_key(q) for q in chain[:i + 1] if q.get("key_file")]   # every key file read so far, the first provider's too
                    payload = scrub_keys(err_body if err_body is not None else e.read(), used)
                    self.send_response(e.code)
                    self.send_header("Content-Type", e.headers.get("Content-Type", "application/json"))
                    self.send_header("Content-Length", str(len(payload)))
                    self.send_header("X-Jarvis-Provider", prov["name"])
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
                self._answer(resp, stream, m, prov["name"], face)
                return
            with _lock:
                STATS["errors"] += 1
            return self._json(502, {"error": {"message": "no model provider answered: " + "; ".join(tried)}})

        def _answer(self, resp, stream, m, provider, face=False):
            self._provider = provider
            with resp:
                ctype = resp.headers.get("Content-Type", "")
                if stream or "text/event-stream" in ctype:
                    self._relay_stream(resp, m, face)
                else:
                    data = resp.read()
                    if (m.to_value or face) and "json" in ctype:
                        try:
                            obj = json.loads(data)
                            if face:
                                obj = hide_reasoning_json(obj)
                            data = json.dumps(restore_json(obj, m) if m.to_value else obj, ensure_ascii=False).encode("utf-8")
                        except ValueError:
                            pass
                    self.send_response(resp.status)
                    self.send_header("Content-Type", ctype or "application/json")
                    self.send_header("Content-Length", str(len(data)))
                    self.send_header("X-Jarvis-Provider", provider)
                    self.end_headers()
                    self.wfile.write(data)

        def _relay_stream(self, resp, m, face=False):
            self.send_response(resp.status)
            self.send_header("X-Jarvis-Provider", getattr(self, "_provider", ""))
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "close")
            self.end_headers()
            self.close_connection = True
            sse = SseRestorer(m, hide_reasoning=face)
            quiet_since = time.time()
            try:
                for line in resp:
                    text = line.decode("utf-8", "replace")
                    if text.startswith("data:"):
                        out = sse.event(text[5:].strip())
                        if out is None:                              # only the model's reasoning: nothing to show the owner
                            if time.time() - quiet_since >= STREAM_KEEPALIVE_S:
                                self.wfile.write(b": still working\n")      # an SSE comment: readers skip it, nothing times the stream out
                                self.wfile.flush()
                                quiet_since = time.time()
                            continue
                        text = "data: " + out + "\n"
                    self.wfile.write(text.encode("utf-8"))
                    self.wfile.flush()
                    quiet_since = time.time()
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
    ap.add_argument("--providers", default=PROVIDERS_FILE, help="providers file")
    a = ap.parse_args()
    providers, cooldown = load_providers(a.providers, upstream=a.upstream)
    red = Redactor()
    srv = serve(a.port, providers, cooldown=cooldown, red=red)
    print(f"privacy gateway on http://127.0.0.1:{a.port}/v1 -> " + " then ".join(p["name"] for p in providers), flush=True)
    if degraded_reasons(red, deep_required()):
        print("WARNING: the PII model is not loaded: model calls are refused (503) until it is; see /health", flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    main()
