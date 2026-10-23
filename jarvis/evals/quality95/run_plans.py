"""JARVIS 9.5, C3 command understanding: plan-shape cases (plan_cases.json) plus the two measured misses (A07, H19),
each repeated, through the real local decision model. Windows, agent-venv; llama-swap on 127.0.0.1:8080.
    python jarvis\\evals\\quality95\\run_plans.py [--model decider] [--repeat 5]
A case id passes only if every variant passed in every repeat; repeat_rate is reported per id (rules v1: below 0.95
caps the capability at 8.5). Also writes the plan latency rows (C3 SLO and C11 L-plan-p50).
Results: jarvis/evals/quality95/results/plans-<time>.json
"""
import argparse
import json
import statistics
import sys
import time
from collections import defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[2]))
sys.path.insert(0, str(HERE.parent / "pc_control"))
from jarvis.plugins.desktop import core, decide  # noqa: E402
import eval_decision  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="decider")
    ap.add_argument("--repeat", type=int, default=5)
    a = ap.parse_args()
    cases = json.loads((HERE / "plan_cases.json").read_text(encoding="utf-8"))
    pc = HERE.parent / "pc_control"
    for setname, cid in (("decision_cases.json", "A07"), ("decision_cases_holdout.json", "H19")):
        cases += [c for c in json.loads((pc / setname).read_text(encoding="utf-8")) if c["id"] == cid]
    profiles = core.profiles()
    decide.decide("open notepad", profiles, model=a.model, timeout=300)          # load the model; not timed
    runs, lat = defaultdict(list), []
    for rep in range(a.repeat):
        for c in cases:
            p = decide.decide(c["req"], profiles, model=a.model, timeout=120, context=c.get("context", ""))
            ok, why = eval_decision.score(p, c["expect"])
            if p.model != "rule":
                lat.append(p.seconds)
            runs[c["id"]].append({"req": c["req"], "rep": rep, "ok": ok, "why": why, "status": p.status, "seconds": p.seconds,
                                  "tools": [s["tool"] for s in p.steps]})
            print(("PASS " if ok else "FAIL ") + c["id"], c["req"][:50], why[:100], flush=True)
    rows = []
    for cid, rs in runs.items():
        rate = sum(r["ok"] for r in rs) / len(rs)
        fails = [r for r in rs if not r["ok"]]
        rows.append({"cap": "C3_command_understanding", "id": cid, "status": "pass" if rate == 1.0 else "fail",
                     "repeat_rate": round(rate, 3), "runs": len(rs),
                     "detail": f"{len(rs) - len(fails)}/{len(rs)} runs" + (f"; e.g. {fails[0]['req']!r}: {fails[0]['why']}" if fails else "")})
    lat.sort()
    p50 = round(statistics.median(lat), 2) if lat else None
    p95 = round(lat[int(0.95 * (len(lat) - 1))], 2) if lat else None
    rules = json.loads((HERE / "capabilities.json").read_text(encoding="utf-8"))["capabilities"]
    slo = rules["C3_command_understanding"]["slo"]["plan_p50_s"]
    for cap, cid in (("C3_command_understanding", "C3-plan-latency"), ("C11_performance", "L-plan-p50")):
        rows.append({"cap": cap, "id": cid, "status": "pass" if p50 is not None and p50 <= slo else "fail", "slo": True,
                     "detail": f"model {a.model}: p50 {p50} s, p95 {p95} s over {len(lat)} warm calls (SLO p50 <= {slo} s)"})
    out = HERE / "results" / f"plans-{time.strftime('%Y%m%d-%H%M%S')}.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps({"time": time.strftime("%Y-%m-%d %H:%M:%S"), "model": a.model, "repeat": a.repeat,
                               "prompt_version": decide.PROMPT_VERSION, "rows": rows, "runs": runs}, indent=1), encoding="utf-8")
    for r in rows:
        print(f"{r['id']:28} {r['status']:5} {r['detail'][:110]}")
    print("saved", out)


if __name__ == "__main__":
    main()
