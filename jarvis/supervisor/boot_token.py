"""Per-boot token for the supervisor (plan P1 step 5): one fresh random token each boot, kept in the run directory only."""
from __future__ import annotations

import os
import secrets
from pathlib import Path

TOKEN_FILE = "boot.token"


def new_token() -> str:
    return secrets.token_hex(32)


def write_token(run_dir: Path, token: str) -> Path:
    run_dir = Path(run_dir)
    run_dir.mkdir(parents=True, exist_ok=True)
    path = run_dir / TOKEN_FILE
    path.write_text(token, encoding="utf-8")
    if os.name == "posix":
        path.chmod(0o600)
    return path


def read_token(run_dir: Path) -> str | None:
    try:
        return (Path(run_dir) / TOKEN_FILE).read_text(encoding="utf-8").strip() or None
    except OSError:
        return None


def rotate(run_dir: Path) -> str:
    """A new token for this boot; any token left by an earlier boot is overwritten."""
    token = new_token()
    write_token(run_dir, token)
    return token
