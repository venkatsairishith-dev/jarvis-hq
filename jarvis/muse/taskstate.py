"""Durable task state machine with leases, approval waits and retry safety.

Pure standard library (sqlite3). Every write is a single transaction, so a
crash never leaves a half-written state. The clock is injected for tests.

Worker protocol:
  claim -> (renew every lease_s/3) -> [mark_effect_started -> side effect ->
  mark_effect_done] -> complete | fail | request_approval.
A task whose lease lapses while an irreversible effect was in flight
(outcome_uncertain=1) is failed by recover() and never requeued automatically.

Provenance: behaviour ported from openmuse (github.com/CopilotKit/openmuse, MIT, Copyright (c) 2026 OpenMuse contributors), commit 1ac68f3, apps/server/src/engine/worker.ts and service.ts (task leases, approval waits); reimplemented in Python.
"""
from __future__ import annotations

import json
import sqlite3
import threading
import time
import uuid
from typing import Any, Callable, Optional

QUEUED, SCHEDULED, RUNNING = "queued", "scheduled", "running"
WAITING_APPROVAL, SUCCEEDED, FAILED = "waiting_approval", "succeeded", "failed"
CANCELLED, PAUSED = "cancelled", "paused"
STATES = (QUEUED, SCHEDULED, RUNNING, WAITING_APPROVAL, SUCCEEDED, FAILED, CANCELLED, PAUSED)

# Spec transitions. approve() requeues (waiting_approval->queued) and recover()
# requeues (running->queued) so the next claim continues; both are system moves.
LEGAL: dict[str, frozenset[str]] = {
    QUEUED: frozenset({RUNNING, CANCELLED}),
    SCHEDULED: frozenset({QUEUED, CANCELLED}),
    RUNNING: frozenset({WAITING_APPROVAL, SUCCEEDED, FAILED, PAUSED, CANCELLED, QUEUED}),
    WAITING_APPROVAL: frozenset({RUNNING, CANCELLED, FAILED, QUEUED}),
    PAUSED: frozenset({QUEUED, CANCELLED}),
    FAILED: frozenset({QUEUED}),
    SUCCEEDED: frozenset(),
    CANCELLED: frozenset(),
}


class TaskError(Exception):
    """Base class for task store errors."""


class BadTransition(TaskError):
    """Illegal state transition."""


class LeaseLost(TaskError):
    """Caller does not own a live lease on the task."""


class RetryRefused(TaskError):
    """Retry refused because the previous outcome is uncertain."""


class UnknownTask(TaskError):
    """No such task id."""


class ApprovalError(TaskError):
    """Approval already decided or not pending."""


_SCHEMA = """
CREATE TABLE IF NOT EXISTS tasks(
  id TEXT PRIMARY KEY, kind TEXT NOT NULL, params TEXT, state TEXT NOT NULL,
  run_at REAL, lease_owner TEXT, lease_until REAL, attempts INTEGER NOT NULL DEFAULT 0,
  key TEXT UNIQUE, outcome_uncertain INTEGER NOT NULL DEFAULT 0,
  approval TEXT, result TEXT, error TEXT, created REAL NOT NULL, updated REAL NOT NULL,
  seq INTEGER
);
CREATE TABLE IF NOT EXISTS task_history(
  n INTEGER PRIMARY KEY AUTOINCREMENT, task_id TEXT NOT NULL, at REAL NOT NULL,
  from_state TEXT, to_state TEXT NOT NULL, actor TEXT
);
CREATE INDEX IF NOT EXISTS tasks_state ON tasks(state, created);
"""


class _Tx:
    """Context manager: serialise threads, BEGIN IMMEDIATE, COMMIT/ROLLBACK."""

    def __init__(self, store: "TaskStore") -> None:
        self.store = store

    def __enter__(self) -> sqlite3.Connection:
        self.store._lock.acquire()
        try:
            self.store._db.execute("BEGIN IMMEDIATE")
        except BaseException:
            self.store._lock.release()
            raise
        return self.store._db

    def __exit__(self, et, ev, tb) -> bool:
        try:
            self.store._db.execute("ROLLBACK" if et else "COMMIT")
        finally:
            self.store._lock.release()
        return False


class TaskStore:
    def __init__(self, path: Any = ":memory:", clock: Callable[[], float] = time.time,
                 lease_s: float = 60, max_attempts: int = 5) -> None:
        self.clock = clock
        self.lease_s = lease_s
        self.max_attempts = max_attempts      # a task that keeps losing its worker is failed, not requeued forever
        self._lock = threading.RLock()
        self._db = sqlite3.connect(str(path), check_same_thread=False,
                                   isolation_level=None, timeout=30)
        self._db.row_factory = sqlite3.Row
        self._db.executescript(_SCHEMA)

    def close(self) -> None:
        with self._lock:
            self._db.close()

    # -- internals ---------------------------------------------------------
    def _tx(self) -> _Tx:
        return _Tx(self)

    def _row(self, db: sqlite3.Connection, task_id: str) -> sqlite3.Row:
        r = db.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
        if r is None:
            raise UnknownTask(task_id)
        return r

    def _move(self, db: sqlite3.Connection, row: sqlite3.Row, to: str, actor: str,
              now: float, **cols: Any) -> None:
        frm = row["state"]
        if to not in LEGAL.get(frm, ()):
            raise BadTransition(f"{frm} -> {to}")
        cols.update(state=to, updated=now)
        sets = ", ".join(f"{k}=?" for k in cols)
        db.execute(f"UPDATE tasks SET {sets} WHERE id=?", (*cols.values(), row["id"]))
        db.execute("INSERT INTO task_history(task_id, at, from_state, to_state, actor) "
                   "VALUES(?,?,?,?,?)", (row["id"], now, frm, to, actor))

    def _owned(self, row: sqlite3.Row, worker: str, now: float) -> None:
        if (row["state"] != RUNNING or row["lease_owner"] != worker
                or row["lease_until"] is None or row["lease_until"] <= now):
            raise LeaseLost(f"{worker} does not hold a live lease on {row['id']}")

    @staticmethod
    def _task(row: sqlite3.Row) -> dict:
        d = dict(row)
        for k in ("params", "approval", "result"):
            d[k] = json.loads(d[k]) if d[k] is not None else None
        d.pop("seq", None)
        return d

    # -- API ---------------------------------------------------------------
    def get(self, task_id: str) -> dict:
        with self._lock:
            return self._task(self._row(self._db, task_id))

    def list(self, states: Optional[tuple[str, ...]] = None, since: Optional[float] = None, limit: int = 200) -> list[dict]:
        """Read-only listing, newest change first: tasks in `states` (all when None) updated at or after `since`."""
        where, args = [], []
        if states:
            where.append(f"state IN ({', '.join('?' * len(states))})")
            args += list(states)
        if since is not None:
            where.append("updated >= ?")
            args.append(since)
        sql = "SELECT * FROM tasks" + (" WHERE " + " AND ".join(where) if where else "") + " ORDER BY updated DESC LIMIT ?"
        with self._lock:
            return [self._task(r) for r in self._db.execute(sql, (*args, int(limit))).fetchall()]

    def enqueue(self, kind: str, params: Any = None, run_at: Optional[float] = None,
                key: Optional[str] = None) -> str:
        with self._tx() as db:
            if key is not None:
                r = db.execute("SELECT id FROM tasks WHERE key=?", (key,)).fetchone()
                if r:
                    return r["id"]
            now = self.clock()
            tid = uuid.uuid4().hex
            state = SCHEDULED if run_at is not None and run_at > now else QUEUED
            seq = db.execute("SELECT COALESCE(MAX(seq),0)+1 FROM tasks").fetchone()[0]
            db.execute("INSERT INTO tasks(id,kind,params,state,run_at,key,created,updated,seq) "
                       "VALUES(?,?,?,?,?,?,?,?,?)",
                       (tid, kind, json.dumps(params), state, run_at, key, now, now, seq))
            db.execute("INSERT INTO task_history(task_id, at, from_state, to_state, actor) "
                       "VALUES(?,?,?,?,?)", (tid, now, None, state, "enqueue"))
            return tid

    def claim(self, worker: str) -> Optional[dict]:
        with self._tx() as db:
            now = self.clock()
            due = db.execute("SELECT * FROM tasks WHERE state=? AND run_at<=? ORDER BY run_at, seq",
                             (SCHEDULED, now)).fetchall()
            for r in due:
                self._move(db, r, QUEUED, "scheduler", now)
            r = db.execute("SELECT * FROM tasks WHERE state=? ORDER BY seq LIMIT 1",
                           (QUEUED,)).fetchone()
            if r is None:
                return None
            self._move(db, r, RUNNING, worker, now, lease_owner=worker,
                       lease_until=now + self.lease_s, attempts=r["attempts"] + 1)
            return self._task(self._row(db, r["id"]))

    def renew(self, task_id: str, worker: str) -> float:
        """Extend the lease; call every lease_s/3. A lapsed lease raises LeaseLost."""
        with self._tx() as db:
            now = self.clock()
            self._owned(self._row(db, task_id), worker, now)
            until = now + self.lease_s
            db.execute("UPDATE tasks SET lease_until=?, updated=? WHERE id=?", (until, now, task_id))
            return until

    def recover(self) -> list[str]:
        """Handle running tasks with expired leases. Returns affected ids."""
        out: list[str] = []
        with self._tx() as db:
            now = self.clock()
            rows = db.execute("SELECT * FROM tasks WHERE state=? AND lease_until<=?",
                              (RUNNING, now)).fetchall()
            for r in rows:
                if r["outcome_uncertain"]:
                    self._move(db, r, FAILED, "recover", now, lease_owner=None,
                               lease_until=None, error="outcome uncertain: not retried")
                elif r["attempts"] >= self.max_attempts:
                    self._move(db, r, FAILED, "recover", now, lease_owner=None, lease_until=None,
                               error="max attempts reached")
                else:
                    self._move(db, r, QUEUED, "recover", now, lease_owner=None, lease_until=None)
                out.append(r["id"])
        return out

    def _mark(self, task_id: str, worker: str, value: int) -> None:
        with self._tx() as db:
            now = self.clock()
            self._owned(self._row(db, task_id), worker, now)
            db.execute("UPDATE tasks SET outcome_uncertain=?, updated=? WHERE id=?",
                       (value, now, task_id))

    def mark_effect_started(self, task_id: str, worker: str) -> None:
        """Call immediately BEFORE an irreversible side effect."""
        self._mark(task_id, worker, 1)

    def mark_effect_done(self, task_id: str, worker: str) -> None:
        self._mark(task_id, worker, 0)

    def retry(self, task_id: str, force: bool = False,
              confirm_duplicate_risk: bool = False) -> None:
        with self._tx() as db:
            r = self._row(db, task_id)
            if r["state"] != FAILED:
                raise BadTransition(f"{r['state']} -> {QUEUED}")
            if r["outcome_uncertain"] and not (force and confirm_duplicate_risk):
                raise RetryRefused("previous outcome uncertain; retry may duplicate a side effect")
            self._move(db, r, QUEUED, "owner-retry", self.clock(), outcome_uncertain=0, error=None)

    def request_approval(self, task_id: str, worker: str, payload: Any) -> None:
        with self._tx() as db:
            now = self.clock()
            r = self._row(db, task_id)
            self._owned(r, worker, now)
            appr = {"payload": payload, "decision": None, "by": None, "decided_at": None}
            self._move(db, r, WAITING_APPROVAL, worker, now, lease_owner=None,
                       lease_until=None, approval=json.dumps(appr))

    def approve(self, task_id: str, decision: bool, by: str) -> None:
        with self._tx() as db:
            now = self.clock()
            r = self._row(db, task_id)
            if r["state"] != WAITING_APPROVAL:
                raise ApprovalError(f"task is {r['state']}, no approval pending")
            appr = json.loads(r["approval"] or "{}")
            if appr.get("decision") is not None:
                raise ApprovalError("approval already decided")
            appr.update(decision=bool(decision), by=by, decided_at=now)
            self._move(db, r, QUEUED if decision else CANCELLED, by, now,
                       approval=json.dumps(appr))

    def _simple(self, task_id: str, to: str, actor: str, **cols: Any) -> None:
        with self._tx() as db:
            self._move(db, self._row(db, task_id), to, actor, self.clock(), **cols)

    def pause(self, task_id: str) -> None:
        self._simple(task_id, PAUSED, "pause", lease_owner=None, lease_until=None)

    def resume(self, task_id: str, force: bool = False, confirm_duplicate_risk: bool = False) -> None:
        """Paused -> queued. Refused while an irreversible effect was in flight (same rule as retry): running it again could repeat it."""
        with self._tx() as db:
            r = self._row(db, task_id)
            if r["outcome_uncertain"] and not (force and confirm_duplicate_risk):
                raise RetryRefused("an irreversible effect was in flight; resuming may duplicate it")
            self._move(db, r, QUEUED, "resume", self.clock(), outcome_uncertain=0)

    def cancel(self, task_id: str) -> None:
        self._simple(task_id, CANCELLED, "cancel", lease_owner=None, lease_until=None)

    def complete(self, task_id: str, worker: str, result: Any = None) -> None:
        with self._tx() as db:
            now = self.clock()
            r = self._row(db, task_id)
            self._owned(r, worker, now)
            self._move(db, r, SUCCEEDED, worker, now, lease_owner=None, lease_until=None,
                       result=json.dumps(result))

    def fail(self, task_id: str, worker: str, error: str) -> None:
        with self._tx() as db:
            now = self.clock()
            r = self._row(db, task_id)
            self._owned(r, worker, now)
            self._move(db, r, FAILED, worker, now, lease_owner=None, lease_until=None,
                       error=error)

    def history(self, task_id: str) -> list[tuple[float, Optional[str], str, Optional[str]]]:
        """Audit trail of (time, from_state, to_state, actor); no params/results."""
        with self._lock:
            self._row(self._db, task_id)
            rows = self._db.execute("SELECT at, from_state, to_state, actor FROM task_history "
                                    "WHERE task_id=? ORDER BY n", (task_id,)).fetchall()
        return [(r["at"], r["from_state"], r["to_state"], r["actor"]) for r in rows]
