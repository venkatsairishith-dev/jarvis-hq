"""One shape for "stop and ask the owner" (D59): a small form with a question and a few choices.

Several places need to stop and ask instead of guessing: the language gate ("Did you mean: ..."), the closed-loop step executor (a
step could not be confirmed), and the ambiguity rules. They all build the SAME dict here, so the face renders one kind of card and the
reliability gate (jarvis/evals/reliability) recognises one kind of answer. `kind: "jarvis_ask"` is the name the face's ask tool will
use; if that tool lands with different field names, change them HERE and in `is_form`, nowhere else.

Standard library only. The functions never raise on odd input: they clean it.
"""
from __future__ import annotations

import re
from typing import Any, Iterable

KIND = "jarvis_ask"
VERSION = 1
MAX_OPTIONS = 6
MIN_OPTIONS = 2
MAX_LABEL = 80
MAX_QUESTION = 200

_WS = re.compile(r"\s+")


def _clean(text: Any, limit: int) -> str:
    return _WS.sub(" ", str(text or "")).strip()[:limit]


def build_form(question: str, options: Iterable[Any], *, reason: str = "", allow_other: bool = True, source: str = "",
               default: str | None = None) -> dict:
    """A form: `question`, 2 to 6 distinct `options` (strings, or dicts with `label` and optional `value`), a free-text 'Something else'
    when `allow_other`. Options beyond the sixth are dropped; duplicates (ignoring case) are dropped; fewer than two usable options
    still returns a form with the free-text answer only (never an empty choice list the face cannot show)."""
    seen: set[str] = set()
    out: list[dict] = []
    for raw in options or []:
        label = _clean(raw.get("label") if isinstance(raw, dict) else raw, MAX_LABEL)
        value = _clean(raw.get("value", label) if isinstance(raw, dict) else raw, 300) or label
        if not label or label.lower() in seen:
            continue
        seen.add(label.lower())
        out.append({"id": f"o{len(out) + 1}", "label": label, "value": value})
        if len(out) >= MAX_OPTIONS:
            break
    form = {"kind": KIND, "version": VERSION, "question": _clean(question, MAX_QUESTION) or "Could you say a bit more?",
            "options": out, "allow_other": bool(allow_other or len(out) < MIN_OPTIONS)}
    if reason:
        form["reason"] = _clean(reason, 160)
    if source:
        form["source"] = _clean(source, 40)
    if default and any(o["id"] == default for o in out):
        form["default"] = default
    return form


def is_form(obj: Any, min_options: int = 0) -> bool:
    """True for a dict that is an ask form: the right kind, a question, and at least `min_options` choices."""
    if not isinstance(obj, dict) or obj.get("kind") != KIND:
        return False
    if not str(obj.get("question") or "").strip():
        return False
    opts = obj.get("options")
    return isinstance(opts, list) and len(opts) >= min_options


def find_form(value: Any, depth: int = 0) -> dict | None:
    """The first ask form inside a tool result or message (a dict, a list, or a JSON string holding either). Used by the eval to see
    whether a run ended in a question to the owner."""
    if depth > 6:
        return None
    if isinstance(value, str):
        s = value.strip()
        if s[:1] in "{[" and KIND in s:
            import json
            try:
                return find_form(json.loads(s), depth + 1)
            except ValueError:
                return None
        return None
    if isinstance(value, dict):
        if is_form(value):
            return value
        for v in value.values():
            hit = find_form(v, depth + 1)
            if hit:
                return hit
    elif isinstance(value, (list, tuple)):
        for v in value:
            hit = find_form(v, depth + 1)
            if hit:
                return hit
    return None


def did_you_mean(candidates: Iterable[str], *, source: str = "language", reason: str = "") -> dict:
    """The language gate's form: 'Did you mean: ...' with the candidate readings of what the owner said."""
    cands = [c for c in (_clean(x, MAX_LABEL) for x in candidates) if c]
    q = "Did you mean: " + (cands[0] if cands else "something else") + "?" if len(cands) <= 1 else "Which did you mean?"
    return build_form(q, cands, reason=reason or "I was not sure I understood.", source=source)
