"""Regression tests added after mutation testing of visible.py (2026-10-19): token boundaries around an invisible character,
variation selectors, look-alike host names and the plain-text neutralizer."""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from jarvis_kernel import visible as V

ZWJ, ZWNJ, ZWSP = "‍", "‌", "​"
KA, SSA = "क", "ष"            # Devanagari letters


def counts(s, strict=False):
    r = V.Report()
    r.text(s, "text", strict)
    return r.hostile, r.benign, r.status()


class Boundaries(unittest.TestCase):
    def test_joiner_between_indic_letters_is_benign_in_plain_text(self):
        self.assertEqual(counts(f"{KA}{ZWJ}{SSA}"), (0, 1, "notice"))
        self.assertEqual(counts(f"{KA}{ZWJ}{SSA}", strict=True)[2], "blocked")

    def test_the_same_joiner_inside_an_address_is_hostile_wherever_the_dot_is(self):
        # the host-like part is only on ONE side of the invisible character: the whole token must be read
        for s in (f"a.{KA}{ZWJ}{SSA}", f"{KA}{ZWJ}{SSA}.a", f"go to x.{KA}{ZWJ}{SSA} now", f"https://{KA}{ZWJ}{SSA}"):
            with self.subTest(s=s):
                h, b, st = counts(s)
                self.assertEqual((h, b, st), (1, 0, "blocked"))
        # a space ends the token: the host-like word next to it does not poison the Hindi word
        self.assertEqual(counts(f"x.y {KA}{ZWJ}{SSA} a.b"), (0, 1, "notice"))

    def test_variation_selector_after_the_joiner_is_looked_over_to_the_real_neighbour(self):
        h, b, st = counts(f"{KA}{ZWJ}️{SSA}")
        self.assertEqual((h, b), (1, 1))                      # the selector is hostile there; the joiner still sees the letters
        self.assertEqual(counts(f"{KA}️{ZWJ}{SSA}")[:2], (1, 1))

    def test_ideographic_variation_selectors_need_a_cjk_neighbour(self):
        for vs in ("︀", "︅", "︍"):
            with self.subTest(vs=repr(vs)):
                self.assertEqual(counts("漢" + vs), (0, 1, "notice"))
                self.assertEqual(counts("a" + vs)[0], 1)
                self.assertEqual(counts(vs)[0], 1)


class HostFlags(unittest.TestCase):
    def test_lookalike_host_is_flagged_and_plain_host_is_not(self):
        appe = "\u0430\u0440\u0440\u0435.com"                  # all-Cyrillic look-alike of "appe.com"
        flags = V.host_flags(f"open https://{appe}/login please")
        self.assertEqual(len(flags), 1)
        self.assertIn("look-alike letters for the ASCII name appe.com", flags[0])
        self.assertEqual(V.host_flags("open https://appe.com/login"), [])
        r = V.Report()
        r.hosts(f"https://{appe}", "target")
        self.assertTrue(r.warned)
        self.assertEqual(r.status(), "warning")

    def test_mixed_script_label_is_flagged(self):
        flags = V.host_flags("https://pаypalο.com")
        self.assertEqual(len(flags), 1)
        self.assertIn("mixes scripts", flags[0])


class Neutralize(unittest.TestCase):
    def test_hostile_characters_are_written_out_and_benign_ones_stay(self):
        self.assertEqual(V.neutralize(f"a{ZWSP}b"), "a<U+200B>b")
        self.assertEqual(V.neutralize("a\x07b"), "a<U+0007>b")
        self.assertEqual(V.neutralize("\x1b[2Jx"), "<U+001B>[2Jx")
        self.assertEqual(V.neutralize(f"{KA}{ZWJ}{SSA}"), f"{KA}{ZWJ}{SSA}")
        self.assertEqual(V.neutralize(f"{KA}{ZWNJ}{SSA} x.y{ZWNJ}z"), f"{KA}{ZWNJ}{SSA} x.y<U+200C>z")
        self.assertEqual(V.neutralize("plain text\nline\t2"), "plain text\nline\t2")


class StrictPath(unittest.TestCase):
    def test_strict_and_free_fields(self):
        self.assertTrue(V.strict_path("target"))
        self.assertTrue(V.strict_path("params.url"))
        self.assertFalse(V.strict_path("params.prompt"))
        self.assertFalse(V.strict_path("params.items.2.text"))
        self.assertTrue(V.strict_path("card.recipient"))
        self.assertFalse(V.strict_path("card.body"))
        self.assertTrue(V.strict_path("params.args.to"))
        self.assertTrue(V.strict_path(""))


if __name__ == "__main__":
    unittest.main()
