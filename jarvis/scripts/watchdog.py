"""Self-healing watchdog for the N.E.K.O + JARVIS stack. Windows; standard library only; makes no model calls.

    python watchdog.py            one pass: check, heal what is down, log, exit (run it every few minutes: install-watchdog.ps1)
    python watchdog.py --dry-run  check and say what it would do, change nothing

Checks: the servers answer (N.E.K.O main, memory, agent, plugin runtime, local speech, JARVIS console, widget, privacy
gateway, local models), the JARVIS plugins run (including `desktop`, which hosts the 60 s PC monitor), the "Hey Neko" wake
listener (127.0.0.1:48945) answers when it is meant to run, and the JARVIS kernel is reachable and authorized.
Heals: starts the missing servers with neko-start.ps1 (it only starts what is down and never stops anything), starts a
stopped plugin through the plugin runtime, and restarts a wake listener that stopped by running neko-start.ps1 again (that
script starts it only when the wake-word model exists). It never touches the JARVIS kernel, the kill switch, approvals or
the server. At most 3 heal attempts an hour per problem; after that it stops trying and says so in watchdog_status.json
(`gave_up_on`, which the Neko icon shows as an error), because a fix that does not hold needs a person.

The wake listener is the microphone, so the watchdog only brings it back when the owner had it on: it must have been seen
running since this boot (neko-start.ps1 -NoWake never starts it, so it is never seen), the wake-word model must exist, and
the owner's off switch <data>\\wake.off must not exist (POST /pause on the listener now writes it and POST /resume removes it,
so a pause survives a restart; before that it was a file nobody created). Otherwise the watchdog never starts it, and its stack restarts pass
-NoWake so they do not start it either.
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

DATA = os.environ.get("NEKO_STORAGE_SELECTED_ROOT", r"D:\jarvis\N.E.K.O-data")
HERE = os.path.dirname(os.path.abspath(__file__))
LOG = os.path.join(DATA, "logs", "watchdog.log")
STATE = os.path.join(DATA, "logs", "watchdog_state.json")
STATUS = os.path.join(DATA, "logs", "watchdog_status.json")
SERVERS = {"main": ("http://127.0.0.1:48911/health", 48911), "memory": ("http://127.0.0.1:48912/health", 48912),
           "agent": ("http://127.0.0.1:48915/health", 48915), "plugins": ("http://127.0.0.1:48916/health", 48916),
           "speech": ("http://127.0.0.1:48930/health", 48930), "console": ("http://127.0.0.1:8799/api/health", 8799),   # liveness only (open, no credential, instant); kernel detail comes from the authenticated /api/health below
           "widget": ("http://127.0.0.1:48940/health", 48940), "privacy": ("http://127.0.0.1:48950/health", 48950),
           "local_models": ("http://127.0.0.1:8080/health", 8080)}
# Servers that neko-start.ps1 starts only when their program is installed (it tests this very path). When it is not installed
# the server can never come up, so watching it would only retry three times an hour and then show the Neko icon as an error
# forever (VOI-D11). It is watched once the program exists. The privacy gateway is deliberately NOT optional here: its absence
# leaves private text unprotected, so that must stay visible.
OPTIONAL_SERVERS = {"local_models": r"D:\jarvis\tools\llama-swap\v261\llama-swap.exe"}      # same path as neko-start.ps1
PLUGINS = ("jarvis_bridge", "main_agent", "doc_tools", "learn_vault", "local_team", "web_search", "deliverables", "research",
           "desktop")
WAKE_URL = "http://127.0.0.1:48945/health"          # liveness only (open); /status needs the wake token (re-review M3)
WAKE_MODEL_GLOB = r"D:\jarvis\models\voice\kws\versions\*\tokens.txt"     # the same test neko-start.ps1 makes
WAKE_OFF = os.path.join(DATA, "wake.off")                # the owner's switch: while it exists the listener is never started
WAKE_KEY = "wake"
MAX_HEALS_PER_HOUR = 3
BOOT_GRACE_SECONDS = 180
SAME_BOOT_S = 300                                        # two boot-time estimates this close are the same boot


def decide(servers_down, plugins_down, heals, now, uptime_s, wake_down=False):
    """Pure decision. `heals` maps a problem key to the timestamps of earlier heal attempts. `wake_down`: the wake listener
    is meant to run (see wake_wanted) and does not answer. Returns (actions, gave_up)."""
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
        return actions, gave_up                      # the stack restart also starts what else is missing
    for p in plugins_down:
        key = "plugin:" + p
        if len(recent.get(key, [])) >= MAX_HEALS_PER_HOUR:
            gave_up.append(key)
        else:
            actions.append(("start_plugin", p))
    if wake_down:
        if len(recent.get(WAKE_KEY, [])) >= MAX_HEALS_PER_HOUR:
            gave_up.append(WAKE_KEY)
        else:
            actions.append(("start_wake", WAKE_KEY))
    return actions, gave_up


def wake_wanted(seen, boot_at, model_exists, owner_off):
    """Pure decision: should the wake listener be running? (wanted, why). It is the microphone, so only when the owner had
    it on: seen running since this boot (a -NoWake start never runs it), its model provisioned, and no off switch."""
    if owner_off:
        return False, "off: the owner's switch wake.off exists"
    if not model_exists:
        return False, "off: no wake-word model (neko-start.ps1 does not start it either)"
    if not seen or abs(float(seen.get("boot_at") or 0) - boot_at) > SAME_BOOT_S:
        return False, "off: not started since this boot (neko-start.ps1 -NoWake, or never on)"
    return True, "on: it ran since this boot"


def http_ok(url, timeout=5.0, auth=None):
    """`auth`: name of the server whose master token (0600 file in the run folder, scripts/control_auth.py) to send, for a private read."""
    try:
        headers = control_auth.headers_for(auth) if auth else {}
        with loopback_net.urlopen(urllib.request.Request(url, headers=headers), timeout=timeout) as r:
            return 200 <= r.status < 300, r.read()
    except (urllib.error.URLError, OSError, TimeoutError, ValueError):
        return False, b""


def hardware_profile():
    """The model profile hwdetect.py saved ("vram4", "cpu8", ..., "remote_only"), or "" when unknown."""
    try:
        with open(os.path.join(DATA, "neko", "hardware.json"), encoding="utf-8") as f:
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
    """(servers down, plugins not running, kernel health, wake listener answers)."""
    skipped = set(optional_missing(exists, profile))
    down = [name for name, (url, _) in SERVERS.items() if name not in skipped and not http_ok(url)[0]]
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
        ok, raw = http_ok("http://127.0.0.1:8799/api/health", timeout=10, auth="console")
        try:
            kernel = (json.loads(raw.decode("utf-8", "replace")) or {}).get("kernel", {}) if ok else {}
        except ValueError:
            kernel = {}
    return down, plugins_down, kernel, http_ok(WAKE_URL, timeout=3)[0]


def degraded_components(down, kernel, fetch=None, wake_up=False, wake_wanted_=False):
    """Components that ANSWER but cannot do their job (completion H): a server being up is not the system being healthy. Returns a list of
    short reasons (names only). These do not trigger a restart (restarting does not install a model or fix a refused token); they make the
    status unhealthy, so the widget, the daily report and the exit code never say "healthy" while kernel, gateway or voice are broken."""
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
    if wake_up and wake_wanted_:
        ok, raw = fetch(WAKE_URL, 3.0)
        try:
            ready = json.loads(raw.decode("utf-8", "replace")).get("ready") if ok else None
        except (ValueError, AttributeError):
            ready = None
        if ready is False:
            bad.append("wake: listener answers but is not working (microphone or detector)")
    return bad


def wake_model_exists():
    import glob
    return bool(glob.glob(WAKE_MODEL_GLOB))


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


def save_state(heals, seen, now):
    write_json_atomic(STATE, {"heals": {k: [t for t in v if now - t < 7200] for k, v in heals.items()}, "wake_seen": seen})


def run_stack_start(for_problem, no_wake):
    """neko-start.ps1 starts only what is down. -NoWake when the wake listener must not be started (see wake_wanted)."""
    # Output goes to a file, never a pipe: the servers neko-start.ps1 launches inherit its handles, and a pipe would
    # stay open (and this call would never return) for as long as they run.
    with open(os.path.join(DATA, "logs", "watchdog_start.out.log"), "ab") as out:
        r = subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", os.path.join(HERE, "neko-start.ps1")]
                           + (["-NoWake"] if no_wake else []),
                           stdin=subprocess.DEVNULL, stdout=out, stderr=subprocess.STDOUT, timeout=600,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    log("start_stack", for_problem=for_problem, exit=r.returncode, no_wake=no_wake)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    now = time.time()
    uptime = uptime_seconds()
    boot_at = now - uptime
    down, plugins_down, kernel, wake_up = observe()
    saved = load_state()
    heals = saved.get("heals", {})
    seen = saved.get("wake_seen")
    if wake_up:
        seen = {"boot_at": boot_at, "at": now}
    wanted, wake_why = wake_wanted(seen, boot_at, wake_model_exists(), os.path.exists(WAKE_OFF))
    wake_down = wanted and not wake_up
    actions, gave_up = decide(down, plugins_down, heals, now, uptime, wake_down=wake_down)
    degraded = degraded_components(down, kernel, wake_up=wake_up, wake_wanted_=wanted)
    healthy = not down and not plugins_down and not wake_down and not degraded
    status = {"checked": time.strftime("%Y-%m-%d %H:%M:%S"), "healthy": healthy, "servers_down": down, "plugins_down": plugins_down,
              "degraded": degraded, "optional_not_installed": optional_missing(),
              "wake": {"up": wake_up, "wanted": wanted, "why": wake_why},
              "kernel": {k: kernel.get(k) for k in ("reachable", "authorized", "killed")}, "gave_up_on": gave_up}
    if not healthy or gave_up:
        log("problem", servers_down=down, plugins_down=plugins_down, wake_down=wake_down, actions=[a_[0] for a_ in actions],
            gave_up_on=gave_up)
    if kernel and (not kernel.get("reachable") or not kernel.get("authorized")):
        log("kernel_attention", detail="JARVIS kernel unreachable or not authorized; the watchdog does not touch it", kernel=kernel)
    if kernel and kernel.get("killed"):
        status["note"] = "kill switch is ON (left alone)"
    for kind, arg in ([] if a.dry_run else actions):
        if kind in ("start_stack", "start_wake"):
            if stack_script_running():
                log("skip", reason="neko-start.ps1 is already running")
                continue
            heals.setdefault(arg, []).append(now)
            save_state(heals, seen, now)                  # the attempt is on record BEFORE it starts (a crash in it still counts)
            run_stack_start(arg, no_wake=not wanted)
        elif kind == "start_plugin":
            heals.setdefault("plugin:" + arg, []).append(now)
            save_state(heals, seen, now)
            req = urllib.request.Request(f"http://127.0.0.1:48916/plugin/{arg}/start", data=b"{}", method="POST",
                                         headers={"Content-Type": "application/json"})
            try:
                with loopback_net.urlopen(req, timeout=60) as r:
                    log("start_plugin", plugin=arg, http=r.status)
            except Exception as exc:
                log("start_plugin_failed", plugin=arg, error=type(exc).__name__)
        elif kind == "wait":
            log("wait", reason=arg)
    if a.dry_run:
        for kind, arg in actions:
            print(f"dry run: would {kind} {arg}")
    if not a.dry_run:
        save_state(heals, seen, now)
        write_json_atomic(STATUS, status, indent=1)
    if healthy:
        print("healthy:", json.dumps(status["kernel"]))
    return 0 if healthy else 1


if __name__ == "__main__":
    sys.exit(main())
