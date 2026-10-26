"""The language layer on the SERVER side of the kernel (D58): Telegram text and owner commands reach Hermes in English or are flagged.

The owner's models read English only. The translation models live on the owner's LAPTOP (the face, the console and the voice path translate before text
leaves it). Text that first arrives on the server (Telegram) has not been through them. This module decides what the kernel does with it:

  * English: passes through, no cost, nothing recorded.
  * Telugu, Hindi or romanized Telugu/Hindi (detected deterministically: script blocks, a small word list) and a LOCAL translator is configured on this machine
    (JARVIS_TRANSLATE_KERNEL=local, default OFF: hermes-agent-vm has 2 CPU and about 11 GiB next to Hermes and the kernel, and no translation model was measured
    there): the text is translated here with the same shared module, local backends only.
  * otherwise: the text goes on AS IT IS and the caller records "skipped" (audit event, and the X-Jarvis-Translated: skipped header on the llm-proxy reply). The
    message is never dropped and never sent to an online translator.

The shared module (jarvis/plugins/_shared/translate.py, standard library only) is loaded BY PATH: the server has no `jarvis` package. If the file is not deployed
there, `shared()` is None and the text is flagged by a built-in script check.

A laptop-side hand-off (the kernel queues a "translate" job for the laptop node and waits) is the intended next step and is NOT built here (see D58).
"""
from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_CANDIDATES = (_HERE.parent.parent / "plugins" / "_shared" / "translate.py",)      # <jarvis>/kernel/jarvis_kernel -> <jarvis>/plugins/_shared


def mode() -> str:
    """'off' (default on the server) or 'local' (translate here with a local backend the owner installed)."""
    return "local" if os.environ.get("JARVIS_TRANSLATE_KERNEL", "off").strip().lower() == "local" else "off"


def shared():
    """The shared language module, or None. Never raises."""
    if "jarvis_translate" in sys.modules:
        return sys.modules["jarvis_translate"]
    try:
        from jarvis.plugins._shared import translate as mod            # the laptop, and tests
        return mod
    except ImportError:
        pass
    for path in _CANDIDATES:
        if path.exists():
            try:
                spec = importlib.util.spec_from_file_location("jarvis_translate", path)
                mod = importlib.util.module_from_spec(spec)
                sys.modules["jarvis_translate"] = mod
                spec.loader.exec_module(mod)
                return mod
            except Exception:                                            # noqa: BLE001
                sys.modules.pop("jarvis_translate", None)
    return None


def _native_letters(text: str) -> int:
    return sum(1 for c in str(text) if 0x0900 <= ord(c) <= 0x097F or 0x0C00 <= ord(c) <= 0x0C7F)


def english_body(body: dict, translator=None) -> tuple[dict, str]:
    """(body, status) for a chat request on the server (llm-proxy: Hermes and the lab agents). Only the LAST message, and only if it is a user message,
    is looked at: system prompts, earlier turns and tool results are data and are never translated. status as in english_for_agent."""
    msgs = body.get("messages") if isinstance(body, dict) else None
    if not isinstance(msgs, list) or not msgs or not isinstance(msgs[-1], dict) or msgs[-1].get("role") != "user":
        return body, "no"
    c = msgs[-1].get("content")
    text = c if isinstance(c, str) else " ".join(p.get("text", "") for p in (c or []) if isinstance(p, dict) and isinstance(p.get("text"), str))
    mod = shared()
    try:
        if mod is None:
            return body, ("skipped" if _native_letters(text) >= 3 else "no")
        if mod.detect_language(text) == "en":
            return body, "no"
        if translator is None and mode() != "local":
            return body, "skipped"
        new, res = mod.english_messages(msgs, translator=translator, source="llm-proxy")
        if res is None:
            return body, "no"
        return ({**body, "messages": new}, "yes") if res.translated else (body, "skipped")
    except Exception:                                                    # noqa: BLE001
        return body, "skipped"


def english_for_agent(text: str, source: str = "telegram", translator=None) -> tuple[str, str]:
    """(text, status) with status "no" (English, untouched), "yes" (translated here) or "skipped" (non-English and left as it was)."""
    text = str(text or "")
    mod = shared()
    if mod is None:
        return text, ("skipped" if _native_letters(text) >= 3 else "no")
    try:
        if translator is None and mode() != "local":
            return text, ("skipped" if mod.detect_language(text) != "en" else "no")
        info = mod.translate_to_english(text, translator=translator, source=source)
        if info["source_lang"] == "en":
            return text, "no"
        return (info["english"], "yes") if info["translated"] else (text, "skipped")
    except Exception:                                                    # noqa: BLE001 - the kernel never breaks on the language layer
        return text, "skipped"
