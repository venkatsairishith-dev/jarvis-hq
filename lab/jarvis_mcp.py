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

from mcp.server.mcpserver import Image, MCPServer   # mcp 2.x (FastMCP was renamed)

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


# ---------- the agents' own computer (the "Agent PC" desktop the owner watches) ----------
# These act only inside this lab box, on display :1 (never on the owner's Windows PC). They complement Hermes's
# computer_use (cua-driver, Linux support still alpha) with xdotool, which types into web pages reliably.
DESKTOP_APPS = {"firefox": ["firefox", "--new-window"], "terminal": ["xfce4-terminal"], "files": ["thunar"]}
_DESKTOP_ENV = None


def _denv():
    global _DESKTOP_ENV
    if _DESKTOP_ENV is None:
        uid = os.getuid()
        _DESKTOP_ENV = dict(os.environ, DISPLAY=":1", GNOME_ACCESSIBILITY="1", XDG_RUNTIME_DIR=f"/run/user/{uid}",
                            DBUS_SESSION_BUS_ADDRESS=f"unix:path=/run/user/{uid}/bus")
        _DESKTOP_ENV.pop("WAYLAND_DISPLAY", None)
    return _DESKTOP_ENV


def _x(*args, timeout=30):
    r = subprocess.run(["xdotool", *args], env=_denv(), capture_output=True, text=True, timeout=timeout)
    return r.returncode, (r.stdout or "").strip()


def _focus(window):
    """Bring the first window whose title contains `window` to the front (no-op when empty)."""
    if not window:
        return True
    code, ids = _x("search", "--onlyvisible", "--name", window)
    if code or not ids:
        return False
    _x("windowactivate", "--sync", ids.splitlines()[0])
    return True


@mcp.tool()
def desktop_open(target: str) -> str:
    """Open something on YOUR OWN computer (the Agent PC the owner watches): a web address starting with http:// or
    https:// opens in Firefox; 'firefox', 'terminal' or 'files' opens that app. Windows stay open after you finish."""
    if target.startswith(("http://", "https://")):
        cmd = DESKTOP_APPS["firefox"] + [target]
    elif target in DESKTOP_APPS:
        cmd = DESKTOP_APPS[target]
    else:
        return _say({"status": "refused", "reason": "open a http(s) address or one of: firefox, terminal, files"})
    subprocess.Popen(cmd, env=_denv(), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
    time.sleep(4)
    return _say({"status": "opened", "target": target})


@mcp.tool()
def desktop_windows() -> str:
    """List the windows open on your computer (title only), to know what to focus."""
    code, ids = _x("search", "--onlyvisible", "--name", ".")
    titles = []
    for wid in (ids.splitlines() if not code else [])[:40]:
        _, name = _x("getwindowname", wid)
        if name and name not in ("Desktop", "xfce4-panel"):
            titles.append(name)
    return _say({"windows": titles})


@mcp.tool()
def desktop_click(x: int, y: int, window: str = "") -> str:
    """Click at screen pixel (x, y) of your 1600x900 screen (use desktop_screenshot to see it). window: optional part
    of a window title to bring to the front first."""
    if not (0 <= x < 1600 and 0 <= y < 900):
        return _say({"status": "refused", "reason": "outside the 1600x900 screen"})
    if not _focus(window):
        return _say({"status": "no such window", "window": window})
    _x("mousemove", str(x), str(y), "click", "1")
    return _say({"status": "clicked", "x": x, "y": y})


@mcp.tool()
def desktop_type(text: str, window: str = "") -> str:
    """Type text with the keyboard into whatever has focus (click the field first). window: optional title part."""
    if len(text) > 2000:
        return _say({"status": "refused", "reason": "at most 2000 characters at a time"})
    if not _focus(window):
        return _say({"status": "no such window", "window": window})
    _x("type", "--delay", "25", text, timeout=120)
    return _say({"status": "typed", "chars": len(text)})


@mcp.tool()
def desktop_key(keys: str, window: str = "") -> str:
    """Press keys, e.g. 'Return', 'ctrl+l', 'ctrl+t', 'Tab', 'Escape', 'alt+F4'. window: optional title part."""
    if not _focus(window):
        return _say({"status": "no such window", "window": window})
    code, _ = _x("key", *keys.split())
    return _say({"status": "pressed" if code == 0 else "failed", "keys": keys})


@mcp.tool()
def desktop_screenshot() -> Image:
    """A picture of your whole screen (1600x900). Use it to find where to click."""
    png = os.path.join(TASKS, "..", "last-screen.png")
    os.makedirs(TASKS, exist_ok=True)
    subprocess.run(f"xwd -root -silent | convert xwd:- png:{png}", shell=True, env=_denv(), timeout=30)
    return Image(path=png)


if __name__ == "__main__":
    mcp.run()
