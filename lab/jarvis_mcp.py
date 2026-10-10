"""JARVIS tools for agents (MCP, stdio).

Profile "lab" (default): lab agents in the WSL lab box, public-lane token; includes their own computer (the Agent PC).
Profile "owner" (JARVIS_MCP_PROFILE=owner): the owner's own Hermes on the server, private-lane token; adds reading the
owner's folders, drops the lab-only tools.
Every tool is a thin client: it sends the action to the JARVIS kernel with the agent's own token and reports what the
kernel decided. The kernel's policy, approvals, limits and audit log decide; nothing here can widen them. Outputs land
on the owner's laptop in D:\\jarvis\\outputs\\<agent>\\ (written by the laptop node).
"""
import json
import os
import subprocess
import time
import urllib.error
import urllib.request
import uuid

from mcp.server.mcpserver import Image, MCPServer   # mcp 2.x (FastMCP was renamed)

PROFILE = os.environ.get("JARVIS_MCP_PROFILE", "lab")
ENDPOINT = os.environ.get("JARVIS_ENDPOINT", "http://100.125.91.51:8790")
TOKEN = open(os.path.expanduser("~/.jarvis-llm-token")).read().strip()
TASKS = os.path.expanduser("~/work/tasks")
AGENT_CMDS = {
    "hermes": lambda p, d: ["hermes", "chat", "-q", p, "--oneshot", "--quiet", "--in", d, "--max-turns", "40"],
    "opencode": lambda p, d: ["opencode", "run", "--dir", d, p],
    "goose": lambda p, d: ["goose", "run", "--max-turns", "40", "-t", p],
}
mcp = MCPServer("jarvis")


def tool(*profiles):
    """Register the function as a tool only in the given profiles."""
    def deco(fn):
        return mcp.tool()(fn) if PROFILE in profiles else fn
    return deco


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


CONTROL_STATE = "/run/jarvis-lab/control.json"      # written by lab_control.py (root): who has the Agent PC
ACTIVITY = os.path.expanduser(os.environ.get("JARVIS_ACTIVITY", "~/lab/activity.jsonl"))
STARTED = time.time()
_seen_return = [None]
_who = []


def _caller():
    """Which agent started this tool server (for the hub's activity feed): its program name, never its prompt."""
    if not _who:
        try:
            argv = [a.decode(errors="replace") for a in open(f"/proc/{os.getppid()}/cmdline", "rb").read().split(b"\0") if a]
            head = " ".join(os.path.basename(a) for a in argv[:2])
            _who.append(next((k for k in ("hermes", "opencode", "goose") if k in head), "agent"))
        except OSError:
            _who.append("agent")
    return _who[0]


def _log(tool, detail=""):
    try:
        with open(ACTIVITY, "a") as fh:
            fh.write(json.dumps({"ts": time.time(), "time": time.strftime("%H:%M:%S"), "agent": _caller(), "tool": tool,
                                 "detail": str(detail)[:160], "ppid": os.getppid()}) + "\n")
    except OSError:
        pass


def _control():
    try:
        with open(CONTROL_STATE) as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {"mode": "agent"}


def _say(reply):
    """After a takeover the next reply carries the owner's hand-back note once, so the agent re-checks the screen.
    Agents started after the owner handed back never get it."""
    s = _control()
    back = s.get("returned_at")
    if back and back > STARTED and back != _seen_return[0]:
        _seen_return[0] = back
        start = s.get("owner_since") or back
        if isinstance(reply, dict):
            reply = {**reply, "note": f"The owner had control of your computer from {time.strftime('%H:%M:%S', time.localtime(start))} "
                                      f"to {time.strftime('%H:%M:%S', time.localtime(back))} and may have changed things. Look again "
                                      "(desktop_windows or desktop_screenshot) before you continue with what is left of your task."}
    return json.dumps(reply, ensure_ascii=False)


def _owner_has_control():
    return _control().get("mode") == "owner"


def _outcome(tool, r):
    """Log what the owner's laptop reported, not just that the agent asked (hub activity feed, demo checker)."""
    res = (r or {}).get("result") or {}
    if r.get("state") == "done" and res.get("ok"):
        _log(tool + ":done", res.get("saved") or res.get("shown") or res.get("opened") or res.get("folder") or res.get("done") or "")
    else:
        _log(tool + ":failed", r.get("state") or r.get("status") or res.get("error") or "")
    return r


OWNER_BUSY = {"status": "waiting", "reason": "the owner has taken control of this computer; wait a minute, then call desktop_windows to see if they handed it back"}


@tool("lab", "owner")
def save_output(name: str, content: str) -> str:
    """Give a file to the OWNER: saves it on the owner's laptop in D:\\jarvis\\outputs\\<agent>\\<name>.
    This is the ONLY way to deliver a result to the owner; write_file only writes inside your own computer and the
    owner never sees it. Pass the full text as content. name: a plain file name ending in .md, .txt, .html, .json,
    .csv, .log or .py (no folders). Never overwrites."""
    _log("save_output", name)
    r = _act("output", "save", name, {"content": content})
    return _say(_outcome("save_output", _wait(r["job"]) if r.get("status") == "queued" else r))


@tool("lab", "owner")
def show_output(name: str) -> str:
    """Open on the owner's screen a file you ALREADY delivered with save_output (use the same name). Files written
    with write_file cannot be shown. Text opens in Notepad, .html in the browser. At most 12 per hour."""
    _log("show_output", name)
    r = _act("output", "show", name)
    return _say(_outcome("show_output", _wait(r["job"]) if r.get("status") == "queued" else r))


@tool("lab", "owner")
def pc_action(op: str, target: str, text: str = "", approval_id: str = "") -> str:
    """Act on the owner's Windows PC. op: launch (notepad, calc or mspaint), click or type; target: 'app|element name'.
    The owner must approve every action. First call without approval_id: you get one back and the owner is asked on
    Telegram. After they approve, call again with the SAME op, target and text plus that approval_id. Password
    managers, banking and wallets are always refused."""
    _log("pc_action", f"{op} {target}" + (" (approved)" if approval_id else ""))
    r = _act("ui", op, target, {"text": text} if text else {}, approval_id)
    if r.get("status") == "queued":
        return _say(_outcome("pc_action", _wait(r["job"])))
    return _say(r)


def _pc(op, target, path="", approval_id=""):
    """Open something on the owner's laptop through the kernel (pc.<op>): the owner's own agent at once, a lab agent
    after the owner approves on Telegram (call again with the same arguments plus approval_id)."""
    _log(op, f"{target} {path}".strip() + (" (approved)" if approval_id else ""))
    r = _act("pc", op, target, {"path": path} if path else {}, approval_id)
    if r.get("status") == "queued":
        return _say(_outcome(op, _wait(r["job"])))
    return _say(r)


@tool("lab", "owner")
def open_app(name: str, approval_id: str = "") -> str:
    """Open an app on the OWNER's Windows laptop by its Start-menu name, e.g. 'Spotify', 'Google Chrome', 'Word',
    'Notepad'. If the name is unclear you get the closest names back: ask the owner which one. Password managers,
    wallets and banking apps are never opened."""
    return _pc("open_app", name, "", approval_id)


@tool("lab", "owner")
def open_folder(folder: str, path: str = "", approval_id: str = "") -> str:
    """Open a folder on the OWNER's laptop in File Explorer. folder: Downloads, Documents, Desktop, Pictures, Videos or
    Music. path: optional sub-folder inside it, a relative path or just a name to look for (e.g. 'invoices' finds
    'Downloads\\Invoices 2026'). If several match you get the closest names back: ask the owner which one."""
    return _pc("open_folder", folder, path, approval_id)


@tool("lab", "owner")
def open_file(folder: str, path: str, approval_id: str = "") -> str:
    """Open a document or media file on the OWNER's laptop with its usual app (PDF, Word, Excel, images, video,
    music, text...). folder: Downloads, Documents, Desktop, Pictures, Videos or Music; path: the file's name or relative
    path. Programs and scripts are never opened."""
    return _pc("open_file", folder, path, approval_id)


@tool("owner")
def list_folder(folder: str, path: str = "") -> str:
    """List what is in one of the OWNER's folders (Downloads, Documents, Desktop, Pictures, Videos, Music), newest
    first, up to 200 entries; folders end with a backslash. Use it before open_folder/open_file when unsure of a name."""
    return _pc("list_folder", folder, path)


@tool("owner")
def task_start(goal: str, criteria: list[str], plan: list[str] | None = None, request: str = "", max_attempts: int = 3,
               budget_minutes: int = 120) -> str:
    """Open an outcome task for a request that needs real work (not a quick answer). goal: the outcome in one sentence.
    criteria: 2-8 acceptance criteria anyone can CHECK (e.g. 'all pages return 200 and show no console errors'), not
    vague wishes. plan: short steps. request: the owner's words. The owner sees the task live in Mission Control.
    Returns the task id."""
    _log("task_start", goal)
    return _say(_act("task", "create", goal, {"goal": goal, "criteria": criteria, "plan": plan or [], "request": request,
                                              "source": "jarvis", "max_attempts": max_attempts,
                                              "budget_s": max(1, min(int(budget_minutes), 360)) * 60}))


@tool("owner")
def task_update(task_id: str, state: str = "", step: str = "", note: str = "", result: str = "",
                evidence: list[str] | None = None) -> str:
    """Record progress on your task. state (optional): running, verifying, repairing, paused, completed, failed or
    cancelled. completed only with evidence that every criterion holds; otherwise failed with the exact blocker in result."""
    params = {k: v for k, v in (("state", state), ("step", step), ("note", note), ("result", result)) if v}
    if evidence:
        params["evidence"] = evidence
    _log("task_update", f"{task_id} {state}")
    return _say(_act("task", "update", task_id, params))


@tool("owner")
def task_get(task_id: str) -> str:
    """A task's goal, criteria, state, attempts, verification verdict, evidence and event history."""
    return _say(_act("task", "get", task_id))


@tool("owner")
def lab_work(task_id: str, instructions: str, agent: str = "hermes", public_ok: bool = False) -> str:
    """Hand a task's hands-on work to the lab: agents with a terminal, files, code, the web and their own computer.
    The lab runner executes it, an independent verifier checks every acceptance criterion, failures are repaired up to
    the retry budget, and you get the outcome in this conversation when it ends (minutes, not seconds; tell the owner
    it has started). agent: hermes (general, uses sub-agents), opencode (coding) or goose. The lab runs on PUBLIC-lane
    models: put only information that may be shared in instructions and set public_ok to true. The lab CANNOT see the
    owner's computer: never put D:\\ or C:\\ paths in instructions and never ask it to save there. Files the owner should
    get are delivered by the runner itself after verification, to D:\\jarvis\\outputs\\task-<task_id>\\; just include a
    criterion like 'the files are delivered to the owner'. Call this ONCE per task."""
    _log("lab_work", f"{task_id} {agent}")
    return _say(_act("task", "dispatch", task_id, {"instructions": instructions, "agent": agent, "public_ok": public_ok}))


@tool("lab", "owner")
def models_status() -> str:
    """List the models you may use (your lane only), whether each is reachable, and which one is preferred."""
    return _say(_act("models", "status"))


@tool("lab", "owner")
def switch_model(provider: str) -> str:
    """Prefer another model provider (same lane only) when the current one is failing. At most 20 switches a day."""
    _log("switch_model", provider)
    return _say(_act("models", "prefer", provider))


@tool("lab")
def start_lab_task(agent: str, prompt: str) -> str:
    """Hand a task to another lab agent (hermes, opencode or goose). Returns a task id; check it with lab_task_result."""
    if agent not in AGENT_CMDS:
        return _say({"status": "refused", "reason": "agent must be hermes, opencode or goose"})
    _log("start_lab_task", f"{agent}: {prompt[:100]}")
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


@tool("lab")
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


@tool("lab")
def desktop_open(target: str) -> str:
    """Open something on YOUR OWN computer (the Agent PC the owner watches): a web address starting with http:// or
    https:// opens in Firefox; 'firefox', 'terminal' or 'files' opens that app. Windows stay open after you finish."""
    if _owner_has_control():
        return _say(OWNER_BUSY)
    _log("desktop_open", target)
    if target.startswith(("http://", "https://")):
        cmd = DESKTOP_APPS["firefox"] + [target]
    elif target in DESKTOP_APPS:
        cmd = DESKTOP_APPS[target]
    else:
        return _say({"status": "refused", "reason": "open a http(s) address or one of: firefox, terminal, files"})
    subprocess.Popen(cmd, env=_denv(), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
    time.sleep(4)
    return _say({"status": "opened", "target": target})


@tool("lab")
def desktop_windows() -> str:
    """List the windows open on your computer (title only), to know what to focus."""
    if _owner_has_control():
        return _say(OWNER_BUSY)
    _log("desktop_windows")
    code, ids = _x("search", "--onlyvisible", "--name", ".")
    titles = []
    for wid in (ids.splitlines() if not code else [])[:40]:
        _, name = _x("getwindowname", wid)
        if name and name not in ("Desktop", "xfce4-panel"):
            titles.append(name)
    return _say({"windows": titles})


@tool("lab")
def desktop_click(x: int, y: int, window: str = "") -> str:
    """Click at screen pixel (x, y) of your 1600x900 screen (use desktop_screenshot to see it). window: optional part
    of a window title to bring to the front first."""
    if not (0 <= x < 1600 and 0 <= y < 900):
        return _say({"status": "refused", "reason": "outside the 1600x900 screen"})
    if _owner_has_control():
        return _say(OWNER_BUSY)
    _log("desktop_click", f"{x},{y} {window}")
    if not _focus(window):
        return _say({"status": "no such window", "window": window})
    _x("mousemove", str(x), str(y), "click", "1")
    return _say({"status": "clicked", "x": x, "y": y})


@tool("lab")
def desktop_type(text: str, window: str = "") -> str:
    """Type text with the keyboard into whatever has focus (click the field first). window: optional title part."""
    if len(text) > 2000:
        return _say({"status": "refused", "reason": "at most 2000 characters at a time"})
    if _owner_has_control():
        return _say(OWNER_BUSY)
    _log("desktop_type", f"{len(text)} characters {window}")
    if not _focus(window):
        return _say({"status": "no such window", "window": window})
    _x("type", "--delay", "25", text, timeout=120)
    return _say({"status": "typed", "chars": len(text)})


@tool("lab")
def desktop_key(keys: str, window: str = "") -> str:
    """Press keys, e.g. 'Return', 'ctrl+l', 'ctrl+t', 'Tab', 'Escape', 'alt+F4'. window: optional title part."""
    if _owner_has_control():
        return _say(OWNER_BUSY)
    _log("desktop_key", f"{keys} {window}")
    if not _focus(window):
        return _say({"status": "no such window", "window": window})
    code, _ = _x("key", *keys.split())
    return _say({"status": "pressed" if code == 0 else "failed", "keys": keys})


@tool("lab")
def desktop_screenshot() -> Image:
    """A picture of your whole screen (1600x900). Use it to find where to click."""
    _log("desktop_screenshot")
    png = os.path.join(TASKS, "..", "last-screen.png")
    os.makedirs(TASKS, exist_ok=True)
    subprocess.run(f"xwd -root -silent | convert xwd:- png:{png}", shell=True, env=_denv(), timeout=30)
    return Image(path=png)


if __name__ == "__main__":
    mcp.run()
