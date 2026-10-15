"""Find private values in text, swap them for placeholders such as [PASSWORD_1], and put them back later.

Built on Microsoft Presidio (recognisers with validation: e-mail, phone, card numbers with Luhn, IBAN, IP, crypto) plus
recognisers added here for what Presidio does not cover: passwords/PINs/OTPs written after a cue word, API keys and tokens,
private keys, JWTs, Indian Aadhaar and PAN numbers. Names and places are NOT redacted by default (it would wreck ordinary
conversation); set JARVIS_PRIVACY_NAMES=1 to include PERSON and LOCATION.

The mapping lives only in memory for one request. Nothing here logs a value.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field

from presidio_analyzer import AnalyzerEngine, Pattern, PatternRecognizer, RecognizerResult
from presidio_analyzer.nlp_engine import NlpEngineProvider

BASE_ENTITIES = ["EMAIL_ADDRESS", "PHONE_NUMBER", "CREDIT_CARD", "IBAN_CODE", "IP_ADDRESS", "CRYPTO"]
CUSTOM_ENTITIES = ["PASSWORD", "API_KEY", "PRIVATE_KEY", "JWT", "AADHAAR", "PAN"]
NAME_ENTITIES = ["PERSON", "LOCATION"]
PLACEHOLDER = re.compile(r"\[([A-Z_]+)_(\d+)\]")

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
            Pattern("bearer", r"(?i)\bbearer\s+[A-Za-z0-9._\-]{20,}\b", 0.8),
        ]),
        PatternRecognizer(supported_entity="PRIVATE_KEY", name="PrivateKey", patterns=[
            Pattern("pem", r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]+?-----END [A-Z ]*PRIVATE KEY-----", 0.99)]),
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


@dataclass
class Mapping:
    """One request's placeholders: value -> placeholder and back. Same value, same placeholder."""
    to_tag: dict = field(default_factory=dict)
    to_value: dict = field(default_factory=dict)
    counts: dict = field(default_factory=dict)

    def tag(self, entity: str, value: str) -> str:
        if value in self.to_tag:
            return self.to_tag[value]
        n = self.counts.get(entity, 0) + 1
        self.counts[entity] = n
        t = f"[{entity}_{n}]"
        self.to_tag[value], self.to_value[t] = t, value
        return t


class Redactor:
    def __init__(self, names: bool | None = None, threshold: float = 0.5):
        if names is None:
            names = os.environ.get("JARVIS_PRIVACY_NAMES", "0") == "1"
        provider = NlpEngineProvider(nlp_configuration={"nlp_engine_name": "spacy", "models": [{"lang_code": "en", "model_name": "en_core_web_sm"}]})
        self.engine = AnalyzerEngine(nlp_engine=provider.create_engine(), supported_languages=["en"])
        for r in _custom_recognizers():
            self.engine.registry.add_recognizer(r)
        self.entities = BASE_ENTITIES + CUSTOM_ENTITIES + (NAME_ENTITIES if names else [])
        self.threshold = threshold

    def find(self, text: str):
        if not text or not text.strip():
            return []
        results = self.engine.analyze(text=text, language="en", entities=self.entities, score_threshold=self.threshold)
        # keep the strongest, longest non-overlapping spans
        results.sort(key=lambda r: (-(r.end - r.start), -r.score))
        chosen = []
        for r in results:
            if all(r.end <= c.start or r.start >= c.end for c in chosen):
                chosen.append(r)
        return sorted(chosen, key=lambda r: r.start)

    def redact(self, text: str, mapping: Mapping) -> str:
        spans = self.find(text)
        out, pos = [], 0
        for r in spans:
            value = text[r.start:r.end]
            if PLACEHOLDER.fullmatch(value):
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


class StreamRestorer:
    """Restores placeholders in a streamed reply, where a tag like [PASSWORD_1] can arrive split across chunks."""

    MAX_TAG = 40

    def __init__(self, mapping: Mapping):
        self.mapping, self.buf = mapping, ""

    def feed(self, chunk: str) -> str:
        if not self.mapping.to_value:
            return chunk
        self.buf += chunk or ""
        cut = self.buf.rfind("[")
        if cut != -1 and "]" not in self.buf[cut:] and len(self.buf) - cut <= self.MAX_TAG:
            ready, self.buf = self.buf[:cut], self.buf[cut:]          # hold a possibly unfinished tag
        else:
            ready, self.buf = self.buf, ""
        return restore(ready, self.mapping)

    def flush(self) -> str:
        out, self.buf = restore(self.buf, self.mapping), ""
        return out
