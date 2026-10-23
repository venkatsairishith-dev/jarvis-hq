"""screen_text.clean_result / cleaned: every string a plugin result carries is cleaned by default (re-review M12). Synthetic canaries
only. The gateway is the labelled FakeGateway of fakes.py (the real redactor is exercised elsewhere)."""
from __future__ import annotations

import asyncio
import json
import time

import pytest

from jarvis.plugins._shared import screen_text as st
from jarvis.plugins._shared.tests.fakes import ALL, C_EMAIL, C_FILE, C_KEY, C_OCR, C_PATH, C_STREET, FakeGateway


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.delenv(st.FLAG, raising=False)


def dump(v):
    return json.dumps(v, ensure_ascii=False)


def assert_clean(v, allowed=()):
    raw = dump(v)
    for canary in ALL:
        if canary not in allowed:
            assert canary not in raw, canary


def test_every_field_named_in_the_review_is_cleaned_and_a_brand_new_field_too():
    rec = {"status": "completed", "task": "t-1", "steps": [
        {"n": 1, "tool": "open_app", "agent": "desktop", "attempts": 1, "passed": True,
         "checked": f"the window for {C_PATH} appeared ({C_KEY}) for {C_OCR}", "uncertainty": f"maybe {C_STREET}",
         "uncertain": [f"{C_EMAIL} opened {C_FILE}"]}],
        "question": f"Which one: {C_PATH} or {C_FILE}, {C_EMAIL}?", "explain": f"File: {C_PATH}\nthen {C_OCR}",
        "approvals": [{"id": "a1", "tool": f"read {C_PATH}", "op": C_KEY, "target": C_FILE}],
        "brand_new_field_nobody_listed": f"{C_OCR} {C_PATH} {C_KEY} {C_EMAIL}",
        "nested": {"deep": [{"x": [f"{C_EMAIL}", {"y": C_PATH}]}]}}
    out = st.clean_result(rec)
    assert_clean(out)
    assert json.loads(dump(out)) == out                                              # JSON round-trip
    assert out["status"] == "completed" and out["task"] == "t-1" and out["steps"][0]["n"] == 1      # ids, states, counts stay raw
    assert out["steps"][0]["passed"] is True and out["steps"][0]["attempts"] == 1 and out["steps"][0]["tool"] == "open_app"
    assert "<a .xlsx file>" in out["question"] and "[email]" in out["question"]      # owner-facing text: readable, cleaned
    assert out["steps"][0]["checked"].startswith("[")                                # observed text: withheld (flag off), a note only
    assert out["brand_new_field_nobody_listed"].startswith("[")                      # an unlisted key is the strict case


def test_a_raw_key_with_text_in_it_is_still_cleaned():
    out = st.clean_result({"status": f"failed for {C_EMAIL}", "task": C_PATH, "id": "x" * 200, "command_id": "abc-123"})
    assert_clean(out)
    assert out["command_id"] == "abc-123"


def test_observed_text_goes_out_only_after_the_gateway_cleared_it_or_to_a_verified_local_model(monkeypatch):
    rec = {"steps": [{"n": 1, "checked": f"{C_OCR} lives at {C_STREET}; file {C_PATH}"}]}
    assert "stayed on this PC" in st.clean_result(rec)["steps"][0]["checked"]                  # flag off: withheld
    monkeypatch.setenv(st.FLAG, "1")
    g = FakeGateway()
    try:
        monkeypatch.setattr(st, "_gateway_base", lambda: f"http://127.0.0.1:{g.port}")
        out = st.clean_result(rec)
        assert_clean(out)
        assert "[PERSON_1]" in out["steps"][0]["checked"] and g.redact_requests                 # cleared by the gateway
        sent = json.dumps(g.redact_requests)
        assert C_PATH not in sent and C_FILE not in sent                                       # the path never reached even the gateway
    finally:
        g.close()
    monkeypatch.setattr(st, "_gateway_base", lambda: "http://127.0.0.1:1")                      # gateway down: withheld, not raw
    out = st.clean_result(rec)
    assert_clean(out) and out["steps"][0]["checked"].startswith("[screen text withheld")


def test_text_the_plugin_already_released_is_not_released_twice(monkeypatch):
    monkeypatch.setenv(st.FLAG, "1")
    g = FakeGateway()
    try:
        monkeypatch.setattr(st, "_gateway_base", lambda: f"http://127.0.0.1:{g.port}")
        out = st.clean_result({st.SCREEN_KEY: True, "text": "already cleared [PERSON_1] text"})
        assert out["text"] == "already cleared [PERSON_1] text" and g.redact_requests == []
    finally:
        g.close()


def test_a_large_deeply_nested_result_is_bounded_and_fast():
    deep: object = "leaf " + C_EMAIL
    for _ in range(60):
        deep = {"d": [deep, {"s": C_PATH}]}
    wide = {"rows": [{"a": f"{C_EMAIL} {i}", "b": [C_PATH] * 5, "n": i} for i in range(4000)]}
    t0 = time.monotonic()
    out1, out2 = st.clean_result(deep), st.clean_result(wide)
    assert time.monotonic() - t0 < 10
    assert_clean(out1) or True
    assert C_EMAIL not in dump(out1) and C_PATH not in dump(out1)
    assert C_EMAIL not in dump(out2) and C_PATH not in dump(out2)
    assert len(out2["rows"]) <= st.MAX_LIST and "(nested too deeply)" in dump(out1)


def test_odd_values_never_raise_and_never_pass_through_raw():
    class Weird:
        def __str__(self):
            return f"weird {C_EMAIL}"

    out = st.clean_result({"a": b"bytes " + C_PATH.encode(), "b": Weird(), "c": {1, 2}, "d": float("nan"), 5: C_KEY, "e": (f"met {C_OCR} today",)})
    assert_clean(out)


def test_the_decorator_cleans_ok_and_err_and_keeps_the_error_code():
    from jarvis.plugins.main_agent.tests import test_plugin  # noqa: F401  (only to make sure the SDK imports in this process)
    from jarvis.pluginapi import Err, Ok, SdkError

    @st.cleaned
    async def good(self=None):
        return Ok({"count": 2, "note": f"{C_PATH} {C_EMAIL}"})

    @st.cleaned
    async def bad(self=None):
        return Err(SdkError(f"could not read {C_PATH} with {C_KEY}", code="E_X"))

    ok = asyncio.run(good())
    assert isinstance(ok, Ok) and ok.value["count"] == 2 and C_PATH not in dump(ok.value) and C_EMAIL not in dump(ok.value)
    err = asyncio.run(bad())
    assert isinstance(err, Err) and C_PATH not in str(err.error) and C_KEY not in str(err.error)
    assert getattr(err.error, "code", None) == "E_X"


def test_a_lone_identifier_passes_but_a_lone_name_or_file_name_does_not():
    out = st.clean_result({"a": "ap1", "b": "20261017-101500-abcdef", "c": "observed", "d": "RED", "e": "Priyanka", "f": C_FILE,
                           "g": "Priyanka Rao", "h": "1.2.3", "i": C_KEY, "j": "/etc/passwd"})
    assert out["a"] == "ap1" and out["b"] == "20261017-101500-abcdef" and out["c"] == "observed" and out["d"] == "RED" and out["h"] == "1.2.3"
    for k in ("e", "f", "g", "i", "j"):
        assert out[k].startswith("["), (k, out[k])           # a note, never the text
