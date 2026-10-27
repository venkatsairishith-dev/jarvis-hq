"""The node's side of the persistent UI Automation worker (D68): start it once, talk to it in JSON lines, keep it healthy, and fall back to the
PowerShell scripts whenever it is not there.

Why: every UI Automation job started its own PowerShell (about 235 to 343 ms before any work, measured and noted in tools/registry.json), and one click
or one typed text took two or three of them (pick the window, find it, act). One long-lived Python process that already holds the UI Automation
connection answers in a few milliseconds. The worker is a faster implementation of the SAME jobs: the kernel's policy, the job kinds, the effect classes, the
PIN rules and "screen text is untrusted data" are not touched, and nothing in this file decides permission.

What is guaranteed here (each point has a test in tests/test_uia_lifecycle.py, against a REAL child process):
  * one request at a time, every request has an id and its own time limit; one answer line is capped (MAX_LINE) and a longer one kills the worker;
  * a worker that crashes, hangs, talks nonsense or floods is killed and restarted later, with a growing wait (BACKOFF); until then the PowerShell path serves;
  * a library that is not installed is found out once (the worker says so in its first line) and the worker stays off until the node restarts;
  * a CHANGING job (click, type, focus, window state, paste) is never repeated through PowerShell once its request was sent: if the worker died or hung
    in the middle the outcome is unknown and the job fails (`ui-action-failed:other`); the plugin's own verify-and-retry ladder decides what to do next.
    A read is simply asked again the old way when the worker crashed;
  * the worker shares the job's cancel: while a request waits, the process is registered with runtime, so a cancel kills it (and the next request starts a new one);
  * it runs at the node's own priority, no shell (an argument vector), no network (the worker imports none), stdout is the protocol and nothing else,
    it stops after IDLE_EXIT_S without work and with the node (atexit, and when the pipe closes).
Switches: JARVIS_UIA_WORKER=0 turns it off (PowerShell as before); JARVIS_UIA_PYTHON names the worker's python (default: the uia-venv under the data folder).
"""
from __future__ import annotations

import atexit
import json
import os
import queue
import subprocess
import sys
import threading
import time

from . import safe_mode

NOWIN = 0x08000000 if os.name == "nt" else 0       # CREATE_NO_WINDOW; no priority class is set: the worker runs at the node's normal priority
PROTO = 1
MAX_LINE = 1024 * 1024                            # bytes of one answer line; the worker caps itself at the same size
REQUEST_TIMEOUT_S = 20.0                          # one request (the old PowerShell had 30 s, snapshot 60 s)
READY_TIMEOUT_S = 20.0                            # the first start imports comtypes and the UI Automation type library
PING_TIMEOUT_S = 3.0
PING_AFTER_IDLE_S = 30.0                          # a worker that was idle this long must answer a ping before it gets a request
IDLE_EXIT_S = 600.0                               # no work for ten minutes: the worker is stopped (D50: it holds memory), the next request starts it
RECYCLE_AFTER = 5000                              # requests served by one process; then a fresh one (a long-lived COM client leaks a little)
BACKOFF = (1.0, 3.0, 10.0, 30.0, 120.0)           # seconds to wait before starting a worker again after the 1st, 2nd ... failure in a row
READS = {"ping", "windows", "find", "snapshot", "window_text"}


class NotSent(Exception):
    """Nothing was sent to a worker (off, backing off, did not start): the caller may use the PowerShell path."""


class Lost(Exception):
    """The request was sent and no usable answer came (crash, hang, oversize, garbled): what it did is unknown."""

    def __init__(self, why: str):
        super().__init__(why)
        self.why = why


class Refused(Exception):
    """The worker answered, with one of its fixed reason codes."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def _kill(p) -> None:
    try:
        p.kill()
    except OSError:
        pass
    try:
        p.wait(timeout=5)
    except Exception:  # noqa: BLE001
        pass
    for f in (p.stdin, p.stdout):
        try:
            if f:
                f.close()
        except OSError:
            pass


class WorkerClient:
    def __init__(self, argv, env=None, *, request_timeout=REQUEST_TIMEOUT_S, ready_timeout=READY_TIMEOUT_S, max_line=MAX_LINE, backoff=BACKOFF,
                 idle_exit_s=IDLE_EXIT_S, ping_after_idle_s=PING_AFTER_IDLE_S, recycle_after=RECYCLE_AFTER, clock=time.monotonic, popen=subprocess.Popen,
                 adopt=None, release=None, cancelled=None):
        self.argv, self.env = argv, env
        self.request_timeout, self.ready_timeout, self.max_line, self.backoff = request_timeout, ready_timeout, max_line, tuple(backoff)
        self.idle_exit_s, self.ping_after_idle_s, self.recycle_after = idle_exit_s, ping_after_idle_s, recycle_after
        self.clock, self._popen = clock, popen
        self._adopt, self._release, self._cancelled = adopt or (lambda p: None), release or (lambda p: None), cancelled or (lambda: False)
        self._lock = threading.Lock()
        self.proc = None
        self._inbox: queue.Queue | None = None
        self._ids = iter(range(1, 10**9))
        self.fails = 0                  # failures in a row; a good answer resets it
        self.next_try = 0.0             # not before this time (clock())
        self.disabled = ""              # why the worker is off for good (until the node restarts)
        self.lib = ""
        self.last_used = 0.0
        self.served = 0                 # requests answered by the current process
        self.starts = 0
        self.total = 0
        self._atexit = False

    # ---- state
    def alive(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def usable(self) -> bool:
        """Worth asking: not off for good, and either running or past its back-off."""
        return not self.disabled and (self.alive() or self.clock() >= self.next_try)

    def status(self) -> dict:
        return {"alive": self.alive(), "pid": getattr(self.proc, "pid", None) if self.alive() else None, "lib": self.lib, "served": self.served,
                "starts": self.starts, "fails": self.fails, "disabled": self.disabled, "backoff_s": max(0.0, round(self.next_try - self.clock(), 1))}

    def _failed(self) -> None:
        self.fails += 1
        self.next_try = self.clock() + self.backoff[min(self.fails - 1, len(self.backoff) - 1)]

    def _drop(self, count_failure: bool) -> None:
        p, self.proc, self._inbox = self.proc, None, None
        if p is not None:
            _kill(p)
        if count_failure:
            self._failed()

    # ---- starting
    def _reader(self, p, inbox) -> None:
        try:
            while True:
                line = p.stdout.readline(self.max_line + 1)
                if not line:
                    inbox.put(("eof", None))
                    return
                if len(line) > self.max_line and not line.endswith(b"\n"):
                    inbox.put(("oversize", None))
                    return
                inbox.put(("line", line))
        except (OSError, ValueError):
            inbox.put(("eof", None))

    def _start(self) -> None:
        if self.disabled:
            raise NotSent(self.disabled)
        if self.alive():
            return
        if self.proc is not None:                       # it died on its own while nobody was asking: nothing was lost, so one start right away, but it is counted
            self._drop(False)
            if not self._cancelled():
                self.fails += 1
        if self.clock() < self.next_try:
            raise NotSent("backing off after a failure")
        argv = self.argv() if callable(self.argv) else self.argv
        if not argv:
            raise NotSent("no worker python")
        try:
            p = self._popen(list(argv), stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, env=self.env, close_fds=True,
                            creationflags=NOWIN)
        except (OSError, ValueError):
            self._failed()
            raise NotSent("could not start the worker") from None
        inbox: queue.Queue = queue.Queue()
        self.proc, self._inbox, self.served, self.starts = p, inbox, 0, self.starts + 1
        threading.Thread(target=self._reader, args=(p, inbox), daemon=True).start()
        if not self._atexit:
            self._atexit = True
            atexit.register(self.shutdown)
        try:
            kind, line = inbox.get(timeout=self.ready_timeout)
        except queue.Empty:
            self._drop(True)
            raise NotSent("the worker did not report ready") from None
        hello = _parse(line) if kind == "line" else None
        if not hello or "ready" not in hello:
            self._drop(True)
            raise NotSent("the worker did not report ready")
        if hello.get("ready") is not True or hello.get("proto") != PROTO:
            self.disabled = "worker says: " + str(hello.get("error") or "protocol mismatch")[:60]       # a missing library does not fix itself while the node runs
            self._drop(False)
            raise NotSent(self.disabled)
        self.lib = str(hello.get("lib") or "")[:60]

    # ---- one request
    def request(self, op: str, args: dict | None = None, timeout: float | None = None) -> dict:
        timeout = self.request_timeout if timeout is None else timeout
        with self._lock:
            for attempt in (0, 1):
                self._start()
                if attempt == 0 and op != "ping" and self.clock() - self.last_used > self.ping_after_idle_s and self.last_used:
                    if not self._ping():                  # idle for a while: it must still answer before it gets real work
                        self._drop(True)
                        self.next_try = 0.0               # counted as a failure, but one fresh start right away
                        continue
                rid = next(self._ids)
                line = (json.dumps({"id": rid, "op": op, "args": args or {}}, ensure_ascii=True, separators=(",", ":")) + "\n").encode("ascii")
                try:
                    self.proc.stdin.write(line)
                    self.proc.stdin.flush()
                except (OSError, ValueError):
                    self._drop(True)                      # the pipe was already closed: the worker was dead before this request, so nothing ran
                    if attempt == 0:
                        self.next_try = 0.0               # one immediate fresh start for a worker that died while idle
                        continue
                    raise NotSent("the worker is gone") from None
                return self._await(rid, timeout)
            raise NotSent("the worker is gone")

    def _await(self, rid: int, timeout: float) -> dict:
        p, inbox = self.proc, self._inbox
        try:
            self._adopt(p)                                # a cancel that arrived just before kills it and raises Cancelled
            end = self.clock() + timeout
            while True:
                left = end - self.clock()
                try:
                    kind, line = inbox.get(timeout=max(0.0, left)) if left > 0 else inbox.get_nowait()
                except queue.Empty:
                    self._drop(True)
                    raise Lost("timeout") from None
                if kind == "eof":
                    cancelled = self._cancelled()
                    self._drop(not cancelled)
                    raise Lost("cancelled" if cancelled else "crash")
                if kind == "oversize":
                    self._drop(True)
                    raise Lost("oversize")
                msg = _parse(line)
                if msg is None:
                    self._drop(True)
                    raise Lost("garbled")
                if msg.get("id") != rid:
                    continue                              # an answer to something older: ignored
                self.fails, self.last_used, self.served, self.total = 0, self.clock(), self.served + 1, self.total + 1
                if self.served >= self.recycle_after:
                    self._stop()
                if msg.get("ok") is True and isinstance(msg.get("result"), dict):
                    return msg["result"]
                raise Refused(str(msg.get("code") or "other"))
        except BaseException:
            if self.proc is p and p.poll() is not None:
                self._drop(False)
            raise
        finally:
            self._release(p)

    def _ping(self) -> bool:
        try:
            rid = next(self._ids)
            self.proc.stdin.write((json.dumps({"id": rid, "op": "ping", "args": {}}) + "\n").encode("ascii"))
            self.proc.stdin.flush()
            end = self.clock() + PING_TIMEOUT_S
            while self.clock() < end:
                kind, line = self._inbox.get(timeout=max(0.01, end - self.clock()))
                if kind != "line":
                    return False
                m = _parse(line)
                if m and m.get("id") == rid:
                    return bool(m.get("ok"))
        except (OSError, ValueError, queue.Empty):
            return False
        return False

    def ping(self) -> bool:
        try:
            return bool(self.request("ping", timeout=PING_TIMEOUT_S).get("pong"))
        except (NotSent, Lost, Refused):
            return False

    # ---- stopping
    def _stop(self) -> None:
        """Ask the worker to quit; kill it when it does not. Caller holds the lock or is the only user."""
        p = self.proc
        if p is None:
            return
        try:
            if p.poll() is None:
                p.stdin.write(b'{"id":0,"op":"quit","args":{}}\n')
                p.stdin.flush()
                p.wait(timeout=2)
        except Exception:  # noqa: BLE001
            pass
        self._drop(False)

    def shutdown(self) -> None:
        got = self._lock.acquire(timeout=3)
        try:
            self._stop()
        finally:
            if got:
                self._lock.release()

    def reap(self, now: float | None = None) -> bool:
        """Stop the worker when nobody used it for IDLE_EXIT_S (called from the node's loop, like connectors.reap). True if it stopped one."""
        if not self.alive() or not self._lock.acquire(blocking=False):
            return False
        try:
            if self.alive() and (self.clock() if now is None else now) - self.last_used > self.idle_exit_s:
                self._stop()
                return True
        finally:
            self._lock.release()
        return False


def _parse(line):
    try:
        v = json.loads(line)
    except (TypeError, ValueError):
        return None
    return v if isinstance(v, dict) else None


# ------------------------------------------------------------------------------------------------------ the node-wide worker
_client: WorkerClient | None = None
_injected = False


def worker_script() -> str:
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "uia_worker.py")


def worker_python() -> str | None:
    """The python of the worker's own venv (JARVIS_UIA_PYTHON, else <data folder>\\tools\\uia-venv). None = not installed: the PowerShell path serves."""
    p = os.environ.get("JARVIS_UIA_PYTHON", "").strip()
    if not p:
        from .paths import jarvis_home
        p = os.path.join(jarvis_home(), "tools", "uia-venv", "Scripts" if os.name == "nt" else "bin", "python.exe" if os.name == "nt" else "python")
    return p if os.path.isfile(p) else None


def _argv():
    py = worker_python()
    return [py, "-I", worker_script()] if py else None


def switch() -> str:
    return os.environ.get("JARVIS_UIA_WORKER", "").strip().lower()


def enabled() -> bool:
    """On by default on Windows once the uia-venv exists; JARVIS_UIA_WORKER=0 turns it off; =1 asks for it on any OS (development)."""
    v = switch()
    if v in ("0", "off", "false", "no") or safe_mode.active():       # D71: safe mode turns the worker off, whatever else is set
        return False
    if _injected:
        return _client is not None
    if v in ("1", "on", "true", "yes"):
        return worker_python() is not None
    if "PYTEST_CURRENT_TEST" in os.environ:                # a unit test never starts the real worker on its own (tests that want one inject theirs)
        return False
    return os.name == "nt" and worker_python() is not None


def client() -> WorkerClient | None:
    global _client
    if not enabled():
        return None
    if _client is None:
        from . import runtime
        from .runtime import safe_environ
        _client = WorkerClient(_argv, env={**safe_environ(), "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"}, adopt=runtime.adopt, release=runtime.release,
                               cancelled=runtime.cancelled)
    return _client


def set_client(c: WorkerClient | None) -> None:
    """Tests: use this client (or, with None, none) instead of the real one."""
    global _client, _injected
    _client, _injected = c, c is not None


def reset() -> None:
    global _client, _injected
    if _client is not None:
        _client.shutdown()
    _client, _injected = None, False


def reap() -> None:
    if _client is not None:
        _client.reap()


def shutdown() -> None:
    if _client is not None:
        _client.shutdown()


def call(op: str, args: dict, *, timeout: float | None = None) -> dict | None:
    """The worker's answer for `op`, or None = "use the PowerShell script" (worker off, down, backing off, library missing, or it cannot do this).
    Raises RuntimeError("ui-action-failed:<code>") for a refusal or failure the PowerShell script would have reported the same way; the code is one of the
    fixed words of winbackend.REASONS, never screen text. A changing job whose request was sent is never repeated through PowerShell."""
    c = client()
    if c is None:
        return None
    from . import runtime
    runtime.check()
    mutating = op not in READS
    try:
        return c.request(op, args, timeout)
    except NotSent:
        return None
    except Refused as r:
        if r.code == "unsupported":
            return None                                  # this adapter lacks a primitive and did nothing
        if r.code in ("other", "bad request", "oversize") and not mutating:
            return None                                  # a read the worker could not do: the old way may
        raise RuntimeError("ui-action-failed:" + (r.code if r.code.replace(" ", "").isalpha() else "other")) from None
    except Lost as e:
        runtime.check()                                  # a cancel killed it: the job is over
        if not mutating and e.why != "timeout":
            return None                                  # a crash during a read: ask again the old way (a hung application would hang PowerShell too)
        raise RuntimeError("ui-action-failed:other") from None


# ------------------------------------------------------------------------------------------------------ what the node's modules ask
def windows_of(proc: str) -> tuple[list[dict], int] | None:
    """windowpick.list_windows through the worker: ([{hwnd, pid, start, name}], foreground hwnd), or None."""
    r = call("windows", {"proc": proc})
    if r is None:
        return None
    return list(r.get("windows") or []), int(r.get("fg") or 0)


def snapshot(proc: str, max_elements: int, max_depth: int, values: bool, pick: dict) -> dict | None:
    r = call("snapshot", {"proc": proc, "max": int(max_elements), "depth": int(max_depth), "values": bool(values), **_target(pick)}, timeout=60)
    return r


def window_text(name: str) -> list | None:
    r = call("window_text", {"name": name}, timeout=60)
    return None if r is None else list(r.get("rows") or [])


def _target(pick: dict) -> dict:
    """The window the node chose (windowpick.pick_env), in the worker's words."""
    try:
        return {"hwnd": int(pick["JARVIS_UI_HWND"]), "pid": int(pick["JARVIS_UI_PID"])} if pick.get("JARVIS_UI_HWND") else {}
    except (TypeError, ValueError):
        return {}


def act(op: str, proc: str, mutating: bool, **args) -> dict | None:
    """One action on the window of `proc`: the window is chosen by windowpick.py (several windows and a change = SeveralWindowsSayWhich, as before),
    then the worker does it. None = use PowerShell."""
    c = client()
    if c is None or not c.usable():
        return None
    from . import windowpick
    pick = windowpick.pick_env(proc, mutating=mutating)
    return call(op, {"proc": proc, **_target(pick), **args})
