"""Sliding-window quota for a count of requests or tokens."""
import time
from collections import deque
from typing import Callable


class Quota:
    def __init__(self, limit: float, window_s: float, clock: Callable[[], float] = time.monotonic) -> None:
        self.limit = limit
        self.window_s = window_s
        self._clock = clock
        self._events: deque = deque()   # (time, amount)
        self._used = 0.0

    def _slide(self) -> None:
        cutoff = self._clock() - self.window_s
        while self._events and self._events[0][0] <= cutoff:
            self._used -= self._events.popleft()[1]

    def remaining(self) -> float:
        self._slide()
        return max(0.0, self.limit - self._used)

    def can_take(self, n: float = 1) -> bool:
        return n <= self.remaining()

    def try_take(self, n: float = 1) -> bool:
        if not self.can_take(n):
            return False
        self._events.append((self._clock(), n))
        self._used += n
        return True

    def reset_in(self) -> float:
        """Seconds until the oldest counted event leaves the window (0 when nothing is counted)."""
        self._slide()
        return max(0.0, self._events[0][0] + self.window_s - self._clock()) if self._events else 0.0
