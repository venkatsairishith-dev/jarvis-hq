"""Re-review M4 (2026-10-19): private text must not stay in the kernel's own stores for ever.

Real boundaries: the real HTTP handler, a real sqlite file (WAL mode), and the raw bytes of state.db, state.db-wal after the purge.
A synthetic canary goes into an approval card (used, denied, expired, still pending), a lab share request, a job, a task, an owner
command and a pc.open_* action; the clock is moved past the windows; every table and file the kernel writes is then dumped.
"""
import json
import os
import sqlite3
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
from jarvis_kernel import retention as R
from jarvis_kernel.jobs import Jobs
from jarvis_kernel.llm_proxy import make_handler
from jarvis_kernel.owner import _hash_pin
from jarvis_kernel.policy import Action
from jarvis_kernel.store import Kernel
from jarvis_kernel.tasks import Tasks

PIN = "correct horse battery"
H = 3600
DAY = 86400


def canary(tag):
    return f"CANARY-{tag}-7f3a-Priyanka-SYNTH"


class Rig(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = os.path.join(self.tmp.name, "d")
        self.k = Kernel(self.dir)
        self.start()

    def start(self):
        def hermes(text):
            return "JARVIS says " + canary("reply") + " about " + text[:20]
        self.H, self.tokens = make_handler(self.k, [], owner_hermes=hermes)
        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), self.H)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.srv.server_address[1]}"
        self.agent, self.pub = self.tokens.issue("hermes", "private"), self.tokens.issue("lab", "public")
        self.con = self.tokens.issue("console", "owner")
        self.k.db.execute("INSERT OR REPLACE INTO owner_config(key, value) VALUES ('approval_pin', ?)", (_hash_pin(PIN),))

    def tearDown(self):
        self.srv.shutdown(); self.k.db.close(); self.tmp.cleanup()

    def call(self, method, path, body=None, tok=None):
        r = urllib.request.Request(self.url + path, data=json.dumps(body).encode() if body is not None else None, method=method,
                                   headers={"Authorization": "Bearer " + (tok or self.agent), "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(r, timeout=15) as resp:
                return resp.status, json.loads(resp.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read() or b"{}")

    def act(self, body, tok=None):
        return self.call("POST", "/v1/jarvis/actions", body, tok)

    def approve(self, aid):
        return self.call("POST", "/v1/jarvis/owner/approve", {"id": aid, "confirm": aid, "pin": PIN}, self.con)

    def ask_lab(self, prompt, target, approval_id=None):
        body = {"tool": "lab", "op": "task", "target": target, "params": {"prompt": prompt}}
        if approval_id:
            body["approval_id"] = approval_id
        return self.act(body)

    # ---- everything the kernel keeps
    def dump_tables(self) -> str:
        out = []
        for (name,) in self.k.db.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall():
            out.append(name + ":" + json.dumps(self.k.db.execute(f'SELECT * FROM "{name}"').fetchall(), default=str))
        return "\n".join(out)

    def dump_files(self) -> bytes:
        blob = b""
        for name in sorted(os.listdir(self.dir)):
            with open(os.path.join(self.dir, name), "rb") as fh:
                blob += fh.read()
        return blob

    def everywhere(self, needle: str) -> list:
        """Where `needle` is still found: table dumps, owner state, raw database files (main, WAL)."""
        found = []
        if needle in self.dump_tables():
            found.append("tables")
        if needle.encode() in self.dump_files():
            found.append("files")
        st = json.dumps(self.call("GET", "/v1/jarvis/owner/state", tok=self.con)[1])
        if needle in st:
            found.append("owner state")
        return found

    def sweep(self, later):
        return R.Retention(self.k).sweep("test", now=time.time() + later)


class Approvals(Rig):
    def test_decided_and_expired_cards_lose_their_text_but_keep_their_shape(self):
        used, denied, expired = canary("used"), canary("denied"), canary("expired")
        c, r1 = self.ask_lab(used, "sub-used")
        self.assertEqual(self.approve(r1["approval_id"])[0], 200)
        self.assertEqual(self.ask_lab(used, "sub-used", r1["approval_id"])[1]["status"], "allowed")          # used once
        c, r2 = self.ask_lab(denied, "sub-denied")
        self.assertEqual(self.call("POST", "/v1/jarvis/owner/deny", {"id": r2["approval_id"]}, self.con)[0], 200)
        c, r3 = self.ask_lab(expired, "sub-expired")                                                          # never decided
        self.k.db.execute("UPDATE approvals SET expires=? WHERE id=?", (time.time() - 100, r3["approval_id"]))     # its ten minutes ran out
        for tag in (used, denied, expired):
            self.assertTrue(self.everywhere(tag), "the canary is stored before the purge (the test would prove nothing)")
        # inside the grace period (24 h after the decision / expiry) nothing is removed
        self.assertEqual(self.sweep(2 * H)["approvals"], 0)
        self.assertTrue(self.everywhere(used))
        out = self.sweep(25 * H)
        self.assertEqual(out["approvals"], 3)
        for tag in (used, denied, expired):
            self.assertEqual(self.everywhere(tag), [], tag)
        # ids, states, shape and lengths remain; history renders the stub
        hist = {h["id"]: h for h in self.k.approval_history(20)}
        self.assertEqual({hist[r1["approval_id"]]["state"], hist[r2["approval_id"]]["state"], hist[r3["approval_id"]]["state"]},
                         {"used", "denied", "expired"})
        for h in hist.values():
            self.assertEqual((h["action"]["tool"], h["action"]["op"], h["action"]["lane"]), ("lab", "task", "private"))
            self.assertIn("[text removed", h["action"]["target"])
        info = self.k.approval_info(r1["approval_id"])
        self.assertEqual((info["state"], info["requester"]), ("used", "hermes"))
        act = info["action"]
        self.assertTrue(act["purged"] and act["text_removed"].startswith("text removed after use, "))
        self.assertRegex(act["text_removed"], r"^text removed after use, \d+ characters$")
        self.assertEqual(act["digest"], self.k.db.execute("SELECT action_hash FROM approvals WHERE id=?", (r1["approval_id"],)).fetchone()[0])
        self.assertEqual(len(act["digest"]), 12)
        self.assertEqual(act["fields"]["params.prompt"]["chars"], len(used))
        self.assertEqual(len(act["fields"]["params.prompt"]["id"]), 12)
        self.assertFalse(self.k.approval_info(r1["approval_id"])["incomplete"])
        # the state route (UI) still renders
        st = self.call("GET", "/v1/jarvis/owner/state", tok=self.con)[1]
        self.assertEqual(len(st["approval_history"]), 3)
        # a second run changes nothing (idempotent) and the audit says what was done
        self.assertEqual(self.sweep(26 * H)["approvals"], 0)
        kinds = [(k, json.loads(p)) for k, p in self.k.db.execute("SELECT kind, payload FROM audit WHERE kind='retention_run'").fetchall()]
        self.assertEqual(len(kinds), 1); self.assertEqual(kinds[0][1]["approvals"], 3)
        self.assertTrue(self.k.audit.verify()[0])

    def test_an_undecided_pending_card_keeps_its_whole_text(self):
        text = canary("pending") + " " + "x" * 3000 + " END-OF-CARD"
        c, r = self.ask_lab(text, "sub-pending")
        self.sweep(60)                                                  # inside its ten minutes: the owner may still read and approve it
        card = next(a for a in self.k.pending_approvals() if a["id"] == r["approval_id"])
        self.assertTrue(card["action"]["params"]["prompt"].endswith("END-OF-CARD"))
        self.assertEqual(self.approve(r["approval_id"])[0], 200)
        # approved but not yet used: also untouched until it expires
        self.sweep(5 * 60)
        self.assertIn(canary("pending"), self.dump_tables())
        self.assertEqual(self.ask_lab(text, "sub-pending", r["approval_id"])[1]["status"], "allowed")

    def test_a_purged_card_cannot_be_approved_or_used_and_every_surface_reads_it(self):
        c, r = self.ask_lab(canary("x"), "sub-x")
        self.k.db.execute("UPDATE approvals SET expires=? WHERE id=?", (time.time() - 100, r["approval_id"]))
        self.sweep(25 * H)
        aid = r["approval_id"]
        self.assertFalse(self.k.approve(aid))
        self.assertEqual(self.approve(aid)[0], 404)
        self.assertEqual(self.ask_lab(canary("x"), "sub-x", aid)[0], 403)
        # Telegram and the CLI read the stub through approval_info
        from test_telegram import FakeAPI, msg
        from jarvis_kernel.telegram import Bridge
        api = FakeAPI()
        b = Bridge(self.k, api, 111222333, [])
        b.handle(msg(f"/approve {aid}", chat=111222333))
        self.assertIn("No such pending approval", api.sent[-1][1])
        self.assertEqual(self.k.approval_info(aid)["action"]["tool"], "lab")


class LabShares(Rig):
    def test_the_approved_private_text_is_deleted_once_the_share_is_used_denied_or_expired(self):
        def dispatch(tag):
            c, t = self.act({"tool": "task", "op": "create", "target": "Build " + tag,
                             "params": {"goal": "Build " + tag, "criteria": ["it runs"], "request": "please build " + tag + " now"}})
            c, r = self.act({"tool": "task", "op": "dispatch", "target": t["task"],
                             "params": {"instructions": "write " + canary(tag), "public_ok": True}})
            self.assertEqual((c, r["status"]), (202, "needs_approval"))
            return t["task"], r["approval_id"]
        t1, a1 = dispatch("one"); t2, a2 = dispatch("two"); t3, a3 = dispatch("three")
        rows = lambda: {r[0] for r in self.k.db.execute("SELECT approval_id FROM lab_share_pending").fetchall()}
        self.assertEqual(rows(), {a1, a2, a3})
        self.sweep(30)
        self.assertEqual(rows(), {a1, a2, a3})                           # still waiting for the owner: kept
        self.assertEqual(self.approve(a1)[0], 200)
        self.assertEqual(self.act({"tool": "task", "op": "dispatch", "target": t1, "params": {"public_ok": True}, "approval_id": a1})[0], 200)  # used
        self.assertEqual(self.call("POST", "/v1/jarvis/owner/deny", {"id": a2}, self.con)[0], 200)
        self.sweep(60)
        self.assertEqual(rows(), {a3})                                   # used and denied are gone at once; the pending one stays
        self.sweep(2 * H)
        self.assertEqual(rows(), set())                                   # expired


class Jobs_(Rig):
    def test_job_params_and_results_keep_their_ids_and_lose_their_text(self):
        c, r = self.act({"tool": "output", "op": "save", "target": "notes.md", "params": {"content": "secret " + canary("job")}})
        self.assertEqual(c, 200)
        jid = r["job"]
        J = Jobs(self.k)
        self.assertTrue(J.next_for("laptop"))
        self.assertTrue(J.finish("laptop", jid, {"ok": True, "saved": "D:/jarvis/outputs/" + canary("path"), "note": canary("result")}))
        c2, r2 = self.act({"tool": "output", "op": "save", "target": "later.md", "params": {"content": "keep me " + canary("open")}})
        self.assertEqual(self.sweep(5 * DAY)["jobs"], 0)                  # inside the 14 days
        self.assertTrue(self.everywhere(canary("job")))
        out = self.sweep(15 * DAY)
        self.assertEqual(out["jobs"], 1)
        for tag in ("job", "path", "result"):
            self.assertEqual(self.everywhere(canary(tag)), [], tag)
        self.assertIn(canary("open"), self.dump_tables())                 # a job that is not final is untouched
        row = self.k.db.execute("SELECT kind, state, params, result FROM jobs WHERE id=?", (jid,)).fetchone()
        self.assertEqual((row[0], row[1]), ("output_save", "done"))
        p, res = json.loads(row[2]), json.loads(row[3])
        self.assertTrue(p["_purged"] and p["content"]["_chars"] == len("secret " + canary("job")))
        self.assertTrue(res["_purged"] and res["ok"] is True and res["chars"] > 10)
        st = self.call("GET", "/v1/jarvis/owner/state", tok=self.con)[1]            # the UI's job list still renders
        self.assertIn(jid, [j["id"] for j in st["jobs"]])
        self.assertEqual(J.get_for("hermes", jid)["state"], "done")
        self.assertEqual(self.sweep(16 * DAY)["jobs"], 0)


class TasksAndCommands(Rig):
    def test_tasks_events_and_commands_are_reduced_after_their_window_and_open_ones_are_not(self):
        T = Tasks(self.k)
        c, t = self.act({"tool": "task", "op": "create", "target": canary("goal"),
                         "params": {"goal": canary("goal"), "criteria": ["done " + canary("crit")], "request": "please " + canary("req"),
                                    "plan": ["step " + canary("plan")]}})
        tid = t["task"]
        T.update(None, tid, state="cancelled", note="because " + canary("note"), result="result " + canary("result"))
        c, t2 = self.act({"tool": "task", "op": "create", "target": canary("opengoal"), "params": {"goal": canary("opengoal"), "criteria": ["x"],
                                                                                                "request": "an open task " + canary("open")}})
        c, cmd = self.call("POST", "/v1/jarvis/owner/command", {"text": "tell me about " + canary("cmd")}, self.con)
        for _ in range(100):
            row = self.k.db.execute("SELECT state FROM owner_commands WHERE id=?", (cmd["id"],)).fetchone()
            if row and row[0] != "running":
                break
            time.sleep(0.05)
        self.assertIn(canary("reply"), self.dump_tables())
        self.assertEqual(self.sweep(10 * DAY)["tasks"], 0)
        self.assertEqual(self.sweep(29 * DAY)["commands"], 0)
        out = self.sweep(31 * DAY)
        self.assertEqual((out["tasks"], out["commands"]), (1, 1))
        for tag in ("goal", "crit", "req", "plan", "note", "result", "cmd", "reply"):
            self.assertEqual(self.everywhere(canary(tag)), [], tag)
        self.assertIn(canary("opengoal"), self.dump_tables())              # the open task keeps its words
        done = T.get(tid)
        self.assertEqual((done["state"], done["owner"], done["lane"]), ("cancelled", "hermes", "private"))
        self.assertTrue(done["goal"].startswith("[text removed") and str(len(canary("goal"))) in done["goal"])
        self.assertEqual((done["plan"], done["criteria"], done["evidence"]), ([], [], []))
        self.assertTrue(all(e["note"] == "" or e["note"].startswith("[text removed") for e in done["events"]))
        cmds = self.call("GET", "/v1/jarvis/owner/commands", tok=self.con)[1]["commands"]
        mine = next(c for c in cmds if c["id"] == cmd["id"])
        self.assertEqual(mine["state"], "done")
        self.assertTrue(mine["text"].startswith("[text removed") and mine["reply"].startswith("[text removed"))
        self.assertEqual(self.sweep(40 * DAY), {"enabled": True, **dict.fromkeys(R.Retention.COUNT_KEYS, 0)})

    def test_the_cap_reduces_old_rows_even_inside_the_window(self):
        R.save_settings(self.k.db, {"tasks_keep": 2, "commands_keep": 1})
        T = Tasks(self.k)
        ids = []
        for i in range(4):
            tid = T.create("hermes", "private", "req " + canary(f"cap{i}"), "goal " + canary(f"cap{i}"), None, ["x"])
            T.update(None, tid, state="cancelled")
            self.k.db.execute("UPDATE tasks SET updated=? WHERE id=?", (time.time() - (4 - i) * 60, tid))
            ids.append(tid)
        self.assertEqual(self.sweep(10)["tasks"], 2)                       # the two oldest; the newest two stay readable
        self.assertEqual(sorted(T.get(i)["goal"].startswith("[text removed") for i in ids), [False, False, True, True])


class Audit(Rig):
    def test_new_audit_rows_do_not_hold_pc_open_names_paths_or_the_memory_scope(self):
        folder = "C:\\Users\\" + canary("folder")
        c, r = self.act({"tool": "pc", "op": "open_folder", "target": canary("target"), "params": {"path": folder}}, self.pub)
        self.assertEqual(c, 202, r)
        card = next(a for a in self.k.pending_approvals() if a["id"] == r["approval_id"])
        self.assertIn(canary("target"), json.dumps(card))                   # the owner still reads what he approves
        self.act({"tool": "memory", "op": "add", "target": "a fact", "params": {"scope": canary("scope"), "kind": "fact"}})
        rows = "".join(p for (p,) in self.k.db.execute("SELECT payload FROM audit").fetchall())
        self.assertNotIn(canary("target"), rows); self.assertNotIn(canary("folder"), rows); self.assertNotIn(canary("scope"), rows)
        self.assertIn("open_folder", rows); self.assertIn("name hidden", rows)
        self.assertIn(f"label, {len(canary('scope'))} characters", rows)
        self.assertTrue(self.k.audit.verify()[0])
        a = self.k.audit.anchor()
        self.assertEqual(self.k.audit.verify(a)[0], True)


class Settings(Rig):
    def test_off_means_nothing_is_removed_and_windows_can_be_changed(self):
        c, r = self.ask_lab(canary("off"), "sub-off")
        R.save_settings(self.k.db, {"enabled": False})
        self.assertEqual(self.sweep(30 * DAY), {"enabled": False})
        self.assertTrue(self.everywhere(canary("off")))
        R.save_settings(self.k.db, {"enabled": True, "approvals_grace_hours": 72})
        self.assertEqual(self.sweep(2 * DAY)["approvals"], 0)              # 72 h grace
        self.assertEqual(self.sweep(4 * DAY)["approvals"], 1)
        with self.assertRaises(ValueError):
            R.save_settings(self.k.db, {"tasks_days": -1})
        with self.assertRaises(ValueError):
            R.save_settings(self.k.db, {"nonsense": 1})

    def test_the_environment_switch_turns_it_off(self):
        os.environ["JARVIS_RETENTION"] = "off"
        try:
            self.assertFalse(R.settings(self.k.db)["enabled"])
        finally:
            os.environ.pop("JARVIS_RETENTION")

    def test_the_cli_shows_changes_and_runs_it(self):
        env = dict(os.environ, PYTHONPATH=os.path.join(HERE, ".."), HOME=self.tmp.name)
        env.pop("JARVIS_RETENTION", None)
        self.k.db.close()
        run = lambda *a: subprocess.run([sys.executable, "-B", "-m", "jarvis_kernel", "--data", self.dir, "retention", *a], capture_output=True,
                                        text=True, env=env, cwd=os.path.join(HERE, ".."))
        r = run("--show")
        self.assertEqual(r.returncode, 0, r.stderr); self.assertEqual(json.loads(r.stdout)["settings"]["tasks_days"], 30)
        r = run("--set", "tasks_days=60", "--set", "approvals_grace_hours=48")
        self.assertEqual(json.loads(r.stdout)["settings"]["tasks_days"], 60)
        self.assertEqual(run("--set", "tasks_days=-5").returncode, 1)
        r = run("--off"); self.assertFalse(json.loads(r.stdout)["settings"]["enabled"])
        r = run("--on", "--run"); out = json.loads(r.stdout)
        self.assertTrue(out["settings"]["enabled"]); self.assertEqual(out["run"]["enabled"], True)
        self.k = Kernel(self.dir)                                          # tearDown closes it


class MoreStores(unittest.TestCase):
    """Re-check F (2026-10-19): projectfix.data, connector call rows, finished automations, terminated agent definitions, approvals.reason,
    the keyed fingerprints, and --dry-run."""
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = os.path.join(self.tmp.name, "d")
        self.k = Kernel(self.dir)
        from jarvis_kernel.projectfix import ProjectFix
        from jarvis_kernel.connectors import Connectors
        from jarvis_kernel.automations import Automations
        from jarvis_kernel.agents import AgentDefs
        self.jobs, self.tasks = Jobs(self.k), Tasks(self.k)
        ProjectFix(self.k, self.tasks, self.jobs, lambda *a, **kw: (500, {}))
        Connectors(self.k, self.jobs)
        Automations(self.k, self.tasks, self.jobs)
        AgentDefs(self.k, self.tasks)
        self.now = time.time()
        self.old = self.now - 400 * DAY

    def tearDown(self):
        self.k.db.close(); self.tmp.cleanup()

    def everywhere(self, needle):
        t = "".join(json.dumps(self.k.db.execute(f'SELECT * FROM "{n}"').fetchall(), default=str)
                    for (n,) in self.k.db.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall())
        files = b"".join(open(os.path.join(self.dir, n), "rb").read() for n in os.listdir(self.dir) if os.path.isfile(os.path.join(self.dir, n)))
        return needle in t or needle.encode() in files

    def seed(self):
        db = self.k.db
        db.execute("INSERT INTO projectfix VALUES ('t-done','proj','done',?,?,?)",
                   (json.dumps({"lab_text": {"instructions": canary("pfix")}, "lab_job": "aaaaaaaaaaaa", "attempts": 2, "outcome": "fixed " + canary("pfout")}), self.old, self.old))
        db.execute("INSERT INTO projectfix VALUES ('t-live','proj','apply',?,?,?)", (json.dumps({"lab_text": canary("pflive")}), self.old, self.old))
        db.execute("INSERT INTO connector_calls(ts,connector,tool,risk,agent,lane,decision,outcome,args_digest) VALUES (?,?,?,?,?,?,?,?,?)",
                   (self.old, "mail", "send", "write", "hermes", "private", "approved", "ok", "0123456789abcdef"))
        db.execute("INSERT INTO connector_calls(ts,connector,tool,risk,agent,lane,decision,outcome,args_digest) VALUES (?,?,?,?,?,?,?,?,?)",
                   (self.now, "mail", "send", "write", "hermes", "private", "approved", "ok", "fedcba9876543210"))
        db.execute("INSERT INTO automations(id,name,owner,action_kind,action,trigger_kind,schedule,tz,node,event_spec,enabled,state,created,updated) "
                   "VALUES ('auto1',?,'owner','command',?, 'once','1','UTC','server',NULL,0,'completed',?,?)",
                   ("name " + canary("autoname"), json.dumps({"text": canary("autoact"), "n": 3}), self.old, self.old))
        db.execute("INSERT INTO automations(id,name,owner,action_kind,action,trigger_kind,schedule,tz,node,event_spec,enabled,state,created,updated) "
                   "VALUES ('auto2',?,'owner','command',?, 'once','1','UTC','server',NULL,1,'scheduled',?,?)",
                   ("name " + canary("autolive"), json.dumps({"text": canary("autolivetext")}), self.old, self.old))
        db.execute("INSERT INTO automation_runs(automation_id,run_key,status,detail) VALUES ('auto1','k1','refused',?)", (canary("rundetail"),))
        db.execute("INSERT INTO agent_defs(id,name,purpose,instructions,criteria,workspace,state,version,history,created,updated) VALUES "
                   "('ag1','old agent',?,?,?,?,'terminated',1,'[]',?,?)", (canary("agpurp"), canary("aginstr"), json.dumps([canary("agcrit")]), canary("agws"), self.old, self.old))
        db.execute("INSERT INTO agent_defs(id,name,purpose,instructions,criteria,workspace,state,version,history,created,updated) VALUES "
                   "('ag2','live agent',?,?,'[]','','active',1,'[]',?,?)", (canary("aglive"), canary("aglive2"), self.old, self.old))
        db.execute("INSERT INTO approvals(id,action_hash,level,expires,state,action,reason,created,requester) VALUES ('rr11rr11rr11rr11',?,1,?,'used',?,?,?,'hermes')",
                   ("ab" * 32, self.old + 600, json.dumps({"tool": "browser", "op": "open", "target": "https://" + canary("host") + ".example", "params": {}, "lane": "private"}),
                    "tool is blocked: " + canary("reasonhost"), self.old))

    def test_finished_records_lose_their_text_and_live_ones_keep_it(self):
        self.seed()
        out = R.Retention(self.k).sweep("test", self.now)
        self.assertEqual((out["projectfix"], out["connector_calls"], out["definitions"], out["reasons"] + out["approvals"] > 0), (1, 1, 2, True), out)
        for tag in ("pfix", "pfout", "autoname", "autoact", "rundetail", "agpurp", "aginstr", "agcrit", "agws", "host", "reasonhost"):
            self.assertFalse(self.everywhere(canary(tag)), tag)
        for tag in ("pflive", "autolive", "autolivetext", "aglive", "aglive2"):
            self.assertTrue(self.everywhere(canary(tag)), tag)
        d = json.loads(self.k.db.execute("SELECT data FROM projectfix WHERE task='t-done'").fetchone()[0])
        self.assertEqual((d["lab_job"], d["attempts"], d["_purged"]), ("aaaaaaaaaaaa", 2, True))
        self.assertEqual(d["lab_text"]["_chars"] > 0, True)
        rows = self.k.db.execute("SELECT args_digest FROM connector_calls ORDER BY ts").fetchall()
        self.assertEqual([r[0] for r in rows], [None, "fedcba9876543210"])
        self.assertEqual(self.k.db.execute("SELECT status FROM automation_runs WHERE automation_id='auto1'").fetchone()[0], "refused")
        again = R.Retention(self.k).sweep("test", self.now)
        self.assertEqual(sum(v for k, v in again.items() if k in R.Retention.COUNT_KEYS), 0, again)      # idempotent
        self.assertTrue(self.k.audit.verify()[0])
        self.assertEqual([r[0] for r in self.k.db.execute("SELECT kind FROM audit").fetchall()].count("retention_run"), 1)

    def test_off_switch_and_windows(self):
        self.seed()
        R.save_settings(self.k.db, {"enabled": False})
        self.assertEqual(R.Retention(self.k).sweep("test", self.now), {"enabled": False})
        self.assertTrue(self.everywhere(canary("pfix")))
        R.save_settings(self.k.db, {"enabled": True, "projectfix_days": 1000, "connector_days": 1000, "definitions_days": 1000})
        out = R.Retention(self.k).sweep("test", self.now)
        self.assertEqual((out["projectfix"], out["connector_calls"], out["definitions"]), (0, 0, 0))

    def test_dry_run_counts_and_changes_nothing(self):
        self.seed()
        before_tables = {n: self.k.db.execute(f'SELECT * FROM "{n}"').fetchall() for (n,) in self.k.db.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        files_before = sorted(os.listdir(self.dir))
        out = R.Retention(self.k).sweep("test", self.now, dry_run=True)
        self.assertTrue(out["dry_run"])
        self.assertEqual((out["projectfix"], out["connector_calls"], out["definitions"]), (1, 1, 2))
        after = {n: self.k.db.execute(f'SELECT * FROM "{n}"').fetchall() for (n,) in self.k.db.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        self.assertEqual(before_tables, after)
        self.assertEqual(files_before, sorted(os.listdir(self.dir)))            # not even the key file
        self.assertTrue(self.everywhere(canary("pfix")))

    def test_fingerprints_are_keyed_in_a_separate_0600_file_and_short_text_has_none(self):
        k = self.k
        r = R.Retention(k)
        long_text, short_text = canary("fplong") + " " * 3, "1234"
        tid = self.tasks.create("hermes", "private", long_text + " request", "goal " + short_text, None, ["c"])
        self.tasks.update(None, tid, state="cancelled", note=short_text)
        k.db.execute("UPDATE tasks SET updated=? WHERE id=?", (self.old, tid))
        r.sweep("test", self.now)
        row = k.db.execute("SELECT request, goal FROM tasks WHERE id=?", (tid,)).fetchone()
        self.assertRegex(row[0], r"id [0-9a-f]{12}\]$")                       # long text keeps a prefix
        self.assertNotIn(" id ", row[1])                                       # short text keeps none
        key = os.path.join(self.dir, R.KEY_FILE)
        self.assertEqual(oct(os.stat(key).st_mode & 0o777), "0o600")
        # the prefix is not the old salted sha256 of the text, and is not computable from state.db alone
        import hashlib
        salt = r._salt()
        self.assertNotIn(hashlib.sha256((salt + "|" + (long_text + " request")).encode()).hexdigest()[:12], row[0])
        kt = open(key).read().strip()
        raw = b"".join(open(os.path.join(self.dir, n), "rb").read() for n in os.listdir(self.dir) if n.startswith("state.db"))
        self.assertNotIn(kt.encode(), raw)                                         # the key is in no state.db / WAL byte
        # no key and no way to make one: no prefix at all (fail closed)
        r2 = R.Retention(k); r2._key_cache = b""
        self.assertEqual(r2._fp("x" * 50), "")

    def test_the_cli_dry_run_changes_nothing(self):
        self.seed()
        self.k.db.close()
        p = subprocess.run([sys.executable, "-B", "-m", "jarvis_kernel", "--data", self.dir, "retention", "--dry-run"], cwd=os.path.join(HERE, ".."),
                           capture_output=True, text=True)
        self.assertEqual(p.returncode, 0, p.stderr)
        out = json.loads(p.stdout)
        self.assertTrue(out["dry_run"]["dry_run"]); self.assertEqual(out["dry_run"]["projectfix"], 1)
        self.k = Kernel(self.dir)
        self.assertTrue(self.everywhere(canary("pfix")))


class Migration(unittest.TestCase):
    """A database written by the CURRENT (pre-change) code and schema: the first run applies the policy once, audited."""
    def test_an_old_database_is_cleaned_once_and_every_reader_still_works(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = os.path.join(tmp, "old")
            os.makedirs(d)
            now = time.time()
            old = now - 90 * DAY
            db = sqlite3.connect(os.path.join(d, "state.db"), isolation_level=None)
            db.execute("PRAGMA journal_mode=WAL")
            # the original six-column approvals table (older columns are added by Kernel on open), and the older jobs table
            db.execute("CREATE TABLE approvals(id TEXT PRIMARY KEY, action_hash TEXT, level INT, expires REAL, state TEXT, action TEXT)")
            db.execute("CREATE TABLE jobs(id TEXT PRIMARY KEY, node TEXT, kind TEXT, params TEXT, state TEXT, created REAL, expires REAL, "
                       "result TEXT, notified INT DEFAULT 0, started REAL)")
            card = json.dumps({"tool": "lab", "op": "task", "target": "sub", "params": {"prompt": canary("oldcard") + "x" * 2000 + "...[truncated]"},
                               "lane": "private", "requester": "hermes"})
            db.execute("INSERT INTO approvals VALUES ('aaaa111122223333','" + "ab" * 32 + "',2,?, 'used', ?)", (old + 600, card))
            db.execute("INSERT INTO approvals VALUES ('bbbb111122223333','" + "cd" * 32 + "',2,?, 'pending', ?)", (old + 600, card))
            live_card = card.replace(canary("oldcard"), canary("livecard"))
            db.execute("INSERT INTO approvals VALUES ('cccc111122223333','" + "ef" * 32 + "',2,?, 'pending', ?)", (now + 600, live_card))      # live
            db.execute("INSERT INTO jobs(id,node,kind,params,state,created,expires,result) VALUES ('aaaaaaaaaaaa','laptop','output_save',?,'done',?,?,?)",
                       (json.dumps({"agent": "hermes", "name": "n.md", "content": canary("oldjob")}), old, old + 100, json.dumps({"ok": True, "x": canary("oldres")})))
            db.close()
            k = Kernel(d)
            try:
                Jobs(k)                                                   # the running kernel always has (adds the finished column)
                tasks = Tasks(k)
                tid = tasks.create("hermes", "private", "req " + canary("oldreq"), "goal " + canary("oldgoal"), None, ["c " + canary("oldcrit")])
                tasks.update(None, tid, state="cancelled", note=canary("oldnote"))
                k.db.execute("UPDATE tasks SET updated=? WHERE id=?", (old, tid))
                k.db.execute("CREATE TABLE IF NOT EXISTS owner_commands(id TEXT PRIMARY KEY, ts REAL, source TEXT, text TEXT, state TEXT, reply TEXT, error TEXT, finished REAL)")
                k.db.execute("INSERT INTO owner_commands VALUES ('cmd1',?,'console',?, 'done', ?, NULL, ?)", (old, canary("oldcmd"), canary("oldrep"), old))
                k.db.execute("INSERT INTO owner_commands VALUES ('cmd2',?,'console',?, 'running', NULL, NULL, NULL)", (old, canary("runcmd")))
                k.db.execute("CREATE TABLE IF NOT EXISTS lab_share_pending(approval_id TEXT PRIMARY KEY, task TEXT, agent TEXT, kind TEXT, instructions TEXT, "
                             "criteria TEXT, created REAL)")
                k.db.execute("INSERT INTO lab_share_pending VALUES ('aaaa111122223333','t','hermes','hermes',?,'[]',?)", (canary("oldshare"), old))
                k.db.execute("INSERT INTO lab_share_pending VALUES ('cccc111122223333','t','hermes','hermes',?,'[]',?)", (canary("liveshare"), now))
                R_ = R.Retention(k)
                first = R_.sweep("startup")
                self.assertEqual((first["approvals"], first["lab_shares"], first["jobs"], first["tasks"], first["commands"]), (2, 1, 1, 1, 1))
                self.assertEqual(R_.sweep("startup"), {"enabled": True, **dict.fromkeys(R.Retention.COUNT_KEYS, 0)})
                tables = "".join(json.dumps(k.db.execute(f'SELECT * FROM "{n}"').fetchall(), default=str)
                                 for (n,) in k.db.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall())
                files = b"".join(open(os.path.join(d, n), "rb").read() for n in os.listdir(d))
                for tag in ("oldcard", "oldjob", "oldres", "oldreq", "oldgoal", "oldcrit", "oldnote", "oldcmd", "oldrep", "oldshare"):
                    self.assertNotIn(canary(tag), tables, tag)
                    self.assertNotIn(canary(tag).encode(), files, tag)
                self.assertIn(canary("runcmd"), tables)                  # a command still running is untouched
                self.assertIn(canary("liveshare"), tables)               # a live share is untouched
                self.assertIn(canary("livecard"), tables)                # and so is a live pending card
                live = next(a for a in k.pending_approvals() if a["id"] == "cccc111122223333")
                self.assertTrue(live["truncated"])                       # the live legacy card is intact (and still cut off, so unapprovable)
                hist = {h["id"]: h for h in k.approval_history(10)}
                self.assertEqual(hist["aaaa111122223333"]["state"], "used"); self.assertEqual(hist["bbbb111122223333"]["state"], "expired")
                self.assertIn("[text removed", hist["aaaa111122223333"]["action"]["target"])
                self.assertEqual(k.approval_info("aaaa111122223333")["action"]["fields"]["params.prompt"]["chars"] > 2000, True)
                self.assertTrue(k.audit.verify()[0])
                kinds = [r[0] for r in k.db.execute("SELECT kind FROM audit").fetchall()]
                self.assertEqual(kinds.count("retention_run"), 1)
            finally:
                k.db.close()


if __name__ == "__main__":
    unittest.main()
