"""The job that is running on this node, and every process it started, so the owner can stop it (2026-10-16).

The node runs one job at a time. A handler starts programs only through run() / popen() here; cancel(job id) kills the
whole process tree of each (taskkill /T /F on Windows) and marks the job cancelled, and the handler's result becomes
{"ok": false, "cancelled": true}. Nothing else in the node starts processes for jobs.
"""
import os
import subprocess
import threading

NOWIN = 0x08000000 if os.name == "nt" else 0       # CREATE_NO_WINDOW: no flashing console
_lock = threading.Lock()
_current = {"id": None, "procs": [], "cancelled": threading.Event()}


class Cancelled(Exception):
    pass


def begin(jid):
    with _lock:
        _current.update(id=jid, procs=[], cancelled=threading.Event())


def end():
    with _lock:
        _current.update(id=None, procs=[], cancelled=threading.Event())   # a finished job's cancel never leaks into what follows


def current():
    return _current["id"]


def cancelled() -> bool:
    return _current["cancelled"].is_set()


def check():
    """Handlers call this between steps: a cancel stops the next step even when no process is running."""
    if cancelled():
        raise Cancelled()


def _kill_tree(p):
    if p.poll() is not None:
        return
    if os.name == "nt":
        subprocess.run(["taskkill", "/PID", str(p.pid), "/T", "/F"], capture_output=True, creationflags=NOWIN, timeout=15)
    else:
        p.kill()


def cancel(jid) -> bool:
    """Stop the job with this id (only if it is the one running). Returns whether it was."""
    with _lock:
        if jid is None or _current["id"] != jid:
            return False
        _current["cancelled"].set()
        procs = list(_current["procs"])
    for p in procs:
        try:
            _kill_tree(p)
        except Exception:
            pass
    return True


def popen(args, **kw):
    """Start a program for the running job (registered, so a cancel can kill it)."""
    check()
    kw.setdefault("creationflags", NOWIN)
    p = subprocess.Popen(args, **kw)
    with _lock:
        _current["procs"].append(p)
        if _current["cancelled"].is_set():
            _kill_tree(p)
            raise Cancelled()
    return p


def run(args, timeout=30, env=None, cwd=None, input_text=None):
    """subprocess.run for job work: text in and out, a time limit, and stoppable by cancel()."""
    p = popen(args, stdin=subprocess.PIPE if input_text is not None else subprocess.DEVNULL, stdout=subprocess.PIPE,
              stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace", env=env, cwd=cwd)
    try:
        out, err = p.communicate(input=input_text, timeout=timeout)
    except subprocess.TimeoutExpired:
        _kill_tree(p)
        p.communicate()
        raise
    if cancelled():
        raise Cancelled()
    return subprocess.CompletedProcess(args, p.returncode, out, err)


def powershell(script, env=None, timeout=30):
    """A fixed PowerShell script from the node's own code. Values reach it through environment variables only."""
    return run(["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", script],
               timeout=timeout, env={**os.environ, **(env or {})})
