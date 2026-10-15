"""Self-healing watchdog for the N.E.K.O + JARVIS stack. Windows; standard library only; makes no model calls.

    python watchdog.py            one pass: check, heal what is down, log, exit (run it every few minutes: install-watchdog.ps1)
    python watchdog.py --dry-run  check and say what it would do, change nothing

Checks: the servers answer (N.E.K.O main, memory, agent, plugin runtime, local speech, JARVIS console, widget, privacy
gateway, local models), the five plugins run,
the JARVIS kernel is reachable and authorized. Heals: starts the missing servers with neko-start.ps1 (it only starts what is
down and never stops anything) and starts a stopped plugin through the plugin runtime. It never touches the JARVIS kernel, the
kill switch, approvals or the server. At most 3 heal attempts an hour per problem; after that it stops trying and says so in
watchdog_status.json, because a fix that does not hold needs a person.
"""
import argparse
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request

DATA = os.environ.get("NEKO_STORAGE_SELECTED_ROOT", r"D:\jarvis\N.E.K.O-data")
HERE = os.path.dirname(os.path.abspath(__file__))
LOG = os.path.join(DATA, "logs", "watchdog.log")
STATE = os.path.join(DATA, "logs", "watchdog_state.json")
STATUS = os.path.join(DATA, "logs", "watchdog_status.json")
SERVERS = {"main": ("http://127.0.0.1:48911/health", 48911), "memory": ("http://127.0.0.1:48912/health", 48912),
           "agent": ("http://127.0.0.1:48915/health", 48915), "plugins": ("http://127.0.0.1:48916/health", 48916),
           "speech": ("http://127.0.0.1:48930/health", 48930), "console": ("http://127.0.0.1:8799/api/owner/state", 8799),   # fast liveness; kernel detail comes from /api/health below
           "widget": ("http://127.0.0.1:48940/health", 48940), "privacy": ("http://127.0.0.1:48950/health", 48950),
           "local_models": ("http://127.0.0.1:8080/health", 8080)}
PLUGINS = ("jarvis_bridge", "main_agent", "doc_tools", "learn_vault", "local_team")
MAX_HEALS_PER_HOUR = 3
BOOT_GRACE_SECONDS = 180


def decide(servers_down, plugins_down, heals, now, uptime_s):
    """Pure decision. `heals` maps a problem key to the timestamps of earlier heal attempts. Returns (actions, gave_up)."""
    if uptime_s < BOOT_GRACE_SECONDS:
        return [("wait", "the machine just started; the login task is bringing the stack up")], []
    recent = {k: [t for t in v if now - t < 3600] for k, v in heals.items()}
    actions, gave_up = [], []
    core = list(servers_down)
    if core:
        key = "stack:" + ",".join(sorted(core))
        if len(recent.get(key, [])) >= MAX_HEALS_PER_HOUR:
            gave_up.append(key)
        else:
            actions.append(("start_stack", key))
    elif plugins_down:
        for p in plugins_down:
            key = "plugin:" + p
            if len(recent.get(key, [])) >= MAX_HEALS_PER_HOUR:
                gave_up.append(key)
            else:
                actions.append(("start_plugin", p))
    return actions, gave_up


def http_ok(url, timeout=5.0):
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return 200 <= r.status < 300, r.read()
    except (urllib.error.URLError, OSError, TimeoutError, ValueError):
        return False, b""


def observe():
    down = [name for name, (url, _) in SERVERS.items() if not http_ok(url)[0]]
    plugins_down = []
    if "plugins" not in down:
        ok, raw = http_ok("http://127.0.0.1:48916/plugins", timeout=10)
        try:
            status = {p["id"]: p.get("status") for p in json.loads(raw.decode("utf-8", "replace")).get("plugins", [])} if ok else {}
        except ValueError:
            status = {}
        plugins_down = [p for p in PLUGINS if status.get(p) != "running"]
    kernel = {}
    if "console" not in down:
        ok, raw = http_ok("http://127.0.0.1:8799/api/health", timeout=10)
        try:
            kernel = (json.loads(raw.decode("utf-8", "replace")) or {}).get("kernel", {}) if ok else {}
        except ValueError:
            kernel = {}
    return down, plugins_down, kernel


def uptime_seconds():
    """Seconds since the machine booted (a direct system call: no process to start, so it is instant even under load)."""
    try:
        import ctypes
        get_tick = ctypes.windll.kernel32.GetTickCount64
        get_tick.restype = ctypes.c_uint64
        return get_tick() / 1000.0
    except Exception:
        return 10 ** 6


def stack_script_running():
    try:
        out = subprocess.run(["powershell", "-NoProfile", "-Command",
                              "(Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like '*neko-start.ps1*' -and $_.ProcessId -ne $PID }).Count"],
                             capture_output=True, text=True, timeout=30).stdout.strip()
        return int(out or 0) > 0
    except Exception:
        return False


def log(event, **kw):
    os.makedirs(os.path.dirname(LOG), exist_ok=True)
    rec = {"ts": time.strftime("%Y-%m-%d %H:%M:%S"), "event": event, **kw}
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    print(json.dumps(rec, ensure_ascii=False), flush=True)


def load_state():
    try:
        return json.load(open(STATE, encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    now = time.time()
    down, plugins_down, kernel = observe()
    heals = load_state().get("heals", {})
    actions, gave_up = decide(down, plugins_down, heals, now, uptime_seconds())
    healthy = not down and not plugins_down
    status = {"checked": time.strftime("%Y-%m-%d %H:%M:%S"), "healthy": healthy, "servers_down": down, "plugins_down": plugins_down,
              "kernel": {k: kernel.get(k) for k in ("reachable", "authorized", "killed")}, "gave_up_on": gave_up}
    if not healthy or gave_up:
        log("problem", servers_down=down, plugins_down=plugins_down, actions=[a_[0] for a_ in actions], gave_up_on=gave_up)
    if kernel and (not kernel.get("reachable") or not kernel.get("authorized")):
        log("kernel_attention", detail="JARVIS kernel unreachable or not authorized; the watchdog does not touch it", kernel=kernel)
    if kernel and kernel.get("killed"):
        status["note"] = "kill switch is ON (left alone)"
    for kind, arg in ([] if a.dry_run else actions):
        if kind == "start_stack":
            if stack_script_running():
                log("skip", reason="neko-start.ps1 is already running")
                continue
            heals.setdefault(arg, []).append(now)
            # Output goes to a file, never a pipe: the servers neko-start.ps1 launches inherit its handles, and a pipe would
            # stay open (and this call would never return) for as long as they run.
            with open(os.path.join(DATA, "logs", "watchdog_start.out.log"), "ab") as out:
                r = subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", os.path.join(HERE, "neko-start.ps1")],
                                   stdin=subprocess.DEVNULL, stdout=out, stderr=subprocess.STDOUT, timeout=600,
                                   creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            log("start_stack", for_problem=arg, exit=r.returncode)
        elif kind == "start_plugin":
            heals.setdefault("plugin:" + arg, []).append(now)
            req = urllib.request.Request(f"http://127.0.0.1:48916/plugin/{arg}/start", data=b"{}", method="POST",
                                         headers={"Content-Type": "application/json"})
            try:
                with urllib.request.urlopen(req, timeout=60) as r:
                    log("start_plugin", plugin=arg, http=r.status)
            except Exception as exc:
                log("start_plugin_failed", plugin=arg, error=type(exc).__name__)
        elif kind == "wait":
            log("wait", reason=arg)
    if a.dry_run:
        for kind, arg in actions:
            print(f"dry run: would {kind} {arg}")
    if not a.dry_run:
        os.makedirs(os.path.dirname(STATE), exist_ok=True)
        json.dump({"heals": {k: [t for t in v if now - t < 7200] for k, v in heals.items()}}, open(STATE, "w", encoding="utf-8"))
        json.dump(status, open(STATUS, "w", encoding="utf-8"), indent=1)
    if healthy:
        print("healthy:", json.dumps(status["kernel"]))
    return 0 if healthy else 1


if __name__ == "__main__":
    sys.exit(main())
