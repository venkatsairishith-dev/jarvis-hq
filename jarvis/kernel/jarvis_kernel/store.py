"""Kernel: SQLite state, single-use approvals, kill switch. Every decision is audited.

Known limit: a process running as the SAME OS user can edit the files directly. The real fix is to run
agents under a different OS user than the kernel (planned with the Hermes phase). Until then the kill
file and approvals are protected by policy (the kernel data dir is a protected resource) and by audit.
"""
import contextlib
import dataclasses
import json
import os
import secrets
import sqlite3
import threading
import time
from pathlib import Path

from .audit import AuditLog
from .policy import Action, Decision, decide, DENY, NAMES


class Killed(Exception):
    pass


# The owner approves what he SEES (review M6, 2026-10-18). An approval card now carries the complete scrubbed text of the action
# (it used to cut every value at 2000 characters while the digest bound all of it, so text past the cut could be approved
# unseen). A card longer than this is not shown cut: the request is refused instead and nothing is sent for approval.
CARD_MAX = 150_000
CARD_BLOCKED = "[BLOCKED: private key]"
CLOCK_SLACK = 5.0          # seconds a clock may differ between two steps of one approval before it is read as "set back"


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


def add_column(db, table: str, coldef: str) -> bool:
    """ALTER TABLE ADD COLUMN for a database written by an older version. Only "duplicate column name" (already there) is
    ignored; a locked, read-only, full or corrupt database now surfaces instead of leaving the table without the column
    (static finding ST-4)."""
    try:
        db.execute("ALTER TABLE " + table + " ADD COLUMN " + coldef)
        return True
    except sqlite3.OperationalError as e:
        if "duplicate column name" in str(e).lower():
            return False
        raise


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
        self._private_files()
        self.db.execute(
            "CREATE TABLE IF NOT EXISTS approvals(id TEXT PRIMARY KEY, action_hash TEXT, level INT, expires REAL, state TEXT, action TEXT)"
        )
        for col in ("reason TEXT", "decided REAL", "job TEXT", "created REAL", "requester TEXT"):   # older databases were created without these columns
            add_column(self.db, "approvals", col)
        self.audit = AuditLog(self.db)
        self.kill_file = self.dir / "KILL"
        self.lock = threading.RLock()
        # The kernel's own data dir is always protected: no action may touch state.db or KILL.
        self.protected = set(protected or ()) | {str(self.dir).replace("\\", "/").lower(), "hermes-agent-vm-2"}   # the data dir and the protected server are always off limits

    # --- kill switch -------------------------------------------------
    def _private_files(self):
        """Re-check F (2026-10-19): the data folder and the database held the owner's text but were created with the process umask
        (0775 / 0644 here), so any local account could read them. They are now owner-only (0700 / 0600; SQLite gives the -wal and
        -shm files the database's mode). JARVIS_DATA_MODE=keep leaves the modes alone (a deployment that shares the folder on
        purpose). Best effort: a filesystem without modes (Windows) is left as it is."""
        if os.environ.get("JARVIS_DATA_MODE", "").strip().lower() == "keep" or os.name == "nt":
            return
        try:
            if self.dir.stat().st_uid == os.getuid():
                os.chmod(self.dir, 0o700)
            for n in ("state.db", "state.db-wal", "state.db-shm", "KILL"):
                p = self.dir / n
                if p.exists() and p.stat().st_uid == os.getuid():
                    os.chmod(p, 0o600)
        except OSError:
            pass

    def kill(self, why: str = "manual"):
        self.kill_file.write_text(str(time.time()))
        self._private_files()
        self.audit.append("kill", {"why": why})

    def resume(self):
        since = self._kill_since()
        n = self._revoke_decided_since(since) if since is not None else 0       # approvals decided WHILE the switch was on
        self.kill_file.unlink(missing_ok=True)
        self.audit.append("resume", {"withdrawn_approvals": n} if n else {})

    def _kill_since(self):
        """When the kill switch was switched on (the KILL file holds the time), or None when that cannot be read."""
        try:
            return float(self.kill_file.read_text().strip())
        except (OSError, ValueError):
            return None

    def _revoke_decided_since(self, since: float) -> int:
        """Review C (2026-10-19): approve() refuses while the switch is on, so nothing should have been approved during it. If
        something was anyway (a race, a hand-edited database), it is withdrawn when the switch is lifted: the agent asks again
        and the owner approves again. Approvals given BEFORE the kill stay valid (they were not used while killed)."""
        with self.lock:
            cur = self.db.execute("UPDATE approvals SET state='denied', reason=? WHERE state='approved' AND decided>=?",
                                  ("withdrawn: approved while the kill switch was on", since))
        if cur.rowcount:
            self.audit.append("approvals_withdrawn", {"count": cur.rowcount, "why": "approved during a kill"})
        return cur.rowcount

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
        card = None
        if d.needs_approval and not d.denied and not self.killed():
            card = a.describe(CARD_MAX)
            why = self._card_problem(card)
            if why:                               # never an approval the owner could not read to the end
                d, card = Decision(DENY, why), None
        self.audit.append("request", {"action": a.describe(), "level": d.name, "reason": d.reason})
        aid = None
        if card is not None:
            aid = secrets.token_hex(8)
            with self.lock:
                now = time.time()
                self.db.execute(
                    "INSERT INTO approvals(id, action_hash, level, expires, state, action, reason, created, requester) VALUES (?,?,?,?,?,?,?,?,?)",
                    (aid, a.digest(), d.level, now + self.APPROVAL_TTL, "pending", json.dumps(card, default=str), d.reason[:200], now,
                     a.requester[:80]),
                )
        return d, aid

    @staticmethod
    def _card_problem(card) -> str:
        """Why this card cannot be shown to the owner in full ('' when it can)."""
        if Kernel._truncated(card):
            return "part of this request is too long to show the owner in full, so it cannot be approved: ask again with shorter text"
        n = Kernel._text_len(card)
        if n > CARD_MAX:
            return (f"this request is {n} characters, more than the {CARD_MAX} the owner can be shown on one approval card, so it "
                    "cannot be approved: ask again with shorter text. Nothing was sent for approval")
        return ""

    def approval_info(self, aid: str):
        r = self.db.execute("SELECT action, level, state, expires, requester FROM approvals WHERE id=?", (aid,)).fetchone()
        if not r:
            return None
        act = _action_json(r[0])
        return {"action": act, "level": NAMES[r[1]], "state": r[2], "seconds_left": round(r[3] - time.time()),
                "requester": r[4] or "", "incomplete": self._truncated(act), "blocked": self._blocked(act)}

    def approve(self, aid: str) -> bool:
        """Human step. The CLI only calls this after a real-console confirmation. Atomic: pending -> approved once.
        An approval whose stored card is cut off (made before cards were complete, or damaged) is never approved: the owner
        could not have seen all of it. He denies it and the agent asks again."""
        if self.killed():                         # review C: never while the kill switch is on (deny and expiry still work)
            self.audit.append("approve_refused", {"id": aid, "why": "kill switch"})
            return False
        r = self.db.execute("SELECT action FROM approvals WHERE id=? AND state='pending'", (aid,)).fetchone()
        if r and self._truncated(_action_json(r[0])):
            self.audit.append("approve_refused", {"id": aid, "why": "card incomplete"})
            return False
        if r and self._blocked(_action_json(r[0])):      # re-review M1: invisible or misleading text on the card
            self.audit.append("approve_refused", {"id": aid, "why": "card has invisible or misleading characters"})
            return False
        with self.lock:
            now = time.time()
            # created <= now: a clock set back to before the request was made cannot revive a stale one (re-check D, 2026-10-19)
            cur = self.db.execute("UPDATE approvals SET state='approved', decided=? WHERE id=? AND state='pending' AND expires>=? "
                                  "AND (created IS NULL OR created<=?)", (now, aid, now, now + CLOCK_SLACK))
        ok = cur.rowcount == 1
        if ok and self.killed():                  # a kill that landed between the check and the update: withdraw it again
            self.db.execute("UPDATE approvals SET state='denied', reason=? WHERE id=? AND state='approved'", ("withdrawn: kill switch", aid))
            self.audit.append("approve_refused", {"id": aid, "why": "kill switch"})
            return False
        self.audit.append("approved" if ok else "approve_refused", {"id": aid})
        return ok

    def withdraw(self, aid: str, why: str = "withdrawn") -> bool:
        """Safe direction, no PIN: an approval that is still pending OR approved-but-unused is cancelled for good (its task was
        cancelled, the owner said stop). A used one cannot be taken back."""
        cur = self.db.execute("UPDATE approvals SET state='denied', decided=?, reason=? WHERE id=? AND state IN ('pending','approved')",
                              (time.time(), ("withdrawn: " + why)[:200], aid))
        if cur.rowcount == 1:
            self.audit.append("approval_withdrawn", {"id": aid, "why": why[:60]})
        return cur.rowcount == 1

    def deny(self, aid: str) -> bool:
        """Human step: pending -> denied, once. A denied approval can never be approved or used."""
        cur = self.db.execute("UPDATE approvals SET state='denied', decided=? WHERE id=? AND state='pending'", (time.time(), aid))
        ok = cur.rowcount == 1
        self.audit.append("denied" if ok else "deny_refused", {"id": aid})
        return ok

    @staticmethod
    def _digest_as(a: Action, requester: str) -> str:
        """The digest `a` would have had if `requester` had asked for it (to tell "other agent" from "other action")."""
        return dataclasses.replace(a, requester=requester).digest()

    @staticmethod
    def _blocked(act) -> bool:
        """The card carries invisible, control or misleading characters that make it unapprovable (visible.py)."""
        inv = act.get("invisible") if isinstance(act, dict) else None
        return isinstance(inv, dict) and inv.get("status") == "blocked"

    @staticmethod
    def _text_len(x) -> int:
        """Characters of text the owner would read on the card (JSON escaping does not count)."""
        if isinstance(x, str):
            return len(x)
        if isinstance(x, dict):
            return sum(len(str(k)) + Kernel._text_len(v) for k, v in x.items())
        if isinstance(x, (list, tuple)):
            return sum(Kernel._text_len(v) for v in x)
        return len(str(x))

    @staticmethod
    def _truncated(x) -> bool:
        """True when a shown value is not the whole text: cut at a limit, or replaced because it held a private key."""
        if isinstance(x, str):
            return x.endswith("...[truncated]") or x == CARD_BLOCKED
        if isinstance(x, dict):
            return x.get("unreadable") is True or any(Kernel._truncated(v) for v in x.values())
        if isinstance(x, list):
            return any(Kernel._truncated(v) for v in x)
        return False

    def pending_approvals(self, limit=20):
        """Approvals waiting for the owner, newest first: id, level, why, the scrubbed action as it is bound, and the
        first characters of its digest so the owner can match it to what the agent asked for."""
        now = time.time()
        rows = self.db.execute("SELECT id, level, action, expires, reason, action_hash, requester FROM approvals WHERE state='pending' AND expires>=? "
                               "ORDER BY expires DESC LIMIT ?", (now, limit)).fetchall()
        out = []
        for i, lv, a, e, why, h, who in rows:
            act = _action_json(a)
            # `requester` = the agent that asked ("" = the owner's own request on the owner plane): the card says who, not only what
            out.append({"id": i, "level": NAMES[lv], "action": act, "seconds_left": round(e - now), "reason": why or "", "digest": (h or "")[:12],
                        "requester": who or "", "truncated": self._truncated(act), "blocked": self._blocked(act),
                        "invisible_chars": int(act.get("invisible_chars") or 0), "has_invisible": bool(act.get("has_invisible")),
                        "chars": self._text_len(act)})
        return out

    def approval_history(self, limit=15):
        """Decided approvals, newest first, with the authoritative outcome: denied, expired, approved (not yet used),
        or used (consumed once; `job` is the job it authorized, if any)."""
        now = time.time()
        rows = self.db.execute("SELECT id, level, action, expires, state, decided, job, reason, requester FROM approvals WHERE state!='pending' "
                               "OR expires<? ORDER BY COALESCE(decided, expires) DESC LIMIT ?", (now, limit)).fetchall()
        out = []
        for i, lv, a, e, st, dec, job, why, who in rows:
            state = st if st != "pending" else "expired"
            if st == "approved" and e < now:
                state = "expired"
            act = _action_json(a)
            out.append({"id": i, "level": NAMES[lv], "state": state, "at": dec or e, "job": job, "reason": why or "", "requester": who or "",
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
                    "UPDATE approvals SET state='used', decided=? WHERE id=? AND state='approved' AND expires>=? AND action_hash=? AND level>=? "
                    "AND (decided IS NULL OR decided<=?)",                     # decided <= now: a clock set back cannot revive an expired one
                    (time.time(), approval_id or "", time.time(), a.digest(), d.level, time.time() + CLOCK_SLACK),
                )
            if cur.rowcount == 1:
                ok, reason = True, "approved, single use"
            else:
                r = self.db.execute("SELECT state, expires, action_hash, requester FROM approvals WHERE id=?", (approval_id or "",)).fetchone()
                if not r:
                    reason = "approval required"
                elif r[0] != "approved":
                    reason = f"approval {r[0]}"
                elif r[1] < time.time():
                    reason = "approval expired"
                elif r[2] != a.digest():
                    # the digest covers the requester: say so when that is the only difference, so the owner's log shows a
                    # cross-agent attempt for what it is (audit SEC-D3)
                    same_but_who = (r[3] or "") != a.requester and r[2] == self._digest_as(a, r[3] or "")
                    reason = "approval was given for another agent's request" if same_but_who else "approval is for a different action"
                else:
                    reason = "approval level too low"
        self.audit.append("authorize", {"action": a.describe(), "level": d.name, "allowed": ok, "reason": reason})
        d.reason, d.allowed = reason, ok
        return d
