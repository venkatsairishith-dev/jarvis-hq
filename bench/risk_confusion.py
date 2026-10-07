"""Risk only: list items where a mechanism predicted a LOWER level than the truth, and the confusion counts.
Usage: python risk_confusion.py [mechanism] [items.jsonl]   (reads data/last_results.json)"""
import json
import os
import sys
from collections import Counter

HERE = os.path.dirname(__file__)
mech = sys.argv[1] if len(sys.argv) > 1 else "hybrid"
src = sys.argv[2] if len(sys.argv) > 2 else os.path.join(HERE, "data", "risk_fresh.jsonl")
r = json.load(open(os.path.join(HERE, "data", "last_results.json")))
items = [json.loads(l) for l in open(src, encoding="utf-8") if l.strip()]
L = {"GREEN": 0, "YELLOW": 1, "RED": 2, "DENY": 3}
under = over = 0
print(f"{mech}: items predicted LOWER than the truth")
for i, it in enumerate(items):
    t, h = it["label"], r[mech]["risk"][i][1]
    if L.get(h, -1) < L[t]:
        under += 1
        print(f"  {t:6s} -> {str(h):6s} :: {it['text'][:105]}")
    elif L.get(h, -1) > L[t]:
        over += 1
print("over-cautious:", over, "| under:", under)
print("confusion (truth, predicted):", dict(Counter((it["label"], r[mech]["risk"][i][1]) for i, it in enumerate(items) if r[mech]["risk"][i][1] != it["label"])))
