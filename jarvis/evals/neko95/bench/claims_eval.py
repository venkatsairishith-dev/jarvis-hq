"""Claim-checker evaluation: plugin/plugins/research/verify.supported on labelled (passage, claim) pairs.

    RESEARCH_NLI_DIR=<model dir> python jarvis/evals/neko95/bench/claims_eval.py [--set claims_v2] [--json]

Reports precision, recall, false-acceptance rate (unsupported claims confirmed: the serious error), false-rejection rate
(supported claims left unconfirmed), per-category errors, and a calibration table (checker score band -> share of
claims in the band that are really supported). Sets: plugin/plugins/research/tests/fixtures/<name>.json.
Results: results/claims-<set>-<time>.json (key "summary"/"pairs").
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]
sys.path.insert(0, str(ROOT))
from plugin.plugins.research import verify  # noqa: E402

FIX = ROOT / "plugin/plugins/research/tests/fixtures"


def load(name: str):
    d = json.loads((FIX / f"{name}.json").read_text(encoding="utf-8"))
    pairs = []
    for p in d["pairs"]:
        if len(p) == 3:                       # nli_pairs.json: [passage, yes|no, claim]
            pid, lab, claim = p
            pairs.append((pid, "supported" if lab == "yes" else "unsupported", "-", claim))
        else:
            pairs.append(tuple(p))
    return d["passages"], pairs


def evaluate(passages, pairs):
    rows = []
    t0 = time.perf_counter()
    for pid, lab, cat, claim in pairs:
        ok, score = verify.supported(claim, [passages[pid]])
        rows.append({"passage": pid, "label": lab, "category": cat, "claim": claim, "confirmed": bool(ok), "score": round(score, 3)})
    secs = time.perf_counter() - t0
    tp = sum(r["confirmed"] and r["label"] == "supported" for r in rows)
    fp = sum(r["confirmed"] and r["label"] == "unsupported" for r in rows)
    fn = sum(not r["confirmed"] and r["label"] == "supported" for r in rows)
    tn = sum(not r["confirmed"] and r["label"] == "unsupported" for r in rows)
    pos, neg = tp + fn, fp + tn
    cats = defaultdict(Counter)
    for r in rows:
        wrong = r["confirmed"] != (r["label"] == "supported")
        cats[r["category"]]["n"] += 1
        cats[r["category"]]["wrong"] += wrong
    bands = [(0.0, 0.1), (0.1, 0.5), (0.5, 0.9), (0.9, 0.99), (0.99, 1.01)]
    calib = []
    for lo, hi in bands:
        inb = [r for r in rows if lo <= r["score"] < hi]
        if inb:
            calib.append({"score": f"{lo}-{min(hi, 1.0)}", "n": len(inb),
                          "really_supported": round(sum(r["label"] == "supported" for r in inb) / len(inb), 2)})
    return {"n": len(rows), "supported": pos, "unsupported": neg,
            "precision": round(tp / (tp + fp), 3) if tp + fp else None, "recall": round(tp / pos, 3) if pos else None,
            "false_acceptance": f"{fp}/{neg}", "false_acceptance_rate": round(fp / neg, 3) if neg else None,
            "false_rejection": f"{fn}/{pos}", "false_rejection_rate": round(fn / pos, 3) if pos else None,
            "errors_by_category": {k: f"{v['wrong']}/{v['n']}" for k, v in sorted(cats.items())},
            "calibration": calib, "seconds": round(secs, 1),
            "false_accepts": [r["claim"] for r in rows if r["confirmed"] and r["label"] == "unsupported"],
            "false_rejects": [r["claim"] for r in rows if not r["confirmed"] and r["label"] == "supported"]}, rows


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--set", default="claims_v2")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--no-save", action="store_true")
    a = ap.parse_args(argv)
    if not verify.available():
        raise SystemExit("claim-checker model files not found (RESEARCH_NLI_DIR)")
    passages, pairs = load(a.set)
    summary, rows = evaluate(passages, pairs)
    print(json.dumps(summary, indent=1, ensure_ascii=False))
    if not a.no_save:
        out = HERE.parent / "results" / f"claims-{a.set}-{time.strftime('%Y%m%d-%H%M%S')}.json"
        out.write_text(json.dumps({"time": time.strftime("%Y-%m-%d %H:%M:%S"), "set": a.set, "model_dir": verify.MODEL_DIR,
                                   "summary": summary, "pairs": rows}, indent=1, ensure_ascii=False), encoding="utf-8")
        print("saved", out)


if __name__ == "__main__":
    main()
