"""Memory retrieval (2026-10-17): resolve() ranks with SQLite FTS5 BM25 over stemmed words. Measured on a fixture written
before any resolver ran on it (fixtures/memory_retrieval.json): the word-overlap resolver of HEAD got hit@1 0.936 and MRR
0.957 there; these tests hold the new one to its measured quality and keep the old safety rules: no candidate without a
shared word, never a wrong 'unique', no copy of any text left on disk."""
import json
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from jarvis_kernel import memory as M
from jarvis_kernel.store import Kernel

FIXTURE = os.path.join(os.path.dirname(__file__), "fixtures", "memory_retrieval.json")


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.k = Kernel(os.path.join(self.tmp.name, "d"))
        self.m = M.Memories(self.k)

    def tearDown(self):
        self.k.db.close(); self.tmp.cleanup()

    def fill(self, texts):
        return [self.m.add("o", t, "fact")["id"] for t in texts]


class Fixture(Base):
    def test_hit_at_1_and_mrr_on_the_fixture(self):
        with open(FIXTURE, encoding="utf-8") as f:
            fx = json.load(f)
        ids = self.fill(fx["memories"])
        hits, rr, wrong_unique = 0, 0.0, []
        for case in fx["queries"]:
            r = self.m.resolve("o", case["q"])
            got, want = [c["id"] for c in r["candidates"]], ids[case["want"]]
            hits += got[:1] == [want]
            rr += 1.0 / (got.index(want) + 1) if want in got else 0.0
            if r["unique"] and got[:1] != [want]:
                wrong_unique.append(case["q"])
        n = len(fx["queries"])
        self.assertGreaterEqual(n, 30)
        self.assertGreaterEqual(hits / n, 0.97, f"hit@1 {hits / n:.3f}")       # HEAD's overlap resolver: 0.936
        self.assertGreaterEqual(rr / n, 0.98, f"MRR {rr / n:.3f}")               # HEAD: 0.957
        self.assertEqual(wrong_unique, [])                                        # forget-by-content acts on 'unique'


class Rules(Base):
    def test_a_plural_or_other_form_of_the_same_word_is_found(self):
        a, b = self.fill(["Allergic to peanuts; check every recipe for nuts", "Likes spicy food but mild curries for guests"])
        r = self.m.resolve("o", "nut allergy in recipes")
        self.assertEqual([c["id"] for c in r["candidates"]], [a])                # HEAD: no candidate at all

    def test_a_rare_shared_word_outranks_a_common_one(self):
        a, b, c = self.fill(["Prefers trains over buses for trips under 500 km", "Prefers a window seat on short train rides",
                             "Short answers on the phone"])
        r = self.m.resolve("o", "train or bus for short trips")
        self.assertEqual(r["candidates"][0]["id"], a)                             # HEAD: the short train ride first

    def test_no_shared_word_is_no_candidate(self):
        self.fill(["Use dark mode in every app", "Passport is kept in the bedroom safe"])
        for q in ("what is my blood group", "", "?!? ...", "a an to is it", "​​", "x" * 2_000_000):
            r = self.m.resolve("o", q)
            self.assertEqual(r, {"unique": False, "candidates": []}, q[:20])

    def test_unique_never_more_readily_than_the_overlap_rule(self):
        self.fill(["I prefer dark mode", "I prefer snake_case file names for delivered code"])
        self.assertFalse(self.m.resolve("o", "I prefer")["unique"])
        r = self.m.resolve("o", "snake_case file names")
        self.assertTrue(r["unique"]); self.assertIn("snake_case", r["candidates"][0]["text"])
        # BM25 and the overlap score disagree on the winner: not unique
        with mock.patch.object(M, "_fts_rank", lambda q, texts: ({0: -9.0, 1: -1.0}, {"prefer", "snake", "case", "file", "name"},
                                                                 {0: {"prefer", "dark", "mode"}, 1: {"prefer", "snake", "case", "file", "name", "deliv", "code"}})):
            r = self.m.resolve("o", "prefer snake_case file names")
        self.assertEqual(r["candidates"][0]["text"], "I prefer dark mode")
        self.assertFalse(r["unique"])

    def test_without_fts5_the_overlap_ranking_is_used(self):
        a, b = self.fill(["Prefers an aisle seat on long flights", "Book the front seat in a taxi"])
        with mock.patch.object(M, "_fts_rank", lambda q, texts: None):
            r = self.m.resolve("o", "aisle seat")
            self.assertEqual([c["id"] for c in r["candidates"]], [a, b])
            self.assertEqual(self.m.resolve("o", "nut allergy"), {"unique": False, "candidates": []})

    def test_no_index_or_text_copy_is_kept_on_disk(self):
        (a,) = self.fill(["Locker code is behind the blue door"])
        self.m.resolve("o", "locker blue door")
        names = {r[0] for r in self.k.db.execute("SELECT name FROM sqlite_master").fetchall()}
        self.assertFalse({n for n in names if "fts" in n.lower() or n in ("d", "q", "dv", "qv")}, names)
        self.m.forget("o", a)
        self.assertEqual(self.m.resolve("o", "locker blue door")["candidates"], [])


if __name__ == "__main__":
    unittest.main()
