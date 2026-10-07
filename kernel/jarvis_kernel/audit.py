"""Append-only, hash-chained audit log with an off-box anchor."""
import hashlib
import json
import sqlite3
import time

GENESIS = "0" * 64


def _h(prev: str, ts: float, kind: str, payload: str) -> str:
    return hashlib.sha256(f"{prev}|{ts!r}|{kind}|{payload}".encode()).hexdigest()


class AuditLog:
    def __init__(self, db: sqlite3.Connection):
        self.db = db
        db.execute(
            "CREATE TABLE IF NOT EXISTS audit(seq INTEGER PRIMARY KEY, ts REAL, kind TEXT, payload TEXT, prev TEXT, hash TEXT)"
        )
        db.commit()

    def head(self):
        r = self.db.execute("SELECT seq, hash FROM audit ORDER BY seq DESC LIMIT 1").fetchone()
        return r if r else (0, GENESIS)

    def append(self, kind: str, payload: dict) -> int:
        seq, prev = self.head()
        ts = time.time()
        body = json.dumps(payload, sort_keys=True, default=str)
        self.db.execute(
            "INSERT INTO audit(seq, ts, kind, payload, prev, hash) VALUES (?,?,?,?,?,?)",
            (seq + 1, ts, kind, body, prev, _h(prev, ts, kind, body)),
        )
        self.db.commit()
        return seq + 1

    def anchor(self) -> dict:
        seq, h = self.head()
        return {"seq": seq, "hash": h}

    def verify(self, anchor: dict | None = None):
        """Return (ok, message). Detects edits, deleted rows, truncation and a rewrite from genesis
        (the last two only when a trusted anchor is given)."""
        prev, count = GENESIS, 0
        for seq, ts, kind, payload, p, h in self.db.execute("SELECT seq,ts,kind,payload,prev,hash FROM audit ORDER BY seq"):
            count += 1
            if seq != count:
                return False, f"gap or reorder at seq {seq}"
            if p != prev or _h(prev, ts, kind, payload) != h:
                return False, f"chain broken at seq {seq}"
            prev = h
        if anchor:
            if count < anchor["seq"]:
                return False, f"truncated: have {count}, anchor says {anchor['seq']}"
            if anchor["seq"]:
                r = self.db.execute("SELECT hash FROM audit WHERE seq=?", (anchor["seq"],)).fetchone()
                if not r or r[0] != anchor["hash"]:
                    return False, "history rewritten: anchor hash differs"
        return True, f"ok: {count} events"
