"""JARVIS lab runner: the isolation boundary between the code the lab tests and the state that decides "verified".

Why this exists (re-review H2 follow-up, 2026-10-19). Everything the lab checks is code a lab agent wrote. If that code runs as the
same OS user as the process that writes the verdict, nothing the runner does afterwards can prove the verdict is genuine: the code can
write the verdict file through a map, inherit the verifier's stdout, ptrace the verifier, or lie to the test harness from inside it.
So the delivered code now runs as its own, dedicated, unprivileged identity (the SANDBOX identity, `jarvis-sandbox`), started by the
root runner through a small launcher that drops privileges in a fixed order and cannot be asked to do anything else. The verdict state
(slot, capture, result channels, the read-only copy of the deliverable) is owned by the runner (root) or the VERIFY identity
(`jarvis-verify`), in directories the sandbox identity cannot enter. A container backend (rootless podman, no network, no capabilities,
read-only root) is described and its command line is built here, but it needs a runtime that this server does not have: it is NOT RUN.

What the boundary is, in plain words, and what it is not:
  - It is OS-user separation inside one kernel. It stops the sandbox identity from reading or writing any file, descriptor, process memory or
    process of the runner and of the verify identity, from regaining privileges (no_new_privs, groups cleared, setuid(0) refused), and from
    using more than its resource limits. It does not stop a kernel bug, a root-owned file with loose permissions, a runner that is itself
    compromised, or anything done by a process that is not separated (the keep-id test mode and any run with a missing identity have NO
    separation and are reported as mode "none": nothing can be "verified" there).
  - The sandbox identity can still list the process table and read command lines (the nonce is visible). It cannot read environments,
    descriptors or memory of other users' processes, and the nonce alone writes nothing.
  - A verifier whose own tools run delivered code is NOT inside this boundary (that depends on the agent CLI's terminal backend, which is
    not installed on the Linux server and was not checked). Such a verdict is never "verified": see Boundary.mode.

Fifth pass (LAB-RT red team): shared world-writable folders and System V IPC are emptied of the sandbox identity's things after every step
(purge_uid_files), "full" needs a verifier identity of its own and one process per test, floats like 1e999 are refused, the launcher makes the
sandbox the OOM killer's first choice, and the self-test proves no capabilities remain and the verify home / runner token are unreadable.

Pure helpers (configuration, container command line, strict JSON) are testable anywhere; the launcher and the self-test need root and the
two users, and the tests create them in a throw-away container.
"""
import ctypes
import json
import os
import pwd
import re
import resource
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import time

# ---- configuration (environment, read at import; tests set the module attributes)
REQUEST = os.environ.get("JARVIS_LAB_ISOLATION", "auto")                    # auto | identity | container | none
SANDBOX_USER = os.environ.get("JARVIS_LAB_SANDBOX_USER", "jarvis-sandbox")  # runs ALL delivered / tested code
VERIFY_USER = os.environ.get("JARVIS_LAB_VERIFY_USER", "jarvis-verify")     # runs the LLM verifier; owns the verdict slot
VERIFIER_TOOLS = os.environ.get("JARVIS_LAB_VERIFIER_TOOLS", "")            # "sandboxed": the OWNER states the verifier's tools run in a sandbox
CONTAINER_IMAGE = os.environ.get("JARVIS_LAB_SANDBOX_IMAGE", "")
RUNNER_TOKEN = os.environ.get("JARVIS_LAB_TOKEN_FILE", "/etc/jarvis-lab/runner.token")   # the self-test proves the sandbox cannot open it
PYTHON3 = "/usr/bin/python3" if os.path.exists("/usr/bin/python3") else sys.executable

LIMITS = {"cpu_s": 120, "as_bytes": 2 << 30, "nproc": 64, "nofile": 64, "fsize": 64 << 20, "core": 0}
SANDBOX_ENV_ALLOW = ("PATH", "HOME", "TMPDIR", "LANG", "PYTHONDONTWRITEBYTECODE", "PYTHONHASHSEED", "TERM")
MODES = ("full", "identity", "container", "none")


# ---------------------------------------------------------------------------------------------------------------- strict JSON
class StrictJSONError(ValueError):
    pass


def _no_dupes(pairs):
    out = {}
    for k, v in pairs:
        if k in out:
            raise StrictJSONError(f"duplicate key {k!r}")
        out[k] = v
    return out


def _bad_const(name):
    raise StrictJSONError(f"{name} is not allowed")


def _finite_float(text):
    """json.loads calls parse_constant only for the words NaN / Infinity; a number such as 1e999 reaches float() and becomes inf silently."""
    value = float(text)
    if value != value or value in (float("inf"), float("-inf")):
        raise StrictJSONError(f"the number {text[:20]!r} is not finite")
    return value


def _depth(x, limit, cur=1):
    if cur > limit:
        raise StrictJSONError("nested too deep")
    if isinstance(x, dict):
        for v in x.values():
            _depth(v, limit, cur + 1)
    elif isinstance(x, list):
        for v in x:
            _depth(v, limit, cur + 1)


def strict_loads(data, max_bytes=200_000, max_depth=8):
    """Parse ONE JSON document from bytes or str: UTF-8 only, no duplicate keys, no NaN/Infinity, limited size and depth, nothing after it.
    Raises StrictJSONError (a ValueError)."""
    if isinstance(data, str):
        data = data.encode("utf-8", "strict")
    if len(data) > max_bytes:
        raise StrictJSONError("too large")
    try:
        text = data.decode("utf-8", "strict")
    except UnicodeDecodeError as e:
        raise StrictJSONError("not UTF-8") from e
    try:
        obj = json.loads(text, object_pairs_hook=_no_dupes, parse_constant=_bad_const, parse_float=_finite_float)
    except StrictJSONError:
        raise
    except RecursionError as e:             # a RuntimeError, not a ValueError: deep nesting inside the size limit used to crash the caller
        raise StrictJSONError("nested too deep") from e
    except ValueError as e:
        raise StrictJSONError("not valid JSON") from e
    _depth(obj, max_depth)
    return obj


def strict_raw_decode(text, idx=0, max_depth=8):
    """Like json.JSONDecoder().raw_decode(text, idx) with the same strictness; returns (object, end)."""
    dec = json.JSONDecoder(object_pairs_hook=_no_dupes, parse_constant=_bad_const, parse_float=_finite_float)
    try:
        obj, end = dec.raw_decode(text, idx)
    except StrictJSONError:
        raise
    except RecursionError as e:
        raise StrictJSONError("nested too deep") from e
    except ValueError as e:
        raise StrictJSONError("not valid JSON") from e
    _depth(obj, max_depth)
    return obj, end


# ---------------------------------------------------------------------------------------------------------------- the boundary
class Boundary:
    """What isolation this attempt really has. Built by resolve(); `backend` is "identity", "container" or "none"; `reasons` says why not
    when it is none. `self_test` is "not run", "ok" or "failed: ..." (run_self_test). mode() is the word the report and the kernel use."""

    def __init__(self, backend="none", reasons=(), sandbox=None, verify=None, agent=None):
        self.backend, self.reasons = backend, list(reasons)
        self.sandbox, self.verify, self.agent = sandbox, verify, agent
        self.self_test = "not run"
        self.verifier_tools = VERIFIER_TOOLS == "sandboxed"

    @property
    def active(self):
        return self.backend in ("identity", "container") and self.self_test == "ok"

    def mode(self):
        """full: separated execution AND the owner states the verifier's tools are sandboxed; identity / container: the tested code is separated
        (the verifier's own tools are not known to be); none: no separation, or the self-test failed."""
        if not self.active:
            return "none"
        # "full" also needs a verifier identity of its own: with no verify user the verifier runs as the agent user (the worker's), so the verdict
        # state is owned by the identity whose processes and files the worker shaped. The owner's statement alone does not make that full.
        return "full" if self.verifier_tools and self.separate_verifier() else self.backend

    def describe(self, processes=0, granularity="", cpu_seconds=0.0):
        """The `isolation` block of the report (the kernel validates this exact shape)."""
        reasons = list(self.reasons)
        # A suite of more than SANDBOX_MAX_PROCS tests runs in groups (granularity "chunk:N"): a test can then answer for the others in its group
        # (shown by the red team), so such a run is never "full".
        chunked = str(granularity).startswith("chunk")
        if chunked and self.active:
            reasons.append("the tests ran in groups (" + str(granularity)[:12] + "): one test can answer for the others in its group, so not full")
        if self.backend in ("identity", "container") and self.self_test != "ok":
            reasons.append("isolation self-test: " + self.self_test)
        if self.active and not self.verifier_tools:
            reasons.append("the verifier's own tools are not known to run in a sandbox (JARVIS_LAB_VERIFIER_TOOLS is not set)")
        elif self.active and not self.separate_verifier():
            reasons.append("the verifier has no identity of its own (no verify user): not full")
        mode = self.backend if (chunked and self.mode() == "full") else self.mode()
        return {"mode": mode, "backend": self.backend, "self_test": self.self_test[:120],
                "verifier_tools_isolated": bool(self.verifier_tools and self.active and self.separate_verifier() and not chunked),
                "separate_verifier_identity": bool(self.sandbox is not None and self.separate_verifier()),
                "sandbox_uid": self.sandbox.pw_uid if self.sandbox else -1, "verify_uid": self.verifier_pw().pw_uid if self.verifier_pw() else -1,
                "runner_uid": os.geteuid(), "processes": int(processes), "granularity": granularity[:20], "cpu_seconds": round(float(cpu_seconds), 2),
                "reasons": [str(r)[:160] for r in reasons][:6]}

    def agent_uid(self):
        return self.agent.pw_uid if self.agent else -1

    def separate_verifier(self):
        """The verifier has an identity of its own: a verify user that is neither the agent user nor the sandbox identity."""
        return bool(self.verify is not None and self.verify.pw_uid != self.agent_uid()
                    and (self.sandbox is None or self.verify.pw_uid != self.sandbox.pw_uid))

    def verifier_pw(self):
        """The identity the verifier runs as: the verify user when it exists, else the agent user (and then there is no separate identity)."""
        return self.verify or self.agent


def _getpw(name):
    try:
        return pwd.getpwnam(name)
    except KeyError:
        return None


def container_available():
    return bool(shutil.which("podman")) and bool(CONTAINER_IMAGE)


def resolve(agent_pw=None, request=None):
    """Decide the backend. Never raises, never claims more than exists: every missing piece is a reason and the backend is then "none".
    A Boundary with backend identity/container is still not active until run_self_test says ok."""
    req = (request or REQUEST or "auto").lower()
    reasons = []
    if req not in ("auto", "identity", "container", "none"):
        return Boundary("none", [f"unknown isolation setting {req!r}"], agent=agent_pw)
    if req == "none":
        return Boundary("none", ["isolation is switched off by configuration"], agent=agent_pw)
    sandbox, verify = _getpw(SANDBOX_USER), _getpw(VERIFY_USER)
    if req in ("auto", "container") and container_available() and os.geteuid() != 0:
        pass                                                         # rootless podman can run as the runner's own user (documented backend)
    if req == "container":
        if not container_available():
            return Boundary("none", ["container isolation needs podman and JARVIS_LAB_SANDBOX_IMAGE; one is missing"], agent=agent_pw)
        return Boundary("container", [], sandbox=sandbox, verify=verify, agent=agent_pw)
    # identity backend
    if os.geteuid() != 0:
        reasons.append("the runner is not root, so it cannot start code under another user")
    if sandbox is None:
        reasons.append(f"the sandbox user {SANDBOX_USER!r} does not exist")
    elif sandbox.pw_uid == 0:
        reasons.append("the sandbox user is root")
    elif agent_pw is not None and sandbox.pw_uid == agent_pw.pw_uid:
        reasons.append("the sandbox user is the agent user (no separation)")
    elif verify is not None and sandbox.pw_uid == verify.pw_uid:
        reasons.append("the sandbox user is the verify user (no separation)")
    if reasons:
        if req == "auto" and container_available():
            return Boundary("container", [], sandbox=sandbox, verify=verify, agent=agent_pw)
        return Boundary("none", reasons, sandbox=sandbox, verify=verify, agent=agent_pw)
    if verify is None:
        reasons.append(f"the verify user {VERIFY_USER!r} does not exist: the verifier runs as the agent user (its verdict state is not separate)")
    return Boundary("identity", reasons, sandbox=sandbox, verify=verify, agent=agent_pw)


# ---------------------------------------------------------------------------------------------------------------- the launcher
# Started as root by the runner; becomes the sandbox identity and then the target program, in this order and no other:
# umask, descriptor allowlist, resource limits, working folder, supplementary groups cleared, gid, uid (all three ids), proof that root
# cannot be regained, no_new_privs, exec with a scrubbed environment. Any failure ends with exit 126 and a message, never with the target
# running half-configured.
LAUNCHER_SRC = r'''
import ctypes, json, os, resource, sys
spec = json.loads(sys.argv[1])
def die(msg):
    try:
        os.write(2, ("sandbox launcher: " + msg + "\n").encode())
    finally:
        os._exit(126)
try:
    os.umask(0o077)
    keep = set(spec["keep_fds"]) | {0, 1, 2}
    for name in os.listdir("/proc/self/fd"):
        fd = int(name)
        if fd not in keep:
            try:
                os.close(fd)
            except OSError:
                pass
    try:                                                     # the sandbox is the first thing the kernel's OOM killer picks, never the runner
        with open("/proc/self/oom_score_adj", "w") as fh:
            fh.write("1000")
    except OSError:
        pass
    L = spec["limits"]
    for res, (soft, hard) in ((resource.RLIMIT_CPU, (L["cpu_s"], L["cpu_s"] + 5)), (resource.RLIMIT_AS, (L["as_bytes"], L["as_bytes"])),
                              (resource.RLIMIT_NPROC, (L["nproc"], L["nproc"])), (resource.RLIMIT_NOFILE, (L["nofile"], L["nofile"])),
                              (resource.RLIMIT_FSIZE, (L["fsize"], L["fsize"])), (resource.RLIMIT_CORE, (0, 0))):
        resource.setrlimit(res, (soft, hard))
    os.chdir(spec["cwd"])
    os.setgroups([])
    os.setresgid(spec["gid"], spec["gid"], spec["gid"])
    os.setresuid(spec["uid"], spec["uid"], spec["uid"])
    try:
        os.setuid(0)
    except OSError:
        pass
    else:
        die("root could be regained")
    if os.getuid() == 0 or os.geteuid() == 0:
        die("still root")
    libc = ctypes.CDLL(None, use_errno=True)
    if libc.prctl(38, 1, 0, 0, 0) != 0:                      # PR_SET_NO_NEW_PRIVS
        die("no_new_privs refused")
except SystemExit:
    raise
except BaseException as e:
    die("setup failed: %s: %s" % (type(e).__name__, e))
try:
    os.execve(spec["argv"][0], spec["argv"], spec["env"])
except BaseException as e:
    die("exec failed: %s" % e)
'''


def sandbox_env(scratch, extra=None):
    env = {"PATH": "/usr/local/bin:/usr/bin:/bin", "HOME": scratch, "TMPDIR": scratch, "LANG": "C.UTF-8", "PYTHONDONTWRITEBYTECODE": "1",
           "PYTHONHASHSEED": "0", "TERM": "dumb"}
    for k, v in (extra or {}).items():
        if k in SANDBOX_ENV_ALLOW:
            env[k] = v
    return env


def launcher_command(pw, argv, cwd, env, keep_fds=(), limits=None):
    """The command line the runner (root) executes to run `argv` as `pw` with the boundary applied. Pure."""
    spec = {"uid": pw.pw_uid, "gid": pw.pw_gid, "cwd": cwd, "argv": list(argv), "env": dict(env), "keep_fds": sorted(set(keep_fds)),
            "limits": dict(LIMITS, **(limits or {}))}
    return [PYTHON3, "-I", "-B", "-c", LAUNCHER_SRC, json.dumps(spec)]


def container_command(argv, scratch_host, work_host, result_fd, harness_host, limits=None, image=None):
    """The podman command line of the container backend (documented, not run on this server). Pure.
    No network, no capabilities, read-only root, no_new_privs, a user namespace with the image's unprivileged user, pids / memory / cpu
    limits, a private tmpfs for scratch, the deliverable copy and the harness mounted read-only, one extra descriptor (the result pipe) kept
    open with --preserve-fds, nothing else from the runner."""
    L = dict(LIMITS, **(limits or {}))
    return ["podman", "run", "--rm", "--network=none", "--cap-drop=all", "--read-only", "--security-opt=no-new-privileges",
            "--userns=auto", "--user=65534:65534", f"--pids-limit={L['nproc']}", f"--memory={L['as_bytes']}", "--memory-swap=" + str(L["as_bytes"]),
            "--cpus=1", f"--ulimit=cpu={L['cpu_s']}", f"--ulimit=fsize={L['fsize']}", "--ulimit=core=0", f"--ulimit=nofile={L['nofile']}",
            "--tmpfs=/scratch:rw,size=128m,mode=0700,noexec,nosuid", "--env=HOME=/scratch", "--env=TMPDIR=/scratch", "--env=PYTHONDONTWRITEBYTECODE=1",
            f"--volume={work_host}:/work:ro", f"--volume={harness_host}:/harness:ro", "--workdir=/scratch", f"--preserve-fds={max(0, result_fd - 2)}",
            image or CONTAINER_IMAGE, "python3", "-I", "-B", *argv]


def spawn_sandboxed(boundary, argv, scratch, stdout, stderr, pass_fds=(), limits=None, cwd=None, stdin=subprocess.DEVNULL):
    """Start `argv` inside the boundary. Identity backend: through the launcher as the sandbox user. Container backend: not implemented on
    this server (raises). Descriptors: 0 (stdin), 1, 2 and `pass_fds` only. start_new_session so the whole group can be killed."""
    if boundary.backend == "identity":
        cmd = launcher_command(boundary.sandbox, argv, cwd or scratch, sandbox_env(scratch), keep_fds=pass_fds, limits=limits)
        return subprocess.Popen(cmd, cwd="/", stdin=stdin, stdout=stdout, stderr=stderr, pass_fds=tuple(pass_fds), close_fds=True,
                                start_new_session=True, env={"PATH": "/usr/bin:/bin"})
    raise RuntimeError("the container backend is not available on this server (NOT RUN): " + " ".join(container_command(argv, scratch, scratch, 3, scratch)[:4]))


# ---------------------------------------------------------------------------------------------------------------- process helpers
def uid_pids(uid, exclude=()):
    """Every live (non-zombie) process of `uid`, by /proc."""
    out = []
    for name in os.listdir("/proc"):
        if not name.isdigit() or int(name) in exclude:
            continue
        try:
            if os.stat(f"/proc/{name}").st_uid != uid:
                continue
            with open(f"/proc/{name}/stat", "rb") as fh:
                state = fh.read().decode("utf-8", "replace").rsplit(")", 1)[1].split()[0]
        except (OSError, IndexError):
            continue
        if state != "Z":
            out.append(int(name))
    return out


def kill_uid(uid, wait_s=10.0):
    """SIGKILL every process of a DEDICATED identity (the sandbox and verify users run nothing else) and wait. Returns (killed pids, survivors)."""
    if uid == 0 or uid == os.geteuid():
        return [], []
    killed, deadline = set(), time.time() + wait_s
    while True:
        pids = uid_pids(uid)
        if not pids:
            return sorted(killed), []
        killed.update(pids)
        for pid in pids:
            for sig in (signal.SIGCONT, signal.SIGKILL):
                try:
                    os.kill(pid, sig)
                except OSError:
                    break
        time.sleep(0.03)
        if time.time() > deadline:
            return sorted(killed), uid_pids(uid)


# Places any user may write to, and so places the sandbox identity can leave things in for the next test or for the next job. The scratch
# folder of a step is deleted by the supervisor; these are not, and a test could prepare state for another (or fill them) through them.
SHARED_DIRS = ("/tmp", "/var/tmp", "/dev/shm", "/run/lock", "/var/lock", "/dev/mqueue")


def purge_uid_files(uid, dirs=None):
    """Delete everything the dedicated identity `uid` owns in the world-writable folders (SHARED_DIRS), links removed and never followed, and its
    System V shared memory segments, semaphores and message queues. Call it only when no process of `uid` is alive (after kill_uid): it is then
    race-free. Never for root. Returns how many things were removed."""
    if uid is None or uid == 0:
        return 0
    removed = 0
    for top in (SHARED_DIRS if dirs is None else dirs):
        try:
            for _dirpath, dirnames, filenames, dirfd in os.fwalk(top, topdown=False, follow_symlinks=False):
                for name in list(filenames) + list(dirnames):
                    try:
                        st = os.stat(name, dir_fd=dirfd, follow_symlinks=False)
                        if st.st_uid != uid:
                            continue
                        if stat.S_ISDIR(st.st_mode):
                            os.rmdir(name, dir_fd=dirfd)
                        else:
                            os.unlink(name, dir_fd=dirfd)
                        removed += 1
                    except OSError:
                        pass
        except OSError:
            continue
    return removed + (purge_ipc(uid) if dirs is None else 0)


IPC_PURGE_SRC = r'''
import ctypes, os
libc = ctypes.CDLL(None, use_errno=True)
me, n = os.getuid(), 0
for table, call in (("shm", lambda i: libc.shmctl(i, 0, None)), ("sem", lambda i: libc.semctl(i, 0, 0, 0)), ("msg", lambda i: libc.msgctl(i, 0, None))):
    try:
        with open("/proc/sysvipc/" + table) as fh:
            head = fh.readline().split()
            rows = [ln.split() for ln in fh]
        col = head.index({"shm": "shmid", "sem": "semid", "msg": "msqid"}[table])
        for row in rows:
            if me in (int(row[head.index("uid")]), int(row[head.index("cuid")])) and call(int(row[col])) == 0:
                n += 1
    except (OSError, ValueError, IndexError):
        pass
print(n)
'''


def purge_ipc(uid):
    """Remove the System V shared memory segments, semaphores and message queues created or owned by `uid` (/proc/sysvipc lists them). It is done by
    a process that BECOMES that identity first (the same launcher as the sandbox): the owner may always remove its own objects, and the runner
    needs no extra capability for it. Returns the count (0 when it cannot be done, e.g. the runner is not root)."""
    try:
        pw = pwd.getpwuid(uid)
        cmd = launcher_command(pw, [PYTHON3, "-I", "-B", "-c", IPC_PURGE_SRC], "/", {"PATH": "/usr/bin:/bin"})
        r = subprocess.run(cmd, cwd="/", stdin=subprocess.DEVNULL, capture_output=True, timeout=20, env={"PATH": "/usr/bin:/bin"})
        return int(r.stdout.strip() or 0) if r.returncode == 0 else 0
    except (KeyError, ValueError, OSError, subprocess.SubprocessError):
        return 0


# ---------------------------------------------------------------------------------------------------------------- the self-test
PROBE_SRC = r'''
import ctypes, json, os, resource, sys
a = json.loads(sys.argv[1])
res = {}
def attempt(name, fn):
    try:
        fn()
        res[name] = "ALLOWED"
    except PermissionError:
        res[name] = "denied"
    except OSError as e:
        res[name] = "denied" if e.errno in (1, 13, 2) else "errno %d" % e.errno
def listdir(p): os.listdir(p)
def openf(p, fl): os.close(os.open(p, fl))
def readf(p):
    with open(p, "rb") as fh: fh.read(1)
attempt("list_state_dir", lambda: listdir(a["slot_dir"]))
attempt("open_state_file_rw", lambda: openf(a["slot_file"], os.O_RDWR))
attempt("open_state_file_ro", lambda: openf(a["slot_file"], os.O_RDONLY))
attempt("create_in_state_dir", lambda: openf(a["slot_dir"] + "/new", os.O_CREAT | os.O_WRONLY))
attempt("rename_state_file", lambda: os.rename(a["slot_file"], a["slot_file"] + ".x"))
attempt("symlink_in_state_dir", lambda: os.symlink("/etc/passwd", a["slot_dir"] + "/l"))
attempt("hardlink_state_file", lambda: os.link(a["slot_file"], os.path.join(a["own"], "hl")))
attempt("write_runner_dir", lambda: openf(a["runner_dir"] + "/new", os.O_CREAT | os.O_WRONLY))
attempt("read_verifier_environ", lambda: readf("/proc/%d/environ" % a["vpid"]))
attempt("read_verifier_mem", lambda: openf("/proc/%d/mem" % a["vpid"], os.O_RDONLY))
attempt("list_verifier_fds", lambda: listdir("/proc/%d/fd" % a["vpid"]))
attempt("read_verifier_cwd", lambda: os.readlink("/proc/%d/cwd" % a["vpid"]))
attempt("read_runner_environ", lambda: readf("/proc/%d/environ" % a["rpid"]))
for i, fdn in enumerate(a["runner_fds"]):
    attempt("open_runner_fd_%d" % i, lambda fdn=fdn: openf("/proc/%d/fd/%d" % (a["rpid"], fdn), os.O_RDWR))
def ptrace():
    libc = ctypes.CDLL(None, use_errno=True)
    if libc.ptrace(16, a["vpid"], 0, 0) != -1:        # PTRACE_ATTACH
        return
    raise PermissionError(ctypes.get_errno())
attempt("ptrace_verifier", ptrace)
def kill0():
    os.kill(a["vpid"], 19)                             # SIGSTOP
attempt("signal_verifier", kill0)
attempt("regain_root", lambda: os.setuid(0))
for name, path in a["private"].items():                # folders and files that hold the verifier's / the runner's credentials
    attempt("private_" + name, lambda path=path: (listdir(path) if os.path.isdir(path) else openf(path, os.O_RDONLY)))
caps = {}
for line in open("/proc/self/status"):
    if line.split(":")[0] in ("CapInh", "CapPrm", "CapEff", "CapAmb"):
        caps[line.split(":")[0]] = int(line.split(":")[1].strip(), 16)
res["capabilities_dropped"] = "denied" if len(caps) == 4 and not any(caps.values()) else "ALLOWED " + json.dumps(caps)
res["uid_not_root"] = "denied" if os.getuid() != 0 and os.geteuid() != 0 else "ALLOWED"
res["groups_empty"] = "denied" if os.getgroups() == [] else "ALLOWED"
libc = ctypes.CDLL(None, use_errno=True)
res["no_new_privs"] = "denied" if libc.prctl(39, 0, 0, 0, 0) == 1 else "ALLOWED"
fds = []
for n in os.listdir("/proc/self/fd"):
    try:
        fds.append(os.readlink("/proc/self/fd/" + n))
    except OSError:
        pass
extra = [t for t in fds if not (t.startswith("pipe:") or t in ("/dev/null", "/proc/%d/fd" % os.getpid()) or t.startswith("socket:") and False)]
res["fd_allowlist"] = "denied" if not extra else "ALLOWED " + ",".join(extra)[:80]
res["env_allowlist"] = "denied" if set(os.environ) <= set(a["env_allow"]) else "ALLOWED " + ",".join(sorted(set(os.environ) - set(a["env_allow"])))
L = a["limits"]
got = {"nproc": resource.getrlimit(resource.RLIMIT_NPROC)[0], "nofile": resource.getrlimit(resource.RLIMIT_NOFILE)[0],
       "fsize": resource.getrlimit(resource.RLIMIT_FSIZE)[0], "core": resource.getrlimit(resource.RLIMIT_CORE)[0],
       "cpu_s": resource.getrlimit(resource.RLIMIT_CPU)[0], "as_bytes": resource.getrlimit(resource.RLIMIT_AS)[0]}
res["limits_applied"] = "denied" if all(got[k] == L[k] for k in got) else "ALLOWED " + json.dumps(got)
sys.stdout.write(json.dumps(res))
'''


def run_self_test(boundary, agent_pw=None):
    """Run the probe as the sandbox identity against a real arrangement (a verifier-identity process holding a state file and a secret, runner
    descriptors, a state directory) and set boundary.self_test to "ok" or "failed: <what>". Fail closed: any error is a failure. Cheap
    (about a second); the runner runs it before every verification."""
    if boundary.backend != "identity":
        boundary.self_test = "failed: no backend to test" if boundary.backend == "none" else "failed: the container backend was not run (NOT RUN)"
        return boundary.self_test
    base = fd_r = fd_w = vproc = tmpfd = None
    try:
        vpw = boundary.verifier_pw()
        base = tempfile.mkdtemp(prefix="lab-selftest-")
        os.chmod(base, 0o711)
        slot_dir = os.path.join(base, "slot")
        os.mkdir(slot_dir, 0o750)
        os.chown(slot_dir, 0, vpw.pw_gid)
        os.chmod(slot_dir, 0o750)
        slot_file = os.path.join(slot_dir, "VERIFY.json")
        with open(slot_file, "w") as fh:
            fh.write("{}")
        os.chown(slot_file, vpw.pw_uid, vpw.pw_gid)
        os.chmod(slot_file, 0o600)
        runner_dir = os.path.join(base, "runner")
        os.mkdir(runner_dir, 0o755)                                   # root-owned: nothing may be created in it
        own = tempfile.mkdtemp(prefix="lab-selftest-own-")
        os.chown(own, boundary.sandbox.pw_uid, boundary.sandbox.pw_gid)
        fd_r, fd_w = os.pipe()                                        # descriptors the runner holds (a pipe, a private file)
        tmpfd = os.open(os.path.join(base, "private"), os.O_RDWR | os.O_CREAT, 0o600)
        # the verifier stand-in: the verify identity, holding the state file open, with a secret in its environment
        vcode = "import os,sys,time\nfd=os.open(sys.argv[1],os.O_RDWR)\nsys.stdout.write('ready\\n');sys.stdout.flush()\ntime.sleep(60)\n"
        vcmd = launcher_command(vpw, [PYTHON3, "-I", "-B", "-c", vcode, slot_file], "/", {"PATH": "/usr/bin:/bin", "SECRET": "nonce-123"})
        vproc = subprocess.Popen(vcmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL, start_new_session=True)
        if vproc.stdout.readline().strip() != b"ready":
            raise RuntimeError("the verifier stand-in did not start")
        args = {"slot_dir": slot_dir, "slot_file": slot_file, "runner_dir": runner_dir, "own": own, "vpid": vproc.pid, "rpid": os.getpid(),
                "runner_fds": [fd_r, fd_w, tmpfd], "env_allow": list(SANDBOX_ENV_ALLOW), "limits": dict(LIMITS),
                "private": {"verify_home": vpw.pw_dir, "runner_token": RUNNER_TOKEN}}
        r, w = os.pipe()
        p = spawn_sandboxed(boundary, [PYTHON3, "-I", "-B", "-c", PROBE_SRC, json.dumps(args)], own, stdout=w, stderr=subprocess.DEVNULL)
        os.close(w)
        out = b""
        end = time.time() + 30
        while time.time() < end:
            chunk = os.read(r, 65536)
            if not chunk:
                break
            out += chunk
        os.close(r)
        try:
            p.wait(timeout=10)
        except subprocess.TimeoutExpired:
            os.killpg(p.pid, signal.SIGKILL)
            raise RuntimeError("the probe did not finish")
        res = json.loads(out.decode("utf-8", "strict"))
        bad = sorted(k for k, v in res.items() if v != "denied")
        if p.returncode != 0:
            bad.append(f"probe exit {p.returncode}")
        expected = {"list_state_dir", "open_state_file_rw", "ptrace_verifier", "read_verifier_environ", "regain_root", "limits_applied", "fd_allowlist",
                    "capabilities_dropped", "private_verify_home", "private_runner_token"}
        if not expected <= set(res):
            bad.append("probe incomplete")
        # the arrangement itself must be real: the files are what they claim to be
        if os.stat(slot_file).st_uid == boundary.sandbox.pw_uid:
            bad.append("state file owned by the sandbox user")
        boundary.self_test = "ok" if not bad else "failed: " + ", ".join(bad)[:100]
    except BaseException as e:                                            # includes everything: fail closed
        boundary.self_test = f"failed: {type(e).__name__}: {str(e)[:60]}"
    finally:
        if vproc is not None:
            try:
                os.killpg(vproc.pid, signal.SIGKILL)
            except OSError:
                pass
            vproc.wait()
            if vproc.stdout:
                vproc.stdout.close()
        for fd in (fd_r, fd_w, tmpfd):
            if fd is not None:
                try:
                    os.close(fd)
                except OSError:
                    pass
        for d in (base, locals().get("own")):
            if d:
                shutil.rmtree(d, ignore_errors=True)
    return boundary.self_test
