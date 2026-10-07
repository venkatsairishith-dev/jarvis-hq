"""Kernel: SQLite state, single-use approvals, kill switch. Every decision is audited."""
import json
import os
import secrets
import sqlite3
import time
from pathlib import Path

from .audit import AuditLog
from .policy import Action, Decision, decide, NAMES


class Killed(Exception):
    pass


class Kernel:
    APPROVAL_TTL = 600  # seconds

    def __init__(self, data_dir: str | None = None, protected: set | None = None):
        self.dir = Path(data_dir or os.environ.get("JARVIS_DATA") or Path.home() / ".jarvis-kernel")
        self.dir.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.dir / "state.db")
        self.db.execute(
            "CREATE TABLE IF NOT EXISTS approvals(id TEXT PRIMARY KEY, action_hash TEXT, level INT, expires REAL, state TEXT)"
        )
        self.db.execute("CREATE TABLE IF NOT EXISTS tasks(id INTEGER PRIMARY KEY, ts REAL, action TEXT, level TEXT, state TEXT)")
        self.audit = AuditLog(self.db)
        self.kill_file = self.dir / "KILL"
        self.protected = protected or set()

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
        if self.killed():
            raise Killed("kill switch is on")

    # --- authorize ---------------------------------------------------
    def request(self, a: Action, suggested: int | None = None):
        """Decide and record. Returns (decision, approval_id or None)."""
        d = decide(a, suggested, self.protected)
        self.audit.append("request", {"action": a.__dict__, "level": d.name, "reason": d.reason})
        aid = None
        if d.needs_approval and not d.denied and not self.killed():
            aid = secrets.token_hex(8)
            self.db.execute(
                "INSERT INTO approvals VALUES (?,?,?,?,?)", (aid, a.digest(), d.level, time.time() + self.APPROVAL_TTL, "pending")
            )
            self.db.commit()
        return d, aid

    def approve(self, aid: str) -> bool:
        """Human step. The CLI only calls this from an interactive terminal."""
        r = self.db.execute("SELECT state, expires FROM approvals WHERE id=?", (aid,)).fetchone()
        if not r or r[0] != "pending" or r[1] < time.time():
            self.audit.append("approve_refused", {"id": aid})
            return False
        self.db.execute("UPDATE approvals SET state='approved' WHERE id=?", (aid,))
        self.db.commit()
        self.audit.append("approved", {"id": aid})
        return True

    def authorize(self, a: Action, approval_id: str | None = None, suggested: int | None = None) -> Decision:
        """Final gate before any action runs. Raises Killed; returns the decision (check .allowed via return)."""
        self.check_alive()
        d = decide(a, suggested, self.protected)
        ok = False
        if d.denied:
            reason = d.reason
        elif not d.needs_approval:
            ok, reason = True, "allowed by policy"
        else:
            r = self.db.execute("SELECT action_hash, level, expires, state FROM approvals WHERE id=?", (approval_id or "",)).fetchone()
            if not r:
                reason = "approval required"
            elif r[3] != "approved":
                reason = f"approval {r[3]}"
            elif r[2] < time.time():
                reason = "approval expired"
            elif r[0] != a.digest():
                reason = "approval is for a different action"
            elif r[1] < d.level:
                reason = "approval level too low"
            else:
                self.db.execute("UPDATE approvals SET state='used' WHERE id=?", (approval_id,))
                self.db.commit()
                ok, reason = True, "approved, single use"
        self.audit.append("authorize", {"action": a.__dict__, "level": d.name, "allowed": ok, "reason": reason})
        d.reason = reason
        d.allowed = ok
        return d
