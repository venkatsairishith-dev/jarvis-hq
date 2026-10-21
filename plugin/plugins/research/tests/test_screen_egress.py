"""Research never takes screen, OCR or file text, and anything a caller puts into its question is cleared by the privacy gateway
before one search, fetch or writer call (audit 2026-10-18, Low: OCR / file-name egress). SYNTHETIC canaries only."""
import asyncio
import json

import pytest

from plugin.plugins import research as r
from plugin.plugins._shared.tests import fakes
from plugin.plugins._shared.tests.fakes import C_EMAIL, C_KEY, C_OCR, C_PATH, C_STREET, leaked
from plugin.plugins.research.tests.test_gateway_required import FakePlugins
from plugin.sdk.plugin import Err

QUESTION = f"What does this mean: 'Dear {C_OCR}, your file {C_PATH} at {C_STREET}, write to {C_EMAIL}, key {C_KEY}'?"


@pytest.fixture
def gateway(monkeypatch):
    made = []

    def make(**kw):
        g = fakes.FakeGateway(**kw)
        made.append(g)
        monkeypatch.setattr(r, "GATEWAY_HEALTH", f"http://127.0.0.1:{g.port}/health")
        monkeypatch.setattr(r, "GATEWAY_REDACT", f"http://127.0.0.1:{g.port}/redact")
        return g
    yield make
    for g in made:
        g.close()


def run_entry(question):
    p = r.ResearchPlugin.__new__(r.ResearchPlugin)
    p.plugins = FakePlugins()
    fn = getattr(r.ResearchPlugin.research_topic, "__wrapped__", r.ResearchPlugin.research_topic)
    return asyncio.run(fn(p, question=question, depth="quick")), p.plugins.searched


def test_research_has_no_input_for_screen_text_only_the_question_and_options():
    meta = r.ResearchPlugin.research_topic.__neko_event_meta__
    assert set(meta.input_schema["properties"]) == {"question", "depth", "deliverable", "open_after"}


def test_a_question_that_holds_screen_text_leaves_for_search_only_as_the_gateway_cleared_it(monkeypatch, gateway):
    g = gateway()
    monkeypatch.setattr(r, "_plan", lambda s, u: json.dumps({"queries": ["what does this letter mean"]}))
    out, searched = run_entry(QUESTION)                  # later steps fail in this sandbox; only what was searched matters here
    assert g.redact_requests and g.redact_requests[0]["names"] is True
    assert searched and all(leaked(a) == [] for _, a in searched), searched


@pytest.mark.parametrize("health", [None, {"ok": True, "ready": False, "degraded": ["pii_model_missing"], "pii_model": False}])
def test_gateway_down_or_degraded_nothing_is_searched_and_the_error_holds_none_of_it(monkeypatch, gateway, health):
    if health is None:
        monkeypatch.setattr(r, "GATEWAY_HEALTH", f"http://127.0.0.1:{fakes.free_port()}/health")
    else:
        gateway(health=health)
    out, searched = run_entry(QUESTION)
    assert isinstance(out, Err) and searched == [] and leaked(str(out.error)) == []
