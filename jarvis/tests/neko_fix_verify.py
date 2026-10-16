"""The fix half of the troubleshooting check, on THIS laptop, with the node's own code (2026-10-16). Windows, node env.

    D:\\jarvis\\tools\\browser-venv\\Scripts\\python.exe jarvis\\tests\\neko_fix_verify.py

The owner's approval is SIMULATED here (no kernel, no PIN): in real use the kernel holds this edit until the owner approves
it. Steps: confirm the sample app shows its error, back up and fix config.json, dismiss nothing (the app exits after its
dialog), start it again, read its windows, then undo the fix with the backup so the fixture is broken again for next time.
"""
import json
import os
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "node"))
from jarvis_node import diag, repair, winbackend  # noqa: E402

APP = r"D:\jarvis\tests\neko-sample-app\NekoSampleApp.exe"
CFG = r"D:\jarvis\tests\neko-sample-app\config.json"


def windows():
    r = diag.run({"op": "window_text", "target": "NekoSampleApp"})
    data = json.load(open(r["local_result"], encoding="utf-8")) if r.get("local_result") else []
    if r.get("local_result"):
        os.remove(r["local_result"])
    return data


def start():
    subprocess.Popen([APP])
    time.sleep(3)


def main():
    out = {}
    start()
    before = windows()
    out["before"] = [(w["title"], w["dialog"]) for w in before]
    winbackend.click("NekoSampleApp|OK", {})                 # dismiss the error the way the owner would
    time.sleep(1)
    fix = repair.edit_file(CFG, '"port": 80x80', '"port": 8080')
    out["fix"] = {k: fix.get(k) for k in ("ok", "backup", "verified_on_disk", "error")}
    start()
    after = windows()
    out["after"] = [(w["title"], w["dialog"]) for w in after]
    fixed = any(not w["dialog"] and "running on port 8080" in w["title"] for w in after) and not any(w["dialog"] for w in after)
    subprocess.run(["taskkill", "/IM", "NekoSampleApp.exe", "/F"], capture_output=True)
    undo = repair.restore(fix["backup"]) if fix.get("backup") else {"ok": False}
    out["undo"] = {k: undo.get(k) for k in ("ok", "verified_on_disk")}
    out["config_broken_again"] = "80x80" in open(CFG, encoding="utf-8").read()
    ok = any(d for _, d in out["before"]) and fix.get("ok") and fixed and undo.get("ok") and out["config_broken_again"]
    print(json.dumps(out, indent=1))
    print("RESULT", "PASS" if ok else "FAIL", "(approval simulated)")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
