"""D54 (2026-10-26): screen_text.release_for_request - the text of ONE window for ONE explicit request of the owner ("solve what is on the screen").
It ignores JARVIS_SCREEN_TEXT_TO_MODEL (the request is the permission) and keeps every other rule: the secret pass, and the privacy gateway's
confirmed redaction or nothing. The switch itself, and release() for every other path, are unchanged. SYNTHETIC canaries."""
import re
from pathlib import Path

import pytest

from jarvis.plugins._shared import screen_text as st
from jarvis.plugins._shared.tests import fakes
from jarvis.plugins._shared.tests.fakes import C_EMAIL, C_FILE, C_KEY, C_OCR, C_PATH, C_STREET, NOT_CLEARED, leaked

TEXT = f"Problem for {C_OCR}: {C_PATH} {C_EMAIL} {C_STREET} key={C_KEY}. Write a program."


@pytest.fixture
def gateways(monkeypatch):
    g = fakes.Gateways(monkeypatch)
    yield g
    g.close()


@pytest.fixture(autouse=True)
def _flag_off(monkeypatch):
    monkeypatch.delenv("JARVIS_SCREEN_TEXT_TO_MODEL", raising=False)


def test_with_the_switch_off_the_request_still_clears_the_text_through_the_gateway(gateways):
    gw = gateways.make()
    got = st.release_for_request(TEXT)
    assert got.allowed and got.via == "gateway" and leaked(got.text) == [] and "[PERSON_1]" in got.text and "Write a program" in got.text
    assert len(gw.redact_requests) == 1 and gw.redact_requests[0]["names"] is True
    assert C_KEY not in gw.redact_requests[0]["text"] and C_FILE not in gw.redact_requests[0]["text"], "the secret pass and the path reduction ran first"


def test_the_switch_and_release_for_every_other_path_are_unchanged(gateways):
    gw = gateways.make()
    assert st.flag_on() is False
    got = st.release(TEXT)
    assert not got.allowed and got.via == "withheld" and got.text == ""
    assert st.release(TEXT, model_url="http://127.0.0.1:8080/v1").via == "withheld"
    assert gw.redact_requests == [], "release() never asked the gateway with the switch off"
    st.release_for_request(TEXT)
    assert st.flag_on() is False


@pytest.mark.parametrize("name,setup", NOT_CLEARED[:5], ids=[n for n, _ in NOT_CLEARED[:5]])
def test_when_redaction_cannot_be_confirmed_nothing_is_released_and_the_note_has_no_text(gateways, monkeypatch, name, setup):
    setup(gateways, monkeypatch)
    got = st.release_for_request(TEXT)
    assert not got.allowed and got.via == "withheld" and got.text == "" and leaked(got.note) == []
    assert "not sent" in got.note or "was not sent" in got.note


def test_a_private_key_is_withheld_whole_and_the_gateway_is_not_asked(gateways):
    gw = gateways.make()
    got = st.release_for_request("-----BEGIN OPENSSH PRIVATE KEY-----\nxyz\n-----END OPENSSH PRIVATE KEY-----")
    assert not got.allowed and gw.redact_requests == [] and gw.health_requests == 0


def test_an_empty_window_is_nothing_to_send(gateways):
    got = st.release_for_request("   ")
    assert got.allowed and got.via == "none" and got.text == ""


def test_a_failure_inside_the_helper_withholds_and_never_returns_the_raw_text(gateways, monkeypatch):
    gateways.make()

    def boom(*a, **k):
        raise RuntimeError("holding " + C_OCR)
    monkeypatch.setattr(st, "gateway_redact", boom)
    got = st.release_for_request(TEXT)
    assert not got.allowed and got.text == "" and leaked(got.note) == []


def test_only_the_goal_runners_solve_step_calls_it():
    """The explicit-request path must not become a second way out: nothing but screen_text.py itself and the solve path of goal.py (and tests) names it."""
    root = Path(st.__file__).resolve().parents[2]
    users = []
    for p in root.rglob("*.py"):
        parts = p.parts
        if "tests" in parts or p.name.startswith("test_") or "node_modules" in parts or "face" in parts or "evals" in parts:
            continue
        if re.search(r"\brelease_for_request\b", p.read_text(encoding="utf-8", errors="replace")):
            users.append(p.relative_to(root).as_posix())
    assert sorted(users) == ["plugins/_shared/screen_text.py", "plugins/desktop/goal.py"], users
