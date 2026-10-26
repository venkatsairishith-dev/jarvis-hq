"""D63: what the backend does for a form that is used by voice. The face understands the owner's words with a deterministic grammar FIRST
(composables/jarvis-ask-voice-logic.ts); only when that is unsure does it call `jarvis_ask_voice` (a panel tool, not listed to the chat model) for
one of three read-only helps:

  translate   the English of a Telugu / Hindi / Tenglish phrase, on this laptop only (plugins/_shared/translate.py, D58)
  decide      which of a few candidate controls the phrase names (plugins/_shared/decider.py, kind `form_control`): candidates only, with a
              confidence; below the bar `ask` is true and the face asks "Did you mean A or B?" (it never guesses)
  speech      whether a Telugu / Hindi text may be SPOKEN (language.tts_verified, D58); otherwise the face shows it and speaks English or nothing

What this module can NOT do, on purpose: it returns a candidate key or a sentence and nothing else. It does not press a button, tick a box, answer a
form, approve anything or speak. It never imports the console client or the kernel. A phrase that carries a PIN, a password or an approval is refused
before any model sees it. The one-time token of the open form is required for every call, so an old or foreign call gets nothing. The candidate keys
are checked against the open form's own spec (the labels the model sees come from the spec, not from the caller), and labels are only names.
"""
from __future__ import annotations

import re
from typing import Any, Callable

from jarvis.bridge.ask_spec import INPUTS, iter_nodes, plain

MAX_CANDIDATES = 12
OPS = ("translate", "decide", "speech")
CONSOLE_ONLY = "PINs and approvals are only given in the JARVIS console. Type it there; never say it aloud."

# Speech that carries a secret or tries to approve: refused before translation or any model (English, Telugu and Hindi spellings).
SECRET_WORDS = r"(?:pin|passcode|password|passwd|otp|one[- ]?time (?:code|password)|పిన్|పాస్‌?వర్డ్|పాస్వర్డ్|ఓటీపీ|पिन|पासवर्ड|पासकोड|ओटीपी)"
NUMBER_WORD = r"(?:zero|oh|one|two|three|four|five|six|seven|eight|nine|ten)"
# a PIN or password SAID: the word followed (within a few words) by digits, spoken digits or, for a password, "is <something>". "pin code 500081" (a postal code) and "what is a pin" are not.
SECRET_SPEECH = re.compile(rf"(?i)\b{SECRET_WORDS}(?!\w)(?!\s*code\b)[^.!?]{{0,24}}?(?:\d[\d\s-]{{2,}}|\bis\s+\S*\d|\bis\s+{NUMBER_WORD}\b|\bequals\b|=)"
                           rf"|\b(?:password|passwd|passcode|\u092a\u093e\u0938\u0935\u0930\u094d\u0921|\u0c2a\u0c3e\u0c38\u0c4d\u0c35\u0c30\u0c4d\u0c21\u0c4d)(?!\w)\s+is\s+\S+"
                           rf"|{SECRET_WORDS}\s*(?:number|\u0c28\u0c02\u0c2c\u0c30\u0c4d|\u0928\u0902\u092c\u0930)\s*\d")
APPROVAL_SPEECH = re.compile(r"(?i)\b(?:approve[ds]?|approval|authori[sz]e[ds]?|అప్రూవ్|अप्रूव|मंज़ूर|मंजूर|అనుమతించు)\b")

Post = Callable[[dict], str]


def says_secret(text: Any) -> bool:
    return bool(SECRET_SPEECH.search(str(text or "")))


def says_approval(text: Any) -> bool:
    return bool(APPROVAL_SPEECH.search(str(text or "")))


def key_field(fid: str) -> str:
    return f"f:{fid}"


def key_option(fid: str, oid: str) -> str:
    return f"o:{fid}/{oid}"


def key_button(bid: str) -> str:
    return f"b:{bid}"


def targets(spec: dict) -> dict[str, dict]:
    """{key: {label, field_label, kind}} for every control of the form: the inputs, the options of the choices and the rows of a table, the buttons.
    The face builds the same keys (jarvis-ask-voice-logic.ts voiceTargets); a key the caller sends is only valid if it is here."""
    out: dict[str, dict] = {}
    for n in iter_nodes(spec.get("fields") or []):
        if n.get("type") not in INPUTS or not n.get("id"):
            continue
        fid, flabel = n["id"], plain(n.get("label") or n["id"], 120)
        out[key_field(fid)] = {"label": flabel, "field_label": flabel, "kind": n["type"]}
        if n["type"] == "table":
            cols = [c["id"] for c in n.get("columns") or []][:2]
            for r in n.get("rows") or []:
                label = plain(" ".join(str(r.get("cells", {}).get(c, "")) for c in cols), 100) or r["id"]
                out[key_option(fid, r["id"])] = {"label": label, "field_label": flabel, "kind": "row"}
        for o in n.get("options") or []:
            out[key_option(fid, o["id"])] = {"label": plain(o.get("label") or o["id"], 100), "field_label": flabel, "kind": "option"}
    for b in spec.get("buttons") or []:
        out[key_button(b["id"])] = {"label": plain(b.get("label") or b["id"], 40), "field_label": "", "kind": "button"}
    return out


def _unique_labels(keys: list[str], known: dict[str, dict]) -> tuple[list[str], dict[str, str]]:
    labels: list[str] = []
    back: dict[str, str] = {}
    for k in keys:
        base = known[k]["label"] or k
        if known[k]["kind"] in ("option", "row") and known[k]["field_label"]:
            base = f"{base} ({known[k]['field_label']})"
        label, n = base, 2
        while label in back:
            label, n = f"{base} #{n}", n + 1
        back[label] = k
        labels.append(label)
    return labels, back


def understand(broker: Any, form_id: str, token: str, op: str, text: str = "", phrase: str = "", candidates: Any = None, *,
               post: Post | None = None, translator: Any = None) -> dict:
    """The body of jarvis_ask_voice. Never raises."""
    seen = broker.peek(form_id, token)
    if seen is None:
        return {"ok": False, "message": "That form is no longer open."}
    if op not in OPS:
        return {"ok": False, "message": f"op is one of {', '.join(OPS)}"}
    text = str(text or "")[:500]
    try:
        if op == "speech":
            return {"ok": True, "plans": _speech_plans()}
        if says_secret(text) or says_approval(text) or says_secret(phrase) or says_approval(phrase):
            return {"ok": False, "refused": True, "message": CONSOLE_ONLY}
        if op == "translate":
            return _translate(text, translator)
        return _decide(seen["spec"], text, str(phrase or "")[:200], candidates, post)
    except Exception as exc:  # noqa: BLE001 - the panel gets a sentence, the grammar already did what it could
        return {"ok": False, "message": f"voice help is not available: {type(exc).__name__}"}


def _translate(text: str, translator: Any) -> dict:
    from jarvis.plugins._shared import translate
    info = translate.translate_to_english(text, translator=translator, source="voice") if translator is not None else translate.english_info(text, source="voice")
    return {"ok": True, "english": plain(info["english"], 500), "source_lang": info["source_lang"], "translated": bool(info["translated"]),
            "confidence": round(float(info.get("confidence") or 0.0), 3)}


def _decide(spec: dict, text: str, phrase: str, candidates: Any, post: Post | None) -> dict:
    from jarvis.plugins._shared import decider
    known = targets(spec)
    keys: list[str] = []
    for c in candidates if isinstance(candidates, list) else []:
        k = str(c.get("key") if isinstance(c, dict) else c)
        if k in known and k not in keys:
            keys.append(k)
    keys = keys[:MAX_CANDIDATES]
    if len(keys) < 2:
        return {"ok": True, "pick": keys[0] if keys else None, "confidence": 0.0, "ask": True, "options": keys, "source": "none"}
    labels, back = _unique_labels(keys, known)
    d = decider.decide("form_control", phrase or text, labels, context="a form titled: " + plain(spec.get("title"), 60), post=post)
    pick = back.get(d.choice or "")
    order = ([pick] if pick else []) + [k for k in keys if k != pick]
    return {"ok": True, "pick": pick, "confidence": round(float(d.confidence), 3), "ask": bool(d.ask or pick is None), "options": order[:3], "source": d.source}


def _speech_plans() -> dict:
    from jarvis.plugins._shared import translate
    cfg = translate.load_config()
    # The voice is assumed installed here; the speech server checks again and answers "not spoken" if it is not (D58).
    return {lang: translate.voice_plan(lang, {lang: True}, cfg) for lang in ("en", "te", "hi")}
