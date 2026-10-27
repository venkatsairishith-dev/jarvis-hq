"""D75 (2026-10-27): things a person can simply ASK are ANSWERED, instantly, here. No app window, no model round trip, no kernel job, no network.

    "23 times 47"                      -> "23 times 47 is 1,081."
    "15% of 2400"                      -> "15% of 2,400 is 360."
    "what is 1200 divided by 7"        -> "1200 divided by 7 is about 171.4286 (exactly 1200/7, rounded to 4 decimal places)."
    "5 lakh plus 2 lakh"               -> "5,00,000 plus 2,00,000 is 7,00,000 (7 lakh)."
    "convert 100 celsius to fahrenheit"-> "100 °C is 212 °F (°F = °C x 9/5 + 32)."
    "what is the date after 45 days"   -> "45 days from today (Tuesday, 27 October 2026) is Friday, 11 December 2026."
    "EMI of 5 lakh at 9% for 5 years"  -> "The EMI is about 10,379.18 a month (P x r x (1+r)^n / ((1+r)^n - 1), r = 9%/12 a month, n = 60 months, rounded to 2 decimal places)."
    "23 guna 47", "1200 ko 7 se bhaag karo", "100 taggindi 40"  (Tenglish / Hinglish operator words)

Owner, 2026-10-27: "it should do complex tasks, not simple ones like open calculator and do 2+3: can't we just do that by talking to JARVIS? What is the need of
Calculator, be practical." So a plain question is answered here; the Calculator app is used only when the owner NAMES the app or the screen ("in Calculator",
"open calculator", "show me on screen"): those requests are refused here (None) and the catalogue's `calculate` entry runs as before.

The contract, in one place:
  * `answer(text, now=None) -> Answer | None`. None means "not mine" (ambiguous, unit-less, not pure maths / conversion / date, too big, an app was named): the normal path
    runs. It never raises and never guesses. The one exception to "silent": a division by zero is ANSWERED ("that has no answer").
  * Exact: every number is a `fractions.Fraction` (no float anywhere); a root or a power that is not exact is a `decimal.Decimal` at 40 digits. "about" is said only when
    the printed number is rounded, and the sentence says how ("rounded to 4 decimal places", "to the paisa", "to 6 significant digits").
  * No eval, no exec, no `ast` of the owner's text: the text is cut into number / operator / bracket tokens by a regular expression, and a tiny recursive-descent parser
    evaluates them. A word that is not in the small vocabulary, an underscore, a letter variable, an implicit multiplication ("2(3)"), two numbers side by side, a bare
    "%", a name: nothing is evaluated; the answer is None.
  * Caps (so a long or hostile message costs microseconds): 200 characters; numbers of at most 30 digits; at most 120 tokens; brackets nested at most 24 deep; an exponent
    of at most 1,000 and a result of at most 60,000 bits; a factorial of at most 1,000; at most 30 numbers in a list; a loan of at most 50 years.
  * Local only: standard library; `tenglish.skel` for the spelling-insensitive Tenglish / Hinglish operator words; the PC clock for dates and times.
No network and no subprocess: tests monkeypatch `socket` and `subprocess` to fail and every answer still comes.
"""
from __future__ import annotations

import calendar
import datetime as dt
import math
import re
import unicodedata
from dataclasses import dataclass
from decimal import Decimal, localcontext
from fractions import Fraction

from . import tenglish

ENV_FLAG = "JARVIS_DIRECT_ANSWERS"          # 0 / off / false / no turns the module off (the quick path then behaves as before D75)

MAX_INPUT = 200
MAX_DIGITS = 30
MAX_TOKENS = 120
MAX_DEPTH = 24
MAX_EXP = 1000
MAX_BITS = 60000
MAX_FACT = 1000
MAX_LIST = 30
MAX_YEARS = 50
SHOWN_DIGITS = 40                           # an exact integer longer than this is given as "about 1.0715 x 10^301"
PREC = 40


@dataclass(frozen=True)
class Answer:
    text: str                # one short sentence the owner reads and hears
    kind: str                # math | percent | finance | convert | date | time | format
    value: str = ""          # the bare main number for tests and logs ("1081", "171.4286"); "" when the answer is not one number
    approx: bool = False     # the printed number is rounded (the sentence says how)


class Refuse(Exception):
    """Internal: this is not a question this module answers. Becomes None."""


class Said(Exception):
    """Internal: a question this module answers with a sentence that is not a number (division by zero)."""

    def __init__(self, text: str, kind: str = "math"):
        super().__init__(text)
        self.text, self.kind = text, kind


# ------------------------------------------------------------------------------------------------------------------ small tables
_SMALL = {w: i for i, w in enumerate("zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen seventeen eighteen nineteen".split())}
_TENS = {"twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90}
_SCALES = {"hundred": 100, "sau": 100, "thousand": 10 ** 3, "hazaar": 10 ** 3, "hazar": 10 ** 3, "lakh": 10 ** 5, "lakhs": 10 ** 5, "lac": 10 ** 5, "lacs": 10 ** 5,
           "laksha": 10 ** 5, "lakshalu": 10 ** 5, "crore": 10 ** 7, "crores": 10 ** 7, "million": 10 ** 6, "millions": 10 ** 6, "billion": 10 ** 9, "billions": 10 ** 9,
           "trillion": 10 ** 12}
_INDIAN_WORDS = {"lakh", "lakhs", "lac", "lacs", "laksha", "lakshalu", "crore", "crores", "hazaar", "hazar", "sau"}
_NUM = r"(?:\d+(?:\.\d+)?|\.\d+)"
_DIGITWORD = {w: i for w, i in _SMALL.items() if i < 10}

# Tenglish / Hinglish operator words (spelling-insensitive: tenglish.skel folds gunaa = guna, bhaag = bhag, ...)
_sk = tenglish.skel
_OPWORDS: dict[str, str] = {}
for _tag, _words in {
    "x": "guna gunaa gunakaram gunakar gunitam gunna gunincu guninchu gunimchu guninchandi multiply multiplied times into",
    "d": "bhag bhaag bhaga bhagam bhagakaram bhaagam bhaginchu bhagincu bhaginchandi bhagimchu divide divided",
    "p": "jodo jod jodiye jodna joda kalupu kalapandi kalipi kalupandi kalapu jama plus",
    "m": "taggindi tagginchu taggimchu taggimpu tagginchandi tagginchi ghatao ghataiye ghata ghatana minus kam",
}.items():
    for _w in _words.split():
        _OPWORDS[_sk(_w)] = _tag
_POST_OBJ = {_sk(w) for w in "ko ni ki ke".split()}
_POST_WITH = {_sk(w) for w in "se tho toh to saath".split()}
_POST_FROM = {_sk(w) for w in "mein me nundi nunchi".split()}
_CONNECT = {_sk(w) for w in "aur mariyu and".split()}
_RESULT_WORDS = "entha enta enti emiti avutundi avutundhi ayindi ela kitna kitne kitni kya hota hoti hoga hai hain answer result cheppu cheppandi batao bataiye bolo bol sir bro ra na pls plz please ok jarvis".split()
_SOFT = {_sk(w) for w in _RESULT_WORDS} | set(tenglish._AUX) | {_sk(f) for f, _ in tenglish.LEXICON["tell"]}

# explicit app / screen words: the owner asked for the app, so the app is used (the catalogue's `calculate` entry runs, not this module)
_APP_WORDS = re.compile(r"\b(?:calculator|calc|calci|calcu|notepad|wordpad|excel|spreadsheet|paint|powerpoint|chrome|edge|firefox|screen|screens|display|monitor|window|windows|"
                        r"app|application|desktop|clipboard|file|folder|document|sheet|cell|terminal|console|widget)\b")
_ACTION_START = re.compile(r"^(?:open|launch|start|run|type|write|enter|save|copy|paste|press|click|close|quit|play|search|google|email|e-mail|send|remind|schedule|create|make|"
                           r"download|install|put|note|visit|go to|look up|say|speak|read|draw|print)\b")
_NEGATED = re.compile(r"\b(?:don't|dont|do not|never|stop|without|cancel|not)\b")


# ------------------------------------------------------------------------------------------------------------------ the folding of the owner's text
_PRE = str.maketrans({"²": "^2", "³": "^3", "√": " sqrt ", "×": " * ", "÷": " / ", "−": "-", "–": "-", "—": "-", "∕": "/", "⁄": "/", "·": "*", "°": " deg ", "₹": " rs ",
                      "€": " eur ", "’": "'", "‘": "'", "“": '"', "”": '"', "＄": "$"})
_INVISIBLE = {"Cf", "Cc", "Cs", "Co", "Cn"}


def _fold(raw: str) -> str:
    """The owner's text as plain lower-case ASCII, or Refuse. Spoofing (invisible characters, digits of two scripts in one number, letters that only look like English)
    is refused, never repaired."""
    if not isinstance(raw, str) or not raw.strip() or len(raw) > MAX_INPUT:
        raise Refuse
    s = raw.translate(_PRE)
    if any(unicodedata.category(c) in _INVISIBLE and c not in "\t\n\r" for c in s):
        raise Refuse
    s = unicodedata.normalize("NFKC", s)
    if any(unicodedata.category(c) in _INVISIBLE and c not in "\t\n\r" for c in s):
        raise Refuse
    parts = []
    for tok in re.findall(r"\d+|\D+", s):                         # \d is any Unicode decimal digit
        if tok[0].isdigit():
            scripts = {unicodedata.name(c, "").split(" DIGIT")[0] if " DIGIT" in unicodedata.name(c, "") else "ASCII" for c in tok}
            if len(scripts) != 1:
                raise Refuse                                      # "2" next to Arabic-Indic "3" in one number: a spoof
            tok = "".join(str(unicodedata.decimal(c)) for c in tok)
        parts.append(tok)
    s = "".join(parts).lower()
    if not all(ord(c) < 128 for c in s):
        raise Refuse                                              # a Cyrillic "o", Greek letters, Telugu or Hindi that was not translated: not English, not ours
    s = " ".join(s.split())
    s = _group_digits(s)
    return s


_WESTERN = re.compile(r"\d{1,3}(?:,\d{3})+(?:\.\d+)?")
_INDIAN = re.compile(r"\d{1,2}(?:,\d{2})*,\d{3}(?:\.\d+)?")


def _group_digits(s: str) -> str:
    """Thousands separators: "1,000" and "1,00,000" are numbers; "1,5", "12,34", "1.000,50" are locale-ambiguous and refused."""
    def fix(m: re.Match) -> str:
        t = m.group(0)
        if _WESTERN.fullmatch(t) or _INDIAN.fullmatch(t):
            return t.replace(",", "")
        raise Refuse
    return re.sub(r"\d+(?:,\d+)+(?:\.\d+)?", fix, s)


_INDIAN_GROUPED = re.compile(r"(?<![\d,])\d{1,2}(?:,\d{2})+,\d{3}(?![\d,])")


@dataclass
class Ctx:
    s0: str = ""                  # folded, polite words and the question opener removed
    s1: str = ""                  # s0 with number words turned into digits and currency / percent words normalised
    pol: str = ""                 # folded, polite words removed only (the date and time handlers read the opener)
    indian: bool = False          # the owner thinks in lakh and crore: group and say numbers that way
    cur: str = ""                 # a currency symbol the owner used ("₹", "$", "€") or ""
    asked: bool = False           # the text is plainly a question or a request ("what is ...?", "calculate ..."), not a bare string of characters
    money: bool = False
    now: dt.datetime | None = None


_POLITE = re.compile(r"^(?:(?:hey |ok |okay |hi |hello )?jarvis[, ]+|please |kindly |can you |could you |would you |will you |tell me |let me know |just |quickly |i want to know |"
                     r"i need to know |do you know |you know |so |and )+")
_OPENER = re.compile(r"^(?:what(?:'s| is| are| was| will be)?|whats|how much is|how much are|calculate|compute|work out|figure out|find out|find|solve|evaluate|give me|get me|"
                     r"the answer to|the result of|result of|answer to)\s+")
_SUFFIX = re.compile(r"(?:\s*(?:=|\?|(?<![\d)])!|\.|,)+|\s+(?:please|pls|plz|for me|jarvis|equals|is equal to|equal to|thanks|thank you))+$")


_ARTICLE = re.compile(r"^(?:a|an|the) (?!hundred\b|thousand\b|lakh|crore|million|billion|trillion|half\b|third\b|quarter\b)")


def _trim_soft(s: str) -> str:
    """Tenglish / Hinglish words that only say 'tell me' or 'do it' at either end ("... entha", "... kitna hota hai", "batao ...", "... cheyyi") are dropped."""
    words = s.split()
    while words and words[0].isalpha() and _sk(words[0]) in _SOFT:
        words.pop(0)
    while words and words[-1].isalpha() and _sk(words[-1]) in _SOFT:
        words.pop()
    return " ".join(words)


def _strip(s: str, opener: bool) -> str:
    prev = None
    while prev != s:
        prev = s
        s = _POLITE.sub("", s.strip())
        s = _SUFFIX.sub("", s)
        if opener:
            s = _OPENER.sub("", s)
    return re.sub(r"(?<![\d)])!+$", "", s.strip(" .,?=")).strip()


# ------------------------------------------------------------------------------------------------------------------ number words
def _is_lit(t: str) -> bool:
    return bool(re.fullmatch(_NUM, t))


def _words_to_digits(s: str, ctx: Ctx) -> str:
    """"twenty three" -> "23", "two lakh fifty thousand" -> "250000", "2.5 crore" -> "25000000", "one hundred and five" -> "105", "two point five" -> "2.5"."""
    s = re.sub(r"\b(twenty|thirty|forty|fifty|sixty|seventy|eighty|ninety)-(one|two|three|four|five|six|seven|eight|nine)\b", r"\1 \2", s)
    toks = s.split(" ")
    out: list[str] = []
    i = 0
    n = len(toks)

    def kind(t: str) -> str:
        if t in _SMALL:
            return "small"
        if t in _TENS:
            return "tens"
        if t in _SCALES:
            return "scale"
        return ""

    def starts(j: int) -> bool:
        t = toks[j]
        if kind(t) in ("small", "tens"):
            return True
        if kind(t) == "scale":
            return False
        if _is_lit(t) and j + 1 < n and kind(toks[j + 1]) == "scale":
            return True
        if t in ("a", "an") and j + 1 < n and kind(toks[j + 1]) == "scale":
            return True
        return False

    while i < n:
        if not starts(i):
            out.append(toks[i])
            i += 1
            continue
        total, cur, last_big, j = Fraction(0), Fraction(0), None, i
        prev = ""                                                  # kind of the previous token in this run
        frac_digits = ""
        while j < n:
            t = toks[j]
            k = kind(t)
            if t in ("a", "an") and j == i and j + 1 < n and kind(toks[j + 1]) == "scale":
                cur, prev = Fraction(1), "lit"
            elif _is_lit(t) and j + 1 < n and kind(toks[j + 1]) == "scale" and prev in ("", "scale"):
                cur, prev = cur + Fraction(t), "lit"
            elif k in ("small", "tens") and prev in ("", "scale", "and"):
                v = _SMALL.get(t, _TENS.get(t))
                r = cur % 100
                if r == 0 or (r >= 20 and r % 10 == 0 and v < 10):
                    cur, prev = cur + v, k
                else:
                    break
            elif k in ("small", "tens") and prev == "tens" and t in _SMALL and _SMALL[t] < 10 and cur % 100 >= 20 and cur % 10 == 0:
                cur, prev = cur + _SMALL[t], "small"
            elif k == "scale":
                mult = _SCALES[t]
                if mult == 100:
                    if not (1 <= cur <= 99 or (cur >= 100 and prev == "lit")) and not prev == "lit":
                        break
                    cur, prev = cur * 100, "scale"
                else:
                    if cur == 0 or (last_big is not None and mult >= last_big):
                        break
                    total, cur, last_big, prev = total + cur * mult, Fraction(0), mult, "scale"
                if t in _INDIAN_WORDS:
                    ctx.indian = True
            elif t == "and" and prev == "scale" and j + 1 < n and (kind(toks[j + 1]) in ("small", "tens") or (_is_lit(toks[j + 1]) and j + 2 < n and kind(toks[j + 2]) == "scale")):
                prev = "and"
            elif t == "point" and prev in ("small", "tens", "scale", "lit") and j + 1 < n and (toks[j + 1] in _DIGITWORD or re.fullmatch(r"\d+", toks[j + 1])):
                k2 = j + 1
                while k2 < n and (toks[k2] in _DIGITWORD or re.fullmatch(r"\d+", toks[k2])):
                    frac_digits += str(_DIGITWORD[toks[k2]]) if toks[k2] in _DIGITWORD else toks[k2]
                    k2 += 1
                j = k2
                break
            else:
                break
            j += 1
        if j == i:                                                 # nothing was consumed: a lone word the run could not take
            out.append(toks[i])
            i += 1
            continue
        if prev == "and":                                          # a dangling "and": hand it back
            j -= 1
        value = total + cur
        if frac_digits:
            value += Fraction(int(frac_digits), 10 ** len(frac_digits))
        out.append(_exact_text(value))
        i = j
    return " ".join(out)


def _exact_text(v: Fraction) -> str:
    """A non-negative Fraction with a terminating decimal expansion as plain digits."""
    if v.denominator == 1:
        return str(v.numerator)
    places = _term_places(v.denominator)
    if places is None:
        raise Refuse
    scaled = v * 10 ** places
    s = str(int(scaled)).rjust(places + 1, "0")
    return (s[:-places] + "." + s[-places:]).rstrip("0").rstrip(".")


# ------------------------------------------------------------------------------------------------------------------ formatting numbers
def _term_places(den: int) -> int | None:
    a = b = 0
    while den % 2 == 0:
        den //= 2
        a += 1
    while den % 5 == 0:
        den //= 5
        b += 1
    return max(a, b) if den == 1 else None


def _group(intpart: str, indian: bool) -> str:
    if len(intpart) <= 3:
        return intpart
    head, tail = intpart[:-3], intpart[-3:]
    if indian:
        head = ",".join(reversed([head[max(0, k - 2):k] for k in range(len(head), 0, -2)]))
    else:
        head = ",".join(reversed([head[max(0, k - 3):k] for k in range(len(head), 0, -3)]))
        return (head + "," + tail) if head else tail
    return head + "," + tail


def _round_half_up(v: Fraction, places: int) -> Fraction:
    scaled = abs(v) * 10 ** places
    q = math.floor(scaled + Fraction(1, 2))
    r = Fraction(q, 10 ** places)
    return -r if v < 0 else r


def _fixed(v: Fraction, places: int, indian: bool, trim: bool = False) -> str:
    """v has at most `places` decimals. Grouped integer part, a decimal point, the decimals (trimmed of zeros when asked)."""
    neg = v < 0
    scaled = int(abs(v) * 10 ** places)
    s = str(scaled).rjust(places + 1, "0")
    ip, fp = (s[:-places], s[-places:]) if places else (s, "")
    if trim:
        fp = fp.rstrip("0")
    text = _group(ip, indian) + ("." + fp if fp else "")
    return ("-" if neg else "") + text


def _approx_places(v: Fraction) -> int:
    a = abs(v)
    p, t = 4, a
    while t and t < Fraction(1, 10) and p < 24:
        t *= 10
        p += 1
    return p


def _sci(v: Fraction, sig: int = 6) -> str:
    with localcontext() as c:
        c.prec = sig
        d = +(Decimal(v.numerator) / Decimal(v.denominator))
    mant, _, exp = f"{d:.{sig - 1}e}".partition("e")
    mant = mant.rstrip("0").rstrip(".") if "." in mant else mant
    return f"{mant} x 10^{int(exp)}"


@dataclass
class Shown:
    text: str
    approx: bool = False
    how: str = ""
    plain: str = ""

    def __post_init__(self):
        self.plain = self.plain.replace(",", "")                     # the bare number for logs and tests never has thousands separators


def show(v, ctx: Ctx, money: bool = False, places: int | None = None) -> Shown:
    """A number as the owner reads it. `v` is a Fraction (exact) or a Decimal (a root or power that is not exact: always 'about')."""
    forced = isinstance(v, Decimal)
    if forced:
        v = Fraction(v)
    if money:
        exact = (v * 100).denominator == 1
        r = v if exact else _round_half_up(v, 2)
        dp = 2 if (r * 100) % 100 else 0
        text = _fixed(r, dp, ctx.indian)
        return Shown((ctx.cur + text) if ctx.cur else text, not exact, "" if exact else "rounded to 2 decimal places", _fixed(r, 2, False).replace(",", ""))
    if places is not None:
        r = _round_half_up(v, places)
        txt = _fixed(r, places, ctx.indian)
        return Shown(txt, r != v, f"rounded to {places} decimal place{'s' if places != 1 else ''}", _fixed(r, places, False))
    if not forced:
        if v.denominator == 1:
            digits = len(str(abs(v.numerator)))
            if digits > SHOWN_DIGITS:
                return Shown(_sci(v), True, "rounded to 6 significant digits", _sci(v))
            return Shown(_fixed(v, 0, ctx.indian), False, "", str(v.numerator))
        tp = _term_places(v.denominator)
        if tp is not None and tp <= 12 and len(str(abs(v.numerator))) <= 30:
            return Shown(_fixed(v, tp, ctx.indian), False, "", _fixed(v, tp, False))
    if abs(v) >= Fraction(10) ** SHOWN_DIGITS:
        return Shown(_sci(v), True, "rounded to 6 significant digits", _sci(v))
    p = _approx_places(v)
    r = _round_half_up(v, p)
    return Shown(_fixed(r, p, ctx.indian, trim=True), True, f"rounded to {p} decimal places", _fixed(r, p, False, trim=True))


def _about(sh: Shown) -> str:
    return ("about " + sh.text) if sh.approx else sh.text


def _frac_note(v) -> str:
    """"exactly 1200/7, " for a non-terminating rational with a short numerator and denominator."""
    if isinstance(v, Fraction) and v.denominator != 1 and _term_places(v.denominator) is None and len(str(abs(v.numerator))) <= 7 and len(str(v.denominator)) <= 7:
        return f"exactly {v.numerator}/{v.denominator}, "
    return ""


def _lakh_gloss(v, ctx: Ctx) -> str:
    """" (7 lakh)" for an exact result of a question asked in lakh and crore."""
    if not ctx.indian or not isinstance(v, Fraction) or abs(v) < 100000:
        return ""
    for unit, size in (("crore", 10 ** 7), ("lakh", 10 ** 5)):
        if abs(v) >= size:
            q = v / size
            tp = _term_places(q.denominator)
            if tp is not None and tp <= 4:
                return f" ({_fixed(q, tp, False, trim=True)} {unit})"
            return ""
    return ""


_ONES = "zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen seventeen eighteen nineteen".split()
_TENW = "_ _ twenty thirty forty fifty sixty seventy eighty ninety".split()


def _below_1000(n: int) -> str:
    out = []
    if n >= 100:
        out.append(_ONES[n // 100] + " hundred")
        n %= 100
    if n >= 20:
        out.append(_TENW[n // 10] + (("-" + _ONES[n % 10]) if n % 10 else ""))
    elif n or not out:
        out.append(_ONES[n])
    return " ".join(out)


def _in_words(n: int, indian: bool) -> str:
    if n == 0:
        return "zero"
    if n < 0:
        return "minus " + _in_words(-n, indian)
    parts: list[str] = []
    if indian:
        units = (("crore", 10 ** 7), ("lakh", 10 ** 5), ("thousand", 10 ** 3))
        for name, size in units:
            q, n = divmod(n, size)
            if q:
                parts.append((_in_words(q, True) if name == "crore" and q >= 100 else _below_1000(q)) + " " + name)
        if n:
            parts.append(_below_1000(n))
        return " ".join(parts)
    for name, size in (("trillion", 10 ** 12), ("billion", 10 ** 9), ("million", 10 ** 6), ("thousand", 10 ** 3)):
        q, n = divmod(n, size)
        if q:
            parts.append(_below_1000(q) + " " + name)
    if n:
        parts.append(_below_1000(n))
    return " ".join(parts)


# ------------------------------------------------------------------------------------------------------------------ the expression evaluator
_TOK = re.compile(r"\s*(?:(\d+(?:\.\d+)?|\.\d+)|(\*\*)|([-+*/^()!])|([a-z]+))")
_FUNCS = {"sqrt", "cbrt", "abs", "fact"}
_WORD_OPS = {"mod": "mod", "x": "*", "into": "*", "over": "/", "times": "*", "plus": "+", "minus": "-"}
_PHRASES = [
    (r"\bto the power of\b|\braised to the power of\b|\braised to\b|\bto the power\b|\bpower\b", " ^ "),
    (r"\bmultiplied by\b|\bmultiply by\b|\bmultiplied with\b", " * "),
    (r"\bdivided by\b|\bdivide by\b|\bdivided with\b|\bdivided into\b", " / "),
    (r"\bsquare root of\b|\bsquare root\b|\bsqrt of\b|\bsquare-root\b", " sqrt "),
    (r"\bcube root of\b|\bcube root\b|\bcbrt of\b", " cbrt "),
    (r"\bsquared\b", " ^ 2 "), (r"\bcubed\b", " ^ 3 "),
    (r"\bmodulo\b", " mod "),
    (r"\bfactorial of\b|\bfactorial\b", " fact "),
    (r"\babsolute value of\b|\babs of\b|\bmodulus of\b", " abs "),
    (r"\bopen (?:bracket|parenthesis)\b", " ( "), (r"\bclose (?:bracket|parenthesis)\b", " ) "),
]


def _iroot(a: int, n: int) -> int:
    """floor of the n-th root of a >= 0 by bisection (exact integers)."""
    if a < 2 or n == 1:
        return a
    lo, hi = 0, 1 << (a.bit_length() // n + 1)
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if mid ** n <= a:
            lo = mid
        else:
            hi = mid - 1
    return lo


def _exact_root(x: Fraction, n: int) -> Fraction | None:
    if x < 0:
        if n % 2 == 0:
            return None
        r = _exact_root(-x, n)
        return None if r is None else -r
    a, b = _iroot(x.numerator, n), _iroot(x.denominator, n)
    return Fraction(a, b) if a ** n == x.numerator and b ** n == x.denominator else None


def _dec(v) -> Decimal:
    return v if isinstance(v, Decimal) else Decimal(v.numerator) / Decimal(v.denominator)


def _big(v) -> bool:
    return isinstance(v, Fraction) and (v.numerator.bit_length() > MAX_BITS or v.denominator.bit_length() > MAX_BITS)


class _Parser:
    def __init__(self, toks: list[tuple[str, object]]):
        self.t, self.i, self.nodes, self.depth = toks, 0, 0, 0

    # token helpers
    def peek(self):
        return self.t[self.i] if self.i < len(self.t) else ("end", None)

    def take(self):
        tok = self.peek()
        self.i += 1
        return tok

    def tick(self):
        self.nodes += 1
        if self.nodes > MAX_TOKENS:
            raise Refuse

    # grammar
    def parse(self):
        v = self.expr()
        if self.peek()[0] != "end":
            raise Refuse
        return v

    def expr(self):
        self.depth += 1
        if self.depth > MAX_DEPTH:
            raise Refuse
        v = self.term()
        while self.peek() in (("op", "+"), ("op", "-")):
            op = self.take()[1]
            self.tick()
            r = self.term()
            v = self.add(v, r) if op == "+" else self.add(v, self.neg(r))
        self.depth -= 1
        return v

    def term(self):
        v = self.unary()
        while self.peek() in (("op", "*"), ("op", "/"), ("op", "mod")):
            op = self.take()[1]
            self.tick()
            r = self.unary()
            v = self.mul(v, r) if op == "*" else self.div(v, r) if op == "/" else self.mod(v, r)
        return v

    def unary(self):
        if self.peek() in (("op", "-"), ("op", "+")):
            op = self.take()[1]
            self.tick()
            v = self.unary()
            return self.neg(v) if op == "-" else v
        return self.power()

    def power(self):
        base = self.postfix()
        if self.peek() == ("op", "^"):
            self.take()
            self.tick()
            if self.peek() in (("op", "-"), ("op", "+")):
                sign = self.take()[1]
                e = self.power_rhs()
                e = self.neg(e) if sign == "-" else e
            else:
                e = self.power_rhs()
            return self.pow(base, e)
        return base

    def power_rhs(self):
        return self.power()

    def postfix(self):
        v = self.primary()
        while self.peek() == ("op", "!"):
            self.take()
            self.tick()
            v = self.fact(v)
        return v

    def primary(self):
        kind, val = self.take()
        self.tick()
        if kind == "num":
            return val
        if kind == "op" and val == "(":
            v = self.expr()
            if self.take() != ("op", ")"):
                raise Refuse
            self.depth -= 0
            return v
        if kind == "func":
            return self.func(str(val))
        raise Refuse

    def func(self, name: str):
        if self.peek() == ("op", "("):
            self.take()
            arg = self.expr()
            if self.take() != ("op", ")"):
                raise Refuse
        else:                                                       # no brackets: the argument is ONE number and nothing may follow it ("square root of 144 plus 5" is ambiguous)
            neg = False
            if self.peek() == ("op", "-"):
                self.take()
                neg = True
            kind, val = self.take()
            if kind != "num":
                raise Refuse
            arg = self.neg(val) if neg else val
            if self.peek()[0] not in ("end",) and self.peek() != ("op", ")") and self.peek() != ("op", "!"):
                raise Refuse
        return {"sqrt": self.sqrt, "cbrt": self.cbrt, "abs": self.abs, "fact": self.fact}[name](arg)

    # arithmetic (exact where possible)
    @staticmethod
    def neg(v):
        return -v

    @staticmethod
    def abs(v):
        return abs(v)

    def add(self, a, b):
        if isinstance(a, Fraction) and isinstance(b, Fraction):
            r = a + b
        else:
            with localcontext() as c:
                c.prec = PREC
                r = _dec(a) + _dec(b)
        if _big(r):
            raise Refuse
        return r

    def mul(self, a, b):
        if isinstance(a, Fraction) and isinstance(b, Fraction):
            r = a * b
        else:
            with localcontext() as c:
                c.prec = PREC
                r = _dec(a) * _dec(b)
        if _big(r):
            raise Refuse
        return r

    def div(self, a, b):
        if b == 0:
            raise Said("Dividing by zero has no answer.")
        if isinstance(a, Fraction) and isinstance(b, Fraction):
            r = a / b
        else:
            with localcontext() as c:
                c.prec = PREC
                r = _dec(a) / _dec(b)
        if _big(r):
            raise Refuse
        return r

    def mod(self, a, b):
        if not (isinstance(a, Fraction) and isinstance(b, Fraction) and a.denominator == 1 and b.denominator == 1 and a >= 0 and b >= 0):
            raise Refuse
        if b == 0:
            raise Said("Dividing by zero has no answer.")
        return Fraction(a.numerator % b.numerator)

    def fact(self, a):
        if not (isinstance(a, Fraction) and a.denominator == 1 and 0 <= a <= MAX_FACT):
            raise Refuse
        return Fraction(math.factorial(a.numerator))

    def sqrt(self, a):
        if a < 0:
            raise Refuse
        if isinstance(a, Fraction):
            r = _exact_root(a, 2)
            if r is not None:
                return r
        with localcontext() as c:
            c.prec = PREC
            return _dec(a).sqrt()

    def cbrt(self, a):
        if isinstance(a, Fraction):
            r = _exact_root(a, 3)
            if r is not None:
                return r
        if a < 0:
            raise Refuse
        with localcontext() as c:
            c.prec = PREC
            return _dec(a) ** (Decimal(1) / Decimal(3))

    def pow(self, base, e):
        if not isinstance(e, Fraction):
            raise Refuse
        if e.denominator == 1:
            n = e.numerator
            if abs(n) > MAX_EXP:
                raise Refuse
            if isinstance(base, Fraction):
                if base == 0:
                    if n == 0:
                        raise Refuse                                 # 0^0 is not defined: never guess
                    if n < 0:
                        raise Said("Dividing by zero has no answer.")
                    return Fraction(0)
                bits = base.numerator.bit_length() + base.denominator.bit_length()
                if bits * abs(n) > MAX_BITS:
                    raise Refuse
                return base ** n
            with localcontext() as c:
                c.prec = PREC
                if base == 0:
                    raise Refuse
                return base ** n
        if base < 0 or e.denominator > 12 or abs(e.numerator) > MAX_EXP:
            raise Refuse
        if isinstance(base, Fraction):
            r = _exact_root(base, e.denominator)
            if r is not None:
                return self.pow(r, Fraction(e.numerator))
            if base == 0:
                raise Refuse
        with localcontext() as c:
            c.prec = PREC
            return _dec(base) ** (Decimal(e.numerator) / Decimal(e.denominator))


def _tokenise(s: str) -> list[tuple[str, object]]:
    """Cut an arithmetic string into number / operator / bracket / function tokens. Anything else -> Refuse."""
    for pat, rep in _PHRASES:
        s = re.sub(pat, rep, s)
    s = re.sub(r"(?<=\d)x(?=\d)", " * ", s)
    pos, toks = 0, []
    s = s.strip()
    while pos < len(s):
        m = _TOK.match(s, pos)
        if not m or m.end() == pos:
            raise Refuse
        pos = m.end()
        num, pow2, sym, word = m.groups()
        if num is not None:
            if len(num.replace(".", "")) > MAX_DIGITS:
                raise Refuse
            toks.append(("num", Fraction(num)))
        elif pow2:
            toks.append(("op", "^"))
        elif sym:
            toks.append(("op", sym))
        else:
            if word in _FUNCS:
                toks.append(("func", word))
            elif word in _WORD_OPS:
                toks.append(("op", _WORD_OPS[word]))
            else:
                raise Refuse                                        # any other word: a name, a unit, an injection. Never evaluated.
        if len(toks) > MAX_TOKENS:
            raise Refuse
    # adjacency that would be an implicit multiplication or two numbers: refused
    for a, b in zip(toks, toks[1:]):
        if a[0] == "num" and b[0] in ("num", "func") or (a == ("op", ")") and b[0] in ("num", "func")) or (a[0] == "num" and b == ("op", "(")) or (a == ("op", ")") and b == ("op", "(")):
            raise Refuse
        if a[0] == "op" and a[1] in ("+", "-", "*", "/", "^", "mod") and b[0] == "op" and b[1] in ("*", "/", "^", "mod", ")", "!"):
            raise Refuse
    for i, tok in enumerate(toks):                                  # "-2^2": minus two squared, or (minus two) squared? People disagree, so it is not guessed
        if tok == ("op", "-") and (i == 0 or (toks[i - 1][0] == "op" and toks[i - 1][1] not in (")", "!"))) and i + 2 < len(toks) and toks[i + 1][0] == "num" and toks[i + 2] == ("op", "^"):
            raise Refuse
    if not toks or toks[0] == ("op", "*") or toks[0] == ("op", "/") or toks[0] == ("op", "^") or toks[0] == ("op", "mod"):
        raise Refuse
    if toks[-1][0] == "op" and toks[-1][1] not in (")", "!"):
        raise Refuse
    if toks[-1][0] == "func":
        raise Refuse
    return toks


def evaluate(s: str):
    """Value of an arithmetic string (Fraction, or Decimal when a root is not exact). Refuse for anything that is not plain arithmetic."""
    toks = _tokenise(s)
    body = toks[1:] if toks and toks[0] in (("op", "+"), ("op", "-")) else toks
    if len(body) == 1 and body[0][0] == "num":
        raise Refuse                                                # a lone (signed) number is not a question
    if not any(k == "op" and v in ("+", "-", "*", "/", "^", "mod", "!") for k, v in toks) and not any(k == "func" for k, _ in toks):
        raise Refuse
    return _Parser(toks).parse()


def _echo(toks: list[tuple[str, object]], ctx: Ctx) -> str:
    """The question as words: "23 times 47", "(3 plus 4) times 5", "the square root of 144", "-5 plus 3"."""
    out: list[str] = []
    neg = False
    for i, (kind, val) in enumerate(toks):
        prev = toks[i - 1] if i else None
        if kind == "op" and val == "-" and (prev is None or prev[0] == "func" or (prev[0] == "op" and prev[1] not in (")", "!"))) and i + 1 < len(toks) and toks[i + 1][0] == "num":
            neg = True                                               # a minus sign in front of a number: glued to it
        elif kind == "num":
            text = _fixed(val, _term_places(val.denominator) or 0, ctx.indian, trim=True) if val.denominator != 1 else _fixed(val, 0, ctx.indian)
            out.append(("-" if neg else "") + ctx.cur + text)
            neg = False
        elif kind == "func":
            out.append({"sqrt": "the square root of", "cbrt": "the cube root of", "abs": "the absolute value of", "fact": "the factorial of"}[str(val)])
        else:
            out.append({"+": "plus", "-": "minus", "*": "times", "/": "divided by", "^": "to the power of", "mod": "mod", "!": "factorial", "(": "(", ")": ")"}[str(val)])
    return " ".join(out).replace("( ", "(").replace(" )", ")")


# ------------------------------------------------------------------------------------------------------------------ the Indic operator words
_SYM = {"x": "*", "d": "/", "p": "+", "m": "-"}
_INDIC_FORMS = (r"NO?NW?[xdp]", r"NCN[p]", r"NF?W?N[m]", r"N[xdpm]N")          # N number, O object marker (ko, ni), W with (se, tho), F from (mein, nundi), C and (aur), x d p m operators


def _indic(s: str) -> str:
    """"23 guna 47" -> "23 * 47"; "1200 ko 7 se bhaag karo" -> "1200 / 7"; "23 ni 47 tho guna cheyyi" -> "23 * 47"; "100 mein se 30 ghatao" -> "100 - 30"; "50 aur 70 jodo" -> "50 + 70".
    Only when the whole string is two numbers, operator words, postpositions and 'do it' words (reading order of Telugu and Hindi: the operator comes last); anything else is
    returned unchanged (and the evaluator then refuses it if it is not plain arithmetic)."""
    tags: list[str] = []
    nums: list[str] = []
    for w in s.split():
        k = _sk(w) if w.isalpha() else ""
        if _is_lit(w):
            tags.append("N")
            nums.append(w)
        elif w.isalpha() and k in _OPWORDS:
            tags.append(_OPWORDS[k])
        elif w.isalpha() and k in _POST_OBJ:
            tags.append("O")
        elif w.isalpha() and k in _POST_WITH:
            tags.append("W")
        elif w.isalpha() and k in _POST_FROM:
            tags.append("F")
        elif w.isalpha() and k in _CONNECT:
            tags.append("C")
        elif w.isalpha() and k in _SOFT:
            continue
        else:
            return s
    code = "".join(tags)
    if len(nums) != 2:
        return s
    for form in _INDIC_FORMS:
        if re.fullmatch(form, code):
            op = next(t for t in code if t in _SYM)
            return f"{nums[0]} {_SYM[op]} {nums[1]}"
    return s


# ------------------------------------------------------------------------------------------------------------------ money, percent, loans
_CURRENCY_BEFORE = re.compile(r"(?:\brs\.?|\binr\b|\busd\b|\beur\b|\$)\s*(?=\d)")
_CURRENCY_AFTER = re.compile(r"(?<=\d)\s*(?:rupees?|rs\b\.?|inr\b|dollars?|usd\b|euros?|eur\b|bucks?)")
def _prepare(ctx: Ctx, s0: str) -> None:
    """Currency markers out (remembered), percent words as %, number words as digits."""
    s = s0
    cur = ""
    if re.search(r"₹|\brs\b|\binr\b|\brupees?\b", s):
        cur = "₹"
    elif re.search(r"\$|\busd\b|\bdollars?\b|\bbucks?\b", s):
        cur = "$"
    elif re.search(r"\beur\b|\beuros?\b", s):
        cur = "€"
    s = _CURRENCY_BEFORE.sub("", s)
    s = _CURRENCY_AFTER.sub("", s)
    s = re.sub(r"\bper ?cent(?:age)?\b", " % ", s)
    s = _words_to_digits(" ".join(s.split()), ctx)
    s = " ".join(re.sub(r"\s*%\s*", "% ", s).split())
    s = re.sub(rf"^({_NUM}) (?:ka|ki|ke|lo|mein|me) ({_NUM})%$", r"\2% of \1", s)
    ctx.s1 = s
    if cur:
        ctx.cur, ctx.money = cur, True


def _num(s: str) -> Fraction:
    return Fraction(s)


def _pct_of(p: Fraction, n: Fraction) -> Fraction:
    return n * p / 100


def _check_size(*vals) -> None:
    for v in vals:
        if abs(v) > Fraction(10) ** 15:
            raise Refuse


_TAXW = r"(?P<tax>gst|vat|sales tax|tax)"


def _taxname(name: str) -> str:
    return name.upper() if name in ("gst", "vat") else name.title()


def _first(patterns: list[str], s: str) -> re.Match | None:
    for p in patterns:
        m = re.fullmatch(p, s)
        if m:
            return m
    return None


def _h_percent(ctx: Ctx) -> Answer | None:
    """A percentage of a number, a discount, GST / VAT, a tip, a split bill, one number as a percentage of another, up or down by a percentage, a percentage change.
    Every pattern names its parts (p = percent, n = amount, k = people, a / b = the two numbers) and must match the WHOLE question."""
    s = ctx.s1
    N = _NUM
    m = re.fullmatch(rf"(?P<p>{N})% of (?P<n>{N})", s)
    if m:
        p, n = _num(m["p"]), _num(m["n"])
        _check_size(p, n)
        v = _pct_of(p, n)
        sh, shn = show(v, ctx, money=ctx.money), show(n, ctx, money=ctx.money)
        return Answer(f"{_pc(p, ctx)} of {shn.text} is {_about(sh)}" + _hint(sh) + ".", "percent", sh.plain, sh.approx)
    m = _first([rf"(?P<p>{N})% (?:off|discount|discount on|discount off) (?:on |of |for )?(?P<n>{N})", rf"discount of (?P<p>{N})% (?:on|of|for) (?P<n>{N})",
                rf"(?P<n>{N}) (?:with|after|less) (?:a )?(?P<p>{N})% (?:off|discount)", rf"(?:price |cost |amount )?(?:after|with) (?:a )?(?P<p>{N})% discount (?:on|of|for) (?P<n>{N})",
                rf"(?P<n>{N}) (?:after|with) (?P<p>{N})% discount"], s)
    if m:
        p, n = _num(m["p"]), _num(m["n"])
        _check_size(p, n)
        if p > 100:
            raise Refuse
        saved = _pct_of(p, n)
        net = n - saved
        texts, approx = _money_texts([net, saved, n], ctx)
        return Answer(f"A {_pc(p, ctx)} discount on {texts[2]} brings it to {texts[0]}" + (f" (you save {texts[1]})" if saved else "") + ("; amounts rounded to 2 decimal places" if approx else "") + ".",
                      "finance", show(net, ctx, money=True).plain, approx)
    m = _first([rf"(?:add )?(?P<p>{N})% {_TAXW} (?:on|to|for|of) (?P<n>{N})", rf"{_TAXW} (?:of|at) (?P<p>{N})% (?:on|for|to) (?P<n>{N})", rf"{_TAXW} on (?P<n>{N}) (?:at|of|@) (?P<p>{N})%",
                rf"(?P<n>{N}) (?:plus|\+|with) (?P<p>{N})% {_TAXW}"], s)
    if m:
        p, n = _num(m["p"]), _num(m["n"])
        _check_size(p, n)
        tax = _pct_of(p, n)
        texts, approx = _money_texts([tax, n + tax, n], ctx)
        return Answer(f"{_taxname(m['tax'])} at {_pc(p, ctx)} on {texts[2]} is {texts[0]}, so the total is {texts[1]}" + ("; rounded to 2 decimal places" if approx else "") + ".",
                      "finance", show(n + tax, ctx, money=True).plain, approx)
    m = _first([rf"(?:remove|take off|take out|subtract|exclude) (?P<p>{N})% {_TAXW} (?:from|off|out of) (?P<n>{N})",
                rf"(?P<n>{N}) (?:including|inclusive of|with|incl\.?) (?P<p>{N})% {_TAXW}(?: price)?(?:,? (?:price )?(?:before|without|excluding) (?:gst|vat|tax))?",
                rf"(?:price |amount )?(?:before|without|excluding|ex) {_TAXW} (?:for|of|on) (?P<n>{N}) (?:including|inclusive of|with) (?P<p>{N})%",
                rf"(?:price |amount )?(?:before|without|excluding|ex) (?P<p>{N})% {_TAXW} (?:for|of|on|from) (?P<n>{N})"], s)
    if m:
        p, n = _num(m["p"]), _num(m["n"])
        _check_size(p, n)
        base = n / (1 + p / 100)
        tax = n - base
        texts, approx = _money_texts([base, tax, n], ctx)
        return Answer(f"{texts[2]} including {_pc(p, ctx)} {_taxname(m['tax'])} is {texts[0]} before it, with {texts[1]} of it as tax" + ("; rounded to 2 decimal places" if approx else "") + ".",
                      "finance", show(base, ctx, money=True).plain, approx)
    m = _first([rf"(?P<p>{N})% tip (?:on|for|of) (?:a |the )?(?:(?:bill|check|meal|dinner|lunch) )?(?:of )?(?P<n>{N})", rf"tip (?:of |at )?(?P<p>{N})% (?:on|for|of) (?:a |the )?(?:(?:bill|check|meal) )?(?:of )?(?P<n>{N})",
                rf"tip on (?:a |the )?(?:(?:bill|check|meal) )?(?:of )?(?P<n>{N}) (?:at|of|@) (?P<p>{N})%"], s)
    if m:
        p, n = _num(m["p"]), _num(m["n"])
        _check_size(p, n)
        tip = _pct_of(p, n)
        texts, approx = _money_texts([tip, n + tip, n], ctx)
        return Answer(f"A {_pc(p, ctx)} tip on {texts[2]} is {texts[0]}, so the total is {texts[1]}" + ("; rounded to 2 decimal places" if approx else "") + ".", "finance", show(tip, ctx, money=True).plain, approx)
    TIPTAIL = rf"(?: with (?:a )?(?P<t>{N})% tip)?"
    PEOPLE = r"(?: people| persons| ways| friends| of us)?"
    m = _first([rf"split (?:a |the |my |our )?(?:bill |check |total |amount )?(?:of )?(?P<n>{N}) (?:equally |evenly )?(?:between|among|amongst|by|into|in|across) (?P<k>{N}){PEOPLE}{TIPTAIL}",
                rf"(?P<n>{N}) (?:bill )?(?:split|divided) (?:equally |evenly )?(?:between|among|amongst|across) (?P<k>{N}){PEOPLE}{TIPTAIL}",
                rf"(?P<n>{N}) (?:bill )?split (?:by|into|in )?(?P<k>{N}){PEOPLE}{TIPTAIL}",
                rf"split (?:a |the )?(?:bill |check )?(?:of )?(?P<n>{N}) (?P<k>{N}) ways{TIPTAIL}", rf"share (?:a |the )?(?:bill |check )?(?:of )?(?P<n>{N}) (?:equally |evenly )?(?:between|among|amongst) (?P<k>{N}){PEOPLE}{TIPTAIL}"], s)
    if m:
        n, k = _num(m["n"]), _num(m["k"])
        p = _num(m["t"]) if m["t"] else Fraction(0)
        if k.denominator != 1 or not 1 <= k <= 1000:
            raise Refuse
        _check_size(n, p)
        total = n + _pct_of(p, n)
        each = total / k
        texts, approx = _money_texts([each, total], ctx)
        tipbit = f" with a {_pc(p, ctx)} tip" if p else ""
        return Answer(f"Splitting {show(n, ctx, money=True).text} {int(k)} ways{tipbit} is {'about ' if approx else ''}{texts[0]} each" + ("; rounded to 2 decimal places" if approx else "") + ".", "finance",
                      show(each, ctx, money=True).plain, approx)
    m = _first([rf"(?P<a>{N}) is (?:what ?)?% of (?P<b>{N})", rf"(?:what ?)?% of (?P<b>{N}) is (?P<a>{N})", rf"(?P<a>{N}) as a ?% of (?P<b>{N})", rf"(?P<a>{N}) out of (?P<b>{N}) as a ?%",
                rf"(?P<a>{N}) out of (?P<b>{N}) in ?%", rf"(?P<a>{N}) of (?P<b>{N}) as a ?%"], s)
    if m:
        a, b = _num(m["a"]), _num(m["b"])
        if b == 0:
            raise Said("A percentage of zero has no answer.", "percent")
        _check_size(a, b)
        v = a / b * 100
        sh = show(v, ctx)
        return Answer(f"{show(a, ctx).text} is {_about(sh)}% of {show(b, ctx).text}" + _hint(sh, v) + ".", "percent", sh.plain, sh.approx)
    up = _first([rf"(?P<n>{N}) (?:\+|plus|increased by|increase by|raised by|up by|more) (?P<p>{N})%", rf"(?:increase|raise) (?P<n>{N}) by (?P<p>{N})%", rf"(?:increase|raise|add) (?P<p>{N})% (?:to|on) (?P<n>{N})"], s)
    down = _first([rf"(?P<n>{N}) (?:-|minus|decreased by|decrease by|reduced by|reduce by|down by|less) (?P<p>{N})%", rf"(?:decrease|reduce) (?P<n>{N}) by (?P<p>{N})%",
                   rf"(?:subtract|take off|take away) (?P<p>{N})% (?:from|off) (?P<n>{N})"], s)
    for mm, sign in ((up, 1), (down, -1)):
        if mm:
            n, p = _num(mm["n"]), _num(mm["p"])
            _check_size(n, p)
            v = n + sign * _pct_of(p, n)
            sh = show(v, ctx, money=ctx.money)
            return Answer(f"{show(n, ctx, money=ctx.money).text} {'up' if sign > 0 else 'down'} {_pc(p, ctx)} is {_about(sh)}" + _hint(sh) + ".", "percent", sh.plain, sh.approx)
    m = _first([rf"%? ?(?:change|increase|decrease|growth|difference|rise|drop) (?:from|between) (?P<a>{N}) (?:to|and) (?P<b>{N})", rf"(?:the )?(?:change|increase|decrease|growth|difference|rise|drop) (?:in )?% (?:from|between) (?P<a>{N}) (?:to|and) (?P<b>{N})",
                rf"(?P<a>{N}) to (?P<b>{N}) % ?(?:change|increase|decrease|growth)", rf"% (?:change|increase|decrease) (?:of )?(?P<a>{N}) to (?P<b>{N})"], s)
    if m and "%" in s:
        a, b = _num(m["a"]), _num(m["b"])
        if a == 0:
            raise Said("A change from zero has no percentage.", "percent")
        _check_size(a, b)
        v = (b - a) / a * 100
        if v == 0:
            return Answer(f"From {show(a, ctx).text} to {show(b, ctx).text} there is no change.", "percent", "0")
        sh = show(abs(v), ctx)
        word = "an increase" if v > 0 else "a decrease"
        return Answer(f"From {show(a, ctx).text} to {show(b, ctx).text} is {word} of {_about(sh)}%" + _hint(sh, abs(v)) + ".", "percent", sh.plain, sh.approx)
    return None


def _pc(p: Fraction, ctx: Ctx) -> str:
    return show(p, ctx).text + "%"


def _hint(sh: Shown, v=None) -> str:
    if not sh.approx:
        return ""
    ex = _frac_note(v) if v is not None else ""
    return f" ({ex}{sh.how})" if (ex or sh.how) else ""


def _money_texts(vals: list[Fraction], ctx: Ctx) -> tuple[list[str], bool]:
    """Several amounts of one sentence with the same number of decimals; True when any was rounded to 2 places."""
    rounded, approx = [], False
    for v in vals:
        if (v * 100).denominator != 1:
            approx = True
            v = _round_half_up(v, 2)
        rounded.append(v)
    dp = 2 if any((v * 100) % 100 for v in rounded) else 0
    return [(ctx.cur if ctx.cur else "") + _fixed(v, dp, ctx.indian) for v in rounded], approx


_FILLER = {"emi", "of", "for", "on", "a", "the", "loan", "at", "@", "with", "rate", "interest", "p.a.", "pa", "per", "annum", "annual", "year", "years", "yr", "yrs", "month", "months", "mth",
           "mths", "tenure", "over", "monthly", "payment", "installment", "instalment", "calculate", "amount", "is", "what", "my", "principal", "compounded", "simple", "compound", "and",
           "a", "an", "to", "pay", "paying", "borrow", "borrowing", "repay", "if", "i", "lend", "invest", "in", "yearly", "annually", "p", "a.", "%", "of", "take", "taken", "taking", "would", "be"}


def _loan_parts(s: str) -> tuple[Fraction, Fraction, Fraction, str]:
    """(principal, annual rate %, tenure number, 'years' | 'months') from a loan or interest question made only of those three numbers and filler words."""
    toks = re.findall(rf"{_NUM}%?|[a-z.@]+%?", s)
    nums = [t for t in toks if re.match(r"[\d.]", t)]
    words = [t for t in toks if not re.match(r"[\d.]", t)]
    if any(w.strip("%") not in _FILLER for w in words):
        raise Refuse
    rate = [t for t in nums if t.endswith("%")]
    plain = [t for t in nums if not t.endswith("%")]
    if not rate and "%" in s:
        raise Refuse
    unit = re.search(rf"({_NUM}) ?(years?|yrs?|months?|mths?)\b", s)
    if len(rate) != 1 or not unit or len(plain) != 2:
        raise Refuse
    tenure = _num(unit.group(1))
    if unit.group(1) in plain:
        plain.remove(unit.group(1))
    if len(plain) != 1:
        raise Refuse
    return _num(plain[0]), _num(rate[0].rstrip("%")), tenure, ("years" if unit.group(2).startswith("y") else "months")


def _h_loans(ctx: Ctx) -> Answer | None:
    s = ctx.s1
    if re.search(r"\bemi\b|\bmonthly (?:payment|installment|instalment)", s):
        P, rate, t, unit = _loan_parts(s)
        months = t * 12 if unit == "years" else t
        if months.denominator != 1 or not 1 <= months <= MAX_YEARS * 12 or not 0 <= rate <= 100 or not 0 < P <= 10 ** 12:
            raise Refuse
        n, r = int(months), rate / 1200
        emi = P / n if r == 0 else P * r * (1 + r) ** n / ((1 + r) ** n - 1)
        texts, approx = _money_texts([emi], ctx)
        formula = "P x r x (1+r)^n / ((1+r)^n - 1)" if r else "P / n"
        rtxt = f"{show(rate, ctx).text}%/12" if r else "0%"
        how = ", rounded to 2 decimal places" if approx else ""
        return Answer(f"The EMI is {'about ' if approx else ''}{texts[0]} a month ({formula}, r = {rtxt} a month, n = {n} months{how}).", "finance", show(emi, ctx, money=True).plain, approx)
    m = re.search(r"\bsimple interest\b", s)
    if m:
        P, rate, t, unit = _loan_parts(s)
        years = t / 12 if unit == "months" else t
        if not 0 <= rate <= 100 or not 0 < P <= 10 ** 12 or years > MAX_YEARS:
            raise Refuse
        interest = P * rate * years / 100
        texts, approx = _money_texts([interest, P + interest], Ctx(indian=ctx.indian, cur=ctx.cur))
        return Answer(f"The simple interest is {texts[0]} (P x R x T / 100), so the total is {texts[1]}" + ("; rounded to 2 decimal places" if approx else "") + ".", "finance",
                      show(interest, ctx, money=True).plain, approx)
    m = re.search(r"\bcompound interest\b", s)
    if m:
        P, rate, t, unit = _loan_parts(s)
        years = t / 12 if unit == "months" else t
        if years.denominator != 1 or not 0 <= rate <= 100 or not 0 < P <= 10 ** 12 or not 0 < years <= MAX_YEARS:
            raise Refuse
        amount = P * (1 + rate / 100) ** int(years)
        texts, approx = _money_texts([amount - P, amount], Ctx(indian=ctx.indian, cur=ctx.cur))
        return Answer(f"The compound interest, compounded yearly, is {texts[0]} (P x (1 + R/100)^T - P), so the total is {texts[1]}" + ("; rounded to 2 decimal places" if approx else "") + ".", "finance",
                      show(amount - P, ctx, money=True).plain, approx)
    return None


# ------------------------------------------------------------------------------------------------------------------ units
_LENGTH = {"mm": (Fraction(1, 1000), "millimetre", "mm"), "cm": (Fraction(1, 100), "centimetre", "cm"), "m": (Fraction(1), "metre", "m"), "km": (Fraction(1000), "kilometre", "km"),
           "in": (Fraction(254, 10000), "inch", "inches"), "ft": (Fraction(3048, 10000), "foot", "feet"), "yd": (Fraction(9144, 10000), "yard", "yards"),
           "mi": (Fraction(1609344, 1000), "mile", "miles"), "nmi": (Fraction(1852), "nautical mile", "nautical miles")}
_MASS = {"mg": (Fraction(1, 10 ** 6), "milligram", "mg"), "g": (Fraction(1, 1000), "gram", "g"), "kg": (Fraction(1), "kilogram", "kg"), "tonne": (Fraction(1000), "tonne", "tonnes"),
         "oz": (Fraction(28349523125, 10 ** 12), "ounce", "ounces"), "lb": (Fraction(45359237, 10 ** 8), "pound", "pounds"), "st": (Fraction(45359237 * 14, 10 ** 8), "stone", "stone")}
_VOLUME = {"ml": (Fraction(1, 1000), "millilitre", "mL"), "l": (Fraction(1), "litre", "litres"), "gal": (Fraction(3785411784, 10 ** 9), "US gallon", "US gallons"),
           "qt": (Fraction(946352946, 10 ** 9), "US quart", "US quarts"), "pt": (Fraction(473176473, 10 ** 9), "US pint", "US pints"), "cup": (Fraction(2365882365, 10 ** 10), "US cup", "US cups"),
           "floz": (Fraction(295735295625, 10 ** 13), "US fluid ounce", "US fluid ounces"), "tbsp": (Fraction(147867647813, 10 ** 13) , "US tablespoon", "US tablespoons"),
           "tsp": (Fraction(492892159375, 10 ** 14), "US teaspoon", "US teaspoons")}
_SPEED = {"mps": (Fraction(1), "metre per second", "m/s"), "kmh": (Fraction(10, 36), "kilometre per hour", "km/h"), "mph": (Fraction(44704, 100000), "mile per hour", "mph"),
          "knot": (Fraction(1852, 3600), "knot", "knots")}
_DATA = {"bit": (Fraction(1, 8), "bit", "bits"), "byte": (Fraction(1), "byte", "bytes"), "kb": (Fraction(1024), "KB", "KB"), "mb": (Fraction(1024 ** 2), "MB", "MB"),
         "gb": (Fraction(1024 ** 3), "GB", "GB"), "tb": (Fraction(1024 ** 4), "TB", "TB"), "pb": (Fraction(1024 ** 5), "PB", "PB")}
_TIME = {"ms": (Fraction(1, 1000), "millisecond", "ms"), "s": (Fraction(1), "second", "seconds"), "min": (Fraction(60), "minute", "minutes"), "h": (Fraction(3600), "hour", "hours"),
         "day": (Fraction(86400), "day", "days"), "week": (Fraction(604800), "week", "weeks")}
_CALENDAR = {"month": (Fraction(1), "month", "months"), "year": (Fraction(12), "year", "years"), "decade": (Fraction(120), "decade", "decades"), "century": (Fraction(1200), "century", "centuries")}
_AREA = {"sqm": (Fraction(1), "square metre", "square metres"), "sqkm": (Fraction(10 ** 6), "square kilometre", "square kilometres"), "sqft": (Fraction(9290304, 10 ** 8), "square foot", "square feet"),
         "sqyd": (Fraction(83612736, 10 ** 8), "square yard", "square yards"), "acre": (Fraction(40468564224, 10 ** 7), "acre", "acres"), "hectare": (Fraction(10000), "hectare", "hectares"),
         "sqmi": (Fraction(2589988110336, 10 ** 6), "square mile", "square miles")}
_KINDS = {"length": _LENGTH, "mass": _MASS, "volume": _VOLUME, "speed": _SPEED, "data": _DATA, "time": _TIME, "calendar": _CALENDAR, "area": _AREA}
_ALIASES = {
    "mm": "mm millimeter millimeters millimetre millimetres", "cm": "cm centimeter centimeters centimetre centimetres", "m": "m meter meters metre metres",
    "km": "km kms kilometer kilometers kilometre kilometres", "in": "in inch inches", "ft": "ft foot feet", "yd": "yd yds yard yards", "mi": "mi mile miles", "nmi": "nautical mile nautical miles",
    "mg": "mg milligram milligrams", "g": "g gram grams gm gms", "kg": "kg kgs kilo kilos kilogram kilograms kilogramme", "tonne": "tonne tonnes metric ton metric tons",
    "oz": "oz ounce ounces", "lb": "lb lbs pound pounds", "st": "stone stones",
    "ml": "ml milliliter milliliters millilitre millilitres", "l": "l ltr liter liters litre litres", "gal": "gal gallon gallons", "qt": "qt quart quarts", "pt": "pint pints",
    "cup": "cup cups", "floz": "fl oz floz fluid ounce fluid ounces", "tbsp": "tbsp tablespoon tablespoons", "tsp": "tsp teaspoon teaspoons",
    "mps": "m/s mps meters per second metres per second meter per second metre per second", "kmh": "km/h kmh kmph kph kilometers per hour kilometres per hour kilometer per hour kilometre per hour km per hour kms per hour",
    "mph": "mph miles per hour mile per hour mi/h", "knot": "knot knots kt kts",
    "bit": "bit bits", "byte": "byte bytes", "kb": "kb kilobyte kilobytes kib", "mb": "mb megabyte megabytes mib", "gb": "gb gigabyte gigabytes gib", "tb": "tb terabyte terabytes tib", "pb": "pb petabyte petabytes",
    "ms": "ms msec millisecond milliseconds", "s": "s sec secs second seconds", "min": "min mins minute minutes", "h": "h hr hrs hour hours", "day": "day days", "week": "week weeks",
    "month": "month months", "year": "year years yr yrs", "decade": "decade decades", "century": "century centuries",
    "sqm": "sqm sq m sq meter sq metre square meter square meters square metre square metres m2 m^2", "sqkm": "sqkm sq km square kilometer square kilometers square kilometre square kilometres km2 km^2",
    "sqft": "sqft sq ft square foot square feet ft2 ft^2", "sqyd": "sqyd sq yd square yard square yards", "acre": "acre acres", "hectare": "hectare hectares ha", "sqmi": "sq mi square mile square miles",
}
_MULTI = ("metric ton", "metric tons", "nautical mile", "nautical miles", "fl oz", "fluid ounce", "fluid ounces", "meters per second", "metres per second", "meter per second",
          "metre per second", "kilometers per hour", "kilometres per hour", "kilometer per hour", "kilometre per hour", "km per hour", "kms per hour", "miles per hour", "mile per hour",
          "sq m", "sq meter", "sq metre", "square meter", "square meters", "square metre", "square metres", "sq km", "square kilometer", "square kilometers", "square kilometre",
          "square kilometres", "sq ft", "square foot", "square feet", "sq yd", "square yard", "square yards", "sq mi", "square mile", "square miles")


def _split_aliases(names: str) -> list[str]:
    """The alias words of one unit: the multi word ones are taken out first (longest first), the rest are single words."""
    out, rest = [], f" {names} "
    for phrase in sorted(_MULTI, key=len, reverse=True):
        if f" {phrase} " in rest:
            out.append(phrase)
            rest = rest.replace(f" {phrase} ", " ", 1)
    return out + rest.split()


_UNIT_OF: dict[str, tuple[str, str]] = {}            # alias -> (kind, canonical unit)
for _canon, _names in _ALIASES.items():
    _kind = next(k for k, tbl in _KINDS.items() if _canon in tbl)
    for _alias in _split_aliases(_names):
        _UNIT_OF[_alias] = (_kind, _canon)

_TEMP = {"c": "c", "celsius": "c", "celcius": "c", "centigrade": "c", "f": "f", "fahrenheit": "f", "farenheit": "f", "k": "k", "kelvin": "k"}
_UNIT_RX = "|".join(re.escape(u) for u in sorted(set(_UNIT_OF) | set(_TEMP), key=len, reverse=True))
_QTY = rf"(?:-?{_NUM}|an?|one)"
_MAGNITUDE = {"thousand": 10 ** 3, "thousands": 10 ** 3, "lakh": 10 ** 5, "lakhs": 10 ** 5, "lac": 10 ** 5, "lacs": 10 ** 5, "crore": 10 ** 7, "crores": 10 ** 7,
              "million": 10 ** 6, "millions": 10 ** 6, "billion": 10 ** 9, "billions": 10 ** 9, "trillion": 10 ** 12, "trillions": 10 ** 12}
_MAG_NAME = {10 ** 3: "thousand", 10 ** 5: "lakh", 10 ** 7: "crore", 10 ** 6: "million", 10 ** 9: "billion", 10 ** 12: "trillion"}
_TEMP_NAME = {"c": "°C", "f": "°F", "k": "K"}


def _qty(t: str) -> Fraction:
    return Fraction(1) if t in ("a", "an", "one") else Fraction(t)


def _h_convert(ctx: Ctx) -> Answer | None:
    s = ctx.s1
    s = re.sub(r"\b(?:degrees?|deg) (celsius|celcius|centigrade|fahrenheit|farenheit|kelvin|c|f|k)\b", r"\1", s)
    s = re.sub(r"(?<=\d)(?=[a-z°])", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    s = re.sub(r"^convert(?:ing)? ", "", s)
    s = re.sub(r"^(?:how much is|how much are) ", "", s)
    m = (re.fullmatch(rf"({_QTY}) ({_UNIT_RX}) (?:to|into|in|as|=|equals?|is how many|in how many|is how much in) ({_UNIT_RX})", s)
         or re.fullmatch(rf"how many ({_UNIT_RX}) (?:are |is )?(?:there )?(?:in|are in|is in|make up|in a|in an) (?:an? |one )?(?:({_NUM}) )?({_UNIT_RX})", s)
         or re.fullmatch(rf"({_QTY}) ({_UNIT_RX}) how many ({_UNIT_RX})", s))
    if not m:
        return None
    g = m.groups()
    if s.startswith("how many"):
        to_u, q, from_u = g[0], g[1] or "1", g[2]
    else:
        q, from_u, to_u = g
    qv = _qty(q)
    _check_size(qv)
    # temperature
    if from_u in _TEMP and to_u in _TEMP:
        a, b = _TEMP[from_u], _TEMP[to_u]
        c = qv if a == "c" else (qv - 32) * 5 / 9 if a == "f" else qv - Fraction(27315, 100)
        if c < Fraction(-27315, 100):
            raise Refuse                                         # below absolute zero: not a question this module answers
        out = c if b == "c" else c * 9 / 5 + 32 if b == "f" else c + Fraction(27315, 100)
        sh = show(out, ctx)
        if a == b:
            return Answer(f"{show(qv, ctx).text} {_TEMP_NAME[a]} is {show(qv, ctx).text} {_TEMP_NAME[b]}.", "convert", show(qv, ctx).plain)
        formula = {("c", "f"): "°F = °C x 9/5 + 32", ("f", "c"): "°C = (°F - 32) x 5/9", ("c", "k"): "K = °C + 273.15", ("k", "c"): "°C = K - 273.15",
                   ("f", "k"): "K = (°F - 32) x 5/9 + 273.15", ("k", "f"): "°F = (K - 273.15) x 9/5 + 32"}[(a, b)]
        extra = f"; {sh.how}" if sh.approx else ""
        return Answer(f"{show(qv, ctx).text} {_TEMP_NAME[a]} is {_about(sh)} {_TEMP_NAME[b]} ({formula}{extra}).", "convert", sh.plain, sh.approx)
    if from_u in _TEMP or to_u in _TEMP or from_u not in _UNIT_OF or to_u not in _UNIT_OF:
        return None
    k1, c1 = _UNIT_OF[from_u]
    k2, c2 = _UNIT_OF[to_u]
    if k1 != k2:
        return None                                              # metres to kilograms, a day to a year: never guessed
    tbl = _KINDS[k1]
    f1, f2 = tbl[c1][0], tbl[c2][0]
    out = qv * f1 / f2
    sh = show(out, ctx)
    n1 = tbl[c1][1] if qv == 1 else tbl[c1][2]
    n2 = tbl[c2][1] if out == 1 else tbl[c2][2]
    parts = [] if (qv == 1 and not sh.approx) else [_unit_relation(tbl, c1, c2, ctx)]       # "1 day is 86,400 seconds" already is the relation
    if k1 == "data" and c1 != "bit" and c2 != "bit":
        parts.append("counting 1,024 per step as Windows does")
    if sh.approx:
        parts.append(sh.how)
    tail = f" ({'; '.join(parts)})" if parts else ""
    return Answer(f"{show(qv, ctx).text} {n1} is {_about(sh)} {n2}{tail}.", "convert", sh.plain, sh.approx)


def _unit_relation(tbl: dict, c1: str, c2: str, ctx: Ctx) -> str:
    """"1 inch = 2.54 cm": the direction whose ratio is exact when only one is (1 hour = 60 minutes, not 1 minute = 0.01667 hours)."""
    r = tbl[c1][0] / tbl[c2][0]
    inv = 1 / r
    exact = _term_places(r.denominator) is not None and _term_places(r.denominator) <= 12
    exact_inv = _term_places(inv.denominator) is not None and _term_places(inv.denominator) <= 12
    if not exact and exact_inv:
        a, b, ratio = c2, c1, inv
    else:
        a, b, ratio = c1, c2, r
    sh = show(ratio, ctx)
    return f"1 {tbl[a][1]} = {_about(sh)} {tbl[b][1] if ratio == 1 else tbl[b][2]}"


def _h_magnitude(ctx: Ctx) -> Answer | None:
    s0 = re.sub(r"^convert ", "", ctx.s0)
    m = re.fullmatch(r"(.+?) (?:to|in|into|as) (thousands?|lakhs?|lacs?|crores?|millions?|billions?|trillions?)", s0)
    if not m:
        return None
    qs, target = m.group(1), m.group(2)
    probe = Ctx()
    try:
        q = _words_to_digits(re.sub(r"\s+", " ", qs).strip(), probe)
    except Refuse:
        return None
    if not re.fullmatch(_NUM, q):
        return None
    ctx.indian = ctx.indian or probe.indian or target.rstrip("s") in ("lakh", "lac", "crore")
    qv = Fraction(q)
    size = _MAGNITUDE[target if target in _MAGNITUDE else target + "s"] if (target in _MAGNITUDE or target + "s" in _MAGNITUDE) else _MAGNITUDE[target.rstrip("s")]
    out = qv / size
    sh = show(out, ctx)
    name = _MAG_NAME[size]
    shown_q = show(qv, ctx).text
    return Answer(f"{shown_q} is {_about(sh)} {name}" + (f" ({sh.how})" if sh.approx else "") + ".", "convert", sh.plain, sh.approx)


# ------------------------------------------------------------------------------------------------------------------ formatting questions
def _h_format(ctx: Ctx) -> Answer | None:
    s = ctx.s1
    s = re.sub(r"^(?:format|write|show|display|print|give)\s+(?:me\s+)?", "", s)
    m = re.fullmatch(rf"({_NUM}) (?:with|using|in) (?:commas|thousands? separators?|digit grouping|indian commas|western commas|(?:the )?(indian|western|international|us) (?:format|style|system|numbering|grouping|commas))", s)
    if m:
        v = Fraction(m.group(1))
        if len(m.group(1).replace(".", "")) > MAX_DIGITS:
            raise Refuse
        indian = "indian" in s
        ip = m.group(1).split(".")[0]
        frac = "." + m.group(1).split(".")[1] if "." in m.group(1) else ""
        text = _group(ip.lstrip("0") or "0", indian) + frac
        return Answer(f"{m.group(1)} written with {'Indian' if indian else 'Western'} commas is {text}.", "format", text.replace(",", ""))
    m = re.fullmatch(rf"({_NUM}) in (?:(?:english|indian|western) )?words|spell(?: out)? ({_NUM})|(?:write|say|spell) ({_NUM}) in (?:(?:english|indian|western) )?words", s)
    if m:
        q = next(x for x in m.groups() if x)
        if "." in q or len(q) > 15:
            raise Refuse
        n = int(q)
        indian = ctx.indian or bool(re.search(r"\bindian\b", ctx.s0)) or n >= 10 ** 15
        if n >= 10 ** 15:
            raise Refuse
        return Answer(f"{_group(str(n), indian)} in words is {_in_words(n, indian)}.", "format", str(n))
    m = (re.fullmatch(rf"round (?:off )?({_NUM}) (?:to|up to|off to) ({_NUM}) decimals?(?: places?)?", s) or re.fullmatch(rf"({_NUM}) rounded (?:off )?(?:to|up to) ({_NUM}) decimals?(?: places?)?", s)
         or re.fullmatch(rf"round (?:off )?({_NUM}) to ({_NUM}) (?:dp|places?|decimal places?)", s))
    if m:
        v, places = Fraction(m.group(1)), Fraction(m.group(2))
        if places.denominator != 1 or not 0 <= places <= 12 or len(m.group(1).replace(".", "")) > MAX_DIGITS:
            raise Refuse
        p = int(places)
        r = _round_half_up(v, p)
        txt = _fixed(r, p, False)
        return Answer(f"{m.group(1)} rounded to {p} decimal place{'s' if p != 1 else ''} is {txt} (halves round up).", "format", txt, r != v)
    m = re.fullmatch(rf"round (?:off )?({_NUM}) (?:to|off to) the nearest (ten|hundred|thousand|10|100|1000|whole number|integer|whole|one)", s)
    if m:
        v = Fraction(m.group(1))
        unit = {"ten": 10, "10": 10, "hundred": 100, "100": 100, "thousand": 1000, "1000": 1000}.get(m.group(2), 1)
        r = Fraction(math.floor(v / unit + Fraction(1, 2))) * unit
        return Answer(f"{m.group(1)} rounded to the nearest {m.group(2)} is {_group(str(int(r)), False)} (halves round up).", "format", str(int(r)), r != v)
    return None


# ------------------------------------------------------------------------------------------------------------------ lists and verbs
_LIST_SEP = r"(?:\s*,\s*(?:and\s+)?|\s+and\s+|\s+&\s+)"


def _numbers_of(text: str) -> list[Fraction]:
    parts = [p for p in re.split(_LIST_SEP, text.strip()) if p != ""]
    if not 2 <= len(parts) <= MAX_LIST or not all(re.fullmatch(rf"-?{_NUM}", p) for p in parts):
        raise Refuse
    return [Fraction(p) for p in parts]


def _join(vals: list[Fraction], ctx: Ctx) -> str:
    texts = [show(v, ctx).text for v in vals]
    return ", ".join(texts[:-1]) + " and " + texts[-1] if len(texts) > 1 else texts[0]


def _h_lists(ctx: Ctx) -> Answer | None:
    s = ctx.s1
    s = re.sub(r"^(?:the )?", "", s)
    m = re.fullmatch(r"(?:add|sum of|sum|total of|total|add up|the sum of) (.+)", s)
    if m and not re.search(r" to ", m.group(1)):
        vals = _numbers_of(m.group(1))
        v = sum(vals, Fraction(0))
        sh = show(v, ctx, money=ctx.money)
        return Answer(f"The sum of {_join(vals, ctx)} is {_about(sh)}" + _hint(sh, v) + ".", "math", sh.plain, sh.approx)
    m = re.fullmatch(r"add (-?" + _NUM + r") to (-?" + _NUM + r")", s)
    if m:
        a, b = Fraction(m.group(1)), Fraction(m.group(2))
        sh = show(a + b, ctx, money=ctx.money)
        return Answer(f"{show(a, ctx).text} plus {show(b, ctx).text} is {_about(sh)}" + _hint(sh, a + b) + ".", "math", sh.plain, sh.approx)
    m = re.fullmatch(r"(?:the )?(average|mean|avg) (?:of )?(.+)", s)
    if m:
        vals = _numbers_of(m.group(2))
        v = sum(vals, Fraction(0)) / len(vals)
        sh = show(v, ctx)
        return Answer(f"The average of {_join(vals, ctx)} is {_about(sh)}" + _hint(sh, v) + ".", "math", sh.plain, sh.approx)
    m = re.fullmatch(r"(?:the )?(max|maximum|largest|biggest|highest|greatest|min|minimum|smallest|lowest|least) (?:number |value )?(?:of|among|in|from|between) (.+)", s)
    if m:
        vals = _numbers_of(m.group(2))
        big = m.group(1) in ("max", "maximum", "largest", "biggest", "highest", "greatest")
        v = max(vals) if big else min(vals)
        sh = show(v, ctx)
        return Answer(f"The {'largest' if big else 'smallest'} of {_join(vals, ctx)} is {sh.text}.", "math", sh.plain)
    m = re.fullmatch(r"(?:the )?(gcd|hcf|greatest common (?:divisor|factor)|highest common factor|lcm|least common multiple|lowest common multiple) (?:of )?(.+)", s)
    if m:
        vals = _numbers_of(m.group(2))
        if any(v.denominator != 1 or v <= 0 or v > 10 ** 15 for v in vals):
            raise Refuse
        ints = [int(v) for v in vals]
        if m.group(1) in ("gcd", "hcf") or m.group(1).startswith(("greatest", "highest")):
            r, name = math.gcd(*ints), "greatest common divisor"
        else:
            r, name = math.lcm(*ints), "least common multiple"
        return Answer(f"The {name} of {_join(vals, ctx)} is {_group(str(r), ctx.indian)}.", "math", str(r))
    m = (re.fullmatch(rf"multiply ({_NUM}) (?:by|and|with|x) ({_NUM})", s) or re.fullmatch(rf"(?:the )?product of ({_NUM}) (?:and|with|x) ({_NUM})", s))
    if m:
        a, b = Fraction(m.group(1)), Fraction(m.group(2))
        sh = show(a * b, ctx, money=ctx.money)
        return Answer(f"{show(a, ctx).text} times {show(b, ctx).text} is {_about(sh)}" + _hint(sh, a * b) + ".", "math", sh.plain, sh.approx)
    m = re.fullmatch(rf"subtract ({_NUM}) from ({_NUM})", s)
    if m:
        a, b = Fraction(m.group(1)), Fraction(m.group(2))
        sh = show(b - a, ctx, money=ctx.money)
        return Answer(f"{show(b, ctx).text} minus {show(a, ctx).text} is {_about(sh)}" + _hint(sh, b - a) + ".", "math", sh.plain, sh.approx)
    m = re.fullmatch(rf"divide ({_NUM}) (?:by|with|into) ({_NUM})", s)
    if m:
        return _arith(f"{m.group(1)} / {m.group(2)}", ctx)
    m = re.fullmatch(rf"(?:the )?remainder (?:when|of) ({_NUM}) (?:is )?divided by ({_NUM})", s)
    if m:
        return _arith(f"{m.group(1)} mod {m.group(2)}", ctx)
    m = re.fullmatch(rf"(?:the )?(square|cube) of (-?{_NUM})", s)
    if m:
        arg = m.group(2) if not m.group(2).startswith("-") else f"({m.group(2)})"
        return _arith(f"{arg} ^ {2 if m.group(1) == 'square' else 3}", ctx)
    m = re.fullmatch(rf"(half|quarter|a third|third|double|twice|triple|thrice) (?:of )?({_NUM})", s)
    if m:
        op = {"half": "/ 2", "quarter": "/ 4", "a third": "/ 3", "third": "/ 3", "double": "* 2", "twice": "* 2", "triple": "* 3", "thrice": "* 3"}[m.group(1)]
        return _arith(f"{m.group(2)} {op}", ctx)
    return None


def _arith(s: str, ctx: Ctx) -> Answer | None:
    toks = _tokenise(s)
    v = evaluate(s)
    sh = show(v, ctx, money=ctx.money)
    echo = _echo(toks, ctx) if len(toks) <= 11 else ""
    lead = (echo[:1].upper() + echo[1:] + " is ") if echo else "That comes to "
    body = f"{lead}{_about(sh)}"
    if sh.approx:
        body += _hint(sh, v)
    else:
        body += _lakh_gloss(v, ctx)
    return Answer(body + ".", "math", sh.plain, sh.approx)


def _h_arithmetic(ctx: Ctx) -> Answer | None:
    s = ctx.s1
    s = _indic(s)
    if "%" in s:
        raise Refuse
    if not ctx.asked and re.fullmatch(r"\d+(?:[-/]\d+)+", s):
        raise Refuse                                                # "555-1234", "12-05-2026", "3/4": a phone number, a date or a fraction? A bare string is not guessed
    if not re.search(r"\d", s):
        return None
    return _arith(s, ctx)


# ------------------------------------------------------------------------------------------------------------------ dates and times
_MONTHS = {m.lower(): i for i, m in enumerate(calendar.month_name) if m}
_MONTHS.update({m.lower(): i for i, m in enumerate(calendar.month_abbr) if m})
_MONTHS["sept"] = 9
_MON_RX = "|".join(sorted(_MONTHS, key=len, reverse=True))
_FIXED_DAYS = {"christmas": (12, 25), "christmas day": (12, 25), "new year": (1, 1), "new years": (1, 1), "new year's": (1, 1), "new years day": (1, 1), "new year's day": (1, 1),
               "new year day": (1, 1), "valentines day": (2, 14), "valentine's day": (2, 14), "halloween": (10, 31), "republic day": (1, 26)}
_DAY_RX = "|".join(sorted((re.escape(k) for k in _FIXED_DAYS), key=len, reverse=True))


def _fmt_date(d: dt.date) -> str:
    return f"{d.strftime('%A')}, {d.day} {d.strftime('%B %Y')}"


def _clock(now: dt.datetime) -> str:
    h = now.hour % 12 or 12
    return f"{h}:{now.minute:02d} {'AM' if now.hour < 12 else 'PM'}"


def _tz(now: dt.datetime) -> str:
    try:
        name = now.tzname() if now.tzinfo is not None else ""
    except Exception:  # noqa: BLE001
        name = ""
    return f" ({name})" if name else ""


def _add_months(d: dt.date, n: int) -> tuple[dt.date, bool]:
    y, m = divmod(d.year * 12 + d.month - 1 + n, 12)
    y, m = y, m + 1
    if not 1000 <= y <= 9999:
        raise Refuse
    last = calendar.monthrange(y, m)[1]
    return dt.date(y, m, min(d.day, last)), d.day > last


def _parse_date(text: str, today: dt.date, *, mode: str) -> dt.date:
    """One date the owner named. mode: 'until' (a year-less date is the next one on or after today), 'since' (the latest on or before today), 'exact' (a year is needed)."""
    t = text.strip()
    if t in ("today", "now"):
        return today
    if t == "tomorrow":
        return today + dt.timedelta(days=1)
    if t == "yesterday":
        return today - dt.timedelta(days=1)
    t2 = t.replace("the ", "", 1) if t.startswith("the ") else t
    if t2 in _FIXED_DAYS:
        mo, da = _FIXED_DAYS[t2]
        return _year_pick(mo, da, None, today, mode)
    m = re.fullmatch(r"(\d{4})-(\d{1,2})-(\d{1,2})", t)
    if m:
        return _mk(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    m = re.fullmatch(r"(\d{1,2})[/.-](\d{1,2})[/.-](\d{4})", t)
    if m:
        a, b, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if a == b:
            return _mk(y, a, b)
        if a > 12 and b <= 12:
            return _mk(y, b, a)
        if b > 12 and a <= 12:
            return _mk(y, a, b)
        raise Refuse                                                # 03/04/2026: day first or month first? Never guessed.
    m = re.fullmatch(rf"(\d{{1,2}})(?:st|nd|rd|th)?(?: of)? ({_MON_RX})\.?(?:,? (\d{{4}}))?", t2)
    if m:
        return _year_pick(_MONTHS[m.group(2)], int(m.group(1)), int(m.group(3)) if m.group(3) else None, today, mode)
    m = re.fullmatch(rf"({_MON_RX})\.? (\d{{1,2}})(?:st|nd|rd|th)?(?:,? (\d{{4}}))?", t2)
    if m:
        return _year_pick(_MONTHS[m.group(1)], int(m.group(2)), int(m.group(3)) if m.group(3) else None, today, mode)
    raise Refuse


def _mk(y: int, m: int, d: int) -> dt.date:
    if not (1000 <= y <= 9999 and 1 <= m <= 12 and 1 <= d <= calendar.monthrange(y, m)[1]):
        raise Refuse
    return dt.date(y, m, d)


def _year_pick(mo: int, da: int, year: int | None, today: dt.date, mode: str) -> dt.date:
    if year is not None:
        return _mk(year, mo, da)
    if mode == "exact":
        raise Refuse
    for y in ((today.year, today.year + 1, today.year + 2, today.year + 3, today.year + 4) if mode == "until" else (today.year, today.year - 1, today.year - 2, today.year - 3, today.year - 4)):
        try:
            d = dt.date(y, mo, da)
        except ValueError:
            continue
        if (mode == "until" and d >= today) or (mode == "since" and d <= today):
            return d
    raise Refuse


_DATE_ANY = rf"(?:\d{{4}}-\d{{1,2}}-\d{{1,2}}|\d{{1,2}}[/.-]\d{{1,2}}[/.-]\d{{4}}|\d{{1,2}}(?:st|nd|rd|th)?(?: of)? (?:{_MON_RX})\.?(?:,? \d{{4}})?|(?:{_MON_RX})\.? \d{{1,2}}(?:st|nd|rd|th)?(?:,? \d{{4}})?|(?:the )?(?:{_DAY_RX})|today|tomorrow|yesterday)"
_WHAT = r"(?:(?:what|which)(?:'s| is| was| will be| would be| will it be| would it be| is it| was it)? )?"
_UNIT_T = r"(day|week|month|year)s?"


def _h_datetime(ctx: Ctx) -> Answer | None:
    now = ctx.now or dt.datetime.now().astimezone()
    today = now.date()
    s = ctx.pol
    s = re.sub(r"[?!.,]+$", "", s).strip()
    s = s.replace("what's", "what is").replace("whats", "what is")
    # the clock
    if re.fullmatch(r"(?:what is |what )?(?:the )?(?:current |local |exact )?time(?: now| right now| is it| it is| is it now| is it right now| today)?|what time is it(?: now| right now| currently)?|tell me the time|time now|current time|what is the time now|what time do you have", s):
        return Answer(f"It is {_clock(now)}{_tz(now)}.", "time", _clock(now))
    m = re.fullmatch(rf"(?:what is |what )?(?:the )?time (?:will it be |would it be |is it )?(?:in|after) ({_NUM}) (hour|minute|min|hr)s?(?: from now)?|what time will it be in ({_NUM}) (hour|minute|min|hr)s?|({_NUM}) (hour|minute|min|hr)s? from now(?: what time)?", s)
    if m:
        g = [x for x in m.groups() if x]
        n, unit = Fraction(g[0]), g[1]
        if n > 24 * 366:
            raise Refuse
        when = now + dt.timedelta(seconds=int(n * (60 if unit in ("minute", "min") else 3600)))
        day = "" if when.date() == today else " tomorrow" if when.date() == today + dt.timedelta(days=1) else f" on {_fmt_date(when.date())}"
        return Answer(f"In {show(n, ctx).text} {'minute' if unit in ('minute', 'min') else 'hour'}{'s' if n != 1 else ''} it will be {_clock(when)}{day}{_tz(now)}.", "time", _clock(when))
    m = re.fullmatch(rf"(?:what time was it|what was the time|the time was|time was|time) ({_NUM}) (hour|minute|min|hr)s? ago|({_NUM}) (hour|minute|min|hr)s? ago(?: what time)?", s)
    if m:
        g = [x for x in m.groups() if x]
        n, unit = Fraction(g[0]), g[1]
        if n > 24 * 366:
            raise Refuse
        when = now - dt.timedelta(seconds=int(n * (60 if unit in ("minute", "min") else 3600)))
        day = "" if when.date() == today else " yesterday" if when.date() == today - dt.timedelta(days=1) else f" on {_fmt_date(when.date())}"
        return Answer(f"{show(n, ctx).text} {'minute' if unit in ('minute', 'min') else 'hour'}{'s' if n != 1 else ''} ago it was {_clock(when)}{day}.", "time", _clock(when))
    # today
    if re.fullmatch(r"(?:what is |what )?(?:the )?(?:today'?s |current |todays )?date(?: today| now| is it| is it today)?|what day is it(?: today| now)?|what day is today|which day is it(?: today)?|today'?s date|date today|what is today|what is today'?s date|what is the day today|what day today|what date is it(?: today)?|what is the day|which day is today|what is the date today", s):
        if re.search(r"\bday\b", s) and "date" not in s:
            return Answer(f"Today is {today.strftime('%A')}, {_fmt_date(today).split(', ', 1)[1]}.", "date", today.strftime("%A"))
        return Answer(f"Today is {_fmt_date(today)}.", "date", today.isoformat())
    m = re.fullmatch(r"(?:what is |what )?(?:the )?(?:current )?(month|year)(?: is it| it is)?(?: now| today)?|what (month|year) is it(?: now| today)?", s)
    if m:
        which = m.group(1) or m.group(2)
        return Answer(f"It is {today.strftime('%B %Y') if which == 'month' else today.year}.", "date", str(today.year) if which == "year" else today.strftime("%B"))
    m = re.fullmatch(r"(?:the )?(day after tomorrow|day before yesterday)|(?:what )?(?:is |was )?(?:the )?(?:date|day)(?: of the week)? (?:of |on )?(tomorrow|yesterday|(?:the )?day after tomorrow|(?:the )?day before yesterday)|(?:what )?(?:is |was )?(tomorrow|yesterday)'?s? (?:date|day)|what (?:day|date) (?:is|was|will it be|will be) (?:it )?(tomorrow|yesterday|(?:the )?day after tomorrow|(?:the )?day before yesterday)|what (?:is )?(?:the )?(date|day) (?:is |will be )?(?:it )?(tomorrow|yesterday)", s)
    if m:
        w = next(x for x in m.groups() if x and x not in ("date", "day"))
        w = re.sub(r"^the ", "", w)
        off = {"tomorrow": 1, "yesterday": -1, "day after tomorrow": 2, "day before yesterday": -2}[w]
        d = today + dt.timedelta(days=off)
        word = {"tomorrow": "Tomorrow is", "yesterday": "Yesterday was", "day after tomorrow": "The day after tomorrow is", "day before yesterday": "The day before yesterday was"}[w]
        return Answer(f"{word} {_fmt_date(d)}.", "date", d.isoformat())
    # a date n days / weeks / months / years from now
    after = (rf"{_WHAT}(?:the )?(?:date|day)(?: of the week)? (?:after|in|from now in|will it be in|will it be after|exactly) ({_NUM}) {_UNIT_T}(?: from (?:now|today))?"
             rf"|{_WHAT}(?:the )?(?:date|day)? ?({_NUM}) {_UNIT_T} (?:from (?:now|today)|later|ahead|hence|from today)"
             rf"|({_NUM}) {_UNIT_T} from (?:now|today)(?: what (?:date|day))?"
             rf"|what (?:will be )?(?:the )?(?:date|day) (?:will it be )?(?:after|in) ({_NUM}) {_UNIT_T}")
    m = re.fullmatch(after, s)
    sign = 1
    if not m:
        before = (rf"{_WHAT}(?:the )?(?:date|day)(?: of the week)? ({_NUM}) {_UNIT_T} ago|({_NUM}) {_UNIT_T} ago(?: what (?:date|day))?|{_WHAT}(?:the )?(?:date|day) before ({_NUM}) {_UNIT_T}")
        m = re.fullmatch(before, s)
        sign = -1
    if m:
        g = [x for x in m.groups() if x]
        n, unit = Fraction(g[0]), g[1]
        if n.denominator != 1 or n > 100000:
            raise Refuse
        n = int(n) * sign
        clamped = False
        if unit == "day":
            d = today + dt.timedelta(days=n)
        elif unit == "week":
            d = today + dt.timedelta(weeks=n)
        elif unit == "month":
            d, clamped = _add_months(today, n)
        else:
            d, clamped = _add_months(today, n * 12)
        label = f"{abs(n)} {unit}{'s' if abs(n) != 1 else ''}"
        rel = "from today" if sign > 0 else "ago"
        tail = " (the last day of that month, since it has fewer days)" if clamped else ""
        return Answer(f"{label} {rel} ({_fmt_date(today)}) is {_fmt_date(d)}{tail}.", "date", d.isoformat())
    # days until / since / between
    m = re.fullmatch(rf"(?:how many )?(days?|weeks?) (?:are |is )?(?:there |left |remaining )?(?:until|till|to|before|left until|left till|left for|remaining until|for|left to) ({_DATE_ANY})|(?:how many )?(days?|weeks?) (?:are )?left (?:for|until|till|to) ({_DATE_ANY})|how many (days?|weeks?) (?:until|till|to|before|are left until|are left till) ({_DATE_ANY})|countdown to ({_DATE_ANY})", s)
    if m:
        g = [x for x in m.groups() if x]
        unit, text = (g[0], g[1]) if len(g) == 2 else ("day", g[0])
        d = _parse_date(text, today, mode="until")
        return _span_answer(today, d, unit, "until", ctx)
    m = re.fullmatch(rf"(?:how many )?(days?|weeks?) (?:has it been |have passed |passed )?since ({_DATE_ANY})|how long (?:has it been )?since ({_DATE_ANY})", s)
    if m:
        g = [x for x in m.groups() if x]
        unit, text = (g[0], g[1]) if len(g) == 2 else ("day", g[0])
        d = _parse_date(text, today, mode="since")
        return _span_answer(d, today, unit, "since", ctx)
    m = re.fullmatch(rf"(?:how many )?(days?|weeks?) (?:are there )?between ({_DATE_ANY}) and ({_DATE_ANY})|(?:how many )?(days?|weeks?) (?:from|between) ({_DATE_ANY}) to ({_DATE_ANY})", s)
    if m:
        g = [x for x in m.groups() if x]
        unit, a, b = g
        d1, d2 = _parse_date(a, today, mode="exact"), _parse_date(b, today, mode="exact")
        return _span_answer(min(d1, d2), max(d1, d2), unit, "between", ctx, d1, d2)
    # the weekday of a date
    m = re.fullmatch(rf"(?:what |which )?(?:day|weekday)(?: of the week)? (?:is|was|will be|falls on|does|did|will) (?:the )?(?:on )?({_DATE_ANY})(?: fall on| fall)?|(?:what |which )?day of (?:the )?week (?:is|was|for|of|on) ({_DATE_ANY})", s)
    if m:
        text = next(x for x in m.groups() if x)
        d = _parse_date(text, today, mode="exact" if not re.search(r"today|tomorrow|yesterday|christmas|new year|valentine|halloween|republic", text) else "until")
        verb = "is" if d >= today else "was"
        return Answer(f"{_fmt_date(d).split(',')[1].strip()} {verb} a {d.strftime('%A')}.", "date", d.strftime("%A"))
    m = re.fullmatch(r"is (\d{4}) a leap year|(?:was|will) (\d{4}) (?:be )?a leap year|(?:is )?(\d{4}) leap year", s)
    if m:
        y = int(next(x for x in m.groups() if x))
        if not 1 <= y <= 9999:
            raise Refuse
        leap = calendar.isleap(y)
        return Answer(f"Yes, {y} is a leap year." if leap else f"No, {y} is not a leap year.", "date", "yes" if leap else "no")
    m = re.fullmatch(rf"(?:how many )?days (?:are )?(?:there )?in ({_MON_RX})(?: (\d{{4}}))?|how many days (?:are )?in ({_MON_RX})(?: (\d{{4}}))?", s)
    if m:
        g = m.groups()
        mon = _MONTHS[g[0] or g[2]]
        y = int(g[1] or g[3]) if (g[1] or g[3]) else today.year
        if not 1 <= y <= 9999:
            raise Refuse
        n = calendar.monthrange(y, mon)[1]
        return Answer(f"{calendar.month_name[mon]} {y} has {n} days.", "date", str(n))
    return None


def _span_answer(a: dt.date, b: dt.date, unit: str, how: str, ctx: Ctx, d1: dt.date | None = None, d2: dt.date | None = None) -> Answer:
    days = (b - a).days
    weeks = unit.startswith("week")
    if how == "until":
        if days == 0:
            return Answer(f"That is today ({_fmt_date(b)}).", "date", "0")
        if days < 0:
            raise Refuse
    if how == "since" and days == 0:
        return Answer(f"That is today ({_fmt_date(a)}).", "date", "0")
    if weeks:
        w, r = divmod(days, 7)
        span = f"{w} week{'s' if w != 1 else ''}" + (f" and {r} day{'s' if r != 1 else ''}" if r else "")
        val = f"{w}w{r}d"
    else:
        span = f"{_group(str(days), ctx.indian)} day{'s' if days != 1 else ''}"
        val = str(days)
    if how == "until":
        return Answer(f"It is {span} until {_fmt_date(b)}.", "date", val)
    if how == "since":
        return Answer(f"It has been {span} since {_fmt_date(a)}.", "date", val)
    return Answer(f"There are {span} between {_fmt_date(d1)} and {_fmt_date(d2)}.", "date", val)


# ------------------------------------------------------------------------------------------------------------------ the entry point
_QUICK_REJECT = re.compile(r"\d|how many|how much|[+*/^√×÷%!]|\b(?:zero|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|thirteen|fourteen|fifteen|sixteen|seventeen|eighteen|nineteen|twenty|thirty|forty|fifty|sixty|seventy|eighty|ninety|"
                           r"hundred|thousand|lakhs?|crores?|million|half|quarter|double|twice|triple|thrice|third|squared?|cubed?|roots?|factorial|time|date|days?|today|tomorrow|yesterday|"
                           r"months?|years?|hours?|minutes?|weeks?|leap|christmas|emi|gst|tip|discount|split|percent|average|sum|total|gcd|hcf|lcm|max|min|largest|smallest|abs|absolute|"
                           r"remainder|multiply|subtract|divide|add|plus|minus|times|divided|mod|power|guna|bhaag|jodo|taggindi)\b")


def enabled(env: dict | None = None) -> bool:
    import os
    env = os.environ if env is None else env
    return str(env.get(ENV_FLAG, "")).strip().lower() not in ("0", "false", "no", "off")


def explicit_app(text: str) -> bool:
    """True when the owner NAMED the app or the screen ("in Calculator", "open calculator", "show me on screen", "type it in Notepad"): the app is used then."""
    low = " ".join(str(text or "").lower().split())
    if not low:
        return False
    return bool(_APP_WORDS.search(low) or _ACTION_START.match(_POLITE.sub("", low)) or _NEGATED.search(low))


_HANDLERS = (_h_datetime, _h_magnitude, _h_convert, _h_loans, _h_percent, _h_format, _h_lists, _h_arithmetic)


def answer(text: str, now: dt.datetime | None = None) -> Answer | None:
    """The direct answer, or None when this is not a plain maths / conversion / date question (or the owner asked for the app). Never raises."""
    try:
        if not isinstance(text, str) or len(text) > MAX_INPUT * 2 or not text.strip():
            return None
        low = text.lower()
        if not _QUICK_REJECT.search(low):
            return None
        s = _fold(text)
        if explicit_app(s):
            return None
        ctx = Ctx(now=now)
        ctx.pol = _trim_soft(_strip(s, opener=False))
        ctx.asked = "?" in text or bool(_OPENER.match(ctx.pol)) or bool(re.match(r"(?:convert|calculate|compute|add|subtract|multiply|divide|sum|average|round|split|how|days|weeks|is)\b", ctx.pol))
        ctx.s0 = _ARTICLE.sub("", _trim_soft(_strip(s, opener=True)))
        if not ctx.s0 or explicit_app(ctx.s0):
            return None
        ctx.indian = bool(_INDIAN_GROUPED.search(text.replace(" ", ""))) or bool(re.search(r"\blakhs?\b|\blacs?\b|\bcrores?\b|\bhaz+aar\b|\blaksha|\bindian\b|\brupees?\b|\brs\b|₹|\binr\b", low))
        _prepare(ctx, ctx.s0)
        if re.fullmatch(r"[\d.]+", ctx.s1) or not ctx.s1:
            return None
        for handler in _HANDLERS:
            got = handler(ctx)
            if got is not None:
                return got
        return None
    except Said as e:
        return Answer(e.text, e.kind)
    except (Refuse, ValueError, ArithmeticError, RecursionError, MemoryError, TypeError, IndexError, KeyError, AttributeError):
        return None
    except Exception:  # noqa: BLE001 - a direct answer may never break the quick path; the normal path runs instead
        return None


def main(argv: list[str] | None = None) -> int:
    """`python -m jarvis.plugins._shared.direct_answer "23 times 47" "15% of 2400"` prints what would be answered (or that the normal path runs)."""
    import sys
    for phrase in (sys.argv[1:] if argv is None else argv):
        got = answer(phrase)
        print(f"{phrase!r} -> {got.text if got else '(not mine: the normal path runs)'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
