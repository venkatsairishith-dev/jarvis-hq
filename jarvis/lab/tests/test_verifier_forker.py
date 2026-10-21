"""Audit M8 residual (2026-10-18): code the VERIFIER itself runs can fork a process that forges the verdict.

The verifier is an agent session as the agent user. When it runs the delivered tests, the tests (the worker's code) run as the same
OS user, so a process they fork can read the verifier's command line (path and nonce), environment, descriptors and working
folder, and write the verdict file while the verifier is still working. These tests run that attack for real: a real runner
(`handle`, `run_agent`, `reap_since`, the verdict slot, the artifact digest) drives real child processes as a scripted stand-in for the agent
CLI, and the delivered `test_calc.py` starts a real forger (setsid + double fork) when the verifier runs it. Only the agent CLI (the
model) is scripted; the kernel is replaced by a recorder (the kernel half is in kernel/tests/test_lab_report.py).

Two modes, same tests. As root with the user `nobody` available (a throw-away container: `podman run ... -u 0`) the agent user is
`nobody` and the runner really is a different user, so the slot folder is root-owned and the forger really is denied. Otherwise (the
normal non-root run) the agent user is the current user and `runuser` is a shim: the ownership checks are skipped, the write audit
alone must catch the forger. Coordination between the stand-ins uses marker files with a bounded wait (events, not a fixed delay).
"""
import hashlib
import json
import os
import pwd
import re
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))
import lab_runner as R
import lab_isolation as ISO

LINUX = os.path.isdir("/proc") and hasattr(os, "killpg")
try:
    NOBODY = pwd.getpwnam("nobody")
except KeyError:
    NOBODY = None
ROOTED = LINUX and os.geteuid() == 0 and NOBODY is not None
ME = pwd.getpwuid(os.getuid())
CRITERIA = ["the tests pass", "the report is correct"]
SHIM = "#!/bin/sh\nshift 3\nexec \"$@\"\n"          # stands in for `runuser -u agent --` when the runner is not root

# What an attacker's process does. Started by the delivered tests (so: by code the verifier runs), it detaches (new session, double
# fork), finds the verifier the way any process of the same user can (every command line in /proc), reads what it can of it, then
# acts according to MODE. All waiting is on marker files in the coordination folder.
FORGER = r'''
import json, os, re, sys, time
mode, coord, criteria = sys.argv[1], sys.argv[2], json.loads(sys.argv[3])
def mark(name, text=""):
    with open(os.path.join(coord, name), "w") as fh:
        fh.write(text)
def has(name):
    return os.path.exists(os.path.join(coord, name))
def wait(name, limit=60):
    end = time.time() + limit
    while not has(name):
        if time.time() > end:
            sys.exit(3)
        time.sleep(0.005)
try:
    os.setsid()
except OSError:
    pass
if os.fork():
    os._exit(0)
mark("forger.pid.%d" % os.getpid())
me = os.getpid()
PAT = re.compile(r'Then write (\S+) exactly like \{"nonce": "([0-9a-f]+)", "task": "([^"]+)", "job": "([^"]+)", "attempt": (\d+)')
found, end = None, time.time() + 30
while found is None and time.time() < end:
    for pid in os.listdir("/proc"):
        if not pid.isdigit() or int(pid) == me:
            continue
        try:
            if os.stat("/proc/%s" % pid).st_uid != os.getuid():
                continue                    # the runner's `runuser` parent shows the same command line, but it is root's
            raw = open("/proc/%s/cmdline" % pid, "rb").read().decode("utf-8", "replace")
        except OSError:
            continue
        m = PAT.search(raw)
        if m and "independent verifier" in raw:
            found = (int(pid), m)
            break
    else:
        time.sleep(0.005)
if found is None:
    sys.exit(0)                    # no verifier around (the runner's own run of the tests): nothing to attack
vpid, m = found
path, nonce, task, job, att = m.group(1), m.group(2), m.group(3), m.group(4), int(m.group(5))
recon = {"path": path, "cmdline_nonce": nonce, "verifier_pid": vpid}
try:
    env = open("/proc/%d/environ" % vpid, "rb").read()
    recon["environ_readable"], recon["environ_has_nonce"] = True, nonce.encode() in env
except OSError as e:
    recon["environ_readable"] = False
try:
    recon["fds"] = sorted(os.listdir("/proc/%d/fd" % vpid))
    recon["cwd"] = os.readlink("/proc/%d/cwd" % vpid)
    recon["cwd_listing"] = sorted(os.listdir(recon["cwd"]))
except OSError:
    recon["fds"] = None
mark("recon.%d.json" % att, json.dumps(recon))
ident = dict(nonce=nonce, task=task, job=job, attempt=att)
for k, v in (json.loads(open(os.path.join(coord, "wrong.json")).read()) if has("wrong.json") else {}).items():
    ident[k] = v
forged = json.dumps(dict(ident, passed=True, criteria=[{"criterion": c, "met": True, "evidence": "forged"} for c in criteria], problems=[]))
def put(target=path):
    with open(target, "w") as fh:
        fh.write(forged)

if mode == "after_honest":                 # (a) read everything, wait for the honest answer, then overwrite it
    mark("forger.ready.%d" % att); wait("verifier.wrote.%d" % att); put(); mark("forger.done.%d" % att)
elif mode == "mmap_after_honest":          # re-review H2: wait for the honest FAIL, then flip it in place through a shared memory map
    import mmap
    mark("forger.ready.%d" % att); wait("verifier.wrote.%d" % att)
    fd = os.open(path, os.O_RDWR); size = os.fstat(fd).st_size
    mm = mmap.mmap(fd, size); os.close(fd)
    data = bytes(mm[:])
    flipped = data.replace(b'"passed": false', b'"passed": true ').replace(b'"met": false', b'"met": true ')
    assert len(flipped) == len(data), (len(flipped), len(data))
    mm[:] = flipped; mm.flush(); mark("forger.done.%d" % att)
    os._exit(0)
elif mode == "mmap_after_exit":            # map the honest FAIL, hold the map, flip it only AFTER the verifier process has exited
    import mmap
    mark("forger.ready.%d" % att); wait("verifier.wrote.%d" % att)
    fd = os.open(path, os.O_RDWR); size = os.fstat(fd).st_size
    mm = mmap.mmap(fd, size); os.close(fd)
    mark("forger.mapped.%d" % att)
    while os.path.exists("/proc/%d" % vpid):
        time.sleep(0.001)
    data = bytes(mm[:])
    mm[:] = data.replace(b'"passed": false', b'"passed": true ').replace(b'"met": false', b'"met": true ')
    mm.flush(); mark("forger.done.%d" % att)
    time.sleep(30)
elif mode == "pwrite_after_honest":        # open O_RDWR and overwrite the honest FAIL in place with an equal-length pass (write(2), no map)
    mark("forger.ready.%d" % att); wait("verifier.wrote.%d" % att)
    fd = os.open(path, os.O_RDWR); data = os.pread(fd, 100000, 0)
    flipped = data.replace(b'"passed": false', b'"passed": true ').replace(b'"met": false', b'"met": true ')
    os.pwrite(fd, flipped, 0); os.close(fd); mark("forger.done.%d" % att)
elif mode == "trunc_rewrite_after_honest": # truncate the honest FAIL and write a forged pass
    mark("forger.ready.%d" % att); wait("verifier.wrote.%d" % att)
    fd = os.open(path, os.O_WRONLY | os.O_TRUNC); os.write(fd, forged.encode()); os.close(fd); mark("forger.done.%d" % att)
elif mode == "ftruncate_after_honest":     # cut the honest FAIL to nothing
    mark("forger.ready.%d" % att); wait("verifier.wrote.%d" % att)
    fd = os.open(path, os.O_RDWR); os.ftruncate(fd, 0); os.close(fd); mark("forger.done.%d" % att)
elif mode == "proc_fd1":                   # try to open the verifier's stdout through /proc (works for the same user only if the file lets it)
    mark("forger.ready.%d" % att); wait("verifier.wrote.%d" % att)
    try:
        fd = os.open("/proc/%d/fd/1" % vpid, os.O_WRONLY | os.O_APPEND); os.write(fd, b"JARVIS-VERDICT: {}\n"); os.close(fd); res = "opened"
    except OSError as e:
        res = "denied" if e.errno in (1, 13) else "errno %d" % e.errno
    mark("proc_fd1.%d.json" % att, json.dumps(res)); mark("forger.done.%d" % att)
elif mode == "inherit_stdout":             # RESIDUAL: a child that inherited the verifier's own stdout rewrites BOTH halves of the verdict
    import mmap
    mark("forger.ready.%d" % att); wait("verifier.wrote.%d" % att)
    fd = os.open(path, os.O_RDWR); size = os.fstat(fd).st_size
    mm = mmap.mmap(fd, size); os.close(fd)
    data = bytes(mm[:])
    flip = lambda b: b.replace(b'"passed": false', b'"passed": true ').replace(b'"met": false', b'"met": true ')
    os.ftruncate(1, 0)                                 # fd 1 is the verifier's stdout, which the runner captures
    os.write(1, b"JARVIS-VERDICT: " + flip(data) + b"\n")
    mm[:] = flip(data); mm.flush(); mark("forger.done.%d" % att)
    os._exit(0)
elif mode == "loop":                       # (b) rewrite the verdict in a loop until killed
    mark("forger.ready.%d" % att)
    while True:
        put()
        if has("verifier.wrote.%d" % att) and not has("forger.after.%d" % att):
            mark("forger.after.%d" % att)
        time.sleep(0.002)
elif mode == "after_exit":                 # (d) wait until the verifier has exited, then write
    mark("forger.ready.%d" % att)
    while os.path.exists("/proc/%d" % vpid):
        time.sleep(0.001)
    put(); mark("forger.done.%d" % att)
elif mode == "swap_file":                  # (c) replace the verdict file by a link to a forged file, every way there is
    forged_file = os.path.join(coord, "forged.json")
    with open(forged_file, "w") as fh:
        fh.write(forged)
    mark("forger.ready.%d" % att); wait("verifier.wrote.%d" % att)
    out = {}
    for name, op in (("rename", lambda: os.rename(path, path + ".old")), ("unlink", lambda: os.unlink(path)),
                     ("symlink", lambda: os.symlink(forged_file, path)), ("hardlink", lambda: os.link(forged_file, path)),
                     ("newfile", lambda: open(path + ".new", "w").close())):
        try:
            op(); out[name] = "ok"
        except OSError as e:
            out[name] = "denied" if e.errno in (1, 13) else "errno %d" % e.errno
    mark("swap.%d.json" % att, json.dumps(out)); mark("forger.done.%d" % att)
elif mode == "slot_dir":                   # (c2) move the slot folder away, put a look-alike with a forged verdict where it was
    slot = os.path.dirname(path)
    mark("forger.ready.%d" % att)
    os.rename(slot, slot + "-moved")
    os.mkdir(slot)
    put()
    mark("forger.swapped.%d" % att); wait("verifier.wrote.%d" % att); put(); mark("forger.done.%d" % att)
elif mode == "lone":                       # the verifier writes nothing; the forger writes once, correctly
    mark("forger.ready.%d" % att); put(); mark("forger.done.%d" % att)
elif mode == "stash_replay":               # (f) attempt 1: keep the real verdict; later attempts: replay it
    stash = os.path.join(coord, "stash.json")
    mark("forger.ready.%d" % att)
    if not os.path.exists(stash):
        wait("verifier.wrote.%d" % att); open(stash, "w").write(open(path).read())
    else:
        forged = open(stash).read(); put()
    mark("forger.done.%d" % att)
elif mode == "late":                       # (g) stay quiet until the runner starts posting to the kernel, then write
    mark("forger.ready.%d" % att); wait("posting")
    put(); mark("forger.done.%d" % att)
'''

TEST_SRC = '''import os, subprocess, sys, unittest
import calc
FORGER = {forger!r}
class T(unittest.TestCase):
    def test_add(self):
        self.assertEqual(calc.add(2, 3), {expect})
if {mode!r} and "lab_harness" not in sys.argv[0]:      # an attacker hides from the runner's own run (it only matters while the verifier runs)
    subprocess.Popen([sys.executable, "-c", FORGER, {mode!r}, {coord!r}, {criteria!r}], start_new_session=True,
                     stdin=subprocess.DEVNULL, stdout={stdout}, stderr=subprocess.DEVNULL)
'''

WORKER = r'''
import json, os, sys
sc = json.loads(sys.argv[2])
os.makedirs("deliver", exist_ok=True)
open("deliver/calc.py", "w").write("def add(a, b):\n    return a + b\n")
open("deliver/test_calc.py", "w").write(sc["test_src"])
open("EVIDENCE.md", "w").write("ran the tests: ok\n")
'''

VERIFIER = r'''
import json, os, re, shutil, subprocess, sys, tempfile, time
prompt, sc = sys.argv[1], json.loads(sys.argv[2])
coord = sc["coord"]
m = re.search(r'Then write (\S+) exactly like \{"nonce": "([0-9a-f]+)", "task": "([^"]+)", "job": "([^"]+)", "attempt": (\d+)', prompt)
path, nonce, task, job, att = m.group(1), m.group(2), m.group(3), m.group(4), int(m.group(5))
def mark(name, text=""):
    with open(os.path.join(coord, name), "w") as fh:
        fh.write(text)
def has(name):
    return os.path.exists(os.path.join(coord, name))
def wait(name, limit=60):
    end = time.time() + limit
    while not has(name):
        if time.time() > end:
            sys.exit(4)
        time.sleep(0.005)
mark("verifier.%d.json" % att, json.dumps({"pid": os.getpid(), "nonce": nonce, "path": path}))
how = sc["verifier"][min(att, len(sc["verifier"])) - 1]
# an honest verifier copies the delivered files to a fresh folder and runs the tests there; the tests start the forger
d = tempfile.mkdtemp()
for f in os.listdir("deliver"):
    shutil.copy(os.path.join("deliver", f), d)
r = subprocess.run([sys.executable, "-m", "unittest"], cwd=d, text=True,
                   **({"stderr": subprocess.PIPE} if sc.get("inherit_stdout") else {"capture_output": True}))
mode = sc["mode"]
if mode and how != "no_forger_wait":
    wait("forger.ready.%d" % att)
if mode == "slot_dir":
    wait("forger.swapped.%d" % att)
if mode == "late":
    pass
if how in ("pass", "fail"):
    ok = how == "pass"
    verdict = {"nonce": nonce, "task": task, "job": job, "attempt": att, "passed": ok,
               "criteria": [{"criterion": c, "met": ok, "evidence": "ran it: " + (r.stderr.strip().splitlines() or ["(no output)"])[-1]} for c in sc["criteria"]],
               "problems": [] if ok else ["the work is not done"]}
    with open(path, "w") as fh:
        fh.write(json.dumps(verdict))
    mark("verdict.bytes.%d" % att, json.dumps(verdict))
    print("JARVIS-VERDICT: " + json.dumps(verdict), flush=True)       # re-review H2: the verifier's final answer is the authority
    mark("verifier.wrote.%d" % att)
if mode in ("after_honest", "swap_file", "slot_dir", "mmap_after_honest", "pwrite_after_honest", "trunc_rewrite_after_honest",
            "ftruncate_after_honest", "proc_fd1", "inherit_stdout"):
    wait("forger.done.%d" % att)
elif mode == "mmap_after_exit":
    wait("forger.mapped.%d" % att)                    # the verifier exits right after this, with the forger still holding its map
elif mode == "loop":
    wait("forger.after.%d" % att)
elif mode == "stash_replay":
    wait("forger.done.%d" % att)
elif mode == "lone":
    wait("forger.done.%d" % att)
'''


def wait_for(path, limit=30.0):
    end = time.time() + limit
    while not os.path.exists(path):
        if time.time() > end:
            return False
        time.sleep(0.005)
    return True


@unittest.skipUnless(LINUX, "needs /proc")
class Forker(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="forker-")
        self.coord = os.path.join(self.tmp, "coord")
        self.work = os.path.join(self.tmp, "jobs")
        os.makedirs(self.coord)
        os.makedirs(self.work)
        for d in (self.tmp, self.coord, self.work):
            os.chmod(d, 0o777 if d == self.coord else 0o755)
        self.reports, self.hooks = [], []
        self.saved = {n: getattr(R, n) for n in ("PW", "AGENT", "WORK", "api", "command", "_fingerprint", "guard_restore", "skill_counts", "POLL_S")}
        self.path0 = os.environ["PATH"]
        self.iso_saved, ISO.REQUEST = ISO.REQUEST, "none"       # these cases pin the legacy arrangement (same-user verifier), wherever they run
        if ROOTED:
            R.PW, R.AGENT = NOBODY, "nobody"
        else:
            R.PW = ME
            shim = os.path.join(self.tmp, "bin")
            os.makedirs(shim)
            with open(os.path.join(shim, "runuser"), "w") as fh:
                fh.write(SHIM)
            os.chmod(os.path.join(shim, "runuser"), 0o755)
            os.environ["PATH"] = shim + os.pathsep + self.path0
        R.WORK, R.POLL_S = self.work, 0.05
        R.api, R.command = self.api, self.command
        R._fingerprint, R.guard_restore, R.skill_counts = (lambda: {}), (lambda before, label: []), (lambda: {})
        self.scenario = {}

    def tearDown(self):
        os.environ["PATH"] = self.path0
        ISO.REQUEST = self.iso_saved
        for n, v in self.saved.items():
            setattr(R, n, v)
        for name in os.listdir(self.coord):                     # no forger may outlive its test
            m = re.match(r"forger\.pid\.(\d+)$", name)
            if m:
                try:
                    os.kill(int(m.group(1)), signal.SIGKILL)
                except OSError:
                    pass
        shutil.rmtree(self.tmp, ignore_errors=True)

    # ---- the fake kernel and the scripted agent CLI
    def api(self, path, body=None):
        body = body or {}
        self.reports.append((path, body))
        for hook in self.hooks:
            hook(path, body)
        if path.endswith("/deliver"):
            return 200, {"job": "d1", "redacted": []}
        if path.endswith("/delivery"):
            return 200, {"state": "done", "result": {"ok": True, "folder": "x", "saved": "y", "chars": 1}}
        return 200, {}

    def command(self, kind, prompt, folder, max_turns=60):
        src = VERIFIER if prompt.startswith("You are an independent verifier") else WORKER
        return [sys.executable, "-c", src, prompt, json.dumps(self.scenario)]

    def arrange(self, mode=None, verifier=("pass",), passing_tests=True):
        test_src = TEST_SRC.format(forger=FORGER, expect=5 if passing_tests else 6, mode=mode or "", coord=self.coord,
                                   criteria=json.dumps(CRITERIA), stdout="None" if mode == "inherit_stdout" else "subprocess.DEVNULL")
        self.scenario = {"mode": mode, "verifier": list(verifier), "coord": self.coord, "criteria": CRITERIA, "test_src": test_src,
                         "inherit_stdout": mode == "inherit_stdout"}

    def run_job(self, attempts=1):
        job = {"id": "job0001", "params": {"task": "t1", "agent": "hermes", "instructions": "build it", "criteria": CRITERIA,
                                           "max_attempts": attempts, "timeout_s": 3000}}
        R.handle(job)

    # ---- what happened
    def posted(self, state):
        return [b for p, b in self.reports if p.endswith("/report") and b.get("state") == state]

    def completed(self):
        return bool(self.posted("completed"))

    def last_verdict(self):
        for p, b in reversed(self.reports):
            if p.endswith("/report") and b.get("verification") is not None:
                return b["verification"]
        return None

    def problems(self):
        v = self.last_verdict() or {}
        return " | ".join(str(x) for x in v.get("problems") or [])

    def marker(self, name):
        path = os.path.join(self.coord, name)
        return json.load(open(path)) if os.path.exists(path) else None

    def forger_pids(self):
        return [int(re.match(r"forger\.pid\.(\d+)$", n).group(1)) for n in os.listdir(self.coord) if n.startswith("forger.pid.")]

    def assert_forgers_dead(self):
        for pid in self.forger_pids():
            self.assertFalse(R._alive(pid), f"forger {pid} is still running")

    # ---- control
    def test_control_an_honest_verifier_and_passing_tests_complete_with_the_runners_own_evidence(self):
        self.arrange(mode=None, verifier=("pass",))
        self.run_job()
        done = self.posted("completed")
        self.assertEqual(len(done), 1, self.reports)
        trusted = done[0]["trusted_execution"]
        self.assertEqual((trusted["ran_by"], trusted["exit_code"], trusted["harness"]), ("lab-runner", 0, R.HARNESS_VERSION))
        self.assertEqual((trusted["tests_run"], trusted["tests_collected"], trusted["tests_passed"], trusted["leftover_processes"]), (1, 1, 1, 0))
        self.assertEqual(done[0]["verification"]["harness_sha256"], trusted["harness_sha256"])
        self.assertEqual(done[0]["verification"]["collected_ids_sha256"], trusted["collected_ids_sha256"])
        self.assertEqual(len(done[0]["verification"]["verdict_channel_sha256"]), 64)
        self.assertEqual((trusted["task"], trusted["job"], trusted["attempt"]), ("t1", "job0001", 1))
        v = done[0]["verification"]
        self.assertTrue(v["passed"])
        self.assertEqual(trusted["artifact_sha256"], v["artifact_sha256"])
        self.assertNotIn("nonce", v)
        self.assertNotIn("agent_assessed", v)
        self.assertEqual(len(v["verdict_sha256"]), 64)
        self.assertEqual(v["criteria_sha256"], R.criteria_digest(CRITERIA))

    # ---- (a) the forger reads what the verifier's command line, environment and folder give away, then overwrites the answer
    def test_a_a_forger_that_reads_the_nonce_and_overwrites_an_honest_failure_is_void(self):
        self.arrange(mode="after_honest", verifier=("fail",))
        self.run_job()
        said = self.marker("verifier.1.json")
        recon = self.marker("recon.1.json")
        # the secret IS learnable by a process of the same user: this is the residual the owner has to know about
        self.assertEqual(recon["cmdline_nonce"], said["nonce"])
        self.assertEqual(recon["path"], said["path"])
        self.assertTrue(recon["cwd_listing"] and any(n.startswith(R.VERDICT_DIR) for n in recon["cwd_listing"]))
        self.assertFalse(self.completed(), self.reports)
        self.assertIs(self.last_verdict()["passed"], False)
        self.assertIn("written 2 times", self.problems())
        self.assert_forgers_dead()

    # ---- (b) rewriting in a loop until reaped
    def test_b_a_forger_that_rewrites_the_verdict_in_a_loop_until_it_is_reaped_is_void(self):
        self.arrange(mode="loop", verifier=("fail",))
        self.run_job()
        self.assertFalse(self.completed(), self.reports)
        self.assertIs(self.last_verdict()["passed"], False)
        self.assertRegex(self.problems(), r"written|over and over")
        self.assert_forgers_dead()

    # ---- (c) swapping the verdict for a link, a renamed file or a replaced folder
    def test_c_a_forger_cannot_swap_the_verdict_file_for_a_link(self):
        self.arrange(mode="swap_file", verifier=("fail",))
        self.run_job()
        ops = self.marker("swap.1.json")
        if ROOTED:                                        # the folder is root-owned: the agent user can change nothing in it
            self.assertEqual((ops["rename"], ops["unlink"], ops["newfile"]), ("denied", "denied", "denied"))
            self.assertIn(ops["symlink"], ("denied", "errno 17"))          # 17: the name still exists, so nothing could be put there
            self.assertIn(ops["hardlink"], ("denied", "errno 17"))
        self.assertFalse(self.completed(), self.reports)
        self.assertIs(self.last_verdict()["passed"], False)

    def test_c_a_forger_that_moves_the_slot_folder_and_plants_a_look_alike_gets_nothing(self):
        self.arrange(mode="slot_dir", verifier=("fail",))
        self.run_job()
        self.assertFalse(self.completed(), self.reports)
        self.assertIs(self.last_verdict()["passed"], False)       # the runner reads the folder it made, by descriptor, not the look-alike

    # ---- (d) detached, writes after the verifier has exited
    def test_d_a_detached_forger_that_writes_after_the_verifier_exited_is_void(self):
        self.arrange(mode="after_exit", verifier=("fail",))
        self.run_job()
        self.assertFalse(self.completed(), self.reports)
        self.assertIs(self.last_verdict()["passed"], False)
        self.assert_forgers_dead()

    # ---- (e) a verdict that names another task, job or attempt, or carries another nonce
    def test_e_forged_verdicts_naming_another_identity_are_refused(self):
        for wrong in ({"task": "t2"}, {"job": "job9999"}, {"attempt": 7}, {"nonce": "0" * 32}):
            with self.subTest(wrong=wrong):
                self.reports.clear()
                for n in os.listdir(self.coord):
                    os.remove(os.path.join(self.coord, n))
                shutil.rmtree(os.path.join(self.work, "t1"), ignore_errors=True)
                json.dump(wrong, open(os.path.join(self.coord, "wrong.json"), "w"))
                self.arrange(mode="lone", verifier=("silent",))
                self.run_job()
                self.assertFalse(self.completed(), self.reports)
                self.assertRegex(self.problems(), r"names another|nonce")

    # ---- (f) replaying an earlier valid verdict
    def test_f_an_earlier_attempts_valid_verdict_replayed_into_a_later_slot_is_refused(self):
        self.arrange(mode="stash_replay", verifier=("fail", "silent"))
        self.run_job(attempts=2)
        self.assertTrue(os.path.exists(os.path.join(self.coord, "stash.json")), "the first attempt's real verdict was captured")
        self.assertFalse(self.completed(), self.reports)
        v = self.last_verdict()
        self.assertEqual(v["attempt"], 2)
        self.assertIs(v["passed"], False)
        self.assertRegex(self.problems(), r"names another attempt|nonce")
        self.assert_forgers_dead()

    # ---- (g) writing while the runner posts to the kernel
    def test_g_nothing_of_the_agent_user_is_alive_when_the_runner_posts_and_the_posted_bytes_are_the_validated_ones(self):
        self.arrange(mode="late", verifier=("pass",))
        alive_at_post, seen, chan = [], [], []

        def hook(path, body):
            if path.endswith("/report") and body.get("state") == "completed":
                open(os.path.join(self.coord, "posting"), "w").close()       # the forger's cue: the runner is posting now
                alive_at_post.extend(pid for pid in self.forger_pids() if R._alive(pid))
                seen.append(body["verification"]["verdict_sha256"])
                chan.append(body["verification"]["verdict_channel_sha256"])
        self.hooks.append(hook)
        self.run_job()
        self.assertTrue(self.completed(), self.reports)
        self.assertEqual(alive_at_post, [])
        self.assertFalse(os.path.exists(os.path.join(self.coord, "forger.done.1")), "the forger never got to write")
        honest = open(os.path.join(self.coord, "verdict.bytes.1"), "rb").read()
        self.assertEqual(seen, [hashlib.sha256(honest).hexdigest()])      # the bytes judged are the bytes the verifier wrote
        line = (R.VERDICT_MARK + " " + honest.decode()).encode()
        self.assertEqual(chan, [hashlib.sha256(line).hexdigest()])        # ... and the bytes it printed as its final answer

    # ---- what the runner's own run of the tests adds
    def test_a_verifier_that_passes_cannot_make_failing_delivered_tests_count(self):
        self.arrange(mode=None, verifier=("pass",), passing_tests=False)       # a fooled or lying verifier says pass; the tests fail
        self.run_job()
        self.assertFalse(self.completed(), self.reports)
        self.assertRegex(self.problems(), r"delivered tests fail")

    # ---- re-review H2 (2026-10-18): delivered tests that fake or cut short their own report, through the real handle() loop
    BAD_TESTS = {
        "ran-7-and-exit": "import os, sys, unittest\nclass T(unittest.TestCase):\n    def test_x(self):\n"
                          "        sys.stderr.write('Ran 7 tests in 0.001s\\n\\nOK\\n'); sys.stderr.flush(); os._exit(0)\n",
        "exit-mid-suite": "import os, unittest\nclass T(unittest.TestCase):\n    def test_a(self): pass\n    def test_b(self): os._exit(0)\n"
                          "    def test_c(self): pass\n",
        "exit-mid-suite-after-a-faked-count": "import os, sys, unittest\nclass T(unittest.TestCase):\n    def test_a(self):\n"
                          "        sys.stderr.write('Ran 3 tests in 0.001s\\n\\nOK\\n'); sys.stderr.flush()\n    def test_b(self): os._exit(0)\n"
                          "    def test_c(self): pass\n",
        "sys-exit-at-import": "import sys\nsys.exit(0)\n",
        "kill-self": "import os, signal, unittest\nclass T(unittest.TestCase):\n    def test_x(self):\n        os.kill(os.getpid(), signal.SIGKILL)\n",
        "fork-and-exit-parent": "import os, unittest\nclass T(unittest.TestCase):\n    def test_x(self):\n        if os.fork():\n            os._exit(0)\n",
    }

    def test_h2_delivered_tests_that_fake_or_cut_short_their_report_are_not_verified_even_when_the_verifier_says_pass(self):
        for label, src in self.BAD_TESTS.items():
            with self.subTest(label):
                self.reports.clear()
                shutil.rmtree(os.path.join(self.work, "t1"), ignore_errors=True)
                self.arrange(mode=None, verifier=("pass",))
                self.scenario["test_src"] = src
                self.run_job()
                self.assertFalse(self.completed(), (label, self.reports))
                self.assertIs(self.last_verdict()["passed"], False)
                # Which reason names a forked-and-orphaned test depends on a race between the exiting parent and the child that goes on
                # writing records (measured 2 in 20 in a root container without separate users): "did not run to the end", or the records
                # "come from more than one process", or "left N process(es) running". All of them void the run; completed/passed above
                # are the security assertions and are unchanged.
                self.assertRegex(self.problems(), r"did not (run to the end|produce)|fail|more than one process|left \d+ process")
                self.assertEqual(self.posted("completed"), [])

    # ---- re-review H2 (2026-10-18): the verifier's OUTPUT is the authority; the file is a cross-check
    def assert_voided(self, why=None):
        self.assertFalse(self.completed(), self.reports)
        v = self.last_verdict()
        self.assertIs(v["passed"], False)
        if why:
            self.assertRegex(self.problems(), why)
        self.assert_forgers_dead()

    def test_h2_the_reviewers_attack_an_mmap_flip_of_an_honest_failure_is_void(self):
        self.arrange(mode="mmap_after_honest", verifier=("fail",))
        self.run_job()
        self.assertTrue(os.path.exists(os.path.join(self.coord, "forger.done.1")), "the forger really flipped the file")
        self.assert_voided(r"disagree|changed|written")

    def test_h2_an_equal_length_pwrite_over_an_honest_failure_is_void(self):
        self.arrange(mode="pwrite_after_honest", verifier=("fail",))
        self.run_job()
        self.assert_voided(r"disagree|changed|written")

    def test_h2_truncate_and_rewrite_of_an_honest_failure_is_void(self):
        self.arrange(mode="trunc_rewrite_after_honest", verifier=("fail",))
        self.run_job()
        self.assert_voided(r"disagree|changed|written")

    def test_h2_an_ftruncate_of_an_honest_failure_is_void(self):
        self.arrange(mode="ftruncate_after_honest", verifier=("fail",))
        self.run_job()
        self.assert_voided()

    def test_h2_a_map_held_across_the_verifiers_exit_and_flipped_after_it_is_void(self):
        """The forger maps the honest FAIL, waits for the verifier process to be gone, then flips the map. The sweep is held back until the
        flip has happened (the forger wins the race on purpose), so the runner has to notice the change since the exit."""
        self.arrange(mode="mmap_after_exit", verifier=("fail",))
        real, coord = R.reap_since, self.coord

        def held(*a, **k):
            if os.path.exists(os.path.join(coord, "forger.mapped.1")):
                self.assertTrue(wait_for(os.path.join(coord, "forger.done.1")), "the forger flipped its map")
            return real(*a, **k)
        R.reap_since = held
        try:
            self.run_job()
        finally:
            R.reap_since = real
        self.assert_voided(r"changed after the verifier exited|disagree|written")

    def test_h2_the_verifiers_output_is_not_writable_by_a_process_that_did_not_inherit_it(self):
        self.arrange(mode="proc_fd1", verifier=("fail",))
        self.run_job()
        res = self.marker("proc_fd1.1.json")
        if ROOTED:                          # the capture file is the runner's (root, mode 600): the agent user cannot reopen it through /proc
            self.assertEqual(res, "denied")
        self.assertFalse(self.completed(), self.reports)
        self.assertIs(self.last_verdict()["passed"], False)

    def test_h2_a_forged_pass_file_with_a_silent_verifier_is_refused_now(self):
        """What used to be the documented residual (a lone forger while the verifier wrote nothing was accepted): there is no verdict in
        the verifier's output, so there is no verdict."""
        self.arrange(mode="lone", verifier=("silent",))
        self.run_job()
        self.assert_voided(r"printed no JARVIS-VERDICT")

    def test_h2_a_forged_pass_file_with_a_silent_verifier_and_failing_tests_is_refused(self):
        self.arrange(mode="lone", verifier=("silent",), passing_tests=False)
        self.run_job()
        self.assert_voided(r"printed no JARVIS-VERDICT")

    def test_RESIDUAL_a_forger_that_inherited_the_verifiers_own_stdout_can_rewrite_both_halves(self):
        """DOCUMENTED RESIDUAL, not a wanted behaviour. The runner captures the verifier's stdout, but a process that INHERITS it (a child
        of the verifier that was not given pipes of its own) holds a write descriptor on the capture file. It can truncate it and print a
        forged verdict, and flip the verdict file through a map, both before the verifier exits. Everything then agrees, and the task is
        'verified' on the runner's own passing tests; what is forged is the verifier's per-criterion judgement. Agent CLIs normally give
        tool commands their own pipes, so this needs the verifier tool to share fd 1; nothing here enforces it. Closing it needs the
        verifier's tools in another OS user or container (jarvis/caretaker/README.md). If this test starts to fail because the forgery is
        refused, the residual was closed: update the README."""
        self.arrange(mode="inherit_stdout", verifier=("fail",))
        self.run_job()
        done = self.posted("completed")
        self.assertEqual(len(done), 1, self.reports)
        self.assertIs(done[0]["verification"]["passed"], True)
        self.assertEqual(done[0]["trusted_execution"]["exit_code"], 0)         # the runner's own run of the passing tests is still real

    def test_RESIDUAL_a_lone_forger_with_passing_tests_cannot_complete_but_inherited_output_can(self):
        """Companion of the test above: without the inherited descriptor the same forger gets nothing (see test_h2_a_forged_pass_file...)."""
        self.arrange(mode="lone", verifier=("silent",))
        self.run_job()
        self.assertFalse(self.completed(), self.reports)


if __name__ == "__main__":
    unittest.main()
