"""The console's door to the language layer (D58). The console runs in its own Python and does not import the `jarvis` plugin package, so the shared module
(jarvis/plugins/_shared/translate.py, standard library only) is loaded BY PATH, once. Everything here never raises: if the layer is missing the text is returned
as it was and the caller's behaviour is exactly what it was before.

  english(text, source)       the English text of what the owner typed (English: the same text, no model call, nothing recorded)
  english_info(text, source)  the whole result {"english", "source_lang", "confidence", "translated", ...} or None for English / no layer
"""
from __future__ import annotations

import importlib.util
import os
import sys

_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "plugins", "_shared", "translate.py")


def shared():
    if os.environ.get("JARVIS_TRANSLATE", "1") == "0":
        return None
    if "jarvis_translate" in sys.modules:
        return sys.modules["jarvis_translate"]
    try:
        from jarvis.plugins._shared import translate as mod            # tests and a venv that has the package
        return mod
    except ImportError:
        pass
    try:
        spec = importlib.util.spec_from_file_location("jarvis_translate", _PATH)
        mod = importlib.util.module_from_spec(spec)
        sys.modules["jarvis_translate"] = mod
        spec.loader.exec_module(mod)
        return mod
    except Exception:                                                    # noqa: BLE001
        sys.modules.pop("jarvis_translate", None)
        return None


def english_info(text: str, source: str = "console"):
    mod = shared()
    if mod is None or not str(text or "").strip():
        return None
    try:
        info = mod.english_info(text, source=source)
    except Exception:                                                    # noqa: BLE001
        return None
    return None if info.get("source_lang") == "en" else info


def english(text: str, source: str = "console") -> str:
    info = english_info(text, source)
    return str(text) if info is None else info["english"]
