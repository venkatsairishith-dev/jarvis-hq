"""Licence gate for jarvis/tools/registry.json (plan P1 step 2).

An entry that is marked "shipped": true must carry a machine-readable SPDX id ("licence_spdx"), a "licence_file" and a
"notice_file". In the "shared" profile it must also not carry a forbidden SPDX id (strong copyleft, network copyleft,
source-available or non-commercial). The "personal" profile only checks that the fields exist.

Entries without "shipped" are not gated; --report lists them with their free-text licence so the unresolved ones stay visible.

    python jarvis/tools/licence_gate.py [--profile personal|shared] [--registry PATH] [--report]
Exit code 0 = pass, 1 = at least one violation.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
DEFAULT_REGISTRY = HERE / "registry.json"

# SPDX ids (base ids, version suffixes and -only/-or-later variants are matched by prefix) not allowed in a shared build.
FORBIDDEN_PREFIXES = ("GPL-", "AGPL-", "LGPL-", "SSPL-", "BUSL-", "CC-BY-NC", "Elastic-", "Commons-Clause", "Proprietary")
SPDX_TOKEN = re.compile(r"^[A-Za-z0-9.+-]+$")
EMPTY = {"", "unknown", "none", "n/a"}


def _spdx_ids(expr: str) -> list[str]:
    """Split a simple SPDX expression (AND / OR / WITH, parentheses) into its ids."""
    parts = re.split(r"\s+(?:AND|OR|WITH)\s+|[()]", expr)
    return [p.strip() for p in parts if p.strip()]


def check_entry(entry: dict, profile: str) -> list[str]:
    """Return the violations of one entry (empty when it passes or is not shipped)."""
    if not entry.get("shipped"):
        return []
    name = entry.get("name", "<unnamed>")
    problems: list[str] = []
    spdx = str(entry.get("licence_spdx", "")).strip()
    if spdx.lower() in EMPTY:
        problems.append(f"{name}: shipped but no licence_spdx")
    else:
        for ident in _spdx_ids(spdx):
            if ident in {"WITH"} or not SPDX_TOKEN.match(ident):
                problems.append(f"{name}: licence_spdx has an unreadable token {ident!r}")
            elif profile == "shared" and ident.startswith(FORBIDDEN_PREFIXES):
                problems.append(f"{name}: {ident} is not allowed in the shared profile")
    for key in ("licence_file", "notice_file"):
        if not str(entry.get(key, "")).strip():
            problems.append(f"{name}: shipped but no {key}")
    return problems


def check_registry(entries: list[dict], profile: str = "personal") -> list[str]:
    out: list[str] = []
    for e in entries:
        out.extend(check_entry(e, profile))
    return out


def report(entries: list[dict]) -> list[str]:
    """Lines for the entries that are not shipped-and-resolved: unknown or free-text licences."""
    lines = []
    for e in entries:
        lic = str(e.get("licence", "")).strip()
        if e.get("shipped"):
            continue
        if lic.lower() in EMPTY or lic.lower().startswith("unknown"):
            lines.append(f"unresolved: {e.get('name')} -- licence {lic or '<none>'}")
    return lines


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--profile", choices=("personal", "shared"), default="personal")
    ap.add_argument("--registry", type=Path, default=DEFAULT_REGISTRY)
    ap.add_argument("--report", action="store_true", help="also list entries whose licence is unknown")
    args = ap.parse_args(argv)
    entries = json.loads(args.registry.read_text(encoding="utf-8"))
    problems = check_registry(entries, args.profile)
    for p in problems:
        print("FAIL:", p)
    if args.report:
        for line in report(entries):
            print(line)
    shipped = sum(1 for e in entries if e.get("shipped"))
    print(f"licence gate ({args.profile}): {len(entries)} entries, {shipped} shipped, {len(problems)} violation(s)")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
