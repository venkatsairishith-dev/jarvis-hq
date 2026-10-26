"""D60: gateway speed work. A per-message redaction cache that keeps placeholders correct, the p90 and phase numbers in /health and in the
content-free timing log, and the optional tool-turn token cap. A LABELLED FAKE detector again (see test_gateway_plumbing.py): this proves the
cache and the plumbing, not that Presidio finds a value."""
import json
import os
import threading
import urllib.request

import pytest

import test_gateway_plumbing as P

gateway, Mapping = P.gateway, P.Mapping
c = P.c


class CountingDetector(P.LabelledFakeDetector):
    """The fake detector, plus a count per text so 'was it scanned again' is a number."""

    def __init__(self):
        super().__init__()
        self.calls = {}

    def redact(self, text, mapping, deep=True, names=None):
        self.calls[text] = self.calls.get(text, 0) + 1
        return super().redact(text, mapping, deep, names)


def body(*messages, tools=None):
    out = {"model": "m", "messages": list(messages)}
    if tools:
        out["tools"] = tools
    return out


def msg(role, text):
    return {"role": role, "content": text}


def test_an_unchanged_message_is_not_scanned_again_and_its_result_is_the_same():
    det = CountingDetector()
    cached = gateway.CachedRedaction(det)
    history = [msg("system", f"memory {c('a')}"), msg("user", f"my token is {c('b')}"), msg("assistant", "ok done")]
    first = gateway.redact_body(body(*history), cached, Mapping())
    second = gateway.redact_body(body(*history), cached, Mapping())
    assert first == second
    assert "CANARY-7f3a" not in json.dumps(second)
    assert all(n == 1 for n in det.calls.values()), det.calls            # each text reached the detector exactly once
    snap = cached.snapshot()
    assert snap["hits"] == 3 and snap["misses"] == 3


def test_placeholders_follow_this_requests_mapping_not_the_one_that_filled_the_cache():
    det = CountingDetector()
    cached = gateway.CachedRedaction(det)
    # request 1 meets A first; request 2 meets B first and A second: A must be [CANARY_2] there, and must restore to A
    a, b = c("A"), c("B")
    m1 = Mapping()
    out1 = gateway.redact_body(body(msg("user", f"first {a}")), cached, m1)
    assert out1["messages"][0]["content"] == "first [CANARY_1]"
    m2 = Mapping()
    out2 = gateway.redact_body(body(msg("user", f"new {b}"), msg("user", f"first {a}"), msg("user", f"again {a} and {b}")), cached, m2)
    texts = [m["content"] for m in out2["messages"]]
    assert texts == ["new [CANARY_1]", "first [CANARY_2]", "again [CANARY_2] and [CANARY_1]"]
    assert m2.to_value == {"[CANARY_1]": b, "[CANARY_2]": a}
    # the same answer without any cache
    plain = gateway.redact_body(body(msg("user", f"new {b}"), msg("user", f"first {a}"), msg("user", f"again {a} and {b}")), P.LabelledFakeDetector(), Mapping())
    assert out2 == plain
    # restoring a reply works through the request's own mapping
    assert gateway.restore_json({"x": "reply [CANARY_2] [CANARY_1]"}, m2) == {"x": f"reply {a} {b}"}


def test_a_changed_message_is_scanned_again_and_the_unchanged_ones_are_not():
    det = CountingDetector()
    cached = gateway.CachedRedaction(det)
    old = [msg("user", f"one {c('1')}"), msg("assistant", "reply one")]
    gateway.redact_body(body(*old), cached, Mapping())
    changed = [msg("user", f"one {c('1')} edited {c('2')}"), msg("assistant", "reply one")]
    out = gateway.redact_body(body(*changed), cached, Mapping())
    assert "CANARY-7f3a" not in json.dumps(out)
    assert det.calls[f"one {c('1')} edited {c('2')}"] == 1             # new text: scanned
    assert det.calls["reply one"] == 1                                 # unchanged: from the cache
    assert det.calls[f"one {c('1')}"] == 1                             # the old text is not scanned a second time either


def test_deep_and_names_are_part_of_the_key():
    det = CountingDetector()
    cached = gateway.CachedRedaction(det)
    m = Mapping()
    cached.redact("hello world", m, True, None)
    cached.redact("hello world", m, False, None)
    cached.redact("hello world", m, True, True)
    assert det.calls["hello world"] == 3


def test_a_text_that_already_looks_like_a_placeholder_is_never_cached():
    det = CountingDetector()
    cached = gateway.CachedRedaction(det)
    tricky = f"I typed [CANARY_1] myself and {c('x')}"
    for _ in range(2):
        out = cached.redact(tricky, Mapping())
    assert det.calls[tricky] == 2, "scanned both times"
    assert out == "I typed [CANARY_1] myself and [CANARY_1]"            # the detector's own result, untouched by the cache layer
    assert cached.snapshot()["uncacheable"] == 2


def test_nothing_is_remembered_while_the_checker_was_down():
    class Flaky(CountingDetector):
        def __init__(self):
            super().__init__()
            self.stats = {"jev_unreachable": 0}

        def redact(self, text, mapping, deep=True, names=None):
            self.stats["jev_unreachable"] += 1                         # every call happens while the decision model is unreachable
            return super().redact(text, mapping, deep, names)
    det = Flaky()
    cached = gateway.CachedRedaction(det)
    for _ in range(2):
        cached.redact(f"secret {c('q')}", Mapping())
    assert det.calls[f"secret {c('q')}"] == 2 and cached.snapshot()["size"] == 0


def test_the_cache_is_bounded():
    cached = gateway.CachedRedaction(CountingDetector(), max_entries=8)
    for i in range(40):
        cached.redact(f"message number {i} {c(str(i))}", Mapping())
    assert cached.snapshot()["size"] == 8


def test_a_detector_error_is_not_swallowed_or_cached():
    class Boom(CountingDetector):
        def redact(self, text, mapping, deep=True, names=None):
            raise RuntimeError("boom")
    cached = gateway.CachedRedaction(Boom())
    with pytest.raises(RuntimeError):
        cached.redact("anything", Mapping())
    assert cached.snapshot()["size"] == 0


def test_the_wrapper_passes_the_detectors_attributes_through():
    det = CountingDetector()
    cached = gateway.CachedRedaction(det)
    assert cached.model is det.model and cached.names is False
    assert gateway.degraded_reasons(cached, True) == []


def test_requests_of_a_chat_share_an_identical_byte_prefix_so_a_provider_can_cache_it():
    """The same system prompt and tool list, then a different last message: the forwarded body is identical up to that message."""
    tools = [{"type": "function", "function": {"name": "desktop__do_goal", "description": "Do a goal", "parameters": {"type": "object", "properties": {"goal": {"type": "string"}}}}}]
    cached = gateway.CachedRedaction(CountingDetector())
    system = msg("system", "You are JARVIS. Static persona text.")
    a = json.dumps(gateway.redact_body({"model": "m", "tools": tools, "messages": [system, msg("user", f"open notepad {c('1')}")]}, cached, Mapping()))
    b = json.dumps(gateway.redact_body({"model": "m", "tools": tools, "messages": [system, msg("user", "what is on my screen")]}, cached, Mapping()))
    cut = a.index('"role": "user"')
    assert a[:cut] == b[:cut]


# ---------------------------------------------------------------- the HTTP edge: /health numbers, the timing log, the token cap
def serve(det, providers=None, up_handler=None):
    up, gw, base = P.start_gateway(det)
    return up, gw, base


def test_health_shows_median_p90_phases_and_the_cache(tmp_path, monkeypatch):
    log = tmp_path / "latency.jsonl"
    monkeypatch.setenv("JARVIS_LATENCY_LOG", str(log))
    det = CountingDetector()
    up, gw, base = serve(det)
    try:
        for i in range(4):
            code, _ = P.post(base, "/v1/chat/completions", body(msg("system", "static"), msg("user", f"hello {i}")))
            assert code == 200
        # the plain-upstream handler has no named provider rows until the provider name is known; the cache numbers are always there
        health = json.loads(urllib.request.urlopen(base + "/health", timeout=5).read())
        assert health["redaction_cache"]["hits"] >= 3                  # "static" came from the cache three times
        assert "latency_p90_ms" in health and "latency_calls" in health
        for name, row in health["latency_ms"].items():
            assert set(row) == {"redact_ms", "queue_ms", "first_ms", "total_ms"}
            assert set(health["latency_p90_ms"][name]) == set(row)
    finally:
        P.stop(up, gw)
    lines = [json.loads(x) for x in log.read_text(encoding="utf-8").splitlines()]
    assert len(lines) == 4
    for line in lines:
        assert line["src"] == "gateway" and {"redact_ms", "first_ms", "total_ms", "req_bytes", "tools", "msgs", "after"} <= set(line)
        text = json.dumps(line)
        assert "hello" not in text and "static" not in text, "the timing line holds no message text"
    assert lines[-1]["cache_hits"] >= 1


def test_the_percentile_helper():
    assert gateway._percentile([], 0.9) == 0
    assert gateway._percentile([5], 0.9) == 5
    assert gateway._percentile(list(range(1, 11)), 0.9) == 9
    assert gateway._percentile([10, 1, 5], 0.5) == 5


def test_the_timing_line_drops_anything_that_is_not_a_number_flag_or_short_word(tmp_path, monkeypatch):
    log = tmp_path / "x.jsonl"
    monkeypatch.setenv("JARVIS_LATENCY_LOG", str(log))
    gateway.write_latency_line({"total_ms": 5, "provider": "dahl", "leak": "my password is hunter2 and more words", "tools": 3, "ok": True})
    row = json.loads(log.read_text(encoding="utf-8"))
    assert row["total_ms"] == 5 and row["provider"] == "dahl" and "leak" not in row
    monkeypatch.setenv("JARVIS_LATENCY_LOG", "off")
    gateway.write_latency_line({"total_ms": 6})
    assert len(log.read_text(encoding="utf-8").splitlines()) == 1


def test_face_token_cap_is_off_unless_configured_and_only_for_the_face_with_tools():
    tools = [{"type": "function", "function": {"name": "t"}}]
    on = {"name": "dahl", "face_tool_max_tokens": 512}
    assert gateway.face_token_cap({"name": "dahl"}, {"tools": tools}, True) == {}
    assert gateway.face_token_cap(on, {"tools": tools}, True) == {"max_tokens": 512}
    assert gateway.face_token_cap(on, {"tools": tools}, False) == {}                       # not the face
    assert gateway.face_token_cap(on, {"messages": []}, True) == {}                        # no tools: an ordinary reply may be long
    assert gateway.face_token_cap(on, {"tools": tools, "max_tokens": 4000}, True) == {}    # the caller set its own
    for bad in (10, 100000, "512", True, None):
        assert gateway.face_token_cap({"face_tool_max_tokens": bad}, {"tools": tools}, True) == {}


def test_the_cap_reaches_the_upstream_body_for_the_face(monkeypatch):
    seen = []

    class Up(P.Recorder):
        def do_POST(self):
            n = int(self.headers["Content-Length"])
            seen.append(json.loads(self.rfile.read(n)))
            raw = json.dumps({"choices": [{"message": {"role": "assistant", "content": "ok"}}]}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)
    from http.server import ThreadingHTTPServer
    up = ThreadingHTTPServer(("127.0.0.1", 0), Up)
    threading.Thread(target=up.serve_forever, daemon=True).start()
    providers = [{"name": "dahl", "base_url": f"http://127.0.0.1:{up.server_address[1]}/v1", "key": "from_request", "face_tool_max_tokens": 300,
                  "face_extra_body": {}, "extra_body": {}}]
    handler = gateway.make_handler(P.LabelledFakeDetector(), providers, 10)
    handler.log_message = lambda *a, **k: None
    gw = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    gw.daemon_threads = True
    threading.Thread(target=gw.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{gw.server_address[1]}"
    tool = [{"type": "function", "function": {"name": "desktop__do_goal", "parameters": {"type": "object", "properties": {}}}}]
    try:
        for key in ("jarvis-local", "someone-elses-key"):
            req = urllib.request.Request(base + "/v1/chat/completions", data=json.dumps(body(msg("user", "hi"), tools=tool)).encode(),
                                         headers={"Content-Type": "application/json", "Authorization": f"Bearer {key}"})
            urllib.request.urlopen(req, timeout=10).read()
    finally:
        P.stop(up, gw)
    assert seen[0].get("max_tokens") == 300 and "max_tokens" not in seen[1]
