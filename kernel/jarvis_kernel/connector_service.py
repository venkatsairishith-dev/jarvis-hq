"""The paths that act on the connector registry: an agent's call, the owner's test, connect, disconnect and "try a read tool".

Kept apart from connectors.py (the registry and its records) so the whole authorization-and-execution path reads in one place:

  call_tool:  connector enabled? lane allowed? tool enabled, classified, unchanged since the owner enabled it? arguments
              well-formed, free of credentials, within the declared schema? under the hourly limit? -> policy decides from the
              OWNER'S risk class (read runs; write and send need an approval bound to the exact arguments; the rest is
              refused) -> run (a job on the laptop node for a local server, the kernel itself for a remote one) -> the
              result is scrubbed, capped and labelled untrusted -> the call is logged and audited without its content.
"""
import hashlib
import json
import os
import time

from . import mcpwire as W
from .connectors import ConnectorError, Connectors, WAIT_SECONDS
from .scrub import scrub


def _bearer(secret_ref: str):
    """The bearer token of a remote connector, read by reference from the kernel's secrets folder. "" = none configured,
    None = configured but unreadable."""
    if not secret_ref:
        return ""
    from . import gateway
    root = os.path.realpath(gateway.secrets_dir())
    full = os.path.realpath(os.path.join(root, secret_ref.replace("/", os.sep)))
    try:
        if os.path.commonpath([root, full]) != root or not os.path.isfile(full):
            return None
        with open(full, encoding="utf-8-sig") as fh:
            v = fh.read(4097).strip()
    except (OSError, ValueError):
        return None
    return v if 0 < len(v) <= 4096 else None


def _args_digest(args) -> str:
    return hashlib.sha256(json.dumps(args, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()[:16]


class ConnectorService(Connectors):
    """`jobs` is the kernel's Jobs; `runner` executes remote (https) operations and has mcphttp.run_op's signature."""

    def __init__(self, kernel, jobs, clock=time.time, runner=None, wait=WAIT_SECONDS):
        super().__init__(kernel, jobs, clock)
        from . import mcphttp
        self.runner = runner or mcphttp.run_op
        self.wait = wait

    def _params(self, c, op, tool="", args=None, risk="", digest=""):
        return {"connector": c["name"], "op": op, "tool": tool, "args": args or {}, "risk": risk, "public_ok": "public" in c["lanes"],
                "digest": digest, "timeout_s": c["timeout_s"], "max_output": c["max_output"]}

    def _run_https(self, c, params):
        bearer = _bearer(c["secret_ref"])
        if bearer is None:
            return {"ok": False, "op": params["op"], "error_class": "needs_setup", "error": "the secret file for this connector is missing", "ms": 0}
        return self.runner(params["op"], c["endpoint"], bearer or None, params["tool"], params["args"], params["digest"],
                           float(c["timeout_s"]), int(c["max_output"]))

    def _wait_job(self, jid, owner, seconds=None):
        end = self.clock() + (self.wait if seconds is None else seconds)
        while True:
            j = self.jobs.get_for(owner, jid)
            if j and j["state"] in ("done", "failed", "expired"):
                return j
            if self.clock() >= end:
                return j
            time.sleep(0.2)

    # ---- owner operations: test, connect, disconnect. Nothing is sent to the service beyond the protocol handshake and the tool list.
    def owner_op(self, name: str, op: str) -> dict:
        if op not in ("test", "connect", "disconnect"):
            raise ConnectorError("unknown operation")
        c = self._row(name)
        if not c:
            raise ConnectorError("no such connector", 404)
        if not c["enabled"] and op != "disconnect":
            raise ConnectorError("the connector is disabled: enable it first", 409)
        params = self._params(c, op)
        if c["transport"] == "https":
            if op == "disconnect":
                self.k.db.execute("UPDATE connectors SET state='stopped' WHERE name=?", (name,))
                self.k.audit.append("connector_disconnect", {"connector": name, "ok": True})
                return {"ok": True, "state": "stopped", "connector": self.get(name)}
            if self.k.killed():
                raise ConnectorError("the kill switch is on", 503)
            res = self._run_https(c, params)
            self.apply_discovery(name, res, "test" if op == "test" else "connect")
            return {"ok": bool(res.get("ok")), "error": res.get("error"), "error_class": res.get("error_class"), "ms": res.get("ms"),
                    "connector": self.get(name)}
        from .jobs import JobError
        from .store import Killed
        try:
            jid = self.jobs.create(c["node"], "connector_op", params, owner="owner", lane="private")
        except Killed:
            raise ConnectorError("the kill switch is on", 503) from None
        except JobError as e:
            raise ConnectorError(str(e)[:200], 403) from None
        j = self._wait_job(jid, "owner")
        if j is None or j["state"] not in ("done", "failed"):
            return {"pending": True, "job": jid, "connector": self.get(name),
                    "note": "the laptop node has not answered yet; the result is applied when it does"}
        r = j.get("result") or {}
        return {"ok": j["state"] == "done", "job": jid, "error": r.get("error"), "error_class": r.get("error_class"), "ms": r.get("ms"),
                "connector": self.get(name)}

    def stop_in_background(self, name: str):
        """Ask the node to stop a connector's server without waiting for it (used when a connector is removed)."""
        c = self._row(name)
        if not c or c["transport"] != "stdio":
            return False
        try:
            self.jobs.create(c["node"], "connector_op", self._params(c, "disconnect"), owner="owner", lane="private")
            return True
        except Exception:
            return False

    # ---- the call path, for agents and for the owner's "try a read tool"
    def call_tool(self, agent: str, lane: str, name: str, tool: str, args, approval_id: str = None, owner_try: bool = False):
        from .jobs import JobError, action_for
        from .store import Killed

        def refuse(why, code=403):
            return code, {"status": "refused", "reason": why}
        c = self._row(name)
        if not c or not c["enabled"] or lane not in c["lanes"]:
            return refuse("no such connector is available to this agent")
        t = next((x for x in self.tools(name) if x["tool"] == tool), None)
        if (t is None or not t["enabled"] or not t["present"] or t["changed"] or t["risk"] not in W.ENABLEABLE
                or not t["approved_digest"] or t["approved_digest"] != t["digest"]):
            self._log_call(connector=name, tool=str(tool)[:128], risk=(t or {}).get("risk"), agent=agent, lane=lane, decision="refused",
                           outcome="tool not enabled", ms=0, bytes=0, error_class="not_enabled", args_digest=None, job=None)
            self.k.audit.append("connector_call_refused", {"connector": name, "tool": str(tool)[:128], "agent": agent, "why": "not enabled"})
            return refuse("this tool is not enabled")
        if owner_try and t["risk"] != "read":
            return refuse("only read tools can be tried from here", 400)
        if not isinstance(args, dict):
            return refuse("arguments must be an object", 400)
        blob = json.dumps(args, default=str)
        if len(blob) > 16384:
            return refuse("arguments are too large (16 KB at most)", 413)
        clean, found, blocked = scrub(blob, deep=True)
        if blocked or found or clean != blob:
            return refuse("the arguments contain what looks like a credential or a private key; they were not sent", 400)
        problems = W.validate_args(t["schema"], args)
        if problems:
            return refuse("; ".join(problems)[:300], 400)
        if self.call_count_last_hour(name) >= c["max_calls_per_hour"]:
            return refuse(f"this connector's limit of {c['max_calls_per_hour']} calls per hour is reached", 429)
        params = self._params(c, "call", tool, args, t["risk"], t["digest"])
        try:
            action = action_for("connector_op", params, lane, untrusted=True)
            aid = str(approval_id or "") or None
            if not aid:
                d, aid = self.k.request(action)
                if d.denied:
                    return refuse(d.reason)
                if aid:
                    return 202, {"status": "needs_approval", "approval_id": aid, "level": d.name, "risk": t["risk"],
                                 "next": f"the owner approves {aid} in the JARVIS console (Command Center); then send the same call again "
                                         "with approval_id"}
            if not self.acquire(name):
                return refuse("this connector is busy with another call", 429)
            try:
                call_id = self._log_call(connector=name, tool=tool, risk=t["risk"], agent=agent, lane=lane,
                                         decision="approved" if aid else "allowed", outcome="started", ms=0, bytes=0, error_class=None,
                                         args_digest=_args_digest(args), job=None)
                if c["transport"] == "https":
                    d2 = self.k.authorize(action, aid)           # the final gate; consumes a bound approval
                    if not d2.allowed:
                        self.k.db.execute("UPDATE connector_calls SET decision='refused', outcome=? WHERE id=?", (d2.reason[:80], call_id))
                        return refuse(d2.reason)
                    return self._deliver(call_id, c, tool, self._run_https(c, params), None)
                jid = self.jobs.create(c["node"], "connector_op", params, approval_id=aid, owner=agent, lane=lane)
                self.k.db.execute("UPDATE connector_calls SET job=? WHERE id=?", (jid, call_id))
                j = self._wait_job(jid, agent)
                if j is None or j["state"] not in ("done", "failed"):
                    self.k.db.execute("UPDATE connector_calls SET outcome='pending' WHERE id=?", (call_id,))
                    return 202, {"status": "pending", "job": jid,
                                 "next": "the laptop node has not answered yet; ask for the result with connector_result and this job id"}
                res = j.get("result") or {"ok": False, "error": "no result"}
                if j["state"] == "failed" and not res.get("error_class"):
                    res = dict(res, error_class="failed")
                return self._deliver(call_id, c, tool, res, jid)
            finally:
                self.release(name)
        except Killed:
            return refuse("the kill switch is on", 503)
        except JobError as e:
            return refuse(str(e)[:200])

    def _body(self, name, tool, res, max_output):
        out = self.finish_result(name, tool, res, max_output)
        ok = bool(res.get("ok")) and not out["is_error"]
        body = {"status": "done" if ok else "failed", "result": out}
        if not ok:
            body["error_class"] = str(res.get("error_class") or "tool_error")
            body["reason"] = scrub(str(res.get("error", "")))[0][:300]
        return ok, out, body

    def _deliver(self, call_id, c, tool, res, jid):
        ok, out, body = self._body(c["name"], tool, res, int(c["max_output"]))
        nbytes = len(out["text"].encode("utf-8", "replace"))
        self.k.db.execute("UPDATE connector_calls SET outcome=?, ms=?, bytes=?, error_class=? WHERE id=?",
                          ("ok" if ok else "failed", int(res.get("ms") or 0), nbytes,
                           None if ok else str(res.get("error_class") or "failed")[:30], call_id))
        if jid is None:                           # a remote call is applied here; a node call by the finish hook
            self.k.audit.append("connector_call_done", {"connector": c["name"], "tool": tool, "ok": ok, "class": res.get("error_class"),
                                                         "ms": int(res.get("ms") or 0), "bytes": nbytes})
            if ok:
                self.k.db.execute("UPDATE connectors SET last_ok=?, last_error=NULL, last_error_class=NULL WHERE name=?", (self.clock(), c["name"]))
            else:
                self.k.db.execute("UPDATE connectors SET last_error=?, last_error_class=? WHERE name=?",
                                  (scrub(str(res.get("error", "")))[0][:300], str(res.get("error_class") or "failed")[:30], c["name"]))
        return 200, body

    def result_for(self, agent: str, jid: str):
        """The processed result of a call that was still pending, only for the agent that made it."""
        j = self.jobs.get_for(agent, str(jid)[:20])
        if not j or j["kind"] != "connector_op":
            return 404, {"status": "refused", "reason": "no such job"}
        if j["state"] not in ("done", "failed", "expired"):
            return 202, {"status": "pending", "job": jid}
        row = self.k.db.execute("SELECT connector, tool FROM connector_calls WHERE job=?", (str(jid)[:20],)).fetchone()
        if not row:
            return 404, {"status": "refused", "reason": "no such call"}
        c = self._row(row[0])
        res = j.get("result") or {}
        if j["state"] == "expired":
            res = {"ok": False, "error": "the job expired before the node ran it", "error_class": "expired"}
        _ok, _out, body = self._body(row[0], row[1], res, int((c or {}).get("max_output") or W.DEFAULT_MAX_OUTPUT))
        return 200, body
