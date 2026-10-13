"""Agent actions through the model endpoint: outputs, PC actions with approval, model switching, lab tasks."""
import json
import os
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(__file__)
sys.path.insert(0, os.path.join(HERE, ".."))
sys.path.insert(0, os.path.join(HERE, "..", "..", "node"))
from jarvis_kernel.gateway import Provider
from jarvis_kernel.jobs import Jobs
from jarvis_kernel.llm_proxy import SHOWS_PER_HOUR, make_handler
from jarvis_kernel.store import Kernel
from jarvis_kernel.telegram import Bridge
from jarvis_node import jobs as NJ


def upstream(name, seen):
    class U(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.0"
        def log_message(self, *a): pass
        def do_POST(self):
            self.rfile.read(int(self.headers["Content-Length"])); seen.append(name)
            out = json.dumps({"choices": [{"message": {"role": "assistant", "content": name}}]}).encode()
            self.send_response(200); self.send_header("Content-Type", "application/json"); self.send_header("Content-Length", str(len(out))); self.end_headers(); self.wfile.write(out)
    s = ThreadingHTTPServer(("127.0.0.1", 0), U); threading.Thread(target=s.serve_forever, daemon=True).start()
    return s, f"http://127.0.0.1:{s.server_address[1]}/v1"


class FakeAPI:
    def __init__(self): self.sent = []
    def send(self, chat, text): self.sent.append(text)


class Actions(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.k = Kernel(os.path.join(self.tmp.name, "d")); self.seen = []
        self.ups = [upstream(n, self.seen) for n in ("pubA", "pubB", "priv")]
        provs = [Provider("pubA", "public", self.ups[0][1], None, ["any"], priority=1, model_pattern="."),
                 Provider("pubB", "public", self.ups[1][1], None, ["any"], priority=2, model_pattern="."),
                 Provider("priv", "private", self.ups[2][1], None, ["any"], priority=1)]
        H, tokens = make_handler(self.k, provs)
        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), H); threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.srv.server_address[1]}"
        self.lab = tokens.issue("lab", "public"); self.other = tokens.issue("lab2", "public"); self.owner = tokens.issue("hermes")
        self.tokens = tokens

    def tearDown(self):
        for s, _ in self.ups: s.shutdown()
        self.srv.shutdown(); self.k.db.close(); self.tmp.cleanup()

    def act(self, body, tok=None):
        r = urllib.request.Request(self.url + "/v1/jarvis/actions", data=json.dumps(body).encode(), method="POST",
                                   headers={"Content-Type": "application/json", "Authorization": "Bearer " + (tok or self.lab)})
        try:
            resp = urllib.request.urlopen(r, timeout=10); return resp.status, json.loads(resp.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())

    def job(self, jid, tok=None):
        r = urllib.request.Request(self.url + "/v1/jarvis/jobs/" + jid, headers={"Authorization": "Bearer " + (tok or self.lab)})
        try:
            return json.loads(urllib.request.urlopen(r, timeout=10).read())
        except urllib.error.HTTPError as e:
            return e.code

    def test_save_output_is_queued_for_the_laptop_scrubbed_and_private_to_the_agent(self):
        code, r = self.act({"tool": "output", "op": "save", "target": "report.md", "params": {"content": "done. token gho_abcdefghijklmnopqrstuvwxyz0123"}})
        self.assertEqual((code, r["status"]), (200, "queued"))
        row = self.k.db.execute("SELECT node, kind, params, owner, notified FROM jobs WHERE id=?", (r["job"],)).fetchone()
        self.assertEqual(row[0:2], ("laptop", "output_save")); self.assertEqual(row[3], "lab"); self.assertEqual(row[4], 1)
        self.assertNotIn("gho_abc", row[2])                                     # secrets are scrubbed before the job is stored
        self.assertEqual(self.job(r["job"])["state"], "queued")
        self.assertEqual(self.job(r["job"], self.other), 404)                   # another agent cannot read it
        self.assertEqual(Jobs(self.k).to_report(), [])                         # agent jobs are not sent to the owner's Telegram
        code, r = self.act({"tool": "output", "op": "save", "target": "k.txt", "params": {"content": "-----BEGIN OPENSSH PRIVATE KEY-----\nx"}})
        self.assertEqual(code, 400)

    def test_unsafe_output_names_are_refused(self):
        for name in ("../evil.md", "a/b.md", ".env", "run.exe", "keys.json", "secrets.txt", "x" * 90 + ".md"):
            code, r = self.act({"tool": "output", "op": "save", "target": name, "params": {"content": "x"}})
            self.assertEqual(r["status"], "refused", name)
        self.assertEqual(self.k.db.execute("SELECT COUNT(*) FROM jobs").fetchone()[0], 0)

    def test_pc_action_needs_a_single_use_approval_for_exactly_that_action(self):
        a = {"tool": "ui", "op": "click", "target": "notepad|Save", "params": {}}
        code, r = self.act(a)
        self.assertEqual((code, r["status"]), (202, "needs_approval")); aid = r["approval_id"]
        code, r = self.act({**a, "approval_id": aid}); self.assertEqual(r["status"], "refused")        # not approved yet
        self.assertTrue(self.k.approve(aid))
        code, r = self.act({**a, "target": "notepad|Delete", "approval_id": aid}); self.assertEqual(r["status"], "refused")   # different action
        code, r = self.act({**a, "approval_id": aid}); self.assertEqual((code, r["status"]), (200, "queued"))
        code, r = self.act({**a, "approval_id": aid}); self.assertEqual(r["status"], "refused")        # single use
        code, r = self.act({"tool": "ui", "op": "launch", "target": "bitwarden"}); self.assertEqual((code, r["status"]), (403, "refused"))
        code, r = self.act({"tool": "ui", "op": "format_disk", "target": "c"}); self.assertEqual(r["status"], "refused")

    def test_opening_things_on_the_pc_owner_at_once_lab_only_with_approval(self):
        # the owner's own agent (private lane): queued at once
        code, r = self.act({"tool": "pc", "op": "open_app", "target": "Spotify"}, tok=self.owner)
        self.assertEqual((code, r["status"]), (200, "queued"))
        row = self.k.db.execute("SELECT kind, params FROM jobs WHERE id=?", (r["job"],)).fetchone()
        self.assertEqual(row[0], "pc_open")
        self.assertEqual(json.loads(row[1]), {"agent": "hermes", "op": "open_app", "target": "Spotify", "path": ""})
        code, r = self.act({"tool": "pc", "op": "list_folder", "target": "Downloads"}, tok=self.owner)
        self.assertEqual(r["status"], "queued")
        # a lab agent (public lane): needs the owner's approval, and never lists the owner's folders
        a = {"tool": "pc", "op": "open_folder", "target": "Downloads", "params": {"path": "invoices"}}
        code, r = self.act(a)
        self.assertEqual((code, r["status"]), (202, "needs_approval"))
        self.assertTrue(self.k.approve(r["approval_id"]))
        code, r = self.act({**a, "approval_id": r["approval_id"]})
        self.assertEqual((code, r["status"]), (200, "queued"))
        code, r = self.act({"tool": "pc", "op": "list_folder", "target": "Downloads"})
        self.assertEqual((code, r["status"]), (403, "refused"))
        # never: password managers and wallets, the secrets folder, unknown operations (both lanes)
        for tok in (self.owner, self.lab):
            for bad in ({"op": "open_app", "target": "Bitwarden"}, {"op": "open_folder", "target": "Documents", "params": {"path": "jarvis-secrets"}},
                        {"op": "run_program", "target": "cmd"}):
                code, r = self.act({"tool": "pc", **bad}, tok=tok)
                self.assertEqual(r["status"], "refused", (tok == self.owner, bad))

    def test_hub_overview_is_read_only_and_holds_no_prompt_text(self):
        hub = self.tokens.issue("hub", "dashboard")
        def post(path, body, tok):
            r = urllib.request.Request(self.url + path, data=json.dumps(body).encode(), method="POST",
                                       headers={"Content-Type": "application/json", "Authorization": "Bearer " + tok})
            try:
                return urllib.request.urlopen(r, timeout=10).status
            except urllib.error.HTTPError as e:
                return e.code
        def get(tok):
            r = urllib.request.Request(self.url + "/v1/jarvis/overview", headers={"Authorization": "Bearer " + tok} if tok else {})
            try:
                resp = urllib.request.urlopen(r, timeout=10); return resp.status, resp.read().decode()
            except urllib.error.HTTPError as e:
                return e.code, ""
        secret_words = "PURPLE-ELEPHANT ghp_abcdefghijklmnopqrstuvwxyz0123456789"
        self.assertEqual(post("/v1/chat/completions", {"messages": [{"role": "user", "content": secret_words}]}, self.lab), 200)
        self.act({"tool": "output", "op": "save", "target": "r.md", "params": {"content": secret_words}})
        self.act({"tool": "ui", "op": "click", "target": "notepad|Save"})                  # stays pending
        code, text = get(hub)
        self.assertEqual(code, 200)
        o = json.loads(text)
        self.assertEqual((o["kpis"]["model_calls_24h"], o["kpis"]["approvals_pending"], o["killed"]), (1, 1, False))
        self.assertEqual(o["agents"][0]["name"], "lab"); self.assertEqual(o["agents"][0]["provider"], "pubA")
        self.assertEqual(o["actions_24h"], {"output.save": 1, "ui.click": 1})
        self.assertEqual((sum(o["series_24h"]["calls"]), o["series_24h"]["calls"][-1], sum(o["series_24h"]["actions"])), (1, 1, 2))
        self.assertEqual([j["kind"] for j in o["jobs"]], ["output_save"])
        self.assertEqual(o["approvals"][0]["tool"], "ui")
        self.assertNotIn("PURPLE-ELEPHANT", text); self.assertNotIn("ghp_", text)          # counts and names, never content
        for tok in (self.lab, self.owner):                                                 # agents never read it
            self.assertEqual(get(tok)[0], 403)
        self.assertEqual(get(None)[0], 401)
        self.assertEqual(post("/v1/chat/completions", {"messages": [{"role": "user", "content": "x"}]}, hub), 403)
        self.assertEqual(post("/v1/jarvis/actions", {"tool": "output", "op": "save", "target": "x.md", "params": {"content": "x"}}, hub), 403)

    def test_owner_is_told_about_pending_approvals_once(self):
        self.act({"tool": "ui", "op": "type", "target": "notepad|Text editor", "params": {"text": "hello"}})
        api = FakeAPI(); b = Bridge(self.k, api, 1, [])
        b.flush_approvals(); b.flush_approvals()
        self.assertEqual(len(api.sent), 1); self.assertIn("/approve", api.sent[0]); self.assertIn("ui.type", api.sent[0])

    def test_model_switch_stays_inside_the_lane_and_reorders_the_chain(self):
        code, r = self.act({"tool": "models", "op": "status"})
        self.assertEqual(sorted(p["name"] for p in r["providers"]), ["pubA", "pubB"])          # never the private provider
        code, r = self.act({"tool": "models", "op": "prefer", "target": "priv"}); self.assertEqual(r["status"], "refused")
        code, r = self.act({"tool": "models", "op": "prefer", "target": "pubB"}); self.assertEqual(r["status"], "done")
        r = urllib.request.Request(self.url + "/v1/chat/completions", data=json.dumps({"messages": [{"role": "user", "content": "x"}]}).encode(),
                                   method="POST", headers={"Content-Type": "application/json", "Authorization": "Bearer " + self.lab})
        urllib.request.urlopen(r, timeout=10).read()
        self.assertEqual(self.seen[-1], "pubB")

    def test_limits_kill_switch_and_lab_tasks(self):
        code, r = self.act({"tool": "lab", "op": "task", "target": "opencode", "params": {"prompt": "write a test"}})
        self.assertEqual(r["status"], "allowed")
        for i in range(SHOWS_PER_HOUR):
            self.act({"tool": "output", "op": "show", "target": f"r{i}.md"})
        code, r = self.act({"tool": "output", "op": "show", "target": "late.md"}); self.assertEqual(code, 429)
        self.k.kill("t")
        code, r = self.act({"tool": "output", "op": "save", "target": "a.md", "params": {"content": "x"}}); self.assertEqual(code, 503)
        code, r = self.act({"tool": "output", "op": "save", "target": "a.md"}, tok="wrong"); self.assertEqual(code, 401)


class NodeOutputs(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.root = os.path.realpath(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_save_stays_in_the_agent_folder_and_never_overwrites(self):
        r1 = NJ.output_save({"agent": "lab", "name": "report.md", "content": "one"}, self.root)
        r2 = NJ.output_save({"agent": "lab", "name": "report.md", "content": "two"}, self.root)
        self.assertEqual((r1["saved"], r2["saved"]), ("report.md", "report (2).md"))
        self.assertEqual(open(os.path.join(self.root, "lab", "report.md"), encoding="utf-8").read(), "one")
        for bad in ({"agent": "lab", "name": "../x.md", "content": "x"}, {"agent": "..", "name": "x.md", "content": "x"},
                    {"agent": "lab", "name": "x.exe", "content": "x"}, {"agent": "lab", "name": "x.md", "content": 5}):
            self.assertFalse(NJ.output_save(bad, self.root)["ok"], bad)
        self.assertEqual(sorted(os.listdir(self.root)), ["lab"])

    def test_show_opens_only_saved_outputs(self):
        NJ.output_save({"agent": "lab", "name": "r.md", "content": "x"}, self.root)
        opened = []
        self.assertTrue(NJ.output_show({"agent": "lab", "name": "r.md"}, self.root, opened.append)["ok"])
        self.assertFalse(NJ.output_show({"agent": "lab", "name": "nope.md"}, self.root, opened.append)["ok"])
        self.assertEqual(len(opened), 1)

    def test_ui_action_uses_the_backend_and_reports_failure_kinds_only(self):
        calls = []
        ok = NJ.ui_action({"op": "launch", "target": "notepad", "text": ""}, {"launch": lambda t, p: calls.append(t)})
        self.assertEqual((ok["ok"], calls), (True, ["notepad"]))
        def boom(t, p): raise ValueError("secret screen text")
        bad = NJ.ui_action({"op": "click", "target": "x", "text": ""}, {"click": boom})
        self.assertEqual(bad, {"ok": False, "error": "ValueError"})
        self.assertFalse(NJ.ui_action({"op": "format", "target": "x"}, {})["ok"])


class NodeOpen(unittest.TestCase):
    """The laptop node's opener, on a fake Downloads folder and a fake Start list (nothing really opens)."""
    APPS = [("Spotify", "SpotifyAB.SpotifyMusic_zpdnekdrzrea0!Spotify"), ("Google Chrome", "Chrome"),
            ("Uninstall Spotify", "x"), ("Notepad", "{1AC14E77}\\notepad.exe"), ("Bitwarden", "8bit.bitwarden"),
            ("Microsoft Word", "Microsoft.Office.WINWORD.EXE.15"), ("Microsoft Edge", "MSEdge")]

    def setUp(self):
        from jarvis_node import pcopen
        self.P = pcopen
        self.tmp = tempfile.TemporaryDirectory(); self.root = os.path.realpath(self.tmp.name)
        for d in ("Invoices 2026", "Photos/Trip to Goa", "Projects/jarvis-site"):
            os.makedirs(os.path.join(self.root, d))
        for f, body in (("Invoices 2026/march.pdf", "x"), ("setup.exe", "MZ"), ("notes.txt", "hi"), ("run.bat", "@echo")):
            open(os.path.join(self.root, f), "w").write(body)
        self.roots, self.opened, self.launched = {"downloads": self.root}, [], []

    def tearDown(self):
        self.tmp.cleanup()

    def run_op(self, **p):
        return self.P.pc_open(p, roots=self.roots, apps=self.APPS, opener=self.opened.append, launcher=self.launched.append)

    def test_apps_open_from_the_start_list_by_name(self):
        self.assertEqual(self.run_op(op="open_app", target="spotify"), {"ok": True, "opened": "Spotify"})
        self.assertEqual(self.run_op(op="open_app", target="chrome")["opened"], "Google Chrome")
        self.assertEqual(self.run_op(op="open_app", target="word")["opened"], "Microsoft Word")
        self.assertEqual(self.launched, ["SpotifyAB.SpotifyMusic_zpdnekdrzrea0!Spotify", "Chrome", "Microsoft.Office.WINWORD.EXE.15"])
        no = self.run_op(op="open_app", target="photoshop")
        self.assertFalse(no["ok"]); self.assertIn("closest", no)
        self.assertFalse(self.run_op(op="open_app", target="microsoft")["ok"])          # Word or Edge: ambiguous, ask
        self.assertFalse(self.run_op(op="open_app", target="bitwarden")["ok"])          # never
        self.assertEqual(len(self.launched), 3)

    def test_folders_by_name_inside_the_known_folder_only(self):
        r = self.run_op(op="open_folder", target="Downloads", path="invoices")
        self.assertEqual(r, {"ok": True, "opened": os.path.join("Downloads", "Invoices 2026")})
        self.assertEqual(self.run_op(op="open_folder", target="downloads", path="goa")["opened"], os.path.join("Downloads", "Photos", "Trip to Goa"))
        self.assertEqual(self.run_op(op="open_folder", target="Downloads")["opened"], "Downloads")
        for bad in ("..", "../..", "C:\\Windows", "/etc", "..\\Invoices 2026"):
            self.assertFalse(self.run_op(op="open_folder", target="Downloads", path=bad)["ok"], bad)
        self.assertFalse(self.run_op(op="open_folder", target="System32")["ok"])          # only the known folders
        self.assertEqual(len(self.opened), 3)
        self.assertTrue(all(p.startswith(self.root) for p in self.opened))

    def test_only_documents_open_never_programs_or_scripts(self):
        self.assertTrue(self.run_op(op="open_file", target="Downloads", path="march")["ok"])
        self.assertTrue(self.run_op(op="open_file", target="Downloads", path="notes.txt")["ok"])
        for bad in ("setup", "setup.exe", "run.bat"):
            self.assertFalse(self.run_op(op="open_file", target="Downloads", path=bad)["ok"], bad)
        self.assertEqual([os.path.basename(p) for p in self.opened], ["march.pdf", "notes.txt"])

    def test_list_newest_first(self):
        r = self.run_op(op="list_folder", target="Downloads")
        self.assertTrue(r["ok"]); self.assertEqual(r["count"], 6)                    # 3 folders + 3 files at the top
        self.assertIn("Invoices 2026\\", [i["name"] for i in r["items"]])
        self.assertEqual(self.opened, [])

    def test_start_list_parsing(self):
        apps = self.P.start_apps(lambda: '[{"Name": "Spotify", "AppID": "S!App"}, {"Name": "", "AppID": "x"}]')
        self.assertEqual(apps, [("Spotify", "S!App")])
        self.assertEqual(self.P.start_apps(lambda: '{"Name": "One", "AppID": "1"}'), [("One", "1")])


if __name__ == "__main__":
    unittest.main()
