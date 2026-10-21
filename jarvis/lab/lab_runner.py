"""JARVIS lab runner: executes, verifies and repairs outcome tasks that JARVIS dispatched to the lab.

Runs as root inside the WSL lab box with a runner token (root-only file) that can only fetch lab jobs and report task
progress; the lab agents never see it. For each job:
  1. work      a lab agent (Hermes, OpenCode or goose) runs as the unprivileged agent user in its own folder,
               with the instructions and the acceptance criteria;
  2. verify    a separate verifier session checks every criterion itself (commands, tests, files) and writes its verdict;
               the worker's own claim is never taken as proof. Audit M8 (2026-10-18): the worker and the verifier are the same
               OS user, so a process the worker left behind (background job, `setsid`) could forge the verdict. Therefore every
               process the agent user started since the attempt began is killed after the worker ends (also after a normal
               exit) and again after the verifier ends; the verdict is read from a fresh folder made after that, under a random
               name, and counts only if it carries this run's nonce, task id, job id and attempt and the delivered files did not
               change while it was checked. Second pass (same day): the verifier shares its OS user with the code it runs, so a
               process forked by that code can read the nonce and write the verdict file. No secret can prevent that, so the
               verdict FILE is no longer the proof: (1) the slot folder is root-owned and holds one pre-made file, written in place and
               watched by inotify (two writers, a replaced file, a link or a late write void it); (2) "verified" needs the RUNNER'S OWN
               run of the delivered tests (`trusted_execution`), which the kernel requires; without runnable tests the task can only
               end "completed, NOT verified". Third pass (re-review H2, 2026-10-18): inotify cannot see an `mmap` store and coalesces
               events, so a second agent-user process could flip an honest FAIL in the file in place; and the test count was parsed
               from text the tests printed. Now: (3) the verifier's FINAL ANSWER (its stdout, captured by the runner into a file only
               the runner owns, hashed) is the authority for `passed` and the criteria rows; VERIFY.json is a cross-check that is
               copied and sealed at the instant the verifier exits (before the sweep), compared again after every agent-user process
               is dead, and must agree with the answer; any difference or missing half voids the attempt; (4) the runner counts the
               delivered tests itself through its own harness (a separate collection process, then a run that reports every test to
               a runner-owned pipe); text the tests print is never parsed, a premature exit leaves a gap, and the record carries the
               harness result hash and the hash of the collected test ids, which the verdict names and the kernel checks.
               Fourth pass (2026-10-19, lab_isolation.py): all delivered code now runs as its own identity (`jarvis-sandbox`), one process per
               test id, started by a root launcher with limits and an allowlist; the verifier runs as `jarvis-verify` in a read-only copy and owns
               the verdict state in folders the sandbox cannot enter; a self-test runs before every verification; without the identities (or as a
               non-root runner, or on a failed self-test) the boundary is "none" and nothing is "verified"; only the owner's statement that the
               verifier's own tools are sandboxed makes the mode "full".
               What this still cannot stop: see the note above `reap_since` and jarvis/caretaker/README.md;
  3. repair    if verification fails and attempts remain, a repair attempt gets the verifier's findings;
  4. report    every stage, the evidence and the verdict go to the kernel; the task ends completed or failed, never
               "probably done". Cancelled tasks stop the worker; paused tasks freeze it (SIGSTOP) until resumed.
Custom code, justified in docs/01_RESEARCH_AUDIT.md: Hermes Kanban (closest match: --goal judge, retries) cannot take
work from another host; this runner is the bridge, and it uses the agents and their CLIs as they are.
"""
import ctypes
import errno
import hashlib
import hmac
import json
import os
import pwd
import re
import secrets
import shutil
import stat
import signal
import struct
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))     # python3 -I (the unit's command line) does not add the script's own folder
import lab_isolation as ISO          # the isolation boundary (identities, launcher, self-test); lives next to this file

ENDPOINT = os.environ.get("JARVIS_ENDPOINT", "http://100.125.91.51:8790")
TOKEN_FILE = "/etc/jarvis-lab/runner.token"            # root 600
AGENT = "agent"
try:
    PW = pwd.getpwnam(AGENT)
except KeyError:             # not the lab box: the pure helpers below (follow_status, process_tree) stay importable for tests
    PW = None
HOME = PW.pw_dir if PW else os.path.expanduser("~")
WORK = os.path.join(HOME, "work", "jobs")
LOCAL_BIN = os.path.join(HOME, ".local", "bin")
POLL_S = 10
VERIFY_FSIZE_BLOCKS = 400_000                                 # ulimit -f for the verify identity (512-byte blocks in sh: about 200 MB)
VERIFY_TIMEOUT_S = 900                                        # wall clock of one verifier session
VERIFY_BIN = os.environ.get("JARVIS_LAB_VERIFY_BIN", "")      # where the verify user's agent CLI is installed (when it is not the agent user's)


def api(path, body=None):
    tok = open(TOKEN_FILE).read().strip()
    r = urllib.request.Request(ENDPOINT + path, data=json.dumps(body or {}).encode(), method="POST",
                               headers={"Content-Type": "application/json", "Authorization": "Bearer " + tok})
    try:
        with urllib.request.urlopen(r, timeout=30) as resp:
            return resp.status, json.loads(resp.read() or b"{}")
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read() or b"{}")
        except ValueError:
            return e.code, {}
    except OSError as e:
        return 0, {"error": type(e).__name__}


# Environment names that carry or point at a GitHub/git credential (GH_TOKEN, GITHUB_TOKEN, GH_CONFIG_DIR, GIT_ASKPASS, GIT_CONFIG_*, SSH_AUTH_SOCK ...).
# An agent's environment is built from scratch (`env -i`), so these can only arrive through the goose env file, which is filtered (completion H).
GIT_HOST_CREDENTIAL_ENV = re.compile(r"^(GH_|GITHUB_|GIT_ASKPASS|GIT_CONFIG|GIT_SSH|GIT_CREDENTIAL|GIT_TERMINAL|SSH_AUTH_SOCK|SSH_ASKPASS|NETRC|CREDENTIAL)", re.I)


def agent_env(kind, pw=None):
    # The agent's own bin goes LAST: a worker once planted ~/.local/bin/python3 (rewriting print(1+1) to 3, to game a
    # check) and, first on PATH, it shadowed the real Python for every later task. System tools always win now.
    # pw: run as another identity (the verify user): its own home, name and bin folder.
    home, name, uid = (pw.pw_dir, pw.pw_name, pw.pw_uid) if pw is not None else (HOME, AGENT, PW.pw_uid)
    env = {"HOME": home, "USER": name, "LOGNAME": name, "LANG": "C.UTF-8", "TERM": "dumb",
           "PATH": f"/usr/local/bin:/usr/bin:/bin:{os.path.join(home, '.local', 'bin') if pw is not None else LOCAL_BIN}",
           "XDG_RUNTIME_DIR": f"/run/user/{uid}", "DBUS_SESSION_BUS_ADDRESS": f"unix:path=/run/user/{uid}/bus", "DISPLAY": ":1"}
    if kind == "goose":
        try:
            for line in open(os.path.join(HOME, ".jarvis-goose.env")):
                if "=" in line and not line.startswith("#"):
                    k, v = line.rstrip("\n").split("=", 1)
                    if GIT_HOST_CREDENTIAL_ENV.search(k.strip()):
                        continue                         # completion H: a GitHub/git credential never reaches an agent through its env file
                    env[k] = v
        except OSError:
            pass
    return env


def command(kind, prompt, folder, max_turns=60):
    if kind == "hermes":
        return [os.path.join(LOCAL_BIN, "hermes"), "chat", "-q", prompt, "--oneshot", "--quiet", "--in", folder, "--max-turns", str(max_turns)]
    if kind == "opencode":
        return [os.path.join(LOCAL_BIN, "opencode"), "run", "--dir", folder, prompt]
    return [os.path.join(LOCAL_BIN, "goose"), "run", "--no-session", "--max-turns", str(max_turns), "-t", prompt]


STOPPED_BY_OWNER, TASK_CLOSED, STOPPED_BY_KILL = "stopped by the owner", "task closed", "stopped by the kill switch"
STOP_REASONS = (STOPPED_BY_OWNER, TASK_CLOSED, STOPPED_BY_KILL)      # why a worker or verifier was stopped from outside
STATUS_EVERY_S = 5                                                   # how often a running worker asks the kernel whether to go on


def follow_status(code, reply, frozen=False):
    """What a running worker must do after asking the kernel for its job's status: (action, reason).

    action is "stop" (kill the worker and every process it started; reason says why), "freeze" (SIGSTOP: the owner paused
    the task), "thaw" (SIGCONT: it was resumed) or "none". Pure: no I/O, so it is tested without the lab box.

    The kill switch comes first (audit SEC-D10, 2026-10-18): the status used to carry only the task's state, so a worker
    already running ignored the kill switch until its task was cancelled by hand. A network blip, an error or a kernel restart
    (any answer that is not a clear 200) never stops the work, except the kernel's own "kill switch is on" refusal."""
    reply = reply if isinstance(reply, dict) else {}
    if code == 503 and "kill switch" in str(reply.get("reason", "")).lower():
        return "stop", STOPPED_BY_KILL
    if code != 200:
        return "none", ""
    if reply.get("killed") is True:
        return "stop", STOPPED_BY_KILL
    state = reply.get("state")
    if state in ("cancelled", "failed", "completed"):
        return "stop", STOPPED_BY_OWNER if state == "cancelled" else TASK_CLOSED
    if reply.get("cancel_requested") is True:
        return "stop", STOPPED_BY_OWNER                          # the owner cancelled this very job
    if state == "paused":
        return ("none", "") if frozen else ("freeze", "")
    return ("thaw", "") if frozen else ("none", "")


def process_tree(root_pid, proc="/proc"):
    """root_pid and every descendant of it (children, grandchildren ...), read from /proc. A worker's helpers that started
    their own session or process group (a daemonised server, `setsid`) are not reached by killing the process group, so the
    kill walks the parent links as well. Linux only; returns [root_pid] where /proc is unavailable."""
    parent = {}
    try:
        names = os.listdir(proc)
    except OSError:
        return [root_pid]
    for name in names:
        if not name.isdigit():
            continue
        try:
            with open(os.path.join(proc, name, "stat"), "rb") as fh:
                data = fh.read().decode("utf-8", "replace")
            parent[int(name)] = int(data[data.rindex(")") + 2:].split()[1])     # field 4 (ppid), after the "(comm)" that may hold spaces
        except (OSError, ValueError, IndexError):
            continue
    out, todo = [root_pid], [root_pid]
    while todo:
        cur = todo.pop()
        for pid, ppid in parent.items():
            if ppid == cur and pid not in out:
                out.append(pid)
                todo.append(pid)
    return out


def kill_tree(pid, hard=True):
    """Stop the worker started with start_new_session (its process group) and every descendant found through /proc.
    hard=False sends SIGTERM first and waits briefly for the group (the owner's cancel); hard=True is SIGKILL at once (the kill
    switch). The tree is read BEFORE anything is signalled, so children that outlive their parent are still found."""
    tree = process_tree(pid)
    sigs = [signal.SIGKILL] if hard else [signal.SIGTERM, signal.SIGKILL]
    for sig in sigs:
        for target in tree:
            try:
                os.kill(target, signal.SIGCONT)                 # a frozen process ignores TERM until it is thawed
            except OSError:
                pass
        try:
            os.killpg(pid, sig)
        except OSError:
            pass
        for target in tree:
            try:
                os.kill(target, sig)
            except OSError:
                pass
        if sig != signal.SIGKILL:
            deadline = time.time() + 15
            while time.time() < deadline and any(_alive(t) for t in tree):
                time.sleep(0.5)
    return tree


def _alive(pid):
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    try:                                                         # a zombie waiting for its parent is already dead
        with open(f"/proc/{pid}/stat", "rb") as fh:
            return fh.read().decode("utf-8", "replace").rsplit(")", 1)[1].split()[0] != "Z"
    except (OSError, IndexError):
        return True


# ---- audit M8: nothing the worker started may outlive the worker, and the verdict must be this run's own
def uptime():
    """Seconds since boot (the clock /proc start times use)."""
    try:
        with open("/proc/uptime") as fh:
            return float(fh.read().split()[0])
    except (OSError, ValueError, IndexError):
        return 0.0


def process_table(proc="/proc"):
    """{pid: {"ppid", "pgrp", "sid", "uid", "start" (seconds since boot), "zombie"}} for every readable process."""
    try:
        tick = float(os.sysconf("SC_CLK_TCK"))
    except (ValueError, OSError, AttributeError):
        tick = 100.0
    out = {}
    try:
        names = os.listdir(proc)
    except OSError:
        return out
    for name in names:
        if not name.isdigit():
            continue
        try:
            with open(os.path.join(proc, name, "stat"), "rb") as fh:
                data = fh.read().decode("utf-8", "replace")
            f = data[data.rindex(")") + 2:].split()          # after "(comm)": state is f[0], ppid f[1], pgrp f[2], session f[3]
            out[int(name)] = {"ppid": int(f[1]), "pgrp": int(f[2]), "sid": int(f[3]), "start": int(f[19]) / tick,
                              "uid": os.stat(os.path.join(proc, name)).st_uid, "zombie": f[0] == "Z"}
        except (OSError, ValueError, IndexError):
            continue
    return out


def _ancestors(table, pid):
    seen = set()
    while pid and pid not in seen:
        seen.add(pid)
        pid = table.get(pid, {}).get("ppid", 0)
    return seen


def reap_since(uid, since_up, roots=(), proc="/proc", wait_s=10.0, own=None):
    """Kill, with SIGKILL, every process that could be the worker's (or the verifier's) leftovers, then wait for them to die.
    Returns (killed pids, pids that did not die within wait_s).

    A process is a leftover when it belongs to the agent user `uid` and started at or after `since_up` (seconds since boot, taken
    just before the run): that catches background jobs, `setsid` and double-forked daemons even when they have been re-parented
    to init, changed directory or cleared their environment. Anything in the process tree, session or group of a pid in `roots`
    goes too. Processes that started BEFORE the run (the lab's own services) are left alone; the runner and its ancestors never
    die. The agent user is expected to run nothing else while a job is on: a process another service starts during a run is
    killed with the rest (it is started again by its supervisor).

    NOT stopped by this: code the verifier itself runs shares its OS user, so it can read the verifier's command line (the nonce),
    environment, descriptors and working folder, open VERIFY.json read-write and flip it through a map, and, if it INHERITED the
    verifier's stdout (nothing here makes sure it does not), truncate and rewrite the captured final answer too. What limits it now:
    the final answer, not the file, decides (a flipped or forged file disagrees with it and voids the attempt; a file written while the
    verifier printed nothing is not a verdict); the file is copied and sealed at the exit and compared after this sweep; the write
    audit is a secondary detector; the runner's own harness counts the delivered tests; the kernel binds one verdict per attempt to the
    evidence. What remains, exactly: a process that holds the verifier's stdout descriptor, or that attaches to the verifier itself
    (ptrace, /proc/<pid>/mem: same user), before the verifier exits, can make both halves agree on a forged pass, and tests that attack
    their own harness can forge the test report. Removing that needs the verifier's tool execution and the delivered tests in a
    different OS user or container (owner decision; jarvis/caretaker/README.md)."""
    table = process_table(proc)
    keep = _ancestors(table, own or os.getpid()) | {1}
    marked = set()
    for pid, info in table.items():
        if pid in keep or info["zombie"]:
            continue
        if uid is not None and info["uid"] == uid and info["start"] >= since_up - 0.05:
            marked.add(pid)
        elif any(info["pgrp"] == r or info["sid"] == r for r in roots):
            marked.add(pid)
    for r in roots:
        for pid in process_tree(r, proc):
            if pid not in keep and not table.get(pid, {}).get("zombie"):
                marked.add(pid)
    killed = sorted(marked)
    deadline = time.time() + wait_s
    pending = set(killed)
    while pending:
        for pid in pending:
            for sig in (signal.SIGCONT, signal.SIGKILL):           # thaw first: a frozen process still dies to KILL, but be sure
                try:
                    os.kill(pid, sig)
                except OSError:
                    break
        time.sleep(0.05)
        pending = {pid for pid in pending if _alive(pid)}
        if pending and time.time() > deadline:
            break
    return killed, sorted(pending)


def artifact_digest(folder):
    """One sha256 over the files the owner would receive (./deliver/): names and contents, links not followed. The runner takes
    it before and after the verifier runs; a verdict is void if the files changed in between."""
    h = hashlib.sha256()
    base = os.path.join(folder, DELIVER)
    try:
        names = sorted(os.listdir(base))
    except OSError:
        names = []
    for name in names:
        path = os.path.join(base, name)
        try:
            fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        except OSError:
            h.update(b"?" + name.encode("utf-8", "replace") + b"\0")        # a link or unreadable entry still changes the digest
            continue
        try:
            if not stat.S_ISREG(os.fstat(fd).st_mode):
                h.update(b"!" + name.encode("utf-8", "replace") + b"\0")
                continue
            h.update(b"f" + name.encode("utf-8", "replace") + b"\0")
            while True:
                chunk = os.read(fd, 1 << 20)
                if not chunk:
                    break
                h.update(chunk)
        finally:
            os.close(fd)
    return h.hexdigest()


# ---- root must never follow a link the worker can place (red-team 2026-10-19). The worker owns its folder tree (the job folder, ~/work, ~/.local/bin);
# a symlink it leaves where the runner is about to open, create, chown or move something would be followed with root's rights (it made the runner
# overwrite any root-owned file, its own module included). Everything the runner, as root, does inside that tree goes through these helpers: a
# directory chain is opened one component at a time with O_NOFOLLOW (a link made by anyone but root is refused; a link made by root is the
# administrator's and is followed), entries are opened relative to the directory descriptor, and files are created O_EXCL|O_NOFOLLOW.
def _allowed_owners(extra=None):
    owners = {0, os.geteuid()}
    if PW is not None:
        owners.add(PW.pw_uid)
    if extra is not None:
        owners.add(extra.pw_uid)
    return owners


def _walk_dirs(path, create_pw=None):
    """A descriptor for the directory `path` (absolute). Missing folders are made (and handed to `create_pw`) when create_pw is given. Raises OSError for a
    link not made by root, a non-directory, or a folder owned by somebody else than root, the runner or the agent user."""
    if not os.path.isabs(path):
        raise OSError(errno.EINVAL, "an absolute path is needed", path)
    todo = [x for x in os.path.normpath(path).split("/") if x]
    owners = _allowed_owners(create_pw)
    fd = os.open("/", os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    links = 0
    try:
        while todo:
            part = todo.pop(0)
            try:
                st = os.lstat(part, dir_fd=fd)
            except FileNotFoundError:
                if create_pw is None:
                    raise
                os.mkdir(part, 0o755, dir_fd=fd)
                st = None
            if st is not None and stat.S_ISLNK(st.st_mode):
                if st.st_uid != 0 or links >= 8:
                    raise OSError(errno.ELOOP, "a link in the path was not made by root", part)
                links += 1
                target = os.readlink(part, dir_fd=fd)
                if target.startswith("/"):
                    os.close(fd)
                    fd = os.open("/", os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
                todo = [x for x in os.path.normpath(target).split("/") if x and x != "."] + todo
                continue
            nfd = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=fd)
            if st is None:
                os.fchown(nfd, create_pw.pw_uid, create_pw.pw_gid)
            os.close(fd)
            fd = nfd
            if os.fstat(fd).st_uid not in owners:
                raise OSError(errno.EPERM, "a folder in the path belongs to somebody else", part)
        return fd
    except BaseException:
        os.close(fd)
        raise


def ensure_folder(path, pw):
    """Make `path` (and any missing parent) and hand the last folder to `pw`, without following links."""
    fd = _walk_dirs(path, create_pw=pw)
    try:
        os.fchown(fd, pw.pw_uid, pw.pw_gid)
    finally:
        os.close(fd)


def open_log(folder, name):
    """A new, empty log file `name` in `folder` for the runner to write ('w'): any link or file the worker left under that name is removed first (never
    followed, never written through) and the new file is created exclusively."""
    dfd = _walk_dirs(folder)
    try:
        for _ in range(4):
            try:
                os.unlink(name, dir_fd=dfd)
            except FileNotFoundError:
                pass
            try:
                fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o644, dir_fd=dfd)
            except FileExistsError:
                continue                                   # the worker put something back between the two calls: try again, bounded
            return os.fdopen(fd, "w")
        raise OSError(errno.EEXIST, "the log name keeps being re-created", name)
    finally:
        os.close(dfd)


def read_file_nofollow(path, limit=2_000_000):
    """The bytes of a regular file whose folder chain and last name contain no link the worker could have made."""
    dfd = _walk_dirs(os.path.dirname(path))
    try:
        fd = os.open(os.path.basename(path), os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=dfd)
        try:
            data = b""
            while len(data) <= limit:
                chunk = os.read(fd, 1 << 16)
                if not chunk:
                    break
                data += chunk
            return data[:limit]
        finally:
            os.close(fd)
    finally:
        os.close(dfd)


def write_file_nofollow(path, data, mode, pw):
    """Create `path` (which must not exist) with `data`, `mode`, owned by `pw`, without following any link."""
    dfd = _walk_dirs(os.path.dirname(path))
    try:
        fd = os.open(os.path.basename(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600, dir_fd=dfd)
        try:
            os.write(fd, data)
            os.fchmod(fd, mode)
            if pw is not None:
                os.fchown(fd, pw.pw_uid, pw.pw_gid)
        finally:
            os.close(fd)
    finally:
        os.close(dfd)


def _remove_at(dfd, name, dev=None, depth=0):
    """Remove `name` (a file, a link or a whole folder) inside the directory `dfd`, never following a link and never leaving the file system."""
    st = os.lstat(name, dir_fd=dfd)
    if not stat.S_ISDIR(st.st_mode):
        os.unlink(name, dir_fd=dfd)
        return
    if (dev is not None and st.st_dev != dev) or depth > 40:
        return
    fd = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=dfd)
    try:
        for entry in os.listdir(fd):
            _remove_at(fd, entry, st.st_dev, depth + 1)
    finally:
        os.close(fd)
    os.rmdir(name, dir_fd=dfd)


VERDICT_DIR = ".verdict-"


def clear_verdicts(folder):
    """Remove every verdict file or folder a previous run (or the worker) left in the workspace."""
    try:
        dfd = _walk_dirs(folder)
    except OSError:
        return
    try:
        for name in os.listdir(dfd):
            if name == "VERIFY.json" or name.startswith(VERDICT_DIR) or (name.startswith("VERIFY") and name.endswith(".json")):
                try:
                    _remove_at(dfd, name)
                except OSError:
                    pass
    finally:
        os.close(dfd)


def check_verdict(verdict, want):
    """Is this VERIFY.json the verdict of THIS verifier run? `want` = {"nonce", "task", "job", "attempt"}. Returns "" or the reason
    it is not. Pure. A verdict from an earlier attempt, another task or job, or a file written without the nonce is refused."""
    if not isinstance(verdict, dict):
        return "the verdict is not a JSON object"
    given = verdict.get("nonce")
    if not isinstance(given, str) or not hmac.compare_digest(given.encode("utf-8", "replace"), str(want["nonce"]).encode()):
        return "the verdict does not carry this run's nonce (stale, forged or written by something other than the verifier)"
    for key in ("task", "job", "attempt"):
        if type(verdict.get(key)) is not type(want[key]) or verdict.get(key) != want[key]:        # True == 1 in Python: the type counts too
            return f"the verdict names another {key} ({str(verdict.get(key))[:40]!r}, expected {str(want[key])[:40]!r})"
    if not isinstance(verdict.get("passed"), bool):
        return "the verdict has no boolean passed"
    return ""


VERDICT_FILE = "VERIFY.json"
VERDICT_MAX = 200_000                                  # bytes: a verdict is a few KB; more is not a verdict


# ---- inotify, read-only use: the runner (root) watches the verdict file and its folder while the verifier works.
_IN_MODIFY, _IN_ATTRIB, _IN_CLOSE_WRITE = 0x2, 0x4, 0x8
_IN_MOVED_FROM, _IN_MOVED_TO, _IN_CREATE, _IN_DELETE, _IN_DELETE_SELF, _IN_MOVE_SELF = 0x40, 0x80, 0x100, 0x200, 0x400, 0x800
_IN_Q_OVERFLOW, _IN_IGNORED = 0x4000, 0x8000


class WriteAudit:
    """Counts every write to the verdict file, whoever makes it and by which name (audit M8, second pass).

    The verifier shares its OS user with the code it runs, so a process forked by that code can read the verifier's command line
    (path and nonce) and write the verdict too. Nothing can tell the two writers apart by owner or by secret, but the NUMBER of
    writers is visible: an inotify watch on the file's inode reports every modification and every close of a writable handle, through
    any path or hard link (a rename or link into the folder is reported by the watch on the folder). The honest verifier writes the
    file once (one writable handle, one close). Two writes, writes after the close, a replaced file, changed attributes or an
    overflowing event queue (a forger rewriting in a loop) void the verdict.

    SECONDARY DETECTOR ONLY (re-review H2, 2026-10-18). It cannot see writes through a shared memory map (an `mmap` store generates no
    inotify event) and inotify coalesces repeated events, so an in-place rewrite can look like one clean write. Nothing here is relied on
    for the verdict's authenticity any more: the verifier's output channel is the authority and VerdictSlot compares content (see there)."""

    def __init__(self, dirfd, name):
        self.fd, self.error, self.wd_file, self.wd_dir = -1, "", -1, -1
        self.events, self.ignored = [], set()
        try:
            libc = ctypes.CDLL(None, use_errno=True)
            libc.inotify_add_watch.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_uint32]
            self.fd = libc.inotify_init1(os.O_NONBLOCK | os.O_CLOEXEC)
            if self.fd < 0:
                raise OSError(ctypes.get_errno(), "inotify_init1")
            base = f"/proc/self/fd/{dirfd}"                    # the folder by descriptor, not by a name the agent could swap
            self.wd_file = libc.inotify_add_watch(self.fd, os.path.join(base, name).encode(),
                                                  _IN_MODIFY | _IN_ATTRIB | _IN_CLOSE_WRITE | _IN_DELETE_SELF | _IN_MOVE_SELF)
            self.wd_dir = libc.inotify_add_watch(self.fd, base.encode(), _IN_CREATE | _IN_DELETE | _IN_MOVED_FROM | _IN_MOVED_TO)
            if self.wd_file < 0 or self.wd_dir < 0:
                raise OSError(ctypes.get_errno(), "inotify_add_watch")
        except (OSError, AttributeError, TypeError) as e:
            self.error = f"cannot audit writes to the verdict file ({type(e).__name__})"
            self.close()

    def drain(self):
        """Read what the kernel queued; keeps it in self.events (judge looks at all of it) and returns the new events."""
        out = self._read()
        self.events.extend(out)
        return out

    def absorb(self):
        """Drain and return how many events there are now: the runner's own change (its seal) is bracketed by two calls."""
        self.drain()
        return len(self.events)

    def ignore(self, start, end):
        """Forget the ATTRIB events on the file between two absorb() marks: the runner's own chown/chmod of the sealed verdict file."""
        for i in range(start, end):
            wd, mask = self.events[i]
            if wd == self.wd_file and (mask & _IN_ATTRIB) and not mask & (_IN_MODIFY | _IN_CLOSE_WRITE | _IN_DELETE_SELF | _IN_MOVE_SELF):
                self.ignored.add(i)

    def _read(self):
        out = []
        if self.fd < 0:
            return out
        while True:
            try:
                buf = os.read(self.fd, 1 << 16)
            except BlockingIOError:
                break
            except OSError:
                break
            if not buf:
                break
            pos = 0
            while pos + 16 <= len(buf):
                wd, mask, _cookie, n = struct.unpack_from("iIII", buf, pos)
                out.append((wd, mask))
                pos += 16 + n
        return out

    def judge(self):
        """"" when the events show exactly one honest-looking write, else why not (or the "no VERIFY.json" sentence for none)."""
        if self.error:
            return self.error
        self.drain()
        events = [e for i, e in enumerate(self.events) if i not in self.ignored]
        if any(m & _IN_Q_OVERFLOW for _, m in events):
            return "the verdict file was written over and over (event queue overflowed); only one write is accepted"
        if any(wd == self.wd_dir for wd, _ in events):
            return "the verdict file was replaced, renamed or linked instead of written in place"
        mine = [m for wd, m in events if wd == self.wd_file]
        if any(m & (_IN_ATTRIB | _IN_DELETE_SELF | _IN_MOVE_SELF | _IN_IGNORED) for m in mine):
            return "the verdict file's attributes were changed or it was moved or deleted"
        closes = [i for i, m in enumerate(mine) if m & _IN_CLOSE_WRITE]
        if not closes:
            return "the verifier wrote no VERIFY.json"
        if len(closes) > 1:
            return f"the verdict file was written {len(closes)} times (by more than one process?); only one write is accepted"
        if any(m & _IN_MODIFY for m in mine[closes[0] + 1:]):
            return "the verdict file was changed after the verifier closed it"
        return ""

    def close(self):
        if self.fd >= 0:
            try:
                os.close(self.fd)
            except OSError:
                pass
        self.fd = -1


class VerdictSlot:
    """Where THIS verifier run writes its verdict FILE (a cross-check; the verifier's OUTPUT is the authority, see VerifierOutput).

    The folder is made by the runner after the worker's processes are dead, under a random name; when the runner is root the folder
    stays ROOT-owned (mode 755) and holds one file, VERIFY.json, which the runner creates empty and gives to the agent user (mode 600).
    The agent user can therefore write that one file in place but cannot create, remove, rename or link anything in the folder. The
    runner keeps the folder open by descriptor and holds its own READ-ONLY descriptor of the file's inode from the start.

    Re-review H2 (2026-10-18): a second agent-user process can open that file O_RDWR, `mmap` it and flip bytes in place with no inotify
    event, so the file alone can never be the proof. What is done instead:
      1. `freeze()` runs at the instant the verifier process exits (before the sweep of the agent user's processes): it reads the file
         ONCE through the held descriptor and then SEALS it (mode 400, owned by root when the runner is root), so no later open succeeds;
      2. after every agent-user process is dead, `read()` reads the file twice more and compares all three with the frozen copy: any
         difference (a detached writer, an mmap that was still open at the exit, anything) voids the attempt;
      3. the caller (handle) takes the verdict from the verifier's output and uses this file only to cross-check it (see settle_verdict).
    A forger that is alive and writes BEFORE the verifier exits is not stopped by 1-2 (it is the same user as the verifier); it is caught
    only because the output channel disagrees with the file."""

    def __init__(self, folder, owner=None, closed=False):
        """owner: the identity (a pwd entry) that writes the verdict file (default: the agent user). closed: the folder is root:owner-group mode
        750, so no other user (the sandbox identity above all) can even enter it; the caller made `folder` itself unreachable for them too."""
        self.name = VERDICT_DIR + secrets.token_hex(8)
        self.path = os.path.join(folder, self.name)
        self.verdict_path = os.path.join(self.path, VERDICT_FILE)
        self.nonce = secrets.token_hex(16)
        owner = owner or PW
        self.uid = owner.pw_uid if owner is not None else os.getuid()
        self.frozen, self.sealed, self.seal_error = None, False, ""
        parent = _walk_dirs(folder)                      # no link on the way (the slot is made where the runner means it to be)
        try:
            os.mkdir(self.name, 0o755, dir_fd=parent)
            self.dirfd = os.open(self.name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
        finally:
            os.close(parent)
        if closed and owner is not None and os.geteuid() == 0:
            os.fchown(self.dirfd, 0, owner.pw_gid)
            os.fchmod(self.dirfd, 0o750)
        fd = os.open(VERDICT_FILE, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=self.dirfd)
        try:
            if owner is not None and os.geteuid() == 0:
                os.fchown(fd, owner.pw_uid, owner.pw_gid)
            st = os.fstat(fd)
        finally:
            os.close(fd)
        self.ident = (st.st_dev, st.st_ino)
        self.rfd = os.open(VERDICT_FILE, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC, dir_fd=self.dirfd)
        if (os.fstat(self.rfd).st_dev, os.fstat(self.rfd).st_ino) != self.ident:
            raise OSError("the verdict file is not the file the runner made")
        self.audit = WriteAudit(self.dirfd, VERDICT_FILE)

    def _snapshot(self):
        """(bytes, "") of the file through the held descriptor, or (None, reason). Checks the file the runner made, one link, a sane size.
        The first call (freeze) also checks that the agent user owns it; after the seal the owner is no longer the agent user."""
        try:
            st = os.fstat(self.rfd)
            if not stat.S_ISREG(st.st_mode) or (st.st_dev, st.st_ino) != self.ident:
                return None, "the verdict file is not the file the runner made"
            if not self.sealed and st.st_uid != self.uid:
                return None, "the verdict file is not owned by the agent user"
            if st.st_nlink != 1:
                return None, "the verdict file has another hard link"
            if st.st_size > VERDICT_MAX:
                return None, "the verdict file is too large"
            data, off = b"", 0
            while len(data) <= VERDICT_MAX:
                chunk = os.pread(self.rfd, 1 << 16, off)
                if not chunk:
                    break
                data += chunk
                off += len(chunk)
            if len(data) > VERDICT_MAX:
                return None, "the verdict file is too large"
            return data, ""
        except OSError as e:
            return None, f"the verdict file could not be read ({type(e).__name__})"

    def _name_is_ours(self):
        """The name in the runner's folder still leads to the inode the runner made (a rename or replacement of the file is not followed)."""
        try:
            st = os.stat(VERDICT_FILE, dir_fd=self.dirfd, follow_symlinks=False)
        except OSError:
            return False
        return (st.st_dev, st.st_ino) == self.ident

    def freeze(self):
        """Call when the verifier process has exited, BEFORE the sweep of the agent user's processes. Idempotent. Never raises."""
        if self.frozen is not None:
            return
        self.frozen = self._snapshot()
        self._seal()

    def _seal(self):
        """The file stops being something the agent user can open: root-owned when the runner is root, mode 400 either way (when the runner is not root the
        owner could chmod it back, but then it is the same user, which is the case this class cannot fully cover). The runner's own
        ATTRIB events are not counted against the verifier. Descriptors and maps opened earlier stay valid; that is what the compare is for."""
        n0 = self.audit.absorb()
        try:
            if os.geteuid() == 0:
                os.fchown(self.rfd, 0, 0)
            os.fchmod(self.rfd, 0o400)
            self.sealed = True
        except OSError as e:
            self.seal_error = f"cannot seal the verdict file ({type(e).__name__})"
        self.audit.ignore(n0, self.audit.absorb())

    def read(self, want):
        """(verdict dict, "", sha256 of the exact bytes) for a verdict file that is this run's, else (None, reason, ""). Call it only after
        every process of the agent user is dead. Reads through the held descriptor (twice) and compares with the copy frozen at the
        verifier's exit; the bytes judged are the bytes returned."""
        self.freeze()                       # a caller that never froze (a stand-in agent) gets the freeze now, still before the audit
        why = self.audit.judge()
        if why:
            return None, why, ""
        if not self._name_is_ours():
            return None, "the verdict file is not the file the runner made", ""
        first, why = self._snapshot()
        if first is None:
            return None, why, ""
        again, why = self._snapshot()
        if again != first:
            return None, "the verdict file changed while it was read", ""
        if self.frozen[0] is None:
            return None, self.frozen[1], ""
        if self.frozen[0] != first:
            return None, ("the verdict file changed after the verifier exited (a detached writer, or a shared memory map held open "
                          "across the exit); the attempt is void"), ""
        if self.seal_error:
            return None, self.seal_error, ""
        if not first:
            return None, "the verifier wrote no VERIFY.json", ""
        try:
            verdict = ISO.strict_loads(first, VERDICT_MAX)
        except ValueError:
            return None, "the verifier's VERIFY.json is not valid JSON (strict: UTF-8, no duplicate keys)", ""
        bad = check_verdict(verdict, want)
        return (None, bad, "") if bad else (verdict, "", hashlib.sha256(first).hexdigest())

    def close(self):
        self.audit.close()
        for fd in (self.rfd, self.dirfd):
            try:
                os.close(fd)
            except OSError:
                pass



def new_verdict_slot(folder, owner=None, closed=False):
    """A fresh VerdictSlot for THIS verifier run, plus the nonce the verdict must carry. Made after the worker's processes are
    dead, so nothing the worker started knows the name."""
    slot = VerdictSlot(folder, owner, closed)
    return slot, slot.nonce


def read_verdict(slot, want):
    """(verdict dict, "") for a verdict that is this run's, else (None, reason). See VerdictSlot.read."""
    verdict, why, _ = slot.read(want)
    return verdict, why


# ---- re-review H2 (2026-10-18): the verifier's OUTPUT, not a file the agent user can still write, is the authority for the verdict
VERDICT_MARK = "JARVIS-VERDICT:"                       # the verifier's final answer is one line: this marker, then the verdict as one-line JSON
CAPTURE_MAX = 4_000_000                                # bytes of the verifier's output that are kept


class VerifierOutput:
    """The verifier's stdout, captured by the runner into an unnamed file that only the runner owns (mode 600, deleted at once).

    The verifier session gets a descriptor opened O_WRONLY|O_APPEND on it as its fd 1; the runner reads through its own descriptor.
    Why this is a better authority than VERIFY.json: when the runner is root, a process of the agent user can write this file ONLY
    through a descriptor it inherited from the verifier (re-opening it through /proc/<pid>/fd is refused by the file's owner and mode),
    whereas it can open VERIFY.json O_RDWR and `mmap` it. Code the verifier runs through its tools normally does not inherit the
    verifier's own stdout (the tool harness gives it pipes), but nothing here ENFORCES that: a child that does inherit fd 1 can append to
    this file or truncate it. That is the residual (jarvis/caretaker/README.md); it needs the verifier's tools in another OS user.

    freeze() copies the bytes at the instant the verifier exits, before the sweep; final() reads again after every agent-user process
    is dead. Any difference voids the attempt."""

    def __init__(self):
        self.file = tempfile.TemporaryFile(buffering=0)             # O_TMPFILE where the filesystem has it, else created and unlinked
        self.rfd = self.file.fileno()
        try:                                                        # the verifier's end: append-only (cannot be written at an offset)
            self.wfd = os.open(f"/proc/self/fd/{self.rfd}", os.O_WRONLY | os.O_APPEND | os.O_CLOEXEC)
        except OSError:
            self.wfd = os.dup(self.rfd)
        self.frozen = None

    def feed(self, data):
        """Write bytes as the verifier would print them (used by the scripted stand-ins in tests; the real verifier writes fd 1)."""
        os.write(self.wfd, data if isinstance(data, bytes) else str(data).encode("utf-8"))

    def read(self):
        data, off = b"", 0
        while len(data) <= CAPTURE_MAX:
            chunk = os.pread(self.rfd, 1 << 16, off)
            if not chunk:
                break
            data += chunk
            off += len(chunk)
        return data[:CAPTURE_MAX + 1]

    def freeze(self):
        if self.frozen is None:
            try:
                self.frozen = self.read()
            except OSError:
                self.frozen = b""

    def final(self):
        """(bytes, "") read after the sweep, or (None, why) when they differ from what the verifier left when it exited."""
        self.freeze()
        data = self.read()
        if data != self.frozen:
            return None, ("the verifier's output changed after it exited (something that inherited its output kept writing); "
                          "the attempt is void")
        if len(data) > CAPTURE_MAX:
            return None, "the verifier's output is too large"
        return data, ""

    def close(self):
        for fd in (self.wfd,):
            try:
                os.close(fd)
            except OSError:
                pass
        self.file.close()


def parse_channel(data, want):
    """(verdict dict, "", sha256 of the verdict text) for the verifier's output when it holds exactly one JARVIS-VERDICT marker at the start of
    a line, followed by one JSON object (one line as asked, or spread over lines: a model may pretty-print) that is this run's (nonce,
    task, job, attempt, boolean passed), else (None, reason, ""). The hash covers the marker and the JSON object, nothing around it. Pure."""
    text = (data or b"").decode("utf-8", "replace")
    starts = [m.end() for m in re.finditer(r"^[ \t]*" + re.escape(VERDICT_MARK), text, re.M)]
    if not starts:
        return None, f"the verifier printed no {VERDICT_MARK} line as its final answer", ""
    if len(starts) > 1:
        return None, f"the verifier's output holds {len(starts)} {VERDICT_MARK} lines; exactly one is accepted", ""
    rest = text[starts[0]:]
    skip = len(rest) - len(rest.lstrip())
    try:
        verdict, end = ISO.strict_raw_decode(rest, skip)         # no duplicate keys, no NaN, shallow
    except ValueError:
        return None, f"the verifier's {VERDICT_MARK} line is not valid JSON (strict: UTF-8, no duplicate keys)", ""
    raw = (VERDICT_MARK + rest[:end]).strip().encode("utf-8", "replace")
    if len(raw) > VERDICT_MAX:
        return None, "the verifier's verdict is too large", ""
    bad = check_verdict(verdict, want)
    return (None, bad, "") if bad else (verdict, "", hashlib.sha256(raw).hexdigest())


def verdict_disagreement(chan, filed):
    """"" when the verdict printed by the verifier and the file it wrote say the same thing that matters (passed, and met per criterion
    row, in order); else what differs. The wording of the evidence is the model's and may differ. Pure."""
    if chan.get("passed") is not filed.get("passed"):
        return f"passed is {chan.get('passed')} in the verifier's output but {filed.get('passed')} in the verdict file"

    def mets(v):
        rows = v.get("criteria") if isinstance(v.get("criteria"), list) else []
        return [r.get("met") is True for r in rows if isinstance(r, dict)]
    a, b = mets(chan), mets(filed)
    if a != b:
        return f"the criteria rows differ between the verifier's output ({a}) and the verdict file ({b})"
    return ""


def settle_verdict(slot, out, want):
    """(verdict dict, "", file sha256, channel sha256) when the verifier's output holds this run's verdict AND the verdict file, frozen at
    the verifier's exit and unchanged after the sweep, agrees with it; else (None, reason, "", ""). The OUTPUT is the authority (its
    `passed` and rows are what goes on); the file is a cross-check only: any disagreement, change, or missing half voids the attempt.
    Call after every process of the agent user is dead."""
    filed, why_file, file_sha = slot.read(want)
    data, why_out = out.final()
    if filed is None:                                    # the file's own reasons first (they name what the write audit saw)
        return None, why_file, "", ""
    if data is None:
        return None, why_out, "", ""
    chan, why_chan, chan_sha = parse_channel(data, want)
    if chan is None:
        return None, why_chan, "", ""
    diff = verdict_disagreement(chan, filed)
    if diff:
        return None, "the verdict file and the verifier's output disagree (" + diff + "); something altered one of them", "", ""
    return chan, "", file_sha, chan_sha


def sanitize_verdict(verdict):
    """Only these fields of the verifier's file go on to the kernel, clipped. Anything else it carried (a `trusted_execution`, a
    `verifier`, an `agent_assessed`, a `kernel_check` ...) is dropped: the runner adds the evidence it owns itself."""
    rows = []
    for r in (verdict.get("criteria") if isinstance(verdict.get("criteria"), list) else [])[:30]:
        if isinstance(r, dict):
            rows.append({"criterion": str(r.get("criterion", ""))[:300], "met": r.get("met") is True, "evidence": str(r.get("evidence", ""))[:600]})
    out = {"passed": verdict.get("passed") is True, "problems": [str(x)[:400] for x in (verdict.get("problems") or [])[:20]]
           if isinstance(verdict.get("problems"), list) else []}
    if isinstance(verdict.get("criteria"), list):
        out["criteria"] = rows
    return out


class KernelWatch:
    """Observability for the runner's deliberate fail-open (audit M9). While a worker runs, a kernel that does not answer (network
    blip, restart) never stops the work, except the kernel's own 'kill switch is on' refusal. That means a kill switch switched on
    during an outage is only seen once the kernel answers again. This class turns the silence into events: `observe(code, now)` returns
    the notes to log/report when the kernel stops answering and when it answers again (with how long and what that means)."""

    def __init__(self):
        self.since = None

    def observe(self, code, now):
        if code == 0:                                    # api() answers 0 when the kernel could not be reached at all
            if self.since is None:
                self.since = now
                return ["kernel unreachable: the worker keeps running (fail-open by design); the kill switch and the owner's "
                        "cancel are checked again as soon as the kernel answers"]
            return []
        if self.since is not None:
            took, self.since = int(now - self.since), None
            return [f"kernel answered again after {took} s without contact; the worker kept running meanwhile (fail-open by design)"]
        return []


def run_agent(job, kind, prompt, folder, timeout, log_name, capture=None, on_exit=None, user=None, cwd=None):
    """Run one agent session as the agent user; returns (exit code or None on timeout, seconds, stopped_reason).

    capture (a VerifierOutput): the session's stdout goes there, its stderr to the log, instead of both to the log (re-review H2: the
    verifier's final answer is the authority for its verdict). on_exit: called the moment the session process has exited by itself, BEFORE
    the sweep of the agent user's processes, so the runner can copy and seal what it must compare against later.
    user (a pwd entry): run as that identity instead of the agent user (the verify identity); cwd: its working folder (the read-only copy
    of the deliverable); `folder` is then where the log is written and what the agent CLI is told as its folder."""
    try:
        log = open_log(folder, log_name)                 # never through a link the worker left under that name (it was a root file overwrite)
    except OSError:
        log = open(os.devnull, "w")
    out, err = (capture.wfd, log) if capture is not None else (log, subprocess.STDOUT)
    argv = command(kind, prompt, cwd or folder)
    if user is not None and VERIFY_BIN:
        argv = [os.path.join(VERIFY_BIN, os.path.basename(argv[0])), *argv[1:]] if os.path.dirname(argv[0]) else argv
    env = agent_env(kind) if user is None else agent_env(kind, user)
    if user is not None:                              # the verify identity: no core files, and a ceiling on what it can write to the runner's capture and log
        argv = ["sh", "-c", f'umask 077; ulimit -c 0; ulimit -f {VERIFY_FSIZE_BLOCKS}; exec "$@"', "sh", *argv]
    p = subprocess.Popen(["runuser", "-u", AGENT if user is None else user.pw_name, "--", "env", "-i", *[f"{k}={v}" for k, v in env.items()],
                          *argv], cwd=cwd or folder, stdout=out, stderr=err,
                         stdin=subprocess.DEVNULL, start_new_session=True)
    t0, frozen, reason, last = time.time(), False, "", 0.0
    up0, watch = uptime(), KernelWatch()
    try:
        while p.poll() is None:
            try:
                p.wait(timeout=1)                       # wakes the moment the session exits (a plain sleep would add up to 1 s)
            except subprocess.TimeoutExpired:
                pass
            if p.poll() is not None:
                break
            if time.time() - t0 > timeout:
                reason = "timeout"
                break
            if time.time() - last >= STATUS_EVERY_S:                # follow the owner's pause / cancel and the kill switch
                last = time.time()
                code, r = api("/v1/jarvis/runner/status", {"job": job})
                for note in watch.observe(code, time.time()):       # fail-open on an unreachable kernel is deliberate, never silent
                    print(time.strftime("%H:%M:%S"), "job", job, note, flush=True)
                    if code != 0:
                        api("/v1/jarvis/runner/report", {"job": job, "note": note})
                action, why = follow_status(code, r, frozen)        # a network blip or kernel restart never stops the work
                if action == "stop":
                    reason = why
                    break
                if action == "freeze":
                    os.killpg(p.pid, signal.SIGSTOP); frozen = True
                elif action == "thaw":
                    os.killpg(p.pid, signal.SIGCONT); frozen = False
        if p.poll() is None:
            try:
                kill_tree(p.pid, hard=(reason == STOPPED_BY_KILL))
                p.wait(15)
            except (OSError, subprocess.TimeoutExpired):
                os.killpg(p.pid, signal.SIGKILL)
            _reap_after(p.pid, up0, user)
            return None, time.time() - t0, reason
        if on_exit is not None:
            on_exit()                                 # the session has exited on its own: copy and seal before anything else runs
        _reap_after(p.pid, up0, user)                 # a NORMAL exit leaves background / setsid helpers behind too (audit M8)
        return p.returncode, time.time() - t0, ""
    finally:
        if capture is not None:                       # the session's stdout, for people reading the log (the runner parsed its own copy)
            try:
                log.write("\n--- stdout ---\n" + capture.read()[:200_000].decode("utf-8", "replace"))
            except (OSError, ValueError):
                pass
        log.close()


LEFTOVER_SURVIVORS = []                           # pids that did not die after the last run_agent (read by handle)


def _reap_after(root_pid, up0, user=None):
    """After every agent run, normal exit included: kill what the run started (see reap_since). Records survivors. `user`: the identity the
    run used, when it was not the agent user (the verify identity runs nothing else, so everything of it dies)."""
    if user is not None and user.pw_uid != (PW.pw_uid if PW else -1) and os.geteuid() == 0:
        _, survivors = ISO.kill_uid(user.pw_uid)
        if not survivors:
            ISO.purge_uid_files(user.pw_uid)
        LEFTOVER_SURVIVORS[:] = survivors
        return
    if PW is None:
        return
    _, survivors = reap_since(PW.pw_uid, up0, roots=(root_pid,))
    LEFTOVER_SURVIVORS[:] = survivors


DELIVER = "deliver"                                   # the workspace folder whose files go to the owner after a passed check
DELIVER_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 _.-]{0,79}\.(md|txt|html|json|csv|log|py)\Z")   # the kernel's output rule
DELIVER_MAX_FILES, DELIVER_MAX_CHARS = 10, 200_000
DELIVER_WAIT_S = 35 * 60                              # the laptop job waits up to 30 min for the node, then expires
WANTS_DELIVERY = re.compile(r"\b(deliver\w*|sav(e|ed|ing)\b|outputs? (folder|director\w*|files?)|owner'?s? (computer|laptop|folder))", re.I)


def work_prompt(task, folder, instructions, criteria, attempt, problems):
    crit = "\n".join(f"{i}. {c}" for i, c in enumerate(criteria, 1))
    fix = ("\n\nThis is attempt %d. An independent check of the previous attempt found these problems; fix them first:\n- " % attempt
           + "\n- ".join(problems)) if problems else ""
    return (f"Task {task} from the owner, via JARVIS. Your workspace is {folder} (the current folder); build everything there."
            f"\n\nWhat to achieve:\n{instructions}\n\nAcceptance criteria (all must hold):\n{crit}{fix}\n\n"
            "Delivery: the owner's computer and its folders (D:\\..., C:\\..., /mnt/...) are NOT visible from here; never ask "
            "for access to them and never wait for it. Put the final version of every file the owner should receive in "
            f"./{DELIVER}/ (plain file names, .py .md .txt .html .json .csv .log only, at most {DELIVER_MAX_FILES} files, no "
            f"sub-folders: if a criterion names a path such as tests/test_x.py, deliver it flat as ./{DELIVER}/test_x.py and make "
            f"it work there). After an independent check passes, the system copies ./{DELIVER}/ to the owner's computer itself; "
            "that is how 'save' or 'deliver to the owner' criteria are met.\n\n"
            "Rules: check your own work by actually running it (tests, commands, opening pages), including the copies in "
            f"./{DELIVER}/. Delivered files must work from any folder on another computer: no absolute paths (find files "
            "relative to the test file itself), and the tests must pass when only the delivered files are copied to an empty "
            "folder; the system checks exactly that. When you are done, write EVIDENCE.md: for each criterion, what you did and the command or check that "
            "shows it holds, with its real output. If something cannot be done, say exactly why in EVIDENCE.md instead of "
            "pretending. Do not write VERIFY.json.")


def verify_prompt(task, criteria, verdict=None):
    """`verdict` = {"path", "nonce", "job", "attempt"}: where this run's verdict goes and what it must carry (audit M8). Without
    it the older fixed VERIFY.json in the workspace is described (kept so a prompt can still be built without a run)."""
    crit = "\n".join(f"{i}. {c}" for i, c in enumerate(criteria, 1))
    where = (verdict or {}).get("path") or "VERIFY.json"
    ident = (f'"nonce": "{verdict["nonce"]}", "task": "{task}", "job": "{verdict["job"]}", "attempt": {verdict["attempt"]}, '
             if verdict else "")
    return (f"You are an independent verifier for task {task}. Another agent worked in the current folder; do not trust its "
            "claims or EVIDENCE.md. Check each acceptance criterion yourself by running the code, the tests and the commands, "
            f"and by reading the files. Do not fix anything and do not change any file except {where}.\n\n"
            f"Acceptance criteria:\n{crit}\n\n"
            f"Delivery: the files in ./{DELIVER}/ are exactly what the owner will receive; check THOSE copies: copy them to a "
            "new empty folder (mktemp -d) and run the tests there, so nothing else in this workspace helps them pass. You cannot see the owner's computer and must not judge it: a criterion about saving or "
            f"delivering files to the owner is met when the right, working files are in ./{DELIVER}/ (the system delivers them "
            "after your check and confirms each one). Never cite files outside this folder as delivered. Delivered files are flat "
            f"(no sub-folders): a criterion that names a path like tests/test_x.py is met by test_x.py in ./{DELIVER}/ if it works "
            "there.\n\n"
            f'Then write {where} exactly like {{{ident}"passed": true or false, "criteria": [{{"criterion": "...", "met": true, '
            '"evidence": "what you ran and saw"}], "problems": ["specific problem to fix", ...]}. Copy the nonce, task, job and '
            "attempt values above unchanged, write one criteria row per acceptance criterion, and write the file only at the "
            "path named above. passed is true only if every criterion is met. The file already exists and is empty: write it "
            "IN PLACE, exactly once, with one command (a shell redirect, or open(path, 'w')); that folder accepts no new files, so "
            "do not write a temporary file and rename it, and do not write the verdict a second time (a verdict written more "
            "than once is void). Think through the whole verdict first.\n\n"
            f"Your FINAL ANSWER is the verdict itself, which is what counts: print exactly one line, starting with {VERDICT_MARK} "
            "followed by the same JSON as one line (no line breaks inside it), with the same values as the file, and nothing else "
            f"that starts with {VERDICT_MARK}. A verdict whose final answer and file disagree, or that has no such line, is void.")


def safe_read(path, limit=200_000):
    """Read a file the agent wrote. This runs as root, so it never follows a link and only reads the agent's own
    regular files (an agent could otherwise point EVIDENCE.md at a system file)."""
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except OSError:
        return None
    try:
        st = os.fstat(fd)
        if st.st_uid != PW.pw_uid or not stat.S_ISREG(st.st_mode):
            return None
        return os.read(fd, limit).decode(errors="replace")
    finally:
        os.close(fd)


def read_json(path):
    try:
        v = json.loads(safe_read(path) or "")
        return v if isinstance(v, dict) else None
    except ValueError:
        return None


def deliverables(folder, required):
    """The files in ./deliver/ as [(name, text)], and the problems that stop them being delivered. Read like safe_read:
    the folder and each file must be the agent's own, real (no links), within the kernel's output rules."""
    path = os.path.join(folder, DELIVER)
    try:
        st = os.lstat(path)
    except OSError:
        return [], ([f"nothing to deliver: put the owner's files in ./{DELIVER}/"] if required else [])
    if not stat.S_ISDIR(st.st_mode) or st.st_uid != PW.pw_uid:
        return [], [f"./{DELIVER} must be a plain folder made by the worker"]
    files, problems = [], []
    for name in sorted(os.listdir(path)):
        if name.startswith(".") or name == "__pycache__":
            continue
        if not DELIVER_NAME.match(name) or ".." in name:
            problems.append(f"./{DELIVER}/{name}: not deliverable (plain names, .py .md .txt .html .json .csv .log, no folders)")
            continue
        text = safe_read(os.path.join(path, name), DELIVER_MAX_CHARS + 1)
        if text is None or len(text) > DELIVER_MAX_CHARS:
            problems.append(f"./{DELIVER}/{name}: not a readable text file under {DELIVER_MAX_CHARS} characters")
            continue
        files.append((name, text))
    if len(files) > DELIVER_MAX_FILES:
        problems.append(f"./{DELIVER}/ has {len(files)} files; at most {DELIVER_MAX_FILES}")
    if required and not files and not problems:
        problems.append(f"nothing to deliver: put the owner's files in ./{DELIVER}/")
    for name, text in files:                     # the owner's computer has no lab paths: such files break once delivered
        if os.path.realpath(folder) in text or "/home/agent" in text:
            problems.append(f"./{DELIVER}/{name} refers to the lab path {folder}: use paths relative to the file itself")
    return files, problems


PYTHON3 = "/usr/bin/python3" if os.path.exists("/usr/bin/python3") else sys.executable


def snapshot_digest(files):
    """One sha256 over exactly the (name, text) pairs that were read once from ./deliver/: what is run, checked and delivered."""
    h = hashlib.sha256()
    for name, text in files:
        h.update(b"f" + name.encode("utf-8", "replace") + b"\0" + text.encode("utf-8", "replace") + b"\0")
    return h.hexdigest()


# ---- re-review H2 (2026-10-18): the runner counts the delivered tests itself; nothing the tests print is evidence
HARNESS_VERSION = "lab-harness/1"
HARNESS_SRC = r'''
import json, os, sys
import unittest
mode, work, fd = sys.argv[1], sys.argv[2], int(sys.argv[3])
_seq = [0]


def emit(rec):
    rec["seq"] = _seq[0]
    rec["pid"] = os.getpid()                 # a test that forks and lets the parent exit leaves records from two processes
    _seq[0] += 1
    data = (json.dumps(rec, separators=(",", ":")) + "\n").encode("utf-8", "replace")
    while data:
        data = data[os.write(fd, data):]


def clip(x):
    return str(x)[:300]


def flat(suite):
    for t in suite:
        if isinstance(t, unittest.TestSuite):
            yield from flat(t)
        else:
            yield t


os.chdir(work)
sys.path.insert(0, work)
if mode == "run" and len(sys.argv) > 4:                  # the sandbox supervisor names the tests of THIS process; nothing else is loaded
    suite = unittest.TestLoader().loadTestsFromNames(sys.argv[4:])
else:
    suite = unittest.TestLoader().discover(work, pattern="test*.py", top_level_dir=work)
tests = list(flat(suite))
for t in tests:
    emit({"e": "c", "id": clip(t.id())})
emit({"e": "collected", "n": len(tests)})
if mode == "collect":
    sys.exit(0)
RANK = {"ok": 0, "skip": 1, "xfail": 2, "xpass": 3, "fail": 4, "error": 5}


class Result(unittest.TestResult):
    cur, out = None, None

    def startTest(self, test):
        super().startTest(test)
        self.cur, self.out = test, None
        emit({"e": "start", "id": clip(test.id())})

    def note(self, test, o):
        if test is self.cur:
            if self.out is None or RANK[o] > RANK[self.out]:
                self.out = o
        else:                                   # a class or module fixture failed or was skipped: no test of its own
            emit({"e": "stop", "id": clip(test), "o": o, "orphan": True})

    def addSuccess(self, test):
        super().addSuccess(test); self.note(test, "ok")

    def addFailure(self, test, err):
        super().addFailure(test, err); self.note(test, "fail")

    def addError(self, test, err):
        super().addError(test, err); self.note(test, "error")

    def addSkip(self, test, reason):
        super().addSkip(test, reason); self.note(test, "skip")

    def addExpectedFailure(self, test, err):
        super().addExpectedFailure(test, err); self.note(test, "xfail")

    def addUnexpectedSuccess(self, test):
        super().addUnexpectedSuccess(test); self.note(test, "xpass")

    def addSubTest(self, test, subtest, err):
        super().addSubTest(test, subtest, err)
        if err is not None:
            self.note(test, "fail" if issubclass(err[0], test.failureException) else "error")

    def stopTest(self, test):
        super().stopTest(test)
        emit({"e": "stop", "id": clip(test.id()), "o": self.out or "none"})
        self.cur, self.out = None, None


result = Result()
suite.run(result)
ok = result.wasSuccessful()
emit({"e": "done", "run": result.testsRun, "failures": len(result.failures), "errors": len(result.errors), "skipped": len(result.skipped),
      "xfail": len(result.expectedFailures), "xpass": len(result.unexpectedSuccesses), "ok": bool(ok)})
for kind, rows in (("FAIL", result.failures), ("ERROR", result.errors)):          # for people (and the repair prompt); never parsed
    for t, tb in rows[:20]:
        sys.stderr.write("%s: %s\n%s\n" % (kind, t, tb[-1500:]))
sys.stdout.flush(); sys.stderr.flush()
sys.exit(5 if not result.testsRun else (0 if ok else 1))
'''
HARNESS_SHA256 = hashlib.sha256(HARNESS_SRC.encode()).hexdigest()
HARNESS_OUT_MAX = 1_000_000
HARNESS_COLLECT_S, HARNESS_RUN_S = 60, 180          # seconds: importing the test files, then running them
HARNESS_REC_MAX = 4_000_000


RECORD_LINE_MAX, RECORD_COUNT_MAX = 2000, 50_000
_RECORD_KEYS = {"c": {"id"}, "collected": {"n"}, "start": {"id"}, "stop": {"id", "o"}, "done": {"run", "failures", "errors", "skipped", "xfail", "xpass", "ok"}}
_OUTCOMES = ("ok", "skip", "xfail", "xpass", "fail", "error", "none")


def _int(x):
    return isinstance(x, int) and not isinstance(x, bool) and 0 <= x <= 10 ** 6


def record_schema_problem(rec):
    """"" when `rec` has exactly the keys and types of one harness event, else what is wrong. Pure. (Strict: nothing extra, nothing missing.)"""
    kind = rec.get("e")
    if kind not in _RECORD_KEYS:
        return "unknown event"
    want = _RECORD_KEYS[kind] | {"e", "seq", "pid"}
    if kind == "stop" and "orphan" in rec:
        want = want | {"orphan"}
        if rec["orphan"] is not True:
            return "bad orphan flag"
    if set(rec) != want:
        return "wrong fields"
    if not _int(rec["seq"]) or not (isinstance(rec["pid"], int) and not isinstance(rec["pid"], bool) and rec["pid"] > 0):
        return "bad seq or pid"
    if "id" in rec and not (isinstance(rec["id"], str) and 0 < len(rec["id"]) <= 300):
        return "bad id"
    if kind == "stop" and rec["o"] not in _OUTCOMES:
        return "bad outcome"
    if kind == "collected" and not _int(rec["n"]):
        return "bad count"
    if kind == "done":
        if not all(_int(rec[k]) for k in ("run", "failures", "errors", "skipped", "xfail", "xpass")) or not isinstance(rec["ok"], bool):
            return "bad totals"
    return ""


def _parse_records(raw):
    """(records, problems) from the bytes the harness wrote to the runner's result pipe. Every line is one strict JSON object (UTF-8, no
    duplicate keys, shallow, under RECORD_LINE_MAX bytes) with exactly the keys and types of one event (record_schema_problem), whose `seq`
    counts 0, 1, 2 ... with no gap and no repeat; anything else (a half line, a repeated or skipped number, text that is not JSON, an
    oversized line, too many records) is a problem. Pure."""
    recs, probs = [], []
    if not raw:
        return recs, probs
    lines = raw.split(b"\n")
    if lines[-1] != b"":
        probs.append("the last harness record is cut off (the test process stopped while writing)")
    if len(lines) > RECORD_COUNT_MAX:
        probs.append("too many records on the harness channel")
        return recs, probs
    for ln in lines[:-1]:
        if len(ln) > RECORD_LINE_MAX:
            probs.append("a record on the harness channel is oversized")
            continue
        try:
            rec = ISO.strict_loads(ln, RECORD_LINE_MAX, 3)
        except ValueError:
            probs.append("a line on the harness channel is not a record (written by the tests?)")
            continue
        if not isinstance(rec, dict) or rec.get("seq") != len(recs):
            probs.append("a record on the harness channel is out of sequence (written by the tests?)")
            continue
        why = record_schema_problem(rec)
        if why:
            probs.append("a record on the harness channel does not match the schema (" + why + ")")
            continue
        recs.append(rec)
    return recs, probs


def _collected(recs):
    """(ids, index after the `collected` record) or (None, why)."""
    ids = []
    for i, r in enumerate(recs):
        if r.get("e") == "c" and isinstance(r.get("id"), str):
            ids.append(r["id"])
        elif r.get("e") == "collected":
            if len(set(ids)) != len(ids):
                return None, "the collected test ids are not unique (a test names itself like another; one of them would never run)"
            return (ids, i + 1) if r.get("n") == len(ids) else (None, "the collected count does not match the collected ids")
        else:
            return None, "unexpected record while collecting"
    return None, "the test collection never finished"


def judge_harness(collect, run):
    """(problems, facts) from the two harness steps. Each step = {"records", "bad" (problems parsing them), "exit" (int or None),
    "timed_out", "output"}. `facts` is None unless the run is complete and consistent. Pure: no I/O.

    A test run is evidence only when ALL of this holds, so a test that stops its own process (os._exit, sys.exit at import, kill -9 of
    itself, a fork whose parent exits) or prints a summary it did not earn leaves a gap and the run is void:
      - the collection step (a separate process that only imports the test files) finished and exited 0;
      - the run step announced the same test ids, then every one of them started and stopped once, in order, and a final `done` record
        followed, which is the last record; the process exit code is the one the harness would give (0 only if every test passed);
      - the counts in `done` equal the counts the runner made itself from the per-test records;
      - nothing on the channel was out of sequence."""
    probs = []
    for name, st in (("collection", collect), ("run", run)):
        if st is None:
            continue
        probs += [f"{name} step: {b}" for b in st["bad"]]
        if st["timed_out"]:
            probs.append(f"the {name} step of the delivered tests ran out of time")
        if len({r.get("pid") for r in st["records"]}) > 1:
            probs.append(f"the {name} step's records come from more than one process (a test forked and the original process went away)")
            return probs, None
    ids, at = _collected(collect["records"]) if collect else (None, "no collection step")
    if ids is None:
        probs.append("the delivered tests could not be collected: " + str(at))
        return probs, None
    if collect["exit"] != 0 or len(collect["records"]) != at:
        probs.append(f"the collection step did not end normally (exit {collect['exit']}); importing the test files stopped the process")
        return probs, None
    if run is None:
        return probs, None
    rids, rat = _collected(run["records"])
    if rids is None:
        probs.append("the test run never announced its tests: " + str(rat))
        return probs, None
    if sorted(rids) != sorted(ids):
        probs.append("the run found other tests than the collection step did (the test files behave differently each time they are imported)")
        return probs, None
    todo, outcomes, open_id, done, orphans, bad = list(ids), [], None, None, [], ""
    for r in run["records"][rat:]:
        e = r.get("e")
        if done is not None:
            bad = "records after the final report"
            break
        if e == "start" and open_id is None and isinstance(r.get("id"), str) and r["id"] in todo:
            open_id = r["id"]
            todo.remove(open_id)
        elif e == "stop" and r.get("orphan") is True and r.get("o") in ("fail", "error", "skip", "xfail", "xpass"):
            orphans.append(r["o"])
        elif e == "stop" and open_id is not None and r.get("id") == open_id and r.get("o") in ("ok", "skip", "xfail", "xpass", "fail", "error", "none"):
            outcomes.append((open_id, r["o"]))
            open_id = None
        elif e == "done" and open_id is None:
            done = r
        else:
            bad = "an unexpected record in the test run"
            break
    n = len(outcomes)
    if bad:
        probs.append(f"the test run is not trustworthy: {bad}")
        return probs, None
    if done is None or todo or open_id is not None:
        probs.append(f"the delivered tests did not run to the end: {n} of {len(ids)} finished and the final report is "
                     f"{'missing' if done is None else 'present'} (the test process exited with {run['exit']}); "
                     "a test that stops its own process, or a crash, is not a pass")
        return probs, None
    count = lambda o: sum(1 for _, x in outcomes if x == o)
    orph = lambda o: sum(1 for x in orphans if x == o)
    # The harness's own totals must agree with the per-test records the runner counted itself (failures and errors may be larger: a test
    # with several failing subtests, or a class or module fixture that failed, counts more than once there).
    if (done.get("run") != n or done.get("xfail") != count("xfail") or done.get("xpass") != count("xpass")
            or done.get("skipped") != count("skip") + orph("skip")
            or not isinstance(done.get("failures"), int) or done["failures"] < count("fail") + orph("fail")
            or not isinstance(done.get("errors"), int) or done["errors"] < count("error") + orph("error")):
        probs.append("the harness's final counts contradict its own per-test records")
        return probs, None
    failed = count("fail") + orph("fail") + count("xpass") + count("none")
    errored = count("error") + orph("error")
    if (done.get("ok") is True) != (failed == 0 and errored == 0):
        probs.append("the harness's verdict contradicts its own per-test records")
        return probs, None
    expected_exit = 5 if n == 0 else (0 if failed == 0 and errored == 0 else 1)
    if run["exit"] != expected_exit:
        probs.append(f"the process exit status ({run['exit']}) contradicts its own records (expected {expected_exit})")
        return probs, None
    facts = {"tests_collected": len(ids), "tests_run": n, "tests_passed": count("ok"), "tests_failed": failed, "tests_errored": errored,
             "tests_skipped": count("skip") + orph("skip"), "tests_expected_failures": count("xfail"),
             "collected_ids_sha256": hashlib.sha256("\n".join(sorted(ids)).encode("utf-8", "replace")).hexdigest(),
             "exit": run["exit"], "outcomes": sorted(outcomes)}
    facts["harness_sha256"] = hashlib.sha256(json.dumps({"v": HARNESS_VERSION, "ids": facts["collected_ids_sha256"], "outcomes": facts["outcomes"],
                                                         "exit": run["exit"], "done": {k: done.get(k) for k in ("run", "failures", "errors", "skipped", "xfail", "xpass", "ok")}},
                                                        sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return probs, facts


def _drain(fd, sink, limit):
    """Read a pipe to its end into the list `sink` (kept up to `limit` bytes); runs in a thread so a chatty test never blocks."""
    kept = 0
    while True:
        try:
            chunk = os.read(fd, 1 << 16)
        except OSError:
            return
        if not chunk:
            return
        if kept < limit:
            sink.append(chunk[:limit - kept])
            kept += len(chunk)


def harness_step(mode, workdir, script, limit):
    """Run the harness (mode "collect" or "run") on the delivered files in `workdir` as the agent user, time-limited, and return the
    step dict judge_harness reads. The harness writes its records to a pipe the runner made (fd passed down, closed in the runner); the
    runner waits for the process, kills EVERYTHING of the agent user that started during the step, and only then reads the pipe to its
    end, so nothing that outlives the step can add records. The tests' own stdout and stderr are captured separately; they are never
    parsed (a forged 'Ran 7 tests' line is just text)."""
    r, w = os.pipe()
    up0, rec, out, timed_out = uptime(), [], [], False
    cmd = ["runuser", "-u", AGENT, "--", "env", "-i", *[f"{k}={v}" for k, v in agent_env("hermes").items()],
           "timeout", str(limit), PYTHON3, "-I", "-B", script, mode, workdir, str(w)]
    p = subprocess.Popen(cmd, cwd=workdir, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                         pass_fds=(w,), start_new_session=True)
    os.close(w)
    t_rec = threading.Thread(target=_drain, args=(r, rec, HARNESS_REC_MAX), daemon=True)
    t_out = threading.Thread(target=_drain, args=(p.stdout.fileno(), out, HARNESS_OUT_MAX), daemon=True)
    t_rec.start()
    t_out.start()
    try:
        p.wait(timeout=limit + 30)
    except subprocess.TimeoutExpired:
        timed_out = True
        try:
            os.killpg(p.pid, signal.SIGKILL)
        except OSError:
            pass
    leftovers = 0
    if PW is not None:
        killed, stuck = reap_since(PW.pw_uid, up0, roots=(p.pid,))
        leftovers = len((set(killed) | set(stuck)) - {p.pid})       # the step's own process (already waited for) is always in the list
    t_rec.join(20)
    t_out.join(20)
    if t_rec.is_alive() or t_out.is_alive():
        timed_out = True
    os.close(r)
    p.stdout.close()
    recs, bad = _parse_records(b"".join(rec))
    code = p.returncode
    return {"records": recs, "bad": bad, "exit": code, "timed_out": timed_out or code == 124, "leftovers": leftovers,
            "output": b"".join(out).decode("utf-8", "replace")}


# ---- the sandbox supervisor: every piece of delivered code runs as the SANDBOX identity, one fresh process per test id
HARNESS2_VERSION = "lab-harness/2"
SANDBOX_COLLECT_S, SANDBOX_PROC_S, SANDBOX_TOTAL_S, SANDBOX_MAX_PROCS = 30, 60, 600, 200
SANDBOX_OUT_CAP = 64_000
FAILED_IMPORT = "unittest.loader._FailedTest."


def sandbox_process(boundary, script, mode, files, args, limit_s, limits=None):
    """Run ONE harness process as the sandbox identity in a brand-new scratch folder holding a fresh copy of the delivered files, and return
    the step dict judge_harness reads plus `wall`, `cpu`, `signal`. The runner (root) holds the result pipe; the process gets it as one
    extra descriptor (3 or so) and nothing else: stdin /dev/null, stdout and stderr one pipe the runner drains with a size cap. Limits: wall
    clock here, CPU / memory / processes / files / file size in the launcher. When it has exited (or been killed at the deadline) EVERY
    process of the sandbox identity is killed and counted as a leftover; the scratch folder is deleted. Exit status and CPU come from wait4."""
    sb = boundary.sandbox
    ISO.kill_uid(sb.pw_uid)                                      # nothing of an earlier step is alive, and nothing it left in a shared folder remains
    ISO.purge_uid_files(sb.pw_uid)
    scratch = tempfile.mkdtemp(prefix="lab-sbx-")
    work = os.path.join(scratch, "work")
    r = w = -1
    p = None
    out, rec = [], []
    t_rec = t_out = None
    status, ru, timed_out = 0, None, False
    try:
        os.mkdir(work, 0o700)
        for name, text in files:
            fd = os.open(os.path.join(work, name), os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(text)
            os.chown(os.path.join(work, name), sb.pw_uid, sb.pw_gid)
        for d in (work, scratch):
            os.chown(d, sb.pw_uid, sb.pw_gid)
            os.chmod(d, 0o700)
        ISO.kill_uid(sb.pw_uid)                                  # nothing of an earlier step is alive
        r, w = os.pipe()
        t0 = time.time()
        p = ISO.spawn_sandboxed(boundary, [ISO.PYTHON3, "-I", "-B", script, mode, work, str(w), *args], scratch, stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, pass_fds=(w,), limits=limits, cwd=work)
        os.close(w)
        w = -1
        t_rec = threading.Thread(target=_drain, args=(r, rec, HARNESS_REC_MAX), daemon=True)
        t_out = threading.Thread(target=_drain, args=(p.stdout.fileno(), out, SANDBOX_OUT_CAP), daemon=True)
        t_rec.start(); t_out.start()
        deadline = t0 + limit_s
        while True:
            pid, status, ru = os.wait4(p.pid, os.WNOHANG)
            if pid:
                break
            if time.time() > deadline:
                timed_out = True
                try:
                    os.killpg(p.pid, signal.SIGKILL)
                except OSError:
                    pass
                pid, status, ru = os.wait4(p.pid, 0)
                break
            time.sleep(0.01)
        p.returncode = 0                                          # reaped here (wait4 for the rusage); Popen must not wait again
        wall = time.time() - t0
        killed, stuck = ISO.kill_uid(sb.pw_uid)                  # every process of the identity dies; any that was still alive is a leftover
        t_rec.join(20); t_out.join(20)
        if t_rec.is_alive() or t_out.is_alive():
            timed_out = True
        recs, bad = _parse_records(b"".join(rec))
        if os.WIFEXITED(status):
            code, sig = os.WEXITSTATUS(status), 0
        else:
            code, sig = -os.WTERMSIG(status), os.WTERMSIG(status)
        return {"records": recs, "bad": bad, "exit": code, "signal": sig, "timed_out": timed_out, "leftovers": len(set(killed) | set(stuck)),
                "output": b"".join(out).decode("utf-8", "replace"), "wall": wall, "cpu": (ru.ru_utime + ru.ru_stime) if ru else 0.0}
    finally:
        for fd in (r, w):
            if fd >= 0:
                try:
                    os.close(fd)
                except OSError:
                    pass
        if p is not None:
            if p.poll() is None:
                try:
                    os.killpg(p.pid, signal.SIGKILL)
                except OSError:
                    pass
            if p.stdout:
                p.stdout.close()
        ISO.kill_uid(sb.pw_uid)
        ISO.purge_uid_files(sb.pw_uid)                           # world-writable folders and System V IPC: the next test starts from nothing
        shutil.rmtree(scratch, ignore_errors=True)


def synth_collect(ids):
    """What a collection step would have said for exactly these ids: the reference a single-test process is judged against."""
    recs = [{"e": "c", "id": i, "seq": k, "pid": 1} for k, i in enumerate(ids)] + [{"e": "collected", "n": len(ids), "seq": len(ids), "pid": 1}]
    return {"records": recs, "bad": [], "exit": 0, "timed_out": False, "output": ""}


def sandbox_run(boundary, files):
    """Run the delivered tests inside the boundary and return (problems, record) like portable_run.
    (a) one sandbox process collects the test ids (importing the files runs their module-level code, so it is isolated and time-boxed);
    (b) every collected id then runs in its OWN fresh sandbox process (fresh scratch folder, own timeout, own result pipe), or in chunks of ids
        when there are more than SANDBOX_MAX_PROCS (granularity "chunk:N");
    (c) the supervisor accepts a process only if its records are structurally exact (schema, ids exactly the ones it was given, counts
        consistent, sequence, nothing after the final record), the exit status is the one its own records imply, it was not killed by a
        signal or the clock, and no process of the sandbox identity survived it;
    (d) the totals are summed by the supervisor from the per-process facts; CPU and wall time come from the operating system (wait4).
    What a lying test can still do: print or write a consistent record for ITS OWN process (so it can claim ITS OWN id passed without having
    checked anything); it cannot write another id's record, change another process's exit status, see another process's scratch folder (each is
    fresh and deleted), reach any verdict state, or reach the verifier."""
    hd = tempfile.mkdtemp(prefix="harness-")
    t0 = time.time()
    problems, per = [], []
    cpu, procs = 0.0, 0
    try:
        os.chmod(hd, 0o755)
        script = os.path.join(hd, "lab_harness.py")
        with open(script, "w", encoding="utf-8") as fh:
            fh.write(HARNESS_SRC)
        os.chmod(script, 0o644)
        collect = sandbox_process(boundary, script, "collect", files, [], SANDBOX_COLLECT_S)
        procs += 1
        cpu += collect["cpu"]
        cprobs, _ = judge_collect(collect)
        leftovers = collect["leftovers"]
        ids = []
        if not cprobs:
            ids, _at = _collected(collect["records"])
        if cprobs or ids is None:
            return finish_run(files, t0, cprobs or ["the delivered tests could not be collected"], None, leftovers, collect["output"],
                              collect["exit"], collect["exit"], boundary.describe(procs, "id", cpu), HARNESS2_VERSION)
        failed_imports = [i for i in ids if i.startswith(FAILED_IMPORT)]
        runnable = [i for i in ids if not i.startswith(FAILED_IMPORT)]
        if len(runnable) > SANDBOX_MAX_PROCS:
            size = -(-len(runnable) // SANDBOX_MAX_PROCS)
            groups, gran = [runnable[i:i + size] for i in range(0, len(runnable), size)], f"chunk:{size}"
        else:
            groups, gran = [[i] for i in runnable], "id"
        outputs, outcomes, exits = [], [], []
        passed = failed = errored = skipped = xfail = 0
        for g in groups:
            if time.time() - t0 > SANDBOX_TOTAL_S:
                problems.append("the delivered tests used up the total time allowed for a run")
                break
            run = sandbox_process(boundary, script, "run", files, g, SANDBOX_PROC_S)
            procs += 1
            cpu += run["cpu"]
            leftovers += run["leftovers"]
            outputs.append(run["output"])
            if run["signal"] or run["exit"] == 126:
                problems.append(f"a test process was killed by signal {run['signal']}" if run["signal"] else
                                "a test process could not be started inside the sandbox: " + run["output"].strip()[-120:])
                continue
            probs, facts = judge_harness(synth_collect(g), run)
            if probs:
                problems += probs
                continue
            outcomes += facts["outcomes"]
            exits.append(run["exit"])
            passed += facts["tests_passed"]; failed += facts["tests_failed"]; errored += facts["tests_errored"]
            skipped += facts["tests_skipped"]; xfail += facts["tests_expected_failures"]
        errored += len(failed_imports)
        outcomes += [(i, "error") for i in failed_imports]
        n = len(outcomes)
        out = "\n".join(outputs)[-SANDBOX_OUT_CAP:]
        if problems or n != len(ids):
            if n != len(ids) and not problems:
                problems.append(f"only {n} of {len(ids)} collected tests finished")
            facts = None
        else:
            ids_sha = hashlib.sha256("\n".join(sorted(ids)).encode("utf-8", "replace")).hexdigest()
            facts = {"tests_collected": len(ids), "tests_run": n, "tests_passed": passed, "tests_failed": failed, "tests_errored": errored,
                     "tests_skipped": skipped, "tests_expected_failures": xfail, "collected_ids_sha256": ids_sha,
                     "harness_sha256": hashlib.sha256(json.dumps({"v": HARNESS2_VERSION, "ids": ids_sha, "outcomes": sorted(outcomes), "exits": exits,
                                                                  "gran": gran}, sort_keys=True, separators=(",", ":")).encode()).hexdigest()}
        exit_code = 0 if facts and not failed and not errored else (1 if facts else -1)
        return finish_run(files, t0, problems, facts, leftovers, out, exit_code, collect["exit"], boundary.describe(procs, gran, cpu), HARNESS2_VERSION)
    finally:
        shutil.rmtree(hd, ignore_errors=True)


def judge_collect(collect):
    """(problems, ids) for a collection step. Pure."""
    probs = [f"collection step: {b}" for b in collect["bad"]]
    if collect["timed_out"]:
        probs.append("the collection step of the delivered tests ran out of time")
    if collect.get("signal"):
        probs.append(f"the collection step was killed by signal {collect['signal']}")
    if len({r.get("pid") for r in collect["records"]}) > 1:
        probs.append("the collection step's records come from more than one process")
    ids, at = _collected(collect["records"])
    if ids is None:
        probs.append("the delivered tests could not be collected: " + str(at))
        return probs, None
    if collect["exit"] != 0 or len(collect["records"]) != at:
        probs.append(f"the collection step did not end normally (exit {collect['exit']}); importing the test files stopped the process")
    return probs, ids


def ISO_NONE_BLOCK():
    """The isolation block of a run that had no separation (the legacy harness, as the agent user): the kernel never verifies on it."""
    b = CURRENT_BOUNDARY
    reasons = list(b.reasons) if b is not None else ["no isolation boundary was set up for this run"]
    if b is not None and b.backend != "none":
        reasons.append(("isolation self-test: " + b.self_test) if b.self_test != "ok" else "the sandbox was not used for this run")
    return ISO.Boundary("none", reasons, agent=PW).describe()


def finish_run(files, t0, probs, facts, leftovers, out, exit_code, collect_exit, isolation, version, tail_extra=""):
    """(problems, record) from the judged facts of one test run, whatever backend ran it. The record is the runner's `trusted_execution`."""
    ran = facts["tests_run"] if facts else 0
    summary = (f"[lab-runner] {facts['tests_passed']} passed, {facts['tests_failed']} failed, {facts['tests_errored']} errors, "
               f"{facts['tests_skipped']} skipped, {facts['tests_collected']} collected" if facts else
               f"[lab-runner] no complete test report ({'; '.join(probs)[:200] or 'unknown'})")
    full = out + "\n" + summary
    record = {"ran_by": "lab-runner", "command": "python3 lab harness (unittest discover, test*.py)", "harness": version,
              "harness_script_sha256": HARNESS_SHA256, "exit_code": exit_code, "collect_exit_code": collect_exit,
              "tests_run": ran, "tests_collected": facts["tests_collected"] if facts else 0, "tests_passed": facts["tests_passed"] if facts else 0,
              "tests_failed": facts["tests_failed"] if facts else 0, "tests_errored": facts["tests_errored"] if facts else 0,
              "tests_skipped": facts["tests_skipped"] if facts else 0,
              "tests_expected_failures": facts["tests_expected_failures"] if facts else 0, "leftover_processes": leftovers,
              "collected_ids_sha256": facts["collected_ids_sha256"] if facts else hashlib.sha256(b"").hexdigest(),
              "harness_sha256": facts["harness_sha256"] if facts else hashlib.sha256(b"no complete report").hexdigest(),
              "output_sha256": hashlib.sha256(full.encode("utf-8", "replace")).hexdigest(), "output_tail": full.strip()[-300:],
              "files_sha256": snapshot_digest(files), "seconds": round(time.time() - t0, 1), "isolation": isolation}
    if leftovers:
        return [f"the delivered tests left {leftovers} process(es) running after they finished (a clean run leaves none); they were "
                "stopped and the run is not evidence"] + probs, record
    if probs:
        return ["the delivered tests did not produce a complete, trustworthy report: " + " | ".join(probs)[:500]], record
    if facts["tests_run"] < 1:
        return ["the delivered test files ran no tests (python3 -m unittest found nothing to run)"], record
    if facts["tests_failed"] or facts["tests_errored"] or exit_code != 0:
        tail = [x for x in out.strip().splitlines() if x.strip()][-4:]
        return [f"the delivered tests fail when run from a fresh folder (lab harness, exit {exit_code}): {facts['tests_failed']} failed, "
                f"{facts['tests_errored']} errors | " + " | ".join(tail)[:400]], record
    if facts["tests_passed"] < 1:
        return ["the delivered tests ran but none passed (all skipped); skipped tests are not evidence"], record
    return [], record


CURRENT_BOUNDARY = None                              # the ISO.Boundary of the verification in progress (set by handle; None outside a run)


def portable_run(folder, files):
    """Run the delivered tests from a fresh, empty folder (as the agent user, time-limited), the way the owner will, and record what
    happened. Returns (problems, record). `record` is None when there are no tests to run. The runner itself starts the processes and counts the
    tests, so the record is the RUNNER's evidence (`trusted_execution`): nothing the verifier or the tests print can change it.
    It proves that the worker's tests all ran to the end and passed on the exact bytes that will be delivered; it does not prove the
    tests are any good.

    Re-review H2 (2026-10-18): the number of tests used to be parsed from the 'Ran N tests' line the tests' own process printed on stderr, so a
    'test' that printed that line and called os._exit(0) was recorded as N tests and exit 0. Now (1) a SEPARATE process, time-boxed, only
    imports the test files and lists their ids (the static collection); (2) a second process runs exactly those tests through the unittest
    API under a runner-owned harness (HARNESS_SRC) that reports each test start and stop to a pipe the runner holds; (3) the runner
    counts from those records, requires every collected test to have finished, a final report as the last record, the harness's exit
    code, and no process of the agent user left running afterwards (they are killed and the run is void); (4) at least one test must have
    PASSED (skipped-only is not a pass). A premature exit leaves a gap and the run is void.
    RESIDUAL, exactly: the tests run inside the harness's own process and OS user, so a test (or any module it imports) that is written to
    attack the harness can write fake records to the pipe's file descriptor (the number is in argv), replace the harness's functions, or
    patch the unittest classes, and produce a complete and consistent report. Only running the tests as a different OS user or in a
    container that the harness does not share can prevent it (jarvis/caretaker/README.md)."""
    if not any(n.startswith("test") and n.endswith(".py") for n, _ in files):
        return [], None
    if CURRENT_BOUNDARY is not None and CURRENT_BOUNDARY.active:       # separated execution: see sandbox_run
        return sandbox_run(CURRENT_BOUNDARY, files)
    hd = tempfile.mkdtemp(prefix="harness-")       # root-owned (the runner's own), readable by the agent user: the tests cannot edit the harness
    d = tempfile.mkdtemp(prefix="portable-")       # the service's private /tmp, root 700 while root writes (no link races)
    try:
        os.chmod(hd, 0o755)
        script = os.path.join(hd, "lab_harness.py")
        with open(script, "w", encoding="utf-8") as fh:
            fh.write(HARNESS_SRC)
        os.chmod(script, 0o644)
        for name, text in files:
            fd = os.open(os.path.join(d, name), os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o644)
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(text)
            if PW is not None:
                os.chown(os.path.join(d, name), PW.pw_uid, PW.pw_gid)
        if PW is not None:
            os.chown(d, PW.pw_uid, PW.pw_gid)        # then hand it to the agent user (tests may write files)
        t0 = time.time()
        collect = harness_step("collect", d, script, HARNESS_COLLECT_S)
        run = harness_step("run", d, script, HARNESS_RUN_S) if collect["exit"] == 0 and not collect["bad"] and not collect["timed_out"] else None
        probs, facts = judge_harness(collect, run)
        leftovers = collect["leftovers"] + (run["leftovers"] if run else 0)
        out = ((run or collect)["output"])
        exit_code = run["exit"] if run and run["exit"] is not None else (collect["exit"] if collect["exit"] not in (None, 0) else -1)
        return finish_run(files, t0, probs, facts, leftovers, (run or collect)["output"], exit_code,
                          collect["exit"] if collect["exit"] is not None else -1, ISO_NONE_BLOCK(), HARNESS_VERSION)
    finally:
        subprocess.run(["rm", "-rf", "--one-file-system", d, hd])


def portable_check(folder, files):
    """The problems portable_run found ([] when there are no tests or they pass). Kept for callers that need no record."""
    return portable_run(folder, files)[0]


def deliver(jid, files):
    """Send each verified file to the owner's laptop through the kernel and wait until the laptop confirms it.
    Returns (evidence lines, blocker or "")."""
    queued = []
    for name, text in files:
        code, r = api("/v1/jarvis/runner/deliver", {"job": jid, "name": name, "content": text})
        if code != 200:
            return [], f"delivery of {name} refused: {r.get('reason', code)}"
        queued.append((name, r["job"], r.get("redacted") or []))
    lines, t0, pending = [], time.time(), list(queued)
    while pending:
        action, why = follow_status(*api("/v1/jarvis/runner/status", {"job": jid}))      # the kill switch ends the wait too
        if action == "stop":
            return lines, why
        if time.time() - t0 > DELIVER_WAIT_S:
            return lines, f"the laptop did not confirm {', '.join(n for n, _, _ in pending)} in {DELIVER_WAIT_S // 60} min (offline?)"
        time.sleep(POLL_S)
        for item in list(pending):
            name, out, redacted = item
            code, j = api("/v1/jarvis/runner/delivery", {"job": jid, "delivery": out})
            if code != 200 or j.get("state") in ("queued", "running"):
                continue                                         # a blip or still waiting for the laptop
            res = j.get("result") or {}
            if j.get("state") != "done" or not res.get("ok"):
                return lines, f"delivery of {name} {j.get('state')}: {res.get('error', 'no result')}"
            note = f" (kernel redacted: {', '.join(redacted)})" if redacted else ""
            lines.append(f"delivered: {res.get('folder')}\\{res.get('saved')} ({res.get('chars')} chars) job:{out}{note}")
            pending.remove(item)
    return lines, ""


USAGE = os.path.join(HOME, ".hermes", "skills", ".usage.json")    # Hermes's own skill telemetry (tools/skill_usage.py)

# The agent's toolchain and start-up files: a task must never change them (a planted ~/.local/bin/python3 gamed a check
# and broke every later task). Fingerprinted around each worker/verifier run; any change is put back and fails the attempt.
GUARDED_DIRS = [LOCAL_BIN, os.path.join(HOME, ".hermes", "skills", "jarvis-staged")]
GUARDED_FILES = [os.path.join(HOME, f) for f in (".bashrc", ".profile", ".bash_profile", ".bash_login", ".pythonrc", ".pydistutils.cfg",
                                                  ".config/pip/pip.conf", ".hermes/config.yaml", ".hermes/SOUL.md")]
QUARANTINE = "/root/quarantine"
SMALL = 2_000_000                              # files up to this size are kept in memory so a change can be put back


def _fingerprint():
    """{path: (kind, size, mtime_ns, inode, content or None)} for the guarded paths (links are not followed)."""
    out = {}
    paths = list(GUARDED_FILES)
    for d in GUARDED_DIRS:
        for root, dirs, files in os.walk(d):
            paths += [os.path.join(root, f) for f in files + [x for x in dirs if os.path.islink(os.path.join(root, x))]]
    for p in paths:
        try:
            st = os.lstat(p)
        except OSError:
            continue
        kind = "link" if stat.S_ISLNK(st.st_mode) else "file"
        try:
            data = os.readlink(p) if kind == "link" else (read_file_nofollow(p, SMALL) if st.st_size <= SMALL else None)
        except OSError:                                    # swapped for a link while it was read (or unreadable): compared by size, time, inode instead
            data = None
        out[p] = (kind, st.st_size, st.st_mtime_ns, st.st_ino, st.st_mode & 0o7777, data)
    return out


def _same(a, b):
    """Same file: content for small files (a rewrite with identical bytes is no change), else size/mtime/inode."""
    if a[0] != b[0] or a[4] != b[4]:
        return False
    if a[5] is not None and b[5] is not None:
        return a[5] == b[5]
    return a[1:4] == b[1:4]


def _quarantine(p, qpath):
    """Move `p` (a file or a link of the agent user's toolchain) into the quarantine folder without following any link on the way."""
    dfd = _walk_dirs(os.path.dirname(p))
    try:
        base = os.path.basename(p)
        qfd = _walk_dirs(os.path.dirname(qpath))
        try:
            os.rename(base, os.path.basename(qpath), src_dir_fd=dfd, dst_dir_fd=qfd)
            return
        except OSError as e:
            if e.errno != errno.EXDEV:
                raise
        finally:
            os.close(qfd)
        st = os.lstat(base, dir_fd=dfd)                           # another file system: copy, then remove
        if stat.S_ISLNK(st.st_mode):
            os.symlink(os.readlink(base, dir_fd=dfd), qpath)
        else:
            fd = os.open(base, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=dfd)
            try:
                with open(qpath, "xb") as out:
                    while True:
                        chunk = os.read(fd, 1 << 20)
                        if not chunk:
                            break
                        out.write(chunk)
            finally:
                os.close(fd)
        os.unlink(base, dir_fd=dfd)
    finally:
        os.close(dfd)


def _put_back(p, old):
    """Re-create the toolchain file or link `p` as it was (`old` = a fingerprint row), never through a link."""
    if old[0] == "link":
        dfd = _walk_dirs(os.path.dirname(p))
        try:
            os.symlink(old[5], os.path.basename(p), dir_fd=dfd)
        finally:
            os.close(dfd)
    else:
        write_file_nofollow(p, old[5], old[4], PW)


def guard_restore(before, label):
    """Compare with the fingerprint taken before the run; put back anything changed. Returns the problems found. As root, inside the agent user's
    tree: every move, read and write goes through the link-safe helpers (a link planted at the moment of the restore is refused, not followed)."""
    after, problems = _fingerprint(), []
    qdir = os.path.join(QUARANTINE, time.strftime("%Y%m%d-%H%M%S") + "-" + label)
    for p, now in after.items():
        old = before.get(p)
        if old and _same(old, now):
            continue
        rel = p.replace(HOME, "~")
        os.makedirs(qdir, mode=0o700, exist_ok=True)
        try:
            _quarantine(p, os.path.join(qdir, rel.replace("/", "_")))       # keep the evidence, out of the agent's reach
        except OSError as e:
            problems.append(f"the task changed {rel} and it could not be moved to quarantine safely ({type(e).__name__})")
            continue
        if old and old[5] is not None:                                   # changed: put the original back
            try:
                _put_back(p, old)
                problems.append(f"the task changed {rel}; it was put back")
            except OSError as e:
                problems.append(f"the task changed {rel}; it could not be put back safely ({type(e).__name__})")
        elif old:
            problems.append(f"the task changed {rel} (too large to put back automatically; moved to quarantine)")
        else:
            problems.append(f"the task added {rel}; it was removed")
    for p in set(before) - set(after):
        old = before[p]
        if old[5] is not None:
            try:
                _put_back(p, old)
            except OSError as e:
                problems.append(f"the task deleted {p.replace(HOME, '~')}; it could not be put back safely ({type(e).__name__})")
                continue
        problems.append(f"the task deleted {p.replace(HOME, '~')}; it was put back")
    return [x + ". Never change the toolchain or start-up files to satisfy a check." for x in problems][:8]


def skill_counts():
    """{skill: views + uses} from Hermes's usage file (read like any agent file: no links, the agent's own)."""
    try:
        data = json.loads(safe_read(USAGE, 2_000_000) or "{}")
    except (ValueError, RecursionError):
        return {}
    out = {}
    for k, v in (data.items() if isinstance(data, dict) else ()):
        if isinstance(v, dict):
            try:                                   # the agent user writes this file: a string, a list or 1e999 must not abort the job
                out[str(k)[:80]] = int(v.get("view_count") or 0) + int(v.get("use_count") or 0)
            except (ValueError, TypeError, OverflowError):
                continue
    return out


def skills_used(before):
    after = skill_counts()
    return sorted(k for k, n in after.items() if n > before.get(k, 0))[:20]


def post_final(body, tries=5):
    """POST a final report, retrying when the kernel cannot be reached or fails (code 0 or 5xx); a 4xx is the kernel's decision and is returned
    at once. Returns (code, reply). Bounded: a kernel that stays away leaves the task for the owner, but the runner never reports success."""
    code, reply = 0, {}
    for i in range(tries):
        code, reply = api("/v1/jarvis/runner/report", body)
        if code == 200 or 400 <= code < 500:
            break
        time.sleep(min(POLL_S, 2.0) * (i + 1) / 4)
    return code, reply if isinstance(reply, dict) else {}


def prepare_boundary():
    """Resolve the isolation backend and run its self-test, now (before every verification). Never raises; anything wrong = mode "none"."""
    try:
        b = ISO.resolve(PW)
        if b.backend == "identity":
            ISO.run_self_test(b)
        elif b.backend == "container":
            b.self_test = "failed: the container backend was not run on this server (NOT RUN)"
        return b
    except BaseException as e:                                   # fail closed, with the reason visible
        return ISO.Boundary("none", [f"isolation setup failed: {type(e).__name__}: {str(e)[:80]}"], agent=PW)


def make_view(files, evidence):
    """A root-owned, read-only copy of the deliverable for the verifier (cwd of its session): deliver/<files> and EVIDENCE.md, mode 0644 / 0755,
    owned by the runner, in a folder nobody else can write. Returns its path."""
    view = tempfile.mkdtemp(prefix="vview-")
    os.makedirs(os.path.join(view, DELIVER))
    for name, text in files:
        with open(os.path.join(view, DELIVER, name), "w", encoding="utf-8") as fh:
            fh.write(text)
        os.chmod(os.path.join(view, DELIVER, name), 0o644)
    if evidence is not None:
        with open(os.path.join(view, "EVIDENCE.md"), "w", encoding="utf-8") as fh:
            fh.write(evidence)
        os.chmod(os.path.join(view, "EVIDENCE.md"), 0o644)
    os.chmod(os.path.join(view, DELIVER), 0o755)
    os.chmod(view, 0o755)
    return view


def drop_view(*paths):
    for pth in paths:
        if pth:
            shutil.rmtree(pth, ignore_errors=True)


def handle(job):
    """Run one lab job. The isolation boundary in force is per attempt (CURRENT_BOUNDARY); it is cleared when the job ends."""
    global CURRENT_BOUNDARY
    try:
        return _handle(job)
    finally:
        CURRENT_BOUNDARY = None


def _handle(job):
    global CURRENT_BOUNDARY
    p = job["params"]
    jid, task, kind = job["id"], p["task"], p["agent"]
    folder = os.path.join(WORK, task)
    ensure_folder(folder, PW)                    # no link on the way: a symlinked job folder would have been chowned to the agent user, wherever it led
    per_try = max(300, p["timeout_s"] // p["max_attempts"])
    wants = bool(WANTS_DELIVERY.search(p["instructions"] + "\n" + "\n".join(p["criteria"])))
    problems, verdict, history = [], None, []
    for attempt in range(1, p["max_attempts"] + 1):
        state = "running" if attempt == 1 else "repairing"
        if attempt > 1:
            api("/v1/jarvis/runner/report", {"job": jid, "state": "repairing", "note": "; ".join(problems)[:900],
                                              "step": f"attempt {attempt}: fixing {len(problems)} problem(s)"})
        code, r = api("/v1/jarvis/runner/report", {"job": jid, "state": "running", "attempt": True,
                                                   "step": f"attempt {attempt}: {kind} is working"})
        if code != 200:
            return finish(jid, False, f"could not start attempt {attempt}: {r.get('reason', code)}", attempt)
        usage0, guard0 = skill_counts(), _fingerprint()
        up_work = uptime()
        LEFTOVER_SURVIVORS.clear()
        rc, secs, why = run_agent(jid, kind, work_prompt(task, folder, p["instructions"], p["criteria"], attempt, problems),
                                  folder, per_try, f"work-{attempt}.log")
        # Whatever the worker left running (a normal exit included) dies BEFORE anything reads a verdict (audit M8).
        # run_agent already did this for its own process tree; this is the sweep by user and start time, and it also covers a
        # run_agent that was replaced in a test. Survivors void the attempt: a process we cannot kill could still forge.
        _, survivors = reap_since(PW.pw_uid, up_work) if PW is not None else ([], [])
        survivors = sorted(set(survivors) | set(LEFTOVER_SURVIVORS))
        tampered = guard_restore(guard0, f"{task}-work-{attempt}")
        if survivors:
            tampered.append(f"{len(survivors)} process(es) the worker started could not be stopped; the attempt cannot be verified")
        if tampered:
            api("/v1/jarvis/runner/report", {"job": jid, "note": "integrity: " + "; ".join(tampered)[:900]})
        used = skills_used(usage0)
        if used:                                                 # the kernel's history says which skills shaped this attempt
            api("/v1/jarvis/runner/report", {"job": jid, "note": f"skills used (attempt {attempt}): " + ", ".join(used)})
        if why in STOP_REASONS:
            return stopped(jid, why, attempt)
        clear_verdicts(folder)                                   # only the verifier of THIS run writes a verdict, and not here
        api("/v1/jarvis/runner/report", {"job": jid, "state": "verifying",
                                          "step": f"attempt {attempt}: independent check ({'worker timed out' if rc is None else f'worker exit {rc}'}, {int(secs)} s)"})
        guard1 = _fingerprint()
        # The isolation boundary of THIS verification: identities resolved, self-test run (fail closed: anything wrong = mode "none").
        boundary = CURRENT_BOUNDARY = prepare_boundary()
        view = vparent = None
        files0 = None
        vuser = None
        if boundary.active:                                      # separated: the verifier gets a read-only root-owned copy, in its own identity
            ISO.kill_uid(boundary.sandbox.pw_uid)
            vuser = boundary.verifier_pw()
            if vuser.pw_uid != PW.pw_uid:
                ISO.kill_uid(vuser.pw_uid)
            files0, _bad0 = deliverables(folder, wants)
            view = make_view(files0, safe_read(os.path.join(folder, "EVIDENCE.md"), 20_000))
            vparent = tempfile.mkdtemp(prefix="vstate-")         # root-owned; the sandbox identity may enter it but not the slot inside
            os.chmod(vparent, 0o711)
            slot, nonce = new_verdict_slot(vparent, vuser, closed=True)
        else:
            slot, nonce = new_verdict_slot(folder)               # fresh, random, made after the worker's processes are dead
        out = VerifierOutput()                                   # the verifier's stdout: the authority for its verdict (re-review H2)
        want = {"nonce": nonce, "task": task, "job": jid, "attempt": attempt}
        digest0, up_verify = artifact_digest(folder), uptime()
        LEFTOVER_SURVIVORS.clear()

        def at_exit():                                           # the verifier has just exited, nothing swept yet: copy and seal both halves
            out.freeze()
            slot.freeze()
        extra_run = {"user": vuser, "cwd": view} if boundary.active else {}
        vrc, vsecs, vwhy = run_agent(jid, "hermes", verify_prompt(task, p["criteria"], {"path": slot.verdict_path, **want}),
                                     folder, VERIFY_TIMEOUT_S, f"verify-{attempt}.log", capture=out, on_exit=at_exit, **extra_run)
        at_exit()                                                # no-op after a real run_agent; the freeze for a stand-in that returned
        _, vsurvivors = reap_since(PW.pw_uid, up_verify) if PW is not None else ([], [])     # nothing of the verifier's outlives it either
        vsurvivors = sorted(set(vsurvivors) | set(LEFTOVER_SURVIVORS))
        tampered += guard_restore(guard1, f"{task}-verify-{attempt}")
        if vwhy in STOP_REASONS:
            slot.close()
            out.close()
            drop_view(view, vparent)
            clear_verdicts(folder)
            return stopped(jid, vwhy, attempt)
        digest1 = artifact_digest(folder)
        # Every process of the agent user is dead (both sweeps above). The verdict is the one the verifier PRINTED as its final answer
        # (a runner-owned capture, copied at the verifier's exit and compared again now); the file it also wrote was frozen and sealed at
        # that exit and is only a cross-check: a difference between them, or between the exit and now, voids the attempt. The bytes
        # judged here are the bytes sent on (nothing is re-read from the disk).
        verdict, why_bad, verdict_sha, channel_sha = settle_verdict(slot, out, want)
        slot.close()
        out.close()
        drop_view(view, vparent)
        clear_verdicts(folder)
        if verdict is None:
            verdict = {"passed": False, "problems": [why_bad], "failure_class": "tool"}
            if why_bad != "the verifier wrote no VERIFY.json":
                api("/v1/jarvis/runner/report", {"job": jid, "note": "integrity: " + why_bad[:300]})
        else:
            if vrc is None:       # a verifier that timed out did not finish its own check: a file found afterwards is not its verdict
                verdict = {"passed": False, "problems": ["the verifier ran out of time; a verdict file found afterwards is not accepted"],
                           "failure_class": "timeout"}
            else:
                verdict = sanitize_verdict(verdict)              # only passed / criteria / problems go on; the runner adds the rest
            verdict.update(verdict_sha256=verdict_sha, verdict_channel_sha256=channel_sha, verifier_exit=vrc)
        # The identity the kernel checks every verdict against, stamped by the runner on whatever it reports (also a refusal).
        verdict.update(artifact_sha256=digest1, attempt=attempt, task=task, job=jid, criteria_sha256=criteria_digest(p["criteria"]))
        if digest0 != digest1:
            tampered.append("the delivered files changed while the verifier was checking them")
        if vsurvivors:
            tampered.append(f"{len(vsurvivors)} process(es) started during the check could not be stopped")
        if rc is None:
            verdict.setdefault("problems", []).insert(0, f"the worker ran out of time ({per_try} s)")
            verdict["failure_class"] = "timeout"
        if tampered:                                             # a changed toolchain voids the attempt, whatever the verifier said
            verdict["passed"] = False
            verdict["failure_class"] = "integrity"
            verdict["problems"] = tampered + list(verdict.get("problems") or [])
        files, bad = deliverables(folder, wants)                 # read ONCE; these bytes are what is run, checked and delivered
        if files0 is not None and not bad and snapshot_digest(files) != snapshot_digest(files0):
            bad = ["the delivered files changed between the copy the verifier saw and the files the runner tested"]
        trusted = None
        if not bad and verdict.get("passed") is True:
            up_trusted = uptime()
            bad, trusted = portable_run(folder, files)           # the runner's own execution of the delivered tests (trusted_execution)
            _, t_surv = reap_since(PW.pw_uid, up_trusted) if PW is not None else ([], [])
            if t_surv:
                bad.append(f"{len(t_surv)} process(es) started by the delivered tests could not be stopped")
            if artifact_digest(folder) != digest1:
                bad.append("the delivered files changed while the runner ran their tests")
        if bad:                                                  # the runner's own check; the verifier cannot override it
            verdict["passed"] = False
            verdict["problems"] = bad + list(verdict.get("problems") or [])
            trusted = None
        if verdict.get("passed") is True:
            if trusted is not None:
                trusted.update(task=task, job=jid, attempt=attempt, artifact_sha256=digest1)
                # the verdict names the evidence it rests on; the kernel refuses a record that is not the one named (harness result, test ids)
                verdict.update(harness_sha256=trusted["harness_sha256"], collected_ids_sha256=trusted["collected_ids_sha256"])
            else:
                verdict["agent_assessed"] = True                 # no runnable tests: the kernel keeps this as "NOT verified"
        up_sealed = uptime()
        history.append({"attempt": attempt, "worker_exit": rc, "worker_s": int(secs), "passed": bool(verdict.get("passed"))})
        evidence = evidence_of(folder, verdict)
        if verdict.get("passed") is True:
            extra = {"trusted_execution": trusted} if trusted is not None else {}
            if files:
                api("/v1/jarvis/runner/report", {"job": jid, "step": f"attempt {attempt}: verified; delivering {len(files)} file(s) to the owner"})
                lines, blocker = deliver(jid, files)
                evidence = lines + evidence
                if blocker:
                    api("/v1/jarvis/runner/report", {"job": jid, "state": "failed", "result": f"verified, but NOT delivered: {blocker}"[:600],
                                                      "evidence": evidence, "verification": {**verdict, "history": history}, **extra,
                                                      "step": "stopped: delivery failed"})
                    return finish(jid, False, "delivery failed: " + blocker, attempt)
            # Again after sending: nothing of the agent user may have started since the check was sealed, and the files must be the ones checked.
            killed_late, stuck_late = reap_since(PW.pw_uid, up_sealed) if PW is not None else ([], [])
            late = killed_late or stuck_late
            if late or artifact_digest(folder) != digest1:
                why_late = ("a process of the agent user was started or survived after the check" if late
                            else "the delivered files changed after the check")
                api("/v1/jarvis/runner/report", {"job": jid, "state": "failed", "result": f"voided: {why_late}; the task is not verified"[:600],
                                                  "evidence": evidence, "verification": {**verdict, "passed": False, "failure_class": "integrity",
                                                                                          "problems": [why_late], "history": history},
                                                  "step": "stopped: integrity check after sending failed"})
                return finish(jid, False, why_late, attempt)
            if trusted is not None and trusted["isolation"]["mode"] == "full":       # the block the kernel will judge, not the boundary alone (groups, see describe)
                done = f"verified after {attempt} attempt(s)" + (f"; {len(files)} file(s) delivered and confirmed by the laptop" if files else "")
                step = "done: every criterion verified" + (", files delivered" if files else "")
            elif trusted is not None:                            # the tests passed, but the boundary is not one the kernel will verify on
                why_not = "; ".join(trusted["isolation"]["reasons"])[:300] or f"isolation mode {trusted['isolation']['mode']}"
                done = (f"completed after {attempt} attempt(s), NOT verified: the delivered tests passed ("
                        + ("inside the sandbox" if boundary.active else "WITHOUT an isolation boundary") + f"), but {why_not}"
                        + (f"; {len(files)} file(s) delivered and confirmed by the laptop" if files else ""))
                step = f"done: completed, NOT verified (isolation: {boundary.mode()})" + (", files delivered" if files else "")
            else:
                done = (f"completed after {attempt} attempt(s), NOT verified: no runnable tests were delivered, so only the verifier's own "
                        "opinion backs it" + (f"; {len(files)} file(s) delivered and confirmed by the laptop" if files else ""))
                step = "done: completed, NOT verified (no runnable tests)" + (", files delivered" if files else "")
            code, rep = post_final({"job": jid, "state": "completed", "result": done, "evidence": evidence,
                                    "verification": {**verdict, "history": history}, **extra, "step": step})
            if code != 200:                                      # the kernel did not take the report: the job did not succeed, whatever we think
                return finish(jid, False, f"the kernel did not accept the final report (HTTP {code}): {str(rep.get('reason', ''))[:150]}", attempt)
            return finish(jid, True, done, attempt)
        problems = [str(x)[:300] for x in (verdict.get("problems") or ["the verifier found unmet criteria"])][:8]
        if attempt == p["max_attempts"]:
            api("/v1/jarvis/runner/report", {"job": jid, "state": "failed", "result": f"not verified after {attempt} attempt(s): " + "; ".join(problems)[:600],
                                              "evidence": evidence, "verification": {**verdict, "history": history},
                                              "step": "stopped: retry budget used up"})
            return finish(jid, False, "retry budget used up", attempt)
    return finish(jid, False, "no attempt ran", 0)


def criteria_digest(criteria):
    """Hash of the job's criteria in the kernel's canonical form (tasks.criteria_sha256): the verdict must name the criteria it checked."""
    return hashlib.sha256(json.dumps([str(c) for c in (criteria or [])], ensure_ascii=True, separators=(",", ":")).encode()).hexdigest()


def evidence_of(folder, verdict):
    items = [f"workspace: {folder}"]
    for c in (verdict.get("criteria") or [])[:15]:
        if isinstance(c, dict):
            items.append(f"{'MET' if c.get('met') else 'NOT MET'}: {str(c.get('criterion'))[:200]} -- {str(c.get('evidence'))[:400]}")
    ev = safe_read(os.path.join(folder, "EVIDENCE.md"), 1500)
    items.append("EVIDENCE.md: " + ev if ev is not None else "EVIDENCE.md: missing")
    files = sorted(f for f in os.listdir(folder) if not f.endswith(".log"))[:40]
    items.append("files: " + ", ".join(files))
    return items


def stopped(jid, why, attempts):
    """The worker or verifier was stopped from outside. The owner's cancel already closed the task; the kill switch did not,
    so the runner closes it here, honestly: failed, with the reason, and nothing was verified or delivered (audit SEC-D10)."""
    if why == STOPPED_BY_KILL:
        api("/v1/jarvis/runner/report", {"job": jid, "state": "failed", "step": "stopped: the kill switch was switched on",
                                          "result": ("stopped by the kill switch: the lab worker and everything it started were killed. "
                                                     "Nothing was verified or delivered. Dispatch the task again after the switch is lifted.")})
    return finish(jid, False, why, attempts)


def finish(jid, ok, summary, attempts):
    api("/v1/jarvis/runner/finish", {"job": jid, "result": {"ok": ok, "summary": summary[:300], "attempts": attempts,
                                                             **({} if ok else {"error": summary[:200]})}})
    print(time.strftime("%H:%M:%S"), "job", jid, "ok" if ok else "failed", summary, flush=True)


def sweep_dedicated():
    """At start (after a crash or restart): everything still running as the sandbox or verify identity is a leftover of a dead attempt and is killed.
    Those identities run nothing else. Needs root; does nothing otherwise."""
    if os.geteuid() != 0:
        return
    for name in (ISO.SANDBOX_USER, ISO.VERIFY_USER):
        pw = ISO._getpw(name)
        if pw is not None and pw.pw_uid != 0 and (PW is None or pw.pw_uid != PW.pw_uid):
            _, survivors = ISO.kill_uid(pw.pw_uid)
            if not survivors:
                ISO.purge_uid_files(pw.pw_uid)
    tmp = tempfile.gettempdir()                                  # folders of an attempt that died with its runner (nothing else is running now)
    for n in os.listdir(tmp):
        if n.startswith(("vview-", "vstate-", "lab-sbx-", "harness-", "lab-selftest", "portable-")):
            shutil.rmtree(os.path.join(tmp, n), ignore_errors=True)


def main():
    if PW is None:
        sys.exit(f"the lab runner needs the unprivileged user '{AGENT}' (run it in the lab box)")
    try:
        ensure_folder(WORK, PW)
    except OSError as e:                         # the agent user turned part of its work folder into a link: do not start, say why
        sys.exit(f"the lab runner will not start: {WORK} is not a plain folder chain ({e}). Look at what the agent user did there, then restart.")
    sweep_dedicated()
    b = prepare_boundary()
    print("lab runner started; isolation:", b.mode(), "-", "; ".join(b.reasons + ([b.self_test] if b.self_test != "ok" else [])) or "self-test ok", flush=True)
    while True:
        code, r = api("/v1/jarvis/runner/next")
        job = r.get("job") if code == 200 else None
        if not job:
            time.sleep(POLL_S)
            continue
        print(time.strftime("%H:%M:%S"), "job", job["id"], "task", job["params"]["task"], flush=True)
        try:
            handle(job)
        except Exception as e:                          # one bad job must not stop the runner
            finish(job["id"], False, f"runner error: {type(e).__name__}", 0)


if __name__ == "__main__":
    main()
