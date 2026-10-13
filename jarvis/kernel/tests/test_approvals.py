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
