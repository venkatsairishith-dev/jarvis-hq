"""Laptop node agent: pairs once, then reports outbound to the kernel. Read-only; it never runs commands from the kernel."""
import argparse
import json
import os
import socket
import sys
import threading
import time
import urllib.request

from .collector import collect

SECRETS = r"D:\jarvis-secrets"
TOKEN_FILE = os.path.join(SECRETS, "node_token.txt")
DEFAULT_URL = "http://100.125.91.51:8765"  # kernel, reachable only over Tailscale


def _post(url, path, obj, token=None):
    req = urllib.request.Request(url + path, data=json.dumps(obj).encode(), method="POST", headers={"Content-Type": "application/json"})
    if token:
        req.add_header("Authorization", "Bearer " + token)
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read())


def pair(url, code_file, name):
    code = open(code_file).read().strip()
    tok = _post(url, "/pair", {"code": code, "name": name})["token"]
    with open(TOKEN_FILE, "w") as f:
        f.write(tok)
    print("paired; token saved to", TOKEN_FILE)  # the token itself is never printed


def _do_job(url, token, job):
    """Run one authorized job and post the result. Runs in a background thread so heartbeats never stall."""
    from .jobs import run_job
    try:
        result = run_job(job)
    except Exception as e:
        result = {"ok": False, "error": type(e).__name__}
    try:
        _post(url, "/jobs/result", {"id": job["id"], "result": result}, token)
    except Exception as e:
        print("could not post job result:", type(e).__name__, file=sys.stderr)


def _watch_cycle(watcher, url, token):
    """Report files that arrived in allow-listed folders. Acknowledged only after the kernel accepted them."""
    reports = watcher.scan()
    delivered = {}
    for w, files in reports.items():
        try:
            for i in range(0, len(files), 50):
                _post(url, "/event", {"watch": w, "files": files[i:i + 50]}, token)
            delivered[w] = files
        except Exception as e:
            print("could not report file arrivals:", type(e).__name__, file=sys.stderr)
    if delivered:
        watcher.ack(delivered)


def run(url, name, once=False, collect_every=60, beat=15, poll=1, clock=time.time, sleep=time.sleep):
    """Two cadences: the status report and file scan every `beat` seconds, the check for a queued job every `poll`
    seconds. A request spoken to JARVIS used to wait up to a whole report interval before the laptop even saw it.
    poll 3 -> 1 s (2026-10-16): the wait for a queued job was the largest fixed part of a one-step PC action."""
    with open(TOKEN_FILE) as fh:
        token = fh.read().strip()
    state, last_collect, last_report, killed = {}, 0.0, None, False
    worker = None
    from . import runtime
    from .paths import jarvis_home
    from .watch import Watcher, load_watches
    watcher = Watcher(load_watches(), os.path.join(jarvis_home(), "cache", "node-watch-state.json"))
    while True:
        try:
            now = clock()
            busy = bool(worker is not None and worker.is_alive())
            # while a job runs, report at every poll: that is how a cancel from the owner reaches it within seconds
            if once or last_report is None or now - last_report >= beat or busy:
                if now - last_collect >= collect_every or not state:
                    state, last_collect = collect(), now
                    state["watches"] = watcher.names()
                r = _post(url, "/report", {"state": state, "busy": busy}, token)
                last_report = now
                killed = bool(r.get("killed"))
                for jid in r.get("cancel") or []:
                    if runtime.cancel(jid):
                        print("cancelled job", jid, file=sys.stderr)
                    elif runtime.current() == jid:
                        print("could NOT stop every program of job", jid, "(see the cancel failed line); the job is flagged cancelled", file=sys.stderr)
                if killed and busy:                          # the kill switch stops the job in hand too
                    runtime.cancel(runtime.current())
                if killed:
                    state, last_collect = {}, 0.0  # kernel kill switch on: stop collecting until it is lifted
                    print("kernel kill switch is on; paused", file=sys.stderr)
                else:
                    _watch_cycle(watcher, url, token)
            if not once and (worker is None or not worker.is_alive()):
                try:
                    from .connectors import reap
                    reap()                                       # stop connector servers nobody has used for ten minutes
                except Exception:
                    pass
                try:
                    from .uia import reap as uia_reap
                    uia_reap()                                   # D68: and the UI Automation worker, after ten idle minutes (it holds memory; the next job starts it again)
                except Exception:
                    pass
            if not once and not killed and (worker is None or not worker.is_alive()):
                job = _post(url, "/jobs/next", {}, token).get("job")
                if job:
                    worker = threading.Thread(target=_do_job, args=(url, token, job), daemon=True)
                    worker.start()
        except Exception as e:
            print("report failed:", type(e).__name__, file=sys.stderr)
        if once:
            return
        sleep(poll)


_MUTEX = []          # keeps the handle alive for the life of the process


def single_instance(name: str = "Local\\JarvisNodeRun") -> bool:
    """True when this is the only `jarvis_node run` of the user's session. Stop-ScheduledTask ends only the wrapper script, not the node it started, so every restart
    left one more node polling for the same jobs (laptop 2026-10-27: three of them, the oldest running code from hours earlier; a job went to whichever answered
    first and "Open Settings" failed in one of them). A named mutex is released by Windows when the process dies, however it dies."""
    if os.name != "nt":
        return True
    try:
        import ctypes
        k = ctypes.WinDLL("kernel32", use_last_error=True)
        k.CreateMutexW.restype = ctypes.c_void_p
        handle = k.CreateMutexW(None, False, name)
        if not handle:
            return True                                   # cannot tell: run
        if ctypes.get_last_error() == 183:                # ERROR_ALREADY_EXISTS
            return False
        _MUTEX.append(handle)
        return True
    except Exception:                                     # noqa: BLE001 - a guard that cannot work must not stop the node
        return True


def main():
    p = argparse.ArgumentParser(prog="jarvis-node")
    p.add_argument("cmd", choices=["pair", "run", "once"])
    p.add_argument("--url", default=DEFAULT_URL)
    p.add_argument("--name", default=socket.gethostname().lower())
    p.add_argument("--code-file")
    a = p.parse_args()
    if a.cmd == "pair":
        pair(a.url, a.code_file, a.name)
    else:
        if a.cmd == "run" and not single_instance():
            print("another jarvis_node run is already running in this session; this one exits")
            return
        run(a.url, a.name, once=(a.cmd == "once"))


if __name__ == "__main__":
    main()
