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
UI_OPS = {"launch", "click", "type", "focus", "key", "scroll", "click_at"}
PC_OPS = {"open_app", "open_folder", "open_file", "list_folder"}   # "open Spotify", "open the X folder in Downloads"
# Neko's PC control (2026-10-16). Browser: the owner's EXISTING Chrome profiles, by alias; the node keeps the registry.
BROWSER_OPS = {"open": "open", "read": "snapshot", "tabs": "snapshot", "whoami": "snapshot", "find": "snapshot",
               "media": "snapshot", "play": "play", "click": "click", "type": "type", "press": "press"}
DIAG_OPS = {"processes", "app_errors", "crashes", "services", "disk", "network", "app_info", "window_text"}
REPAIR_OPS = {"restart_app", "edit_file", "restore", "install"}
URL = re.compile(r"^https?://[^\s\"'<>\\]{1,2000}$")
SAFE_TEXT = re.compile(r"^[^\r\n\0]*$")
KINDS = {
    # kind: (policy tool, policy op or None when the op comes from the job's params)
    "project_check": ("tests", "run"),
    "output_save": ("output", "save"),
    "output_show": ("output", "show"),
    "ui_action": ("ui", None),
    "pc_open": ("pc", None),
    "lab_run": ("lab", "dispatch"),        # an outcome task's work, run, verified and repaired by the lab runner
    # Observation of the owner's Windows desktop (2026-10-11): read-only, the node answers from its own Win32/UIA calls.
    "ui_windows": ("ui", "list_windows"),  # visible top-level windows: title, process, size, foreground
    "ui_snapshot": ("ui", "snapshot"),     # the accessibility tree of one window (names, types; never passwords)
    "screenshot": ("ui", "screenshot"),    # a PNG of the desktop, written to the owner's outputs folder (never sent inline)
    # Connectors (2026-10-11): one operation on a local MCP server the owner allowed on that machine (connectors.py).
    "connector_op": ("connector", None),
    # Neko (2026-10-16): the owner's existing Chrome profiles (Playwright MCP extension), read-only diagnostics, repairs
    # with a backup first, and what the PC is playing (Windows media session).
    "browser_ext": ("browser", None),
    "diag": ("diag", None),
    "repair": ("repair", None),
    "media_state": ("media", "state"),
}
CONNECTOR_OPS = {"discover", "test", "connect", "disconnect", "call", "status"}
RESULT_LIMITS = {"connector_op": 250_000}   # a tool list is larger than most results; every other kind keeps 60,000
LAB_AGENTS = {"hermes", "opencode", "goose"}
LAB_RUN_TTL = 2 * 3600                     # a lab run that never reports is failed after 2 h (task deadlines are shorter)


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
              and isinstance(params["target"], str) and 0 < len(params["target"]) <= 120 and isinstance(params["text"], str) and len(params["text"]) <= 500
              and (params["op"] != "key" or re.match(r"^[a-z0-9]+(\+[a-z0-9]+){0,3}$", params["text"]) is not None)
              and (params["op"] != "scroll" or params["text"] in ("up", "down"))
              and (params["op"] != "click_at" or re.match(r"^[A-Za-z0-9_.\- ]{1,80}\|\d{1,5},\d{1,5}$", params["target"]) is not None))
    elif kind == "browser_ext":
        op = params.get("op")
        ok = (keys == {"profile", "op", "url", "target", "text"} and op in BROWSER_OPS
              and re.match(r"^[a-z0-9][a-z0-9_-]{0,31}$", str(params["profile"])) is not None
              and isinstance(params["url"], str) and (params["url"] == "" or URL.match(params["url"]) is not None)
              and isinstance(params["target"], str) and len(params["target"]) <= 300 and SAFE_TEXT.match(params["target"]) is not None
              and isinstance(params["text"], str) and len(params["text"]) <= 500 and SAFE_TEXT.match(params["text"]) is not None
              and (op != "open" or params["url"] != "")
              and (op not in ("click", "type", "press", "play", "find") or params["target"] != "" or op == "press"))
    elif kind == "diag":
        ok = (keys == {"op", "target"} and params["op"] in DIAG_OPS and isinstance(params["target"], str)
              and re.match(r"^[A-Za-z0-9_. :()-]{0,80}$", params["target"]) is not None)
    elif kind == "repair":
        op, t, args = params.get("op"), params.get("target"), params.get("args")
        ok = keys == {"op", "target", "args"} and op in REPAIR_OPS and isinstance(t, str) and isinstance(args, dict)
        if ok and op == "restart_app":
            ok = re.match(r"^[A-Za-z0-9_.-]{1,40}$", t) is not None and args == {}
        elif ok and op == "edit_file":
            ok = (0 < len(t) <= 260 and SAFE_TEXT.match(t) is not None and set(args) == {"find", "replace"}
                  and all(isinstance(args[k], str) and len(args[k]) <= 4000 for k in args) and args["find"] != "")
        elif ok and op == "restore":
            ok = re.match(r"^\d{8}-\d{6}-[0-9a-f]{6}$", t) is not None and args == {}
        elif ok and op == "install":
            ok = re.match(r"^[A-Za-z0-9][A-Za-z0-9.+_-]{1,80}$", t) is not None and args == {}
    elif kind == "media_state":
        ok = keys == set()
    elif kind == "pc_open":
        ok = (keys == {"agent", "op", "target", "path"} and NAME.match(str(params["agent"])) and params["op"] in PC_OPS
              and isinstance(params["target"], str) and 0 < len(params["target"].strip()) <= 80
              and isinstance(params["path"], str) and len(params["path"]) <= 200
              and not any(c in params["target"] + params["path"] for c in "\r\n\0"))
    elif kind == "ui_windows":
        ok = keys == set()
    elif kind == "ui_snapshot":
        ok = keys == {"target"} and isinstance(params["target"], str) and re.match(r"^[A-Za-z0-9_.-]{0,40}$", params["target"]) is not None
    elif kind == "screenshot":
        ok = keys == set()
    elif kind == "connector_op":
        ok = (keys == {"connector", "op", "tool", "args", "risk", "public_ok", "digest", "timeout_s", "max_output"}
              and re.match(r"^[a-z][a-z0-9_-]{1,31}$", str(params["connector"])) and params["op"] in CONNECTOR_OPS
              and isinstance(params["tool"], str) and re.match(r"^[A-Za-z0-9_.-]{0,128}$", params["tool"])
              and isinstance(params["args"], dict) and len(json.dumps(params["args"], default=str)) <= 16384
              and params["risk"] in ("", "read", "write", "send") and isinstance(params["public_ok"], bool)
              and isinstance(params["digest"], str) and len(params["digest"]) <= 64
              and isinstance(params["timeout_s"], int) and 3 <= params["timeout_s"] <= 120
              and isinstance(params["max_output"], int) and 500 <= params["max_output"] <= 60000
              and (params["op"] != "call" or (params["tool"] and params["risk"])))
    elif kind == "lab_run":
        ok = (keys == {"task", "agent", "instructions", "criteria", "max_attempts", "timeout_s"} and re.match(r"^[0-9a-f]{12}$", str(params["task"]))
              and params["agent"] in LAB_AGENTS and isinstance(params["instructions"], str) and 0 < len(params["instructions"]) <= 8000
              and isinstance(params["criteria"], list) and 0 < len(params["criteria"]) <= 20 and all(isinstance(c, str) for c in params["criteria"])
              and isinstance(params["max_attempts"], int) and 1 <= params["max_attempts"] <= 5
              and isinstance(params["timeout_s"], int) and 120 <= params["timeout_s"] <= 5400)
    else:
        ok = False
    if not ok:
        raise JobError("bad job parameters")


def action_for(kind: str, params: dict, lane: str = "private", untrusted: bool = False, owner_direct: bool = False):
    """The policy Action a job stands for. Output content is NOT part of it (its words must not trip path rules);
    the file name is, so a secret-looking name is still refused. owner_direct: the owner asked for it on the owner plane."""
    a = _action_for(kind, params, lane, untrusted)
    a.owner_direct = bool(owner_direct) and not untrusted
    return a


def _action_for(kind: str, params: dict, lane: str, untrusted: bool):
    from .policy import Action
    tool, op = KINDS[kind]
    if kind == "browser_ext":                 # the profile is part of it: an approval for one account is not for another
        p = {"profile": params["profile"]}
        if params["text"]:
            p["text"] = params["text"]
        return Action("browser", BROWSER_OPS[params["op"]], params["url"] or params["target"] or params["op"],
                      dict(p, element=params["target"]) if params["url"] and params["target"] else p, lane, untrusted)
    if kind == "diag":
        return Action("diag", params["op"], params["target"] or params["op"], {}, lane, untrusted)
    if kind == "repair":                      # the edit itself is part of it: an approval binds to exactly this change
        return Action("repair", params["op"], params["target"], dict(params["args"]), lane, untrusted)
    if kind == "media_state":
        return Action("media", "state", "system", {}, lane, untrusted)
    if kind == "project_check":
        return Action(tool, op, params["project"])
    if kind in ("output_save", "output_show"):
        return Action(tool, op, f"outputs/{params['agent']}/{params['name']}", {}, lane, untrusted)
    if kind == "ui_windows":
        return Action("ui", "list_windows", "windows", {}, lane, untrusted)
    if kind == "ui_snapshot":
        return Action("ui", "snapshot", params["target"] or "foreground", {}, lane, untrusted)
    if kind == "screenshot":
        return Action("ui", "screenshot", "desktop", {}, lane, untrusted)
    if kind == "lab_run":                     # the instructions are not part of the action (their words must not trip path rules)
        return Action("lab", "dispatch", params["agent"], {"task": params["task"]}, lane, untrusted)
    if kind == "connector_op":
        if params["op"] == "call":            # the arguments ARE part of the action: an approval binds to exactly them
            return Action("connector", "call", f"{params['connector']}.{params['tool']}",
                          {"risk": params["risk"], "public_ok": params["public_ok"], "digest": params["digest"], "args": params["args"]},
                          lane, untrusted)
        return Action("connector", "manage", params["connector"], {}, lane, untrusted)
    if kind == "pc_open":
        return Action("pc", params["op"], params["target"], {"path": params["path"]} if params["path"] else {}, lane, untrusted)
    return Action("ui", params["op"], params["target"], {"text": params["text"]} if params["text"] else {}, lane, untrusted)


class Jobs:
    def __init__(self, kernel):
        self.k = kernel
        kernel.db.execute(
            "CREATE TABLE IF NOT EXISTS jobs(id TEXT PRIMARY KEY, node TEXT, kind TEXT, params TEXT, state TEXT, created REAL, expires REAL, "
            "result TEXT, notified INT DEFAULT 0, started REAL)")
        for col in ("started REAL", "owner TEXT", "finished REAL", "cancel_requested REAL"):   # older databases lack these columns
            try:
                kernel.db.execute("ALTER TABLE jobs ADD COLUMN " + col)
            except Exception:
                pass
        kernel.db.execute("CREATE INDEX IF NOT EXISTS jobs_node_state ON jobs(node, state, created)")
        kernel.db.execute("CREATE INDEX IF NOT EXISTS jobs_owner_kind ON jobs(owner, kind, state)")

    def create(self, node: str, kind: str, params: dict, approval_id: str = None, owner: str = None, lane: str = "private",
               owner_direct: bool = False) -> str:
        """Authorize, then queue. owner = the agent that asked (None = the human owner). Agent jobs are untrusted
        sessions, are reported to the agent (not to Telegram), and only that agent can read their result.
        owner_direct: the owner asked for exactly this on the owner plane (never for an agent's job)."""
        if kind not in KINDS:
            raise JobError("unknown job kind")
        _check_params(kind, params)
        a = action_for(kind, params, lane, untrusted=owner is not None, owner_direct=owner_direct and owner is None)
        d = self.k.authorize(a, approval_id)        # raises Killed when the kill switch is on
        if not d.allowed:
            raise JobError("refused by policy: " + d.reason)
        jid = secrets.token_hex(6)
        now = time.time()
        self.k.db.execute("INSERT INTO jobs(id,node,kind,params,state,created,expires,owner,notified) VALUES (?,?,?,?,?,?,?,?,?)",
                          (jid, node, kind, json.dumps(params), "queued", now, now + JOB_TTL, owner, 1 if owner else 0))
        self.k.audit.append("job_queued", {"id": jid, "kind": kind, "node": node, "owner": owner or "owner"})
        if approval_id:
            self.k.link_approval_job(approval_id, jid)          # so the owner can see what an approval actually did
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
        kind_row = self.k.db.execute("SELECT kind FROM jobs WHERE id=?", (jid,)).fetchone()
        if len(blob) > RESULT_LIMITS.get(kind_row[0] if kind_row else "", 60000):
            result, blob = {"ok": False, "error": "result too large"}, '{"ok": false, "error": "result too large"}'
        state = "done" if result.get("ok") else "cancelled" if result.get("cancelled") else "failed"
        with self.k.db.tx():                       # the job and the registry change together: nobody reads "done" with stale registry state
            cur = self.k.db.execute("UPDATE jobs SET state=?, result=?, finished=? WHERE id=? AND node=? AND state='running'",
                                    (state, blob, time.time(), jid, node))
            self.k.audit.append("job_finished", {"id": jid, "ok": bool(result.get("ok")), "accepted": cur.rowcount == 1})
            if cur.rowcount == 1 and kind_row and kind_row[0] == "connector_op":
                try:                               # the registry learns what the node found, in whichever process received it
                    from . import connectors
                    connectors.on_job_finished(self.k, jid, node, result)
                except Exception as e:
                    self.k.audit.append("connector_hook_failed", {"id": jid, "error": type(e).__name__})
        return cur.rowcount == 1

    def cancel(self, jid: str, owner: str = None) -> dict:
        """Stop a job (the safe direction: no approval). Queued: it never runs. Running: the node is told on its next
        report and kills what the job started; the job ends as 'cancelled' when the node confirms. owner None = the human
        owner (any job); an agent may cancel only its own."""
        now = time.time()
        q = "UPDATE jobs SET state='cancelled', finished=?, result=? WHERE id=? AND state='queued'" + ("" if owner is None else " AND owner=?")
        args = [now, json.dumps({"ok": False, "cancelled": True, "error": "cancelled before it started"}), jid] + ([] if owner is None else [owner])
        if self.k.db.execute(q, args).rowcount == 1:
            self.k.audit.append("job_cancelled", {"id": jid, "was": "queued"})
            return {"id": jid, "state": "cancelled"}
        q = "UPDATE jobs SET cancel_requested=? WHERE id=? AND state='running'" + ("" if owner is None else " AND owner=?")
        if self.k.db.execute(q, [now, jid] + ([] if owner is None else [owner])).rowcount == 1:
            self.k.audit.append("job_cancel_requested", {"id": jid})
            return {"id": jid, "state": "cancelling", "note": "the node stops it on its next report (a few seconds)"}
        r = self.k.db.execute("SELECT state FROM jobs WHERE id=?", (jid,)).fetchone()
        return {"id": jid, "state": r[0] if r else "unknown", "note": "nothing to cancel"}

    def cancels_for(self, node: str) -> list:
        """Running jobs on this node that the owner asked to stop (told to the node with every report)."""
        return [r[0] for r in self.k.db.execute(
            "SELECT id FROM jobs WHERE node=? AND state='running' AND cancel_requested IS NOT NULL", (node,)).fetchall()]

    def sweep(self, now=None):
        """Expire queued jobs nobody picked up and fail jobs a node started but never answered. Returns how many of
        each. Called by the kernel's own sweeper loop (it used to run only inside the Telegram bridge, so with Telegram
        off a stuck lab job blocked saves and re-dispatch until the task deadline)."""
        now = now or time.time()
        expired = self.k.db.execute("UPDATE jobs SET state='expired' WHERE state='queued' AND expires<?", (now,)).rowcount
        stuck = self.k.db.execute("UPDATE jobs SET state='failed', result=?, finished=? WHERE state='running' AND started IS NOT NULL AND "
                                  "((kind!='lab_run' AND started<?) OR (kind='lab_run' AND started<?))",
                                  (json.dumps({"ok": False, "error": "the node started the job but never reported a result (it may have gone to sleep or lost power)"}),
                                   now, now - RUN_TTL, now - LAB_RUN_TTL)).rowcount
        if stuck:
            self.k.audit.append("job_stuck_failed", {"count": stuck})
        return {"expired": expired, "stuck_failed": stuck}

    def fail_orphans(self, node: str, now=None, grace: float = 30.0) -> int:
        """A node that says it is idle cannot be running a job the kernel still shows as running: it restarted, or the
        worker died. Fail those at once (an honest, specific error) instead of after the 30 minute sweep. The grace period
        covers a job just handed over."""
        now = now or time.time()
        n = self.k.db.execute("UPDATE jobs SET state='failed', result=?, finished=? WHERE node=? AND state='running' AND started IS NOT NULL "
                              "AND started<? AND kind!='lab_run'",
                              (json.dumps({"ok": False, "error": "the node restarted or lost this job before it finished; nothing was reported"}),
                               now, node, now - grace)).rowcount
        if n:
            self.k.audit.append("job_orphaned_failed", {"node": node, "count": n})
        return n

    def to_report(self):
        """Finished (or expired) jobs the owner has not been told about yet: [(id, kind, state, params, result)]."""
        self.sweep()
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
