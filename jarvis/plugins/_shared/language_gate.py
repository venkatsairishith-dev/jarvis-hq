"""The language gate (D59, lever c): act on a Telugu / Hindi / Tenglish command only when the reading is CONFIDENT; otherwise ask "Did you mean ...?".

    text ──> English already?  yes ──> pass through unchanged
              │ no
              ├─> tenglish rules (deterministic, BEFORE any model)  confident and complete? ──> act on the rules' English
              │
              └─> otherwise the model's translation, if a translator was lent:
                    its answer and the rules' reading must AGREE on what is being asked (the verbs), or its own confidence must be high;
                    confident ──> act      not confident ──> the ask-back form listing the readings (never a silent guess)

Why a threshold and not "trust the translation": a wrong reading of a harmless command wastes a turn, a wrong reading of "delete" or "close"
loses the owner's work. So destructive readings need a higher bar (`DESTRUCTIVE_THRESHOLD`), and a command whose rules found a verb but not
its object ("delete" with no file) is always a question. The model is OPTIONAL and lent by the caller (`model_translate`); with none, the
gate still works for everything the lexicon covers and asks for the rest.

The gate decides; it does not act, and the kernel's PIN rules still apply to whatever is done next (D27, D53).
Standard library only.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Callable

from . import ask_form, tenglish

ACT_THRESHOLD = 0.85
DESTRUCTIVE_THRESHOLD = 0.95
DESTRUCTIVE = frozenset({"delete"})

PASS, ACT, ASK = "pass_through", "act", "ask"

# What a plain English command asks for, by verb: lets the gate compare a model's translation with the rules' reading.
_ENGLISH_INTENT = (
    ("delete", r"\b(delete|remove|erase|trash|discard|recycle|move\b.*\brecycle bin)\b"),
    ("open", r"\b(open|launch|start|run|go to)\b"),
    ("type", r"\b(type|write|enter|jot|note down|put the text)\b"),
    ("save", r"\bsave\b"),
    ("close", r"\b(close|exit|quit|shut)\b"),
    ("show", r"\b(show|display|bring up)\b"),
    ("play", r"\b(play|put on)\b"),
    ("search", r"\b(search|find|look up|look for)\b"),
    ("tell", r"\b(tell|what(?:'s| is)|how (?:is|are)|weather|read out)\b"),
)


def english_intents(text: str) -> set[str]:
    low = str(text or "").lower()
    return {name for name, pat in _ENGLISH_INTENT if re.search(pat, low)}


@dataclass
class Decision:
    action: str                        # pass_through | act | ask
    english: str                       # the text to carry on with (the original, for pass_through; "" when asking)
    confidence: float
    source: str                        # english | rules | model | rules+model | none
    language: str = "en"
    form: dict | None = None
    notes: list[str] = field(default_factory=list)


Translator = Callable[[str], "str | tuple[str, float | None]"]


def _bar(intents: set[str]) -> float:
    return DESTRUCTIVE_THRESHOLD if intents & DESTRUCTIVE else ACT_THRESHOLD


def _ask(language: str, candidates: list[str], notes: list[str], conf: float) -> Decision:
    if candidates:
        form = ask_form.did_you_mean(candidates, source="language", reason="I am not sure I read that right, and I do not want to guess.")
    else:
        form = ask_form.build_form("I could not understand that. Could you say it another way?", [], source="language")
    return Decision(ASK, "", round(conf, 3), "none", language, form, notes)


def decide(text: str, *, model_translate: Translator | None = None, threshold: float = ACT_THRESHOLD) -> Decision:
    """The gate. `model_translate(text)` returns English, or (English, confidence 0..1 or None). It may raise: that means no translation."""
    info = tenglish.detect(text)
    if info.language == "en":
        return Decision(PASS, str(text or ""), 1.0, "english", "en")
    notes: list[str] = []
    p = tenglish.parse(text)
    rules_intents = set(p.intents)
    complete = bool(p.steps) and not p.unplaced and not p.residue
    if complete and p.confidence >= max(threshold, _bar(rules_intents)):
        return Decision(ACT, p.english, p.confidence, "rules", info.language, None, [f"rules read {', '.join(p.intents)}"])
    if p.steps:
        notes.append(f"rules read {', '.join(p.intents)} at {p.confidence}" + (" (words left over)" if p.unplaced or p.residue else ""))
    candidates = [p.english] if p.steps else []
    if model_translate is None:
        return _ask(info.language, candidates, notes + ["no translator available"], p.confidence)
    try:
        got = model_translate(str(text))
    except Exception as e:  # noqa: BLE001 - no translation is not a crash
        return _ask(info.language, candidates, notes + [f"the translator failed ({type(e).__name__})"], p.confidence)
    eng, mconf = (got if isinstance(got, tuple) else (got, None))
    eng = " ".join(str(eng or "").split())[:400]
    if not eng:
        return _ask(info.language, candidates, notes + ["the translator returned nothing"], p.confidence)
    m_intents = english_intents(eng)
    base = float(mconf) if isinstance(mconf, (int, float)) and 0 <= mconf <= 1 else 0.5      # an unscored translation is not trusted
    if rules_intents and m_intents:
        if rules_intents == m_intents and p.confidence >= 0.6:
            conf, source = max(base, 0.9), "rules+model"                                      # two independent readings say the same
            notes.append("model and rules agree on " + ", ".join(sorted(m_intents)))
        else:
            conf, source = min(base, 0.4), "model"                                            # they disagree about what is being asked
            notes.append(f"model reads {sorted(m_intents)}, rules read {sorted(rules_intents)}")
    else:
        conf, source = base, "model"
    if conf >= max(threshold, _bar(rules_intents | m_intents)):
        return Decision(ACT, eng, round(conf, 3), source, info.language, None, notes)
    cands = [eng] + [c for c in candidates if c.lower().rstrip(".") != eng.lower().rstrip(".")]
    return _ask(info.language, cands, notes + [f"model confidence {round(conf, 2)} is below the bar"], conf)
