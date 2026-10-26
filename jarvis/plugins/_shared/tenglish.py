"""Tenglish (Telugu words in English letters), romanized Hindi and native Telugu/Hindi commands -> a plain English command, by RULES (D59, lever c).

The owner mixes languages: "Notepad open chesi milk, eggs, bread, rice rayandi". A model that translates this guesses; a verb table does
not. This module is the deterministic step that runs BEFORE any model:

    parse("Notepad open chesi milk, eggs, bread, rice rayandi")
      -> Parse(english="Open Notepad and type: milk, eggs, bread, rice.", confidence=0.95)

How it reads a command
  * the verbs come from one lexicon (LEXICON below): English words people keep (open, save, delete ...), Telugu in Latin letters (teruvu,
    rayi/rayandi, teesey, chupinchu, vetuku, cheppu ...), Hindi in Latin letters (kholo, likho, hatao, dikhao ...), and Telugu/Hindi in
    their own scripts (stems). Spelling is normalised (repeated letters collapse: raayi = rayi), so loose spelling still matches.
  * Telugu and Hindi put the verb LAST ("notepad open chesi ..." / "notepad kholo"): words before a verb belong to it. English puts the
    verb first; words after it belong to it. A clause is whichever shape it has.
  * "chesi", "karke", "mariyu", "aur", "and", "then" start the next clause. "chey/cheyyi/karo" next to a verb are helpers, not verbs.
  * a quoted piece ("...") is the text to type, verbatim.
  * a verb that means two things ("vesey", "pettu": play OR put) is decided by the other words ("song", "recycle bin"); if they do not
    decide it, the confidence is low and the caller asks instead of acting.

What it never does: act. It returns words and a confidence. `confidence` is the LOWEST verb confidence in the command, lowered for words
it could not place. Callers (language_gate.py) act only above a threshold and ask "Did you mean ...?" below it.
The one verb the owner listed whose meaning is not certain, "tegu", is in the lexicon at 0.6 so that it can only ever produce a question.
The lexicon is a starting set written from the owner's own list and common usage; the Telugu and Hindi speaker confirms and extends it
(D59: the laptop session runs `tenglish.py --selftest` against the owner's real phrases, see evals/reliability/PROTOCOL.md).
Standard library only.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field

# ------------------------------------------------------------------------------------------------------------------ lexicon
# intent -> Latin-letter surface forms. Matching is on the normalised skeleton (skel), so each form stands for its spelling variants.
# (form, confidence). 0.95 = a word that means this and little else; lower = ambiguous or unsure.
LEXICON: dict[str, list[tuple[str, float]]] = {
    "open": [("open", 0.95), ("launch", 0.95), ("teruvu", 0.95), ("teravandi", 0.95), ("teravu", 0.95), ("teruvandi", 0.95), ("terava", 0.9),
             ("terichi", 0.9), ("tera", 0.85), ("kholo", 0.95), ("kholiye", 0.95), ("kholna", 0.95), ("khol", 0.9), ("kholen", 0.95)],
    "type": [("type", 0.95), ("write", 0.95), ("rayi", 0.95), ("rayandi", 0.95), ("rayu", 0.9), ("rasi", 0.9), ("likho", 0.95),
             ("likhiye", 0.95), ("likh", 0.9), ("likhna", 0.95), ("likhen", 0.95)],
    "save": [("save", 0.95), ("sahejo", 0.95), ("sahej", 0.9)],
    "delete": [("delete", 0.95), ("remove", 0.9), ("erase", 0.95), ("teesey", 0.95), ("teeseyyi", 0.95), ("teeseyandi", 0.95), ("teesiveyyi", 0.95),
               ("tolagimchu", 0.95), ("tolagimchandi", 0.95), ("hatao", 0.95), ("hataiye", 0.95), ("hatana", 0.9), ("mitao", 0.95),
               ("mitaiye", 0.95), ("tegu", 0.6)],
    "close": [("close", 0.95), ("exit", 0.9), ("quit", 0.9), ("muyyi", 0.95), ("mooyi", 0.95), ("mooseyyi", 0.95), ("musey", 0.9),
              ("moosiveyyi", 0.95), ("mooyandi", 0.95), ("band", 0.9)],
    "show": [("show", 0.95), ("display", 0.95), ("chupinchu", 0.95), ("chupinchandi", 0.95), ("chupu", 0.9), ("chupandi", 0.9),
             ("dikhao", 0.95), ("dikhaiye", 0.95), ("dikha", 0.9)],
    "play": [("play", 0.95), ("chalao", 0.95), ("chalaiye", 0.95), ("bajao", 0.95), ("bajaiye", 0.95),
             ("vesey", 0.7), ("veseyyi", 0.7), ("veyyi", 0.7), ("veyandi", 0.7), ("pettu", 0.7), ("pettandi", 0.7), ("petti", 0.7)],
    "search": [("search", 0.95), ("find", 0.9), ("vetuku", 0.95), ("vetakandi", 0.95), ("vedhuku", 0.95), ("vedakandi", 0.95), ("khojo", 0.95),
               ("khoj", 0.9), ("dhundo", 0.95), ("dhoondo", 0.95), ("dhundhiye", 0.95)],
    "tell": [("tell", 0.9), ("cheppu", 0.95), ("cheppandi", 0.95), ("chepu", 0.9), ("batao", 0.95), ("bataiye", 0.95), ("bolo", 0.85)],
}
AMBIGUOUS_PLAY = ("vesey", "veseyyi", "veyyi", "veyandi", "pettu", "pettandi", "petti")   # "put / set / play": play only next to a media word
AUX = ("chey", "cheyyi", "cheyandi", "cheyyandi", "karo", "kar", "kijiye", "karna", "kariye", "karen", "do")   # "open chey", "delete karo"
CONJ = ("chesi", "karke", "mariyu", "aur", "and", "then", "tarvata", "taruvata", "phir", "fir", "after", "next")  # the next clause starts
STOP = ("lo", "ni", "ki", "ku", "nu", "idi", "adi", "mida", "meeda", "pina", "nunchi", "nundi", "tho", "kosam", "gurinchi", "ela", "undi",
        "emiti", "ento", "mein", "me", "ko", "ka", "ke", "ek", "par", "se", "liye", "kya", "hai", "mujhe", "mere", "mera", "meri", "the", "a",
        "an", "my", "please", "pls", "plz", "to", "in", "on", "of", "for", "it", "now", "can", "you", "could", "will", "hey", "hi", "jarvis",
        "ani", "ra", "bro", "sir", "na", "naku", "nenu", "nuvvu", "meeru", "thanks", "ok", "oka")
DEMONSTRATIVE = {"ee": "this", "this": "this", "yeh": "this", "ye": "this", "aa": "that", "that": "that", "woh": "that", "wo": "that"}
APP_WORDS = {                           # phrase -> canonical name (longest phrase wins)
    "visual studio code": "VS Code", "vs code": "VS Code", "vscode": "VS Code", "file explorer": "File Explorer", "note pad": "Notepad",
    "notepad": "Notepad", "calculator": "Calculator", "calc": "Calculator", "calci": "Calculator", "chrome": "Chrome", "edge": "Edge",
    "paint": "Paint", "explorer": "File Explorer", "word": "Word", "excel": "Excel", "settings": "Settings", "recycle bin": "Recycle Bin",
    "youtube": "YouTube", "spotify": "Spotify",
    "నోట్ప్యాడ్": "Notepad", "కాలిక్యులేటర్": "Calculator", "క్రోమ్": "Chrome",
    "नोटपैड": "Notepad", "कैलकुलेटर": "Calculator", "कैल्कुलेटर": "Calculator", "क्रोम": "Chrome",
}
MEDIA = ("song", "songs", "music", "video", "videos", "movie", "youtube", "pata", "paata", "patalu", "gaana", "gana", "gaane", "spotify", "playlist")
WEATHER = ("weather", "vatavaranam", "vaataavaranam", "mausam", "vaatavaranam", "వాతావరణం", "వాతావరణ", "मौसम")

# native-script verb stems (Telugu, Devanagari): a token that STARTS with the stem is that verb. (stem, confidence, ambiguous)
NATIVE: dict[str, list[tuple[str, float, bool]]] = {
    "open": [("తెర", 0.9, False), ("खोल", 0.9, False)],
    "type": [("రాయ", 0.9, False), ("టైప్", 0.9, False), ("लिख", 0.9, False)],
    "save": [("సేవ్", 0.9, False), ("सेव", 0.9, False), ("सहेज", 0.9, False)],
    "delete": [("తొలగ", 0.9, False), ("డిలీట్", 0.9, False), ("తీసేయ", 0.9, False), ("हटा", 0.9, False), ("मिटा", 0.9, False), ("डिलीट", 0.9, False)],
    "close": [("మూయ", 0.9, False), ("మూసి", 0.9, False), ("క్లోజ్", 0.9, False), ("बंद", 0.9, False), ("बन्द", 0.9, False)],
    "show": [("చూప", 0.9, False), ("दिखा", 0.9, False)],
    "play": [("ప్లే", 0.9, False), ("వేయ", 0.7, True), ("పెట్ట", 0.7, True), ("चला", 0.9, False), ("बजा", 0.9, False), ("प्ले", 0.9, False)],
    "search": [("వెతక", 0.9, False), ("వెతుక", 0.9, False), ("खोज", 0.9, False), ("ढूंढ", 0.9, False), ("ढूँढ", 0.9, False)],
    "tell": [("చెప్ప", 0.9, False), ("बता", 0.9, False)],
}
NATIVE_CONJ = ("చేసి", "మరియు", "తర్వాత", "और", "फिर", "करके")
NATIVE_AUX = ("చేయి", "చేయండి", "చేయ", "करो", "करें", "कीजिए", "करना")
NATIVE_STOP = ("లో", "ని", "కి", "ను", "ఈ", "ఆ", "ఒక", "में", "को", "का", "की", "के", "एक", "यह", "ये", "मुझे", "मेरा", "मेरी", "मेरे", "क्या", "है")

_NATIVE_RANGE = re.compile(r"[ऀ-ॿఀ-౿]")
_ZW = dict.fromkeys((0x200C, 0x200D))


def skel(word: str) -> str:
    """A spelling-insensitive key for a Latin word: lower case, accents off, repeated letters collapsed, h after a consonant dropped."""
    w = unicodedata.normalize("NFKD", str(word).lower())
    w = "".join(c for c in w if "a" <= c <= "z")
    w = re.sub(r"(.)\1+", r"\1", w)
    w = re.sub(r"([bcdgkpt])h", r"\1", w)             # kholo = kolo, dhundo = dundo, chesi = cesi
    return w.replace("w", "v")


def _build() -> dict[str, tuple[str, float, str]]:
    verbs: dict[str, tuple[str, float, str]] = {}
    for intent, forms in LEXICON.items():
        for form, conf in forms:
            key = skel(form)
            if key in verbs and verbs[key][0] != intent:
                raise ValueError(f"tenglish lexicon: {form!r} collides with {verbs[key][2]!r} ({verbs[key][0]} vs {intent})")
            if key not in verbs or conf > verbs[key][1]:
                verbs[key] = (intent, conf, form)
    return verbs


_VERBS = _build()
_AUX = {skel(w) for w in AUX}
_CONJ = {skel(w) for w in CONJ}
_STOP = {skel(w) for w in STOP}
_MEDIA = {skel(w) for w in MEDIA}
_WEATHER = {skel(w) if w.isascii() else w for w in WEATHER}
_AMBIG = {skel(w) for w in AMBIGUOUS_PLAY}
_DEMO = {skel(k): v for k, v in DEMONSTRATIVE.items()}
_NCONJ, _NAUX, _NSTOP = set(NATIVE_CONJ), set(NATIVE_AUX), set(NATIVE_STOP)
_APPS = {k.translate(_ZW).lower(): v for k, v in APP_WORDS.items()}
_TOKEN = re.compile(r"§\d+§|[ऀ-ॿఀ-౿]+|[A-Za-z0-9]+(?:['’][A-Za-z]+)?")
_QUOTED = re.compile(r"\"([^\"]+)\"|“([^”]+)”|‘([^’]{2,})’")


# ------------------------------------------------------------------------------------------------------------------ detection
@dataclass(frozen=True)
class LangInfo:
    script: str                    # "latin" | "telugu" | "devanagari" | "mixed"
    language: str                  # "en" | "te" | "hi" | "te-rom" | "hi-rom" | "mixed"
    markers: tuple[str, ...] = ()  # the romanized function words/verbs that gave it away


_TE_ROM = {skel(w) for w in ("lo", "ni", "ki", "chesi", "cheyyi", "cheyandi", "rayi", "rayandi", "teruvu", "teravandi", "cheppu", "cheppandi", "chupinchu",
                              "vetuku", "teesey", "teeseyyi", "mooyi", "muyyi", "vesey", "veyyi", "pettu", "mariyu", "tarvata", "naku", "nenu", "meeru",
                              "vatavaranam", "pata", "paata", "ela", "undi", "ento", "emiti", "tegu", "mida", "nunchi", "kosam", "tolagimchu")}
_HI_ROM = {skel(w) for w in ("kholo", "kholiye", "likho", "likhiye", "hatao", "mitao", "dikhao", "dikhaiye", "batao", "bataiye", "chalao", "bajao",
                              "khojo", "dhundo", "karo", "kijiye", "karke", "phir", "mein", "mujhe", "mera", "meri", "kya", "mausam", "gaana")}


def detect(text: str) -> LangInfo:
    """Script and language of a command. Romanized Telugu/Hindi is told by function words and verbs; plain English words are never enough."""
    t = str(text or "")
    te = sum(1 for c in t if 0x0C00 <= ord(c) < 0x0C80)
    hi = sum(1 for c in t if 0x0900 <= ord(c) < 0x0980)
    lat = sum(1 for c in t if "a" <= c.lower() <= "z")
    if te and hi:
        return LangInfo("mixed", "mixed")
    if te:
        return LangInfo("telugu" if not lat else "mixed", "te")
    if hi:
        return LangInfo("devanagari" if not lat else "mixed", "hi")
    words = [skel(w) for w in re.findall(r"[A-Za-z]+", t)]
    te_hits = [w for w in words if w in _TE_ROM]
    hi_hits = [w for w in words if w in _HI_ROM]
    if te_hits and len(te_hits) >= len(hi_hits):
        return LangInfo("latin", "te-rom", tuple(dict.fromkeys(te_hits)))
    if hi_hits:
        return LangInfo("latin", "hi-rom", tuple(dict.fromkeys(hi_hits)))
    return LangInfo("latin", "en")


# ------------------------------------------------------------------------------------------------------------------ parsing
@dataclass
class Step:
    intent: str
    target: str = ""               # app, file, site, song, query ...
    text: str = ""                 # what to type
    app: str = ""                  # for "type": the app to type into (opened first)
    confidence: float = 0.95
    word: str = ""                 # the verb as the owner wrote it
    to_bin: bool = False

    def english(self) -> str:
        t = self.target
        if self.intent == "type":
            body = f": {self.text}" if self.text else ""
            return f"Open {self.app} and type{body}" if self.app else f"Type{body}"
        return {
            "open": f"Open {t}", "save": f"Save {t}" if t else "Save the file", "close": f"Close {t}", "show": f"Show {t}",
            "play": f"Play {t}", "search": f"Search for {t}", "tell": f"Tell me {t or 'what you need'}",
            "delete": f"Move {t or 'it'} to the Recycle Bin" if self.to_bin else f"Delete {t}",
        }.get(self.intent, t).strip()


@dataclass
class Parse:
    steps: list[Step] = field(default_factory=list)
    confidence: float = 0.0
    language: str = "en"
    unplaced: list[str] = field(default_factory=list)
    residue: list[str] = field(default_factory=list)       # native-script words left in a place, file or search field: a model must translate them

    @property
    def intents(self) -> list[str]:
        return [s.intent for s in self.steps]

    @property
    def english(self) -> str:
        parts = [s.english() for s in self.steps]
        parts = [parts[0]] + [p[:1].lower() + p[1:] for p in parts[1:]]          # every step starts with a verb word
        return (" and then ".join(parts) + ".") if parts else ""


@dataclass
class _Tok:
    raw: str
    key: str
    start: int
    end: int
    kind: str = "word"             # word | verb | aux | conj | stop | app | quote
    intent: str = ""
    conf: float = 0.0
    value: str = ""
    ambiguous: bool = False


def _app_spans(text: str) -> list[tuple[int, int, str]]:
    low, spans = text.lower(), []
    for phrase in sorted(_APPS, key=len, reverse=True):
        latin = phrase.isascii()
        pat = (r"(?<![A-Za-z0-9])" + re.escape(phrase) + r"(?![A-Za-z0-9])") if latin else re.escape(phrase)    # Telugu glues "lo" on: no end test
        for m in re.finditer(pat, low):
            if all(m.end() <= s or m.start() >= e for s, e, _ in spans):
                spans.append((m.start(), m.end(), _APPS[phrase]))
    return sorted(spans)


def _tokens(text: str, quotes: dict[str, str]) -> list[_Tok]:
    spans, toks = _app_spans(text), []
    for m in _TOKEN.finditer(text):
        inside = next((sp for sp in spans if sp[0] <= m.start() < sp[1]), None)
        if inside:
            if m.start() == inside[0]:
                toks.append(_Tok(text[inside[0]:inside[1]], "", inside[0], inside[1], "app", value=inside[2]))
            continue
        raw = m.group(0)
        if raw in quotes:
            toks.append(_Tok(raw, raw, m.start(), m.end(), "quote", value=quotes[raw]))
        else:
            toks.append(_Tok(raw, raw if _NATIVE_RANGE.search(raw) else skel(raw), m.start(), m.end()))
    return toks


def _edit1(a: str, b: str) -> bool:
    if abs(len(a) - len(b)) > 1:
        return False
    if len(a) == len(b):
        return sum(x != y for x, y in zip(a, b)) <= 1
    s, l = (a, b) if len(a) < len(b) else (b, a)
    i = 0
    while i < len(s) and s[i] == l[i]:
        i += 1
    return s[i:] == l[i + 1:]


def _classify(t: _Tok) -> None:
    if t.kind != "word":
        return
    k = t.key
    if not _NATIVE_RANGE.search(t.raw):
        if k in _VERBS:
            t.kind, (t.intent, t.conf, t.value) = "verb", _VERBS[k]
            t.ambiguous = k in _AMBIG
        elif k in _CONJ:
            t.kind = "conj"
        elif k in _AUX:
            t.kind = "aux"
        elif k in _STOP:
            t.kind = "stop"
        elif len(k) >= 5 and k not in _MEDIA and k not in _WEATHER:
            for vk, (intent, conf, form) in _VERBS.items():                   # one letter off a long verb: a reading, at low confidence
                if len(vk) >= 5 and _edit1(k, vk):
                    t.kind, t.intent, t.conf, t.value, t.ambiguous = "verb", intent, min(conf, 0.7), form, vk in _AMBIG
                    break
        return
    if k in _NCONJ:
        t.kind = "conj"
    elif k in _NAUX:
        t.kind = "aux"
    elif k in _NSTOP:
        t.kind = "stop"
    else:
        for intent, stems in NATIVE.items():
            for stem, conf, amb in stems:
                if conf > 0 and k.startswith(stem):
                    t.kind, t.intent, t.conf, t.value, t.ambiguous = "verb", intent, conf, k, amb
                    return


def _words(args: list[_Tok], src: str) -> str:
    """The original words of the arguments. Neighbours keep the owner's own punctuation ('milk, eggs, bread'); demonstratives become this/that."""
    args = [a for a in args if a.kind in ("word", "app", "quote")]
    if not args:
        return ""
    if all(args[i + 1].start - args[i].end <= 2 for i in range(len(args) - 1)) and not any(a.key in _DEMO for a in args):
        return src[args[0].start:args[-1].end].strip(" ,;")
    return " ".join(_DEMO.get(a.key, a.raw) if a.kind == "word" else a.raw for a in args).strip(" ,;")


def _step(verb: _Tok, args: list[_Tok], src: str, quotes: dict[str, str]) -> Step:
    intent, conf = verb.intent, verb.conf
    app = next((a.value for a in args if a.kind == "app"), "")
    quote = next((a for a in args if a.kind == "quote"), None)
    rest = [a for a in args if a.kind not in ("app", "quote") and a.kind != "stop"]
    keys = {a.key for a in rest}
    media = bool(keys & _MEDIA) or app in ("YouTube", "Spotify")
    if verb.ambiguous:                                                       # vesey / pettu: put, set or play?
        if app == "Recycle Bin":
            obj = _words(rest, src)
            return Step("delete", obj, confidence=0.9, word=verb.raw, to_bin=True)
        conf = 0.9 if media else min(conf, 0.5)
        intent = "play"
    text = _words(rest, src)
    if intent == "type":
        said = quote.value if quote else text
        return Step("type", app, said, app=app, confidence=conf if said else min(conf, 0.5), word=verb.raw)
    if intent == "open":
        target = app or text
        return Step("open", target, confidence=conf if target else min(conf, 0.5), word=verb.raw)
    if intent == "tell":
        place = _words([a for a in rest if a.key not in _WEATHER], src)
        place = place.title() if place.islower() and place.isascii() else place
        if keys & _WEATHER:
            return Step("tell", "the weather" + (f" in {place}" if place else ""), confidence=conf, word=verb.raw)
        said = quote.value if quote else text
        return Step("tell", said, confidence=conf if said else min(conf, 0.5), word=verb.raw)
    target = app or (quote.value if quote else text)
    if intent == "play" and app in ("YouTube", "Spotify") and (quote or text):
        target = f"{quote.value if quote else text} on {app}"
    if intent in ("delete", "play", "search", "close", "show") and (not target or target.lower() in ("this", "that")):
        conf = min(conf, 0.5)                                                # delete WHAT, play WHAT, "this" alone: ask
    return Step(intent, target, confidence=conf, word=verb.raw)


def parse(text: str) -> Parse:
    """Read a command into steps. An empty `steps` means no verb was found (not a command this module understands)."""
    raw = " ".join(str(text or "").translate(_ZW).split())[:600]
    quotes: dict[str, str] = {}

    def stash(m: re.Match) -> str:
        key = f"§{len(quotes)}§"
        quotes[key] = next(g for g in m.groups() if g)
        return key
    src = _QUOTED.sub(stash, raw)
    out = Parse(language=detect(_QUOTED.sub(" ", str(text or ""))).language)
    toks = _tokens(src, quotes)
    for t in toks:
        _classify(t)
    clauses: list[list[_Tok]] = [[]]
    for t in toks:                                                           # "chesi", "and", "aur" ... start the next clause
        if t.kind == "conj":
            if clauses[-1]:
                clauses.append([])
        else:
            clauses[-1].append(t)
    for cl in clauses:
        verbs = [i for i, t in enumerate(cl) if t.kind == "verb"]
        if not verbs:
            out.unplaced += [t.raw for t in cl if t.kind in ("word", "app", "quote")]
            continue
        if all(t.kind in ("aux", "stop") for t in cl[verbs[-1] + 1:]):       # verb-final (Telugu, Hindi): the words before a verb are its own
            bounds = [-1] + verbs
            groups = [(cl[b], cl[a + 1:b]) for a, b in zip(bounds, verbs)]
        else:                                                                # verb-first (English): the words after it, up to the next verb
            groups = []
            for n, v in enumerate(verbs):
                end = verbs[n + 1] if n + 1 < len(verbs) else len(cl)
                groups.append((cl[v], (cl[:verbs[0]] if n == 0 else []) + cl[v + 1:end]))
        for verb, args in groups:
            out.steps.append(_step(verb, args, src, quotes))
    merged: list[Step] = []                                                  # "open X" then "type ..." with no app named: one step
    for st in out.steps:
        prev = merged[-1] if merged else None
        if st.intent == "type" and not st.app and prev and prev.intent == "open" and prev.target:
            st.app, st.confidence = prev.target, min(st.confidence, prev.confidence)
            merged[-1] = st
        else:
            merged.append(st)
    out.steps = merged
    for st in out.steps:                                                     # typed text may stay in its language; anything else must be English
        if st.intent != "type":
            out.residue += re.findall(r"[\u0900-\u097f\u0c00-\u0c7f]+", st.target)
    if out.steps:
        out.confidence = round(min(s.confidence for s in out.steps) * (0.9 ** len(out.unplaced)) * (0.8 ** min(len(out.residue), 3)), 3)
    return out


# ------------------------------------------------------------------------------------------------------------------ command line
def main(argv: list[str] | None = None) -> int:
    """`python -m jarvis.plugins._shared.tenglish "Notepad open chesi milk rayandi"` prints how a phrase is read (for the owner's own phrases)."""
    import json
    import sys
    args = sys.argv[1:] if argv is None else argv
    for phrase in args:
        p = parse(phrase)
        print(json.dumps({"phrase": phrase, "language": p.language, "english": p.english, "confidence": p.confidence, "unplaced": p.unplaced,
                          "steps": [vars(st) for st in p.steps]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
