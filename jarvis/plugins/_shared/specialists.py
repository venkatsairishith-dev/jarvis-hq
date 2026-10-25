"""The roster of local specialist models (D51): which llama-swap alias does which kind of work, from ONE table.

The table is jarvis/config/specialists.json (kind -> alias, fallback alias, max tokens, note). Code asks `resolve("screen_read")` and gets
the alias to send as the request's `model`; nothing else decides which model does what. The kind "reason" means the online provider
(through the privacy gateway) and has no local alias.

  * A missing file means the built-in copy of the table below (the laptop keeps working).
  * A file that is not JSON, a kind the code does not know, a kind that is not a plain word, an alias that is not written like a
    llama-swap name, or a local kind without an alias is a SpecialistError that says which entry.
  * An alias that llama-swap.yaml does not define (yet) is NOT an error: `not_installed()` lists them so a test or the doctor can say
    "not installed yet".

Standard library only.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

JARVIS = Path(__file__).resolve().parents[2]
TABLE_FILE = JARVIS / "config" / "specialists.json"
SWAP_FILE = JARVIS / "local_models" / "llama-swap.yaml"
KINDS = ("reason", "ui_decide", "screen_read", "screen_locate", "doc_read", "chat_simple")
_ALIAS = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._\-]{0,63}$")
_KIND = re.compile(r"^[a-z][a-z_]{1,30}$")


class SpecialistError(ValueError):
    """The table is wrong, or a kind was asked for that it does not have. The message is safe to show."""


@dataclass(frozen=True)
class Specialist:
    kind: str
    where: str                  # "local" | "online"
    alias: str = ""
    fallback: str = ""
    max_tokens: int = 0
    note: str = ""

    @property
    def aliases(self) -> tuple[str, ...]:
        return tuple(a for a in (self.alias, self.fallback) if a)


BUILT_IN = {
    "reason": {"where": "online", "note": "the online provider through the privacy gateway"},
    "ui_decide": {"where": "local", "alias": "decider", "fallback": "decider-ling", "max_tokens": 400},
    "screen_read": {"where": "local", "alias": "vision", "fallback": "vision-ground", "max_tokens": 200},
    "screen_locate": {"where": "local", "alias": "vision-ground", "fallback": "vision", "max_tokens": 200},
    "doc_read": {"where": "local", "alias": "lightonocr-docreader", "fallback": "ocr-docs", "max_tokens": 1800},
    "chat_simple": {"where": "local", "alias": "local-chat", "fallback": "general", "max_tokens": 400},
}


def parse(data) -> dict[str, Specialist]:
    """Validate a table (the file's content). Raises SpecialistError naming the entry that is wrong."""
    kinds = data.get("kinds") if isinstance(data, dict) else None
    if not isinstance(kinds, dict) or not kinds:
        raise SpecialistError("specialists table: needs a non-empty \"kinds\" object")
    out: dict[str, Specialist] = {}
    for kind, row in kinds.items():
        if not isinstance(kind, str) or not _KIND.match(kind):
            raise SpecialistError(f"specialists table: {str(kind)[:30]!r} is not a task kind name")
        if kind not in KINDS:
            raise SpecialistError(f"specialists table: unknown task kind {kind!r}; the code knows {', '.join(KINDS)}")
        if not isinstance(row, dict):
            raise SpecialistError(f"specialists table: kind {kind!r} must be an object")
        where = row.get("where", "local")
        if where not in ("local", "online"):
            raise SpecialistError(f"specialists table: kind {kind!r}: where must be \"local\" or \"online\"")
        if (kind == "reason") != (where == "online"):
            raise SpecialistError(f"specialists table: kind {kind!r}: only \"reason\" is online, every other kind runs locally")
        alias, fallback = row.get("alias", ""), row.get("fallback", "")
        for label, value in (("alias", alias), ("fallback", fallback)):
            if value and (not isinstance(value, str) or not _ALIAS.match(value)):
                raise SpecialistError(f"specialists table: kind {kind!r}: {label} {str(value)[:30]!r} is not a llama-swap model name")
        if where == "local" and not alias:
            raise SpecialistError(f"specialists table: kind {kind!r} runs locally and needs an alias")
        if where == "online" and (alias or fallback):
            raise SpecialistError(f"specialists table: kind {kind!r} is the online provider and takes no local alias")
        mt = row.get("max_tokens", 0)
        if not isinstance(mt, int) or isinstance(mt, bool) or not 0 <= mt <= 8192:
            raise SpecialistError(f"specialists table: kind {kind!r}: max_tokens must be a whole number from 0 to 8192")
        out[kind] = Specialist(kind, where, alias, fallback, mt, str(row.get("note") or "")[:300])
    return out


def load(path: Path | str | None = None) -> dict[str, Specialist]:
    """The table. No file means the built-in defaults; a file that is there but wrong is an error."""
    p = Path(path) if path else TABLE_FILE
    try:
        text = p.read_text(encoding="utf-8")
    except OSError:
        return parse({"kinds": BUILT_IN})
    try:
        data = json.loads(text)
    except ValueError:
        raise SpecialistError("specialists table: the file is not valid JSON") from None
    return parse(data)


def resolve(kind: str, table: dict[str, Specialist] | None = None) -> Specialist:
    table = load() if table is None else table
    if kind not in table:
        raise SpecialistError(f"no specialist for task kind {str(kind)[:30]!r}; known kinds: {', '.join(sorted(table))}")
    return table[kind]


def swap_aliases(swap_file: Path | str | None = None) -> set[str]:
    """Every model id and alias that llama-swap.yaml defines (comments ignored). Empty when the file cannot be read."""
    try:
        text = Path(swap_file or SWAP_FILE).read_text(encoding="utf-8")
    except OSError:
        return set()
    text = "\n".join(ln for ln in text.splitlines() if not ln.lstrip().startswith("#"))
    section = text.split("\nmodels:", 1)[-1].split("\ngroups:", 1)[0]
    names = set(re.findall(r"(?m)^  ([A-Za-z0-9][\w.\-]*):\s*$", section))
    for group in re.findall(r"(?m)^\s+aliases:\s*\[([^\]]*)\]", section):
        names |= {a.strip() for a in group.split(",") if a.strip()}
    return names


def not_installed(table: dict[str, Specialist] | None = None, swap_file: Path | str | None = None) -> list[tuple[str, str, str]]:
    """[(kind, "alias"|"fallback", name)] for every alias the llama-swap file does not define yet. Not an error: the roster may be ahead of the install."""
    table = load() if table is None else table
    have = swap_aliases(swap_file)
    if not have:
        return []
    return [(k, label, name) for k, s in table.items() for label, name in (("alias", s.alias), ("fallback", s.fallback)) if name and name not in have]
