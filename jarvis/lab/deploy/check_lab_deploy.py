#!/usr/bin/python3 -I
"""Check the lab box's isolation deployment (run as root ON the lab box, after installing; changes nothing).

    python3 -I /opt/jarvis-hub/check_lab_deploy.py            # or: --dir <install folder>

Prints one line per check, `OK`, `WARN` or `FAIL`, then the isolation boundary the runner will really use (the same self-test the runner runs before
every verification). Exit status 0 only when nothing FAILed. It checks facts the isolation relies on and that no test of this repository can see:
the three users and their groups, private home folders, root-owned install files and folders nobody else can write (the runner's own files,
the interpreter and its library folders, the folders on the sandbox's PATH), and the runner's token. A WARN is a hardening gap, not a hole.
"""
import json
import os
import pwd
import grp
import stat
import subprocess
import sys

DIR = os.path.dirname(os.path.abspath(__file__))
if "--dir" in sys.argv:
    DIR = os.path.abspath(sys.argv[sys.argv.index("--dir") + 1])
USERS = {"agent": os.environ.get("JARVIS_LAB_AGENT_USER", "agent"), "sandbox": os.environ.get("JARVIS_LAB_SANDBOX_USER", "jarvis-sandbox"),
         "verify": os.environ.get("JARVIS_LAB_VERIFY_USER", "jarvis-verify")}
TOKEN = os.environ.get("JARVIS_LAB_TOKEN_FILE", "/etc/jarvis-lab/runner.token")
bad = 0


def say(level, what):
    global bad
    bad += level == "FAIL"
    print(f"{level:4} {what}")


def root_only_chain(path, label):
    """Every folder from / to `path` (and `path`) belongs to root and cannot be written by group or others (a sticky folder like /tmp is not accepted)."""
    parts = [p for p in os.path.abspath(path).split("/") if p]
    cur, problems = "/", []
    for part in [""] + parts:
        cur = os.path.join(cur, part) if part else "/"
        st = os.lstat(cur)
        if stat.S_ISLNK(st.st_mode):
            if st.st_uid != 0:
                problems.append(f"{cur} is a link not owned by root")
            continue
        if st.st_uid != 0 or st.st_mode & 0o022:
            problems.append(f"{cur} is {stat.filemode(st.st_mode)} uid {st.st_uid}")
    say("FAIL" if problems else "OK", f"{label}: " + ("; ".join(problems) if problems else "root-owned, not writable by group or others, all the way from /"))


if os.geteuid() != 0:
    sys.exit("run this as root on the lab box")
pw = {}
for role, name in USERS.items():
    try:
        pw[role] = pwd.getpwnam(name)
    except KeyError:
        say("FAIL", f"the {role} user {name!r} does not exist")
if len(pw) == 3:
    ids = {p.pw_uid for p in pw.values()}
    say("OK" if len(ids) == 3 and 0 not in ids else "FAIL", "agent, sandbox and verify are three different non-root users")
    gids = {p.pw_gid for p in pw.values()}
    say("OK" if len(gids) == 3 and 0 not in gids else "FAIL", "each has its own primary group (no shared group)")
    for role in ("sandbox", "verify"):
        groups = sorted(set(os.getgrouplist(pw[role].pw_name, pw[role].pw_gid)) - {pw[role].pw_gid})
        say("OK" if not groups else "FAIL", f"{pw[role].pw_name} is in no other group" + (f" (but in {[grp.getgrgid(g).gr_name for g in groups]})" if groups else ""))
    say("OK" if pw["sandbox"].pw_shell in ("/usr/sbin/nologin", "/sbin/nologin", "/bin/false", "/usr/bin/false") else "WARN",
        f"the sandbox user has no login shell ({pw['sandbox'].pw_shell})")
    for role in ("verify", "agent"):
        home = pw[role].pw_dir
        if os.path.isdir(home):
            mode = stat.S_IMODE(os.stat(home).st_mode)
            level = "FAIL" if role == "verify" else "WARN"
            say("OK" if not mode & 0o077 else level, f"{home} is {oct(mode)}: " + ("private" if not mode & 0o077 else
                f"others can read it (the sandbox could read the {role} identity's credentials): chmod 700 {home}"))
        else:
            say("WARN", f"{role} home {home} does not exist")
for name in ("lab_runner.py", "lab_isolation.py"):
    path = os.path.join(DIR, name)
    if os.path.isfile(path):
        root_only_chain(path, f"{path}")
    else:
        say("FAIL", f"{path} is not installed")
if os.path.exists(TOKEN):
    st = os.stat(TOKEN)
    say("OK" if st.st_uid == 0 and not st.st_mode & 0o077 else "FAIL", f"{TOKEN} is {stat.filemode(st.st_mode)} uid {st.st_uid} (must be root 600)")
    root_only_chain(os.path.dirname(TOKEN), os.path.dirname(TOKEN))
else:
    say("WARN", f"{TOKEN} not found (the runner cannot report without it)")
py = "/usr/bin/python3" if os.path.exists("/usr/bin/python3") else sys.executable
root_only_chain(os.path.realpath(py), f"interpreter {py}")
try:
    paths = json.loads(subprocess.run([py, "-I", "-S", "-c", "import json,site,sys;print(json.dumps(sys.path))"], capture_output=True, text=True, timeout=20).stdout or "[]")
    paths += json.loads(subprocess.run([py, "-I", "-c", "import json,sys;print(json.dumps(sys.path))"], capture_output=True, text=True, timeout=20).stdout or "[]")
except (OSError, ValueError, subprocess.SubprocessError):
    paths = []
for d in sorted({p for p in paths if p and os.path.isdir(p)}):
    root_only_chain(d, f"python library folder {d}")
for d in ("/usr/local/bin", "/usr/bin", "/bin"):                        # the sandbox's PATH
    if os.path.isdir(d):
        root_only_chain(d, f"sandbox PATH folder {d}")
sys.path.insert(0, DIR)
try:
    import lab_isolation as ISO
    agent = pw.get("agent")
    boundary = ISO.resolve(agent)
    if boundary.backend == "identity":
        ISO.run_self_test(boundary)
    info = boundary.describe()
    say("OK" if boundary.active else "FAIL", f"isolation self-test: mode {info['mode']}, backend {info['backend']}, self-test {info['self_test']}")
    for r in info["reasons"]:
        say("WARN", r)
    if boundary.backend == "identity" and boundary.sandbox is not None:
        probe = "import ctypes; print('allowed' if ctypes.CDLL(None, use_errno=True).unshare(0x10000000) == 0 else 'denied')"      # CLONE_NEWUSER
        r = subprocess.run(ISO.launcher_command(boundary.sandbox, [py, "-I", "-c", probe], "/", {"PATH": "/usr/bin:/bin"}), capture_output=True, text=True, timeout=30)
        if r.stdout.strip() == "allowed":
            say("WARN", "the sandbox user can create user namespaces (more kernel surface for a kernel bug; not a hole by itself): "
                        "sysctl kernel.unprivileged_userns_clone=0 (Debian) or kernel.apparmor_restrict_unprivileged_userns=1 (Ubuntu 24.04)")
        else:
            say("OK", "the sandbox user cannot create user namespaces")
except Exception as e:                                                   # nothing may be claimed on an error
    say("FAIL", f"the isolation self-test could not run: {type(e).__name__}: {str(e)[:100]}")
print("RESULT:", "FAIL" if bad else "OK")
sys.exit(1 if bad else 0)
