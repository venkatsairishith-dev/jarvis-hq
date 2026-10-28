"""D77 (2026-10-27): a RESTRICTED runner for code a model wrote (the problem-set mission checks every solution by running it on the sample cases).

Running model-written code is dangerous, so it is never run in JARVIS's own process, shell or environment. One interface, two targets:

    Runner.execute(language, source, stdins, limits) -> [RunResult, ...]      one result per stdin (the source is compiled once)
    LocalRunner      this PC: a separate process per run (see below)
    RemoteRunner     the VM2 "Agent PC" (jarvis/lab): the same request sent through a transport the host lends; NOT wired here and never touched by this module
    choose_runner(cfg, transport)   VM2 when the config key `run_on_vm2` is on AND a transport answers, else the local runner (said in the result's note)

What the local runner does (best effort per OS; every result carries `isolation`, the honest list of what was and was not in force):
  * a minimal environment built from scratch (no JARVIS variable, no secret, HOME and TMP inside the run's temp folder), a temp working folder removed afterwards;
  * Linux / macOS: a tiny launcher (python -I -c) sets CPU-time, memory (RLIMIT_DATA), file-size, open-files and (non-root) process limits, starts a new session,
    enters a new network namespace (unshare, so there is no network, not even loopback) and a new PID namespace where the OS allows it, and applies Landlock so the
    process can WRITE only inside its temp folder and READ only the system and tool folders (not the owner's documents, not secrets);
    then it execs the compiler or interpreter. The parent also watches wall time, the process count and the memory of the whole tree and kills the tree;
  * Windows: a Job Object (jarvis/supervisor/jobobject.py) with a job memory limit, an active-process limit, a per-process CPU limit and kill-on-close; the parent kills
    the job on timeout. Windows has no per-process network or folder isolation without a firewall rule or an AppContainer: `isolation` says "not isolated" for those;
  * the output of each run is read through a size cap; more than the cap kills the run (status output_limit);
  * compilers and interpreters are looked up ONLY in a fixed allowlist of paths (config/problemset.json `tool_paths`, with built-in defaults), never on PATH; a missing
    one is a status `missing_tool` with a plain how-to-install line (installing is the owner's step).
It never opens the network, never installs anything, never writes outside its temp folder (where the OS allows that), and never raises into the caller.

Standard library only.
"""
from __future__ import annotations

import glob
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Callable, Protocol, Sequence

IS_WINDOWS = sys.platform == "win32"
IS_LINUX = sys.platform.startswith("linux")
LANGUAGES = ("python", "c", "cpp", "java", "javascript")
ALIASES = {"py": "python", "python3": "python", "c++": "cpp", "cxx": "cpp", "js": "javascript", "node": "javascript", "nodejs": "javascript"}
EXT = {"python": ".py", "c": ".c", "cpp": ".cpp", "java": ".java", "javascript": ".js"}

# status words (fixed; the mission reads them)
OK, RUNTIME_ERROR, TIMEOUT, MEMORY, OUTPUT_LIMIT, PROCESS_LIMIT = "ok", "runtime_error", "timeout", "memory", "output_limit", "process_limit"
COMPILE_ERROR, MISSING_TOOL, UNSUPPORTED, ERROR = "compile_error", "missing_tool", "unsupported_language", "error"


@dataclass(frozen=True)
class Limits:
    cpu_s: float = 5.0              # CPU seconds of one run
    wall_s: float = 10.0            # wall-clock seconds of one run
    mem_mb: int = 512               # memory of the run (the whole tree is watched)
    out_bytes: int = 64_000         # stdout kept; a run that writes 4x this is killed
    err_bytes: int = 16_000
    max_procs: int = 32             # live processes of one run (a fork bomb)
    fsize_mb: int = 8               # the biggest file a run may write in its temp folder
    compile_wall_s: float = 40.0
    compile_cpu_s: float = 40.0
    compile_mem_mb: int = 1536


@dataclass
class RunResult:
    status: str
    stdout: str = ""
    stderr: str = ""
    exit_code: int | None = None
    wall_s: float = 0.0
    truncated: bool = False
    note: str = ""
    isolation: dict = field(default_factory=dict)       # {"network": "unshared" | "not isolated: why", "fs_write": ..., "fs_read": ..., "procs": ..., "memory": ...}
    runner: str = "local"

    def as_dict(self) -> dict:
        return {"status": self.status, "stdout": self.stdout, "stderr": self.stderr, "exit_code": self.exit_code, "wall_s": round(self.wall_s, 3),
                "truncated": self.truncated, "note": self.note, "isolation": self.isolation, "runner": self.runner}


def limits_from(d: Any) -> Limits:
    """Limits from a dict (config / a remote request); unknown or wrong-typed keys keep the default."""
    base = Limits()
    if not isinstance(d, dict):
        return base
    kw = {}
    for k, v in d.items():
        if hasattr(base, k) and isinstance(v, (int, float)) and not isinstance(v, bool) and v > 0:
            kw[k] = type(getattr(base, k))(v)
    return replace(base, **kw)


def norm_language(name: Any) -> str:
    n = re.sub(r"[^a-z+#0-9]", "", str(name or "").lower())
    n = ALIASES.get(n, n)
    return n if n in LANGUAGES else ""


# ------------------------------------------------------------------------------------------------------------- comparing outputs
def norm_out(s: str) -> list[str]:
    lines = [ln.rstrip() for ln in str(s or "").replace("\r\n", "\n").replace("\r", "\n").split("\n")]
    while lines and not lines[-1]:
        lines.pop()
    while lines and not lines[0]:
        lines.pop(0)
    return lines


def same_output(actual: str, expected: str) -> bool:
    """Equal after trailing spaces and blank edge lines are ignored; token by token a number may differ by 1e-6 (relative) when the EXPECTED one is written with a decimal
    point or an exponent. Never looser than that: a wrong word, a missing token or an extra line is a difference."""
    a, e = norm_out(actual), norm_out(expected)
    if a == e:
        return True
    ta, te = " ".join(a).split(), " ".join(e).split()
    if len(ta) != len(te):
        return False
    for x, y in zip(ta, te):
        if x == y:
            continue
        try:
            fx, fy = float(x), float(y)
        except ValueError:
            return False
        if "." not in y and "e" not in y.lower():
            return False
        if abs(fx - fy) > 1e-6 * max(1.0, abs(fy)):
            return False
    return True


# ------------------------------------------------------------------------------------------------------------- the tool allowlist
INSTALL_HINTS = {
    "python": "Python 3 is not installed in the places JARVIS may run code from. Install it from python.org (tick 'Add to PATH' is not needed) and tell me to try again.",
    "c": "No C compiler (gcc) was found in the allowed places. Install MSYS2 or w64devkit (gcc), or use Python instead. Installing is your step.",
    "cpp": "No C++ compiler (g++) was found in the allowed places. Install MSYS2 or w64devkit (g++), or use Python instead. Installing is your step.",
    "java": "No Java JDK (javac and java) was found in the allowed places. Install a JDK (for example Temurin 21), then tell me to try again. Installing is your step.",
    "javascript": "Node.js was not found in the allowed places. Install Node.js LTS from nodejs.org, then tell me to try again. Installing is your step.",
}

DEFAULT_TOOL_PATHS: dict[str, dict[str, list[str]]] = {
    "posix": {
        "python": ["/usr/bin/python3", "/usr/local/bin/python3", "/opt/homebrew/bin/python3"],
        "gcc": ["/usr/bin/gcc", "/usr/local/bin/gcc", "/opt/homebrew/bin/gcc-*"],
        "g++": ["/usr/bin/g++", "/usr/local/bin/g++", "/opt/homebrew/bin/g++-*"],
        "javac": ["/usr/bin/javac", "/usr/lib/jvm/*/bin/javac", "/usr/local/bin/javac", "/opt/homebrew/opt/openjdk/bin/javac"],
        "java": ["/usr/bin/java", "/usr/lib/jvm/*/bin/java", "/usr/local/bin/java", "/opt/homebrew/opt/openjdk/bin/java"],
        "node": ["/usr/bin/node", "/usr/local/bin/node", "/opt/node*/bin/node", "/opt/homebrew/bin/node"],
    },
    "windows": {
        "python": ["%ProgramFiles%\\Python3*\\python.exe", "%LOCALAPPDATA%\\Programs\\Python\\Python3*\\python.exe", "D:\\jarvis\\tools\\python*\\python.exe",
                   "%LOCALAPPDATA%\\Python\\pythoncore-3*\\python.exe"],        # the Python install manager's layout (this laptop: pythoncore-3.14-64), laptop 2026-10-28
        "gcc": ["C:\\msys64\\ucrt64\\bin\\gcc.exe", "C:\\msys64\\mingw64\\bin\\gcc.exe", "C:\\w64devkit\\bin\\gcc.exe", "D:\\jarvis\\tools\\w64devkit\\bin\\gcc.exe",
                "%ProgramFiles%\\mingw-w64\\*\\mingw64\\bin\\gcc.exe", "C:\\mingw64\\bin\\gcc.exe"],            # C:\mingw64\bin: this laptop's own gcc (2026-10-28)
        "g++": ["C:\\msys64\\ucrt64\\bin\\g++.exe", "C:\\msys64\\mingw64\\bin\\g++.exe", "C:\\w64devkit\\bin\\g++.exe", "D:\\jarvis\\tools\\w64devkit\\bin\\g++.exe",
                "%ProgramFiles%\\mingw-w64\\*\\mingw64\\bin\\g++.exe", "C:\\mingw64\\bin\\g++.exe"],
        "javac": ["%ProgramFiles%\\Eclipse Adoptium\\jdk-*\\bin\\javac.exe", "%ProgramFiles%\\Java\\jdk*\\bin\\javac.exe", "%ProgramFiles%\\Microsoft\\jdk-*\\bin\\javac.exe",
                  "D:\\jarvis\\tools\\jdk*\\bin\\javac.exe"],
        "java": ["%ProgramFiles%\\Eclipse Adoptium\\jdk-*\\bin\\java.exe", "%ProgramFiles%\\Java\\jdk*\\bin\\java.exe", "%ProgramFiles%\\Microsoft\\jdk-*\\bin\\java.exe",
                 "D:\\jarvis\\tools\\jdk*\\bin\\java.exe"],
        "node": ["%ProgramFiles%\\nodejs\\node.exe", "D:\\jarvis\\tools\\node*\\node.exe"],
    },
}
NEEDS = {"python": ("python",), "c": ("gcc",), "cpp": ("g++",), "java": ("javac", "java"), "javascript": ("node",)}


def tool_paths_from(cfg: Any) -> dict[str, list[str]]:
    """The allowlist for this OS: the defaults, with a config's `tool_paths` (a dict of lists of absolute path patterns) replacing a tool's list. Relative patterns are dropped."""
    table = {k: list(v) for k, v in DEFAULT_TOOL_PATHS["windows" if IS_WINDOWS else "posix"].items()}
    given = cfg.get("tool_paths") if isinstance(cfg, dict) else None
    if isinstance(given, dict):
        for tool, pats in given.items():
            if tool in table and isinstance(pats, list):
                table[tool] = [p for p in pats if isinstance(p, str) and os.path.isabs(os.path.expandvars(p))]
    return table


def find_tool(tool: str, table: dict[str, list[str]]) -> str | None:
    """The first existing regular file matching an allowlisted pattern (newest-looking last glob match first); never PATH."""
    for pat in table.get(tool, []):
        pat = os.path.expandvars(pat)
        hits = sorted(glob.glob(pat), reverse=True) if any(c in pat for c in "*?[") else [pat]
        for h in hits:
            try:
                if os.path.isfile(h) and os.access(h, os.X_OK):
                    return os.path.realpath(h) if not IS_WINDOWS else h
            except OSError:
                continue
    return None


# ------------------------------------------------------------------------------------------------------------- the launcher (POSIX)
# Runs in a fresh interpreter (python -I -c LAUNCHER spec_json) in the child: sets the limits, the namespaces and Landlock, then execs the target. Reports what it
# managed on fd 3 as JSON. Every step is best effort and recorded; a failed isolation step never silently looks like a success.
LAUNCHER = r'''
import ctypes, json, os, resource, signal, sys
spec = json.loads(sys.argv[1])
rep = {}
def lim(name, soft, hard=None):
    try:
        resource.setrlimit(getattr(resource, name), (soft, soft if hard is None else hard)); return True
    except Exception as e:
        rep.setdefault("limit_errors", []).append(name + ":" + type(e).__name__); return False
os.setsid()
L = spec["limits"]
cpu = max(1, int(-(-float(L["cpu_s"]) // 1)))                  # whole seconds, rounded up (a limit of 0 would kill the program at once)
lim("RLIMIT_CPU", cpu, cpu + 1)
rep["memory"] = "limited" if lim("RLIMIT_DATA", int(L["mem_mb"]) * 1024 * 1024) else "not limited"
lim("RLIMIT_FSIZE", int(L["fsize_mb"]) * 1024 * 1024)
lim("RLIMIT_NOFILE", 256)
lim("RLIMIT_CORE", 0)
if spec.get("nproc") and os.geteuid() != 0:
    rep["procs"] = "limited" if lim("RLIMIT_NPROC", int(spec["nproc"])) else "not limited"
else:
    rep["procs"] = "watched by the parent (the process limit does not apply to this user)"
libc = ctypes.CDLL(None, use_errno=True)
libc.syscall.restype = ctypes.c_long
try:
    libc.prctl(38, 1, 0, 0, 0)                      # PR_SET_NO_NEW_PRIVS
except Exception:
    pass
# network + pid namespaces
NEWNET, NEWUSER, NEWPID = 0x40000000, 0x10000000, 0x20000000
def unshare(flags):
    return libc.unshare(flags) == 0
net = unshare(NEWUSER | NEWNET | NEWPID)
pidns = net
if not net:
    net = unshare(NEWNET); pidns = False
    if not net:
        net = False
rep["network"] = "unshared" if net else "not isolated: the OS refused a new network namespace"
rep["pid_namespace"] = bool(pidns)
# Landlock: write only in the run folder; read only the system and tool folders
def landlock():
    ABI = libc.syscall(444, None, 0, 1)
    if ABI < 1:
        return "not isolated: Landlock is not available on this kernel"
    EXEC, WF, RF, RD, RMD, RMF, MC, MD, MR, MS, MF, MB, MSYM, REF, TR, IOC = [1 << i for i in range(16)]
    handled = EXEC | WF | RF | RD | RMD | RMF | MC | MD | MR | MS | MF | MB | MSYM
    if ABI >= 2: handled |= REF
    if ABI >= 3: handled |= TR
    if ABI >= 5: handled |= IOC
    class Attr(ctypes.Structure):
        _fields_ = [("handled_access_fs", ctypes.c_uint64)]
    class Rule(ctypes.Structure):
        _pack_ = 1
        _fields_ = [("allowed_access", ctypes.c_uint64), ("parent_fd", ctypes.c_int32)]
    fd = libc.syscall(444, ctypes.byref(Attr(handled)), ctypes.sizeof(Attr), 0)
    if fd < 0:
        return "not isolated: Landlock refused the ruleset"
    filemask = EXEC | WF | RF | TR | (IOC if ABI >= 5 else 0)
    def add(path, access):
        try:
            p = os.open(path, os.O_PATH | os.O_CLOEXEC)
        except OSError:
            return
        try:
            isdir = os.path.isdir(path)
            a = access & handled
            if not isdir:
                a &= filemask
            if a:
                libc.syscall(445, fd, 1, ctypes.byref(Rule(a, p)), 0)
        finally:
            os.close(p)
    ro = EXEC | RF | RD
    for d in spec["read"]:
        add(d, ro)
    add(spec["workdir"], handled)
    for f in ("/dev/null", "/dev/zero", "/dev/urandom", "/dev/random"):
        add(f, RF | WF | TR | (IOC if ABI >= 5 else 0))
    if libc.syscall(446, fd, 0) != 0:
        os.close(fd)
        return "not isolated: Landlock could not be enforced"
    os.close(fd)
    return "restricted (Landlock ABI %d): writes only in the run folder, reads only system and tool folders" % ABI
try:
    rep["fs"] = landlock()
except Exception as e:
    rep["fs"] = "not isolated: " + type(e).__name__
try:
    os.write(spec["report_fd"], json.dumps(rep).encode()); os.close(spec["report_fd"])
except OSError:
    pass
os.chdir(spec["workdir"])
argv, env = spec["argv"], spec["env"]
def relay(pid, encode):
    _, st = os.waitpid(pid, 0)
    if os.WIFSIGNALED(st):
        sig = os.WTERMSIG(st)
        if encode:
            os._exit(128 + sig)                        # init of a PID namespace cannot be ended by its own signal: the signal is passed as 128 + n
        signal.signal(sig, signal.SIG_DFL)
        os.kill(os.getpid(), sig)
    os._exit(os.WEXITSTATUS(st))
if pidns:
    init = os.fork()                                   # the first child of a new PID namespace is its init: when it ends, everything in the namespace is killed
    if init:
        relay(init, False)
    prog = os.fork()                                   # init only waits; the program is its child, so CPU-limit and other signals reach it normally
    if prog:
        relay(prog, True)
os.execve(argv[0], argv, env)
'''


def _read_dirs(tool_paths: Sequence[str]) -> list[str]:
    base = ["/usr", "/lib", "/lib32", "/lib64", "/libx32", "/bin", "/sbin", "/opt", "/etc", "/proc", "/sys"]
    out = list(base)
    for t in tool_paths:
        for p in {t, os.path.realpath(t)}:
            d = os.path.dirname(os.path.dirname(p))
            if d and d != "/" and not any(d == b or d.startswith(b + "/") for b in base):
                out.append(d)
    return out


# ------------------------------------------------------------------------------------------------------------- process watching
def _proc_table() -> dict[int, tuple[int, int, int, int]]:
    """{pid: (ppid, session, rss_pages, pgrp)} from /proc (Linux); {} elsewhere."""
    table: dict[int, tuple[int, int, int, int]] = {}
    try:
        names = os.listdir("/proc")
    except OSError:
        return table
    for n in names:
        if not n.isdigit():
            continue
        try:
            with open(f"/proc/{n}/stat", "rb") as f:
                raw = f.read().decode("utf-8", "replace")
            rest = raw[raw.rindex(")") + 2:].split()
            ppid, pgrp, sess, rss = int(rest[1]), int(rest[2]), int(rest[3]), int(rest[21])
            table[int(n)] = (ppid, sess, rss, pgrp)
        except (OSError, ValueError, IndexError):
            continue
    return table


def _tree(root: int, table: dict[int, tuple[int, int, int, int]]) -> set[int]:
    """root, its descendants by parent links, and every process of its session / group (a double-forked child stays in the session)."""
    members = {pid for pid, (_pp, s, _r, pg) in table.items() if s == root or pg == root}
    if root in table:
        members.add(root)
    changed = bool(members)
    while changed:
        changed = False
        for pid, (ppid, _s, _rss, _pg) in table.items():
            if pid not in members and ppid in members:
                members.add(pid)
                changed = True
    return members


def _kill_tree(root: int, popen: subprocess.Popen | None = None) -> None:
    for _ in range(4):
        table = _proc_table()
        pids = _tree(root, table) | {root}
        for pid in pids:
            try:
                os.kill(pid, signal.SIGKILL)
            except (OSError, ProcessLookupError):
                pass
        try:
            os.killpg(root, signal.SIGKILL)
        except (OSError, ProcessLookupError):
            pass
        time.sleep(0.02)
        if not (_tree(root, _proc_table()) - {root}):
            break
    if popen is not None:
        try:
            popen.kill()
        except OSError:
            pass


class _Pump(threading.Thread):
    """Reads a pipe up to a cap (the rest is counted and dropped, so the child never blocks on a full pipe)."""

    def __init__(self, stream, cap: int):
        super().__init__(daemon=True)
        self.stream, self.cap, self.buf, self.total = stream, cap, bytearray(), 0

    def run(self):
        try:
            while True:
                chunk = self.stream.read(8192)
                if not chunk:
                    break
                self.total += len(chunk)
                room = self.cap - len(self.buf)
                if room > 0:
                    self.buf += chunk[:room]
        except (OSError, ValueError):
            pass


def _feed(stream, data: bytes) -> None:
    try:
        stream.write(data)
        stream.flush()
    except (OSError, ValueError):
        pass
    finally:
        try:
            stream.close()
        except OSError:
            pass


def _text(b: bytes | bytearray) -> str:
    return bytes(b).decode("utf-8", "replace")


MEM_WORDS = re.compile(r"MemoryError|std::bad_alloc|OutOfMemoryError|Cannot allocate memory|JavaScript heap out of memory|out of memory|memory allocation of \d+ bytes failed", re.I)


# ------------------------------------------------------------------------------------------------------------- the runners
class Runner(Protocol):
    name: str

    def execute(self, language: str, source: str, stdins: Sequence[str], limits: Limits | None = None) -> list[RunResult]: ...
    def tool_status(self, language: str) -> tuple[bool, str]: ...


class LocalRunner:
    """Runs on this PC in a restricted separate process (see the module docstring)."""
    name = "local"

    def __init__(self, tool_paths: dict[str, list[str]] | None = None, work_base: str | os.PathLike | None = None, cfg: dict | None = None,
                 clock: Callable[[], float] = time.monotonic):
        self.tools = tool_paths or tool_paths_from(cfg)
        wb = work_base or (cfg.get("work_dir") if isinstance(cfg, dict) and isinstance(cfg.get("work_dir"), str) and cfg.get("work_dir").strip() else None)
        self.work_base = Path(wb) if wb else None             # where each run's temp folder is made (the owner's rule: D: for anything that is not tiny); None = the system temp
        self.clock = clock

    # ---- tools
    def tool_status(self, language: str) -> tuple[bool, str]:
        lang = norm_language(language)
        if not lang:
            return False, f"I run {', '.join(LANGUAGES)}; '{str(language)[:20]}' is not one of them."
        missing = [t for t in NEEDS[lang] if not find_tool(t, self.tools)]
        if missing:
            return False, INSTALL_HINTS[lang]
        return True, ""

    # ---- one process
    def _env(self, work: Path, tool_dirs: list[str]) -> dict[str, str]:
        tmp = str(work / "tmp")
        if IS_WINDOWS:
            root = os.environ.get("SystemRoot", r"C:\Windows")
            return {"SystemRoot": root, "WINDIR": root, "PATH": os.pathsep.join(tool_dirs + [root + r"\System32"]), "TEMP": tmp, "TMP": tmp, "USERPROFILE": str(work),
                    "HOME": str(work), "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"}
        return {"PATH": os.pathsep.join(tool_dirs + ["/usr/bin", "/bin"]), "HOME": str(work), "TMPDIR": tmp, "TEMP": tmp, "TMP": tmp, "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8",
                "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1", "PYTHONDONTWRITEBYTECODE": "1", "PYTHONNOUSERSITE": "1", "JAVA_TOOL_OPTIONS": "-Dfile.encoding=UTF-8"}

    def _spawn_run(self, argv: list[str], work: Path, stdin: bytes, lim: Limits, *, compile_step: bool, tool_dirs: list[str], tool_files: list[str],
                   mem_mb: int | None = None, cpu_s: float | None = None, wall_s: float | None = None) -> RunResult:
        cpu = cpu_s if cpu_s is not None else lim.cpu_s
        wall = wall_s if wall_s is not None else lim.wall_s
        mem = mem_mb if mem_mb is not None else lim.mem_mb
        env = self._env(work, tool_dirs)
        t0 = self.clock()
        iso: dict = {}
        proc: subprocess.Popen | None = None
        job = None
        rfd = wfd = -1
        try:
            if IS_WINDOWS:
                proc, job, iso = self._popen_windows(argv, work, env, lim, cpu, mem)
            else:
                rfd, wfd = os.pipe()
                spec = {"limits": {"cpu_s": cpu, "mem_mb": mem, "fsize_mb": lim.fsize_mb}, "nproc": None if compile_step else lim.max_procs * 4, "workdir": str(work), "report_fd": wfd,
                        "read": _read_dirs(tool_files), "argv": argv, "env": env}
                full = [sys.executable, "-I", "-c", LAUNCHER, json.dumps(spec)]
                proc = subprocess.Popen(full, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, cwd=str(work), env={"PATH": "/usr/bin:/bin"},
                                        pass_fds=(wfd,), close_fds=True)
                os.close(wfd)
                wfd = -1
        except OSError as e:
            if rfd >= 0:
                os.close(rfd)
            if wfd >= 0:
                os.close(wfd)
            return RunResult(ERROR, note=f"the process could not be started ({type(e).__name__})", runner=self.name)
        out, err = _Pump(proc.stdout, lim.out_bytes * 4 + 1 if not compile_step else 200_000), _Pump(proc.stderr, lim.err_bytes if not compile_step else 100_000)
        out.start()
        err.start()
        threading.Thread(target=_feed, args=(proc.stdin, stdin), daemon=True).start()
        status, note = "", ""
        cap = lim.out_bytes if not compile_step else 200_000
        mem_cap = int(mem * 1.3 * 1024 * 1024) + 64 * 1024 * 1024
        pagesz = os.sysconf("SC_PAGE_SIZE") if hasattr(os, "sysconf") else 4096
        try:
            while True:
                if proc.poll() is not None:
                    out.join(timeout=0.5)           # a background process that kept the pipe open does not keep us waiting (the tree is killed below)
                    break
                el = self.clock() - t0
                if el > wall:
                    status, note = TIMEOUT, f"stopped after {wall:g} s (wall time)"
                    break
                if not compile_step and out.total > cap * 4:
                    status, note = OUTPUT_LIMIT, f"the output went past {cap * 4} bytes and the run was stopped"
                    break
                if IS_LINUX:
                    table = _proc_table()
                    members = _tree(proc.pid, table)
                    if len(members) > (lim.max_procs if not compile_step else 400):
                        status, note = PROCESS_LIMIT, f"more than {lim.max_procs} processes were started and the run was stopped"
                        break
                    if sum(table[p][2] for p in members if p in table) * pagesz > mem_cap:
                        status, note = MEMORY, f"the run used more than {mem} MB of memory and was stopped"
                        break
                time.sleep(0.02 if el < 1 else 0.05)
        finally:
            self._kill(proc, job)                   # always: nothing the run started may outlive it (background processes, a double fork)
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self._kill(proc, job)
        out.join(timeout=2)
        err.join(timeout=2)
        for s in (proc.stdout, proc.stderr):
            try:
                s.close()
            except OSError:
                pass
        if rfd >= 0:
            iso = self._read_report(rfd)
        elif job is not None:
            self._close_job(job)
        wall_s = self.clock() - t0
        so, se = _text(out.buf[:cap]), _text(err.buf)
        truncated = out.total > cap
        code = proc.returncode
        sig = -code if code is not None and code < 0 else (code - 128 if code is not None and iso.get("pid_namespace") and 129 <= code <= 159 else 0)
        if not status:
            if code == 0:
                status = OK
            elif sig == getattr(signal, "SIGXCPU", -1) or (IS_WINDOWS and code == 0xC0000044):      # the Windows job object's CPU limit ends the process with STATUS_QUOTA_EXCEEDED
                status, note = TIMEOUT, f"stopped after {cpu:g} s of CPU time"
            elif sig == getattr(signal, "SIGKILL", -1) and wall_s < wall:
                status, note = (MEMORY, "the run was killed by the memory limit") if MEM_WORDS.search(se) else (RUNTIME_ERROR, "killed by the system (the CPU or memory limit)")
            elif MEM_WORDS.search(se):
                status, note = MEMORY, "the run ran out of memory"
            else:
                status = RUNTIME_ERROR
                if sig:
                    note = f"ended by signal {sig}"
        return RunResult(status, so, se, code, wall_s, truncated, note, iso, self.name)

    @staticmethod
    def _read_report(rfd: int) -> dict:
        try:
            with os.fdopen(rfd, "rb") as f:
                data = f.read(8192)
            return json.loads(data.decode()) if data else {"network": "unknown (the launcher reported nothing)"}
        except (OSError, ValueError):
            return {"network": "unknown"}

    @staticmethod
    def _kill(proc: subprocess.Popen, job) -> None:
        if IS_WINDOWS:
            try:
                if job is not None:
                    from jarvis.supervisor import jobobject as J
                    J._k32.TerminateJobObject(job, 1)
            except Exception:  # noqa: BLE001
                pass
            try:
                proc.kill()
            except OSError:
                pass
        else:
            _kill_tree(proc.pid, proc)

    @staticmethod
    def _close_job(job) -> None:
        try:
            from jarvis.supervisor import jobobject as J
            J._k32.CloseHandle(job)
        except Exception:  # noqa: BLE001
            pass

    def _popen_windows(self, argv, work, env, lim: Limits, cpu: float, mem: int):
        """Windows: a Job Object with memory, active-process, CPU-time and kill-on-close limits. The process is put in the job right after it starts."""
        from jarvis.supervisor import jobobject as J
        job, problems = J.create_job(J.JobLimits(job_memory_bytes=int(mem * 1024 * 1024 * 2), kill_on_close=True, throttle=False,
                                                 active_processes=lim.max_procs, process_time_s=cpu))
        proc = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, cwd=str(work), env=env,
                                creationflags=0x08000000 | 0x00000200)          # CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP
        ok, err = J.adopt_pid(job, proc.pid)
        iso = {"memory": "job memory limit" if not problems else "partly limited: " + "; ".join(problems),
               "procs": "job active-process limit" if ok else f"not limited (the job refused the process: error {err})",
               "network": "not isolated: Windows has no per-process network switch here (no firewall rule or AppContainer)",
               "fs_write": "not isolated: the process runs as the owner; it is only started in a temp folder",
               "fs": "not isolated on Windows"}
        return proc, job, iso

    # ---- the whole job
    def execute(self, language: str, source: str, stdins: Sequence[str], limits: Limits | None = None) -> list[RunResult]:
        lim = limits or Limits()
        stdins = list(stdins) or [""]
        lang = norm_language(language)
        if not lang:
            return [RunResult(UNSUPPORTED, note=f"'{str(language)[:20]}' is not a language I run ({', '.join(LANGUAGES)})", runner=self.name) for _ in stdins]
        paths = {t: find_tool(t, self.tools) for t in NEEDS[lang]}
        if not all(paths.values()):
            return [RunResult(MISSING_TOOL, note=INSTALL_HINTS[lang], runner=self.name) for _ in stdins]
        try:
            base = self.work_base
            if base is not None:
                base.mkdir(parents=True, exist_ok=True)
            work = Path(tempfile.mkdtemp(prefix="jarvis-ps-", dir=str(base) if base else None)).resolve()
        except OSError as e:
            return [RunResult(ERROR, note=f"no temp folder ({type(e).__name__})", runner=self.name) for _ in stdins]
        try:
            (work / "tmp").mkdir()
            if not IS_WINDOWS:
                os.chmod(work, 0o700)
            return self._execute_in(lang, source, stdins, lim, work, paths)
        except Exception as e:  # noqa: BLE001 - never raise into the mission
            return [RunResult(ERROR, note=f"the runner failed ({type(e).__name__})", runner=self.name) for _ in stdins]
        finally:
            shutil.rmtree(work, ignore_errors=True)

    def _execute_in(self, lang: str, source: str, stdins: list[str], lim: Limits, work: Path, paths: dict[str, str | None]) -> list[RunResult]:
        tool_files = [p for p in paths.values() if p]
        tool_dirs = sorted({os.path.dirname(p) for p in tool_files})
        exe = ".exe" if IS_WINDOWS else ""
        java_main = "Main"
        if lang == "java":
            m = re.search(r"public\s+(?:final\s+)?class\s+([A-Za-z_]\w*)", source) or re.search(r"\bclass\s+([A-Za-z_]\w*)[^{;]*\{[^}]*static\s+void\s+main", source, re.S)
            java_main = m.group(1) if m else "Main"
        fname = {"python": "main.py", "c": "main.c", "cpp": "main.cpp", "java": f"{java_main}.java", "javascript": "main.js"}[lang]
        (work / fname).write_text(source, encoding="utf-8", newline="\n")
        compiled: RunResult | None = None
        if lang in ("c", "cpp"):
            cc = paths["gcc" if lang == "c" else "g++"]
            std = ["-std=c11"] if lang == "c" else ["-std=c++17"]
            argv = [cc, "-O2", "-pipe", *std, "-o", str(work / ("prog" + exe)), str(work / fname)] + (["-lm"] if lang == "c" else [])
            compiled = self._spawn_run(argv, work, b"", lim, compile_step=True, tool_dirs=tool_dirs, tool_files=tool_files, mem_mb=lim.compile_mem_mb,
                                       cpu_s=lim.compile_cpu_s, wall_s=lim.compile_wall_s)
            run_argv = [str(work / ("prog" + exe))]
        elif lang == "java":
            argv = [paths["javac"], "-encoding", "UTF-8", "-d", str(work), str(work / fname)]
            compiled = self._spawn_run(argv, work, b"", lim, compile_step=True, tool_dirs=tool_dirs, tool_files=tool_files, mem_mb=lim.compile_mem_mb + 1024,
                                       cpu_s=lim.compile_cpu_s, wall_s=lim.compile_wall_s)
            heap = max(64, lim.mem_mb - 64)
            run_argv = [paths["java"], f"-Xmx{heap}m", "-Xss16m", "-XX:+UseSerialGC", "-XX:TieredStopAtLevel=1", "-Xshare:auto", "-cp", str(work), java_main]
        elif lang == "python":
            run_argv = [paths["python"], "-I", "-B", str(work / fname)]
        else:
            run_argv = [paths["node"], f"--max-old-space-size={max(32, lim.mem_mb - 64)}", str(work / fname)]
        if compiled is not None and compiled.status != OK:
            if compiled.status in (RUNTIME_ERROR,):
                msg = (compiled.stderr or compiled.stdout)[:2000]
                bad = RunResult(COMPILE_ERROR, stderr=msg, exit_code=compiled.exit_code, wall_s=compiled.wall_s, note="the code did not compile", isolation=compiled.isolation, runner=self.name)
            else:
                bad = RunResult(compiled.status, stderr=compiled.stderr[:2000], exit_code=compiled.exit_code, wall_s=compiled.wall_s, note="compiling: " + compiled.note,
                                isolation=compiled.isolation, runner=self.name)
            return [bad for _ in stdins]
        extra_mem = 768 if lang in ("java", "javascript") else 0         # the JVM and V8 keep code and metadata outside the heap
        run_lim = replace(lim, mem_mb=lim.mem_mb + extra_mem)
        results = []
        for s in stdins:
            results.append(self._spawn_run(run_argv, work, s.encode("utf-8", "replace"), run_lim, compile_step=False, tool_dirs=tool_dirs, tool_files=tool_files,
                                           mem_mb=lim.mem_mb + extra_mem))
        return results


def mem_hit(stderr: str) -> bool:
    return bool(MEM_WORDS.search(stderr or ""))


class RemoteRunner:
    """The VM2 'Agent PC' target (jarvis/lab). The transport is lent by the host: transport(request) -> {"ok": bool, "results": [RunResult dicts], "error": str}. This module
    never connects to VM2 or to any server itself. A transport that raises, answers badly or says not ok leaves the caller to fall back (see choose_runner)."""
    name = "vm2"

    def __init__(self, transport: Callable[[dict], dict], probe: Callable[[], bool] | None = None):
        self.transport, self.probe = transport, probe

    def reachable(self) -> bool:
        try:
            return bool(self.probe()) if self.probe else True
        except Exception:  # noqa: BLE001
            return False

    def tool_status(self, language: str) -> tuple[bool, str]:
        return (True, "") if norm_language(language) else (False, f"'{str(language)[:20]}' is not a language I run")

    def execute(self, language: str, source: str, stdins: Sequence[str], limits: Limits | None = None) -> list[RunResult]:
        lim = limits or Limits()
        stdins = list(stdins) or [""]
        lang = norm_language(language)
        if not lang:
            return [RunResult(UNSUPPORTED, note="not a language I run", runner=self.name) for _ in stdins]
        try:
            reply = self.transport({"op": "run_code", "language": lang, "source": source, "stdins": stdins, "limits": lim.__dict__})
            rows = reply.get("results") if isinstance(reply, dict) and reply.get("ok") else None
            if not isinstance(rows, list) or len(rows) != len(stdins):
                raise ValueError(str((reply or {}).get("error") if isinstance(reply, dict) else "bad reply")[:120])
            out = []
            for r in rows:
                out.append(RunResult(str(r.get("status") or ERROR), str(r.get("stdout") or "")[:lim.out_bytes], str(r.get("stderr") or "")[:lim.err_bytes], r.get("exit_code"),
                                     float(r.get("wall_s") or 0), bool(r.get("truncated")), str(r.get("note") or "")[:200], dict(r.get("isolation") or {}), self.name))
            return out
        except Exception as e:  # noqa: BLE001
            return [RunResult(ERROR, note=f"VM2 did not run it: {str(e)[:120] or type(e).__name__}", runner=self.name) for _ in stdins]


class ChosenRunner:
    """What choose_runner returns: the runner to use, and a plain line saying why (the report shows it)."""

    def __init__(self, runner: Runner, why: str):
        self.runner, self.why = runner, why


def choose_runner(cfg: dict | None, vm2_transport: Callable[[dict], dict] | None = None, vm2_probe: Callable[[], bool] | None = None,
                  local: LocalRunner | None = None) -> ChosenRunner:
    """VM2 is the preferred target WHEN the config key `run_on_vm2` is on (default off: whether VM2 is up is the owner's server state). On but no transport lent, or
    VM2 not answering: the local restricted runner, and the line says so. This function never touches VM2 itself."""
    local = local or LocalRunner(cfg=cfg)
    if not (isinstance(cfg, dict) and cfg.get("run_on_vm2") is True):
        return ChosenRunner(local, "ran on this PC in a restricted process")
    if vm2_transport is None:
        return ChosenRunner(local, "VM2 is switched on in the settings but no connection to it is set up here, so the code ran on this PC in a restricted process")
    remote = RemoteRunner(vm2_transport, vm2_probe)
    if not remote.reachable():
        return ChosenRunner(local, "VM2 did not answer, so the code ran on this PC in a restricted process")
    return ChosenRunner(remote, "ran on VM2 (the Agent PC)")
