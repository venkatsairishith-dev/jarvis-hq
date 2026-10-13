"""Append-only, hash-chained audit log with an off-box anchor. Safe for several threads and processes."""
import hashlib
import json
import sqlite3
import threading
import time

GENESIS = "0" * 64
_LOCK = threading.RLock()


def _h(prev: str, ts: float, kind: str, payload: str) -> str:
    return hashlib.sha256(f"{prev}|{ts!r}|{kind}|{payload}".encode()).hexdigest()


class AuditLog:
    def __init__(self, db: sqlite3.Connection):
        self.db = db
        db.execute(
            "CREATE TABLE IF NOT EXISTS audit(seq INTEGER PRIMARY KEY, ts REAL, kind TEXT, payload TEXT, prev TEXT, hash TEXT)"
        )
        db.execute("CREATE INDEX IF NOT EXISTS audit_ts ON audit(ts)")       # the overview scans the last 24 h every 10 s
        db.execute("CREATE INDEX IF NOT EXISTS audit_kind ON audit(kind)")
        db.commit()

    def head(self):
        r = self.db.execute("SELECT seq, hash FROM audit ORDER BY seq DESC LIMIT 1").fetchone()
        return r if r else (0, GENESIS)

    def append(self, kind: str, payload: dict) -> int:
        body = json.dumps(payload, sort_keys=True, default=str)
        if hasattr(self.db, "tx"):
            # SafeDB: its connection lock is the only lock (a second, module-level one would invert the lock order
            # of a caller that is already inside db.tx()). Inside such a transaction this is a SAVEPOINT.
            with self.db.tx():
                return self._insert(kind, body)
        with _LOCK:                      # a bare sqlite3 connection (tests, tools)
            self.db.execute("BEGIN IMMEDIATE")  # takes the write lock before reading the head: no two writers share a seq
            try:
                seq = self._insert(kind, body)
                self.db.execute("COMMIT")
            except BaseException:
                self.db.execute("ROLLBACK")
                raise
        return seq

    def _insert(self, kind: str, body: str) -> int:
        seq, prev = self.head()
        ts = time.time()
        self.db.execute("INSERT INTO audit(seq, ts, kind, payload, prev, hash) VALUES (?,?,?,?,?,?)",
                        (seq + 1, ts, kind, body, prev, _h(prev, ts, kind, body)))
        return seq + 1

    def anchor(self) -> dict:
        seq, h = self.head()
        return {"seq": seq, "hash": h}

    def verify(self, anchor=None):
        """Return (ok, message). Detects edits, deleted rows, truncation and a rewrite from genesis
        (the last two only when a trusted anchor is given)."""
        if anchor is not None:
            if not isinstance(anchor, dict) or not isinstance(anchor.get("seq"), int) or not isinstance(anchor.get("hash"), str):
                return False, "malformed anchor"
        prev, count = GENESIS, 0
        for seq, ts, kind, payload, p, h in self.db.execute("SELECT seq,ts,kind,payload,prev,hash FROM audit ORDER BY seq"):
            count += 1
            if seq != count:
                return False, f"gap or reorder at seq {seq}"
            if p != prev or _h(prev, ts, kind, payload) != h:
                return False, f"chain broken at seq {seq}"
            prev = h
        if anchor is not None:
            if count < anchor["seq"]:
                return False, f"truncated: have {count}, anchor says {anchor['seq']}"
            if anchor["seq"]:
                r = self.db.execute("SELECT hash FROM audit WHERE seq=?", (anchor["seq"],)).fetchone()
                if not r or r[0] != anchor["hash"]:
                    return False, "history rewritten: anchor hash differs"
            elif anchor["hash"] != GENESIS:
                return False, "malformed anchor"
        return True, f"ok: {count} events"
