"""Live check (Windows): a lab agent's request to act on the owner's PC waits for the owner's Telegram approval, runs
once after it, and the approval cannot be reused. Needs the owner to send /approve <id> then CONFIRM on Telegram.
Run: python hq\\lab\\tests\\approval_live.py [minutes to wait, default 15]
"""
import json
import shlex
import subprocess
import sys
import time

KEY = r"D:\jarvis-secrets\ssh\jarvis_vm_ed25519"
WAIT_MIN = float(sys.argv[1]) if len(sys.argv) > 1 else 15


def wsl(cmd, timeout=120):
    p = subprocess.run(["wsl.exe", "-d", "JarvisLab", "-u", "agent", "--exec", "bash", "-c", cmd],
                       capture_output=True, timeout=timeout, stdin=subprocess.DEVNULL)
    return p.stdout.decode(errors="replace").replace("\x00", "").strip()


def act(body):
    out = wsl("curl -s -m 30 http://100.125.91.51:8790/v1/jarvis/actions -H 'Content-Type: application/json' "
              "-H \"Authorization: Bearer $(cat ~/.jarvis-llm-token)\" -d " + shlex.quote(json.dumps(body)))
    return json.loads(out or "{}")


def job(jid):
    out = wsl("curl -s -m 30 http://100.125.91.51:8790/v1/jarvis/jobs/" + jid + " -H \"Authorization: Bearer $(cat ~/.jarvis-llm-token)\"")
    return json.loads(out or "{}")


def vm_sql(query, arg):
    py = ("import sqlite3, sys\ndb = sqlite3.connect('/home/ubuntu/.jarvis-kernel/state.db')\n"
          "print(db.execute(sys.argv[1], (sys.argv[2],)).fetchone())")
    p = subprocess.run(["ssh", "-i", KEY, "-o", "BatchMode=yes", "ubuntu@100.125.91.51",
                        f"python3 -c {shlex.quote(py)} {shlex.quote(query)} {shlex.quote(arg)}"], capture_output=True, timeout=60)
    return p.stdout.decode(errors="replace").strip()


def check(name, ok, detail=""):
    print(("PASS " if ok else "FAIL ") + name + (f": {detail}" if detail else ""), flush=True)
    return ok


request = {"tool": "ui", "op": "launch", "target": "notepad", "params": {}}
r = act(request)
aid = r.get("approval_id", "")
results = [check("the request is held for the owner's approval", r.get("status") == "needs_approval" and bool(aid), str(r))]
if not aid:
    sys.exit(1)
time.sleep(25)                                        # the Telegram bridge sends approval notices on its loop
results.append(check("the owner got a Telegram notice", "1" in vm_sql("SELECT count(*) FROM tg_approval_notices WHERE id=?", aid)))
results.append(check("acting with an unapproved id is refused", act(dict(request, approval_id=aid)).get("status") != "queued"))
print(f"WAITING up to {WAIT_MIN:.0f} min: on Telegram send /approve {aid} and then CONFIRM", flush=True)
state = ""
end = time.time() + WAIT_MIN * 60
while time.time() < end:
    state = vm_sql("SELECT state FROM approvals WHERE id=?", aid)
    if "approved" in state or "denied" in state:
        break
    time.sleep(10)
if not check("the owner approved on Telegram", "approved" in state, state):
    print("NOT TESTED further: no approval within the wait (needs the owner).")
    sys.exit(2)
r2 = act(dict(request, approval_id=aid))
results.append(check("after approval the action is queued for the laptop", r2.get("status") == "queued", str(r2)))
done = {}
for _ in range(30):
    done = job(r2.get("job", ""))
    if done.get("state") in ("done", "failed", "expired"):
        break
    time.sleep(3)
results.append(check("the laptop ran it (Notepad opened)", done.get("state") == "done" and (done.get("result") or {}).get("ok"), str(done)))
results.append(check("the same approval cannot be used twice", act(dict(request, approval_id=aid)).get("status") != "queued"))
print(f"passed {sum(results)}/{len(results)}")
