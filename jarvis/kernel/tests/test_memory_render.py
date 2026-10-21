"""The session block (Memories.render) an agent starts every session and every compaction from (MEM-M1, 2026-10-18).

The audit found the old block was oldest-first and cut at 1600 characters: after about 14 entries every newer
preference, every correction saved as a new entry and every activated lesson was missing from the agent's prompt.
Now: bounded by the cap, newest first (an update brings an entry forward), lessons and corrections ahead, the current
task's query brings relevant old entries in, similar (possibly conflicting) entries are marked with the newer one
current, each line shows its provenance, and the last line says how many were left out and how to list them."""
import os
import re
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from jarvis_kernel import scrub as S                                  # noqa: E402
from jarvis_kernel.memory import Memories                             # noqa: E402
from jarvis_kernel.store import Kernel                                # noqa: E402

OWNER = "hermes"
T0 = 1_760_000_000.0            # 2025-10-09 UTC: fixed times, so every order below is deterministic
DAY = 86400.0
TOPICS = ["coffee", "trains", "music", "sleep", "fonts", "budget", "garden", "cricket", "laptop", "weather", "recipes",
          "invoices", "printer", "photos", "language", "calendar", "emails", "reading", "gym", "groceries", "movies",
          "podcasts", "taxes", "plants", "cooking", "cycling", "chess", "museums", "temples", "markets", "tea", "biryani",
          "scooter", "monsoon", "festivals", "cinema", "football", "painting", "yoga", "guitar"]
LINE = re.compile(r"^\[m-[0-9a-f]{8} v\d+\] \((preference|fact|instruction|lesson)\) "
                  r"\{default, added \d{4}-\d\d-\d\d(, updated \d{4}-\d\d-\d\d)?(; [^}]*)?\} \S")


def varied(i):
    """Distinct entries: no two are similar by the lesson gate's measure (they share only 'for the owner')."""
    t = TOPICS[i % len(TOPICS)]
    return f"{t.capitalize()}: x{i}alpha x{i}beta x{i}gamma x{i}delta for the owner"


class Render(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.k = Kernel(os.path.join(self.tmp.name, "k"))
        self.m = Memories(self.k)
        self._gl = dict(S._GL)
        S._GL.update(path=None, checked=True)        # fixture filling only: skip the gitleaks subprocess (built-in pass still runs)

    def tearDown(self):
        S._GL.clear(); S._GL.update(self._gl)
        self.k.db.close(); self.tmp.cleanup()

    def put(self, text, at, kind="preference", owner=OWNER):
        e = self.m.add(owner, text, kind)
        self.k.db.execute("UPDATE memories SET created=?, updated=? WHERE id=?", (at, at, e["id"]))
        return e["id"]

    def fill(self, n, start=T0, step=60.0, owner=OWNER):
        return [self.put(varied(i), start + i * step, owner=owner) for i in range(n)]

    def entry_lines(self, out):
        return [x for x in out.splitlines() if x.startswith("[m-")]

    def test_more_than_14_entries_keeps_the_newest(self):
        self.fill(30)
        newest = self.put("NEWEST: the owner now prefers aisle seats on every flight", T0 + DAY)
        out = self.m.render(OWNER)
        self.assertIn(newest, out)
        self.assertTrue(self.entry_lines(out)[-1].startswith(f"[{newest} v1]"))     # shown last: nearest the conversation
        self.assertRegex(out.splitlines()[-1], r"^\.\.\. \d+ more \(.*jarvis_memory_list")

    def test_the_audits_repro_keeps_the_newest(self):
        """The audit's R1 case verbatim: 30 near-identical preferences, then one new one."""
        for i in range(30):
            self.put(f"Preference number {i:02d}: the owner likes option {i} for recurring situation {i} in daily life", T0 + i)
        newest = self.put("NEWEST: the owner now prefers aisle seats on every flight", T0 + 100)
        out = self.m.render(OWNER)
        self.assertIn("NEWEST", out); self.assertIn(newest, out)

    def test_a_query_brings_in_the_relevant_old_entry(self):
        old = self.put("The owner's passport renewal office is in Begumpet; book the slot online", T0)
        self.fill(40, start=T0 + DAY)
        self.assertNotIn(old, self.m.render(OWNER))                                 # by recency alone it is left out
        out = self.m.render(OWNER, query="renew my passport: book a slot")
        self.assertIn(old, out)
        self.assertIn(self.m.list(OWNER)[-1]["id"], out)                           # recency still counts: the newest stays
        self.assertNotIn(old, self.m.render(OWNER, query="zzz qqq"))               # no shared word: recency order

    def test_the_cap_is_respected_and_the_omitted_count_is_exact(self):
        self.fill(60)
        for limit in (300, 600, 1000, 1600, 2500):
            for q in (None, "trains coffee budget"):
                out = self.m.render(OWNER, limit=limit, query=q)
                self.assertLessEqual(len(out), limit, (limit, q))
                shown = len(self.entry_lines(out))
                self.assertGreater(shown, 0)
                self.assertTrue(out.splitlines()[-1].startswith(f"... {60 - shown} more"), (limit, q, out.splitlines()[-1]))
                for ln in self.entry_lines(out):
                    self.assertRegex(ln, LINE)                                      # provenance in every line
        everything = self.m.render(OWNER, limit=100_000)
        self.assertEqual(len(self.entry_lines(everything)), 60); self.assertNotIn("more (", everything)

    def test_lane_and_agent_isolation_unchanged(self):
        self.fill(5)
        self.assertEqual(self.m.render("labagent"), "")
        self.assertEqual(self.m.render("labagent", query="trains coffee"), "")
        self.assertEqual(self.m.render(OWNER, lane="public"), "")                   # the same name in another lane
        self.assertEqual(len(self.entry_lines(self.m.render(OWNER))), 5)
        self.m.add(OWNER, "Step 3 of 5 done: hotel shortlist saved", "fact", scope="task-42")
        self.assertNotIn("hotel", self.m.render(OWNER))                             # scopes stay apart

    def test_an_update_brings_the_entry_forward(self):
        ids = self.fill(30)
        self.assertNotIn(ids[0], self.m.render(OWNER))
        self.m.update(OWNER, ids[0], "About coffee: the owner now drinks only filter coffee, no sugar", 1)
        out = self.m.render(OWNER)
        last = self.entry_lines(out)[-1]
        self.assertTrue(last.startswith(f"[{ids[0]} v2] (preference)"), last)       # kept, and in the newest place
        self.assertIn(", updated ", last); self.assertIn("filter coffee", last)
        self.assertNotIn("x0alpha", out)                                            # only the latest version is shown

    def test_order_is_deterministic(self):
        self.fill(40)
        a, b = self.m.render(OWNER), self.m.render(OWNER)
        self.assertEqual(a, b)
        self.assertEqual(self.m.render(OWNER, query="garden budget"), self.m.render(OWNER, query="garden budget"))
        self.k.db.execute("UPDATE memories SET created=?, updated=?", (T0, T0))       # all at the same moment: ids decide
        out = self.m.render(OWNER, limit=900)
        shown = [ln[1:11] for ln in self.entry_lines(out)]
        everyone = sorted(e["id"] for e in self.m.list(OWNER))
        self.assertEqual(shown, sorted(shown)); self.assertEqual(shown[0], everyone[0])
        self.assertLess(len(shown), 40)
        self.assertEqual(out, self.m.render(OWNER, limit=900))

    def test_lessons_and_corrections_are_kept_ahead_of_newer_plain_entries(self):
        fixed = self.put("Deliver code files with a README.md", T0)
        self.m.update(OWNER, fixed, "Deliver code files without README; usage notes go in the docstring", 1)
        self.k.db.execute("UPDATE memories SET updated=? WHERE id=?", (T0 + 60, fixed))
        lesson = self.m.add_candidate(OWNER, "When the printer queue stalls, restart the spooler service then retry once", "t1")
        res = self.m.decide_lesson(OWNER, lesson, {"state": "completed", "verification": {"passed": True}, "attempts": 2, "events": []}, True)
        self.assertEqual(res["state"], "active")
        self.k.db.execute("UPDATE memories SET created=?, updated=? WHERE id=?", (T0 + 120, T0 + 120, lesson))
        self.fill(40, start=T0 + 2 * DAY)
        out = self.m.render(OWNER)
        self.assertIn(f"[{fixed} v2]", out); self.assertIn(f"[{lesson} v1] (lesson)", out)
        self.assertIn(self.m.list(OWNER)[-1]["id"], out)

    def test_similar_entries_are_marked_and_the_newer_one_is_current(self):
        old = self.put("The owner prefers window seats on every flight", T0)
        new = self.put("The owner now prefers aisle seats on every flight, not window seats", T0 + DAY)
        out = self.m.render(OWNER)
        lines = self.entry_lines(out)
        self.assertEqual(len(lines), 2)                                             # both shown: never merged
        self.assertIn(f"similar to newer {new}: if they disagree, {new} is current", lines[0])
        self.assertIn("newest of 2 similar entries: if they disagree, this one is current", lines[1])
        self.assertTrue(lines[0].startswith(f"[{old} v1]"))
        self.fill(40, start=T0 + 2 * DAY)
        out = self.m.render(OWNER)
        self.assertIn(new, out); self.assertNotIn(f"[{old} v1]", out)               # when space is short the older one goes first


if __name__ == "__main__":
    unittest.main()
