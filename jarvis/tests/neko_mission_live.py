"""Live long mission through the REAL path (2026-10-16): desktop:start_mission -> durable mission (DBOS) -> local decider
-> research plugin (web search, checked citations) -> deliverables (deck + QA) -> opened, with a window seen -> explanation.
Windows, neko-venv; plugin runtime, kernel and node up. Public topic only (no private data). Afterwards it closes the deck
window it opened (that window only).
    python jarvis\\tests\\neko_mission_live.py
Writes jarvis/evals/neko95/results/mission-<time>.json (row M-research-deck-open-explain, C6).
"""
import json
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "scripts"))
from live_check import run_entry  # noqa: E402

GOAL = "research how home solar panels work and what they cost, make slides, open them and explain what you found"
CLOSE = r"""
Add-Type -AssemblyName UIAutomationClient
$AE=[System.Windows.Automation.AutomationElement]
$n=0
foreach ($w in $AE::RootElement.FindAll([System.Windows.Automation.TreeScope]::Children, [System.Windows.Automation.Condition]::TrueCondition)) {
  if ($env:STEM -and $w.Current.Name.ToLower().Contains($env:STEM)) { $wp=$null
    if ($w.TryGetCurrentPattern([System.Windows.Automation.WindowPattern]::Pattern, [ref]$wp)) { $wp.Close(); $n++ } } }
$n
"""


def main():
    t0 = time.time()
    r = run_entry("desktop", "start_mission", {"goal": GOAL}, wait=60)
    mid = ((r or {}).get("data") or {}).get("mission")
    if not mid:
        print("FAIL could not start:", r)
        rec = {"status": "not_started", "error": str(r)[:300]}
    else:
        print("mission", mid, flush=True)
        rec = {}
        while time.time() - t0 < 1500:
            rec = (run_entry("desktop", "mission_status", {"mission": mid}, wait=30) or {}).get("data") or {}
            if rec.get("status") not in ("queued", "running", None):
                break
            time.sleep(15)
    steps = rec.get("steps") or []
    research = next((s for s in steps if s.get("tool") == "research"), {})
    ok = (rec.get("status") == "completed" and research.get("passed") and "opened" in str(research.get("checked", ""))
          and "Findings:" in str(rec.get("explain", "")))
    detail = f"status {rec.get('status')}; steps {[(s.get('tool'), s.get('passed')) for s in steps]}; " \
             f"research check: {research.get('checked')}; {round(time.time() - t0)} s; error: {rec.get('error')}"
    print(("PASS " if ok else "FAIL ") + "M-research-deck-open-explain", detail)
    print("explain:", str(rec.get("explain", ""))[:400])
    # close the deck window this test opened
    checked = str(research.get("checked") or "")
    stem = Path(checked.split("presentation ", 1)[-1].split(" opened")[0]).stem.lower()[:25] if "presentation " in checked else ""
    if stem:
        n = subprocess.run(["powershell", "-NoProfile", "-Command", CLOSE], capture_output=True, text=True, env={**__import__("os").environ, "STEM": stem}).stdout.strip()
        print("closed deck windows:", n)
    out = HERE.parent / "evals" / "neko95" / "results" / f"mission-{time.strftime('%Y%m%d-%H%M%S')}.json"
    out.write_text(json.dumps({"time": time.strftime("%Y-%m-%d %H:%M:%S"), "goal": GOAL, "record": rec,
                               "rows": [{"cap": "C6_missions", "id": "M-research-deck-open-explain", "status": "pass" if ok else "fail",
                                         "detail": detail[:400]}]}, indent=1, default=str), encoding="utf-8")
    print("saved", out)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
