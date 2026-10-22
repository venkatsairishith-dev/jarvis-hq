"""Combos: named, ordered fallback lists of (provider, model) targets, all in one lane.

A public-lane token can never resolve a private-lane combo, and a combo can never mix lanes."""
from dataclasses import dataclass
from typing import NamedTuple

LANES = ("private", "public")


class ComboError(Exception):
    """A combo definition is invalid."""


class RouteDenied(Exception):
    """The token's lane may not use this combo."""


class Target(NamedTuple):
    provider: str
    model: str


@dataclass(frozen=True)
class Combo:
    name: str
    lane: str
    targets: tuple


def _target(t) -> Target:
    if isinstance(t, dict):
        return Target(str(t.get("provider", "")), str(t.get("model", "")))
    if isinstance(t, (list, tuple)) and len(t) == 2:
        return Target(str(t[0]), str(t[1]))
    raise ComboError("bad target")


def load_combos(data: dict, providers) -> dict:
    """data: {"combos": [{"name", "lane", "targets": [[provider, model], ...]}]}. Returns {name: Combo}."""
    by_name = {p.name: p for p in providers}
    out: dict = {}
    for raw in data.get("combos", []):
        name = raw.get("name")
        if not name or name in out:
            raise ComboError("duplicate or missing combo name")
        lane = raw.get("lane")
        if lane not in LANES:
            raise ComboError("unknown lane")
        targets = tuple(_target(t) for t in raw.get("targets", []))
        if not targets:
            raise ComboError("empty combo")
        for t in targets:
            p = by_name.get(t.provider)
            if p is None:
                raise ComboError("unknown provider")
            if p.paid:
                raise ComboError("paid provider")
            if p.lane != lane:
                raise ComboError("lane mismatch")
        out[name] = Combo(name, lane, targets)
    return out


def resolve(combo: Combo, token_lane: str) -> tuple:
    if token_lane != combo.lane:
        raise RouteDenied("lane mismatch")
    return combo.targets
