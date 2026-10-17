"""Claim-checker evaluation: plugin/plugins/research/verify.supported on labelled (passage, claim) pairs.

    RESEARCH_NLI_DIR=<model dir> python jarvis/evals/neko95/bench/claims_eval.py [--set claims_v2] [--model xsmall] [--json]

Reports precision, recall, false-acceptance rate (unsupported claims confirmed: the serious error), false-rejection rate
(supported claims left unconfirmed), per-category errors, and a calibration table (checker score band -> share of
claims in the band that are really supported), seconds per claim (model loaded beforehand), peak RSS and model size.
Sets: plugin/plugins/research/tests/fixtures/<name>.json. --model picks the checker (verify.BACKENDS, as env
RESEARCH_NLI_MODEL); the run stops if that checker's files are missing instead of measuring the fallback.
Results: results/claims-<set>-<model>-<time>.json (key "summary"/"pairs").
"""
from __future__ import annotations

import argparse
import json
import os
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
            "calibration": calib, "seconds": round(secs, 1), "seconds_per_claim": round(secs / len(rows), 4) if rows else None,
            "false_accepts": [r["claim"] for r in rows if r["confirmed"] and r["label"] == "unsupported"],
            "false_rejects": [r["claim"] for r in rows if not r["confirmed"] and r["label"] == "supported"]}, rows


def peak_rss_mb():
    """Peak resident memory of this process so far (Linux and macOS; None on Windows)."""
    try:
        import resource
    except ImportError:
        return None
    kb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return round(kb / (1e6 if sys.platform == "darwin" else 1e3), 1)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--set", default="claims_v2")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--no-save", action="store_true")
    ap.add_argument("--model", help=f"claim checker: {', '.join(verify.BACKENDS)} (default: env RESEARCH_NLI_MODEL, else {verify.DEFAULT_MODEL})")
    a = ap.parse_args(argv)
    if a.model:
        os.environ["RESEARCH_NLI_MODEL"] = a.model
    b = verify.backend()
    if a.model and b.name != a.model or not verify.available():
        raise SystemExit(f"claim-checker model files not found for {a.model or b.name} (RESEARCH_NLI_DIR)")
    t0 = time.perf_counter()
    verify.entailment("The sky is blue.", "The sky is blue.")             # load the model outside the timed part
    load_secs = time.perf_counter() - t0
    passages, pairs = load(a.set)
    summary, rows = evaluate(passages, pairs)
    onnx = os.path.join(verify.model_dir(b), b.onnx)
    summary = {"model": b.name, **summary, "load_seconds": round(load_secs, 1), "model_mb": round(os.path.getsize(onnx) / 1e6, 1),
               "peak_rss_mb": peak_rss_mb()}
    print(json.dumps(summary, indent=1, ensure_ascii=False))
    if not a.no_save:
        out = HERE.parent / "results" / f"claims-{a.set}-{b.name}-{time.strftime('%Y%m%d-%H%M%S')}.json"
        out.write_text(json.dumps({"time": time.strftime("%Y-%m-%d %H:%M:%S"), "set": a.set, "model_dir": verify.model_dir(b),
                                   "summary": summary, "pairs": rows}, indent=1, ensure_ascii=False), encoding="utf-8")
        print("saved", out)


if __name__ == "__main__":
    main()
