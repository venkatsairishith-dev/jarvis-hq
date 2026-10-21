"""Audit repro: the REAL widget answers 'Hey Neko, stop' with ok=True even when main_agent says nothing was stopped
(the G-028 fixture answers ok=False instead), and a run that is still planning has no run file, so it never counts as running."""
import sys, tempfile, json, os
sys.path.insert(0, "/src/jarvis/widget")
import widget_server as W
W.time.sleep = lambda s: None
def fetch(url, timeout=2.5, data=None):
    if url.endswith("/runs"): return {"run_id": "r9"}
    return {"status": "succeeded", "result": {"stopped": False, "message": "Nothing is running."}}
print("do_action(stop) ->", W.do_action({"action": "stop"}, fetch))
d = tempfile.mkdtemp()
json.dump({"plan": {"summary": "old"}, "state": "done", "results": {}}, open(os.path.join(d, "old.json"), "w"))
print("newest_run while a new task is still planning (no file yet) ->", W.newest_run(d)["state"])
