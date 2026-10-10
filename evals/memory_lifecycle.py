"""Memory lifecycle, live: real Hermes (this checkout's JARVIS memory plugin) + the real JARVIS gateway and kernel, in a
DISPOSABLE profile whose token belongs to its own agent ("memtest"), so the owner's memory is never read or changed.

    sudo -u jarvis-agent -H env HERMES_HOME=<disposable profile> <hermes venv>/bin/python memory_lifecycle.py

Every step is a FRESH session (a resumed one would show a frozen snapshot). After each step the kernel's store is
read directly (not the agent's claim). A decoy entry that shares words with the target must survive deletion, and
after deletion the preference must no longer change the agent's answer.
"""
import json
import os
import sys
import time
import urllib.request

HOME = os.environ["HERMES_HOME"]
TOKEN = open(os.path.join(HOME, "jarvis-memory.token")).read().strip()
GW = "http://127.0.0.1:8790"


def kernel(op, target="", **params):
    r = urllib.request.Request(GW + "/v1/jarvis/actions", method="POST", data=json.dumps(
        {"tool": "memory", "op": op, "target": target, "params": params}).encode(),
        headers={"Content-Type": "application/json", "Authorization": "Bearer " + TOKEN})
    with urllib.request.urlopen(r, timeout=20) as x:
        return json.loads(x.read())


def turn(text, label):
    from run_agent import AIAgent
    a = AIAgent(base_url=GW + "/v1", api_key=TOKEN, provider="custom", model="jarvis", enabled_toolsets=["memory"],
                quiet_mode=True, skip_context_files=True, session_id=f"memtest-{label}-{int(time.time())}", max_iterations=6)
    t0, tries = time.time(), 0
    for tries in range(1, 4):                 # the lab model sometimes ends with no reply at all: ask again, count it
        a = AIAgent(base_url=GW + "/v1", api_key=TOKEN, provider="custom", model="jarvis", enabled_toolsets=["memory"],
                    quiet_mode=True, skip_context_files=True, session_id=f"memtest-{label}-{int(time.time())}-{tries}", max_iterations=6)
        r = a.run_conversation(text) or {}
        if "No reply" not in str(r.get("final_response") or ""):
            break
    tools = [tc["function"]["name"] for m in (r.get("messages") or []) for tc in (m.get("tool_calls") or []) if isinstance(tc, dict)]
    return {"step": label, "tries": tries, "seconds": round(time.time() - t0), "answer": str(r.get("final_response") or r.get("error") or "")[:300],
            "tools_used": tools, "store": [(e["id"], e["version"], e["text"]) for e in kernel("list")["entries"]]}


def main():
    for e in kernel("list")["entries"]:                       # a clean disposable store
        kernel("forget", e["id"])
    decoy = kernel("add", "I prefer dark mode in every app I use")["entry"]
    steps = [
        turn("Remember this lasting preference of mine: name delivered code files in snake_case and always include a README.md. "
             "Save it, then confirm in one line.", "1-save"),
        turn("In one line: what do you know about how I want code files delivered?", "2-recall-fresh-session"),
        turn("Correction to my saved code-delivery preference: no README.md files any more; usage notes go in the module docstring "
             "(snake_case stays). Update the saved entry (do not add a new one) and confirm in one line.", "3-correct"),
        turn("I want a tiny Python file that adds two numbers. Do not write it. In one line each: the file names you would deliver "
             "and where the usage notes would go, following my saved preferences.", "4-apply"),
        turn("Delete my saved preference about code-file delivery (snake_case / docstring). Use its id. Confirm from the tool result "
             "that it was forgotten. Do not touch any other preference.", "5-forget"),
        turn("I want a tiny Python file that adds two numbers. Do not write it. In one line: the file name(s) you would deliver and "
             "where usage notes would go. If I have no saved preference about this, say 'no saved preference'.", "6-after-delete"),
    ]
    store = kernel("list")["entries"]
    report = {"steps": steps,
              "decoy_survived": any(e["id"] == decoy["id"] for e in store),
              "code_pref_gone": not any("snake" in e["text"].lower() or "docstring" in e["text"].lower() for e in store),
              "final_store": [(e["id"], e["text"]) for e in store]}
    for e in store:                                           # leave the disposable store empty
        kernel("forget", e["id"])
    print(json.dumps(report, indent=1))


if __name__ == "__main__":
    main()
