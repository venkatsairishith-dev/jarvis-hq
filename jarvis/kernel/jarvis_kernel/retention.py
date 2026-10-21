"""Retention of private text in the kernel's own stores (re-review M4, 2026-10-19).

The kernel kept the owner's words for ever in places the owner never looks at again: the full text of every approval card (also
after it was used, denied or expired), the text the owner approved for the public lab (until the next share request), the
parameters and results of every job, every task's request, goal, plan, result and notes, and every command he typed or JARVIS
answered. This module bounds that, with one explicit, configurable policy:

  what                          when                                         what is left
  approvals (card text)         decided/used, or expired, + grace (24 h)      tool, op, lane, requester, 12-char digest, and per field
                                                                              its length and a salted hash prefix; "text removed" note
  lab_share_pending             the share is used, denied or expired          nothing (the row is deleted)
  idempotency (kept replies)    24 hours after the request                    nothing (the row is deleted)
  jobs (params, result)         final + JOBS_DAYS (14), or beyond the newest  ids, numbers and booleans stay; text is replaced by its
                                JOBS_KEEP (2000) final jobs                    length and hash prefix
  tasks + task_events           final + TASKS_DAYS (30), or beyond the        ids, state, owner, lane, times, counters stay; request,
                                newest TASKS_KEEP (300) final tasks            goal, step, result, plan, criteria, evidence, notes become
                                                                              "[text removed ..., N characters, id xxxx]"
  owner_commands                finished + COMMANDS_DAYS (30), or beyond the   the same for text, reply and error
                                newest COMMANDS_KEEP (500)

Never touched: anything still pending, running or open (an undecided approval keeps its whole card: the owner must read all of
it before he approves), the audit chain (append-only; see below), the memory store (the owner's own, with its own forget).
The hash is salted with a random value kept in owner_config, so a removed sentence cannot be recovered by guessing it.

Audit: old audit rows cannot be rewritten (that would break the hash chain and the anchors); they stay as they were written. New
rows are metadata-only (policy.Action.describe: memory text, task goals, pc.open_* names and paths, the memory scope label).

Runs: at kernel start (llm_proxy.serve), then about once an hour from the owner tick, and on demand with
`python3 -m jarvis_kernel retention --run`. Each run that changed something writes one audit row "retention_run" with the counts.
It is idempotent: a stub is never stubbed again. After a run that changed rows it checkpoints and truncates the WAL, and SQLite's
secure_delete is on, so removed text does not stay in freed pages or the write-ahead log.

Settings (owner_config 'retention', JSON; `retention --show`, `--set key=value`, `--off`, `--on`):
  enabled (true), approvals_grace_hours (24), jobs_days (14), tasks_days (30), commands_days (30), jobs_keep (2000), tasks_keep (300),
  commands_keep (500). The environment variable JARVIS_RETENTION=off also turns the whole thing off. Off means nothing is removed.
"""
import hashlib
import hmac
import json
import os
import re
import secrets
import sqlite3
import time

DEFAULTS = {"enabled": True, "approvals_grace_hours": 24, "jobs_days": 14, "tasks_days": 30, "commands_days": 30,
            "jobs_keep": 2000, "tasks_keep": 300, "commands_keep": 500,
            # re-check F (2026-10-19): stores that held private text with no limit. Longer windows than the task stores: these are the
            # owner's long-lived definitions and records, and only FINISHED ones are touched.
            "projectfix_days": 60, "connector_days": 90, "definitions_days": 90}
SHORT_MIN = 24                      # text shorter than this keeps NO hash prefix at all (short text can be guessed; see the threat model)
KEY_FILE = "retention.key"          # HMAC key for the hash prefixes, in its own 0600 file beside (not inside) state.db
MARK = "[text removed"
BATCH = 500
SWEEP_EVERY = 3600
JOB_FINAL = ("done", "failed", "expired", "cancelled")
TASK_FINAL = ("completed", "failed", "cancelled")
_ID12 = re.compile(r"^[0-9a-f]{12}$")
_ID = re.compile(r"^[0-9a-f]{12,16}$")
_SAFE_REASON = re.compile(r"^(rule [A-Za-z0-9_.*|-]+|withdrawn: [a-z ]{1,60}|no rule: fail closed)$")


def settings(db) -> dict:
    out = dict(DEFAULTS)
    try:
        r = db.execute("SELECT value FROM owner_config WHERE key='retention'").fetchone()
        if r:
            for k, v in (json.loads(r[0]) or {}).items():
                if k in DEFAULTS and type(v) is type(DEFAULTS[k]):
                    out[k] = v
    except (sqlite3.OperationalError, ValueError, TypeError, AttributeError):
        pass
    if os.environ.get("JARVIS_RETENTION", "").strip().lower() in ("off", "0", "false", "no"):
        out["enabled"] = False
    return out


def save_settings(db, changes: dict) -> dict:
    db.execute("CREATE TABLE IF NOT EXISTS owner_config(key TEXT PRIMARY KEY, value TEXT)")
    cur = {}
    try:
        r = db.execute("SELECT value FROM owner_config WHERE key='retention'").fetchone()
        cur = dict(json.loads(r[0])) if r else {}
    except (ValueError, TypeError):
        cur = {}
    for k, v in changes.items():
        if k not in DEFAULTS:
            raise ValueError(f"unknown setting {k}")
        if type(v) is not type(DEFAULTS[k]) or (not isinstance(v, bool) and v < 0):
            raise ValueError(f"{k} must be {'true or false' if isinstance(DEFAULTS[k], bool) else 'a non-negative whole number'}")
        cur[k] = v
    db.execute("INSERT OR REPLACE INTO owner_config(key, value) VALUES ('retention', ?)", (json.dumps(cur),))
    return settings(db)


class _DryRun(Exception):
    pass


class Retention:
    def __init__(self, kernel, clock=time.time):
        self.k, self.clock, self.last = kernel, clock, None

    # ---- helpers
    def _salt(self) -> str:
        """The OLD per-database salt (kept only so earlier stubs stay readable; new ids no longer use it)."""
        if getattr(self, "_salt_cache", None):
            return self._salt_cache
        db = self.k.db
        db.execute("CREATE TABLE IF NOT EXISTS owner_config(key TEXT PRIMARY KEY, value TEXT)")
        r = db.execute("SELECT value FROM owner_config WHERE key='retention_salt'").fetchone()
        if not r:
            db.execute("INSERT OR IGNORE INTO owner_config(key, value) VALUES ('retention_salt', ?)", (secrets.token_hex(16),))
            r = db.execute("SELECT value FROM owner_config WHERE key='retention_salt'").fetchone()
        self._salt_cache = r[0]
        return r[0]

    def _key(self):
        """The HMAC key for hash prefixes: a random 32-byte value in <data dir>/retention.key (mode 0600), NOT in state.db, so a
        copy of state.db alone (a backup, a leaked file) cannot be used to test guesses against the prefixes. None when the file
        can be neither read nor created: then no prefix is written at all (fail closed)."""
        if getattr(self, "_key_cache", None) is not None:
            return self._key_cache or None
        path = os.path.join(str(self.k.dir), KEY_FILE)
        key = b""
        try:
            try:
                if getattr(self, "_dry", False):                 # --dry-run changes nothing, not even by creating the key file
                    raise FileExistsError
                fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                with os.fdopen(fd, "w") as fh:
                    fh.write(secrets.token_hex(32))
            except FileExistsError:
                pass
            with open(path, "r") as fh:
                key = bytes.fromhex(fh.read().strip())
            if len(key) < 16:
                key = b""
        except (OSError, ValueError):
            key = b""
        if not getattr(self, "_dry", False):
            self._key_cache = key
        return key or None

    def _fp(self, text: str) -> str:
        """A 12-character keyed fingerprint (HMAC-SHA256), or '' for short text or when there is no key."""
        text = "" if text is None else str(text)
        key = self._key()
        if key is None or len(text) < SHORT_MIN:
            return ""
        return hmac.new(key, text.encode("utf-8", "surrogatepass"), hashlib.sha256).hexdigest()[:12]

    def _text(self, s, why: str) -> str:
        s = "" if s is None else str(s)
        if s == "" or s.startswith(MARK):
            return s
        fp = self._fp(s)
        return f"{MARK} {why}, {len(s)} characters" + (f", id {fp}]" if fp else "]")

    def _leaves(self, x, path, out):
        if isinstance(x, str):
            if x:
                out[path] = {"chars": len(x), **({"id": self._fp(x)} if self._fp(x) else {})}
        elif isinstance(x, dict):
            for k, v in x.items():
                self._leaves(v, f"{path}.{k}" if path else str(k), out)
        elif isinstance(x, (list, tuple)):
            for i, v in enumerate(x):
                self._leaves(v, f"{path}.{i}" if path else str(i), out)
        return out

    def _stub(self, v) -> dict:
        """What stays of one removed value: its length, and a keyed fingerprint only when it is long enough to be safe."""
        t = v if isinstance(v, str) else json.dumps(v, default=str, sort_keys=True)
        fp = self._fp(t)
        return {"_chars": len(t), **({"_id": fp} if fp else {})}

    def _keep_scalars(self, d) -> dict:
        """A JSON object reduced to what carries no private text: booleans, numbers, ids (12-16 hex characters); anything else is
        replaced by its length (and a keyed fingerprint when long)."""
        out = {}
        for k, v in (d.items() if isinstance(d, dict) else []):
            if v is None or isinstance(v, (bool, int, float)) or (isinstance(v, str) and _ID.match(v)):
                out[str(k)[:40]] = v
            else:
                out[str(k)[:40]] = self._stub(v)
        out["_purged"] = True
        return out

    # ---- the stores
    def approvals(self, now: float, cfg: dict) -> int:
        grace = cfg["approvals_grace_hours"] * 3600
        rows = self.k.db.execute(
            "SELECT id, action_hash, action, state FROM approvals WHERE action NOT LIKE '%\"purged\": true%' AND "
            "((state IN ('used','denied') AND COALESCE(decided, expires) < ?) OR (state IN ('pending','approved') AND expires < ?)) LIMIT ?",
            (now - grace, now - grace, BATCH)).fetchall()
        n = 0
        for aid, h, action, state in rows:
            try:
                act = json.loads(action)
                act = act if isinstance(act, dict) else {}
            except (TypeError, ValueError):
                act = {}
            body = {k: v for k, v in act.items() if k not in ("tool", "op", "lane", "requester", "invisible", "invisible_chars", "has_invisible")}
            fields = self._leaves(body, "", {})
            total = sum(f["chars"] for f in fields.values())
            why = "after use" if state == "used" else "after it was denied" if state == "denied" else "after it expired"
            stub = {"tool": str(act.get("tool", ""))[:40], "op": str(act.get("op", ""))[:40], "lane": str(act.get("lane", ""))[:12],
                    "target": f"[text removed {why}, {len(str(act.get('target', '')))} characters]", "params": {},
                    "purged": True, "text_removed": f"text removed {why}, {total} characters", "digest": (h or "")[:12],
                    "fields": dict(list(fields.items())[:40])}
            if act.get("requester"):
                stub["requester"] = str(act["requester"])[:80]
            if total < SHORT_MIN:                       # a prefix of the plain digest of a SHORT action could be tested by guessing
                stub["digest"] = ""
            reason = self.k.db.execute("SELECT reason FROM approvals WHERE id=?", (aid,)).fetchone()[0]
            if reason and not _SAFE_REASON.match(reason):      # a policy reason can quote a host or a name from the card
                reason = f"{MARK} {why}, {len(reason)} characters]"
            self.k.db.execute("UPDATE approvals SET action=?, action_hash=?, reason=? WHERE id=?",
                              (json.dumps(stub), stub["digest"], reason, aid))
            n += 1
        return n

    def shares(self, now: float, cfg: dict) -> int:
        try:
            cur = self.k.db.execute(
                "DELETE FROM lab_share_pending WHERE approval_id NOT IN (SELECT id FROM approvals WHERE state IN ('pending','approved') "
                "AND expires >= ?)", (now,))
        except sqlite3.OperationalError:                 # the table is created by the model endpoint: none yet, nothing kept
            return 0
        return cur.rowcount

    def replies(self, now: float, cfg: dict) -> int:
        """Kept idempotency replies (they can hold a reply's text) are deleted after their 24 hours even if no keyed request came."""
        try:
            return self.k.db.execute("DELETE FROM idempotency WHERE created < ?", (now - 24 * 3600,)).rowcount
        except sqlite3.OperationalError:
            return 0

    def jobs(self, now: float, cfg: dict) -> int:
        cutoff = now - cfg["jobs_days"] * 86400
        marks = ",".join("?" * len(JOB_FINAL))
        try:
            rows = self.k.db.execute(
                f"SELECT id, params, result FROM jobs WHERE state IN ({marks}) AND (COALESCE(finished, expires, created) < ? OR id IN "
                f"(SELECT id FROM jobs WHERE state IN ({marks}) ORDER BY COALESCE(finished, expires, created) DESC LIMIT -1 OFFSET ?)) "
                f"AND (params NOT LIKE '%\"_purged\": true%') LIMIT ?", (*JOB_FINAL, cutoff, *JOB_FINAL, cfg["jobs_keep"], BATCH)).fetchall()
        except sqlite3.OperationalError:
            return 0
        n = 0
        for jid, params, result in rows:
            try:
                p = json.loads(params or "{}")
            except ValueError:
                p = {}
            keep = {}
            for k, v in (p.items() if isinstance(p, dict) else []):
                if v is None or isinstance(v, (bool, int, float)) or (isinstance(v, str) and _ID12.match(v)):
                    keep[k] = v
                else:
                    keep[k] = self._stub(v)
            keep["_purged"] = True
            res = None
            if result:
                try:
                    r = json.loads(result)
                except ValueError:
                    r = None
                res = json.dumps({"_purged": True, "chars": len(result), **({"id": self._fp(result)} if self._fp(result) else {}),
                                  **({"ok": r["ok"]} if isinstance(r, dict) and isinstance(r.get("ok"), bool) else {})})
            self.k.db.execute("UPDATE jobs SET params=?, result=? WHERE id=?", (json.dumps(keep), res, jid))
            n += 1
        return n

    def tasks(self, now: float, cfg: dict) -> int:
        cutoff = now - cfg["tasks_days"] * 86400
        marks = ",".join("?" * len(TASK_FINAL))
        try:
            rows = self.k.db.execute(
                f"SELECT id, request, goal, step, result, verification FROM tasks WHERE state IN ({marks}) AND goal NOT LIKE '{MARK}%' AND "
                f"(updated < ? OR id IN (SELECT id FROM tasks WHERE state IN ({marks}) ORDER BY updated DESC LIMIT -1 OFFSET ?)) LIMIT ?",
                (*TASK_FINAL, cutoff, *TASK_FINAL, cfg["tasks_keep"], BATCH)).fetchall()
        except sqlite3.OperationalError:
            return 0
        n = 0
        for tid, request, goal, step, result, verification in rows:
            try:
                v = json.loads(verification or "{}")
            except ValueError:
                v = {}
            keepv = {k: x for k, x in (v.items() if isinstance(v, dict) else []) if isinstance(x, (bool, int, float))
                     or k in ("verifier", "assurance", "outcome", "method") and isinstance(x, str) and len(x) < 80}
            keepv["purged"] = True
            why = "after the retention window"
            self.k.db.execute(
                "UPDATE tasks SET request=?, goal=?, step=?, result=?, plan='[]', criteria='[]', evidence='[]', verification=? WHERE id=?",
                (self._text(request, why), self._text(goal, why) or f"{MARK} {why}, 0 characters]", self._text(step, why),
                 self._text(result, why), json.dumps(keepv), tid))
            for eid, note in self.k.db.execute("SELECT id, note FROM task_events WHERE task=?", (tid,)).fetchall():
                self.k.db.execute("UPDATE task_events SET note=? WHERE id=?", (self._text(note, why), eid))
            n += 1
        return n

    def commands(self, now: float, cfg: dict) -> int:
        cutoff = now - cfg["commands_days"] * 86400
        try:
            rows = self.k.db.execute(
                "SELECT id, text, reply, error FROM owner_commands WHERE state!='running' AND text NOT LIKE ? AND "
                "(ts < ? OR id IN (SELECT id FROM owner_commands WHERE state!='running' ORDER BY ts DESC LIMIT -1 OFFSET ?)) LIMIT ?",
                (MARK + "%", cutoff, cfg["commands_keep"], BATCH)).fetchall()
        except sqlite3.OperationalError:
            return 0
        n = 0
        why = "after the retention window"
        for cid, text, reply, error in rows:
            self.k.db.execute("UPDATE owner_commands SET text=?, reply=?, error=? WHERE id=?",
                              (self._text(text, why) or f"{MARK} {why}, 0 characters]", self._text(reply, why), self._text(error, why), cid))
            n += 1
        return n


    def reasons(self, now: float, cfg: dict) -> int:
        """Stubs written before the reason was handled: a policy reason that quotes a host or a name is replaced by its length."""
        rows = self.k.db.execute(
            "SELECT id, reason FROM approvals WHERE action LIKE '%\"purged\": true%' AND reason IS NOT NULL AND reason != '' AND "
            "reason NOT LIKE 'rule %' AND reason NOT LIKE 'withdrawn:%' AND reason NOT LIKE ? AND reason != 'no rule: fail closed' LIMIT ?",
            (MARK + "%", BATCH)).fetchall()
        n = 0
        for aid, reason in rows:
            if _SAFE_REASON.match(reason):
                continue
            self.k.db.execute("UPDATE approvals SET reason=? WHERE id=?", (f"{MARK} after the retention window, {len(reason)} characters]", aid))
            n += 1
        return n

    def projectfix(self, now: float, cfg: dict) -> int:
        """Finished project-fix procedures: their record holds the lab's instructions, file names and test output."""
        cutoff = now - cfg["projectfix_days"] * 86400
        try:
            rows = self.k.db.execute("SELECT task, data FROM projectfix WHERE stage IN ('done','failed') AND updated < ? AND "
                                     "(data IS NULL OR data NOT LIKE '%\"_purged\": true%') LIMIT ?", (cutoff, BATCH)).fetchall()
        except sqlite3.OperationalError:
            return 0
        for tid, data in rows:
            try:
                d = json.loads(data or "{}")
            except ValueError:
                d = {}
            self.k.db.execute("UPDATE projectfix SET data=? WHERE task=?", (json.dumps(self._keep_scalars(d)), tid))
        return len(rows)

    def connector_calls(self, now: float, cfg: dict) -> int:
        """Connector call rows keep names, risk, lane, decision, timing; the digest of the ARGUMENTS goes (a short argument can be
        guessed from an unkeyed digest)."""
        try:
            return self.k.db.execute("UPDATE connector_calls SET args_digest=NULL WHERE ts < ? AND args_digest IS NOT NULL",
                                     (now - cfg["connector_days"] * 86400,)).rowcount
        except sqlite3.OperationalError:
            return 0

    def definitions(self, now: float, cfg: dict) -> int:
        """Finished automations (completed or expired) and terminated agent definitions: the definition's text goes, the row, its
        state and its run history (ids, status, times) stay. A finished one cannot be resumed or run, so nothing depends on the text."""
        cutoff, n, why = now - cfg["definitions_days"] * 86400, 0, "after the retention window"
        try:
            rows = self.k.db.execute("SELECT id, name, action, event_spec FROM automations WHERE state='completed' AND COALESCE(updated, created, 0) < ? "
                                     "AND action NOT LIKE '%\"_purged\": true%' LIMIT ?", (cutoff, BATCH)).fetchall()
            for aid, name, action, spec in rows:
                try:
                    a = json.loads(action or "{}")
                except ValueError:
                    a = {}
                self.k.db.execute("UPDATE automations SET name=?, action=?, event_spec=? WHERE id=?",
                                  (self._text(name, why) or f"{MARK} {why}, 0 characters]", json.dumps(self._keep_scalars(a)),
                                   json.dumps({"_purged": True}) if spec else spec, aid))
                self.k.db.execute("UPDATE automation_runs SET detail=? WHERE automation_id=? AND detail IS NOT NULL AND detail NOT LIKE ?",
                                  (f"{MARK} {why}]", aid, MARK + "%"))
                n += 1
        except sqlite3.OperationalError:
            pass
        try:
            rows = self.k.db.execute("SELECT id, purpose, instructions, criteria, workspace FROM agent_defs WHERE state='terminated' AND "
                                     "COALESCE(updated, created, 0) < ? AND instructions NOT LIKE ? LIMIT ?", (cutoff, MARK + "%", BATCH)).fetchall()
            for aid, purpose, instr, crit, ws in rows:
                self.k.db.execute("UPDATE agent_defs SET purpose=?, instructions=?, criteria='[]', workspace=? WHERE id=?",
                                  (self._text(purpose, why), self._text(instr, why), self._text(ws, why), aid))
                n += 1
        except sqlite3.OperationalError:
            pass
        return n

    # ---- running it
    COUNT_KEYS = ("approvals", "lab_shares", "idempotency", "jobs", "tasks", "commands", "reasons", "projectfix", "connector_calls", "definitions")

    def _step(self, now, cfg):
        return {"approvals": self.approvals(now, cfg), "lab_shares": self.shares(now, cfg), "idempotency": self.replies(now, cfg),
                "jobs": self.jobs(now, cfg), "tasks": self.tasks(now, cfg), "commands": self.commands(now, cfg),
                "reasons": self.reasons(now, cfg), "projectfix": self.projectfix(now, cfg),
                "connector_calls": self.connector_calls(now, cfg), "definitions": self.definitions(now, cfg)}

    def sweep(self, source: str = "tick", now: float = None, dry_run: bool = False) -> dict:
        """Apply the policy once. Returns the counts ({} keys all zero when nothing was due). Safe to call at any time.
        dry_run: count what WOULD change, then roll everything back (no row, no audit entry, no key file, no WAL checkpoint)."""
        now = self.clock() if now is None else now
        cfg = settings(self.k.db)
        if not cfg["enabled"]:
            return {"enabled": False}
        db = self.k.db
        counts = dict.fromkeys(self.COUNT_KEYS, 0)
        if dry_run:
            self._dry = True
            try:
                try:
                    with db.tx():
                        for _ in range(40):
                            step = self._step(now, cfg)
                            for k, v in step.items():
                                counts[k] += v
                            if max(step["approvals"], step["jobs"], step["tasks"], step["commands"], step["reasons"], step["projectfix"],
                                   step["definitions"]) < BATCH:
                                break
                        raise _DryRun()
                except _DryRun:
                    pass
            finally:
                self._dry = False
            return {"enabled": True, "dry_run": True, **counts}
        self.last = now
        db.execute("PRAGMA secure_delete=ON")
        for _ in range(40):                              # a big backlog goes in batches; each batch is one transaction
            with db.tx():
                step = self._step(now, cfg)
            for k, v in step.items():
                counts[k] += v
            if max(step["approvals"], step["jobs"], step["tasks"], step["commands"], step["reasons"], step["projectfix"], step["definitions"]) < BATCH:
                break
        if any(counts.values()):
            self.k.audit.append("retention_run", {"source": source, **counts})
            db.execute("PRAGMA wal_checkpoint(TRUNCATE)")     # the old text must not stay in the write-ahead log
        return {"enabled": True, **counts}

    def maybe_sweep(self, source: str = "tick") -> dict:
        now = self.clock()
        if self.last is not None and 0 <= now - self.last < SWEEP_EVERY:
            return {}
        return self.sweep(source, now)
