"""Append-only, hash-chained audit log with an off-box anchor. Safe for several threads and processes.

Anchors (audit SEC-D8, 2026-10-18). Without an anchor, verify() sees edits and gaps but not a cut-off tail or a rewrite
from genesis. The kernel now writes the head {seq, hash, ts} to ANCHOR_FILE in its data dir (outside state.db) from the owner
tick, at most every ANCHOR_EVERY seconds and only when the head moved and the chain still matches the last anchor, so an
export never launders a truncation. The owner's `audit/verify` route and `jarvis audit verify` check against the newest
stored anchor. That file lives on the same machine, so it catches a database cut or edit, not someone who can also edit
the file: copying it off the box (jarvis/scripts/pull-anchor.ps1, or any copy of the file) is still a MANUAL owner step.
"""
import hashlib
import json
import os
import sqlite3
import threading
import time

GENESIS = "0" * 64
_LOCK = threading.RLock()
ANCHOR_FILE = "audit-anchors.jsonl"
ANCHOR_EVERY = 900          # seconds between anchor exports (when the head moved)


def _h(prev: str, ts: float, kind: str, payload: str) -> str:
    return hashlib.sha256(f"{prev}|{ts!r}|{kind}|{payload}".encode()).hexdigest()


def component_info(name: str, version: str, path) -> dict:
    """{"name", "version", "sha256"} for the file at `path`; sha256 is "missing" when it cannot be read."""
    h = hashlib.sha256()
    try:
        with open(path, "rb") as fh:
            for chunk in iter(lambda: fh.read(65536), b""):
                h.update(chunk)
        digest = h.hexdigest()
    except OSError:
        digest = "missing"
    return {"name": name, "version": version, "sha256": digest}


def _valid_component(c) -> bool:
    if not isinstance(c, dict) or set(c) != {"name", "version", "sha256"}:
        return False
    if not isinstance(c["name"], str) or not isinstance(c["version"], str) or not isinstance(c["sha256"], str):
        return False
    s = c["sha256"]
    return s == "missing" or (len(s) == 64 and all(ch in "0123456789abcdef" for ch in s))


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

    def append(self, kind: str, payload: dict, component: dict = None) -> int:
        if component is not None:
            if not _valid_component(component):
                raise ValueError("malformed component")
            payload = {**payload, "component": component}   # inside the payload, so the existing hash covers it
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
            if '"component"' in payload:         # cheap test first: most events have none, and verify() walks the whole chain
                try:
                    data = json.loads(payload)
                except ValueError:
                    data = None
                if isinstance(data, dict) and "component" in data and not _valid_component(data["component"]):
                    return False, f"malformed component at seq {seq}"
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


def _valid_anchor(a) -> bool:
    return isinstance(a, dict) and isinstance(a.get("seq"), int) and not isinstance(a.get("seq"), bool) and a["seq"] >= 0 \
        and isinstance(a.get("hash"), str) and len(a["hash"]) == 64


def latest_anchor(path):
    """(newest valid stored anchor or None, number of unreadable lines). The file is append-only JSON lines."""
    try:
        with open(path, encoding="utf-8") as fh:
            lines = [ln.strip() for ln in fh if ln.strip()]
    except FileNotFoundError:
        return None, 0
    except (OSError, UnicodeDecodeError):
        return None, 1
    best, bad = None, 0
    for ln in lines:
        try:
            a = json.loads(ln)
        except ValueError:
            bad += 1
            continue
        if not _valid_anchor(a):
            bad += 1
            continue
        if best is None or a["seq"] >= best["seq"]:
            best = a
    return ({"seq": best["seq"], "hash": best["hash"], "ts": best.get("ts")} if best else None), bad


def export_anchor(audit: "AuditLog", path, now: float = None, every: float = ANCHOR_EVERY):
    """Append the head anchor to `path` (rate-limited). Returns the anchor written, or None when nothing was written: the
    head did not move, the last export is younger than `every` seconds, or the chain no longer matches the last anchor
    (then writing a new one would hide the truncation or rewrite)."""
    now = time.time() if now is None else now
    last, _ = latest_anchor(path)
    seq, h = audit.head()
    if last is not None:
        if seq == last["seq"] and h == last["hash"]:
            return None
        if now - float(last.get("ts") or 0) < every:
            return None
        if seq < last["seq"]:
            return None                                          # cut below the last anchor: keep the evidence
        if last["seq"]:
            r = audit.db.execute("SELECT hash FROM audit WHERE seq=?", (last["seq"],)).fetchone()
            if not r or r[0] != last["hash"]:
                return None                                      # rewritten: keep the evidence
    a = {"seq": seq, "hash": h, "ts": now}
    fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    with os.fdopen(fd, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(a, sort_keys=True) + "\n")
        fh.flush()
        os.fsync(fh.fileno())
    return a
