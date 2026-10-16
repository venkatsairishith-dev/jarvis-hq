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
import math
import os
import re
import secrets
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import gateway, sizing
from .scrub import scrub

MAX_BODY = 2 * 1024 * 1024
MAX_OUT_TOKENS = 4096
PER_MINUTE = 30
PER_DAY = 2000
# A provider's 5xx is tried once more before falling back: Ollama answers a gpt-oss tool call it cannot parse with
# HTTP 500 "error parsing tool call" (ollama/ollama#12884), and a fresh sample usually parses.
RETRY_SAME_PROVIDER = {500, 502, 503}
OUTAGE_REST_S = 30                       # a provider that failed twice with server errors rests this long
MAX_OUTPUT_CHARS = 200_000
# Agent actions (POST /v1/jarvis/actions): every one goes through kernel policy; jobs run on the owner's laptop node.
OUTPUT_NODE = os.environ.get("JARVIS_OUTPUT_NODE", "laptop")
OWNER_PATH = re.compile(r"(?i)(?<![a-z0-9])[a-z]:[\\/]|/mnt/[a-z]/|\\\\[a-z0-9]")   # D:\, C:/, /mnt/d/, \\server
ACTIONS_PER_HOUR = 60
READS_PER_HOUR = 600           # reads (memory/checkpoint at every session start, task get) have their own budget
READ_ACTIONS = {("connector", "list"), ("connector", "result"), ("memory", "list"), ("memory", "get"), ("memory", "resolve"), ("memory", "render"),
                ("task", "get"), ("task", "checkpoint"), ("models", "status")}
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
        lane "runner": the lab runner fetches lab jobs and reports task progress with it, nothing else.
        lane "owner": the owner's console. It reaches only /v1/jarvis/owner/* (commands, approvals, task control); no
        agent ever holds one."""
        if lane not in ("private", "public", "dashboard", "runner", "owner"):
            raise ValueError("lane must be private, public, dashboard, runner or owner")
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
    """Scrub every text the agent sends to a model. Returns (body, findings, blocked).

    The built-in pass runs per text; the deep pass (gitleaks, a subprocess) runs ONCE over all of them. Measured
    2026-10-16 on the Linux test image: one gitleaks run costs ~0.5 s, and running it per message made a 40-message
    agent call take 21 s before the provider was even asked."""
    from . import scrub as scrub_mod
    found, blocked = [], False
    slots = []                                   # (container, key) of every text, after the built-in pass

    def clean(container, key):
        nonlocal blocked
        c, f, b = scrub(container[key], deep=False)
        found.extend(f)
        blocked = blocked or b
        container[key] = c
        slots.append((container, key))

    for m in body.get("messages") or []:
        if isinstance(m.get("content"), str):
            clean(m, "content")
        elif isinstance(m.get("content"), list):
            for part in m["content"]:
                if isinstance(part, dict) and isinstance(part.get("text"), str):
                    clean(part, "text")
        for tc in m.get("tool_calls") or []:
            fn = tc.get("function") or {}
            if isinstance(fn.get("arguments"), str):
                clean(fn, "arguments")
    if blocked or not slots:
        return body, found, blocked
    # Agents send their whole conversation on every call: a text already deep-scanned is taken from the cache (keyed by
    # its digest, holding the REDACTED text, never a secret), so gitleaks only reads what is new. A failed scan caches
    # nothing. Measured 2026-10-16: 20 concurrent fresh calls on 2 cores, p95 20 s with a scan each.
    todo = []
    for c, k in slots:
        h = hashlib.sha256(c[k].encode("utf-8", "surrogatepass")).hexdigest()
        hit = _deep_cache_get(h)
        if hit is None:
            todo.append((c, k, h))
        else:
            c[k], rules = hit
            found.extend(rules)
    if not todo:
        return body, found, blocked
    secrets_found = scrub_mod._gitleaks_secrets("\n\n".join(c[k] for c, k, _h in todo))
    per = [[] for _ in todo]                     # gitleaks rules found in each new text
    # the same rule as scrub(deep=True): a gitleaks finding of 6+ characters is replaced wherever it occurs
    for rule, secret in (secrets_found or []):
        if len(secret) < 6:
            continue
        hit = False
        for n, (c, k, _h) in enumerate(todo):
            if secret in c[k]:
                c[k] = c[k].replace(secret, "[REDACTED]")
                per[n].append("gitleaks:" + rule)
                hit = True
        if hit:
            found.append("gitleaks:" + rule)
    if secrets_found is not None:
        for n, (c, k, h) in enumerate(todo):
            _deep_cache_put(h, (c[k], per[n]))
    return body, found, blocked


_DEEP_CACHE: dict = {}
_DEEP_CACHE_MAX = 4096
_DEEP_LOCK = threading.Lock()


def _deep_cache_get(h: str):
    with _DEEP_LOCK:
        v = _DEEP_CACHE.pop(h, None)
        if v is not None:
            _DEEP_CACHE[h] = v                    # most recently used last
        return v


def _deep_cache_put(h: str, v) -> None:
    with _DEEP_LOCK:
        _DEEP_CACHE[h] = v
        while len(_DEEP_CACHE) > _DEEP_CACHE_MAX:
            _DEEP_CACHE.pop(next(iter(_DEEP_CACHE)))


OWNER_REQUEST_TOKENS = 12000       # what a command from the owner costs the model before it has said anything (measured: 10.6k to 11k)


def _usage(u) -> dict:
    """The token counts a provider reported for a call (integers only), or {} when it reported none."""
    if not isinstance(u, dict):
        return {}
    return {k: int(u[k]) for k in ("prompt_tokens", "completion_tokens", "total_tokens") if isinstance(u.get(k), (int, float))}


def _usage_from_tail(tail: bytes) -> dict:
    """The provider's own token count from the end of a body or stream. Real providers nest objects inside `usage`
    (cached-token details, cost), so the value is decoded as JSON from where it starts, not matched by a pattern."""
    text, out, dec = tail.decode("utf-8", "replace"), {}, json.JSONDecoder()
    for m in re.finditer(r'"usage"\s*:\s*', text):
        try:
            obj, _ = dec.raw_decode(text, m.end())
        except ValueError:
            continue                                   # cut off by the start of the window, or not an object
        u = _usage(obj)
        if u:
            out = u                                    # the last complete one wins (the final stream frame)
    return out


def _recent(times: list, now: float, window: float) -> list:
    return [t for t in times if now - t < window]


def make_handler(kernel, providers, clock=time.time, owner_hermes="auto"):
    from .jobs import PC_OPS, UI_OPS, Jobs, JobError, action_for
    from .policy import Action
    from .store import Killed
    tokens = AgentTokens(kernel)
    jobs = Jobs(kernel)
    from .tasks import Tasks, TaskError
    from .memory import Conflict, Memories, MemoryError
    tasks = Tasks(kernel)
    memories = Memories(kernel)
    from . import owner as owner_mod
    if owner_hermes == "auto":                  # the owner's own Hermes, when this server has its API key
        key = os.path.join(gateway.secrets_dir(), "hermes_api_key.txt")
        from .telegram import hermes_agent
        owner_hermes = (hermes_agent(key, os.environ.get("JARVIS_HERMES_API", "http://127.0.0.1:8642"), session="console-owner",
                                     state_path=os.path.join(str(kernel.dir), "owner_session.txt")) if os.path.exists(key) else None)
    owner_plane = owner_mod.Owner(kernel, tasks, jobs, memories, hermes=owner_hermes, clock=clock)
    owner_plane.node = OUTPUT_NODE
    from .connector_service import ConnectorService
    connectors = ConnectorService(kernel, jobs)
    owner_plane.install_connectors(connectors)
    kernel.db.execute("CREATE TABLE IF NOT EXISTS lane_prefs(lane TEXT PRIMARY KEY, provider TEXT, set_at REAL, by_agent TEXT)")
    lock = threading.Lock()
    dispatch_lock = threading.Lock()            # one dispatch decision at a time (the server answers on many threads)
    cooling = {}                                # provider name -> clock() until which it is not asked (it said 429)
    outage = {}                                 # provider name -> clock() until which it rests after server errors
    calls = {}   # agent -> list of timestamps
    acts, shows, prefers = {}, {}, {}   # agent -> timestamps, per action limit

    def lane_wait(lane):
        """Seconds until any provider of this lane may be asked again; 0 when one can be asked now. The owner plane uses it to say
        "rate limited, back at HH:MM" at once, instead of letting the agent sit in its own ten-minute back-off."""
        try:
            chain = gateway.plan(providers, lane)
        except gateway.RouteError:
            return 0
        # The owner's agent sends about 10k tokens before the owner has typed a word (its instructions and tool list), so a
        # provider that cannot take that much is not one the command could use, and its being free proves nothing.
        chain = [p for p in chain if sizing.fits_with_output(p, OWNER_REQUEST_TOKENS, sizing.MIN_OUTPUT)]
        if not chain:
            return 0
        now = clock()
        with lock:
            rests = [max(cooling.get(p.name, 0), outage.get(p.name, 0)) for p in chain]
        if not rests or any(t <= now for t in rests):
            return 0
        return max(1, math.ceil(min(rests) - now))
    owner_plane.lane_wait = lane_wait

    def preferred(lane):
        r = kernel.db.execute("SELECT provider FROM lane_prefs WHERE lane=?", (lane,)).fetchone()
        return r[0] if r else None

    def agent_action(agent, lane, body):
        """One agent action: (http status, reply). Lane comes from the token, never from the request."""
        tool, op = str(body.get("tool", "")), str(body.get("op", ""))
        target = str(body.get("target", ""))[:200]
        p = body.get("params") if isinstance(body.get("params"), dict) else {}
        now = clock()
        read = (tool, op) in READ_ACTIONS             # a session's start-up reads must never use up the budget for real actions
        key, limit = ((agent, "read"), READS_PER_HOUR) if read else (agent, ACTIONS_PER_HOUR)
        with lock:
            ts = _recent(acts.get(key, []), now, 3600)
            if len(ts) >= limit:
                return 429, {"status": "refused", "reason": f"{'read' if read else 'action'} limit ({limit} per hour)"}
            ts.append(now); acts[key] = ts
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
                                     "next": "the owner approves " + aid + " in the JARVIS console (Command Center) or at the kernel console; then send the same action again with approval_id"}
                jid = jobs.create(OUTPUT_NODE, "ui_action", params, approval_id=aid, owner=agent, lane=lane)
                return 200, {"status": "queued", "job": jid}
            if tool == "ui" and op in ("list_windows", "snapshot"):
                # Looking at the owner's desktop (read-only): the owner's own agent at once, a lab agent only with approval.
                kind, params = (("ui_windows", {}) if op == "list_windows" else ("ui_snapshot", {"target": re.sub(r"[^A-Za-z0-9_.-]", "", target)[:40]}))
                aid = str(body.get("approval_id") or "") or None
                if not aid:
                    d, aid = kernel.request(action_for(kind, params, lane, untrusted=True))
                    if d.denied:
                        return 403, {"status": "refused", "level": d.name, "reason": d.reason}
                    if aid:
                        return 202, {"status": "needs_approval", "approval_id": aid, "level": d.name,
                                     "next": "the owner approves in the console; then send the same action again with approval_id"}
                jid = jobs.create(OUTPUT_NODE, kind, params, approval_id=aid, owner=agent, lane=lane)
                return 200, {"status": "queued", "job": jid}
            if tool == "skill" and op in ("activate", "deactivate"):
                # A staged skill becomes active (or is rolled back) only with the kernel's say-so: policy decides by the
                # risk the skill gate recorded; the owner approves anything above low risk (hq/skills/skillgate.py).
                a = Action("skill", op, target[:80], {"risk": str(p.get("risk", ""))[:10], "where": str(p.get("where", ""))[:10],
                                                     "sha256": str(p.get("sha256", ""))[:64]}, lane, True)
                aid = str(body.get("approval_id") or "") or None
                if aid:
                    d = kernel.authorize(a, aid)
                else:
                    d, aid = kernel.request(a)
                    if d.denied:
                        return 403, {"status": "refused", "level": d.name, "reason": d.reason}
                    if aid:
                        return 202, {"status": "needs_approval", "approval_id": aid, "level": d.name,
                                     "next": "the owner approves " + aid + " in the JARVIS console (Command Center) or at the kernel console; then send the same request with approval_id"}
                    d = kernel.authorize(a)              # no approval needed: the final gate still decides
                if not d.allowed:
                    return 403, {"status": "refused", "level": d.name, "reason": d.reason}
                kernel.audit.append("skill_" + op, {"agent": agent, "skill": target[:80], "risk": a.params["risk"], "where": a.params["where"],
                                                     "sha256": a.params["sha256"]})
                return 200, {"status": "allowed", "level": d.name}
            if tool == "pc" and op in PC_OPS:
                params = {"agent": agent, "op": op, "target": target[:80], "path": str(p.get("path", ""))[:200]}
                aid = str(body.get("approval_id") or "") or None
                if not aid:
                    d, aid = kernel.request(action_for("pc_open", params, lane, untrusted=True))
                    if d.denied:
                        return 403, {"status": "refused", "level": d.name, "reason": d.reason}
                    if aid:
                        return 202, {"status": "needs_approval", "approval_id": aid, "level": d.name,
                                     "next": "the owner approves " + aid + " in the JARVIS console (Command Center) or at the kernel console; then send the same action again with approval_id"}
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
            if tool == "connector":
                # External services and MCP servers. The agent names a connector and a tool; policy decides from the OWNER'S
                # classification, never from anything the server said about itself (connector_service.py).
                if op == "list":
                    if not kernel.authorize(Action("connector", "list", lane, {}, lane, True)).allowed:
                        return 403, {"status": "refused"}
                    return 200, {"status": "done", "connectors": connectors.for_agent(lane)}
                if op == "call":
                    name, _, tname = target.partition(".")
                    return connectors.call_tool(agent, lane, name, tname, p.get("arguments") if "arguments" in p else {}, body.get("approval_id"))
                if op == "result":
                    return connectors.result_for(agent, target)
                return 400, {"status": "refused", "reason": "unknown connector operation"}
            if tool == "task":
                return task_action(agent, lane, op, target, p)
            if tool == "memory":
                return memory_action(agent, lane, op, target, p)
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

    def memory_action(agent, lane, op, target, p):
        """The agent's own durable memory (memory.py): stable ids, versions, verified deletion. Policy first."""
        if op not in ("list", "get", "resolve", "render", "add", "update", "forget"):
            return 400, {"status": "refused", "reason": "unknown memory operation"}
        d = kernel.authorize(Action("memory", op, target[:80], {}, lane, True))
        if not d.allowed:
            return 403, {"status": "refused", "reason": d.reason}
        scope = str(p.get("scope") or "default")[:40]
        try:
            if op == "list":
                return 200, {"status": "done", "entries": memories.list(agent, scope, p.get("query"))}
            if op == "get":
                m = memories.get(agent, target)
                return (200, {"status": "done", "entry": m}) if m else (404, {"status": "refused", "reason": "no such entry"})
            if op == "resolve":
                return 200, {"status": "done", **memories.resolve(agent, target, scope)}
            if op == "render":
                return 200, {"status": "done", "text": memories.render(agent, scope)}
            if op == "add":
                return 200, {"status": "done", "entry": memories.add(agent, target, str(p.get("kind") or "preference"), scope)}
            if op == "update":
                return 200, {"status": "done", "entry": memories.update(agent, target, p.get("text"), p.get("expected_version", -1))}
            mid = target
            if not mid.startswith("m-"):                  # forget by content: only one clear match, never a guess
                r = memories.resolve(agent, target, scope)
                if not r["unique"]:
                    return 409, {"status": "refused", "reason": "no single entry clearly matches; forget by id", "candidates": r["candidates"]}
                mid = r["candidates"][0]["id"]
            return 200, {"status": "done", **memories.forget(agent, mid, p.get("expected_version"))}
        except Conflict as e:
            return 409, {"status": "refused", "reason": str(e), "current": {k: e.current[k] for k in ("id", "text", "version")}}
        except MemoryError as e:
            return 400, {"status": "refused", "reason": str(e)[:200]}

    def dispatch_to_lab(agent, lane, target, p):
        """Hand a task to the lab: the one guarded path (scrubbed instructions, no owner paths, one active dispatch per task).
        Used by an agent's lab_work, by automations and by agent-definition test runs. Returns (http status, reply)."""
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

    def task_action(agent, lane, op, target, p):
        """Outcome tasks (tasks.py): create, update, get, dispatch to the lab. Each goes through policy first."""
        if op == "checkpoint":                            # the agent's open tasks, compact, for a fresh or compacted session
            if not kernel.authorize(Action("task", "get", "checkpoint", {}, lane, True)).allowed:
                return 403, {"status": "refused"}
            tasks.expire()
            return 200, {"status": "done", **tasks.checkpoint(agent)}
        if op not in ("create", "update", "get", "dispatch"):
            return 400, {"status": "refused", "reason": "unknown task operation"}
        tasks.expire()
        d = kernel.authorize(Action("task", op, target, {}, lane, True))
        if not d.allowed:
            return 403, {"status": "refused", "reason": d.reason}
        if op == "create":
            req = " ".join(str(p.get("request", "")).lower().split())
            dup = kernel.db.execute("SELECT id, state FROM tasks WHERE owner=? AND state NOT IN ('completed','failed','cancelled') AND created>? "
                                    "AND lower(request)=? LIMIT 1", (agent, clock() - 7200, req)).fetchone() if len(req) >= 12 else None
            if dup:                      # found live: the same request became two tasks and the lab queued both
                return 409, {"status": "refused", "task": dup[0], "reason": f"this request already has an open task ({dup[0]}, {dup[1]}): "
                             "use that one (task get) instead of creating another; the lab works on one task at a time and will tell you the outcome"}
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
        return dispatch_to_lab(agent, lane, target, p)

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

        def _owner_call(self, method):
            """Owner routes: only a token of lane 'owner' gets in, and an owner token gets nothing else."""
            agent, lane = self._agent()
            if not agent:
                kernel.audit.append("owner_refused", {"why": "bad token"})
                return self._json(401, {"error": "unauthorized"})
            if lane != "owner":
                kernel.audit.append("owner_refused", {"why": "wrong lane", "lane": str(lane)[:12], "agent": agent[:30]})
                return self._json(403, {"error": "owner token only"})
            body = {}
            if method == "POST":
                try:
                    n = int(self.headers.get("Content-Length", "0"))
                    body = json.loads(self.rfile.read(n)) if 0 < n <= MAX_BODY else {}
                    if not isinstance(body, dict):
                        raise ValueError
                except (ValueError, OSError):
                    return self._json(400, {"error": "bad request"})
            if method == "GET" and self.path.split("?")[0].rstrip("/") == owner_mod.PREFIX + "stream":
                return self._owner_stream()
            code, reply = owner_plane.handle(method, self.path, body)
            return self._json(code, reply)

        def _owner_stream(self):
            """Server-sent events: audit rows after ?since= (or Last-Event-ID), oldest first, with a heartbeat. Ends after
            five minutes; the client reconnects with the last id, so nothing is missed or repeated."""
            q = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(self.path).query))
            try:
                since = int(self.headers.get("Last-Event-ID") or q.get("since") or 0)
            except ValueError:
                since = 0
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream"); self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            end, beat = time.time() + 300, 0.0
            try:
                while time.time() < end:
                    rows = owner_plane.events(since, 100)
                    for r in rows:
                        since = r["seq"]
                        self.wfile.write(("id: %d\nevent: audit\ndata: %s\n\n" % (r["seq"], json.dumps(r))).encode())
                    if rows or time.time() - beat > 15:
                        if not rows:
                            self.wfile.write(b": keepalive\n\n")
                        self.wfile.flush(); beat = time.time()
                    time.sleep(0.5 if rows else 1.0)
            except (BrokenPipeError, ConnectionResetError, OSError):
                return
            except Exception:                            # the database went away (shutdown): end the stream, no traceback
                return

        def do_GET(self):
            if self.path.startswith(owner_mod.PREFIX):
                return self._owner_call("GET")
            if self.path.rstrip("/") == "/v1/jarvis/overview":
                agent, lane = self._agent()
                if not agent:
                    return self._json(401, {"error": {"message": "unauthorized"}})
                if lane != "dashboard":                  # agents never read the owner's overview
                    return self._json(403, {"error": {"message": "dashboard token only"}})
                from .overview import overview
                return self._json(200, overview(kernel, providers, clock()))
            if self.path.rstrip("/") == "/v1/models":
                agent, lane = self._agent()
                if not agent:
                    return self._json(401, {"error": {"message": "unauthorized"}})
                m = {"id": "jarvis", "object": "model", "owned_by": "jarvis"}
                try:                                   # agents size their history (and compression) from this
                    ctx = sizing.reported_context_length(gateway.plan(providers, lane)) if lane in ("private", "public") else None
                except gateway.RouteError:
                    ctx = None
                if ctx:
                    m.update(context_length=ctx, max_model_len=ctx)
                return self._json(200, {"object": "list", "data": [m]})
            if self.path.startswith("/v1/jarvis/jobs/"):
                agent, _ = self._agent()
                if not agent:
                    return self._json(401, {"error": {"message": "unauthorized"}})
                j = jobs.get_for(agent, self.path.rsplit("/", 1)[-1][:40])
                return self._json(200, j) if j else self._json(404, {"error": {"message": "no such job"}})
            self._json(404, {"error": {"message": "not found"}})

        def do_POST(self):
            if self.path.startswith(owner_mod.PREFIX):
                return self._owner_call("POST")
            agent, lane = self._agent()
            if not agent:
                kernel.audit.append("agent_model_refused", {"why": "bad token"})
                return self._json(401, {"error": {"message": "unauthorized"}})
            if lane in ("dashboard", "owner"):           # the hub's read-only token and the owner's console token: no model calls, no agent actions
                try:                                     # drain the body first: answering before it is read resets the connection on Windows
                    self.rfile.read(min(int(self.headers.get("Content-Length", "0")), MAX_BODY))
                except (ValueError, OSError):
                    pass
                return self._json(403, {"error": {"message": "this token is not an agent token"}})
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
            est = sizing.estimate_tokens(body)
            summary = sizing.is_summary(body)            # a context-compression call: needs room for a whole checkpoint
            need_out = sizing.SUMMARY_MIN_OUTPUT if summary else sizing.MIN_OUTPUT
            if summary:
                body.pop("max_completion_tokens", None)
                body["max_tokens"] = sizing.SUMMARY_MAX_OUTPUT
            full = list(chain)
            too_small = [p.name for p in chain if not sizing.fits_with_output(p, est, need_out)]
            chain = [p for p in chain if sizing.fits_with_output(p, est, need_out)]   # never send a request it cannot fit
            if not chain:
                return self._too_large(agent, lane, est)
            with lock:
                rest_until = {n: max(cooling.get(n, 0), outage.get(n, 0)) for n in {p.name for p in full}}
                benched = {p.name: rest_until[p.name] for p in chain if rest_until[p.name] > now}
                resting = {n for n, t in rest_until.items() if t > now}
                down_only = bool(benched) and all(outage.get(n, 0) > now and cooling.get(n, 0) <= now for n in benched)
            chain = [p for p in chain if p.name not in benched]  # a provider that said "rate limited" rests until it may be asked again
            if too_small or benched:
                kernel.audit.append("agent_model_skipped", {"agent": agent, "lane": lane, "est_tokens": est, "providers": too_small,
                                                            "cooling": sorted(benched), **({"summary": True} if summary else {})})
            if not chain:
                # Only resting providers could take it. If a provider that is not resting could take the part that
                # compression cannot shrink (system prompt + tools), ask the agent to compress (413); a summary call
                # also gets 413, so the agent uses its own fallback summary. Otherwise it must wait (429).
                fixed = sizing.estimate_fixed(body)
                if summary or any(sizing.fits_with_output(p, fixed + 2000, need_out) for p in full if p.name not in resting):
                    return self._too_large(agent, lane, est)
                wait = max(1, math.ceil(min(benched.values()) - now))
                if down_only:                            # resting after an outage: a short, plain "unavailable" (not 429:
                    return self._unavailable(agent, lane, wait)   # agents back off for minutes on a rate limit)
                return self._rate_limited(agent, lane, wait)
            last, outcomes = "none", []
            for p in chain:
                model, why = p.default_model, "other"
                if not p.model_ok(model):
                    continue
                payload = dict(body, model=model)
                for k in ("max_tokens", "max_completion_tokens"):    # shrink the answer budget to what still fits this provider
                    if k in payload:
                        payload[k] = sizing.output_budget(p, est, payload[k])
                payload.update(p.extra_body)
                if summary:
                    payload["stream"] = False                        # read the whole summary to check it before passing it on
                headers = {"Content-Type": "application/json", "User-Agent": "jarvis-llm-proxy/0.1"}
                if p.key_file:
                    with open(p.key_path(), encoding="utf-8-sig") as fh:
                        headers["Authorization"] = "Bearer " + fh.read().strip()
                req = urllib.request.Request(p.base_url.rstrip("/") + "/chat/completions", data=json.dumps(payload).encode(), headers=headers, method="POST")
                resp, t_call = None, time.time()
                for attempt in (1, 2):
                    try:
                        resp = urllib.request.urlopen(req, timeout=p.timeout_s or 120)
                        break
                    except urllib.error.HTTPError as e:
                        last = f"HTTP {e.code}"
                        try:
                            text = e.read(4000).decode(errors="replace")      # classified, never logged
                        except Exception:
                            text = ""
                        why = sizing.classify(e.code, text)
                        rest = sizing.cooldown_s(e.headers.get("Retry-After") if e.headers else None, text) if why == "rate_limited" else 0
                        if rest:
                            with lock:
                                cooling[p.name] = clock() + rest
                        kernel.audit.append("agent_model_call_failed", {"agent": agent, "provider": p.name, "error": last, "class": why,
                                                                        "prompt_chars": prompt_chars, "est_tokens": est, "attempt": attempt,
                                                                        **({"cooldown_s": rest} if rest else {})})
                        if e.code >= 500 and attempt == 2:      # failed twice: rest it briefly, so retries do not hammer it
                            with lock:
                                outage[p.name] = clock() + OUTAGE_REST_S
                        if e.code not in RETRY_SAME_PROVIDER or why == "too_large":
                            break
                    except Exception as e:
                        last, why = type(e).__name__, "other"
                        kernel.audit.append("agent_model_call_failed", {"agent": agent, "provider": p.name, "error": last, "attempt": attempt})
                        with lock:                               # unreachable: rest it briefly as well
                            outage[p.name] = clock() + OUTAGE_REST_S
                        break
                if resp is None:
                    outcomes.append(why)
                    continue
                if summary:
                    try:
                        with resp:
                            reply = json.loads(resp.read())
                    except (ValueError, OSError) as e:
                        reply, last = {}, type(e).__name__
                    ok, bad = sizing.summary_ok(reply)
                    if not ok:                               # a cut-off or empty checkpoint would freeze the agent's compaction
                        last = f"summary {bad}"
                        kernel.audit.append("agent_model_call_failed", {"agent": agent, "provider": p.name, "error": last,
                                                                        "class": "summary_" + bad, "est_tokens": est})
                        outcomes.append("other")
                        continue
                    for ch in reply.get("choices") or []:     # the checkpoint is the content; reasoning text must not stand in for it
                        for k in ("reasoning", "reasoning_content", "reasoning_details"):
                            (ch.get("message") or {}).pop(k, None)
                    self._send_reply(reply, stream)
                    kernel.audit.append("agent_model_call", {"agent": agent, "lane": lane, "provider": p.name, "summary": True,
                                                             "usage": _usage(reply.get("usage")),
                                                             "prompt_chars": prompt_chars, "est_tokens": est,
                                                             "out_chars": len(reply["choices"][0]["message"]["content"]), "ms": int((time.time() - t_call) * 1000)})
                    return
                with resp:   # from here on we are committed to this provider
                    self.send_response(200)
                    self.send_header("Content-Type", resp.headers.get("Content-Type", "application/json"))
                    if stream:
                        self.send_header("Cache-Control", "no-cache")
                    self.end_headers()
                    sent, tail = 0, b""
                    while True:
                        chunk = resp.read1(65536) if hasattr(resp, "read1") else resp.read(65536)
                        if not chunk:
                            break
                        self.wfile.write(chunk)
                        self.wfile.flush()
                        sent += len(chunk)
                        tail = (tail + chunk)[-8192:]                  # the provider's own token count sits at the end of the body/stream
                kernel.audit.append("agent_model_call", {"agent": agent, "lane": lane, "provider": p.name, "stream": stream, "bytes_out": sent,
                                                         "messages": len(body.get("messages") or []), "tools": len(body.get("tools") or []),
                                                         "prompt_chars": prompt_chars, "tool_names": tool_names, "scrubbed": findings,
                                                         "usage": _usage_from_tail(tail),
                                                         "est_tokens": est, "ms": int((time.time() - t_call) * 1000)})
                return
            kernel.audit.append("agent_model_request_failed", {"agent": agent, "lane": lane, "error": last, "classes": outcomes})
            if outcomes and all(o == "too_large" for o in outcomes):
                return self._too_large(agent, lane, est)       # the agent must shorten its history, not retry the same request
            if outcomes and all(o == "rate_limited" for o in outcomes):
                with lock:
                    waits = [cooling.get(p.name, 0) - clock() for p in chain]
                return self._rate_limited(agent, lane, max(1, math.ceil(min(waits))) if waits else 30)
            self._json(502, {"error": {"message": f"all providers failed ({last})"}})

        def _send_reply(self, reply, stream):
            """A checked, complete reply: as JSON, or as a one-chunk event stream when the agent asked to stream."""
            if not stream:
                return self._json(200, reply)
            ch = (reply.get("choices") or [{}])[0]
            base = {k: reply.get(k) for k in ("id", "created", "model") if reply.get(k) is not None}
            chunks = [dict(base, object="chat.completion.chunk", choices=[{"index": 0, "delta": {"role": "assistant",
                           "content": (ch.get("message") or {}).get("content") or ""}, "finish_reason": None}]),
                      dict(base, object="chat.completion.chunk", choices=[{"index": 0, "delta": {},
                           "finish_reason": ch.get("finish_reason") or "stop"}], **({"usage": reply["usage"]} if reply.get("usage") else {}))]
            data = b"".join(b"data: " + json.dumps(c).encode() + b"\n\n" for c in chunks) + b"data: [DONE]\n\n"
            self.send_response(200); self.send_header("Content-Type", "text/event-stream"); self.send_header("Cache-Control", "no-cache")
            self.end_headers(); self.wfile.write(data); self.wfile.flush()

        def _unavailable(self, agent, lane, wait):
            kernel.audit.append("agent_model_unavailable", {"agent": agent, "lane": lane, "retry_after_s": wait})
            b = json.dumps({"error": {"message": f"the providers that fit this request are down; retry in {wait} s", "type": "server_error"}}).encode()
            self.send_response(503); self.send_header("Retry-After", str(wait)); self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(b))); self.end_headers(); self.wfile.write(b)

        def _rate_limited(self, agent, lane, wait):
            kernel.audit.append("agent_model_rate_limited", {"agent": agent, "lane": lane, "retry_after_s": wait})
            if lane == "private":                         # the owner's own agent: its command must not sit "running" through a back-off
                owner_plane.provider_resting(wait)
            b = json.dumps({"error": {"message": f"all providers in this lane are rate limited; retry in {wait} s", "type": "rate_limit_error"}}).encode()
            self.send_response(429); self.send_header("Retry-After", str(wait)); self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(b))); self.end_headers(); self.wfile.write(b)

        def _too_large(self, agent, lane, est):
            """One fast, clear answer when no provider in the lane can take the request: an OpenAI-style context error
            with HTTP 413, which agents (Hermes) treat as "compress the conversation", never as an outage to retry."""
            kernel.audit.append("agent_model_too_large", {"agent": agent, "lane": lane, "est_tokens": est})
            limit = sizing.lane_context_length(gateway.plan(providers, lane))
            return self._json(413, {"error": {
                "message": f"context length exceeded: this request is about {est} tokens; the largest model in this lane accepts "
                           f"{limit or 'fewer'}. Compress the conversation and try again.",
                "type": "invalid_request_error", "code": "context_length_exceeded"}})

    def route_explain(body, q):
        lane = q.get("lane", "private")
        if lane not in ("private", "public"):
            return 400, {"error": "lane must be private or public"}
        now = clock()
        rest = {n: round(max(cooling.get(n, 0), outage.get(n, 0)) - now) for n in set(cooling) | set(outage)}
        try:
            est = int(q["tokens"]) if q.get("tokens") else None
        except ValueError:
            return 400, {"error": "tokens must be a number"}
        return 200, gateway.explain(providers, lane, q.get("purpose") or None, est, rest, preferred(lane))
    owner_plane.routes[("GET", "route")] = route_explain
    probes = []                                          # timestamps: at most 10 probes an hour (each chat probe is a real, tiny request)

    def probe_route(body, q):
        name = str(body.get("provider", ""))
        p = next((x for x in providers if x.name == name), None)
        if p is None:
            return 404, {"error": "no such provider"}
        now = clock()
        probes[:] = [t for t in probes if now - t < 3600]
        if len(probes) >= 10:
            return 429, {"error": "at most 10 provider probes per hour"}
        probes.append(now)
        res = gateway.probe(p, chat=bool(body.get("chat")))
        kernel.audit.append("provider_probe", {"provider": p.name, "lane": p.lane, "chat": bool(body.get("chat")), "ok": res.get("ok"),
                                               "status": res.get("status"), "usage": res.get("usage") or {}, "ms": res.get("ms")})
        return 200, res
    owner_plane.routes[("POST", "models/probe")] = probe_route
    from .automations import Automations
    from .agents import AgentDefs
    owner_plane.attach(Automations(kernel, tasks, jobs, owner_plane, dispatch=dispatch_to_lab, clock=clock),
                       AgentDefs(kernel, tasks, dispatch=dispatch_to_lab, clock=clock), providers=providers)
    H.owner_plane = owner_plane
    return H, tokens


def serve(kernel, providers, port=8790, binds=("127.0.0.1",)):
    """Loopback always; extra binds (e.g. the Tailscale address, for agents in the laptop's WSL box) only when asked."""
    H, _ = make_handler(kernel, providers)
    from .owner import start_ticker
    start_ticker(H.owner_plane)                  # stuck jobs, deadlines, finished lab work: the kernel's own loop
    servers = [ThreadingHTTPServer((b, port), H) for b in binds]
    for s in servers[1:]:
        threading.Thread(target=s.serve_forever, daemon=True).start()
    servers[0].serve_forever()
