"""Self-healing watchdog for the JARVIS local stack. Windows; standard library only; makes no model calls.

    python watchdog.py            one pass: check, heal what is down, log, exit (run it every few minutes: install-watchdog.ps1)
    python watchdog.py --dry-run  check and say what it would do, change nothing

Checks: the local services answer (the face's tool server, local speech, the JARVIS console, the privacy gateway, local models)
and the JARVIS kernel is reachable and authorized.
Heals: starts the missing services with jarvis-start.ps1 (it only starts what is down and never stops anything). It never
touches the JARVIS kernel, the kill switch, approvals or the server. At most 3 heal attempts an hour per problem; after that it
stops trying and says so in watchdog_status.json (`gave_up_on`), because a fix that does not hold needs a person.
"""
import argparse
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import loopback_net  # noqa: E402  loopback only, no proxy, no redirect (completion H)
import control_auth  # noqa: E402  the console's master token for the authenticated health read

DATA = os.environ.get("JARVIS_DATA_DIR", r"D:\jarvis\data")
HERE = os.path.dirname(os.path.abspath(__file__))
LOG = os.path.join(DATA, "logs", "watchdog.log")
STATE = os.path.join(DATA, "logs", "watchdog_state.json")
STATUS = os.path.join(DATA, "logs", "watchdog_status.json")
SERVERS = {"tools": ("tcp://127.0.0.1:48970", 48970),      # the face's MCP tool server has no plain health page: it must accept a connection
           "speech": ("http://127.0.0.1:48930/health", 48930), "console": ("http://127.0.0.1:8799/api/health", 8799),   # liveness only (open, no credential, instant); kernel detail comes from the authenticated /api/health below
           "privacy": ("http://127.0.0.1:48950/health", 48950), "local_models": ("http://127.0.0.1:8080/health", 8080)}
# Services that jarvis-start.ps1 starts only when their program is installed (it tests this very path). When it is not installed
# the service can never come up, so watching it would only retry three times an hour and then show an error forever (VOI-D11).
# It is watched once the program exists. The privacy gateway is deliberately NOT optional here: its absence leaves private text
# unprotected, so that must stay visible.
OPTIONAL_SERVERS = {"local_models": r"D:\jarvis\tools\llama-swap\v261\llama-swap.exe"}      # same path as jarvis-start.ps1
MAX_HEALS_PER_HOUR = 3
BOOT_GRACE_SECONDS = 180


def decide(servers_down, heals, now, uptime_s):
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
    return actions, gave_up


def tcp_ok(url, timeout=3.0):
    """tcp://host:port answers a connection (the MCP tool server has no plain GET page)."""
    import socket
    host, _, port = url[len("tcp://"):].rpartition(":")
    try:
        with socket.create_connection((host, int(port)), timeout=timeout):
            return True, b""
    except (OSError, ValueError):
        return False, b""


def http_ok(url, timeout=5.0, auth=None):
    """`auth`: name of the server whose master token (0600 file in the run folder, scripts/control_auth.py) to send, for a private read."""
    if url.startswith("tcp://"):
        return tcp_ok(url, min(timeout, 3.0))
    try:
        headers = control_auth.headers_for(auth) if auth else {}
        with loopback_net.urlopen(urllib.request.Request(url, headers=headers), timeout=timeout) as r:
            return 200 <= r.status < 300, r.read()
    except (urllib.error.URLError, OSError, TimeoutError, ValueError):
        return False, b""


def hardware_profile():
    """The model profile hwdetect.py saved ("vram4", "cpu8", ..., "remote_only"), or "" when unknown."""
    try:
        with open(os.path.join(DATA, "plugins", "hardware.json"), encoding="utf-8") as f:
            return str((json.load(f) or {}).get("profile") or "")
    except (OSError, ValueError, AttributeError):
        return ""


def optional_missing(exists=os.path.exists, profile=None):
    """Optional servers that are not expected to run, so they are not checked: their program is not installed, or (local
    models) the hardware profile is remote_only, for which local-models.ps1 deliberately starts none."""
    profile = hardware_profile() if profile is None else profile
    return [name for name, exe in OPTIONAL_SERVERS.items()
            if name in SERVERS and (not exists(exe) or (name == "local_models" and profile == "remote_only"))]


def observe(exists=os.path.exists, profile=None):
    """(services down, kernel health)."""
    skipped = set(optional_missing(exists, profile))
    down = [name for name, (url, _) in SERVERS.items() if name not in skipped and not http_ok(url)[0]]
    kernel = {}
    if "console" not in down:
        ok, raw = http_ok("http://127.0.0.1:8799/api/health", timeout=10, auth="console")
        try:
            kernel = (json.loads(raw.decode("utf-8", "replace")) or {}).get("kernel", {}) if ok else {}
        except ValueError:
            kernel = {}
    return down, kernel


def degraded_components(down, kernel, fetch=None):
    """Components that ANSWER but cannot do their job (completion H): a server being up is not the system being healthy. Returns a list of
    short reasons (names only). These do not trigger a restart (restarting does not install a model or fix a refused token); they make the
    status unhealthy, so the daily report and the exit code never say "healthy" while kernel, gateway or voice are broken."""
    fetch = fetch or (lambda url, timeout=5.0: http_ok(url, timeout=timeout))
    bad = []
    if "console" not in down:
        if not kernel:
            bad.append("kernel: health unreadable (console token missing or refused)")
        elif not kernel.get("reachable"):
            bad.append("kernel: unreachable")
        elif not kernel.get("authorized"):
            bad.append("kernel: token refused")
    for name, url in (("privacy", SERVERS["privacy"][0]), ("speech", SERVERS["speech"][0])):
        if name in down:
            continue
        ok, raw = fetch(url, 5.0)
        try:
            h = json.loads(raw.decode("utf-8", "replace")) if ok else {}
        except ValueError:
            h = {}
        if not isinstance(h, dict) or h.get("ready") is False or h.get("status") == "degraded":
            why = h.get("degraded") or h.get("problems") or ["not ready"] if isinstance(h, dict) else ["unreadable"]
            bad.append(f"{name}: " + ", ".join(str(x) for x in why[:4]))
    return bad


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
                              "(Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like '*jarvis-start.ps1*' -and $_.ProcessId -ne $PID }).Count"],
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
    """The saved heal history. A missing, truncated or wrong-shaped file reads as empty: the watchdog must keep running."""
    try:
        with open(STATE, encoding="utf-8") as f:
            st = json.load(f)
    except (OSError, ValueError):
        return {}
    if not isinstance(st, dict):
        return {}
    heals = st.get("heals")
    st["heals"] = {k: [t for t in v if isinstance(t, (int, float)) and not isinstance(t, bool)]
                   for k, v in heals.items() if isinstance(k, str) and isinstance(v, list)} if isinstance(heals, dict) else {}
    return st


def write_json_atomic(path, obj, **kw):
    """Whole file or nothing: a crash in the middle leaves the old file, never a truncated one (a truncated heal history
    would reset the 'give up after 3 attempts an hour' limit exactly when the machine is in trouble)."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, **kw)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def save_state(heals, now):
    write_json_atomic(STATE, {"heals": {k: [t for t in v if now - t < 7200] for k, v in heals.items()}})


def run_stack_start(for_problem):
    """jarvis-start.ps1 starts only what is down."""
    # Output goes to a file, never a pipe: the servers jarvis-start.ps1 launches inherit its handles, and a pipe would
    # stay open (and this call would never return) for as long as they run.
    with open(os.path.join(DATA, "logs", "watchdog_start.out.log"), "ab") as out:
        r = subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", os.path.join(HERE, "jarvis-start.ps1")]
                           + ["-NoFace"],
                           stdin=subprocess.DEVNULL, stdout=out, stderr=subprocess.STDOUT, timeout=600,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    log("start_stack", for_problem=for_problem, exit=r.returncode)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    now = time.time()
    uptime = uptime_seconds()
    down, kernel = observe()
    heals = load_state().get("heals", {})
    actions, gave_up = decide(down, heals, now, uptime)
    degraded = degraded_components(down, kernel)
    healthy = not down and not degraded
    status = {"checked": time.strftime("%Y-%m-%d %H:%M:%S"), "healthy": healthy, "servers_down": down,
              "degraded": degraded, "optional_not_installed": optional_missing(),
              "kernel": {k: kernel.get(k) for k in ("reachable", "authorized", "killed")}, "gave_up_on": gave_up}
    if not healthy or gave_up:
        log("problem", servers_down=down, actions=[a_[0] for a_ in actions], gave_up_on=gave_up)
    if kernel and (not kernel.get("reachable") or not kernel.get("authorized")):
        log("kernel_attention", detail="JARVIS kernel unreachable or not authorized; the watchdog does not touch it", kernel=kernel)
    if kernel and kernel.get("killed"):
        status["note"] = "kill switch is ON (left alone)"
    for kind, arg in ([] if a.dry_run else actions):
        if kind == "start_stack":
            if stack_script_running():
                log("skip", reason="jarvis-start.ps1 is already running")
                continue
            heals.setdefault(arg, []).append(now)
            save_state(heals, now)                        # the attempt is on record BEFORE it starts (a crash in it still counts)
            run_stack_start(arg)
        elif kind == "wait":
            log("wait", reason=arg)
    if a.dry_run:
        for kind, arg in actions:
            print(f"dry run: would {kind} {arg}")
    if not a.dry_run:
        save_state(heals, now)
        write_json_atomic(STATUS, status, indent=1)
    if healthy:
        print("healthy:", json.dumps(status["kernel"]))
    return 0 if healthy else 1


if __name__ == "__main__":
    sys.exit(main())
