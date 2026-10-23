"""The face's built-in character prompt is the JARVIS card, rendered by jarvis/persona/face_prompt.py."""
from __future__ import annotations

import json

import pytest

from jarvis.persona import face_prompt as P

CARD = json.loads(P.CARD.read_text(encoding="utf-8"))


def test_the_face_prompt_matches_the_card():
    assert P.main(["--check"]) == 0


def test_placeholders_are_filled_and_sentences_start_with_a_capital():
    text = P.render(CARD)
    assert "{" not in text and "JARVIS" in text and "the owner" in text
    assert "\nthe owner" not in text and ". the owner" not in text


def test_vue_i18n_special_characters_are_escaped():
    assert P.escape_i18n("a {b} c@d $e | f") == "a {'{'}b{'}'} c{'@'}d {'$'}e {'|'} f"


def test_an_unknown_placeholder_is_refused():
    with pytest.raises(ValueError):
        P.render({"system_prompt": "{LANLAN_NAME} meets {SOMEONE}"})


def test_only_the_prefix_block_is_replaced():
    yaml_text = "prompt:\n  prefix: |\n    old\n\n  emotion: |\n    keep\n"
    out = P.replace_prefix(yaml_text, P.yaml_block("new line"))
    assert out == "prompt:\n  prefix: |\n    new line\n\n  emotion: |\n    keep\n"
