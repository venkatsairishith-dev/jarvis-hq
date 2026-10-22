import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from jarvis_kernel.gateway import Provider
from jarvis_kernel.router import (Budget, CircuitBreaker, ComboError, KeyPool, NoKey, NoRoute, Quota, RouteDenied,
                                  Router, load_combos, resolve)

SECRET_A, SECRET_B = "sk-SECRETAAAA1111", "sk-SECRETBBBB2222"


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


def prov(name, lane, paid=False):
    return Provider(name=name, lane=lane, base_url="http://x/v1", key_file=None, models=["any"], paid=paid)


PROVIDERS = [prov("priv1", "private"), prov("priv2", "private"), prov("pub1", "public"), prov("pub2", "public"),
             prov("costly", "public", paid=True)]


def combos_data(*extra):
    return {"combos": [{"name": "chat-priv", "lane": "private", "targets": [["priv1", "m1"], ["priv2", "m2"]]},
                       {"name": "chat-pub", "lane": "public", "targets": [["pub1", "m1"], ["pub2", "m2"]]}, *extra]}


class BreakerTests(unittest.TestCase):
    def setUp(self):
        self.c = Clock()
        self.b = CircuitBreaker(failure_threshold=3, cooldown_s=60, clock=self.c)

    def test_opens_after_threshold_of_429(self):
        for _ in range(2):
            self.b.record_failure(429)
        self.assertEqual(self.b.state(), "closed")
        self.b.record_failure(429)
        self.assertEqual(self.b.state(), "open")
        self.assertFalse(self.b.allow())

    def test_5xx_counts_other_4xx_do_not(self):
        for s in (400, 401, 403, 404, 422) * 3:
            self.b.record_failure(s)
        self.assertEqual(self.b.state(), "closed")
        for _ in range(3):
            self.b.record_failure(503)
        self.assertEqual(self.b.state(), "open")

    def test_half_open_one_probe_then_close(self):
        for _ in range(3):
            self.b.record_failure(500)
        self.c.t += 59
        self.assertFalse(self.b.allow())
        self.c.t += 1
        self.assertEqual(self.b.state(), "half_open")
        self.assertTrue(self.b.allow())
        self.assertFalse(self.b.allow())          # only ONE probe
        self.b.record_success()
        self.assertEqual(self.b.state(), "closed")
        self.assertTrue(self.b.allow())

    def test_failed_probe_reopens(self):
        for _ in range(3):
            self.b.record_failure(429)
        self.c.t += 60
        self.assertTrue(self.b.allow())
        self.b.record_failure(500)
        self.assertEqual(self.b.state(), "open")
        self.assertFalse(self.b.allow())
        self.c.t += 60
        self.assertTrue(self.b.allow())

    def test_success_resets_failure_count(self):
        self.b.record_failure(429)
        self.b.record_failure(429)
        self.b.record_success()
        self.b.record_failure(429)
        self.assertEqual(self.b.state(), "closed")

    def test_snapshot(self):
        for _ in range(3):
            self.b.record_failure(429)
        s = self.b.snapshot()
        self.assertEqual(s["state"], "open")
        self.assertEqual(s["retry_in_s"], 60)


class KeyPoolTests(unittest.TestCase):
    def setUp(self):
        self.c = Clock()
        self.p = KeyPool([SECRET_A, SECRET_B], cooldown_s=30, clock=self.c)

    def test_least_recently_used_rotation(self):
        self.assertEqual([self.p.next() for _ in range(4)], [SECRET_A, SECRET_B, SECRET_A, SECRET_B])

    def test_rate_limited_key_cools_then_returns(self):
        self.p.mark_rate_limited(SECRET_A)
        self.assertEqual(self.p.next(), SECRET_B)
        self.assertEqual(self.p.next(), SECRET_B)
        self.c.t += 30
        self.assertEqual(self.p.next(), SECRET_A)

    def test_retry_after_overrides_cooldown(self):
        self.p.mark_rate_limited(SECRET_A, retry_after=5)
        self.p.mark_rate_limited(SECRET_B, retry_after=100)
        with self.assertRaises(NoKey):
            self.p.next()
        self.c.t += 5
        self.assertEqual(self.p.next(), SECRET_A)

    def test_bad_key_removed_until_reset(self):
        self.p.mark_bad(SECRET_A)
        self.assertEqual({self.p.next(), self.p.next()}, {SECRET_B})
        self.p.reset()
        self.assertIn(SECRET_A, {self.p.next(), self.p.next()})

    def test_no_key_raises(self):
        self.p.mark_bad(SECRET_A)
        self.p.mark_bad(SECRET_B)
        with self.assertRaises(NoKey):
            self.p.next()

    def test_no_secret_in_text(self):
        self.p.mark_rate_limited(SECRET_A)
        self.p.mark_bad(SECRET_B)
        with self.assertRaises(NoKey) as cm:
            self.p.next()
        with self.assertRaises(KeyError) as cm2:
            self.p.mark_bad("sk-UNKNOWN9999")
        for text in (str(cm.exception), repr(cm.exception), str(cm2.exception), repr(cm2.exception), repr(self.p),
                     str(self.p.snapshot())):
            for s in (SECRET_A, SECRET_B, "sk-UNKNOWN9999", "SECRET"):
                self.assertNotIn(s, text)
        self.assertEqual([r["key"] for r in self.p.snapshot()["keys"]], ["key#1", "key#2"])


class QuotaTests(unittest.TestCase):
    def test_sliding_window(self):
        c = Clock()
        q = Quota(3, 10, clock=c)
        self.assertTrue(q.try_take())
        c.t += 4
        self.assertTrue(q.try_take(2))
        self.assertFalse(q.try_take())
        self.assertEqual(q.remaining(), 0)
        self.assertEqual(q.reset_in(), 6)
        c.t += 6                                   # first event leaves the window, the later two stay
        self.assertEqual(q.remaining(), 1)
        self.assertTrue(q.try_take())
        self.assertFalse(q.try_take())
        c.t += 10
        self.assertEqual(q.remaining(), 3)
        self.assertEqual(q.reset_in(), 0)

    def test_tokens_and_oversize(self):
        q = Quota(1000, 60, clock=Clock())
        self.assertFalse(q.try_take(1001))
        self.assertTrue(q.try_take(600))
        self.assertFalse(q.try_take(401))
        self.assertTrue(q.try_take(400))


class BudgetTests(unittest.TestCase):
    def test_default_zero_refuses_any_cost(self):
        b = Budget()
        self.assertTrue(b.check(0.0))
        self.assertFalse(b.check(0.0001))

    def test_limit_and_never_negative(self):
        b = Budget(1.0)
        self.assertTrue(b.check(1.0))
        b.add(0.75)
        self.assertFalse(b.check(0.5))
        self.assertFalse(b.check(-1.0))
        with self.assertRaises(ValueError):
            b.add(-0.1)
        with self.assertRaises(ValueError):
            Budget(-1)


class ComboTests(unittest.TestCase):
    def test_loads_and_resolves(self):
        cs = load_combos(combos_data(), PROVIDERS)
        self.assertEqual([t.provider for t in resolve(cs["chat-priv"], "private")], ["priv1", "priv2"])

    def test_public_token_cannot_resolve_private_combo(self):
        cs = load_combos(combos_data(), PROVIDERS)
        with self.assertRaises(RouteDenied):
            resolve(cs["chat-priv"], "public")
        with self.assertRaises(RouteDenied):
            resolve(cs["chat-pub"], "private")

    def test_mixed_lane_rejected(self):
        bad = {"name": "mix", "lane": "public", "targets": [["pub1", "m"], ["priv1", "m"]]}
        with self.assertRaisesRegex(ComboError, "lane mismatch"):
            load_combos(combos_data(bad), PROVIDERS)

    def test_paid_rejected(self):
        with self.assertRaisesRegex(ComboError, "paid"):
            load_combos(combos_data({"name": "p", "lane": "public", "targets": [["costly", "m"]]}), PROVIDERS)

    def test_empty_unknown_duplicate_rejected(self):
        for bad in ({"name": "e", "lane": "public", "targets": []},
                    {"name": "u", "lane": "public", "targets": [["nope", "m"]]},
                    {"name": "chat-pub", "lane": "public", "targets": [["pub1", "m"]]},
                    {"name": "l", "lane": "local", "targets": [["pub1", "m"]]}):
            with self.assertRaises(ComboError):
                load_combos(combos_data(bad), PROVIDERS)


class RouterTests(unittest.TestCase):
    def setUp(self):
        self.c = Clock()
        cs = load_combos(combos_data(), PROVIDERS)
        self.keys = {"pub1": KeyPool([SECRET_A], clock=self.c), "pub2": KeyPool([SECRET_B], clock=self.c)}
        self.br = {n: CircuitBreaker(2, 60, clock=self.c) for n in ("priv1", "priv2", "pub1", "pub2")}
        self.r = Router(cs, self.br, self.keys, {"pub1": Quota(2, 10, clock=self.c)}, {})

    def test_picks_first_then_falls_back_with_reasons(self):
        p = self.r.pick("chat-pub", "public")
        self.assertEqual(p.target.provider, "pub1")
        self.r.report(p.target, 500, key=p.key)
        self.r.report(p.target, 500, key=p.key)
        p2 = self.r.pick("chat-pub", "public")
        self.assertEqual(p2.target.provider, "pub2")
        self.assertEqual(p2.skipped, [("pub1", "breaker open")])

    def test_rate_limit_cools_key(self):
        p = self.r.pick("chat-pub", "public")
        self.r.report(p.target, 429, key=p.key, retry_after=20)
        p2 = self.r.pick("chat-pub", "public")
        self.assertEqual((p2.target.provider, p2.skipped), ("pub2", [("pub1", "no key available")]))
        self.c.t += 20
        self.assertEqual(self.r.pick("chat-pub", "public").target.provider, "pub1")

    def test_quota_and_budget_skip(self):
        self.r.pick("chat-pub", "public")
        self.r.pick("chat-pub", "public")
        p = self.r.pick("chat-pub", "public")
        self.assertEqual((p.target.provider, p.skipped), ("pub2", [("pub1", "quota exhausted")]))
        with self.assertRaises(NoRoute) as cm:
            self.r.pick("chat-pub", "public", est_cost=0.01)
        self.assertEqual(cm.exception.skipped, [("pub1", "quota exhausted"), ("pub2", "budget exceeded")])

    def test_budget_allows_paid_amount_only_when_set(self):
        self.r.budgets["pub1"] = Budget(0.05)
        p = self.r.pick("chat-pub", "public", est_cost=0.03)
        self.assertEqual(p.target.provider, "pub1")
        self.r.report(p.target, 200, cost=0.03)
        self.assertFalse(self.r.budgets["pub1"].check(0.03))

    def test_public_token_cannot_reach_private_combo(self):
        with self.assertRaises(RouteDenied):
            self.r.pick("chat-priv", "public")

    def test_no_route_lists_reasons_without_secrets(self):
        for n in ("pub1", "pub2"):
            self.keys[n].mark_bad(SECRET_A if n == "pub1" else SECRET_B)
        with self.assertRaises(NoRoute) as cm:
            self.r.pick("chat-pub", "public")
        text = str(cm.exception) + repr(cm.exception.skipped)
        self.assertEqual(len(cm.exception.skipped), 2)
        self.assertNotIn("SECRET", text)

    def test_pick_repr_hides_key(self):
        p = self.r.pick("chat-pub", "public")
        self.assertEqual(p.key, SECRET_A)
        self.assertNotIn("SECRET", repr(p))

    def test_half_open_probe_taken_once(self):
        for _ in range(2):
            self.r.report(self.r.combos["chat-priv"].targets[0], 503)
        self.c.t += 60
        self.assertEqual(self.r.pick("chat-priv", "private").target.provider, "priv1")
        self.assertEqual(self.r.pick("chat-priv", "private").target.provider, "priv2")


if __name__ == "__main__":
    unittest.main()
