"""Review addition: the router is used from many server threads; pick() must be atomic."""
import os
import sys
import threading
import time
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from jarvis_kernel.gateway import Provider
from jarvis_kernel.router import CircuitBreaker, NoRoute, Quota, Router, load_combos


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


class SlowBreaker(CircuitBreaker):
    """Widens the gap between "may I?" and "I did", the window a missing lock leaves open."""

    def would_allow(self):
        ok = super().would_allow()
        time.sleep(0.002)
        return ok


class SlowQuota(Quota):
    def can_take(self, n=1):
        ok = super().can_take(n)
        time.sleep(0.002)
        return ok


def one_target_router(**parts):
    prov = Provider(name="p", lane="public", base_url="http://x/v1", key_file=None, models=["any"])
    combos = load_combos({"combos": [{"name": "c", "lane": "public", "targets": [["p", "m"]]}]}, [prov])
    return Router(combos, parts.get("breakers", {}), {}, parts.get("quotas", {}), {})


def hammer(router, n=40):
    ok, refused, gate = [], [], threading.Barrier(n)

    def work():
        gate.wait()
        try:
            router.pick("c", "public")
            ok.append(1)
        except NoRoute:
            refused.append(1)

    threads = [threading.Thread(target=work) for _ in range(n)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    return len(ok), len(refused)


class Concurrency(unittest.TestCase):
    def test_only_one_thread_gets_the_half_open_probe(self):
        clock = Clock()
        br = SlowBreaker(failure_threshold=1, cooldown_s=10, clock=clock)
        br.record_failure(429)
        clock.t += 11                                   # cooldown over: half-open, one probe allowed
        self.assertEqual(hammer(one_target_router(breakers={"p": br})), (1, 39))

    def test_a_quota_of_five_lets_exactly_five_threads_through(self):
        clock = Clock()
        q = SlowQuota(5, 60, clock=clock)
        self.assertEqual(hammer(one_target_router(quotas={"p": q})), (5, 35))


if __name__ == "__main__":
    unittest.main()
