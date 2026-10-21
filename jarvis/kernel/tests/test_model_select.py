import json
import os
import sqlite3
import sys
import tempfile
import time
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from jarvis_kernel import model_select as ms
from jarvis_kernel.gateway import Provider

REPO_CONFIG = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "..", "config"))


def read(path):
    with open(path, "rb") as f:
        return f.read()


def P(name, lane, **kw):
    kw.setdefault("base_url", "http://127.0.0.1:1/v1")
    kw.setdefault("key_file", None)
    kw.setdefault("models", ["any"])
    return Provider(name=name, lane=lane, **kw)


CATALOG = {"source": "test", "built": "2026-10-20", "source_sha256": "0" * 64, "providers": {
    "cloud-a": {"models": {"m-tools": {"tool_call": True, "attachment": False, "limit": {"context": 8000, "output": 2000},
                                       "modalities": {"input": ["text"]}, "cost": {"input": 0, "output": 0}}}},
    "cloud-b": {"models": {"m-plain": {"tool_call": False, "limit": {"context": 131072},
                                       "modalities": {"input": ["text", "image"]}, "cost": {"input": 0.1, "output": 0.4}}}},
}}


def providers():
    return [P("local", "local", priority=10),
            P("cloud-a", "private", priority=20, default_model="m-tools"),
            P("cloud-b", "private", priority=30, default_model="m-plain"),
            P("public-x", "public", priority=5, default_model="m-pub"),
            P("paid-y", "private", priority=1, paid=True, default_model="m-tools")]


class NoNetwork(unittest.TestCase):
    """Every test runs with outbound connections made impossible: the selector must not need them."""

    def setUp(self):
        self.net = mock.patch("urllib.request.urlopen", side_effect=AssertionError("network used"))
        self.sock = mock.patch("socket.create_connection", side_effect=AssertionError("network used"))
        self.net.start(); self.sock.start()

    def tearDown(self):
        self.net.stop(); self.sock.stop()


class TestSelect(NoNetwork):
    def test_plain_task_follows_the_live_route(self):
        req = ms.requirements("hello", lane="private")
        out = ms.select(providers(), CATALOG, req)
        self.assertEqual(out["recommended"], "local")
        self.assertEqual(out["chain"], ["local", "cloud-a", "cloud-b"])
        self.assertFalse(out["differs_from_live_route"])
        self.assertEqual((out["applies"], out["calls_made"]), (False, 0))

    def test_private_task_never_reaches_public_or_paid(self):
        out = ms.select(providers(), CATALOG, ms.requirements("x", lane="private"))
        rows = {r["provider"]: r for r in out["candidates"]}
        self.assertIn("LANE_MISMATCH", rows["public-x"]["route_codes"])
        self.assertIn("PAID_REFUSED", rows["paid-y"]["route_codes"])
        self.assertNotIn("public-x", out["chain"]); self.assertNotIn("paid-y", out["chain"])

    def test_unknown_capability_is_not_assumed(self):
        out = ms.select(providers(), CATALOG, ms.requirements("use a tool", lane="private", tools=True))
        rows = {r["provider"]: r for r in out["candidates"]}
        self.assertIn("TOOLS_UNVERIFIED", rows["local"]["capability_codes"])          # not in the catalog
        self.assertIn("TOOLS_NOT_SUPPORTED", rows["cloud-b"]["capability_codes"])     # catalog says no
        self.assertIn("TOOLS_DECLARED", rows["cloud-a"]["notes"])
        self.assertEqual(out["recommended"], "cloud-a")
        self.assertTrue(out["differs_from_live_route"])
        self.assertEqual(out["live_route_would_choose"], "local")

    def test_owner_may_accept_unverified(self):
        out = ms.select(providers(), CATALOG, ms.requirements("t", lane="private", tools=True), allow_unverified=True)
        self.assertEqual(out["recommended"], "local")
        self.assertNotIn("cloud-b", out["chain"])                                    # a declared "no" still blocks

    def test_vision_and_context_come_from_the_catalog(self):
        req = ms.requirements(lane="private", vision=True, body={"messages": [{"role": "user", "content": "y" * 40000}]})
        out = ms.select(providers(), CATALOG, req)
        rows = {r["provider"]: r for r in out["candidates"]}
        self.assertIn("VISION_NOT_SUPPORTED", rows["cloud-a"]["capability_codes"])
        self.assertIn("CATALOG_CONTEXT_TOO_SMALL", rows["cloud-a"]["capability_codes"])
        self.assertEqual(out["recommended"], "cloud-b")
        self.assertIn("LIST_PRICE_NONZERO", rows["cloud-b"]["notes"])

    def test_image_in_body_is_detected(self):
        body = {"messages": [{"role": "user", "content": [{"type": "text", "text": "what is this"},
                                                            {"type": "image_url", "image_url": {"url": "data:,"}}]}]}
        self.assertTrue(ms.requirements(body=body)["vision"])

    def test_override_is_honoured_only_when_it_passes_every_rule(self):
        req = ms.requirements("t", lane="private")
        ok = ms.select(providers(), CATALOG, req, override="cloud-b")
        self.assertEqual((ok["recommended"], ok["override"]["honoured"]), ("cloud-b", True))
        bad = ms.select(providers(), CATALOG, req, override="public-x")
        self.assertFalse(bad["override"]["honoured"])
        self.assertIn("LANE_MISMATCH", bad["override"]["why"])
        self.assertEqual(bad["recommended"], "local")
        paid = ms.select(providers(), CATALOG, req, override="paid-y")
        self.assertIn("PAID_REFUSED", paid["override"]["why"])
        self.assertEqual(ms.select(providers(), CATALOG, req, override="nope")["override"]["why"], ["UNKNOWN_PROVIDER"])

    def test_nothing_fits_says_so(self):
        out = ms.select([P("cloud-b", "private", default_model="m-plain")], CATALOG,
                        ms.requirements("t", lane="private", tools=True))
        self.assertIsNone(out["recommended"]); self.assertIn("nothing would be called", out["note"])

    def test_measured_failures_move_a_provider_last(self):
        measured = {"local": {"calls": 1, "failed": 5, "failure_share": 0.833, "p50_ms": 100}}
        out = ms.select(providers(), CATALOG, ms.requirements("t", lane="private"), measured=measured)
        self.assertEqual(out["chain"][-1], "local")
        few = {"local": {"calls": 0, "failed": 2, "failure_share": 1.0, "p50_ms": None}}
        self.assertEqual(ms.select(providers(), CATALOG, ms.requirements("t"), measured=few)["chain"][0], "local")

    def test_lane_must_be_given_correctly(self):
        with self.assertRaises(ValueError):
            ms.requirements("t", lane="local")


class TestEvidence(NoNetwork):
    def test_reads_measured_tokens_and_failures_only(self):
        db = sqlite3.connect(":memory:")
        db.execute("CREATE TABLE audit(seq INTEGER PRIMARY KEY, ts REAL, kind TEXT, payload TEXT, prev TEXT, hash TEXT)")
        now = time.time()
        rows = [(now, "agent_model_call", {"provider": "groq", "ms": 400, "usage": {"prompt_tokens": 100, "completion_tokens": 20}}),
                (now, "agent_model_call", {"provider": "groq", "ms": 600, "usage": {}}),
                (now, "agent_model_call_failed", {"provider": "groq", "error": "HTTPError"}),
                (now - 30 * 86400, "agent_model_call", {"provider": "groq", "ms": 1, "usage": {"prompt_tokens": 9}}),
                (now, "agent_action", {"provider": "groq"})]
        db.executemany("INSERT INTO audit(ts, kind, payload) VALUES (?,?,?)", [(t, k, json.dumps(p)) for t, k, p in rows])
        e = ms.evidence(db, days=7, now=now)["groq"]
        self.assertEqual((e["calls"], e["failed"], e["with_usage"]), (2, 1, 1))
        self.assertEqual((e["prompt_tokens"], e["completion_tokens"]), (100, 20))      # the provider's own counts
        self.assertEqual(e["p50_ms"], 500)
        self.assertEqual(e["failure_share"], 0.333)


class TestShadow(NoNetwork):
    def test_replay_compares_without_changing_anything(self):
        db = sqlite3.connect(":memory:")
        db.execute("CREATE TABLE audit(seq INTEGER PRIMARY KEY, ts REAL, kind TEXT, payload TEXT, prev TEXT, hash TEXT)")
        now = time.time()
        calls = [{"provider": "local", "lane": "private", "tools": 0, "est_tokens": 100},        # same
                 {"provider": "local", "lane": "private", "tools": 3, "est_tokens": 100},        # needs tools: local unverified
                 {"provider": "cloud-b", "lane": "private", "tools": 0, "est_tokens": 100},      # selector prefers local
                 {"provider": "gone", "lane": "private"}, {"provider": "local"}]                 # skipped: unknown provider / no lane
        db.executemany("INSERT INTO audit(ts, kind, payload) VALUES (?,?,?)",
                       [(now, "agent_model_call", json.dumps(c)) for c in calls] + [(now, "agent_model_call_failed", json.dumps(calls[0]))])
        out = ms.shadow(db, providers(), CATALOG, 7, now=now)
        self.assertEqual((out["compared"], out["same"], out["skipped_no_lane_or_provider"]), (3, 1, 2))
        self.assertEqual(out["differences"]["local -> cloud-a"]["calls"], 1)
        self.assertIn("TOOLS_UNVERIFIED", out["differences"]["local -> cloud-a"]["served_provider_codes"])
        self.assertEqual(out["differences"]["cloud-b -> local"]["calls"], 1)
        self.assertFalse(out["applies"])
        self.assertEqual(db.execute("SELECT COUNT(*) FROM audit").fetchone()[0], 6)             # nothing written

    def test_empty_log(self):
        db = sqlite3.connect(":memory:")
        db.execute("CREATE TABLE audit(seq INTEGER PRIMARY KEY, ts REAL, kind TEXT, payload TEXT, prev TEXT, hash TEXT)")
        self.assertIsNone(ms.shadow(db, providers(), CATALOG)["agreement"])


class TestFallback(unittest.TestCase):
    def test_rules(self):
        F = ms.fallback_allowed
        self.assertTrue(F("model", "not_sent")[0]); self.assertTrue(F("model", "rejected")[0])
        self.assertFalse(F("model", "output_started")[0]); self.assertFalse(F("model", "unknown")[0])
        self.assertFalse(F("action", "rejected", "key-1")[0])
        self.assertFalse(F("action", "not_sent")[0])                      # no idempotency key: never repeated
        self.assertTrue(F("action", "not_sent", "key-1")[0])
        self.assertFalse(F("action", "unknown", "key-1")[0])
        self.assertFalse(F("action", "output_started", "key-1")[0])
        self.assertFalse(F("whatever", "not_sent")[0])


class TestRealConfig(NoNetwork):
    """The repo's own providers.json and catalog snapshot, with a temporary secrets folder (empty key files only)."""

    def setUp(self):
        super().setUp()
        self.tmp = tempfile.TemporaryDirectory()
        os.makedirs(os.path.join(self.tmp.name, "providers"))
        for f in ("groq.key", "openrouter.key"):
            open(os.path.join(self.tmp.name, "providers", f), "w").close()
        self.env = mock.patch.dict(os.environ, {"JARVIS_SECRETS": self.tmp.name})
        self.env.start()

    def tearDown(self):
        self.env.stop(); self.tmp.cleanup(); super().tearDown()

    def test_catalog_snapshot_is_declared_data_with_a_source(self):
        cat = ms.load_catalog(os.path.join(REPO_CONFIG, "model_catalog.json"))
        self.assertIn("models.dev", cat["source"]); self.assertEqual(len(cat["source_sha256"]), 64)
        self.assertIn("MIT", cat["licence"])

    def test_tool_task_on_real_config_and_files_unchanged(self):
        paths = [os.path.join(REPO_CONFIG, n) for n in ("providers.json", "model_catalog.json")]
        before = [read(p) for p in paths]
        from jarvis_kernel import gateway
        provs = gateway.load(paths[0])
        out = ms.select(provs, ms.load_catalog(paths[1]), ms.requirements("fix the bug", lane="private", tools=True))
        if os.name != "nt":
            self.assertEqual(out["recommended"], "groq")                    # declared tool calls, private lane
        rows = {r["provider"]: r for r in out["candidates"]}
        self.assertIn("TOOLS_UNVERIFIED", rows["vm-llama-cpu"]["capability_codes"] + rows["vm-llama-cpu"]["notes"])
        self.assertIn("NOT_IN_CATALOG", rows["openrouter-private"]["notes"])   # its default model is not listed today
        for r in out["candidates"]:
            if r["lane"] == "public":
                self.assertEqual(r["verdict"], "excluded")
        self.assertEqual([read(p) for p in paths], before)


class TestCatalogBuilder(NoNetwork):
    def test_keeps_only_reachable_models_and_records_the_source(self):
        import importlib.util
        path = os.path.join(REPO_CONFIG, "..", "scripts", "refresh_model_catalog.py")
        spec = importlib.util.spec_from_file_location("refresh_model_catalog", path)
        rmc = importlib.util.module_from_spec(spec); spec.loader.exec_module(rmc)
        raw = json.dumps({"groq": {"models": {"openai/gpt-oss-120b": {"tool_call": True, "secret_field": 1},
                                              "other/model": {"tool_call": True}}},
                          "openrouter": {"models": {"a:free": {"tool_call": False}, "b-paid": {}}}}).encode()
        cfg = {"providers": [{"name": "groq", "default_model": "openai/gpt-oss-120b", "model_pattern": "^openai/"},
                             {"name": "openrouter-private", "default_model": "gone:free", "model_pattern": ":free$"},
                             {"name": "dahl-glm", "default_model": "x"}]}
        doc = rmc.build(raw, cfg, "test-source", today="2026-10-20")
        self.assertEqual(sorted(doc["providers"]["groq"]["models"]), ["openai/gpt-oss-120b"])
        self.assertNotIn("secret_field", doc["providers"]["groq"]["models"]["openai/gpt-oss-120b"])
        self.assertEqual(sorted(doc["providers"]["openrouter-private"]["models"]), ["a:free"])
        self.assertFalse(doc["providers"]["openrouter-private"]["default_model_listed"])
        self.assertNotIn("dahl-glm", doc["providers"])                       # unlisted host: unknown, not borrowed
        self.assertEqual(len(doc["source_sha256"]), 64)
        with self.assertRaises(ValueError):
            rmc.fetch("http://models.dev/api.json")                           # https only


class TestCli(NoNetwork):
    def test_cli_prints_json_and_changes_nothing(self):
        import io
        from contextlib import redirect_stdout
        with tempfile.TemporaryDirectory() as d:
            prov = os.path.join(d, "p.json")
            with open(prov, "w") as f:
                json.dump({"providers": [{"name": "local", "lane": "local", "base_url": "http://127.0.0.1:1/v1",
                                          "key_file": None, "models": ["any"]}]}, f)
            cat = os.path.join(d, "missing.json")
            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = ms.main(["hello", "--providers", prov, "--catalog", cat])
            out = json.loads(buf.getvalue())
            self.assertEqual((rc, out["recommended"], out["applies"]), (0, "local", False))
            self.assertFalse(os.path.exists(cat))
            with redirect_stdout(io.StringIO()):
                self.assertEqual(ms.main(["hello", "--tools", "--providers", prov, "--catalog", cat]), 3)


if __name__ == "__main__":
    unittest.main()
