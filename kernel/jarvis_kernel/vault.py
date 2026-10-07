"""Vault write guard. Agents may write only Markdown files under inbox/, daily/ and decisions/ of the vault.
Everything else (the user's own notes, .git, .obsidian) is refused. Content passes the secret scrubber.
"""
import os

from .scrub import scrub

ALLOWED = ("inbox", "daily", "decisions")
MAX_BYTES = 100_000


class VaultError(Exception):
    pass


def _resolve(root: str, rel: str) -> str:
    root_real = os.path.realpath(root)
    if os.path.isabs(rel) or rel.startswith(("/", "\\")) or (len(rel) > 1 and rel[1] == ":"):
        raise VaultError("absolute paths are not allowed")
    full = os.path.realpath(os.path.join(root_real, rel))  # resolves .., symlinks and junctions
    if os.path.commonpath([root_real, full]) != root_real:
        raise VaultError("path escapes the vault")
    parts = os.path.relpath(full, root_real).replace("\\", "/").split("/")
    if len(parts) < 2 or parts[0] not in ALLOWED:
        raise VaultError(f"agents may write only under {', '.join(ALLOWED)}")
    if any(p.startswith(".") for p in parts):
        raise VaultError("hidden files and folders are not allowed")
    if not full.lower().endswith(".md"):
        raise VaultError("only .md files")
    return full


def write(root: str, rel: str, text: str, append: bool = False, kernel=None) -> str:
    """Write text to the vault. Returns the path written. Raises VaultError when refused. Audited if a kernel is given."""
    try:
        full = _resolve(root, rel)
        clean, findings, blocked = scrub(text)
        if blocked:
            raise VaultError("refused: content contains a private key")
        data = clean.encode("utf-8")
        if len(data) > MAX_BYTES:
            raise VaultError("file too large")
        os.makedirs(os.path.dirname(full), exist_ok=True)
        if os.path.islink(full):
            raise VaultError("refusing to write through a link")
        with open(full, "ab" if append else "wb") as f:
            f.write(data)
        if kernel:
            kernel.audit.append("vault_write", {"path": rel.replace("\\", "/"), "bytes": len(data), "scrubbed": findings, "append": append})
        return full
    except VaultError as e:
        if kernel:
            kernel.audit.append("vault_write_refused", {"path": str(rel)[:200], "why": str(e)})
        raise
