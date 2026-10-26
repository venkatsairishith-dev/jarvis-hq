"""The job that is running on this node, and every process it started, so the owner can stop it (2026-10-16).

The node runs one job at a time. A handler starts programs only through run() / popen() here; cancel(job id) kills the
whole process tree of each (taskkill /T /F on Windows) and marks the job cancelled, and the handler's result becomes
{"ok": false, "cancelled": true}. Nothing else in the node starts processes for jobs.
"""
import os
import re
import sys
import subprocess
import threading

NOWIN = 0x08000000 if os.name == "nt" else 0       # CREATE_NO_WINDOW: no flashing console
_lock = threading.Lock()
_current = {"id": None, "procs": [], "cancelled": threading.Event()}
# A cancel for a job this node was handed but has not begun yet (the kernel hands a job over, the worker thread starts a moment
# later): remembered, so the job is cancelled before its first side effect instead of the cancel being lost (completion A).
_early_cancels = {}
EARLY_CANCEL_KEEP = 64


class Cancelled(Exception):
    pass


def begin(jid):
    with _lock:
        _current.update(id=jid, procs=[], cancelled=threading.Event())
        if jid is not None and _early_cancels.pop(jid, None) is not None:
            _current["cancelled"].set()


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
    """Kill the process (and on Windows its children). Raises OSError/RuntimeError when it could not be stopped (ST-2, 2026-10-19)."""
    if p.poll() is not None:
        return
    if os.name == "nt":
        r = subprocess.run(["taskkill", "/PID", str(p.pid), "/T", "/F"], capture_output=True, creationflags=NOWIN, timeout=15)
        if r.returncode != 0 and p.poll() is None:
            raise RuntimeError(f"taskkill exited {r.returncode} and the process is still running")
    else:
        p.kill()
    try:
        p.wait(timeout=5)
    except subprocess.TimeoutExpired:
        raise RuntimeError("the process is still running after the kill") from None


def cancel_detail(jid):
    """Stop the job with this id. Returns (was_running, errors): errors lists every process that could NOT be stopped (ST-2: a failed kill
    used to be swallowed and cancel() still said True, so the owner was told a job was stopped while its program kept running)."""
    with _lock:
        if jid is None:
            return False, []
        if _current["id"] != jid:
            _early_cancels[jid] = True               # not running (yet): if it begins later it starts cancelled
            while len(_early_cancels) > EARLY_CANCEL_KEEP:
                _early_cancels.pop(next(iter(_early_cancels)))
            return False, []
        _current["cancelled"].set()
        procs = list(_current["procs"])
    errors = []
    for p in procs:
        try:
            _kill_tree(p)
        except Exception as e:                       # noqa: BLE001 - reported, never swallowed
            errors.append(f"pid {getattr(p, 'pid', '?')}: {type(e).__name__}")
    return True, errors


def cancel(jid) -> bool:
    """True only when this job was the one running AND every program it started was stopped. A failed kill is False (the job's
    cancelled flag is still set, so its next step stops, but the caller must not report it as stopped)."""
    running, errors = cancel_detail(jid)
    if errors:
        print("cancel failed:", "; ".join(errors), file=sys.stderr)
    return running and not errors


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


def adopt(p):
    """Register a process that OUTLIVES the job (the persistent UI Automation worker, D68) for as long as ONE request of this job waits on it, so a cancel
    kills it like any other program of the job (the next request starts a fresh one). Not registered while idle: a later job's cancel never touches it."""
    with _lock:
        _current["procs"].append(p)
        if _current["cancelled"].is_set():                       # a cancel that arrived just before: same rule as popen()
            _kill_tree(p)
            raise Cancelled()


def release(p):
    with _lock:
        try:
            _current["procs"].remove(p)
        except ValueError:
            pass


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


# Names that carry or point at a GitHub/git credential. A child program started by a node job must not inherit them from the node's own
# environment (completion E, 2026-10-19); a job that needs one gets it explicitly, never by inheritance.
GIT_HOST_CREDENTIAL_ENV = re.compile(r"^(GH_|GITHUB_|GIT_ASKPASS|GIT_CONFIG|GIT_SSH|GIT_CREDENTIAL|GIT_TERMINAL|SSH_AUTH_SOCK|SSH_ASKPASS|NETRC)", re.I)


def safe_environ() -> dict:
    """The node's environment without GitHub/git credential variables (for children that inherit it)."""
    return {k: v for k, v in os.environ.items() if not GIT_HOST_CREDENTIAL_ENV.search(k)}


def powershell(script, env=None, timeout=30):
    """A fixed PowerShell script from the node's own code. Values reach it through environment variables only."""
    return run(["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", script],
               timeout=timeout, env={**safe_environ(), **(env or {})})
