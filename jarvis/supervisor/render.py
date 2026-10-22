"""Config renderer (plan P1 step 5): fills ${NAME} placeholders and refuses to write anything that looks like a secret.

Only plain settings and "file:<name>" references (to a file under the secrets folder) may land in a rendered file; the secret itself
never does. Errors name the key, never the value.
"""
from __future__ import annotations

import os
import re
from pathlib import Path

PLACEHOLDER = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")
TOKENISH = re.compile(r"[A-Za-z0-9_\-+/=]{20,}")
SECRET_PREFIXES = ("sk-", "ghp_", "xox", "AKIA", "eyJ")


def looks_secret(value: str) -> bool:
    v = value.strip()
    if v.startswith("file:"):
        return False
    if v.startswith(SECRET_PREFIXES):
        return True
    return bool(TOKENISH.fullmatch(v)) and any(c.isalpha() for c in v) and any(c.isdigit() for c in v)


def render(template: str, values: dict[str, str]) -> str:
    for key, val in values.items():
        if looks_secret(str(val)):
            raise ValueError(f"value for {key!r} looks like a secret; use a file: reference")

    def sub(m: re.Match) -> str:
        name = m.group(1)
        if name not in values:
            raise KeyError(name)
        return str(values[name])

    return PLACEHOLDER.sub(sub, template)


def render_to(template_path: Path, out_path: Path, values: dict[str, str]) -> Path:
    text = render(Path(template_path).read_text(encoding="utf-8"), values)
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(text, encoding="utf-8")
    if os.name == "posix":
        out_path.chmod(0o600)
    return out_path


def scan_dir(path: Path) -> list[str]:
    """Relative paths of files under path holding a secret-looking string (a stand-in for a gitleaks scan in tests)."""
    root = Path(path)
    hits: list[str] = []
    for f in sorted(root.rglob("*")):
        if not f.is_file():
            continue
        try:
            text = f.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        if any(looks_secret(tok) for tok in re.findall(r"[A-Za-z0-9_\-+/=]{20,}", text)) or re.search(r"\b(sk-|ghp_|AKIA|eyJ)[A-Za-z0-9_\-]{8,}", text):
            hits.append(f.relative_to(root).as_posix())
    return hits
