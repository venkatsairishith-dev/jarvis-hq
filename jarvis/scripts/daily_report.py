"""One-page daily health report for the JARVIS stack. Windows, read-only, no model calls.

    python daily_report.py        writes <data>\\logs\\daily_report_YYYYMMDD.md (keeps the newest 14) and prints where

Contents: the live checks that need no model (servers, plugins, secrets hygiene, documents, study vault, speech, English-only
persona, watchdog, JARVIS kernel), what the watchdog repaired in the last 24 hours, the most frequent warnings and errors in
today's logs, and free disk space. Scheduled by install-daily-report.ps1.
"""
import datetime
import glob
import json
import os
import shutil
import subprocess
import sys

DATA = os.environ.get("JARVIS_DATA_DIR", r"D:\jarvis\data")
HERE = os.path.dirname(os.path.abspath(__file__))
LOGS = os.path.join(DATA, "logs")


def run(args, timeout=900):
    r = subprocess.run([sys.executable, *args], capture_output=True, text=True, timeout=timeout,
                       env=dict(os.environ, PYTHONUTF8="1", PYTHONIOENCODING="utf-8"))
    return r.returncode, (r.stdout or "") + (r.stderr or "")


def watchdog_events(hours=24):
    out, cutoff = [], datetime.datetime.now() - datetime.timedelta(hours=hours)
    try:
        for ln in open(os.path.join(LOGS, "watchdog.log"), encoding="utf-8"):
            try:
                rec = json.loads(ln)
                if datetime.datetime.strptime(rec["ts"], "%Y-%m-%d %H:%M:%S") >= cutoff:
                    out.append(rec)
            except (ValueError, KeyError):
                continue
    except OSError:
        pass
    return out


def main():
    today = datetime.date.today().strftime("%Y%m%d")
    lines = [f"# Daily report {datetime.date.today().isoformat()}", ""]
    code, text = run([os.path.join(HERE, "live_check.py"), "--no-model", "--no-asr", "--no-browser"])
    results = [l for l in text.splitlines() if l.startswith("[")]
    failed = [l for l in results if l.startswith("[FAIL]")]
    summary = next((l for l in text.splitlines() if "checks passed" in l), "live check produced no summary")
    lines += ["## Live checks (no model)", summary, ""] + [f"- {l}" for l in failed] + ([""] if failed else [])

    ev = watchdog_events()
    problems = [e for e in ev if e.get("event") == "problem"]
    heals = [e for e in ev if e.get("event") in ("start_stack", "start_plugin")]
    lines += ["## Watchdog, last 24 hours", f"{len(problems)} problem(s) seen, {len(heals)} repair(s) run."]
    for e in (problems + heals)[-8:]:
        lines.append(f"- {e['ts']} {e['event']} " + json.dumps({k: v for k, v in e.items() if k not in ('ts', 'event')}, ensure_ascii=False)[:150])
    lines.append("")

    _, digest = run([os.path.join(HERE, "log_digest.py"), "--top", "12", "--day", today], timeout=300)
    lines += ["## Most frequent warnings and errors today", "```"] + digest.strip().splitlines()[:14] + ["```", ""]

    lines += ["## Disk"]
    for drive in ("C:\\", "D:\\"):
        try:
            u = shutil.disk_usage(drive)
            lines.append(f"- {drive} {u.free / 1e9:.1f} GB free of {u.total / 1e9:.0f} GB")
        except OSError:
            pass
    stray = os.path.join(os.path.abspath(os.path.join(HERE, "..", "..")), ".venv")
    if os.path.isdir(stray):
        lines.append(f"- NOTE: a project .venv exists at {stray}; jarvis-start.ps1 removes it if empty")

    path = os.path.join(LOGS, f"daily_report_{today}.md")
    os.makedirs(LOGS, exist_ok=True)
    open(path, "w", encoding="utf-8").write("\n".join(lines) + "\n")
    for old in sorted(glob.glob(os.path.join(LOGS, "daily_report_*.md")))[:-14]:
        os.remove(old)
    print(path)
    print(summary)
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
