"""D70: names stated in Telugu script, Tenglish, Devanagari and Hinglish are learned from the ORIGINAL words and kept away from the translator.

The laptop saw (Phase 4, afb5496): "My wife is Priya" learned, but "naa bharya peru Priya" and "నా భార్య ప్రియ" did not, because the local translator (IndicTrans2)
rendered the name as a common word ("my darling") and the learner read the translator's English. The real translator is NOT available here. `Mangler` below is a
LABELLED FAKE that does exactly what the laptop saw: it keeps the placeholder tokens the translate path uses (JVX0Q), and any name it is allowed to see becomes a
common phrase. So these tests prove the plumbing and the rules; whether the REAL IndicTrans2 keeps `JVX0Q` intact is something only the laptop can show.

The sentences were written by Claude and have not been checked by a native speaker of Telugu or Hindi (D70 says so).
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from jarvis.plugins._shared import name_spans as S
from jarvis.plugins._shared import names as N
from jarvis.plugins._shared import translate as T

# the common words a name turns into when the translator sees it
MANGLED = {"Priya": "my darling", "ప్రియ": "my darling", "प्रिया": "my darling", "priya": "my darling", "Lakshmi": "goddess", "లక్ష్మి": "goddess", "लक्ष्मी": "goddess",
           "Satish": "truth", "సతీష్": "truth", "सतीश": "truth", "Ravi": "sun", "రవి": "sun", "रवि": "sun", "Suresh": "good lord", "సురేష్": "good lord", "Kiran": "ray",
           "Guntur": "the city"}
WORDS = {"naa": "my", "maa": "our", "bharya": "wife", "peru": "name", "amma": "mother", "ki": "to", "call": "call", "chey": "do", "ame": "she", "ni": "", "nannu": "me",
         "నా": "my", "భార్య": "wife", "పేరు": "name", "కాల్": "call", "చేయి": "do", "కి": "to", "ప్రియకి": "to my darling",
         "मेरी": "my", "पत्नी": "wife", "का": "of", "नाम": "name", "है": "is", "को": "to", "कॉल": "call", "करो": "do"}


class Mangler(T.Backend):
    """FAKE translator: word for word, placeholder tokens untouched, every name it sees replaced by a common phrase."""
    name = "mangler"
    local = True

    def __init__(self, token_style=None):
        self.calls: list[str] = []
        self.token_style = token_style                      # None | callable(token) -> str : how this fake damages a placeholder

    def supports(self, src, tgt, romanized=False):
        return True

    def translate(self, text, src, tgt, *, romanized=False, deadline=None):
        self.calls.append(text)
        out = []
        for w in re.split(r"(\s+)", text):
            core = w.strip(".,?!।")
            if re.fullmatch(r"JVX\d+Q", core):
                out.append(w.replace(core, self.token_style(core)) if self.token_style else w)
            else:
                out.append(MANGLED.get(core, WORDS.get(core, core)) + w[len(core):] if core and w.endswith(w[len(core):]) and w[len(core):] in ("", ".", ",", "?", "!", "।") else w)
        return " ".join(x for x in "".join(out).split())


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("JARVIS_LANGUAGE_FILE", str(tmp_path / "language.json"))
    monkeypatch.setenv("JARVIS_GLOSSARY", str(tmp_path / "glossary.json"))
    monkeypatch.delenv("JARVIS_NAMES_FILE", raising=False)
    monkeypatch.delenv("JARVIS_LEARNED_NAMES", raising=False)
    monkeypatch.delenv("JARVIS_NAME_PATTERNS", raising=False)
    yield
    T.set_translator(None)


@pytest.fixture
def store(tmp_path):
    return N.get_store(tmp_path / "names.json")


def translator(backend=None, **cfg):
    b = backend or Mangler()
    t = T.Translator([b], {"keep_original": False, **cfg}, glossary=[])
    T.set_translator(t)
    return t, b


def says(text, store, **cfg):
    """The gateway's face path in miniature: translate (the mangling fake), then learn from the original and its English."""
    t, _ = translator(**cfg)
    english = t.to_english(text).english
    return N.observe(text, english, store=store, manual=[]), english


# ---- 1. what is a name statement: >= 14 statements, each with the right name and relation -------------------------------------------------

LEARN = [
    # (text, canonical name, kind, relation, native alias)
    ("naa bharya peru Priya", "Priya", "family", "wife", ""),
    ("నా భార్య ప్రియ", "Priya", "family", "wife", "ప్రియ"),
    ("నా భార్య పేరు ప్రియ", "Priya", "family", "wife", "ప్రియ"),
    ("మా అమ్మ పేరు లక్ష్మి", "Lakshmi", "family", "mother", "లక్ష్మి"),
    ("maa amma peru Lakshmi", "Lakshmi", "family", "mother", ""),
    ("naa peru Satish", "Satish", "self", "", ""),
    ("నా పేరు సతీష్", "Satish", "self", "", "సతీష్"),
    ("మా నాన్న పేరు సురేష్", "Suresh", "family", "father", "సురేష్"),
    ("naa tammudu peru Ravi", "Ravi", "family", "brother", ""),
    ("naa friend peru Kiran", "Kiran", "friend", "friend", ""),
    ("maa amma ni Lakshmi ani pilustaru", "Lakshmi", "family", "mother", ""),
    ("meri patni ka naam Priya hai", "Priya", "family", "wife", ""),
    ("मेरी पत्नी का नाम प्रिया है", "Priya", "family", "wife", "प्रिया"),
    ("मेरी पत्नी प्रिया है", "Priya", "family", "wife", "प्रिया"),
    ("mera naam Satish hai", "Satish", "self", "", ""),
    ("मेरा नाम सतीश है", "Satish", "self", "", "सतीश"),
    ("mera dost Ravi hai", "Ravi", "friend", "friend", ""),
    ("मेरा भाई रवि है", "Ravi", "family", "brother", "रवि"),
    ("मेरी माँ का नाम लक्ष्मी है", "Lakshmi", "family", "mother", "लक्ष्मी"),
    ("maa ooru Guntur", "Guntur", "place", "hometown", ""),
    ("Priya naa bharya", "Priya", "family", "wife", ""),
    ("ప్రియ నా భార్య", "Priya", "family", "wife", "ప్రియ"),
    ("Jarvis, naa bharya peru Priya.", "Priya", "family", "wife", ""),
]


@pytest.mark.parametrize("text,name,kind,relation,alias", LEARN)
def test_a_statement_in_the_owners_own_words_learns_the_right_name_and_relation_even_when_the_translator_mangles_it(store, text, name, kind, relation, alias):
    obs, english = says(text, store)
    assert obs.learned == [name], (text, english, obs)
    e = store.find(name)
    assert e["kind"] == kind and e["relation"] == relation
    if alias:
        assert alias in e["aliases"]
    assert "darling" not in english and "goddess" not in english and "truth" not in english      # the translator never saw the name


NOT_LEARNED = [
    "maa amma peru emiti?",                    # a question: what is my mother's name
    "naa bharya peru emiti",                   # the same without the mark: the question word
    "నా భార్య పేరు ఏమిటి",
    "मेरी पत्नी का नाम क्या है",
    "naa bharya peru Priya kadu",              # a refusal
    "naa bharya peru Priya ante?",
    "మా అమ్మ పేరు లక్ష్మి కాదు",
    "मेरी पत्नी का नाम प्रिया नहीं है",
    "naa peru cheppu",                         # a request: tell me my name
    "naa peru marchu",                         # a request: change my name
    "meri patni ko call karo",                 # a command, not a statement
    "naa bharya ki call chey",
    "मेरी पत्नी बीमार है",                      # an adjective where a name could sit
    "నా భార్య ఇంటికి వచ్చింది",                   # a sentence about the wife
    "agar meri patni ka naam Priya hai",       # a condition
    "Is my wife called Zara?",                 # English questions too
    "my wife's name is not Zara",
]


@pytest.mark.parametrize("text", NOT_LEARNED)
def test_a_sentence_that_only_looks_like_a_statement_learns_nothing(store, text):
    obs, _ = says(text, store)
    assert not obs.learned and not obs.candidates and store.entries() == [], (text, obs, store.entries())
    assert S.extract(text, never=lambda w, strict=True: N.never_name(w, strict=strict)) == []


def test_a_lower_case_romanized_name_is_accepted_only_after_the_name_word_and_is_asked_once(store):
    obs, _ = says("naa bharya peru priya", store)
    assert obs.learned == ["Priya"]
    e = store.find("Priya")
    assert e["confidence"] < 0.7                                  # not sure: the ask-once rule of D62 handles it
    assert N.pending_question(store) is not None
    # without the name word a lower-case word is not taken for a name
    assert not says("naa bharya priya", N.NameStore(store.path.with_name("other.json")))[0].learned


def test_the_native_spelling_becomes_an_alias_of_the_name_the_owner_gave_in_letters(store):
    says("naa bharya peru Priya", store)
    obs, _ = says("నా భార్య ప్రియ", store)
    e = store.find("Priya")
    assert "ప్రియ" in e["aliases"] and e["count"] == 2
    assert len(store.entries()) == 1


def test_transliteration_of_native_names():
    cases = {"ప్రియ": "Priya", "లక్ష్మి": "Lakshmi", "సతీష్": "Satish", "రవి": "Ravi", "కిరణ్": "Kiran", "सतीश": "Satish", "प्रिया": "Priya", "लक्ष्मी": "Lakshmi",
             "राज": "Raj", "रवि": "Ravi", "सुरेश": "Suresh", "అనిల్": "Anil"}
    for native, latin in cases.items():
        assert S.transliterate(native) == latin, native
    assert S.transliterate("Priya") == "" and S.transliterate("") == ""


# ---- 2. the translator never sees a stated name; the regression the laptop saw ---------------------------------------------------------------

def test_the_laptop_regression_naa_bharya_peru_priya_no_longer_becomes_my_darling():
    t, b = translator(name_rules=False)                           # the placeholder route alone, as for a sentence with more in it
    r = t.to_english("naa bharya peru Priya, ame ki call chey")
    assert "darling" not in r.english and "Priya" in r.english
    assert r.english == "my wife name Priya, she to call do"
    assert b.calls and "Priya" not in b.calls[0] and re.search(r"JVX\d+Q", b.calls[0])       # the backend got a token, not the name


def test_without_the_protection_the_same_translator_does_turn_priya_into_my_darling():
    """The control: the fake does mangle a name it is allowed to see, so the test above proves something."""
    t, b = translator(name_rules=False, protect_names=False)
    assert "my darling" in t.to_english("naa bharya peru Priya, ame ki call chey").english


@pytest.mark.parametrize("text,must,mustnot", [
    ("నా భార్య పేరు ప్రియ, కాల్ చేయి", "Priya", "darling"),
    ("मेरी पत्नी का नाम प्रिया है, कॉल करो", "Priya", "darling"),
    ("maa amma peru Lakshmi, ame ki call chey", "Lakshmi", "goddess"),
])
def test_a_name_inside_a_longer_sentence_is_cut_out_and_put_back(text, must, mustnot):
    t, b = translator(name_rules=False)
    r = t.to_english(text)
    assert must in r.english and mustnot not in r.english
    assert must not in b.calls[0] and not [w for w in MANGLED if w in b.calls[0]]


def test_a_sentence_that_is_nothing_but_a_name_statement_needs_no_model_at_all():
    t, b = translator()
    for text, want in [("naa bharya peru Priya", "My wife's name is Priya."), ("నా భార్య ప్రియ", "My wife is Priya."), ("మా అమ్మ పేరు లక్ష్మి", "My mother's name is Lakshmi."),
                       ("meri patni ka naam Priya hai", "My wife's name is Priya."), ("मेरा नाम सतीश है", "My name is Satish."),
                       ("naa peru Satish", "My name is Satish.")]:
        r = t.to_english(text)
        assert r.english == want and r.translated and r.backend == "name-rules" and r.source_lang != "en", (text, r)
    assert b.calls == []


def test_the_detector_alone_calls_naa_bharya_peru_priya_english_so_the_rule_must_say_otherwise():
    assert T.detect("naa bharya peru Priya").lang == "en"
    t, _ = translator()
    assert t.detect("naa bharya peru Priya").lang == "te-Latn"
    assert t.detect("The weather in Priya is fine").lang == "en"


def test_the_rules_off_switch_and_the_learned_names_off_switch(monkeypatch):
    t, b = translator(name_rules=False, protect_names=False)
    assert t.to_english("naa bharya peru Priya").source_lang == "en" and b.calls == []
    monkeypatch.setenv("JARVIS_LEARNED_NAMES", "0")
    t, b = translator()
    assert t.to_english("naa bharya peru Priya").source_lang == "en" and b.calls == []


def test_a_multi_line_message_translates_the_statement_line_and_leaves_the_english_alone():
    t, b = translator()
    r = t.to_english("[2026-10-27 09:00]\nnaa bharya peru Priya\nPlease keep it short.")
    assert r.english == "[2026-10-27 09:00]\nMy wife's name is Priya.\nPlease keep it short."


# ---- 3. names ALREADY known are protected too ------------------------------------------------------------------------------------------------

def test_a_known_name_is_never_translated_in_a_command(store):
    N.observe("My wife is Priya", "My wife is Priya", store=store, manual=[])
    t, b = translator()
    t._names = N.index(store, [])
    r = t.to_english("Priya ki call chey")
    assert r.english == "Priya to call do" and "darling" not in r.english and "Priya" not in b.calls[0]


def test_a_known_name_written_in_telugu_script_is_found_by_its_transliteration_even_without_an_alias(store):
    N.observe("My wife is Priya", "My wife is Priya", store=store, manual=[])
    assert store.find("Priya")["aliases"] == []
    t, b = translator()
    t._names = N.index(store, [])
    r = t.to_english("ప్రియకి కాల్ చేయి")
    assert "Priya" in r.english and "darling" not in r.english
    assert "ప్రియ" not in b.calls[0]


def test_a_known_name_in_devanagari_is_found_by_its_transliteration(store):
    N.observe("My wife is Lakshmi", "My wife is Lakshmi", store=store, manual=[])
    t, b = translator()
    t._names = N.index(store, [])
    r = t.to_english("लक्ष्मी को कॉल करो")
    assert "Lakshmi" in r.english and "goddess" not in r.english


def test_a_common_telugu_word_is_not_taken_for_a_known_name(store):
    N.observe("My wife is Priya", "My wife is Priya", store=store, manual=[])
    ix = N.index(store, [])
    assert ix.find("నా భార్య పేరు చెప్పు") == []


# ---- 4. the placeholder contract ------------------------------------------------------------------------------------------------------------

def test_the_placeholder_survives_a_translator_that_changes_its_case_or_adds_a_comma():
    for damage in (lambda t: t.lower(), lambda t: t + ",", lambda t: t[:3] + " " + t[3:]):
        t, _ = translator(Mangler(token_style=damage), name_rules=False)
        r = t.to_english("naa bharya peru Priya, ame ki call chey")
        assert r.translated and "Priya" in r.english and "darling" not in r.english, r


def test_a_translator_that_loses_the_placeholder_is_not_trusted_and_the_name_is_never_guessed():
    class Loser(Mangler):
        def translate(self, text, *a, **k):
            return re.sub(r"JVX\d+Q", "", super().translate(text, *a, **k))
    t, _ = translator(Loser(), name_rules=False)
    r = t.to_english("naa bharya peru Priya, ame ki call chey")
    assert not r.translated and r.english == "naa bharya peru Priya, ame ki call chey"           # the original goes on; the learner still reads the original
    store = N.get_store()
    assert N.observe(r.original, r.english, store=store, manual=[]).learned == ["Priya"]


def test_the_masked_text_uses_the_token_format_of_the_translate_path():
    m = T.mask("naa bharya peru Priya, ame ki call chey", [], None, statements=True)
    assert m.text == f"naa bharya peru {m.prefix}0Q, ame ki call chey" and m.items == [{"id": 0, "kind": "name", "text": "Priya"}]
    assert T.unmask(m.text, m) == ("naa bharya peru Priya, ame ki call chey", [])
    n = T.mask("నా భార్య పేరు ప్రియకి కాల్ చేయి", [], None, statements=True)
    assert n.items[0]["text"] == "Priya" and n.text.endswith("కి కాల్ చేయి") and n.text.startswith("నా భార్య పేరు JVX0Q")


def test_english_text_and_text_with_a_token_already_in_it_are_left_alone():
    assert T.mask("My wife's name is Priya", [], None, statements=True).items == []
    m = T.mask("naa bharya peru JVX0Q", [], None, statements=True)
    assert m.items == [] or all("JVX" not in i["text"] for i in m.items)


# ---- 5. the D62 rules stay -------------------------------------------------------------------------------------------------------------------

def test_forget_leaves_a_tombstone_that_only_the_owners_own_sentence_lifts(store):
    says("naa bharya peru Priya", store)
    assert store.forget("Priya")
    assert store.find("Priya") is None
    # asking about her or calling her never brings her back
    says("Priya ki call chey", store)
    says("Priya ki call chey", store)
    says("Priya ki call chey", store)
    assert store.find("Priya") is None
    # his own sentence is newer than "forget"
    assert says("నా భార్య ప్రియ", store)[0].learned == ["Priya"]


def test_a_resent_statement_is_one_sighting_not_two(store):
    says("naa bharya peru Priya", store)
    obs, _ = says("naa bharya peru Priya", store)
    assert obs.skipped == "seen a moment ago" and store.find("Priya")["count"] == 1


def test_the_online_planner_gets_the_relation_word_not_the_names_table(store):
    says("naa bharya peru Priya", store)
    assert N.hint_for("call my wife", store) == "wife: Priya"                      # for the LOCAL decider only (D62)
    assert N.hint_for("what is the weather", store) == ""


def test_the_stated_names_are_not_written_to_any_log_by_this_code(store, tmp_path):
    """The only file written is names.json (and its lock); the journal of translations stays off with keep_original false."""
    says("naa bharya peru Priya", store)
    files = {p.name for p in tmp_path.rglob("*") if p.is_file()}
    assert files <= {"names.json", "names.json.lock"}, files


def test_a_native_script_name_is_not_learned_from_a_tool_result_or_a_page():
    """Learning is still only reachable through observe_owner (the face's own words); the pattern module has no entry of its own."""
    src = Path(S.__file__).read_text(encoding="utf-8")
    assert "open(" not in src and "write_text" not in src and "socket" not in src
    assert not re.search(r"^\s*(?:import|from)\s+(?:requests|httpx|urllib|aiohttp|http)\b", src, re.M)


# ---- 6. the data file ------------------------------------------------------------------------------------------------------------------------

def test_the_patterns_are_data_a_new_word_in_the_file_is_enough(tmp_path, monkeypatch):
    cfg = json.loads(S.PATTERNS_FILE.read_text(encoding="utf-8"))
    assert S.extract("naa jodi peru Zara") == []
    cfg["relations"]["wife"]["te_lat"].append("jodi")
    f = tmp_path / "patterns.json"
    f.write_text(json.dumps(cfg, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setenv("JARVIS_NAME_PATTERNS", str(f))
    got = S.extract("naa jodi peru Zara")
    assert [(g.name, g.relation) for g in got] == [("Zara", "wife")]


def test_a_missing_or_broken_pattern_file_means_the_language_layer_works_as_before(tmp_path, monkeypatch):
    f = tmp_path / "patterns.json"
    monkeypatch.setenv("JARVIS_NAME_PATTERNS", str(tmp_path / "missing.json"))
    assert S.extract("naa bharya peru Priya") == [] and S.statement_english("naa bharya peru Priya") is None
    f.write_text("{not json", encoding="utf-8")
    monkeypatch.setenv("JARVIS_NAME_PATTERNS", str(f))
    assert S.extract("naa bharya peru Priya") == []
    t, b = translator()
    assert t.to_english("naa bharya peru Priya").source_lang == "en"


def test_the_shipped_pattern_file_lists_every_script_and_every_template_compiles():
    pat = S.load()
    assert pat.ok
    by_script = {s: 0 for s in S.SCRIPTS}
    for tpl, script, rx in pat.rx:
        by_script[script] += 1
    assert all(n >= 6 for n in by_script.values()), by_script
    cfg = json.loads(S.PATTERNS_FILE.read_text(encoding="utf-8"))
    assert len(pat.rx) == sum(len(t["scripts"]) for t in cfg["templates"])


def test_the_new_modules_open_no_connection_and_name_no_address():
    base = Path(S.__file__).parent
    for name in ("name_spans.py", "names.py"):
        src = (base / name).read_text(encoding="utf-8")
        assert not re.findall(r"https?://", src), name
        for lib in ("requests", "httpx", "aiohttp", "urllib.request", "socket", "openai", "anthropic"):
            assert not re.search(rf"^\s*(?:import|from)\s+{re.escape(lib)}\b", src, re.M), f"{name} imports {lib}"
