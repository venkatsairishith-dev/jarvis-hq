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
from . import lang

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
PROJECTFIX_TICK = 10          # project fixes advanced per tick (each advance is bounded by projectfix itself) ...
PROJECTFIX_SCAN = 200         # ... out of at most this many open ones looked at (one cheap read each)
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
        self.bad_pins = []                  # times of wrong PINs; kept in owner_config too, so a restart does not reset them
        self._pin_lock = threading.Lock()   # guards bad_pins and _pin_checking (PIN checks in flight)
        self._pin_checking = 0
        self.node = "laptop"     # the node PC jobs are queued for (set from the kernel's output node)
        self.router_snapshot = None   # () -> the router shadow-mode record (set by the gateway)
        self.lane_wait = None     # lane -> seconds until a model provider may be asked again (set by the gateway)
        self._pc_seen = {}        # (holder token, seq) -> the PC job reply it got (duplicate delivery)
        self.routes = {}          # (method, name) -> fn(body, query) -> (code, reply); other modules register theirs here
        self.idem = None          # idempotency keys (idempotency.py), created on the first request that carries one
        kernel.db.execute("CREATE TABLE IF NOT EXISTS owner_commands(id TEXT PRIMARY KEY, ts REAL, source TEXT, text TEXT, "
                          "state TEXT, reply TEXT, error TEXT, finished REAL)")
        kernel.db.execute("CREATE INDEX IF NOT EXISTS owner_commands_ts ON owner_commands(ts)")
        kernel.db.execute("CREATE TABLE IF NOT EXISTS owner_config(key TEXT PRIMARY KEY, value TEXT)")
        kernel.db.execute("CREATE TABLE IF NOT EXISTS owner_notices(id TEXT PRIMARY KEY)")
        # History is never re-announced: only tasks that end after the plane first started are handed back to JARVIS.
        if not kernel.db.execute("SELECT 1 FROM owner_config WHERE key='announce_since'").fetchone():
            kernel.db.execute("INSERT OR IGNORE INTO owner_config(key, value) VALUES ('announce_since', ?)", (str(clock()),))
        self.bad_pins = self._load_bad_pins()
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

    def _load_bad_pins(self) -> list:
        """Wrong-PIN times from the kernel DB (audit SEC-D10: the lockout used to reset on a kernel restart). An unreadable
        record counts as a full lockout for one window: fail closed."""
        r = self.k.db.execute("SELECT value FROM owner_config WHERE key='pin_failures'").fetchone()
        if not r:
            return []
        try:
            return [float(t) for t in json.loads(r[0])][-PIN_TRIES * 4:]
        except (ValueError, TypeError):
            return [self.clock()] * PIN_TRIES

    def _save_bad_pins(self):
        self.k.db.execute("INSERT OR REPLACE INTO owner_config(key, value) VALUES ('pin_failures', ?)",
                          (json.dumps(self.bad_pins[-PIN_TRIES * 4:]),))

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
        # D58: the console, the face and the voice path translate on the laptop BEFORE the text reaches here, so this is normally English. Text that still is
        # not (a client that skips the layer) is translated here only if a local translator is enabled on this server, else flagged in the audit log and kept.
        clean, tr_status = lang.english_for_agent(clean, source=source)
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
            self.k.audit.append("owner_command", {"id": cid, "source": source[:20], "chars": len(clean), **({"translated": tr_status} if tr_status != "no" else {})})
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
                reply, state, err = "", "failed", type(e).__name__
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

    def current_command(self):
        """The id of the command whose agent call holds the gate right now (None when none does, or the owner stopped waiting on
        it). There is one at a time: `busy` is a single lock. A task the owner's agent creates now was started by this command."""
        with self._gate:
            return self._current if self.busy.locked() else None

    def cancel_command(self, cid: str, stop_tasks: bool = False):
        """The owner stopped waiting. The agent's HTTP call cannot be aborted from here, so the answer is ignored when
        it arrives (the row is no longer 'running').

        Plain "Stop waiting" (stop_tasks False) does NOT stop the tasks the command started: they are stopped by stop_all.
        With stop_tasks True (the explicit option; jarvis_bridge:cancel_task uses it) the tasks created while this command was
        the running one are cancelled too, with their queued or running lab and PC jobs, even if the command had already
        answered (the lab work outlives the answer). Only tasks the kernel linked to the command at creation are touched;
        a task an orphaned agent turn creates after the owner stopped waiting cannot be tied to any command (stop_all)."""
        cid = str(cid)[:40]
        cur = self.k.db.execute("UPDATE owner_commands SET state='cancelled', finished=? WHERE id=? AND state='running'", (self.clock(), cid))
        self.k.audit.append("owner_command_cancelled", {"id": cid, "ok": cur.rowcount == 1, "stop_tasks": bool(stop_tasks)})
        if cur.rowcount == 1:
            self._abandon(cid)
        out = {"cancelled": cur.rowcount == 1}
        if stop_tasks:
            out.update(self.stop_command_tasks(cid))
        return out

    def stop_command_tasks(self, cid: str) -> dict:
        """Cancel the open tasks linked to command `cid` and the jobs still queued or running for them (a running job is asked to
        stop: the lab runner and the node kill what it started). Stopping is the safe direction: no PIN."""
        from .tasks import FINAL, TaskError
        stopped, left, jobs, denied = [], [], [], []
        for tid in self.tasks.for_command(cid):
            t = self.tasks.get(tid)
            if not t or t["state"] in FINAL:
                continue
            try:
                self.tasks.update(None, tid, state="cancelled", note=f"stopped with command {cid} by the owner")
                stopped.append(tid)
                denied += self._deny_task_approvals(tid)
            except TaskError:
                left.append(tid)
            for jid in self.tasks.job_ids(tid):
                res = self.jobs.cancel(jid)
                if res["state"] in ("cancelled", "cancelling"):
                    jobs.append(jid)
        self.k.audit.append("owner_command_tasks_stopped", {"id": cid, "tasks": stopped, "not_stopped": left, "jobs": jobs,
                                                           "approvals_denied": denied})
        return {"tasks_stopped": stopped, "tasks_not_stopped": left, "jobs_cancelled": jobs, "approvals_denied": denied}

    def _deny_task_approvals(self, tid: str) -> list:
        """A cancelled task's pending approvals (the sharing approval its lab_work dispatch asked for, recorded in
        lab_share_pending) are denied, so the card cannot be approved and used after the task was stopped. Only approvals recorded
        for THIS task; one already decided or expired is left as it is. A kernel without the lab wiring has no such table."""
        return _withdraw_task_approvals(self.k, tid)["approvals_withdrawn"]

    # ---------- approvals ----------
    def approve(self, aid: str, confirm: str, pin: str):
        aid = str(aid or "")[:40]
        if str(confirm or "") != aid:
            raise OwnerError("type the approval id again to confirm", 400)
        if self.k.killed():                       # review C: nothing is approved while the kill switch is on (deny still works)
            raise OwnerError("the kill switch is on: approving is paused (you can still deny). Resume first, then approve again", 409)
        now = self.clock()
        # The limit counts checks still running too: the PBKDF2 check is slow, so parallel guesses used to all pass the
        # count before any failure was recorded (30 simultaneous guesses were all checked; bench N-006, 2026-10-16).
        with self._pin_lock:
            # A failure time later than now (the clock went back) still counts: the rest is never shortened (bench N-025).
            self.bad_pins = [t for t in self.bad_pins if now - t < PIN_WINDOW]
            if len(self.bad_pins) + self._pin_checking >= PIN_TRIES:
                raise OwnerError("too many wrong PINs; approving over HTTP rests for a few minutes", 429)
            self._pin_checking += 1
        good = False
        try:
            r = self.k.db.execute("SELECT value FROM owner_config WHERE key='approval_pin'").fetchone()
            if not r:
                good = True                       # nothing was guessed: not a wrong PIN
                raise OwnerError("no approval PIN is set: run `python3 -m jarvis_kernel set-approval-pin` at the kernel console, "
                                 "or approve there with `approve <id>`", 403)
            good = _check_pin(str(pin or ""), r[0])
        finally:
            with self._pin_lock:
                self._pin_checking -= 1
                if not good:
                    self.bad_pins.append(now)
                    self._save_bad_pins()
        if not good:
            self.k.audit.append("owner_approve_bad_pin", {"id": aid})
            raise OwnerError("wrong PIN", 403)
        info = self.k.approval_info(aid)
        if not info or info["state"] != "pending":
            raise OwnerError("unknown, used or expired approval", 404)
        if info["level"] != "YELLOW_APPROVAL":
            self.k.audit.append("owner_approve_refused", {"id": aid, "level": info["level"]})
            raise OwnerError(f"{info['level']} actions are approved at the kernel console only", 403)
        if info.get("blocked"):                   # re-review M1 (2026-10-19): invisible, control or direction characters on the card
            raise OwnerError("this approval card contains invisible, control or text-direction characters (shown as <U+XXXX> on the card), "
                             "so what it shows may not be what it does and it cannot be approved: deny it and ask the agent to resend "
                             "plain text", 409)
        if info.get("incomplete"):                # review M6: never approve a card that was cut off or cannot be read
            raise OwnerError("this approval card is cut off (part of the text is not shown), so it cannot be approved: deny it and "
                             "ask the agent to send shorter text", 409)
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

    # ---------- D64: the one-time per-site grants of the browser agent (sitegrants.py) ----------
    # request: makes the approval card (the card text names the site, the exact abilities and the expiry). The owner confirms it with the PIN
    # in the console like any other approval. apply: with that approved approval id it writes the sealed entries; nothing else ever does.
    def _grant_request_from(self, body):
        from . import sitegrants
        try:
            return sitegrants.clean_request(body.get("hosts") or body.get("host"), body.get("abilities"), body.get("days"), body.get("note"), body.get("target") or "pc")
        except sitegrants.GrantError as e:
            raise OwnerError(str(e)[:200], 400)

    def site_grant_request(self, body: dict):
        from . import sitegrants
        from .store import Killed
        req = self._grant_request_from(body)
        a = sitegrants.make_action(req)
        try:
            d, aid = self.k.request(a)
        except Killed:
            raise OwnerError("the kill switch is on", 503) from None
        if d.denied or not aid:
            raise OwnerError("refused by policy: " + d.reason[:160], 403)
        self.k.audit.append("owner_site_grant_requested", {"hosts": req["hosts"], "abilities": req["abilities"], "days": req["days"], "approval": aid})
        return {"status": "needs_approval", "approval_id": aid, "request": req, "card": a.card,
                "how": "approve it in the JARVIS console with your PIN, then ask JARVIS to continue"}

    def site_grant_apply(self, body: dict):
        from . import sitegrants
        from .store import Killed
        aid = str(body.get("approval_id") or "")[:40]
        req = self._grant_request_from(body)
        try:
            d = self.k.authorize(sitegrants.make_action(req), aid)          # consumes the approval: once, for exactly this card
        except Killed:
            raise OwnerError("the kill switch is on", 503) from None
        if not d.allowed:
            self.k.audit.append("owner_site_grant_refused", {"approval": aid, "reason": d.reason[:80]})
            raise OwnerError("the owner has not approved this grant with the PIN: " + d.reason[:100], 403)
        try:
            made = sitegrants.add(req, aid, now=self.clock())
        except sitegrants.GrantError as e:
            raise OwnerError(str(e)[:200], 500)
        self.k.audit.append("owner_site_grant_created", {"hosts": req["hosts"], "abilities": req["abilities"], "days": req["days"], "approval": aid})
        return {"granted": [{"host": e["host"], "allowed": e["allowed"], "expires": e["expires"], "target": e.get("target", "pc")} for e in made]}

    def site_grant_revoke(self, body: dict):
        from . import sitegrants
        try:
            n = sitegrants.revoke(body.get("host"))
        except sitegrants.GrantError as e:
            raise OwnerError(str(e)[:200], 400)
        self.k.audit.append("owner_site_grant_revoked", {"host": str(body.get("host"))[:80], "removed": n})
        return {"removed": n}

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
        if op == "cancel":
            cancel_task_effects(self.k, self.jobs, self.tasks, tid)     # its approvals are withdrawn and its queued/running jobs stopped
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
        denied = self.k.db.execute("UPDATE approvals SET state='denied', decided=?, reason='withdrawn: stop all' "
                                   "WHERE state IN ('pending','approved')", (time.time(),)).rowcount     # approved-but-unused ones too
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
            try:                                # a scheduler fault never stops the rest of the tick (announcements, retention, audit anchor)
                out["automations"] = self.automations.tick()
            except Exception as e:
                out["automations"] = {"error": type(e).__name__}
                self.k.audit.append("owner_tick_error", {"type": type(e).__name__, "part": "automations"})
        if getattr(self, "projectfix", None) is not None:
            out["projectfix"] = self.advance_projectfixes()
        out["announced"] = self.announce_finished()
        out["lessons"] = self.capture_lessons()
        try:                                    # bounded retention of private text (retention.py): about once an hour
            if getattr(self, "retention", None) is None:
                from .retention import Retention
                self.retention = Retention(self.k, self.clock)
            out["retention"] = self.retention.maybe_sweep("tick")
        except Exception as e:                  # never stops the tick
            out["retention"] = {"error": type(e).__name__}
        from .audit import ANCHOR_FILE, export_anchor
        try:                                    # the audit head, copied outside state.db (rate-limited; audit SEC-D8)
            out["anchor"] = export_anchor(self.k.audit, self.k.dir / ANCHOR_FILE, self.clock())
        except OSError as e:
            out["anchor"] = {"error": type(e).__name__}
        return out

    def advance_projectfixes(self, limit: int = PROJECTFIX_TICK):
        """Move each open project fix on as far as it can go (projectfix.py). Bounded: at most `limit` advances per tick,
        least recently moved first, so a long queue takes turns. One broken task never stops the tick: its error is audited,
        it goes to the back of the queue and the next one runs. A fix waiting for an approval the owner has not answered yet
        is skipped (asking again would only add an audit row every tick); approving, denying or expiry is picked up on the
        next tick."""
        pf = self.projectfix
        out = {"advanced": 0, "skipped": 0, "errors": 0}
        try:
            rows = self.k.db.execute("SELECT task, stage, data FROM projectfix WHERE stage NOT IN ('done','failed') "
                                     "ORDER BY updated ASC LIMIT ?", (PROJECTFIX_SCAN,)).fetchall()
        except Exception as e:                      # no table yet (nothing ever started): nothing to do
            out["error"] = type(e).__name__
            return out
        now = time.time()
        for tid, stage, data in rows:
            if out["advanced"] + out["errors"] >= max(1, int(limit)):
                break
            try:
                d = json.loads(data or "{}")
                aid = d.get({"share": "share_approval", "approve": "apply_approval"}.get(stage, ""), "")
                if aid and self.k.db.execute("SELECT 1 FROM approvals WHERE id=? AND state='pending' AND expires>=?",
                                             (aid, now)).fetchone():
                    out["skipped"] += 1
                    continue
                pf.advance(tid)
                out["advanced"] += 1
            except Exception as e:
                out["errors"] += 1
                try:
                    self.k.db.execute("UPDATE projectfix SET updated=? WHERE task=?", (time.time(), tid))   # to the back
                    self.k.audit.append("projectfix_tick_error", {"task": str(tid)[:20], "type": type(e).__name__})
                except Exception:
                    pass
        return out

    def attach_projectfix(self, pf):
        """Owner routes for the build-fix procedure (projectfix.py). Owner plane only: an agent can neither start nor advance
        one. No PIN: these only start or move the procedure on; its own steps keep their approvals (sharing with the lab,
        applying the patch), each approved with the PIN like any other."""
        from .jobs import JobError
        from .projectfix import ProjectFixError
        from .store import Killed
        from .tasks import TaskError
        self.projectfix = pf
        R = self.routes

        def guard(fn):
            def run(body, q):
                try:
                    return fn(body, q)
                except ProjectFixError as e:
                    return (404 if "no such" in str(e) else 400), {"error": str(e)}
                except (JobError, TaskError) as e:
                    return 403, {"error": str(e)[:200]}
                except Killed:
                    return 503, {"error": "the kill switch is on"}
            return run

        def task_of(x):
            tid = str(x.get("task") or x.get("id") or "")
            if not re.fullmatch(r"[0-9a-f]{12}", tid):
                raise ProjectFixError("task must be a project fix's task id")
            return tid

        def start(b, q):
            req = b.get("request")
            if req is not None and not isinstance(req, str):
                raise ProjectFixError("request must be text")
            r = pf.start(str(b.get("project") or ""), scrub((req or "")[:MAX_COMMAND])[0])
            self.k.audit.append("owner_projectfix", {"op": "start", "task": r.get("task")})
            return 201, r

        def advance(b, q):
            return 200, pf.advance(task_of(b))

        def report(b, q):
            if q.get("task"):
                return 200, pf.report(task_of(q))
            rows = self.k.db.execute("SELECT task, project, stage, updated FROM projectfix ORDER BY updated DESC LIMIT 20").fetchall()
            return 200, {"project_fixes": [dict(zip(("task", "project", "stage", "updated"), r)) for r in rows]}
        R[("POST", "project_fix")] = guard(start)
        R[("POST", "project_fix/advance")] = guard(advance)
        R[("GET", "project_fix")] = guard(report)

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
        folder, list the windows, read one window's accessibility tree, take a screenshot, check a project; and (2026-10-16)
        a UI action, a browser action in one of the owner's existing Chrome profiles, a read-only diagnostic, a repair with
        a backup first, or what the PC is playing. The kernel's job validator and policy still decide; the node answers
        with what it actually observed. Ordinary actions run at once (owner_direct); one that needs the owner's approval
        comes back as {"status": "needs_approval", "approval_id"}: approve it with the PIN, then send the same request again
        with that approval_id (it is bound to exactly this action and used once)."""
        from .jobs import JobError, PC_OPS, UI_OPS, BROWSER_OPS, DIAG_OPS, REPAIR_OPS, UNDO_OPS, FILE_OPS, SETTING_OPS, CLIPBOARD_OPS, FILE_TEXT_MAX, action_for
        from .store import Killed
        holder, dup = self._pc_holder(body.get("holder"))
        if dup is not None:                                           # the same request delivered twice: the job it made, no second one
            return dup
        op = str(body.get("op", ""))
        target, path = str(body.get("target", ""))[:80], str(body.get("path", ""))[:200]
        s = lambda k, n: str(body.get(k) or "")[:n]       # noqa: E731
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
        elif op == "ui" and body.get("action") in UI_OPS:
            kind, params = "ui_action", {"agent": "owner", "op": body["action"], "target": s("target", 120),
                                         "text": s("text", 4000 if body["action"] == "type_focused" else 500)}
            if body.get("goal"):                 # D54: the owner's own goal (task-scoped consent); the kernel computes the grant from these words
                params["goal"] = s("goal", 1000)
            if body.get("ctx") is not None:      # the dialog text this click or key is aimed at ("" and "(unreadable dialog)" are asked about)
                params["ctx"] = s("ctx", 600)
        elif op == "file" and body.get("action") in FILE_OPS:        # D54: files in the known folders; the owner's own words ride along (the grant)
            kind, params = "file_op", {"agent": "owner", "op": body["action"], "target": s("target", 120), "path": s("path", 200), "dest": s("dest", 200),
                                       "text": s("text", FILE_TEXT_MAX)}
            if body.get("goal"):
                params["goal"] = s("goal", 1000)
        elif op == "setting" and body.get("action") in SETTING_OPS:
            kind, params = "setting", {"agent": "owner", "op": body["action"], "text": s("text", 300)}
            if body.get("goal"):
                params["goal"] = s("goal", 1000)
        elif op == "clipboard" and body.get("action") in CLIPBOARD_OPS:
            kind, params = "clipboard", {"agent": "owner", "op": body["action"], "text": s("text", 4000)}
            if body.get("goal"):
                params["goal"] = s("goal", 1000)
        elif op == "browser" and body.get("action") in BROWSER_OPS:
            kind, params = "browser_ext", {"profile": s("profile", 32).lower() or "default", "op": body["action"], "url": s("url", 2000),
                                           "target": s("target", 300), "text": s("text", 500)}
            for k in ("host", "via", "ref", "ctx", "task", "ext"):        # D64: the browser agent's optional fields (jobs._browser_ok checks them)
                if body.get(k):
                    params[k] = s(k, 600 if k == "ctx" else 80)
        elif op == "diag" and body.get("check") in DIAG_OPS:
            kind, params = "diag", {"op": body["check"], "target": target}
        elif op == "repair" and body.get("action") in REPAIR_OPS:
            args = body.get("args") if isinstance(body.get("args"), dict) else {}
            kind, params = "repair", {"op": body["action"], "target": s("target", 260), "args": args}
        elif op == "media":
            kind, params = "media_state", {}
        elif op == "undo" and body.get("action") in UNDO_OPS:      # D61: one journaled step reversed (the node re-checks it)
            kind, params = "pc_undo", {"agent": "owner", "op": body["action"], "target": s("target", 260), "path": s("path", 260), "expect": s("expect", 64)}
        else:
            raise OwnerError("op must be open_app, open_folder, open_file, list_folder, windows, snapshot, screenshot, project_check, "
                             "ui (action: " + "|".join(sorted(UI_OPS)) + "), browser (action: " + "|".join(sorted(BROWSER_OPS)) +
                             "), diag (check: " + "|".join(sorted(DIAG_OPS)) + "), repair (action: " + "|".join(sorted(REPAIR_OPS)) +
                             "), undo (action: " + "|".join(sorted(UNDO_OPS)) + ") or media")
        aid = str(body.get("approval_id") or "")[:40] or None
        try:
            if aid is None and kind in ("ui_action", "browser_ext", "diag", "repair", "media_state", "file_op", "setting", "clipboard", "pc_undo"):
                from .jobs import _check_params
                _check_params(kind, params)
                a = action_for(kind, params, "private", untrusted=False, owner_direct=True)
                d, aid = self.k.request(a)
                if d.denied:
                    raise OwnerError("refused by policy: " + d.reason[:160], 403)
                if aid:
                    self.k.audit.append("owner_pc_needs_approval", {"op": op, "kind": kind, "approval": aid})
                    return {"status": "needs_approval", "approval_id": aid, "level": d.name, "reason": d.reason[:200],
                            "action": a.describe(600), "how": "approve it in the JARVIS console with your PIN, then send the same "
                                                              "request again with this approval_id"}
            jid = self.jobs.create(self.node, kind, params, approval_id=aid, owner=None, lane="private", owner_direct=True)
        except JobError as e:
            raise OwnerError(str(e)[:160], 403)
        except Killed:                  # an honest 503 like command(), not a 500 "Killed" logged as an internal error
            raise OwnerError("the kill switch is on", 503) from None
        self.k.audit.append("owner_pc", {"op": op, "job": jid})
        out = {"job": jid, "kind": kind, "node": self.node, "status": "queued"}
        if holder:
            self._pc_seen[holder] = out
            while len(self._pc_seen) > 200:
                self._pc_seen.pop(next(iter(self._pc_seen)))
        return out

    # Fencing (completion A, 2026-10-19). The desktop plugin sends body["holder"] = {token, task, seq} with every PC job request.
    # token = the PC-lock holder's identity; seq = that holder's request counter. A request whose token is NOT the one now in the
    # PC lock file is from a stale executor (its lock was taken over or recovered): refused, no job. The file is read only when
    # the kernel is told where it is (JARVIS_PC_LOCK, or JARVIS_DATA_DIR/jarvis/pc_task.lock); when it is not (kernel
    # and plugin on different machines) only the plugin-side fence and the duplicate rule apply. A (token, seq) seen before is
    # answered with the job it made. Requests without a holder (the console's own buttons) are unchanged.
    def _pc_lock_file(self):
        p = os.environ.get("JARVIS_PC_LOCK")
        if not p and os.environ.get("JARVIS_DATA_DIR"):
            p = os.path.join(os.environ["JARVIS_DATA_DIR"], "jarvis", "pc_task.lock")
        return getattr(self, "pc_lock_path", None) or p

    def _pc_holder(self, h):
        if h is None:
            return None, None
        if not (isinstance(h, dict) and re.fullmatch(r"[0-9a-f]{8,64}", str(h.get("token") or "")) and isinstance(h.get("seq"), int)
                and not isinstance(h.get("seq"), bool) and 0 < h["seq"] < 10**9):
            raise OwnerError("the PC lock holder in this request is malformed", 400)
        key = (h["token"], h["seq"])
        if key in self._pc_seen:
            return key, dict(self._pc_seen[key], duplicate=True)
        path = self._pc_lock_file()
        if path:
            try:
                with open(path, encoding="utf-8") as f:
                    cur = json.load(f).get("token")
            except (OSError, ValueError, AttributeError):
                cur = None
            if cur != h["token"]:
                self.k.audit.append("owner_pc_stale_holder", {"task": str(h.get("task") or "")[:40]})
                raise OwnerError("refused: this request comes from a PC task that no longer holds the PC lock (it was taken over or "
                                 "recovered); nothing was started", 409)
        return key, None

    def cancel_job(self, jid: str):
        """Stop one PC job (no PIN: stopping is the safe direction)."""
        out = self.jobs.cancel(str(jid or "")[:40])
        self.k.audit.append("owner_job_cancel", {"id": out["id"], "state": out["state"]})
        return out

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

            # The owner's own "which provider am I on / switch to <provider>" (plan item 19). The same kernel table and rules as the agents' models.prefer
            # action: the choice only reorders the chain INSIDE the lane (a private lane never reaches a public provider), the kernel authorizes it,
            # and it is written to the audit log. "auto" goes back to the normal order.
            from . import gateway as _gw
            from .policy import Action as _Action
            _LANE_PREFS = "CREATE TABLE IF NOT EXISTS lane_prefs(lane TEXT PRIMARY KEY, provider TEXT, set_at REAL, by_agent TEXT)"

            def lanes_state(b, q):
                self.k.db.execute(_LANE_PREFS)
                out = {}
                for lane in ("private", "public"):
                    try:
                        chain = [p.name for p in _gw.plan(providers, lane)]
                    except _gw.RouteError:
                        chain = []
                    row = self.k.db.execute("SELECT provider, set_at, by_agent FROM lane_prefs WHERE lane=?", (lane,)).fetchone()
                    out[lane] = {"chain": chain, "preferred": row[0] if row else None, "set_at": row[1] if row else None, "by": row[2] if row else None}
                return 200, {"lanes": out}

            def lane_switch(b, q):
                lane, target = str(b.get("lane", "")), str(b.get("provider", "")).strip()
                if lane not in ("private", "public"):
                    raise OwnerError("lane must be private or public")
                self.k.db.execute(_LANE_PREFS)
                if target in ("", "auto"):
                    self.k.db.execute("DELETE FROM lane_prefs WHERE lane=?", (lane,))
                    self.k.audit.append("lane_preference_set", {"lane": lane, "provider": "auto", "agent": "owner"})
                    return 200, {"status": "done", "lane": lane, "preferred": None}
                match = [x for x in providers if x.name == target and x.enabled and _gw.allowed(lane, x.lane)]
                if not match or not match[0].available():
                    return 400, {"error": "no such usable provider in this lane"}
                if not self.k.authorize(_Action("models", "prefer", target, {}, lane, True)).allowed:
                    return 403, {"error": "the kernel refused this switch"}
                self.k.db.execute("INSERT OR REPLACE INTO lane_prefs VALUES (?,?,?,?)", (lane, target, self.clock(), "owner"))
                self.k.audit.append("lane_preference_set", {"lane": lane, "provider": target, "agent": "owner"})
                return 200, {"status": "done", "lane": lane, "preferred": target}
            def router_state(b, q):        # shadow mode: what the router would have picked against the live chain (read only)
                return 200, (self.router_snapshot() if self.router_snapshot else {"mode": "off"})
            R[("GET", "models/router")] = guard(router_state)
            R[("GET", "models/lanes")] = guard(lanes_state)
            R[("POST", "models/switch")] = guard(lane_switch)
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
        R[("GET", "audit/verify")] = guard(lambda b, q: (200, self.audit_verify()))
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

    def audit_verify(self):
        """The audit chain checked against the newest anchor the kernel stored outside state.db (audit SEC-D8: without one a
        cut-off tail passed as "ok"). Says plainly when there is no anchor yet, and that the off-box copy is manual."""
        from .audit import ANCHOR_EVERY, ANCHOR_FILE, latest_anchor
        path = self.k.dir / ANCHOR_FILE
        anchor, bad = latest_anchor(path)
        ok, msg = self.k.audit.verify(anchor)
        out = {"ok": ok, "message": msg, "anchored": anchor is not None,
               "anchor": {"seq": anchor["seq"], "ts": anchor.get("ts")} if anchor else None, "anchor_file": ANCHOR_FILE}
        if anchor is None:
            out["note"] = (f"no anchor yet: the chain itself is checked, but a cut-off tail cannot be seen until the kernel "
                           f"stores its first anchor (about every {ANCHOR_EVERY // 60} minutes while it runs)")
        else:
            out["note"] = ("checked against the newest anchor stored on this machine. Copying the anchor file off the box is "
                           "still a manual owner step (jarvis/scripts/pull-anchor.ps1); only that copy catches someone who "
                           "can also edit the file")
        if bad:
            out["anchor_file_problems"] = f"{bad} unreadable line(s) in the anchor file"
        return out

    # ---------- HTTP-shaped entry point ----------
    def handle(self, method: str, path: str, body: dict, headers=None):
        """(status, reply) for one owner request. The caller has already checked that the token's lane is 'owner'.
        A POST may carry an idempotency key (header Idempotency-Key or body idempotency_key): the same request sent again
        with it returns the first reply instead of acting twice (idempotency.py). Not for a body with a PIN (approve): no
        hash of a PIN is stored, and an approval is used once anyway."""
        if method == "POST" and isinstance(body, dict):
            from . import idempotency
            key = idempotency.key_from(headers, body)
            name = urllib.parse.urlsplit(path).path[len(PREFIX):].strip("/")
            if key is not None and name != "approve" and "pin" not in body:
                if self.idem is None:
                    self.idem = idempotency.Idempotency(self.k, self.clock)
                return self.idem.run("owner:" + name[:60], key, body, lambda: self._handle(method, path, body))
        return self._handle(method, path, body)

    def _handle(self, method: str, path: str, body: dict):
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
                if name == "site_grants":
                    from . import sitegrants
                    return 200, {"sites": sitegrants.active(self.clock())}
            if method == "POST":
                if name == "command":
                    return 202, self.command(body.get("text"), str(body.get("source") or "console"))
                if name == "command/cancel":
                    return 200, self.cancel_command(body.get("id"), stop_tasks=body.get("stop_tasks") is True)
                if name == "approve":
                    return 200, self.approve(body.get("id"), body.get("confirm"), body.get("pin"))
                if name == "deny":
                    return 200, self.deny(body.get("id"))
                if name == "task":
                    return 200, self.task_control(str(body.get("id", "")), str(body.get("op", "")))
                if name == "pc":
                    return 202, self.pc(body)
                if name == "site_grant/request":
                    return 202, self.site_grant_request(body)
                if name == "site_grant/apply":
                    return 200, self.site_grant_apply(body)
                if name == "site_grant/revoke":
                    return 200, self.site_grant_revoke(body)
                if name == "job/cancel":
                    return 200, self.cancel_job(body.get("id"))
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


def _withdraw_task_approvals(kernel, tid) -> dict:
    """Withdraw each approval recorded for task `tid` on its own. Only a missing lab_share_pending table (a kernel without the lab
    wiring) means "nothing to withdraw"; any other database error is raised, and one approval that cannot be withdrawn is
    audited and reported without hiding the others (static finding ST-1: this used to swallow every error)."""
    import sqlite3
    try:
        rows = kernel.db.execute("SELECT p.approval_id FROM lab_share_pending p JOIN approvals a ON a.id = p.approval_id "
                                 "WHERE p.task=? AND a.state IN ('pending','approved')", (str(tid),)).fetchall()
    except sqlite3.OperationalError as e:
        if "no such table" in str(e).lower():
            return {"approvals_withdrawn": [], "approval_failures": []}
        raise
    done, failed = [], []
    for (aid,) in rows:
        try:
            if kernel.withdraw(aid, "its task was cancelled"):
                done.append(aid)
        except Exception as e:                              # noqa: BLE001 - recorded, and the next approval is still tried
            failed.append(aid)
            try:
                kernel.audit.append("approval_withdraw_failed", {"id": aid, "task": str(tid)[:40], "type": type(e).__name__})
            except Exception:                               # noqa: BLE001
                pass
    return {"approvals_withdrawn": done, "approval_failures": failed}


def cancel_task_effects(kernel, jobs, tasks, tid) -> dict:
    """What cancelling a task must also do, for EVERY way of cancelling it (console, Telegram, stop-command): withdraw the
    approvals recorded for it (pending or approved-but-unused) and stop the jobs still queued or running for it, so nothing
    the owner cancelled can still be approved, used or executed (re-check D, 2026-10-19)."""
    res = _withdraw_task_approvals(kernel, tid)
    denied, failed, stopped = res["approvals_withdrawn"], res["approval_failures"], []
    for jid in tasks.job_ids(tid):
        if jobs.cancel(jid)["state"] in ("cancelled", "cancelling"):
            stopped.append(jid)
    return {"approvals_withdrawn": denied, "approval_failures": failed, "jobs_stopped": stopped}


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
