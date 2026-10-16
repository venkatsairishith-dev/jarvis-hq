"""Score local decision models on the PC-task request set (2026-10-16). Windows, neko-venv; llama-swap on 127.0.0.1:8080.

    python jarvis\\evals\\pc_control\\eval_decision.py --models decider-ling,decider-qwen3-1.7b,decider-qwen3-4b [--repeat 1]

For each model, one at a time (llama-swap swaps them on the 4 GB GPU): every case in decision_cases.json goes through
plugin/plugins/desktop/decide.py (the real prompt, schema and deterministic resolution) and is scored against its
expectation. Also measured: the first (cold) call incl. model load, warm latency p50/p95, JSON validity, and the
llama-server process memory. Results: jarvis/evals/pc_control/results/decision-<time>.json.
"""
from __future__ import annotations

import argparse
import json
import statistics
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[2]))
from plugin.plugins.desktop import core, decide  # noqa: E402


def score(plan: decide.Plan, exp: dict) -> tuple[bool, str]:
    first = plan.steps[0] if plan.steps else {}
    tools = [s["tool"] for s in plan.steps]
    if "status" in exp and plan.status != exp["status"]:
        return False, f"status {plan.status} != {exp['status']}"
    if "status_in" in exp and plan.status not in exp["status_in"]:
        return False, f"status {plan.status}"
    if exp.get("status") == "clarify":
        return True, ""
    if plan.status != "ok" and not exp.get("status_in"):
        return False, f"status {plan.status}: {plan.question or plan.problems}"
    if "tool" in exp and first.get("tool") != exp["tool"]:
        return False, f"tool {first.get('tool')} != {exp['tool']}"
    if "tools" in exp and tools[:len(exp["tools"])] != exp["tools"]:
        return False, f"tools {tools}"
    if "tool_in" in exp and first.get("tool") not in exp["tool_in"] and plan.status != "clarify":
        return False, f"tool {first.get('tool')} not in {exp['tool_in']}"
    if "profile" in exp and not any(s.get("profile") == exp["profile"] for s in plan.steps):
        return False, f"profile {[s.get('profile') for s in plan.steps]} != {exp['profile']}"
    if "host" in exp and not any(exp["host"] in str(s.get("url", "")) for s in plan.steps):
        return False, f"url {[s.get('url') for s in plan.steps]}"
    for k in ("app", "query", "target", "text", "name"):
        if k in exp and not any(exp[k].lower() in str(s.get(k, "")).lower().replace("calculator", "calc") for s in plan.steps):
            return False, f"{k} {[s.get(k) for s in plan.steps]}"
    for k in ("key", "deliverable", "action", "check"):
        if k in exp and not any(str(s.get(k, "")).lower() == exp[k] for s in plan.steps):
            return False, f"{k} {[s.get(k) for s in plan.steps]}"
    return True, ""


def server_memory() -> dict:
    ps = ("Get-Process llama-server -ErrorAction SilentlyContinue | ForEach-Object { $_.WorkingSet64 } ; "
          "(Get-Counter '\\GPU Process Memory(*)\\Dedicated Usage' -ErrorAction SilentlyContinue).CounterSamples | "
          "Sort-Object CookedValue -Descending | Select-Object -First 1 -ExpandProperty CookedValue")
    out = subprocess.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True, text=True).stdout.split()
    nums = [float(x) for x in out if x.replace(".", "").isdigit()]
    return {"ram_mb": round(nums[0] / 2**20) if nums else None, "top_gpu_dedicated_mb": round(nums[-1] / 2**20) if len(nums) > 1 else None}


def run_model(model: str, cases: list, profiles: list, repeat: int) -> dict:
    t0 = time.time()
    first = decide.decide("open notepad", profiles, model=model, timeout=300)
    cold = time.time() - t0
    rows, lat = [], []
    for rep in range(repeat):
        for c in cases:
            p = decide.decide(c["req"], profiles, model=model, timeout=120)
            ok, why = score(p, c["expect"])
            if p.model != "rule":
                lat.append(p.seconds)
            rows.append({"id": c["id"], "rep": rep, "ok": ok, "why": why, "status": p.status,
                         "steps": p.steps, "seconds": p.seconds, "json_ok": p.status != "fallback" or "JSON" not in " ".join(p.problems)})
    mem = server_memory()
    lat.sort()
    n_ok = sum(r["ok"] for r in rows)
    return {"model": model, "cases": len(cases), "repeat": repeat, "correct": n_ok, "total": len(rows),
            "accuracy": round(n_ok / len(rows), 3), "json_valid": sum(r["json_ok"] for r in rows),
            "cold_first_call_s": round(cold, 1), "first_call_status": first.status,
            "p50_s": round(statistics.median(lat), 2) if lat else None,
            "p95_s": round(lat[int(0.95 * (len(lat) - 1))], 2) if lat else None, "memory": mem,
            "failures": [{k: r[k] for k in ("id", "why", "steps")} for r in rows if not r["ok"]]}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--models", default="decider-ling,decider-qwen3-1.7b,decider-qwen3-4b")
    ap.add_argument("--repeat", type=int, default=1)
    ap.add_argument("--only", default="")
    a = ap.parse_args()
    cases = json.loads((HERE / "decision_cases.json").read_text(encoding="utf-8"))
    if a.only:
        cases = [c for c in cases if c["id"] in a.only.split(",")]
    profiles = core.profiles()
    out = {"time": time.strftime("%Y-%m-%d %H:%M:%S"), "profiles": len(profiles), "models": []}
    for m in a.models.split(","):
        r = run_model(m.strip(), cases, profiles, a.repeat)
        out["models"].append(r)
        print(json.dumps({k: v for k, v in r.items() if k != "failures"}), flush=True)
        for f in r["failures"]:
            print("   FAIL", f["id"], f["why"][:120], flush=True)
    res = HERE / "results"
    res.mkdir(exist_ok=True)
    p = res / f"decision-{time.strftime('%Y%m%d-%H%M%S')}.json"
    p.write_text(json.dumps(out, indent=1), encoding="utf-8")
    print("saved", p)


if __name__ == "__main__":
    main()
