"""Regression tests added after mutation testing of screen_text.py (2026-10-19): the length range of a removed card number, the
longest id, and the type that release() returns when the privacy gateway is not ready."""
from __future__ import annotations

from jarvis.plugins._shared import screen_text as st


def luhn_number(length: int, prefix: str = "4") -> str:
    """A synthetic digit string of `length` digits that passes the Luhn check."""
    body = prefix + "1" * (length - len(prefix) - 1)
    for check in "0123456789":
        if st._luhn(body + check):
            return body + check
    raise AssertionError("no check digit")


def test_card_numbers_of_13_to_19_digits_are_removed_and_other_lengths_kept():
    for n in (13, 14, 15, 16, 17, 18, 19):
        num = luhn_number(n)
        assert len(num) == n and st._luhn(num)
        text, hits = st.scrub_text(f"pay with {num} today")
        assert text == "pay with [card number removed] today", n
        assert "card_number" in hits
    short = luhn_number(12)
    assert st.scrub_text(f"ref {short} ok") == (f"ref {short} ok", [])
    # grouped the way a card is written
    grouped = luhn_number(16)
    grouped = " ".join(grouped[i:i + 4] for i in range(0, 16, 4))
    assert st.scrub_text(f"card {grouped}.")[0] == "card [card number removed]."
    # a 19-digit run that fails the Luhn check is not a card
    bad = luhn_number(19)
    bad = bad[:-1] + str((int(bad[-1]) + 1) % 10)
    assert st.scrub_text(f"n {bad}")[0] == f"n {bad}"


def test_the_longest_known_id_is_80_characters():
    ok = "task-" + "a" * 75
    assert len(ok) == 80 and st.is_known_id(ok)
    assert not st.is_known_id("task-" + "a" * 76)
    assert st.is_known_id("20261019-121346-f83eca") and not st.is_known_id("20261019-121346-f83eca extra")
    assert not st.is_known_id("")


def test_release_returns_a_withheld_release_object_when_the_gateway_is_not_ready(monkeypatch):
    monkeypatch.setenv(st.FLAG, "1")
    monkeypatch.setattr(st, "gateway_state", lambda: (False, "URLError"))
    out = st.release("some screen text with a name Priyanka")
    assert isinstance(out, st.Released)
    assert (out.text, out.allowed, out.via) == ("", False, "withheld")


def test_release_withholds_and_still_returns_a_release_object_when_anything_inside_it_raises(monkeypatch):
    monkeypatch.setenv(st.FLAG, "1")

    def boom(text):
        raise ValueError("secret pass broke")
    monkeypatch.setattr(st, "scrub_text", boom)
    out = st.release("Dear Priyanka, here is the screen")
    assert isinstance(out, st.Released)
    assert (out.text, out.allowed, out.via) == ("", False, "withheld")
