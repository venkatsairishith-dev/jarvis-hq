"""Private lane, kernel side (re-check F, 2026-10-19): what every provider stub receives for a canary-laden request.

Providers: a PUBLIC stub, a private one that fails, a private one that works (plus a paid private one the free-model guard skips).
A private token sends the canary in a plain call, a streamed call, a tool call, a context-summary call, a call that needs the
retry and one that needs the fallback, and a message built from the memory render. The public stub must see none of it, ever;
the private stubs may. A PUBLIC token's calls never reach a private stub and cannot read a private memory, even with the same name.
"""
import json
import os
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

HERE = os.path.dirname(__file__)
sys.path.insert(0, os.path.join(HERE, ".."))
sys.path.insert(0, HERE)
from jarvis_kernel import sizing
from jarvis_kernel.gateway import Provider
from jarvis_kernel.llm_proxy import make_handler
from jarvis_kernel.store import Kernel
from test_llm_proxy import upstream

CANARY = "CANARY-9c1e-Priyanka-SYNTH-private"


class PrivateLane(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.k = Kernel(os.path.join(self.tmp.name, "d"))
        self.seen, self.servers = [], []
        up = lambda name, **kw: self._up(name, **kw)
        pub_url, bad_url, flaky_url, good_url = up("pub"), up("bad", status=500), up("flaky", fail_first=1), up("good")
        self.providers = [Provider("pub", "public", pub_url, None, ["any"], priority=0),
                          Provider("bad", "private", bad_url, None, ["any"], priority=1),
                          Provider("flaky", "private", flaky_url, None, ["any"], priority=2),
                          Provider("good", "private", good_url, None, ["any"], priority=3)]
        H, self.tokens = make_handler(self.k, self.providers)
        s = ThreadingHTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=s.serve_forever, daemon=True).start()
        self.servers.append(s)
        self.url = f"http://127.0.0.1:{s.server_address[1]}"
        self.priv = self.tokens.issue("hermes", "private")      # (re-issuing a name replaces its token, so the public one is issued later)

    def _up(self, name, **kw):
        s, url = upstream(name, self.seen, **kw)
        self.servers.append(s)
        return url

    def tearDown(self):
        for s in self.servers:
            s.shutdown()
        self.k.db.close(); self.tmp.cleanup()

    def post(self, path, body, tok):
        r = urllib.request.Request(self.url + path, data=json.dumps(body).encode(), method="POST",
                                   headers={"Content-Type": "application/json", "Authorization": "Bearer " + tok})
        try:
            with urllib.request.urlopen(r, timeout=15) as resp:
                return resp.status, resp.read()
        except urllib.error.HTTPError as e:
            return e.code, e.read()

    def chat(self, content, tok=None, **kw):
        return self.post("/v1/chat/completions", {"messages": [{"role": "user", "content": content}], **kw}, tok or self.priv)

    def got(self, name):
        return [b for n, b, a in self.seen if n == name]

    def test_a_canary_in_every_kind_of_private_call_never_reaches_the_public_stub(self):
        tools = [{"type": "function", "function": {"name": "search", "parameters": {"type": "object", "properties": {"q": {"type": "string"}}}}}]
        summary = {"messages": [{"role": "user", "content": sizing.SUMMARY_PREFIX + " summarise: " + CANARY}]}
        self.assertTrue(sizing.is_summary(summary))
        calls = [lambda: self.chat("plain " + CANARY),
                 lambda: self.chat("stream " + CANARY, stream=True),
                 lambda: self.chat("tools " + CANARY, tools=tools),
                 lambda: self.post("/v1/chat/completions", summary, self.priv),
                 lambda: self.chat([{"type": "text", "text": "parts " + CANARY}]),
                 lambda: self.chat("long " + CANARY * 5, max_tokens=50)]
        for i, c in enumerate(calls):
            code, body = c()
            # the stub answers every call with a tool call and no text, so the summary call is rightly refused as an empty summary (502)
            self.assertEqual(code, 502 if i == 3 else 200, (i, body[:300], [n for n, b, a in self.seen]))
        self.assertEqual(self.got("pub"), [])                                   # the public stub was never even asked
        self.assertTrue(self.got("good"))                                       # the private lane answered
        self.assertIn(CANARY, json.dumps(self.got("good")))                     # (it is the private lane: it does get the text)
        # the fallback and retry path: 'bad' fails, 'flaky' fails once then answers; all of that stays private
        names = [n for n, b, a in self.seen]
        self.assertIn("bad", names); self.assertIn("flaky", names)
        self.assertEqual(names.count("pub"), 0)

    def test_every_private_provider_in_the_chain_failing_ends_without_asking_the_public_one(self):
        H, tokens = make_handler(self.k, [self.providers[0], self.providers[1]])        # a public stub and a failing private one only
        s = ThreadingHTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=s.serve_forever, daemon=True).start(); self.servers.append(s)
        url, tok = f"http://127.0.0.1:{s.server_address[1]}", tokens.issue("hermes", "private")
        before = len(self.seen)
        r = urllib.request.Request(url + "/v1/chat/completions", data=json.dumps({"messages": [{"role": "user", "content": CANARY}]}).encode(),
                                   method="POST", headers={"Content-Type": "application/json", "Authorization": "Bearer " + tok})
        try:
            code = urllib.request.urlopen(r, timeout=15).status
        except urllib.error.HTTPError as e:
            code = e.code
        self.assertGreaterEqual(code, 400)
        self.assertEqual([n for n, b, a in self.seen[before:] if n == "pub"], [])

    def test_the_memory_render_of_a_private_entry_goes_only_to_private_stubs_and_a_public_token_cannot_get_it(self):
        def mem(op, target, tok, **params):
            return json.loads(self.post("/v1/jarvis/actions", {"tool": "memory", "op": op, "target": target, "params": params}, tok)[1] or b"{}")
        mem("add", "The owner's secret nickname is " + CANARY, self.priv)
        rendered = mem("render", "", self.priv).get("text", "")
        self.assertIn(CANARY, rendered)
        self.assertEqual(self.chat("context:\n" + rendered)[0], 200)             # the agent puts the render into its prompt
        self.assertEqual(self.got("pub"), [])
        self.pubtok = self.tokens.issue("hermes", "public")
        # a public token with the SAME name: nothing to render, nothing to find, and its model calls go to the public stub only
        self.assertNotIn(CANARY, json.dumps(mem("render", "", self.pubtok)))
        self.assertNotIn(CANARY, json.dumps(mem("list", "", self.pubtok, query="nickname")))
        self.assertNotIn(CANARY, json.dumps(mem("resolve", "secret nickname", self.pubtok)))
        before = len(self.seen)
        self.assertEqual(self.chat("hello", tok=self.pubtok)[0], 200)
        used = {n for n, b, a in self.seen[before:]}
        self.assertEqual(used, {"pub"})                                           # a public token can never reach a private stub
        self.assertNotIn(CANARY, json.dumps([b for n, b, a in self.seen[before:]]))
        # nothing in the audit chain holds the canary either
        rows = " ".join(r[0] for r in self.k.db.execute("SELECT payload FROM audit"))
        self.assertNotIn(CANARY, rows)


class FilesAtRest(unittest.TestCase):
    @unittest.skipUnless(os.name == "posix", "POSIX file modes (0700/0600, umask) do not exist on Windows; see the ACL question")
    def test_the_data_folder_and_database_are_owner_only_even_under_a_loose_umask(self):
        old = os.umask(0o002)
        try:
            with tempfile.TemporaryDirectory() as d:
                k = Kernel(os.path.join(d, "k"))
                k.kill("test"); k.resume(); k.audit.append("x", {})
                k.db.execute("CREATE TABLE IF NOT EXISTS t(x)"); k.db.execute("INSERT INTO t VALUES (1)")
                modes = {n: os.stat(os.path.join(d, "k", n)).st_mode & 0o777 for n in os.listdir(os.path.join(d, "k"))}
                self.assertEqual(os.stat(os.path.join(d, "k")).st_mode & 0o777, 0o700)
                self.assertTrue(modes and all(m == 0o600 for m in modes.values()), modes)
                self.assertIn("state.db", modes)
                k.db.close()
                # an existing loose folder is tightened on the next start
                os.chmod(os.path.join(d, "k"), 0o775); os.chmod(os.path.join(d, "k", "state.db"), 0o644)
                Kernel(os.path.join(d, "k")).db.close()
                self.assertEqual((os.stat(os.path.join(d, "k")).st_mode & 0o777, os.stat(os.path.join(d, "k", "state.db")).st_mode & 0o777), (0o700, 0o600))
        finally:
            os.umask(old)


if __name__ == "__main__":
    unittest.main()
