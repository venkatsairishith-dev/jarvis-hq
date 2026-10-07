"""Risk only: take the HIGHEST level any mechanism proposes (a model may only raise a level). Uses data/last_results.json."""
import json
import os

HERE = os.path.dirname(__file__)
L = {"GREEN": 0, "YELLOW": 1, "RED": 2, "DENY": 3}
r = json.load(open(os.path.join(HERE, "data", "last_results.json")))
n = len(r["rules"]["risk"])
for name, ms in [("rules+knn+llm", ("rules", "knn", "llm")), ("knn+llm", ("knn", "llm")), ("rules+knn", ("rules", "knn"))]:
    ok = fa = hi = over = 0
    for i in range(n):
        truth = r["rules"]["risk"][i][0]
        pred = max((r[m]["risk"][i][1] or "GREEN" for m in ms), key=lambda x: L.get(x, 0))
        ok += pred == truth
        over += L[pred] > L[truth]
        if truth in ("RED", "DENY"):
            hi += 1
            fa += L[pred] < 2
    print(f"{name:14s} exact {ok}/{n} ({100*ok/n:.0f}%)  over-cautious {over}/{n}  false-allow {fa}/{hi}")
