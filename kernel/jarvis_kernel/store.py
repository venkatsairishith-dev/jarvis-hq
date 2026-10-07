"""Kernel: SQLite state, single-use approvals, kill switch. Every decision is audited.

Known limit: a process running as the SAME OS user can edit the files directly. The real fix is to run
agents under a different OS user than the kernel (planned with the Hermes phase). Until then the kill
file and approvals are protected by policy (the kernel data dir is a protected resource) and by audit.
"""
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


class Kernel:
    APPROVAL_TTL = 600  # seconds

    def __init__(self, data_dir=None, protected=None):
        self.dir = Path(data_dir or os.environ.get("JARVIS_DATA") or Path.home() / ".jarvis-kernel").resolve()
        self.dir.mkdir(parents=True, exist_ok=True)
        # isolation_level=None: we issue BEGIN/COMMIT ourselves. check_same_thread=False: the node server uses threads.
        self.db = sqlite3.connect(self.dir / "state.db", timeout=30, isolation_level=None, check_same_thread=False)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute(
            "CREATE TABLE IF NOT EXISTS approvals(id TEXT PRIMARY KEY, action_hash TEXT, level INT, expires REAL, state TEXT, action TEXT)"
        )
        self.audit = AuditLog(self.db)
        self.kill_file = self.dir / "KILL"
        self.lock = threading.RLock()
        # The kernel's own data dir is always protected: no action may touch state.db or KILL.
        self.protected = set(protected or ()) | {str(self.dir).replace("\\", "/").lower()}

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
                self.db.execute(
                    "INSERT INTO approvals VALUES (?,?,?,?,?,?)",
                    (aid, a.digest(), d.level, time.time() + self.APPROVAL_TTL, "pending", json.dumps(a.describe(), default=str)),
                )
        return d, aid

    def approval_info(self, aid: str):
        r = self.db.execute("SELECT action, level, state, expires FROM approvals WHERE id=?", (aid,)).fetchone()
        if not r:
            return None
        return {"action": json.loads(r[0]), "level": NAMES[r[1]], "state": r[2], "seconds_left": round(r[3] - time.time())}

    def approve(self, aid: str) -> bool:
        """Human step. The CLI only calls this after a real-console confirmation. Atomic: pending -> approved once."""
        with self.lock:
            cur = self.db.execute("UPDATE approvals SET state='approved' WHERE id=? AND state='pending' AND expires>=?", (aid, time.time()))
        ok = cur.rowcount == 1
        self.audit.append("approved" if ok else "approve_refused", {"id": aid})
        return ok

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
                    "UPDATE approvals SET state='used' WHERE id=? AND state='approved' AND expires>=? AND action_hash=? AND level>=?",
                    (approval_id or "", time.time(), a.digest(), d.level),
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
