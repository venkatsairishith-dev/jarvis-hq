"""Telegram bridge: a small DETERMINISTIC command handler inside the kernel's trust boundary.

- Only the owner's private chat is served; everything else is ignored (and counted in the audit log).
- Commands are parsed by fixed code. Model output is sent back as plain text and is never interpreted.
- Approvals: only YELLOW_APPROVAL actions, with a typed confirmation inside 60 s. RED needs the kernel console.
  Lifting the kill switch is console-only. Killing is allowed from here.
- The bot token is read from a file at start-up and never logged or put in an error message.
"""
import hashlib
import json
import os
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

from . import gateway
from .scrub import scrub

MAX_IN = 2000
CHUNK = 3500
ASK_PER_HOUR = 20
CONFIRM_WINDOW = 60
MAX_BAD_IDS = 5


class TelegramAPI:
    def __init__(self, token: str, base: str = "https://api.telegram.org"):
        self._token, self._base = token, base.rstrip("/")

    def _call(self, method: str, payload: dict, timeout: int):
        url = f"{self._base}/bot{self._token}/{method}"
        req = urllib.request.Request(url, data=json.dumps(payload).encode(), headers={"Content-Type": "application/json"}, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read())
        except urllib.error.HTTPError as e:
            raise RuntimeError(f"telegram HTTP {e.code}") from None   # `from None`: the original message holds the URL (and token)
        except Exception as e:
            raise RuntimeError(f"telegram error {type(e).__name__}") from None

    def get_me(self):
        """Cheap start-up check that the token is valid. Returns the bot username."""
        return self._call("getMe", {}, 15).get("result", {}).get("username", "")

    def get_updates(self, offset: int, wait: int = 25):
        return self._call("getUpdates", {"offset": offset, "timeout": wait, "allowed_updates": ["message"]}, wait + 10).get("result", [])

    def send(self, chat_id: int, text: str):
        self._call("sendMessage", {"chat_id": chat_id, "text": text, "disable_web_page_preview": True}, 20)  # no parse_mode: plain text only


OFF_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "config", "telegram.off")


def disabled() -> bool:
    """The owner switched Telegram off (2026-10-10: "no need of Telegram for now"): config/telegram.off exists, or
    JARVIS_TELEGRAM=off. Then the bridge does not run and alerts and reports stay on the server (audit + alerts.log).
    Delete the file to switch Telegram back on."""
    return os.environ.get("JARVIS_TELEGRAM", "").lower() == "off" or os.path.exists(OFF_FILE)


def hermes_agent(key_path: str, base: str = "http://127.0.0.1:8642", session: str = "telegram-owner", timeout: int = 900,
                 state_path: str = None, clock=time.time):
    """The owner's own Hermes (private lane, its kernel-gated tools) through its API server on loopback. One session per
    day, so "open it" refers back to what was said before, and a fresh one after a failed turn: a session that grew past
    what the models accept (or got stuck) must not break every later turn. Durable facts live in Hermes memory and the
    kernel's task records, not in the session. The key is read from its file on every call."""
    def current():
        day = time.strftime("%Y%m%d", time.gmtime(clock()))
        try:
            with open(state_path, encoding="utf-8") as fh:
                sid = fh.read().strip()
            if sid.startswith(f"{session}-{day}"):
                return sid
        except (OSError, TypeError):
            pass
        return f"{session}-{day}"

    def fresh(old):
        sid = f"{session}-{time.strftime('%Y%m%d', time.gmtime(clock()))}-{int(clock())}"
        if state_path:
            with open(state_path, "w", encoding="utf-8") as fh:
                fh.write(sid)
        return sid

    def call(text: str) -> str:
        with open(key_path, encoding="utf-8-sig") as fh:
            key = fh.read().strip()
        sid = current()
        body = {"model": "hermes-agent", "messages": [{"role": "user", "content": text}], "stream": False}
        req = urllib.request.Request(base.rstrip("/") + "/v1/chat/completions", data=json.dumps(body).encode(), method="POST",
                                     headers={"Content-Type": "application/json", "Authorization": "Bearer " + key,
                                              "X-Hermes-Session-Id": sid})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                reply = json.loads(r.read())
        except urllib.error.HTTPError as e:
            fresh(sid)
            raise RuntimeError(f"agent HTTP {e.code}; the next message starts a fresh conversation") from None
        except Exception as e:
            fresh(sid)
            raise RuntimeError(f"agent error {type(e).__name__}; the next message starts a fresh conversation") from None
        return ((reply.get("choices") or [{}])[0].get("message") or {}).get("content") or ""
    call.session = current
    return call


class Bridge:
    def __init__(self, kernel, api, owner_chat_id: int, providers=None, clock=time.time, sleep=time.sleep, agent=None, spawn=None):
        self.k, self.api, self.owner, self.providers, self.clock = kernel, api, int(owner_chat_id), providers or [], clock
        self.sleep = sleep      # the poll back-off only; tests swap this, never the global time.sleep
        self.ask_times, self.confirm, self.bad_ids = [], {}, []
        self.agent = agent      # plain messages go to the owner's Hermes when set; /ask stays a quick model answer
        self.spawn = spawn or (lambda work: threading.Thread(target=work, daemon=True).start())
        self.agent_busy = threading.Lock()

    # ---------- output ----------
    def say(self, text: str):
        clean = scrub(text, deep=True)[0]
        for i in range(0, len(clean) or 1, CHUNK):
            self.api.send(self.owner, clean[i:i + CHUNK])

    # ---------- input ----------
    def handle(self, update: dict):
        m = update.get("message") or {}
        chat, sender = m.get("chat") or {}, m.get("from") or {}
        text = m.get("text")
        if chat.get("id") != self.owner or chat.get("type") != "private" or sender.get("id") != self.owner:
            who = hashlib.sha256(str(chat.get("id")).encode()).hexdigest()[:8]
            self.k.audit.append("tg_ignored", {"chat": who})   # never reply to strangers, never log their text
            return
        if not isinstance(text, str):
            return
        if len(text) > MAX_IN:
            return self.say("Message too long (limit 2000 characters).")
        word = text.strip().split(None, 1)
        cmd = word[0].lower() if word else ""
        arg = word[1].strip() if len(word) > 1 else ""
        known = {"/start", "/help", "/status", "/analyze", "/pending", "/approve", "/deny", "/kill", "/resume", "/ask", "confirm", "/recent", "/capabilities", "/can", "/models", "/check",
                 "/tasks", "/task"}
        self.k.audit.append("tg_in", {"cmd": cmd if cmd in known else ("ask" if not cmd.startswith("/") else "unknown")})
        if cmd in ("/start", "/help"):
            return self.say(("Just type a request (\"open my Downloads folder\", \"open Spotify\") and JARVIS does it. " if self.agent else "") +
                            "JARVIS commands: /status, /tasks, /task <id> [pause|resume|cancel], /check <project>, /recent, /capabilities, /can <tool> <op> [target], /models, /analyze [node], /pending, /approve <id>, /deny <id>, /kill, /ask <question> (a quick model answer"
                            + (")" if self.agent else ", or just type)") + ". Lifting the kill switch and approving RED actions are done at the kernel console.")
        if cmd == "/status":
            return self.status()
        if cmd == "/analyze":
            return self.analyze(arg)
        if cmd == "/pending":
            return self.pending()
        if cmd == "/check":
            return self.check(arg)
        if cmd == "/recent":
            return self.recent()
        if cmd == "/capabilities":
            return self.capabilities()
        if cmd == "/can":
            return self.can(arg)
        if cmd == "/models":
            return self.models()
        if cmd == "/tasks":
            return self.tasks_list()
        if cmd == "/task":
            return self.task_control(arg)
        if cmd == "/approve":
            return self.approve_start(arg)
        if cmd == "confirm":
            return self.approve_confirm(arg)
        if cmd == "/deny":
            return self.deny(arg)
        if cmd == "/kill":
            self.k.kill("telegram")
            return self.say("Kill switch is ON. All actions and approvals are blocked. Lift it at the kernel console.")
        if cmd == "/resume":
            return self.say("Lifting the kill switch needs the kernel console, not Telegram.")
        if cmd == "/ask":
            return self.ask(arg)
        if cmd.startswith("/"):
            return self.say("Unknown command. Try /help.")
        return self.agent_turn(text.strip()) if self.agent else self.ask(text.strip())

    # ---------- commands ----------
    def status(self):
        from .nodes import Nodes
        nodes = Nodes(self.k).list()
        lines = [f"Kill switch: {'ON' if self.k.killed() else 'off'}", f"Audit events: {self.k.audit.anchor()['seq']}"]
        lines += [f"Node {n['name']}: {'online' if n['online'] else 'offline'} (last report {n['seconds_since_report']} s ago)" for n in nodes] or ["No nodes paired."]
        avail = [p.name for p in self.providers if p.available()]
        lines.append("Model providers available: " + (", ".join(avail) or "none"))
        from .scrub import gitleaks_path
        lines.append("Secret scanner: " + ("gitleaks deep pass + built-in" if gitleaks_path() else "built-in only (gitleaks not installed)"))
        n = self.k.db.execute("SELECT COUNT(*) FROM approvals WHERE state='pending' AND expires>=?", (self.clock(),)).fetchone()[0]
        lines.append(f"Pending approvals: {n}")
        self.say("\n".join(lines))

    def analyze(self, node_name: str):
        from .nodes import Nodes
        from .analyze import build, unmatched_numbers
        from .policy import Action
        d = self.k.authorize(Action("state", "read", node_name or "any-node"))
        if not d.allowed:
            return self.say("Refused: " + d.reason)
        nodes = [n for n in Nodes(self.k).list() if not node_name or n["name"] == node_name]
        if not nodes:
            return self.say("No node has reported yet.")
        for n in nodes:
            text = build(n)
            bad = unmatched_numbers(text, n) if n["state"] else []
            self.say(text + f"\n[check] numbers not found in collected state: {bad or 'none'}")

    def recent(self, hours=24):
        """What happened while the owner was away: a summary of the audit log, no message text, no secrets."""
        since = self.clock() - hours * 3600
        rows = self.k.db.execute("SELECT ts, kind, payload FROM audit WHERE ts>=? ORDER BY seq", (since,)).fetchall()
        if not rows:
            return self.say(f"Nothing recorded in the last {hours} hours.")
        from collections import Counter
        c = Counter(k for _, k, _ in rows)
        denied = sum(1 for _, k, p in rows if k == "authorize" and '"allowed": false' in p)
        lines = [f"Last {hours} hours: {len(rows)} audit events.",
                 f"Messages from you: {c.get('tg_in', 0)}; model calls: {c.get('model_call', 0)} ok, {c.get('model_call_failed', 0)} failed",
                 f"Actions refused by policy: {denied}; kill switch used: {c.get('kill', 0)}; approvals given: {c.get('approved', 0) + c.get('tg_approved', 0)}",
                 f"Node reports: {c.get('node_report', 0)}; vault writes: {c.get('vault_write', 0)} ok, {c.get('vault_write_refused', 0)} refused; strangers ignored: {c.get('tg_ignored', 0)}"]
        notable_kinds = ("monit_alert", "kill", "resume", "nightly_run", "approved", "tg_approved", "pair_refused", "report_refused",
                         "tg_backlog_dropped", "tg_start_check_failed")
        notable = [(ts, k, p) for ts, k, p in rows if k in notable_kinds]
        if notable:
            lines.append("Notable:")
            for ts, k, p in notable[-8:]:
                try:
                    d = json.loads(p)
                except ValueError:
                    d = {}
                brief = ", ".join(f"{a}={str(b)[:30]}" for a, b in d.items() if a in ("service", "event", "kind", "why", "flags", "sets"))
                lines.append(f"- {time.strftime('%H:%M', time.gmtime(ts))} UTC {k} {brief}".rstrip())
        else:
            lines.append("Nothing notable (no alerts, kills or approvals).")
        self.say("\n".join(lines))

    def capabilities(self):
        """What JARVIS can do right now, from the real policy table, providers and nodes (not a guess)."""
        from .policy import RULES, NAMES
        from .nodes import Nodes
        by = {}
        for tool, ops, lvl in RULES:
            by.setdefault(NAMES[lvl], []).append(f"{tool}.{ops}")
        lines = ["By permission level (from the policy table):"]
        for lvl in ("GREEN", "YELLOW_AUTO", "YELLOW_APPROVAL", "RED"):
            if lvl in by:
                lines.append(f"{lvl}: " + ", ".join(by[lvl]))
        lines.append("Always refused (DENY): secrets and key files, password managers/wallets/banking sites, internal addresses, "
                     "rule/hook/settings files for agents, the protected server, egress after reading untrusted content.")
        lines.append("Unknown actions fail closed as RED (console approval).")
        avail = [f"{p.name} ({p.lane})" for p in self.providers if p.available()]
        lines.append("Model providers up now: " + (", ".join(avail) or "none"))
        nodes = Nodes(self.k).list()
        for n in nodes:
            tools = [t for t, v in (n["state"].get("tools") or {}).items() if v.get("present")]
            lines.append(f"Node {n['name']} is {'online' if n['online'] else 'offline'}; tools there: " + (", ".join(tools) or "unknown"))
        if not nodes:
            lines.append("No nodes paired.")
        self.say("\n".join(lines))

    def can(self, arg: str):
        """/can <tool> <op> [target]: a verdict from the policy table. It does not run anything."""
        parts = arg.split(None, 2)
        if len(parts) < 2:
            return self.say("Usage: /can <tool> <op> [target], for example: /can fs write notes.md")
        from .policy import Action, decide
        d = decide(Action(parts[0], parts[1], parts[2] if len(parts) > 2 else ""), None, self.k.protected)
        verdict = {"GREEN": "YES", "YELLOW_AUTO": "YES (within limits)", "YELLOW_APPROVAL": "YES_WITH_APPROVAL (you can approve from Telegram)",
                   "RED": "YES_WITH_CONSOLE_APPROVAL (needs the kernel console)", "DENY": "NO"}[d.name]
        self.say(f"{verdict}\nLevel: {d.name}. Reason: {d.reason}")

    def _project_names(self):
        path = os.path.join(os.path.dirname(__file__), "..", "..", "config", "projects.json")
        try:
            with open(path, encoding="utf-8-sig") as fh:
                return sorted(json.load(fh)["projects"])
        except (OSError, ValueError, KeyError):
            return []

    def check(self, arg: str):
        """/check <project>: queue a fixed project check (git status, recent commits, the project's own test command) on the laptop."""
        from .jobs import Jobs, JobError
        from .nodes import Nodes
        from .store import Killed
        names = self._project_names()
        name = arg.strip() or (names[0] if len(names) == 1 else "")
        if name not in names:
            return self.say("Known projects: " + (", ".join(names) or "none") + ". Usage: /check <project>")
        nodes = [n for n in Nodes(self.k).list() if (n["state"] or {}).get("os")]       # laptop-type nodes report a full state
        if not nodes:
            return self.say("No laptop node has reported yet.")
        node = nodes[0]
        try:
            Jobs(self.k).create(node["name"], "project_check", {"project": name})
        except Killed:
            return self.say("Kill switch is on: no jobs.")
        except JobError as e:
            return self.say("Refused: " + str(e)[:150])
        self.say(f"Queued a check of {name} on {node['name']} ({'online' if node['online'] else 'offline: it runs when the laptop is back, within 30 minutes'}). "
                 "I will message you with the result. Nothing will be changed.")

    def flush_jobs(self):
        """Tell the owner about finished or expired jobs, once each."""
        from .jobs import Jobs, report_text
        from .analyze import _NUM, _flatten
        jobs = Jobs(self.k)
        for jid, kind, state, params, result in jobs.to_report():
            project = params.get("project", "?")
            if state == "expired":
                text = f"Project check for {project} expired: the laptop did not pick it up within 30 minutes."
            else:
                text = report_text(project, result)
                allowed = set(); _flatten(result, allowed)
                bad = [m for m in _NUM.findall(text) if m not in allowed]
                text += f"\n[check] numbers not found in the result: {bad or 'none'}"
            self.say(text)
            jobs.mark_notified(jid)
            self.k.audit.append("job_reported", {"id": jid, "state": state})

    def flush_approvals(self):
        """Tell the owner, once each, about new actions waiting for approval (e.g. an agent asking to use the PC)."""
        self.k.db.execute("CREATE TABLE IF NOT EXISTS tg_approval_notices(id TEXT PRIMARY KEY)")
        rows = self.k.db.execute("SELECT id FROM approvals WHERE state='pending' AND expires>=? AND id NOT IN "
                                 "(SELECT id FROM tg_approval_notices)", (self.clock(),)).fetchall()
        for (aid,) in rows:
            i = self.k.approval_info(aid)
            if not i:
                continue
            a = i["action"]
            extra = json.dumps(a.get("params") or {})[:200]
            self.say(f"Approval needed ({i['level']}): {a['tool']}.{a['op']} {str(a['target'])[:80]} {extra if extra != '{}' else ''}\n"
                     f"Approve: /approve {aid}    Deny: /deny {aid}    ({i['seconds_left']} s left)")
            self.k.db.execute("INSERT OR IGNORE INTO tg_approval_notices VALUES (?)", (aid,))
            self.k.audit.append("tg_approval_notice", {"id": aid})

    def tasks_list(self):
        from .tasks import Tasks
        rows = Tasks(self.k).recent(8)
        if not rows:
            return self.say("No tasks yet.")
        self.say("Tasks (newest first):\n" + "\n".join(
            f"- {t['id']} {t['state']} {t['attempts']}/{t['max_attempts']}: {str(t['goal'])[:70]}" for t in rows))

    def task_control(self, arg: str):
        """The owner's own control of a task, straight to the kernel (no model in between): show it, pause it (the lab
        freezes the worker), resume it, or cancel it (the lab stops the worker)."""
        from .tasks import Tasks, TaskError
        parts = arg.split()
        T = Tasks(self.k)
        t = T.get(parts[0]) if parts else None
        if not t:
            return self.say("Usage: /task <id> [pause|resume|cancel]. /tasks lists them.")
        op = parts[1].lower() if len(parts) > 1 else ""
        if not op:
            ev = "\n".join(f"  {time.strftime('%H:%M', time.localtime(e['ts']))} {e['state']}: {str(e['note'])[:90]}" for e in t["events"][:6])
            return self.say(f"Task {t['id']}: {t['state']}, attempt {t['attempts']}/{t['max_attempts']}\nGoal: {t['goal']}\n"
                            f"Step: {t['step'] or '-'}\nResult: {t['result'] or '-'}\nLatest:\n{ev}")
        target = {"pause": "paused", "cancel": "cancelled", "resume": "running"}.get(op)
        if not target:
            return self.say("Use pause, resume or cancel.")
        if op == "resume" and t["state"] != "paused":
            return self.say(f"Task {t['id']} is {t['state']}, not paused.")
        try:
            T.update(None, t["id"], state=target, note=f"{op} by the owner (Telegram)")
        except TaskError as e:
            return self.say(f"Refused: {e}")
        self.k.audit.append("tg_task_control", {"id": t["id"], "op": op})
        self.say(f"Task {t['id']}: {target}." + (" The lab stops the worker within about 20 s." if op == "cancel" else
                                                  " The lab freezes the worker within about 20 s." if op == "pause" else ""))

    def with_context(self, text: str) -> str:
        """The first message of a new conversation (a new day, or a fresh one after a failed turn) carries the kernel's
        record of the owner's tasks, so a session change never loses track of work in progress. Preferences need no
        card: they live in the agent's own memory, which every session loads."""
        sid = self.agent.session() if hasattr(self.agent, "session") else None
        if sid is None or sid == getattr(self, "_seen_session", None):
            return text
        self._seen_session = sid
        try:
            from .tasks import Tasks
            rows = [t for t in Tasks(self.k).recent(12) if t["state"] not in ("completed", "failed", "cancelled")
                    or time.time() - t["updated"] < 86400][:6]
        except Exception:
            rows = []
        if not rows:
            return text
        card = "\n".join(f"- {t['id']} {t['state']} ({t['attempts']}/{t['max_attempts']}): {str(t['goal'])[:90]}" for t in rows)
        self.k.audit.append("tg_context_card", {"tasks": len(rows)})
        return (f"[JARVIS kernel] New conversation. Tasks open or ended in the last 24 h (task tool, op get, for details):\n"
                f"{card}\n\n{text}")

    def task_facts(self, since: float) -> str:
        """The kernel's own record of tasks touched during this turn, appended to the agent's reply: a claim like
        "done" cannot hide a task that was never verified."""
        try:
            rows = self.k.db.execute("SELECT id, goal, state, verification FROM tasks WHERE updated >= ? ORDER BY updated", (since - 1,)).fetchall()
        except Exception:
            return ""
        lines = []
        for tid, goal, state, ver in rows:
            v = json.loads(ver or "{}")
            check = ("verified" if v.get("passed") is True else "NOT verified yet") if state != "failed" else "failed"
            lines.append(f"- task {tid} ({str(goal)[:60]}): {state}, {check}")
        return ("\n\nKernel record:\n" + "\n".join(lines)) if lines else ""

    def flush_tasks(self):
        """When lab work for a task has ended (completed or failed), JARVIS hears it in the owner's conversation, checks
        it against the acceptance criteria and tells the owner. Without an agent the owner gets a plain notice."""
        from .tasks import Tasks
        T = Tasks(self.k)
        T.expire()
        self.k.db.execute("CREATE TABLE IF NOT EXISTS tg_task_notices(id TEXT PRIMARY KEY)")
        rows = self.k.db.execute(
            "SELECT id FROM tasks WHERE state IN ('completed','failed','cancelled') AND id NOT IN (SELECT id FROM tg_task_notices) "
            "AND EXISTS (SELECT 1 FROM jobs WHERE jobs.kind='lab_run' AND jobs.params LIKE '%' || tasks.id || '%')").fetchall()
        for (tid,) in rows:
            if self.agent and self.agent_busy.locked():
                return                                   # JARVIS is busy: tell it on a later loop, not a bare notice
            t = T.get(tid)
            self.k.db.execute("INSERT OR IGNORE INTO tg_task_notices VALUES (?)", (tid,))
            ver = t.get("verification") or {}
            summary = (f"Lab work for task {tid} ended: {t['state'].upper()} after {t['attempts']} attempt(s).\n"
                       f"Goal: {t['goal']}\nResult: {t['result'] or '-'}\n"
                       f"Verification: {json.dumps(ver, ensure_ascii=False)[:1200]}\n"
                       f"Evidence: {'; '.join(t.get('evidence') or [])[:1200]}")
            self.k.audit.append("tg_task_notice", {"id": tid, "state": t["state"]})
            if self.agent and self.agent_busy.acquire(blocking=False):
                prompt = ("[JARVIS kernel] " + summary + "\nCheck this against the task's acceptance criteria (task tool, op get), "
                          "close the task honestly if it is still open, deliver files if needed, record any reusable lesson, "
                          "and tell the owner the outcome in a few lines, including what remains if it failed.")

                def work(prompt=prompt, summary=summary):
                    try:
                        self.say(self.agent(self.with_context(prompt)).strip() or summary)
                    except Exception as e:
                        self.k.audit.append("tg_agent_failed", {"type": type(e).__name__})
                        self.say(summary)
                    finally:
                        self.agent_busy.release()
                self.spawn(work)
            else:
                self.say(summary)

    def models(self):
        path = os.path.join(os.path.dirname(__file__), "..", "..", "config", "models.json")
        try:
            with open(path, encoding="utf-8-sig") as fh:
                reg = json.load(fh)["models"]
        except (OSError, ValueError, KeyError):
            return self.say("Model registry file not found.")
        lines = ["Model registry (measured facts only):"]
        for m in reg:
            speed = f", {m['tokens_per_s']} tokens/s" if "tokens_per_s" in m else ""
            lines.append(f"- {m['id']}: {m['status']}; {m['where']}{speed}; {m['purpose']}")
        self.say("\n".join(lines))

    def pending(self):
        rows = self.k.db.execute("SELECT id FROM approvals WHERE state='pending' AND expires>=? ORDER BY expires DESC LIMIT 10", (self.clock(),)).fetchall()
        if not rows:
            return self.say("No pending approvals.")
        out = []
        for (aid,) in rows:
            i = self.k.approval_info(aid)
            out.append(f"{aid}  {i['level']}  {i['action']['tool']}.{i['action']['op']} {str(i['action']['target'])[:60]}  ({i['seconds_left']} s left)")
        self.say("\n".join(out))

    def _bad_id(self, aid):
        now = self.clock()
        self.bad_ids = [t for t in self.bad_ids if now - t < 600] + [now]
        self.k.audit.append("tg_bad_approval_id", {})
        return len(self.bad_ids) >= MAX_BAD_IDS

    def _locked(self):
        now = self.clock()
        return len([t for t in self.bad_ids if now - t < 600]) >= MAX_BAD_IDS

    def approve_start(self, aid: str):
        if self.k.killed():
            return self.say("Kill switch is on: no approvals.")
        if self._locked():
            return self.say("Too many wrong ids. Approvals from Telegram are locked for 10 minutes.")
        info = self.k.approval_info(aid) if aid else None
        if not info or info["state"] != "pending":
            self._bad_id(aid)
            return self.say("No such pending approval.")
        if info["level"] != "YELLOW_APPROVAL":
            return self.say(f"{info['level']} actions are approved at the kernel console, not here.")
        self.confirm[aid] = self.clock()
        a = info["action"]
        self.say(f"Approve this {info['level']} action?\n{json.dumps(a, indent=1)[:1500]}\nTo confirm within {CONFIRM_WINDOW} s send exactly: CONFIRM {aid}")

    def approve_confirm(self, aid: str):
        if self.k.killed():
            return self.say("Kill switch is on: no approvals.")
        t = self.confirm.pop(aid, None)
        if t is None or self.clock() - t > CONFIRM_WINDOW:
            return self.say("No recent /approve for that id. Start with /approve <id>.")
        info = self.k.approval_info(aid)
        if not info or info["level"] != "YELLOW_APPROVAL":
            return self.say("Cannot approve that here.")
        ok = self.k.approve(aid)
        self.k.audit.append("tg_approved" if ok else "tg_approve_failed", {"id": aid})
        self.say("Approved. It is single use and expires soon." if ok else "Could not approve (expired or already used).")

    def deny(self, aid: str):
        cur = self.k.db.execute("UPDATE approvals SET state='denied' WHERE id=? AND state='pending'", (aid,))
        self.k.audit.append("tg_denied", {"id": aid, "ok": cur.rowcount == 1})
        self.say("Denied." if cur.rowcount == 1 else "No such pending approval.")

    def agent_turn(self, text: str):
        """A plain message is a request to JARVIS itself: the owner's Hermes does it with its tools (open an app or a
        folder on the laptop, save and show outputs...), each action still decided by the kernel's policy. It runs in
        the background, so /kill, /approve and job reports keep working while the agent thinks."""
        if not text:
            return
        if self.k.killed():
            return self.say("Kill switch is on: the agent does nothing.")
        now = self.clock()
        self.ask_times = [t for t in self.ask_times if now - t < 3600]
        if len(self.ask_times) >= ASK_PER_HOUR:
            return self.say("Request limit reached (20 per hour). Try later.")
        if not self.agent_busy.acquire(blocking=False):
            return self.say("Still working on your last request. /kill stops everything.")
        self.ask_times.append(now)
        self.k.audit.append("tg_agent_turn", {"chars": len(text)})
        self.say("On it.")

        def work():
            t0 = time.time()
            try:
                out = self.agent(self.with_context(text)).strip()
                self.k.audit.append("tg_agent_done", {"chars": len(out)})
                self.say((out or "Done (no reply text).") + self.task_facts(t0))
            except Exception as e:
                self.k.audit.append("tg_agent_failed", {"type": type(e).__name__})
                self.say("JARVIS could not finish that (" + str(e)[:80] + "). /ask still gets a quick model answer.")
            finally:
                self.agent_busy.release()
        self.spawn(work)

    def ask(self, text: str):
        if not text:
            return self.say("Ask something after /ask, or just type your question.")
        if self.k.killed():
            return self.say("Kill switch is on: no model calls.")
        now = self.clock()
        self.ask_times = [t for t in self.ask_times if now - t < 3600]
        if len(self.ask_times) >= ASK_PER_HOUR:
            return self.say("Question limit reached (20 per hour). Try later.")
        self.ask_times.append(now)
        try:
            out = gateway.call(self.k, self.providers, "private", text, max_tokens=600, timeout=60)
        except gateway.RouteError as e:
            return self.say("No answer: " + str(e)[:200])
        self.say("(model answer, unverified)\n" + out.strip())

    # ---------- loop ----------
    def run(self, once=False):
        offset = 0
        backoff = 1
        if hasattr(self.api, "get_me"):
            try:
                self.k.audit.append("tg_start_check", {"bot": self.api.get_me()[:40]})
            except Exception as e:
                self.k.audit.append("tg_start_check_failed", {"msg": str(e)[:80]})
                raise SystemExit("Telegram token rejected or unreachable at start-up")
        try:
            self.flush_jobs()
        except Exception as e:
            self.k.audit.append("tg_handler_error", {"type": type(e).__name__})
        try:  # a restart must never replay old messages (an old /kill or /approve): acknowledge and drop everything queued
            newest = self.api.get_updates(-1, wait=0)
            if newest:
                offset = newest[-1]["update_id"] + 1
                self.k.audit.append("tg_backlog_dropped", {"up_to": offset})
        except Exception as e:
            self.k.audit.append("tg_poll_error", {"msg": str(e)[:80]})
        self.say("JARVIS kernel online.")
        while True:
            try:
                self.flush_jobs()
                self.flush_approvals()
                self.flush_tasks()
                for u in self.api.get_updates(offset):
                    offset = u["update_id"] + 1
                    try:
                        self.handle(u)
                    except Exception as e:  # one bad message must not stop the bridge
                        self.k.audit.append("tg_handler_error", {"type": type(e).__name__})
                backoff = 1
            except Exception as e:
                self.k.audit.append("tg_poll_error", {"msg": str(e)[:80]})
                self.sleep(backoff)
                backoff = min(backoff * 2, 60)
            if once:
                return


def from_files(kernel, providers):
    d = gateway.secrets_dir()
    with open(os.path.join(d, "telegram_bot_token.txt"), encoding="utf-8-sig") as fh:
        token = fh.read().strip()
    with open(os.path.join(d, "telegram_chat_id.txt"), encoding="utf-8-sig") as fh:
        owner = int(fh.read().strip())
    key = os.path.join(d, "hermes_api_key.txt")      # present on the server: plain messages go to the owner's Hermes
    agent = hermes_agent(key, os.environ.get("JARVIS_HERMES_API", "http://127.0.0.1:8642"),
                         state_path=os.path.join(str(kernel.dir), "tg_session.txt")) if os.path.exists(key) else None
    return Bridge(kernel, TelegramAPI(token), owner, providers, agent=agent)
