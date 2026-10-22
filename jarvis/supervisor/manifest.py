"""Supervisor manifest (plan P1 step 4): the list of processes JARVIS owns, validated before anything starts.

A manifest is a JSON object {"components": [ {...}, ... ]}. Each component:
    name        unique, [a-z0-9_-]+
    argv        list of strings, the command (no shell)
    cwd         optional working directory
    env         optional plain settings; a value that looks like a secret is refused (use "file:<name>" references)
    bind        optional {"host": "...", "port": N}: checked with binds.assert_bind (loopback, or listed in "allowed_hosts")
    health      optional {"kind": "tcp"|"http", "path": "/health", "timeout_s": 30}: always against the bind address
    depends_on  optional list of component names that must be healthy first
    restart     "never" (default) | "on-failure"
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

try:                                    # imported as a package (jarvis.supervisor) or from this folder (tests)
    from . import binds, render
except ImportError:                     # pragma: no cover
    import binds  # type: ignore
    import render  # type: ignore

NAME = re.compile(r"^[a-z0-9][a-z0-9_-]*$")


class ManifestError(ValueError):
    pass


@dataclass
class Component:
    name: str
    argv: list[str]
    cwd: str | None = None
    env: dict[str, str] = field(default_factory=dict)
    host: str | None = None
    port: int | None = None
    health_kind: str | None = None
    health_path: str = "/"
    health_timeout_s: float = 30.0
    depends_on: list[str] = field(default_factory=list)
    restart: str = "never"


def _component(raw: dict, allowed_hosts: tuple[str, ...]) -> Component:
    name = str(raw.get("name", ""))
    if not NAME.match(name):
        raise ManifestError(f"bad component name {name!r}")
    argv = raw.get("argv")
    if not isinstance(argv, list) or not argv or not all(isinstance(a, str) for a in argv):
        raise ManifestError(f"{name}: argv must be a non-empty list of strings")
    env = {str(k): str(v) for k, v in (raw.get("env") or {}).items()}
    for k, v in env.items():
        if render.looks_secret(v):
            raise ManifestError(f"{name}: env {k} looks like a secret; use a file: reference")
    c = Component(name=name, argv=argv, cwd=raw.get("cwd"), env=env, depends_on=list(raw.get("depends_on") or []))
    bind = raw.get("bind")
    if bind:
        try:
            c.host = binds.assert_bind(str(bind.get("host", "")), allowed_hosts)
        except binds.BindError as e:
            raise ManifestError(f"{name}: {e}") from e
        port = bind.get("port")
        if not isinstance(port, int) or not 1 <= port <= 65535:
            raise ManifestError(f"{name}: bind.port must be 1..65535")
        c.port = port
    health = raw.get("health")
    if health:
        if not c.host:
            raise ManifestError(f"{name}: health needs a bind")
        if health.get("kind") not in ("tcp", "http"):
            raise ManifestError(f"{name}: health.kind must be tcp or http")
        c.health_kind = health["kind"]
        c.health_path = str(health.get("path", "/"))
        c.health_timeout_s = float(health.get("timeout_s", 30))
    if raw.get("restart", "never") not in ("never", "on-failure"):
        raise ManifestError(f"{name}: restart must be never or on-failure")
    c.restart = raw.get("restart", "never")
    return c


def parse(data: dict) -> list[Component]:
    allowed = tuple(str(h) for h in data.get("allowed_hosts") or ())
    comps = [_component(r, allowed) for r in data.get("components") or []]
    names = [c.name for c in comps]
    if len(set(names)) != len(names):
        raise ManifestError("duplicate component names")
    for c in comps:
        for d in c.depends_on:
            if d not in names:
                raise ManifestError(f"{c.name}: depends_on unknown component {d!r}")
    order(comps)   # raises on a cycle
    return comps


def load(path: Path) -> list[Component]:
    return parse(json.loads(Path(path).read_text(encoding="utf-8")))


def order(comps: list[Component]) -> list[Component]:
    """Start order: dependencies first. Raises ManifestError on a cycle."""
    by = {c.name: c for c in comps}
    done: list[Component] = []
    state: dict[str, int] = {}

    def visit(c: Component) -> None:
        if state.get(c.name) == 2:
            return
        if state.get(c.name) == 1:
            raise ManifestError(f"dependency cycle at {c.name}")
        state[c.name] = 1
        for d in c.depends_on:
            visit(by[d])
        state[c.name] = 2
        done.append(c)

    for c in comps:
        visit(c)
    return done
