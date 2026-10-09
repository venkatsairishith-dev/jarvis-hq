"""Live check (Windows, after the demo suite): the owner takes the Agent PC from a REAL agent mid-task and hands it back.

Starts a Hermes task that uses the desktop step by step, waits for its first step, presses Take control through the
hub's own endpoint, checks the agent is frozen and does nothing for 30 s, presses Continue task, and checks the agent
finishes what was left and was told to look at the screen again. Run: python hq\\lab\\tests\\takeover_live.py
"""
import json
import shlex
import subprocess
import sys
import time
import urllib.request

HUB = "http://127.0.0.1:8088/control/"
HEADERS = {"Host": "hub.localhost:8088", "X-Jarvis-Hub": "1"}
PROMPT = ("Work on your own computer, one step at a time, in this order: 1) desktop_open "
          "https://en.wikipedia.org/wiki/Estonia 2) desktop_windows 3) read the first sentence of that article with any "
          "tool you have 4) desktop_windows again 5) save_output named estonia-note.md containing that first sentence "
          "6) reply with the word DONE.")


def ctl(method, path):
    r = urllib.request.Request(HUB + path, method=method, headers=HEADERS)
    return json.loads(urllib.request.urlopen(r, timeout=20).read())


def wsl(cmd, timeout=60):
    p = subprocess.run(["wsl.exe", "-d", "JarvisLab", "-u", "agent", "--exec", "bash", "-c", cmd],
                       capture_output=True, timeout=timeout, stdin=subprocess.DEVNULL)
    return p.stdout.decode(errors="replace").replace("\x00", "").strip()


def actions_since(t0):
    code = ("import json,sys\nt0=float(sys.argv[1])\nfor l in open('/home/agent/lab/activity.jsonl'):\n"
            " try: e=json.loads(l)\n except ValueError: continue\n"
            " if float(e.get('ts') or 0)>=t0: print(e.get('agent'), e.get('tool'), str(e.get('detail',''))[:40])")
    out = wsl(f"python3 -c {shlex.quote(code)} {t0}")
    return [line for line in out.splitlines() if line.strip()]


def check(name, ok, detail=""):
    print(("PASS " if ok else "FAIL ") + name + (f": {detail}" if detail else ""), flush=True)
    return ok


results = []
if ctl("GET", "state")["mode"] != "agent":
    ctl("POST", "continue")
t0 = time.time()
wsl("mkdir -p ~/work/takeover && cd ~/work/takeover && setsid -f ~/.local/bin/hermes chat -q " + shlex.quote(PROMPT) +
    " --oneshot --quiet --in ~/work/takeover --max-turns 30 > ~/work/takeover/out.log 2>&1 < /dev/null")
task = None
for _ in range(120):
    s = ctl("GET", "state")
    mine = [t for t in s["tasks"] if "Estonia" in t["goal"]]
    if mine and mine[0]["last"]:
        task = mine[0]
        break
    time.sleep(3)
if not check("agent task started and took a step", task is not None):
    sys.exit(1)
print(f"     {task['agent']} pid {task['pid']}, last step {task['last']}")

s = ctl("POST", "take")
t_take = time.time()
mine = [t for t in s["tasks"] if t["pid"] == task["pid"]]
results.append(check("Take control: mode is owner", s["mode"] == "owner"))
results.append(check("Take control: the task shows as paused", bool(mine and mine[0]["paused"])))
states = wsl(f"ps -o stat= -p {task['pid']}; ps -o stat= --ppid {task['pid']}").split()
results.append(check("Take control: the agent and its helpers are stopped (T)", bool(states) and all(x.startswith("T") for x in states), " ".join(states)))
time.sleep(30)
during = actions_since(t_take)
results.append(check("No agent actions while the owner has control (30 s)", not during, "; ".join(during)))

s = ctl("POST", "continue")
t_back = time.time()
results.append(check("Continue task: mode is agent and returned_at set", s["mode"] == "agent" and bool(s.get("returned_at"))))
finished = False
for _ in range(240):
    if not any(t["pid"] == task["pid"] for t in ctl("GET", "state")["tasks"]):
        finished = True
        break
    time.sleep(3)
results.append(check("The agent finished after Continue task", finished, f"{time.time() - t_back:.0f} s after hand-back"))
after = actions_since(t_back)
results.append(check("The agent went on with what was left (actions after hand-back)", bool(after), "; ".join(after[:6])))
results.append(check("The note reached the laptop", any("save_output:done" in a and "estonia" in a for a in after)))
noted = wsl("grep -rl 'The owner had control of your computer' ~/.hermes 2>/dev/null | grep -v '/logs/' | head -3")
results.append(check("The agent was told the owner had control (in its session)", bool(noted), noted.replace("\n", ", ")))
print("output tail:", wsl("tail -c 300 ~/work/takeover/out.log").replace("\n", " "))
print(f"passed {sum(results)}/{len(results)}")
