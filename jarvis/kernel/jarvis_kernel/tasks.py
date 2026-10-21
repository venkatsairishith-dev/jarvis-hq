"""Outcome tasks: what the owner asked for, the plan and acceptance criteria, where it runs, and how it ended.

A task is the unit the owner sees in Mission Control and Telegram. JARVIS (the owner's Hermes) creates it from a
request, may dispatch work to the lab (a lab runner executes, verifies and repairs it), and closes it with evidence.
Every state change is an event; transitions are checked so a task cannot jump from failed back to running, and
budgets (attempts, deadline) stop retries from running forever.
Custom code, justified in docs/01_RESEARCH_AUDIT.md: Hermes Kanban is single-host by design and cannot span the server
and the lab; Hermes runs and delegations do not survive restarts. These records do, and the kernel stays the authority.
"""
import json
import re
import secrets
import time

STATES = ("planning", "waiting_approval", "queued", "running", "verifying", "repairing", "paused", "completed", "failed", "cancelled")
FINAL = {"completed", "failed", "cancelled"}
NEXT = {   # allowed transitions
    "planning": {"waiting_approval", "queued", "running", "paused", "completed", "failed", "cancelled"},
    "waiting_approval": {"queued", "running", "failed", "cancelled"},
    "queued": {"running", "paused", "failed", "cancelled"},
    "running": {"verifying", "repairing", "waiting_approval", "paused", "completed", "failed", "cancelled"},
    "verifying": {"completed", "repairing", "failed", "paused", "cancelled"},
    "repairing": {"running", "verifying", "failed", "paused", "cancelled"},
    "paused": {"queued", "running", "cancelled", "failed"},
}
TEXT_MAX = {"request": 4000, "goal": 1000, "step": 300, "result": 6000, "note": 1000}
LIST_MAX = 20                         # plan steps, criteria, evidence items
ID = re.compile(r"^[0-9a-f]{12}$")
# Which job kinds can be cited as evidence that a task's outcome exists (audit M11, 2026-10-18). Jobs that only LOOK at something or
# show a file (output_show, ui_windows, ui_snapshot, media_state, browser_ext, diag, connector_op ...) prove nothing about the outcome,
# so a task cannot be "completed" on them. A saved output (the documented deliverable path) still counts; whether a very small
# output_save should be enough is the owner's call (see the report): the kernel cannot judge a file's content against the criteria,
# which is why such a completion is recorded WITHOUT a passed flag ("NOT verified yet").
EVIDENCE_KINDS = frozenset({"output_save", "project_check", "project_apply", "ui_action", "pc_open", "screenshot", "repair", "lab_run"})


class TaskError(Exception):
    pass


HEX64 = re.compile(r"^[0-9a-f]{64}$")
TRUSTED_KEYS = ("ran_by", "command", "exit_code", "tests_run", "output_sha256", "output_tail", "files_sha256", "artifact_sha256",
                "seconds", "task", "job", "attempt",
                # the runner's harness evidence (re-review H2, 2026-10-18): counted by the runner from per-test records, never parsed from output
                "harness", "harness_script_sha256", "collect_exit_code", "tests_collected", "tests_passed", "tests_failed", "tests_errored",
                "tests_skipped", "tests_expected_failures", "leftover_processes", "collected_ids_sha256", "harness_sha256",
                # the isolation boundary the tests ran in (lab isolation, 2026-10-19)
                "isolation")
HARNESS_VERSIONS = ("lab-harness/1", "lab-harness/2")     # /1: one process, no separation; /2: one sandbox process per test id (needs isolation)
ISOLATION_KEYS = {"mode": str, "backend": str, "self_test": str, "verifier_tools_isolated": bool, "separate_verifier_identity": bool,
                  "sandbox_uid": int, "verify_uid": int, "runner_uid": int, "processes": int, "granularity": str, "cpu_seconds": (int, float),
                  "reasons": list}
ISOLATION_MODES = ("full", "identity", "container", "none")
# Keys only the kernel sets on a verdict (assure_verdict). A report that carries one is not believed: a failing verdict used to keep an
# `agent_assessed_pass` it arrived with, and a task could then close as "completed, NOT verified" on a verdict that never passed.
KERNEL_OWNED_KEYS = ("agent_assessed_pass", "criteria_checked", "assurance", "kernel_check", "isolation")


def isolation_problem(iso) -> str:
    """'' when `iso` is an exact, consistent isolation block, else why not. Exact keys and types, no extras. 'full' is the only mode a pass
    can be verified on: separated execution (identity or container backend, self-test ok) AND the owner states the verifier's tools are sandboxed.
    The identities must really be different users (sandbox uid is neither root, nor the verifier's, nor the runner's). Pure."""
    if not isinstance(iso, dict) or set(iso) != set(ISOLATION_KEYS):
        return "the isolation block is missing or has the wrong fields"
    for k, t in ISOLATION_KEYS.items():
        v = iso[k]
        if t is bool:
            ok = isinstance(v, bool)
        elif t is int or t == (int, float):
            ok = isinstance(v, t) and not isinstance(v, bool)
        else:
            ok = isinstance(v, t)
        if not ok:
            return f"isolation.{k} has the wrong type"
    if iso["mode"] not in ISOLATION_MODES or iso["backend"] not in ("identity", "container", "none"):
        return "isolation.mode or backend is not a known value"
    if not all(isinstance(r, str) and len(r) <= 200 for r in iso["reasons"]) or len(iso["reasons"]) > 6 or len(iso["self_test"]) > 120:
        return "isolation.reasons or self_test is out of bounds"
    if iso["mode"] == "none":
        return ""
    if iso["backend"] == "none" or iso["self_test"] != "ok":
        return "isolation claims a boundary but has no backend or its self-test did not pass"
    if iso["mode"] == "full" and not iso["verifier_tools_isolated"]:
        return "isolation mode full without the verifier's tools isolated"
    if iso["mode"] == "full" and (not iso["separate_verifier_identity"] or iso["verify_uid"] == iso["sandbox_uid"]):
        return "isolation mode full without a verifier identity of its own"
    if iso["mode"] == "full" and iso["granularity"] != "id":
        return "isolation mode full needs one process per test (granularity id): a test in a group can answer for its group-mates"
    if iso["mode"] in ("identity", "container") and (iso["mode"] != iso["backend"] or iso["verifier_tools_isolated"]):
        return "isolation.mode does not match its backend or the verifier-tools statement"
    if iso["backend"] == "identity" and (iso["sandbox_uid"] <= 0 or iso["sandbox_uid"] in (iso["verify_uid"], iso["runner_uid"])):
        return "the sandbox identity is root, the verifier's or the runner's (no separation)"
    if iso["processes"] < 2 or iso["cpu_seconds"] < 0:
        return "isolation.processes / cpu_seconds are not credible (a collection and at least one test process)"
    return ""


def criteria_sha256(criteria) -> str:
    """The job's criteria as the kernel stored them at dispatch, hashed in one canonical form. The runner hashes what it was
    handed; a report whose hash differs was made for other criteria (lab verdict audit, 2026-10-18)."""
    import hashlib
    return hashlib.sha256(json.dumps([str(c) for c in (criteria or [])], ensure_ascii=True, separators=(",", ":")).encode()).hexdigest()


def trusted_problem(trusted, ver, tid, job, attempt) -> str:
    """'' when `trusted` is a well-formed record of the RUNNER's own execution of the delivered tests for exactly this task, job and
    attempt and for the artifact the verdict names; else why not. Pure. The kernel cannot re-run anything: this record is as
    trustworthy as the runner's token (root-only on the lab box), which no lab agent can read. It is never taken from the
    verdict file the verifier wrote (the runner puts it in its own report field)."""
    if not isinstance(trusted, dict):
        return "trusted_execution is not an object"
    extra = set(trusted) - set(TRUSTED_KEYS)
    if extra:
        return "trusted_execution has fields the kernel does not know: " + ", ".join(sorted(str(k) for k in extra))[:100]
    for key, maxlen in (("command", 200), ("output_tail", 400), ("harness", 40), ("harness_script_sha256", 64)):
        v = trusted.get(key)
        if v is not None and not (isinstance(v, str) and len(v) <= maxlen):
            return f"trusted_execution.{key} has the wrong type or is too long"
    if trusted.get("ran_by") != "lab-runner":
        return "trusted_execution was not recorded by the lab runner"
    for key, want in (("task", tid), ("job", job), ("attempt", attempt)):
        if trusted.get(key) != want:
            return f"trusted_execution is for another {key}"
    code, n = trusted.get("exit_code"), trusted.get("tests_run")
    if isinstance(code, bool) or code != 0:
        return "the runner's own run of the delivered tests did not exit 0"
    if isinstance(n, bool) or not isinstance(n, int) or n < 1:
        return "the runner's own run executed no tests"
    for key in ("files_sha256", "output_sha256", "artifact_sha256"):
        if not (isinstance(trusted.get(key), str) and HEX64.match(trusted[key])):
            return f"trusted_execution has no valid {key}"
    if trusted["artifact_sha256"] != ver.get("artifact_sha256"):
        return "trusted_execution was made on other files than the verdict names (artifact digest differs)"
    # Re-review H2 (2026-10-18): the number of tests is the runner's own count from a harness it controls (every collected test must have
    # finished, nothing left running), not a line the tests printed. The kernel cannot re-run anything, so it checks that the record is
    # complete, internally consistent and bound to the verdict it rests on.
    if trusted.get("harness") not in HARNESS_VERSIONS:
        return "trusted_execution was not produced by the runner's test harness (an older runner counted tests from their output)"
    ints = {}
    for key in ("tests_collected", "tests_passed", "tests_failed", "tests_errored", "tests_skipped", "tests_expected_failures",
                "leftover_processes", "collect_exit_code"):
        v = trusted.get(key)
        if isinstance(v, bool) or not isinstance(v, int) or (v < 0 and key != "collect_exit_code"):
            return f"trusted_execution has no valid {key}"
        ints[key] = v
    if ints["collect_exit_code"] != 0:
        return "the collection step of the runner's test run did not exit 0"
    if ints["tests_collected"] != n or ints["tests_passed"] + ints["tests_failed"] + ints["tests_errored"] + ints["tests_skipped"] \
            + ints["tests_expected_failures"] != n:
        return "the runner's test counts are inconsistent (collected, run, passed, failed, errored and skipped do not add up)"
    if ints["tests_failed"] or ints["tests_errored"]:
        return "the runner's own run of the delivered tests had failures or errors"
    if ints["tests_passed"] < 1:
        return "the runner's own run of the delivered tests passed no test (skipped-only is not a pass)"
    if ints["leftover_processes"]:
        return "the delivered tests left processes running"
    for key in ("collected_ids_sha256", "harness_sha256"):
        if not (isinstance(trusted.get(key), str) and HEX64.match(trusted[key])):
            return f"trusted_execution has no valid {key}"
        if trusted[key] != ver.get(key):
            return f"the verdict names another {key} than trusted_execution (the evidence is not the one the verdict rests on)"
    iso_why = isolation_problem(trusted.get("isolation"))
    if iso_why:
        return iso_why
    if trusted["isolation"]["mode"] != "none" and trusted.get("harness") != "lab-harness/2":
        return "an isolated run must come from the sandbox harness (lab-harness/2)"
    if trusted["isolation"]["mode"] == "none" and trusted.get("harness") != "lab-harness/1":
        return "a run without isolation must say so (lab-harness/1)"
    if not (isinstance(ver.get("verdict_channel_sha256"), str) and HEX64.match(ver["verdict_channel_sha256"])):
        return "the verdict was not taken from the verifier's output channel (no verdict_channel_sha256)"
    return ""


def assure_verdict(ver, trusted, tid, job, attempt):
    """(verdict to store, problem). A verdict is 'verified' (passed true) only when the runner's OWN execution of the delivered tests
    backs it (`trusted`, valid). The verifier's per-criterion rows are the verifier's opinion: the verifier shares its OS user with
    the code it runs, so its verdict file can be forged. Without trusted execution a pass is kept ONLY as `agent_assessed_pass`
    (no passed flag, so every screen says "NOT verified yet") and only when the runner itself said that no runnable tests exist
    (`agent_assessed`). A failing verdict needs no proof. `problem` is set when a pass had to be demoted (the caller fails the task)."""
    ver = dict(ver)
    ver.pop("trusted_execution", None)                  # nothing the verdict file carried counts as the runner's evidence
    for key in KERNEL_OWNED_KEYS:                       # these say what the KERNEL decided; a report cannot bring them in
        ver.pop(key, None)
    if ver.get("passed") is not True:
        ver.pop("agent_assessed", None)
        return ver, ""
    if trusted is not None:
        why = trusted_problem(trusted, ver, tid, job, attempt)
        if why:
            return dict(ver, passed=False, kernel_check=why[:300]), why
        ver["trusted_execution"] = {k: trusted[k] for k in TRUSTED_KEYS if k in trusted}
        ver["isolation"] = trusted["isolation"]
        ver.pop("agent_assessed", None)
        if trusted["isolation"]["mode"] != "full":
            # The tests really ran (and passed) but not behind a boundary the owner can rely on for "verified": no boundary at all, or code the
            # verifier runs is not known to be sandboxed. Kept as 'completed, NOT verified', with the reason on the record.
            out = {k: v for k, v in ver.items() if k not in ("passed", "agent_assessed")}
            mode = trusted["isolation"]["mode"]
            out.update(agent_assessed_pass=True, criteria_checked=False,
                       assurance="isolated_tests_only" if mode in ("identity", "container") else "unisolated_tests",
                       kernel_check=("not verified: " + ("; ".join(trusted["isolation"]["reasons"]) or f"isolation mode {mode}"))[:300])
            return out, ""
        ver["assurance"] = "runner_executed_tests"
        return ver, ""
    if ver.get("agent_assessed") is True:               # no runnable tests were delivered: the verifier's word alone
        out = {k: v for k, v in ver.items() if k not in ("passed", "agent_assessed")}
        out.update(agent_assessed_pass=True, assurance="agent_assessed", criteria_checked=False)
        return out, ""
    why = "a passed verdict needs the runner's own execution of the delivered tests (trusted_execution), or the runner's statement that none can run"
    return dict(ver, passed=False, kernel_check=why[:300]), why


def _clip(s, n):
    return str(s if s is not None else "")[:n]


def _list(x, n=LIST_MAX, each=500):
    if not isinstance(x, list):
        return []
    return [_clip(v if isinstance(v, str) else json.dumps(v, default=str), each) for v in x[:n]]


class Tasks:
    def __init__(self, kernel):
        self.k = kernel
        kernel.db.execute(
            "CREATE TABLE IF NOT EXISTS tasks(id TEXT PRIMARY KEY, created REAL, updated REAL, owner TEXT, lane TEXT, source TEXT, "
            "request TEXT, goal TEXT, plan TEXT, criteria TEXT, state TEXT, step TEXT, attempts INT, max_attempts INT, "
            "deadline REAL, result TEXT, evidence TEXT, verification TEXT, parent TEXT)")
        kernel.db.execute("CREATE TABLE IF NOT EXISTS task_events(id INTEGER PRIMARY KEY, task TEXT, ts REAL, state TEXT, kind TEXT, note TEXT)")
        kernel.db.execute("CREATE INDEX IF NOT EXISTS task_events_task ON task_events(task)")
        kernel.db.execute("CREATE INDEX IF NOT EXISTS tasks_state_updated ON tasks(state, updated)")
        kernel.db.execute("CREATE INDEX IF NOT EXISTS tasks_owner_updated ON tasks(owner, updated)")
        # Which jobs were created FOR a task (recorded by the kernel when the job is created): only those count as evidence.
        kernel.db.execute("CREATE TABLE IF NOT EXISTS task_jobs(task TEXT, job TEXT, created REAL, PRIMARY KEY(task, job))")
        # Which owner command (owner_commands.id) was the running one when the owner's agent created the task. It lets "stop this
        # command" stop what that command started (the kernel cannot see inside the agent's conversation; this is the link).
        kernel.db.execute("CREATE TABLE IF NOT EXISTS task_commands(task TEXT PRIMARY KEY, command TEXT, created REAL)")
        kernel.db.execute("CREATE INDEX IF NOT EXISTS task_commands_command ON task_commands(command)")
        # One verdict per lab job and attempt (lab verdict audit, 2026-10-18): a replayed, duplicated or late report cannot record a second.
        kernel.db.execute("CREATE TABLE IF NOT EXISTS lab_verdicts(job TEXT, attempt INT, sha TEXT, ts REAL, PRIMARY KEY(job, attempt))")

    # ---- writes ----
    def create(self, owner, lane, request, goal, plan=None, criteria=None, source="agent", max_attempts=3, budget_s=3600, parent=None):
        if not str(goal or "").strip():
            raise TaskError("a task needs a goal")
        if not 1 <= int(max_attempts) <= 5 or not 60 <= int(budget_s) <= 6 * 3600:
            raise TaskError("budget out of range (1-5 attempts, 1 min to 6 h)")
        tid, now = secrets.token_hex(6), time.time()
        self.k.db.execute("INSERT INTO tasks VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                          (tid, now, now, owner, lane, _clip(source, 20), _clip(request, TEXT_MAX["request"]), _clip(goal, TEXT_MAX["goal"]),
                           json.dumps(_list(plan)), json.dumps(_list(criteria)), "planning", "", 0, int(max_attempts), now + int(budget_s),
                           "", "[]", "{}", parent))
        self._event(tid, "planning", "created", _clip(goal, 200))
        self.k.audit.append("task_created", {"id": tid, "owner": owner, "lane": lane})
        return tid

    def update(self, owner, tid, state=None, step=None, plan=None, criteria=None, note=None, result=None, evidence=None,
               verification=None, attempt=False, force=False):
        # One transaction for read, check, write, event and audit row: the runner's report, an agent's update and the
        # owner's cancel can no longer interleave (a final state used to be overwritable between the read and the write).
        with self.k.db.tx():
            t = self._row(tid)
            if not t or (owner is not None and t["owner"] != owner):
                raise TaskError("no such task")
            if t["state"] in FINAL and not force:
                raise TaskError(f"task is already {t['state']}")
            sets, vals = ["updated=?"], [time.time()]
            if state is not None and state != t["state"]:
                if state not in STATES or state not in NEXT.get(t["state"], set()):
                    raise TaskError(f"cannot go from {t['state']} to {state}")
                if state == "repairing" and t["attempts"] >= t["max_attempts"]:
                    raise TaskError("retry budget used up: the task must fail or be completed")
                sets.append("state=?"); vals.append(state)
            if attempt:
                if t["attempts"] >= t["max_attempts"]:
                    raise TaskError("retry budget used up")
                sets.append("attempts=attempts+1")
            for col, val, f in (("step", step, lambda v: _clip(v, TEXT_MAX["step"])), ("plan", plan, lambda v: json.dumps(_list(v))),
                                ("criteria", criteria, lambda v: json.dumps(_list(v))), ("result", result, lambda v: _clip(v, TEXT_MAX["result"])),
                                ("evidence", evidence, lambda v: json.dumps(_list(v, each=1000))),
                                ("verification", verification, lambda v: json.dumps(v if isinstance(v, dict) else {"note": _clip(v, 1000)}, default=str)[:8000])):
                if val is not None:
                    sets.append(f"{col}=?"); vals.append(f(val))
            cur = self.k.db.execute(f"UPDATE tasks SET {', '.join(sets)} WHERE id=? AND state=?", (*vals, tid, t["state"]))
            if cur.rowcount != 1:                       # compare-and-set: another process changed it since we read it
                raise TaskError("the task changed while updating; try again")
            new = state or t["state"]
            self._event(tid, new, "state" if state and state != t["state"] else "update", _clip(note or step or "", TEXT_MAX["note"]))
            if state in FINAL:
                self.k.audit.append("task_" + state, {"id": tid, "owner": t["owner"]})
        return self.get(tid)

    def expire(self, now=None):
        """Tasks past their deadline fail: no task runs forever."""
        now = now or time.time()
        marks = ",".join("?" * len(FINAL))
        cand = [r[0] for r in self.k.db.execute(f"SELECT id FROM tasks WHERE deadline < ? AND state NOT IN ({marks})", (now, *FINAL)).fetchall()]
        ids = []
        for tid in cand:
            with self.k.db.tx():            # the state, its event and its audit row together; a task that just finished is left alone
                # the reason is always in the result: an earlier partial result alone read like the outcome (bench D-068)
                cur = self.k.db.execute(f"UPDATE tasks SET state='failed', updated=?, result=CASE WHEN result='' THEN 'time budget used up' "
                                        f"ELSE substr('time budget used up; last reported: ' || result, 1, {TEXT_MAX['result']}) END "
                                        f"WHERE id=? AND state NOT IN ({marks})", (now, tid, *FINAL))
                if cur.rowcount == 1:
                    self._event(tid, "failed", "state", "time budget used up")
                    self.k.audit.append("task_failed", {"id": tid, "why": "deadline"})
                    ids.append(tid)
        return ids

    def claim_verdict(self, job, attempt, sha="") -> bool:
        """Claim the one verdict slot of (lab job, attempt). True the first time, False for every repeat (also two at once)."""
        cur = self.k.db.execute("INSERT OR IGNORE INTO lab_verdicts(job, attempt, sha, ts) VALUES (?,?,?,?)",
                                (str(job)[:40], int(attempt), str(sha)[:64], time.time()))
        return cur.rowcount == 1

    def link_job(self, tid, jid):
        """Record that job `jid` was created for task `tid` (kernel side, at creation; an agent cannot link one later)."""
        self.k.db.execute("INSERT OR IGNORE INTO task_jobs(task, job, created) VALUES (?,?,?)", (tid, jid, time.time()))

    def link_command(self, tid, cid):
        """Record that task `tid` was created while owner command `cid` was the running one (kernel side, at creation)."""
        self.k.db.execute("INSERT OR IGNORE INTO task_commands(task, command, created) VALUES (?,?,?)", (tid, str(cid)[:40], time.time()))

    def for_command(self, cid) -> list:
        """The tasks created while owner command `cid` was running, oldest first."""
        return [r[0] for r in self.k.db.execute("SELECT task FROM task_commands WHERE command=? ORDER BY created", (str(cid)[:40],)).fetchall()]

    def job_ids(self, tid) -> list:
        """Every job that exists for task `tid` and has not ended: its lab runs and the jobs created for it (task_jobs)."""
        rows = self.k.db.execute(
            "SELECT id FROM jobs WHERE state IN ('queued','running') AND ((kind='lab_run' AND json_extract(params,'$.task')=?) "
            "OR id IN (SELECT job FROM task_jobs WHERE task=?)) ORDER BY created", (tid, tid)).fetchall()
        return [r[0] for r in rows]

    def evidence_jobs(self, tid, owner, cited):
        """Check the jobs an agent cites as job:<id> for completing its task. Returns (accepted ids, problems). Each cited job
        must be the agent's own (or the runner's delivery for this task), created for THIS task (linked when it was created,
        or this task's lab run), finished successfully, and created and finished after the task started. Every citation must
        hold: an unrelated or stale job is not padding, it is a false claim (audit SEC-D1, 2026-10-18)."""
        t = self._row(tid)
        if not t or t["owner"] != owner:
            return [], ["no such task"]
        ok, problems = [], []
        for j in dict.fromkeys(str(c) for c in cited):
            r = self.k.db.execute("SELECT kind, state, owner, params, created, finished FROM jobs WHERE id=?", (j,)).fetchone() \
                if ID.match(j) else None
            if not r:
                problems.append(f"job:{j} does not exist")
                continue
            kind, state, jowner, params, created, finished = r
            try:
                ptask = json.loads(params or "{}").get("task") if kind == "lab_run" else None
            except ValueError:
                ptask = None
            linked = (ptask == tid or jowner == f"runner-{tid}" or
                      self.k.db.execute("SELECT 1 FROM task_jobs WHERE task=? AND job=?", (tid, j)).fetchone() is not None)
            if jowner not in (owner, f"runner-{tid}"):
                problems.append(f"job:{j} is not yours")
            elif not linked:
                problems.append(f"job:{j} was not created for this task")
            elif state != "done":
                problems.append(f"job:{j} has not finished successfully ({state})")
            elif kind not in EVIDENCE_KINDS:
                problems.append(f"job:{j} ({kind}) only looks at or shows something; it does not show that the outcome exists")
            elif (created or 0) < t["created"] or (finished or 0) < t["created"]:
                problems.append(f"job:{j} is older than this task")
            else:
                ok.append(j)
        return ok, problems

    # ---- reads ----
    def _row(self, tid):
        if not ID.match(str(tid)):
            return None
        r = self.k.db.execute("SELECT * FROM tasks WHERE id=?", (tid,)).fetchone()
        if not r:
            return None
        cols = [d[1] for d in self.k.db.execute("PRAGMA table_info(tasks)").fetchall()]
        return dict(zip(cols, r))

    def get(self, tid, owner=None):
        t = self._row(tid)
        if not t or (owner is not None and t["owner"] != owner):
            return None
        for c in ("plan", "criteria", "evidence", "verification"):
            t[c] = json.loads(t[c] or ("{}" if c == "verification" else "[]"))
        t["events"] = [{"ts": ts, "state": s, "kind": k, "note": n} for ts, s, k, n in
                       self.k.db.execute("SELECT ts, state, kind, note FROM task_events WHERE task=? ORDER BY id DESC LIMIT 40", (tid,)).fetchall()]
        return t

    NEXT_ACTION = {
        "planning": "refine the plan and criteria, then hand the work to the lab (lab_work) once",
        "waiting_approval": "wait for the owner's approval",
        "queued": "wait: the lab will start it", "running": "wait: the lab is working; you will be told the outcome",
        "verifying": "wait: an independent check is running", "repairing": "wait: the lab is fixing what the check found",
        "paused": "wait until the owner resumes it",
        "completed": "tell the owner the verified result and where the files are",
        "failed": "tell the owner the exact blocker and what would unblock it",
        "cancelled": "nothing; the owner cancelled it",
    }

    def checkpoint(self, owner, limit=3, max_chars=900, recent_s=6 * 3600):
        """A small, durable picture of the owner's open (and just-finished) tasks, for a fresh or compacted session:
        goal, criteria, state, steps done, steps pending, decisions, next action."""
        rows = self.k.db.execute("SELECT id FROM tasks WHERE owner=? AND (state NOT IN ('completed','failed','cancelled') OR updated>?) "
                                 "ORDER BY updated DESC LIMIT ?", (owner, time.time() - recent_s, limit)).fetchall()
        out = []
        for (tid,) in rows:
            t = self.get(tid)
            ev = list(reversed(t["events"]))
            done = [e["note"] for e in ev if e["kind"] == "state" and e["note"]][-6:]
            decisions = [e["note"] for e in ev if e["kind"] in ("update", "correction") and e["note"]][-4:]
            out.append({"id": tid, "goal": t["goal"], "criteria": t["criteria"], "state": t["state"],
                        "attempts": f"{t['attempts']}/{t['max_attempts']}", "done": done, "pending": t["plan"] if t["state"] == "planning" else [],
                        "decisions": decisions, "result": t["result"], "next": self.NEXT_ACTION.get(t["state"], "")})
        # The goal is trimmed, never the owner's latest correction or the "next:" line: cutting the joined text at max_chars
        # used to drop exactly those after one long goal (bench A-080). When too long, optional lines go first (from the
        # oldest task up), then whole older tasks; essential lines are never cut mid-way.
        blocks = []
        for c in out:
            goal = str(c["goal"])
            block = [(f"Task {c['id']} [{c['state']}, attempts {c['attempts']}]: " + (goal if len(goal) <= 160 else goal[:157] + "..."), True),
                     ("  criteria: " + "; ".join(map(str, c["criteria"]))[:300], False)]
            if c["done"]:
                block.append(("  done: " + " | ".join(map(str, c["done"]))[:300], False))
            if c["pending"]:
                block.append(("  pending: " + "; ".join(map(str, c["pending"]))[:200], False))
            if c["decisions"]:
                block.append(("  decisions: " + " | ".join(map(str, c["decisions"]))[:250], True))
            if c["result"]:
                block.append(("  result: " + str(c["result"])[:200], False))
            block.append(("  next: " + c["next"], True))
            blocks.append(block)

        def joined():
            return "\n".join(line for b in blocks for line, _ in b)
        while len(joined()) > max_chars:
            opt = next((b for b in reversed(blocks) if any(not keep for _, keep in b)), None)
            if opt is not None:
                drop = ("  done:", "  pending:", "  criteria:", "  result:")      # the result (often the blocker) goes last
                opt.remove(min((x for x in opt if not x[1]), key=lambda x: next(i for i, p in enumerate(drop) if x[0].startswith(p))))
            elif len(blocks) > 1:
                blocks.pop()
            else:
                break
        return {"tasks": out, "text": joined()}

    def recent(self, limit=30):
        rows = self.k.db.execute("SELECT id FROM tasks ORDER BY updated DESC LIMIT ?", (limit,)).fetchall()
        return [self.get(r[0]) for r in rows]

    def _event(self, tid, state, kind, note):
        self.k.db.execute("INSERT INTO task_events(task, ts, state, kind, note) VALUES (?,?,?,?,?)", (tid, time.time(), state, kind, note))
