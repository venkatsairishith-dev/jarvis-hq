"""What every Workbench module does with outside text, and where client text may go.

Everything that comes from a file, a transcript, a chat export, a web page or a model is DATA. Three rules, all built on
the existing helpers (nothing new is invented here):

- ``clean(text)``: invisible characters out and instruction-shaped constructs defused (``_shared.untrusted``). Words stay.
- ``addressed_to_ai(text)``: a sentence that speaks to an AI reader ("ignore your instructions", "note to the assistant")
  is flagged with the research plugin's own pattern (``research.verify.INJECTED``). A flagged sentence is kept as evidence
  but never becomes a requirement, a step or part of a prompt's instructions.
- ``scrub(text)``: tokens, keys, passwords and card numbers are replaced (``_shared.screen_text.scrub_text``); text that
  holds a private key is withheld whole. Used on everything written to a log, a prompt or a lesson.

Where client text may be sent (``egress_allowed``): a verified LOCAL model only (``screen_text.local_model_ok``, the check
that guards the owner's memory), unless the project's provider policy names an external provider the owner authorised.
The privacy gateway does not count as local: it forwards to public providers. Fails closed: a check that cannot run says no.
"""
from __future__ import annotations

import re
import unicodedata
from typing import Any

from plugin.plugins._shared import untrusted
from plugin.plugins._shared.screen_text import scrub_text

# Same fallback the research plugin would use if its module could not load: fail toward flagging, never toward trusting.
_FALLBACK_INJECTED = re.compile(
    r"(?i)\b(ignore|disregard|forget|override)\b[^.!?]{0,40}\b(instruction|prompt|rule|guideline|system message)s?\b"
    r"|\b(note|message|instruction)s?\s+(to|for)\s+(ai|llms?|language models?|assistants?|chatbots?|ai assistants?|agents?|jarvis)\b"
    r"|\byou are (now )?(chatgpt|an ai|a language model|an assistant)\b|\bas an ai (assistant|model)\b|\bsystem prompt\b")
_EXTRA_INJECTED = re.compile(
    r"(?i)\b(jarvis|assistant|agent|ai)\b[^.!?]{0,30}\b(must|should|shall|will)\b[^.!?]{0,40}\b(send|transfer|pay|delete|wipe|"
    r"upload|email|forward|approve|disable|share)\b"
    r"|\b(send|email|forward|upload)\b[^.!?]{0,40}\b(password|credentials?|api key|token|secret|private key)s?\b"
    r"|\b(without|skip|bypass)\b[^.!?]{0,20}\b(approval|confirmation|permission|asking)\b")
_ZW = re.compile("[​-‏⁠-⁤­﻿]")


def _injected_pattern() -> re.Pattern:
    try:
        from plugin.plugins.research.verify import INJECTED
        return INJECTED
    except Exception:  # noqa: BLE001 - numpy missing, a broken install: the local copy still flags
        return _FALLBACK_INJECTED


def _plain(text: str) -> str:
    return _ZW.sub("", unicodedata.normalize("NFKC", str(text or "")))


def addressed_to_ai(text: str) -> bool:
    """Whether the text reads as an instruction aimed at JARVIS or an AI reader rather than a client's request."""
    t = _plain(text)
    return bool(_injected_pattern().search(t) or _FALLBACK_INJECTED.search(t) or _EXTRA_INJECTED.search(t))


def clean(text: Any, limit: int | None = None) -> str:
    return untrusted.clean_untrusted(text, limit)


def scrub(text: Any) -> tuple[str, list[str]]:
    """(text without secrets, names of the rules that fired). Rule names only, never the values."""
    return scrub_text(untrusted.as_text(text))


def safe_for_output(text: Any, limit: int | None = None) -> str:
    """Cleaned and scrubbed: for prompts, logs, lessons and deliverables."""
    out, _ = scrub(clean(text))
    return out[:limit] if limit else out


def fence(label: str, text: Any, limit: int = 4000) -> str:
    out, _ = scrub(text)
    return untrusted.fence(label, out, limit)


# ---------------------------------------------------------------- where client text may be sent
LOCAL_ONLY = "local_only"
EXTERNAL_ALLOWED = "external_allowed"
POLICIES = (LOCAL_ONLY, EXTERNAL_ALLOWED)


def model_is_local(base_url: str) -> bool:
    try:
        from plugin.plugins._shared.screen_text import local_model_ok
        return bool(local_model_ok(base_url))
    except Exception:  # noqa: BLE001
        return False


def egress_allowed(policy: dict[str, Any] | None, base_url: str = "", provider: str = "") -> tuple[bool, str]:
    """May this project's text go to the model at ``base_url`` (or the named ``provider``)? (allowed, why)."""
    policy = policy if isinstance(policy, dict) else {}
    if base_url and model_is_local(base_url):
        return True, "a verified local model"
    mode = policy.get("ai_providers", LOCAL_ONLY)
    allowed = {str(p).lower() for p in (policy.get("authorised_providers") or []) if str(p).strip()}
    if mode == EXTERNAL_ALLOWED and provider and provider.lower() in allowed and policy.get("authorised_by_owner") is True:
        return True, f"the owner authorised {provider} for this project"
    if mode != EXTERNAL_ALLOWED:
        return False, "this project's materials stay on local models (provider policy: local only)"
    return False, "that provider is not one the owner authorised for this project"
