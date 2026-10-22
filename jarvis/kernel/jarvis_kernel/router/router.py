"""Router: picks the first usable target of a combo (breaker, key, quota, budget) and learns from the outcome."""
import threading
from dataclasses import dataclass, field
from typing import Optional

from .budget import Budget
from .combos import Target, resolve
from .keypool import NoKey


class NoRoute(Exception):
    def __init__(self, skipped: list) -> None:
        self.skipped = skipped
        super().__init__("no usable target: " + "; ".join(f"{p}: {r}" for p, r in skipped))


@dataclass
class Pick:
    target: Target
    skipped: list                                  # [(provider, reason)], no secrets
    key: Optional[str] = field(default=None, repr=False)   # handed to the caller only; never in repr or reasons


class Router:
    """combos: {name: Combo}. breakers/keypools/budgets: {provider: obj}. quotas: {provider: Quota | {"requests"|"tokens": Quota}}.
    A provider without a key pool needs no key; without a budget it gets Budget(0)."""

    def __init__(self, combos: dict, breakers: dict, keypools: dict, quotas: dict, budgets: dict) -> None:
        self.combos, self.breakers, self.keypools, self.quotas, self.budgets = combos, breakers, keypools, quotas, budgets
        # The kernel serves requests on many threads and the parts below keep plain state: one lock makes pick() and report() atomic
        # (without it two threads could both take the single half-open probe, or both spend the last quota unit).
        self._lock = threading.RLock()

    def _quotas(self, provider: str) -> dict:
        q = self.quotas.get(provider)
        return {} if q is None else q if isinstance(q, dict) else {"requests": q}

    def _budget(self, provider: str) -> Budget:
        return self.budgets.setdefault(provider, Budget())

    def pick(self, combo_name: str, token_lane: str, est_tokens: int = 0, est_cost: float = 0.0) -> Pick:
        with self._lock:
            return self._pick(combo_name, token_lane, est_tokens, est_cost)

    def _pick(self, combo_name: str, token_lane: str, est_tokens: int, est_cost: float) -> Pick:
        combo = self.combos.get(combo_name)
        if combo is None:
            raise NoRoute([(combo_name, "unknown combo")])
        skipped: list = []
        for t in resolve(combo, token_lane):
            br, kp, qs = self.breakers.get(t.provider), self.keypools.get(t.provider), self._quotas(t.provider)
            needs = {"requests": 1, "tokens": est_tokens}
            if br is not None and not br.would_allow():
                skipped.append((t.provider, "breaker " + br.state()))
            elif kp is not None and not kp.available():
                skipped.append((t.provider, "no key available"))
            elif any(not q.can_take(needs.get(k, 1)) for k, q in qs.items()):
                skipped.append((t.provider, "quota exhausted"))
            elif not self._budget(t.provider).check(est_cost):
                skipped.append((t.provider, "budget exceeded"))
            else:
                if br is not None:
                    br.allow()
                for k, q in qs.items():
                    q.try_take(needs.get(k, 1))
                try:
                    key = kp.next() if kp is not None else None
                except NoKey:   # cannot happen after available(); stay safe
                    skipped.append((t.provider, "no key available"))
                    continue
                return Pick(t, skipped, key)
        raise NoRoute(skipped)

    def report(self, target: Target, status: int, cost: float = 0.0, key: Optional[str] = None,
               retry_after: Optional[float] = None) -> None:
        with self._lock:
            self._report(target, status, cost, key, retry_after)

    def _report(self, target: Target, status: int, cost: float, key: Optional[str], retry_after: Optional[float]) -> None:
        br, kp = self.breakers.get(target.provider), self.keypools.get(target.provider)
        if 200 <= status < 300:
            if br is not None:
                br.record_success()
            if cost:
                self._budget(target.provider).add(cost)
            return
        if br is not None:
            br.record_failure(status)
        if kp is not None and key is not None:
            if status == 429:
                kp.mark_rate_limited(key, retry_after)
            elif status in (401, 403):
                kp.mark_bad(key)
