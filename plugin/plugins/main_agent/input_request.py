"""A step that needs the owner: one question, typed fields, validated answers (pending-input contract, 2026-10-20).

A plugin entry asks by returning, in its result (or under ``data`` / ``result``):

    {"status": "needs_input", "input_request": {
        "title": "Gemini API key",                      # short heading (optional)
        "prompt": "Paste the Gemini API key to test it.",
        "fields": [{"name": "api_key", "kind": "secret", "label": "API key", "required": true},
                   {"name": "role", "kind": "choice", "label": "Use it as",
                    "options": [{"value": "primary", "label": "Primary"}, {"value": "fallback", "label": "Fallback"}]}],
        "answer_arg": "answers",                         # the entry argument the answers arrive in on the re-run
        "expires_s": 86400}}

Kinds: ``text`` (max_len, optional), ``secret`` (masked; NEVER persisted, logged, spoken or accepted by voice), ``choice``
(one of options), ``multi`` (several of options), ``bool``. At most 6 fields, 12 options each.

The request is untrusted plugin output: every string is cleaned (``_shared.untrusted.clean_untrusted``), unknown keys are
dropped, and a malformed request is refused as a whole (``RequestError``) -- the step then fails instead of waiting on a
question nobody can answer. A request id is made here (the plugin cannot pick it), so an answer can only ever reach the
question main_agent itself recorded.
"""
from __future__ import annotations

import re
import secrets
import time
from typing import Any

from plugin.plugins._shared.untrusted import clean_untrusted

KINDS = ("text", "secret", "choice", "multi", "bool")
MAX_FIELDS, MAX_OPTIONS = 6, 12
DEFAULT_EXPIRES_S, MAX_EXPIRES_S, MIN_EXPIRES_S = 24 * 3600, 7 * 24 * 3600, 60
_NAME = re.compile(r"^[a-z][a-z0-9_]{0,31}$")
_VALUE = re.compile(r"^[A-Za-z0-9_.:-]{1,64}$")
_ARG = re.compile(r"^[a-z][a-z0-9_]{0,31}$")
_YES = re.compile(r"(?i)^\s*(yes|yeah|yep|sure|ok(ay)?|confirm(ed)?|do it|go ahead|haan?|అవును|हाँ|हां)\s*[.!]?\s*$")
_NO = re.compile(r"(?i)^\s*(no|nope|don'?t|do not|cancel|nahi|nahin|వద్దు|లేదు|नहीं)\s*[.!]?\s*$")
_CANCEL = re.compile(r"(?i)^\s*(cancel|never ?mind|forget it|stop asking|drop it)( (the|that) question)?\s*[.!]?\s*$")
_ANSWER_PREFIX = re.compile(r"(?i)^\s*(the answer is|my answer is|answer|reply|answer is)\s*[:,-]?\s+(.+)$")


class RequestError(ValueError):
    """The plugin's request is malformed: the step fails (nobody could answer it)."""


class AnswerError(ValueError):
    """The owner's answer does not fit the question. The message is safe to show (it never repeats a secret)."""


def _s(value: Any, limit: int) -> str:
    return " ".join(clean_untrusted(value, limit * 2).split())[:limit]


def find_request(value: Any) -> dict[str, Any] | None:
    """The raw ``input_request`` in a step result (top level, ``data`` or ``result``), or None."""
    for layer in (value, (value or {}).get("data") if isinstance(value, dict) else None,
                  (value or {}).get("result") if isinstance(value, dict) else None):
        if isinstance(layer, dict) and isinstance(layer.get("input_request"), dict):
            return layer["input_request"]
    return None


def normalize(raw: dict[str, Any], *, step_id: str, ref: str, now: float | None = None) -> dict[str, Any]:
    """The request as main_agent records and shows it. Raises RequestError when it cannot be answered safely."""
    if not isinstance(raw, dict):
        raise RequestError("the input request is not an object")
    fields_in = raw.get("fields")
    if not isinstance(fields_in, list) or not fields_in:
        raise RequestError("the input request has no fields")
    if len(fields_in) > MAX_FIELDS:
        raise RequestError(f"the input request has more than {MAX_FIELDS} fields")
    fields, names = [], set()
    for f in fields_in:
        if not isinstance(f, dict):
            raise RequestError("a field is not an object")
        name, kind = str(f.get("name") or ""), str(f.get("kind") or "")
        if not _NAME.match(name) or name in names:
            raise RequestError("a field name is missing, repeated or not a short lowercase identifier")
        if kind not in KINDS:
            raise RequestError(f"field {name}: kind must be one of {', '.join(KINDS)}")
        names.add(name)
        out = {"name": name, "kind": kind, "label": _s(f.get("label") or name, 80), "required": f.get("required") is not False}
        if f.get("help"):
            out["help"] = _s(f["help"], 200)
        if kind in ("text", "secret"):
            try:
                out["max_len"] = max(1, min(int(f.get("max_len") or (4096 if kind == "secret" else 500)), 8192))
            except (TypeError, ValueError):
                raise RequestError(f"field {name}: max_len must be a number")
        if kind in ("choice", "multi"):
            opts = f.get("options")
            if not isinstance(opts, list) or not opts or len(opts) > MAX_OPTIONS:
                raise RequestError(f"field {name}: 1 to {MAX_OPTIONS} options are needed")
            seen, clean = set(), []
            for o in opts:
                if not isinstance(o, dict) or not _VALUE.match(str(o.get("value") or "")) or str(o["value"]) in seen:
                    raise RequestError(f"field {name}: every option needs a unique short value")
                seen.add(str(o["value"]))
                clean.append({"value": str(o["value"]), "label": _s(o.get("label") or o["value"], 80)})
            out["options"] = clean
        fields.append(out)
    arg = str(raw.get("answer_arg") or "answers")
    if not _ARG.match(arg):
        raise RequestError("answer_arg must be a short lowercase identifier")
    try:
        ttl = int(raw.get("expires_s") or DEFAULT_EXPIRES_S)
    except (TypeError, ValueError):
        raise RequestError("expires_s must be a number")
    ttl = max(MIN_EXPIRES_S, min(ttl, MAX_EXPIRES_S))
    now = time.time() if now is None else now
    prompt = _s(raw.get("prompt") or "", 600)
    if not prompt:
        raise RequestError("the input request has no prompt")
    return {"id": "q-" + secrets.token_hex(6), "step": step_id, "ref": _s(ref, 80), "title": _s(raw.get("title") or "", 80),
            "prompt": prompt, "fields": fields, "answer_arg": arg, "created": round(now, 3), "expires": round(now + ttl, 3),
            "status": "open"}


def public_view(req: dict[str, Any]) -> dict[str, Any]:
    """What the chat shows: the question and the field definitions. A request never holds a value, so nothing to hide."""
    return {k: req.get(k) for k in ("id", "step", "title", "prompt", "fields", "created", "expires", "status")}


def is_open(req: dict[str, Any] | None, now: float | None = None) -> bool:
    now = time.time() if now is None else now
    return isinstance(req, dict) and req.get("status") == "open" and float(req.get("expires") or 0) > now


def validate(req: dict[str, Any], values: Any) -> tuple[dict[str, Any], dict[str, str]]:
    """(public answers, secret answers). Unknown names are refused, not dropped: an answer meant for another question must
    not half-apply. Messages name the field, never a secret's value."""
    if not isinstance(values, dict):
        raise AnswerError("The answer must be a set of named values.")
    known = {f["name"]: f for f in req.get("fields") or []}
    extra = sorted(set(values) - set(known))
    if extra:
        raise AnswerError(f"This question has no field named {', '.join(extra[:3])}.")
    public: dict[str, Any] = {}
    secret: dict[str, str] = {}
    for name, f in known.items():
        v = values.get(name)
        empty = v is None or (isinstance(v, str) and not v.strip()) or (isinstance(v, list) and not v)
        if empty:
            if f["required"]:
                raise AnswerError(f"{f['label']} is required.")
            continue
        kind = f["kind"]
        if kind in ("text", "secret"):
            if not isinstance(v, str):
                raise AnswerError(f"{f['label']} must be text.")
            v = v.strip()
            if len(v) > f["max_len"]:
                raise AnswerError(f"{f['label']} is longer than {f['max_len']} characters.")
            if any(ord(c) < 32 and c not in "\t" for c in v):
                raise AnswerError(f"{f['label']} contains control characters.")
            if kind == "secret":
                secret[name] = v
            else:
                public[name] = clean_untrusted(v, f["max_len"])
        elif kind == "choice":
            allowed = {o["value"] for o in f["options"]}
            if not isinstance(v, str) or v not in allowed:
                raise AnswerError(f"{f['label']}: choose one of the offered options.")
            public[name] = v
        elif kind == "multi":
            allowed = {o["value"] for o in f["options"]}
            if not isinstance(v, list) or not all(isinstance(x, str) and x in allowed for x in v):
                raise AnswerError(f"{f['label']}: choose from the offered options.")
            public[name] = sorted(set(v), key=[o["value"] for o in f["options"]].index)
        elif kind == "bool":
            if not isinstance(v, bool):
                raise AnswerError(f"{f['label']} must be yes or no.")
            public[name] = v
    return public, secret


def summary(req: dict[str, Any], public: dict[str, Any], secret: dict[str, str]) -> dict[str, Any]:
    """What is written down about an answer: public values, and only the NAMES of secret fields (never their values)."""
    return {"values": dict(public), "secret_fields": sorted(secret)}


def from_utterance(req: dict[str, Any], text: str) -> tuple[str, dict[str, Any] | None, str]:
    """Map a spoken (or plain typed) sentence onto the question, only when that is unambiguous.

    Returns (verdict, values, why): verdict is "answer" (values fit the question), "cancel" (the owner dropped the
    question), or "no" (not an answer: start it as a new task or say why). Rules, to never hijack a new command:
      * a question with a secret field is never answered by voice (it would be spoken and transcribed);
      * one choice field: the sentence must name exactly one option (its label or value);
      * several choice fields of the same options (e.g. primary / fallback): each named option is matched in order;
      * one bool field: yes / no words only;
      * one text field: only with an explicit prefix ("the answer is ...", "answer: ...").
    """
    t = " ".join(str(text or "").split())
    if not t:
        return "no", None, "nothing was said"
    if _CANCEL.match(t):
        return "cancel", None, "the owner dropped the question"
    fields = req.get("fields") or []
    if any(f["kind"] == "secret" for f in fields):
        return "no", None, "this question asks for a secret: type it in the chat, it is never taken by voice"
    low = t.lower()

    def mentions(f: dict[str, Any]) -> list[str]:
        hits = []
        for o in f.get("options") or []:
            for word in {o["label"].lower(), o["value"].lower()}:
                m = re.search(r"(?<![\w])" + re.escape(word) + r"(?![\w])", low)
                if m:
                    hits.append((m.start(), o["value"]))
                    break
        return [v for _, v in sorted(hits)]

    if len(fields) == 1:
        f = fields[0]
        if f["kind"] == "choice":
            hits = mentions(f)
            if len(hits) == 1:
                return "answer", {f["name"]: hits[0]}, ""
            return "no", None, ("it names more than one option" if hits else "it names none of the options")
        if f["kind"] == "multi":
            hits = mentions(f)
            return ("answer", {f["name"]: hits}, "") if hits else ("no", None, "it names none of the options")
        if f["kind"] == "bool":
            if _YES.match(t):
                return "answer", {f["name"]: True}, ""
            if _NO.match(t):
                return "answer", {f["name"]: False}, ""
            return "no", None, "say yes or no"
        if f["kind"] == "text":
            m = _ANSWER_PREFIX.match(t)
            if m:
                return "answer", {f["name"]: m.group(2).strip()}, ""
            return "no", None, 'to answer by voice, start with "the answer is"'
    choice_fields = [f for f in fields if f["kind"] == "choice"]
    if choice_fields and len(choice_fields) == len(fields):
        same = all({o["value"] for o in f["options"]} == {o["value"] for o in choice_fields[0]["options"]} for f in choice_fields)
        hits = mentions(choice_fields[0]) if same else []
        if same and len(hits) == len(choice_fields) and len(set(hits)) == len(hits):
            return "answer", {f["name"]: v for f, v in zip(choice_fields, hits)}, ""
    return "no", None, "this question has several fields: answer it in the chat"
