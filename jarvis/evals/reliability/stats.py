"""Small statistics for the reliability gate: percentiles and a Wilson interval. Standard library only."""
from __future__ import annotations

import math
from typing import Iterable


def percentile(values: Iterable[float], q: float) -> float | None:
    """Nearest-rank-with-interpolation percentile (q in 0..100); None for no data."""
    xs = sorted(float(v) for v in values)
    if not xs:
        return None
    if len(xs) == 1:
        return xs[0]
    k = (len(xs) - 1) * q / 100.0
    lo, hi = math.floor(k), math.ceil(k)
    return xs[lo] + (xs[hi] - xs[lo]) * (k - lo)


def p50(values: Iterable[float]) -> float | None:
    return percentile(values, 50)


def p90(values: Iterable[float]) -> float | None:
    return percentile(values, 90)


def wilson_lower(successes: int, n: int, z: float = 1.96) -> float:
    """The lower end of the 95% Wilson interval for a pass rate: what the true rate is at least, given n runs. 0.0 for n = 0."""
    if n <= 0:
        return 0.0
    p = successes / n
    denom = 1 + z * z / n
    centre = p + z * z / (2 * n)
    margin = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return max(0.0, (centre - margin) / denom)


def rate(successes: int, n: int) -> float | None:
    return None if n <= 0 else successes / n


def runs_needed_for_zero_failures(target: float = 0.9, z: float = 1.96) -> int:
    """How many consecutive passes it takes before the Wilson lower bound reaches `target` (a guide for choosing N)."""
    n = 1
    while wilson_lower(n, n, z) < target:
        n += 1
        if n > 10_000:
            break
    return n
