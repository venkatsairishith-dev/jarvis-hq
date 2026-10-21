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

from . import gateway, idempotency, sizing
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
# The token name of the OWNER's own agent (docs/05_OPERATIONS: the Hermes token is "hermes"). Only its tasks are tied to the owner
# command that is running (audit Low, 2026-10-18): another private agent's task, created while a command runs, is not that command's.
OWNER_AGENT = os.environ.get("JARVIS_OWNER_AGENT", "hermes")
CARETAKER_AGENT = "caretaker"  # the agent token name the caretaker runs under (jarvis/caretaker): its tasks are caretaker tasks
# The only request fields forwarded to a provider: the standard OpenAI chat fields (audit SEC-D6, 2026-10-18). Anything else
# an agent sends is dropped: OpenRouter's `models` (a paid fallback list) and `plugins` (billed web search), `n` (multiplies
# the free quota), `provider`, `user`, llama-server sampling fields such as `n_predict`. The provider entry's own extra_body
# (owner configuration) is still applied after this.
CHAT_FIELDS = frozenset({"model", "messages", "max_tokens", "max_completion_tokens", "temperature", "top_p", "stream",
                         "stream_options", "stop", "tools", "tool_choice", "parallel_tool_calls", "response_format", "seed",
                         "presence_penalty", "frequency_penalty", "logprobs", "top_logprobs", "logit_bias"})
VERIFIERS = ("lab-runner", "owner")   # who may record a verification verdict on a task (never an agent token)


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


TEXT_PART_TYPES = ("text", "input_text")                       # content parts that hold text; every other part is media
MEDIA_NOTE = ("[{kind} was withheld for privacy: only a local model may receive it. Tell the user you could not see it.]")
MEDIA_KINDS = {"image_url": "an image", "input_image": "an image", "image": "an image", "input_audio": "an audio clip",
               "audio": "an audio clip", "file": "a file", "input_file": "a file", "video_url": "a video"}


def withhold_media(body: dict):
    """(copy of the body with every non-text content part replaced by a short text note, how many were replaced).
    Images, audio and files cannot be scrubbed and a screenshot can hold anything, so only a provider of the local lane
    gets them; for every other provider this copy is what is sent (audit M2). The input body is not changed."""
    n = 0
    msgs = []
    for m in body.get("messages") or []:
        m = dict(m)
        if isinstance(m.get("content"), list):
            parts = []
            for part in m["content"]:
                if isinstance(part, str) or (isinstance(part, dict) and part.get("type", "text") in TEXT_PART_TYPES):
                    parts.append(part)
                else:
                    n += 1
                    kind = MEDIA_KINDS.get(part.get("type") if isinstance(part, dict) else None, "an attachment")
                    parts.append({"type": "text", "text": MEDIA_NOTE.format(kind=kind)})
            m["content"] = parts
        elif m.get("content") is not None and not isinstance(m.get("content"), str):
            n += 1
            m["content"] = MEDIA_NOTE.format(kind="content in an unknown format")
        if "audio" in m:                                    # an assistant's earlier audio reply
            n += 1
            del m["audio"]
        msgs.append(m)
    if "messages" in body:
        body = dict(body, messages=msgs)
    return body, n


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

    def clean_tree(container, key):
        """Every string inside a JSON value (tool descriptions and parameter schemas), and the key names: a key can carry a token
        (a property named after a secret value) just as a value can (inventory pass F, 2026-10-19)."""
        nonlocal blocked
        v = container[key]
        if isinstance(v, str):
            clean(container, key)
        elif isinstance(v, dict):
            for k2 in list(v):
                if isinstance(k2, str):
                    nk, kf, kb = scrub(k2, deep=False)
                    found.extend(kf)
                    blocked = blocked or kb
                    if nk != k2:
                        v[nk] = v.pop(k2)
                        k2 = nk
                clean_tree(v, k2)
        elif isinstance(v, list):
            for i in range(len(v)):
                clean_tree(v, i)

    # EVERY text field of the request is scrubbed, not a list of known ones (audit M2, 2026-10-18: message `name`, legacy
    # `function_call`, `reasoning_content`, `refusal`, tool names, extra keys on content parts, `tool_choice` and `stop` used
    # to reach the provider unscrubbed). Media parts (image, audio, file) are not text and are not scrubbed here: they are
    # withheld per provider by `withhold_media`, because only a local model may see them.
    for m in body.get("messages") or []:
        for key in list(m):
            if key == "role" or key == "audio":
                continue
            if key == "content" and isinstance(m[key], list):
                for i, part in enumerate(m[key]):
                    if isinstance(part, str):
                        clean(m[key], i)
                    elif isinstance(part, dict) and part.get("type", "text") in TEXT_PART_TYPES:
                        clean_tree(m[key], i)
            else:
                clean_tree(m, key)
    # Tool names, descriptions and parameter schemas are text the agent wrote and the provider reads (audit SEC-D6: a key in
    # tools[].function.description reached the upstream unscrubbed), and so are tool_choice, stop and a response_format schema.
    for k in ("tools", "tool_choice", "stop", "response_format"):
        if k in body:
            clean_tree(body, k)
    if isinstance(body.get("logit_bias"), dict):          # token numbers only: a key that is not a number would be text
        body["logit_bias"] = {a: b for a, b in body["logit_bias"].items() if isinstance(a, str) and a.lstrip("-").isdigit()
                              and isinstance(b, (int, float)) and not isinstance(b, bool)}
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
    # A new text that gitleaks provably finds nothing in (no rule keyword, nothing it could decode: scrub.gitleaks_gate) is not
    # sent to the subprocess, whose start-up alone is ~0.5 s. It stays in `todo`, so what gitleaks finds in the other texts of
    # this request is still replaced in it, exactly as before; only the scan input is smaller.
    from . import gitleaks_gate
    exe = scrub_mod.gitleaks_path()
    scan_idx = [n for n, (c, k, _h) in enumerate(todo) if not exe or gitleaks_gate.must_scan(c[k], exe)]   # no binary: as before
    if scan_idx:
        secrets_found = _DEEP_BATCH.scan("\n\n".join(todo[n][0][todo[n][1]] for n in scan_idx), scrub_mod)
    else:
        secrets_found = []                           # every new text is one gitleaks provably finds nothing in: a clean verdict
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


class _DeepBatch:
    """Group commit for the deep scan. A lone request is scanned at once; requests that arrive while a scan runs share the
    NEXT run, and under load the first run waits 20 ms for company. Most of a gitleaks run (~0.5 s) is start-up, so 20 concurrent fresh requests used to
    queue for 6-20 s (bench J-038), and a lone request must not wait for company (bench J-018, 500 ms). Each caller gets
    the findings of its joint run and replaces only what occurs in its own text; a failed run is None for all in it."""

    def __init__(self):
        self._lock = threading.Lock()
        self._pending = None                    # the batch the next run will scan: {"texts", "done", "result"}
        self._running = False
        self._last = 0.0                        # when the previous request arrived

    LOAD_GAP_S, GATHER_S = 0.1, 0.02            # under load (requests < 100 ms apart) a run waits 20 ms for company

    def scan(self, text: str, scrub_mod):
        with self._lock:
            now = time.monotonic()
            busy, self._last = now - self._last < self.LOAD_GAP_S, now
            if self._pending is None:
                self._pending = {"texts": [], "done": threading.Event(), "result": None}
            mine = self._pending
            mine["texts"].append(text)
            runner = not self._running
            if runner:
                self._running = True
        if not runner:
            mine["done"].wait(120)
            return mine["result"]
        if busy:
            time.sleep(self.GATHER_S)
        while True:                             # run batches until nobody is waiting
            with self._lock:
                b, self._pending = self._pending, None
                if b is None:
                    self._running = False
                    break
            try:
                b["result"] = scrub_mod._gitleaks_secrets("\n\n".join(b["texts"]))
            except Exception:  # noqa: BLE001 - a failed scan is reported as None, exactly like _gitleaks_secrets
                b["result"] = None
            finally:
                b["done"].set()
        return mine["result"]


_DEEP_BATCH = _DeepBatch()
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


def _unmet_criteria(verdict: dict, criteria: list) -> str:
    """'' when a verifier's per-criterion report (VERIFY.json "criteria") backs its passed verdict, else what is missing.

    `criteria` must be the JOB's snapshot taken at dispatch (the params of the lab job), never the task's current criteria: an
    agent may rewrite the task's criteria while the lab works (audit M8/M11, 2026-10-18). When there are criteria to check, a
    verdict without the per-criterion list (a bare {"passed": true}) is NOT accepted: it is exactly what a forged VERIFY.json
    looks like. Every criterion needs its own row (distinct texts) with met true. The row text is not compared with the
    criterion text because verifiers paraphrase; the count of distinct rows is."""
    rows = verdict.get("criteria")
    if rows is None:
        return "the verdict has no per-criterion report" if criteria else ""
    if not isinstance(rows, list):
        return "the per-criterion report is not a list"
    unmet = [str(r.get("criterion", "?"))[:100] if isinstance(r, dict) else "?" for r in rows
             if not (isinstance(r, dict) and r.get("met") is True)]
    if unmet:
        return "not met: " + "; ".join(unmet)
    distinct = {" ".join(str(r.get("criterion", "")).lower().split()) for r in rows}
    distinct.discard("")
    if len(distinct) < len(criteria):
        return f"only {len(distinct)} of {len(criteria)} criteria were checked"
    return ""


def _recent(times: list, now: float, window: float) -> list:
    return [t for t in times if now - t < window]


def make_handler(kernel, providers, clock=time.time, owner_hermes="auto"):
    from .jobs import PC_OPS, UI_OPS, Jobs, JobError, action_for
    from .policy import Action
    from .store import Killed
    tokens = AgentTokens(kernel)
    jobs = Jobs(kernel)

    def _warm_gitleaks_gate():                  # read the gitleaks version once, off the request path (it costs ~0.3 s the first time)
        try:
            from . import gitleaks_gate
            exe = scrub_gitleaks_path()
            if exe:
                gitleaks_gate.gitleaks_version(exe)
        except Exception:
            pass
    from .scrub import gitleaks_path as scrub_gitleaks_path
    threading.Thread(target=_warm_gitleaks_gate, daemon=True).start()
    from .tasks import FINAL, Tasks, TaskError
    from .memory import Conflict, Memories, MemoryError
    tasks = Tasks(kernel)
    memories = Memories(kernel)
    # a private agent's dispatch text waiting for the owner's approval (dispatch_to_lab); removed when used, pruned after
    kernel.db.execute("CREATE TABLE IF NOT EXISTS lab_share_pending(approval_id TEXT PRIMARY KEY, task TEXT, agent TEXT, kind TEXT, "
                      "instructions TEXT, criteria TEXT, created REAL)")
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
    idem = idempotency.Idempotency(kernel, clock)
    owner_plane.idem = idem
    kernel.db.execute("CREATE TABLE IF NOT EXISTS lane_prefs(lane TEXT PRIMARY KEY, provider TEXT, set_at REAL, by_agent TEXT)")
    lock = threading.Lock()
    dispatch_lock = threading.Lock()            # one dispatch decision at a time (the server answers on many threads)
    create_lock = threading.Lock()              # the duplicate-request check and the create are one step (bench N-069)
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
            # A job the agent runs FOR one of its open tasks (params.task) is linked to it when it is created: only such jobs,
            # finished after the task started, count as evidence that the task is done (audit SEC-D1).
            for_task = str(p.get("task") or "")[:12] if tool in ("output", "ui", "pc") else ""
            if for_task:
                ft = tasks.get(for_task, owner=agent)
                if not ft or ft["state"] in FINAL:
                    return 409, {"status": "refused", "reason": "params.task must name one of your own open tasks"}

            def link(jid):
                if for_task:
                    tasks.link_job(for_task, jid)
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
                link(jid)
                return 200, {"status": "queued", "job": jid}
            if tool == "ui" and op in UI_OPS:
                params = {"agent": agent, "op": op, "target": target, "text": str(p.get("text", ""))[:500]}
                aid = str(body.get("approval_id") or "") or None
                if not aid:
                    d, aid = kernel.request(action_for("ui_action", params, lane, untrusted=True, requester=agent))
                    if d.denied:
                        return 403, {"status": "refused", "level": d.name, "reason": d.reason}
                    if aid:
                        return 202, {"status": "needs_approval", "approval_id": aid, "level": d.name,
                                     "next": "the owner approves " + aid + " in the JARVIS console (Command Center) or at the kernel console; then send the same action again with approval_id"}
                jid = jobs.create(OUTPUT_NODE, "ui_action", params, approval_id=aid, owner=agent, lane=lane)
                link(jid)
                return 200, {"status": "queued", "job": jid}
            if tool == "ui" and op in ("list_windows", "snapshot"):
                # Looking at the owner's desktop (read-only): the owner's own agent at once, a lab agent only with approval.
                kind, params = (("ui_windows", {}) if op == "list_windows" else ("ui_snapshot", {"target": re.sub(r"[^A-Za-z0-9_.-]", "", target)[:40]}))
                aid = str(body.get("approval_id") or "") or None
                if not aid:
                    d, aid = kernel.request(action_for(kind, params, lane, untrusted=True, requester=agent))
                    if d.denied:
                        return 403, {"status": "refused", "level": d.name, "reason": d.reason}
                    if aid:
                        return 202, {"status": "needs_approval", "approval_id": aid, "level": d.name,
                                     "next": "the owner approves in the console; then send the same action again with approval_id"}
                jid = jobs.create(OUTPUT_NODE, kind, params, approval_id=aid, owner=agent, lane=lane)
                link(jid)
                return 200, {"status": "queued", "job": jid}
            if tool == "skill" and op in ("activate", "deactivate"):
                # A staged skill becomes active (or is rolled back) only with the kernel's say-so: policy decides by the
                # risk the skill gate recorded; the owner approves anything above low risk (hq/skills/skillgate.py).
                a = Action("skill", op, target[:80], {"risk": str(p.get("risk", ""))[:10], "where": str(p.get("where", ""))[:10],
                                                     "sha256": str(p.get("sha256", ""))[:64]}, lane, True, requester=agent)
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
                    d, aid = kernel.request(action_for("pc_open", params, lane, untrusted=True, requester=agent))
                    if d.denied:
                        return 403, {"status": "refused", "level": d.name, "reason": d.reason}
                    if aid:
                        return 202, {"status": "needs_approval", "approval_id": aid, "level": d.name,
                                     "next": "the owner approves " + aid + " in the JARVIS console (Command Center) or at the kernel console; then send the same action again with approval_id"}
                jid = jobs.create(OUTPUT_NODE, "pc_open", params, approval_id=aid, owner=agent, lane=lane)
                link(jid)
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
                return task_action(agent, lane, op, target, p, str(body.get("approval_id") or "") or None)
            if tool == "caretaker" and op == "report":
                return caretaker_report(agent, lane, p)
            if tool == "memory":
                return memory_action(agent, lane, op, target, p)
            if tool == "lab" and op == "task":
                return lab_subtask(agent, lane, target, p, str(body.get("approval_id") or "") or None)
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
        # The audit row keeps the op, the lane, the agent and an entry id, never the text or query in `target` (review H1;
        # policy.Action.describe reduces it). The agent is named so the record stays useful without the words.
        d = kernel.authorize(Action("memory", op, target[:80], {}, lane, True, requester=agent))
        if not d.allowed:
            return 403, {"status": "refused", "reason": d.reason}
        scope = str(p.get("scope") or "default")[:40]
        try:
            if op == "list":
                return 200, {"status": "done", "entries": memories.list(agent, scope, p.get("query"), lane=lane)}
            if op == "get":
                m = memories.get(agent, target, lane=lane)
                return (200, {"status": "done", "entry": m}) if m else (404, {"status": "refused", "reason": "no such entry"})
            if op == "resolve":
                return 200, {"status": "done", **memories.resolve(agent, target, scope, lane=lane)}
            if op == "render":
                return 200, {"status": "done", "text": memories.render(agent, scope, query=p.get("query"), lane=lane)}
            if op == "add":
                return 200, {"status": "done", "entry": memories.add(agent, target, str(p.get("kind") or "preference"), scope, lane=lane)}
            if op == "update":
                return 200, {"status": "done", "entry": memories.update(agent, target, p.get("text"), p.get("expected_version", -1), lane=lane)}
            mid = target
            if not mid.startswith("m-"):                  # forget by content: only one clear match, never a guess
                r = memories.resolve(agent, target, scope, lane=lane)
                if not r["unique"]:
                    return 409, {"status": "refused", "reason": "no single entry clearly matches; forget by id", "candidates": r["candidates"]}
                mid = r["candidates"][0]["id"]
            return 200, {"status": "done", **memories.forget(agent, mid, p.get("expected_version"), lane=lane)}
        except Conflict as e:
            return 409, {"status": "refused", "reason": str(e), "current": {k: e.current[k] for k in ("id", "text", "version")}}
        except MemoryError as e:
            return 400, {"status": "refused", "reason": str(e)[:200]}

    def lab_subtask(agent, lane, target, p, aid):
        """An agent hands work to another lab agent (jarvis_mcp start_lab_task): the lab agent runs on public-lane models.
        The action carries the whole prompt, so an approval is bound to exactly that text; the audit rows record only its
        length and digest (policy.Action.describe). From the private lane it needs the owner's approval, like a dispatch
        (audit MEM-M8). A private key is refused whatever the lane."""
        prompt = str(p.get("prompt", ""))
        if len(prompt) > 20000:
            return 400, {"status": "refused", "reason": "the prompt is longer than 20000 characters"}
        if scrub(prompt)[2]:
            return 400, {"status": "refused", "reason": "the prompt contains a private key"}
        a = Action("lab", "task", target[:20], {"prompt": prompt}, lane, True, requester=agent)
        if not aid:
            d, aid = kernel.request(a)
            if d.denied:
                return 403, {"status": "refused", "level": d.name, "reason": d.reason}
            if aid:
                return 202, {"status": "needs_approval", "approval_id": aid, "level": d.name, "reason": d.reason,
                             "shares": {"prompt_chars": len(prompt)},
                             "next": "the owner approves " + aid + " in the JARVIS console with the PIN; then call start_lab_task again with "
                                     "the same agent and prompt plus approval_id"}
        d = kernel.authorize(a, aid)
        return (200, {"status": "allowed"}) if d.allowed else (403, {"status": "refused", "level": d.name, "reason": d.reason})

    def replay_recheck(agent, lane, body):
        """Before a kept reply (idempotency.py) is served again: the checks that can have changed since it was recorded. None
        = serve it. The kill switch ends everything, a replay included. A kept lab.task "allowed" is a grant the caller then
        acts on, so policy is asked again and a DENY now (a protected resource, a changed rule) wins over the old answer. Every
        other kept reply only reports an effect that already happened (a queued job, an entry, an approval id), so serving it
        again grants nothing new; the lane is part of the key's scope. (Review M1.)"""
        if kernel.killed():
            return 503, {"status": "refused", "reason": "kill switch is on"}
        if str(body.get("tool", "")) == "lab" and str(body.get("op", "")) == "task":
            from .policy import decide
            p = body.get("params") if isinstance(body.get("params"), dict) else {}
            a = Action("lab", "task", str(body.get("target", ""))[:200][:20], {"prompt": str(p.get("prompt", ""))}, lane, True, requester=agent)
            d = decide(a, None, kernel.protected)
            if d.denied:
                return 403, {"status": "refused", "level": d.name, "reason": d.reason}
        return None

    def caretaker_report(agent, lane, p):
        """One caretaker run's summary into the audit chain: counts by kind and severity, and the task ids it created."""
        if agent != CARETAKER_AGENT:
            return 403, {"status": "refused", "reason": "only the caretaker's token reports caretaker runs"}
        if not kernel.authorize(Action("caretaker", "report", "run", {}, lane, True)).allowed:
            return 403, {"status": "refused"}

        def counts(x):
            return {str(k)[:30]: int(v) for k, v in list(x.items())[:12] if isinstance(v, int) and not isinstance(v, bool)} if isinstance(x, dict) else {}
        made = [str(t) for t in (p.get("tasks") or [])[:50] if re.fullmatch(r"[0-9a-f]{12}", str(t))]
        rec = {"run": str(p.get("run_id", ""))[:40], "by_kind": counts(p.get("by_kind")), "by_severity": counts(p.get("by_severity")),
               "new": int(p.get("new") or 0) if str(p.get("new") or 0).isdigit() else 0, "tasks": made,
               "owner_only": int(p.get("owner_only") or 0) if str(p.get("owner_only") or 0).isdigit() else 0}
        seq = kernel.audit.append("caretaker_run", rec)
        return 200, {"status": "done", "audit_seq": seq}

    def share_pending(aid, target, agent):
        """What a private agent asked to share with the public lab under approval `aid`, kept until the approval is used or
        expires, so the agent can send the approved dispatch again with the approval id alone."""
        r = kernel.db.execute("SELECT instructions, criteria, kind FROM lab_share_pending WHERE approval_id=? AND task=? AND agent=?",
                              (str(aid)[:40], target, agent)).fetchone()
        return (r[0], json.loads(r[1]), r[2]) if r else None

    def dispatch_to_lab(agent, lane, target, p, approval_id=None):
        """Hand a task to the lab: the one guarded path (scrubbed instructions, no owner paths, one active dispatch per task).
        Used by an agent's lab_work, by automations and by agent-definition test runs. Returns (http status, reply).

        The lab runs on public-lane models. From the private lane (the owner's agent, which holds the owner's data) a dispatch
        shares that content with public models, so it needs the OWNER's approval of exactly what is shared (audit SEC-D5,
        2026-10-18): the agent's public_ok flag is its own claim, not authorization. The approval is bound to the digest of
        the agent kind, the full scrubbed instructions and the criteria; it is used once. The kernel has no PII redactor of
        its own (the privacy gateway runs on the laptop), so the approval is the gate; secrets are still scrubbed."""
        t = tasks.get(target, owner=agent)
        if not t:
            return 404, {"status": "refused", "reason": "no such task"}
        if lane == "private" and p.get("public_ok") is not True:
            return 400, {"status": "refused", "reason": "the lab uses public-lane models: send only what may be shared and set public_ok true "
                         "(the owner then approves exactly what is shared)"}
        aid = str(approval_id or "")[:40] or None
        held = share_pending(aid, target, agent) if (lane == "private" and aid and not str(p.get("instructions") or "").strip()) else None
        if held:                                   # the approved text itself: the agent sends only the approval id again
            p = dict(p, instructions=held[0], criteria=held[1], agent=held[2])
        instr, _, blocked = scrub(str(p.get("instructions", ""))[:8000], deep=True)
        crit = [scrub(str(c)[:500], deep=True)[0] for c in (p.get("criteria") or t["criteria"])[:20]]
        if blocked or not instr.strip() or not crit:
            return 400, {"status": "refused", "reason": "instructions and acceptance criteria are required, and no private keys"}
        if OWNER_PATH.search("\n".join([instr, *crit])):      # the lab cannot see them: such work stalls or "verifies" stale files
            return 400, {"status": "refused", "reason": "the lab cannot see the owner's computer: remove D:\\ / C:\\ / /mnt/ paths from "
                         "the instructions and criteria. The runner delivers verified files to D:\\jarvis\\outputs\\task-<id>\\ itself; "
                         "use a criterion like 'the files are delivered to the owner'."}
        if t.get("source") == "caretaker" or agent == CARETAKER_AGENT:
            # caretaker fixes reach main only as a pull request the owner merges: the text must be the fixed template
            from .policy import caretaker_problems
            why = caretaker_problems(instr, crit)
            if why:
                kernel.audit.append("caretaker_dispatch_refused", {"task": target, "agent": agent, "why": why[:5]})
                return 400, {"status": "refused", "reason": "caretaker lab text must come from the caretaker template: " + "; ".join(why)[:300]}
        agent_kind = str(p.get("agent") or "hermes")[:20]
        share = None
        if lane == "private":
            share = Action("lab", "share", target, {"agent": agent_kind, "instructions": instr, "criteria": crit,
                                                    "to": "public-lane lab models"}, lane, True, requester=agent)
            if not aid:
                if t["state"] not in ("planning", "paused", "waiting_approval"):
                    return 409, {"status": "refused", "reason": f"the lab is already on this task (state {t['state']}); do not dispatch again. "
                                 "End your turn: you will be told in this conversation when it ends."}
                d, aid = kernel.request(share)       # the approval card shows the whole scrubbed text (never cut: store.Kernel.request); the audit row counts only
                if d.denied:
                    return 403, {"status": "refused", "level": d.name, "reason": d.reason}
                if aid:
                    now = clock()
                    # pruned here, not when used: a dispatch sent twice at once must still find the approved text (the
                    # lab_run job holds the same text from then on). Gone once its approval is used, denied or long expired.
                    kernel.db.execute("DELETE FROM lab_share_pending WHERE created < ? OR approval_id IN "
                                      "(SELECT id FROM approvals WHERE state IN ('used','denied'))", (now - 2 * kernel.APPROVAL_TTL,))
                    kernel.db.execute("INSERT OR REPLACE INTO lab_share_pending VALUES (?,?,?,?,?,?,?)",
                                      (aid, target, agent, agent_kind, instr, json.dumps(crit), now))
                    if t["state"] == "planning":
                        tasks.update(agent, target, state="waiting_approval", step="waiting for the owner to approve sharing it with the public lab",
                                     note=f"approval {aid}: {len(instr)} characters of instructions and {len(crit)} criteria for the public lab")
                    return 202, {"status": "needs_approval", "approval_id": aid, "level": d.name, "reason": d.reason,
                                 "shares": {"instruction_chars": len(instr), "criteria": len(crit), "agent": agent_kind},
                                 "next": (f"The lab uses public-lane models, so the owner must approve sharing this task's text first. Tell the "
                                          f"owner in one line: approve {aid} in the JARVIS console with the PIN. Then END your turn. When the "
                                          f"owner says it is approved, call lab_work again with task {target} and approval_id {aid} "
                                          "(the approved text is kept; do not change it).")}
                # no approval needed (never for this action today): the final gate below still decides
        with dispatch_lock:                    # check and create as one step: two requests at once must not both pass
            t = tasks.get(target, owner=agent)
            active = kernel.db.execute("SELECT id FROM jobs WHERE kind='lab_run' AND state IN ('queued','running') AND params LIKE ?",
                                       (f'%"task": "{target}"%',)).fetchone()
            if active or t["state"] not in ("planning", "paused", "waiting_approval"):
                return 409, {"status": "refused", "reason": f"the lab is already on this task (state {t['state']}); do not dispatch again. "
                             "End your turn: you will be told in this conversation when it ends."}
            if share is not None:              # the owner's approval of exactly this text, used once (raises Killed)
                d = kernel.authorize(share, aid)
                if not d.allowed:
                    return 403, {"status": "refused", "level": d.name, "reason": d.reason + (
                        " (the approval covers exactly the text it was asked for: send lab_work with the approval id and the same "
                        "instructions, or with no instructions to use the approved ones)" if "different action" in d.reason else "")}
            params = {"task": target, "agent": agent_kind, "instructions": instr, "criteria": crit,
                      "max_attempts": int(t["max_attempts"]), "timeout_s": max(120, min(int(t["deadline"] - clock()), 5400))}
            jid = jobs.create("lab", "lab_run", params, owner=agent, lane=lane)
            tasks.link_job(target, jid)
            tasks.update(agent, target, state="queued", step=f"dispatched to the lab ({agent_kind})", criteria=crit, note="lab job " + jid
                         + (f" (shared with the owner's approval {aid})" if share is not None else ""))
        return 200, {"status": "queued", "task": target, "job": jid, "next": (
            "Tell the owner in one or two lines that the lab has started (give the task id) and END your turn now. "
            "Do not wait, poll or dispatch again: you will be told the verified outcome in this conversation.")}

    def create_task(agent, lane, target, p):
        """A new outcome task, unless the same request already has an open one."""
        req = " ".join(str(p.get("request", "")).lower().split())
        dup = kernel.db.execute("SELECT id, state FROM tasks WHERE owner=? AND state NOT IN ('completed','failed','cancelled') AND created>? "
                                "AND lower(request)=? LIMIT 1", (agent, clock() - 7200, req)).fetchone() if len(req) >= 12 else None
        if dup:                      # found live: the same request became two tasks and the lab queued both
            return 409, {"status": "refused", "task": dup[0], "reason": f"this request already has an open task ({dup[0]}, {dup[1]}): "
                         "use that one (task get) instead of creating another; the lab works on one task at a time and will tell you the outcome"}
        # the caretaker's token always makes caretaker tasks, so their dispatch always passes the caretaker guard
        source = "caretaker" if agent == CARETAKER_AGENT else str(p.get("source") or "agent")
        tid = tasks.create(agent, lane, p.get("request", ""), p.get("goal", target), p.get("plan"), p.get("criteria"),
                           source, int(p.get("max_attempts") or 3), int(p.get("budget_s") or 3600))
        if lane == "private" and agent == OWNER_AGENT and agent != CARETAKER_AGENT:
            # The owner's agent works inside one owner command at a time (the owner plane has a single busy gate): link the task
            # to it, so stopping that command can stop what it started (owner.cancel_command with stop_tasks).
            cid = owner_plane.current_command()
            if cid:
                tasks.link_command(tid, cid)
        return 200, {"status": "done", "task": tid, "next": (
            "Hands-on work (writing and running code, tests, browsing, files) goes to the lab now: "
            "lab_work(task_id, instructions, public_ok=true). You cannot run or test anything yourself, and this task "
            "can only be marked completed with independent verification or finished job ids as evidence.")}

    def task_action(agent, lane, op, target, p, approval_id=None):
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
            with create_lock:
                return create_task(agent, lane, target, p)
        if op == "get":
            t = tasks.get(target, owner=agent)
            return (200, {"status": "done", "task": t}) if t else (404, {"status": "refused", "reason": "no such task"})
        if op == "update":
            # A verdict is the verifier's, never the worker's (audit SEC-D1, 2026-10-18: an agent wrote {"passed": true} and then
            # closed its own task as "verified"). Only the lab runner's independent check or the owner records one.
            if p.get("verification") is not None:
                kernel.audit.append("task_verification_refused", {"agent": agent[:30], "task": str(target)[:12]})
                return 403, {"status": "refused", "reason": "verification is recorded only by the lab runner's independent check or by the "
                             "owner, never by an agent. Cite evidence (job:<id> of jobs done for this task) or hand the work to the lab (lab_work)."}
            busy = kernel.db.execute("SELECT 1 FROM jobs WHERE kind='lab_run' AND state IN ('queued','running') AND params LIKE ?",
                                     (f'%"task": "{target}"%',)).fetchone()
            if busy and p.get("state") not in (None, "", "paused", "cancelled"):
                return 409, {"status": "refused", "reason": "the lab owns this task's state while it works (you may pause or cancel "
                             "it, or add a note). End your turn: you will be told the outcome."}
            verdict = None
            if p.get("state") == "completed":       # completion needs proof, not a claim
                cur = tasks.get(target, owner=agent)
                if not cur:
                    return 404, {"status": "refused", "reason": "no such task"}
                if cur["state"] in FINAL:           # a repeated completion: refused cleanly, the first outcome stands
                    return 409, {"status": "refused", "reason": f"task is already {cur['state']}"}
                v = cur.get("verification") or {}
                trusted = v.get("verifier") in VERIFIERS
                if trusted and v.get("passed") is False:
                    return 409, {"status": "refused", "reason": "the independent check found unmet criteria; this task cannot be completed. "
                                 "Use state failed with the blocker, or hand the repair to the lab."}
                if not (trusted and v.get("passed") is True):
                    cited = [m for e in (p.get("evidence") or []) for m in re.findall(r"job:([0-9a-f]{12})", str(e))]
                    ok_jobs, problems = tasks.evidence_jobs(target, agent, cited)
                    if problems or not ok_jobs:
                        kernel.audit.append("task_completion_refused", {"agent": agent[:30], "task": str(target)[:12], "cited": len(cited),
                                                                        "accepted": len(ok_jobs)})
                        return 409, {"status": "refused", "reason": "completed needs proof: an independent verification (lab_work) or "
                                     "evidence citing jobs that were done FOR this task as job:<id> (start them with this task's id). "
                                     "If it cannot be verified, use state failed with the blocker.",
                                     "problems": problems[:10] or ["no job:<id> evidence was cited"]}
                    # the kernel records who decided and on what: finished jobs linked to the task. It cannot judge the
                    # criteria themselves, so this is not "verified" (no passed flag); the owner sees "NOT verified yet".
                    verdict = {"verifier": "kernel", "method": "finished jobs created for this task", "outcome": "evidence accepted",
                               "evidence": [f"job:{j}" for j in ok_jobs], "criteria_checked": False, "agent": agent[:40],
                               "recorded_at": clock()}
            t = tasks.update(agent, target, state=p.get("state"), step=p.get("step"), plan=p.get("plan"), criteria=p.get("criteria"),
                             note=p.get("note"), result=p.get("result"), evidence=p.get("evidence"), verification=verdict)
            return 200, {"status": "done", "state": t["state"]}
        return dispatch_to_lab(agent, lane, target, p, approval_id)

    def runner_call(path, body):
        """The lab runner's only endpoints: next lab job, task progress, job result. Reports apply only to the task of a
        lab job that is running, so a runner cannot rewrite other tasks."""
        tasks.expire()
        if path == "/v1/jarvis/runner/next":
            j = jobs.next_for("lab")
            return 200, {"job": j}
        job = str(body.get("job", ""))[:40]
        row = kernel.db.execute("SELECT params, state, cancel_requested FROM jobs WHERE id=? AND node='lab' AND kind='lab_run'", (job,)).fetchone()
        if path == "/v1/jarvis/runner/status" and kernel.killed():
            # The kill switch stops a lab run that is already going (audit SEC-D10): the runner polls this and kills the worker
            # and its children at once. Answered for any job id while the switch is on; it reveals nothing but that state.
            t = tasks.get(json.loads(row[0])["task"]) if row else None
            return 200, {"state": t["state"] if t else "failed", "killed": True}
        if not row or row[1] != "running":
            return 404, {"status": "refused", "reason": "no running lab job"}
        tid = json.loads(row[0])["task"]
        if path == "/v1/jarvis/runner/status":
            t = tasks.get(tid)
            # `cancel_requested` is the owner's cancel of THIS job (jobs.cancel marks a running job and waits for its node: the
            # lab runner used to ignore that mark). Present only when true, so the plain answer stays {"state": ...}.
            return 200, dict({"state": t["state"] if t else "failed"}, **({"cancel_requested": True} if row[2] is not None else {}))
        if path == "/v1/jarvis/runner/report":
            ver = body.get("verification")
            # What is checked is what was dispatched: the criteria stored on the JOB when it was created, not the task's current
            # criteria (an agent can rewrite those while the lab runs). Older jobs without a snapshot fall back to the task's.
            try:
                snap = json.loads(row[0]).get("criteria")
            except ValueError:
                snap = None
            snapshot = [str(c) for c in snap] if isinstance(snap, list) else None
            cur = tasks.get(tid) or {}
            demoted = ""
            if ver is not None:                  # the kernel stamps who recorded the verdict and for which lab job
                from .tasks import assure_verdict, criteria_sha256
                ver = dict(ver) if isinstance(ver, dict) else {"note": str(ver)[:1000]}
                # Lab verdict audit (2026-10-18): a verdict is bound to the TRUSTED identity the kernel holds, never to a secret the
                # checked code could learn. Task, job and attempt must all be present and equal what the kernel dispatched and counted
                # (a stale, replayed or forged file names another one); the criteria hash must be that of the job's own snapshot;
                # the task must be in verification right now (a late report is refused); and each (job, attempt) takes one verdict.
                if ver.get("task") != tid or ver.get("job") != job:                 # exact: an int 123 must not pass for the task id "123"
                    kernel.audit.append("lab_verdict_identity_mismatch", {"job": job[:40], "task": tid[:12]})
                    return 409, {"status": "refused", "reason": "task identity mismatch: the verdict is not for this task and lab job"}
                att = ver.get("attempt")
                if isinstance(att, bool) or not isinstance(att, int) or att != cur.get("attempts"):
                    kernel.audit.append("lab_verdict_attempt_mismatch", {"job": job[:40], "task": tid[:12]})
                    return 409, {"status": "refused", "reason": "attempt mismatch: the verdict is not for the attempt the kernel counted"}
                if snapshot is not None and ver.get("criteria_sha256") != criteria_sha256(snapshot):
                    kernel.audit.append("lab_verdict_criteria_mismatch", {"job": job[:40], "task": tid[:12]})
                    return 409, {"status": "refused", "reason": "criteria mismatch: the verdict was made for other criteria than the job's"}
                if cur.get("state") != "verifying":
                    kernel.audit.append("lab_verdict_out_of_order", {"job": job[:40], "task": tid[:12], "state": str(cur.get("state"))[:20]})
                    return 409, {"status": "refused", "reason": f"a verdict is accepted only while the task is being verified (it is {cur.get('state')})"}
                if not tasks.claim_verdict(job, att, str(ver.get("artifact_sha256", ""))):
                    kernel.audit.append("lab_verdict_repeated", {"job": job[:40], "task": tid[:12], "attempt": att})
                    return 409, {"status": "refused", "reason": "this attempt already has a verdict; a second one is refused"}
                ver.update(verifier="lab-runner", lab_job=job, recorded_at=clock())
                if snapshot is not None:
                    ver["criteria_checked_against"] = len(snapshot)
                # Only the runner's own execution of the delivered tests makes a pass "verified" (see tasks.assure_verdict).
                ver, demoted = assure_verdict(ver, body.get("trusted_execution"), tid, job, att)
                kernel.audit.append("lab_verdict", {"job": job[:40], "task": tid[:12], "attempt": att, "passed": ver.get("passed") is True,
                                                    "assurance": str(ver.get("assurance", "none"))[:30], "demoted": demoted[:100],
                                                    # the evidence the decision rests on (re-review H2, 2026-10-18)
                                                    "harness_sha256": str(ver.get("harness_sha256", ""))[:16],
                                                    "verdict_channel_sha256": str(ver.get("verdict_channel_sha256", ""))[:16],
                                                    "isolation": str((ver.get("isolation") or {}).get("mode", "none"))[:12]})
            want = snapshot if snapshot is not None else (cur.get("criteria") or [])
            if ver is not None and (ver.get("passed") is True or ver.get("agent_assessed_pass") is True) and body.get("state") != "completed":
                gap = _unmet_criteria(ver, want)
                if gap:                          # recorded, but never as a pass: an agent's later "completed" would trust it
                    ver = dict(ver, passed=False, agent_assessed_pass=False, kernel_check=gap[:300])
            if body.get("state") == "completed":
                v = ver if ver is not None else (cur.get("verification") or {})
                if v.get("verifier") not in VERIFIERS or not (v.get("passed") is True or v.get("agent_assessed_pass") is True):
                    if demoted and cur.get("state") not in FINAL:
                        # the runner claimed a pass the kernel cannot back with the runner's own execution: the task fails honestly
                        tasks.update(None, tid, state="failed", result=("not completed: " + demoted)[:600], evidence=body.get("evidence"),
                                     verification=ver)
                        return 409, {"status": "refused", "reason": "not verified: " + demoted[:300]}
                    return 409, {"status": "refused", "reason": "completed needs the independent check's passed verdict"}
                gap = _unmet_criteria(v, want)
                if gap and cur.get("state") not in FINAL:
                    # the verifier's own report contradicts its "passed": the task fails honestly instead of closing as verified
                    tasks.update(None, tid, state="failed", result=("not completed: the verifier's report does not show every criterion "
                                                                    "met: " + gap)[:600], evidence=body.get("evidence"),
                                 verification=dict(v, passed=False, agent_assessed_pass=False, kernel_check=gap[:300]))
                    return 409, {"status": "refused", "reason": "incomplete criteria: " + gap[:300]}
            t = tasks.update(None, tid, state=body.get("state"), step=body.get("step"), note=body.get("note"), result=body.get("result"),
                             evidence=body.get("evidence"), verification=ver, attempt=bool(body.get("attempt")))
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
            code, reply = owner_plane.handle(method, self.path, body, self.headers)
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
                # a retried action with the same Idempotency-Key gets its first reply back instead of acting twice
                # The scope names the lane too: the same agent name re-issued in the other lane never gets the first lane's reply.
                code, reply = idem.run(f"agent:{agent}:{lane}", idempotency.key_from(self.headers, body), body,
                                       lambda: agent_action(agent, lane, body), recheck=lambda: replay_recheck(agent, lane, body))
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
                # a malformed body is a 400, not a dropped connection (audit SEC-D10/R8): an object, a list of message objects
                if not isinstance(body, dict) or not isinstance(body.get("messages", []), list) \
                        or not all(isinstance(m, dict) for m in body.get("messages") or []):
                    raise ValueError
                for k in ("max_tokens", "max_completion_tokens"):
                    if k in body:
                        body[k] = min(int(body[k] or MAX_OUT_TOKENS), MAX_OUT_TOKENS)
            except (ValueError, TypeError, OSError):
                return self._json(400, {"error": {"message": "bad request"}})
            dropped = sorted(str(k)[:30] for k in body if k not in CHAT_FIELDS)[:12]   # names only, never values
            body = {k: v for k, v in body.items() if k in CHAT_FIELDS}
            body, findings, blocked = _scrub_messages(body)
            if blocked:
                kernel.audit.append("agent_model_refused", {"agent": agent, "why": "private key in prompt"})
                return self._json(400, {"error": {"message": "refused: the request contains a private key"}})
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
                if kernel.killed():                    # re-checked before EVERY provider (audit M9: store.check_alive says callers must
                    return self._stopped_by_kill(agent, "before a provider")      # re-check between steps): a fallback must not run
                model, why = p.default_model, "other"
                if not p.model_ok(model):
                    continue
                payload = dict(body, model=model)
                withheld = 0
                if getattr(p, "lane", "") != "local":                # images, audio and files go to a local model only (audit M2)
                    payload, withheld = withhold_media(payload)
                for k in ("max_tokens", "max_completion_tokens"):    # shrink the answer budget to what still fits this provider
                    if k in payload:
                        payload[k] = sizing.output_budget(p, est, payload[k])
                payload.update(p.extra_body)
                if summary:
                    payload["stream"] = False                        # read the whole summary to check it before passing it on
                headers = {"Content-Type": "application/json", "User-Agent": "jarvis-llm-proxy/0.1"}
                try:
                    headers.update(p.auth_headers())               # {} for a keyless or optional-and-absent key
                except (OSError, ValueError) as e:                # key withdrawn or unreadable: this provider fails, try the next
                    last, why = f"key unreadable ({type(e).__name__})", "other"
                    kernel.audit.append("agent_model_call_failed", {"agent": agent, "provider": p.name, "error": last, "class": "no_key"})
                    outcomes.append(why)
                    continue
                req = urllib.request.Request(p.base_url.rstrip("/") + "/chat/completions", data=json.dumps(payload).encode(), headers=headers, method="POST")
                resp, t_call = None, time.time()
                for attempt in (1, 2):
                    if attempt == 2 and kernel.killed():   # the retry of the same provider is a new step too
                        return self._stopped_by_kill(agent, "before a retry")
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
                if kernel.killed():                        # the switch went on while the provider was answering: nothing is passed on
                    resp.close()
                    return self._stopped_by_kill(agent, "before the answer was passed on")
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
                    sent, tail, broke = 0, b"", None
                    try:
                        while True:
                            chunk = resp.read1(65536) if hasattr(resp, "read1") else resp.read(65536)
                            if not chunk:
                                break
                            if kernel.killed():                        # between chunks: the kill switch ends a stream that is going
                                raise Killed("kill switch is on")
                            self.wfile.write(chunk)
                            self.wfile.flush()
                            sent += len(chunk)
                            tail = (tail + chunk)[-8192:]              # the provider's own token count sits at the end of the body/stream
                    except Exception as e:                             # stalled/cut mid-answer: end here, never invent a [DONE]
                        broke = "KillSwitch" if isinstance(e, Killed) else type(e).__name__
                        self.close_connection = True                   # the agent sees a cut-off answer, not a complete one
                if broke:
                    kernel.audit.append("agent_model_call_failed", {"agent": agent, "provider": p.name, "error": broke,
                                                                    "class": "killed" if broke == "KillSwitch" else "stream_broken",
                                                                    "stream": stream, "bytes_out": sent,
                                                                    "est_tokens": est, "ms": int((time.time() - t_call) * 1000)})
                    return
                kernel.audit.append("agent_model_call", {"agent": agent, "lane": lane, "provider": p.name, "stream": stream, "bytes_out": sent,
                                                         "messages": len(body.get("messages") or []), "tools": len(body.get("tools") or []),
                                                         "prompt_chars": prompt_chars, "tool_names": tool_names, "scrubbed": findings,
                                                         **({"media_withheld": withheld} if withheld else {}),
                                                         **({"dropped_fields": dropped} if dropped else {}),
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

        def _stopped_by_kill(self, agent, where):
            """The kill switch went on while a model request was being served: stop here, call no further provider."""
            kernel.audit.append("agent_model_stopped_by_kill", {"agent": agent, "where": where})
            return self._json(503, {"error": {"message": "kill switch is on"}})

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
    # projectfix (2026-10-18): fix a failing build in an allowlisted project. Owner routes only (owner/project_fix); the
    # owner's tick advances open fixes. The lab is reached only through dispatch_to_lab (the owner approves the sharing).
    from .projectfix import ProjectFix
    owner_plane.attach_projectfix(ProjectFix(kernel, tasks, jobs, dispatch=dispatch_to_lab, node=owner_plane.node))
    H.owner_plane = owner_plane
    return H, tokens


class ProxyServer(ThreadingHTTPServer):
    """The model proxy's HTTP server. Python's default listen queue is 5: a burst of more than five simultaneous agent
    calls (parallel sub-agents) had the extra connections refused and retried by TCP after about a second (measured in
    bench J-038: the last requests arrived 1.06 s late). Worker threads never keep the process alive on shutdown."""
    request_queue_size = 128
    daemon_threads = True


def serve(kernel, providers, port=8790, binds=("127.0.0.1",)):
    """Loopback always; extra binds (e.g. the Tailscale address, for agents in the laptop's WSL box) only when asked."""
    H, _ = make_handler(kernel, providers)
    try:                                         # re-review M4: bound the private text already stored (retention.py; idempotent, audited)
        from .retention import Retention
        Retention(kernel).sweep("startup")
    except Exception as e:
        print("retention at start failed:", type(e).__name__)
    from .owner import start_ticker
    start_ticker(H.owner_plane)                  # stuck jobs, deadlines, finished lab work: the kernel's own loop
    servers = [ProxyServer((b, port), H) for b in binds]
    for s in servers[1:]:
        threading.Thread(target=s.serve_forever, daemon=True).start()
    servers[0].serve_forever()
