"""The language layer (D58): detection, masking, the budget and fallbacks, local-only backends, caching, reply language, the owner's record.

Every model here is a LABELLED FAKE (translate.FakeBackend): word-for-word replacement from a table. What this file proves is the plumbing and the rules
(English costs nothing, protected items come back exactly, a slow or broken translator never blocks, nothing leaves the machine). It does NOT prove
translation quality: that is jarvis/evals/translate/run_eval.py on the laptop with the real model.
"""
from __future__ import annotations

import json
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from jarvis.plugins._shared import translate as T

TE = {"ఓపెన్": "open", "నోట్‌ప్యాడ్": "notepad", "చేయి": "do", "షాపింగ్": "shopping", "లిస్ట్": "list", "రాయి": "write", "ఫైల్": "file", "సేవ్": "save",
      "మరియు": "and", "ఈ": "this", "తెరువు": "open"}
HI = {"नोटपैड": "notepad", "खोलो": "open", "और": "and", "शॉपिंग": "shopping", "लिस्ट": "list", "लिखो": "write"}
TENGLISH = {"chesi": "and", "rayi": "write", "cheyyi": "do", "ee": "this", "lo": "in", "ni": ""}


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("JARVIS_LANGUAGE_FILE", str(tmp_path / "language.json"))
    monkeypatch.setenv("JARVIS_GLOSSARY", str(tmp_path / "glossary.json"))
    T.set_translator(None)
    yield
    T.set_translator(None)


def fake(table=None, **kw):
    b = T.FakeBackend(table={**TE, **HI, **TENGLISH, **(table or {})}, **kw)
    return b, T.Translator([b], {"keep_original": False, **kw.pop("cfg", {})}, glossary=[])


# ------------------------------------------------------------------------------------------------------------ detection

@pytest.mark.parametrize("text,lang", [
    ("నోట్‌ప్యాడ్ ఓపెన్ చేయి", "te"),
    ("नोटपैड खोलो और शॉपिंग लिस्ट लिखो", "hi"),
    ("notepad open chesi shopping list rayi", "te-Latn"),
    ("mujhe weather batao aaj", "hi-Latn"),
    ("kal subah 7 baje alarm laga do", "hi-Latn"),
    ("open Notepad మరియు shopping list రాయి", "te"),
    ("Please save the report as D:\\docs\\a.txt", "en"),
    ("What is the weather in Hyderabad today?", "en"),
    ("open notepad and type hello", "en"),
    ("Play some music by Rahman", "en"),
    ("Please remind me to call Kiran at 5pm", "en"),
    ("", "en"),
    ("12345 !!!", "en"),
    ("ok", "en"),
])
def test_detect_language(text, lang):
    assert T.detect_language(text) == lang


def test_a_symbol_letter_or_two_inside_english_stays_english():
    assert T.detect_language("rating ఈ") == "en" and T.detect_language("ఈ file") == "en"            # one native letter: a symbol, not a language


def test_a_close_call_goes_to_a_model_only_when_one_is_wired_and_asked():
    b = T.FakeBackend(ident="te-Latn")
    t = "rate that thing please"
    assert T.detect_language(t) == "en"                      # no model, no hit: English passes through at no cost
    # a text that is unsure: no word hit but Indic-looking letters
    unsure = "aaranna nenaara baaruni"
    d = T.detect(unsure)
    if d.unsure:
        assert T.detect(unsure, b).method == "model" and b.identify_calls
    b2 = T.FakeBackend(ident="te-Latn")
    T.Translator([b2], {"identify_with_model": False}, glossary=[]).detect(unsure)
    assert not b2.identify_calls, "identify_with_model is off by default: a close call never costs a model swap"
    assert T.DEFAULTS["identify_with_model"] is False


# ------------------------------------------------------------------------------------------------------------ English costs nothing

def test_english_input_makes_no_backend_call_and_is_returned_unchanged():
    b, t = fake()
    for text in ("Open Notepad and type the shopping list", "What is the weather in Hyderabad today?", "delete D:\\Temp\\old.txt", ""):
        r = t.to_english(text)
        assert r.english == text and r.source_lang == "en" and r.translated is False and r.status == "no"
    assert b.calls == [] and b.identify_calls == []
    info = T.translate_to_english("Open Notepad", translator=t)
    assert info["english"] == "Open Notepad" and info["source_lang"] == "en" and b.calls == []
    assert T.english("Open Notepad") == "Open Notepad"


# ------------------------------------------------------------------------------------------------------------ translating

def test_telugu_script_is_translated_and_the_result_has_the_documented_shape():
    b, t = fake()
    info = T.translate_to_english("నోట్‌ప్యాడ్ ఓపెన్ చేయి", translator=t)
    assert set(info) >= {"english", "source_lang", "confidence", "preserved", "original", "translated"}
    assert info["english"] == "notepad open do" and info["source_lang"] == "te" and info["translated"] is True
    assert info["original"] == "నోట్‌ప్యాడ్ ఓపెన్ చేయి" and 0 < info["confidence"] <= 1


def test_devanagari_tenglish_and_mixed_are_translated():
    b, t = fake()
    assert t.to_english("नोटपैड खोलो और शॉपिंग लिस्ट लिखो").english == "notepad open and shopping list write"
    r = t.to_english("notepad ee chesi rayi")
    assert r.source_lang == "te-Latn" and r.english == "notepad this and write" and b.calls[-1][1:] == ("te", "en")
    mixed = t.to_english("open Notepad మరియు shopping list రాయి")
    assert mixed.source_lang == "te" and mixed.english == "open notepad and shopping list write" or "write" in mixed.english


def test_a_hindi_romanized_sentence_asks_the_backend_for_hindi_romanized():
    seen = {}

    class Spy(T.FakeBackend):
        def translate(self, text, src, tgt, *, romanized=False, deadline=None):
            seen.update(src=src, romanized=romanized)
            return super().translate(text, src, tgt, romanized=romanized, deadline=deadline)

    t = T.Translator([Spy()], {"keep_original": False}, glossary=[])
    t.to_english("mujhe weather batao aaj")
    assert seen == {"src": "hi", "romanized": True}


def test_only_runs_of_non_english_lines_are_translated_in_a_multi_line_message():
    b, t = fake()
    text = "[Mon Oct 26 10:00]\nఓపెన్ నోట్‌ప్యాడ్ చేయి\nKeep replies short, no markdown.\n"
    r = t.to_english(text)
    assert r.english == "[Mon Oct 26 10:00]\nopen notepad do\nKeep replies short, no markdown.\n"
    assert [c[0] for c in b.calls] == ["ఓపెన్ నోట్‌ప్యాడ్ చేయి"], "the face's own English lines must not go to the translator"


# ------------------------------------------------------------------------------------------------------------ masking

def test_paths_urls_code_quotes_numbers_emails_and_names_are_masked_and_restored():
    b, t = fake()
    t._glossary = ["JARVIS", "Satish"]
    text = ('ఓపెన్ "my list.txt" మరియు C:\\Users\\Satish\\Docs\\a b.txt https://example.com/x?y=1 `rm -rf /tmp/x` '
            'a.b@example.com 10:30 pm 500 JARVIS Satish ~/notes/todo.md')
    r = t.to_english(text)
    sent = b.calls[0][0]
    for secret in ("my list.txt", "C:\\Users", "example.com", "rm -rf", "a.b@example.com", "10:30", "JARVIS", "Satish", "todo.md"):
        assert secret not in sent, f"{secret} must be masked before the model sees it"
    for kept in ('"my list.txt"', "C:\\Users\\Satish\\Docs\\a", "https://example.com/x?y=1", "`rm -rf /tmp/x`", "a.b@example.com", "10:30 pm", "500", "JARVIS", "Satish",
                 "~/notes/todo.md"):
        assert kept in r.english, f"{kept} must come back exactly"
    kinds = {p["kind"] for p in r.preserved}
    assert {"quoted", "path", "url", "code", "email", "number", "name"} <= kinds
    assert r.english.startswith("open ")


def test_a_fenced_code_block_survives_whole():
    b, t = fake()
    code = "```python\nprint('హలో')\n```"
    r = t.to_english(f"ఈ ఫైల్ రాయి {code}")
    assert code in r.english and "హలో" not in b.calls[0][0]


def test_a_backend_that_loses_or_repeats_a_protected_item_is_not_trusted():
    class Eater(T.FakeBackend):
        def translate(self, text, src, tgt, *, romanized=False, deadline=None):
            return "open it"                                   # the placeholder is gone

    t = T.Translator([Eater()], {"keep_original": False}, glossary=[])
    r = t.to_english("ఓపెన్ C:\\Temp\\old.txt")
    assert r.translated is False and r.english == "ఓపెన్ C:\\Temp\\old.txt" and "protected item" in r.skipped_reason
    ok = T.FakeBackend(table=TE)
    t2 = T.Translator([Eater(), ok], {"keep_original": False}, glossary=[])
    r2 = t2.to_english("ఓపెన్ C:\\Temp\\old.txt")
    assert r2.translated and r2.english == "open C:\\Temp\\old.txt", "the next backend is tried"


def test_masking_is_idempotent_on_text_without_protected_items_and_tokens_cannot_collide_with_the_text():
    m = T.mask("just words here", [])
    assert m.text == "just words here" and m.items == []
    m2 = T.mask("the code JVX0Q and C:\\a\\b.txt", [])
    assert m2.prefix != "JVX" and T.unmask(m2.text, m2) == ("the code JVX0Q and C:\\a\\b.txt", [])


def test_the_glossary_file_extends_what_is_never_translated(tmp_path):
    g = tmp_path / "glossary.json"
    g.write_text(json.dumps({"terms": ["Zorbo"], "owner_names": ["సతీష్"]}, ensure_ascii=False), encoding="utf-8")
    assert {"Zorbo", "సతీష్"} <= set(T.load_glossary(g))
    m = T.mask("open Zorbo for సతీష్", T.load_glossary(g))
    assert "Zorbo" not in m.text and "సతీష్" not in m.text
    assert T.load_glossary(tmp_path / "missing.json") == []


# ------------------------------------------------------------------------------------------------------------ the budget, the breaker, the fallbacks

def test_a_slow_backend_hits_the_budget_and_the_original_goes_on_unchanged():
    b = T.FakeBackend(table=TE, delay=0.6)
    t = T.Translator([b], {"keep_original": False, "budget_s": 0.15}, glossary=[])
    t0 = time.monotonic()
    r = t.to_english("ఓపెన్ నోట్‌ప్యాడ్ చేయి")
    assert time.monotonic() - t0 < 0.5
    assert r.translated is False and r.english == "ఓపెన్ నోట్‌ప్యాడ్ చేయి" and r.status == "skipped" and "0.15" in r.skipped_reason
    assert r.low_confidence is True


def test_after_a_failure_the_backend_rests_so_the_next_request_is_fast():
    clock = [0.0]
    b = T.FakeBackend(table=TE, fail=RuntimeError("down"))
    t = T.Translator([b], {"keep_original": False, "breaker_s": 30.0}, glossary=[], clock=lambda: clock[0])
    assert t.to_english("ఓపెన్ నోట్‌ప్యాడ్").translated is False
    n = len(b.calls)
    r = t.to_english("ఓపెన్ చేయి")
    assert len(b.calls) == n and "resting" in r.skipped_reason, "no second call while the breaker is open"
    clock[0] = 31.0
    b.fail = None
    assert t.to_english("ఓపెన్ చేయి").translated is True


def test_the_next_backend_answers_when_the_first_fails():
    bad = T.FakeBackend(fail=TimeoutError("x"))
    good = T.FakeBackend(table=TE)
    good.name = "second"
    t = T.Translator([bad, good], {"keep_original": False}, glossary=[])
    r = t.to_english("ఓపెన్ చేయి")
    assert r.translated and r.backend == "second" and r.english == "open do"


def test_no_translator_at_all_is_a_skip_not_an_error():
    t = T.Translator([], {"keep_original": False}, glossary=[])
    r = t.to_english("ఓపెన్ చేయి")
    assert r.translated is False and r.english == "ఓపెన్ చేయి" and "no translator" in r.skipped_reason and r.status == "skipped"


def test_a_backend_that_returns_the_source_script_is_low_confidence():
    b = T.FakeBackend(sentences={"ఓపెన్ చేయి": "ఓపెన్ చేయి"})
    t = T.Translator([b], {"keep_original": False}, glossary=[])
    r = t.to_english("ఓపెన్ చేయి")
    assert r.low_confidence is True and r.confidence <= 0.3


def test_the_second_identical_request_comes_from_the_cache():
    b, t = fake()
    t.to_english("ఓపెన్ నోట్‌ప్యాడ్")
    t.to_english("ఓపెన్ నోట్‌ప్యాడ్")
    assert len(b.calls) == 1 and t.stats["cache_hits"] == 1


# ------------------------------------------------------------------------------------------------------------ local only

class NotLocal(T.Backend):
    name = "online"
    local = False


def test_a_backend_that_is_not_local_is_refused():
    with pytest.raises(T.NotLocalError):
        T.Translator([NotLocal()])
    with pytest.raises(T.NotLocalError):
        T.Translator().add_backend(object())                    # type: ignore[arg-type]


@pytest.mark.parametrize("url", ["https://api.example.com/v1", "http://10.0.0.5:8080/v1", "http://127.0.0.1.evil.com/v1", "http://localhost@evil.com/v1",
                                 "http://user:pw@127.0.0.1:8080/v1", "ftp://127.0.0.1/v1", "http://[::2]/v1", ""])
def test_the_llama_swap_backend_refuses_every_address_that_is_not_plain_loopback(url):
    with pytest.raises(T.TranslateError):
        T.LlamaSwapBackend(url, ("decider",))


class _Upstream(BaseHTTPRequestHandler):
    seen: list = []

    def log_message(self, *a):
        pass

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        _Upstream.seen.append(body)
        user = body["messages"][-1]["content"]
        text = user.split("\n")[1] if user.startswith("<<") else user
        raw = json.dumps({"choices": [{"message": {"content": "<think>x</think>" + ("open " + text if text else "")}}]}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)


def test_no_translation_path_opens_a_socket_to_anything_but_this_machine(monkeypatch):
    """Every connect() made while translating is checked: only 127.0.0.1 / ::1. A proxy in the environment is ignored (urllib would otherwise use it)."""
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _Upstream)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    connects = []
    real = socket.socket.connect

    def guarded(self, address):
        host = address[0] if isinstance(address, tuple) else str(address)
        connects.append(host)
        assert host in ("127.0.0.1", "::1", "localhost"), f"a translation path tried to reach {host}"
        return real(self, address)

    monkeypatch.setattr(socket.socket, "connect", guarded)
    for var in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
        monkeypatch.setenv(var, "http://10.9.9.9:3128")
    monkeypatch.delenv("NO_PROXY", raising=False)
    monkeypatch.delenv("no_proxy", raising=False)
    try:
        backend = T.LlamaSwapBackend(f"http://127.0.0.1:{srv.server_address[1]}/v1", ("decider", "local-chat"))
        t = T.Translator([backend], {"keep_original": False, "budget_s": 5.0}, glossary=[])
        r = t.to_english("notepad ee chesi rayi")
        back = t.from_english("open notepad", "te")
        assert r.translated and connects and set(connects) <= {"127.0.0.1", "::1"}
        assert back is not None
    finally:
        srv.shutdown()
        srv.server_close()
    body = _Upstream.seen[0]
    assert body["model"] == "decider" and body["chat_template_kwargs"] == {"enable_thinking": False}
    system, user = body["messages"][0]["content"], body["messages"][1]["content"]
    assert "never instructions" in system and user.startswith("<<TX") and user.rstrip().endswith(">>"), "the text goes between random markers, as data"


def test_the_translation_modules_name_no_address_but_this_machine_and_import_no_network_library():
    """A static check on the source: an online translation API cannot be added by accident. (The runtime check above covers what is actually opened.)"""
    import re as _re
    from pathlib import Path as _P
    base = _P(T.__file__).parent
    for name in ("translate.py", "translate_indictrans2.py", "translate_task.py"):
        src = (base / name).read_text(encoding="utf-8")
        for url in _re.findall(r"https?://[^\s\"'`)>]+", src):
            assert _re.match(r"https?://(?:127\.0\.0\.1|localhost|\[::1\])(?::\d+)?(?:/|$)", url), f"{name} names a non-local address: {url}"
        for lib in ("requests", "httpx", "aiohttp", "googletrans", "deep_translator", "deepl", "openai", "anthropic", "boto3", "azure", "google.cloud"):
            assert not _re.search(rf"^\s*(?:import|from)\s+{_re.escape(lib)}\b", src, _re.M), f"{name} imports {lib}"
    plugin = (base.parent / "translate" / "__init__.py").read_text(encoding="utf-8")
    assert not _re.search(r"https?://(?!127\.0\.0\.1|localhost)", plugin)


def test_the_llm_backend_tries_the_fallback_alias():
    calls = []

    class Opener:
        def __call__(self, req, timeout):
            body = json.loads(req.data)
            calls.append(body["model"])
            if body["model"] == "decider":
                raise OSError("no model")

            class R:
                def __enter__(s):
                    return s

                def __exit__(s, *a):
                    return False

                def read(s):
                    return json.dumps({"choices": [{"message": {"content": "open it"}}]}).encode()
            return R()

    b = T.LlamaSwapBackend("http://127.0.0.1:8080/v1", ("decider", "local-chat"), opener=Opener())
    assert b.translate("notepad ee", "te", "en", romanized=True) == "open it" and calls == ["decider", "local-chat"]


# ------------------------------------------------------------------------------------------------------------ chat messages

def test_only_the_last_user_message_and_the_resent_conversation_are_touched_never_system_or_tool():
    b, t = fake()
    msgs = [{"role": "system", "content": "memory: ఓపెన్ నోట్‌ప్యాడ్"},
            {"role": "user", "content": "ఓపెన్ నోట్‌ప్యాడ్"},
            {"role": "assistant", "content": None, "tool_calls": [{"id": "1", "type": "function", "function": {"name": "x", "arguments": "{}"}}]},
            {"role": "tool", "tool_call_id": "1", "content": "ఓపెన్ నోట్‌ప్యాడ్ చేయి"},
            {"role": "user", "content": "ఈ ఫైల్ సేవ్ చేయి"}]
    new, res, hist = T.english_messages_ex(msgs, translator=t)
    assert new[0] == msgs[0], "system untouched"
    assert new[3] == msgs[3], "tool result untouched: it is data, never translated into something a model reads as an instruction"
    assert new[2] == msgs[2]
    assert new[4]["content"] == "this file save do" and res.translated
    assert new[1]["content"] == "open notepad", "an earlier owner turn is English too"
    assert msgs[4]["content"] == "ఈ ఫైల్ సేవ్ చేయి", "the caller's list is not modified"
    assert hist["translated"] == 1


def test_a_last_message_that_is_a_tool_result_means_no_new_owner_words():
    b, t = fake()
    msgs = [{"role": "user", "content": "open notepad"}, {"role": "tool", "content": "ఓపెన్ చేయి"}]
    new, res = T.english_messages(msgs, translator=t)
    assert res is None and new == msgs and b.calls == []


def test_a_user_message_given_as_parts_has_only_its_text_parts_translated():
    b, t = fake()
    msgs = [{"role": "user", "content": [{"type": "text", "text": "ఓపెన్ నోట్‌ప్యాడ్"}, {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAAA"}}]}]
    new, res = T.english_messages(msgs, translator=t)
    assert new[0]["content"][0]["text"] == "open notepad" and new[0]["content"][1] == msgs[0]["content"][1]


def test_a_reply_shown_in_telugu_comes_back_as_its_english_for_free_when_the_conversation_is_resent():
    b, t = fake(table={"open": "ఓపెన్"})
    out = t.from_english("Open notepad. Done now.", "te")
    assert out.translated
    n = len(b.calls)
    msgs = [{"role": "user", "content": "hello"}, {"role": "assistant", "content": out.english}, {"role": "user", "content": "thanks"}]
    new, res, hist = T.english_messages_ex(msgs, translator=t)
    assert new[1]["content"] == "Open notepad. Done now." and len(b.calls) == n, "restored from the cache: no model call"


def test_history_that_does_not_fit_the_budget_is_left_as_it_was_and_counted():
    b = T.FakeBackend(table=TE, delay=0.05)
    t = T.Translator([b], {"keep_original": False, "history_budget_s": 0.0}, glossary=[])
    msgs = [{"role": "user", "content": "ఓపెన్ నోట్‌ప్యాడ్"}, {"role": "assistant", "content": "ok"}, {"role": "user", "content": "hello"}]
    new, res, hist = T.english_messages_ex(msgs, translator=t)
    assert new[0]["content"] == "ఓపెన్ నోట్‌ప్యాడ్" and hist["skipped"] == 1 and b.calls == []


# ------------------------------------------------------------------------------------------------------------ the owner's record

def test_the_original_is_kept_for_the_owner_on_this_pc_only_when_something_was_translated(tmp_path):
    b = T.FakeBackend(table=TE)
    t = T.Translator([b], {"keep_original": True}, glossary=[])
    T.translate_to_english("ఓపెన్ నోట్‌ప్యాడ్", translator=t, source="test")
    T.translate_to_english("open notepad", translator=t, source="test")
    rows = [json.loads(ln) for ln in (tmp_path / "translate" / "translations.jsonl").read_text(encoding="utf-8").splitlines()]
    assert len(rows) == 1 and rows[0]["original"] == "ఓపెన్ నోట్‌ప్యాడ్" and rows[0]["english"] == "open notepad" and rows[0]["lang"] == "te"
    t2 = T.Translator([T.FakeBackend(table=TE)], {"keep_original": False}, glossary=[])
    T.translate_to_english("ఈ ఫైల్", translator=t2)
    assert len((tmp_path / "translate" / "translations.jsonl").read_text(encoding="utf-8").splitlines()) == 1


# ------------------------------------------------------------------------------------------------------------ reply language

def test_reply_language_defaults_to_english_and_is_a_session_setting():
    assert T.reply_language() == "en"
    assert T.set_reply_language("te") == "te" and T.reply_language() == "te"
    with pytest.raises(T.TranslateError):
        T.set_reply_language("fr")


@pytest.mark.parametrize("text,want", [
    ("reply in Telugu", "te"), ("Please reply in English.", "en"), ("switch to Hindi", "hi"), ("from now on answer in hindi please", "hi"),
    ("telugu lo cheppu", "te"), ("hindi mein bolo", "hi"), ("తెలుగులో చెప్పు", "te"), ("हिंदी में बोलो", "hi"), ("reply in my language", "match"),
    ("write a reply in English to Bob about the meeting", None), ("translate this to Telugu", None), ("what is Telugu", None), ("open notepad", None), ("", None),
])
def test_the_reply_in_telugu_intent(text, want):
    assert T.reply_language_intent(text) == want


def test_saying_the_intent_changes_the_setting_and_other_words_do_not():
    assert T.apply_reply_language_intent("reply in Telugu") == "te" and T.reply_language() == "te"
    assert T.apply_reply_language_intent("write a reply in English to Bob") is None and T.reply_language() == "te"
    assert T.apply_reply_language_intent("reply in English") == "en" and T.reply_language() == "en"


def test_target_for_reply_follows_the_setting_and_match_follows_the_last_message():
    assert T.target_for_reply("te") == "en"
    T.set_reply_language("hi")
    assert T.target_for_reply("te") == "hi"
    T.set_reply_language("match")
    assert [T.target_for_reply(x) for x in ("te", "hi-Latn", "en", "te-Latn")] == ["te", "hi", "en", "te"]


def test_reply_text_is_english_by_default_and_converted_only_when_asked_and_possible():
    b, t = fake(table={"open": "ఓపెన్", "file": "ఫైల్"})
    assert T.reply_text("open file", "te", translator=t) == ("open file", {"language": "en", "translated": False, "skipped": False})
    T.set_reply_language("te")
    text, meta = T.reply_text("open file", "en", translator=t)
    assert text == "ఓపెన్ ఫైల్" and meta["translated"] and meta["language"] == "te"
    down = T.Translator([], {"keep_original": False}, glossary=[])
    text, meta = T.reply_text("open file", "en", translator=down)
    assert text == "open file" and meta["skipped"] is True, "no translator: the English reply is shown, and the layer says it could not convert"


def test_the_streamed_reply_is_converted_a_finished_sentence_at_a_time_and_tool_arguments_are_not_its_business():
    b, t = fake(table={"open": "ఓపెన్", "Done": "అయింది"})
    st = T.SentenceTranslator("te", t)
    out = st.feed("open it. Do") + st.feed("ne now") + st.flush()
    assert "ఓపెన్" in out and "అయింది" in out and not st.skipped


def test_the_sentence_converter_passes_english_through_and_says_so_when_it_cannot_convert():
    st = T.SentenceTranslator("te", T.Translator([], {}, glossary=[]))
    assert st.feed("One. Two. ") + st.flush() == "One. Two. " and st.skipped is True


def test_voice_is_planned_only_for_a_voice_that_is_installed_and_verified():
    assert T.voice_plan("en", {})["speak"] is True
    assert T.voice_plan("te", {"te": False}, {"tts_verified": {"te": True}})["speak"] is False
    p = T.voice_plan("te", {"te": True}, {"tts_verified": {"te": False}})
    assert p["speak"] is False and "not yet verified" in p["note"]
    assert T.voice_plan("te", {"te": True}, {"tts_verified": {"te": True}})["speak"] is True
    assert "no voice for it is installed" in T.voice_plan("hi", {}, {"tts_verified": {"hi": True}})["note"]


# ------------------------------------------------------------------------------------------------------------ entries and config

def test_entry_text_english_costs_nothing_and_a_doubtful_translation_becomes_a_question():
    b = T.FakeBackend(table=TE, confidence=0.3)
    t = T.Translator([b], {"keep_original": False, "min_confidence": 0.55}, glossary=[])
    T.set_translator(t)
    assert T.entry_text("open notepad") == ("open notepad", None) and b.calls == []
    english, info = T.entry_text("ఓపెన్ నోట్‌ప్యాడ్")
    assert english == "open notepad" and "I understood this as" in info["clarify"]
    english, info = T.entry_text("ఓపెన్ నోట్‌ప్యాడ్", confirmed=True)
    assert "clarify" not in info


def test_entry_text_with_no_translator_returns_the_original_and_a_sentence_for_the_owner():
    T.set_translator(T.Translator([], {"keep_original": False}, glossary=[]))
    english, info = T.entry_text("ఓపెన్ నోట్‌ప్యాడ్")
    assert english == "ఓపెన్ నోట్‌ప్యాడ్" and info["translated"] is False and "could not translate" in T.skipped_note(info)
    assert T.skipped_note(None) == ""


def test_the_shipped_config_has_a_language_section_and_the_translate_kind():
    cfg = T.load_config()
    assert cfg["reply_language"] == "en" and cfg["budget_s"] == 3.0 and cfg["text_backends"] == ["service", "llm"]
    assert cfg["tts_verified"] == {"te": False, "hi": False}, "no Telugu or Hindi voice is spoken until the owner verified it"
    assert T.translate_aliases() == ("decider", "local-chat")
    assert T.load_config("/nonexistent/specialists.json")["budget_s"] == 3.0


def test_a_broken_config_means_the_defaults(tmp_path):
    p = tmp_path / "s.json"
    p.write_text("{not json", encoding="utf-8")
    assert T.load_config(p)["reply_language"] == "en"
    p.write_text(json.dumps({"language": {"reply_language": "klingon", "budget_s": 9}}), encoding="utf-8")
    cfg = T.load_config(p)
    assert cfg["reply_language"] == "en" and cfg["budget_s"] == 9


def test_the_indictrans2_backend_is_simply_not_installed_when_its_folders_are_missing(tmp_path):
    from jarvis.plugins._shared import translate_indictrans2 as I2
    assert I2.build({"indictrans2": {"root": str(tmp_path)}}) is None
    for d in (I2.DIR_IN, I2.DIR_OUT):
        (tmp_path / d).mkdir()
        (tmp_path / d / "translate.py").write_text("# stand-in", encoding="utf-8")
    b = I2.IndicTrans2Onnx(tmp_path, 3, 0, loader=lambda folder: type("M", (), {"translate": lambda self, s, a, t: f"[{a}>{t}] {s}"})())
    assert b.supports("te", "en") and b.supports("hi", "en") and b.supports("en", "te") and not b.supports("te", "en", romanized=True) and not b.supports("te", "hi")
    out = b.translate("ఒకటి. రెండు.", "te", "en")
    assert out == "[tel_Telu>eng_Latn] ఒకటి. [tel_Telu>eng_Latn] రెండు."
    b.translate("hello", "en", "te")
    assert sorted(b._models) == ["in", "out"], "both directions stay loaded: every turn needs both and a switch cost 4.5 s of loading (measured 2026-10-26)"
    one = I2.IndicTrans2Onnx(tmp_path, 3, 0, loader=lambda folder: type("M", (), {"translate": lambda self, s, a, t: s})(), one_at_a_time=True)
    one.translate("x", "te", "en")
    one.translate("hello", "en", "te")
    assert list(one._models) == ["out"], "one_at_a_time: true keeps the old rule"


def test_long_sentences_are_split_for_the_128_token_limit():
    from jarvis.plugins._shared import translate_indictrans2 as I2
    parts = I2.split_sentences(("word " * 120).strip() + ". Short one.")
    assert all(len(p) <= I2.MAX_CHARS_PER_CALL for p in parts) and parts[-1] == "Short one."


def _service_script():
    import importlib.util
    from pathlib import Path as _P
    path = _P(T.__file__).resolve().parents[2] / "scripts" / "translate_server.py"
    spec = importlib.util.spec_from_file_location("translate_server_under_test", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_one_translation_service_serves_every_process_and_refuses_everything_but_loopback(tmp_path):
    """The service holds the model once; ServiceBackend asks it over loopback. A LABELLED FAKE model sits behind the real handler."""
    from jarvis.plugins._shared import translate_indictrans2 as I2
    for d in (I2.DIR_IN, I2.DIR_OUT):
        (tmp_path / d).mkdir()
        (tmp_path / d / "translate.py").write_text("# stand-in", encoding="utf-8")
    model = type("M", (), {"translate": lambda self, s, a, t: f"<{a}>{t}> {s}"})()
    backend = I2.IndicTrans2Onnx(tmp_path, 3, 0, loader=lambda folder: model)
    script = _service_script()
    srv = ThreadingHTTPServer(("127.0.0.1", 0), script.make_handler(backend))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        url = f"http://127.0.0.1:{srv.server_address[1]}/v1"
        svc = T.ServiceBackend(url)
        assert svc.supports("te", "en") and svc.supports("en", "hi") and not svc.supports("te", "en", romanized=True) and not svc.supports("te", "hi")
        t = T.Translator([svc], {"keep_original": False}, glossary=[])
        r = t.to_english("ఓపెన్ నోట్‌ప్యాడ్")
        assert r.translated and r.english.startswith("<tel_Telu>eng_Latn> "), "the service called the model for Telugu to English"
        import urllib.request as _u
        health = json.loads(_u.urlopen(url + "/health", timeout=5).read())
        assert health["ready"] is True and health["requests"] == 1 and health["loaded"] == ["in"]
        bad = _u.Request(url + "/translate", data=json.dumps({"text": "x", "src": "te", "tgt": "hi"}).encode(), method="POST", headers={"Content-Type": "application/json"})
        with pytest.raises(Exception) as e:
            _u.urlopen(bad, timeout=5)
        assert "422" in str(e.value)
        rebinding = _u.Request(url + "/health", headers={"Host": "evil.example"})
        with pytest.raises(Exception) as e2:
            _u.urlopen(rebinding, timeout=5)
        assert "403" in str(e2.value)
    finally:
        srv.shutdown()
        srv.server_close()


def test_the_service_backend_rests_when_the_service_is_not_running_and_the_next_backend_answers():
    svc = T.ServiceBackend("http://127.0.0.1:9/v1")                  # nothing listens on the discard port
    llm = T.FakeBackend(table=TE)
    llm.name = "llm"
    t = T.Translator([svc, llm], {"keep_original": False, "budget_s": 2.0}, glossary=[])
    assert t.to_english("ఓపెన్ నోట్‌ప్యాడ్").backend == "llm"
    n = len(llm.calls)
    t.to_english("ఈ ఫైల్")
    assert len(llm.calls) == n + 1


@pytest.mark.parametrize("url", ["https://translate.example.com/v1", "http://10.1.1.1:48960/v1", "http://localhost@evil.com/v1"])
def test_the_service_backend_refuses_every_address_but_loopback(url):
    with pytest.raises(T.TranslateError):
        T.ServiceBackend(url)


def test_a_config_that_points_a_backend_off_this_machine_builds_no_backend():
    t = T.build_default({"text_backends": ["service", "llm"], "service": {"base_url": "https://translate.example.com/v1"}, "llm": {"base_url": "http://10.0.0.9:8080/v1"}})
    assert t.backends == []
