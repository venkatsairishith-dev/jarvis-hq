"""Runs the checks that wait on the owner (2026-10-16): once the guided owner test has finished (kill switch lifted,
PIN approval and denial done, voice test done) AND Visual Studio Code answers again, it runs, in order:
  1. the coordinator end to end through the kernel (neko_coordinator_live.py)
  2. Windows-MCP vs Neko UIA on Notepad (compare_windows_mcp.py), then removes the Windows-MCP test venv
  3. the full acceptance suite (run_suite.py)
Everything goes to D:\\jarvis\\outputs\\neko\\after-owner.log. Windows, neko-venv. Waits at most 12 hours.
"""
import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
NEKO = r"D:\jarvis\tools\neko-venv\Scripts\python.exe"
NODE_PY = r"D:\jarvis\tools\browser-venv\Scripts\python.exe"
GUIDED = Path(r"D:\jarvis\outputs\neko\guided-owner-test.json")
LOG = Path(r"D:\jarvis\outputs\neko\after-owner.log")


def log(msg):
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(time.strftime("%H:%M:%S ") + msg + "\n")


def code_ok():
    out = subprocess.run(["powershell", "-NoProfile", "-Command",
                          "@(Get-Process Code -ErrorAction SilentlyContinue | Where-Object { $_.MainWindowHandle -ne 0 -and -not $_.Responding }).Count"],
                         capture_output=True, text=True).stdout.strip()
    return out == "0"


def guided_done():
    try:
        return bool(json.loads(GUIDED.read_text(encoding="utf-8")).get("finished"))
    except (OSError, ValueError):
        return False


def run(args, label):
    log(f"start {label}")
    p = subprocess.run(args, cwd=str(REPO), capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=7200)
    log(f"end {label}: exit {p.returncode}\n" + "\n".join((p.stdout or "").splitlines()[-40:]))


def main():
    LOG.parent.mkdir(parents=True, exist_ok=True)
    log("waiting for the guided owner test to finish and VS Code to respond")
    t0 = time.time()
    while time.time() - t0 < 12 * 3600 and not (guided_done() and code_ok()):
        time.sleep(30)
    if not (guided_done() and code_ok()):
        log(f"gave up: guided finished={guided_done()}, VS Code responding={code_ok()}")
        return 1
    run([NEKO, "jarvis/tests/neko_coordinator_live.py"], "coordinator end to end")
    run([NODE_PY, "jarvis/evals/pc_control/compare_windows_mcp.py", "Snapshot,Type"], "Windows-MCP vs Neko UIA")
    shutil.rmtree(r"D:\jarvis\tools\windows-mcp-venv", ignore_errors=True)
    log("removed the Windows-MCP test venv")
    run([NEKO, "jarvis/evals/pc_control/run_suite.py", "--decider", "decider"], "full suite")
    log("all done")
    return 0


if __name__ == "__main__":
    sys.exit(main())
