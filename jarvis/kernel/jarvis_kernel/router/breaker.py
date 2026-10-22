"""Circuit breaker: stops calling a provider that keeps failing with 429 or 5xx, then probes it once after a cooldown."""
import time
from typing import Callable

CLOSED, OPEN, HALF_OPEN = "closed", "open", "half_open"


def counts_as_failure(status: int) -> bool:
    """Only rate limits (429) and server errors (5xx) count; other 4xx are the caller's fault, not the provider's."""
    return status == 429 or 500 <= status <= 599


class CircuitBreaker:
    def __init__(self, failure_threshold: int = 5, cooldown_s: float = 60, success_threshold: int = 1,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self.failure_threshold = failure_threshold
        self.cooldown_s = cooldown_s
        self.success_threshold = success_threshold
        self._clock = clock
        self._state = CLOSED
        self._failures = 0
        self._successes = 0
        self._opened_at = 0.0
        self._probe_out = False

    def _refresh(self) -> None:
        if self._state == OPEN and self._clock() - self._opened_at >= self.cooldown_s:
            self._state, self._successes, self._probe_out = HALF_OPEN, 0, False

    def state(self) -> str:
        self._refresh()
        return self._state

    def would_allow(self) -> bool:
        """Like allow() but does not take the half-open probe slot."""
        self._refresh()
        return self._state == CLOSED or (self._state == HALF_OPEN and not self._probe_out)

    def allow(self) -> bool:
        self._refresh()
        if self._state == CLOSED:
            return True
        if self._state == HALF_OPEN and not self._probe_out:
            self._probe_out = True
            return True
        return False

    def _open(self) -> None:
        self._state, self._opened_at, self._probe_out, self._successes = OPEN, self._clock(), False, 0

    def record_failure(self, status: int) -> None:
        self._refresh()
        if self._state == HALF_OPEN:
            if counts_as_failure(status):
                self._open()
            else:
                self._probe_out = False   # a non-counting answer frees the probe slot
            return
        if self._state == OPEN or not counts_as_failure(status):
            return
        self._failures += 1
        if self._failures >= self.failure_threshold:
            self._open()

    def record_success(self) -> None:
        self._refresh()
        if self._state == HALF_OPEN:
            self._successes += 1
            self._probe_out = False
            if self._successes >= self.success_threshold:
                self._state, self._failures = CLOSED, 0
        elif self._state == CLOSED:
            self._failures = 0

    def snapshot(self) -> dict:
        self._refresh()
        retry = max(0.0, self.cooldown_s - (self._clock() - self._opened_at)) if self._state == OPEN else 0.0
        return {"state": self._state, "failures": self._failures, "failure_threshold": self.failure_threshold,
                "retry_in_s": retry}
