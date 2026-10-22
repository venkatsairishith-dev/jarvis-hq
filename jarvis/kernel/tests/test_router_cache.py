import ast
import os
import sys
import threading
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from jarvis_kernel.router import ResponseCache, cached_call
from jarvis_kernel.router import cache as cache_mod


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


def body(text="hi", **kw):
    return {"model": "m", "temperature": 0, "messages": [{"role": "user", "content": text}], **kw}


def resp(text="yo", finish="stop", **msg):
    return {"choices": [{"index": 0, "finish_reason": finish, "message": {"role": "assistant", "content": text, **msg}}]}


class CacheTests(unittest.TestCase):
    def setUp(self):
        self.c = Clock()
        self.cache = ResponseCache(max_entries=3, ttl_s=60, max_entry_bytes=1000, clock=self.c)

    def k(self, b=None, lane="public", prov="p", model="m", who="a"):
        return self.cache.key(lane, prov, model, b or body(), who)

    def put(self, k, b=None, r=None):
        return self.cache.put(k, r or resp(), cacheable_request=b or body(), lane="public", provider="p")

    def test_hit_miss_and_stats(self):
        k = self.k()
        self.assertIsNone(self.cache.get(k))
        self.assertTrue(self.put(k))
        self.assertEqual(self.cache.get(k), resp())
        s = self.cache.stats()
        self.assertEqual((s["hits"], s["misses"], s["entries"]), (1, 1, 1))

    def test_ttl(self):
        k = self.k()
        self.put(k)
        self.c.t += 59
        self.assertIsNotNone(self.cache.get(k))
        self.c.t += 2
        self.assertIsNone(self.cache.get(k))
        self.assertEqual(self.cache.stats()["entries"], 0)

    def test_lru_order(self):
        ks = [self.k(body(str(i))) for i in range(4)]
        for i in range(3):
            self.put(ks[i], body(str(i)))
        self.cache.get(ks[0])
        self.put(ks[3], body("3"))
        self.assertIsNone(self.cache.get(ks[1]))
        self.assertIsNotNone(self.cache.get(ks[0]))
        self.assertEqual(self.cache.stats()["evictions"], 1)

    def test_key_separation(self):
        keys = {self.k(), self.k(lane="private"), self.k(prov="q"), self.k(model="n"), self.k(who="b"), self.k(body("other"))}
        self.assertEqual(len(keys), 6)

    def test_volatile_ignored(self):
        b2 = body(stream=True, user="u", metadata={"a": 1}, stream_options={"x": 1})
        self.assertEqual(self.k(), self.k(b2))
        b = body(a=1, b=2)
        self.assertEqual(self.k(b), self.k(dict(reversed(list(b.items())))))

    def test_refusals(self):
        cases = {
            "stream": body(stream=True), "temp": body(temperature=0.7), "tools": body(tools=[{"type": "function"}]),
            "functions": body(functions=[{}]), "tool_choice": body(tool_choice="auto"), "n": body(n=2),
            "image": {"model": "m", "messages": [{"role": "user", "content": [{"type": "image_url", "image_url": {"url": "x"}}]}]},
            "pw": body("my pw is [PASSWORD_1]"), "email": body("mail [EMAIL_2]"),
        }
        for name, b in cases.items():
            with self.subTest(name):
                self.assertFalse(self.put(self.k(b), b))
        self.assertEqual(self.cache.stats()["entries"], 0)
        ok = body(temperature=0, n=1)
        self.assertTrue(self.put(self.k(ok), ok))

    def test_response_refusals(self):
        for name, r in {"length": resp(finish="length"), "tool_calls": resp(finish="tool_calls"),
                        "cf": resp(finish="content_filter"), "tc_in_stop": resp(tool_calls=[{"id": "1"}]),
                        "big": resp("x" * 2000)}.items():
            with self.subTest(name):
                self.assertFalse(self.put(self.k(), r=r))
        self.assertEqual(self.cache.stats()["refused"], 5)

    def test_placeholder_never_served(self):
        b = body("[EMAIL_1]")
        calls = []
        out = [cached_call(self.cache, "public", "p", "m", b, "a", lambda: calls.append(1) or resp()) for _ in range(2)]
        self.assertEqual([o[1] for o in out], ["bypass", "bypass"])
        self.assertEqual(len(calls), 2)

    def test_copies(self):
        k = self.k()
        r = resp()
        self.put(k, r=r)
        r["choices"][0]["message"]["content"] = "mutated"
        g = self.cache.get(k)
        g["choices"][0]["message"]["content"] = "mutated2"
        self.assertEqual(self.cache.get(k), resp())

    def test_invalidate(self):
        kp, kq = self.k(lane="public"), self.k(body("z"), lane="private")
        self.cache.put(kp, resp(), cacheable_request=body(), lane="public", provider="p")
        self.cache.put(kq, resp(), cacheable_request=body("z"), lane="private", provider="q")
        self.assertEqual(self.cache.invalidate_lane("private"), 1)
        self.assertIsNotNone(self.cache.get(kp))
        self.assertIsNone(self.cache.get(kq))
        self.assertEqual(self.cache.invalidate_provider("p"), 1)
        self.put(kp)
        self.cache.clear()
        self.assertEqual(self.cache.stats()["entries"], 0)

    def test_cached_call(self):
        calls = []

        def f():
            calls.append(1)
            return resp()
        a = cached_call(self.cache, "public", "p", "m", body(), "a", f)
        b = cached_call(self.cache, "public", "p", "m", body(), "a", f)
        c = cached_call(self.cache, "public", "p", "m", body(), "other", f)
        d = cached_call(self.cache, "public", "p", "m", body(stream=True), "a", f)
        self.assertEqual([a[1], b[1], c[1], d[1]], ["miss", "hit", "miss", "bypass"])
        self.assertEqual(len(calls), 3)

    def test_threads(self):
        cache = ResponseCache(max_entries=20, ttl_s=60, clock=self.c)
        errs = []

        def work(i):
            try:
                for j in range(200):
                    b = body(str(j % 40))
                    k = cache.key("public", "p", "m", b, str(i))
                    cache.put(k, resp(str(j)), cacheable_request=b, lane="public", provider="p")
                    cache.get(k)
            except Exception as e:  # pragma: no cover
                errs.append(e)
        ts = [threading.Thread(target=work, args=(i,)) for i in range(16)]
        for t in ts:
            t.start()
        for t in ts:
            t.join()
        self.assertEqual(errs, [])
        self.assertLessEqual(cache.stats()["entries"], 20)

    def test_no_persistence(self):
        with cache_mod_source() as src:
            tree = ast.parse(src)
        mods = set()
        for n in ast.walk(tree):
            if isinstance(n, ast.Import):
                mods |= {a.name.split(".")[0] for a in n.names}
            elif isinstance(n, ast.ImportFrom):
                mods.add((n.module or "").split(".")[0])
            elif isinstance(n, ast.Call) and isinstance(n.func, ast.Name):
                self.assertNotIn(n.func.id, ("open", "exec", "eval"))
        self.assertFalse(mods & {"pickle", "shelve", "sqlite3", "marshal", "os", "pathlib", "io", "tempfile"})


class cache_mod_source:
    def __enter__(self):
        self.f = open(cache_mod.__file__, encoding="utf-8")
        return self.f.read()

    def __exit__(self, *a):
        self.f.close()


class TemperatureRule(unittest.TestCase):
    def test_an_absent_temperature_is_never_cached_or_served(self):
        from jarvis_kernel.router.cache import request_cacheable
        no_temp = {"model": "m", "messages": [{"role": "user", "content": "hi"}]}
        self.assertFalse(request_cacheable(no_temp))
        self.assertTrue(request_cacheable({**no_temp, "temperature": 0}))
        self.assertTrue(request_cacheable({**no_temp, "temperature": 0.0}))
        self.assertFalse(request_cacheable({**no_temp, "temperature": 0.2}))
        self.assertFalse(request_cacheable({**no_temp, "temperature": False}))
        calls = []
        cache = ResponseCache()
        for _ in range(2):
            out, how = cached_call(cache, "public", "p", "m", no_temp, "a", lambda: calls.append(1) or resp())
            self.assertEqual(how, "bypass")
        self.assertEqual(len(calls), 2)


if __name__ == "__main__":
    unittest.main()
