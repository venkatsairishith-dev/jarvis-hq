"""language_gate: act on a confident reading, ask 'Did you mean ...?' on a doubtful one. Fake translators only (D59, lever c)."""
from jarvis.plugins._shared import ask_form, language_gate as G


class Translator:
    def __init__(self, answer, conf=None, raises=False):
        self.answer, self.conf, self.raises, self.calls = answer, conf, raises, 0

    def __call__(self, text):
        self.calls += 1
        if self.raises:
            raise RuntimeError("model down")
        return (self.answer, self.conf) if self.conf is not None else self.answer


def test_english_passes_through_untouched_and_never_calls_the_model():
    t = Translator("x")
    d = G.decide("Open Notepad and write a shopping list", model_translate=t)
    assert d.action == G.PASS and d.english == "Open Notepad and write a shopping list" and t.calls == 0


def test_confident_rules_act_before_the_model_is_asked():
    t = Translator("something else", 0.99)
    d = G.decide("Notepad open chesi milk, eggs, bread, rice rayandi", model_translate=t)
    assert d.action == G.ACT and d.source == "rules" and d.english == "Open Notepad and type: milk, eggs, bread, rice." and t.calls == 0


def test_native_script_with_an_english_payload_acts_on_rules():
    d = G.decide("कैलकुलेटर खोलो")
    assert d.action == G.ACT and d.english == "Open Calculator." and d.language == "hi"


def test_unknown_words_with_no_translator_ask():
    d = G.decide("anna oka pani cheyyi")                      # "brother, do a thing": no verb the lexicon knows
    assert d.action == G.ASK and ask_form.is_form(d.form) and "no translator" in " ".join(d.notes)


def test_model_translation_without_a_confidence_is_not_trusted_on_its_own():
    d = G.decide("అన్నా ఈ పని చెయ్యి", model_translate=Translator("Brother, do this task"))
    assert d.action == G.ASK and d.form["question"].startswith("Did you mean: Brother, do this task")


def test_model_translation_with_high_confidence_acts():
    d = G.decide("అన్నా ఈ పని చెయ్యి", model_translate=Translator("Open the calendar", 0.93))
    assert d.action == G.ACT and d.source == "model" and d.english == "Open the calendar"


def test_model_and_rules_agreeing_raise_confidence_even_without_a_score():
    # rules find the verb "show" but the object has native residue (a place name): rules alone are not complete
    d = G.decide("పారిస్ వాతావరణం చెప్పు", model_translate=Translator("Tell me the weather in Paris"))
    assert d.action == G.ACT and d.source == "rules+model" and d.english == "Tell me the weather in Paris"


def test_model_and_rules_disagreeing_ask_with_both_readings():
    d = G.decide("ee file ni tegu", model_translate=Translator("Save this file", 0.9))
    assert d.action == G.ASK
    labels = [o["label"] for o in d.form["options"]]
    assert "Save this file" in labels and any("Delete" in x for x in labels)


def test_destructive_readings_need_the_higher_bar():
    low = G.decide("ee file ni tegu")                         # the owner's uncertain word, 0.6
    assert low.action == G.ASK and any("Delete this file" in o["label"] for o in low.form["options"])
    mid = G.decide("ee file ni tolagimchu", model_translate=Translator("Delete this file", 0.9))
    assert mid.action == G.ACT and mid.source == "rules"       # an exact lexicon verb with its object reaches 0.95
    uncertain = G.decide("naku ee file ayipoyindi", model_translate=Translator("Delete this file", 0.9))
    assert uncertain.action == G.ASK                           # 0.9 is enough for a harmless command, not for a delete


def test_a_translator_that_fails_or_returns_nothing_asks():
    assert G.decide("anna oka pani cheyyi", model_translate=Translator("", raises=True)).action == G.ASK
    assert G.decide("anna oka pani cheyyi", model_translate=Translator("   ", 0.9)).action == G.ASK


def test_english_intents():
    assert G.english_intents("Open Notepad and type hello") == {"open", "type"}
    assert G.english_intents("Move this file to the Recycle Bin") == {"delete"}
    assert G.english_intents("What is the weather in Paris") == {"tell"}
