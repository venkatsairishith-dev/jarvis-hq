"""What was there before a caretaker change: jarvis/caretaker/history.jsonl, one JSON object per line.

    {"time": "2026-10-17T21:30:00Z", "tool": "<registry name>", "old_pin": "...", "new_pin": "...",
     "pr_url": "https://github.com/.../pull/N" or "pending", "finding_id": "cf-..."}

A lab sub-agent appends the line on its caretaker branch BEFORE it changes anything, so the old pin is in git next to
the change (and in the pull request body); after opening the pull request it records the URL with set-pr, which
appends a second line for the same finding (the file is append-only; the newest line for a finding wins).

    python -m jarvis.caretaker.history add --tool NAME --old OLD --new NEW --finding cf-... [--pr URL|pending]
    python -m jarvis.caretaker.history set-pr --finding cf-... --pr URL
    python -m jarvis.caretaker.history show [--finding cf-...]
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import sys
from pathlib import Path

HISTORY = Path(__file__).resolve().parent / "history.jsonl"
FID = re.compile(r"^cf-[0-9a-f]{20}$")
PR = re.compile(r"^(pending|https://github\.com/[\w.-]+/[\w.-]+/pull/\d+)$")
FIELDS = ("time", "tool", "old_pin", "new_pin", "pr_url", "finding_id")


class HistoryError(ValueError):
    pass


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def check(rec: dict) -> dict:
    if set(rec) != set(FIELDS):
        raise HistoryError(f"a history line has exactly the fields {', '.join(FIELDS)}")
    if not FID.match(str(rec["finding_id"])):
        raise HistoryError("finding_id must look like cf- and 20 hex digits")
    if not PR.match(str(rec["pr_url"])):
        raise HistoryError("pr_url must be 'pending' or a GitHub pull request URL")
    for k in ("tool", "old_pin", "new_pin"):
        if not str(rec[k]).strip() or len(str(rec[k])) > 300 or "\n" in str(rec[k]):
            raise HistoryError(f"{k} must be one line of 1 to 300 characters")
    return rec


def read(path=HISTORY) -> list:
    path = Path(path)
    if not path.exists():
        return []
    out = []
    for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if line.strip():
            try:
                out.append(check(json.loads(line)))
            except (ValueError, HistoryError) as e:
                raise HistoryError(f"{path.name}:{n}: {e}") from None
    return out


def record(tool, old_pin, new_pin, finding_id, pr_url="pending", path=HISTORY, time=None) -> dict:
    rec = check({"time": time or _now(), "tool": str(tool), "old_pin": str(old_pin), "new_pin": str(new_pin),
                 "pr_url": str(pr_url), "finding_id": str(finding_id)})
    read(path)                                     # never append to a damaged file
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, sort_keys=True) + "\n")
        f.flush()
        os.fsync(f.fileno())
    return rec


def set_pr(finding_id, pr_url, path=HISTORY, time=None) -> dict:
    prev = [r for r in read(path) if r["finding_id"] == finding_id]
    if not prev:
        raise HistoryError(f"no history line for {finding_id}: record the old pin first (add)")
    last = prev[-1]
    return record(last["tool"], last["old_pin"], last["new_pin"], finding_id, pr_url, path, time)


def latest(path=HISTORY) -> dict:
    """{finding_id: newest line}."""
    out = {}
    for r in read(path):
        out[r["finding_id"]] = r
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="caretaker history (what was there before a change)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("add")
    a.add_argument("--tool", required=True); a.add_argument("--old", required=True); a.add_argument("--new", required=True)
    a.add_argument("--finding", required=True); a.add_argument("--pr", default="pending")
    s = sub.add_parser("set-pr"); s.add_argument("--finding", required=True); s.add_argument("--pr", required=True)
    sh = sub.add_parser("show"); sh.add_argument("--finding")
    for p in (a, s, sh):
        p.add_argument("--file", default=str(HISTORY), help=argparse.SUPPRESS)
    x = ap.parse_args(argv)
    try:
        if x.cmd == "add":
            print(json.dumps(record(x.tool, x.old, x.new, x.finding, x.pr, x.file)))
        elif x.cmd == "set-pr":
            print(json.dumps(set_pr(x.finding, x.pr, x.file)))
        else:
            for r in read(x.file):
                if not x.finding or r["finding_id"] == x.finding:
                    print(json.dumps(r))
    except HistoryError as e:
        print(f"refused: {e}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
