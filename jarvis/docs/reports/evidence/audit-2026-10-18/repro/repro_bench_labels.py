"""Audit repro (read-only): how the console's Bench screen labels the evidence behind the G (voice) and L (UX) cases."""
import json, sys
from collections import Counter
sys.path.insert(0, "/src/jarvis/console")
from jarvis_console.bench import Bench
for f in ("g_voice", "l_ux"):
    cases = json.load(open(f"/src/jarvis/evals/neko95/bench/cases/{f}.json"))
    print(f, dict(Counter(Bench.evidence_of(c) for c in cases)))
    for c in cases:
        if c["check"] == "none" or c["id"] in ("G-002", "G-003", "G-004"):
            print("  ", c["id"], Bench.evidence_of(c), "| check:", c["check"], "|", c["title"][:60])
