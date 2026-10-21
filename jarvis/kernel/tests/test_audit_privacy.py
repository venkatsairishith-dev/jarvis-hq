"""H1 (review 2026-10-18): the hash-chained audit must never keep memory text or a raw memory query. The text of an entry has
to disappear with the entry (SEC-D2), and the audit is append-only, so it may hold only metadata: op, opaque entry id, lane,
agent, outcome. Everything here goes through the real HTTP handler and the real audit table, then dumps every place the
audit is read: the table, the owner's events route, the overview tail, the SSE-style event rows and the chain itself."""
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(__file__))
import test_tasks as TT                      # its Base gives a kernel + gateway over HTTP (not imported by name: no re-run)

CANARY = "CANARY-7f3a-Priyanka-SYNTH"        # synthetic: stands for a private memory entry's text
QUERY = "QUERY-9c1e-Rohan-SYNTH"             # synthetic: stands for a raw search query
NEW_TEXT = "CANARY-update-2b8d-Meera-SYNTH"
TASK_GOAL = "CANARY-goal-51aa-Dr-Rao-SYNTH"


class MemoryNeverInTheAudit(TT.Base):
    def mem(self, op, target, tok=None, **params):
        return self.post("/v1/jarvis/actions", {"tool": "memory", "op": op, "target": target, "params": params}, tok or self.owner)

    def get(self, path, tok):
        import urllib.request
        r = urllib.request.Request(self.url + path, headers={"Authorization": "Bearer " + tok})
        with urllib.request.urlopen(r, timeout=10) as resp:
            return resp.status, json.loads(resp.read())

    def dump(self, audit_only=False):
        """Every serialized form of the audit that anyone can read."""
        out = [json.dumps(self.k.db.execute("SELECT seq, ts, kind, payload, prev, hash FROM audit ORDER BY seq").fetchall())]
        con = self.tokens.issue("console", "owner")
        out.append(json.dumps(self.get("/v1/jarvis/owner/events?since=0&limit=100000", con)[1]))
        from jarvis_kernel.overview import overview
        ov = overview(self.k, [])
        if audit_only:                       # the owner's own task list legitimately shows the goal: only the audit tail here
            out.append(json.dumps(ov["audit"], default=str))
            return "\n".join(out)
        out.append(json.dumps(self.get("/v1/jarvis/owner/state", con)[1]))
        out.append(json.dumps(ov, default=str))
        out.append(json.dumps(self.k.pending_approvals(50)))
        out.append(json.dumps(self.k.approval_history(50)))
        return "\n".join(out)

    def test_add_get_list_resolve_render_update_forget_leave_no_text_in_the_audit(self):
        e = self.mem("add", f"My relative {CANARY} lives in the blue house", kind="fact")[1]["entry"]
        mid = e["id"]
        self.assertEqual(self.mem("get", mid)[0], 200)
        self.assertEqual(self.mem("get", f"{QUERY} unknown")[0], 404)                      # a text where an id belongs
        self.mem("list", "", query=QUERY)                                                  # raw query in params
        self.assertEqual(self.mem("list", QUERY)[0], 200)                                  # raw query in the target
        self.mem("resolve", f"{CANARY} blue house")
        self.mem("render", QUERY, query=QUERY)
        self.assertEqual(self.mem("update", mid, text=f"My relative {NEW_TEXT} moved", expected_version=1)[0], 200)
        text = self.dump()
        for needle in (CANARY, QUERY, NEW_TEXT, "blue house"):
            self.assertNotIn(needle, text, f"{needle!r} reached an audit-derived view before forget")
        # forget by CONTENT (the resolve step takes the text as the target) and by id
        other = self.mem("add", f"Second entry {CANARY} for forget by id", kind="fact")[1]["entry"]
        code, r = self.mem("forget", f"{NEW_TEXT} moved")
        self.assertEqual((code, r["forgotten"]["id"]), (200, mid))
        self.assertEqual(self.mem("forget", other["id"])[0], 200)
        self.assertEqual(self.mem("forget", f"m-zzzz {CANARY} not an id")[0], 400)         # looks like an id, is text
        text = self.dump()
        for needle in (CANARY, QUERY, NEW_TEXT, "blue house", "moved", "forget by id"):
            self.assertNotIn(needle, text, f"{needle!r} is in the audit after forget")
        # the raw bytes of the audit table, not only its decoded rows
        for (payload,) in self.k.db.execute("SELECT payload FROM audit").fetchall():
            self.assertNotIn("CANARY", payload)

    def test_the_audit_still_says_what_happened(self):
        """Diagnostic usefulness: op, entry id, lane, agent and outcome are still there."""
        mid = self.mem("add", f"{CANARY} diag", kind="fact")[1]["entry"]["id"]
        self.mem("get", mid)
        self.mem("forget", mid)
        rows = [json.loads(p) for (p,) in self.k.db.execute("SELECT payload FROM audit WHERE kind='authorize' ORDER BY seq").fetchall()]
        mem = [r for r in rows if r["action"]["tool"] == "memory"]
        self.assertEqual([r["action"]["op"] for r in mem], ["add", "get", "forget"])
        self.assertEqual([r["allowed"] for r in mem], [True, True, True])
        for r in mem:
            self.assertEqual((r["action"]["lane"], r["action"]["requester"]), ("private", "hermes"))
        self.assertEqual([r["action"]["target"] for r in mem][1:], [mid, mid])               # the opaque id is kept
        kinds = [k for (k,) in self.k.db.execute("SELECT kind FROM audit WHERE kind LIKE 'memory_%'").fetchall()]
        self.assertEqual(kinds, ["memory_add", "memory_forget"])

    def test_a_refused_memory_action_does_not_keep_the_text_either(self):
        pub = self.tokens.issue("pubmem", "public")
        self.mem("add", f"{CANARY} public lane", tok=pub)
        priv = self.tokens.issue("pubmem", "private")                                        # same name, other lane
        self.mem("get", f"{QUERY} nothing", tok=priv)
        self.mem("nonsense-op", f"{CANARY} bad op")                                          # refused before policy
        self.assertNotIn("CANARY", self.dump())
        self.assertNotIn(QUERY, self.dump())

    def test_the_stored_idempotent_reply_is_cleared_but_the_audit_never_had_it(self):
        mid = None
        body = {"tool": "memory", "op": "add", "target": f"{CANARY} keyed", "params": {"kind": "fact"}}
        import urllib.request
        r = urllib.request.Request(self.url + "/v1/jarvis/actions", data=json.dumps(body).encode(), method="POST",
                                   headers={"Content-Type": "application/json", "Authorization": "Bearer " + self.owner,
                                            "Idempotency-Key": "mem-add-canary-1"})
        mid = json.loads(urllib.request.urlopen(r, timeout=10).read())["entry"]["id"]
        self.assertEqual(self.mem("forget", mid)[0], 200)
        blob = json.dumps(self.k.db.execute("SELECT * FROM idempotency").fetchall()) + self.dump()
        self.assertNotIn("CANARY", blob)

    def test_a_task_goal_is_not_copied_into_the_audit_either(self):
        """A task's goal is the owner's own words; the task row holds it, the permanent audit chain does not."""
        code, r = self.act("create", TASK_GOAL, {"request": "please arrange something private", "criteria": ["done"]}, self.owner)
        self.assertEqual(code, 200)
        self.assertNotIn("CANARY-goal", self.dump(audit_only=True))
        self.assertEqual(self.get(f"/v1/jarvis/owner/task/{r['task']}", self.tokens.issue("console", "owner"))[0], 200)

    def test_the_chain_and_the_anchor_still_verify(self):
        mid = self.mem("add", f"{CANARY} chain", kind="fact")[1]["entry"]["id"]
        self.mem("forget", mid)
        ok, msg = self.k.audit.verify()
        self.assertTrue(ok, msg)
        a = self.k.audit.anchor()
        self.assertEqual(self.k.audit.verify(a), (True, f"ok: {a['seq']} events"))


class DescribeUnit(unittest.TestCase):
    def test_describe_hides_memory_text_whatever_the_limit(self):
        from jarvis_kernel.policy import Action
        for limit in (200, 2000, 100000):
            d = Action("memory", "add", CANARY, {}, "private", True, requester="hermes").describe(limit)
            self.assertNotIn("CANARY", json.dumps(d))
            self.assertEqual((d["tool"], d["op"], d["lane"], d["requester"]), ("memory", "add", "private", "hermes"))
        self.assertEqual(Action("memory", "get", "m-0a1b2c3d", {}, "private").describe()["target"], "m-0a1b2c3d")
        self.assertNotIn("m-0a1b2c3dXYZ", json.dumps(Action("memory", "get", "m-0a1b2c3dXYZ").describe()))   # not an id: text


if __name__ == "__main__":
    unittest.main()
