"""Read-only check of the bundled plugins' packaged metadata (plugin/plugins/*/plugin.meta.json). Any OS.

    python jarvis/scripts/check_plugin_metadata.py [--json] [plugin ...]

For each plugin: whether the host would accept its plugin.meta.json (the same reader the host uses), and if not, why:
missing file, old schema, file list differs (names), byte total differs, files listed that git ignores (so the snapshot
can only match on the machine that built it), and the machine that built it. Exit 1 if any snapshot would be refused.
It never writes anything; rebuilding is a laptop step (jarvis/scripts/rebuild-plugin-metadata.ps1), because the
snapshot records the build machine's environment (os/python/arch).
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from plugin.core.packaged_metadata import (  # noqa: E402
    PACKAGED_METADATA_FILENAME, PACKAGED_METADATA_SCHEMA_VERSION, build_environment, source_stat_summary)
from plugin.server.infrastructure.packaged_metadata import read_packaged_metadata  # noqa: E402


def _git_ignored(paths: list[Path]) -> set[str]:
    if not paths:
        return set()
    try:
        p = subprocess.run(["git", "-C", str(ROOT), "check-ignore", "--stdin"], input="\n".join(map(str, paths)),
                           capture_output=True, text=True, timeout=30)
        return {Path(x).as_posix() for x in p.stdout.splitlines() if x.strip()}
    except (OSError, subprocess.TimeoutExpired):
        return set()


def check(plugin_dir: Path) -> dict:
    meta = plugin_dir / PACKAGED_METADATA_FILENAME
    row = {"plugin": plugin_dir.name, "accepted": False, "problems": []}
    if not meta.is_file():
        row["problems"].append("no plugin.meta.json")
        return row
    try:
        raw = json.loads(meta.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        row["problems"].append(f"unreadable: {type(e).__name__}")
        return row
    row["built_on"] = raw.get("build_env")
    row["accepted"] = read_packaged_metadata(plugin_dir) is not None
    if raw.get("schema_version") != PACKAGED_METADATA_SCHEMA_VERSION:
        row["problems"].append(f"schema {raw.get('schema_version')} (current {PACKAGED_METADATA_SCHEMA_VERSION})")
    listed = set(raw.get("source_files") or [])
    summary = source_stat_summary(plugin_dir)
    on_disk = set(summary.names)
    if listed - on_disk:
        row["problems"].append(f"listed but not on disk: {sorted(listed - on_disk)[:8]}")
    if on_disk - listed:
        row["problems"].append(f"on disk but not listed: {sorted(on_disk - listed)[:8]}")
    if raw.get("source_bytes") != summary.total_bytes:
        row["problems"].append(f"bytes {raw.get('source_bytes')} listed, {summary.total_bytes} on disk")
    ignored = _git_ignored([plugin_dir / n for n in sorted(listed)])
    if ignored:
        rel = sorted(Path(x).relative_to(plugin_dir).as_posix() if Path(x).is_absolute() else x for x in ignored)
        row["problems"].append(f"lists git-ignored files (valid only on the build machine): {rel[:6]}")
    if row["accepted"] and not row["problems"]:
        row["problems"] = []
    elif row["accepted"]:
        row["note"] = "accepted here, but see problems"
    if not row["accepted"] and not row["problems"]:
        row["problems"].append("content digest differs (a source file changed after the build)")
    return row


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("plugins", nargs="*")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    dirs = sorted(p.parent for p in (ROOT / "plugin" / "plugins").glob("*/plugin.toml"))
    if a.plugins:
        dirs = [d for d in dirs if d.name in set(a.plugins)]
    rows = [check(d) for d in dirs]
    if a.json:
        print(json.dumps({"this_machine": build_environment(), "plugins": rows}, indent=1))
    else:
        print("this machine:", build_environment())
        for r in rows:
            status = "OK     " if r["accepted"] and not r["problems"] else ("WARN   " if r["accepted"] else "REFUSED")
            print(f"{status} {r['plugin']:24} built on {r.get('built_on')}")
            for p in r["problems"]:
                print(f"          - {p}")
    refused = [r["plugin"] for r in rows if not r["accepted"]]
    print(f"{len(refused)} refused: {', '.join(refused) or '-'}")
    return 1 if refused else 0


if __name__ == "__main__":
    sys.exit(main())
