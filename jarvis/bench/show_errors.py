"""Show misclassified items from the last run (use after: OOD=<file> python run_bench.py)."""
import json
import os
import sys

HERE = os.path.dirname(__file__)
task = sys.argv[1] if len(sys.argv) > 1 else "risk"
src = sys.argv[2] if len(sys.argv) > 2 else os.path.join(HERE, "data", "ood_items.jsonl")
r = json.load(open(os.path.join(HERE, "data", "last_results.json")))
items = [json.loads(l) for l in open(src, encoding="utf-8") if l.strip()]
rows = [i for i in items if i["task"] == task]
print(f"{task} mistakes (truth -> hybrid | knn | llm | rules)")
for idx, it in enumerate(rows):
    t = it["label"]
    h, k, l, ru = (r[m][task][idx][1] for m in ("hybrid", "knn", "llm", "rules"))
    if h != t:
        print(f"  {t} -> {h} | {k} | {l} | {ru} :: {it['text'][:120]}")
