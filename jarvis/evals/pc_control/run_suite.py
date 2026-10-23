"""The PC-control acceptance suite: every category, one row per distinct case, one JSON (2026-10-16). Windows.

    D:\\jarvis\\tools\\agent-venv\\Scripts\\python.exe jarvis\\evals\\pc_control\\run_suite.py [--decider decider-qwen3-4b-2507] [--skip-live]

  A  natural language -> plan: decision_cases.json (dev) + decision_cases_holdout.json, through the chosen local model
  B  Windows desktop execution (live, Notepad and the sample app)            live_desktop.py
  C  browser agent on local pages in an isolated headless Chrome (live)     live_browser.py
  D  diagnosis and recovery on sample-app copies (live)                      live_diagnosis.py
  E  coordination: coordinator, cancellation, budgets, locks (unit)          pytest
  F  privacy and security: policy, approvals, allowlists, redaction (unit) + a live leak scan of logs and outputs
Results: results/suite-<time>.json and a printed table (passed / failed / not tested per category).
"""
import argparse
import json
import re
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
PY = r"D:\jarvis\tools\agent-venv\Scripts\python.exe"
NODE_PY = r"D:\jarvis\tools\browser-venv\Scripts\python.exe"
PRIV_PY = r"D:\jarvis\tools\privacy-venv\Scripts\python.exe"
RES = HERE / "results"


def sh(args, cwd=REPO, timeout=3600):
    return subprocess.run(args, cwd=str(cwd), capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout)


def newest(prefix):
    files = sorted(RES.glob(prefix + "-*.json"), key=lambda p: p.stat().st_mtime)
    return json.loads(files[-1].read_text(encoding="utf-8")) if files else {}


def pytest_cases(cat, paths, py=PY, keyword=None, cwd=REPO):
    xml = RES / f"_junit-{cat}.xml"
    args = [py, "-m", "pytest", *paths, "-q", "-p", "no:cacheprovider", f"--junitxml={xml}"] + (["-k", keyword] if keyword else [])
    sh(args, cwd=cwd)
    rows = []
    for tc in ET.parse(xml).getroot().iter("testcase"):
        failed = tc.find("failure") is not None or tc.find("error") is not None
        skipped = tc.find("skipped") is not None
        rows.append({"cat": cat, "id": f"{cat}-{tc.get('classname', '').split('.')[-1]}.{tc.get('name')}", "name": tc.get("name"),
                     "status": "not_tested" if skipped else ("fail" if failed else "pass")})
    xml.unlink(missing_ok=True)
    return rows


def unittest_cases(cat, module, py, cwd):
    """For environments without pytest: unittest -v prints one 'name (module.Class...) ... ok' line per test."""
    p = sh([py, "-m", "unittest", "-v", module], cwd=cwd)
    rows = []
    for m in re.finditer(r"^(test\w+) \(([\w.]+)\)\S*\s*(?:\n.*?)?\.\.\. (ok|FAIL|ERROR|skipped.*)$", p.stderr, re.M):
        st = m.group(3)
        rows.append({"cat": cat, "id": f"{cat}-{m.group(2).split('.')[-1]}.{m.group(1)}", "name": m.group(1),
                     "status": "pass" if st == "ok" else ("not_tested" if st.startswith("skipped") else "fail")})
    if not rows:
        rows.append({"cat": cat, "id": f"{cat}-{module}", "name": module, "status": "fail", "detail": p.stderr[-300:]})
    return rows


def decision_rows(decider):
    rows = []
    for setname, tag in (("decision_cases.json", "dev"), ("decision_cases_holdout.json", "holdout")):
        sh([PY, str(HERE / "eval_decision.py"), "--models", decider, "--set", setname])
        r = newest("decision")
        m = r["models"][0]
        failed = {f["id"]: f["why"] for f in m["failures"]}
        cases = json.loads((HERE / setname).read_text(encoding="utf-8"))
        rows += [{"cat": "A", "id": c["id"], "name": c["req"], "status": "fail" if c["id"] in failed else "pass",
                  "detail": failed.get(c["id"], ""), "set": tag} for c in cases]
        rows.append({"cat": "A-meta", "id": f"A-{tag}-latency", "name": f"{decider} {tag}: p50 {m['p50_s']} s, p95 {m['p95_s']} s, "
                     f"cold {m['cold_first_call_s']} s, RAM {m['memory'].get('ram_mb')} MB, GPU {m['memory'].get('top_gpu_dedicated_mb')} MB",
                     "status": "info"})
    return rows


def live_rows(cat, script, py):
    p = sh([py, str(HERE / script)], timeout=1800)
    prefix = {"live_desktop.py": "desktop", "live_browser.py": "browser", "live_diagnosis.py": "diagnosis"}[script]
    r = newest(prefix)
    rows = [{"cat": cat, "id": c["id"], "name": c["name"], "status": "pass" if c["ok"] else "fail", "detail": c.get("detail", "")}
            for c in r.get("cases", [])]
    rows += [{"cat": cat, "id": n["id"], "name": n["name"], "status": "not_tested", "detail": n["why"]} for n in r.get("not_tested", [])]
    if not rows:
        rows.append({"cat": cat, "id": f"{cat}-run", "name": script, "status": "fail", "detail": (p.stderr or p.stdout)[-300:]})
    return rows


def leak_scan(since):
    """F: secrets and screen captures in what the system writes."""
    pats = {"bearer token": r"Bearer [A-Za-z0-9._-]{16,}", "groq key": r"gsk_[A-Za-z0-9]{20,}", "tavily key": r"tvly-[A-Za-z0-9]{10,}",
            "openai-style key": r"\bsk-[A-Za-z0-9]{20,}", "extension token": r"PLAYWRIGHT_MCP_EXTENSION_TOKEN=\S",
            "full owner address": r"venkatgaminggem17@gmail\.com"}
    data = Path(r"D:\jarvis\data")
    files = list((data / "logs").glob("*.log")) + list((data / "plugins" / "pc_tasks").glob("*.json")) + [data / "plugins" / "browser_profiles.json"]
    rows = []
    for name, rx in pats.items():
        hits = [f.name for f in files if f.exists() and re.search(rx, f.read_text(encoding="utf-8", errors="replace"))]
        rows.append({"cat": "F", "id": f"F-leak-{name.replace(' ', '-')}", "name": f"no {name} in logs, task records or the profile registry",
                     "status": "fail" if hits else "pass", "detail": ", ".join(hits[:5])})
    shots = [p.name for p in Path(r"D:\jarvis\outputs\screens").glob("*.png") if p.stat().st_mtime >= since] if Path(r"D:\jarvis\outputs\screens").is_dir() else []
    rows.append({"cat": "F", "id": "F-no-screenshots", "name": "no screenshots were taken or kept by the suite", "status": "fail" if shots else "pass",
                 "detail": ", ".join(shots)})
    left = [p.name for p in Path(r"D:\jarvis\outputs\local-only").glob("*")] if Path(r"D:\jarvis\outputs\local-only").is_dir() else []
    rows.append({"cat": "F", "id": "F-no-local-text-left", "name": "no screen/page text or Playwright logs left behind",
                 "status": "fail" if left else "pass", "detail": ", ".join(left[:5])})
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--decider", default="decider-qwen3-4b-2507")
    ap.add_argument("--skip-live", action="store_true")
    ap.add_argument("--only", default="A,B,C,D,E,F")
    a = ap.parse_args()
    want = set(a.only.split(","))
    since = time.time()
    rows = []
    if "A" in want:
        rows += decision_rows(a.decider)
    if not a.skip_live:
        if "B" in want:
            rows += live_rows("B", "live_desktop.py", NODE_PY)
        if "C" in want:
            rows += live_rows("C", "live_browser.py", NODE_PY)
        if "D" in want:
            rows += live_rows("D", "live_diagnosis.py", PY)
    if "E" in want:
        rows += pytest_cases("E", ["jarvis/plugins/desktop/tests/test_coordinator.py"], keyword="not resolution and not round3")
        rows += pytest_cases("E", ["jarvis/kernel/tests/test_node_loop.py"], keyword="cancel")
        rows += pytest_cases("E", ["jarvis/kernel/tests/test_node_plugin_paths.py"], keyword="Cancellation")
        rows += pytest_cases("E", ["jarvis/kernel/tests/test_pc_task_kernel.py"], keyword="cancel")
        rows += pytest_cases("E", ["jarvis/plugins/desktop/tests/test_core.py"])
    if "F" in want:
        rows += pytest_cases("F", ["jarvis/kernel/tests/test_pc_task_kernel.py"], keyword="not cancel")
        rows += pytest_cases("F", ["jarvis/kernel/tests/test_node_plugin_paths.py"], keyword="Profiles or allowlist or argv or Diagnostics or Repairs or Keys or youtube")
        rows += unittest_cases("F", "test_privacy", PRIV_PY, REPO / "jarvis" / "privacy")
        rows += leak_scan(since)
    real = [r for r in rows if r["status"] != "info"]
    by = {}
    for r in real:
        c = by.setdefault(r["cat"], {"pass": 0, "fail": 0, "not_tested": 0})
        c[r["status"]] += 1
    out = RES / f"suite-{time.strftime('%Y%m%d-%H%M%S')}.json"
    out.write_text(json.dumps({"time": time.strftime("%Y-%m-%d %H:%M:%S"), "decider": a.decider, "by_category": by, "rows": rows}, indent=1),
                   encoding="utf-8")
    for cat in sorted(by):
        print(f"{cat}: {by[cat]}")
    for r in rows:
        if r["status"] in ("fail", "info"):
            print(f"  {r['status'].upper()} {r['id']}: {r['name'][:80]} {r.get('detail', '')[:120]}")
    tot = {k: sum(v[k] for v in by.values()) for k in ("pass", "fail", "not_tested")}
    print("TOTAL", tot, "->", out)
    return 1 if tot["fail"] else 0


if __name__ == "__main__":
    sys.exit(main())
