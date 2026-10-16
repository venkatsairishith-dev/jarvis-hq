"""End to end through the REAL path: plugin desktop:run_pc_task -> local decision model -> coordinator -> console ->
kernel -> laptop node -> Windows / the owner's Chrome (2026-10-16). Windows, neko-venv. Needs the kill switch off.
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
    ("G01", "open example.com in my VenkatAMV account", "completed", ["open_website"]),
    ("G02", "open notepad", "completed", ["open_app"]),
    ("G03", "type hello from neko into notepad", "completed", ["type_text"]),
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


def main():
    rows = []
    _, st = jget("http://127.0.0.1:8799/api/owner/state", headers={"X-Jarvis-Console": "1"})
    if (st or {}).get("killed"):
        print("BLOCKED: the kill switch is on (lift it at the kernel console with RESUME first)")
        return 2
    subprocess.run(["taskkill", "/IM", "notepad.exe", "/F"], capture_output=True)
    for cid, req, want_status, want_tools in CASES:
        helper = None
        if any(t in ("open_website", "play_media", "browser") for t in want_tools):
            run_entry("desktop", "open_website", {"url": "https://example.com/", "profile": "default"}, wait=60)
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
        ok = d.get("status") == want_status and tools[:len(want_tools)] == want_tools and all(s.get("passed") for s in d.get("steps") or [])
        rows.append({"id": cid, "request": req, "ok": ok, "status": d.get("status"), "summary": d.get("summary"), "steps": d.get("steps"),
                     "plan_seconds": d.get("plan_seconds"), "seconds": secs, "error": r.get("error")})
        print(f"[{'PASS' if ok else 'FAIL'}] {cid} {req!r}: {d.get('status')} in {secs} s — {str(d.get('summary'))[:140]}", flush=True)
        time.sleep(1)
    subprocess.run(["taskkill", "/IM", "notepad.exe", "/F"], capture_output=True)
    out = Path(__file__).resolve().parents[1] / "evals" / "pc_control" / "results" / f"coordinator-{time.strftime('%Y%m%d-%H%M%S')}.json"
    out.write_text(json.dumps({"cases": rows}, indent=1, default=str), encoding="utf-8")
    print("SUMMARY", sum(r["ok"] for r in rows), "of", len(rows), "->", out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
