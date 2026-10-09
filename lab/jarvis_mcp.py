"""JARVIS tools for lab agents (MCP, stdio). Runs inside the WSL lab box as the agent user.

Every tool is a thin client: it sends the action to the JARVIS kernel with the agent's own token (public lane) and
reports what the kernel decided. The kernel's policy, approvals, limits and audit log decide; nothing here can widen
them. Outputs land on the owner's laptop in D:\\jarvis\\outputs\\<agent>\\ (written by the laptop node, not by this box).
"""
import json
import os
import subprocess
import time
import urllib.error
import urllib.request
import uuid

from mcp.server.mcpserver import MCPServer   # mcp 2.x (FastMCP was renamed)

ENDPOINT = os.environ.get("JARVIS_ENDPOINT", "http://100.125.91.51:8790")
TOKEN = open(os.path.expanduser("~/.jarvis-llm-token")).read().strip()
TASKS = os.path.expanduser("~/work/tasks")
AGENT_CMDS = {
    "hermes": lambda p, d: ["hermes", "chat", "-q", p, "--oneshot", "--quiet", "--in", d, "--max-turns", "40"],
    "opencode": lambda p, d: ["opencode", "run", "--dir", d, p],
    "goose": lambda p, d: ["goose", "run", "--max-turns", "40", "-t", p],
}
mcp = MCPServer("jarvis")


def _call(method, path, body=None):
    r = urllib.request.Request(ENDPOINT + path, data=json.dumps(body).encode() if body is not None else None, method=method,
                               headers={"Content-Type": "application/json", "Authorization": "Bearer " + TOKEN})
    try:
        with urllib.request.urlopen(r, timeout=30) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        try:
            return json.loads(e.read())
        except ValueError:
            return {"status": "refused", "reason": f"HTTP {e.code}"}
    except OSError as e:
        return {"status": "error", "reason": type(e).__name__}


def _act(tool, op, target="", params=None, approval_id=""):
    body = {"tool": tool, "op": op, "target": target, "params": params or {}}
    if approval_id:
        body["approval_id"] = approval_id
    return _call("POST", "/v1/jarvis/actions", body)


def _wait(job, seconds=75):
    """The laptop node picks jobs up about every 15 seconds."""
    end = time.time() + seconds
    while time.time() < end:
        j = _call("GET", "/v1/jarvis/jobs/" + job)
        if j.get("state") in ("done", "failed", "expired"):
            return j
        time.sleep(3)
    return {"state": "still queued", "id": job}


def _say(reply):
    return json.dumps(reply, ensure_ascii=False)


@mcp.tool()
def save_output(name: str, content: str) -> str:
    """Save a result for the owner on their laptop, in D:\\jarvis\\outputs\\<agent>\\<name>.
    name: a plain file name ending in .md, .txt, .html, .json, .csv, .log or .py (no folders). Never overwrites."""
    r = _act("output", "save", name, {"content": content})
    return _say(_wait(r["job"]) if r.get("status") == "queued" else r)


@mcp.tool()
def show_output(name: str) -> str:
    """Open a saved output on the owner's screen (text in Notepad, .html in the browser). At most 12 per hour."""
    r = _act("output", "show", name)
    return _say(_wait(r["job"]) if r.get("status") == "queued" else r)


@mcp.tool()
def pc_action(op: str, target: str, text: str = "", approval_id: str = "") -> str:
    """Act on the owner's Windows PC. op: launch (notepad, calc or mspaint), click or type; target: 'app|element name'.
    The owner must approve every action. First call without approval_id: you get one back and the owner is asked on
    Telegram. After they approve, call again with the SAME op, target and text plus that approval_id. Password
    managers, banking and wallets are always refused."""
    r = _act("ui", op, target, {"text": text} if text else {}, approval_id)
    return _say(_wait(r["job"]) if r.get("status") == "queued" else r)


@mcp.tool()
def models_status() -> str:
    """List the models this lab may use, whether each is reachable, and which one is preferred."""
    return _say(_act("models", "status"))


@mcp.tool()
def switch_model(provider: str) -> str:
    """Prefer another model provider (same lane only) when the current one is failing. At most 20 switches a day."""
    return _say(_act("models", "prefer", provider))


@mcp.tool()
def start_lab_task(agent: str, prompt: str) -> str:
    """Hand a task to another lab agent (hermes, opencode or goose). Returns a task id; check it with lab_task_result."""
    if agent not in AGENT_CMDS:
        return _say({"status": "refused", "reason": "agent must be hermes, opencode or goose"})
    r = _act("lab", "task", agent, {"prompt": prompt})
    if r.get("status") != "allowed":
        return _say(r)
    tid = uuid.uuid4().hex[:10]
    d = os.path.join(TASKS, tid)
    os.makedirs(d)
    env = dict(os.environ, PATH=os.path.expanduser("~/.local/bin") + ":" + os.environ.get("PATH", ""))
    if agent == "goose":
        for line in open(os.path.expanduser("~/.jarvis-goose.env")):
            if "=" in line and not line.startswith("#"):
                k, v = line.rstrip("\n").split("=", 1); env[k] = v
    with open(os.path.join(d, "task.json"), "w") as fh:
        json.dump({"agent": agent, "prompt": prompt, "started": time.time()}, fh)
    log = open(os.path.join(d, "output.log"), "w")
    p = subprocess.Popen(AGENT_CMDS[agent](prompt, d), cwd=d, stdout=log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                         env=env, start_new_session=True)
    with open(os.path.join(d, "pid"), "w") as fh:
        fh.write(str(p.pid))
    return _say({"status": "started", "task_id": tid, "folder": d})


@mcp.tool()
def lab_task_result(task_id: str) -> str:
    """Status and the last lines of output of a task started with start_lab_task."""
    d = os.path.join(TASKS, os.path.basename(task_id))
    if not os.path.isdir(d):
        return _say({"status": "no such task"})
    pid = int(open(os.path.join(d, "pid")).read())
    running = os.path.exists(f"/proc/{pid}")
    tail = open(os.path.join(d, "output.log"), errors="replace").read()[-3000:]
    files = sorted(f for f in os.listdir(d) if f not in ("task.json", "output.log", "pid"))
    return _say({"status": "running" if running else "finished", "files": files[:40], "output_tail": tail})


if __name__ == "__main__":
    mcp.run()
