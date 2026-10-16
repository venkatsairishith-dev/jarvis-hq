"""Neko 9.5 scorecard: per-capability scores computed from saved test results with the frozen rules in
capabilities.json (no judgement in here). Windows, neko-venv.
    python jarvis\\evals\\neko95\\score.py [--out jarvis\\evals\\neko95\\results\\score-<time>.json]
Sources:
  * the newest jarvis/evals/pc_control/results/suite-*.json (categories A-F, one row per case)
  * the newest results/coordinator-*.json (live requests through the kernel)
  * jarvis/evals/neko95/results/*.json written by the neko95 test scripts (rows {cap, id, status, critical?, detail})
  * evidence_manual.json: live results that predate saved files; each row names its source; kept small
A critical case with no result counts as NOT RUN (cap 5.0); a failed critical case caps at 6.0.
"""
import argparse
import glob
import json
import os
import statistics
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
PC = HERE.parent / "pc_control" / "results"
RULES = json.loads((HERE / "capabilities.json").read_text(encoding="utf-8"))
SUITE_CAT = {"A": "C3_command_understanding", "B": "C4_windows_control", "C": "C5_browser", "D": "C7_troubleshooting",
             "E": "C12_orchestration", "F": "C14_safety"}
E_ALIASES = {"E-lock": ["test_lock_one_task_at_a_time_and_stale_lock_taken_over"],
             "E-retry-transient-only": ["test_transient_failure_is_retried_once_only", "test_refusals_and_denials_are_not_retried"],
             "E-verification": ["test_completed_only_when_verified", "test_play_needs_the_chosen_video_playing"],
             "E-cancel": ["test_cancel_between_steps_and_time_budget", "test_a_cancel_reaches_the_running_job_at_the_next_poll",
                          "test_cancel_kills_the_running_process_and_the_job_says_cancelled"]}


def newest(pattern):
    files = sorted(glob.glob(str(pattern)), key=os.path.getmtime)
    return files[-1] if files else None


def rows_from_suite():
    f = newest(PC / "suite-*.json")
    rows = []
    if not f:
        return rows, None
    s = json.loads(Path(f).read_text(encoding="utf-8"))
    for r in s["rows"]:
        if r["status"] == "info" or r["cat"] not in SUITE_CAT:
            continue
        rows.append({"cap": SUITE_CAT[r["cat"]], "id": r["id"], "status": r["status"], "detail": r.get("detail", ""), "source": Path(f).name})
    # derived ids
    by_name = {r["id"].split(".")[-1]: r for r in rows if r["cap"] == "C12_orchestration"}
    for alias, names in E_ALIASES.items():
        hits = [by_name[n] for n in names if n in by_name]
        if hits:
            rows.append({"cap": "C12_orchestration", "id": alias, "status": "pass" if all(h["status"] == "pass" for h in hits) else "fail",
                         "source": Path(f).name, "derived": True})
    f_rows = [r for r in rows if r["cap"] == "C14_safety"]
    if f_rows:
        rows.append({"cap": "C14_safety", "id": "F-all", "status": "pass" if all(r["status"] == "pass" for r in f_rows) else "fail",
                     "source": Path(f).name, "derived": True})
    for cid in ("C11", "C13", "C19", "C17"):
        hit = next((r for r in rows if r["id"] == cid and r["cap"] == "C5_browser"), None)
        if hit and cid == "C17":
            rows.append({"cap": "C14_safety", "id": "S-injection-browser", "status": hit["status"], "source": hit["source"], "derived": True})
    return rows, f


def rows_from_live_runs(suite_file):
    """The newest live desktop (B) and diagnosis (D) runs when they are newer than the suite file: the same scripts the
    suite runs, saved on their own (one row per case)."""
    rows = []
    since = os.path.getmtime(suite_file) if suite_file else 0
    for pattern, cap in (("desktop-*.json", "C4_windows_control"), ("diagnosis-*.json", "C7_troubleshooting")):
        f = newest(PC / pattern)
        if not f or os.path.getmtime(f) <= since:
            continue
        for c in json.loads(Path(f).read_text(encoding="utf-8")).get("cases", []):
            rows.append({"cap": cap, "id": c["id"], "status": "pass" if c["ok"] else "fail", "detail": c.get("detail", ""),
                         "source": Path(f).name})
    return rows


def rows_from_coordinator():
    f = newest(PC / "coordinator-*.json")
    if not f:
        return []
    cases = json.loads(Path(f).read_text(encoding="utf-8"))["cases"]
    rows = [{"cap": "C12_orchestration", "id": c["id"], "status": "pass" if c["ok"] else "fail", "source": Path(f).name} for c in cases]
    rows.append({"cap": "C12_orchestration", "id": "G-live-12", "status": "pass" if all(c["ok"] for c in cases) and len(cases) >= 12 else "fail",
                 "source": Path(f).name, "derived": True})
    g03 = next((c for c in cases if c["id"] == "G03"), None)
    if g03:
        rows.append({"cap": "C4_windows_control", "id": "G03-typed-readback", "status": "pass" if g03["ok"] else "fail", "source": Path(f).name})
    return rows


def rows_from_files():
    rows = []
    for f in sorted(glob.glob(str(HERE / "results" / "*.json"))):
        if Path(f).name.startswith("score-"):
            continue
        d = json.loads(Path(f).read_text(encoding="utf-8"))
        for r in d.get("rows", []):
            rows.append({**r, "source": Path(f).name})
    man = HERE / "evidence_manual.json"
    if man.exists():
        for r in json.loads(man.read_text(encoding="utf-8"))["rows"]:
            rows.append({**r, "manual": True})
    return rows


def score(rows):
    caps = RULES["capabilities"]
    caps_cfg = RULES["_rules"]["caps"]
    out = {}
    for key, cfg in caps.items():
        mine = {}
        for r in rows:                                   # the newest result per id wins (files are read oldest first)
            if r["cap"] == key:
                mine[r["id"]] = r
        executed = [r for r in mine.values() if r["status"] in ("pass", "fail")]
        passed = sum(r["status"] == "pass" for r in executed)
        base = round(10 * passed / len(executed), 2) if executed else 0.0
        crit_missing = [c for c in cfg["critical"] if c not in mine or mine[c]["status"] not in ("pass", "fail")]
        crit_failed = [c for c in cfg["critical"] if c in mine and mine[c]["status"] == "fail"]
        cap, why = 10.0, []
        if crit_missing:
            cap, why = min(cap, caps_cfg["critical_not_run"]), why + [f"critical not run: {', '.join(crit_missing)}"]
        if crit_failed:
            cap, why = min(cap, caps_cfg["critical_failed"]), why + [f"critical failed: {', '.join(crit_failed)}"]
        slo_missed = [r["id"] for r in mine.values() if r.get("slo") and r["status"] == "fail"]
        if slo_missed:
            cap, why = min(cap, caps_cfg["slo_missed"]), why + ["SLO missed: " + ", ".join(slo_missed)]
        flaky = [f"{r['id']} {r['repeat_rate']}" for r in mine.values() if r.get("repeat_rate") is not None and r["repeat_rate"] < 0.95]
        if cfg.get("repeat") and flaky:
            cap, why = min(cap, caps_cfg["repeat_reliability_below_0_95"]), why + ["repeat reliability < 0.95: " + ", ".join(flaky)]
        out[key] = {"name": cfg["name"], "score": round(min(base, cap), 2), "base": base, "cap": cap, "passed": passed,
                    "executed": len(executed), "critical_total": len(cfg["critical"]), "critical_missing": crit_missing,
                    "critical_failed": crit_failed, "why": why}
    scores = [v["score"] for v in out.values()]
    return out, round(statistics.mean(scores), 2), min(scores)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--label", default="")
    a = ap.parse_args()
    suite_rows, suite_file = rows_from_suite()
    rows = suite_rows + rows_from_live_runs(suite_file) + rows_from_coordinator() + rows_from_files()
    per, mean, low = score(rows)
    for k, v in per.items():
        print(f"{k:28} {v['score']:5.2f}  ({v['passed']}/{v['executed']} passed; critical missing {len(v['critical_missing'])}, failed {len(v['critical_failed'])})")
    print(f"OVERALL mean {mean}  lowest {low}  target 9.5 each and mean")
    res = HERE / "results"
    res.mkdir(exist_ok=True)
    out = res / f"score-{time.strftime('%Y%m%d-%H%M%S')}{('-' + a.label) if a.label else ''}.json"
    out.write_text(json.dumps({"time": time.strftime("%Y-%m-%d %H:%M:%S"), "label": a.label, "suite_file": suite_file and Path(suite_file).name,
                               "rules_version": RULES["_rules"]["version"], "overall_mean": mean, "lowest": low, "capabilities": per,
                               "rows": rows}, indent=1), encoding="utf-8")
    print("saved", out)


if __name__ == "__main__":
    main()
