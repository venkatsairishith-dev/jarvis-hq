"""Shadow mode: the router watches the live chain and says what it would have picked, without ever changing a call (plan item 19).

The proxy's own chain (lane plan, owner preference, size filter, rest periods) stays the only thing that routes. On every call
`observe()` asks a Router that was built from the same lane plan which provider it would ask first, and records agreement or the
difference; `outcome()` feeds the live results (status codes) to the router's own breakers so it learns from the same traffic.
The router here has its own state objects, so nothing it does (a taken quota unit, a half-open probe) touches the live path.
Nothing in this module raises into the caller: a fault in shadow mode is counted and ignored."""
import threading
import time
from collections import deque

from .breaker import CircuitBreaker
from .combos import Combo, Target
from .router import NoRoute, Router

MODES = ("off", "shadow")           # "on" (the router advises or decides) is not accepted yet
AUDIT_EVERY_S = 60                  # one audit event per distinct difference per minute, so a persistent difference cannot flood the log


def normalize_mode(value) -> str:
    v = str(value or "off").strip().lower()
    return v if v in MODES else "off"


class Shadow:
    def __init__(self, providers, plan, audit=None, clock=time.time, recent: int = 50) -> None:
        """providers: the kernel's Provider list. plan(lane) -> that lane's chain of Provider objects (gateway.plan)."""
        self._clock, self._audit, self._lock = clock, audit, threading.Lock()
        self.counts = {"calls": 0, "agree": 0, "differ": 0, "router_no_route": 0, "live_none": 0, "errors": 0}
        self.recent = deque(maxlen=recent)
        self._last_audit: dict = {}
        combos, breakers = {}, {}
        for lane in ("private", "public"):
            try:
                chain = plan(lane)
            except Exception:
                continue
            targets = tuple(Target(p.name, p.default_model or "") for p in chain)
            if targets:
                combos[lane] = Combo(lane, lane, targets)
                for p in chain:
                    breakers.setdefault(p.name, CircuitBreaker(clock=clock))
        self.router = Router(combos, breakers, {}, {}, {})

    def observe(self, lane: str, live_chain: list, est_tokens: int = 0, preferred: str | None = None) -> None:
        """live_chain: provider names the proxy will try, in order (after preference, size filter and rests)."""
        try:
            live = live_chain[0] if live_chain else None
            try:
                pick = self.router.pick(lane, lane, est_tokens)
                mine = pick.target.provider
                skipped = [list(s) for s in pick.skipped]
            except NoRoute as e:
                mine, skipped = None, [list(s) for s in e.skipped]
            with self._lock:
                self.counts["calls"] += 1
                if live is None:
                    self.counts["live_none"] += 1
                if mine is None:
                    self.counts["router_no_route"] += 1
                if mine == live:
                    self.counts["agree"] += 1
                    return
                self.counts["differ"] += 1
                now = self._clock()
                rec = {"at": round(now, 1), "lane": lane, "live": live, "router": mine, "owner_preferred": preferred,
                       "router_skipped": skipped[:6]}
                self.recent.append(rec)
                key = (lane, live, mine)
                due = now - self._last_audit.get(key, 0) >= AUDIT_EVERY_S
                if due:
                    self._last_audit[key] = now
            if due and self._audit is not None:
                self._audit("router_shadow_diff", {k: v for k, v in rec.items() if k != "at"})
        except Exception:
            with self._lock:
                self.counts["errors"] += 1

    def outcome(self, provider: str, status: int, retry_after=None) -> None:
        """A live attempt finished: status is the HTTP code (use 503 for a connection failure)."""
        try:
            for combo in self.router.combos.values():
                for t in combo.targets:
                    if t.provider == provider:
                        self.router.report(t, status, retry_after=retry_after)
                        return
        except Exception:
            with self._lock:
                self.counts["errors"] += 1

    def snapshot(self) -> dict:
        with self._lock:
            c = dict(self.counts)
            recent = list(self.recent)[-20:]
        c["agree_rate"] = round(c["agree"] / c["calls"], 3) if c["calls"] else None
        return {"mode": "shadow", "counts": c, "recent_differences": recent,
                "breakers": {n: b.state() for n, b in self.router.breakers.items()}}
