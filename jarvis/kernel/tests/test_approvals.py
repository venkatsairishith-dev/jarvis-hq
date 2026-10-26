"""The approval workflow end to end against a disposable kernel: what the owner is shown, who may approve, what an approval
is bound to, single use, expiry, denial, the audit trail, and the outcome the UI reads back."""
import json
import os
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

HERE = os.path.dirname(__file__)
sys.path.insert(0, os.path.join(HERE, ".."))
from jarvis_kernel.llm_proxy import make_handler
from jarvis_kernel.store import Kernel

PIN = "correct horse battery"


class Approvals(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.k = Kernel(os.path.join(self.tmp.name, "d"))
        H, tokens = make_handler(self.k, [], owner_hermes=None)
        self.plane = H.owner_plane
        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.srv.server_address[1]}"
        self.owner, self.agent, self.lab = tokens.issue("console", "owner"), tokens.issue("hermes", "private"), tokens.issue("lab", "public")
        self.other = tokens.issue("other-private", "private")        # a second agent in the SAME lane (audit SEC-D3)
        self.plane.set_pin(PIN)

    def tearDown(self):
        self.srv.shutdown(); self.k.db.close(); self.tmp.cleanup()

    def call(self, method, path, body, tok):
        r = urllib.request.Request(self.url + path, data=json.dumps(body).encode() if body is not None else None, method=method,
                                   headers={"Authorization": "Bearer " + tok, "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(r, timeout=15) as resp:
                return resp.status, json.loads(resp.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read() or b"{}")

    def ask(self, text="Hello from the test", target="notepad|Edit", approval_id=None, tok=None, op="type"):
        body = {"tool": "ui", "op": op, "target": target, "params": {"text": text}}
        if approval_id:
            body["approval_id"] = approval_id
        return self.call("POST", "/v1/jarvis/actions", body, tok or self.agent)

    def state(self):
        return self.call("GET", "/v1/jarvis/owner/state", None, self.owner)[1]

    def approve(self, aid, pin=PIN, confirm=None):
        return self.call("POST", "/v1/jarvis/owner/approve", {"id": aid, "confirm": aid if confirm is None else confirm, "pin": pin}, self.owner)

    def audit_kinds(self):
        return [e["kind"] for e in self.call("GET", "/v1/jarvis/owner/events?since=0&limit=500", None, self.owner)[1]["events"]]

    # ---- what the owner is shown
    def test_the_prompt_shows_the_exact_action_the_reason_and_the_digest(self):
        long_text = "x" * 450 + " end-marker"
        code, r = self.ask(long_text)
        self.assertEqual((code, r["status"]), (202, "needs_approval"))
        p = self.state()["approvals"][0]
        self.assertEqual(p["id"], r["approval_id"])
        self.assertEqual((p["action"]["tool"], p["action"]["op"], p["action"]["target"]), ("ui", "type", "notepad|Edit"))
        self.assertEqual(p["action"]["params"]["text"], long_text)                 # not cut at 200: what is bound is what is shown
        self.assertFalse(p["truncated"])
        self.assertIn("rule ui", p["reason"])
        self.assertEqual(len(p["digest"]), 12)
        self.assertEqual(p["level"], "YELLOW_APPROVAL")
        # an over-long value is displayed truncated and says so
        code, r2 = self.ask("y" * 500 + "z", target="notepad|Edit2")
        shown = {a["id"]: a for a in self.state()["approvals"]}[r2["approval_id"]]
        self.assertFalse(shown["truncated"])                                       # 501 chars still fits (limit 2000)
        self.assertEqual(shown["action"]["params"]["text"], "y" * 500)             # the endpoint itself clips text at 500 before policy

    def test_a_secret_in_the_arguments_is_redacted_in_the_prompt_but_still_bound(self):
        code, r = self.ask("password=hunter2hunter2hunter2")
        p = {a["id"]: a for a in self.state()["approvals"]}[r["approval_id"]]
        self.assertNotIn("hunter2", json.dumps(p))
        self.assertTrue(self.approve(r["approval_id"])[0] == 200)
        self.assertEqual(self.ask("password=hunter2hunter2hunter2", approval_id=r["approval_id"])[1]["status"], "queued")

    # ---- who may approve
    def test_approval_needs_the_pin_the_typed_id_and_an_owner_token(self):
        aid = self.ask()[1]["approval_id"]
        self.assertEqual(self.approve(aid, pin="wrong pin value")[0], 403)
        self.assertEqual(self.approve(aid, confirm="not-the-id")[0], 400)
        self.assertEqual(self.call("POST", "/v1/jarvis/owner/approve", {"id": aid, "confirm": aid, "pin": PIN}, self.agent)[0], 403)   # an agent cannot
        self.assertEqual(self.call("POST", "/v1/jarvis/owner/approve", {"id": aid, "confirm": aid, "pin": PIN}, self.lab)[0], 403)
        self.assertEqual(self.k.approval_info(aid)["state"], "pending")
        self.assertEqual(self.approve(aid)[0], 200)
        self.assertEqual(self.k.approval_info(aid)["state"], "approved")

    def test_an_agent_cannot_use_an_approval_before_the_owner_decides(self):
        aid = self.ask()[1]["approval_id"]
        code, r = self.ask(approval_id=aid)
        self.assertNotEqual(code, 200)
        self.assertEqual(self.k.db.execute("SELECT COUNT(*) FROM jobs").fetchone()[0], 0)

    # ---- what an approval is bound to
    def test_an_approved_action_runs_once_and_the_outcome_is_linked(self):
        aid = self.ask("one")[1]["approval_id"]
        self.assertEqual(self.approve(aid)[0], 200)
        code, r = self.ask("one", approval_id=aid)
        self.assertEqual((code, r["status"]), (200, "queued"))
        replay = self.ask("one", approval_id=aid)
        self.assertNotEqual(replay[0], 200)                                         # single use: a replay is refused
        self.assertEqual(self.k.db.execute("SELECT COUNT(*) FROM jobs").fetchone()[0], 1)
        h = {x["id"]: x for x in self.state()["approval_history"]}[aid]
        self.assertEqual((h["state"], h["job"]), ("used", r["job"]))                # the UI reads the authoritative outcome

    def test_a_changed_action_needs_its_own_approval(self):
        aid = self.ask("send this")[1]["approval_id"]
        self.assertEqual(self.approve(aid)[0], 200)
        for changed in (dict(text="send THAT"), dict(text="send this", target="notepad|Other")):
            code, r = self.ask(changed["text"], target=changed.get("target", "notepad|Edit"), approval_id=aid)
            self.assertNotEqual(code, 200, changed)
        self.assertEqual(self.k.approval_info(aid)["state"], "approved")           # still unused, still bound to the original
        self.assertEqual(self.k.db.execute("SELECT COUNT(*) FROM jobs").fetchone()[0], 0)
        self.assertEqual(self.ask("send this", approval_id=aid)[1]["status"], "queued")

    def test_an_approval_does_not_carry_over_to_another_agents_lane(self):
        aid = self.ask("lane test")[1]["approval_id"]
        self.assertEqual(self.approve(aid)[0], 200)
        code, r = self.ask("lane test", approval_id=aid, tok=self.lab)               # the same words from a public-lane agent
        self.assertNotEqual(code, 200)

    # ---- who asked (audit SEC-D3, 2026-10-18)
    def test_an_approval_given_for_one_agents_request_cannot_be_used_by_another_agent_in_the_same_lane(self):
        """R3 of the audit: agent A asks for `ui click Notepad`, the owner approves, agent B (also private) sends the same action
        with A's approval id. It used to answer 200 queued and run as B's job."""
        aid = self.ask("x", target="Notepad", op="click", tok=self.agent)[1]["approval_id"]
        self.assertEqual(self.approve(aid)[0], 200)
        code, r = self.ask("x", target="Notepad", op="click", approval_id=aid, tok=self.other)
        self.assertEqual(code, 403, r)
        self.assertIn("another agent", r["reason"])
        self.assertEqual(self.k.db.execute("SELECT COUNT(*) FROM jobs").fetchone()[0], 0)
        self.assertEqual(self.k.approval_info(aid)["state"], "approved")           # the refused attempt did not use it up
        code, r = self.ask("x", target="Notepad", op="click", approval_id=aid, tok=self.agent)   # the asking agent's retry still works
        self.assertEqual((code, r["status"]), (200, "queued"))
        self.assertEqual(self.k.db.execute("SELECT owner FROM jobs").fetchone()[0], "hermes")
        self.assertNotEqual(self.ask("x", target="Notepad", op="click", approval_id=aid, tok=self.agent)[0], 200)   # still single use

    def test_each_agent_can_ask_for_the_same_action_and_gets_its_own_approval(self):
        a1 = self.ask("same", tok=self.agent)[1]["approval_id"]
        a2 = self.ask("same", tok=self.other)[1]["approval_id"]
        self.assertNotEqual(a1, a2)
        by_id = {a["id"]: a for a in self.state()["approvals"]}
        self.assertNotEqual(by_id[a1]["digest"], by_id[a2]["digest"])               # who asked is part of what is approved
        self.assertEqual(self.approve(a2)[0], 200)
        self.assertEqual(self.ask("same", approval_id=a2, tok=self.agent)[0], 403)  # B's approval is not A's
        self.assertEqual(self.ask("same", approval_id=a2, tok=self.other)[1]["status"], "queued")

    def test_the_approval_card_and_history_say_which_agent_asked(self):
        aid = self.ask("card", tok=self.other)[1]["approval_id"]
        card = {a["id"]: a for a in self.state()["approvals"]}[aid]
        self.assertEqual((card["requester"], card["action"]["requester"]), ("other-private", "other-private"))
        self.assertEqual(self.k.approval_info(aid)["requester"], "other-private")
        self.assertEqual(self.approve(aid)[0], 200)
        self.ask("card", approval_id=aid, tok=self.other)
        h = {x["id"]: x for x in self.state()["approval_history"]}[aid]
        self.assertEqual((h["state"], h["requester"]), ("used", "other-private"))
        self.assertIn("other-private", json.dumps([e["payload"] for e in self.call("GET", "/v1/jarvis/owner/events?since=0&limit=500", None, self.owner)[1]["events"]
                                                   if e["kind"] == "request"]))      # and the audit row

    def test_the_owners_own_requests_carry_no_requester_and_still_work(self):
        """The owner-direct paths (owner.pc) are unchanged: an approval the owner asked for himself is bound to no agent."""
        body = {"op": "ui", "action": "click", "target": "notepad|Delete draft", "text": ""}     # a consequential word on the control (typed text is content since D54): the owner approves it
        code, r = self.call("POST", "/v1/jarvis/owner/pc", body, self.owner)
        self.assertEqual(r["status"], "needs_approval")
        aid = r["approval_id"]
        self.assertEqual(self.k.approval_info(aid)["requester"], "")
        self.assertEqual(self.approve(aid)[0], 200)
        code, r = self.call("POST", "/v1/jarvis/owner/pc", dict(body, approval_id=aid), self.owner)
        self.assertEqual((code, r["status"]), (202, "queued"))
        # an agent cannot spend an approval the owner took out for himself, even for the same words
        code, r = self.call("POST", "/v1/jarvis/owner/pc", dict(body, text="delete the other draft"), self.owner)
        aid2 = r["approval_id"]
        self.assertEqual(self.approve(aid2)[0], 200)
        self.assertNotEqual(self.ask("delete the other draft", approval_id=aid2, tok=self.agent)[0], 200)

    def test_the_node_endpoint_cannot_consume_an_approval_an_agent_asked_for_unless_it_names_that_agent(self):
        from jarvis_kernel.nodes import Nodes, make_handler as node_handler
        nodes = Nodes(self.k)
        NH, _ = node_handler(self.k)
        nsrv = ThreadingHTTPServer(("127.0.0.1", 0), NH)
        threading.Thread(target=nsrv.serve_forever, daemon=True).start()
        try:
            nurl = f"http://127.0.0.1:{nsrv.server_address[1]}"

            def npost(path, obj, tok=None):
                r = urllib.request.Request(nurl + path, data=json.dumps(obj).encode(), method="POST")
                if tok:
                    r.add_header("Authorization", "Bearer " + tok)
                return json.loads(urllib.request.urlopen(r, timeout=10).read())
            ntok = npost("/pair", {"code": nodes.new_code(), "name": "laptop"})["token"]
            aid = self.ask("by agent", tok=self.agent)[1]["approval_id"]
            self.assertEqual(self.approve(aid)[0], 200)
            act = {"tool": "ui", "op": "type", "target": "notepad|Edit", "params": {"text": "by agent"}, "lane": "private", "untrusted_session": True}
            r = npost("/authorize", {"action": act, "approval_id": aid}, ntok)           # the node names no requester: refused
            self.assertEqual(r["allowed"], False)
            self.assertIn("another agent", r["reason"])
            r = npost("/authorize", {"action": dict(act, requester="hermes"), "approval_id": aid}, ntok)   # naming the agent is explicit and audited
            self.assertEqual(r["allowed"], True)
            # the node's own request has no requester and is approved and used as before
            q = npost("/request", {"action": {"tool": "ui", "op": "type", "target": "notepad|Edit", "params": {"text": "node's own"}, "lane": "private"}}, ntok)
            self.assertTrue(q["approval_id"])
            self.assertEqual(self.k.approval_info(q["approval_id"])["requester"], "")
            self.assertEqual(self.approve(q["approval_id"])[0], 200)
            r = npost("/authorize", {"action": {"tool": "ui", "op": "type", "target": "notepad|Edit", "params": {"text": "node's own"}, "lane": "private"},
                                     "approval_id": q["approval_id"]}, ntok)
            self.assertEqual(r["allowed"], True)
        finally:
            nsrv.shutdown()

    # ---- denial, expiry, invalid
    def test_denial_prevents_execution_and_is_final(self):
        aid = self.ask("deny me")[1]["approval_id"]
        self.assertEqual(self.call("POST", "/v1/jarvis/owner/deny", {"id": aid}, self.owner)[0], 200)
        self.assertEqual(self.approve(aid)[0], 404)                                  # a denied approval can never be approved
        code, r = self.ask("deny me", approval_id=aid)
        self.assertNotEqual(code, 200)
        self.assertEqual(self.k.db.execute("SELECT COUNT(*) FROM jobs").fetchone()[0], 0)
        self.assertEqual({x["id"]: x for x in self.state()["approval_history"]}[aid]["state"], "denied")

    def test_expired_and_unknown_approvals_fail_safely(self):
        aid = self.ask("will expire")[1]["approval_id"]
        self.k.db.execute("UPDATE approvals SET expires=? WHERE id=?", (time.time() - 5, aid))
        self.assertEqual(self.approve(aid)[0], 404)
        self.assertNotEqual(self.ask("will expire", approval_id=aid)[0], 200)
        self.assertEqual({x["id"]: x for x in self.state()["approval_history"]}[aid]["state"], "expired")
        aid2 = self.ask("approved then expired")[1]["approval_id"]
        self.assertEqual(self.approve(aid2)[0], 200)
        self.k.db.execute("UPDATE approvals SET expires=? WHERE id=?", (time.time() - 5, aid2))
        self.assertNotEqual(self.ask("approved then expired", approval_id=aid2)[0], 200)
        for bad in ("", "nope", "0" * 16, "../../etc"):
            self.assertNotEqual(self.ask("x", approval_id=bad)[0], 200)
            self.assertIn(self.approve(bad or "x")[0], (400, 403, 404))
        self.assertEqual(self.k.db.execute("SELECT COUNT(*) FROM jobs").fetchone()[0], 0)

    def test_the_kill_switch_stops_a_pending_approval_from_being_used(self):
        aid = self.ask("kill me")[1]["approval_id"]
        self.assertEqual(self.approve(aid)[0], 200)
        self.k.kill("test")
        code, r = self.ask("kill me", approval_id=aid)
        self.assertNotEqual(code, 200)
        self.assertEqual(self.k.db.execute("SELECT COUNT(*) FROM jobs").fetchone()[0], 0)
        self.k.resume()
        self.assertEqual(self.ask("kill me", approval_id=aid)[1]["status"], "queued")      # it was not consumed while killed

    # ---- audit
    def test_every_decision_is_in_the_audit_chain(self):
        a1 = self.ask("audit one")[1]["approval_id"]
        self.approve(a1)
        self.ask("audit one", approval_id=a1)
        a2 = self.ask("audit two")[1]["approval_id"]
        self.call("POST", "/v1/jarvis/owner/deny", {"id": a2}, self.owner)
        self.approve(a1, pin="wrong pin value")
        kinds = self.audit_kinds()
        for want in ("request", "approved", "owner_approved_http", "authorize", "job_queued", "denied", "owner_denied_http", "owner_approve_bad_pin"):
            self.assertIn(want, kinds, want)
        ok, msg = self.k.audit.verify()
        self.assertTrue(ok, msg)

    def test_red_actions_are_not_approvable_over_http(self):
        d, aid = self.k.request(__import__("jarvis_kernel.policy", fromlist=["Action"]).Action("publish", "post", "blog"))
        self.assertEqual(self.approve(aid)[0], 403)
        self.assertEqual(self.k.approval_info(aid)["state"], "pending")


if __name__ == "__main__":
    unittest.main()
