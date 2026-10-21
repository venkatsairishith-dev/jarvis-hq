"""The shared screen-text helper: the one rule for what may leave this PC (see plugins/_shared/screen_text.py). SYNTHETIC canaries."""
import pytest

from plugin.plugins._shared import screen_text as st
from plugin.plugins._shared.tests import fakes
from plugin.plugins._shared.tests.fakes import C_EMAIL, C_FILE, C_KEY, C_OCR, C_PATH, C_STREET, leaked

LOCAL = "http://127.0.0.1:8080/v1"
TEXT = f"Dear {C_OCR}, {C_PATH} {C_EMAIL} {C_STREET} key={C_KEY}"


@pytest.fixture
def gateways(monkeypatch):
    g = fakes.Gateways(monkeypatch)
    yield g
    g.close()


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    monkeypatch.delenv("NEKO_SCREEN_TEXT_TO_MODEL", raising=False)
    monkeypatch.delenv("JARVIS_LOCAL_LLM", raising=False)


def test_flag_off_withholds_everything_even_for_a_local_model(gateways):
    gw = gateways.make()
    for url in (None, LOCAL):
        got = st.release(TEXT, model_url=url)
        assert not got.allowed and got.via == "withheld" and got.text == "" and leaked(got.note) == []
    assert gw.redact_requests == [] and gw.health_requests == 0                  # the gateway is not even asked


def test_a_verified_local_model_gets_the_text_after_the_secret_pass_and_the_gateway_is_not_used(monkeypatch, gateways):
    monkeypatch.setenv("NEKO_SCREEN_TEXT_TO_MODEL", "1")
    gateways.down()
    got = st.release(TEXT, model_url=LOCAL)
    assert got.allowed and got.via == "local" and C_OCR in got.text and C_KEY not in got.text and "[provider_key removed]" in got.text


@pytest.mark.parametrize("url", ["https://inference.dahl.global/v1", "http://127.0.0.1:48950/v1", "http://10.1.2.3:8080/v1",
                                 "http://127.0.0.1.evil.invalid:8080/v1", "http://u:p@127.0.0.1:8080/v1", "", "not a url"])
def test_only_the_loopback_model_is_local_everything_else_must_be_cleared(monkeypatch, gateways, url):
    monkeypatch.setenv("NEKO_SCREEN_TEXT_TO_MODEL", "1")
    gw = gateways.make()
    got = st.release(TEXT, model_url=url)
    assert got.via == "gateway" and leaked(got.text) == [] and len(gw.redact_requests) == 1


def test_the_gateway_port_can_never_be_the_local_model(monkeypatch, gateways):
    monkeypatch.setenv("NEKO_SCREEN_TEXT_TO_MODEL", "1")
    gw = gateways.make()
    monkeypatch.setenv("JARVIS_LOCAL_LLM", f"http://127.0.0.1:{gw.port}/v1")       # a misconfiguration
    assert st.release(TEXT, model_url=f"http://127.0.0.1:{gw.port}/v1").via == "gateway"


def test_no_model_given_means_the_gateway_path(monkeypatch, gateways):
    monkeypatch.setenv("NEKO_SCREEN_TEXT_TO_MODEL", "1")
    gw = gateways.make()
    got = st.release(TEXT)
    assert got.allowed and got.via == "gateway" and leaked(got.text) == [] and "[PERSON_1]" in got.text
    asked = gw.redact_requests[0]["text"]
    assert gw.redact_requests[0]["names"] is True and C_KEY not in asked
    assert C_PATH not in asked and C_FILE not in asked and C_EMAIL not in asked and "<a .xlsx file>" in asked   # what the gateway cannot hide


@pytest.mark.parametrize("why,setup", fakes.NOT_CLEARED, ids=[w for w, _ in fakes.NOT_CLEARED])
def test_whatever_fails_the_text_is_withheld_and_never_the_fallback(monkeypatch, gateways, why, setup):
    monkeypatch.setenv("NEKO_SCREEN_TEXT_TO_MODEL", "1")
    setup(gateways, monkeypatch)
    got = st.release_safely(TEXT)
    assert not got.allowed and got.text == "" and got.note == st.WITHHELD and leaked(got.note) == [], why


def test_a_private_key_is_withheld_whole_even_for_a_local_model(monkeypatch):
    monkeypatch.setenv("NEKO_SCREEN_TEXT_TO_MODEL", "1")
    got = st.release("-----BEGIN OPENSSH PRIVATE KEY-----\nb3BlbnNzaC1rZXk", model_url=LOCAL)
    assert not got.allowed and "private key" in got.note


def test_release_safely_distrusts_a_helper_that_returns_garbage(monkeypatch):
    monkeypatch.setattr(st, "release", lambda *a, **k: "the raw text")
    assert st.release_safely(TEXT) == st.Released("", False, "withheld", st.WITHHELD)
    monkeypatch.setattr(st, "release", lambda *a, **k: st.Released(TEXT, False, "gateway"))      # not allowed, not withheld: refused too
    assert st.release_safely(TEXT).text == ""


def test_empty_text_has_nothing_to_release():
    assert st.release("  ") == st.Released("", True, "none")


# ---- the gateway contract is the research plugin's, not a second opinion
HEALTHS = [dict(fakes.READY), {"ok": True, "ready": False, "degraded": ["pii_model_missing"], "pii_model": False},
           {"ok": True, "pii_model": True}, {"ok": True, "ready": True, "pii_model": False}, {"ok": False, "ready": True, "pii_model": True},
           ["not", "a", "gateway"], "ok"]
ANSWERS = [dict(fakes.CONFIRMED), {"names": True, "deep": True, "ready": False}, {"names": False, "deep": True, "ready": True},
           {"names": True, "deep": False, "ready": True}, {"names": "yes", "deep": True}, {}]


@pytest.mark.parametrize("health", HEALTHS)
def test_readiness_agrees_with_the_research_plugins_check(monkeypatch, gateways, health):
    from plugin.plugins import research as r
    g = gateways.make(health=health)
    monkeypatch.setattr(r, "GATEWAY_HEALTH", f"http://127.0.0.1:{g.port}/health")
    assert st.gateway_state()[0] is r._gateway_state()[0]


@pytest.mark.parametrize("answer", ANSWERS)
def test_redaction_acceptance_agrees_with_the_research_plugins_check(monkeypatch, gateways, answer):
    from plugin.plugins import research as r
    g = gateways.make(redact_extra=answer)
    monkeypatch.setattr(r, "GATEWAY_REDACT", f"http://127.0.0.1:{g.port}/redact")

    def ok(fn):
        try:
            fn("some text")
            return True
        except Exception:  # noqa: BLE001
            return False
    assert ok(st.gateway_redact) is ok(r._redact)


# ---- file and directory names
def test_a_file_is_only_ever_its_class():
    assert st.file_class(C_PATH) == "a .xlsx file" and st.file_class(C_FILE) == "a .xlsx file"
    assert st.file_class("/home/synth/notes") == "a folder or file" and st.file_class("") == "a folder or file"
    assert leaked(st.file_class(C_PATH)) == []


def test_paths_and_file_names_in_free_text_are_reduced_without_touching_urls_or_prose():
    out = st.neutral_paths(f"cannot write {C_PATH}: see {C_FILE} and Documents\\Taxes\\2026\\a.xlsx. Open http://127.0.0.1:8799 and and/or")
    assert leaked(out) == [] and "Taxes" not in out and "http://127.0.0.1:8799" in out and "and/or" in out
    assert out.count("<a .xlsx file>") == 3


def test_messages_lose_secrets_paths_and_emails_and_are_cut():
    out = st.clean_message(f"{C_PATH} {C_KEY} {C_EMAIL} " + "x" * 500, 100)
    assert leaked(out) == [] and len(out) <= 100


def test_metadata_keeps_numbers_urls_and_identity_emails_but_loses_paths():
    meta = st.sanitize_meta({"title": f"{C_FILE} - Excel", "url": f"https://example.com/{C_FILE}", "seen": ["a@b.example.invalid"], "count": 3,
                             "rows": [{"path": C_PATH, "ok": True}]})
    assert meta["title"] == "<a .xlsx file> - Excel" and meta["url"].endswith(C_FILE)       # addresses are left as they are
    assert meta["seen"] == ["a@b.example.invalid"] and meta["count"] == 3 and meta["rows"][0] == {"path": "<a .xlsx file>", "ok": True}


def test_logs_may_say_how_long_a_text_was_and_nothing_else():
    assert st.length_only(TEXT) == f"{len(TEXT)} characters" and leaked(st.length_only(TEXT)) == []


# ---- the REAL gateway code and the REAL Presidio + spaCy recogniser (skipped without them; see research/tests/test_gateway_required.py)
class TestRealGateway:
    """Run with PYTHONPATH=<dir holding presidio_analyzer, presidio_anonymizer, spacy, en_core_web_sm>. Only the GLiNER model is a
    labelled stub (not installable here). What the real recogniser does NOT hide is pinned in the owner-facing docs, not here: a
    model's misses (an unusual name, a street) are not a property to assert."""

    def _serve(self, monkeypatch):
        import threading
        from http.server import ThreadingHTTPServer
        from plugin.plugins.research.tests.test_gateway_required import NoEntityModel, _real_privacy
        gw, red = _real_privacy()
        try:
            redactor = red.Redactor(names=False, model=NoEntityModel(), verifier=None)
        except OSError:
            pytest.skip("spaCy model en_core_web_sm is not installed")
        handler = gw.make_handler(redactor, "http://127.0.0.1:1/v1", 5, require_deep=True)
        handler.log_message = lambda *a, **k: None
        srv = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        monkeypatch.setenv("JARVIS_PRIVACY_PORT", str(srv.server_address[1]))
        monkeypatch.setenv("NEKO_SCREEN_TEXT_TO_MODEL", "1")
        return srv

    def test_names_cards_and_keys_are_hidden_and_files_and_emails_are_reduced_before_the_gateway(self, monkeypatch):
        srv = self._serve(monkeypatch)
        try:
            got = st.release(f"Dear John Smith,\ncall Maria Garcia\nmail john.smith@gmail.com\ncard 4111 1111 1111 1111\nfile {C_PATH}\nkey {C_KEY}")
            assert got.via == "gateway" and got.allowed
            for gone in ("John Smith", "Maria Garcia", "john.smith@gmail.com", "4111 1111 1111 1111", "Salary-2026", "SYNTH-Owner", C_KEY):
                assert gone not in got.text, gone
            assert "<a .xlsx file>" in got.text
        finally:
            srv.shutdown()
            srv.server_close()

    def test_a_real_gateway_without_its_pii_model_withholds(self, monkeypatch):
        import threading
        from http.server import ThreadingHTTPServer
        from plugin.plugins.research.tests.test_gateway_required import _real_privacy
        gw, red = _real_privacy()
        try:
            redactor = red.Redactor(names=False, model=None, verifier=None)
        except OSError:
            pytest.skip("spaCy model en_core_web_sm is not installed")
        handler = gw.make_handler(redactor, "http://127.0.0.1:1/v1", 5, require_deep=True)
        handler.log_message = lambda *a, **k: None
        srv = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        monkeypatch.setenv("JARVIS_PRIVACY_PORT", str(srv.server_address[1]))
        monkeypatch.setenv("NEKO_SCREEN_TEXT_TO_MODEL", "1")
        try:
            got = st.release_safely(TEXT)
            assert not got.allowed and got.text == "" and leaked(got.note) == []
        finally:
            srv.shutdown()
            srv.server_close()


def test_the_question_card_text_is_kept_but_cleaned():
    """2026-10-21: a task's clarifying question (input_request.prompt) was replaced by the 'text stayed on this PC' note, so the owner
    saw an empty card. It is a sentence the plugin wrote for the owner: kept, with paths and keys removed like `question`."""
    bs = chr(92)
    path = "C:" + bs + "Users" + bs + "x" + bs + "Secret Plans.docx"
    out = st.clean_result({"input_request": {"title": "Clarification", "prompt": "Which report do you mean? I found " + path,
                                              "fields": [{"name": "clarification", "label": "Your answer"}]}})
    prompt = out["input_request"]["prompt"]
    assert prompt.startswith("Which report do you mean?")
    assert "Secret Plans" not in prompt and "Users" not in prompt
    assert st.OFF_NOTE not in prompt
