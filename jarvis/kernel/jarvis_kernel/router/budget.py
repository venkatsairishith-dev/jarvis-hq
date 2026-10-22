"""USD budget. The default limit is $0: nothing paid may be spent unless a limit is set explicitly."""


class Budget:
    def __init__(self, limit_usd: float = 0.0) -> None:
        if limit_usd < 0:
            raise ValueError("limit must not be negative")
        self.limit_usd = float(limit_usd)
        self.spent_usd = 0.0

    def check(self, cost_usd: float) -> bool:
        """True if spending cost_usd now would stay within the limit."""
        return cost_usd >= 0 and self.spent_usd + cost_usd <= self.limit_usd + 1e-12

    def add(self, cost_usd: float) -> None:
        if cost_usd < 0:
            raise ValueError("cost must not be negative")
        self.spent_usd += cost_usd

    def remaining(self) -> float:
        return max(0.0, self.limit_usd - self.spent_usd)
