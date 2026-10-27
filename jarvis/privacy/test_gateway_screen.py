"""The privacy gateway and the screen text the owner asks about (D73): the online model sees placeholders only, the owner sees and hears the real values.

LABELLED FAKES, as in test_gateway_plumbing.py: the detector is a regex stand-in that gives the same KINDS the real one does (a PII-model label "bank account number"
for a bare number, PASSWORD for a cue-word secret ...), the provider is a scripted server on a local socket that records every body it receives, the translator is
translate.FakeBackend. What this proves: the map lives in the gateway only; a streamed or whole answer for the FACE gets the real values in its TEXT (and nowhere else);
the next request's history carries the placeholder form (the leak path); secrets are never stored or restored; an unknown or invented placeholder gets nothing; no real
value is in any request body a provider received, in the latency log, in /health or in the error stream. It does not prove that Presidio or GLiNER find any particular
value (test_privacy.py does, with the real models).

    python -m pytest -q jarvis/privacy/test_gateway_screen.py
"""
from __future__ import annotations

import json
import re
import threading
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

import test_gateway_plumbing as plumbing
from jarvis.plugins._shared import screen_text as st
from jarvis.plugins._shared import translate as T

gateway = plumbing.gateway
redactor = plumbing.redactor
Mapping, ScreenVault, StreamRestorer = redactor.Mapping, redactor.ScreenVault, redactor.StreamRestorer

FACE = "Bearer jarvis-local"
OTHER = "Bearer test-not-a-key"
# made-up screen contents; none is real data
NOTE = "Order number 73912 was sent by Ravi to Anitha. Wifi password: hunter2xyz9. Card 4111 1111 1111 1111. Ref ZX-97156."
REAL = ("73912", "Ravi", "Anitha", "hunter2xyz9", "4111 1111 1111 1111", "ZX-97156", "97156")
SECRETS = ("hunter2xyz9", "4111 1111 1111 1111", "4111")


class ScreenDetector:
    """FAKE detector: gives the kinds the real one gives. Not Presidio, not GLiNER."""
    names = False
    stats: dict = {}
    RULES = [("PERSON", r"\b(?:Ravi|Anitha|Satish|Priya)\b"),
             ("PASSWORD", r"(?<=password: )\S+?(?=\.(?:\s|$)|\s|$)"),
             ("CREDIT_CARD", r"\b4111 1111 1111 1111\b"),
             ("API_KEY", r"\bsk-[A-Za-z0-9]{20,}\b"),
             ("BANK_ACCOUNT", r"\bZX-\d{5}\b|(?<![\w-])\d{5}(?![\w-])|\b\d{12,}\b")]     # the PII model calls a bare number "bank account number"

    def __init__(self):
        self.model = object()
        self.seen = []
        self.rx = re.compile("|".join(f"(?P<{k}>{p})" for k, p in self.RULES))

    def redact(self, text, mapping, deep=True, names=None):
        self.seen.append(text)
        out, pos = [], 0
        for mm in self.rx.finditer(text):
            if redactor.PLACEHOLDER.fullmatch(mm.group(0)) or redactor._ONLY_MARKS.fullmatch(mm.group(0)):     # as Redactor.redact
                continue
            out.append(text[pos:mm.start()])
            out.append(mapping.tag(mm.lastgroup, mm.group(0)))
            pos = mm.end()
        out.append(text[pos:])
        return "".join(out)


@pytest.fixture(autouse=True)
def layer(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("JARVIS_LANGUAGE_FILE", str(tmp_path / "language.json"))
    monkeypatch.setenv("JARVIS_GLOSSARY", str(tmp_path / "glossary.json"))
    monkeypatch.setenv("JARVIS_LEARNED_NAMES", "0")
    monkeypatch.setenv("JARVIS_LATENCY_LOG", str(tmp_path / "latency.jsonl"))
    monkeypatch.delenv("JARVIS_TRANSLATE", raising=False)
    monkeypatch.delenv("JARVIS_SCREEN_RESTORE", raising=False)
    T.set_translator(T.Translator([T.FakeBackend(table={})], {"keep_original": False, "budget_s": 1.0, "history_budget_s": 1.0}, glossary=[]))
    yield tmp_path
    T.set_translator(None)


class Provider:
    """A scripted provider. `script` is a list of replies used in order (the last repeats): a str is the answer text, a dict is the answer's message
    (or {"chunks": [...]} for a streamed one). Every body it receives is kept as text in `.bodies`."""

    def __init__(self, *script):
        self.script, self.bodies, outer = list(script), [], self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_POST(self):
                raw = self.rfile.read(int(self.headers["Content-Length"])).decode("utf-8")
                outer.bodies.append(raw)
                i = min(len(outer.bodies), len(outer.script)) - 1
                rep = outer.script[i]
                if isinstance(rep, dict) and "chunks" in rep:
                    self.send_response(200)
                    self.send_header("Content-Type", "text/event-stream")
                    self.end_headers()
                    for j, piece in enumerate(rep["chunks"]):
                        last = j == len(rep["chunks"]) - 1
                        ev = {"choices": [{"index": 0, "delta": {"content": piece}, "finish_reason": "stop" if last else None}]}
                        self.wfile.write(("data: " + json.dumps(ev) + "\n\n").encode())
                        self.wfile.flush()
                    self.wfile.write(b"data: [DONE]\n\n")
                    return
                msg = {"role": "assistant", "content": rep} if isinstance(rep, str) else rep
                data = json.dumps({"choices": [{"index": 0, "message": msg, "finish_reason": "stop"}]}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.srv.server_address[1]}/v1"


class Gw:
    def __init__(self, *script, vault=None, detector=None):
        self.up = Provider(*script)
        self.vault = vault if vault is not None else ScreenVault()
        self.det = detector or ScreenDetector()
        handler = gateway.make_handler(self.det, self.up.url, 10, vault=self.vault)
        handler.log_message = lambda *a, **k: None
        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self.srv.daemon_threads = True
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.port = self.srv.server_address[1]
        self.base = f"http://127.0.0.1:{self.port}"

    def close(self):
        for s in (self.srv, self.up.srv):
            s.shutdown()
            s.server_close()

    def post(self, path, body, key=FACE):
        req = urllib.request.Request(self.base + path, data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
                                     headers={"Content-Type": "application/json", "Authorization": key})
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                return r.status, r.read().decode("utf-8")
        except urllib.error.HTTPError as e:
            return e.code, e.read().decode("utf-8")

    def redact(self, text, **extra):
        code, raw = self.post("/redact", {"text": text, "names": True, **extra})
        assert code == 200, raw
        return json.loads(raw)

    def chat(self, messages, key=FACE, stream=False):
        code, raw = self.post("/v1/chat/completions", {"model": "m", "messages": messages, "stream": stream}, key)
        assert code == 200, raw
        if not stream:
            return json.loads(raw)["choices"][0]["message"]
        chunks = [json.loads(ln[5:]) for ln in raw.splitlines() if ln.startswith("data:") and "[DONE]" not in ln]
        return {"content": "".join((c["choices"][0]["delta"].get("content") or "") for c in chunks if c.get("choices")),
                "pieces": [(c["choices"][0]["delta"].get("content") or "") for c in chunks if c.get("choices")], "raw": raw}


@pytest.fixture
def gw():
    made = []

    def make(*script, **kw):
        g = Gw(*script, **kw)
        made.append(g)
        return g
    yield make
    for g in made:
        g.close()


def tool_round(window_text, question="what does the notepad say"):
    """The messages of the round after the plugin read the window: the owner's question, the model's tool call, the plugin's result."""
    return [{"role": "user", "content": question},
            {"role": "assistant", "content": None, "tool_calls": [{"id": "c1", "type": "function", "function": {"name": "desktop__read_window_text", "arguments": "{\"app\":\"notepad\"}"}}]},
            {"role": "tool", "tool_call_id": "c1", "content": json.dumps({"ok": True, "text": window_text})}]


def never_in(haystacks, values=REAL):
    for h in haystacks:
        for v in values:
            assert v not in h, f"a real value left the PC: {v!r}"


# ---------------------------------------------------------------------------------------------------------------- /redact
def test_a_restorable_redaction_gives_tags_for_what_the_owner_may_see_and_redacted_for_secrets(gw):
    g = gw("ok")
    a = g.redact(NOTE, restorable=True)
    t = a["text"]
    assert "[NUMBER_1]" in t and "[PERSON_1]" in t and "[PERSON_2]" in t, t
    assert "[REDACTED:password]" in t and "[REDACTED:credit_card]" in t, t
    never_in([t])
    assert a["restorable"] is True and a["secrets_hidden"] == 2 and a["names"] is True and a["deep"] is True and a["ready"] is True
    assert "ZX-" not in t and t.count("[NUMBER_") >= 1
    shown = g.vault.display()
    assert shown["[NUMBER_1]"] == "73912" and shown["[PERSON_1]"] == "Ravi"
    assert "ZX-97156" in shown.values(), "a 'XX-12345' reference is a number the owner may hear again"
    assert not any(v in SECRETS for v in shown.values()), "a secret is never kept"
    assert not any(s in repr(g.vault.__dict__) for s in SECRETS)


def test_without_the_flag_the_redaction_answers_exactly_as_before(gw):
    g = gw("ok")
    a = g.redact("Order number 73912 was sent by Ravi")
    assert a["text"] == "Order number [BANK_ACCOUNT_1] was sent by [PERSON_1]" and "restorable" not in a
    assert g.vault.size() == 0


def test_the_switch_off_means_the_old_answer_and_an_empty_vault(gw, monkeypatch):
    monkeypatch.setenv("JARVIS_SCREEN_RESTORE", "0")
    g = gw("[PERSON_1] said hi")
    a = g.redact("Ravi", restorable=True)
    assert a["text"] == "[PERSON_1]" and "restorable" not in a and g.vault.size() == 0
    assert g.chat(tool_round(a["text"]))["content"] == "[PERSON_1] said hi"


def test_the_same_value_keeps_its_tag_across_reads_and_a_new_one_continues_the_numbers(gw):
    g = gw("ok")
    g.redact("Ravi called", restorable=True)
    second = g.redact("Anitha and Ravi", restorable=True)["text"]
    assert second == "[PERSON_2] and [PERSON_1]"


def test_a_tag_that_is_really_on_the_screen_cannot_be_mistaken_for_ours(gw):
    g = gw("ok")
    a = g.redact("the chat shows [PERSON_1] and [NUMBER_9] and [REDACTED:password]", restorable=True)
    assert "[PERSON_1]" not in a["text"] and "(PERSON_1)" in a["text"] and "(NUMBER_9)" in a["text"]
    assert g.vault.size() == 0


# ---------------------------------------------------------------------------------------------------------------- the answer, whole
def test_the_faces_answer_gets_the_real_values_and_the_provider_only_ever_saw_tags(gw):
    g = gw("The order is [NUMBER_1], sent by [PERSON_1] to [PERSON_2]. The password is hidden: [REDACTED:password].")
    text = g.redact(NOTE, restorable=True)["text"]
    msg = g.chat(tool_round(text))
    assert msg["content"] == "The order is 73912, sent by Ravi to Anitha. The password is hidden: [REDACTED:password]."
    never_in(g.up.bodies)
    sent = g.up.bodies[-1]
    assert "[NUMBER_1]" in sent and "[PERSON_1]" in sent and "[REDACTED:password]" in sent


def test_another_caller_gets_the_tags_back_not_the_values(gw):
    g = gw("The order is [NUMBER_1], sent by [PERSON_1].")
    text = g.redact(NOTE, restorable=True)["text"]
    assert g.chat(tool_round(text), key=OTHER)["content"] == "The order is [NUMBER_1], sent by [PERSON_1]."
    assert g.chat(tool_round(text), key="")["content"] == "The order is [NUMBER_1], sent by [PERSON_1]."


def test_unknown_and_invented_placeholders_stay_as_they_are(gw):
    reply = "[PERSON_9] and [NUMBER_7] and [PASSWORD_1] and [CREDIT_CARD_1] and [REDACTED:credit_card] and [PERSON_1] and [FOO_1]"
    g = gw(reply)
    text = g.redact(NOTE, restorable=True)["text"]
    assert g.chat(tool_round(text))["content"] == reply.replace("[PERSON_1]", "Ravi")


def test_a_model_cannot_ask_for_a_secret_back_by_naming_its_tag(gw):
    g = gw("[PASSWORD_1] [PASSWORD_2] [CREDIT_CARD_1] [BANK_ACCOUNT_1] [OTP_1] [API_KEY_1]")
    text = g.redact(NOTE + " sk-abcdefghijklmnopqrstuvwxyz0123 pin", restorable=True)["text"]
    out = g.chat(tool_round(text))["content"]
    assert out == "[PASSWORD_1] [PASSWORD_2] [CREDIT_CARD_1] [BANK_ACCOUNT_1] [OTP_1] [API_KEY_1]"
    never_in([out] + g.up.bodies, SECRETS + ("sk-abcdefghijklmnopqrstuvwxyz0123",))


def test_a_long_or_unlisted_kind_is_not_restorable():
    v = ScreenVault()
    assert v.tag("PASSWORD", "hunter2xyz9") is None and v.tag("CREDIT_CARD", "4111 1111 1111 1111") is None
    assert v.tag("PERSON", "x" * 201) is None and v.tag("PERSON", "  ") is None
    assert v.size() == 0


def test_tool_call_arguments_are_not_restored_only_the_text(gw):
    g = gw({"role": "assistant", "content": "Typing [PERSON_1] now.", "tool_calls": [{"id": "c2", "type": "function", "function": {"name": "type_text", "arguments": "{\"text\": \"[PERSON_1]\"}"}}]})
    text = g.redact(NOTE, restorable=True)["text"]
    msg = g.chat(tool_round(text))
    assert msg["content"] == "Typing Ravi now."
    assert msg["tool_calls"][0]["function"]["arguments"] == "{\"text\": \"[PERSON_1]\"}", "screen text never reaches a tool through the model's call"


def test_a_value_the_owner_types_himself_comes_back_in_the_tool_call_but_a_screen_only_value_does_not(gw):
    call = {"role": "assistant", "content": None, "tool_calls": [{"id": "c2", "type": "function", "function": {"name": "set_timer", "arguments": "{\"seconds\": \"[NUMBER_1]\", \"who\": \"[PERSON_1]\"}"}}]}
    g = gw(call)
    g.redact("Order 73912 was sent by Ravi", restorable=True)                      # both are now on the vault
    msgs = [{"role": "user", "content": "set the timer to 73912 seconds"}]       # the owner typed one of them himself
    sent_call = g.chat(msgs)["tool_calls"][0]["function"]["arguments"]
    assert json.loads(sent_call) == {"seconds": "73912", "who": "[PERSON_1]"}, "his own number is his again; the name only came from the screen"
    assert "73912" not in g.up.bodies[-1] and "[NUMBER_1]" in g.up.bodies[-1]


# ---------------------------------------------------------------------------------------------------------------- the answer, streamed
def test_a_streamed_answer_is_restored_even_when_a_tag_is_split_across_chunks(gw):
    chunks = ["The order is [NU", "MBER_", "1], sent by [PERSON", "_1] to [PE", "RSON_2", "]. See [1", "] and [link", " text] ok", " [", "x"]
    g = gw({"chunks": chunks})
    text = g.redact(NOTE, restorable=True)["text"]
    got = g.chat(tool_round(text), stream=True)
    assert got["content"] == "The order is 73912, sent by Ravi to Anitha. See [1] and [link text] ok [x"
    assert not any("[NU" in p or "[PERSON" in p or "RSON_" in p for p in got["pieces"]), "no half tag reaches the owner"
    never_in(g.up.bodies)


def test_every_split_point_gives_the_same_text():
    v = ScreenVault()
    v.tag("PERSON", "Ravi")
    v.tag("NUMBER", "73912")
    m = Mapping(to_value=v.display())
    whole = "Hi [PERSON_1], order [NUMBER_1] [PERSON_2] [x] [PERSON_1][NUMBER_1]!"
    want = "Hi Ravi, order 73912 [PERSON_2] [x] Ravi73912!"
    for i in range(len(whole) + 1):
        r = StreamRestorer(m)
        assert r.feed(whole[:i]) + r.feed(whole[i:]) + r.flush() == want, i
    r = StreamRestorer(m)                                    # one character at a time
    assert "".join(r.feed(ch) for ch in whole) + r.flush() == want


def test_only_a_possible_tag_start_is_held_back():
    m = Mapping(to_value={"[PERSON_1]": "Ravi"})
    r = StreamRestorer(m)
    assert r.feed("see [link") == "see [link", "'[link' cannot become a tag, so nothing waits"
    assert r.feed(" and [") == " and ", "a lone bracket might"
    assert r.feed("PERSON_") == "" and r.feed("1") == "" and r.feed("] ok") == "Ravi ok"
    assert r.feed("done [PER") == "done " and r.flush() == "[PER", "what is left at the end is released, not lost"
    assert StreamRestorer(m).feed("[" + "A" * 60) == "[" + "A" * 60, "a long run of capitals is not held for ever"


def test_the_incremental_buffer_does_not_hold_ordinary_text():
    r = StreamRestorer(Mapping(to_value={"[PERSON_1]": "Ravi"}))
    assert [r.feed(x) for x in ("Hello ", "world, ", "this is text.")] == ["Hello ", "world, ", "this is text."]


# ---------------------------------------------------------------------------------------------------------------- the leak path: history
def test_the_next_turns_history_carries_the_tags_not_the_values(gw):
    g = gw("The order is [NUMBER_1], sent by [PERSON_1].", "You are welcome.")
    text = g.redact(NOTE, restorable=True)["text"]
    first = g.chat(tool_round(text))["content"]
    assert first == "The order is 73912, sent by Ravi."
    # what the face does next: it kept the text it showed and sends it back as history, with the owner's next words (who may use the real names)
    history = tool_round(text) + [{"role": "assistant", "content": first}, {"role": "user", "content": "thanks, tell Ravi that 73912 is fine"}]
    again = g.chat(history)
    assert again["content"] == "You are welcome."
    second_body = g.up.bodies[-1]
    never_in([second_body])
    assert "[NUMBER_1]" in second_body and "[PERSON_1]" in second_body
    msgs = json.loads(second_body)["messages"]
    assert msgs[-2]["content"] == "The order is [NUMBER_1], sent by [PERSON_1]."
    assert msgs[-1]["content"] == "thanks, tell [PERSON_1] that [NUMBER_1] is fine", "the owner's own words are pseudonymized consistently too"


def test_a_real_value_anywhere_else_in_a_request_is_hidden_the_same_way(gw):
    g = gw("ok")
    g.redact("Ravi 73912", restorable=True)
    body = {"model": "m", "messages": [{"role": "system", "content": "memory: Ravi's order 73912"}, {"role": "user", "content": "hi"},
                                       {"role": "assistant", "content": None, "tool_calls": [{"id": "c1", "type": "function", "function": {"name": "t", "arguments": "{\"who\": \"Ravi\"}"}}]},
                                       {"role": "tool", "tool_call_id": "c1", "content": "Ravi: 73912"}],
            "tools": [{"type": "function", "function": {"name": "t", "description": "for Ravi only", "parameters": {"type": "object"}}}]}
    assert g.post("/v1/chat/completions", body)[0] == 200
    sent = g.up.bodies[-1]
    never_in([sent], ("Ravi", "73912"))
    assert "[PERSON_1]" in sent and "[NUMBER_1]" in sent


def test_hiding_matches_whole_words_only_and_longest_first():
    v = ScreenVault()
    v.tag("NUMBER", "240")
    v.tag("NUMBER", "2400 EUR")
    v.tag("PERSON", "Ravi")
    assert v.hide("total 240 and 2400 EUR and 24000 and Ravi's and XRavi and Ravi") == "total [NUMBER_1] and [NUMBER_2] and 24000 and [PERSON_1]'s and XRavi and [PERSON_1]"
    assert v.hide(None) is None and v.hide("") == ""
    assert v.hide_tree({"model": "Ravi", "messages": [{"role": "user", "content": "Ravi"}], "n": 3}) == {"model": "Ravi", "messages": [{"role": "user", "content": "[PERSON_1]"}], "n": 3}


# ---------------------------------------------------------------------------------------------------------------- tags never mean two things
def test_this_requests_own_tags_never_clash_with_the_screens(gw):
    g = gw("[PERSON_1] and [PERSON_2] and [PERSON_3]")
    text = g.redact("Ravi wrote", restorable=True)["text"]
    msgs = tool_round(text)
    msgs.append({"role": "user", "content": "and Satish and Priya"})                  # the owner's own words: this request's detector tags them
    out = g.chat(msgs)["content"]
    assert out == "Ravi and Satish and Priya"
    sent = json.loads(g.up.bodies[-1])["messages"]
    assert sent[-1]["content"] == "and [PERSON_2] and [PERSON_3]", sent[-1]
    never_in(g.up.bodies, ("Ravi", "Satish", "Priya"))


def test_a_number_is_never_given_to_a_second_value_even_after_the_first_is_forgotten():
    now = [0.0]
    v = ScreenVault(ttl_s=10, clock=lambda: now[0])
    assert v.tag("PERSON", "Ravi") == "[PERSON_1]"
    now[0] = 100.0
    assert v.size() == 0, "an entry nobody used for the time limit is forgotten"
    assert v.tag("PERSON", "Anitha") == "[PERSON_2]", "[PERSON_1] in an old history must not restore to someone else"
    assert v.counters() == {"PERSON": 2}
    v.clear()
    assert v.tag("PERSON", "Satish") == "[PERSON_3]"


def test_an_entry_stays_while_requests_still_carry_its_value_and_goes_when_nothing_does():
    now = [0.0]
    v = ScreenVault(ttl_s=10, clock=lambda: now[0])
    v.tag("PERSON", "Ravi")
    v.tag("PERSON", "Anitha")
    now[0] = 8
    assert v.hide("hi Ravi") == "hi [PERSON_1]"                  # the face's history still holds Ravi
    now[0] = 15
    assert v.size() == 1 and v.display() == {"[PERSON_1]": "Ravi"}, "Anitha was not met again, so she is forgotten"
    now[0] = 30
    assert v.size() == 0 and v.hide("hi Ravi") == "hi Ravi"


def test_the_vault_is_bounded_and_uses_the_least_recently_used_first():
    now = [0.0]
    v = ScreenVault(max_entries=3, clock=lambda: now[0])
    for i, n in enumerate(("a1", "b2", "c3")):
        now[0] = i
        v.tag("NUMBER", n)
    now[0] = 10
    v.tag("NUMBER", "a1")                                    # used again: now the newest
    now[0] = 11
    assert v.tag("NUMBER", "d4") == "[NUMBER_4]"
    assert sorted(v.display().values()) == ["a1", "c3", "d4"] and v.size() == 3


# ---------------------------------------------------------------------------------------------------------------- nothing but the owner's screen sees a value
def test_no_log_header_health_or_error_stream_holds_a_real_value(gw, layer, capsys):
    g = gw("The order is [NUMBER_1], sent by [PERSON_1].")
    text = g.redact(NOTE, restorable=True)["text"]
    req = urllib.request.Request(g.base + "/v1/chat/completions", data=json.dumps({"model": "m", "messages": tool_round(text)}).encode(),
                                 headers={"Content-Type": "application/json", "Authorization": FACE})
    with urllib.request.urlopen(req, timeout=10) as r:
        headers = str(dict(r.headers))
        r.read()
    health = urllib.request.urlopen(g.base + "/health", timeout=10).read().decode()
    log = (layer / "latency.jsonl").read_text(encoding="utf-8") if (layer / "latency.jsonl").exists() else ""
    err = capsys.readouterr()
    never_in([headers, health, log, err.out, err.err, json.dumps(g.det.stats)])
    assert json.loads(health)["screen_vault"] == {"entries": 4, "restore": True}
    assert "73912" not in json.dumps({k: v for k, v in g.vault.__dict__.items() if k in ("_counts",)})


def test_the_vault_is_never_written_to_disk(gw, layer):
    g = gw("[PERSON_1]")
    g.redact(NOTE, restorable=True)
    g.chat(tool_round(g.redact(NOTE, restorable=True)["text"]))
    for p in layer.rglob("*"):
        if p.is_file():
            never_in([p.read_text(encoding="utf-8", errors="replace")])


def test_the_source_has_no_way_to_persist_or_print_the_map():
    import os
    with open(os.path.join(plumbing.HERE, "redactor.py"), encoding="utf-8") as fh:
        text = fh.read()
    src = text[text.index("class ScreenVault"):]            # the vault and screen_redact: the end of the file
    for word in ("open(", "print(", "logging", "json.dump", "write(", "pickle", "sys.stderr"):
        assert word not in src, word


# ---------------------------------------------------------------------------------------------------------------- the classes
@pytest.mark.parametrize("entity,value,kind", [
    ("PERSON", "Ravi", "PERSON"), ("LOCATION", "Guntur", "LOCATION"), ("EMAIL_ADDRESS", "a@b.example", "EMAIL_ADDRESS"), ("PHONE_NUMBER", "+91 98765 43210", "PHONE_NUMBER"),
    ("ADDRESS", "12 Elm St", "ADDRESS"), ("USERNAME", "ravi_k88", "USERNAME"),
    ("BANK_ACCOUNT", "73912", "NUMBER"), ("BANK_ACCOUNT", "ZX-97156", "NUMBER"), ("PASSPORT", "40057", "NUMBER"), ("DRIVING_LICENCE", "1 234", "NUMBER"),
    ("BANK_ACCOUNT", "123456789", None), ("BANK_ACCOUNT", "1234 5678 9012", None), ("PASSPORT", "K1234567", None), ("BANK_ACCOUNT", "GB29NWBK60161331926819", None),
    ("CREDIT_CARD", "4111111111111111", None), ("IBAN_CODE", "GB29 NWBK 6016 1331 9268 19", None), ("PASSWORD", "hunter2", None), ("PASSWORD", "482913", None),
    ("API_KEY", "sk-abc", None), ("PRIVATE_KEY", "-----BEGIN", None), ("JWT", "eyJ", None), ("OTP", "123456", None), ("CRYPTO", "1A1zP1", None),
    ("AADHAAR", "2345 6789 0123", None), ("PAN", "ABCDE1234F", None), ("SOMETHING_NEW", "x", None), ("NUMBER", "12", "NUMBER")])
def test_which_kinds_the_owner_may_hear_again(entity, value, kind):
    assert redactor.screen_class(entity, value) == kind


def test_the_real_redactor_class_lists_are_what_the_screen_rules_know():
    assert set(redactor.BASE_ENTITIES + redactor.CUSTOM_ENTITIES) - redactor.SCREEN_RESTORABLE >= {"CREDIT_CARD", "IBAN_CODE", "PASSWORD", "API_KEY", "PRIVATE_KEY", "JWT", "AADHAAR", "PAN", "CRYPTO"}
    assert "PHONE_NUMBER" in redactor.SCREEN_RESTORABLE and not (redactor.SCREEN_RESTORABLE & {"PASSWORD", "CREDIT_CARD", "IBAN_CODE", "API_KEY", "OTP"})


# ---------------------------------------------------------------------------------------------------------------- the whole path, from the plugin to the owner
def test_from_the_plugins_release_through_the_gateway_to_the_owners_screen(gw, monkeypatch):
    """The desktop plugin's own helpers against this gateway: release_for_request (the secret pass, the gateway's /redact) -> the plugin boundary (clean_result)
    -> the face's chat request -> the provider -> the owner's reply; and the provider never saw a real value."""
    g = gw("The order is [NUMBER_1], sent by [PERSON_1] to [PERSON_2]. I hid a secret: [key_assignment removed]. [card number removed] too.")
    monkeypatch.setenv("JARVIS_PRIVACY_PORT", str(g.port))
    got = st.release_for_request(NOTE)
    assert got.allowed and got.via == "gateway"
    never_in([got.text, got.note])
    assert "[NUMBER_1]" in got.text and "[PERSON_1]" in got.text
    assert "copy a tag exactly" in got.note and "say that you hid a secret" in got.note, "the model is told how to give a tag back"
    result = st.clean_result({"ok": True, st.SCREEN_KEY: True, "text": got.text, "text_note": got.note})
    never_in([json.dumps(result)])
    assert result["text"].count("[") >= 3 and "[PERSON_1]" in result["text"]
    msg = g.chat(tool_round(result["text"]))
    assert msg["content"].startswith("The order is 73912, sent by Ravi to Anitha. I hid a secret:")
    never_in(g.up.bodies)
    assert "hunter2xyz9" not in json.dumps(result) and "4111" not in json.dumps(result)


def test_the_note_has_the_hint_only_when_the_text_holds_tags(gw, monkeypatch):
    g = gw("ok")
    monkeypatch.setenv("JARVIS_PRIVACY_PORT", str(g.port))
    plain = st.release_for_request("Total due is fine, nothing private here")
    assert plain.allowed and "copy a tag" not in plain.note
    assert st.with_tag_hint("n", "x [REDACTED:password]") != "n" and st.with_tag_hint("n", "x [card number removed]") != "n" and st.with_tag_hint("n", "[1] [x]") == "n"
