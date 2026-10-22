"""Generate THIRD_PARTY_NOTICES.md from jarvis/tools/registry.json (plan P1 step 3).

One section per registry entry with "shipped": true: name, upstream_url, pinned_version, licence_spdx and the text of its
"licence_file" and "notice_file" (paths relative to --licenses-dir). Entries without shipped true are not included.
A file that does not exist is listed as "MISSING: <path>"; with --strict that makes the exit code 1.
--check regenerates in memory and exits 1 if the file on disk differs (stale notice file).

    python jarvis/tools/make_notices.py [--registry PATH] [--licenses-dir DIR] [--out PATH] [--strict] [--check]
Exit code 0 = ok, 1 = missing file under --strict, or stale file under --check.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
DEFAULT_REGISTRY = HERE / "registry.json"
DEFAULT_LICENSES = HERE.parent / "licenses"
DEFAULT_OUT = HERE.parent / "THIRD_PARTY_NOTICES.md"

HEADER = "# Third-party notices\n\nThis product includes the following third-party components.\n"


def _file_block(label: str, rel: str, base: Path, missing: list[str]) -> list[str]:
    rel = str(rel or "").strip()
    if not rel:
        return []
    path = base / rel
    if not path.is_file():
        missing.append(str(path))
        return [f"### {label}", "", f"MISSING: {path.as_posix()}", ""]
    text = path.read_text(encoding="utf-8").replace("\r\n", "\n").rstrip("\n")
    return [f"### {label}", "", "```text", text, "```", ""]


def render(entries: list[dict], licenses_dir: Path) -> tuple[str, list[str]]:
    """Return (markdown, missing file paths)."""
    missing: list[str] = []
    lines = [HEADER]
    for e in entries:
        if e.get("shipped") is not True:
            continue
        lines += [
            f"## {e.get('name', '<unnamed>')}",
            "",
            f"- Upstream: {e.get('upstream_url', '')}",
            f"- Version: {e.get('pinned_version', '')}",
            f"- Licence (SPDX): {e.get('licence_spdx', '')}",
            "",
        ]
        lines += _file_block("Licence", e.get("licence_file", ""), licenses_dir, missing)
        lines += _file_block("Notice", e.get("notice_file", ""), licenses_dir, missing)
    return "\n".join(lines).rstrip("\n") + "\n", missing


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--registry", type=Path, default=DEFAULT_REGISTRY)
    ap.add_argument("--licenses-dir", type=Path, default=DEFAULT_LICENSES)
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    ap.add_argument("--strict", action="store_true", help="exit 1 if a licence or notice file is missing")
    ap.add_argument("--check", action="store_true", help="exit 1 if --out differs from the regenerated text; writes nothing")
    args = ap.parse_args(argv)
    entries = json.loads(args.registry.read_text(encoding="utf-8"))
    text, missing = render(entries, args.licenses_dir)
    for m in missing:
        print("MISSING:", m)
    rc = 1 if (missing and args.strict) else 0
    if args.check:
        current = args.out.read_bytes().decode("utf-8").replace("\r\n", "\n") if args.out.is_file() else None
        if current != text:
            print(f"STALE: {args.out} is out of date; run make_notices.py to regenerate")
            return 1
        print(f"{args.out} is up to date")
        return rc
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_bytes(text.encode("utf-8"))
    shipped = sum(1 for e in entries if e.get("shipped") is True)
    print(f"wrote {args.out}: {shipped} shipped entries, {len(missing)} missing file(s)")
    return rc


if __name__ == "__main__":
    sys.exit(main())
