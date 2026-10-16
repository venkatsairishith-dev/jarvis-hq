"""Guided live checks that need the OWNER (2026-10-16). Windows, neko-venv. Runs in a visible window and tells the owner
exactly what to do; records what actually happened to D:\\jarvis\\outputs\\neko\\guided-owner-test.json.

  1. Emergency stop: if the kill switch is on, an SSH window opens at the kernel console; the owner types RESUME there.
  2. Approval: Neko's troubleshooter proposes the sample app's config fix; the owner approves it in the JARVIS console with
     the PIN; the fix is applied and verified, then undone with its backup (the sample stays broken for next time).
  3. Denial: the same fix is proposed again; the owner presses Deny; the config must stay unchanged.
  4. "Hey Neko": five real attempts, two near misses ("Hey Nico"), one "never mind" cancel; counts before and after.
Nothing here types a PIN, a password or RESUME for the owner. No audio is recorded by this script.
"""
import json
import os
import subprocess
import sys
import time
import webbrowser
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from live_check import jget, run_entry  # noqa: E402

H = {"X-Jarvis-Console": "1"}
CFG = r"D:\jarvis\tests\neko-sample-app\config.json"
OUT = Path(r"D:\jarvis\outputs\neko\guided-owner-test.json")
R = {"started": time.strftime("%Y-%m-%d %H:%M:%S")}


def save():
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(R, indent=1, default=str), encoding="utf-8")


def say(msg):
    print("\n" + "=" * 70 + "\n" + msg + "\n" + "=" * 70, flush=True)


def console(method, name, body=None, timeout=30):
    return jget(f"http://127.0.0.1:8799/api/owner/{name}", method=method, body=body, headers=H, timeout=timeout)


def wake():
    return jget("http://127.0.0.1:48945/status", timeout=5)[1] or {}


def step1():
    _, st = console("GET", "state")
    if not st.get("killed"):
        R["estop_resume"] = "kill switch was already off"
        return
    say("STEP 1 of 4: the emergency stop is ON (I engaged it to test it).\n"
        "An SSH window to the JARVIS kernel is opening. Type RESUME there and press Enter.")
    subprocess.Popen(["powershell", "-NoExit", "-Command",
                      'ssh -t -i D:\\jarvis-secrets\\ssh\\jarvis_vm_ed25519 ubuntu@100.125.91.51 '
                      '"cd ~/jarvis/kernel && python3 -m jarvis_kernel resume"'], creationflags=subprocess.CREATE_NEW_CONSOLE)
    t0 = time.time()
    while time.time() - t0 < 1800:
        time.sleep(3)
        if not console("GET", "state")[1].get("killed"):
            R["estop_resume"] = f"lifted by the owner after {int(time.time() - t0)} s"
            say("Thank you: the kill switch is off. You can close the SSH window.")
            return
    R["estop_resume"] = "not lifted within 30 minutes"
    save()
    sys.exit(1)


def troubleshoot(note):
    say(note)
    webbrowser.open("http://127.0.0.1:8799/")
    return run_entry("desktop", "run_troubleshooter", {"app": "NekoSampleApp", "start_name": "Neko Sample App"}, wait=420)


def step2():
    subprocess.run(["taskkill", "/IM", "NekoSampleApp.exe", "/F"], capture_output=True)
    before = open(CFG, encoding="utf-8").read()
    r = troubleshoot("STEP 2 of 4: APPROVE.\nNeko will show the sample app's error and propose a fix for config.json.\n"
                     "In the JARVIS console (opening now at http://127.0.0.1:8799), find the waiting approval\n"
                     "'repair edit_file ... config.json' and APPROVE it with your PIN. (You have 5 minutes.)")
    d = r.get("data") or {}
    after = open(CFG, encoding="utf-8").read()
    R["approve"] = {"outcome": d.get("outcome"), "cause": d.get("cause"), "verified_cause": d.get("verified"),
                    "steps": [s["state"] for s in d.get("steps") or []], "backup": d.get("backup"), "verification": d.get("verification"),
                    "config_was_broken": "80x80" in before, "config_fixed": '"port": 8080' in after}
    subprocess.run(["taskkill", "/IM", "NekoSampleApp.exe", "/F"], capture_output=True)
    if d.get("backup"):
        _, j = console("POST", "pc", {"op": "repair", "action": "restore", "target": d["backup"], "args": {}})
        res = {}
        for _ in range(60):
            time.sleep(1)
            _, res = console("GET", f"job/{(j or {}).get('job')}")
            if (res or {}).get("state") in ("done", "failed", "cancelled", "expired"):
                break
        R["rollback"] = {"job_state": (res or {}).get("state"), "config_broken_again": "80x80" in open(CFG, encoding="utf-8").read(),
                         "result_ok": ((res or {}).get("result") or {}).get("ok")}
    say("Step 2 recorded: " + json.dumps(R["approve"].get("outcome")))
    save()


def step3():
    before = open(CFG, encoding="utf-8").read()
    r = troubleshoot("STEP 3 of 4: DENY.\nThe same fix will be proposed again. This time press DENY in the JARVIS console.")
    d = r.get("data") or {}
    subprocess.run(["taskkill", "/IM", "NekoSampleApp.exe", "/F"], capture_output=True)
    R["deny"] = {"outcome": d.get("outcome"), "steps": [s["state"] for s in d.get("steps") or []],
                 "config_unchanged": open(CFG, encoding="utf-8").read() == before}
    save()


def wait_change(key, before, secs):
    t0 = time.time()
    while time.time() - t0 < secs:
        time.sleep(0.5)
        s = wake()
        if (s.get("counts") or {}).get(key, 0) > before:
            return s
    return wake()


def step4():
    s0 = wake()
    R["wake_before"] = {"counts": s0.get("counts"), "audio": s0.get("audio")}
    attempts = []
    say("STEP 4 of 4: \"HEY NEKO\" with your real voice. Sit where you usually sit.\n"
        "For each prompt below, say the phrase ONCE in your normal voice, then wait for the next prompt.")
    for i in range(1, 6):
        c0 = wake().get("counts") or {}
        say(f"Attempt {i} of 5 — say now:   Hey Neko, what is the time?")
        s = wait_change("detections", c0.get("detections", 0), 20)
        woke = (s.get("counts") or {}).get("detections", 0) > c0.get("detections", 0)
        if woke:                                          # let it record, transcribe and hand over
            t0 = time.time()
            while time.time() - t0 < 40 and wake().get("state") != "wake_listening":
                time.sleep(0.5)
        s = wake()
        attempts.append({"attempt": i, "woke": woke, "last": s.get("last"),
                         "task_started": (s.get("counts") or {}).get("tasks_started", 0) > c0.get("tasks_started", 0)})
        print("   ->", "WOKE" if woke else "did not wake", (s.get("last") or {}).get("what") if woke else "", flush=True)
        time.sleep(4)
    near = []
    for i in range(1, 3):
        c0 = wake().get("counts") or {}
        say(f"Near miss {i} of 2 — say now:   Hey Nico")
        s = wait_change("detections", c0.get("detections", 0), 12)
        near.append({"attempt": i, "woke": (s.get("counts") or {}).get("detections", 0) > c0.get("detections", 0)})
        if near[-1]["woke"]:                              # do not start a task from a near miss: cancel it
            jget("http://127.0.0.1:48945/cancel", method="POST", body={}, timeout=5)
        print("   ->", "woke (false wake)" if near[-1]["woke"] else "stayed asleep", flush=True)
        time.sleep(4)
    c0 = wake().get("counts") or {}
    say("Cancel test — say:   Hey Neko   ... wait for the chime ...   never mind")
    s = wait_change("cancelled", c0.get("cancelled", 0), 30)
    R["cancel_by_voice"] = {"cancelled": (s.get("counts") or {}).get("cancelled", 0) > c0.get("cancelled", 0), "last": s.get("last")}
    s1 = wake()
    R["wake_attempts"] = attempts
    R["near_misses"] = near
    R["wake_after"] = {"counts": s1.get("counts"), "audio": s1.get("audio")}
    _, status = jget("http://127.0.0.1:48940/api/status", timeout=20)
    R["newest_task_after"] = {k: (status.get("task") or {}).get(k) for k in ("id", "state", "summary", "age_s")}
    save()


if __name__ == "__main__":
    only = sys.argv[1:] or ["1", "2", "3", "4"]
    for n, fn in (("1", step1), ("2", step2), ("3", step3), ("4", step4)):
        if n in only:
            fn()
            save()
    say(f"ALL DONE. Results: {OUT}\nYou can close this window.")
    R["finished"] = time.strftime("%Y-%m-%d %H:%M:%S")
    save()
    input("Press Enter to close.")
