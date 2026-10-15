"""Group the warnings and errors in the N.E.K.O logs by pattern, newest first. Windows, read-only.

    python log_digest.py [--since HH:MM] [--top 25] [--day YYYYMMDD]

Numbers, ids and quoted values are folded so repeats of the same problem count together. Useful after any change: run it, fix the
top entries that are ours, run it again.
"""
import argparse
import collections
import datetime
import glob
import os
import re

DATA = os.environ.get("NEKO_STORAGE_SELECTED_ROOT", r"D:\jarvis\N.E.K.O-data")
LINE = re.compile(r"^(\d{4}-\d{2}-\d{2}) (\d{2}:\d{2}:\d{2}) - (\S+) - (WARNING|ERROR|CRITICAL) - (.*)$")


def fold(msg: str) -> str:
    msg = re.sub(r"\b[0-9a-f]{8}-[0-9a-f-]{27}\b|\b[0-9a-f]{12,}\b", "<id>", msg)
    msg = re.sub(r"0x[0-9a-fA-F]+", "<hex>", msg)
    msg = re.sub(r"\d+(?:\.\d+)?", "<n>", msg)
    msg = re.sub(r"'[^']{0,60}'|\"[^\"]{0,60}\"", "<q>", msg)
    return msg[:160]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", default="00:00")
    ap.add_argument("--top", type=int, default=25)
    ap.add_argument("--day", default=datetime.date.today().strftime("%Y%m%d"))
    a = ap.parse_args()
    day = f"{a.day[:4]}-{a.day[4:6]}-{a.day[6:]}"
    counts = collections.Counter()
    first, last, example = {}, {}, {}
    for path in glob.glob(os.path.join(DATA, "logs", "**", "*.log"), recursive=True):
        if a.day not in os.path.basename(path) and not path.endswith(("launcher.out.log", "launcher.err.log")):
            continue
        comp = os.path.basename(path).replace(".log", "")
        try:
            lines = open(path, encoding="utf-8", errors="replace").read().splitlines()
        except OSError:
            continue
        for ln in lines:
            m = LINE.match(ln)
            if not m or m.group(1) != day or m.group(2)[:5] < a.since:
                continue
            key = (m.group(4), comp[:34], fold(m.group(5)))
            counts[key] += 1
            first.setdefault(key, m.group(2))
            last[key] = m.group(2)
            example.setdefault(key, m.group(5)[:200])
    print(f"{sum(counts.values())} warnings/errors, {len(counts)} distinct, {day} since {a.since}")
    for (level, comp, msg), n in counts.most_common(a.top):
        k = (level, comp, msg)
        print(f"{n:5d} {level[:4]} {first[k]}-{last[k]} [{comp}] {msg}".encode("ascii", "backslashreplace").decode()[:230])


if __name__ == "__main__":
    main()
