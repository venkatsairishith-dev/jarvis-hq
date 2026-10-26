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

Speed (D60): a per-message redaction cache (CachedRedaction) remembers, for each text, its redacted template and the private values it holds, so the
history, the system prompt and the tool definitions that every round resends are not scanned again; placeholders are re-numbered through each request's
own Mapping, a changed text is scanned afresh, and nothing is remembered while the decision model was unreachable (JARVIS_PRIVACY_MESSAGE_CACHE=0 turns
it off). /health adds latency_p90_ms, latency_calls and redaction_cache beside latency_ms (redact_ms, queue_ms, first_ms, total_ms). Each model request
also appends one content-free line (phase times, sizes, counts, the provider's name) to JARVIS_LATENCY_LOG or <data dir>/logs/latency.jsonl, which
jarvis/tools/latency_report.py reads. A provider entry may set `face_tool_max_tokens` (off unless set) to cap the face's tool-carrying requests.

The Authorization header is passed through untouched and never stored or logged. Logs hold counts by kind, never values.
"""
from __future__ import annotations

import argparse
import collections
import hashlib
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
import redactor as redactor_module  # noqa: E402
from redactor import Mapping, Redactor, StreamRestorer, restore  # noqa: E402

DEFAULT_UPSTREAM = os.environ.get("JARVIS_PRIVACY_UPSTREAM", "https://inference.dahl.global/v1")
# The face sends this placeholder instead of a key; the gateway then uses the first provider's key file (set "key_file" on it).
LOCAL_KEY_TOKEN = "jarvis-local"
PASS_HEADERS = ("authorization", "content-type", "accept", "user-agent", "openai-organization", "openai-project")
MAX_BODY = 32 * 1024 * 1024
STREAM_KEEPALIVE_S = 15.0           # while only reasoning arrives (and is hidden), an SSE comment goes out at this interval
_lock = threading.Lock()
STATS = {"requests": 0, "redacted": {}, "errors": 0, "started": time.time(), "inflight": 0, "last_call_end": 0.0}


LATENCY_KEYS = ("redact_ms", "queue_ms", "first_ms", "total_ms")


def _percentile(values, q: float):
    """The q-quantile (0..1) of a list of numbers by rank; 0 for an empty list. p90 of the last 50 calls is what /health shows."""
    ordered = sorted(values)
    if not ordered:
        return 0
    return ordered[min(len(ordered) - 1, max(0, int(round(q * (len(ordered) - 1)))))]


def latency_log_path() -> str:
    """Where the content-free timing lines go (D60): JARVIS_LATENCY_LOG, else <data dir>/logs/latency.jsonl. "off" or "0" turns it off."""
    env = os.environ.get("JARVIS_LATENCY_LOG", "").strip()
    if env.lower() in ("off", "0", "no", "false"):
        return ""
    if env:
        return env
    base = (os.environ.get("JARVIS_DATA_DIR") or "").strip() or (r"D:\jarvis\data" if os.name == "nt" else os.path.join(os.path.expanduser("~"), ".jarvis", "data"))
    return os.path.join(base, "logs", "latency.jsonl")


_latency_write_lock = threading.Lock()


def write_latency_line(row: dict) -> None:
    """One JSON line: phase names and milliseconds, the provider's name, a few counts. No text, no ids, no header, no key. Never raises."""
    path = latency_log_path()
    if not path:
        return
    safe = {k: v for k, v in row.items() if isinstance(v, (int, float, bool)) or (isinstance(v, str) and len(v) <= 40 and re.fullmatch(r"[A-Za-z0-9_.:-]*", v))}
    try:
        with _latency_write_lock:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            if os.path.exists(path) and os.path.getsize(path) > 5 * 1024 * 1024:       # one rotation: the report reads the newest 5 MB
                os.replace(path, path + ".1")
            with open(path, "a", encoding="utf-8") as fh:
                fh.write(json.dumps({"t": round(time.time(), 3), "src": "gateway", **safe}) + "\n")
    except OSError:
        pass


def _count(mapping: Mapping):
    with _lock:
        STATS["requests"] += 1
        for kind, n in mapping.counts.items():
            STATS["redacted"][kind] = STATS["redacted"].get(kind, 0) + n


# ---------- outbound: English only (D58) ----------
# The models downstream (online providers and the local ones) only understand English. A chat request whose LAST message is a user message in Telugu, Hindi or
# romanized Telugu/Hindi is translated on THIS PC (a local model, never an online one) before it is redacted and forwarded; system prompts and
# tool results are never touched (they are data: a Telugu sentence inside a tool result stays Telugu data). Earlier user turns and earlier replies that the face
# resends in Telugu or Hindi are made English too (from the cache first, so only new text costs model time). The original goes nowhere: the body is rebuilt
# from the whitelist below, so no field can carry it; the owner's record of it stays in a file on this PC (translate.keep_record). When the translator is
# missing, slow or wrong the request goes on unchanged and the reply says so: X-Jarvis-Translated: skipped. JARVIS_TRANSLATE=0 turns the layer off.

def _translate_module():
    """The shared language layer, or None. Imported by name when `jarvis` is importable, else loaded by path; never raises."""
    if os.environ.get("JARVIS_TRANSLATE", "1") == "0":
        return None
    try:
        from jarvis.plugins._shared import translate as mod
        return mod
    except ImportError:
        pass
    try:
        import importlib.util
        if "jarvis_translate" in sys.modules:
            return sys.modules["jarvis_translate"]
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "plugins", "_shared", "translate.py")
        spec = importlib.util.spec_from_file_location("jarvis_translate", path)
        mod = importlib.util.module_from_spec(spec)
        sys.modules["jarvis_translate"] = mod
        spec.loader.exec_module(mod)
        return mod
    except Exception:                                    # noqa: BLE001 - no language layer: the gateway works as before
        sys.modules.pop("jarvis_translate", None)
        return None


def _text_of(content) -> str:
    if isinstance(content, str):
        return content
    return " ".join(p.get("text", "") for p in (content or []) if isinstance(p, dict) and isinstance(p.get("text"), str))


def translate_input(body, face: bool = False):
    """(body, info) with the last user message in English. info is None when there is nothing to report (English, reply in English), else
    {"status": "no"|"yes"|"skipped", "lang": language of the owner's last message, "reply": language the reply is shown in}.
    Never raises, never blocks past the translator's own budget."""
    mod = _translate_module()
    if mod is None or not isinstance(body, dict) or not isinstance(body.get("messages"), list):
        return body, None
    try:
        msgs = body["messages"]
        new, res, hist = mod.english_messages_ex(msgs, source="gateway")
        last_lang = res.source_lang if res is not None else "en"
        if res is None:                                   # "match" follows the owner's last message, whichever turn it was
            for m in reversed(msgs):
                if isinstance(m, dict) and m.get("role") == "user":
                    last_lang = mod.detect_language(_text_of(m.get("content")))
                    break
        out = dict(body)
        did = hist["translated"] + (1 if res is not None and res.translated else 0)
        left = hist["skipped"] + (1 if res is not None and not res.translated else 0)
        if did:
            out["messages"] = new
        status = "skipped" if left else ("yes" if did else "no")
        if new and isinstance(new[-1], dict) and new[-1].get("role") == "user":
            if res is not None:
                mod.apply_reply_language_intent(res.original)
            mod.apply_reply_language_intent(_text_of(new[-1].get("content")))
            if face:                                      # D62: the owner's own words (the face's typed or spoken message), never a tool result: names are learned here
                observe = getattr(mod, "observe_owner", None)
                if observe is not None:
                    observe(_text_of(msgs[-1].get("content")), _text_of(new[-1].get("content")))
        reply = mod.target_for_reply(last_lang) if face else "en"
        if status == "no" and reply == "en":
            return out, None
        return out, {"status": status, "lang": last_lang, "reply": reply, "mod": mod}
    except Exception:                                    # noqa: BLE001 - the layer never breaks the gateway
        with _lock:
            STATS["translate_errors"] = STATS.get("translate_errors", 0) + 1
        return body, {"status": "skipped", "lang": "?", "reply": "en", "mod": None}


def _reply_event(data: str, st):
    """One streamed chunk with its text converted to the owner's language, a finished sentence at a time (st: translate.SentenceTranslator).
    None when the chunk became empty (its sentence is not finished yet)."""
    try:
        ev = json.loads(data)
    except ValueError:
        return data
    if not isinstance(ev, dict):
        return data
    held = False
    for ch in ev.get("choices") or []:
        d = ch.get("delta") or {}
        c = d.get("content")
        if isinstance(c, str) and c:
            d["content"] = st.feed(c)
            held = held or not d["content"]
        if ch.get("finish_reason"):
            tail = st.flush()
            if tail:
                d["content"] = (d.get("content") or "") + tail
        ch["delta"] = d
    if held and "usage" not in ev and all(not (c.get("delta") or {}).get("content") and not (c.get("delta") or {}).get("tool_calls")
                                         and not c.get("finish_reason") and not (c.get("delta") or {}).get("role") for c in ev.get("choices") or []):
        return None
    return json.dumps(ev, ensure_ascii=False)


def _reply_json(obj, tinfo):
    """A non-streamed reply whose message text is in the owner's language (only `content` text; never tool-call arguments)."""
    mod = tinfo["mod"]
    for ch in (obj.get("choices") or []) if isinstance(obj, dict) else []:
        msg = ch.get("message") if isinstance(ch, dict) else None
        if isinstance(msg, dict) and isinstance(msg.get("content"), str) and msg["content"].strip():
            st = mod.SentenceTranslator(tinfo["reply"])
            msg["content"] = st.feed(msg["content"]) + st.flush()
            tinfo["reply_skipped"] = tinfo.get("reply_skipped") or st.skipped
    return obj


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


class CachedRedaction:
    """A per-message redaction cache in front of any detector (D60). A chat resends its whole history every round (3 to 7 rounds a goal),
    so the same system prompt, tool definitions and earlier messages arrive again and again. The detector (Presidio + spaCy + the PII model) already
    remembers spans per text, but its cache is dropped whole at 512 entries and is not shared with the tool definitions' many short strings; this layer
    keeps, per text, the redacted TEMPLATE and the private values it holds, keyed by a hash of (text, deep, names). A repeat costs one dictionary
    look-up and a re-tag through THIS request's Mapping, so placeholder numbers stay correct for the request (the first new value is [X_1] in every
    request, whatever earlier requests held) and restoring the reply works as before. A changed text has another hash, so it is scanned again.

    Safety rules: a text that already holds something that looks like a placeholder is never cached (the template could not tell the owner's
    literal "[PASSWORD_1]" from ours); a result made while the detector reported a degraded check (decision model unreachable, `stats.jev_unreachable`
    grew) is not remembered, as in the detector itself; the cache lives in memory only, is bounded (entries and text size), and holds no more than
    the detector's own cache already holds. Everything the detector exposes (model, names, stats ...) is passed through."""

    MAX_ENTRIES = 4096
    MAX_TEXT = 256 * 1024

    def __init__(self, red, max_entries: int | None = None):
        self._red = red
        self._max = max_entries or self.MAX_ENTRIES
        self._entries: "collections.OrderedDict[tuple, tuple]" = collections.OrderedDict()
        self._lock = threading.Lock()
        self.hits = 0
        self.misses = 0
        self.uncacheable = 0

    def __getattr__(self, name):                      # model, names, stats, find ... (only called for what this class lacks)
        return getattr(self._red, name)

    @staticmethod
    def _key(text: str, deep, names) -> tuple:
        return (hashlib.sha256(text.encode("utf-8", "surrogatepass")).digest(), bool(deep), names if isinstance(names, str) else bool(names) if names is not None else None)

    def snapshot(self) -> dict:
        with self._lock:
            return {"hits": self.hits, "misses": self.misses, "uncacheable": self.uncacheable, "size": len(self._entries)}

    def redact(self, text, mapping: Mapping, deep: bool = True, names=None):
        if not isinstance(text, str) or not text.strip() or len(text) > self.MAX_TEXT or redactor_placeholder(text):
            if isinstance(text, str) and text.strip():
                with self._lock:
                    self.uncacheable += 1
            return self._red.redact(text, mapping, deep, names)
        key = self._key(text, deep, names)
        with self._lock:
            entry = self._entries.get(key)
            if entry is not None:
                self._entries.move_to_end(key)
                self.hits += 1
        if entry is not None:
            template, values = entry
            if not values:
                return template
            local = {}
            for tag, entity, value in values:             # in order of first appearance, as the detector would have tagged them
                local[tag] = mapping.tag(entity, value)
            return redactor_placeholder_sub(lambda mm: local.get(mm.group(0), mm.group(0)), template)
        # a miss: run the detector on a scratch mapping, so the template and its values can be kept; then tag them in the real one
        scratch = Mapping()
        before = _degraded_count(self._red)
        out = self._red.redact(text, scratch, deep, names)
        with self._lock:
            self.misses += 1
        values = []
        for tag, value in scratch.to_value.items():
            mm = redactor_placeholder_match(tag)
            if mm is None:
                values = None
                break
            values.append((tag, mm.group(1), value))
        if values is None:
            return self._red.redact(text, mapping, deep, names)        # cannot happen with our Mapping; fail safe: no cache
        local = {tag: mapping.tag(entity, value) for tag, entity, value in values}
        result = redactor_placeholder_sub(lambda mm: local.get(mm.group(0), mm.group(0)), out) if local else out
        if _degraded_count(self._red) == before:          # not remembered while the checker is down
            with self._lock:
                self._entries[key] = (out, tuple(values))
                self._entries.move_to_end(key)
                while len(self._entries) > self._max:
                    self._entries.popitem(last=False)
        return result


def _degraded_count(red) -> int:
    try:
        return int((getattr(red, "stats", None) or {}).get("jev_unreachable", 0))
    except (TypeError, ValueError, AttributeError):
        return 0


def redactor_placeholder(text: str) -> bool:
    return redactor_module.PLACEHOLDER.search(text) is not None


def redactor_placeholder_match(tag: str):
    return redactor_module.PLACEHOLDER.fullmatch(tag)


def redactor_placeholder_sub(repl, text: str) -> str:
    return redactor_module.PLACEHOLDER.sub(repl, text)


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
                raise GatewayRefusal(f"a tool call has an unknown shape ({type(c).__name__}), so the request was not forwarded")
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


class ReplayResponse:
    """A streamed provider answer whose first lines were already read: they come out again, then the rest."""

    def __init__(self, resp, head):
        self._r, self._head = resp, head
        self.status, self.headers = resp.status, resp.headers

    def __iter__(self):
        yield from self._head
        yield from self._r

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self._r.close()
        return False


PEEK_DEADLINE_S = 60.0


def _delta_has_answer(ch) -> bool:
    if not isinstance(ch, dict):
        return False
    d = ch.get("delta") if isinstance(ch.get("delta"), dict) else ch.get("message") if isinstance(ch.get("message"), dict) else {}
    content = d.get("content")
    return (isinstance(content, str) and bool(content.strip())) or bool(d.get("tool_calls")) or (isinstance(ch.get("text"), str) and bool(ch["text"].strip()))


def peek_stream(resp, whole: bool = False) -> tuple[list, str]:
    """Read a provider's SSE answer far enough to judge it: ("ok") once content or a tool call arrives (or, with `whole`, the stream ended with some,
    or PEEK_DEADLINE_S passed with the provider still talking), ("error") when an event is an error object or the connection broke before any answer,
    ("empty") when the stream ends with neither content nor a tool call. Returns the lines read."""
    head, ok, t0 = [], False, time.time()
    try:
        for line in resp:
            head.append(line)
            text = line.decode("utf-8", "replace")
            if text.startswith("data:"):
                payload = text[5:].strip()
                if payload == "[DONE]":
                    break
                try:
                    ev = json.loads(payload)
                except ValueError:
                    ev = None
                if isinstance(ev, dict):
                    if ev.get("error") and not ev.get("choices"):
                        return head, "error"
                    if any(_delta_has_answer(ch) for ch in (ev.get("choices") if isinstance(ev.get("choices"), list) else [])):
                        ok = True
            if ok and not whole:
                break
            if time.time() - t0 > PEEK_DEADLINE_S:
                return head, "ok"                      # still producing after a minute: relay it rather than fail a slow answer
    except Exception:                                  # noqa: BLE001 - IncompleteRead, a reset, a timeout: a broken stream before an answer is "error"
        return head, "ok" if ok else "error"
    return head, "ok" if ok else "empty"


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
_sems: dict[str, threading.BoundedSemaphore] = {}


def _slots(prov: dict):
    """The semaphore of a provider that sets max_concurrent, else None."""
    n = int(prov.get("max_concurrent") or 0)
    if n <= 0:
        return None
    with _lock:
        return _sems.setdefault(prov["name"], threading.BoundedSemaphore(n))
_keys: dict[str, str] = {}


def read_pin() -> tuple[str, str]:
    """Benchmark switch (owner 2026-10-25: test every model on screen): a one-line file `name` or `name model` in the secrets root
    ('gateway-pin.txt', not a secret) pins the chain to that one provider. Absent or empty means the normal chain."""
    path = os.environ.get("JARVIS_GATEWAY_PIN_FILE") or os.path.join(SECRET_ROOT, "gateway-pin.txt")
    try:
        with open(path, encoding="utf-8-sig") as fh:        # a BOM (PowerShell 5.1 writes one) must not become part of the name
            parts = fh.readline().split(None, 1)
    except (OSError, ValueError):                            # UnicodeDecodeError is a ValueError: a UTF-16 file means "no pin", not a dead gateway
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


def face_token_cap(prov: dict, body, face: bool) -> dict:
    """D60, OFF unless a provider entry sets `face_tool_max_tokens` (an integer 64..8192): the face's request that offers tools and sets no limit
    of its own gets `max_tokens` = that number, so a tool turn (which is a short call or a short sentence) cannot run on for a minute. Only safe
    with a provider whose reasoning is off or cheap: a reasoning model counts its hidden thinking against the same limit and can answer with
    nothing (MiniMax M2.x cannot switch thinking off at all, read 2026-10-26), so the entry stays unset until the laptop run shows it is safe."""
    n = prov.get("face_tool_max_tokens")
    if not face or isinstance(n, bool) or not isinstance(n, int) or not 64 <= n <= 8192 or not isinstance(body, dict):
        return {}
    if not body.get("tools") or body.get("max_tokens") is not None or body.get("max_completion_tokens") is not None:
        return {}
    return {"max_tokens": n}


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


FIRST_BYTE_S = 30.0          # a streamed answer from a provider that is not the last link must begin within this many seconds


def make_handler(red: Redactor, upstream: str | list, timeout: float, cooldown: float = 60.0, require_deep: bool = False):
    """require_deep: refuse (503) every model call while the PII model is not loaded. `serve()` and `main()` turn it on
    unless the owner sets JARVIS_PRIVACY_REQUIRE_DEEP=0; it is off here only so a test or a bench can drive the handler
    with a stand-in detector that has no model."""
    providers = upstream if isinstance(upstream, list) else [{"name": "upstream", "base_url": upstream.rstrip("/"), "key": "from_request"}]
    upstream = providers[0]["base_url"]   # the caller's-key provider
    if os.environ.get("JARVIS_PRIVACY_MESSAGE_CACHE", "1") != "0" and not isinstance(red, CachedRedaction):
        red = CachedRedaction(red)        # D60: a repeated message (history, system prompt, tool definitions) is not scanned again

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, fmt, *args):                     # never echo bodies or headers
            sys.stderr.write("%s %s\n" % (time.strftime("%H:%M:%S"), fmt % args))

        def _json(self, code, obj):
            raw = json.dumps(obj).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self._lang_headers()
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
                                            "latency_ms": {n: {k: sorted(r[k] for r in rows)[len(rows) // 2] for k in LATENCY_KEYS}
                                                           for n, rows in STATS.get("latency", {}).items() if rows},     # median of the last 50 calls per provider
                                            "latency_p90_ms": {n: {k: _percentile([r[k] for r in rows], 0.9) for k in LATENCY_KEYS}
                                                               for n, rows in STATS.get("latency", {}).items() if rows},
                                            "latency_calls": {n: len(rows) for n, rows in STATS.get("latency", {}).items() if rows},
                                            "redaction_cache": red.snapshot() if isinstance(red, CachedRedaction) else None,
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
            self._t0, self._t_fwd, self._t_first = time.time(), None, None
            self._queue_s, self._trace, self._provider = 0.0, {}, None
            cache = red.snapshot() if isinstance(red, CachedRedaction) else None
            try:
                return self._forward_inner(raw)
            finally:
                now = time.time()
                with _lock:
                    STATS["inflight"] -= 1
                    STATS["last_call_end"] = now
                    prov = getattr(self, "_provider", None)
                    log_row = None
                    if prov and self._t_fwd:
                        rows = STATS.setdefault("latency", {}).setdefault(prov, [])
                        row = {"redact_ms": round((self._t_fwd - self._t0) * 1000), "first_ms": round(((self._t_first or now) - self._t_fwd) * 1000),
                               "total_ms": round((now - self._t0) * 1000), "queue_ms": round(getattr(self, "_queue_s", 0.0) * 1000)}
                        rows.append(row)
                        del rows[:-50]
                        log_row = {**row, "provider": prov, **getattr(self, "_trace", {})}
                        if cache is not None:
                            now_cache = red.snapshot()
                            log_row["cache_hits"], log_row["cache_misses"] = now_cache["hits"] - cache["hits"], now_cache["misses"] - cache["misses"]
                if log_row:
                    write_latency_line(log_row)

        def _lang_headers(self):
            ti = getattr(self, "_tinfo", None)
            if not ti:
                return
            if ti["status"] in ("yes", "skipped"):
                self.send_header("X-Jarvis-Translated", ti["status"])
            if ti.get("reply", "en") != "en":
                self.send_header("X-Jarvis-Reply-Language", ti["reply"] + (";skipped" if ti.get("reply_skipped") else ""))

        def _forward_inner(self, raw):
            self._tinfo = None
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
                    body, self._tinfo = translate_input(body, is_face(self.headers))
                    if self._tinfo and self._tinfo["status"] in ("yes", "skipped"):
                        with _lock:
                            key = "translated" if self._tinfo["status"] == "yes" else "translate_skipped"
                            STATS[key] = STATS.get(key, 0) + 1
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
                msgs = body.get("messages") if isinstance(body.get("messages"), list) else []
                last_role = msgs[-1].get("role") if msgs and isinstance(msgs[-1], dict) else ""
                # content-free facts for the timing line: sizes and counts only (what kind of round this was: after the owner's words or after a tool result)
                self._trace = {"req_bytes": len(raw), "msgs": len(msgs), "tools": len(body.get("tools") or []), "stream": stream,
                               "after": last_role if last_role in ("user", "tool", "assistant", "system") else ""}
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
            # A caller that REASONS (D51: the goal planner) says "X-Jarvis-Route: online": it wants the online chain first, whatever its size. The local model
            # stays the last link, so a dead online chain is still answered (the reply's X-Jarvis-Provider header says who answered).
            online_first = (self.headers.get("X-Jarvis-Route") or "").strip().lower() == "online"
            if online_first:
                self._trace["route"] = "online"                # the goal planner's call (D51), told apart from the chat's rounds in the timing log
            chain = chain if online_first else sorted(chain, key=lambda p: not (p.get("prefer_first") and is_local_provider(p) and est <= int(p.get("prefer_max_tokens") or 3000)
                                                       and n_tools <= int(p.get("prefer_max_tools") or 3)))
            pin_name, pin_model = read_pin()
            if pin_name and raw is not None and self._path() in ("/chat/completions", "/completions"):
                pinned = next((p for p in providers if p["name"] == pin_name), None)
                if pinned is not None:                       # a benchmark run: this one provider only, no failover, so the result is honest
                    chain = [pinned]
            tried = []
            self._t_fwd = time.time()
            for i, prov in enumerate(chain):
                last = i == len(chain) - 1
                hdrs, data = dict(headers), raw
                if pin_model and prov is first_own and len(chain) == 1 and body is not None:
                    data = json.dumps({**body, "model": pin_model}, ensure_ascii=False).encode("utf-8")
                extra = {**clean_extra_body(prov.get("extra_body")), **(clean_extra_body(prov.get("face_extra_body")) if face else {}),
                         **face_token_cap(prov, body, face)}
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
                # "max_concurrent": the provider admits only that many requests at once (Dahl: "model_concurrency", HTTP 429 for the second request of the face).
                # Wait for a turn instead of being refused; a wait that runs out moves on to the next provider.
                sem = _slots(prov)
                waited = time.time()
                if sem is not None and not sem.acquire(timeout=float(prov.get("queue_wait_s") or 90)):
                    tried.append(f"{prov['name']}: busy (queue)")
                    if not last:
                        continue
                    sem = None
                self._queue_s = (self._queue_s if hasattr(self, "_queue_s") else 0.0) + (time.time() - waited if sem is not None else 0.0)
                try:
                    req = urllib.request.Request(prov["base_url"] + self._path(), data=data, headers=hdrs,
                                                 method="POST" if data is not None else "GET")
                    # A provider that has not sent a first byte in `first_byte_timeout_s` (default 30; the last link keeps the full timeout) is given up on, and the
                    # next one is asked. Measured on the laptop 2026-10-27 (reliability quick run): first bytes after 45 s, 53 s and 315 s from Groq and Dahl, goals
                    # that ended with no answer at all after 183 s although another provider was free (normal first byte: GLM 1.2 s, p90 9 s).
                    first_byte = timeout if (last or not stream) else min(timeout, float(prov.get("first_byte_timeout_s") or FIRST_BYTE_S))
                    try:
                        resp = urllib.request.urlopen(req, timeout=first_byte)
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
                    if stream and not last and "text/event-stream" in (resp.headers.get("Content-Type") or ""):
                        # Dahl delivered its tool-parser error INSIDE a 200 stream ("Unterminated string"), MiniMax a final answer of nothing (face, 2026-10-26):
                        # look at the stream before the caller sees any of it, and move on to the next provider when it is an error or empty.
                        head, verdict = peek_stream(resp)           # relays from the first real content on: streaming is kept (speed), an empty answer is still caught
                        if verdict != "ok":
                            tried.append(f"{prov['name']}: stream {verdict}")
                            resp.close()
                            continue
                        resp = ReplayResponse(resp, head)
                    self._t_first = time.time()
                    with _lock:
                        STATS.setdefault("by_provider", {})[prov["name"]] = STATS.setdefault("by_provider", {}).get(prov["name"], 0) + 1
                    self._answer(resp, stream, m, prov["name"], face)
                    return
                finally:
                    if sem is not None:
                        sem.release()
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
                    if (m.to_value or face or (getattr(self, "_tinfo", None) or {}).get("reply", "en") != "en") and "json" in ctype:
                        try:
                            obj = json.loads(data)
                            if face:
                                obj = hide_reasoning_json(obj)
                            obj = restore_json(obj, m) if m.to_value else obj
                            ti = getattr(self, "_tinfo", None)
                            if ti and ti.get("reply", "en") != "en" and ti.get("mod") is not None:
                                obj = _reply_json(obj, ti)
                            data = json.dumps(obj, ensure_ascii=False).encode("utf-8")
                        except ValueError:
                            pass
                    self.send_response(resp.status)
                    self.send_header("Content-Type", ctype or "application/json")
                    self.send_header("Content-Length", str(len(data)))
                    self.send_header("X-Jarvis-Provider", provider)
                    self._lang_headers()
                    self.end_headers()
                    self.wfile.write(data)

        def _relay_stream(self, resp, m, face=False):
            self.send_response(resp.status)
            self.send_header("X-Jarvis-Provider", getattr(self, "_provider", ""))
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "close")
            self._lang_headers()
            self.end_headers()
            self.close_connection = True
            sse = SseRestorer(m, hide_reasoning=face)
            ti = getattr(self, "_tinfo", None)
            conv = ti["mod"].SentenceTranslator(ti["reply"]) if ti and ti.get("reply", "en") != "en" and ti.get("mod") is not None else None
            quiet_since = time.time()
            try:
                for line in resp:
                    text = line.decode("utf-8", "replace")
                    if text.startswith("data:"):
                        out = sse.event(text[5:].strip())
                        if conv is not None and out is not None and out.strip() != "[DONE]":
                            out = _reply_event(out, conv)           # the owner's language, a finished sentence at a time (D58)
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
