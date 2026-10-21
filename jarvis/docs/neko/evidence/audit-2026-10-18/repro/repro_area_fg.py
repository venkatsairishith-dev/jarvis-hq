"""Read-only reproductions for the F/G audit. Imports repo code; writes nothing into the repo."""
import sys
sys.path.insert(0, "/src")
sys.path.insert(0, "/src/jarvis/widget")

# R1: the Neko icon shows "sleeping" after a failed / held / partial / interrupted main-agent run
import neko_state as ns
status = {"servers": {n: True for n in ns.CORE}, "jarvis": {"reachable": True}, "watchdog": {}}
for st in ("done", "failed", "held", "partial", "cancelled", "interrupted"):
    s = dict(status, task={"id": "r1", "state": st, "summary": "demo task", "age_s": 10, "steps": []})
    out = ns.decide(s, [], None, None, None, None, None)
    print(f"R1 run state={st:12s} -> icon state={out['state']:10s} detail={out.get('detail')!r}")

# R2: a crash-looping program is announced on every check: the 30-minute quiet period never applies to crashes
from plugin.plugins.desktop import monitor as M
t = [1000.0]
rec = [100]
def crashes(after):
    rec[0] += 1
    return [{"record": rec[0], "id": 1000, "app": "Looper.exe"}]
st = M.State(bookmark=1)
m = M.Monitor(st, crashes=crashes, newest=lambda: 1, disks=lambda: {}, port=lambda p: True, services={}, clock=lambda: t[0])
n = []
for i in range(5):
    t[0] += 60
    n.append(len([a for a in m.check() if a.kind == "crash"]))
print("R2 crash alerts per 60 s check for one program crashing once a minute:", n, "(QUIET_S =", M.QUIET_S, ")")

# R3: if the crash collector raises (e.g. Event Log query error), disk/service/stuck checks of that tick never run
def boom(after):
    raise OSError("EvtQuery failed")
m3 = M.Monitor(M.State(bookmark=1), crashes=boom, disks=lambda: {"C:\\": 1.0}, services={}, port=lambda p: True)
try:
    print("R3", m3.check())
except Exception as e:
    print("R3 check() raised", type(e).__name__, "-> the low-disk alert (C: 1.0 GB < 12 GB) was never produced")

# R4: the icon only knows main-agent runs; a kernel lab task / node job the owner started elsewhere does not show as working
s = dict(status, task=None)
print("R4 kernel has running jobs but no main-agent run ->", ns.decide(s, [], None, None, None, None, None)["state"])

# R5: a deliverable whose QA FAILED is shown as "completed" (the page draws a green check; neko.html never reads `passed`)
d = {"file": "/x/deck.pptx", "folder": "/x", "name": "deck.pptx", "passed": False, "age_s": 5}
out = ns.decide(dict(status, task=None), [], None, None, d, None, None)
print("R5 deliverable qa.passed=False ->", out["state"], out.get("detail"), "passed:", out.get("passed"))

# R6: the watchdog never checks or heals the desktop plugin (it hosts the proactive monitor)
sys.path.insert(0, "/src/jarvis/scripts")
import importlib.util
spec = importlib.util.spec_from_file_location("wd", "/src/jarvis/scripts/watchdog.py")
wd = importlib.util.module_from_spec(spec); spec.loader.exec_module(wd)
print("R6 'desktop' in watchdog.PLUGINS:", "desktop" in wd.PLUGINS, "| decide() with only desktop down ->",
      wd.decide([], [p for p in ["desktop"] if p in wd.PLUGINS], {}, 10**6, 10**6))
