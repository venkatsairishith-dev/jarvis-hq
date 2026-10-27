"""Find private values in text, swap them for placeholders such as [PASSWORD_1], and put them back later.

Built on Microsoft Presidio (recognisers with validation: e-mail, phone, card numbers with Luhn, IBAN, IP, crypto) plus
recognisers added here for what Presidio does not cover: passwords/PINs/OTPs written after a cue word, API keys and tokens,
private keys, JWTs, Indian Aadhaar and PAN numbers, and a small PII model (GLiNER-PII) for secrets with no cue word. Names and places are NOT redacted by default (it would wreck ordinary
conversation); set JARVIS_PRIVACY_NAMES=1 to include PERSON and LOCATION.

The mapping lives only in memory for one request. Nothing here logs a value.
"""
from __future__ import annotations

import json
import os
import re
import threading
import time
import urllib.request
from dataclasses import dataclass, field

from presidio_analyzer import AnalyzerEngine, Pattern, PatternRecognizer, RecognizerResult
from presidio_analyzer.nlp_engine import NlpEngineProvider

BASE_ENTITIES = ["EMAIL_ADDRESS", "PHONE_NUMBER", "CREDIT_CARD", "IBAN_CODE", "IP_ADDRESS", "CRYPTO"]
CUSTOM_ENTITIES = ["PASSWORD", "API_KEY", "PRIVATE_KEY", "JWT", "AADHAAR", "PAN"]

# Two small encoder models find secrets written without a cue word ("netflix login is ravi_k88 / Sunny#Day2024"):
#  1. GLiNER-PII small (Knowledgator, Apache-2.0, int8 ONNX, ~20 ms a sentence) proposes candidates generously (0.3);
#  2. the Jev-style decision model (the owner's OpenJev "Verdict" service, a typed choice question) keeps a candidate only if
#     it is a secret rather than ordinary information. Pattern hits are trusted without a check.
# Measured 2026-10-16 on made-up chat messages never used for tuning: patterns alone hid 0 of 12 such secrets; patterns +
# GLiNER 12 of 12 but flagged 12 of 12 ordinary messages; patterns + GLiNER + Jev 12 of 12 and 3 of 12. A false alarm only
# costs the public model one word (the reply is restored locally). Jev unreachable: every candidate is hidden (safe side).
# Model files missing: the patterns still run.
PII_MODEL_DIR = os.environ.get("JARVIS_PRIVACY_MODEL", r"D:\jarvis\models\pii\gliner-pii-small-v1.0")
PII_MODEL_THRESHOLD = 0.3
JEV_URL = os.environ.get("JARVIS_JEV_URL", "http://127.0.0.1:8091/v1/systemone")
JEV_MODEL = os.environ.get("JARVIS_JEV_MODEL", "verdict-1.4")
JEV_SECRET_MIN = 0.5
JEV_SECRET = {"secret": "a password, PIN, passcode, account or ID number, key or token that must stay private",
              "ordinary": "ordinary information such as a time, a price, a quantity, a percentage, a place, a product, a film or a person"}
_SENTENCE = re.compile(r"[^\n]+?(?:[.!?](?=\s)|\n|$)|\n")    # never splits "Mango!Tree77"
PII_LABELS = {"password": "PASSWORD", "username": "USERNAME", "bank account number": "BANK_ACCOUNT",
              "credit card number": "CREDIT_CARD", "passport number": "PASSPORT", "driver's license number": "DRIVING_LICENCE",
              "one-time code": "OTP", "api key": "API_KEY", "street address": "ADDRESS"}
NAME_ENTITIES = ["PERSON", "LOCATION"]
PEOPLE_ENTITIES = ["PERSON"]                         # names="people": people only (a public search query may keep a city or a country)
PLACEHOLDER = re.compile(r"\[([A-Z_]+)_(\d+)\]")
# a value that is only our own marks ("[PERSON_1]", "[REDACTED:password]"): "password: [REDACTED:password]" is not a new secret to tag (D73)
_ONLY_MARKS = re.compile(r"(?:\[(?:[A-Z_]+_\d+|REDACTED:[a-z_]+)\][\s.,;:!?)]*)+")

# "my password is X", "pwd: X", "PIN = 1234", "otp 482913", "api key: X" -> only X is private
_SECRET_AFTER_CUE = (r"(?i)(?:pass(?:word|wd|code|phrase)?|pwd|pin|otp|secret|security code|cvv|api[ _-]?key|access[ _-]?token|token)"
                     r"(?:\s+(?:is|was|=|:|-)|\s*[:=])\s*[\"']?(?P<v>[^\s\"',;]{3,128})")


class _CueRecognizer(PatternRecognizer):
    """Marks only the value after the cue word, not the cue itself."""

    def __init__(self):
        super().__init__(supported_entity="PASSWORD", patterns=[Pattern("secret_after_cue", _SECRET_AFTER_CUE, 0.9)], name="SecretAfterCue")

    def analyze(self, text, entities, nlp_artifacts=None, regex_flags=None):
        out = []
        for m in re.finditer(_SECRET_AFTER_CUE, text):
            end = m.start("v") + len(m.group("v").rstrip(".!?:)"))   # "password = X." -> the full stop is not part of X
            if end - m.start("v") >= 3:
                out.append(RecognizerResult("PASSWORD", m.start("v"), end, 0.9))
        return out


def _custom_recognizers():
    return [
        _CueRecognizer(),
        PatternRecognizer(supported_entity="API_KEY", name="ApiKeys", patterns=[
            Pattern("openai", r"\bsk-(?:proj-)?[A-Za-z0-9_\-]{20,}\b", 0.95),
            Pattern("github", r"\bgh[pousr]_[A-Za-z0-9]{30,}\b", 0.95),
            Pattern("huggingface", r"\bhf_[A-Za-z0-9]{30,}\b", 0.95),
            Pattern("aws", r"\bAKIA[0-9A-Z]{16}\b", 0.95),
            Pattern("google", r"\bAIza[0-9A-Za-z_\-]{35}\b", 0.95),
            Pattern("slack", r"\bxox[abpr]-[A-Za-z0-9\-]{10,}\b", 0.95),
            Pattern("groq", r"\bgsk_[A-Za-z0-9]{40,}\b", 0.95),           # Groq is this gateway's own fallback (audit SEC-D7)
            Pattern("stripe", r"\b(?:sk|rk)_live_[A-Za-z0-9]{16,}\b", 0.95),
            Pattern("bearer", r"(?i)\bbearer\s+[A-Za-z0-9._\-]{20,}\b", 0.8),
        ]),
        PatternRecognizer(supported_entity="PRIVATE_KEY", name="PrivateKey", patterns=[
            # " BLOCK" is how OpenPGP armour ends the line; without it a PGP secret key passed this pattern (audit SEC-D7)
            Pattern("pem", r"-----BEGIN [A-Z ]*PRIVATE KEY(?: BLOCK)?-----[\s\S]+?-----END [A-Z ]*PRIVATE KEY(?: BLOCK)?-----", 0.99)]),
        PatternRecognizer(supported_entity="JWT", name="Jwt", patterns=[
            Pattern("jwt", r"\beyJ[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}\b", 0.95)]),
        PatternRecognizer(supported_entity="AADHAAR", name="Aadhaar", patterns=[
            Pattern("aadhaar", r"\b[2-9]\d{3}[ -]?\d{4}[ -]?\d{4}\b", 0.5)],
            context=["aadhaar", "aadhar", "uidai", "uid"]),
        # Presidio's own phone recogniser scores 0.4 (below the threshold); these are specific enough to trust
        PatternRecognizer(supported_entity="PHONE_NUMBER", name="PhoneStrict", patterns=[
            Pattern("india_mobile", r"(?<![\d+])(?:\+91[\s-]?|0)?[6-9]\d{4}[\s-]?\d{5}(?!\d)", 0.6),
            Pattern("international", r"(?<![\w+])\+\d{1,3}[\s-]?\(?\d{1,4}\)?(?:[\s-]?\d{2,4}){2,4}(?!\d)", 0.6)]),
        PatternRecognizer(supported_entity="PAN", name="IndianPan", patterns=[
            Pattern("pan", r"\b[A-Z]{3}[ABCFGHLJPT][A-Z]\d{4}[A-Z]\b", 0.7)]),
    ]


def sentences(text: str):
    """(offset, sentence) pairs. The PII model reads one sentence at a time: surrounding text lowers its scores (measured)."""
    return [(mm.start(), mm.group(0)) for mm in _SENTENCE.finditer(text) if mm.group(0).strip()]


# Words that name a secret rather than being one: the model sometimes marks them ("the vpn login is ...", "ATM pin ...").
CUE_WORDS = {"password", "passwords", "passcode", "passphrase", "pass", "pwd", "pw", "pin", "otp", "code", "key", "token",
             "login", "username", "user", "id", "vpn", "atm", "wifi", "wi-fi", "account", "number", "card", "cvv", "secret",
             "admin", "root", "ssh", "api"}
# Names of the apps and places the owner asks JARVIS to use, and the plain verbs around them. The deep model marked "Notepad" as a person, "WordPad" as a
# username and the whole of "Open Notepad" as an API key (live 2026-10-26, /redact on the running gateway, the Jev verifier not reachable), so the chat model
# never saw the request and the reply carried "[USERNAME 19]". A span made ONLY of these words is not a secret and is left alone. Nothing else is relaxed:
# "Notepad hunter2" still has a word that is not here.
BENIGN_WORDS = {
    "notepad", "wordpad", "calculator", "calc", "paint", "mspaint", "chrome", "google", "edge", "microsoft", "firefox", "word", "excel", "powerpoint",
    "outlook", "teams", "spotify", "youtube", "vlc", "explorer", "file", "files", "settings", "control", "panel", "task", "manager", "code", "vscode",
    "visual", "studio", "whatsapp", "telegram", "discord", "zoom", "slack", "snipping", "tool", "clock", "calendar", "mail", "photos", "maps", "camera",
    "terminal", "powershell", "cmd", "command", "prompt", "windows", "documents", "downloads", "desktop", "pictures", "videos", "music", "notepad++",
    "open", "launch", "start", "close", "run", "play", "show", "use", "type", "write", "save", "the", "my", "app", "a", "an", "in", "on", "to", "and", "or",
    "editor", "text", "browser", "folder", "window"}
_TRAILING = ".,;:?)\"'"


def _widen(sentence: str, s: int, e: int) -> tuple[int, int]:
    """Grow a span over characters glued to it ("Blue7Whale" + "#"), so no piece of a secret is left behind; sentence
    punctuation at the very end is not taken."""
    while s > 0 and not sentence[s - 1].isspace() and sentence[s - 1] not in "([\"'":
        s -= 1
    while e < len(sentence) and not sentence[e].isspace():
        e += 1
    while e > s and sentence[e - 1] in _TRAILING:
        e -= 1
    return s, e


def model_candidates(model, sentence: str):
    """GLiNER-PII candidates in one sentence, as (kind, start, end, score) relative to the sentence."""
    out = []
    for ent in model.predict_entities(sentence, list(PII_LABELS), threshold=PII_MODEL_THRESHOLD):
        kind = PII_LABELS.get(ent["label"])
        if not kind:
            continue
        s, e = _widen(sentence, ent["start"], ent["end"])
        if len(sentence[s:e].strip()) < 4:       # "AI" became [API_KEY_1] in a research query (seen live); real
            continue                             # secrets this short are caught by the cue patterns ("pin 123")
        words = sentence[s:e].lower().split()
        if words and all(w.strip(_TRAILING) in CUE_WORDS for w in words):
            continue
        if words and all(w.strip(_TRAILING) in BENIGN_WORDS for w in words):       # "Notepad", "Open Notepad", "WordPad": app names, not people or keys
            continue
        if any(o[1] == s and o[2] == e for o in out):
            continue
        out.append((kind, s, e, float(ent["score"])))
    return out


def jev_secret_probabilities(sentence: str, spans: list[str]) -> list[float]:
    """Ask the Jev-style decision model, in one call, whether each span in `sentence` is a secret. Raises if unreachable."""
    questions = {f"q{i}": {"type": "choice", "instructions": f"Is \"{span}\" in this message a secret that must not be shared?",
                           "criteria": JEV_SECRET} for i, span in enumerate(spans)}
    body = {"state": f"Message: {_clip(sentence, 1500)}", "model": JEV_MODEL, "questions": questions}
    req = urllib.request.Request(JEV_URL, data=json.dumps(body).encode("utf-8"), headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=10) as r:
        answers = json.loads(r.read().decode("utf-8"))["answers"]
    return [float(answers[f"q{i}"]["probabilities"]["secret"]) for i in range(len(spans))]


def _clip(text: str, n: int) -> str:
    return text if len(text) <= n else text[:n]


def load_pii_model(path: str = PII_MODEL_DIR):
    """The int8 ONNX GLiNER-PII model, or None (no package, no files): the patterns then work alone."""
    if not path or not os.path.isfile(os.path.join(path, "onnx", "model_quint8.onnx")):
        return None
    try:
        from gliner import GLiNER
        return GLiNER.from_pretrained(path, load_onnx_model=True, load_tokenizer=True, onnx_model_file="onnx/model_quint8.onnx")
    except Exception:                                   # noqa: BLE001 - a broken optional model must not stop redaction
        return None


def _merge_adjacent(spans, text):
    """A secret the model split at a symbol (Mango / ! / Tree77) becomes one span, so the symbol does not leak either."""
    merged = []
    for r in spans:
        gap = text[merged[-1].end:r.start] if merged else None
        if gap is not None and len(gap) <= 2 and not any(ch.isspace() for ch in gap):
            p = merged[-1]
            merged[-1] = RecognizerResult(p.entity_type, p.start, r.end, max(p.score, r.score))
        else:
            merged.append(r)
    return merged


@dataclass
class Mapping:
    """One request's placeholders: value -> placeholder and back. Same value, same placeholder."""
    to_tag: dict = field(default_factory=dict)
    to_value: dict = field(default_factory=dict)
    counts: dict = field(default_factory=dict)
    base: dict = field(default_factory=dict)       # D73: numbers already taken by the screen vault; this request's tags continue after them

    def tag(self, entity: str, value: str) -> str:
        if value in self.to_tag:
            return self.to_tag[value]
        n = self.counts.get(entity, 0) + 1
        self.counts[entity] = n
        t = f"[{entity}_{n + self.base.get(entity, 0)}]"
        self.to_tag[value], self.to_value[t] = t, value
        return t


class Redactor:
    def __init__(self, names: bool | None = None, threshold: float = 0.5, model="auto", verifier="auto"):
        if names is None:
            names = os.environ.get("JARVIS_PRIVACY_NAMES", "0") == "1"
        provider = NlpEngineProvider(nlp_configuration={"nlp_engine_name": "spacy", "models": [{"lang_code": "en", "model_name": "en_core_web_sm"}]})
        self.engine = AnalyzerEngine(nlp_engine=provider.create_engine(), supported_languages=["en"])
        for r in _custom_recognizers():
            self.engine.registry.add_recognizer(r)
        self.model = load_pii_model() if model == "auto" else model
        # verifier(sentence, [span, ...]) -> [probability it is a secret, ...]; None keeps every model candidate
        self.verifier = jev_secret_probabilities if verifier == "auto" else verifier
        self.names = bool(names)                 # people and places hidden on every call (JARVIS_PRIVACY_NAMES=1)
        self.entities = BASE_ENTITIES + CUSTOM_ENTITIES + (NAME_ENTITIES if names else [])
        self.threshold = threshold
        self.stats = {"model_candidates": 0, "kept_by_jev": 0, "dropped_by_jev": 0, "jev_unreachable": 0}
        # Caches (in memory only, like the values themselves): a chat resends its history and a system prompt that changes a
        # little every turn, so whole messages and single sentences are both remembered; only new sentences cost model time.
        self._cache: dict = {}              # text -> spans
        self._seg_cache: dict = {}          # sentence -> confirmed model spans (kind, start, end, score), relative

    def _model_spans(self, text: str):
        out = []
        for off, seg in sentences(text):
            confirmed = self._seg_cache.get(seg)
            if confirmed is None:
                cands = model_candidates(self.model, seg)
                self.stats["model_candidates"] += len(cands)
                unsure = False
                if not cands or self.verifier is None:
                    confirmed = cands
                else:
                    try:                                # one call per sentence: all its candidates as separate questions
                        probs = self.verifier(seg, [seg[s:e] for _, s, e, _ in cands])
                        keep = [p >= JEV_SECRET_MIN for p in probs]
                        self.stats["kept_by_jev"] += sum(keep)
                        self.stats["dropped_by_jev"] += len(keep) - sum(keep)
                        confirmed = [c for c, k in zip(cands, keep) if k]
                    except Exception:                   # noqa: BLE001 - decision model down: hide them (safe side)
                        self.stats["jev_unreachable"] += 1
                        confirmed, unsure = cands, True
                if not unsure:                          # an answer made while the checker was down is not remembered
                    if len(self._seg_cache) > 4096:
                        self._seg_cache.clear()
                    self._seg_cache[seg] = confirmed
            out.extend(RecognizerResult(k, off + s, off + e, sc) for k, s, e, sc in confirmed)
        return out

    def find(self, text: str, deep: bool = True, names: bool | None = None):
        """deep: also run the PII model (+ decision model). The gateway uses it for every message, whatever its role: the
        system prompt carries recalled memory and earlier assistant replies repeat private values (audit MEM-M3). The
        sentence cache keeps a turn fast: only new sentences are read by the model."""
        if not text or not text.strip():
            return []
        deep = deep and self.model is not None
        names = self.names if names is None else (names if names == "people" else bool(names))   # True: also people and places; "people": people only; for this call only
        hit = self._cache.get((text, deep, names))
        if hit is not None:
            return hit
        unreachable_before = self.stats["jev_unreachable"]
        entities = self.entities if names == self.names else BASE_ENTITIES + CUSTOM_ENTITIES + (PEOPLE_ENTITIES if names == "people" else NAME_ENTITIES if names else [])
        results = self.engine.analyze(text=text, language="en", entities=entities, score_threshold=self.threshold)
        if deep:
            results = results + self._model_spans(text)   # overlaps with pattern hits are resolved below
        # keep the strongest, longest non-overlapping spans
        results.sort(key=lambda r: (-(r.end - r.start), -r.score))
        chosen = []
        for r in results:
            if all(r.end <= c.start or r.start >= c.end for c in chosen):
                chosen.append(r)
        chosen = _merge_adjacent(sorted(chosen, key=lambda r: r.start), text)
        if self.stats["jev_unreachable"] == unreachable_before:     # not remembered while the checker is down
            if len(self._cache) > 512:
                self._cache.clear()
            self._cache[(text, deep, names)] = chosen
        return chosen

    def redact(self, text: str, mapping: Mapping, deep: bool = True, names: bool | None = None) -> str:
        spans = self.find(text, deep, names)
        out, pos = [], 0
        for r in spans:
            value = text[r.start:r.end]
            if PLACEHOLDER.fullmatch(value) or _ONLY_MARKS.fullmatch(value):
                continue
            out.append(text[pos:r.start])
            out.append(mapping.tag(r.entity_type, value))
            pos = r.end
        out.append(text[pos:])
        return "".join(out)


def restore(text: str, mapping: Mapping) -> str:
    if not text or not mapping.to_value:
        return text
    return PLACEHOLDER.sub(lambda m: mapping.to_value.get(m.group(0), m.group(0)), text)


# The end of a chunk that could be the start of a placeholder: "[", "[PERS", "[PERSON_", "[PERSON_12" (but not "[link text", "[1]" or a finished tag).
_PARTIAL_TAG = re.compile(r"\[[A-Z_]*\d*\Z")


class StreamRestorer:
    """Restores placeholders in a streamed reply, where a tag like [PASSWORD_1] can arrive split across chunks. Only the end of the text that could
    still become a tag is held back (D73: not every "[" up to 40 characters), so ordinary text and brackets are not delayed."""

    MAX_TAG = 40

    def __init__(self, mapping: Mapping):
        self.mapping, self.buf = mapping, ""

    def feed(self, chunk: str) -> str:
        if not self.mapping.to_value:
            return chunk
        self.buf += chunk or ""
        m = _PARTIAL_TAG.search(self.buf)
        if m is not None and len(self.buf) - m.start() <= self.MAX_TAG:
            ready, self.buf = self.buf[:m.start()], self.buf[m.start():]          # hold a possibly unfinished tag
        else:
            ready, self.buf = self.buf, ""
        return restore(ready, self.mapping)

    def flush(self) -> str:
        out, self.buf = restore(self.buf, self.mapping), ""
        return out


# ---------------------------------------------------------------------------------------------------------------------------------------------
# D73: reversible pseudonymization of SCREEN text, for the owner's own display and voice only.
#
# The online model only ever sees placeholders ("[PERSON_1]", "[NUMBER_2]"). The map placeholder -> real value is kept HERE, in memory, in the
# privacy gateway (never written, never logged, never in a header); when the model's answer reaches the face (the owner's chat bubble and his voice) the
# gateway puts the real values back into the answer's TEXT, and into nothing else (not into tool-call arguments, not for any other caller). Because the
# face keeps the answer it showed and sends it back as history, the same map also hides the real values again in every later request (`hide`), so a real
# value never reaches a provider. Secrets are never put in the map: they stay "[REDACTED:kind]" and cannot be restored by any path.
SCREEN_RESTORABLE = frozenset({"PERSON", "LOCATION", "EMAIL_ADDRESS", "PHONE_NUMBER", "IP_ADDRESS", "ADDRESS", "USERNAME", "NUMBER"})
# Labels the PII model gives to ID-like text. A SHORT digit run or a "XX-12345" reference cannot be a bank account (9 to 18 digits), a card (13 to 19), a
# passport (letters and digits, no separator) or a licence, so it is an ordinary number: [NUMBER_n] instead of [BANK_ACCOUNT_n], restorable. This is what
# makes "order number 73912" a hidden "private" value that the model then refuses or guesses (reliability run 2026-10-27).
SCREEN_ID_LABELS = frozenset({"BANK_ACCOUNT", "PASSPORT", "DRIVING_LICENCE"})
_SHORT_NUMBER = re.compile(r"(?:\d[\d ,.\-]{0,12}|[A-Za-z]{1,4}[-_/]\d{1,10})\Z")


def screen_class(entity: str, value: str):
    """The placeholder kind a screen value gets and may be restored under, or None: a secret, never stored, never restorable.
    An unknown kind is a secret (an allow-list, not a block-list): CREDIT_CARD, IBAN_CODE, PASSWORD (cue words, PINs, codes), API_KEY, PRIVATE_KEY, JWT,
    CRYPTO, AADHAAR, PAN, and the ID labels unless the value is a short number."""
    if entity in SCREEN_RESTORABLE:
        return entity
    if entity in SCREEN_ID_LABELS and _SHORT_NUMBER.match(value or ""):
        digits = re.sub(r"\D", "", value)
        if 1 <= len(digits) <= 8:
            return "NUMBER"
    return None


class ScreenVault:
    """Placeholders created for screen text, with the real values behind them. One per gateway process, memory only.

    Rules: a tag number is never reused for another value (the counters only go up, even after an entry is forgotten), the same value always has the same
    tag, nothing longer than MAX_VALUE characters is stored, at most MAX_ENTRIES are kept (the least recently used go first), an entry is forgotten after
    `ttl_s` seconds without use (made again by a screen read, or met again as a real value in a request), and `clear()` forgets all. Nothing here logs, prints or writes a value."""

    MAX_ENTRIES = 2000
    MAX_VALUE = 200
    TTL_S = 24 * 3600.0
    SKIP_KEYS = frozenset({"model", "role", "type", "tool_call_id", "id", "name", "object", "finish_reason"})

    def __init__(self, ttl_s: float | None = None, max_entries: int | None = None, clock=time.monotonic):
        self.ttl_s = self.TTL_S if ttl_s is None else float(ttl_s)
        self.max_entries = max_entries or self.MAX_ENTRIES
        self._clock = clock
        self._lock = threading.Lock()
        self._to_tag: dict = {}                 # value -> tag
        self._to_value: dict = {}               # tag -> value
        self._used: dict = {}                   # tag -> time of last use
        self._counts: dict = {}                 # kind -> highest number ever given
        self._rx = None                         # compiled `hide` pattern; None = rebuild

    # -- internals (lock held)
    def _expire(self):
        if self.ttl_s <= 0 or not self._used:
            return
        cutoff = self._clock() - self.ttl_s
        for t in [t for t, at in self._used.items() if at < cutoff]:
            self._drop(t)

    def _drop(self, tag: str):
        value = self._to_value.pop(tag, None)
        self._used.pop(tag, None)
        if value is not None:
            self._to_tag.pop(value, None)
        self._rx = None

    # -- the map
    def tag(self, kind: str, value: str):
        """The tag for `value` (made if new), or None when the value may not be stored (empty, too long, not a restorable kind)."""
        if kind not in SCREEN_RESTORABLE or not isinstance(value, str) or not value.strip() or len(value) > self.MAX_VALUE:
            return None
        with self._lock:
            self._expire()
            t = self._to_tag.get(value)
            if t is None:
                n = self._counts.get(kind, 0) + 1
                self._counts[kind] = n
                t = f"[{kind}_{n}]"
                self._to_tag[value], self._to_value[t] = t, value
                self._used[t] = self._clock()
                self._rx = None
                while len(self._to_value) > self.max_entries:
                    self._drop(min((x for x in self._used if x != t), key=self._used.get, default=t))
            if t not in self._to_value:
                return None
            self._used[t] = self._clock()
            return t

    def counters(self) -> dict:
        """Numbers already given, per kind: a request's own tags continue after them (Mapping.base), so a tag never means two things."""
        with self._lock:
            return dict(self._counts)

    def display(self) -> dict:
        """tag -> real value, a copy: what a reply to the owner's face may be restored with."""
        with self._lock:
            self._expire()
            return dict(self._to_value)

    def _touch(self, tags):
        with self._lock:
            now = self._clock()
            for t in tags:
                if t in self._used:
                    self._used[t] = now

    def size(self) -> int:
        with self._lock:
            self._expire()
            return len(self._to_value)

    def clear(self):
        with self._lock:
            self._to_tag.clear(), self._to_value.clear(), self._used.clear()
            self._rx = None

    # -- the way out: real values back to tags, in everything that goes to a provider
    def _pattern(self):
        with self._lock:
            self._expire()
            if not self._to_tag:
                return None
            if self._rx is None:
                vals = sorted(self._to_tag, key=len, reverse=True)
                self._rx = re.compile(r"(?<!\w)(?:" + "|".join(re.escape(v) for v in vals) + r")(?!\w)")
            return self._rx, dict(self._to_tag)

    def hide(self, text: str, used: set | None = None) -> str:
        """`text` with every real value this vault knows replaced by its tag (whole words only). `used`, if given, collects the tags that were put in."""
        if not text or not isinstance(text, str):
            return text
        got = self._pattern()
        if got is None:
            return text
        rx, to_tag = got
        hit = set()

        def sub(m):
            tag = to_tag.get(m.group(0), m.group(0))
            hit.add(tag)
            return tag
        out = rx.sub(sub, text)
        if hit:
            self._touch(hit)                       # an entry stays while a request still carries its value (the face's history); an unused one is forgotten
            if used is not None:
                used.update(hit)
        return out

    def hide_tree(self, obj, _key: str = "", used: set | None = None):
        """`hide` over every string of a JSON value (the request body); keys and the fixed words of the API stay."""
        if isinstance(obj, str):
            return obj if _key in self.SKIP_KEYS else self.hide(obj, used)
        if isinstance(obj, list):
            return [self.hide_tree(x, _key, used) for x in obj]
        if isinstance(obj, dict):
            return {k: self.hide_tree(v, k if isinstance(k, str) else "", used) for k, v in obj.items()}
        return obj


def neutralise_tags(text: str) -> str:
    """A tag-shaped text that is really on the screen ("[PERSON_1]" in a chat window) becomes "(PERSON_1)", so it can never be mistaken for one of ours."""
    return PLACEHOLDER.sub(lambda m: f"({m.group(1)}_{m.group(2)})", text)


def screen_redact(red, vault: ScreenVault, text: str, names=True):
    """(text for the model, {kind: count}, number of secrets hidden). Private values the owner may see again become vault tags; secrets become
    "[REDACTED:kind]" and are forgotten at once. `red` is any detector with the Redactor.redact signature."""
    m = Mapping()
    safe = neutralise_tags(str(text))
    out = red.redact(safe, m, True, names=names) if names else red.redact(safe, m)
    repl, hidden = {}, 0
    for tag, value in m.to_value.items():
        mm = PLACEHOLDER.fullmatch(tag)
        entity = mm.group(1) if mm else ""
        kind = screen_class(entity, value)
        new = vault.tag(kind, value) if kind else None
        if new is None:
            new, hidden = f"[REDACTED:{entity.lower() or 'private'}]", hidden + 1
        repl[tag] = new
    out = PLACEHOLDER.sub(lambda mm: repl.get(mm.group(0), mm.group(0)), out) if repl else out
    return out, dict(m.counts), hidden
