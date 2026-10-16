"""Neko capability benchmark v2 (2026-10-16): versioned cases run against the real code, one recorded result per case.

    python jarvis/evals/neko95/bench/bench.py [--area K] [--id K-001] [--repeat 1] [--list]

Cases live in cases/<area>.json; each names a check in adapters/<area>.py that drives the REAL module (kernel policy,
stores, plugins, browser with local fixture pages, ...) and returns (passed, observed). Dangerous or external side
effects are replaced by fixtures and mocks inside the adapters; nothing here sends, buys, deletes user data or touches
an account.

A case whose platform is "windows" or "owner_hardware" is never run here and is recorded as DEFERRED, with the reason.
A check that raises is recorded as ERROR (counts as failed, never as passed). Nondeterministic cases may ask for
repeats; every repeat must pass.

Results: results/bench-<time>.json  {"bench_version", "time", "host", "cases": [...]}. The key is "cases", not "rows",
so the frozen v1 scorer (score.py, capabilities.json) never reads these. scorecard.py turns them into the v2 scorecard.
"""
from __future__ import annotations

import argparse
import importlib
import json
import platform
import sys
import time
import traceback
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]
RESULTS = HERE.parent / "results"
BENCH_VERSION = 2
AREAS = {
    "A": "Intent understanding and planning", "B": "Desktop automation", "C": "Browser automation",
    "D": "Long-horizon tasks", "E": "Error diagnosis and self-repair", "F": "Memory and personalization",
    "G": "Voice pipeline (offline parts)", "H": "Research and factual reliability", "I": "Documents and presentations",
    "J": "Provider routing and resources", "K": "Security and permissions", "L": "UI and user experience",
    "M": "Skill acquisition and promotion", "N": "Reliability under adverse conditions", "X": "Context selection",
}
RISKS = ("critical", "high", "medium", "low")
PLATFORMS = {"any": None, "windows": "needs the Windows laptop", "owner_hardware": "DEFERRED — OWNER HARDWARE REQUIRED",
             "logged_in_browser": "needs the owner's logged-in Chrome profiles"}
REQUIRED = ("id", "area", "title", "scenario", "expected", "risk", "check", "dims")

for p in (ROOT, ROOT / "jarvis" / "kernel", ROOT / "jarvis" / "node"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))


def load_cases(area: str | None = None) -> list[dict]:
    cases, seen = [], set()
    for f in sorted((HERE / "cases").glob("*.json")):
        for c in json.loads(f.read_text(encoding="utf-8")):
            missing = [k for k in REQUIRED if k not in c]
            if missing:
                raise ValueError(f"{f.name}: case {c.get('id')} lacks {missing}")
            if c["id"] in seen:
                raise ValueError(f"duplicate case id {c['id']}")
            if c["risk"] not in RISKS or c["area"] not in AREAS or c.get("platform", "any") not in PLATFORMS:
                raise ValueError(f"{c['id']}: bad risk, area or platform")
            seen.add(c["id"])
            if area is None or c["area"] == area:
                cases.append(c)
    return cases


SPLIT = "{{SPLIT}}"      # synthetic secrets are stored split by this marker so secret scanners never see a whole one


def _unfold(x):
    if isinstance(x, str):
        return x.replace(SPLIT, "")
    if isinstance(x, list):
        return [_unfold(v) for v in x]
    if isinstance(x, dict):
        return {_unfold(k): _unfold(v) for k, v in x.items()}
    return x


def run_case(c: dict, repeat: int = 1) -> dict:
    row = {k: c[k] for k in ("id", "area", "title", "risk", "dims")}
    reason = PLATFORMS[c.get("platform", "any")]
    if reason:
        return {**row, "status": "deferred", "observed": reason, "ms": 0}
    try:
        mod = importlib.import_module(f"adapters.{c['area'].lower()}")
        fn = getattr(mod, c["check"])
    except Exception as e:  # noqa: BLE001 - a missing check is an error, not a skip
        return {**row, "status": "error", "observed": f"no check {c['check']}: {type(e).__name__}: {e}"[:400], "ms": 0}
    n = max(1, int(c.get("repeat", repeat)))
    t0, observed, ok = time.perf_counter(), "", True
    for i in range(n):
        try:
            passed, observed = fn(_unfold(c.get("args") or {}))
        except Exception as e:  # noqa: BLE001
            tb = traceback.format_exc(limit=3).splitlines()[-1]
            return {**row, "status": "error", "observed": f"run {i + 1}/{n}: {type(e).__name__}: {e} | {tb}"[:500],
                    "ms": round((time.perf_counter() - t0) * 1000)}
        if not passed:
            ok = False
            observed = f"run {i + 1}/{n}: {observed}"
            break
    return {**row, "status": "pass" if ok else "fail", "observed": str(observed)[:500], "repeats": n,
            "ms": round((time.perf_counter() - t0) * 1000)}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--area")
    ap.add_argument("--id")
    ap.add_argument("--repeat", type=int, default=1)
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--no-save", action="store_true")
    a = ap.parse_args(argv)
    sys.path.insert(0, str(HERE))
    cases = [c for c in load_cases(a.area) if not a.id or c["id"] == a.id]
    if a.list:
        for c in cases:
            print(c["id"], c["risk"], c.get("platform", "any"), c["title"])
        print(len(cases), "cases")
        return 0
    rows = []
    for c in cases:
        r = run_case(c, a.repeat)
        rows.append(r)
        if r["status"] not in ("pass", "deferred"):
            print(f"{r['status'].upper():5} {r['id']} {r['title']}: {r['observed'][:200]}", flush=True)
    counts = {s: sum(r["status"] == s for r in rows) for s in ("pass", "fail", "error", "deferred")}
    print(json.dumps(counts))
    if not a.no_save:
        RESULTS.mkdir(exist_ok=True)
        out = RESULTS / f"bench-{time.strftime('%Y%m%d-%H%M%S')}{'-' + a.area if a.area else ''}.json"
        out.write_text(json.dumps({"bench_version": BENCH_VERSION, "time": time.strftime("%Y-%m-%d %H:%M:%S"),
                                   "host": f"{platform.system()} {platform.machine()} py{platform.python_version()}",
                                   "selection": {"area": a.area, "id": a.id}, "counts": counts, "cases": rows},
                                  indent=1, ensure_ascii=False), encoding="utf-8")
        print("saved", out)
    return 0 if counts["fail"] == counts["error"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
