"""End to end through the REAL path: plugin desktop:run_pc_task -> local decision model -> coordinator -> console ->
kernel -> laptop node -> Windows / the owner's Chrome (2026-10-16). Windows, agent-venv. Needs the kill switch off.
The extension's connect prompt is approved for an example.com tab by UI Automation (the owner's instruction).
Each row: the request, the status, the steps with their checks, the time; and whether that matches what should happen.
"""
import json
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "scripts"))
from live_check import jget, run_entry  # noqa: E402

ALLOW = str(HERE.parent / "scripts" / "allow_playwright_connection.ps1")
CASES = [
    # since 2026-10-16 a named account is checked first (browser whoami), then the site opens (QUALITY_95_EVALUATION_PLAN.md)
    ("G01", "open example.com in my VenkatAMV account", "completed", ["browser", "open_website"]),
    ("G02", "open notepad", "completed", ["open_app"]),
    ("G03", "type hello from jarvis into notepad", "completed", ["type_text"]),
    ("G04", "minimize notepad", "completed", ["window"]),
    ("G05", "restore the notepad window", "completed", ["window"]),
    ("G06", "how much disk space do I have left", "completed", ["check_pc"]),
    ("G07", "play a Kalyani song and tell me when it is playing", "completed", ["play_media"]),
    ("G08", "what song is playing right now", "completed", ["check_pc"]),
    ("G09", "pause the music", "completed", ["press_key"]),
    ("G10", "open my venkat account", "needs_clarification", []),
    ("G11", "what is the capital of Italy", "not_a_pc_task", []),
    ("G12", "which google account is chrome signed into", "completed", ["browser"]),
]


HAS_TOOL = {"G03"}


def main():
    rows = []
    _, st = jget("http://127.0.0.1:8799/api/owner/state", headers=None)
    if (st or {}).get("killed"):
        print("BLOCKED: the kill switch is on (lift it at the kernel console with RESUME first)")
        return 2
    subprocess.run(["taskkill", "/IM", "notepad.exe", "/F"], capture_output=True)
    for cid, req, want_status, want_tools in CASES:
        helper = None
        if any(t in ("open_website", "play_media", "browser") for t in want_tools):
            # the connect page lists the tabs of the profile being connected: open the example tab in THAT profile
            run_entry("desktop", "open_website", {"url": "https://example.com/", "profile": "VenkatAMV" if "VenkatAMV" in req else "default"}, wait=60)
            helper = subprocess.Popen(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", ALLOW, "-TimeoutS", "120"],
                                      stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        t0 = time.time()
        r = run_entry("desktop", "run_pc_task", {"request": req}, wait=420)
        secs = round(time.time() - t0, 1)
        if helper:
            try:
                helper.communicate(timeout=10)
            except subprocess.TimeoutExpired:
                helper.kill()
        d = r.get("data") or {}
        tools = [s.get("tool") for s in d.get("steps") or []]
        # typing cases check that the typing step is there and verified (as A34/H19 do): an "open the app" step before it
        # only brings the existing window to the front (QUALITY_95_EVALUATION_PLAN.md, scorer changes)
        shape = all(t in tools for t in want_tools) if cid in HAS_TOOL else tools[:len(want_tools)] == want_tools
        ok = d.get("status") == want_status and shape and all(s.get("passed") for s in d.get("steps") or [])
        old_rule = d.get("status") == want_status and tools[:len(want_tools)] == want_tools and all(s.get("passed") for s in d.get("steps") or [])
        rows.append({"id": cid, "request": req, "ok": ok, "ok_old_rule": old_rule, "status": d.get("status"), "task": d.get("task"), "summary": d.get("summary"), "steps": d.get("steps"),
                     "plan_seconds": d.get("plan_seconds"), "seconds": secs, "error": r.get("error")})
        print(f"[{'PASS' if ok else 'FAIL'}] {cid} {req!r}: {d.get('status')} in {secs} s — {str(d.get('summary'))[:140]}", flush=True)
        time.sleep(1)
    subprocess.run(["taskkill", "/IM", "notepad.exe", "/F"], capture_output=True)
    # how long one UI action takes end to end (plugin -> console -> kernel -> node -> Windows -> result), from the task
    # records' own step times; the plan time is measured separately (C3/C11 L-plan-p50)
    tasks = Path(r"D:\jarvis\data\jarvis\pc_tasks")
    ui = []
    for r in rows:
        tid = (r.get("task") or "")
        f = tasks / f"{tid}.json"
        if tid and f.exists():
            rec = json.loads(f.read_text(encoding="utf-8"))
            ui += [round(s["finished"] - s["started"], 2) for s in rec.get("steps", [])
                   if s.get("tool") in ("window", "type_text", "press_key", "open_app") and s.get("finished") and s.get("started")]
    ui.sort()
    p50 = ui[len(ui) // 2] if ui else None
    quality95 = [{"cap": "C11_performance", "id": "L-ui-action-p50", "status": "pass" if p50 is not None and p50 <= 3.0 else "fail",
               "slo": True, "detail": f"one UI step end to end, p50 {p50} s over {len(ui)} steps {ui} (SLO 3.0 s)"},
              {"cap": "C4_windows_control", "id": "W-ui-action-latency", "status": "pass" if p50 is not None and p50 <= 3.0 else "fail",
               "slo": True, "detail": f"p50 {p50} s over {len(ui)} steps"}]
    (Path(__file__).resolve().parents[1] / "evals" / "quality95" / "results" / f"uilatency-{time.strftime('%Y%m%d-%H%M%S')}.json").write_text(
        json.dumps({"rows": quality95}, indent=1), encoding="utf-8")
    print("UI action p50:", p50, "s over", len(ui), "steps", ui)
    out = Path(__file__).resolve().parents[1] / "evals" / "pc_control" / "results" / f"coordinator-{time.strftime('%Y%m%d-%H%M%S')}.json"
    out.write_text(json.dumps({"cases": rows}, indent=1, default=str), encoding="utf-8")
    print("SUMMARY", sum(r["ok"] for r in rows), "of", len(rows), "->", out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
