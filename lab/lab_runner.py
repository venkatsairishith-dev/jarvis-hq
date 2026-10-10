"""JARVIS lab runner: executes, verifies and repairs outcome tasks that JARVIS dispatched to the lab.

Runs as root inside the WSL lab box with a runner token (root-only file) that can only fetch lab jobs and report task
progress; the lab agents never see it. For each job:
  1. work      a lab agent (Hermes, OpenCode or goose) runs as the unprivileged agent user in its own folder,
               with the instructions and the acceptance criteria;
  2. verify    a separate verifier session checks every criterion itself (commands, tests, files) and writes VERIFY.json;
               the worker's own claim is never taken as proof;
  3. repair    if verification fails and attempts remain, a repair attempt gets the verifier's findings;
  4. report    every stage, the evidence and the verdict go to the kernel; the task ends completed or failed, never
               "probably done". Cancelled tasks stop the worker; paused tasks freeze it (SIGSTOP) until resumed.
Custom code, justified in docs/01_RESEARCH_AUDIT.md: Hermes Kanban (closest match: --goal judge, retries) cannot take
work from another host; this runner is the bridge, and it uses the agents and their CLIs as they are.
"""
import json
import os
import pwd
import re
import stat
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.request

ENDPOINT = os.environ.get("JARVIS_ENDPOINT", "http://100.125.91.51:8790")
TOKEN_FILE = "/etc/jarvis-lab/runner.token"            # root 600
AGENT = "agent"
PW = pwd.getpwnam(AGENT)
HOME = PW.pw_dir
WORK = os.path.join(HOME, "work", "jobs")
LOCAL_BIN = os.path.join(HOME, ".local", "bin")
POLL_S = 10


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


def agent_env(kind):
    env = {"HOME": HOME, "USER": AGENT, "LOGNAME": AGENT, "LANG": "C.UTF-8", "TERM": "dumb",
           "PATH": f"{LOCAL_BIN}:/usr/local/bin:/usr/bin:/bin", "XDG_RUNTIME_DIR": f"/run/user/{PW.pw_uid}",
           "DBUS_SESSION_BUS_ADDRESS": f"unix:path=/run/user/{PW.pw_uid}/bus", "DISPLAY": ":1"}
    if kind == "goose":
        try:
            for line in open(os.path.join(HOME, ".jarvis-goose.env")):
                if "=" in line and not line.startswith("#"):
                    k, v = line.rstrip("\n").split("=", 1)
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


def run_agent(job, kind, prompt, folder, timeout, log_name):
    """Run one agent session as the agent user; returns (exit code or None on timeout, seconds, stopped_reason)."""
    log = open(os.path.join(folder, log_name), "w")
    p = subprocess.Popen(["runuser", "-u", AGENT, "--", "env", "-i", *[f"{k}={v}" for k, v in agent_env(kind).items()],
                          *command(kind, prompt, folder)], cwd=folder, stdout=log, stderr=subprocess.STDOUT,
                         stdin=subprocess.DEVNULL, start_new_session=True)
    t0, frozen, reason = time.time(), False, ""
    while p.poll() is None:
        time.sleep(5)
        if time.time() - t0 > timeout:
            reason = "timeout"
            break
        if int(time.time() - t0) % 20 < 5:                      # follow the owner's pause / cancel
            code, r = api("/v1/jarvis/runner/status", {"job": job})
            state = r.get("state") if code == 200 else None     # a network blip or kernel restart never stops the work
            if state in ("cancelled", "failed", "completed"):
                reason = "stopped by the owner" if state == "cancelled" else "task closed"
                break
            if state == "paused" and not frozen:
                os.killpg(p.pid, signal.SIGSTOP); frozen = True
            elif state != "paused" and frozen:
                os.killpg(p.pid, signal.SIGCONT); frozen = False
    if p.poll() is None:
        try:
            os.killpg(p.pid, signal.SIGCONT); os.killpg(p.pid, signal.SIGTERM)
            p.wait(15)
        except (OSError, subprocess.TimeoutExpired):
            os.killpg(p.pid, signal.SIGKILL)
        return None, time.time() - t0, reason
    return p.returncode, time.time() - t0, ""


DELIVER = "deliver"                                   # the workspace folder whose files go to the owner after a passed check
DELIVER_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 _.-]{0,79}\.(md|txt|html|json|csv|log|py)$")   # the kernel's output rule
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
            f"sub-folders). After an independent check passes, the system copies ./{DELIVER}/ to the owner's computer itself; "
            "that is how 'save' or 'deliver to the owner' criteria are met.\n\n"
            "Rules: check your own work by actually running it (tests, commands, opening pages), including the copies in "
            f"./{DELIVER}/. Delivered files must work from any folder on another computer: no absolute paths (find files "
            "relative to the test file itself), and the tests must pass when only the delivered files are copied to an empty "
            "folder; the system checks exactly that. When you are done, write EVIDENCE.md: for each criterion, what you did and the command or check that "
            "shows it holds, with its real output. If something cannot be done, say exactly why in EVIDENCE.md instead of "
            "pretending. Do not write VERIFY.json.")


def verify_prompt(task, criteria):
    crit = "\n".join(f"{i}. {c}" for i, c in enumerate(criteria, 1))
    return (f"You are an independent verifier for task {task}. Another agent worked in the current folder; do not trust its "
            "claims or EVIDENCE.md. Check each acceptance criterion yourself by running the code, the tests and the commands, "
            "and by reading the files. Do not fix anything and do not change any file except VERIFY.json.\n\n"
            f"Acceptance criteria:\n{crit}\n\n"
            f"Delivery: the files in ./{DELIVER}/ are exactly what the owner will receive; check THOSE copies: copy them to a "
            "new empty folder (mktemp -d) and run the tests there, so nothing else in this workspace helps them pass. You cannot see the owner's computer and must not judge it: a criterion about saving or "
            f"delivering files to the owner is met when the right, working files are in ./{DELIVER}/ (the system delivers them "
            "after your check and confirms each one). Never cite files outside this folder as delivered.\n\n"
            'Then write VERIFY.json exactly like {"passed": true or false, "criteria": [{"criterion": "...", "met": true, '
            '"evidence": "what you ran and saw"}], "problems": ["specific problem to fix", ...]}. passed is true only if every '
            "criterion is met.")


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


def portable_check(folder, files):
    """Run the delivered tests from a fresh, empty folder (as the agent user, time-limited), the way the owner will.
    Returns the problems found ([] when there are no tests or they pass)."""
    if not any(n.startswith("test") and n.endswith(".py") for n, _ in files):
        return []
    import tempfile
    d = tempfile.mkdtemp(prefix="portable-")    # the service's private /tmp, root 700 while root writes (no link races)
    try:
        for name, text in files:
            fd = os.open(os.path.join(d, name), os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o644)
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(text)
            os.chown(os.path.join(d, name), PW.pw_uid, PW.pw_gid)
        os.chown(d, PW.pw_uid, PW.pw_gid)        # then hand it to the agent user (tests may write files)
        r = subprocess.run(["runuser", "-u", AGENT, "--", "env", "-i", *[f"{k}={v}" for k, v in agent_env("hermes").items()],
                            "timeout", "180", "python3", "-m", "unittest"], cwd=d, capture_output=True, text=True, stdin=subprocess.DEVNULL)
        if r.returncode != 0:
            tail = (r.stderr or r.stdout).strip().splitlines()[-6:]
            return [f"the delivered tests fail when run from a fresh folder (python3 -m unittest, exit {r.returncode}): " + " | ".join(tail)[:500]]
        return []
    finally:
        subprocess.run(["rm", "-rf", "--one-file-system", d])


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


def handle(job):
    p = job["params"]
    jid, task, kind = job["id"], p["task"], p["agent"]
    folder = os.path.join(WORK, task)
    os.makedirs(folder, exist_ok=True)
    os.chown(folder, PW.pw_uid, PW.pw_gid)
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
        rc, secs, why = run_agent(jid, kind, work_prompt(task, folder, p["instructions"], p["criteria"], attempt, problems),
                                  folder, per_try, f"work-{attempt}.log")
        if why in ("stopped by the owner", "task closed"):
            return finish(jid, False, why, attempt)
        try:
            os.remove(os.path.join(folder, "VERIFY.json"))       # only the verifier writes it
        except OSError:
            pass
        api("/v1/jarvis/runner/report", {"job": jid, "state": "verifying",
                                          "step": f"attempt {attempt}: independent check ({'worker timed out' if rc is None else f'worker exit {rc}'}, {int(secs)} s)"})
        vrc, vsecs, vwhy = run_agent(jid, "hermes", verify_prompt(task, p["criteria"]), folder, 900, f"verify-{attempt}.log")
        if vwhy in ("stopped by the owner", "task closed"):
            return finish(jid, False, vwhy, attempt)
        verdict = read_json(os.path.join(folder, "VERIFY.json")) or {"passed": False, "problems": ["the verifier wrote no VERIFY.json"],
                                                                      "failure_class": "tool"}
        if rc is None:
            verdict.setdefault("problems", []).insert(0, f"the worker ran out of time ({per_try} s)")
            verdict["failure_class"] = "timeout"
        files, bad = deliverables(folder, wants)
        if not bad and verdict.get("passed") is True:
            bad = portable_check(folder, files)
        if bad:                                                  # the runner's own check; the verifier cannot override it
            verdict["passed"] = False
            verdict["problems"] = bad + list(verdict.get("problems") or [])
        history.append({"attempt": attempt, "worker_exit": rc, "worker_s": int(secs), "passed": bool(verdict.get("passed"))})
        evidence = evidence_of(folder, verdict)
        if verdict.get("passed") is True:
            if files:
                api("/v1/jarvis/runner/report", {"job": jid, "step": f"attempt {attempt}: verified; delivering {len(files)} file(s) to the owner"})
                lines, blocker = deliver(jid, files)
                evidence = lines + evidence
                if blocker:
                    api("/v1/jarvis/runner/report", {"job": jid, "state": "failed", "result": f"verified, but NOT delivered: {blocker}"[:600],
                                                      "evidence": evidence, "verification": {**verdict, "history": history},
                                                      "step": "stopped: delivery failed"})
                    return finish(jid, False, "delivery failed: " + blocker, attempt)
            done = f"verified after {attempt} attempt(s)" + (f"; {len(files)} file(s) delivered and confirmed by the laptop" if files else "")
            api("/v1/jarvis/runner/report", {"job": jid, "state": "completed", "result": done,
                                              "evidence": evidence, "verification": {**verdict, "history": history},
                                              "step": "done: every criterion verified" + (", files delivered" if files else "")})
            return finish(jid, True, done, attempt)
        problems = [str(x)[:300] for x in (verdict.get("problems") or ["the verifier found unmet criteria"])][:8]
        if attempt == p["max_attempts"]:
            api("/v1/jarvis/runner/report", {"job": jid, "state": "failed", "result": f"not verified after {attempt} attempt(s): " + "; ".join(problems)[:600],
                                              "evidence": evidence, "verification": {**verdict, "history": history},
                                              "step": "stopped: retry budget used up"})
            return finish(jid, False, "retry budget used up", attempt)
    return finish(jid, False, "no attempt ran", 0)


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


def finish(jid, ok, summary, attempts):
    api("/v1/jarvis/runner/finish", {"job": jid, "result": {"ok": ok, "summary": summary[:300], "attempts": attempts,
                                                             **({} if ok else {"error": summary[:200]})}})
    print(time.strftime("%H:%M:%S"), "job", jid, "ok" if ok else "failed", summary, flush=True)


def main():
    os.makedirs(WORK, exist_ok=True)
    os.chown(WORK, PW.pw_uid, PW.pw_gid)
    print("lab runner started", flush=True)
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
