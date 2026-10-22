"""Pre-flight before the owner sits down to test: everything that would put an ERROR ON THE SCREEN, checked first, in plain words.

    D:\\jarvis\\tools\\neko-venv\\Scripts\\python.exe jarvis\\scripts\\preflight.py [--no-live-check] [--json]

Checks (all read-only, nothing is started or changed):
  1. the live health check (live_check.py, ~70 checks of the running servers, plugins, secrets handling): every failure is listed;
  2. every scheduled task whose name starts with "Jarvis": a task whose last run ended in a real error code is a window or a log line
     waiting to happen (a script that no longer exists, a program that crashed). Codes that only mean "running / not run yet / stopped
     by the user" are fine;
  3. the wake listener: running, and not stuck in "error";
  4. D: has room (the one storage rule: nothing outside D:).
Exit code 0 only when everything is green. A red line always says what is wrong and the next step; nothing is hidden or skipped.
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
MIN_FREE_GB = 5.0

# Task Scheduler "Last Result" values that are not errors: 0 success, 0x41300 ready, 0x41301 running, 0x41303 never run yet,
# 0x41304 no more runs, 0x41306 stopped by the user, 0x41325 queued.
BENIGN_RESULTS = {0, 0x41300, 0x41301, 0x41303, 0x41304, 0x41306, 0x41325}
KNOWN = {0x80070002: "a file the task starts is missing", 0x8007010B: "the folder the task starts in is missing",
         0x80070005: "the task is not allowed to run", 0xC000013A: "the task was stopped with Ctrl+C", 1: "the program ended with an error",
         2: "the program ended with an error (code 2)", 0xFFFFFFFF: "the program ended with an error (-1)"}


def result_value(text: str) -> int | None:
    """Task Scheduler prints Last Result as a decimal (sometimes signed) number or hex; None when it cannot be read."""
    t = str(text or "").strip()
    try:
        v = int(t, 16) if t.lower().startswith("0x") else int(t)
    except ValueError:
        return None
    return v & 0xFFFFFFFF


def audit_tasks(csv_text: str) -> list[dict]:
    """Every Jarvis* task with its verdict. csv_text is `schtasks /Query /FO CSV /V` output (English column names)."""
    rows, seen = [], set()
    for row in csv.DictReader(io.StringIO(csv_text)):
        name = (row.get("TaskName") or "").strip()
        if not name or name == "TaskName":                       # /V repeats the header for every folder
            continue
        short = name.rsplit("\\", 1)[-1]
        if not short.lower().startswith("jarvis") or short in seen:
            continue
        seen.add(short)
        code = result_value(row.get("Last Result", ""))
        status = (row.get("Status") or "").strip()
        run = (row.get("Task To Run") or "").strip()
        if (row.get("Scheduled Task State") or "").strip().lower() == "disabled":
            rows.append({"task": short, "ok": True, "detail": "disabled (not running)", "runs": run[:120]})
            continue
        ok = code is None or code in BENIGN_RESULTS
        why = "" if ok else f"last run ended with code {code:#x}" + (f": {KNOWN[code]}" if code in KNOWN else "")
        rows.append({"task": short, "ok": ok, "detail": why or (status or "ok"), "runs": run[:120]})
    return rows


def parse_live_check(text: str) -> list[tuple[str, bool, str]]:
    out = []
    for line in str(text or "").splitlines():
        if line.startswith("[PASS] ") or line.startswith("[FAIL] "):
            name, _, detail = line[7:].partition(" -- ")
            out.append((name.strip(), line.startswith("[PASS]"), detail.strip()))
    return out


def run(cmd: list[str], timeout: float) -> str:
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, errors="replace").stdout


def wake_state(fetch) -> tuple[bool, str]:
    try:
        st = fetch()
    except Exception as exc:                                     # the listener is not answering at all
        return False, f"the wake listener does not answer ({type(exc).__name__}): start the stack with jarvis\\scripts\\neko-start.ps1"
    state = str((st or {}).get("state") or "")
    if state in ("error", ""):
        return False, f"the wake listener is in state '{state or 'unknown'}': {str((st or {}).get('detail') or '')[:120]}"
    return True, f"state {state}"


def gather(live_text: str | None, tasks_csv: str | None, wake, free_gb: float | None) -> list[dict]:
    items: list[dict] = []
    if live_text is not None:
        for name, ok, detail in parse_live_check(live_text):
            items.append({"area": "live check", "name": name, "ok": ok, "detail": detail})
    if tasks_csv is not None:
        for r in audit_tasks(tasks_csv):
            items.append({"area": "scheduled task", "name": r["task"], "ok": r["ok"], "detail": r["detail"]})
    if wake is not None:
        ok, detail = wake_state(wake)
        items.append({"area": "voice", "name": "wake listener", "ok": ok, "detail": detail})
    if free_gb is not None:
        items.append({"area": "disk", "name": "D: free space", "ok": free_gb >= MIN_FREE_GB, "detail": f"{free_gb:.1f} GB free (need {MIN_FREE_GB:.0f})"})
    return items


def report(items: list[dict]) -> tuple[str, int]:
    bad = [i for i in items if not i["ok"]]
    lines = [f"{'OK  ' if i['ok'] else 'RED '} [{i['area']}] {i['name']}" + (f" -- {i['detail']}" if i["detail"] and not i["ok"] else "") for i in items]
    head = f"PRE-FLIGHT: {len(items) - len(bad)} green, {len(bad)} red"
    return "\n".join([head, *lines, ("ALL GREEN: nothing should show an error." if not bad else
                                      f"NOT READY: fix the {len(bad)} red line(s) above first; each one would show up as an error or a failed test.")]), (1 if bad else 0)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--no-live-check", action="store_true", help="skip the ~70-check live health run (about 1 minute)")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    live = None
    if not a.no_live_check:
        live = run([sys.executable, str(HERE / "live_check.py"), "--no-asr", "--no-browser", "--no-model"], 600)
    tasks = None
    if os.name == "nt":
        tasks = run(["schtasks", "/Query", "/FO", "CSV", "/V"], 60)
    wake = None
    try:
        import control_auth
        import loopback_net
        import urllib.request

        def fetch():
            req = urllib.request.Request("http://127.0.0.1:48945/status", headers=control_auth.headers_for("wake"))
            with loopback_net.urlopen(req, timeout=5) as r:
                return json.loads(r.read().decode("utf-8", "replace"))
        wake = fetch
    except Exception:                                            # no helpers on this machine: the check says so below
        wake = None
    root = Path(os.environ.get("JARVIS_DATA_ROOT") or ("D:\\" if os.name == "nt" else "/"))
    free = shutil.disk_usage(root).free / 1e9 if root.exists() else None
    items = gather(live, tasks, wake, free)
    text, code = report(items)
    print(json.dumps(items, indent=1) if a.json else text)
    return code


if __name__ == "__main__":
    sys.path.insert(0, str(HERE))
    raise SystemExit(main())
