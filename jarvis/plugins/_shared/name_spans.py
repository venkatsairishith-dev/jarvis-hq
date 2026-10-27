"""Name statements in the owner's OWN words (D70): find the NAME span in the ORIGINAL text, before any translation.

Why this exists. D62 learned a name from the owner's English or from what the translator made of his Telugu or Hindi. The local translator (IndicTrans2) renders a
name as a common word: "naa bharya peru Priya" came back as "my darling" and nothing was learned. So the name is taken out of the original text FIRST, by a closed
set of sentence patterns kept as DATA in `config/name_patterns.json` (kinship, friend, boss, place and the owner's own name; Telugu script, romanized Telugu,
Devanagari, romanized Hindi). The same spans are used twice:

  learn      names.observe reads `extract(original)`: the name (Latin spelling, or a transliteration of a native-script name), the relation and the kind.
  protect    translate.mask replaces each span by a placeholder before the translator and puts it back after, so the translator never sees the name.
  render     statement_english(text): a sentence that is NOTHING but such a statement ("naa bharya peru Priya") is turned into English by a template, with no
             model at all ("My wife's name is Priya.").

A sentence that only LOOKS like a statement is not one: a question ("maa amma peru emiti?", "what is my wife's name"), a refusal ("naa bharya peru Priya kadu"), a
condition ("if my wife is Priya") or a request. The marker words are data too. A Latin-letter name needs a capital letter, except in a template that has the name
word ("peru", "naam") in it, where a lower-case name is accepted at a lower confidence (the owner is then asked once).

A name written in the owner's script is stored in Latin letters by a small transliteration table (also data): ప్రియ -> Priya, लक्ष्मी -> Lakshmi. The native spelling
stays as an alias. It is a guess at the spelling the owner would accept; nothing here checks it against a person.

Standard library only; loadable by path like translate.py and names.py. Nothing here opens a network connection or writes a file.
"""
from __future__ import annotations

import json
import os
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

PATTERNS_FILE = Path(__file__).resolve().parents[2] / "config" / "name_patterns.json"
SCRIPTS = ("te", "te_lat", "hi", "hi_lat")
_RANGE = {"te": "ఀ-౿", "hi": "ऀ-ॿ"}
NATIVE = "ऀ-ॿఀ-౿‌‍"
_ZW = dict.fromkeys((0x200C, 0x200D))
_PLACEHOLDER = re.compile(r"^[A-Za-z]{3,8}\d+Q$")                   # a mask token (JVX0Q): already protected, never a name


@dataclass(frozen=True)
class Span:
    start: int                 # the NAME in the text (not the whole statement)
    end: int
    name: str                  # the Latin spelling to store and to restore
    native: str                # the native-script spelling as written, case ending removed ('' for a Latin-letter name)
    kind: str                  # self | family | friend | other | place
    relation: str              # wife, mother, "younger brother", hometown ... ('' for self)
    conf: float
    rule: str                  # template id
    script: str                # te | te_lat | hi | hi_lat
    whole: bool                # the statement is the whole sentence (nothing else in it)
    english: str               # the statement in English, by template


def _fold(s: str) -> str:
    return unicodedata.normalize("NFC", str(s or "")).translate(_ZW).casefold()


# ====================================================================================================================
# 1. The data, compiled
# ====================================================================================================================

class Patterns:
    def __init__(self, data: dict | None):
        d = data if isinstance(data, dict) else {}
        self.ok = bool(d.get("templates"))
        self.rel_of: dict[str, dict[str, tuple[str, str]]] = {s: {} for s in SCRIPTS}            # script -> folded surface -> (relation, kind)
        self.place_of: dict[str, dict[str, str]] = {s: {} for s in SCRIPTS}
        self.rel_words: set[str] = set()
        for rel, spec in (d.get("relations") or {}).items():
            if isinstance(spec, dict):
                for s in SCRIPTS:
                    for w in spec.get(s) or []:
                        self.rel_of[s][_fold(w)] = (str(rel), str(spec.get("kind") or "other"))
                        self.rel_words.add(_fold(w))
        for rel, spec in (d.get("place_words") or {}).items():
            if isinstance(spec, dict):
                for s in SCRIPTS:
                    for w in spec.get(s) or []:
                        self.place_of[s][_fold(w)] = str(rel)
                        self.rel_words.add(_fold(w))
        self.stop = {_fold(w) for k in ("latin", "native") for w in ((d.get("stop_words") or {}).get(k) or [])}
        self.lead = {_fold(w) for w in d.get("lead_words") or []}
        mk = d.get("markers") or {}
        self.markers: set[str] = set()
        for kind in ("question", "negation", "condition"):
            for words in (mk.get(kind) or {}).values():
                self.markers |= {_fold(w) for w in words}
        self.question_start = {_fold(w) for w in mk.get("question_start_en") or []}
        self.lower_reject = tuple(str(x).lower() for x in d.get("lower_name_reject_endings") or [])
        ce = d.get("case_endings") or {}
        self.endings = {s: sorted((str(x) for x in ce.get(s) or []), key=len, reverse=True) for s in ("te", "hi")}
        self.poss_words = {_fold(w) for k in ("possessive", "self_possessive", "subject", "name_word") for ws in (d.get(k) or {}).values() for w in ws}
        self.translit = d.get("translit") or {}
        self._tl = self._build_translit()
        self.rx: list[tuple[dict, str, re.Pattern]] = []
        for tpl in d.get("templates") or []:
            if isinstance(tpl, dict):
                for script in tpl.get("scripts") or []:
                    if script in SCRIPTS:
                        try:
                            self.rx.append((tpl, script, self._compile(d, tpl, script)))
                        except re.error:
                            continue

    # ---- regex
    @staticmethod
    def _alt(words: list[str], latin: bool) -> str:
        ws = sorted({w for w in words if w}, key=lambda w: (-len(w), w))
        if not ws:
            return "(?!)"
        body = "|".join(re.escape(w) for w in ws)
        return f"(?i:{body})" if latin else f"(?:{body})"

    def _compile(self, d: dict, tpl: dict, script: str) -> re.Pattern:
        latin = script.endswith("_lat")

        def words(key: str) -> list[str]:
            return list((d.get(key) or {}).get(script) or [])

        # the surface forms as written in the data (the folded keys lose nothing for Latin; native keys are NFC)
        rels = list(self.rel_of[script])
        slots = {
            "POSS": self._alt(words("possessive"), latin), "SPOSS": self._alt(words("self_possessive"), latin), "NW": self._alt(words("name_word"), latin),
            "LINK": self._alt(words("link"), latin), "COP": self._alt(words("copula"), latin), "RELSUF": self._alt(words("relation_suffix"), latin),
            "ANI": self._alt(words("ani"), latin), "CALL": self._alt(words("call_verb"), latin), "NANNU": self._alt(words("nannu"), latin),
            "MUJHE": self._alt(words("mujhe"), latin), "HCALL": self._alt(words("hindi_call_verb"), latin), "IAM": self._alt(words("i_am_subject"), latin),
            "IAMV": self._alt(words("i_am_verb"), latin), "SUBJ": self._alt(words("subject"), latin), "LOC": self._alt(words("locative"), latin),
            "LIVE": self._alt(words("live_verb"), latin), "REL": "(?P<rel>" + self._alt(rels, latin) + ")",
            "PLACE": "(?P<plc>" + self._alt([w for w in self.place_of[script]], latin) + ")",
        }
        if latin:
            slots["NAME"] = r"(?P<n>[A-Za-z][A-Za-z'’\-]{1,24}(?:[ \t]+[A-Z][A-Za-z'’\-]{1,24})?)"
        else:
            slots["NAME"] = rf"(?P<n>[{_RANGE[script]}‌‍]{{2,30}})"
        pat = str(tpl["pattern"])
        for k in sorted(slots, key=len, reverse=True):                    # longest slot name first (REL before RELSUF is by braces anyway)
            pat = pat.replace("{" + k + "}", slots[k])
        return re.compile(rf"(?<![A-Za-z{NATIVE}])(?:{pat})(?![A-Za-z{NATIVE}'’\-])")

    # ---- transliteration
    def _build_translit(self) -> dict[str, dict]:
        out: dict[str, dict] = {}
        for sc, spec in self.translit.items():
            if isinstance(spec, dict):
                out[sc] = {k: spec.get(k) for k in ("drop_final_a", "virama", "vowels", "signs", "marks", "consonants", "ignore")}
        return out


_cache: dict[str, tuple[tuple, Patterns]] = {}


def patterns_path() -> Path:
    env = os.environ.get("JARVIS_NAME_PATTERNS", "").strip()
    return Path(env) if env else PATTERNS_FILE


def load(path: Path | str | None = None) -> Patterns:
    """The compiled patterns, re-read when the file changes. A missing or broken file gives a Patterns that finds nothing (the language layer then works as before)."""
    p = Path(path) if path else patterns_path()
    try:
        st = p.stat()
        sig = (st.st_mtime_ns, st.st_size)
    except OSError:
        return Patterns(None)
    hit = _cache.get(str(p))
    if hit and hit[0] == sig:
        return hit[1]
    try:
        pat = Patterns(json.loads(p.read_text(encoding="utf-8")))
    except (OSError, ValueError):
        pat = Patterns(None)
    _cache[str(p)] = (sig, pat)
    return pat


# ====================================================================================================================
# 2. Transliteration of a native-script name
# ====================================================================================================================

def _script_of_word(word: str) -> str:
    for ch in word:
        o = ord(ch)
        if 0x0C00 <= o <= 0x0C7F:
            return "te"
        if 0x0900 <= o <= 0x097F:
            return "hi"
    return ""


def transliterate(word: str, pat: Patterns | None = None) -> str:
    """A native-script name in Latin letters, capitalised: ప్రియ -> Priya, लक्ष्मी -> Lakshmi, సతీష్ -> Satish, राज -> Raj. '' when the word is not Telugu or Devanagari."""
    pat = pat or load()
    word = unicodedata.normalize("NFC", str(word or "")).translate(_ZW)
    sc = _script_of_word(word)
    spec = pat._tl.get(sc)
    if not spec:
        return ""
    cons, vowels, signs, marks = spec["consonants"] or {}, spec["vowels"] or {}, spec["signs"] or {}, spec["marks"] or {}
    virama, ignore = spec["virama"], set(spec.get("ignore") or [])
    out: list[str] = []
    pending = ""                                  # a consonant waiting for its vowel
    for i, ch in enumerate(word):
        if ch in ignore:
            continue
        if ch in cons:
            if pending:
                out.append(pending + "a")
            pending = cons[ch]
        elif ch == virama:
            out.append(pending)
            pending = ""
        elif ch in signs:
            out.append(pending + signs[ch])
            pending = ""
        elif ch in vowels:
            if pending:
                out.append(pending + "a")
                pending = ""
            out.append(vowels[ch])
        elif ch in marks:
            if pending:
                out.append(pending + "a")
                pending = ""
            m = marks[ch]
            if m in ("m", "n") and ch in ("ం", "ं"):                         # anusvara: m before a lip sound or at the end, else n
                nxt = cons.get(word[i + 1], "") if i + 1 < len(word) else ""
                m = "m" if (not nxt or nxt[:1] in ("p", "b", "m")) else "n"
            out.append(m)
        else:
            return ""                                                       # a character the table does not know: no guess
    if pending:
        out.append(pending + ("" if spec.get("drop_final_a") else "a"))
    s = "".join(out)
    return s[:1].upper() + s[1:] if len(s) >= 2 else ""


# ====================================================================================================================
# 3. Is this sentence a statement?
# ====================================================================================================================

_TOKEN = re.compile(rf"[A-Za-z'’]+|[{NATIVE}]+")
_SENT = re.compile(r"[^.!?।\n]+[.!?।]*")


def _tokens(s: str) -> list[str]:
    return [_fold(t) for t in _TOKEN.findall(s)]


def is_statement(sentence: str, pat: Patterns | None = None) -> bool:
    """False for a question, a refusal, a condition or a request: nothing is learned from it and nothing in it is protected as a new name."""
    pat = pat or load()
    s = str(sentence or "")
    if "?" in s or "？" in s:
        return False
    toks = _tokens(s)
    if any(t in pat.markers for t in toks):
        return False
    if any(t.endswith("n't") or t.endswith("n’t") for t in toks):
        return False
    lead = [t for t in toks]
    while lead and lead[0] in pat.lead:
        lead.pop(0)
    if lead and lead[0] in pat.question_start and re.fullmatch(r"[A-Za-z']+", lead[0]):
        return False
    return True


def _core(sent: str, pat: Patterns) -> tuple[int, int]:
    """The part of a sentence that is the statement: without a greeting in front and punctuation behind."""
    end = len(sent.rstrip(" \t.!?।,;:"))
    start = 0
    while True:
        m = re.match(rf"[\s,;:\-]*([A-Za-z'’]+|[{NATIVE}]+)", sent[start:end])
        if not m or _fold(m.group(1)) not in pat.lead:
            break
        start += m.end()
    m = re.match(r"[\s,;:\-]*", sent[start:end])
    return start + (m.end() if m else 0), end


# ====================================================================================================================
# 4. Extraction
# ====================================================================================================================

def _cap(tok: str) -> str:
    return tok.capitalize() if tok.islower() or tok.isupper() else tok


def _latin_name(raw: str, *, lower_ok: bool, strict: bool, pat: Patterns, never: Callable[..., bool] | None) -> str | None:
    toks = [t.strip("'’-") for t in raw.split()]
    toks = [t for t in toks if t]
    if not toks:
        return None
    if toks[0][:1].islower() and (not lower_ok or toks[0].lower().endswith(pat.lower_reject)):
        return None
    out: list[str] = []
    for i, t in enumerate(toks):
        k = _fold(t)
        if len(t) < 2 or k in pat.stop or k in pat.rel_words or k in pat.poss_words or k in pat.markers or _PLACEHOLDER.match(t):
            break
        if never is not None and never(t, strict):
            break
        out.append(t)
    if not out:
        return None
    name = " ".join(_cap(t) for t in out)
    return name if 2 <= len(name) <= 40 else None


def _native_name(raw: str, script: str, pat: Patterns, never: Callable[..., bool] | None) -> tuple[str, int] | None:
    """(the native token without its case ending, how many characters were cut off the end) or None."""
    tok = raw
    cut = 0
    for end in pat.endings.get("te" if script == "te" else "hi", []):
        if tok.endswith(end) and len(tok) - len(end) >= 2:
            tok, cut = tok[: -len(end)], len(end)
            break
    tok = unicodedata.normalize("NFC", tok).translate(_ZW)
    k = _fold(tok)
    if len(tok) < 2 or k in pat.stop or k in pat.rel_words or k in pat.poss_words or k in pat.markers:
        return None
    if never is not None and never(tok, False):
        return None
    return tok, cut


def extract(text: str, *, never: Callable[..., bool] | None = None, patterns: Patterns | None = None) -> list[Span]:
    """The name statements in `text`, in order, without overlaps. `never(word, strict)` is names.never_name (a verb, a pronoun, a day: never a name). Questions,
    refusals and conditions give nothing. The text is the owner's ORIGINAL words; the caller (names.observe, translate.mask) decides what may be done with them."""
    pat = patterns or load()
    if not pat.ok or not text:
        return []
    text = str(text)[:4000]
    found: list[Span] = []
    for sm in _SENT.finditer(text):
        sent, base = sm.group(0), sm.start()
        if not sent.strip() or not is_statement(sent, pat):
            continue
        c0, c1 = _core(sent, pat)
        for tpl, script, rx in pat.rx:
            for m in rx.finditer(sent):
                sp = _span(m, tpl, script, base, c0, c1, pat, never)
                if sp is not None:
                    found.append(sp)
    found.sort(key=lambda s: (s.start, -s.conf))
    out: list[Span] = []
    for sp in found:
        if out and sp.start < out[-1].end:
            if sp.conf > out[-1].conf and sp.start == out[-1].start:
                out[-1] = sp
            continue
        out.append(sp)
    return out


def _span(m: re.Match, tpl: dict, script: str, base: int, c0: int, c1: int, pat: Patterns, never) -> Span | None:
    kind = str(tpl.get("kind") or "other")
    strict = kind == "self"
    raw = m.group("n")
    native = ""
    if script.endswith("_lat"):
        name = _latin_name(raw, lower_ok=bool(tpl.get("lower_ok")), strict=strict, pat=pat, never=never)
        if not name:
            return None
        n_start = m.start("n")
        ends = [t.end() for t in re.finditer(r"\S+", raw)]
        n_end = n_start + ends[min(len(name.split()), len(ends)) - 1]
    else:
        if tpl.get("latin_name_only"):
            return None
        got = _native_name(raw, script, pat, never)
        if got is None:
            return None
        native, cut = got
        name = transliterate(native, pat)
        if not name or (never is not None and never(name, strict)) or _fold(name) in pat.stop:
            return None
        n_start, n_end = m.start("n"), m.end("n") - cut
    relation = str(tpl.get("relation") or "")
    if kind == "relation":
        got_rel = pat.rel_of[script].get(_fold(m.group("rel")))
        if got_rel is None:
            return None
        relation, kind = got_rel
    elif kind == "place" and not relation:
        relation = pat.place_of[script].get(_fold(m.groupdict().get("plc") or ""), "hometown")
    whole = m.start() <= c0 and m.end() >= c1
    if tpl.get("anchor") == "sentence" and not whole:
        return None
    conf = float(tpl.get("conf") or 0.8)
    if script.endswith("_lat") and raw[:1].islower():
        conf = min(conf, 0.65)                                                # a lower-case name: accepted, but asked once
    english = str(tpl.get("en") or "").replace("{name}", name).replace("{rel}", relation)
    return Span(base + n_start, base + n_end, name, native, kind, relation, conf, str(tpl.get("id")), script, whole, english)


# ====================================================================================================================
# 5. What the callers ask for
# ====================================================================================================================

def protect_spans(text: str, *, never: Callable[..., bool] | None = None) -> list[tuple[int, int, str]]:
    """[(start, end, text it goes back to)] for translate.mask: each name in a name statement, replaced by a placeholder before the translator and restored after.
    The restore text is the Latin spelling (a native-script name is restored as its transliteration, so the English text reads "my wife's name is Priya")."""
    return [(sp.start, sp.end, sp.name) for sp in extract(text, never=never)]


def statement_english(text: str, *, never: Callable[..., bool] | None = None) -> tuple[str, str] | None:
    """(English, language) when the text is NOTHING but name statements ("naa bharya peru Priya" -> "My wife's name is Priya."), else None. No model is used."""
    pat = load()
    if not pat.ok or not str(text or "").strip():
        return None
    pieces: list[str] = []
    lang = ""
    for sm in _SENT.finditer(str(text)[:2000]):
        if not sm.group(0).strip(" \t\r\n.!?।,;:"):
            continue
        spans = [s for s in extract(sm.group(0), never=never, patterns=pat) if s.whole]
        if len(spans) != 1:
            return None
        pieces.append(spans[0].english)
        lang = lang or spans[0].script
    return (" ".join(pieces), lang.replace("_lat", "-Latn")) if pieces else None
