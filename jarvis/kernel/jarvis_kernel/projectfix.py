"""Fix a failing build in an allowlisted project, verified by the kernel (2026-10-18).

"Inspect my project, identify why the build is failing, fix the root cause, run the relevant tests, and report what was
verified." Not a new orchestrator: one fixed procedure over the kernel's existing pieces, like the desktop troubleshooter.

  1. task      an outcome task with explicit criteria (tasks.py), owned by "projectfix"
  2. inspect   project_check on the laptop node (allowlisted projects only): the real failing output is the evidence
  3. fix       a lab sub-task through dispatch_to_lab: the owner approves sharing exactly that text with the public lab
               (private -> public). The lab works on a CLONE of the project (the caretaker pattern: the project's
               lab_clone URL, the exact commit) and delivers deliver/projectfix.json {base, patch, files, root_cause}
               through the runner's normal delivery. The owner's tree is never touched by the lab.
  4. apply     project_apply on the node, only with the owner's approval of exactly that patch (digest-bound, used
               once): first proven in a scratch clone (tests pass with it; every changed file is needed), then applied
               with a backup of each file and an undo (repair restore <backup>)
  5. verify    the kernel re-runs project_check itself. Completed only if the tests exit 0, the root cause is named,
               the working tree changed in exactly the patch's files, and backups exist. Never on the lab's word.
  6. report    what was verified, with job ids, the test output tail, the diff summary and what is left to do

Every wait (the laptop, the owner's approvals, the lab) is a state; advance() moves on as far as it can and says what it
waits for. Whatever cannot be done here ends the task FAILED with the missing piece named: a project that is not
allowlisted, no lab_clone, the lab never picking the job up, a fix that does not make the tests pass, a fix touching
files it did not need.
"""
import hashlib
import json
import re
import threading
import time

AGENT = "projectfix"
NODE = "laptop"
PATCH_MAX = 100_000
MAX_FILES = 5
FIX_FILE = "projectfix.json"
# a project-relative path: no absolute paths, no "..", nothing inside .git, no spaces (the patch headers are split on them)
PROJECT_FILE = re.compile(r"^(?!/)(?!.*(?:^|/)\.\.?(?:/|$))(?!\.git(?:/|$))[A-Za-z0-9_][A-Za-z0-9_./-]{0,199}$")
TEST_FILE = re.compile(r"(?i)(^|/)(tests?/|test_[^/]*$|[^/]*_test\.py$|conftest\.py$)")
OWNER_PATHS = re.compile(r"(?i)(?<![a-z0-9])[a-z]:[\\/][^\s\"'<>|]*|/mnt/[a-z]/[^\s\"'<>|]*|\\\\[a-z0-9][^\s\"'<>|]*")
CRITERIA = [
    "the project's test command exits 0 when the kernel re-runs project_check after the fix",
    "the root cause is named: the file and the defect",
    "only the files needed changed: each changed file is needed for the tests to pass, no test file changed, and the "
    "working tree changed in exactly those files",
    "nothing was changed without the owner's approval, and every changed file has a backup and an undo",
]


class ProjectFixError(Exception):
    pass


def patch_files(patch: str):
    """The files a unified git diff changes, and '' -- or [] and why it is refused. Only changes to existing text files:
    no new, deleted, renamed, copied, binary or mode-changed files, nothing outside the file sections. The node checks
    the same before it applies anything."""
    if not isinstance(patch, str) or not patch.strip() or len(patch) > PATCH_MAX:
        return [], "the patch is empty or too large"
    files = []
    for block in re.split(r"(?m)^(?=diff --git )", patch):
        if not block.startswith("diff --git "):
            if block.strip():
                return [], "the patch has text outside its file sections"
            continue
        m = re.fullmatch(r"diff --git a/(\S+) b/(\S+)", block.splitlines()[0])
        if not m or m.group(1) != m.group(2):
            return [], "a file header that is not a plain change of one file"
        f = m.group(1)
        if re.search(r"(?m)^(new file mode|deleted file mode|rename from|rename to|copy from|copy to|old mode|new mode|"
                     r"similarity index|GIT binary patch|Binary files)", block):
            return [], f"{f}: only changes to existing text files are applied (no new, deleted, renamed, binary or mode changes)"
        if not re.search(rf"(?m)^--- a/{re.escape(f)}$", block) or not re.search(rf"(?m)^\+\+\+ b/{re.escape(f)}$", block):
            return [], f"{f}: the file section has no --- a/ and +++ b/ lines"
        if not PROJECT_FILE.match(f):
            return [], f"{f[:80]}: not a plain path inside the project"
        if f in files:
            return [], f"{f}: changed twice in one patch"
        files.append(f)
    if not files:
        return [], "the patch changes no file"
    return files, ""


def diff_stat(patch: str) -> dict:
    lines = patch.splitlines()
    return {"added": sum(1 for ln in lines if ln.startswith("+") and not ln.startswith("+++")),
            "removed": sum(1 for ln in lines if ln.startswith("-") and not ln.startswith("---"))}


def _no_owner_paths(text: str) -> str:
    """The lab cannot see the owner's computer (dispatch_to_lab refuses such paths): failing output names files by their
    place in the project only."""
    return OWNER_PATHS.sub("<path on the laptop>", text)


class ProjectFix:
    def __init__(self, kernel, tasks, jobs, dispatch, node: str = NODE, clock=time.time):
        """dispatch: llm_proxy's dispatch_to_lab (the one guarded path to the lab, with the owner's sharing approval)."""
        self.k, self.tasks, self.jobs, self.dispatch, self.node, self.clock = kernel, tasks, jobs, dispatch, node, clock
        self._lock = threading.Lock()              # one advance at a time: a sweeper and the owner never both create jobs
        kernel.db.execute("CREATE TABLE IF NOT EXISTS projectfix(task TEXT PRIMARY KEY, project TEXT, stage TEXT, data TEXT, "
                          "created REAL, updated REAL)")

    # ---------------------------------------------------------------- records
    def _row(self, tid):
        r = self.k.db.execute("SELECT project, stage, data FROM projectfix WHERE task=?", (str(tid),)).fetchone()
        return (r[0], r[1], json.loads(r[2] or "{}")) if r else None

    def _put(self, tid, stage, data):
        self.k.db.execute("UPDATE projectfix SET stage=?, data=?, updated=? WHERE task=?", (stage, json.dumps(data), self.clock(), tid))

    def _job(self, jid):
        r = self.k.db.execute("SELECT state, result, params, owner FROM jobs WHERE id=?", (str(jid or ""),)).fetchone()
        if not r:
            return None
        return {"id": jid, "state": r[0], "result": json.loads(r[1]) if r[1] else None, "params": json.loads(r[2] or "{}"), "owner": r[3]}

    def _task_state(self, tid, state, **kw):
        t = self.tasks.get(tid)
        if t and t["state"] != state and t["state"] not in ("completed", "failed", "cancelled"):
            self.tasks.update(AGENT, tid, state=state, **kw)
        elif t and kw and t["state"] not in ("completed", "failed", "cancelled"):
            self.tasks.update(AGENT, tid, **kw)

    def _fail(self, tid, data, why, stage="failed"):
        data["outcome"] = why
        self._put(tid, stage, data)
        t = self.tasks.get(tid)
        if t and t["state"] not in ("completed", "failed", "cancelled"):
            self.tasks.update(None, tid, state="failed", result=self._text(tid, data, why)[:6000], note=why[:300],
                              evidence=self._evidence(data))
        lab = self.tasks.get(data["lab_task"]) if data.get("lab_task") else None
        if lab and lab["state"] not in ("completed", "failed", "cancelled"):      # nobody waits for the lab any more
            try:
                self.tasks.update(None, lab["id"], state="cancelled", note=f"the outcome task {tid} ended: {why[:200]}")
            except Exception:                      # it changed meanwhile: its own final state stands
                pass
        return self.report(tid)

    def _wait(self, tid, data, stage, why, task_state=None):
        data["waiting"] = why
        self._put(tid, stage, data)
        if task_state:
            self._task_state(tid, task_state, step=why[:300])
        return dict(self.report(tid), waiting=why)

    @staticmethod
    def _evidence(data):
        ev = [f"job:{data[k]}" for k in ("inspect_job", "apply_job", "verify_job") if data.get(k)]
        if data.get("lab_task"):
            ev.append(f"lab task {data['lab_task']}" + (f" (lab job {data['lab_job']})" if data.get("lab_job") else ""))
        return ev

    # ---------------------------------------------------------------- start
    def start(self, project: str, request: str = "") -> dict:
        from .jobs import NAME
        if not NAME.match(str(project or "")):
            raise ProjectFixError("a project is named by its allowlist name (letters, digits, . _ -)")
        tid = self.tasks.create(AGENT, "private", request or f"fix the failing build of {project}",
                                f"Find why the tests of project {project} fail, fix the root cause, re-run the tests and report what was verified",
                                plan=["inspect: project_check on the laptop", "fix: the lab works on a clone (owner approves sharing)",
                                      "apply: the owner approves the exact patch; backup and undo", "verify: the kernel re-runs project_check",
                                      "report"], criteria=CRITERIA, source="projectfix", max_attempts=1, budget_s=6 * 3600)
        try:
            jid = self.jobs.create(self.node, "project_check", {"project": project}, owner=AGENT)
        except Exception as e:                     # kill switch or policy: the task says so instead of sitting in planning
            self.tasks.update(None, tid, state="failed", result=f"the project check could not be queued ({type(e).__name__}: "
                                                                f"{str(e)[:160]}). Nothing was run or changed", note="not started")
            raise
        self.tasks.link_job(tid, jid)
        now = self.clock()
        self.k.db.execute("INSERT INTO projectfix VALUES (?,?,?,?,?,?)", (tid, project, "inspect", json.dumps({"inspect_job": jid}), now, now))
        self.tasks.update(AGENT, tid, state="running", step=f"inspecting: the project's tests run on the laptop (job {jid})")
        self.k.audit.append("projectfix_started", {"task": tid, "project": project, "job": jid})
        return self.advance(tid)

    # ---------------------------------------------------------------- advance
    def advance(self, tid: str) -> dict:
        """Move the task on as far as it can go now; returns the report, with `waiting` when it waits for something."""
        with self._lock:
            return self._advance(tid)

    def _advance(self, tid: str) -> dict:
        from .store import Killed
        row = self._row(tid)
        if not row:
            raise ProjectFixError("no such project fix")
        project, stage, data = row
        t = self.tasks.get(tid)
        if not t or t["state"] in ("completed", "failed", "cancelled"):
            if stage not in ("done", "failed"):        # ended outside the procedure (the owner cancelled it, its deadline passed):
                state = t["state"] if t else "gone"    # the record follows, so the owner's tick stops advancing it
                data["outcome"] = data.get("outcome") or f"the task ended outside the procedure ({state})"
                self._put(tid, "done" if state == "completed" else "failed", data)
                lab = self.tasks.get(data["lab_task"]) if data.get("lab_task") else None
                if lab and lab["state"] not in ("completed", "failed", "cancelled"):      # nobody waits for the lab any more
                    try:
                        self.tasks.update(None, lab["id"], state="cancelled", note=f"the outcome task {tid} ended ({state})")
                    except Exception:
                        pass
            return self.report(tid)
        if t["state"] == "paused":                     # the owner paused it: nothing moves until it is resumed
            return dict(self.report(tid), waiting="paused by the owner: resume the task to go on")
        try:
            for _ in range(8):                         # each stage either moves on (next loop) or returns
                step = getattr(self, "_" + stage)
                out = step(tid, project, data)
                if out is not None:
                    return out
                project, stage, data = self._row(tid)
        except Killed:
            return dict(self.report(tid), waiting="the kill switch is on: nothing runs until the owner lifts it at the kernel console")
        return self.report(tid)

    def _inspect(self, tid, project, data):
        j = self._job(data["inspect_job"])
        if j["state"] in ("queued", "running"):
            return self._wait(tid, data, "inspect", f"waiting for the laptop to run the project's tests (job {j['id']}, {j['state']})")
        r = j["result"] or {}
        if j["state"] == "expired":
            return self._fail(tid, data, f"the laptop never picked up the check (job {j['id']} expired): is the JARVIS node running? "
                                         "Nothing was run or changed")
        if j["state"] != "done" or not r.get("ok"):
            err = str(r.get("error") or j["state"])
            if "allowlist" in err:
                return self._fail(tid, data, f"refused: project {project} is not in the laptop's allowlist (config/projects.json). "
                                             "Nothing was run or changed")
            return self._fail(tid, data, f"the project check could not run: {err[:200]}. Nothing was changed")
        g, tests = r.get("git") or {}, r.get("tests") or {}
        data["before"] = {"head": g.get("head"), "branch": g.get("branch"), "changed": g.get("changed") or [],
                          "dirty_files": g.get("dirty_files"), "tests": tests, "lab_clone": r.get("lab_clone") or ""}
        if not tests.get("ran"):
            return self._fail(tid, data, f"the tests could not run ({tests.get('why') or 'no test command'}), so no fix could be "
                                         "verified. Nothing was changed")
        if tests.get("exit_code") == 0:
            data["outcome"] = "nothing to fix: the tests already pass"
            self._put(tid, "done", data)
            self.tasks.update(None, tid, state="completed", result=self._text(tid, data, "")[:6000], evidence=self._evidence(data),
                              verification={"verifier": "kernel", "method": "project_check on the laptop", "passed": True,
                                            "note": "the tests already pass; nothing was changed", "evidence": [f"job:{j['id']}"]})
            return self.report(tid)
        if not re.fullmatch(r"[0-9a-f]{40}", str(g.get("head") or "")):
            return self._fail(tid, data, "the project's last commit could not be read, so the lab could not work on the same "
                                         "code. Nothing was changed")
        if not data["before"]["lab_clone"]:
            return self._fail(tid, data, "the lab cannot see this project: it has no lab_clone in the laptop's config/projects.json "
                                         f"(a git URL the lab's login can read that holds commit {g['head'][:12]}). The failing "
                                         "tests are reported above; nothing was shared or changed")
        self._put(tid, "share", data)
        return None

    def _instructions(self, project, data) -> tuple[str, list]:
        b = data["before"]
        t = b["tests"]
        tail = "\n".join(_no_owner_paths(str(x)) for x in (t.get("failed") or []) + (t.get("tail") or []))[:2500]
        crit = [f"deliver/{FIX_FILE} holds base (the commit), patch (a unified git diff), files and root_cause",
                f"the patch applies with git apply to commit {b['head']} of a fresh clone",
                "with the patch, the project's test command exits 0 in a fresh clone",
                "the patch changes only existing source files needed for the fix: no test file, no new, deleted or renamed file",
                "root_cause names each changed file and the defect in it"]
        instr = (f"Project {project}: its tests fail. Find the root cause and fix it in the source, not in the tests.\n"
                 f"clone: {_no_owner_paths(b['lab_clone'])}\ncommit: {b['head']}\n"
                 f"test command: {t.get('cmd') or 'the project test command'}\n"
                 f"failing output (last lines): \n{tail}\n\n"
                 "Work in a clone at exactly that commit. Change as little as possible: only the files the fix needs, never a "
                 "test file, no new or deleted files. Run the tests in the clone. Then write "
                 f"deliver/{FIX_FILE} as JSON: {{\"base\": \"<the commit>\", \"patch\": \"<git diff output>\", "
                 "\"files\": [\"<each changed file>\"], \"root_cause\": \"<file: what was wrong>\"}. Deliver nothing else.")
        return instr[:8000], crit

    def _share(self, tid, project, data):
        if not data.get("lab_task"):
            instr, crit = self._instructions(project, data)
            data["lab_task"] = self.tasks.create(AGENT, "private", f"fix the failing tests of {project}",
                                                 f"Fix the failing tests of project {project} at commit {data['before']['head'][:12]} "
                                                 "and deliver the fix as a patch", criteria=crit, source="projectfix",
                                                 max_attempts=2, budget_s=2 * 3600, parent=tid)
            data["lab_text"] = {"instructions": instr, "criteria": crit}
            self._put(tid, "share", data)
        lab = data["lab_task"]
        aid = data.get("share_approval")
        if aid:
            code, r = self.dispatch(AGENT, "private", lab, {"public_ok": True}, approval_id=aid)
        else:
            code, r = self.dispatch(AGENT, "private", lab, dict(data["lab_text"], public_ok=True, agent="hermes"))
        reason = str(r.get("reason") or "")
        if code == 202:
            data["share_approval"] = r["approval_id"]
            return self._wait(tid, data, "share", f"waiting for your approval {r['approval_id']} in the JARVIS console (PIN) to share "
                              f"the failing output and the clone address of {project} with the public lab "
                              f"({r.get('shares', {}).get('instruction_chars', '?')} characters). Nothing was shared yet",
                              task_state="waiting_approval")
        if code == 200:
            data["lab_job"] = r["job"]
            self._task_state(tid, "running", step=f"the lab is working on a clone (lab task {lab}, job {r['job']})")
            self._put(tid, "lab", data)
            return None
        if "approval pending" in reason:
            return self._wait(tid, data, "share", f"waiting for your approval {aid} in the JARVIS console (PIN) to share the "
                              "project's failing output with the public lab. Nothing was shared yet", task_state="waiting_approval")
        if "approval denied" in reason:
            return self._fail(tid, data, f"you declined sharing {project} with the public lab (approval {aid}); nothing was shared "
                                         "or changed")
        if aid and re.search(r"approval (expired|required|used)", reason):
            data.pop("share_approval", None)           # ask again for the same text
            self._put(tid, "share", data)
            return None
        return self._fail(tid, data, f"the lab hand-off was refused: {reason[:240]}. Nothing was shared or changed")

    def _lab(self, tid, project, data):
        j = self._job(data["lab_job"])
        lt = self.tasks.get(data["lab_task"]) or {}
        if lt.get("state") in ("failed", "cancelled") or (j and j["state"] in ("expired", "failed", "cancelled") and lt.get("state") != "completed"):
            if j and j["state"] == "expired" or "never picked it up" in str(lt.get("result")):
                return self._fail(tid, data, f"the lab is unavailable: it never picked up lab job {data['lab_job']} (the WSL lab box on "
                                             "the laptop is off or cannot reach the kernel). Nothing in your project was changed")
            return self._fail(tid, data, f"the lab did not produce a verified fix: {str(lt.get('result') or (j or {}).get('result'))[:300]}. "
                                         "Nothing in your project was changed")
        if lt.get("state") != "completed":
            since = time.strftime("%H:%M", time.localtime(self.k.db.execute("SELECT created FROM jobs WHERE id=?", (data["lab_job"],)).fetchone()[0]))
            what = "is working on it" if (j or {}).get("state") == "running" else f"has not picked it up yet (queued since {since}; " \
                "if the lab box is off, this ends when the job expires after 30 minutes)"
            return self._wait(tid, data, "lab", f"the lab {what}: lab task {data['lab_task']}, job {data['lab_job']}")
        fix, why = self._delivered_fix(data)
        if why:
            return self._fail(tid, data, f"the lab's fix is refused: {why}. Nothing in your project was changed")
        data["fix"] = fix
        self._put(tid, "approve", data)
        return None

    def _delivered_fix(self, data):
        """The lab's fix as delivered (the runner's own output job for this lab task, after its independent check), and the
        kernel's static checks before the owner is asked: '' when it may be offered for approval."""
        lab = data["lab_task"]
        row = self.k.db.execute("SELECT params, state FROM jobs WHERE kind='output_save' AND owner=? AND state='done' "
                                "AND json_extract(params,'$.name')=? ORDER BY created DESC LIMIT 1", (f"runner-{lab}", FIX_FILE)).fetchone()
        if not row:
            return None, f"the lab delivered no {FIX_FILE}"
        try:
            fix = json.loads(json.loads(row[0])["content"])
        except (ValueError, KeyError, TypeError):
            return None, f"{FIX_FILE} is not valid JSON"
        if not isinstance(fix, dict):
            return None, f"{FIX_FILE} is not a JSON object"
        base, patch, files, cause = fix.get("base"), fix.get("patch"), fix.get("files"), str(fix.get("root_cause") or "").strip()
        if base != data["before"]["head"]:
            return None, "the fix was made for another commit than the one that failed"
        parsed, why = patch_files(patch)
        if why:
            return None, why
        if not isinstance(files, list) or sorted(map(str, files)) != sorted(parsed):
            return None, "the files it lists are not the files its patch changes"
        if len(parsed) > MAX_FILES:
            return None, f"it changes {len(parsed)} files (at most {MAX_FILES})"
        tests = [f for f in parsed if TEST_FILE.search(f)]
        if tests:
            return None, f"it changes test files ({', '.join(tests)}): a fix may not change the tests to make them pass"
        if not cause or len(cause) > 1000:
            return None, "it names no root cause"
        unnamed = [f for f in parsed if f not in cause and f.rsplit("/", 1)[-1] not in cause]
        if unnamed:
            return None, f"it changes files its root cause does not mention ({', '.join(unnamed)}): only the files needed may change"
        return {"base": base, "patch": patch, "files": parsed, "root_cause": cause[:1000],
                "digest": hashlib.sha256(patch.encode()).hexdigest(), "stat": diff_stat(patch)}, ""

    def _apply_params(self, project, fix):
        return {"project": project, "base": fix["base"], "patch": fix["patch"], "digest": fix["digest"], "files": fix["files"]}

    def _approve(self, tid, project, data):
        from .jobs import JobError, action_for
        fix = data["fix"]
        params = self._apply_params(project, fix)
        aid = data.get("apply_approval")
        if not aid:
            act = action_for("project_apply", params, "private", untrusted=True, requester=AGENT)
            # The bound params hold the patch only as its digest; the card shows the patch itself (the digest is its sha256, checked
            # by jobs._check_params) and the root cause, so the owner approves the change, not a list of file names (review M6).
            act.card = {"patch": fix["patch"], "root_cause": fix["root_cause"]}
            d, aid = self.k.request(act)
            if d.denied:
                return self._fail(tid, data, f"refused by policy: {d.reason}. Nothing in your project was changed")
            if aid:
                data["apply_approval"] = aid
                st = fix["stat"]
                return self._wait(tid, data, "approve", f"waiting for your approval {aid} in the JARVIS console (PIN) to apply the "
                                  f"fix to {project}: {', '.join(fix['files'])} (+{st['added']} -{st['removed']} lines, digest "
                                  f"{fix['digest'][:12]}). The laptop first proves it in a scratch copy, then backs up each file. "
                                  "Nothing in your project was changed yet", task_state="waiting_approval")
        try:
            jid = self.jobs.create(self.node, "project_apply", params, approval_id=aid, owner=AGENT)
        except JobError as e:
            msg = str(e)
            if "approval pending" in msg:
                return self._wait(tid, data, "approve", f"waiting for your approval {aid} in the JARVIS console (PIN) to apply the fix "
                                  f"to {project}. Nothing in your project was changed yet", task_state="waiting_approval")
            if "approval denied" in msg:
                return self._fail(tid, data, f"you declined applying the fix (approval {aid}); nothing in your project was changed. "
                                             "The proposed patch stays in this task's record")
            if re.search(r"approval (expired|required|used)", msg):
                data.pop("apply_approval", None)
                self._put(tid, "approve", data)
                return None
            return self._fail(tid, data, f"applying was refused: {msg[:240]}. Nothing in your project was changed")
        self.tasks.link_job(tid, jid)
        data["apply_job"] = jid
        self._task_state(tid, "running", step=f"applying the approved fix on the laptop (job {jid}): scratch-copy proof first")
        self._put(tid, "applying", data)
        return None

    def _applying(self, tid, project, data):
        j = self._job(data["apply_job"])
        if j["state"] in ("queued", "running"):
            return self._wait(tid, data, "applying", f"the laptop is proving the fix in a scratch copy and then applying it (job {j['id']})")
        r = j["result"] or {}
        data["apply"] = {k: r.get(k) for k in ("applied", "backups", "undo", "trial", "verified_on_disk", "error") if k in r}
        if j["state"] == "expired":
            return self._fail(tid, data, f"the laptop never picked up the approved fix (job {j['id']} expired). Nothing was changed")
        if j["state"] != "done" or not r.get("ok"):
            changed = bool(r.get("backups"))
            return self._fail(tid, data, f"the fix was not applied: {str(r.get('error') or j['state'])[:300]}" +
                              (f". Undo what was changed: {'; '.join(r.get('undo') or [])}" if changed else ". Nothing in your project was changed"))
        self._task_state(tid, "verifying", step="the kernel re-runs the project's tests")
        jid = self.jobs.create(self.node, "project_check", {"project": project}, owner=AGENT)
        self.tasks.link_job(tid, jid)
        data["verify_job"] = jid
        self._put(tid, "verify", data)
        return None

    def _verify(self, tid, project, data):
        j = self._job(data["verify_job"])
        undo = "; ".join((data.get("apply") or {}).get("undo") or [])
        if j["state"] in ("queued", "running"):
            return self._wait(tid, data, "verify", f"the laptop is re-running the tests after the fix (job {j['id']})")
        r = j["result"] or {}
        if j["state"] != "done" or not r.get("ok"):
            return self._fail(tid, data, f"the fix was applied, but the independent re-run could not run ({str(r.get('error') or j['state'])[:160]}). "
                                         f"Undo: {undo}")
        tests, g = r.get("tests") or {}, r.get("git") or {}
        data["after"] = {"tests": tests, "changed": g.get("changed") or [], "head": g.get("head")}
        fix, ap = data["fix"], data.get("apply") or {}
        new = sorted(set(g.get("changed") or []) - set(data["before"].get("changed") or []))
        trial = ap.get("trial") or {}
        checks = [
            (CRITERIA[0], tests.get("ran") is True and tests.get("exit_code") == 0,
             f"job:{j['id']}: exit code {tests.get('exit_code')}; {tests.get('summary', '')}"),
            (CRITERIA[1], bool(fix.get("root_cause")), fix.get("root_cause", "")[:300]),
            (CRITERIA[2], new == sorted(fix["files"]) and sorted(trial.get("needed") or []) == sorted(fix["files"])
             and not any(TEST_FILE.search(f) for f in fix["files"]),
             f"working tree changed in {new}; patch files {fix['files']}; each needed in the scratch-copy proof: {trial.get('needed')}"),
            (CRITERIA[3], bool(data.get("apply_approval")) and len(ap.get("backups") or []) == len(fix["files"]) and bool(ap.get("undo")),
             f"approval {data.get('apply_approval')}; backups {ap.get('backups')}"),
        ]
        verdict = {"verifier": "kernel", "method": "project_check re-run on the laptop after the approved fix (projectfix)",
                   "passed": all(ok for _, ok, _ in checks),
                   "criteria": [{"criterion": c, "met": ok, "evidence": ev} for c, ok, ev in checks],
                   "evidence": self._evidence(data), "recorded_at": self.clock()}
        if not verdict["passed"]:
            unmet = [c for c, ok, _ in checks if not ok]
            data["outcome"] = "not completed: " + "; ".join(unmet)
            self._put(tid, "failed", data)
            self.tasks.update(None, tid, state="failed", verification=verdict, evidence=self._evidence(data),
                              result=self._text(tid, data, f"not completed, the kernel's re-run does not show: {'; '.join(unmet)}. "
                                                           f"The fix is applied; undo: {undo}")[:6000])
            return self.report(tid)
        data["outcome"] = "fixed and verified"
        self._put(tid, "done", data)
        self.tasks.update(None, tid, state="completed", verification=verdict, evidence=self._evidence(data),
                          result=self._text(tid, data, "")[:6000])
        self.k.audit.append("projectfix_verified", {"task": tid, "project": project, "files": len(fix["files"])})
        return self.report(tid)

    def _done(self, tid, project, data):
        return self.report(tid)

    def _failed(self, tid, project, data):
        return self.report(tid)

    # ---------------------------------------------------------------- report
    def _text(self, tid, data, why) -> str:
        row = self._row(tid)
        project = row[0] if row else "?"
        b, a, fix, ap = data.get("before") or {}, data.get("after") or {}, data.get("fix") or {}, data.get("apply") or {}
        lines = []
        if b:
            t = b.get("tests") or {}
            lines.append(f"Project {project}: the tests {'passed' if t.get('exit_code') == 0 else 'failed'} before (job {data.get('inspect_job')}: "
                         f"exit code {t.get('exit_code')}; {t.get('summary', '')})." +
                         (" Last output lines: " + " | ".join(map(str, (t.get("tail") or [])[-3:])) if t.get("exit_code") not in (0, None) else ""))
        if fix:
            st = fix.get("stat") or {}
            lines.append(f"Root cause (from the lab, lab task {data.get('lab_task')}): {fix.get('root_cause')}")
            lines.append(f"Fix: {', '.join(fix.get('files') or [])} (+{st.get('added')} -{st.get('removed')} lines, digest {str(fix.get('digest'))[:12]}).")
        if ap.get("applied"):
            lines.append(f"Applied with your approval {data.get('apply_approval')} (job {data.get('apply_job')}) after a scratch-copy proof; "
                         f"backups {', '.join(ap.get('backups') or [])}. Undo: {'; '.join(ap.get('undo') or [])}.")
        if a:
            t = a.get("tests") or {}
            lines.append(f"Verified by the kernel: the re-run (job {data.get('verify_job')}) exited {t.get('exit_code')}; {t.get('summary', '')}. "
                         f"Changed in the working tree: {', '.join(sorted(set(a.get('changed') or []) - set(b.get('changed') or []))) or 'nothing'}.")
        if data.get("outcome") == "fixed and verified":
            lines.append("Left for you: the change is not committed; review it (git diff) and commit it, or undo it.")
        elif data.get("outcome", "").startswith("nothing to fix"):
            lines.append("Nothing to fix: the tests already pass. Nothing was changed.")
        elif why:
            lines.append("Not completed: " + why)
        return "\n".join(lines)

    def report(self, tid) -> dict:
        row = self._row(tid)
        if not row:
            raise ProjectFixError("no such project fix")
        project, stage, data = row
        t = self.tasks.get(tid) or {}
        return {"task": tid, "project": project, "stage": stage, "state": t.get("state"), "outcome": data.get("outcome"),
                "waiting": data.get("waiting") if stage not in ("done", "failed") else None,
                "verified": (t.get("verification") or {}).get("passed") is True and t.get("state") == "completed",
                "jobs": {k: data.get(k) for k in ("inspect_job", "apply_job", "verify_job", "lab_job") if data.get(k)},
                "lab_task": data.get("lab_task"), "approvals": {k: data.get(k) for k in ("share_approval", "apply_approval") if data.get(k)},
                "text": t.get("result") or self._text(tid, data, data.get("waiting") or "")}
