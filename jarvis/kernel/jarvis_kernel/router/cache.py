"""In-memory response cache for the router. Safe by construction: lane, provider and principal are part of the key,
only deterministic plain-text non-streaming chat completions are stored, and nothing is ever persisted."""
import copy
import hashlib
import json
import re
import threading
import time
from collections import OrderedDict

VOLATILE = ("stream", "user", "metadata", "stream_options")
PLACEHOLDER = re.compile(r"\[[A-Z_]+_\d+\]")
_TOOL_FIELDS = ("tools", "functions", "tool_choice", "function_call")


def _canon(obj):
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True, default=str)


def _plain_text_messages(body):
    msgs = body.get("messages")
    if not isinstance(msgs, list) or not msgs:
        return False
    for m in msgs:
        if not isinstance(m, dict):
            return False
        if m.get("tool_calls") or m.get("function_call") or m.get("role") in ("tool", "function"):
            return False
        c = m.get("content")
        if c is None or isinstance(c, str):
            continue
        if isinstance(c, list):
            for part in c:
                if not (isinstance(part, dict) and part.get("type") == "text" and isinstance(part.get("text"), str)):
                    return False
            continue
        return False
    return True


def request_cacheable(body):
    """True when a request may be cached or served from cache (non-streaming, plain text, explicit temperature 0, no tools, no placeholders)."""
    if not isinstance(body, dict) or body.get("stream"):
        return False
    t = body.get("temperature")
    # Only an EXPLICIT temperature 0: an absent temperature means the model's default sampling, i.e. a random answer, and replaying one
    # random answer for the whole TTL would be surprising.
    if t is None or isinstance(t, bool) or not isinstance(t, (int, float)) or t != 0:
        return False
    if any(body.get(f) is not None for f in _TOOL_FIELDS):
        return False
    n = body.get("n")
    if n is not None and (isinstance(n, bool) or not isinstance(n, int) or n > 1):
        return False
    if not _plain_text_messages(body):
        return False
    if PLACEHOLDER.search(_canon(body)):
        return False
    return True


def response_cacheable(resp, max_bytes):
    if not isinstance(resp, dict):
        return False
    choices = resp.get("choices")
    if not isinstance(choices, list) or not choices:
        return False
    for ch in choices:
        if not isinstance(ch, dict) or ch.get("finish_reason") != "stop":
            return False
        msg = ch.get("message")
        if not isinstance(msg, dict) or msg.get("tool_calls") or msg.get("function_call"):
            return False
    try:
        return len(_canon(resp).encode("utf-8")) <= max_bytes
    except (TypeError, ValueError):
        return False


class ResponseCache:
    def __init__(self, max_entries=256, ttl_s=300, max_entry_bytes=256 * 1024, clock=time.monotonic):
        self.max_entries, self.ttl_s, self.max_entry_bytes, self._clock = max_entries, ttl_s, max_entry_bytes, clock
        self._lock = threading.Lock()
        self._d = OrderedDict()  # key -> (expires, lane, provider, value)
        self._hits = self._misses = self._refused = self._evictions = 0

    @staticmethod
    def key(lane, provider, model, body, principal):
        clean = {k: v for k, v in body.items() if k not in VOLATILE}
        doc = {"lane": lane, "provider": provider, "model": model, "principal": principal, "body": clean}
        return hashlib.sha256(_canon(doc).encode("utf-8")).hexdigest()

    def get(self, key):
        with self._lock:
            e = self._d.get(key)
            if e is None:
                self._misses += 1
                return None
            if self._clock() >= e[0]:
                del self._d[key]
                self._misses += 1
                return None
            self._d.move_to_end(key)
            self._hits += 1
            return copy.deepcopy(e[3])

    def put(self, key, value, *, cacheable_request, lane="", provider=""):
        ok = request_cacheable(cacheable_request) and response_cacheable(value, self.max_entry_bytes)
        with self._lock:
            if not ok:
                self._refused += 1
                return False
            self._d[key] = (self._clock() + self.ttl_s, lane, provider, copy.deepcopy(value))
            self._d.move_to_end(key)
            while len(self._d) > self.max_entries:
                self._d.popitem(last=False)
                self._evictions += 1
            return True

    def _drop(self, pred):
        with self._lock:
            dead = [k for k, e in self._d.items() if pred(e)]
            for k in dead:
                del self._d[k]
            return len(dead)

    def invalidate_lane(self, lane):
        return self._drop(lambda e: e[1] == lane)

    def invalidate_provider(self, name):
        return self._drop(lambda e: e[2] == name)

    def clear(self):
        return self._drop(lambda e: True)

    def stats(self):
        with self._lock:
            return {"entries": len(self._d), "hits": self._hits, "misses": self._misses,
                    "refused": self._refused, "evictions": self._evictions}


def cached_call(cache, lane, provider, model, body, principal, call):
    """Return (response, "hit"|"miss"|"bypass")."""
    if not request_cacheable(body):
        return call(), "bypass"
    k = cache.key(lane, provider, model, body, principal)
    got = cache.get(k)
    if got is not None:
        return got, "hit"
    resp = call()
    cache.put(k, resp, cacheable_request=body, lane=lane, provider=provider)
    return resp, "miss"
