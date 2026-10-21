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


class Budget(TT.Base):
    def test_reads_do_not_use_up_the_action_budget(self):
        import jarvis_kernel.llm_proxy as LP
        for _ in range(70):                                                   # more than the 60 actions per hour
            self.assertEqual(self.post("/v1/jarvis/actions", {"tool": "memory", "op": "render", "target": "", "params": {}}, self.owner)[0], 200)
        code, r = self.act("create", "t", {"goal": "Still allowed", "criteria": ["x"]}, self.owner)
        self.assertEqual(code, 200)                                           # a real action still goes through
        for _ in range(LP.ACTIONS_PER_HOUR):
            self.post("/v1/jarvis/actions", {"tool": "memory", "op": "add", "target": f"note {_}", "params": {}}, self.owner)
        self.assertEqual(self.post("/v1/jarvis/actions", {"tool": "memory", "op": "add", "target": "one more", "params": {}}, self.owner)[0], 429)


class BadVersionOverHttp(TT.Base):
    def test_a_malformed_expected_version_gets_a_400_and_changes_nothing(self):
        # bench F-041 (2026-10-17): int("latest") raised inside the handler and the connection dropped with no answer
        def mem(op, target, **params):
            return self.post("/v1/jarvis/actions", {"tool": "memory", "op": op, "target": target, "params": params}, self.owner)
        mid = mem("add", "Wake me at 6 on weekdays")[1]["entry"]["id"]
        for op, v in [("update", x) for x in ("latest", None, [1], {"v": 1}, True)] + [("forget", x) for x in ("latest", [1], {"v": 1})]:
            code, r = mem(op, mid, **({"text": "Wake me at 9"} if op == "update" else {}), expected_version=v)
            self.assertEqual(code, 400, (op, v, r))
            self.assertIn("expected_version", r["reason"])
        e = mem("get", mid)[1]["entry"]
        self.assertEqual((e["version"], e["text"]), (1, "Wake me at 6 on weekdays"))
        self.assertEqual(mem("update", mid, text="Wake me at 7", expected_version="1")[0], 200)   # a number sent as text still works


class DamagedInput(unittest.TestCase):
    """Bench F-042/F-043 (2026-10-17): one damaged row and garbage queries."""

    def setUp(self):
        import tempfile
        from jarvis_kernel.memory import Memories
        from jarvis_kernel.store import Kernel
        self.tmp = tempfile.TemporaryDirectory()
        self.k = Kernel(os.path.join(self.tmp.name, "k"))
        self.m = Memories(self.k)

    def tearDown(self):
        self.k.db.close()
        self.tmp.cleanup()

    def test_one_damaged_history_does_not_hide_the_other_entries(self):
        good = [self.m.add("hermes", t)["text"] for t in ("I prefer metric units", "Use dark mode in every app")]
        bad = self.m.add("hermes", "Call me Venkat", "fact")
        self.k.db.execute("UPDATE memories SET history=? WHERE id=?", ('[{"v": 1, "op": "add", "text": "Call me Ven', bad["id"]))
        self.assertEqual([x["text"] for x in self.m.list("hermes")], good + ["Call me Venkat"])
        self.assertTrue(all(t in self.m.render("hermes") for t in good))
        self.assertEqual(self.m.resolve("hermes", "dark mode every app")["candidates"][0]["text"], "Use dark mode in every app")
        e = self.m.get("hermes", bad["id"])
        self.assertTrue(e["history_damaged"]); self.assertEqual(e["history"], [])
        self.assertEqual(self.m.update("hermes", bad["id"], "Call me Venkat, please", 1)["version"], 2)   # still correctable

    def test_a_query_with_no_word_in_common_offers_no_candidates(self):
        import time
        for t in ("I like short answers", "Use dark mode in every app", "My daughter's school is DPS Hyderabad"):
            self.m.add("hermes", t)
        for q in ("zebra crossing", "", "?!? ...", "a an to is it", "x" * 2_000_000):
            t0 = time.perf_counter()
            r = self.m.resolve("hermes", q)
            self.assertEqual((r["unique"], r["candidates"]), (False, []), q[:20])
            self.assertLess(time.perf_counter() - t0, 2.0)
        self.assertEqual([c["text"] for c in self.m.resolve("hermes", "dark mode")["candidates"]], ["Use dark mode in every app"])


class Erasure(unittest.TestCase):
    def test_a_forgotten_text_is_gone_from_the_database_files(self):
        # bench F-008: the row was blanked but the text stayed in state.db-wal (and freed page space keeps old bytes)
        import tempfile
        from jarvis_kernel.memory import Memories
        from jarvis_kernel.store import Kernel
        with tempfile.TemporaryDirectory() as d:
            k = Kernel(os.path.join(d, "k"))
            try:
                m = Memories(k)
                marker = "ZEBRA-HINT-" + os.urandom(4).hex()
                e = m.add("hermes", f"my pet hint is {marker}", "fact")
                m.update("hermes", e["id"], f"my pet hint is {marker} (corrected)", 1)
                m.add("hermes", "an unrelated preference that stays", "preference")
                r = m.forget("hermes", e["id"])
                self.assertTrue(r["verified"] and r["erased"])
                for name in os.listdir(k.dir):
                    if name.startswith("state.db"):
                        with open(os.path.join(k.dir, name), "rb") as f:
                            self.assertFalse(marker.encode() in f.read(), f"forgotten text still in {name}")
                self.assertEqual([x["text"] for x in m.list("hermes")], ["an unrelated preference that stays"])
            finally:
                k.db.close()


class Lanes(TT.Base):
    """MEM-M2 (2026-10-18): an entry records the lane of the token that wrote it and is read only in that lane. Before, rows
    were keyed by the token NAME only, so re-issuing a name in the public lane exposed its private entries (audit R2)."""
    def mem(self, op, target, tok, **params):
        return self.post("/v1/jarvis/actions", {"tool": "memory", "op": op, "target": target, "params": params}, tok)

    def test_a_name_reissued_in_the_public_lane_never_reads_its_private_entries(self):
        priv = self.tokens.issue("agentx", "private")
        e = self.mem("add", "Private fact: the PAN card is in the blue folder", priv)[1]["entry"]
        pub = self.tokens.issue("agentx", "public")                       # the audit's repro: same name, public lane
        self.assertEqual(self.mem("list", "", pub)[1]["entries"], [])
        self.assertEqual(self.mem("list", "", pub, query="PAN card")[1]["entries"], [])
        self.assertEqual(self.mem("get", e["id"], pub)[0], 404)
        self.assertNotIn("PAN", self.mem("render", "", pub)[1]["text"])
        self.assertEqual(self.mem("resolve", "PAN card blue folder", pub)[1]["candidates"], [])
        self.assertEqual(self.mem("update", e["id"], pub, text="changed by the public token", expected_version=1)[0], 400)
        self.assertEqual(self.mem("forget", e["id"], pub)[0], 400)
        self.assertEqual(self.mem("forget", "PAN card blue folder", pub)[0], 409)
        self.assertEqual(self.mem("add", "Private fact: the PAN card is in the blue folder", pub)[1]["entry"].get("duplicate"), None)
        lanes = dict(self.k.db.execute("SELECT lane, count(*) FROM memories WHERE owner='agentx' GROUP BY lane").fetchall())
        self.assertEqual(lanes, {"private": 1, "public": 1})                # the public copy is its own public row
        priv = self.tokens.issue("agentx", "private")                     # back in the private lane: its own entry, untouched
        got = self.mem("get", e["id"], priv)[1]["entry"]
        self.assertEqual((got["text"], got["version"], got["lane"]), ("Private fact: the PAN card is in the blue folder", 1, "private"))
        self.assertEqual(len(self.mem("list", "", priv)[1]["entries"]), 1)

    def test_rows_from_before_the_lane_column_are_private(self):
        import tempfile as _t
        from jarvis_kernel.llm_proxy import AgentTokens
        from jarvis_kernel.memory import Memories
        from jarvis_kernel.store import Kernel
        with _t.TemporaryDirectory() as d:
            k = Kernel(os.path.join(d, "old"))
            k.db.execute("CREATE TABLE memories(id TEXT PRIMARY KEY, owner TEXT, scope TEXT, kind TEXT, text TEXT, version INT, "
                         "state TEXT, created REAL, updated REAL, history TEXT)")                 # the schema before 2026-10-18
            k.db.execute("INSERT INTO memories VALUES ('m-0000aaaa','agenty','default','fact','Old private fact',1,'active',1,1,'[]')")
            AgentTokens(k).issue("agenty", "public")
            m = Memories(k)                                               # migrates: unknown lane means private
            self.assertEqual(m.list("agenty"), [])
            self.assertEqual(m.render("agenty"), "")
            self.assertEqual([x["text"] for x in m.list("agenty", lane="private")], ["Old private fact"])
            self.assertIn("memories_lane_migrated", [r[0] for r in k.db.execute("SELECT kind FROM audit").fetchall()])
            Memories(k)                                                   # a second start does not migrate again
            self.assertEqual(k.db.execute("SELECT count(*) FROM audit WHERE kind='memories_lane_migrated'").fetchone()[0], 1)
            k.db.close()
