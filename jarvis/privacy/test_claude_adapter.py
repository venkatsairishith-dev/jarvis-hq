"""Claude planner adapter (owner exception, expires 2026-10-31): pure-function tests plus the gateway chain. No network, no real key.

    python -m pytest jarvis/privacy/test_claude_adapter.py -q -p no:cacheprovider
The gateway tests use the labelled fake detector from test_gateway_plumbing.py, so they prove routing, redaction-before-send,
restore-after, fallback to Dahl and the expiry/cap rules, not Presidio's detection (that is test_privacy.py).
"""
import datetime as dt
import json
import os
import sys
import threading
from http.server import ThreadingHTTPServer

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import test_gateway_plumbing as tgp  # noqa: E402

import claude_adapter as ca  # noqa: E402

gateway = tgp.gateway
PROV = {"name": "claude", "api": "anthropic", "preferred": True, "key_file": "claude.key", "model": "claude-sonnet-5-5",
        "max_tokens_cap": 1000, "expires": "2026-10-31", "base_url": "https://api.anthropic.com"}
DAHL_BASE = None


def body(**kw):
    b = {"model": "x", "messages": [{"role": "system", "content": "be brief"}, {"role": "user", "content": "plan this"}]}
    b.update(kw)
    return b


class FakeMsg:
    def __init__(self, text="the plan", stop="end_turn"):
        self.id, self.stop_reason = "msg_1", stop
        self.content = [type("B", (), {"type": "thinking", "text": None})(), type("B", (), {"type": "text", "text": text})()]
        self.usage = type("U", (), {"input_tokens": 11, "output_tokens": 7})()


class FakeClient:
    def __init__(self, msg=None, boom=None):
        self.calls, self.msg, self.boom = [], msg or FakeMsg(), boom
        self.messages = self

    def create(self, **kw):
        self.calls.append(kw)
        if self.boom:
            raise self.boom
        return self.msg


# ---- pure adapter ----
def test_expiry_boundary_and_bad_date():
    assert not ca.expired(PROV, dt.date(2026, 10, 31))
    assert ca.expired(PROV, dt.date(2026, 11, 1))
    assert ca.expired({"expires": "not a date"}) and not ca.expired({})


def test_call_is_refused_after_expiry_without_touching_the_client():
    c = FakeClient()
    old = ca.today_utc
    ca.today_utc = lambda: dt.date(2026, 11, 1)
    try:
        with pytest.raises(ca.ClaudeUnavailable, match="expired"):
            ca.call(PROV, "k", body(), client=c)
    finally:
        ca.today_utc = old
    assert c.calls == []


@pytest.mark.parametrize("extra", [{"stream": True}, {"tools": [{"type": "function"}]}, {"response_format": {"type": "json_object"}},
                                   {"n": 2}, {"tool_choice": "auto"}])
def test_unsupported_shapes_are_not_sent(extra):
    assert ca.unsupported(body(**extra))
    with pytest.raises(ca.ClaudeUnavailable):
        ca.call(PROV, "k", body(**extra), client=FakeClient())


def test_images_and_tool_roles_are_unsupported_but_text_parts_are_fine():
    assert ca.unsupported(body(messages=[{"role": "user", "content": [{"type": "image_url", "image_url": {"url": "x"}}]}]))
    assert ca.unsupported(body(messages=[{"role": "tool", "content": "x"}]))
    assert ca.unsupported(body(messages=[{"role": "user", "content": [{"type": "text", "text": "hi"}]}])) is None


def test_request_shape_drops_sampling_applies_cap_and_merges_turns():
    r = ca.build_request(body(temperature=0.2, top_p=0.9, max_tokens=99999, messages=[
        {"role": "system", "content": "s1"}, {"role": "user", "content": "a"}, {"role": "user", "content": "b"},
        {"role": "assistant", "content": "c"}]), PROV)
    assert r["model"] == "claude-sonnet-5-5" and r["max_tokens"] == 1000 and r["system"] == "s1"
    assert "temperature" not in r and "top_p" not in r
    assert r["messages"] == [{"role": "user", "content": "a\n\nb"}, {"role": "assistant", "content": "c"}]
    assert ca.build_request(body(max_tokens=50), PROV)["max_tokens"] == 50
    assert ca.build_request(body(), PROV)["max_tokens"] == 1000


def test_answer_is_openai_shaped_with_usage_and_ignores_thinking_blocks():
    out = ca.call(PROV, "k", body(), client=FakeClient(FakeMsg("hello")))
    assert out["choices"][0]["message"] == {"role": "assistant", "content": "hello"}
    assert out["choices"][0]["finish_reason"] == "stop" and out["usage"]["total_tokens"] == 18
    assert ca.call(PROV, "k", body(), client=FakeClient(FakeMsg("cut", "max_tokens")))["choices"][0]["finish_reason"] == "length"


def test_refusal_and_sdk_errors_raise_without_leaking_text():
    with pytest.raises(ca.ClaudeUnavailable, match="declined"):
        ca.call(PROV, "k", body(), client=FakeClient(FakeMsg("", "refusal")))
    with pytest.raises(ca.ClaudeUnavailable) as e:
        ca.call(PROV, "sk-SECRET-TEXT", body(), client=FakeClient(boom=RuntimeError("bad key sk-SECRET-TEXT")))
    assert "SECRET" not in str(e.value) and "RuntimeError" in str(e.value)


# ---- gateway chain ----
class Env:
    """A gateway with [claude (preferred), dahl-like upstream recorder], Claude's call and key faked."""

    def __init__(self, monkeypatch, claude_result):
        self.claude_calls = []
        tgp.Recorder.seen = []
        gateway._cooldown.clear()                    # module-level state; tests may run in any order
        up = ThreadingHTTPServer(("127.0.0.1", 0), tgp.Recorder)
        threading.Thread(target=up.serve_forever, daemon=True).start()
        provs = [dict(PROV), {"name": "dahl", "base_url": f"http://127.0.0.1:{up.server_address[1]}/v1", "key": "from_request"}]
        provs.sort(key=lambda p: 0 if p.get("preferred") else 1)

        def fake_call(prov, key, b, timeout=0, client=None):
            self.claude_calls.append(json.dumps(b))
            if isinstance(claude_result, Exception):
                raise claude_result
            return claude_result

        monkeypatch.setattr(gateway.claude_adapter, "call", fake_call)
        monkeypatch.setattr(gateway, "read_key", lambda p: "test-key")
        handler = gateway.make_handler(tgp.LabelledFakeDetector(), provs, 10)
        handler.log_message = lambda *a, **k: None
        gw = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        gw.daemon_threads = True
        threading.Thread(target=gw.serve_forever, daemon=True).start()
        self.up, self.gw, self.base = up, gw, f"http://127.0.0.1:{gw.server_address[1]}"

    def close(self):
        tgp.stop(self.up, self.gw)


CHAT = {"model": "x", "messages": [{"role": "user", "content": "plan for CANARY-7f3a-name-SYNTH"}]}


def answer(text):
    return {"id": "c", "object": "chat.completion", "model": "claude-sonnet-5-5",
            "choices": [{"index": 0, "message": {"role": "assistant", "content": text}, "finish_reason": "stop"}], "usage": {}}


def test_claude_is_asked_first_with_redacted_text_and_the_reply_is_restored(monkeypatch):
    e = Env(monkeypatch, answer("done for [CANARY_1]"))
    try:
        status, out = tgp.post(e.base, "/chat/completions", CHAT)
        assert status == 200
        assert "CANARY-7f3a" not in e.claude_calls[0]                    # redacted before it reached the adapter
        assert "CANARY-7f3a-name-SYNTH" in out["choices"][0]["message"]["content"]       # placeholder put back for the caller
        assert tgp.Recorder.seen == []                                    # Dahl was not needed
    finally:
        e.close()


def test_when_claude_is_unavailable_dahl_answers_and_never_sees_the_canary(monkeypatch):
    e = Env(monkeypatch, ca.ClaudeUnavailable("owner exception expired"))
    try:
        status, out = tgp.post(e.base, "/chat/completions", CHAT)
        assert status == 200 and out["choices"][0]["message"]["content"] == "ok"
        assert len(tgp.Recorder.seen) == 1 and "CANARY-7f3a" not in tgp.Recorder.seen[0]
    finally:
        e.close()


def test_non_chat_calls_never_go_to_claude(monkeypatch):
    e = Env(monkeypatch, answer("x"))
    try:
        tgp.post(e.base, "/embeddings", {"model": "x", "input": "hi"})
        assert e.claude_calls == []
    finally:
        e.close()


def test_load_providers_drops_an_expired_claude_entry_and_orders_it_first(tmp_path, monkeypatch):
    cfg = {"providers": [{"name": "dahl", "base_url": "https://d/v1", "enabled": True, "key": "from_request"},
                         {"name": "claude", "api": "anthropic", "preferred": True, "enabled": True, "base_url": "https://api.anthropic.com",
                          "key_file": "claude.key", "model": "claude-sonnet-5-5", "expires": "2026-10-31"}]}
    p = tmp_path / "providers.json"
    p.write_text(json.dumps(cfg), encoding="utf-8")
    monkeypatch.setattr(ca, "today_utc", lambda: dt.date(2026, 10, 30))
    provs, _ = gateway.load_providers(str(p))
    assert [x["name"] for x in provs] == ["claude", "dahl"]
    monkeypatch.setattr(ca, "today_utc", lambda: dt.date(2026, 11, 1))
    provs, _ = gateway.load_providers(str(p))
    assert [x["name"] for x in provs] == ["dahl"]


def test_real_providers_file_keeps_dahl_and_asks_claude_first(monkeypatch):
    """Regression: an anthropic entry placed before the caller's-key provider in the file once dropped Dahl from the chain."""
    monkeypatch.setattr(ca, "today_utc", lambda: dt.date(2026, 10, 22))
    provs, _ = gateway.load_providers(os.path.join(HERE, "providers.json"))
    names = [p["name"] for p in provs]
    assert names[:2] == ["claude", "dahl"], names
    assert next(p for p in provs if p["name"] == "dahl").get("key") == "from_request"
    monkeypatch.setattr(ca, "today_utc", lambda: dt.date(2026, 11, 1))
    provs, _ = gateway.load_providers(os.path.join(HERE, "providers.json"))
    assert "claude" not in [p["name"] for p in provs] and provs[0]["name"] == "dahl"
