"""Idempotency keys for requests that cause side effects (2026-10-17).

The gap: a client that did not get the reply (the phone lost its connection, the console's 20 s timeout fired, an MCP tool
call was retried) sends the same request again, and the kernel acted again: a second owner command for JARVIS, a second
PC job (an app opened twice, a file saved twice), a second agent action. The guards that already exist cover other
cases and stay as they are: a node's job result is accepted once (jobs.finish), an automation slot fires once (its
UNIQUE fence), a lab dispatch refuses while the task has an active lab job, and an approval is used once.

With a key, the first request runs and its reply is kept for 24 hours; the same key with the same request returns that
reply (marked "replayed") without acting again. The same key with a different request is refused (422), and a key whose
first request is still being handled answers 409 instead of running it a second time. Only replies below 400 are kept:
a refused request did nothing, so sending it again may act. Without a key nothing changes.
The client sends the header Idempotency-Key, or the body field idempotency_key (the console passes bodies through
unchanged). Scope: the owner route, or the agent whose token sent it AND the lane of that token ("agent:<name>:<lane>"), so two
clients' keys never meet and a reply kept for the private lane is never served to the same name re-issued in the public lane
(review M1, 2026-10-18). Before a kept reply is served the caller may re-check what can have changed (kill switch, policy).
A kept reply must not outlive what it shows: when a memory entry is forgotten, every kept reply that shows it is replaced
by a short note (redact_replies; SEC-D2, 2026-10-18). The row stays, so the same key still never acts twice.
"""
import hashlib
import json
import re
import sqlite3
import time

TTL = 24 * 3600
KEY = re.compile(r"^[A-Za-z0-9_.:-]{8,100}$")


def key_from(headers, body) -> str:
    """The request's key: the Idempotency-Key header, else the body's idempotency_key (removed from the body: it is not
    part of the request itself). None when there is none."""
    k = headers.get("Idempotency-Key") if headers is not None else None
    if isinstance(body, dict) and "idempotency_key" in body:
        b = body.pop("idempotency_key")
        k = k or (str(b) if b is not None else None)
    return k if k else None


def redact_replies(db, needle: str, why: str) -> int:
    """Replace every kept reply that contains *needle* (an id such as a memory entry's m-xxxxxxxx) with {"status": "done",
    "redacted": why}. The row keeps its key, fingerprint, state and code: a repeat of that request is still answered from
    here (now with the note) and never acts again. Returns how many replies were redacted. Callers run it inside their own
    transaction (memory forget), so the reply and the entry disappear together."""
    needle = str(needle)
    if not KEY.match(needle):                       # ids only: never a pattern that matches every reply
        raise ValueError("redact_replies needs an id (8 to 100 letters, digits, '_', '.', ':' or '-')")
    pattern = "%" + needle.replace("\\", "\\\\").replace("_", "\\_").replace("%", "\\%") + "%"
    try:
        r = db.execute("UPDATE idempotency SET reply=? WHERE reply IS NOT NULL AND reply LIKE ? ESCAPE '\\'",
                       (json.dumps({"status": "done", "redacted": str(why)[:200]}), pattern))
    except sqlite3.OperationalError:                # no idempotency table on this kernel: no reply was ever kept
        return 0
    return r.rowcount


def _refusal(code, msg):
    return code, {"status": "refused", "reason": msg, "error": msg}


class Idempotency:
    def __init__(self, kernel, clock=time.time):
        self.k, self.clock = kernel, clock
        kernel.db.execute("CREATE TABLE IF NOT EXISTS idempotency(scope TEXT, key TEXT, fingerprint TEXT, created REAL, state TEXT, "
                          "code INT, reply TEXT, PRIMARY KEY(scope, key))")
        kernel.db.execute("CREATE INDEX IF NOT EXISTS idempotency_created ON idempotency(created)")

    def run(self, scope: str, key, request, fn, recheck=None):
        """(status, reply): fn() once per (scope, key) within 24 h; the stored reply for a repeat.
        recheck: called just before a stored reply is served; it returns None (serve it) or a (status, reply) refusal that is
        served instead. A kept reply must not outlive the decision it recorded (review M1): the caller re-runs the checks that
        can have changed since (kill switch, policy). The row stays, so the request still never acts twice."""
        if key is None:
            return fn()
        key = str(key)
        if not KEY.match(key):
            return _refusal(400, "bad Idempotency-Key: 8 to 100 letters, digits, '_', '.', ':' or '-'")
        fp = hashlib.sha256(json.dumps(request, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()
        now = self.clock()
        db = self.k.db
        with db.tx():                                   # claim the key: of two identical requests at once, one runs
            db.execute("DELETE FROM idempotency WHERE created<?", (now - TTL,))
            claimed = db.execute("INSERT OR IGNORE INTO idempotency(scope, key, fingerprint, created, state) VALUES (?,?,?,?,'running')",
                                 (scope, key, fp, now)).rowcount == 1
            row = None if claimed else db.execute("SELECT fingerprint, state, code, reply FROM idempotency WHERE scope=? AND key=?",
                                                  (scope, key)).fetchone()
        if not claimed:
            if row[0] != fp:
                self.k.audit.append("idempotency_mismatch", {"scope": scope[:60], "key": key[:40]})
                return _refusal(422, "this Idempotency-Key was already used for a different request")
            if row[1] != "done":
                self.k.audit.append("idempotency_in_flight", {"scope": scope[:60], "key": key[:40]})
                return _refusal(409, "the same request is still being handled (or it was interrupted); check its state instead of "
                                     "sending it again")
            stop = recheck() if recheck is not None else None
            if stop is not None:
                self.k.audit.append("idempotent_replay_refused", {"scope": scope[:60], "key": key[:40], "code": stop[0]})
                return stop
            self.k.audit.append("idempotent_replay", {"scope": scope[:60], "key": key[:40], "code": row[2]})
            reply = json.loads(row[3])
            return row[2], dict(reply, replayed=True) if isinstance(reply, dict) else reply
        try:
            code, reply = fn()
        except BaseException:
            db.execute("DELETE FROM idempotency WHERE scope=? AND key=? AND state='running'", (scope, key))
            raise
        if code < 400:
            db.execute("UPDATE idempotency SET state='done', code=?, reply=? WHERE scope=? AND key=?",
                       (code, json.dumps(reply, default=str), scope, key))
        else:                                           # refused: nothing happened, so the same request may act later
            db.execute("DELETE FROM idempotency WHERE scope=? AND key=? AND state='running'", (scope, key))
        return code, reply
