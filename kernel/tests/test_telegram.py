import json
import os
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(__file__)
sys.path.insert(0, os.path.join(HERE, ".."))
from jarvis_kernel.gateway import Provider
from jarvis_kernel.nodes import Nodes
from jarvis_kernel.policy import Action
from jarvis_kernel.store import Kernel
from jarvis_kernel.telegram import Bridge, TelegramAPI, CHUNK

OWNER = 111222333


class FakeAPI:
    def __init__(self):
        self.sent = []

    def send(self, chat_id, text):
        self.sent.append((chat_id, text))

    def get_updates(self, offset, wait=25):
        return []


def msg(text, chat=OWNER, sender=None, typ="private"):
    return {"update_id": 1, "message": {"text": text, "chat": {"id": chat, "type": typ}, "from": {"id": sender if sender is not None else chat}}}


class LLM(BaseHTTPRequestHandler):
    seen = []
    def log_message(self, *a): pass
    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        LLM.seen.append(body)
        reply = LLM.reply
        out = json.dumps({"choices": [{"message": {"content": reply}}]}).encode()
        self.send_response(200); self.send_header("Content-Length", str(len(out))); self.end_headers(); self.wfile.write(out)
    reply = "an answer"


class T(unittest.TestCase):
    def setUp(self):
        LLM.seen = []; LLM.reply = "an answer"
        self.tmp = tempfile.TemporaryDirectory()
        self.k = Kernel(os.path.join(self.tmp.name, "d"))
        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), LLM)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.prov = [Provider("fake", "private", f"http://127.0.0.1:{self.srv.server_address[1]}/v1", None, ["any"])]
        self.api, self.now = FakeAPI(), [1000.0]
        self.b = Bridge(self.k, self.api, OWNER, self.prov, clock=lambda: self.now[0])

    def tearDown(self):
        self.srv.shutdown(); self.k.db.close(); self.tmp.cleanup()

    def last(self):
        return self.api.sent[-1][1] if self.api.sent else None

    def audit_kinds(self):
        return [r[0] for r in self.k.db.execute("SELECT kind FROM audit")]

    # ---- who is served ----
    def test_strangers_are_ignored_and_cannot_kill(self):
        for u in (msg("/kill", chat=999), msg("/kill", chat=OWNER, sender=999), msg("/kill", typ="group"), msg("/status", chat=-100123, typ="supergroup")):
            self.b.handle(u)
        self.assertEqual(self.api.sent, [])
        self.assertFalse(self.k.killed())
        self.assertEqual(self.audit_kinds().count("tg_ignored"), 4)

    def test_stranger_text_not_logged(self):
        self.b.handle(msg("my secret plan 12345", chat=999))
        rows = " ".join(r[0] for r in self.k.db.execute("SELECT payload FROM audit"))
        self.assertNotIn("secret plan", rows); self.assertNotIn("999", rows)

    # ---- basics ----
    def test_help_status_and_unknown(self):
        self.b.handle(msg("/help")); self.assertIn("/status", self.last())
        self.b.handle(msg("/status")); self.assertIn("Kill switch: off", self.last()); self.assertIn("fake", self.last())
        self.assertIn("Secret scanner:", self.last())
        self.b.handle(msg("/frobnicate")); self.assertIn("Unknown", self.last())

    def test_kill_from_phone_and_resume_refused(self):
        self.b.handle(msg("/kill")); self.assertTrue(self.k.killed())
        self.b.handle(msg("/resume")); self.assertIn("console", self.last()); self.assertTrue(self.k.killed())
        self.b.handle(msg("what is 2+2")); self.assertIn("Kill switch is on", self.last()); self.assertEqual(LLM.seen, [])

    # ---- approvals ----
    def request(self, tool="pkg", op="install", target="requests"):
        d, aid = self.k.request(Action(tool, op, target)); return aid

    def test_approve_flow_needs_exact_confirm_and_is_single_use(self):
        a = Action("pkg", "install", "requests"); aid = self.request()
        self.b.handle(msg(f"/approve {aid}")); self.assertIn(f"CONFIRM {aid}", self.last()); self.assertIn("requests", self.last())
        self.assertFalse(self.k.authorize(a, aid).allowed)          # not approved by /approve alone
        self.b.handle(msg(f"CONFIRM {aid}")); self.assertIn("Approved", self.last())
        self.assertTrue(self.k.authorize(a, aid).allowed)
        self.assertFalse(self.k.authorize(a, aid).allowed)          # replay refused

    def test_confirm_without_approve_or_after_window_refused(self):
        aid = self.request()
        self.b.handle(msg(f"CONFIRM {aid}")); self.assertIn("No recent", self.last())
        self.b.handle(msg(f"/approve {aid}")); self.now[0] += 61
        self.b.handle(msg(f"CONFIRM {aid}")); self.assertIn("No recent", self.last())
        self.assertFalse(self.k.authorize(Action("pkg", "install", "requests"), aid).allowed)

    def test_red_actions_cannot_be_approved_from_telegram(self):
        aid = self.request("money", "pay", "5")
        self.b.handle(msg(f"/approve {aid}")); self.assertIn("console", self.last())
        self.b.handle(msg(f"CONFIRM {aid}")); self.assertIn("No recent", self.last())
        self.assertFalse(self.k.authorize(Action("money", "pay", "5"), aid).allowed)

    def test_deny_blocks_later_approval(self):
        aid = self.request(); self.b.handle(msg(f"/deny {aid}")); self.assertIn("Denied", self.last())
        self.b.handle(msg(f"/approve {aid}")); self.assertIn("No such", self.last())

    def test_wrong_ids_lock_out_after_five(self):
        for i in range(5): self.b.handle(msg(f"/approve nope{i}"))
        aid = self.request(); self.b.handle(msg(f"/approve {aid}")); self.assertIn("locked", self.last())
        self.now[0] += 601
        self.b.handle(msg(f"/approve {aid}")); self.assertIn("CONFIRM", self.last())

    def test_no_approvals_while_killed(self):
        aid = self.request(); self.b.handle(msg(f"/approve {aid}")); self.k.kill("t")
        self.b.handle(msg(f"CONFIRM {aid}")); self.assertIn("Kill switch is on", self.last())

    # ---- ask ----
    def test_plain_text_goes_to_model_and_is_labelled_unverified(self):
        self.b.handle(msg("what is the capital of France"))
        self.assertIn("unverified", self.last()); self.assertIn("an answer", self.last())
        self.assertEqual(LLM.seen[0]["messages"][0]["content"], "what is the capital of France")

    def test_model_output_is_never_interpreted_as_a_command(self):
        LLM.reply = "/kill\nCONFIRM abc\n/approve abc"
        self.b.handle(msg("hello"))
        self.assertFalse(self.k.killed())
        self.assertEqual(len([a for a in self.audit_kinds() if a.startswith("tg_approve")]), 0)

    def test_secrets_scrubbed_both_ways(self):
        self.b.handle(msg("use gho_abcdefghijklmnopqrstuvwxyz0123 please"))
        self.assertNotIn("gho_abc", LLM.seen[0]["messages"][0]["content"])
        LLM.reply = "your key is API_KEY=hunter2hunter2 ok"
        self.b.handle(msg("repeat it"))
        self.assertNotIn("hunter2", self.last())

    def test_ask_rate_limit(self):
        for i in range(20): self.b.handle(msg(f"q{i}"))
        self.b.handle(msg("one more")); self.assertIn("limit", self.last()); self.assertEqual(len(LLM.seen), 20)
        self.now[0] += 3601
        self.b.handle(msg("again")); self.assertIn("unverified", self.last())

    def test_long_input_refused_and_long_output_chunked(self):
        self.b.handle(msg("x" * 2500)); self.assertIn("too long", self.last()); self.assertEqual(LLM.seen, [])
        LLM.reply = "y" * (CHUNK * 2 + 10)
        n = len(self.api.sent); self.b.handle(msg("long please"))
        parts = self.api.sent[n:]; self.assertGreaterEqual(len(parts), 3); self.assertTrue(all(len(t) <= CHUNK for _, t in parts))

    def test_no_provider_gives_a_clean_message(self):
        b = Bridge(self.k, self.api, OWNER, [], clock=lambda: self.now[0])
        b.handle(msg("hi")); self.assertIn("No answer", self.last())

    # ---- analyze ----
    def test_analyze_reports_from_state_only(self):
        n = Nodes(self.k); tok = n.pair(n.new_code(), "laptop")
        from test_nodes import STATE
        n.report(tok, STATE)
        self.b.handle(msg("/analyze laptop"))
        self.assertIn("MacBookPro16,1", self.api.sent[-1][1]); self.assertIn("numbers not found in collected state: none", self.api.sent[-1][1])

    # ---- the API wrapper never leaks the token ----
    def test_api_errors_do_not_contain_the_token(self):
        class H(BaseHTTPRequestHandler):
            def log_message(self, *a): pass
            def do_POST(self):
                self.send_response(401); self.send_header("Content-Length", "0"); self.end_headers()
        s = ThreadingHTTPServer(("127.0.0.1", 0), H); threading.Thread(target=s.serve_forever, daemon=True).start()
        api = TelegramAPI("123456:SECRET_TOKEN_VALUE_abcdefghijklmnopqrstuv", f"http://127.0.0.1:{s.server_address[1]}")
        with self.assertRaises(RuntimeError) as cm:
            api.send(1, "x")
        self.assertNotIn("SECRET_TOKEN", str(cm.exception)); self.assertIsNone(cm.exception.__cause__); self.assertTrue(cm.exception.__suppress_context__)
        api2 = TelegramAPI("123456:SECRET_TOKEN_VALUE_abcdefghijklmnopqrstuv", "http://127.0.0.1:9")
        with self.assertRaises(RuntimeError) as cm2:
            api2.get_updates(0, wait=1)
        self.assertNotIn("SECRET_TOKEN", str(cm2.exception))
        s.shutdown()

    # ---- /recent, /capabilities, /can, /models ----
    def test_recent_summarises_the_audit_log_without_content(self):
        self.b.handle(msg("/recent")); self.assertIn("Last 24 hours", self.last())
        self.k.audit.append("monit_alert", {"service": "jarvis_embed", "event": "Does not exist", "kind": "fail"})
        self.k.audit.append("kill", {"why": "cli"})
        self.k.audit.append("authorize", {"action": {"tool": "money"}, "allowed": False, "level": "RED"})
        self.b.handle(msg("what is 2 plus 2 gho_abcdefghijklmnopqrstuvwxyz0123"))
        self.b.handle(msg("/recent"))
        out = self.last()
        self.assertIn("Notable:", out); self.assertIn("monit_alert", out); self.assertIn("jarvis_embed", out); self.assertIn("kill", out)
        self.assertIn("Actions refused by policy: 1", out)
        self.assertNotIn("2 plus 2", out); self.assertNotIn("gho_", out)      # message text is never in the audit log or the summary

    def test_recent_with_nothing_notable(self):
        self.b.handle(msg("/status")); self.b.handle(msg("/recent"))
        self.assertIn("Nothing notable", self.last())

    def test_capabilities_come_from_the_real_policy_and_state(self):
        n = Nodes(self.k); tok = n.pair(n.new_code(), "laptop")
        from test_nodes import STATE
        n.report(tok, STATE)
        self.b.handle(msg("/capabilities")); out = self.last()
        for expect in ("GREEN:", "fs.read", "YELLOW_APPROVAL:", "RED:", "Always refused (DENY)", "fake (private)", "Node laptop is online", "git"):
            self.assertIn(expect, out)
        self.assertNotIn("ollama", out.split("Node laptop")[1])           # STATE has no ollama, so it is not claimed

    def test_can_gives_a_verdict_from_the_policy_and_runs_nothing(self):
        for text, expect in (("/can fs read notes.md", "YES\n"), ("/can fs write notes.md", "YES_WITH_APPROVAL"), ("/can money pay 5", "YES_WITH_CONSOLE_APPROVAL"),
                             ("/can fs read D:/jarvis-secrets/keys.json", "NO\n"), ("/can oci terminate hermes-agent-vm-2", "NO\n"), ("/can nonsense thing", "YES_WITH_CONSOLE_APPROVAL")):
            self.b.handle(msg(text)); self.assertTrue(self.last().startswith(expect), (text, self.last()))
        self.b.handle(msg("/can")); self.assertIn("Usage", self.last())
        self.assertEqual(self.k.db.execute("SELECT COUNT(*) FROM approvals").fetchone()[0], 0)   # asking never creates an approval

    def test_models_lists_the_registry(self):
        self.b.handle(msg("/models")); out = self.last()
        self.assertIn("qwen3-4b-q4km-laptop", out); self.assertIn("production", out); self.assertIn("35 tokens/s", out)
        self.assertIn("groq-gpt-oss-120b", out)

    def test_restart_drops_the_backlog_instead_of_replaying_it(self):
        class Queued(FakeAPI):
            def __init__(self):
                super().__init__(); self.calls = []
            def get_updates(self, offset, wait=25):
                self.calls.append(offset)
                if offset == -1:   # Telegram returns the newest queued update for offset -1
                    return [msg("/kill") | {"update_id": 41}]
                return []
        api = Queued()
        Bridge(self.k, api, OWNER, self.prov).run(once=True)
        self.assertFalse(self.k.killed())                      # the old /kill was NOT executed
        self.assertEqual(api.calls[:2], [-1, 42])               # peeked, then polled from after it
        self.assertIn("tg_backlog_dropped", self.audit_kinds())
        self.assertEqual([t for _, t in api.sent], ["JARVIS kernel online."])

    def test_every_kind_of_network_failure_is_free_of_the_token(self):
        import socket
        tok = "123456:SECRET_TOKEN_VALUE_abcdefghijklmnopqrstuv"
        class Reset(BaseHTTPRequestHandler):
            def log_message(self, *a): pass
            def do_POST(self):
                self.connection.shutdown(socket.SHUT_RDWR)
        class Garbage(BaseHTTPRequestHandler):
            def log_message(self, *a): pass
            def do_POST(self):
                body = b"not json at all " + tok.encode()
                self.send_response(200); self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)
        class Err500(BaseHTTPRequestHandler):
            def log_message(self, *a): pass
            def do_POST(self):
                self.send_response(500); self.send_header("Content-Length", "0"); self.end_headers()
        for handler in (Reset, Garbage, Err500):
            s = ThreadingHTTPServer(("127.0.0.1", 0), handler); threading.Thread(target=s.serve_forever, daemon=True).start()
            api = TelegramAPI(tok, f"http://127.0.0.1:{s.server_address[1]}")
            for call in (lambda: api.send(1, "x"), lambda: api.get_updates(0, wait=1), lambda: api.get_me()):
                with self.assertRaises(Exception) as cm:
                    call()
                text = repr(cm.exception) + str(cm.exception) + repr(getattr(cm.exception, "__cause__", None)) + repr(getattr(cm.exception, "__context__", None))
                self.assertNotIn("SECRET_TOKEN", text, handler.__name__)
            s.shutdown()

    def test_start_up_check_refuses_a_bad_token(self):
        class BadToken(FakeAPI):
            def get_me(self): raise RuntimeError("telegram HTTP 401")
        with self.assertRaises(SystemExit):
            Bridge(self.k, BadToken(), OWNER, self.prov).run(once=True)
        self.assertIn("tg_start_check_failed", self.audit_kinds())
        ok = FakeAPI(); ok.get_me = lambda: "mybot"
        Bridge(self.k, ok, OWNER, self.prov).run(once=True)
        self.assertIn("tg_start_check", self.audit_kinds())

    def test_poll_errors_back_off_and_are_audited_without_token(self):
        class Bad(FakeAPI):
            def get_updates(self, offset, wait=25): raise RuntimeError("telegram error URLError")
        # Swap only the bridge's own back-off sleep. Patching the global time.sleep also caught other code's sleeps
        # (on Linux, subprocess wait loops while gitleaks ran), which made this test flaky on a busy server.
        slept = []
        Bridge(self.k, Bad(), OWNER, self.prov, sleep=slept.append).run(once=True)
        self.assertEqual(slept, [1]); self.assertIn("tg_poll_error", self.audit_kinds())

    # ---- plain messages go to the owner's Hermes (JARVIS itself) ----
    def agent_bridge(self, agent):
        pending = []
        b = Bridge(self.k, self.api, OWNER, self.prov, clock=lambda: self.now[0], agent=agent, spawn=pending.append)
        return b, pending

    def test_plain_message_is_a_request_to_the_agent_in_the_background(self):
        asked = []
        b, pending = self.agent_bridge(lambda text: asked.append(text) or "Opened Downloads on your laptop.")
        b.handle(msg("open my downloads folder"))
        self.assertEqual(self.last(), "On it.")
        b.handle(msg("open spotify"))                                   # one request at a time
        self.assertIn("Still working", self.last())
        b.handle(msg("/status"))                                        # commands still answer while it works
        self.assertIn("Kill switch", self.last())
        pending[0]()
        self.assertEqual((asked, self.last()), (["open my downloads folder"], "Opened Downloads on your laptop."))
        b.handle(msg("open spotify"))                                   # free again
        self.assertEqual(self.last(), "On it.")
        self.assertEqual(LLM.seen, [])                                  # the plain model was not used
        b.handle(msg("/ask what is 2+2"))                               # /ask stays the quick model answer
        self.assertEqual(len(LLM.seen), 1)
        self.assertIn("tg_agent_turn", self.audit_kinds())

    def test_agent_failure_is_reported_and_releases_the_turn(self):
        def boom(text): raise RuntimeError("agent HTTP 500")
        b, pending = self.agent_bridge(boom)
        b.handle(msg("open spotify")); pending[0]()
        self.assertIn("could not finish", self.last()); self.assertIn("tg_agent_failed", self.audit_kinds())
        b.handle(msg("try again")); self.assertEqual(self.last(), "On it.")

    def test_agent_obeys_kill_switch_and_limits(self):
        b, pending = self.agent_bridge(lambda text: "ok")
        self.k.kill("t"); b.handle(msg("open spotify"))
        self.assertIn("Kill switch", self.last()); self.assertEqual(pending, [])
        self.k.resume()
        for i in range(20):
            b.handle(msg(f"request {i}")); pending.pop()()
        b.handle(msg("one more")); self.assertIn("limit", self.last())

    def test_hermes_api_call_sends_key_and_session_and_hides_errors(self):
        from jarvis_kernel.telegram import hermes_agent
        seen = []

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a): pass
            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                seen.append((self.path, self.headers.get("Authorization"), self.headers.get("X-Hermes-Session-Id"), body))
                code = 500 if body["messages"][0]["content"] == "fail" else 200
                out = json.dumps({"choices": [{"message": {"content": "Opened Downloads."}}]}).encode()
                self.send_response(code); self.send_header("Content-Length", str(len(out))); self.end_headers(); self.wfile.write(out)
        srv = ThreadingHTTPServer(("127.0.0.1", 0), H); threading.Thread(target=srv.serve_forever, daemon=True).start()
        try:
            keyf = os.path.join(self.tmp.name, "hermes_api_key.txt"); open(keyf, "w").write("hk_SECRETVALUE_123\n")
            now = [1791633600.0]                                              # 2026-10-10 UTC
            state = os.path.join(self.tmp.name, "tg_session.txt")
            call = hermes_agent(keyf, f"http://127.0.0.1:{srv.server_address[1]}", state_path=state, clock=lambda: now[0])
            self.assertEqual(call("open my downloads folder"), "Opened Downloads.")
            path, auth, session, body = seen[0]
            self.assertEqual((path, auth, session, body["model"]), ("/v1/chat/completions", "Bearer hk_SECRETVALUE_123", "telegram-owner-20261010", "hermes-agent"))  # gitleaks:allow (fake test key)
            call("open it"); self.assertEqual(seen[1][2], "telegram-owner-20261010")       # same conversation
            with self.assertRaises(RuntimeError) as cm:
                call("fail")
            self.assertNotIn("SECRETVALUE", str(cm.exception))
            call("again"); fresh = seen[3][2]
            self.assertTrue(fresh.startswith("telegram-owner-20261010-"), fresh)           # a failed turn: fresh session
            again = hermes_agent(keyf, f"http://127.0.0.1:{srv.server_address[1]}", state_path=state, clock=lambda: now[0])
            again("after a restart"); self.assertEqual(seen[4][2], fresh)                  # kept across a bridge restart
            now[0] += 86400; call("next day"); self.assertEqual(seen[5][2], "telegram-owner-20261011")
        finally:
            srv.shutdown()


if __name__ == "__main__":
    unittest.main()
