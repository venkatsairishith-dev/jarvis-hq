"""Key pool: rotates opaque API keys least-recently-used first, cools rate-limited keys, drops bad ones.

Keys are opaque strings. They never appear in an exception message, repr or snapshot (only "key#1".. labels)."""
import time
from typing import Callable, Optional


class NoKey(Exception):
    """No key is available right now."""


class KeyPool:
    def __init__(self, keys: list, cooldown_s: float = 60, clock: Callable[[], float] = time.monotonic) -> None:
        self._keys = list(dict.fromkeys(keys))
        self.cooldown_s = cooldown_s
        self._clock = clock
        self._cool_until: dict = {}
        self._bad: set = set()
        self._used: dict = {k: 0 for k in self._keys}   # use sequence number: 0 = never used
        self._seq = 0

    def __repr__(self) -> str:
        return f"KeyPool(keys={len(self._keys)})"

    def _ok(self, key: str) -> bool:
        return key not in self._bad and self._cool_until.get(key, 0.0) <= self._clock()

    def available(self) -> bool:
        return any(self._ok(k) for k in self._keys)

    def next(self) -> str:
        live = [k for k in self._keys if self._ok(k)]
        if not live:
            raise NoKey("no key available")
        key = min(live, key=lambda k: self._used[k])   # min is stable: pool order breaks ties
        self._seq += 1
        self._used[key] = self._seq
        return key

    def _check(self, key: str) -> None:
        if key not in self._used:
            raise KeyError("unknown key")

    def mark_rate_limited(self, key: str, retry_after: Optional[float] = None) -> None:
        self._check(key)
        self._cool_until[key] = self._clock() + (self.cooldown_s if retry_after is None else retry_after)

    def mark_bad(self, key: str) -> None:
        self._check(key)
        self._bad.add(key)

    def reset(self) -> None:
        self._bad.clear()
        self._cool_until.clear()

    def snapshot(self) -> dict:
        now = self._clock()
        rows = []
        for i, k in enumerate(self._keys, 1):
            state = "bad" if k in self._bad else "cooling" if self._cool_until.get(k, 0.0) > now else "ok"
            rows.append({"key": f"key#{i}", "state": state, "cooling_for_s": max(0.0, self._cool_until.get(k, 0.0) - now)})
        return {"keys": rows, "available": sum(r["state"] == "ok" for r in rows)}
