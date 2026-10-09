"""Live check (Windows, after the demo suite): a lab agent switches its own model with the jarvis tool switch_model,
the JARVIS endpoint follows on the next calls (kernel audit), and the lane preference is put back afterwards.
Run: python hq\\lab\\tests\\switch_live.py
"""
import shlex
import subprocess
import time

KEY = r"D:\jarvis-secrets\ssh\jarvis_vm_ed25519"
PROMPT = ("Use your jarvis tools: first call models_status, then call switch_model with provider dahl-glm. "
          "After that, answer this: what is 21*21? Reply with only the number.")


def wsl(cmd, timeout=600):
    p = subprocess.run(["wsl.exe", "-d", "JarvisLab", "-u", "agent", "--exec", "bash", "-c", cmd],
                       capture_output=True, timeout=timeout, stdin=subprocess.DEVNULL)
    return p.stdout.decode(errors="replace").replace("\x00", "").strip()


def audit_since(t0):
    py = ("import sqlite3, json, sys\n"
          "db = sqlite3.connect('/home/ubuntu/.jarvis-kernel/state.db')\n"
          "for kind, p in db.execute(\"SELECT kind, payload FROM audit WHERE ts >= ? AND kind IN ('agent_action', 'agent_model_call') ORDER BY seq\", (float(sys.argv[1]),)):\n"
          "    d = json.loads(p)\n"
          "    print(kind, d.get('lane'), d.get('tool') or '', d.get('op') or '', d.get('status') or '', d.get('provider') or '')\n")
    p = subprocess.run(["ssh", "-i", KEY, "-o", "BatchMode=yes", "ubuntu@100.125.91.51", f"python3 -c {shlex.quote(py)} {t0}"],
                       capture_output=True, timeout=60)
    return [line.split() for line in p.stdout.decode(errors="replace").splitlines() if line.strip()]


def prefer(provider):
    body = '{"tool": "models", "op": "prefer", "target": "%s", "params": {}}' % provider
    return wsl("curl -s -m 30 http://100.125.91.51:8790/v1/jarvis/actions -H 'Content-Type: application/json' "
               "-H \"Authorization: Bearer $(cat ~/.jarvis-llm-token)\" -d " + shlex.quote(body))


def check(name, ok, detail=""):
    print(("PASS " if ok else "FAIL ") + name + (f": {detail}" if detail else ""), flush=True)
    return ok


print("reset before:", prefer("dahl-minimax"))
t0 = time.time() - 2
out = wsl("mkdir -p ~/work/switch && timeout 500 ~/.local/bin/hermes chat -q " + shlex.quote(PROMPT) +
          " --oneshot --quiet --in ~/work/switch --max-turns 12 2>&1 | tail -c 300")
rows = audit_since(t0)
actions = [r for r in rows if r[0] == "agent_action"]
calls = [r[-1] for r in rows if r[0] == "agent_model_call"]
switched = [i for i, r in enumerate(rows) if r[0] == "agent_action" and "prefer" in r and "done" in r]
results = [
    check("the agent called models_status", any("status" in r for r in actions), str(actions[:3])),
    check("the agent switched to dahl-glm (allowed by policy)", bool(switched), str(actions)),
    check("model calls before the switch went to dahl-minimax", bool(calls) and calls[0] == "dahl-minimax", str(calls)),
    check("model calls after the switch went to dahl-glm",
          bool(switched) and any(r[0] == "agent_model_call" and r[-1] == "dahl-glm" for r in rows[switched[0]:]), str(calls)),
    check("the agent answered 441", "441" in out, out[-80:].replace("\n", " ")),
]
print("reset after:", prefer("dahl-minimax"))
print(f"passed {sum(results)}/{len(results)}")
