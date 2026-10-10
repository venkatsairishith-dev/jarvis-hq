"""Durable memory: stable ids, versions, conflicts, deletion that is verified and never hits unrelated entries;
and the task checkpoint a fresh or compacted session starts from."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(__file__))
import test_tasks as TT                      # its Base gives a kernel + gateway over HTTP (not imported by name: no re-run)


class Lifecycle(TT.Base):
    def mem(self, op, target, tok=None, **params):
        return self.post("/v1/jarvis/actions", {"tool": "memory", "op": op, "target": target, "params": params}, tok or self.owner)

    def test_save_recall_correct_conflict_forget(self):
        code, r = self.mem("add", "I prefer snake_case file names for delivered code")
        self.assertEqual(code, 200); e = r["entry"]; self.assertTrue(e["id"].startswith("m-")); self.assertEqual(e["version"], 1)
        decoy = self.mem("add", "I prefer dark mode in every app")[1]["entry"]
        self.assertEqual(self.mem("add", "I prefer  snake_case file names for delivered code")[1]["entry"]["id"], e["id"])  # no duplicate
        self.assertEqual(self.mem("add", "my token is ghp_abcdefghijklmnopqrstuvwxyz0123456789")[0], 400)               # never a secret
        rendered = self.mem("render", "")[1]["text"]
        self.assertIn(f"[{e['id']} v1]", rendered); self.assertIn(decoy["id"], rendered)
        # correction: versioned; a stale writer gets a conflict, not a silent overwrite
        code, r = self.mem("update", e["id"], text="I prefer snake_case file names and no README files", expected_version=1)
        self.assertEqual((code, r["entry"]["version"]), (200, 2))
        code, r = self.mem("update", e["id"], text="stale writer", expected_version=1)
        self.assertEqual((code, r["current"]["version"]), (409, 2))
        self.assertEqual(r["current"]["text"], "I prefer snake_case file names and no README files")
        # forget by content: an ambiguous query deletes nothing
        code, r = self.mem("forget", "I prefer")
        self.assertEqual(code, 409); self.assertEqual(len(self.mem("list", "")[1]["entries"]), 2)
        # forget by content: one clear match; the decoy that shares words survives
        code, r = self.mem("forget", "snake_case file names README")
        self.assertEqual((code, r["forgotten"]["id"], r["verified"]), (200, e["id"], True))
        left = self.mem("list", "")[1]["entries"]
        self.assertEqual([x["id"] for x in left], [decoy["id"]])
        self.assertNotIn(e["id"], self.mem("render", "")[1]["text"])               # no longer in what a session loads
        self.assertEqual(self.mem("get", e["id"])[0], 404)
        self.assertEqual(self.mem("forget", e["id"])[0], 400)                      # already gone, said plainly
        # forget by id, with the version the caller saw
        code, r = self.mem("forget", decoy["id"], expected_version=1)
        self.assertEqual((code, r["verified"]), (200, True)); self.assertEqual(self.mem("list", "")[1]["entries"], [])
        kinds = [k for (k,) in self.k.db.execute("SELECT kind FROM audit WHERE kind LIKE 'memory_%' ORDER BY seq").fetchall()]
        self.assertEqual(kinds, ["memory_add", "memory_add", "memory_update", "memory_forget", "memory_forget"])

    def test_entries_belong_to_the_agent_that_wrote_them(self):
        mid = self.mem("add", "owner likes short answers")[1]["entry"]["id"]
        other = self.tokens.issue("memtest")
        self.assertEqual(self.mem("list", "", tok=other)[1]["entries"], [])
        self.assertEqual(self.mem("get", mid, tok=other)[0], 404)
        self.assertEqual(self.mem("forget", mid, tok=other)[0], 400)
        self.assertEqual(len(self.mem("list", "")[1]["entries"]), 1)               # the owner's entry is untouched

    def test_checkpoint_carries_goal_criteria_state_steps_and_next_action(self):
        code, r = self.act("create", "t", {"goal": "Build a word-count tool", "criteria": ["tests pass", "README explains usage"],
                                            "plan": ["write tool", "write tests", "write README"]}, self.owner)
        tid = r["task"]
        self.act("update", tid, {"note": "owner wants no external packages"}, self.owner)
        cp = self.act("checkpoint", "", {}, self.owner)[1]
        self.assertEqual(cp["tasks"][0]["id"], tid)
        for part in ("Build a word-count tool", "tests pass", "planning", "write README", "no external packages", "next: refine the plan"):
            self.assertIn(part, cp["text"])
        self.assertLessEqual(len(cp["text"]), 1800)
        self.assertEqual(self.act("checkpoint", "", {}, self.lab)[1]["tasks"], [])  # only the agent's own tasks


if __name__ == "__main__":
    unittest.main()
