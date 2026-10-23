"""Context-selection benchmark: the old head cut vs the deterministic evidence selector vs trim-1 (three points).

    JARVIS_TRIM1_BIN=/path/to/trim python jarvis/evals/quality95/bench/context_bench.py [--budgets 4000,8000,16000] [--repeat 3]

Per fixture and mode: bytes kept, reduction, critical-line recall, answer-line kept, exact preservation (every kept line
is a verbatim input line, in order), latency (wall and CPU), errors. "Answer kept" is a deterministic proxy for
downstream accuracy (a reader cannot answer from a line it never sees); no model reads the result here.
Results: results/context-<time>.json
"""
from __future__ import annotations

import argparse
import json
import os
import resource
import statistics
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]
sys.path.insert(0, str(ROOT / "jarvis" / "kernel"))
sys.path.insert(0, str(HERE))
from jarvis_kernel import context_select as CS  # noqa: E402
from jarvis_kernel.sizing import estimate_tokens  # noqa: E402


def tokens(text: str) -> int:
    return estimate_tokens({"messages": [{"role": "tool", "content": text}]})
from context_fixtures import fixtures  # noqa: E402

MODES = ["off", "evidence", "trim1-conservative", "trim1-balanced", "trim1-aggressive",
         "native-conservative", "native-balanced", "native-aggressive"]   # native = trim-1 alone, no budget, no fallback


def preserved(orig: str, out: str) -> bool:
    lines, j = orig.split("\n"), 0
    for line in out.split("\n"):
        if line.startswith("[... ") and line.endswith(" lines omitted ...]"):
            continue
        if line.startswith("[truncated: ") or (line.startswith("… ") and (line.endswith(" lines …") or line.endswith(" line …"))):
            continue
        while j < len(lines) and lines[j] != line:
            j += 1
        if j == len(lines):
            # the head cut may end inside a line: allow a prefix of the next line as the very last kept line
            return False
        j += 1
    return True


def run(budgets, repeat, fx):
    have_trim = bool(os.environ.get(CS.ENV_TRIM1)) and os.path.isfile(os.environ.get(CS.ENV_TRIM1, ""))
    rows = []
    for budget in budgets:
        for mode in MODES:
            if (mode.startswith("trim1") or mode.startswith("native")) and not have_trim:
                continue
            for f in fx:
                walls, cpus, sel = [], [], None
                for _ in range(repeat):
                    c0, w0 = resource.getrusage(resource.RUSAGE_SELF), time.perf_counter()
                    ch0 = resource.getrusage(resource.RUSAGE_CHILDREN)
                    if mode.startswith("native-"):
                        txt, note = CS._trim1(f["text"], f["query"], mode.split("-", 1)[1], 5.0)
                        sel = CS.Selection(txt if txt is not None else f["text"], mode, txt is not None, 0, 0, [], "", note)
                    else:
                        sel = CS.select(f["text"], f["query"], budget, mode, timeout_s=5.0)
                    w1, c1, ch1 = time.perf_counter(), resource.getrusage(resource.RUSAGE_SELF), resource.getrusage(resource.RUSAGE_CHILDREN)
                    walls.append((w1 - w0) * 1000)
                    cpus.append(((c1.ru_utime + c1.ru_stime) - (c0.ru_utime + c0.ru_stime)
                                 + (ch1.ru_utime + ch1.ru_stime) - (ch0.ru_utime + ch0.ru_stime)) * 1000)
                out_lines = set(sel.text.split("\n"))
                crit = [c in out_lines for c in f["critical"]]
                rows.append({"budget": budget, "mode": mode, "mode_used": sel.mode, "id": f["id"], "kind": f["kind"],
                             "position": f["position"], "in_bytes": len(f["text"].encode()), "out_bytes": len(sel.text.encode()),
                             "in_tokens": tokens(f["text"]), "out_tokens": tokens(sel.text),
                             "fits": len(sel.text) <= budget, "critical_recall": sum(crit) / len(crit), "answer_kept": f["answer"] in out_lines,
                             "preserved": preserved(f["text"], sel.text), "ms": statistics.median(walls), "cpu_ms": statistics.median(cpus),
                             "note": sel.note})
    return rows


def summarize(rows):
    out = {}
    for key in sorted({(r["budget"], r["mode"]) for r in rows}):
        rs = [r for r in rows if (r["budget"], r["mode"]) == key and r["kind"] != "short"]
        shorts = [r for r in rows if (r["budget"], r["mode"]) == key and r["kind"] == "short"]
        red = [1 - r["out_bytes"] / r["in_bytes"] for r in rs]
        worst = sorted(rs, key=lambda r: (r["answer_kept"], r["critical_recall"]))[:3]
        out[f"{key[0]}:{key[1]}"] = {
            "n": len(rs), "tokens_in_mean": round(statistics.mean(r["in_tokens"] for r in rs)),
            "tokens_out_mean": round(statistics.mean(r["out_tokens"] for r in rs)), "reduction_mean": round(statistics.mean(red), 3), "reduction_min": round(min(red), 3),
            "answer_kept": f"{sum(r['answer_kept'] for r in rs)}/{len(rs)}",
            "critical_recall_mean": round(statistics.mean(r["critical_recall"] for r in rs), 3),
            "critical_all_kept": f"{sum(r['critical_recall'] == 1 for r in rs)}/{len(rs)}",
            "fits_budget": f"{sum(r['fits'] for r in rs)}/{len(rs)}",
            "preserved": f"{sum(r['preserved'] for r in rs)}/{len(rs)}",
            "short_untouched": f"{sum(r['out_bytes'] == r['in_bytes'] for r in shorts)}/{len(shorts)}",
            "ms_p50": round(statistics.median(r["ms"] for r in rs), 2), "ms_max": round(max(r["ms"] for r in rs), 2),
            "cpu_ms_p50": round(statistics.median(r["cpu_ms"] for r in rs), 2),
            "worst": [f"{w['id']} answer={w['answer_kept']} recall={w['critical_recall']:.2f}" for w in worst
                      if not (w["answer_kept"] and w["critical_recall"] == 1)],
        }
    return out


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--budgets", default="4000,8000,16000")
    ap.add_argument("--repeat", type=int, default=3)
    ap.add_argument("--set", choices=("dev", "holdout", "holdout2"), default="dev",
                    help="dev = context_fixtures.py (the selector was tuned on it); holdout = context_fixtures_holdout.json "
                         "(cases written by Dahl's model without seeing the selector: gen_holdout_dahl.py; never tuned on)")
    a = ap.parse_args(argv)
    if a.set in ("holdout", "holdout2"):
        name = "context_fixtures_holdout.json" if a.set == "holdout" else "context_fixtures_holdout2.json"
        fx = json.loads((HERE / name).read_text(encoding="utf-8"))
    else:
        fx = fixtures()
    rows = run([int(b) for b in a.budgets.split(",")], a.repeat, fx)
    summ = summarize(rows)
    for k, v in summ.items():
        print(k, json.dumps({x: v[x] for x in ("n", "tokens_in_mean", "tokens_out_mean", "reduction_mean", "answer_kept", "critical_all_kept", "fits_budget", "preserved",
                                              "short_untouched", "ms_p50", "cpu_ms_p50")}, ensure_ascii=False))
        if v["worst"]:
            print("   worst:", v["worst"])
    res = HERE.parent / "results"
    res.mkdir(exist_ok=True)
    out = res / f"context-{a.set}-{time.strftime('%Y%m%d-%H%M%S')}.json"
    out.write_text(json.dumps({"time": time.strftime("%Y-%m-%d %H:%M:%S"), "set": a.set, "fixtures": len(fx), "repeat": a.repeat,
                               "trim1_bin": bool(os.environ.get(CS.ENV_TRIM1)), "summary": summ, "detail": rows}, ensure_ascii=False),
                   encoding="utf-8")
    print("saved", out)


if __name__ == "__main__":
    main()
