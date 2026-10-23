"""A study vault in Obsidian format: plain Markdown notes in a folder, searchable, with progress tracking.

Design idea from Cyrene-Agent's Learn mode (MIT): bind a vault, understand material, organise notes, generate exercises,
maintain progress. Safety rules here: every path is relative to the vault and must stay inside it, only `.md` files are
touched, hidden folders (including `.obsidian`) are off limits, a note is never overwritten (create fails if it exists,
append adds to it), and progress lives in a reserved `_progress/` folder the note tools cannot write to.
This vault is separate from JARVIS's memory vault, whose write rules (inbox, daily, decisions only) are unchanged.
"""

from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path
from typing import Any

MAX_NOTE_BYTES = 100 * 1024
MAX_READ_BYTES = 200 * 1024
MAX_FILES_SCANNED = 2000
MAX_SEARCH_FILE_BYTES = 500 * 1024
STATUSES = ("not_started", "learning", "review", "mastered")
RESERVED = "_progress"
_WORD = re.compile(r"[A-Za-z0-9\u00C0-\uFFFF]+")


class VaultError(Exception):
    """A refusal or failure whose message is safe to show to the user."""


def vault_root() -> Path:
    return Path(os.environ.get("LEARN_VAULT_DIR", r"D:\jarvis\learn-vault")).resolve()


def _clean_rel(path_str: str, *, for_write: bool) -> Path:
    first = str(path_str or "").strip().replace("\\", "/")
    if re.match(r"^[A-Za-z]:", first) or first.startswith("/"):       # checked before any stripping
        raise VaultError("Use a path inside the vault, not an absolute path.")
    raw = first.strip("/")
    if not raw:
        raise VaultError("Give the note's path inside the vault, for example notes/photosynthesis.md.")
    parts = [p for p in raw.split("/") if p]
    if any(p in (".", "..") or p.startswith(".") for p in parts):
        raise VaultError("Paths with '..' or hidden folders are not allowed.")
    if any(re.search(r'[<>:"|?*\x00-\x1f]', p) for p in parts):
        raise VaultError("The path contains characters that are not allowed.")
    if for_write and parts[0].lower() == RESERVED:
        raise VaultError(f"'{RESERVED}' is reserved for progress tracking.")
    rel = Path(*parts)
    if rel.suffix.lower() != ".md":
        rel = rel.with_name(rel.name + ".md")
    if len(str(rel)) > 200:
        raise VaultError("The path is too long.")
    return rel


def _inside(root: Path, rel: Path) -> Path:
    full = (root / rel).resolve()
    if full != root and root not in full.parents:
        raise VaultError("That path leaves the vault.")
    return full


def status() -> dict[str, Any]:
    root = vault_root()
    if not root.is_dir():
        return {"exists": False, "path": str(root), "notes": 0, "message": "The study vault does not exist yet; saving a note creates it."}
    notes = sum(1 for p in root.rglob("*.md") if not any(part.startswith(".") for part in p.relative_to(root).parts))
    return {"exists": True, "path": str(root), "notes": notes, "topics": len(progress())}


def list_notes(folder: str = "", limit: int = 100) -> list[dict[str, Any]]:
    root = vault_root()
    if not root.is_dir():
        return []
    base = root
    if str(folder or "").strip():
        rel = Path(*[p for p in str(folder).replace("\\", "/").split("/") if p])
        if any(p.startswith(".") or p == ".." for p in rel.parts):
            raise VaultError("Paths with '..' or hidden folders are not allowed.")
        base = _inside(root, rel)
    out = []
    for p in sorted(base.rglob("*.md")):
        r = p.relative_to(root)
        if any(part.startswith(".") for part in r.parts):
            continue
        out.append({"path": r.as_posix(), "bytes": p.stat().st_size})
        if len(out) >= max(1, min(int(limit), 500)):
            break
    return out


def read_note(path_str: str) -> dict[str, Any]:
    root = vault_root()
    rel = _clean_rel(path_str, for_write=False)
    full = _inside(root, rel)
    if not full.is_file():
        raise VaultError("That note does not exist.")
    if full.stat().st_size > MAX_READ_BYTES:
        raise VaultError("That note is too large to read (200 KB limit).")
    return {"path": rel.as_posix(), "text": full.read_text(encoding="utf-8", errors="replace")}


def search_notes(query: str, limit: int = 10) -> list[dict[str, Any]]:
    root = vault_root()
    words = [w.lower() for w in _WORD.findall(str(query or ""))][:8]
    if not words:
        raise VaultError("Give a few words to search for.")
    if not root.is_dir():
        return []
    hits: list[dict[str, Any]] = []
    scanned = 0
    for p in root.rglob("*.md"):
        r = p.relative_to(root)
        if any(part.startswith(".") for part in r.parts):
            continue
        scanned += 1
        if scanned > MAX_FILES_SCANNED:
            break
        try:
            if p.stat().st_size > MAX_SEARCH_FILE_BYTES:
                continue
            text = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        low = text.lower()
        title = r.stem.lower()
        score = sum(low.count(w) for w in words) + 5 * sum(1 for w in words if w in title)
        if not score:
            continue
        snippet = ""
        for line in text.splitlines():
            if any(w in line.lower() for w in words):
                snippet = " ".join(line.split())[:160]
                break
        hits.append({"path": r.as_posix(), "score": score, "snippet": snippet})
    hits.sort(key=lambda h: (-h["score"], h["path"]))
    return hits[: max(1, min(int(limit), 50))]


def save_note(path_str: str, content: str, mode: str = "create") -> dict[str, Any]:
    mode = str(mode or "create").lower()
    if mode not in ("create", "append"):
        raise VaultError("mode must be create or append.")
    text = str(content or "")
    if not text.strip():
        raise VaultError("There is no content to save.")
    if len(text.encode("utf-8")) > MAX_NOTE_BYTES:
        raise VaultError("The note is too long (100 KB limit).")
    root = vault_root()
    rel = _clean_rel(path_str, for_write=True)
    full = _inside(root, rel)
    exists = full.exists()
    if exists and mode == "create":
        raise VaultError("That note already exists. Use append to add to it, or choose another name.")
    if not exists and mode == "append":
        raise VaultError("That note does not exist yet. Use create.")
    full.parent.mkdir(parents=True, exist_ok=True)
    if mode == "append":
        with open(full, "a", encoding="utf-8", newline="") as fh:
            fh.write("\n" + (text if text.endswith("\n") else text + "\n"))
    else:
        full.write_text(text if text.endswith("\n") else text + "\n", encoding="utf-8", newline="")
    return {"path": rel.as_posix(), "mode": mode, "bytes": full.stat().st_size}


# ---------- progress ----------
def _progress_file() -> Path:
    return vault_root() / RESERVED / "progress.jsonl"


def record_progress(topic: str, status_value: str, note: str = "") -> dict[str, Any]:
    topic = " ".join(str(topic or "").split())[:100]
    status_value = str(status_value or "").lower()
    if not topic:
        raise VaultError("Give the topic.")
    if status_value not in STATUSES:
        raise VaultError(f"status must be one of: {', '.join(STATUSES)}.")
    event = {"ts": time.strftime("%Y-%m-%d %H:%M:%S"), "topic": topic, "status": status_value, "note": " ".join(str(note or "").split())[:300]}
    f = _progress_file()
    f.parent.mkdir(parents=True, exist_ok=True)
    with open(f, "a", encoding="utf-8", newline="") as fh:
        fh.write(json.dumps(event, ensure_ascii=False) + "\n")
    _write_progress_table()
    return event


def progress() -> list[dict[str, Any]]:
    """The latest status per topic, oldest topic first."""
    f = _progress_file()
    if not f.is_file():
        return []
    latest: dict[str, dict[str, Any]] = {}
    for line in f.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            e = json.loads(line)
        except ValueError:
            continue
        if isinstance(e, dict) and e.get("topic") and e.get("status") in STATUSES:
            latest[str(e["topic"]).lower()] = e
    return list(latest.values())


def _write_progress_table() -> None:
    rows = progress()
    lines = ["# Learning progress", "", "| Topic | Status | Updated | Note |", "|---|---|---|---|"]
    for e in rows:
        lines.append("| {} | {} | {} | {} |".format(str(e["topic"]).replace("|", "/"), e["status"], e["ts"], str(e.get("note", "")).replace("|", "/")))
    (_progress_file().parent / "progress.md").write_text("\n".join(lines) + "\n", encoding="utf-8", newline="")
