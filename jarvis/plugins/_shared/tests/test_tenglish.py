"""tenglish: the deterministic verb lexicon and rules that run BEFORE a model (D59, lever c)."""
import pytest

from jarvis.plugins._shared import tenglish as T


def eng(text):
    return T.parse(text).english


def test_lexicon_has_no_collisions_and_covers_the_owners_verbs():
    for word in ("open", "type", "save", "delete", "close", "show", "play", "search", "rayi", "rayandi", "chesi", "teruvu", "tegu", "vesey", "pettu"):
        assert T.skel(word) in T._VERBS or T.skel(word) in T._CONJ or T.skel(word) in T._AUX, word
    assert T._VERBS[T.skel("tegu")][1] < 0.85                       # the owner's uncertain word can only ever produce a question


@pytest.mark.parametrize("a,b", [("rayi", "raayi"), ("rayandi", "raayandi"), ("teruvu", "teruvvu"), ("kholo", "kholo"), ("chupinchu", "chupincu"),
                                  ("teesey", "teeseyy"), ("cheppu", "ceppu")])
def test_spelling_variants_share_a_skeleton(a, b):
    assert T.skel(a) == T.skel(b)


def test_the_owner_example_telugu_verb_last_with_chesi():
    p = T.parse("Notepad open chesi milk, eggs, bread, rice rayandi")
    assert p.english == "Open Notepad and type: milk, eggs, bread, rice." and p.confidence == 0.95 and p.language == "te-rom"
    assert p.intents == ["type"]                                   # "open X" + "type ..." is one step with the app named


def test_verb_last_with_postposition_lo():
    assert eng("Notepad lo shopping list rayi") == "Open Notepad and type: shopping list."
    assert eng("Notepad lo shopping list raayi") == "Open Notepad and type: shopping list."


def test_two_commands_joined_by_chesi_or_and():
    assert eng("Calculator teruvu, Paris weather cheppu") == "Open Calculator and then tell me the weather in Paris."
    assert eng("Calculator open chesi Paris weather cheppu") == "Open Calculator and then tell me the weather in Paris."
    assert eng("kholo calculator aur batao paris mausam") == "Open Calculator and then tell me the weather in Paris."


def test_english_order_still_parses():
    assert eng("Open Notepad and type hello") == "Open Notepad and type: hello."
    assert eng("open notepad type hello world") == "Open Notepad and type: hello world."


def test_quoted_text_is_typed_verbatim():
    assert eng('notepad kholo aur likho "dudh, anda, bread"') == "Open Notepad and type: dudh, anda, bread."
    assert eng('Notepad lo "Hello Anu" rayi') == "Open Notepad and type: Hello Anu."


def test_delete_close_show_search():
    assert eng("ee file ni delete cheyyi") == "Delete this file."
    assert eng("chrome ni muyyi") == "Close Chrome."
    assert eng("report.pdf chupinchu") == "Show report.pdf."
    assert eng("weather news vetuku") == "Search for weather news."
    assert eng("ye file hatao") == "Delete this file."


def test_vesey_and_pettu_depend_on_the_other_words():
    assert eng("ee file ni recycle bin lo veyyi") == "Move this file to the Recycle Bin."
    assert eng("youtube lo Believer song pettu") == "Play Believer song on YouTube."
    p = T.parse("Believer song vesey")
    assert p.english == "Play Believer song." and p.confidence >= 0.9
    lone = T.parse("alarm pettu")                                   # put / set / play: not decided by the words
    assert lone.confidence <= 0.5


def test_tegu_only_ever_asks():
    p = T.parse("tegu report.txt")
    assert p.intents == ["delete"] and p.confidence == 0.6


def test_a_verb_without_its_object_is_low_confidence():
    for phrase in ("delete", "ee delete cheyyi", "play", "search cheyyi"):
        assert T.parse(phrase).confidence <= 0.5, phrase


def test_one_letter_off_a_long_verb_is_a_low_confidence_reading():
    assert T.parse("tolagimchu report").confidence >= 0.9            # exact
    assert T.parse("tolagimchuu reportt").confidence >= 0.9          # repeated letters collapse: still exact
    off = T.parse("deleet report")                                   # one letter missing: a reading, never above 0.7
    assert off.intents == ["delete"] and off.confidence == 0.7


def test_unknown_sentence_is_not_a_command():
    p = T.parse("hello how are you")
    assert p.steps == [] and p.confidence == 0.0


def test_native_script_telugu():
    p = T.parse("నోట్ప్యాడ్ తెరిచి పాలు గుడ్లు రాయండి")
    assert p.english == "Open Notepad and type: పాలు గుడ్లు." and p.language == "te" and p.confidence == 0.9
    assert T.parse("నోట్‌ప్యాడ్‌లో పాలు రాయండి").english == "Open Notepad and type: పాలు."      # with the joiner letters and "lo" glued on
    assert T.parse("కాలిక్యులేటర్ తెరవండి").english == "Open Calculator."


def test_native_script_hindi():
    assert T.parse("कैलकुलेटर खोलो").english == "Open Calculator."
    assert T.parse('नोटपैड खोलो और "दूध" लिखो').english == "Open Notepad and type: दूध."
    assert T.parse("फ़ाइल हटाओ").intents == ["delete"]


def test_native_words_left_in_a_place_name_lower_the_confidence_and_are_listed():
    p = T.parse("पेरिस का मौसम बताओ")
    assert p.english == "Tell me the weather in पेरिस." and p.residue == ["पेरिस"] and p.confidence < 0.85


def test_detect_languages():
    assert T.detect("Open Notepad and type hello").language == "en"
    assert T.detect("Notepad open chesi milk rayandi").language == "te-rom"
    assert T.detect("kholo calculator").language == "hi-rom"
    assert T.detect("నోట్ప్యాడ్ తెరువు").language == "te" and T.detect("कैलकुलेटर खोलो").language == "hi"
    assert T.detect("hello ka ke ko").language == "en"                # single weak function words do not make it Hindi... but 'ka ke ko' are not in the Hindi set
    assert T.detect("telugu తెలుగు hindi हिन्दी").language == "mixed"


def test_plain_english_words_that_look_like_lexicon_entries_stay_english():
    assert T.detect("do the thing then open it").language == "en"


def test_cli_prints_json(capsys):
    T.main(["kholo calculator"])
    assert '"english": "Open Calculator."' in capsys.readouterr().out
