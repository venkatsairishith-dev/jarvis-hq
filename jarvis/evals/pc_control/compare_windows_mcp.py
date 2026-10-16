"""Benchmark: Windows-MCP (CursorTouch, MIT, 0.8.7, isolated venv) vs Neko's own UIA layer on the same Notepad tasks
(2026-10-16). Windows. Telemetry off; only its read/click/type tools are enabled (no PowerShell, registry, files).
    D:\\jarvis\\tools\\browser-venv\\Scripts\\python.exe jarvis\\evals\\pc_control\\compare_windows_mcp.py [--list]
Measures per task: success (checked by reading Notepad's text through Win32) and seconds, over N repeats.
"""
import json
import re
import os
import statistics
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1] / "node"))
sys.path.insert(0, str(HERE))
from jarvis_node import winbackend as W, jobs as J  # noqa: E402
from jarvis_node import mcpclient  # noqa: E402
from jarvis_node.mcpclient import StdioSession  # noqa: E402
mcpclient.DISCOVER_WAIT = 60.0          # Windows-MCP starts slowly and speaks only the 2026 protocol
import live_desktop as LD  # noqa: E402  (its Win32 text reader)

EXE = r"D:\jarvis\tools\windows-mcp-venv\Scripts\windows-mcp.exe"
ENV = {**os.environ, "ANONYMIZED_TELEMETRY": "false"}


def text_of(res):
    raw = "\n".join(c.get("text", "") for c in res.get("content") or [] if c.get("type") == "text")
    try:                                     # it returns a JSON list of text blocks
        v = json.loads(raw)
        return "\n".join(v) if isinstance(v, list) else str(v)
    except ValueError:
        return raw


def fresh_notepad():
    subprocess.run(["taskkill", "/IM", "notepad.exe", "/F"], capture_output=True)
    subprocess.Popen(["notepad.exe"])
    time.sleep(2)


def run_wmcp(tools, reps):
    r = {"snapshot": [], "type": [], "ok": 0, "failures": []}
    t0 = time.time()
    s = StdioSession([EXE, "serve", "--transport", "stdio", "--tools", tools], env=ENV)
    s.start()
    s.handshake(timeout=120)
    r["start_s"] = round(time.time() - t0, 1)
    for i in range(reps):
        fresh_notepad()
        t = time.time()
        try:
            snap = text_of(s.call("Snapshot", {"use_vision": False}, timeout=60))
        except Exception as e:  # noqa: BLE001 - a hang is a measured failure: restart the server and go on
            r["snapshot"].append(time.time() - t)
            r["failures"].append(f"round {i}: Snapshot {type(e).__name__} {getattr(e, 'kind', '')}")
            s.close()
            s = StdioSession([EXE, "serve", "--transport", "stdio", "--tools", tools], env=ENV)
            s.start()
            s.handshake(timeout=120)
            continue
        r["snapshot"].append(time.time() - t)
        L = snap.splitlines()
        k = next((j for j, ln in enumerate(L) if 'window "Untitled - Notepad"' in ln), None)
        line = next((ln for ln in L[k:k + 6] if " edit " in ln), "") if k is not None else ""
        m = re.search(r"\((\d+),\s*(\d+)\)", line)
        if not m:
            r["failures"].append(f"round {i}: Notepad's text field not in its snapshot")
            continue
        t = time.time()
        try:
            s.call("Type", {"loc": [int(m.group(1)), int(m.group(2))], "text": f"wmcp {i}", "clear": True}, timeout=60)
        except Exception as e:  # noqa: BLE001
            r["failures"].append(f"round {i}: Type {type(e).__name__}")
        r["type"].append(time.time() - t)
        r["ok"] += f"wmcp {i}" in LD.ps(LD.VALUE)
    s.close()
    return r


def run_neko(reps):
    r = {"snapshot": [], "type": [], "ok": 0, "failures": []}
    for i in range(reps):
        fresh_notepad()
        t = time.time()
        els = J.ui_snapshot({"target": "notepad"}).get("elements") or []
        r["snapshot"].append(time.time() - t)
        f = next((e for e in els if str(e.get("cls") or "").startswith("Edit")), None)
        t = time.time()
        try:
            W.type_text(f"notepad|{f['id']}", {"text": f"neko {i}"})
        except Exception as e:  # noqa: BLE001
            r["failures"].append(f"round {i}: type {str(e)[:80]}")
        r["type"].append(time.time() - t)
        r["ok"] += f"neko {i}" in LD.ps(LD.VALUE)
    return r


def summary(r, reps):
    med = lambda v: round(statistics.median(v), 2) if v else None      # noqa: E731
    return {"snapshot_median_s": med(r["snapshot"]), "type_median_s": med(r["type"]), "type_success": f"{r['ok']}/{reps}",
            "failures": r["failures"], **({"start_s": r["start_s"]} if "start_s" in r else {})}


def main():
    if "--list" in sys.argv:
        s = StdioSession([EXE, "serve", "--transport", "stdio"], env=ENV)
        s.start()
        s.handshake(timeout=60)
        for t in s.list_tools():
            print(t["name"], "|", " ".join((t.get("description") or "")[:90].split()))
        s.close()
        return
    tools = sys.argv[1] if len(sys.argv) > 1 else "Snapshot,Type"
    reps = 5
    out = {"repeats": reps, "phases": "separate (Windows-MCP first, then Neko), a fresh Notepad each round"}
    out["windows_mcp"] = summary(run_wmcp(tools, reps), reps)
    subprocess.run(["taskkill", "/IM", "windows-mcp.exe", "/F"], capture_output=True)
    time.sleep(2)
    out["neko_uia"] = summary(run_neko(reps), reps)
    subprocess.run(["taskkill", "/IM", "notepad.exe", "/F"], capture_output=True)
    print(json.dumps(out, indent=1))
    (HERE / "results" / f"windows-mcp-{time.strftime('%Y%m%d-%H%M%S')}.json").write_text(json.dumps(out, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
