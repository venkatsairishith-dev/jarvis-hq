"""JARVIS lab control: the owner takes the Agent PC from the agents and hands it back.

Runs as root inside the WSL lab box and listens only on a Unix socket (/run/jarvis-lab/control.sock, root:hub 0660).
Caddy (user hub) reaches it for the hub as hub.localhost:8088/control/. The agent user cannot connect, and there is no
TCP port for Windows to forward. Requests must also carry the header X-Jarvis-Hub: 1, which a page on another site cannot
add without a CORS preflight that is never answered.

take      Freezes (SIGSTOP) every agent task of the agent user (Hermes, OpenCode, goose), with everything it runs,
          exactly where it is. It also freezes whatever drives the screen for an agent (cua-driver, xdotool). Windows
          the agents opened (Firefox, terminals, files) keep running so the owner can use them. Tasks that start while
          the owner has control are frozen within two seconds.
continue  Unfreezes them (SIGCONT) and records when the owner handed back. The agents' desktop tools pass that on once,
          so each agent looks at the screen again before it goes on with what is left.
state     Mode, the running tasks (goal, running time, working or paused, last step) and the latest agent actions.

Programs the owner starts from the desktop (anything under xfce4-session) are never touched.
Custom code, justified in docs/01_RESEARCH_AUDIT.md: VNC viewers share a screen but have no notion of pausing an agent.
"""
import json
import os
import re
import signal
import socketserver
import stat
import subprocess
import sys
import threading
import time
from collections import namedtuple
from http.server import BaseHTTPRequestHandler

try:
    import grp
    import pwd
except ImportError:                                      # Windows: the tests import the pure functions only
    grp = pwd = None

AGENT = "agent"
RUN = "/run/jarvis-lab"                                  # root-owned 0755: agents can read the state, not change it
STATE_FILE = os.path.join(RUN, "control.json")
SOCKET = os.path.join(RUN, "control.sock")
TASKS = {"hermes": "chat", "opencode": "run", "goose": "run"}      # program -> subcommand that runs a task
INTERPRETER = re.compile(r"^(python[\d.]*|node|bun|sh|bash|dash)$")
GOAL_FLAG = {"hermes": "-q", "goose": "-t"}
WINDOWS = {"firefox", "firefox-bin", "xfce4-terminal", "thunar", "Thunar", "mousepad", "ristretto", "xdg-open", "exo-open",
           "gio", "xfce4-appfinder", "galculator", "evince", "atril", "eog", "vlc", "mpv", "xterm"}
SCREEN = {"cua-driver", "xdotool"}                       # programs that move the mouse or type for an agent
DESKTOP = "xfce4-session"
TOOL_LINE = re.compile(r"^(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d),\d+ INFO \[([^\]]*)\] agent\.tool_executor: tool (\S+) completed")
SESSION_ID = re.compile(r"^(\d{8}_\d{6})_")
P = namedtuple("P", "uid ppid state start argv")         # start: clock ticks after boot
LOCK = threading.Lock()


def _agent():
    try:
        p = pwd.getpwnam(AGENT)
        return p.pw_uid, p.pw_dir
    except (AttributeError, KeyError):
        return -1, "/home/" + AGENT


UID, HOME = _agent()
ACTIVITY = os.path.join(HOME, "lab", "activity.jsonl")   # written by the JARVIS tool server (jarvis_mcp.py)
HERMES_LOG = os.path.join(HOME, ".hermes", "logs", "agent.log")


# ---------------------------------------------------------------- processes

def procs():
    """pid -> P for every process."""
    out = {}
    for d in os.listdir("/proc"):
        if not d.isdigit():
            continue
        try:
            with open(f"/proc/{d}/stat") as fh:
                raw = fh.read()
            f = raw[raw.rindex(")") + 2:].split()
            with open(f"/proc/{d}/cmdline", "rb") as fh:
                argv = [a.decode(errors="replace") for a in fh.read().split(b"\0") if a]
            out[int(d)] = P(os.stat(f"/proc/{d}").st_uid, int(f[1]), f[0], int(f[19]), argv)
        except (OSError, ValueError, IndexError):
            continue
    return out


def _clock():
    with open("/proc/uptime") as fh:
        up = float(fh.read().split()[0])
    return time.time() - up, os.sysconf("SC_CLK_TCK")


def _exe(p):
    return os.path.basename(p.argv[0]) if p.argv else ""


def _kind(p):
    """'hermes' for `hermes chat ...` (also `python3 /path/hermes chat ...`), likewise opencode and goose. Decided by
    the program and its subcommand, never by text further on: `sh -c "hermes chat ..."` is a shell, not a task."""
    argv, i = p.argv, 0
    if argv and INTERPRETER.match(_exe(p)):
        i = next((j for j in range(1, len(argv)) if not argv[j].startswith("-")), len(argv))
    if i + 1 < len(argv):
        name = os.path.basename(argv[i])
        if TASKS.get(name) == argv[i + 1]:
            return name
    return None


def _ancestors(ps, pid):
    q, n = ps[pid].ppid, 0
    while q in ps and q > 1 and n < 100:
        yield q
        q, n = ps[q].ppid, n + 1


def _from_desktop(ps, pid):
    """Started by the owner from the desktop (menu, terminal)."""
    return any(_exe(ps[a]) == DESKTOP for a in _ancestors(ps, pid))


def _tree(ps, kids, root):
    """The task's processes, root first: the agent and everything it runs (tool servers, sub-agents, commands, also
    those in their own session), except windows it opened, which stay usable for the owner."""
    out, todo = [], [root]
    while todo:
        q = todo.pop(0)
        out.append(q)
        todo.extend(c for c in kids.get(q, ()) if _exe(ps[c]) not in WINDOWS)
    return out


def tasks(ps, boot=0.0, hz=100, now=None):
    """Running agent tasks of the agent user, oldest first. A task started inside another one (an OpenCode sub-agent
    started by Hermes) is part of that task."""
    now = time.time() if now is None else now
    roots = {pid: k for pid, p in ps.items() if p.uid == UID and (k := _kind(p))}
    kids = {}
    for pid, p in ps.items():
        kids.setdefault(p.ppid, []).append(pid)
    out = []
    for pid, kind in roots.items():
        if any(a in roots for a in _ancestors(ps, pid)) or _from_desktop(ps, pid):
            continue
        argv, flag = ps[pid].argv, GOAL_FLAG.get(kind)
        i = argv.index(flag) + 1 if flag in argv else len(argv)
        goal = argv[i] if i < len(argv) else (argv[-1] if argv else "")
        started = boot + ps[pid].start / hz
        out.append({"pid": pid, "agent": kind, "goal": goal[:240], "started": started, "seconds": max(0, int(now - started)),
                    "paused": ps[pid].state == "T", "tree": _tree(ps, kids, pid)})
    return sorted(out, key=lambda t: t["started"])


def screen_drivers(ps):
    """Agent-user programs that drive the screen, wherever they run (e.g. cua-driver for a Hermes dashboard chat)."""
    return [pid for pid, p in ps.items()
            if p.uid == UID and (_exe(p) in SCREEN or "computercontroller" in p.argv[1:3]) and not _from_desktop(ps, pid)]


def freeze(ps, kill=os.kill):
    """SIGSTOP every agent task (root first) and screen driver that is not stopped yet; returns the pids stopped."""
    done = []
    for pid in [pid for t in tasks(ps) for pid in t["tree"]] + screen_drivers(ps):
        p = ps.get(pid)
        if p and p.uid == UID and p.state not in ("T", "t", "Z", "X") and pid not in done:
            try:
                kill(pid, signal.SIGSTOP)
                done.append(pid)
            except OSError:
                pass
    return done


def thaw(ps, pids, kill=os.kill):
    """SIGCONT these agent-user processes, children before the task that waits for them."""
    for pid in reversed(pids):
        p = ps.get(pid)
        if p and p.uid == UID:
            try:
                kill(pid, signal.SIGCONT)
            except OSError:
                pass


# ---------------------------------------------------------------- what the agents did

def _tail(path, limit=262144):
    """The last lines of a file the agent user owns. This runs as root, so it never follows a link and never reads
    anything that is not the agent's own regular file."""
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except OSError:
        return []
    chunks = []
    try:
        st = os.fstat(fd)
        if st.st_uid != UID or not stat.S_ISREG(st.st_mode):
            return []
        start = max(0, st.st_size - limit)
        os.lseek(fd, start, os.SEEK_SET)
        left = limit
        while left > 0:
            b = os.read(fd, min(65536, left))
            if not b:
                break
            chunks.append(b)
            left -= len(b)
    finally:
        os.close(fd)
    lines = b"".join(chunks).decode(errors="replace").splitlines()
    return lines[1:] if start else lines                # the first line may be cut


def activity(lines):
    """JARVIS tool calls (one JSON object per line, written by the agents' tool server)."""
    out = []
    for line in lines[-300:]:
        try:
            e = json.loads(line)
            out.append({"ts": float(e.get("ts") or 0), "time": str(e.get("time", ""))[:8], "agent": str(e.get("agent", "agent"))[:20],
                        "action": str(e.get("tool", ""))[:40], "detail": str(e.get("detail", ""))[:140], "ppid": int(e.get("ppid") or 0)})
        except (ValueError, TypeError, AttributeError):
            continue
    return out


def hermes_steps(lines):
    """Hermes's own tool calls from its log (JARVIS tools are already in the activity file)."""
    out = []
    for line in lines:                                   # tool lines can sit far behind retries and errors
        m = TOOL_LINE.match(line)
        if not m or m.group(3).startswith("mcp__jarvis__"):
            continue
        try:
            ts = time.mktime(time.strptime(m.group(1), "%Y-%m-%d %H:%M:%S"))
        except ValueError:
            continue
        out.append({"ts": ts, "time": m.group(1)[11:], "agent": "hermes", "action": m.group(3), "detail": "", "session": m.group(2)})
    return out


def _session_start(sid):
    m = SESSION_ID.match(sid)
    try:
        return time.mktime(time.strptime(m.group(1), "%Y%m%d_%H%M%S")) if m else None
    except ValueError:
        return None


def last_step(task, acts, hsteps):
    """The task's latest action: its JARVIS tool calls (by process) and, for Hermes, the log lines of the session that
    began when the task started."""
    tree = set(task["tree"])
    steps = [a for a in acts if a["ppid"] in tree]
    if task["agent"] == "hermes":
        near = sorted({h["session"] for h in hsteps
                       if (s := _session_start(h["session"])) is not None and task["started"] - 5 <= s <= task["started"] + 300})
        if near:
            steps += [h for h in hsteps if h["session"] == near[0]]
    last = max(steps, key=lambda a: a["ts"], default=None)
    return {k: last[k] for k in ("time", "action", "detail")} if last else None


# ---------------------------------------------------------------- state and actions

def _read_state():
    try:
        with open(STATE_FILE) as fh:
            s = json.load(fh)
        return s if isinstance(s, dict) else {"mode": "agent"}
    except (OSError, ValueError):
        return {"mode": "agent"}


def _write_state(s):
    tmp = STATE_FILE + ".tmp"
    with open(tmp, "w") as fh:
        json.dump(s, fh)
    os.chmod(tmp, 0o644)
    os.replace(tmp, STATE_FILE)


def state():
    ps, s = procs(), _read_state()
    boot, hz = _clock()
    acts, hsteps = activity(_tail(ACTIVITY, 131072)), hermes_steps(_tail(HERMES_LOG))
    running = tasks(ps, boot, hz)
    for t in running:
        t["last"] = last_step(t, acts, hsteps)
    recent = sorted(acts + hsteps, key=lambda a: a["ts"])[-14:]
    return {"mode": s.get("mode", "agent"), "since": s.get("since"), "returned_at": s.get("returned_at"),
            "tasks": [{k: v for k, v in t.items() if k != "tree"} for t in running],
            "recent": [{k: r[k] for k in ("time", "agent", "action", "detail")} for r in recent]}


def take():
    with LOCK:
        s = _read_state()
        paused = freeze(procs())
        if s.get("mode") == "owner":
            s["paused"] = sorted(set(s.get("paused") or []) | set(paused))
        else:
            s = {"mode": "owner", "since": time.time(), "returned_at": s.get("returned_at"), "paused": paused}
        _write_state(s)
    return state()


def resume():
    with LOCK:
        s = _read_state()
        ps = procs()
        pids = list(s.get("paused") or [])
        pids += [pid for t in tasks(ps) for pid in t["tree"] if pid not in pids]
        pids += [pid for pid in screen_drivers(ps) if pid not in pids]
        thaw(ps, pids)
        owner = s.get("mode") == "owner"
        now = time.time()
        _write_state({"mode": "agent", "since": now if owner else s.get("since"),
                      "returned_at": now if owner else s.get("returned_at"),
                      "owner_since": s.get("since") if owner else s.get("owner_since"), "paused": []})
    return state()


_screen = {"t": 0.0, "jpg": b""}
_screen_lock = threading.Lock()


def screen_jpeg(max_age=3.0):
    """A small picture of the Agent PC for the hub's overview (cached 3 s, one capture at a time). Fixed command, no
    input from the request."""
    with _screen_lock:
        if time.time() - _screen["t"] >= max_age:
            p = subprocess.run("xwd -root -silent -display :1 | convert xwd:- -resize 800x450 -quality 72 jpeg:-",
                               shell=True, capture_output=True, timeout=20)
            if p.returncode == 0 and p.stdout[:2] == b"\xff\xd8":
                _screen.update(t=time.time(), jpg=p.stdout)
        return _screen["jpg"]


def _guard():
    """While the owner has control, agent tasks that start meanwhile are frozen too."""
    while True:
        time.sleep(2)
        try:
            with LOCK:
                s = _read_state()
                if s.get("mode") == "owner":
                    new = freeze(procs())
                    if new:
                        s["paused"] = sorted(set(s.get("paused") or []) | set(new))
                        _write_state(s)
        except Exception as e:                       # keep guarding whatever one round ran into
            print("guard:", type(e).__name__, e, file=sys.stderr, flush=True)


# ---------------------------------------------------------------- HTTP over the Unix socket

class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _reply(self, code, obj):
        b = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def _route(self, routes):
        if self.headers.get("X-Jarvis-Hub") != "1":
            return self._reply(403, {"error": "missing hub header"})
        fn = routes.get(self.path.split("?")[0].rstrip("/"))
        return self._reply(200, fn()) if fn else self._reply(404, {"error": "not found"})

    def do_GET(self):
        if self.path.split("?")[0] == "/control/screen.jpg" and self.headers.get("X-Jarvis-Hub") == "1":
            jpg = screen_jpeg()
            if not jpg:
                return self._reply(503, {"error": "no picture yet"})
            self.send_response(200)
            self.send_header("Content-Type", "image/jpeg")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(jpg)))
            self.end_headers()
            self.wfile.write(jpg)
            return
        self._route({"/control/state": state})

    def do_POST(self):
        self._route({"/control/take": take, "/control/continue": resume})


def main():
    class Server(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
        daemon_threads = True

    os.makedirs(RUN, mode=0o755, exist_ok=True)
    if not os.path.exists(STATE_FILE):
        _write_state({"mode": "agent", "since": time.time(), "returned_at": None, "paused": []})
    if os.path.exists(SOCKET):
        os.unlink(SOCKET)
    old = os.umask(0o177)
    try:
        srv = Server(SOCKET, Handler)
    finally:
        os.umask(old)
    os.chown(SOCKET, 0, grp.getgrnam("hub").gr_gid)
    os.chmod(SOCKET, 0o660)
    threading.Thread(target=_guard, daemon=True).start()
    srv.serve_forever()


if __name__ == "__main__":
    main()
