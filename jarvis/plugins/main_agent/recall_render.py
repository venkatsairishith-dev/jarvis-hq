"""The one place recalled memories become prompt text: a numbered block with a ceiling on every entry, every line and the whole block.

Recall hits go straight into a model prompt, and a single merged reflection can be thousands of tokens, so the block is cut at
three levels: the text of each entry, each rendered line (the tier/entity tag echoes unknown values verbatim), and the block as
a whole. Entries are truncated, never dropped for length alone: the list is ranked by relevance, so half of the top hit beats none.

Line format: `1. [fact/about the user] text  (2026-05-01, 3mo ago)`. The date is when the remembered event happened (event end,
else event start, else when it was written), so the model reads the age of the fact and not of the note.

Adapted from N.E.K.O's memory/recall_render.py (Apache-2.0, https://github.com/Project-N-E-K-O/N.E.K.O), English only, with
token counts estimated at about four characters per token instead of a tokenizer.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

CHARS_PER_TOKEN = 4
ENTRY_MAX_TOKENS = 400
LINE_OVERHEAD_TOKENS = 40
TOTAL_MAX_TOKENS = 2204

TIER_LABEL = {"fact": "fact", "reflection": "impression", "fact_archive": "archived fact"}
ENTITY_LABEL = {"master": "about the user", "owner": "about the user", "self": "about self", "relationship": "relationship",
                "group_chat": "group chat", "participant": "participant", "group_participant": "group member"}


@dataclass(frozen=True)
class RenderedRecallBlock:
    text: str
    kept: int
    dropped: int


def tokens(text: str) -> int:
    return -(-len(text) // CHARS_PER_TOKEN)


def truncate_to_tokens(text: str, limit: int) -> str:
    cap = limit * CHARS_PER_TOKEN
    return text if len(text) <= cap else text[:cap].rstrip() + "…"


def entry_tag(tier: object, entity: object) -> str:
    """`[tier/entity]`. A value the tables do not know is shown as it is, not swallowed."""
    tier_key, entity_key = str(tier or "").strip(), str(entity or "").strip()
    return f"[{TIER_LABEL.get(tier_key) or tier_key or '?'}/{ENTITY_LABEL.get(entity_key) or entity_key or '-'}]"


def parse_time(value: Any) -> datetime | None:
    """An ISO time as the naive local clock; None for anything that is not a usable time."""
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.astimezone().replace(tzinfo=None) if parsed.tzinfo else parsed


def age_label(anchor: datetime, now: datetime | None = None) -> str:
    days = max(0, int(((now or datetime.now()) - anchor).total_seconds() // 86400))
    if days == 0:
        return "now"
    if days < 7:
        return f"{days}d ago"
    if days < 30:
        return f"{days // 7}w ago"
    return f"{days // 30}mo ago"


def time_suffix(entry: Mapping[str, Any], now: datetime | None = None) -> str:
    """`  (2026-05-01, 3mo ago)` from the first anchor that PARSES (not the first that is non-empty)."""
    for key in ("event_end_at", "event_start_at", "created_at"):
        anchor = parse_time(entry.get(key))
        if anchor is not None:
            return f"  ({anchor:%Y-%m-%d}, {age_label(anchor, now)})"
    return ""


def take_lines_within_budget(lines: Sequence[str], budget_tokens: int) -> tuple[list[str], int]:
    """The leading lines that fit; the first is always kept (it was already cut to the line ceiling). Returns (kept, dropped)."""
    kept: list[str] = []
    used = 0
    for line in lines:
        cost = tokens(line) + (1 if kept else 0)
        if kept and used + cost > budget_tokens:
            break
        kept.append(line)
        used += cost
    return kept, len(lines) - len(kept)


def render_recall_block(results: Sequence[Mapping[str, Any]] | None, lang: str = "en", *, header_template: str | None = None,
                        now: datetime | None = None) -> RenderedRecallBlock:
    """The numbered block the model receives. `lang` is accepted for the callers' sake; the text is English."""
    lines: list[str] = []
    for item in results or []:
        if not isinstance(item, Mapping):
            continue
        text = str(item.get("text") or "").strip()
        if not text:
            continue
        text = truncate_to_tokens(text, ENTRY_MAX_TOKENS)
        lines.append(f"{len(lines) + 1}. {entry_tag(item.get('tier'), item.get('entity'))} {text}{time_suffix(item, now)}")
    lines = [truncate_to_tokens(line, ENTRY_MAX_TOKENS + LINE_OVERHEAD_TOKENS) for line in lines]
    budget = TOTAL_MAX_TOKENS
    if header_template is not None:
        budget -= tokens(header_template.format(n=len(lines))) + 1
    kept, dropped = take_lines_within_budget(lines, max(0, budget))
    block = list(kept)
    if header_template is not None:
        block.insert(0, header_template.format(n=len(kept)))
    return RenderedRecallBlock("\n".join(block), len(kept), dropped)


__all__ = ["RenderedRecallBlock", "render_recall_block"]
