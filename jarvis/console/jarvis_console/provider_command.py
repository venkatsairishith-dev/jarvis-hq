"""Words to a provider-switch request (plan item 19): "switch to groq", "use local for the private lane", "back to automatic",
"which provider am I on".

This only MAPS WORDS. It decides nothing: the request goes to the kernel's owner plane (POST models/switch, GET models/lanes), where the
kernel checks the lane (a private lane never gets a public provider), authorizes it and writes it to the audit log. A phrase that is
not clearly about providers returns None, so "switch to dark mode" or "use the calculator" are never taken for a provider switch.

    parse(text, known) -> None | {"action": "status"}
                        | {"action": "auto", "lanes": ["private", "public"]}      (or just the one lane named)
                        | {"action": "switch", "provider": "groq", "lane": "public"}
                        | {"action": "unknown", "word": "..."}                      (clearly about providers, but no such one)
    known = {"private": ["priv"], "public": ["pubA", "groq"]}   (the chain names the kernel reports per lane)
"""
from __future__ import annotations

import re

LANES = ("private", "public")
_FILLER = re.compile(r"^(?:(?:hey|ok|okay|hi|please|kindly|jarvis|neko)\b[\s,]*)+")
_STATUS = re.compile(r"\b(?:which|what)\s+(?:provider|model|llm)\b.*\b(?:am\s+i|are\s+you|are\s+we|is\s+jarvis)\b.*\b(?:on|using|use)\b"
                     r"|\bwhich\s+(?:provider|model)\b.*\bright\s+now\b|\bwhat(?:'s|\s+is)\s+the\s+(?:current|active)\s+(?:provider|model)\b"
                     r"|\bprovider\s+status\b")
_AUTO = re.compile(r"\b(?:back\s+to|go\s+back\s+to|return\s+to|switch\s+to|set\s+(?:it\s+)?to|use)\s+(?:the\s+)?(?:automatic|auto)\b"
                   r"|\b(?:reset|clear)\s+(?:the\s+|my\s+)?(?:provider|model)\s*(?:choice|preference|switch)?\b"
                   r"|\bstop\s+forcing\s+(?:a|the)\s+(?:provider|model)\b|\bautomatic\s+(?:provider|model)\s+(?:choice|selection)\b")
_SWITCH_VERB = re.compile(r"^(?:switch|change|go|move|set|use|route|send)\b|^make\b.*\b(?:the|my)\s+(?:private|public)?\s*(?:provider|model)\b")
_ABOUT_PROVIDERS = re.compile(r"\b(?:provider|providers|model|llm|lane|api)\b")


def _norm(text: str) -> str:
    t = re.sub(r"[^\w\s.\-]", " ", str(text or "").lower())
    t = re.sub(r"\s+", " ", t).strip()
    return _FILLER.sub("", t).strip()


def _find_provider(t: str, known: dict) -> tuple[str, list[str]] | None:
    """The longest known provider name that appears as a whole word, with the lanes that list it."""
    best: tuple[str, list[str]] | None = None
    seen: dict[str, list[str]] = {}
    for lane, names in known.items():
        for n in names:
            seen.setdefault(str(n), []).append(lane)
    for name in sorted(seen, key=len, reverse=True):
        if re.search(rf"(?<![\w-]){re.escape(name.lower())}(?![\w-])", t):
            best = (name, seen[name])
            break
    return best


def _named_lanes(t: str) -> list[str]:
    return [lane for lane in LANES if re.search(rf"\b{lane}\b", t)]


def parse(text: str, known: dict) -> dict | None:
    t = _norm(text)
    if not t or len(t) > 200:
        return None
    if _STATUS.search(t):
        return {"action": "status"}
    if _AUTO.search(t) and not _find_provider(t, known):
        named = _named_lanes(t)
        return {"action": "auto", "lanes": named or list(LANES)}
    if not _SWITCH_VERB.search(t):
        return None
    found = _find_provider(t, known)
    if found:
        name, lanes = found
        named = _named_lanes(t)
        lane = named[0] if len(named) == 1 else (lanes[0] if len(lanes) == 1 else None)
        if lane is None:
            return {"action": "unknown", "word": name}
        return {"action": "switch", "provider": name, "lane": lane}
    if _ABOUT_PROVIDERS.search(t):
        m = re.search(r"\b(?:to|use|with)\s+(?:the\s+)?([\w.\-]+(?:\s[\w.\-]+)?)", t)
        return {"action": "unknown", "word": (m.group(1) if m else "")[:40]}
    return None


def describe(result: dict, lanes: dict | None = None) -> str:
    """One plain sentence for the owner about what happened (the kernel's reply, never invented)."""
    if not isinstance(result, dict):
        return "I could not tell what happened."
    if result.get("error"):
        return f"The kernel did not switch: {str(result['error'])[:160]}."
    if result.get("status") == "done":
        prov, lane = result.get("preferred"), result.get("lane")
        return f"The {lane} lane is now set to {prov}." if prov else f"The {lane} lane is back to automatic."
    return "Done."
