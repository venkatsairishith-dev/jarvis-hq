"""API keys stored as references, so the secret itself can stay in a protected folder.

A stored API key may be written as ``file:<absolute path>``. At read time the runtime config gets the first line of that
file instead. The stored value stays the reference: ``core_config.json`` and the settings page never contain the secret,
and saving the settings page writes the same reference back.

This is deliberately narrow:
  * Only files under a folder named in ``NEKO_SECRET_REF_ROOTS`` (path-separator list) are read. With the variable unset
    nothing is resolved, so a config value cannot become an arbitrary file read.
  * Absolute paths only, at most 4 KiB, first line only.
  * Failures resolve to an empty key (the provider then reads as "not configured") and never log the value.
"""

from __future__ import annotations

import os
import threading
from pathlib import Path
from typing import Any, MutableMapping

PREFIX = "file:"
ROOTS_ENV = "NEKO_SECRET_REF_ROOTS"
MAX_BYTES = 4096

_cache: dict[str, tuple[int, int, str]] = {}      # real path -> (mtime_ns, size, value)
_lock = threading.Lock()


def is_reference(value: Any) -> bool:
    return isinstance(value, str) and value.startswith(PREFIX)


def _roots() -> list[Path]:
    out: list[Path] = []
    for part in os.environ.get(ROOTS_ENV, "").split(os.pathsep):
        part = part.strip().strip('"')
        if part:
            try:
                out.append(Path(part).resolve())
            except OSError:
                continue
    return out


def resolve_reference(value: str) -> str:
    """The secret a ``file:`` reference points at, or '' when it cannot be used."""
    roots = _roots()
    if not roots:
        return ""
    raw = value[len(PREFIX):].strip().strip('"')
    if not raw:
        return ""
    path = Path(raw)
    if not path.is_absolute():
        return ""
    try:
        real = path.resolve(strict=True)
        if not real.is_file() or not any(real == root or root in real.parents for root in roots):
            return ""
        st = real.stat()
        if st.st_size > MAX_BYTES:
            return ""
        key = str(real)
        with _lock:
            hit = _cache.get(key)
            if hit and hit[0] == st.st_mtime_ns and hit[1] == st.st_size:
                return hit[2]
        text = real.read_text(encoding="utf-8-sig", errors="replace")
        lines = text.splitlines()
        secret = lines[0].strip() if lines else ""
        with _lock:
            _cache[key] = (st.st_mtime_ns, st.st_size, secret)
        return secret
    except (OSError, ValueError):
        return ""


def resolve_config_secrets(config: MutableMapping[str, Any]) -> MutableMapping[str, Any]:
    """Replace ``file:`` references in API-key fields of a runtime config dict with the secrets they name."""
    for name, value in list(config.items()):
        if is_reference(value) and isinstance(name, str) and (name.endswith("_API_KEY") or name.startswith("ASSIST_API_KEY")):
            config[name] = resolve_reference(value)
    return config
