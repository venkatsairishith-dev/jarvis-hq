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
# Output files: a plain name with a known text extension (no folders, no hidden files). The node re-checks the same rule.
OUTPUT_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 _.-]{0,79}\.(md|txt|html|json|csv|log|py)$")
MAX_OUTPUT = 200_000    # characters per saved output
UI_OPS = {"launch", "click", "type"}
PC_OPS = {"open_app", "open_folder", "open_file", "list_folder"}   # "open Spotify", "open the X folder in Downloads"
KINDS = {
    # kind: (policy tool, policy op or None when the op comes from the job's params)
    "project_check": ("tests", "run"),
    "output_save": ("output", "save"),
    "output_show": ("output", "show"),
    "ui_action": ("ui", None),
    "pc_open": ("pc", None),
}


class JobError(Exception):
    pass


def _check_params(kind: str, params: dict):
    """Each kind accepts exactly its own fields with safe values; anything else is refused before policy is asked."""
    if not isinstance(params, dict):
        raise JobError("bad job parameters")
    keys = set(params)
    if kind == "project_check":
        ok = keys == {"project"} and NAME.match(str(params.get("project", "")))
    elif kind == "output_save":
        ok = (keys == {"agent", "name", "content"} and NAME.match(str(params["agent"])) and OUTPUT_NAME.match(str(params["name"]))
              and ".." not in params["name"] and isinstance(params["content"], str) and len(params["content"]) <= MAX_OUTPUT)
    elif kind == "output_show":
        ok = keys == {"agent", "name"} and NAME.match(str(params["agent"])) and OUTPUT_NAME.match(str(params["name"])) and ".." not in params["name"]
    elif kind == "ui_action":
        ok = (keys == {"agent", "op", "target", "text"} and NAME.match(str(params["agent"])) and params["op"] in UI_OPS
              and isinstance(params["target"], str) and 0 < len(params["target"]) <= 120 and isinstance(params["text"], str) and len(params["text"]) <= 500)
    elif kind == "pc_open":
        ok = (keys == {"agent", "op", "target", "path"} and NAME.match(str(params["agent"])) and params["op"] in PC_OPS
              and isinstance(params["target"], str) and 0 < len(params["target"].strip()) <= 80
              and isinstance(params["path"], str) and len(params["path"]) <= 200
              and not any(c in params["target"] + params["path"] for c in "\r\n\0"))
    else:
        ok = False
    if not ok:
        raise JobError("bad job parameters")


def action_for(kind: str, params: dict, lane: str = "private", untrusted: bool = False):
    """The policy Action a job stands for. Output content is NOT part of it (its words must not trip path rules);
    the file name is, so a secret-looking name is still refused."""
    from .policy import Action
    tool, op = KINDS[kind]
    if kind == "project_check":
        return Action(tool, op, params["project"])
    if kind in ("output_save", "output_show"):
        return Action(tool, op, f"outputs/{params['agent']}/{params['name']}", {}, lane, untrusted)
    if kind == "pc_open":
        return Action("pc", params["op"], params["target"], {"path": params["path"]} if params["path"] else {}, lane, untrusted)
    return Action("ui", params["op"], params["target"], {"text": params["text"]} if params["text"] else {}, lane, untrusted)


class Jobs:
    def __init__(self, kernel):
        self.k = kernel
        kernel.db.execute(
            "CREATE TABLE IF NOT EXISTS jobs(id TEXT PRIMARY KEY, node TEXT, kind TEXT, params TEXT, state TEXT, created REAL, expires REAL, "
            "result TEXT, notified INT DEFAULT 0, started REAL)")
        for col in ("started REAL", "owner TEXT", "finished REAL"):   # older databases were created without these columns
            try:
                kernel.db.execute("ALTER TABLE jobs ADD COLUMN " + col)
            except Exception:
                pass

    def create(self, node: str, kind: str, params: dict, approval_id: str = None, owner: str = None, lane: str = "private") -> str:
        """Authorize, then queue. owner = the agent that asked (None = the human owner). Agent jobs are untrusted
        sessions, are reported to the agent (not to Telegram), and only that agent can read their result."""
        if kind not in KINDS:
            raise JobError("unknown job kind")
        _check_params(kind, params)
        a = action_for(kind, params, lane, untrusted=owner is not None)
        d = self.k.authorize(a, approval_id)        # raises Killed when the kill switch is on
        if not d.allowed:
            raise JobError("refused by policy: " + d.reason)
        jid = secrets.token_hex(6)
        now = time.time()
        self.k.db.execute("INSERT INTO jobs(id,node,kind,params,state,created,expires,owner,notified) VALUES (?,?,?,?,?,?,?,?,?)",
                          (jid, node, kind, json.dumps(params), "queued", now, now + JOB_TTL, owner, 1 if owner else 0))
        self.k.audit.append("job_queued", {"id": jid, "kind": kind, "node": node, "owner": owner or "owner"})
        return jid

    def get_for(self, owner: str, jid: str):
        """A job's state and result, only for the agent that created it."""
        now = time.time()
        self.k.db.execute("UPDATE jobs SET state='expired' WHERE state='queued' AND expires<?", (now,))
        r = self.k.db.execute("SELECT kind, state, result FROM jobs WHERE id=? AND owner=?", (jid, owner)).fetchone()
        if not r:
            return None
        return {"id": jid, "kind": r[0], "state": r[1], "result": json.loads(r[2]) if r[2] else None}

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
        cur = self.k.db.execute("UPDATE jobs SET state=?, result=?, finished=? WHERE id=? AND node=? AND state='running'",
                                ("done" if result.get("ok") else "failed", blob, time.time(), jid, node))
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
