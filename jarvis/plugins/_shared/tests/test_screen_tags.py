"""D73: the plugin side of reversible screen text. The gateway hands back tags ([PERSON_1], [NUMBER_2]) that the owner's display turns into the real
values; nothing on the plugin side may cut a tag in half, lose the hint that tells the model to copy a tag, or let the final-answer guard mistake a tag for
an empty answer. SYNTHETIC text only."""
import json

import pytest

from jarvis.plugins._shared import final_answer
from jarvis.plugins._shared import screen_text as st
from jarvis.plugins._shared.tests import fakes


@pytest.fixture
def gateways(monkeypatch):
    g = fakes.Gateways(monkeypatch)
    yield g
    g.close()


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    monkeypatch.delenv("JARVIS_SCREEN_TEXT_TO_MODEL", raising=False)


def test_the_plugin_asks_the_gateway_for_a_reversible_redaction(gateways):
    gw = gateways.make()
    st.gateway_redact("some text")
    assert gw.redact_requests[0]["restorable"] is True and gw.redact_requests[0]["names"] is True


def test_the_hint_is_added_only_when_the_text_holds_tags(gateways, monkeypatch):
    monkeypatch.setenv("JARVIS_SCREEN_TEXT_TO_MODEL", "1")
    gateways.make(swap={"Priyanka": "[PERSON_1]"})
    tagged = st.release("Dear Priyanka")
    plain = st.release("Dear friend")
    assert tagged.allowed and "[PERSON_1]" in tagged.text and "copy a tag exactly" in tagged.note and "say that you hid a secret" in tagged.note
    assert plain.allowed and plain.note == st.CLEARED_NOTE
    assert st.with_tag_hint("n", "x [REDACTED:password]") != "n" and st.with_tag_hint("n", "x [card number removed]") != "n"
    assert st.with_tag_hint("n", "[1] [x] [see below]") == "n" and st.with_tag_hint("n", "") == "n"


def test_a_cut_never_ends_inside_a_tag():
    long = "a" * 2995 + " [PERSON_12] tail"
    assert st.cut_text(long, 2999) == "a" * 2995 + " "
    assert st.cut_text(long, 3010) == "a" * 2995 + " [PERSON_12] ta"
    assert st.cut_text("[link", 3) == "" and st.cut_text("short", 99) == "short"
    assert st.cut_text("x" * 100 + "[" + "y" * 80, 150) == "x" * 100 + "[" + "y" * 49, "a bracket far from the end is not a tag"


def test_the_cleaner_does_not_split_a_tag_and_keeps_our_marks():
    text = "word " * 20 + "[PERSON_1] and [NUMBER_2]"
    out = st.clean_message(text, limit=len("word " * 20) + 5)          # the limit falls inside "[PERSON_1]"
    assert out == "word " * 20 and "[PERSON" not in out, out
    kept = "Wifi is [REDACTED:password] for [PERSON_1], order [NUMBER_2], card [card number removed]"
    assert st.clean_message(kept, limit=300) == kept


def test_a_screen_result_keeps_its_tags_through_the_plugin_boundary():
    res = st.clean_result({"ok": True, st.SCREEN_KEY: True, "text": "Order [NUMBER_1] by [PERSON_1]\nPassword [REDACTED:password]",
                           "text_note": st.CLEARED_NOTE + st.TAG_HINT})
    assert res["text"] == "Order [NUMBER_1] by [PERSON_1] Password [REDACTED:password]"
    assert "[PERSON_1]" in res["text_note"] and "[REDACTED:kind]" in res["text_note"]
    assert json.loads(json.dumps(res)) == res


def test_the_final_answer_guard_keeps_the_tags_it_is_given():
    fx = [final_answer.Effect("read the Notepad window", verified=True)]
    said = "The order is [NUMBER_1], sent by [PERSON_1]. [REDACTED:password] was hidden."
    got = final_answer.guard(said, final_answer.COMPLETED, fx)
    assert got.source == final_answer.MODEL and got.text == said and got.reasons == []
    assert not final_answer.is_empty("[NUMBER_1]") and not final_answer.is_empty("[REDACTED:credit_card]")
    assert final_answer.guard(said, final_answer.COMPLETED, []).text == said, "a chat answer with no effects is only checked for empty and leaks"


def test_the_final_answer_guard_does_not_cut_a_tag_in_half():
    assert final_answer._cut("b" * 695 + "[NUMBER_3] end", 700) == "b" * 695
    said = "x " * 340 + "see [PERSON_1] and more words after the tag " * 3
    out = final_answer.guard(said, final_answer.COMPLETED, []).text
    assert len(out) <= final_answer.MAX_TEXT and out.count("[") == out.count("]")
    reply = "r " * 348 + "[PERSON_1] tail"
    done = final_answer.answer_for_task("done", reply, detailed=True)
    assert len(done) <= final_answer.MAX_TEXT and done.count("[") == done.count("]")
