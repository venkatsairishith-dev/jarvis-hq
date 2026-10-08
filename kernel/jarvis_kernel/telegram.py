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


class Bridge:
    def __init__(self, kernel, api, owner_chat_id: int, providers=None, clock=time.time):
        self.k, self.api, self.owner, self.providers, self.clock = kernel, api, int(owner_chat_id), providers or [], clock
        self.ask_times, self.confirm, self.bad_ids = [], {}, []

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
        known = {"/start", "/help", "/status", "/analyze", "/pending", "/approve", "/deny", "/kill", "/resume", "/ask", "confirm", "/recent", "/capabilities", "/can", "/models", "/check"}
        self.k.audit.append("tg_in", {"cmd": cmd if cmd in known else ("ask" if not cmd.startswith("/") else "unknown")})
        if cmd in ("/start", "/help"):
            return self.say("JARVIS commands: /status, /check <project>, /recent, /capabilities, /can <tool> <op> [target], /models, /analyze [node], /pending, /approve <id>, /deny <id>, /kill, /ask <question> (or just type). "
                            "Lifting the kill switch and approving RED actions are done at the kernel console.")
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
        return self.ask(text.strip())

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
                for u in self.api.get_updates(offset):
                    offset = u["update_id"] + 1
                    try:
                        self.handle(u)
                    except Exception as e:  # one bad message must not stop the bridge
                        self.k.audit.append("tg_handler_error", {"type": type(e).__name__})
                backoff = 1
            except Exception as e:
                self.k.audit.append("tg_poll_error", {"msg": str(e)[:80]})
                time.sleep(backoff)
                backoff = min(backoff * 2, 60)
            if once:
                return


def from_files(kernel, providers):
    d = gateway.secrets_dir()
    with open(os.path.join(d, "telegram_bot_token.txt"), encoding="utf-8-sig") as fh:
        token = fh.read().strip()
    with open(os.path.join(d, "telegram_chat_id.txt"), encoding="utf-8-sig") as fh:
        owner = int(fh.read().strip())
    return Bridge(kernel, TelegramAPI(token), owner, providers)
