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


class TaskError(Exception):
    pass


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
