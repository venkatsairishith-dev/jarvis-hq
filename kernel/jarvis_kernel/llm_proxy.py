"""OpenAI-compatible model endpoint for the agent runtime, so the agent NEVER holds a provider key.

Listens on 127.0.0.1, plus the Tailscale address when started with --bind. The agent authenticates with its own bearer
token (hash stored in the kernel DB, created by `jarvis agent-token`), and the token fixes the lane (private for the owner's
agent, public for test agents). Each request goes through the existing gateway rules: that lane only, free-model pattern,
provider priority and fallback (fallback only before any byte has been streamed back), secret scrubbing of every message
and tool-call argument, an output-token cap, a rate limit, and an audit entry with counts only. The provider's response,
including tool calls and streamed chunks, is relayed unchanged.
"""
import hashlib
import hmac
import json
import os
import re
import secrets
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import gateway
from .scrub import scrub

MAX_BODY = 2 * 1024 * 1024
MAX_OUT_TOKENS = 4096
PER_MINUTE = 30
PER_DAY = 2000
# A provider's 5xx is tried once more before falling back: Ollama answers a gpt-oss tool call it cannot parse with
# HTTP 500 "error parsing tool call" (ollama/ollama#12884), and a fresh sample usually parses.
RETRY_SAME_PROVIDER = {500, 502, 503}
MAX_OUTPUT_CHARS = 200_000
# Agent actions (POST /v1/jarvis/actions): every one goes through kernel policy; jobs run on the owner's laptop node.
OUTPUT_NODE = os.environ.get("JARVIS_OUTPUT_NODE", "laptop")
OWNER_PATH = re.compile(r"(?i)(?<![a-z0-9])[a-z]:[\\/]|/mnt/[a-z]/|\\\\[a-z0-9]")   # D:\, C:/, /mnt/d/, \\server
ACTIONS_PER_HOUR = 60
SHOWS_PER_HOUR = 12
PREFERS_PER_DAY = 20


def _h(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()


class AgentTokens:
    """Each agent token is bound to ONE lane. The agent cannot choose it: private tokens (the owner's agent) reach only
    local and private providers; public tokens (test agents, never given private data) reach only local and public ones."""

    def __init__(self, kernel):
        self.k, self.lock = kernel, threading.Lock()
        kernel.db.execute("CREATE TABLE IF NOT EXISTS agent_tokens(name TEXT PRIMARY KEY, token_h TEXT, created REAL)")
        cols = [r[1] for r in kernel.db.execute("PRAGMA table_info(agent_tokens)").fetchall()]
        if "lane" not in cols:   # older databases: every existing token stays private
            kernel.db.execute("ALTER TABLE agent_tokens ADD COLUMN lane TEXT NOT NULL DEFAULT 'private'")

    def issue(self, name: str, lane: str = "private") -> str:
        """lane "dashboard": the owner's hub reads the overview with it and can do nothing else (no model, no action).
        lane "runner": the lab runner fetches lab jobs and reports task progress with it, nothing else."""
        if lane not in ("private", "public", "dashboard", "runner"):
            raise ValueError("lane must be private, public, dashboard or runner")
        tok = secrets.token_urlsafe(32)
        self.k.db.execute("INSERT OR REPLACE INTO agent_tokens(name, token_h, created, lane) VALUES (?,?,?,?)", (name, _h(tok), time.time(), lane))
        self.k.audit.append("agent_token_issued", {"name": name, "lane": lane})
        return tok

    def identify(self, tok: str):
        """(name, lane) for a valid token, else (None, None)."""
        th = _h(tok)
        with self.lock:                          # one shared connection, many server threads: never iterate a live cursor
            rows = self.k.db.execute("SELECT name, token_h, lane FROM agent_tokens").fetchall()
        for name, h, lane in rows:
            if hmac.compare_digest(h, th):
                return name, lane
        return None, None


def _scrub_messages(body: dict):
    """Scrub every text the agent sends to a model. Returns (body, findings, blocked)."""
    found, blocked = [], False

    def clean(s):
        nonlocal blocked
        c, f, b = scrub(s, deep=True)
        found.extend(f)
        blocked = blocked or b
        return c

    for m in body.get("messages") or []:
        if isinstance(m.get("content"), str):
            m["content"] = clean(m["content"])
        elif isinstance(m.get("content"), list):
            for part in m["content"]:
                if isinstance(part, dict) and isinstance(part.get("text"), str):
                    part["text"] = clean(part["text"])
        for tc in m.get("tool_calls") or []:
            fn = tc.get("function") or {}
            if isinstance(fn.get("arguments"), str):
                fn["arguments"] = clean(fn["arguments"])
    return body, found, blocked


def _recent(times: list, now: float, window: float) -> list:
    return [t for t in times if now - t < window]


def make_handler(kernel, providers, clock=time.time):
    from .jobs import PC_OPS, UI_OPS, Jobs, JobError, action_for
    from .policy import Action
    from .store import Killed
    tokens = AgentTokens(kernel)
    jobs = Jobs(kernel)
    from .tasks import Tasks, TaskError
    tasks = Tasks(kernel)
    kernel.db.execute("CREATE TABLE IF NOT EXISTS lane_prefs(lane TEXT PRIMARY KEY, provider TEXT, set_at REAL, by_agent TEXT)")
    lock = threading.Lock()
    dispatch_lock = threading.Lock()            # one dispatch decision at a time (the server answers on many threads)
    calls = {}   # agent -> list of timestamps
    acts, shows, prefers = {}, {}, {}   # agent -> timestamps, per action limit

    def preferred(lane):
        r = kernel.db.execute("SELECT provider FROM lane_prefs WHERE lane=?", (lane,)).fetchone()
        return r[0] if r else None

    def agent_action(agent, lane, body):
        """One agent action: (http status, reply). Lane comes from the token, never from the request."""
        tool, op = str(body.get("tool", "")), str(body.get("op", ""))
        target = str(body.get("target", ""))[:200]
        p = body.get("params") if isinstance(body.get("params"), dict) else {}
        now = clock()
        with lock:
            ts = _recent(acts.get(agent, []), now, 3600)
            if len(ts) >= ACTIONS_PER_HOUR:
                return 429, {"status": "refused", "reason": "action limit (60 per hour)"}
            ts.append(now); acts[agent] = ts
        try:
            if tool == "output" and op in ("save", "show"):
                params = {"agent": agent, "name": target}
                if op == "save":
                    at_lab = kernel.db.execute("SELECT json_extract(params,'$.task') FROM jobs WHERE kind='lab_run' AND owner=? "
                                               "AND state IN ('queued','running')", (agent,)).fetchone()
                    if at_lab:                          # no second, unverified copy racing the lab's verified delivery
                        return 409, {"status": "refused", "reason": f"task {at_lab[0]} is at the lab: its verified files arrive in "
                                     f"D:\\jarvis\\outputs\\task-{at_lab[0]}\\ by themselves. Do not write them yourself; end your turn."}
                    if lane == "private" and str(target).lower().endswith(".py"):
                        # the owner's agent cannot run code: code reaches the owner only through a verified lab task
                        return 409, {"status": "refused", "reason": "code you cannot run is never delivered: open a task (task_start) "
                                     "and hand it to the lab (lab_work); verified files are delivered by the runner"}
                    clean, found, blocked = scrub(str(p.get("content", "")), deep=True)
                    if blocked:
                        return 400, {"status": "refused", "reason": "the content contains a private key"}
                    params["content"] = clean
                else:
                    with lock:
                        s = _recent(shows.get(agent, []), now, 3600)
                        if len(s) >= SHOWS_PER_HOUR:
                            return 429, {"status": "refused", "reason": "show limit (12 per hour)"}
                        s.append(now); shows[agent] = s
                jid = jobs.create(OUTPUT_NODE, "output_" + op, params, owner=agent, lane=lane)
                return 200, {"status": "queued", "job": jid}
            if tool == "ui" and op in UI_OPS:
                params = {"agent": agent, "op": op, "target": target, "text": str(p.get("text", ""))[:500]}
                aid = str(body.get("approval_id") or "") or None
                if not aid:
                    d, aid = kernel.request(action_for("ui_action", params, lane, untrusted=True))
                    if d.denied:
                        return 403, {"status": "refused", "level": d.name, "reason": d.reason}
                    if aid:
                        return 202, {"status": "needs_approval", "approval_id": aid, "level": d.name,
                                     "next": "the owner approves on Telegram (/approve " + aid + "); then send the same action again with approval_id"}
                jid = jobs.create(OUTPUT_NODE, "ui_action", params, approval_id=aid, owner=agent, lane=lane)
                return 200, {"status": "queued", "job": jid}
            if tool == "pc" and op in PC_OPS:
                params = {"agent": agent, "op": op, "target": target[:80], "path": str(p.get("path", ""))[:200]}
                aid = str(body.get("approval_id") or "") or None
                if not aid:
                    d, aid = kernel.request(action_for("pc_open", params, lane, untrusted=True))
                    if d.denied:
                        return 403, {"status": "refused", "level": d.name, "reason": d.reason}
                    if aid:
                        return 202, {"status": "needs_approval", "approval_id": aid, "level": d.name,
                                     "next": "the owner approves on Telegram (/approve " + aid + "); then send the same action again with approval_id"}
                jid = jobs.create(OUTPUT_NODE, "pc_open", params, approval_id=aid, owner=agent, lane=lane)
                return 200, {"status": "queued", "job": jid}
            if tool == "models" and op == "status":
                if not kernel.authorize(Action("models", "status", lane, {}, lane, True)).allowed:
                    return 403, {"status": "refused"}
                pref = preferred(lane)
                return 200, {"status": "done", "lane": lane, "preferred": pref, "providers": [
                    {"name": x.name, "available": x.available(), "priority": x.priority, "model": x.default_model}
                    for x in providers if x.enabled and gateway.allowed(lane, x.lane)]}
            if tool == "models" and op == "prefer":
                match = [x for x in providers if x.name == target and x.enabled and gateway.allowed(lane, x.lane)]
                if not match or not match[0].available():
                    return 400, {"status": "refused", "reason": "no such usable provider in this lane"}
                with lock:
                    pt = _recent(prefers.get(agent, []), now, 86400)
                    if len(pt) >= PREFERS_PER_DAY:
                        return 429, {"status": "refused", "reason": "switch limit (20 per day)"}
                    pt.append(now); prefers[agent] = pt
                if not kernel.authorize(Action("models", "prefer", target, {}, lane, True)).allowed:
                    return 403, {"status": "refused"}
                kernel.db.execute("INSERT OR REPLACE INTO lane_prefs VALUES (?,?,?,?)", (lane, target, now, agent))
                kernel.audit.append("lane_preference_set", {"lane": lane, "provider": target, "agent": agent})
                return 200, {"status": "done", "preferred": target}
            if tool == "task":
                return task_action(agent, lane, op, target, p)
            if tool == "lab" and op == "task":
                d = kernel.authorize(Action("lab", "task", target, {"chars": len(str(p.get("prompt", "")))}, lane, True))
                return (200, {"status": "allowed"}) if d.allowed else (403, {"status": "refused", "reason": d.reason})
            return 400, {"status": "refused", "reason": "unknown action"}
        except JobError as e:
            return 403, {"status": "refused", "reason": str(e)[:200]}
        except TaskError as e:
            return 409, {"status": "refused", "reason": str(e)[:200]}
        except Killed:
            return 503, {"status": "refused", "reason": "kill switch is on"}

    def task_action(agent, lane, op, target, p):
        """Outcome tasks (tasks.py): create, update, get, dispatch to the lab. Each goes through policy first."""
        if op not in ("create", "update", "get", "dispatch"):
            return 400, {"status": "refused", "reason": "unknown task operation"}
        tasks.expire()
        d = kernel.authorize(Action("task", op, target, {}, lane, True))
        if not d.allowed:
            return 403, {"status": "refused", "reason": d.reason}
        if op == "create":
            tid = tasks.create(agent, lane, p.get("request", ""), p.get("goal", target), p.get("plan"), p.get("criteria"),
                               str(p.get("source") or "agent"), int(p.get("max_attempts") or 3), int(p.get("budget_s") or 3600))
            return 200, {"status": "done", "task": tid, "next": (
                "Hands-on work (writing and running code, tests, browsing, files) goes to the lab now: "
                "lab_work(task_id, instructions, public_ok=true). You cannot run or test anything yourself, and this task "
                "can only be marked completed with independent verification or finished job ids as evidence.")}
        if op == "get":
            t = tasks.get(target, owner=agent)
            return (200, {"status": "done", "task": t}) if t else (404, {"status": "refused", "reason": "no such task"})
        if op == "update":
            busy = kernel.db.execute("SELECT 1 FROM jobs WHERE kind='lab_run' AND state IN ('queued','running') AND params LIKE ?",
                                     (f'%"task": "{target}"%',)).fetchone()
            if busy and p.get("state") not in (None, "", "paused", "cancelled"):
                return 409, {"status": "refused", "reason": "the lab owns this task's state while it works (you may pause or cancel "
                             "it, or add a note). End your turn: you will be told the outcome."}
            if p.get("state") == "completed":       # completion needs proof, not a claim
                cur = tasks.get(target, owner=agent)
                cited = [m for e in (p.get("evidence") or []) for m in re.findall(r"job:([0-9a-f]{12})", str(e))]
                done = [j for j in cited if kernel.db.execute("SELECT 1 FROM jobs WHERE id=? AND owner=? AND state='done'", (j, agent)).fetchone()]
                if cur and not ((cur.get("verification") or {}).get("passed") is True or done):
                    return 409, {"status": "refused", "reason": "completed needs proof: an independent verification (lab_work) or "
                                 "evidence citing finished jobs as job:<id>. If it cannot be verified, use state failed with the blocker."}
            t = tasks.update(agent, target, state=p.get("state"), step=p.get("step"), plan=p.get("plan"), criteria=p.get("criteria"),
                             note=p.get("note"), result=p.get("result"), evidence=p.get("evidence"), verification=p.get("verification"))
            return 200, {"status": "done", "state": t["state"]}
        # dispatch: the lab runs on public-lane models, so only what may be shared goes there (scrubbed; keys refused)
        t = tasks.get(target, owner=agent)
        if not t:
            return 404, {"status": "refused", "reason": "no such task"}
        if lane == "private" and p.get("public_ok") is not True:
            return 400, {"status": "refused", "reason": "the lab uses public-lane models: send only what may be shared and set public_ok true"}
        instr, _, blocked = scrub(str(p.get("instructions", ""))[:8000], deep=True)
        crit = [scrub(str(c)[:500], deep=True)[0] for c in (p.get("criteria") or t["criteria"])[:20]]
        if blocked or not instr.strip() or not crit:
            return 400, {"status": "refused", "reason": "instructions and acceptance criteria are required, and no private keys"}
        if OWNER_PATH.search("\n".join([instr, *crit])):      # the lab cannot see them: such work stalls or "verifies" stale files
            return 400, {"status": "refused", "reason": "the lab cannot see the owner's computer: remove D:\\ / C:\\ / /mnt/ paths from "
                         "the instructions and criteria. The runner delivers verified files to D:\\jarvis\\outputs\\task-<id>\\ itself; "
                         "use a criterion like 'the files are delivered to the owner'."}
        agent_kind = str(p.get("agent") or "hermes")
        with dispatch_lock:                     # check and create as one step: two requests at once must not both pass
            t = tasks.get(target, owner=agent)
            active = kernel.db.execute("SELECT id FROM jobs WHERE kind='lab_run' AND state IN ('queued','running') AND params LIKE ?",
                                       (f'%"task": "{target}"%',)).fetchone()
            if active or t["state"] not in ("planning", "paused"):
                return 409, {"status": "refused", "reason": f"the lab is already on this task (state {t['state']}); do not dispatch again. "
                             "End your turn: you will be told in this conversation when it ends."}
            params = {"task": target, "agent": agent_kind, "instructions": instr, "criteria": crit,
                      "max_attempts": int(t["max_attempts"]), "timeout_s": max(120, min(int(t["deadline"] - clock()), 5400))}
            jid = jobs.create("lab", "lab_run", params, owner=agent, lane=lane)
            tasks.update(agent, target, state="queued", step=f"dispatched to the lab ({agent_kind})", criteria=crit, note="lab job " + jid)
        return 200, {"status": "queued", "task": target, "job": jid, "next": (
            "Tell the owner in one or two lines that the lab has started (give the task id) and END your turn now. "
            "Do not wait, poll or dispatch again: you will be told the verified outcome in this conversation.")}

    def runner_call(path, body):
        """The lab runner's only endpoints: next lab job, task progress, job result. Reports apply only to the task of a
        lab job that is running, so a runner cannot rewrite other tasks."""
        tasks.expire()
        if path == "/v1/jarvis/runner/next":
            j = jobs.next_for("lab")
            return 200, {"job": j}
        job = str(body.get("job", ""))[:40]
        row = kernel.db.execute("SELECT params, state FROM jobs WHERE id=? AND node='lab' AND kind='lab_run'", (job,)).fetchone()
        if not row or row[1] != "running":
            return 404, {"status": "refused", "reason": "no running lab job"}
        tid = json.loads(row[0])["task"]
        if path == "/v1/jarvis/runner/status":
            t = tasks.get(tid)
            return 200, {"state": t["state"] if t else "failed"}
        if path == "/v1/jarvis/runner/report":
            t = tasks.update(None, tid, state=body.get("state"), step=body.get("step"), note=body.get("note"), result=body.get("result"),
                             evidence=body.get("evidence"), verification=body.get("verification"), attempt=bool(body.get("attempt")))
            return 200, {"status": "done", "state": t["state"]}
        if path == "/v1/jarvis/runner/deliver":
            # verified deliverables go to the owner's laptop through the normal output job, each task in its own folder
            # (outputs\task-<id>\), so old files can never be mistaken for this task's results. Only between a passed
            # check and completion; the content came from the public lab, so the job carries the public lane.
            if tasks.get(tid)["state"] != "verifying":
                return 409, {"status": "refused", "reason": "deliver only after verification, before completion"}
            name = str(body.get("name", ""))
            clean, found, blocked = scrub(str(body.get("content", ""))[:MAX_OUTPUT_CHARS], deep=True)
            if blocked:
                return 400, {"status": "refused", "reason": "the file contains a private key"}
            out = jobs.create(OUTPUT_NODE, "output_save", {"agent": f"task-{tid}", "name": name, "content": clean},
                              owner=f"runner-{tid}", lane="public")
            return 200, {"status": "queued", "job": out, "redacted": sorted(set(found))}
        if path == "/v1/jarvis/runner/delivery":
            j = jobs.get_for(f"runner-{tid}", str(body.get("delivery", ""))[:40])
            return (200, j) if j else (404, {"status": "refused", "reason": "no such delivery"})
        if path == "/v1/jarvis/runner/finish":
            res = body.get("result") if isinstance(body.get("result"), dict) else {"ok": False, "error": "no result"}
            jobs.finish("lab", job, {k: res[k] for k in ("ok", "error", "summary", "attempts") if k in res})
            return 200, {"status": "done"}
        return 404, {"status": "refused", "reason": "not found"}

    class H(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.0"   # the connection closes after each response: a stream ends when the socket closes

        def log_message(self, *a):
            pass

        def _json(self, code, obj):
            b = json.dumps(obj).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(b)))
            self.end_headers()
            self.wfile.write(b)

        def _agent(self):
            auth = self.headers.get("Authorization", "")
            return tokens.identify(auth[7:]) if auth.startswith("Bearer ") else (None, None)

        def do_GET(self):
            if self.path.rstrip("/") == "/v1/jarvis/overview":
                agent, lane = self._agent()
                if not agent:
                    return self._json(401, {"error": {"message": "unauthorized"}})
                if lane != "dashboard":                  # agents never read the owner's overview
                    return self._json(403, {"error": {"message": "dashboard token only"}})
                from .overview import overview
                return self._json(200, overview(kernel, providers, clock()))
            if self.path.rstrip("/") == "/v1/models":
                if not self._agent()[0]:
                    return self._json(401, {"error": {"message": "unauthorized"}})
                return self._json(200, {"object": "list", "data": [{"id": "jarvis", "object": "model", "owned_by": "jarvis"}]})
            if self.path.startswith("/v1/jarvis/jobs/"):
                agent, _ = self._agent()
                if not agent:
                    return self._json(401, {"error": {"message": "unauthorized"}})
                j = jobs.get_for(agent, self.path.rsplit("/", 1)[-1][:40])
                return self._json(200, j) if j else self._json(404, {"error": {"message": "no such job"}})
            self._json(404, {"error": {"message": "not found"}})

        def do_POST(self):
            agent, lane = self._agent()
            if not agent:
                kernel.audit.append("agent_model_refused", {"why": "bad token"})
                return self._json(401, {"error": {"message": "unauthorized"}})
            if lane == "dashboard":                      # the hub's read-only token: no model calls, no actions
                return self._json(403, {"error": {"message": "read-only token"}})
            if lane == "runner" or self.path.startswith("/v1/jarvis/runner/"):
                try:                                     # read the body first: refusing before it confuses some clients
                    n = int(self.headers.get("Content-Length", "0"))
                    body = json.loads(self.rfile.read(n)) if 0 < n <= MAX_BODY else {}
                    if not isinstance(body, dict):
                        raise ValueError
                except (ValueError, OSError):
                    return self._json(400, {"status": "refused", "reason": "bad request"})
                if lane != "runner" or not self.path.startswith("/v1/jarvis/runner/"):
                    return self._json(403, {"error": {"message": "runner token and runner endpoints only go together"}})
                try:
                    code, reply = runner_call(self.path.rstrip("/"), body)
                except TaskError as e:
                    code, reply = 409, {"status": "refused", "reason": str(e)[:200]}
                except JobError as e:
                    code, reply = 403, {"status": "refused", "reason": str(e)[:200]}
                except Killed:
                    code, reply = 503, {"status": "refused", "reason": "kill switch is on"}
                return self._json(code, reply)
            if self.path.rstrip("/") == "/v1/jarvis/actions":
                try:
                    n = int(self.headers.get("Content-Length", "0"))
                    if n <= 0 or n > MAX_BODY:
                        return self._json(413, {"status": "refused", "reason": "bad size"})
                    body = json.loads(self.rfile.read(n))
                    if not isinstance(body, dict):
                        raise ValueError
                except (ValueError, OSError):
                    return self._json(400, {"status": "refused", "reason": "bad request"})
                code, reply = agent_action(agent, lane, body)
                kernel.audit.append("agent_action", {"agent": agent, "lane": lane, "tool": str(body.get("tool", ""))[:20],
                                                     "op": str(body.get("op", ""))[:20], "status": reply.get("status")})
                return self._json(code, reply)
            if self.path.rstrip("/") != "/v1/chat/completions":
                return self._json(404, {"error": {"message": "not found"}})
            if kernel.killed():
                return self._json(503, {"error": {"message": "kill switch is on"}})
            now = clock()
            with lock:
                ts = [t for t in calls.get(agent, []) if now - t < 86400]
                if len([t for t in ts if now - t < 60]) >= PER_MINUTE or len(ts) >= PER_DAY:
                    kernel.audit.append("agent_model_refused", {"agent": agent, "why": "rate limit"})
                    return self._json(429, {"error": {"message": "rate limit"}})
                ts.append(now)
                calls[agent] = ts
            try:
                n = int(self.headers.get("Content-Length", "0"))
                if n <= 0 or n > MAX_BODY:
                    return self._json(413, {"error": {"message": "bad size"}})
                body = json.loads(self.rfile.read(n))
            except (ValueError, OSError):
                return self._json(400, {"error": {"message": "bad request"}})
            body, findings, blocked = _scrub_messages(body)
            if blocked:
                kernel.audit.append("agent_model_refused", {"agent": agent, "why": "private key in prompt"})
                return self._json(400, {"error": {"message": "refused: the request contains a private key"}})
            for k in ("max_tokens", "max_completion_tokens"):
                if k in body:
                    body[k] = min(int(body[k] or MAX_OUT_TOKENS), MAX_OUT_TOKENS)
            if "max_tokens" not in body and "max_completion_tokens" not in body:
                body["max_tokens"] = MAX_OUT_TOKENS
            for k in ("reasoning_effort", "reasoning"):   # agent-side reasoning settings vary by client and break some providers;
                body.pop(k, None)                       # the provider entry decides (e.g. Groq: reasoning_effort "low")
            prompt_chars = len(json.dumps(body.get("messages") or [])) + len(json.dumps(body.get("tools") or []))
            tool_names = sorted({str((t.get("function") or {}).get("name", "?"))[:40] for t in body.get("tools") or [] if isinstance(t, dict)})[:40]
            stream = bool(body.get("stream"))
            try:
                chain = gateway.plan(providers, lane)     # the token's lane, never widened (private: the owner's agent)
            except gateway.RouteError as e:
                return self._json(503, {"error": {"message": str(e)}})
            pref = preferred(lane)                        # an agent's "switch model" choice, same lane only
            chain = sorted(chain, key=lambda x: x.name != pref)
            last = "none"
            for p in chain:
                model = p.default_model
                if not p.model_ok(model):
                    continue
                payload = dict(body, model=model)
                payload.update(p.extra_body)
                headers = {"Content-Type": "application/json", "User-Agent": "jarvis-llm-proxy/0.1"}
                if p.key_file:
                    with open(p.key_path(), encoding="utf-8-sig") as fh:
                        headers["Authorization"] = "Bearer " + fh.read().strip()
                req = urllib.request.Request(p.base_url.rstrip("/") + "/chat/completions", data=json.dumps(payload).encode(), headers=headers, method="POST")
                resp = None
                for attempt in (1, 2):
                    try:
                        resp = urllib.request.urlopen(req, timeout=p.timeout_s or 120)
                        break
                    except urllib.error.HTTPError as e:
                        last = f"HTTP {e.code}"
                        kernel.audit.append("agent_model_call_failed", {"agent": agent, "provider": p.name, "error": last,
                                                                        "prompt_chars": prompt_chars, "attempt": attempt})
                        if e.code not in RETRY_SAME_PROVIDER:
                            break
                    except Exception as e:
                        last = type(e).__name__
                        kernel.audit.append("agent_model_call_failed", {"agent": agent, "provider": p.name, "error": last, "attempt": attempt})
                        break
                if resp is None:
                    continue
                with resp:   # from here on we are committed to this provider
                    self.send_response(200)
                    self.send_header("Content-Type", resp.headers.get("Content-Type", "application/json"))
                    if stream:
                        self.send_header("Cache-Control", "no-cache")
                    self.end_headers()
                    sent = 0
                    while True:
                        chunk = resp.read1(65536) if hasattr(resp, "read1") else resp.read(65536)
                        if not chunk:
                            break
                        self.wfile.write(chunk)
                        self.wfile.flush()
                        sent += len(chunk)
                kernel.audit.append("agent_model_call", {"agent": agent, "lane": lane, "provider": p.name, "stream": stream, "bytes_out": sent,
                                                         "messages": len(body.get("messages") or []), "tools": len(body.get("tools") or []),
                                                         "prompt_chars": prompt_chars, "tool_names": tool_names, "scrubbed": findings})
                return
            kernel.audit.append("agent_model_request_failed", {"agent": agent, "lane": lane, "error": last})
            self._json(502, {"error": {"message": f"all providers failed ({last})"}})

    return H, tokens


def serve(kernel, providers, port=8790, binds=("127.0.0.1",)):
    """Loopback always; extra binds (e.g. the Tailscale address, for agents in the laptop's WSL box) only when asked."""
    H, _ = make_handler(kernel, providers)
    servers = [ThreadingHTTPServer((b, port), H) for b in binds]
    for s in servers[1:]:
        threading.Thread(target=s.serve_forever, daemon=True).start()
    servers[0].serve_forever()
