"""M6 (review 2026-10-18): the owner must never approve text that the approval card hid. The digest always bound the full
request, but the card used to cut every value at 2000 characters (a lab_work instruction can be 8000 characters, a lab.task
prompt 20000), so a prompt-injected private agent could put private data after character 2000 and the owner's PIN approval
sent it to the public lab. Now the card holds the whole scrubbed text, or the request is refused; a card that is cut off
cannot be approved by anyone (owner HTTP, kernel console, Telegram all go through Kernel.approve)."""
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(__file__))
import test_tasks as TT                      # its Base gives a kernel + gateway over HTTP (not imported by name: no re-run)
from jarvis_kernel.owner import _hash_pin
from jarvis_kernel.policy import Action
from jarvis_kernel.store import CARD_MAX, Kernel

END = "ZZ-END-CANARY-8d41-SYNTH"             # synthetic: private data an injected agent hides at the very end of a long text
PIN = "correct horse battery"
SIZES = (1990, 2000, 2001, 2500, 8000)


def padded(n, prefix="harmless words "):
    """Exactly n characters ending with the canary."""
    body = (prefix * (n // len(prefix) + 1))[: n - len(END)]
    return body + END


class Cards(TT.Base):
    def setUp(self):
        super().setUp()
        self.con = self.tokens.issue("console", "owner")
        self.k.db.execute("INSERT OR REPLACE INTO owner_config(key, value) VALUES ('approval_pin', ?)", (_hash_pin(PIN),))

    def card(self, aid):
        import urllib.request
        r = urllib.request.Request(self.url + "/v1/jarvis/owner/state", headers={"Authorization": "Bearer " + self.con})
        st = json.loads(urllib.request.urlopen(r, timeout=10).read())
        return next(a for a in st["approvals"] if a["id"] == aid)

    def owner_approve(self, aid):
        return self.post("/v1/jarvis/owner/approve", {"id": aid, "confirm": aid, "pin": PIN}, self.con)

    # ---- lab.task (a private agent hands a prompt to a public-lane lab agent)
    def test_a_lab_task_prompt_is_shown_to_the_owner_to_its_last_character(self):
        for n in SIZES + (20000,):
            prompt = padded(n)
            code, r = self.post("/v1/jarvis/actions", {"tool": "lab", "op": "task", "target": f"sub-{n}", "params": {"prompt": prompt}}, self.owner)
            self.assertEqual((code, r["status"]), (202, "needs_approval"), n)
            card = self.card(r["approval_id"])
            shown = card["action"]["params"]["prompt"]
            self.assertTrue(shown.endswith(END), f"{n}: the canary at the end is not on the card")
            self.assertEqual(len(shown), n)
            self.assertFalse(card["truncated"])
            self.assertEqual(card["chars"] >= n, True)
            # approving is possible, and the approval is for exactly that text
            self.assertEqual(self.owner_approve(r["approval_id"])[0], 200, n)
            code, r2 = self.post("/v1/jarvis/actions", {"tool": "lab", "op": "task", "target": f"sub-{n}", "params": {"prompt": prompt},
                                                       "approval_id": r["approval_id"]}, self.owner)
            self.assertEqual((code, r2["status"]), (200, "allowed"), n)

    def test_the_digest_still_binds_the_full_text(self):
        prompt = padded(5000)
        code, r = self.post("/v1/jarvis/actions", {"tool": "lab", "op": "task", "target": "sub-d", "params": {"prompt": prompt}}, self.owner)
        self.assertEqual(self.owner_approve(r["approval_id"])[0], 200)
        # same first 2000 characters, different tail: a different action, the approval does not cover it
        other = prompt[:4990] + "OTHER-TAIL-"[:10]
        code, r2 = self.post("/v1/jarvis/actions", {"tool": "lab", "op": "task", "target": "sub-d", "params": {"prompt": other},
                                                   "approval_id": r["approval_id"]}, self.owner)
        self.assertEqual(code, 403)
        self.assertIn("different action", r2["reason"])

    # ---- lab_work (task dispatch): instructions up to 8000 characters, 20 criteria of 500
    def test_dispatch_instructions_and_criteria_are_shown_in_full(self):
        for n in (1990, 2000, 2001, 8000):
            code, t = self.act("create", f"Build thing {n}", {"goal": f"Build thing {n}", "criteria": ["it runs"], "request": f"build thing number {n} please"},
                               self.owner)
            self.assertEqual(code, 200)
            instr = padded(n, "write the code ")
            crit = ["criterion one", padded(500, "check ")]
            code, r = self.act("dispatch", t["task"], {"instructions": instr, "criteria": crit, "public_ok": True}, self.owner)
            self.assertEqual((code, r["status"]), (202, "needs_approval"), n)
            card = self.card(r["approval_id"])
            p = card["action"]["params"]
            self.assertTrue(p["instructions"].endswith(END), n)
            self.assertEqual(len(p["instructions"]), n)
            self.assertTrue(p["criteria"][1].endswith(END))
            self.assertFalse(card["truncated"])
            self.assertEqual(self.owner_approve(r["approval_id"])[0], 200)

    # ---- the fallback: a request that cannot be shown in full is refused, never shown cut
    def test_a_request_too_long_to_show_is_refused_and_creates_no_approval(self):
        a = Action("lab", "task", "sub-big", {"prompt": "x" * (CARD_MAX + 1)}, "private", True, requester="hermes")
        d, aid = self.k.request(a)
        self.assertEqual((d.name, aid), ("DENY", None))
        self.assertIn("cannot be approved", d.reason)
        self.assertEqual(self.k.db.execute("SELECT COUNT(*) FROM approvals").fetchone()[0], 0)
        ok = Action("lab", "task", "sub-fits", {"prompt": "x" * (CARD_MAX - 1000)}, "private", True, requester="hermes")
        d, aid = self.k.request(ok)
        self.assertTrue(aid)
        self.assertFalse(self.k.pending_approvals()[0]["truncated"])

    def test_a_private_key_in_the_request_is_refused_not_shown_as_a_placeholder(self):
        key = "-----BEGIN RSA PRIVATE KEY-----\nMIIBOgIBAAJBAKj34GkxFhD90vcNLYLInFEX6Ppy1tPf9Cnzj4p4WGeKLs1Pt8Qu\n-----END RSA PRIVATE KEY-----"
        d, aid = self.k.request(Action("lab", "task", "sub-key", {"prompt": "hello " + key}, "private", True))
        self.assertEqual((d.name, aid), ("DENY", None))

    def test_a_card_that_is_cut_off_cannot_be_approved_by_anyone(self):
        """An approval stored by an older kernel (cut at 2000) or damaged: owner HTTP, kernel console and Telegram all refuse."""
        a = Action("lab", "task", "sub-old", {"prompt": "x" * 100}, "private", True, requester="hermes")
        d, aid = self.k.request(a)
        cut = json.dumps(a.describe(2000) | {"params": {"prompt": "x" * 2000 + "...[truncated]"}})
        self.k.db.execute("UPDATE approvals SET action=? WHERE id=?", (cut, aid))
        self.assertTrue(self.card(aid)["truncated"])
        code, r = self.owner_approve(aid)
        self.assertEqual(code, 409, r)
        self.assertIn("cut off", json.dumps(r))
        self.assertFalse(self.k.approve(aid))
        self.assertEqual(self.k.approval_info(aid)["state"], "pending")
        # the damaged row (unreadable card) is the same
        d, aid2 = self.k.request(a)
        self.k.db.execute("UPDATE approvals SET action=? WHERE id=?", ("{not json", aid2))
        self.assertFalse(self.k.approve(aid2))
        self.assertTrue(self.k.deny(aid2))

    def test_audit_rows_still_carry_only_lengths_for_lab_text(self):
        prompt = padded(3000)
        self.post("/v1/jarvis/actions", {"tool": "lab", "op": "task", "target": "sub-a", "params": {"prompt": prompt}}, self.owner)
        rows = "".join(p for (p,) in self.k.db.execute("SELECT payload FROM audit").fetchall())
        self.assertNotIn("END-CANARY", rows)
        self.assertNotIn("harmless words", rows)


class ProjectApplyCard(unittest.TestCase):
    def test_the_card_carries_the_patch_and_the_digest_binds_it(self):
        import hashlib
        import tempfile
        from jarvis_kernel.jobs import action_for
        patch = "diff --git a/calc.py b/calc.py\n--- a/calc.py\n+++ b/calc.py\n@@ -1,2 +1,2 @@\n def add(a, b):\n-    return a - b\n+    return a + b\n" \
                + "".join(f"# line {i}\n" for i in range(400)) + f"# {END}\n"
        params = {"project": "demo", "base": "a" * 40, "patch": patch, "digest": hashlib.sha256(patch.encode()).hexdigest(), "files": ["calc.py"]}
        with tempfile.TemporaryDirectory() as d:
            k = Kernel(os.path.join(d, "k"))
            try:
                act = action_for("project_apply", params, "private", untrusted=True, requester="projectfix")
                act.card = {"patch": patch, "root_cause": "calc.py: add() subtracts"}
                digest_without = action_for("project_apply", params, "private", untrusted=True, requester="projectfix").digest()
                self.assertEqual(act.digest(), digest_without)                    # the card is display only
                dec, aid = k.request(act)
                self.assertTrue(aid)
                card = k.pending_approvals()[0]
                self.assertTrue(card["action"]["card"]["patch"].endswith(f"# {END}\n"))
                self.assertFalse(card["truncated"])
                self.assertNotIn("card", json.dumps([json.loads(p) for (p,) in k.db.execute("SELECT payload FROM audit").fetchall()]))
            finally:
                k.db.close()


class AggregateLimit(unittest.TestCase):
    """Re-review M15 (2026-10-19): the card limit is on the SUM of all the text the owner would read (keys and values of the
    whole card: tool, op, target, params at any depth, lane, requester, the extra `card`), not only per value. Values that are
    each short enough but together too long must be refused: no approval row of any kind, counted in the table itself.
    Every refused shape has a control of the same shape just under the limit, so a refusal is never an unrelated denial."""

    def setUp(self):
        import tempfile
        self.tmp = tempfile.TemporaryDirectory()
        self.k = Kernel(os.path.join(self.tmp.name, "k"))

    def tearDown(self):
        self.k.db.close()
        self.tmp.cleanup()

    def rows(self):
        return (self.k.db.execute("SELECT COUNT(*) FROM approvals").fetchone()[0],
                self.k.db.execute("SELECT COUNT(*) FROM approvals WHERE state='pending'").fetchone()[0])

    def refused(self, act, why="characters, more than the"):
        before = self.rows()
        d, aid = self.k.request(act)
        self.assertEqual((d.name, aid), ("DENY", None))
        self.assertIn(why, d.reason)
        self.assertEqual(self.rows(), before, "a refused request must not add an approval row")
        self.assertEqual(self.k.db.execute("SELECT COUNT(*) FROM approvals WHERE action_hash=?", (act.digest(),)).fetchone()[0], 0)
        self.assertEqual([x for x in self.k.pending_approvals() if x["digest"] == act.digest()[:12]], [])

    def accepted(self, act, *ends):
        before = self.rows()
        d, aid = self.k.request(act)
        self.assertTrue(aid, d.reason)
        self.assertEqual(self.rows(), (before[0] + 1, before[1] + 1))
        card = next(x for x in self.k.pending_approvals() if x["id"] == aid)
        self.assertFalse(card["truncated"])
        shown = json.dumps(card["action"], ensure_ascii=False)
        for e in ends:
            self.assertIn(e, shown, "the end of a value is missing from the card")
        self.assertTrue(self.k.approve(aid))
        return card

    @staticmethod
    def words(n, tag):
        """Exactly n characters, ending with a distinct synthetic tag."""
        return (("filler words " * (n // 13 + 1))[: n - len(tag)]) + tag

    def test_three_fields_of_60000_characters_are_refused_but_two_are_not(self):
        f = lambda t: self.words(60_000, t)
        self.refused(Action("fs", "write", "notes/a.md", {"one": f("END-ONE"), "two": f("END-TWO"), "three": f("END-THREE")}))
        self.accepted(Action("fs", "write", "notes/a.md", {"one": f("END-ONE"), "two": f("END-TWO")}), "END-ONE", "END-TWO")

    def test_a_long_target_plus_long_params_are_refused_together(self):
        def make(target_n, prompt_n):
            return Action("lab", "task", "sub-" + self.words(target_n - 4, "END-TARGET"), {"prompt": self.words(prompt_n, "END-PROMPT")},
                          "private", True, requester="hermes")
        self.refused(make(90_000, 70_000))
        self.accepted(make(60_000, 60_000), "END-TARGET", "END-PROMPT")

    def test_a_long_extra_card_text_counts_too(self):
        """project_apply carries its patch in `card`, next to small params: the sum includes it."""
        def make(card_n, param_n):
            a = Action("lab", "task", "sub-card", {"prompt": self.words(param_n, "END-PARAM")}, "private", True, requester="hermes")
            a.card = {"patch": self.words(card_n, "END-PATCH")}
            return a
        self.refused(make(100_000, 60_000))
        self.accepted(make(80_000, 60_000), "END-PATCH", "END-PARAM")

    def test_a_list_of_many_items_is_refused_when_the_items_together_are_too_long(self):
        item = lambda i: f"item {i:05d} " + "x" * 90                          # 100 characters each
        self.refused(Action("fs", "write", "notes/list.md", {"items": [item(i) for i in range(1600)]}))      # 160,000
        self.accepted(Action("fs", "write", "notes/list.md", {"items": [item(i) for i in range(1400)]}), item(1399))   # 140,000

    def test_nested_dicts_and_lists_are_counted_at_any_depth(self):
        def make(n):
            return Action("fs", "write", "notes/nest.md", {"a": {"b": {"c": [self.words(n, "END-C1"), {"d": self.words(n, "END-D")}]}},
                                                          "e": [[self.words(n, "END-E")]]})
        self.refused(make(55_000))                                           # 165,000 in three leaves
        self.accepted(make(45_000), "END-C1", "END-D", "END-E")              # 135,000

    def test_the_boundary_is_exact_at_the_limit_accepted_one_more_refused(self):
        def make(n):
            return Action("fs", "write", "t.md", {"text": "x" * n})
        # what counts: every key and every value of the card, as the owner reads it. Counted by hand for this shape:
        # keys tool+op+target+params+lane = 4+2+6+6+4 = 22; values fs+write+t.md+private = 2+5+4+7 = 18; params key "text" = 4.
        overhead = Kernel._text_len(make(0).describe(CARD_MAX))
        self.assertEqual(overhead, 22 + 18 + 4)
        n = CARD_MAX - overhead
        self.assertEqual(Kernel._text_len(make(n).describe(CARD_MAX)), CARD_MAX)
        self.accepted(make(n))                                               # total == CARD_MAX: accepted
        self.assertEqual(Kernel._text_len(make(n + 1).describe(CARD_MAX)), CARD_MAX + 1)
        self.refused(make(n + 1))                                            # total == CARD_MAX + 1: refused

    def test_the_boundary_with_several_fields_and_a_requester(self):
        def make(n):
            return Action("fs", "write", "t.md", {"a": "x" * 50_000, "b": "y" * 50_000, "c": "z" * n}, "private", True, requester="hermes")
        n = CARD_MAX - Kernel._text_len(make(0).describe(CARD_MAX))
        self.assertGreater(n, 40_000)                                        # each field far below the limit, the last one is the remainder
        self.accepted(make(n))
        self.refused(make(n + 1))

    def test_one_value_of_exactly_the_limit_is_refused_by_the_total_not_by_a_cut(self):
        """The value itself is not cut (it is not longer than the per-value limit), the keys and labels push the total over."""
        a = Action("fs", "write", "t.md", {"text": "x" * CARD_MAX})
        self.assertFalse(Kernel._truncated(a.describe(CARD_MAX)))
        self.refused(a)

    def test_one_value_over_the_limit_is_refused_as_cut_and_creates_no_row(self):
        self.refused(Action("fs", "write", "t.md", {"text": "x" * (CARD_MAX + 1)}), why="too long to show the owner in full")

    def test_json_escaping_does_not_count_but_every_character_the_owner_reads_does(self):
        """Quotes and newlines are one character each on the card (escaped they would be two): a text full of them that is
        under the limit as the owner reads it is accepted."""
        text = '"\n' * 45_000                                                   # 135,000 characters, 270,000 escaped in JSON
        self.assertGreater(len(json.dumps(text)), CARD_MAX)
        self.accepted(Action("fs", "write", "t.md", {"text": text}))

    def test_refusals_leave_no_row_even_after_earlier_requests_and_decisions(self):
        before = self.rows()
        d, ok_id = self.k.request(Action("fs", "write", "ok.md", {"text": "short"}))
        self.assertTrue(ok_id)
        self.assertTrue(self.k.deny(ok_id))
        self.refused(Action("fs", "write", "big.md", {"a": "x" * 80_000, "b": "y" * 80_000}))
        self.assertEqual(self.rows(), (before[0] + 1, before[1]))


if __name__ == "__main__":
    unittest.main()
