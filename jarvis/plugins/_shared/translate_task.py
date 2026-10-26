"""Translation as a TASK (D58): "translate this to Telugu", "what does this say", "translate this file" -- and the quality gate.

Everything here uses the local translator of translate.py only (the owner: a model on THIS laptop, never an online service).

  translate_text(text, target)   any pair among en, te, hi (te <-> hi goes through English). For a translation INTO Telugu or Hindi a BACK-TRANSLATION CHECK
                                 runs (English -> language -> English, chrF++ similarity of the two English texts); below the configured minimum the result is
                                 flagged to the owner ("low_confidence", with the reason), never used silently.
  translate_file(path, target)   reads with the reader it is given (doc_tools), translates paragraph by paragraph, writes a NEW file (never overwrites).
  chrf(hyp, ref)                 chrF++ (character n-grams 1..6 + word n-grams 1..2, beta 2), 0..100, no dependencies. Used by the eval runner too.
  add_glossary_term(term)        the owner's "never translate X": appended to config/glossary.json.

The text of a file, a window or the clipboard is DATA: it is translated, never obeyed (the LLM backend fences it between random markers and is told so).
"""
from __future__ import annotations

import json
import math
import os
import re
from collections import Counter
from pathlib import Path
from typing import Any, Callable

try:
    from jarvis.plugins._shared import translate as T
except ImportError:                                        # loaded by path
    import importlib.util
    import sys
    _spec = importlib.util.spec_from_file_location("jarvis_translate", Path(__file__).with_name("translate.py"))
    T = sys.modules.get("jarvis_translate") or importlib.util.module_from_spec(_spec)
    sys.modules["jarvis_translate"] = T
    if not hasattr(T, "Translator"):
        _spec.loader.exec_module(T)


# ---------------------------------------------------------------------------------------------------------------- chrF++

def _ngrams(items: list[str], n: int) -> Counter:
    return Counter(tuple(items[i:i + n]) for i in range(len(items) - n + 1))


def chrf(hyp: str, ref: str, char_order: int = 6, word_order: int = 2, beta: float = 2.0) -> float:
    """chrF++ (Popovic 2017): average F-beta over character n-grams (spaces removed) of order 1..char_order and word n-grams of order 1..word_order.
    Returns 0..100. Empty hypothesis or reference gives 0 (both empty: 100)."""
    hyp, ref = str(hyp or "").strip(), str(ref or "").strip()
    if not hyp and not ref:
        return 100.0
    if not hyp or not ref:
        return 0.0
    hc, rc = list(hyp.replace(" ", "")), list(ref.replace(" ", ""))
    strip = lambda w: w.strip(".,;:!?\"'()[]{}")                                                  # noqa: E731
    hw, rw = [strip(w) for w in hyp.split() if strip(w)], [strip(w) for w in ref.split() if strip(w)]
    scores = []
    for items_h, items_r, order in ((hc, rc, char_order), (hw, rw, word_order)):
        for n in range(1, order + 1):
            h, r = _ngrams(items_h, n), _ngrams(items_r, n)
            th, tr = sum(h.values()), sum(r.values())
            if not th or not tr:
                scores.append((0.0, 0.0))
                continue
            match = sum((h & r).values())
            scores.append((match / th, match / tr))
    f = []
    for p, r in scores:
        f.append(0.0 if p + r == 0 else (1 + beta ** 2) * p * r / (beta ** 2 * p + r))
    return round(100.0 * sum(f) / len(f), 2) if f else 0.0


def similarity(a: str, b: str) -> float:
    """0..1, symmetric enough for a round-trip check: the mean of chrF++ in both directions, case-folded."""
    a, b = str(a or "").casefold(), str(b or "").casefold()
    return round((chrf(a, b) + chrf(b, a)) / 200.0, 3)


# ---------------------------------------------------------------------------------------------------------------- the quality gate

def back_translation(english: str, target: str, *, translator: "T.Translator | None" = None, forward: "T.Result | None" = None) -> dict:
    """English -> target -> English. {"forward": Result-dict, "back": Result-dict, "similarity": 0..1, "ran": bool}. Not run when either step could not translate."""
    t = translator or T.get_translator()
    fwd = forward if forward is not None else t.from_english(english, target, use_cache=False)
    if not fwd.translated:
        return {"ran": False, "similarity": None, "forward": fwd.as_dict(), "back": None, "reason": fwd.skipped_reason}
    back = t.to_english(fwd.english, src_hint=target, use_cache=False)
    if not back.translated:
        return {"ran": False, "similarity": None, "forward": fwd.as_dict(), "back": back.as_dict(), "reason": back.skipped_reason}
    return {"ran": True, "similarity": similarity(english, back.english), "forward": fwd.as_dict(), "back": back.as_dict(), "reason": ""}


def translate_text(text: str, target: str, source: str = "auto", *, translator: "T.Translator | None" = None, check: bool = True) -> dict:
    """Translate `text` into `target` ('en', 'te' or 'hi'). Returns
    {"text", "target", "source_lang", "backend", "confidence", "low_confidence", "flag", "back_translation", "translated", "reason", "preserved"}.
    `flag` is a sentence for the owner when the result is doubtful, else ""; `text` is then still the best attempt, never silently trusted."""
    t = translator or T.get_translator()
    text = str(text or "")
    out: dict[str, Any] = {"text": text, "target": target, "source_lang": "", "backend": "", "confidence": 0.0, "low_confidence": False, "flag": "",
                           "back_translation": None, "translated": False, "reason": "", "preserved": []}
    if target not in ("en", "te", "hi"):
        out.update(reason="unsupported target language", low_confidence=True, flag=f"I can translate into English, Telugu or Hindi, not {str(target)[:20]}.")
        return out
    if not text.strip():
        out.update(reason="nothing to translate", source_lang="en")
        return out
    src = T.detect_language(text) if source in ("auto", "", None) else source
    out["source_lang"] = src
    base = src.split("-")[0]
    if base == target:
        out.update(text=text, translated=False, confidence=1.0, reason="already in that language")
        return out
    if target == "en":
        r = t.to_english(text, src_hint=src if src != "en" else None)
        out.update(text=r.english, backend=r.backend, confidence=r.confidence, translated=r.translated, reason=r.skipped_reason, preserved=r.preserved,
                   low_confidence=r.low_confidence or not r.translated)
        if not r.translated:
            out["flag"] = f"I could not translate that on this PC ({r.skipped_reason or 'no translator'}), so the text is unchanged."
        elif r.low_confidence:
            out["flag"] = f"I am not sure about this translation (confidence {r.confidence:.2f}); please check it."
        return out
    # into Telugu / Hindi: from English, or through English (te <-> hi)
    english = text
    if base != "en":
        pivot = t.to_english(text, src_hint=src)
        if not pivot.translated:
            out.update(reason=pivot.skipped_reason, low_confidence=True,
                       flag=f"I could not read that on this PC ({pivot.skipped_reason or 'no translator'}), so the text is unchanged.")
            return out
        english = pivot.english
    r = t.from_english(english, target)
    out.update(text=r.english if r.translated else text, backend=r.backend, confidence=r.confidence, translated=r.translated, reason=r.skipped_reason, preserved=r.preserved,
               low_confidence=r.low_confidence or not r.translated)
    if not r.translated:
        out["flag"] = f"I could not translate that on this PC ({r.skipped_reason or 'no translator'}), so the text is unchanged."
        return out
    if check:
        bt = back_translation(english, target, translator=t, forward=r)
        out["back_translation"] = {"ran": bt["ran"], "similarity": bt["similarity"], "back": (bt.get("back") or {}).get("english"), "reason": bt.get("reason", "")}
        floor = float(t.cfg.get("back_translation_min", 0.5))
        if bt["ran"] and bt["similarity"] < floor:
            out["low_confidence"] = True
            out["flag"] = (f"Check this translation: translating it back to English gave something quite different (similarity {bt['similarity']:.2f}, "
                           f"below {floor:.2f}). Back: \"{(bt.get('back') or {}).get('english', '')[:200]}\"")
        elif not bt["ran"]:
            out["flag"] = "The back-translation check could not run, so this translation is unchecked."
    if r.low_confidence and not out["flag"]:
        out["flag"] = f"I am not sure about this translation (confidence {r.confidence:.2f}); please check it."
    return out


# ---------------------------------------------------------------------------------------------------------------- files

def new_path(out_dir: Path | str, stem: str, suffix: str) -> Path:
    """A path in out_dir that does not exist yet (name, name_2, name_3 ...). Never returns an existing file."""
    d = Path(out_dir)
    d.mkdir(parents=True, exist_ok=True)
    stem = re.sub(r"[^\w.\- ]", "_", stem)[:80] or "translation"
    p = d / f"{stem}{suffix}"
    n = 2
    while p.exists():
        p = d / f"{stem}_{n}{suffix}"
        n += 1
    return p


def translate_file(path: str, target: str, *, reader: Callable[[str, int], dict], out_dir: Path | str, translator: "T.Translator | None" = None,
                   max_chars: int = 40000) -> dict:
    """Read `path` with `reader(path, max_chars) -> {"text", "name", ...}` (doc_tools.core.read_document), translate paragraph by paragraph, and write a
    NEW file `<name>.<target>.txt` in `out_dir`. The original is never touched. Returns {"path", "chars", "paragraphs", "flags", "low_confidence", "source_lang"}."""
    doc = reader(path, max_chars)
    text = str(doc.get("text") or "")
    if not text.strip():
        raise T.TranslateError("the file has no text to translate")
    name = Path(str(doc.get("name") or path)).stem
    paras = [p for p in re.split(r"\n\s*\n", text) if p.strip()]
    out, flags, low, src = [], [], False, ""
    for i, p in enumerate(paras):
        r = translate_text(p, target, translator=translator, check=(i == 0))      # the round-trip check on the first paragraph only: a sample, not a doubling of the cost
        src = src or r["source_lang"]
        if not r["translated"] and r["reason"] != "already in that language":
            raise T.TranslateError(f"paragraph {i + 1} could not be translated ({r['reason'] or 'no translator'}); no file was written")
        out.append(r["text"])
        if r["flag"]:
            flags.append(f"paragraph {i + 1}: {r['flag']}")
        low = low or r["low_confidence"]
    dest = new_path(out_dir, f"{name}.{target}", ".txt")
    dest.write_text("\n\n".join(out) + "\n", encoding="utf-8")
    return {"path": str(dest), "chars": sum(len(x) for x in out), "paragraphs": len(paras), "flags": flags[:10], "low_confidence": low, "source_lang": src,
            "truncated": bool(doc.get("truncated"))}


# ---------------------------------------------------------------------------------------------------------------- glossary

def add_glossary_term(term: str, kind: str = "terms", path: Path | str | None = None) -> bool:
    """Add a name or term that must never be translated to config/glossary.json (kind: terms | owner_names | family_names | apps | places). True if added."""
    term = " ".join(str(term or "").split())
    if not 2 <= len(term) <= 60 or kind not in ("terms", "owner_names", "family_names", "apps", "places"):
        raise T.TranslateError("a glossary term is 2 to 60 characters")
    p = Path(path) if path else Path(os.environ.get("JARVIS_GLOSSARY") or T.GLOSSARY_FILE)
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {}
    if not isinstance(data, dict):
        data = {}
    lst = data.setdefault(kind, [])
    if not isinstance(lst, list):
        raise T.TranslateError("the glossary file is not in the expected shape")
    if any(str(x).casefold() == term.casefold() for x in lst):
        return False
    lst.append(term)
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return True


_NEVER = re.compile(r"(?i)\b(?:never|don'?t|do not)\s+translate\s+(?P<q>[\"“'‘`]?)(?P<term>[^\"”'’`.\n]{2,40}?)(?P=q)\s*(?:[.!]|$)")


def glossary_intent(text: str) -> str | None:
    """'never translate Satish' -> 'Satish'; None when the text is not that request."""
    m = _NEVER.search(str(text or "")[:200])
    return m.group("term").strip() if m else None


_TRANSLATE_TO = re.compile(r"(?i)\btranslate\b.{0,60}?\b(?:to|into|in)\s+(english|telugu|hindi)\b")
_WHAT_SAYS = re.compile(r"(?i)\bwhat does (?:this|that|it|the|this \w+|that \w+|the \w+) say\b|\bwhat'?s written (?:here|there|on)\b|\bread (?:this|that) (?:to me )?in english\b")


def translate_request(text: str) -> str | None:
    """The target ('en', 'te', 'hi') when the text asks for a translation task ("translate this to Telugu", "what does this say"), else None."""
    t = str(text or "")[:300]
    m = _TRANSLATE_TO.search(t)
    if m:
        return {"english": "en", "telugu": "te", "hindi": "hi"}[m.group(1).lower()]
    return "en" if _WHAT_SAYS.search(t) else None
