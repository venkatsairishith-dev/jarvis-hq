"""Kernel: SQLite state, single-use approvals, kill switch. Every decision is audited.

Known limit: a process running as the SAME OS user can edit the files directly. The real fix is to run
agents under a different OS user than the kernel (planned with the Hermes phase). Until then the kill
file and approvals are protected by policy (the kernel data dir is a protected resource) and by audit.
"""
import contextlib
import json
import os
import secrets
import sqlite3
import threading
import time
from pathlib import Path

from .audit import AuditLog
from .policy import Action, Decision, decide, NAMES


class Killed(Exception):
    pass


def _action_json(s) -> dict:
    """An approval's stored action for display. A damaged row shows as unreadable instead of failing the whole list (one
    bad row made the owner's state page answer 500; bench N-015). What an approval is bound to is its digest, not this."""
    try:
        a = json.loads(s)
        return a if isinstance(a, dict) else {"unreadable": True}
    except (TypeError, ValueError):
        return {"unreadable": True}


class _Rows:
    """A statement's result, read in full while the connection was held: safe to use after another thread moves on."""
    def __init__(self, cur, rows):
        self.rowcount, self.lastrowid, self.description, self._rows = cur.rowcount, cur.lastrowid, cur.description, rows

    def fetchone(self):
        return self._rows.pop(0) if self._rows else None

    def fetchall(self):
        rows, self._rows = self._rows, []
        return rows

    def __iter__(self):
        return iter(self.fetchall())


class SafeDB:
    """The kernel's one SQLite connection, shared by the server threads. Every statement runs under one re-entrant
    lock and is read in full, so threads never interleave inside another's statement (found live: a valid token got
    401 and an existing task 404 under concurrent requests). A transaction holds the same lock from BEGIN to
    COMMIT/ROLLBACK (see AuditLog.append), so no other thread's statement lands inside it."""
    def __init__(self, conn):
        self._c, self.lock, self._depth = conn, threading.RLock(), 0

    def execute(self, sql, args=()):
        with self.lock:
            cur = self._c.execute(sql, args)
            return _Rows(cur, cur.fetchall())

    @contextlib.contextmanager
    def tx(self):
        """One atomic read-check-write: BEGIN IMMEDIATE takes the database write lock (also against other processes),
        the connection lock keeps other threads' statements out, and a nested tx() is a SAVEPOINT, so a state change
        and its audit row commit or roll back together. An exception rolls the whole (or the nested) block back."""
        with self.lock:
            depth = self._depth
            name = f"jk_sp{depth}" if depth else None
            self._c.execute(f"SAVEPOINT {name}" if name else "BEGIN IMMEDIATE")
            self._depth = depth + 1
            try:
                yield self
            except BaseException:
                if name:
                    self._c.execute(f"ROLLBACK TO {name}")
                    self._c.execute(f"RELEASE {name}")
                else:
                    self._c.execute("ROLLBACK")
                raise
            else:
                self._c.execute(f"RELEASE {name}" if name else "COMMIT")
            finally:
                self._depth = depth

    def commit(self):
        with self.lock:
            if self._depth == 0:                 # inside tx() the block's own end commits
                self._c.commit()

    def close(self):
        with self.lock:
            self._c.close()


class Kernel:
    APPROVAL_TTL = 600  # seconds

    def __init__(self, data_dir=None, protected=None):
        self.dir = Path(data_dir or os.environ.get("JARVIS_DATA") or Path.home() / ".jarvis-kernel").resolve()
        self.dir.mkdir(parents=True, exist_ok=True)
        # isolation_level=None: we issue BEGIN/COMMIT ourselves. check_same_thread=False: the node server uses threads.
        self.db = SafeDB(sqlite3.connect(self.dir / "state.db", timeout=30, isolation_level=None, check_same_thread=False))
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute(
            "CREATE TABLE IF NOT EXISTS approvals(id TEXT PRIMARY KEY, action_hash TEXT, level INT, expires REAL, state TEXT, action TEXT)"
        )
        for col in ("reason TEXT", "decided REAL", "job TEXT", "created REAL"):   # older databases were created without these columns
            try:
                self.db.execute("ALTER TABLE approvals ADD COLUMN " + col)
            except Exception:
                pass
        self.audit = AuditLog(self.db)
        self.kill_file = self.dir / "KILL"
        self.lock = threading.RLock()
        # The kernel's own data dir is always protected: no action may touch state.db or KILL.
        self.protected = set(protected or ()) | {str(self.dir).replace("\\", "/").lower(), "hermes-agent-vm-2"}   # the data dir and the protected server are always off limits

    # --- kill switch -------------------------------------------------
    def kill(self, why: str = "manual"):
        self.kill_file.write_text(str(time.time()))
        self.audit.append("kill", {"why": why})

    def resume(self):
        self.kill_file.unlink(missing_ok=True)
        self.audit.append("resume", {})

    def killed(self) -> bool:
        return self.kill_file.exists()

    def check_alive(self):
        """Callers must re-check between steps: a kill that lands after this check stops the NEXT step."""
        if self.killed():
            raise Killed("kill switch is on")

    # --- authorize ---------------------------------------------------
    def request(self, a: Action, suggested=None):
        """Decide and record. Returns (decision, approval_id or None)."""
        d = decide(a, suggested, self.protected)
        self.audit.append("request", {"action": a.describe(), "level": d.name, "reason": d.reason})
        aid = None
        if d.needs_approval and not d.denied and not self.killed():
            aid = secrets.token_hex(8)
            with self.lock:
                now = time.time()
                self.db.execute(
                    "INSERT INTO approvals(id, action_hash, level, expires, state, action, reason, created) VALUES (?,?,?,?,?,?,?,?)",
                    (aid, a.digest(), d.level, now + self.APPROVAL_TTL, "pending", json.dumps(a.describe(2000), default=str), d.reason[:200], now),
                )
        return d, aid

    def approval_info(self, aid: str):
        r = self.db.execute("SELECT action, level, state, expires FROM approvals WHERE id=?", (aid,)).fetchone()
        if not r:
            return None
        return {"action": _action_json(r[0]), "level": NAMES[r[1]], "state": r[2], "seconds_left": round(r[3] - time.time())}

    def approve(self, aid: str) -> bool:
        """Human step. The CLI only calls this after a real-console confirmation. Atomic: pending -> approved once."""
        with self.lock:
            cur = self.db.execute("UPDATE approvals SET state='approved', decided=? WHERE id=? AND state='pending' AND expires>=?",
                                  (time.time(), aid, time.time()))
        ok = cur.rowcount == 1
        self.audit.append("approved" if ok else "approve_refused", {"id": aid})
        return ok

    def deny(self, aid: str) -> bool:
        """Human step: pending -> denied, once. A denied approval can never be approved or used."""
        cur = self.db.execute("UPDATE approvals SET state='denied', decided=? WHERE id=? AND state='pending'", (time.time(), aid))
        ok = cur.rowcount == 1
        self.audit.append("denied" if ok else "deny_refused", {"id": aid})
        return ok

    @staticmethod
    def _truncated(x) -> bool:
        if isinstance(x, str):
            return x.endswith("...[truncated]")
        if isinstance(x, dict):
            return any(Kernel._truncated(v) for v in x.values())
        if isinstance(x, list):
            return any(Kernel._truncated(v) for v in x)
        return False

    def pending_approvals(self, limit=20):
        """Approvals waiting for the owner, newest first: id, level, why, the scrubbed action as it is bound, and the
        first characters of its digest so the owner can match it to what the agent asked for."""
        now = time.time()
        rows = self.db.execute("SELECT id, level, action, expires, reason, action_hash FROM approvals WHERE state='pending' AND expires>=? "
                               "ORDER BY expires DESC LIMIT ?", (now, limit)).fetchall()
        out = []
        for i, lv, a, e, why, h in rows:
            act = _action_json(a)
            out.append({"id": i, "level": NAMES[lv], "action": act, "seconds_left": round(e - now), "reason": why or "", "digest": (h or "")[:12],
                        "truncated": self._truncated(act)})
        return out

    def approval_history(self, limit=15):
        """Decided approvals, newest first, with the authoritative outcome: denied, expired, approved (not yet used),
        or used (consumed once; `job` is the job it authorized, if any)."""
        now = time.time()
        rows = self.db.execute("SELECT id, level, action, expires, state, decided, job, reason FROM approvals WHERE state!='pending' "
                               "OR expires<? ORDER BY COALESCE(decided, expires) DESC LIMIT ?", (now, limit)).fetchall()
        out = []
        for i, lv, a, e, st, dec, job, why in rows:
            state = st if st != "pending" else "expired"
            if st == "approved" and e < now:
                state = "expired"
            act = _action_json(a)
            out.append({"id": i, "level": NAMES[lv], "state": state, "at": dec or e, "job": job, "reason": why or "",
                        "action": {"tool": act.get("tool"), "op": act.get("op"), "target": str(act.get("target", ""))[:120], "lane": act.get("lane")}})
        return out

    def link_approval_job(self, aid: str, job: str):
        self.db.execute("UPDATE approvals SET job=? WHERE id=? AND state='used'", (job, aid or ""))

    def authorize(self, a: Action, approval_id=None, suggested=None) -> Decision:
        """Final gate before any action runs. Raises Killed; otherwise returns a Decision with .allowed set."""
        self.check_alive()
        d = decide(a, suggested, self.protected)
        ok = False
        if d.denied:
            reason = d.reason
        elif not d.needs_approval:
            ok, reason = True, "allowed by policy"
        else:
            # One atomic statement consumes the approval: only a single caller can ever get rowcount 1.
            with self.lock:
                cur = self.db.execute(
                    "UPDATE approvals SET state='used', decided=? WHERE id=? AND state='approved' AND expires>=? AND action_hash=? AND level>=?",
                    (time.time(), approval_id or "", time.time(), a.digest(), d.level),
                )
            if cur.rowcount == 1:
                ok, reason = True, "approved, single use"
            else:
                r = self.db.execute("SELECT state, expires, action_hash FROM approvals WHERE id=?", (approval_id or "",)).fetchone()
                if not r:
                    reason = "approval required"
                elif r[0] != "approved":
                    reason = f"approval {r[0]}"
                elif r[1] < time.time():
                    reason = "approval expired"
                elif r[2] != a.digest():
                    reason = "approval is for a different action"
                else:
                    reason = "approval level too low"
        self.audit.append("authorize", {"action": a.describe(), "level": d.name, "allowed": ok, "reason": reason})
        d.reason, d.allowed = reason, ok
        return d
