"""The owner's control plane: what the owner (the human, through the JARVIS console) may ask the kernel to do over HTTP.

Why it exists: Telegram is off, so nothing let the owner send a command, approve, pause, cancel or stop work except an
SSH console. The loops that told JARVIS a lab task had ended and failed stuck jobs lived inside the Telegram bridge, and
stopped with it. This module is the replacement, with the same authority rules:

- A token of lane "owner" is the only way in. Agents never hold one (the lane comes from the token, never the request).
- The kernel stays the authority: a command is only text for the owner's own Hermes, which acts through the same
  kernel-gated tools as before. Nothing here runs an action by itself except task control and the stop switch.
- Approving over HTTP is YELLOW only, needs the exact approval id typed again AND an owner PIN (set once at the kernel
  console, stored as a salted hash). With no PIN set, HTTP approval is refused: fail closed. RED stays console-only, and
  lifting the kill switch stays console-only. Deny, cancel, pause and kill are safe directions and need no PIN.
- Every call is audited; nothing here puts a secret into a reply (text is scrubbed).
"""
import hashlib
import hmac
import json
import os
import re
import secrets
import threading
import time
import urllib.parse

from .scrub import scrub

# What the owner asks JARVIS to do changes the computer; what it looked like a few minutes ago may not be true now.
STATE_RULE = ("[JARVIS rule: the state of the computer changes. Never answer that something is already open, running or done from "
              "earlier in this conversation: run the action or check it with a tool now, then report what the tool returned.]\n")
ACTION_REQUEST = re.compile(r"\b(open|close|launch|start|run|create|make|build|delete|remove|take|save|download|stop|pause|resume|switch|focus|"
                            r"minimi[sz]e|maximi[sz]e|type|click|press|show|list|find|play|write|install)\b", re.I)

PREFIX = "/v1/jarvis/owner/"
MAX_COMMAND = 2000
MAX_REPLY = 12000
PIN_TRIES = 5                 # wrong PINs allowed ...
PIN_WINDOW = 600              # ... in this many seconds, then HTTP approval rests for the same time
NOISE = ("node_report", "report_refused", "agent_model_call", "agent_model_skipped")   # not shown in the live event feed


class OwnerError(Exception):
    def __init__(self, msg, code=400):
        super().__init__(msg)
        self.code = code


def _hash_pin(pin: str, salt: bytes = None, iters: int = 200_000) -> str:
    salt = salt or os.urandom(16)
    h = hashlib.pbkdf2_hmac("sha256", pin.encode(), salt, iters)
    return f"pbkdf2${iters}${salt.hex()}${h.hex()}"


def _check_pin(pin: str, stored: str) -> bool:
    try:
        _, iters, salt, want = stored.split("$")
        got = hashlib.pbkdf2_hmac("sha256", pin.encode(), bytes.fromhex(salt), int(iters)).hex()
        return hmac.compare_digest(got, want)
    except (ValueError, AttributeError):
        return False


class Owner:
    def __init__(self, kernel, tasks, jobs, memories=None, hermes=None, clock=time.time, spawn=None):
        self.k, self.tasks, self.jobs, self.memories, self.hermes, self.clock = kernel, tasks, jobs, memories, hermes, clock
        self.spawn = spawn or (lambda fn: threading.Thread(target=fn, daemon=True).start())
        self.busy = threading.Lock()
        self._gate = threading.Lock()   # guards the three fields below
        self._current = None            # id of the command whose agent call holds `busy`
        self._released = set()          # commands the owner stopped waiting on: their gate was handed back already
        self.bad_pins = []
        self.node = "laptop"      # the node PC jobs are queued for (set from the kernel's output node)
        self.lane_wait = None     # lane -> seconds until a model provider may be asked again (set by the gateway)
        self.routes = {}          # (method, name) -> fn(body, query) -> (code, reply); other modules register theirs here
        kernel.db.execute("CREATE TABLE IF NOT EXISTS owner_commands(id TEXT PRIMARY KEY, ts REAL, source TEXT, text TEXT, "
                          "state TEXT, reply TEXT, error TEXT, finished REAL)")
        kernel.db.execute("CREATE INDEX IF NOT EXISTS owner_commands_ts ON owner_commands(ts)")
        kernel.db.execute("CREATE TABLE IF NOT EXISTS owner_config(key TEXT PRIMARY KEY, value TEXT)")
        kernel.db.execute("CREATE TABLE IF NOT EXISTS owner_notices(id TEXT PRIMARY KEY)")
        # History is never re-announced: only tasks that end after the plane first started are handed back to JARVIS.
        if not kernel.db.execute("SELECT 1 FROM owner_config WHERE key='announce_since'").fetchone():
            kernel.db.execute("INSERT OR IGNORE INTO owner_config(key, value) VALUES ('announce_since', ?)", (str(clock()),))
        # a command still "running" at start-up belongs to a process that no longer exists
        kernel.db.execute("UPDATE owner_commands SET state='failed', error='the kernel restarted before JARVIS answered', finished=? "
                          "WHERE state='running'", (clock(),))

    # ---------- PIN ----------
    def set_pin(self, pin: str):
        """Console only (cli set-approval-pin). 6-64 characters; stored as a salted PBKDF2 hash."""
        if not 6 <= len(pin) <= 64:
            raise OwnerError("the PIN must have 6 to 64 characters")
        self.k.db.execute("INSERT OR REPLACE INTO owner_config(key, value) VALUES ('approval_pin', ?)", (_hash_pin(pin),))
        self.k.audit.append("owner_pin_set", {})

    def pin_set(self) -> bool:
        return self.k.db.execute("SELECT 1 FROM owner_config WHERE key='approval_pin'").fetchone() is not None

    # ---------- reads ----------
    def state(self):
        now = self.clock()
        tasks = []
        for t in self.tasks.recent(30):
            tasks.append({k: t[k] for k in ("id", "owner", "lane", "source", "goal", "state", "step", "attempts", "max_attempts",
                                            "created", "updated", "deadline", "result", "criteria", "plan", "verification", "evidence")}
                         | {"events": t["events"][:12]})
        jobs = []
        for jid, node, kind, state, created, finished, owner, params, result in self.k.db.execute(
                "SELECT id, node, kind, state, created, finished, owner, params, result FROM jobs ORDER BY created DESC LIMIT 25").fetchall():
            jobs.append({"id": jid, "node": node, "kind": kind, "state": state, "created": created, "finished": finished,
                         "owner": owner or "owner", "params": scrub(params or "")[0][:300],     # light scrub: state() is polled every few
                         "result": scrub(result or "")[0][:300]})                              # seconds; deep (gitleaks) cost 28 s live
        return {"now": now, "killed": self.k.killed(), "audit": self.k.audit.anchor(), "pin_set": self.pin_set(),
                "voice_command_ready": self.hermes is not None, "busy": self.busy.locked(),
                "tasks": tasks, "approvals": self.k.pending_approvals(), "approval_history": self.k.approval_history(), "jobs": jobs,
                "commands": self.commands(10)["commands"]}

    def commands(self, limit=20):
        rows = self.k.db.execute("SELECT id, ts, source, text, state, reply, error, finished FROM owner_commands "
                                 "ORDER BY ts DESC LIMIT ?", (max(1, min(int(limit), 100)),)).fetchall()
        return {"commands": [dict(zip(("id", "ts", "source", "text", "state", "reply", "error", "finished"), r)) for r in rows]}

    def events(self, since=0, limit=100):
        """Audit rows after seq `since`, oldest first, without the heartbeat noise."""
        marks = ",".join("?" * len(NOISE))
        rows = self.k.db.execute(f"SELECT seq, ts, kind, payload FROM audit WHERE seq>? AND kind NOT IN ({marks}) "
                                 f"ORDER BY seq LIMIT ?", (int(since), *NOISE, int(limit))).fetchall()
        out = []
        for seq, ts, kind, payload in rows:
            try:
                p = json.loads(payload)
            except ValueError:
                p = {}
            out.append({"seq": seq, "ts": ts, "kind": kind, "payload": p})
        return out

    # ---------- commands ----------
    def command(self, text: str, source: str = "console"):
        text = str(text or "").strip()
        if not text:
            raise OwnerError("empty command")
        if len(text) > MAX_COMMAND:
            raise OwnerError(f"too long (at most {MAX_COMMAND} characters)", 413)
        if self.hermes is None:
            raise OwnerError("JARVIS's agent is not reachable from the kernel (no Hermes API key on this server)", 503)
        clean, _, blocked = scrub(text, deep=True)
        if blocked:
            raise OwnerError("refused: the command contains a private key", 400)
        if self.k.killed():
            raise OwnerError("the kill switch is on", 503)
        wait = self.lane_wait("private") if self.lane_wait else 0
        if wait > 0:                                             # every provider of the owner's lane is resting after a free-tier limit
            when = time.strftime("%H:%M", time.localtime(self.clock() + wait))
            raise OwnerError(f"JARVIS's model providers are resting after hitting their free-tier limits (about {max(1, round(wait / 60))} min; "
                             f"back around {when}). Nothing was sent. Typing the command again after that works.", 429)
        if not self.busy.acquire(blocking=False):
            raise OwnerError("JARVIS is still working on the previous command", 409)
        cid, now = "c-" + secrets.token_hex(6), self.clock()
        try:
            self.k.db.execute("INSERT INTO owner_commands(id, ts, source, text, state) VALUES (?,?,?,?,?)",
                              (cid, now, source[:20], clean[:MAX_COMMAND], "running"))
            self.k.audit.append("owner_command", {"id": cid, "source": source[:20], "chars": len(clean)})
        except BaseException:
            self.busy.release()
            raise
        self.spawn(lambda: self._run(cid, clean, now))
        return {"id": cid, "state": "running"}

    def _facts(self, since: float, text: str = "") -> str:
        """The kernel's own record of what ran during this turn, appended to the agent's reply: a claim like "done" or
        "already open" cannot hide that nothing was run, or that a task was never verified."""
        rows = self.k.db.execute("SELECT id, goal, state, verification FROM tasks WHERE updated >= ? ORDER BY updated", (since - 1,)).fetchall()
        lines = []
        for tid, goal, state, ver in rows:
            v = json.loads(ver or "{}")
            check = ("verified" if v.get("passed") is True else "NOT verified yet") if state != "failed" else "failed"
            lines.append(f"- task {tid} ({str(goal)[:60]}): {state}, {check}")
        jobs = self.k.db.execute("SELECT kind, state, params, result FROM jobs WHERE created >= ? ORDER BY created LIMIT 8", (since - 1,)).fetchall()
        for kind, state, params, result in jobs:
            try:
                p, r = json.loads(params or "{}"), json.loads(result or "{}")
            except ValueError:
                p, r = {}, {}
            what = " ".join(str(p.get(k))[:40] for k in ("op", "target", "name") if p.get(k)) or kind
            note = r.get("verified") or ("failed: " + str(r.get("error"))[:50] if state == "failed" or r.get("ok") is False else "")
            lines.append(f"- {kind} {what}: {state}" + (f", {note}" if note else ""))
        if not lines and ACTION_REQUEST.search(text or ""):
            lines.append("- no action was run on this computer or in the lab for this request: the answer above was not checked against its current state")
        return ("\n\nKernel record:\n" + "\n".join(lines)) if lines else ""

    def _abandon(self, cid=None):
        """The owner stopped waiting on a command. Its agent call cannot be aborted from here, so the gate is handed back at
        once (the next command would otherwise be refused as 'still working' for as long as the model provider takes), the
        call's own release is skipped, and the agent's conversation is rotated so the orphaned turn cannot interleave with
        the next one. Durable facts live in memory and the kernel's records, not in the conversation."""
        with self._gate:
            cid = cid or self._current
            if cid is None or not self.busy.locked() or self._current not in (None, cid) or cid in self._released:
                return False
            self._released.add(cid)
            self._current = None
            self.busy.release()
        reset = getattr(self.hermes, "reset", None)
        if reset:
            try:
                reset()
            except Exception:
                pass
        return True

    def provider_resting(self, wait: int) -> bool:
        """The gateway has just told the owner's agent that every provider of its lane is resting. The agent would now sleep
        through its own ten-minute back-off with the command showing "running", for nothing: say so at once, free the gate,
        and rotate the conversation. Returns True when a running command was ended."""
        with self._gate:
            cid = self._current
        if not cid:
            return False
        when = time.strftime("%H:%M", time.localtime(self.clock() + wait))
        msg = (f"JARVIS's model providers are resting after hitting their free-tier limits (about {max(1, round(wait / 60))} min; "
               f"back around {when}). Nothing more was done for this command.")
        cur = self.k.db.execute("UPDATE owner_commands SET state='failed', error=?, finished=? WHERE id=? AND state='running'",
                                (msg, self.clock(), cid))
        if cur.rowcount:
            self.k.audit.append("owner_command_provider_resting", {"id": cid, "retry_after_s": int(wait)})
            self._abandon(cid)
        return cur.rowcount == 1

    def _run(self, cid: str, text: str, started: float):
        with self._gate:
            if cid not in self._released:
                self._current = cid
        try:
            try:
                reply = (self.hermes(STATE_RULE + text) or "").strip()
                reply = scrub(reply + self._facts(started, text), deep=True)[0][:MAX_REPLY]
                state, err = "done", None
            except Exception as e:                                   # the agent's own error text may name hosts: keep the class only
                reply, state, err = "", "failed", f"{type(e).__name__}: {str(e)[:160]}"
            cur = self.k.db.execute("UPDATE owner_commands SET state=?, reply=?, error=?, finished=? WHERE id=? AND state='running'",
                                    (state, reply, err, self.clock(), cid))
            self.k.audit.append("owner_command_done" if cur.rowcount else "owner_command_late",
                                {"id": cid, "state": state, "chars": len(reply)})
        finally:
            with self._gate:
                if cid in self._released:
                    self._released.discard(cid)               # the gate was handed back when the owner stopped waiting
                else:
                    if self._current == cid:
                        self._current = None
                    self.busy.release()

    def cancel_command(self, cid: str):
        """The owner stopped waiting. The agent's HTTP call cannot be aborted from here, so the answer is ignored when
        it arrives (the row is no longer 'running'); the tasks it started are stopped by stop_all, not by this."""
        cur = self.k.db.execute("UPDATE owner_commands SET state='cancelled', finished=? WHERE id=? AND state='running'",
                                (self.clock(), str(cid)[:40]))
        self.k.audit.append("owner_command_cancelled", {"id": str(cid)[:40], "ok": cur.rowcount == 1})
        if cur.rowcount == 1:
            self._abandon(str(cid)[:40])
        return {"cancelled": cur.rowcount == 1}

    # ---------- approvals ----------
    def approve(self, aid: str, confirm: str, pin: str):
        aid = str(aid or "")[:40]
        if str(confirm or "") != aid:
            raise OwnerError("type the approval id again to confirm", 400)
        now = self.clock()
        self.bad_pins = [t for t in self.bad_pins if now - t < PIN_WINDOW]
        if len(self.bad_pins) >= PIN_TRIES:
            raise OwnerError("too many wrong PINs; approving over HTTP rests for a few minutes", 429)
        r = self.k.db.execute("SELECT value FROM owner_config WHERE key='approval_pin'").fetchone()
        if not r:
            raise OwnerError("no approval PIN is set: run `python3 -m jarvis_kernel set-approval-pin` at the kernel console, "
                             "or approve there with `approve <id>`", 403)
        if not _check_pin(str(pin or ""), r[0]):
            self.bad_pins.append(now)
            self.k.audit.append("owner_approve_bad_pin", {"id": aid})
            raise OwnerError("wrong PIN", 403)
        info = self.k.approval_info(aid)
        if not info or info["state"] != "pending":
            raise OwnerError("unknown, used or expired approval", 404)
        if info["level"] != "YELLOW_APPROVAL":
            self.k.audit.append("owner_approve_refused", {"id": aid, "level": info["level"]})
            raise OwnerError(f"{info['level']} actions are approved at the kernel console only", 403)
        ok = self.k.approve(aid)
        self.k.audit.append("owner_approved_http", {"id": aid, "ok": ok})
        if not ok:
            raise OwnerError("unknown, used or expired approval", 404)
        return {"approved": aid}

    def deny(self, aid: str):
        ok = self.k.deny(str(aid or "")[:40])
        self.k.audit.append("owner_denied_http", {"id": str(aid or "")[:40], "ok": ok})
        if not ok:
            raise OwnerError("unknown, used or expired approval", 404)
        return {"denied": str(aid)[:40]}

    # ---------- tasks and the stop switches ----------
    def task_control(self, tid: str, op: str):
        from .tasks import TaskError
        t = self.tasks.get(tid)
        if not t:
            raise OwnerError("no such task", 404)
        target = {"pause": "paused", "cancel": "cancelled", "resume": "running"}.get(op)
        if not target:
            raise OwnerError("op must be pause, resume or cancel")
        if op == "resume" and t["state"] != "paused":
            raise OwnerError(f"task is {t['state']}, not paused", 409)
        try:
            t = self.tasks.update(None, tid, state=target, note=f"{op} by the owner (console)")
        except TaskError as e:
            raise OwnerError(str(e), 409)
        self.k.audit.append("owner_task_control", {"id": tid, "op": op})
        return {"id": tid, "state": t["state"]}

    def stop_all(self):
        """Cancel every open task, deny every pending approval, expire queued jobs. The lab runner sees the cancelled
        tasks within about 20 s and stops its workers. The kill switch is separate (kill)."""
        from .tasks import FINAL, TaskError
        cancelled, failed = [], []
        for (tid,) in self.k.db.execute("SELECT id FROM tasks WHERE state NOT IN ('completed','failed','cancelled')").fetchall():
            try:
                self.tasks.update(None, tid, state="cancelled", note="stopped by the owner (stop all)")
                cancelled.append(tid)
            except TaskError:
                failed.append(tid)
        denied = self.k.db.execute("UPDATE approvals SET state='denied' WHERE state='pending'").rowcount
        expired = self.k.db.execute("UPDATE jobs SET state='expired' WHERE state='queued'").rowcount
        if self.busy.locked():
            self.k.db.execute("UPDATE owner_commands SET state='cancelled', finished=? WHERE state='running'", (self.clock(),))
            self._abandon()
        self.k.audit.append("owner_stop_all", {"tasks": len(cancelled), "approvals": denied, "jobs": expired})
        return {"cancelled_tasks": cancelled, "not_cancelled": failed, "denied_approvals": denied, "expired_jobs": expired}

    def install_connectors(self, svc):
        """Owner routes for the connector manager (connector_service.py). Every one needs the owner token (the route table's
        own check); none returns a command line, a secret value or a credential."""
        from .connectors import ConnectorError
        R = self.routes
        self.connectors = svc

        def guard(fn):
            def run(body, q):
                try:
                    return fn(body, q)
                except ConnectorError as e:
                    return e.code, {"error": str(e)}
            return run

        def name_of(b):
            return str(b.get("name") or b.get("id") or "")[:40]

        R[("GET", "connectors")] = guard(lambda b, q: (200, {"connectors": svc.list(), "calls": svc.calls(40)}))
        R[("GET", "connectors/calls")] = guard(lambda b, q: (200, {"calls": svc.calls(min(200, int(q.get("limit", "60") or 60)))}))
        R[("POST", "connectors")] = guard(lambda b, q: (201, svc.create(b.get("definition") or {})))
        R[("POST", "connectors/update")] = guard(lambda b, q: (200, svc.create(b.get("definition") or {}, update=True)))
        R[("POST", "connectors/enable")] = guard(lambda b, q: (200, svc.set_enabled(name_of(b), True)))
        R[("POST", "connectors/disable")] = guard(lambda b, q: (200, svc.set_enabled(name_of(b), False)))
        R[("POST", "connectors/tool")] = guard(lambda b, q: (200, svc.set_tool(name_of(b), str(b.get("tool", ""))[:128],
                                                                              b.get("enabled") if isinstance(b.get("enabled"), bool) else None,
                                                                              b.get("risk") if isinstance(b.get("risk"), str) else None)))
        for op in ("test", "connect", "disconnect"):
            R[("POST", "connectors/" + op)] = guard(lambda b, q, op=op: (200, svc.owner_op(name_of(b), op)))

        def delete(b, q):
            if str(b.get("confirm", "")) != name_of(b):
                raise ConnectorError("type the connector name again to confirm")
            svc.stop_in_background(name_of(b))               # best effort: removal never waits for the laptop
            return 200, svc.delete(name_of(b))
        R[("POST", "connectors/delete")] = guard(delete)

        def try_tool(b, q):
            code, body = svc.call_tool("owner", "private", name_of(b), str(b.get("tool", ""))[:128],
                                       b.get("arguments") if isinstance(b.get("arguments"), dict) else {}, None, owner_try=True)
            return code, body
        R[("POST", "connectors/try")] = guard(try_tool)

    def kill(self):
        self.k.kill("owner console")
        return {"killed": True, "note": "lifting the kill switch is done at the kernel console (jarvis_kernel resume)"}

    # ---------- background work (the loops that used to live in the Telegram bridge) ----------
    def tick(self):
        """Sweep stuck jobs, fail tasks past their deadline, and hand finished lab work to JARVIS once each."""
        out = {"jobs": self.jobs.sweep(), "expired_tasks": self.tasks.expire()}
        if getattr(self, "automations", None) is not None:
            out["automations"] = self.automations.tick()
        out["announced"] = self.announce_finished()
        out["lessons"] = self.capture_lessons()
        return out

    def capture_lessons(self):
        """A task that failed, was repaired and then completed *verified* teaches something. Build a candidate from what
        the task's own events recorded (never from a model's opinion); it waits for the gate and the owner's decision."""
        if self.memories is None:
            return []
        self.k.db.execute("CREATE TABLE IF NOT EXISTS lesson_seen(id TEXT PRIMARY KEY)")
        rows = self.k.db.execute("SELECT id FROM tasks WHERE state='completed' AND attempts>=2 AND id NOT IN (SELECT id FROM lesson_seen) "
                                 "ORDER BY updated DESC LIMIT 5").fetchall()
        made = []
        for (tid,) in rows:
            t = self.tasks.get(tid)
            self.k.db.execute("INSERT OR IGNORE INTO lesson_seen VALUES (?)", (tid,))
            if not t or (t.get("verification") or {}).get("passed") is not True:
                continue
            problems = [e["note"] for e in reversed(t["events"]) if e["state"] == "repairing" and e["note"]]
            if not problems:
                continue
            text = (f"Lesson (task {tid[:6]}): for '{t['goal'][:90]}', the first attempt failed: {problems[0][:170]}. "
                    f"It was repaired and independently verified after {t['attempts']} attempts: {str(t['result'] or '')[:110]}")
            try:
                mid = self.memories.add_candidate(t["owner"], text[:500], tid)
            except Exception:
                mid = None
            if mid:
                made.append(mid)
        return made

    def announce_finished(self):
        """When lab work for a task has ended, JARVIS hears about it, checks it against the acceptance criteria and tells
        the owner (the reply lands in the console and is spoken). Skipped while the Telegram bridge is on: it does this."""
        from . import telegram
        if not telegram.disabled():
            return []
        since = float(self.k.db.execute("SELECT value FROM owner_config WHERE key='announce_since'").fetchone()[0])
        rows = self.k.db.execute(
            "SELECT id FROM tasks WHERE state IN ('completed','failed','cancelled') AND updated > ? AND id NOT IN (SELECT id FROM owner_notices) "
            "AND EXISTS (SELECT 1 FROM jobs WHERE jobs.kind='lab_run' AND jobs.params LIKE '%' || tasks.id || '%')", (since,)).fetchall()
        done = []
        for (tid,) in rows:
            t = self.tasks.get(tid)
            summary = (f"Lab work for task {tid} ended: {t['state'].upper()} after {t['attempts']} attempt(s).\n"
                       f"Goal: {t['goal']}\nResult: {t['result'] or '-'}\n"
                       f"Verification: {json.dumps(t.get('verification') or {}, ensure_ascii=False)[:1200]}\n"
                       f"Evidence: {'; '.join(t.get('evidence') or [])[:1200]}")
            if self.hermes is not None and self.busy.acquire(blocking=False):
                self.k.db.execute("INSERT OR IGNORE INTO owner_notices VALUES (?)", (tid,))
                cid, now = "c-" + secrets.token_hex(6), self.clock()
                prompt = ("[JARVIS kernel] " + summary + "\nCheck this against the task's acceptance criteria (task tool, op get), "
                          "close the task honestly if it is still open, deliver files if needed, record any reusable lesson, "
                          "and tell the owner the outcome in a few lines, including what remains if it failed.")
                self.k.db.execute("INSERT INTO owner_commands(id, ts, source, text, state) VALUES (?,?,?,?,?)",
                                  (cid, now, "outcome", f"(task {tid} ended: {t['state']})", "running"))
                self.spawn(lambda cid=cid, prompt=prompt, now=now: self._run(cid, prompt, now))
                done.append(tid)
            elif self.hermes is None:                                   # nobody to tell: the event feed and task card carry it
                self.k.db.execute("INSERT OR IGNORE INTO owner_notices VALUES (?)", (tid,))
                self.k.audit.append("owner_task_ended", {"id": tid, "state": t["state"]})
                done.append(tid)
            # else: JARVIS is busy; the next tick tries again
        return done

    # ---------- the owner's own PC (deterministic, kernel-authorized jobs for the laptop node) ----------
    def pc(self, body: dict):
        """Queue one fixed kind of job for the laptop node on the owner's behalf: open an app, folder or document, list a
        folder, list the windows, read one window's accessibility tree, take a screenshot, check a project. The kernel's
        job validator and policy still decide; the node answers with what it actually observed."""
        from .jobs import JobError, PC_OPS
        op = str(body.get("op", ""))
        target, path = str(body.get("target", ""))[:80], str(body.get("path", ""))[:200]
        if op in PC_OPS:
            kind, params = "pc_open", {"agent": "owner", "op": op, "target": target, "path": path}
        elif op == "windows":
            kind, params = "ui_windows", {}
        elif op == "snapshot":
            kind, params = "ui_snapshot", {"target": target}
        elif op == "screenshot":
            kind, params = "screenshot", {}
        elif op == "project_check":
            kind, params = "project_check", {"project": target}
        else:
            raise OwnerError("op must be open_app, open_folder, open_file, list_folder, windows, snapshot, screenshot or project_check")
        try:
            jid = self.jobs.create(self.node, kind, params, owner=None, lane="private")
        except JobError as e:
            raise OwnerError(str(e)[:160], 403)
        self.k.audit.append("owner_pc", {"op": op, "job": jid})
        return {"job": jid, "kind": kind, "node": self.node}

    def job(self, jid: str):
        r = self.k.db.execute("SELECT id, node, kind, state, created, finished, result, owner FROM jobs WHERE id=?", (str(jid)[:40],)).fetchone()
        if not r:
            raise OwnerError("no such job", 404)
        try:
            result = json.loads(r[6]) if r[6] else None
        except ValueError:
            result = None
        return {"id": r[0], "node": r[1], "kind": r[2], "state": r[3], "created": r[4], "finished": r[5], "owner": r[7] or "owner", "result": result}

    # ---------- automations and agent definitions (their own modules; routes registered here) ----------
    def attach(self, automations=None, agents=None, providers=None):
        """Register the Automation Studio, Agent Factory, memory and models routes; tick() drives the scheduler."""
        from .automations import AutomationError, Automations
        from .agents import AgentError, AgentDefs, CAPABILITIES, draft_from_sentence
        self.automations, self.agents = automations, agents

        def guard(fn):
            def run(body, q):
                try:
                    return fn(body, q)
                except (AutomationError, AgentError) as e:
                    return e.code, {"error": str(e)}
            return run

        def need_confirm(body, what):
            if str(body.get("confirm", "")) != str(body.get("id", "")):
                raise OwnerError(f"type the {what} id again to confirm")

        R = self.routes
        if providers is not None:
            from .overview import overview

            def models(b, q):
                ov = overview(self.k, providers, self.clock())
                limits = {p.name: {k: getattr(p, k, None) for k in ("max_input_tokens", "max_total_tokens", "paid", "priority", "timeout_s")}
                          for p in providers}
                for row in ov["providers"]:
                    row["limits"] = limits.get(row["name"], {})
                return 200, {"providers": ov["providers"], "agents": ov["agents"], "kpis": ov["kpis"], "actions_24h": ov["actions_24h"],
                             "series_24h": ov["series_24h"], "nodes": ov["nodes"], "outputs": ov["outputs"]}
            R[("GET", "models")] = guard(models)
        if self.memories is not None:
            from .memory import Conflict, MemoryError as MemErr
            M = self.memories

            def mem_get(b, q):
                owners = [r[0] for r in self.k.db.execute("SELECT DISTINCT owner FROM memories ORDER BY owner").fetchall()]
                who = q.get("owner") or (owners[0] if owners else "")
                scope = q.get("scope") or "default"
                full = [M.get(who, e["id"]) for e in M.list(who, scope)] if who else []
                return 200, {"owners": owners, "owner": who, "scope": scope, "entries": full}

            def mem_post(b, q):
                op, who = str(b.get("op", "")), str(b.get("owner", ""))
                if not who:
                    raise OwnerError("owner (whose memory) is required")
                try:
                    if op == "add":
                        return 201, M.add(who, b.get("text"), str(b.get("kind") or "preference"), str(b.get("scope") or "default"))
                    if op == "update":
                        return 200, M.update(who, str(b.get("id")), b.get("text"), b.get("expected_version"))
                    if op == "forget":
                        return 200, M.forget(who, str(b.get("id")), b.get("expected_version"))
                except Conflict as e:
                    return 409, {"error": str(e), "current": {k: e.current[k] for k in ("id", "text", "version")}}
                except MemErr as e:
                    return 400, {"error": str(e)[:200]}
                raise OwnerError("op must be add, update or forget")
            R[("GET", "memory")] = guard(mem_get)
            R[("POST", "memory")] = guard(mem_post)
        def watches(b, q):
            out = []
            try:
                rows = self.k.db.execute("SELECT name, state FROM nodes").fetchall()
            except Exception:                    # no node has ever paired on this kernel: the table does not exist yet
                rows = []
            for name, state in rows:
                try:
                    names = (json.loads(state or "{}").get("watches") or [])
                except ValueError:
                    names = []
                out += [{"node": name, "watch": w} for w in names if isinstance(w, str)]
            return 200, {"watches": out}
        R[("GET", "watches")] = guard(watches)
        if self.memories is not None:
            def lessons_get(b, q):
                return 200, {"lessons": self.memories.candidates(q.get("owner"))}

            def lessons_post(op):
                def f(b, q):
                    from .memory import MemoryError as MemErr
                    who, mid = str(b.get("owner", "")), str(b.get("id", ""))
                    row = self.k.db.execute("SELECT history FROM memories WHERE id=? AND owner=? AND state='candidate'", (mid, who)).fetchone()
                    if not row:
                        return 404, {"error": "no such candidate lesson"}
                    tid = next((x.get("task") for x in json.loads(row[0] or "[]") if x.get("task")), None)
                    try:
                        return 200, self.memories.decide_lesson(who, mid, self.tasks.get(tid) if tid else None, op == "activate")
                    except MemErr as e:
                        return 400, {"error": str(e)[:200]}
                return f
            R[("GET", "lessons")] = guard(lessons_get)
            R[("POST", "lessons/activate")] = guard(lessons_post("activate"))
            R[("POST", "lessons/reject")] = guard(lessons_post("reject"))
        R[("GET", "audit/verify")] = guard(lambda b, q: (200, dict(zip(("ok", "message"), self.k.audit.verify()))))
        if automations is not None:
            def a_create(body, q):
                d = body.get("definition") or {}
                if not isinstance(d, dict):
                    raise OwnerError("definition must be an object")
                if body.get("phrase") and not d.get("schedule"):
                    p = Automations.from_phrase(str(body["phrase"]))
                    if not p:
                        raise OwnerError("I could not read that schedule; use a cron expression or an interval")
                    d = dict(d, trigger_kind=p[0], schedule=p[1])
                return d
            R[("GET", "automations")] = guard(lambda b, q: (200, {"automations": automations.list()}))
            R[("POST", "automations/preview")] = guard(lambda b, q: (200, automations.preview(a_create(b, q))))
            R[("POST", "automations")] = guard(lambda b, q: (201, automations.create(a_create(b, q), owner="owner", enabled=bool(b.get("enabled", True)))))
            R[("POST", "automations/update")] = guard(lambda b, q: (200, automations.update(str(b.get("id")), b.get("definition") or {}, b.get("version"))))
            R[("POST", "automations/pause")] = guard(lambda b, q: (200, automations.set_enabled(str(b.get("id")), False)))
            R[("POST", "automations/resume")] = guard(lambda b, q: (200, automations.set_enabled(str(b.get("id")), True)))
            R[("POST", "automations/run")] = guard(lambda b, q: (200, automations.run_now(str(b.get("id")))))
            R[("POST", "automations/delete")] = guard(lambda b, q: (need_confirm(b, "automation"), (200, automations.delete(str(b.get("id")))))[1])
        if agents is not None:
            R[("GET", "agents")] = guard(lambda b, q: (200, {"agents": agents.list(), "capabilities": CAPABILITIES}))
            R[("POST", "agents/draft")] = guard(lambda b, q: (200, agents.describe(draft_from_sentence(b.get("sentence")))))
            R[("POST", "agents")] = guard(lambda b, q: (201, agents.create(b.get("definition") or {})))
            R[("POST", "agents/update")] = guard(lambda b, q: (200, agents.update(str(b.get("id")), b.get("definition") or {}, b.get("version"))))
            R[("POST", "agents/activate")] = guard(lambda b, q: (200, agents.set_state(str(b.get("id")), "active")))
            R[("POST", "agents/pause")] = guard(lambda b, q: (200, agents.set_state(str(b.get("id")), "paused")))
            R[("POST", "agents/resume")] = guard(lambda b, q: (200, agents.set_state(str(b.get("id")), "active")))
            R[("POST", "agents/terminate")] = guard(lambda b, q: (need_confirm(b, "agent"), (200, agents.set_state(str(b.get("id")), "terminated")))[1])
            R[("POST", "agents/clone")] = guard(lambda b, q: (201, agents.clone(str(b.get("id")), b.get("name"))))
            R[("POST", "agents/delete")] = guard(lambda b, q: (need_confirm(b, "agent"), (200, agents.delete(str(b.get("id")))))[1])
            R[("POST", "agents/test")] = guard(lambda b, q: (202, agents.test(str(b.get("id")), b.get("task"))))

    # ---------- HTTP-shaped entry point ----------
    def handle(self, method: str, path: str, body: dict):
        """(status, reply) for one owner request. The caller has already checked that the token's lane is 'owner'."""
        u = urllib.parse.urlsplit(path)
        name = u.path[len(PREFIX):].strip("/")
        q = dict(urllib.parse.parse_qsl(u.query))
        try:
            if method == "GET":
                if name == "state":
                    return 200, self.state()
                if name == "commands":
                    return 200, self.commands(q.get("limit", 20))
                if name == "events":
                    return 200, {"events": self.events(q.get("since", 0), q.get("limit", 100)), "head": self.k.audit.anchor()["seq"]}
                if name.startswith("task/"):
                    t = self.tasks.get(name[5:])
                    return (200, t) if t else (404, {"error": "no such task"})
                if name.startswith("job/"):
                    return 200, self.job(name[4:])
            if method == "POST":
                if name == "command":
                    return 202, self.command(body.get("text"), str(body.get("source") or "console"))
                if name == "command/cancel":
                    return 200, self.cancel_command(body.get("id"))
                if name == "approve":
                    return 200, self.approve(body.get("id"), body.get("confirm"), body.get("pin"))
                if name == "deny":
                    return 200, self.deny(body.get("id"))
                if name == "task":
                    return 200, self.task_control(str(body.get("id", "")), str(body.get("op", "")))
                if name == "pc":
                    return 202, self.pc(body)
                if name == "stop_all":
                    return 200, self.stop_all()
                if name == "kill":
                    return 200, self.kill()
            fn = self.routes.get((method, name))
            if fn:
                return fn(body, q)
            return 404, {"error": "not found"}
        except OwnerError as e:
            return e.code, {"error": str(e)}
        except Exception as e:                                          # never a traceback to the client
            self.k.audit.append("owner_error", {"route": name[:40], "type": type(e).__name__})
            return 500, {"error": f"{type(e).__name__}"}


def start_ticker(owner: Owner, interval: float = 30.0, stop: threading.Event = None):
    """The kernel's own background loop. One failed tick never ends it."""
    stop = stop or threading.Event()

    def loop():
        while not stop.wait(interval):
            try:
                owner.tick()
            except Exception as e:
                try:
                    owner.k.audit.append("owner_tick_error", {"type": type(e).__name__})
                except Exception:
                    pass
    t = threading.Thread(target=loop, daemon=True, name="owner-ticker")
    t.start()
    return stop
