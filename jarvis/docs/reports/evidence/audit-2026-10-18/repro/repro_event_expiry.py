"""R7: an event-triggered (file-arrival) automation keeps firing after its `expires` time; a cron one stops."""
import os, sys, tempfile
sys.path.insert(0, "/src/jarvis/kernel")
from jarvis_kernel.automations import Automations
from jarvis_kernel.jobs import Jobs
from jarvis_kernel.store import Kernel
from jarvis_kernel.tasks import Tasks

tmp = tempfile.TemporaryDirectory()
k = Kernel(os.path.join(tmp.name, "d"))
now = [1_800_000_000.0]
A = Automations(k, Tasks(k), Jobs(k), owner=None, dispatch=None, clock=lambda: now[0])
job = {"kind": "ui_windows", "params": {}, "node": "laptop"}
ev = A.create({"name": "files", "action_kind": "job", "trigger_kind": "event", "event_spec": {"watch": "jarvis-test", "glob": "*.txt"},
               "action": job, "expires": now[0] + 60})
cr = A.create({"name": "cron", "action_kind": "job", "trigger_kind": "interval", "schedule": "60", "action": job, "expires": now[0] + 60})
now[0] += 3600                                     # one hour later: both are past their expiry
k.db.execute("INSERT INTO watch_events(id,node,watch,rel,size,mtime_ns,ts) VALUES ('e1','laptop','jarvis-test','a.txt',1,1,?)", (now[0],))
out = A.tick(now[0])
print("R7 tick:", {k_: v for k_, v in out.items() if k_ in ("fired", "events")})
for a in (A.get(ev["id"]), A.get(cr["id"])):
    print(f"R7 {a['trigger_kind']:8s} expired={now[0] > a['expires']} state={a['state']} runs={[r['status'] for r in a['runs']]}")
k.db.close(); tmp.cleanup()
