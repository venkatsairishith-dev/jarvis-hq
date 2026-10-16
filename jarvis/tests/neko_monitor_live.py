"""Live test of Neko's proactive PC monitor (plugin/plugins/desktop/monitor.py). Windows, neko-venv, desktop plugin running.
    python jarvis\\tests\\neko_monitor_live.py
PR-crash-alert        end to end: NekoCrashCanary.exe crashes (no popup) -> the plugin's 60 s timer reads the Application
                      log -> an alert is written and pushed. Measures time to alert. Needs build.ps1 -Canary first.
PR-disk-alert         real disk reading with a floor above the current free space -> alert; below it -> none.
PR-service-down-alert a real local listener on a free port is closed -> alert on the 2nd failed check, not the 1st.
PR-stuck-task-alert   a real task record left open past its budget -> alert; a finished one -> none.
PR-no-false-alarms    every alert the plugin wrote that is not a canary is checked against its source; any alert that
                      cannot be confirmed counts as false. Also: the canary crash is announced exactly once.
Results: jarvis/evals/neko95/results/monitor-<time>.json
"""
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from plugin.plugins.desktop import coordinator as coord, monitor as M  # noqa: E402

CANARY = Path(r"D:\jarvis\tests\neko-sample-app\NekoCrashCanary.exe")
LOG = coord.DATA / "neko" / "monitor_alerts.jsonl"
rows = []


def row(cid, ok, detail):
    rows.append({"cap": "C10_proactivity", "id": cid, "status": "pass" if ok else "fail", "detail": detail})
    print(("PASS " if ok else "FAIL ") + cid, detail, flush=True)


def alerts_since(t0):
    if not LOG.exists():
        return []
    out = []
    for line in LOG.read_text(encoding="utf-8").splitlines():
        try:
            r = json.loads(line)
        except ValueError:
            continue
        if r.get("time", 0) >= t0:
            out.append(r)
    return out


def crash_test():
    if not CANARY.exists():
        row("PR-crash-alert", False, "canary not built (build.ps1 -Canary)")
        return None
    state = coord.DATA / "neko" / "monitor_state.json"
    for _ in range(30):                                              # the first tick sets the bookmark; crash after it
        if M.State.load(state).bookmark:
            break
        time.sleep(5)
    else:
        row("PR-crash-alert", False, "the plugin's monitor never ran (no bookmark in monitor_state.json after 150 s)")
        return None
    t0 = time.time()
    p = subprocess.run([str(CANARY)], capture_output=True, timeout=60)
    deadline = t0 + 200
    hit = []
    while time.time() < deadline:
        hit = [a for a in alerts_since(t0) if a["kind"] == "crash" and a["subject"].lower() == "nekocrashcanary.exe"]
        if hit:
            break
        time.sleep(5)
    if hit:
        row("PR-crash-alert", True, f"canary exit {p.returncode}; alerted after {hit[0]['time'] - t0:.0f} s (timer 60 s)")
    else:
        row("PR-crash-alert", False, f"canary exit {p.returncode}; no alert within 200 s")
    time.sleep(70)                                                   # one more timer tick: must not repeat
    again = [a for a in alerts_since(t0) if a["kind"] == "crash" and a["subject"].lower() == "nekocrashcanary.exe"]
    return t0, len(again)


def disk_test():
    free = M.disk_free_gb()
    d = "D:\\" if "D:\\" in free else next(iter(free))
    hi = M.Monitor(M.State(bookmark=1), crashes=lambda a: [], services={}, floors={d: free[d] + 5}).check()
    lo = M.Monitor(M.State(bookmark=1), crashes=lambda a: [], services={}, floors={d: max(free[d] - 5, 0.1)}).check()
    ok = [a.kind for a in hi] == ["disk"] and lo == []
    row("PR-disk-alert", ok, f"{d} free {free[d]:.1f} GB: floor above -> {[a.text for a in hi]}; floor below -> {len(lo)} alerts")


def service_test():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    s.listen(5)
    port = s.getsockname()[1]
    st = M.State(bookmark=1)
    m = M.Monitor(st, crashes=lambda a: [], disks=lambda: {}, services={"canary listener": port})
    first = m.check()
    s.close()
    down1, down2 = m.check(), m.check()
    ok = first == [] and down1 == [] and [a.kind for a in down2] == ["service"]
    row("PR-service-down-alert", ok, f"port {port}: up -> {len(first)}, 1st refusal -> {len(down1)}, 2nd -> {[a.text for a in down2]}")


def stuck_test():
    with tempfile.TemporaryDirectory(dir=r"D:\jarvis-apps\tmp" if os.path.isdir(r"D:\jarvis-apps\tmp") else None) as tmp:
        folder = Path(tmp)
        now = time.time()
        (folder / "a.json").write_text(json.dumps({"id": "canary-open", "request": "monitor canary task", "started": now - 800,
                                                   "status": "running"}), encoding="utf-8")
        (folder / "b.json").write_text(json.dumps({"id": "canary-done", "request": "done task", "started": now - 5000,
                                                   "finished": now - 4000, "status": "completed"}), encoding="utf-8")
        a = M.Monitor(M.State(bookmark=1), crashes=lambda x: [], disks=lambda: {}, services={},
                      tasks=lambda: M.open_tasks(folder), budget_s=600).check()
    ok = [(x.kind, x.subject) for x in a] == [("stuck", "canary-open")]
    row("PR-stuck-task-alert", ok, f"open past budget -> {[x.text for x in a]}")


def confirm(a):
    """Is a non-canary alert true at its source?"""
    if a["kind"] == "crash":
        evs = M.crash_events(max(M.newest_record() - 5000, 0), limit=5000)
        return any(e["app"].lower() == a["subject"].lower() for e in evs)
    if a["kind"] == "disk":
        free = M.disk_free_gb().get(a["subject"])
        return free is not None and free < M.DISK_FLOOR_GB.get(a["subject"], 0) + 1.0
    if a["kind"] == "service":
        port = M.SERVICES.get(a["subject"])
        return port is not None and not M.port_up(port)   # still down now (a recovered one is checked by hand)
    if a["kind"] == "stuck":
        return any(t["id"] == a["subject"] for t in M.open_tasks(coord.TASKS))
    return False


def main():
    res = crash_test()
    disk_test()
    service_test()
    stuck_test()
    start = time.time() - 24 * 3600
    others = [a for a in alerts_since(start) if a["subject"].lower() != "nekocrashcanary.exe"]
    unconfirmed = [a for a in others if not confirm(a)]
    dup = res is not None and res[1] != 1
    ok = not unconfirmed and not dup
    row("PR-no-false-alarms", ok, f"{len(others)} non-canary alerts in 24 h, {len(unconfirmed)} unconfirmed "
                                  f"{[(a['kind'], a['subject']) for a in unconfirmed]}; canary crash announced "
                                  f"{res[1] if res else 'n/a'} time(s)")
    out = ROOT / "jarvis" / "evals" / "neko95" / "results" / f"monitor-{time.strftime('%Y%m%d-%H%M%S')}.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps({"time": time.strftime("%Y-%m-%d %H:%M:%S"), "rows": rows}, indent=1), encoding="utf-8")
    print("saved", out)
    return 0 if all(r["status"] == "pass" for r in rows) else 1


if __name__ == "__main__":
    sys.exit(main())
