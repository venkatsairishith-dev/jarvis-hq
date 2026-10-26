"""Add the `simple_tasks` catalogue's entries to the gate as goals (D59).

The catalogue of simple tasks was being written by another session when this was built, so its file is NOT on this branch and its exact format is
unknown. The adapter below takes the smallest shape any catalogue can be reduced to, and the laptop session maps the real file to it in a few lines:

    [{"id": "open-settings", "phrases": ["open settings", "settings kholo"],
      "verify": [{"check": "window_appeared", "title_contains": "Settings"}],
      "setup": [], "forbid": [], "kind": "act", "lang": "en"}]

Each phrase becomes one goal (`simple-<id>-<n>`) of the capability `simple_tasks`; an entry without a `verify` list is refused (a goal with no way to
check its effect is not a goal). `{work}` and the other template fields work in phrases and checks.
"""
from __future__ import annotations

import json
from pathlib import Path

from . import model


class CatalogueError(ValueError):
    pass


def goals_from_entries(entries: list[dict]) -> list[model.Goal]:
    goals: list[model.Goal] = []
    for e in entries:
        if not e.get("id") or not e.get("phrases"):
            raise CatalogueError(f"catalogue entry without id or phrases: {str(e)[:80]}")
        if not e.get("verify"):
            raise CatalogueError(f"catalogue entry {e['id']} has no verify checks, so its effect could not be measured")
        for n, phrase in enumerate(e["phrases"], 1):
            goals.append(model.Goal(id=f"simple-{e['id']}-{n}", cap="simple_tasks", text=str(phrase), kind=e.get("kind", "act"), lang=e.get("lang", "en"),
                                    setup=list(e.get("setup", [])), verify=list(e["verify"]), forbid=list(e.get("forbid", [])),
                                    timeout_s=float(e.get("timeout_s", 120)), tags=["catalogue"]))
    return goals


def merge(cat: model.Catalog, path: str | Path) -> int:
    """Append the catalogue's goals to the `simple_tasks` capability (existing goal ids are kept, not duplicated). Returns how many were added."""
    entries = json.loads(Path(path).read_text(encoding="utf-8"))
    if isinstance(entries, dict):
        entries = entries.get("entries", [])
    new = goals_from_entries(entries)
    cap = cat.capabilities["simple_tasks"]
    have = {g.id for g in cap.goals}
    added = [g for g in new if g.id not in have]
    cap.goals.extend(added)
    return len(added)
