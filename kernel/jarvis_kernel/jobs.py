"""Job queue for nodes. A job is a fixed, pre-defined kind of work (no free-form commands) that the kernel has authorized.

Flow: owner asks (Telegram /check) -> kernel authorizes -> job is queued for a node -> the node polls, runs it from its own
whitelist, posts the result -> the Telegram bridge reports it once. Jobs expire if the node is offline too long.
"""
import json
import re
import secrets
import time

JOB_TTL = 1800          # a queued job waits at most 30 minutes for the node to come back
RUN_TTL = 1800          # a job that starts but never reports (laptop slept, node died) is failed after 30 minutes
NAME = re.compile(r"^[A-Za-z0-9_.-]{1,40}$")
KINDS = {
    # kind: (policy tool, policy op) the kernel checks before queueing
    "project_check": ("tests", "run"),
}


class JobError(Exception):
    pass


class Jobs:
    def __init__(self, kernel):
        self.k = kernel
        kernel.db.execute(
            "CREATE TABLE IF NOT EXISTS jobs(id TEXT PRIMARY KEY, node TEXT, kind TEXT, params TEXT, state TEXT, created REAL, expires REAL, "
            "result TEXT, notified INT DEFAULT 0, started REAL)")
        try:   # older databases were created without the started column
            kernel.db.execute("ALTER TABLE jobs ADD COLUMN started REAL")
        except Exception:
            pass

    def create(self, node: str, kind: str, params: dict) -> str:
        from .policy import Action
        if kind not in KINDS:
            raise JobError("unknown job kind")
        if not isinstance(params, dict) or set(params) != {"project"} or not NAME.match(str(params.get("project", ""))):
            raise JobError("bad job parameters")
        tool, op = KINDS[kind]
        d = self.k.authorize(Action(tool, op, params["project"]))        # raises Killed when the kill switch is on
        if not d.allowed:
            raise JobError("refused by policy: " + d.reason)
        jid = secrets.token_hex(6)
        now = time.time()
        self.k.db.execute("INSERT INTO jobs(id,node,kind,params,state,created,expires) VALUES (?,?,?,?,?,?,?)",
                          (jid, node, kind, json.dumps(params), "queued", now, now + JOB_TTL))
        self.k.audit.append("job_queued", {"id": jid, "kind": kind, "node": node})
        return jid

    def next_for(self, node: str):
        """Hand the oldest queued, unexpired job to the node (atomically marks it running)."""
        now = time.time()
        self.k.db.execute("UPDATE jobs SET state='expired' WHERE state='queued' AND expires<?", (now,))
        for jid, kind, params in self.k.db.execute("SELECT id, kind, params FROM jobs WHERE node=? AND state='queued' ORDER BY created LIMIT 3", (node,)).fetchall():
            cur = self.k.db.execute("UPDATE jobs SET state='running', started=? WHERE id=? AND state='queued'", (now, jid))
            if cur.rowcount == 1:
                self.k.audit.append("job_started", {"id": jid, "node": node})
                return {"id": jid, "kind": kind, "params": json.loads(params)}
        return None

    def finish(self, node: str, jid: str, result: dict) -> bool:
        blob = json.dumps(result, default=str)
        if len(blob) > 60000:
            result, blob = {"ok": False, "error": "result too large"}, '{"ok": false, "error": "result too large"}'
        cur = self.k.db.execute("UPDATE jobs SET state=?, result=? WHERE id=? AND node=? AND state='running'",
                                ("done" if result.get("ok") else "failed", blob, jid, node))
        self.k.audit.append("job_finished", {"id": jid, "ok": bool(result.get("ok")), "accepted": cur.rowcount == 1})
        return cur.rowcount == 1

    def to_report(self):
        """Finished (or expired) jobs the owner has not been told about yet: [(id, kind, state, params, result)]."""
        now = time.time()
        self.k.db.execute("UPDATE jobs SET state='expired' WHERE state='queued' AND expires<?", (now,))
        stuck = self.k.db.execute("UPDATE jobs SET state='failed', result=? WHERE state='running' AND started IS NOT NULL AND started<?",
                                  (json.dumps({"ok": False, "error": "the laptop started the job but never reported a result (it may have gone to sleep or lost power)"}), now - RUN_TTL))
        if stuck.rowcount:
            self.k.audit.append("job_stuck_failed", {"count": stuck.rowcount})
        rows = self.k.db.execute("SELECT id, kind, state, params, result FROM jobs WHERE notified=0 AND state IN ('done','failed','expired')").fetchall()
        return [(i, k, s, json.loads(p), json.loads(r) if r else None) for i, k, s, p, r in rows]

    def mark_notified(self, jid: str):
        self.k.db.execute("UPDATE jobs SET notified=1 WHERE id=?", (jid,))


def report_text(project: str, r: dict) -> str:
    """Plain-text report. Every number comes from the result dict (checked by a test with the same number checker as /analyze)."""
    if not r or not r.get("ok"):
        return f"Project check for {project} failed to run: {(r or {}).get('error', 'no result')}."
    g, t = r["git"], r.get("tests") or {}
    lines = [f"Project {project}: branch {g['branch']}, {g['dirty_files']} changed file(s), {g['ahead']} commit(s) ahead and {g['behind']} behind its upstream."]
    if g.get("last_commits"):
        lines.append("Latest commits: " + "; ".join(g["last_commits"]))
    if t.get("ran"):
        verdict = "PASSED" if t["exit_code"] == 0 else "FAILED"
        lines.append(f"Tests {verdict}: exit code {t['exit_code']} in {t['seconds']} s. {t.get('summary', '')}".strip())
        if t["exit_code"] != 0 and t.get("tail"):
            lines.append("Last output lines:\n" + "\n".join(t["tail"]))
    else:
        lines.append("Tests were not run: " + t.get("why", "no test command configured") + ".")
    n = t.get("files_changed_by_tests", 0) if t.get("ran") else 0
    lines.append((f"Running the tests added or changed {n} file(s) in the working tree." if n else "The test run left the working tree unchanged.")
                 + " I changed nothing myself; any fix needs your approval.")
    return "\n".join(lines)
