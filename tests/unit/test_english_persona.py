"""English-only persona: presets, the shared language rule and the card-derived profile-name fact.

A Chinese preset profile, or a prompt that allows "multiple languages", makes the model answer in a Chinese/English mix and
imitate the Chinese lines it finds in its own history.
"""
import os
import re
import sys

import pytest

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '../../')))

from config.prompts.prompts_chara import _L10N
from memory.persona import PersonaManager
from utils import persona_presets as pp

CJK = re.compile(r"[㐀-䶿一-鿿]")
ACTIVE = [p["preset_id"] for p in pp._PRESETS]


@pytest.mark.parametrize("preset_id", ACTIVE)
def test_every_active_preset_has_a_complete_english_profile(preset_id):
    zh = next(p for p in pp._PRESETS if p["preset_id"] == preset_id)["profile"]
    en = pp._PROFILE_EN[preset_id]
    assert len(en) == len(zh) == 7                                   # same seven fields as the Chinese profile
    for label, value in en.items():
        assert not CJK.search(label) and not CJK.search(value), (preset_id, label)
        assert value.strip()


@pytest.mark.parametrize("preset_id", ACTIVE)
def test_applying_a_preset_in_english_writes_an_english_profile(preset_id):
    for lang in ("en", "en-US"):
        payload = pp.build_persona_override_payload(preset_id, lang=lang)
        assert payload["profile"] == pp._PROFILE_EN[preset_id]
        assert not CJK.search(" ".join([*payload["profile"], *payload["profile"].values()]))
    assert payload["profile"] is not pp._PROFILE_EN[preset_id]        # a copy: callers cannot corrupt the table


def test_other_languages_keep_the_original_profile():
    zh = pp.build_persona_override_payload("sharp_tongued_junior", lang="zh")
    assert "性格" in zh["profile"] and CJK.search(zh["profile"]["性格"])
    ja = pp.build_persona_override_payload("sharp_tongued_junior", lang="ja")
    assert "性格" in ja["profile"]


def test_the_english_prompt_says_english_only_everywhere():
    assert "multiple languages" not in _L10N["en"]["language_style"]
    assert "English only" in _L10N["en"]["language_style"]
    for preset_id in ACTIVE:
        guidance = pp.get_persona_prompt_guidance(preset_id, "en")
        assert "Speak English only" in guidance, preset_id
        assert "multiple languages" not in guidance and "日本語" not in guidance, preset_id


def _facts_for(monkeypatch, lang, pinned=None):
    monkeypatch.setattr("utils.language_utils.get_global_language_full", lambda *a, **k: lang)
    pm = object.__new__(PersonaManager)
    persona = {}
    master = {"档案名": "Boss", "Gender": "Male"}
    pm._apply_character_card_sync("YUI", persona, master, {"YUI": {"Nickname": "YUI"}}, locale=pinned)
    return {f["text"] for f in persona["master"]["facts"]}


def test_the_profile_name_fact_is_labelled_in_english_not_with_the_internal_key(monkeypatch):
    texts = _facts_for(monkeypatch, "en")
    assert "Profile name: Boss" in texts
    assert not any(CJK.search(t) for t in texts)


def test_other_languages_keep_the_original_label(monkeypatch):
    assert "档案名: Boss" in _facts_for(monkeypatch, "zh")


def test_the_label_follows_the_cards_pinned_locale_not_the_live_global_language(monkeypatch):
    # global language flipped to Chinese, but this card is pinned to English: the sync must not change the label (idempotent)
    assert "Profile name: Boss" in _facts_for(monkeypatch, "zh", pinned="en")
    assert "\u6863\u6848\u540d: Boss" in _facts_for(monkeypatch, "en", pinned="zh-CN")
