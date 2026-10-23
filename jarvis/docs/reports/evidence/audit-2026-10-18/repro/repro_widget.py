"""Audit repro (read-only): the widget reports a voice task as started while the main agent is still planning;
a plan that fails afterwards is never reported back to the wake listener."""
import sys, time
sys.path.insert(0, "/src/jarvis/widget")
import widget_server as W
W.time.sleep = lambda s: None                        # skip the 0.5 s polls
calls = []
def fetch(url, timeout=2.5, data=None):
    calls.append(url)
    if url.endswith("/runs"): return {"run_id": "abc"}
    return {"status": "running"}                     # run_task is still planning (LLM call)
print("start_task ->", W.start_task("open notepad", "work", fetch), "after", len(calls) - 1, "status polls")
