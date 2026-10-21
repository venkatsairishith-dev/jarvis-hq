"""MEM-M11: a question is never searched unredacted because the privacy gateway is down (fail closed)."""
import asyncio
import json

import pytest

from plugin.plugins import research as r
from plugin.sdk.plugin import Err

PRIVATE_Q = "My mother Anita Rao has chest pain at 12 Elm Street, which clinic?"


class FakePlugins:
    def __init__(self):
        self.searched = []

    async def call_entry_json(self, entry, args, timeout=0):
        self.searched.append((entry, args))

        class Res:
            value = {"results": [{"url": "https://example.com/a", "title": "t", "snippet": "s"}]}
        return Res()


def run_entry(question=PRIVATE_Q):
    p = r.ResearchPlugin.__new__(r.ResearchPlugin)
    p.plugins = FakePlugins()
    fn = r.ResearchPlugin.research_topic
    fn = getattr(fn, "__wrapped__", fn)
    return asyncio.run(fn(p, question=question, depth="quick")), p.plugins.searched


def test_gateway_down_means_nothing_is_searched(monkeypatch):
    monkeypatch.setattr(r, "_gateway_up", lambda: False)
    out, searched = run_entry()
    assert isinstance(out, Err) and "privacy gateway" in str(out.error) and "nothing was searched" in str(out.error)
    assert searched == []                                                  # the question never left, in any form
    assert "Anita" not in str(out.error) and "Elm" not in str(out.error)   # and the refusal does not repeat it


def test_a_gateway_probe_that_raises_is_treated_as_down(monkeypatch):
    def boom():
        raise OSError("refused")
    monkeypatch.setattr(r, "_gateway_up", boom)
    out, searched = run_entry()
    assert isinstance(out, Err) and searched == []


def test_gateway_up_but_redact_call_fails_still_searches_nothing(monkeypatch):
    monkeypatch.setattr(r, "_gateway_up", lambda: True)

    def fail(req, timeout=0):
        raise OSError("connection reset")
    monkeypatch.setattr(r.loopback_http, "urlopen", fail)             # the loopback seam (re-review M13)
    out, searched = run_entry()
    assert isinstance(out, Err) and "could not check the question" in str(out.error) and searched == []
    assert "Anita" not in str(out.error)


def test_the_explicit_offline_path_still_works():
    redact, note = None, None
    import unittest.mock as m
    with m.patch.object(r, "_gateway_up", lambda: False):
        redact, note = r._redactor(allow_unredacted=True)
        with pytest.raises(r.ResearchError, match="nothing was searched"):
            r._redactor()
    assert redact is None and "explicitly allowed" in note
    with m.patch.object(r, "_gateway_up", lambda: True):
        assert r._redactor() == (r._redact, None)


def test_a_caller_of_the_tool_cannot_switch_the_check_off(monkeypatch):
    """No tool argument turns it off: the entry has no such parameter and ignores unknown ones."""
    import inspect
    assert "allow_unredacted" not in inspect.signature(getattr(r.ResearchPlugin.research_topic, "__wrapped__", r.ResearchPlugin.research_topic)).parameters
    assert "allow_unredacted" not in json.dumps(r.ResearchPlugin.research_topic.__dict__, default=str)
    monkeypatch.setattr(r, "_gateway_up", lambda: False)
    p = r.ResearchPlugin.__new__(r.ResearchPlugin)
    p.plugins = FakePlugins()
    fn = getattr(r.ResearchPlugin.research_topic, "__wrapped__", r.ResearchPlugin.research_topic)
    out = asyncio.run(fn(p, question=PRIVATE_Q, depth="quick", allow_unredacted=True))
    assert isinstance(out, Err) and p.plugins.searched == []


# ---------- audit M5 (2026-10-18): with the gateway UP, the question is searched only as the gateway cleared it ----------
#
# FakeGateway below is a LABELLED FAKE of the gateway's HTTP surface (/health, /redact), not Presidio: it swaps the made-up
# canary names for placeholders so the plugin's plumbing can be checked at the HTTP edge. The real redactor is exercised in
# RealGateway (needs Presidio + spaCy; skipped with a reason otherwise) and in jarvis/privacy/test_privacy.py.
import threading  # noqa: E402
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer  # noqa: E402

C_NAME, C_PLACE = "CANARY-7f3a-Priyanka-SYNTH", "CANARY-7f3a-Elm-Street-SYNTH"
CANARY_Q = f"My mother {C_NAME} has chest pain at {C_PLACE}, which clinic?"
READY = {"ok": True, "ready": True, "degraded": [], "pii_model": True, "names": False, "public_egress": True}


class FakeGateway:
    def __init__(self, health=None, redact_extra=None, raw_health=None):
        self.health, self.raw_health, self.redact_requests = health if health is not None else dict(READY), raw_health, []
        self.redact_extra = redact_extra if redact_extra is not None else {"names": True, "deep": True, "ready": True}
        outer = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _send(self, code, raw):
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def do_GET(self):
                self._send(200, outer.raw_health if outer.raw_health is not None else json.dumps(outer.health).encode())

            def do_POST(self):
                req = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                outer.redact_requests.append(req)
                text = req["text"].replace(C_NAME, "[PERSON_1]").replace(C_PLACE, "[LOCATION_1]")
                self._send(200, json.dumps({"text": text, "kinds": {}, **outer.redact_extra}).encode())
        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.base = f"http://127.0.0.1:{self.srv.server_address[1]}"

    def close(self):
        self.srv.shutdown()
        self.srv.server_close()


@pytest.fixture
def fake_gateway(monkeypatch):
    made = []

    def make(**kw):
        g = FakeGateway(**kw)
        made.append(g)
        monkeypatch.setattr(r, "GATEWAY_HEALTH", g.base + "/health")
        monkeypatch.setattr(r, "GATEWAY_REDACT", g.base + "/redact")
        return g
    yield make
    for g in made:
        g.close()


def test_a_ready_gateway_means_the_question_is_searched_only_as_the_gateway_cleared_it(monkeypatch, fake_gateway):
    g = fake_gateway()
    monkeypatch.setattr(r, "_plan", lambda s, u: json.dumps({"queries": ["which clinic for chest pain"]}))
    out, searched = run_entry(CANARY_Q)                # later steps fail in this sandbox; only what was searched matters here
    assert g.redact_requests and g.redact_requests[0]["names"] is True       # the plugin asks for names and places to be hidden
    assert searched and all("CANARY" not in json.dumps(a) for _, a in searched), searched


@pytest.mark.parametrize("health", [
    {"ok": True, "ready": False, "degraded": ["pii_model_missing"], "pii_model": False},     # gateway up, PII model missing
    {"ok": True, "pii_model": True},                                                         # an old gateway: no ready flag
    {"ok": True, "ready": True, "pii_model": False},                                         # ready but no model
    {"ok": False, "ready": True, "pii_model": True},
    ["not", "a", "gateway"], "ok"])
def test_a_gateway_that_answers_200_but_is_not_ready_is_refused_and_nothing_is_searched(monkeypatch, fake_gateway, health):
    g = fake_gateway(health=health)
    out, searched = run_entry(CANARY_Q)
    assert isinstance(out, Err) and searched == [] and g.redact_requests == []
    assert "cannot hide names and places" in str(out.error) and "CANARY" not in str(out.error)
    assert r._gateway_up() is False


def test_a_200_that_is_not_json_is_not_a_gateway(monkeypatch, fake_gateway):
    fake_gateway(raw_health=b"<html>ok</html>")
    out, searched = run_entry(CANARY_Q)
    assert isinstance(out, Err) and searched == []


@pytest.mark.parametrize("extra", [{"deep": True, "ready": True}, {"names": False, "deep": True}, {"names": True, "deep": False},
                                    {"names": "yes", "deep": True}, {}])
def test_a_redact_answer_that_does_not_confirm_names_and_the_model_is_refused(monkeypatch, fake_gateway, extra):
    g = fake_gateway(redact_extra=extra)
    out, searched = run_entry(CANARY_Q)
    assert isinstance(out, Err) and searched == [] and len(g.redact_requests) == 1
    assert "CANARY" not in str(out.error)


def test_the_writer_gets_the_question_as_it_was_cleared_never_as_typed():
    from plugin.plugins.research import core
    prompts = []

    def cleared(q):
        return q.replace(C_NAME, "[PERSON_1]").replace(C_PLACE, "[LOCATION_1]")

    def search(q, n):
        assert "CANARY" not in q
        return [{"url": "https://example.com/a", "title": "t", "snippet": "s"}]

    def fetch(url):
        return {"url": url, "title": "Clinic", "text": "Chest pain needs urgent care. " * 20, "date": "2026-01-01"}

    def write(system, user):
        prompts.append(user)
        raise RuntimeError("stop after the prompt")
    try:
        core.run(CANARY_Q, depth="quick", search=search, fetch=fetch, plan_llm=None, write_llm=write, verifier=None, redact=cleared)
    except Exception:  # noqa: BLE001 - only the prompt that reached the writer matters
        pass
    assert prompts and "CANARY" not in prompts[0]


def _real_privacy():
    pytest.importorskip("presidio_analyzer", reason="needs Presidio + spaCy en_core_web_sm; run with PYTHONPATH=<dir with them> "
                                                      "(see jarvis/privacy/test_privacy.py)")
    pytest.importorskip("spacy")
    import importlib
    import sys
    from pathlib import Path
    pdir = str(Path(__file__).resolve().parents[4] / "jarvis" / "privacy")
    sys.path.insert(0, pdir)
    try:
        red, gw = importlib.import_module("redactor"), importlib.import_module("gateway")
    finally:
        sys.path.remove(pdir)
        sys.modules.pop("redactor", None)
        sys.modules.pop("gateway", None)
    return gw, red


class NoEntityModel:
    """LABELLED STUB of the GLiNER-PII model (not installable here): finds nothing, so only real Presidio + spaCy work."""

    def predict_entities(self, text, labels, threshold=0.3):
        return []


def real_gateway(monkeypatch, model):
    gw, red = _real_privacy()
    try:
        redactor = red.Redactor(names=False, model=model, verifier=None)
    except OSError:
        pytest.skip("spaCy model en_core_web_sm is not installed")
    handler = gw.make_handler(redactor, "http://127.0.0.1:1/v1", 5, require_deep=True)
    handler.log_message = lambda *a, **k: None
    srv = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_address[1]}"
    monkeypatch.setattr(r, "GATEWAY_HEALTH", base + "/health")
    monkeypatch.setattr(r, "GATEWAY_REDACT", base + "/redact")
    return srv


class TestRealGateway:
    """The REAL gateway code and the REAL Presidio + spaCy name recogniser behind the plugin's HTTP calls (no fake gateway).
    Only the GLiNER model is a labelled stub. Run: PYTHONPATH=<presidio+spacy dir> python -m pytest this file."""

    def test_names_and_places_are_hidden_by_the_real_redactor_without_JARVIS_PRIVACY_NAMES(self, monkeypatch):
        monkeypatch.delenv("JARVIS_PRIVACY_NAMES", raising=False)
        srv = real_gateway(monkeypatch, NoEntityModel())
        try:
            assert r._gateway_up() is True
            out = r._redact("John Smith flew to Paris with a fever")
            assert "John Smith" not in out and "Paris" not in out and "fever" in out
        finally:
            srv.shutdown()
            srv.server_close()

    def test_a_real_gateway_without_its_pii_model_is_refused(self, monkeypatch):
        srv = real_gateway(monkeypatch, None)
        try:
            assert r._gateway_up() is False
            out, searched = run_entry("John Smith flew to Paris")
            assert isinstance(out, Err) and searched == [] and "pii_model_missing" in str(out.error)
        finally:
            srv.shutdown()
            srv.server_close()
