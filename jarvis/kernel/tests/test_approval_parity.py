"""Approval / kill-switch PARITY (re-check D, 2026-10-19): every route that can approve, deny, execute, cancel or resume is held to
the same rules, with real handlers (the model endpoint + owner plane over HTTP, the node endpoint over HTTP, the Telegram bridge,
the CLI's approve command, Kernel itself) on a real sqlite file.

Matrix (rows = routes that can GRANT an approval; every cell is asserted below):
 route          auth                      PIN / second step            kill switch   RED       cut-off / invisible card   audited
 owner HTTP     owner token (lane owner)  PIN + typed id               refused 409   refused   refused 409                approved, owner_approved_http, owner_approve_*
 Telegram       the owner's private chat  /approve then CONFIRM <id>   refused       refused   refused                    approved, tg_approved
 CLI approve    a real terminal           typed id                     refused       allowed   refused                    approved
 Kernel.approve (callers above)           -                            refused       allowed   refused                    approved / approve_refused
Routes that can only DENY or CANCEL (owner HTTP deny / task / stop_all, Telegram /deny and /task, command cancel) need the same
auth and never need a PIN (the safe direction); they work while the kill switch is on.
Routes that USE an approval or EXECUTE (agent actions, owner pc/command, node /authorize, Jobs.create, automations) all end in
Kernel.authorize, which refuses while killed and consumes a bound approval exactly once.
"""
import json
import os
import signal
import subprocess
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
sys.path.insert(0, HERE)
from jarvis_kernel import cli
from jarvis_kernel import nodes as nodes_mod
from jarvis_kernel.jobs import Jobs
from jarvis_kernel.llm_proxy import make_handler
from jarvis_kernel.owner import _hash_pin
from jarvis_kernel.policy import Action
from jarvis_kernel.store import Kernel
from jarvis_kernel.tasks import Tasks
from jarvis_kernel.telegram import Bridge
from test_telegram import FakeAPI, msg

PIN = "correct horse battery"
OWNER_CHAT = 111222333


class Rig(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = os.path.join(self.tmp.name, "d")
        self.k = Kernel(self.dir)
        self.open()

    def open(self):
        self.H, self.tokens = make_handler(self.k, [], owner_hermes=lambda text: "ok")
        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), self.H)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.srv.server_address[1]}"
        self.a, self.b = self.tokens.issue("hermes", "private"), self.tokens.issue("other", "private")
        self.pub, self.con = self.tokens.issue("labx", "public"), self.tokens.issue("console", "owner")
        self.k.db.execute("INSERT OR REPLACE INTO owner_config(key, value) VALUES ('approval_pin', ?)", (_hash_pin(PIN),))
        self.api = FakeAPI()
        self.tg = Bridge(self.k, self.api, OWNER_CHAT, [])
        NH, self.nodes = nodes_mod.make_handler(self.k)
        self.nsrv = ThreadingHTTPServer(("127.0.0.1", 0), NH)
        threading.Thread(target=self.nsrv.serve_forever, daemon=True).start()
        self.node_tok = self.nodes.pair(self.nodes.new_code(), "laptop")

    def tearDown(self):
        self.srv.shutdown(); self.nsrv.shutdown(); self.k.db.close(); self.tmp.cleanup()

    # ---- helpers
    def http(self, method, path, body=None, tok=None, base=None, headers=None):
        h = {"Content-Type": "application/json", **(headers or {})}
        if tok:
            h["Authorization"] = "Bearer " + tok
        r = urllib.request.Request((base or self.url) + path, data=json.dumps(body).encode() if body is not None else None, method=method, headers=h)
        try:
            with urllib.request.urlopen(r, timeout=15) as resp:
                return resp.status, json.loads(resp.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read() or b"{}")

    def act(self, body, tok=None, headers=None):
        return self.http("POST", "/v1/jarvis/actions", body, tok or self.a, headers=headers)

    def ui(self, text="hello", target="notepad|Edit", tok=None, aid=None, op="type"):
        body = {"tool": "ui", "op": op, "target": target, "params": {"text": text}}
        if aid:
            body["approval_id"] = aid
        return self.act(body, tok)

    def pending(self, text="hello", **kw):
        code, r = self.ui(text, **kw)
        self.assertEqual((code, r["status"]), (202, "needs_approval"), r)
        return r["approval_id"]

    def state_of(self, aid):
        return self.k.db.execute("SELECT state FROM approvals WHERE id=?", (aid,)).fetchone()[0]

    def kinds(self):
        return [k for (k,) in self.k.db.execute("SELECT kind FROM audit ORDER BY seq").fetchall()]

    def jobs(self):
        return self.k.db.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]

    # ---- the four ways to grant (each returns None; look at the approval's state afterwards)
    def grant_owner(self, aid, pin=PIN, tok="console"):
        return self.http("POST", "/v1/jarvis/owner/approve", {"id": aid, "confirm": aid, "pin": pin}, self.con if tok == "console" else tok)

    def grant_tg(self, aid, chat=OWNER_CHAT):
        self.tg.handle(msg(f"/approve {aid}", chat=chat))
        self.tg.handle(msg(f"CONFIRM {aid}", chat=chat))
        return self.api.sent[-1][1] if self.api.sent else ""

    def grant_cli(self, aid, typed=None):
        real, shown = cli._ask_console, []
        cli._ask_console = lambda prompt: (shown.append(prompt), (aid if typed is None else typed) + "\n")[1]
        try:
            rc = cli.main(["--data", self.dir, "approve", aid])
        finally:
            cli._ask_console = real
        return rc, shown

    def grant_kernel(self, aid):
        return self.k.approve(aid)

    def routes(self):
        return {"owner_http": lambda aid: self.grant_owner(aid), "telegram": self.grant_tg, "cli": self.grant_cli, "kernel": self.grant_kernel}


class Granting(Rig):
    """Rows of the matrix: who can grant, with what, when."""
    def cli_ready(self):
        """cli.main opens its own Kernel on the same file; ours must not hold a write lock meanwhile (it does not)."""

    def test_every_route_grants_a_plain_card_and_audits_it(self):
        for name, grant in self.routes().items():
            with self.subTest(route=name):
                aid = self.pending("plain text " + name, target="notepad|" + name)
                before = len(self.kinds())
                grant(aid)
                self.assertEqual(self.state_of(aid), "approved", name)
                new = self.kinds()[before:]
                self.assertIn("approved", new)
                self.assertTrue({"owner_http": "owner_approved_http", "telegram": "tg_approved"}.get(name, "approved") in new, (name, new))
                # and the use is bound and single-use for every route alike
                code, r = self.ui("plain text " + name, target="notepad|" + name, aid=aid)
                self.assertEqual((code, r["status"]), (200, "queued"), name)
                self.assertEqual(self.ui("plain text " + name, target="notepad|" + name, aid=aid)[0], 403, name)

    def test_authentication_cells(self):
        aid = self.pending("auth")
        for tok in (self.a, self.b, self.pub, "nope", None):          # agent tokens (any lane), a bad token, none
            with self.subTest(tok=bool(tok)):
                self.assertIn(self.grant_owner(aid, tok=tok)[0], (401, 403))
        self.assertEqual(self.state_of(aid), "pending")
        self.grant_tg(aid, chat=999)                                    # another chat: ignored, never replied to
        self.assertEqual(self.state_of(aid), "pending"); self.assertIn("tg_ignored", self.kinds())
        real = cli._ask_console
        cli._ask_console = lambda p: None                               # no real terminal (a pipe or a script)
        try:
            self.assertEqual(cli.main(["--data", self.dir, "approve", aid]), 3)
        finally:
            cli._ask_console = real
        self.assertEqual(self.state_of(aid), "pending")
        # the node's token reaches /authorize, never an approve route; and an agent cannot reach owner routes at all
        self.assertEqual(self.http("POST", "/v1/jarvis/owner/approve", {"id": aid, "confirm": aid, "pin": PIN}, self.node_tok)[0], 401)

    def test_pin_and_second_step_cells(self):
        aid = self.pending("pin")
        self.assertEqual(self.grant_owner(aid, pin="wrong pin value")[0], 403)
        self.assertEqual(self.http("POST", "/v1/jarvis/owner/approve", {"id": aid, "confirm": "x", "pin": PIN}, self.con)[0], 400)     # id not retyped
        self.assertEqual(self.http("POST", "/v1/jarvis/owner/approve", {"id": aid, "confirm": aid}, self.con)[0], 403)                   # no PIN
        self.tg.handle(msg(f"/approve {aid}", chat=OWNER_CHAT))
        self.assertEqual(self.state_of(aid), "pending")                 # /approve alone does not approve
        self.tg.handle(msg(f"CONFIRM {'0' * 16}", chat=OWNER_CHAT))
        self.assertEqual(self.state_of(aid), "pending")
        self.assertEqual(self.grant_cli(aid, typed="not the id")[0], 1)
        self.assertEqual(self.state_of(aid), "pending")
        self.k.db.execute("DELETE FROM owner_config WHERE key='approval_pin'")
        self.assertEqual(self.grant_owner(aid)[0], 403)                 # no PIN set: over HTTP nothing can be approved
        self.assertEqual(self.state_of(aid), "pending")

    def test_kill_switch_blocks_every_grant_but_not_a_deny(self):
        ids = {n: self.pending("kill " + n, target="notepad|" + n) for n in ("owner_http", "telegram", "cli", "kernel", "deny_owner", "deny_tg")}
        self.k.kill("test")
        for name, grant in self.routes().items():
            with self.subTest(route=name):
                grant(ids[name])
                self.assertEqual(self.state_of(ids[name]), "pending", name)
        self.assertEqual(self.http("POST", "/v1/jarvis/owner/deny", {"id": ids["deny_owner"]}, self.con)[0], 200)
        self.tg.handle(msg(f"/deny {ids['deny_tg']}", chat=OWNER_CHAT))
        self.assertEqual((self.state_of(ids["deny_owner"]), self.state_of(ids["deny_tg"])), ("denied", "denied"))
        self.k.resume()
        self.assertEqual(self.grant_owner(ids["owner_http"])[0], 200)

    def test_red_actions_are_granted_at_the_console_only(self):
        d, aid = self.k.request(Action("money", "send", "somebody", {"amount": "1"}, "private", True, requester="hermes"))
        self.assertEqual(d.name, "RED")
        self.assertEqual(self.grant_owner(aid)[0], 403)
        self.grant_tg(aid)
        self.assertEqual(self.state_of(aid), "pending")
        self.assertTrue(any("kernel console" in t for _, t in self.api.sent), self.api.sent)
        rc, shown = self.grant_cli(aid)                                   # the kernel console (real terminal) can
        self.assertEqual((rc, self.state_of(aid)), (0, "approved"))
        # a DENY-level action never gets a card at all
        d2, aid2 = self.k.request(Action("fs", "read", "/home/x/.ssh/id_rsa"))
        self.assertEqual((d2.name, aid2), ("DENY", None))

    def test_cut_off_and_invisible_cards_are_refused_by_every_route(self):
        tags = "".join(chr(0xE0000 + ord(c)) for c in "go")
        ids = {"owner_http": self.pending("a" + tags, target="notepad|1"), "telegram": self.pending("b" + tags, target="notepad|2"),
               "cli": self.pending("c" + tags, target="notepad|3"), "kernel": self.pending("d" + tags, target="notepad|4")}
        for name, grant in self.routes().items():
            with self.subTest(route=name, why="invisible"):
                grant(ids[name])
                self.assertEqual(self.state_of(ids[name]), "pending")
        cut = {n: self.pending("cut " + n, target="notepad|c" + n) for n in ("owner_http", "telegram", "cli", "kernel")}
        for aid in cut.values():
            row = json.loads(self.k.db.execute("SELECT action FROM approvals WHERE id=?", (aid,)).fetchone()[0])
            row["params"]["text"] = "x" * 2000 + "...[truncated]"
            self.k.db.execute("UPDATE approvals SET action=? WHERE id=?", (json.dumps(row), aid))
        for name, grant in self.routes().items():
            with self.subTest(route=name, why="cut off"):
                grant(cut[name])
                self.assertEqual(self.state_of(cut[name]), "pending")
        self.assertIn("approve_refused", self.kinds())

    def test_replaying_an_owner_approve_with_the_same_key_does_not_approve_again_or_bypass_anything(self):
        aid = self.pending("replay")
        hdr = {"Idempotency-Key": "owner-approve-0001"}
        c1, r1 = self.http("POST", "/v1/jarvis/owner/approve", {"id": aid, "confirm": aid, "pin": PIN}, self.con, headers=hdr)
        self.assertEqual(c1, 200)
        c2, r2 = self.http("POST", "/v1/jarvis/owner/approve", {"id": aid, "confirm": aid, "pin": PIN}, self.con, headers=hdr)
        self.assertEqual(c2, 404, r2)                                     # a body with a PIN is never cached; the card is simply no longer pending
        self.assertEqual(self.kinds().count("approved"), 1)               # approved once, not twice
        self.assertEqual(self.k.db.execute("SELECT COUNT(*) FROM idempotency WHERE reply LIKE ?", (f"%{PIN}%",)).fetchone()[0], 0)   # the PIN is never kept


class HostileRendering(Rig):
    """Card text with ANSI / HTML / markdown / bidi / tag characters, on every surface that prints it (CLI terminal, Telegram, the
    owner JSON that the React UI shows as escaped text). Nothing hostile reaches a terminal or a chat as an active character."""
    ESC = "\x1b[2J\x1b[31m"
    RLO = "\u202e"

    def test_cli_prompt_has_no_raw_escape_or_direction_characters(self):
        esc = self.pending("e " + self.ESC, target="notepad|esc")                     # a control character: refused outright
        self.assertEqual(self.grant_cli(esc), (1, []))
        self.assertEqual(self.state_of(esc), "pending")
        aid = self.pending("x <b>bold</b> [link](http://evil) `code` \\x1b[31m")
        rc, shown = self.grant_cli(aid)
        text = "".join(shown)
        self.assertTrue(text, "the prompt was shown")
        for ch in ("\x1b", "\u202e", "\x07", "\r"):
            self.assertNotIn(ch, text)
        self.assertEqual(self.state_of(aid), "approved" if rc == 0 else "pending")
        bad = self.pending("y " + self.RLO + "gnp.exe", target="notepad|bidi")
        rc, shown = self.grant_cli(bad)
        self.assertEqual((rc, self.state_of(bad), shown), (1, "pending", []))      # refused before anything is shown or typed

    def test_telegram_sends_plain_text_without_escape_bidi_or_tag_characters(self):
        aid = self.pending("z " + self.ESC + " <i>x</i> *bold* [a](http://e)")
        self.grant_tg(aid)
        for _, t in self.api.sent:
            for ch in ("\x1b", "\u202e", "\x00"):
                self.assertNotIn(ch, t)
        tags = "".join(chr(0xE0000 + ord(c)) for c in "hi")
        bad = self.pending("w" + tags + self.RLO, target="notepad|tg")
        self.api.sent.clear()
        self.grant_tg(bad)
        self.assertEqual(self.state_of(bad), "pending")
        self.assertTrue(all(not any(0xE0000 <= ord(c) <= 0xE007F or c == self.RLO for c in t) for _, t in self.api.sent))

    def test_owner_json_exposes_hostile_text_as_visible_markers_never_as_active_characters(self):
        aid = self.pending("q " + self.ESC + self.RLO + "<script>alert(1)</script>")
        code, st = self.http("GET", "/v1/jarvis/owner/state", None, self.con)
        raw = json.dumps(st, ensure_ascii=False)
        self.assertNotIn("\x1b", raw); self.assertNotIn(self.RLO, raw)
        self.assertIn("<U+", raw)
        card = [c for c in st["approvals"] if c["id"] == aid][0]
        self.assertTrue(card["blocked"])
        self.assertEqual(self.grant_owner(aid)[0], 409)


class Using(Rig):
    """Cells that are about USING an approval: binding, single use, expiry, races, restart."""
    def test_an_approval_is_bound_to_the_exact_action_agent_lane_and_content(self):
        aid = self.pending("exact text", target="notepad|Edit")
        self.assertEqual(self.grant_owner(aid)[0], 200)
        for label, call in (("other text", lambda: self.ui("exact text!", aid=aid)),
                            ("other target", lambda: self.ui("exact text", target="notepad|Edit2", aid=aid)),
                            ("other op", lambda: self.ui("exact text", op="key", aid=aid)),
                            ("other agent, same lane", lambda: self.ui("exact text", tok=self.b, aid=aid)),
                            ("public lane", lambda: self.ui("exact text", tok=self.pub, aid=aid))):
            with self.subTest(label):
                code, r = call()
                self.assertIn(code, (403, 409), (label, r)); self.assertNotEqual(r.get("status"), "queued")
        self.assertEqual(self.state_of(aid), "approved")                  # none of the wrong uses consumed it
        self.assertEqual(self.ui("exact text", aid=aid)[1]["status"], "queued")

    def test_the_same_name_reissued_in_another_lane_cannot_use_it(self):
        aid = self.pending("lane text")
        self.assertEqual(self.grant_owner(aid)[0], 200)
        pub_same_name = self.tokens.issue("hermes", "public")
        code, r = self.ui("lane text", tok=pub_same_name, aid=aid)
        self.assertNotEqual(r.get("status"), "queued")
        self.assertEqual(self.jobs(), 0)

    def test_a_lab_sharing_approval_is_for_one_task_and_one_text(self):
        def mk(tag):
            c, t = self.act({"tool": "task", "op": "create", "target": "Build " + tag,
                             "params": {"goal": "Build " + tag, "criteria": ["it runs"], "request": "please build " + tag + " now"}})
            c, r = self.act({"tool": "task", "op": "dispatch", "target": t["task"], "params": {"instructions": "write " + tag, "public_ok": True}})
            self.assertEqual(c, 202, r)
            return t["task"], r["approval_id"]
        t1, a1 = mk("one"); t2, a2 = mk("two")
        self.assertEqual(self.grant_owner(a1)[0], 200)
        # approval a1 (task one) cannot start task two, even with the other's text
        c, r = self.act({"tool": "task", "op": "dispatch", "target": t2, "params": {"instructions": "write two", "public_ok": True}, "approval_id": a1})
        self.assertEqual(c, 403)
        # nor can another agent use it
        c, r = self.act({"tool": "task", "op": "dispatch", "target": t1, "params": {"public_ok": True}, "approval_id": a1}, self.b)
        self.assertNotEqual(c, 200)
        self.assertEqual(self.act({"tool": "task", "op": "dispatch", "target": t1, "params": {"public_ok": True}, "approval_id": a1})[0], 200)

    def test_single_use_under_a_race(self):
        aid = self.pending("race")
        self.assertEqual(self.grant_owner(aid)[0], 200)
        barrier, results = threading.Barrier(8), []

        def use():
            barrier.wait()
            results.append(self.ui("race", aid=aid)[1].get("status"))
        ts = [threading.Thread(target=use) for _ in range(8)]
        [t.start() for t in ts]; [t.join() for t in ts]
        self.assertEqual(results.count("queued"), 1, results)
        self.assertEqual(self.jobs(), 1)
        # and the grant itself is single too: eight owners approving at once approve once
        aid2 = self.pending("race2", target="notepad|Edit2")
        wins, barrier2 = [], threading.Barrier(8)

        def grant():
            barrier2.wait()
            wins.append(self.k.approve(aid2))
        ts = [threading.Thread(target=grant) for _ in range(8)]
        [t.start() for t in ts]; [t.join() for t in ts]
        self.assertEqual(wins.count(True), 1)

    def test_expiry_and_a_clock_set_back(self):
        aid = self.pending("expiry")
        self.k.db.execute("UPDATE approvals SET expires=? WHERE id=?", (time.time() - 1, aid))
        self.assertEqual(self.grant_owner(aid)[0], 404)                   # expired before it was approved
        aid = self.pending("expiry2", target="notepad|E2")
        self.assertEqual(self.grant_owner(aid)[0], 200)
        self.k.db.execute("UPDATE approvals SET expires=? WHERE id=?", (time.time() - 1, aid))
        self.assertEqual(self.ui("expiry2", target="notepad|E2", aid=aid)[0], 403)      # approved, then expired
        # a clock set back to BEFORE the request was made: the request is not valid yet, the decision is not valid yet
        aid3 = self.pending("clock", target="notepad|E3")
        real = time.time
        time.time = lambda: real() - 3600
        try:
            self.assertFalse(self.k.approve(aid3))
        finally:
            time.time = real
        self.assertEqual(self.grant_owner(aid3)[0], 200)
        real = time.time
        time.time = lambda: real() - 3600
        try:
            self.assertEqual(self.ui("clock", target="notepad|E3", aid=aid3)[0], 403)     # decided "in the future": refused
        finally:
            time.time = real
        self.assertEqual(self.ui("clock", target="notepad|E3", aid=aid3)[1]["status"], "queued")

    def test_a_kill_minus_9_between_approve_and_use_keeps_the_binding(self):
        """A real kernel process is killed with SIGKILL right after the approval is granted. The restarted kernel honours the approval
        for exactly that action and for nothing else."""
        script = (
            "import sys, os, signal; sys.path.insert(0, %r)\n"
            "from jarvis_kernel.store import Kernel\nfrom jarvis_kernel.policy import Action\n"
            "k = Kernel(%r)\n"
            "a = Action('ui', 'type', 'notepad|Edit', {'text': 'across a crash'}, 'private', True, requester='hermes')\n"
            "d, aid = k.request(a)\nassert k.approve(aid)\nprint(aid, flush=True)\nos.kill(os.getpid(), signal.SIGKILL)\n") % (os.path.join(HERE, ".."), self.dir)
        self.srv.shutdown(); self.nsrv.shutdown(); self.k.db.close()
        p = subprocess.run([sys.executable, "-B", "-c", script], capture_output=True, text=True)
        self.assertEqual(p.returncode, -signal.SIGKILL, p.stderr)
        aid = p.stdout.split()[0]
        self.k = Kernel(self.dir)                                          # the "restart"
        self.open()
        self.assertEqual(self.k.approval_info(aid)["state"], "approved")
        code, r = self.ui("a different text", aid=aid)                     # a different action is still refused
        self.assertNotEqual(r.get("status"), "queued")
        code, r = self.ui("across a crash", tok=self.b, aid=aid)           # another agent too
        self.assertNotEqual(r.get("status"), "queued")
        code, r = self.ui("across a crash", aid=aid)
        self.assertEqual((code, r["status"]), (200, "queued"))
        self.assertEqual(self.ui("across a crash", aid=aid)[0], 403)

    def test_a_kill_switch_file_survives_a_restart_and_blocks_after_it(self):
        aid = self.pending("restart kill")
        self.assertEqual(self.grant_owner(aid)[0], 200)
        self.k.kill("test")
        self.srv.shutdown(); self.nsrv.shutdown(); self.k.db.close()
        self.k = Kernel(self.dir); self.open()
        self.assertEqual(self.ui("restart kill", aid=aid)[0], 503)
        self.assertEqual(self.jobs(), 0)


class Cancelling(Rig):
    def mk_dispatch(self, tag):
        c, t = self.act({"tool": "task", "op": "create", "target": "Build " + tag,
                         "params": {"goal": "Build " + tag, "criteria": ["it runs"], "request": "please build " + tag + " now"}})
        c, r = self.act({"tool": "task", "op": "dispatch", "target": t["task"], "params": {"instructions": "write " + tag, "public_ok": True}})
        return t["task"], r["approval_id"]

    def test_cancelling_a_task_withdraws_pending_and_approved_cards_whichever_route_cancels(self):
        for via in ("owner_http", "telegram"):
            with self.subTest(via=via):
                tp, ap = self.mk_dispatch("p" + via)          # pending when the task is cancelled
                ta, aa = self.mk_dispatch("a" + via)          # approved but unused when the task is cancelled
                self.assertEqual(self.grant_owner(aa)[0], 200)
                for tid in (tp, ta):
                    if via == "owner_http":
                        self.assertEqual(self.http("POST", "/v1/jarvis/owner/task", {"id": tid, "op": "cancel"}, self.con)[0], 200)
                    else:
                        self.tg.handle(msg(f"/task {tid} cancel", chat=OWNER_CHAT))
                self.assertEqual((self.state_of(ap), self.state_of(aa)), ("denied", "denied"))
                self.assertFalse(self.k.approve(ap))
                c, r = self.act({"tool": "task", "op": "dispatch", "target": ta, "params": {"public_ok": True}, "approval_id": aa})
                self.assertNotEqual(c, 200)
                self.assertEqual(self.jobs(), 0)

    def test_cancelling_a_task_stops_the_jobs_still_queued_for_it(self):
        c, t = self.act({"tool": "task", "op": "create", "target": "Save things", "params": {"goal": "Save things", "criteria": ["x"], "request": "save my things please"}})
        tid = t["task"]
        c, r = self.act({"tool": "output", "op": "save", "target": "n.md", "params": {"content": "hello", "task": tid}})
        self.assertEqual(c, 200, r)
        jid = r["job"]
        self.assertEqual(self.http("POST", "/v1/jarvis/owner/task", {"id": tid, "op": "cancel"}, self.con)[0], 200)
        self.assertEqual(self.k.db.execute("SELECT state FROM jobs WHERE id=?", (jid,)).fetchone()[0], "cancelled")
        self.assertIsNone(Jobs(self.k).next_for("laptop"))                 # the node is never handed it
        # the same through Telegram
        c, t = self.act({"tool": "task", "op": "create", "target": "Save more", "params": {"goal": "Save more", "criteria": ["x"], "request": "save more things please"}})
        c, r = self.act({"tool": "output", "op": "save", "target": "m.md", "params": {"content": "hello", "task": t["task"]}})
        self.tg.handle(msg(f"/task {t['task']} cancel", chat=OWNER_CHAT))
        self.assertEqual(self.k.db.execute("SELECT state FROM jobs WHERE id=?", (r["job"],)).fetchone()[0], "cancelled")

    def test_stop_all_withdraws_every_unused_approval_and_expires_queued_jobs(self):
        pend = self.pending("stop pending", target="notepad|P")
        appr = self.pending("stop approved", target="notepad|A")
        self.assertEqual(self.grant_owner(appr)[0], 200)
        c, r = self.act({"tool": "output", "op": "save", "target": "q.md", "params": {"content": "queued"}})
        self.assertEqual(self.http("POST", "/v1/jarvis/owner/stop_all", {}, self.con)[0], 200)
        self.assertEqual((self.state_of(pend), self.state_of(appr)), ("denied", "denied"))
        self.assertEqual(self.ui("stop approved", target="notepad|A", aid=appr)[0], 403)
        self.assertIsNone(Jobs(self.k).next_for("laptop"))

    def test_approve_then_cancel_race_never_leaves_a_usable_approval(self):
        for i in range(12):
            tid, aid = self.mk_dispatch(f"race{i}")
            outcome = []

            def approve():
                outcome.append(("approve", self.grant_owner(aid)[0]))

            def cancel():
                outcome.append(("cancel", self.http("POST", "/v1/jarvis/owner/task", {"id": tid, "op": "cancel"}, self.con)[0]))
            ts = [threading.Thread(target=approve), threading.Thread(target=cancel)]
            [t.start() for t in ts]; [t.join() for t in ts]
            code, r = self.act({"tool": "task", "op": "dispatch", "target": tid, "params": {"public_ok": True}, "approval_id": aid})
            self.assertNotEqual(code, 200, (i, outcome, r))          # whichever won, the cancelled task cannot be started
            self.assertEqual(self.jobs(), 0)


class Executing(Rig):
    """Every route that would run something refuses while the kill switch is on, and creates nothing."""
    def test_every_execution_route_refuses_during_a_kill(self):
        tid = self.act({"tool": "task", "op": "create", "target": "t", "params": {"goal": "Goal for kill", "criteria": ["x"], "request": "a request to test the kill"}})[1]["task"]
        self.k.kill("test")
        calls = {
            "agent ui": lambda: self.ui("x")[0],
            "agent pc": lambda: self.act({"tool": "pc", "op": "open_app", "target": "notepad"})[0],
            "agent output": lambda: self.act({"tool": "output", "op": "save", "target": "n.md", "params": {"content": "x"}})[0],
            "agent lab.task": lambda: self.act({"tool": "lab", "op": "task", "target": "s", "params": {"prompt": "p"}}, self.pub)[0],
            "agent task dispatch": lambda: self.act({"tool": "task", "op": "dispatch", "target": tid, "params": {"instructions": "x", "public_ok": True}})[0],
            "agent task create": lambda: self.act({"tool": "task", "op": "create", "target": "g", "params": {"goal": "another goal", "criteria": ["x"], "request": "another request here"}})[0],
            "agent connector": lambda: self.act({"tool": "connector", "op": "call", "target": "x.y", "params": {"arguments": {}}})[0],
            "agent memory add": lambda: self.act({"tool": "memory", "op": "add", "target": "a fact", "params": {}})[0],
            "agent skill": lambda: self.act({"tool": "skill", "op": "activate", "target": "s", "params": {"risk": "low", "where": "lab"}})[0],
            "owner pc": lambda: self.http("POST", "/v1/jarvis/owner/pc", {"op": "open_app", "target": "notepad"}, self.con)[0],
            "owner command": lambda: self.http("POST", "/v1/jarvis/owner/command", {"text": "open my downloads"}, self.con)[0],
            "model call": lambda: self.http("POST", "/v1/chat/completions", {"messages": [{"role": "user", "content": "hi"}]}, self.a)[0],
            "node authorize": lambda: self.http("POST", "/authorize", {"action": {"tool": "state", "op": "read", "target": "x"}}, self.node_tok, base=f"http://127.0.0.1:{self.nsrv.server_address[1]}")[1].get("allowed"),
            "node job fetch": lambda: self.http("POST", "/jobs/next", {}, self.node_tok, base=f"http://127.0.0.1:{self.nsrv.server_address[1]}")[1].get("job"),
        }
        for name, call in calls.items():
            with self.subTest(route=name):
                got = call()
                self.assertTrue(got in (False, None) or (isinstance(got, int) and got >= 400), (name, got))
        self.assertEqual(self.jobs(), 0)
        d = json.loads(json.dumps(self.http("GET", "/v1/jarvis/owner/state", None, self.con)[1]))
        self.assertTrue(d["killed"])
        # automations and the owner command both honour it too (their own checks)
        self.assertEqual(Jobs(self.k).next_for("laptop"), None)

    def test_the_owner_http_has_a_kill_but_no_resume_and_telegram_cannot_resume(self):
        self.assertEqual(self.http("POST", "/v1/jarvis/owner/kill", {}, self.con)[0], 200)
        self.assertTrue(self.k.killed())
        for path in ("resume", "owner/resume", "unkill", "kill/off"):
            self.assertEqual(self.http("POST", "/v1/jarvis/owner/" + path.replace("owner/", ""), {}, self.con)[0], 404)
        self.tg.handle(msg("/resume", chat=OWNER_CHAT))
        self.assertTrue(self.k.killed())
        self.assertEqual(cli.main(["--data", self.dir, "status"]) or 0, 0)


if __name__ == "__main__":
    unittest.main()
