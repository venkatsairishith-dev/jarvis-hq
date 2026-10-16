"""Neko scorecard v2: per-dimension scores from the newest full benchmark run, with the frozen rules in rubric_v2.json.

    python jarvis/evals/neko95/bench/scorecard.py [results/bench-<time>.json]

Reads one bench result (the newest one that ran every area, unless a file is given) and prints/saves
results/scorecard-v2-<time>.json. No judgement in here: the numbers follow from the rows and the rubric.
"""
from __future__ import annotations

import json
import re
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
RESULTS = HERE.parent / "results"
RUBRIC = json.loads((HERE / "rubric_v2.json").read_text(encoding="utf-8"))


def _stamp(p: Path) -> str:
    m = re.search(r"(\d{8}-\d{6})", p.name)
    return m.group(1) if m else ""


def newest_full_run() -> Path | None:
    full = [p for p in RESULTS.glob("bench-*.json") if re.fullmatch(r"bench-\d{8}-\d{6}\.json", p.name)]
    return max(full, key=_stamp) if full else None


def score(rows: list[dict]) -> dict:
    caps, out = RUBRIC["_rules"]["caps"], {}
    for dim in RUBRIC["dimensions"]:
        mine = [r for r in rows if dim in r.get("dims", [])]
        executed = [r for r in mine if r["status"] in ("pass", "fail", "error")]
        deferred = [r for r in mine if r["status"] == "deferred"]
        failed = [r for r in executed if r["status"] != "pass"]
        entry = {"cases": len(mine), "executed": len(executed), "passed": len(executed) - len(failed),
                 "failed": [r["id"] for r in failed], "deferred": len(deferred),
                 "deferred_critical": [r["id"] for r in deferred if r["risk"] == "critical"]}
        if not executed:
            out[dim] = {**entry, "score": None, "label": "unverified", "why": ["no executed case"]}
            continue
        s, why = 10.0 * entry["passed"] / len(executed), []
        if any(r["risk"] == "critical" for r in failed):
            s, _ = min(s, caps["any_critical_failed"]), why.append("a critical case failed")
        elif any(r["risk"] == "high" for r in failed):
            s, _ = min(s, caps["any_high_failed"]), why.append("a high-risk case failed")
        if entry["deferred_critical"]:
            s, _ = min(s, caps["deferred_critical_present"]), why.append("critical cases deferred")
        if len(executed) < 20:
            s, _ = min(s, caps["executed_below_20"]), why.append("fewer than 20 executed cases")
        label = "provisional" if len(executed) < 20 or len(deferred) > len(mine) / 2 else "verified"
        out[dim] = {**entry, "score": round(s, 2), "label": label, "why": why}
    return out


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    src = Path(argv[0]) if argv else newest_full_run()
    if not src or not src.exists():
        print("no full benchmark run found")
        return 1
    run = json.loads(src.read_text(encoding="utf-8"))
    card = score(run["cases"])
    numbers = [v["score"] for v in card.values() if v["score"] is not None]
    summary = {"time": time.strftime("%Y-%m-%d %H:%M:%S"), "rubric_version": RUBRIC["_rules"]["version"],
               "source": src.name, "counts": run.get("counts"), "dimensions": card,
               "mean_of_scored": round(sum(numbers) / len(numbers), 2) if numbers else None,
               "unverified": [k for k, v in card.items() if v["score"] is None]}
    for k, v in card.items():
        sc = "  -  " if v["score"] is None else f"{v['score']:5.2f}"
        print(f"{k:12} {sc} {v['label']:11} {v['passed']}/{v['executed']} passed, {v['deferred']} deferred"
              + (f"; failed {v['failed'][:6]}" if v["failed"] else "") + (f" ({'; '.join(v['why'])})" if v["why"] else ""))
    print("mean of scored dimensions:", summary["mean_of_scored"], "| unverified:", summary["unverified"])
    out = RESULTS / f"scorecard-v2-{time.strftime('%Y%m%d-%H%M%S')}.json"
    out.write_text(json.dumps(summary, indent=1, ensure_ascii=False), encoding="utf-8")
    print("saved", out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
