import json, os, sys, tempfile, threading, urllib.request, urllib.error
from http.server import ThreadingHTTPServer
sys.path.insert(0, "/src/jarvis/kernel")
from jarvis_kernel.store import Kernel
from jarvis_kernel.llm_proxy import make_handler
tmp = tempfile.TemporaryDirectory(); k = Kernel(os.path.join(tmp.name, "kd"))
H, tokens = make_handler(k, [], owner_hermes=None)
srv = ThreadingHTTPServer(("127.0.0.1", 0), H); threading.Thread(target=srv.serve_forever, daemon=True).start()
A = tokens.issue("hermes", "private")
def act(body):
    req = urllib.request.Request(f"http://127.0.0.1:{srv.server_address[1]}/v1/jarvis/actions", data=json.dumps(body).encode(), method="POST",
                                 headers={"Authorization": "Bearer " + A, "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=20) as r: return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e: return e.code, json.loads(e.read())
c, r = act({"tool": "task", "op": "create", "target": "t", "params": {"goal": "summarise", "criteria": ["a summary exists"]}})
tid = r["task"]
pii = "Owner SYNTH-Name lives at 12 Example Road, phone +91 90000 00000, email synth.owner@example.com"
c, r = act({"tool": "task", "op": "dispatch", "target": tid, "params": {"instructions": "Summarise: " + pii, "public_ok": True}})
row = k.db.execute("SELECT node, kind, params FROM jobs WHERE kind='lab_run'").fetchone()
print("R10_private_to_public_lab", json.dumps({"code": c, "status": r.get("status"), "lab_job_node": row and row[0],
      "pii_in_lab_job": bool(row and "synth.owner@example.com" in row[2] and "+91 90000 00000" in row[2])}))
srv.shutdown(); k.db.close(); tmp.cleanup()
