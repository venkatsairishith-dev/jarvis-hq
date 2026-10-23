"""Emergency stop, live (2026-10-16): engage the kernel kill switch from the console, check a PC action is refused while
it is on, lift it at the kernel console (SSH, as documented), check actions work again. Windows, agent-venv."""
import json
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from live_check import jget  # noqa: E402
sys.path.insert(0, str(Path(__file__).resolve().parent))
from console_secret import headers as console_headers  # noqa: E402

SSH = ["ssh", "-i", r"D:\jarvis-secrets\ssh\jarvis_vm_ed25519", "-o", "BatchMode=yes", "ubuntu@100.125.91.51"]


def console(method, name, body=None):
    return jget(f"http://127.0.0.1:8799/api/owner/{name}", method=method, body=body, headers=console_headers() if method != "GET" else None, timeout=30)


out = {}
_, st = console("GET", "state")
out["before_killed"] = st.get("killed")
out["engage"] = console("POST", "kill", {})
time.sleep(1)
out["action_while_stopped"] = console("POST", "pc", {"op": "diag", "check": "disk"})
_, st = console("GET", "state")
out["state_killed"] = st.get("killed")
r = subprocess.run(SSH + ["cd ~/jarvis/kernel && python3 -m jarvis_kernel resume && python3 -m jarvis_kernel status | head -3"],
                   capture_output=True, text=True, timeout=60)
out["resume"] = (r.returncode, r.stdout.strip()[-200:])
time.sleep(2)
code, j = console("POST", "pc", {"op": "diag", "check": "disk"})
res = {}
for _ in range(30):
    time.sleep(1)
    _, res = console("GET", f"job/{(j or {}).get('job')}")
    if (res or {}).get("state") in ("done", "failed", "cancelled", "expired"):
        break
out["action_after_resume"] = (code, (res or {}).get("state"))
print(json.dumps(out, indent=1, default=str))
ok = (out["engage"][0] == 200 and out["action_while_stopped"][0] >= 400 and out["state_killed"] is True
      and out["resume"][0] == 0 and out["action_after_resume"] == (202, "done"))
print("RESULT", "PASS" if ok else "FAIL")
sys.exit(0 if ok else 1)
