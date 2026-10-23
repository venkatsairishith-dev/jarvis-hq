"""Audit reproductions (read-only against the repo; all state in a temp dir). Run from /src in the neko-test container."""
import json
import os
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, "/src")
sys.path.insert(0, "/src/jarvis/kernel")
sys.path.insert(0, "/src/jarvis/node")
tmp = Path(tempfile.mkdtemp())
os.environ["NEKO_STORAGE_SELECTED_ROOT"] = str(tmp)

print("=== R1: node ui_windows result has no hwnd; plugin 'new window' check depends on it")
from jarvis_node import jobs as nj
fake = lambda: [{"hwnd": 11, "pid": 1, "process": "explorer", "title": "Desktop", "class": "x", "foreground": False, "minimized": False, "x": 0, "y": 0, "w": 1, "h": 1}]
r = nj.ui_windows({}, lister=fake)
print("ui_windows keys:", sorted(r["windows"][0].keys()))
before = {w.get("hwnd") for w in r["windows"]}
after = nj.ui_windows({}, lister=lambda: fake() + [{"hwnd": 22, "pid": 2, "process": "POWERPNT", "title": "solar-deck - PowerPoint",
                                                   "class": "x", "foreground": True, "minimized": False, "x": 0, "y": 0, "w": 1, "h": 1}])
stem = "solar-deck"
hit = [w for w in after["windows"] if w.get("hwnd") not in before and stem[:25] in str(w.get("title", "")).lower()]
print("before set:", before, "| new PowerPoint window counted as new:", bool(hit))

print("\n=== R2: a single PC task takes over a running mission's lock after 660 s (budget of the CALLER is used)")
from plugin.plugins.desktop import coordinator as C
lock = tmp / "pc_task.lock"
lock.write_text(json.dumps({"task": "m-mission", "pid": os.getpid(), "at": time.time() - 700}))   # mission started 700 s ago, owner alive
try:
    C.acquire("t-single", lock, budget=C.TOTAL_BUDGET_S)
    print("single task acquired the lock; holder now:", json.loads(lock.read_text())["task"])
except C.Busy as e:
    print("Busy:", e)
lock.write_text(json.dumps({"task": "m-mission", "pid": os.getpid(), "at": time.time() - 700}))
try:
    C.acquire("m-other", lock, budget=30 * 60)
    print("mission-budget caller acquired")
except C.Busy as e:
    print("with the mission's 30-min budget the same lock is respected:", str(e)[:60])

print("\n=== R3: browser 'type' by element ref skips the password-field refusal")
from jarvis_node import browserext as B
snap = "- Page URL: https://login.example.com/\n- Page Title: Sign in\n- textbox \"Email\" [ref=e3]\n- textbox \"Password\" [ref=e5]\n- button \"Sign in\" [ref=e7]"
calls = []


class Sess:
    def call(self, tool, args, timeout=45.0):
        calls.append((tool, {k: v for k, v in args.items() if k != "text"}))
        return snap


print("auth_required(snapshot):", B.auth_required(snap, "https://login.example.com/"))
try:
    B.act(Sess(), "type", "", "Password", "hunter2", {"dir": "Default"})
    print("by name: typed (unexpected)")
except B.BrowserError as e:
    print("by name 'Password': refused ->", str(e)[:70])
calls.clear()
out = B.act(Sess(), "type", "", "e5", "hunter2", {"dir": "Default"})
print("by ref 'e5':", {k: out[k] for k in ("ok", "typed_chars", "ref")}, "tools called:", [c[0] for c in calls])

print("\n=== R4: the build-failure request through decide() (fast path; model down = fallback)")
from plugin.plugins.desktop import decide as D
req = "Inspect my project, identify why the build is failing, fix the cause, run the tests, and summarize the verified result."
print("fast_path:", D.fast_path(req, []))
p = D.decide(req, [], post=None, timeout=2)
print("decide (no local model on this server):", p.status, p.problems)
for model_plan in ([{"tool": "troubleshoot", "app": "project"}], [{"tool": "troubleshoot", "app": ""}], [{"tool": "research", "query": "why is the build failing"}],
                   [{"tool": "check_pc", "action": "processes"}], [{"tool": "answer"}]):
    p = D.validate(json.dumps({"steps": model_plan}), [], req)
    print(" model says", model_plan, "->", p.status, [(s["tool"], s.get("app") or s.get("query") or s.get("check")) for s in p.steps], p.question or "")
print("tools the decider can choose:", sorted(D.TOOLS))

print("\n=== R5: mission planner drops a clarification question (plan_fn returns [] for status != ok)")
plan = D.decide("open that", [], post=lambda body: json.dumps({"steps": [{"tool": "clarify", "question": "What should I open?"}]}))
plan_fn = lambda goal, ctx: (lambda p: p.steps if p.status == "ok" else [])(plan)
print("decide:", plan.status, repr(plan.question), "| what the mission gets:", plan_fn("open that", ""))

print("\n=== R6: coordinator.verify passes browser click/type, window, press_key and check_pc on ok=True alone")
for st in ({"tool": "browser", "action": "click", "target": "Submit"}, {"tool": "window", "action": "minimize", "app": "notepad"},
           {"tool": "press_key", "key": "ctrl+s", "app": "word"}, {"tool": "check_pc", "check": "disk"}):
    print(st["tool"], st.get("action") or st.get("key") or st.get("check"), "->", C.verify(st, {"ok": True}))
print("open_website with node verified=window (any foreground Chrome window):",
      C.verify({"tool": "open_website", "profile": "Profile 1"}, {"ok": True, "profile": "Profile 1", "verified": "window", "profile_name_matches": False}))
