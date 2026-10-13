"""How well was a spoken phrase transcribed? Word and character error rates on normalized text.

Normalization is deliberately gentle: Unicode NFC, case folding, punctuation and zero-width joiners removed, whitespace
collapsed. It does not "fix" spelling, so a wrong Devanagari or Telugu spelling still counts as an error.
"""
import re
import unicodedata

_ZW = dict.fromkeys(map(ord, "​‌‍⁠﻿"), None)     # zero-width space/non-joiner/joiner, word joiner, BOM


def normalize(text: str) -> str:
    """NFC, case folded, punctuation and symbols replaced by spaces. Letters, combining vowel signs (matras) and digits are
    kept: a wrong vowel sign in Hindi or Telugu is a real error and must stay visible."""
    t = unicodedata.normalize("NFC", str(text or "")).translate(_ZW).casefold()
    t = "".join(" " if unicodedata.category(ch)[0] in "PSZC" else ch for ch in t)
    return re.sub(r"\s+", " ", t).strip()


def _edits(a: list, b: list) -> int:
    prev = list(range(len(b) + 1))
    for i, x in enumerate(a, 1):
        cur = [i]
        for j, y in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (x != y)))
        prev = cur
    return prev[-1]


def wer(expected: str, heard: str):
    e, h = normalize(expected).split(), normalize(heard).split()
    if not e:
        return None if not h else 1.0
    return round(_edits(e, h) / len(e), 3)


def cer(expected: str, heard: str):
    e, h = list(normalize(expected).replace(" ", "")), list(normalize(heard).replace(" ", ""))
    if not e:
        return None if not h else 1.0
    return round(_edits(e, h) / len(e), 3)


def score(expected: str, heard: str) -> dict:
    return {"wer": wer(expected, heard), "cer": cer(expected, heard), "exact": normalize(expected) == normalize(heard),
            "expected_norm": normalize(expected), "heard_norm": normalize(heard)}


def summarize(rows: list) -> dict:
    """Per language: phrases, mean WER/CER, exact matches, median transcription latency."""
    out = {}
    for lang in sorted({r["lang"] for r in rows}):
        rs = [r for r in rows if r["lang"] == lang and r.get("wer") is not None]
        if not rs:
            continue
        ms = sorted(r["ms"] for r in rs if r.get("ms") is not None)
        out[lang] = {"phrases": len(rs), "mean_wer": round(sum(r["wer"] for r in rs) / len(rs), 3),
                     "mean_cer": round(sum(r["cer"] for r in rs) / len(rs), 3), "exact": sum(1 for r in rs if r.get("exact")),
                     "median_ms": ms[len(ms) // 2] if ms else None, "no_speech": sum(1 for r in rows if r["lang"] == lang and not r.get("speech", True))}
    return out
