"""Task traces: a read-only, redacted view of what happened to one outcome task, and an analyzer over many.

A trace is NOT a second task store: it is assembled on demand from what the kernel already records (tasks,
task_events, jobs, the audit log) through its own read-only SQLite connection, so it adds no writer to the kernel's
shared connection. Every free-text field goes through the secret scrubber and is clipped; the audit log itself holds
no prompt or reply text. --public drops free text altogether (for anything leaving the private lane).

Pattern adapted from OpenJarvis (Apache-2.0, https://github.com/open-jarvis/OpenJarvis at ecbde4b3):
src/openjarvis/core/types.py Trace/TraceStep (fields: steps, outcome, feedback, tokens, latency) and
src/openjarvis/traces/analyzer.py TraceAnalyzer (per-route and per-tool stats). Not copied: OpenJarvis stores raw
messages in its own traces.db without redaction or size limits; here the kernel record is the source of truth.
The analyzer only reports and suggests; it never changes policies, routing or skills.
"""
import collections
import json
import os
import re
import sqlite3
import time

from .scrub import scrub

TEXT = 300                       # longest free-text field kept in a trace
MAX_TRACE_BYTES = 48_000         # a saved trace is bounded
FINAL = ("completed", "failed", "cancelled")
CATEGORIES = (                   # first match wins; the goal and criteria decide
    ("web", r"\b(website|web ?page|pages?|browser|url|localhost|vercel|frontend|ui)\b"),
    ("software", r"\b(code|cli|python|script|tests?|bug|fix|build|package|function|api|unittest)\b"),
    ("research", r"\b(research|compare|find out|investigate|sources?|summari[sz]e)\b"),
    ("document", r"\b(document|report|write|readme|docs?|essay|notes?)\b"),
)
ROLES = {"lab": "lab worker/verifier", "lab-runner": "runner"}


def open_readonly(data_dir: str = None) -> sqlite3.Connection:
    """A separate read-only connection to the kernel database (never the kernel's shared writer)."""
    d = data_dir or os.environ.get("JARVIS_DATA") or os.path.join(os.path.expanduser("~"), ".jarvis-kernel")
    path = os.path.abspath(os.path.join(d, "state.db")).replace("\\", "/")
    return sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=10)


def _text(s, public=False, n=TEXT):
    if public:
        return None
    return scrub(str(s or ""))[0][:n]


def category(goal: str, criteria) -> str:
    blob = (str(goal) + " " + " ".join(map(str, criteria or []))).lower()
    for name, rx in CATEGORIES:
        if re.search(rx, blob):
            return name
    return "other"


def _row(db, sql, args=()):
    try:
        cur = db.execute(sql, args)
    except sqlite3.OperationalError as e:      # a table this kernel never created (e.g. no job yet): nothing recorded
        if "no such table" in str(e):
            return []
        raise
    cols = [c[0] for c in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


def build(db, tid: str, public: bool = False) -> dict | None:
    """The trace of one task, or None. Bounded and redacted."""
    rows = _row(db, "SELECT * FROM tasks WHERE id=?", (tid,))
    if not rows:
        return None
    t = rows[0]
    crit = json.loads(t.get("criteria") or "[]")
    ver = json.loads(t.get("verification") or "{}")
    evidence = json.loads(t.get("evidence") or "[]")
    events = _row(db, "SELECT ts, state, kind, note FROM task_events WHERE task=? ORDER BY id", (tid,))
    jobs = _row(db, "SELECT id, node, kind, state, owner, created, finished, params, result FROM jobs WHERE "
                    "(kind='lab_run' AND json_extract(params,'$.task')=?) OR owner=? ORDER BY created", (tid, f"runner-{tid}"))
    end = t["updated"] if t["state"] in FINAL else time.time()
    lab = [j for j in jobs if j["kind"] == "lab_run"]
    runs = [(j["created"], j["finished"] or end) for j in lab]
    agents = {t["owner"]}

    def in_task(ts, agent):
        if agent == t["owner"]:
            return t["created"] - 600 <= ts <= end              # planning starts just before the task is created
        return agent in ("lab", "lab-runner") and any(a <= ts <= b for a, b in runs)

    audit = [(ts, kind, json.loads(p)) for ts, kind, p in db.execute(
        "SELECT ts, kind, payload FROM audit WHERE ts BETWEEN ? AND ? AND (kind LIKE 'agent_model%' OR kind='agent_action' "
        "OR kind LIKE 'task_%' OR kind='job_queued' OR kind LIKE 'tg_%') ORDER BY seq", (t["created"] - 600, end + 5)).fetchall()]
    calls, fails, skipped, actions = collections.Counter(), collections.Counter(), collections.Counter(), collections.Counter()
    prompt_chars, out_bytes, routing = 0, 0, []
    for ts, kind, d in audit:
        agent = d.get("agent")
        if agent and not in_task(ts, agent):
            continue
        if agent:
            agents.add(agent)
        role = ROLES.get(agent, "planner" if agent == t["owner"] else agent)
        if kind == "agent_model_call":
            calls[(role, d.get("provider"))] += 1
            prompt_chars += int(d.get("prompt_chars") or 0); out_bytes += int(d.get("bytes_out") or 0)
        elif kind == "agent_model_call_failed":
            fails[(role, d.get("provider"), d.get("error"), d.get("class", ""))] += 1
        elif kind in ("agent_model_skipped", "agent_model_too_large", "agent_model_rate_limited", "agent_model_request_failed"):
            skipped[(role, kind)] += 1
            if len(routing) < 20:
                routing.append({"t": round(ts - t["created"]), "role": role, "event": kind,
                                **{k: d[k] for k in ("est_tokens", "providers", "cooling", "retry_after_s", "error") if k in d}})
        elif kind == "agent_action":
            actions[(role, f"{d.get('tool')}.{d.get('op')}", d.get("status"))] += 1
    deliveries = [{"job": j["id"], "state": j["state"], "saved": (json.loads(j["result"] or "{}") or {}).get("saved"),
                   "path": None if public else (json.loads(j["result"] or "{}") or {}).get("folder")}
                  for j in jobs if j["kind"] == "output_save"]
    crit_v = [{"criterion": _text(c.get("criterion"), public, 160), "met": bool(c.get("met")),
               "evidence": _text(c.get("evidence"), public, 200)} for c in (ver.get("criteria") or [])[:15] if isinstance(c, dict)]
    attempts = ver.get("history") or []
    trace = {
        "trace_version": 1, "task": tid, "lane": t["lane"], "source": t["source"], "category": category(t["goal"], crit),
        "goal": _text(t["goal"], public), "criteria": [_text(c, public, 160) for c in crit] if not public else len(crit),
        "environment": {"lab_agent": json.loads(lab[0]["params"]).get("agent") if lab else None,
                        "nodes": sorted({j["node"] for j in jobs}), "agents": sorted(a for a in agents if a)},
        "outcome": {"state": t["state"], "verified": ver.get("passed") is True, "attempts": t["attempts"],
                    "max_attempts": t["max_attempts"], "result": _text(t["result"], public),
                    "failure_class": ver.get("failure_class"), "corrected": any(e["kind"] == "correction" for e in events)},
        "timing": {"created": round(t["created"]), "duration_s": round(end - t["created"]),
                   "first_dispatch_s": round(lab[0]["created"] - t["created"]) if lab else None,
                   "lab_s": round(sum(b - a for a, b in runs))},
        "events": [{"t": round(e["ts"] - t["created"]), "state": e["state"], "kind": e["kind"], "note": _text(e["note"], public, 200)}
                   for e in events][-40:],
        "verification": {"passed": ver.get("passed"), "criteria": crit_v, "attempt_history": attempts[:5],
                         "problems": [_text(p, public, 200) for p in (ver.get("problems") or [])[:8]] if not public else len(ver.get("problems") or [])},
        "repairs": [{"t": round(e["ts"] - t["created"]), "problems": _text(e["note"], public, 300)} for e in events if e["state"] == "repairing" and e["kind"] == "state"],
        "jobs": [{"id": j["id"], "kind": j["kind"], "node": j["node"], "state": j["state"],
                  "s": round((j["finished"] or end) - j["created"])} for j in jobs][:30],
        "deliveries": deliveries[:10],
        "delivery_evidence": [_text(e, public, 200) for e in evidence if str(e).startswith("delivered: ")][:10] if not public else
                             sum(1 for e in evidence if str(e).startswith("delivered: ")),
        "models": {"calls": [{"role": r, "provider": p, "n": n} for (r, p), n in calls.most_common(10)],
                   "failures": [{"role": r, "provider": p, "error": e, "class": c, "n": n} for (r, p, e, c), n in fails.most_common(12)],
                   "routing": routing, "gateway_refusals": {f"{r}:{k}": n for (r, k), n in skipped.items()},
                   "est_tokens_in": int(prompt_chars / 3.5), "bytes_out": out_bytes, "cost_usd": 0.0},
        "actions": [{"role": r, "action": a, "status": s, "n": n} for (r, a, s), n in actions.most_common(15)],
        "notes": "model and action rows are attributed by time window and agent; lab rows assume one lab job at a time",
    }
    raw = json.dumps(trace)
    while len(raw) > MAX_TRACE_BYTES and trace["events"]:
        trace["events"] = trace["events"][len(trace["events"]) // 2:]
        trace["truncated"] = True
        raw = json.dumps(trace)
    return trace


def save(trace: dict, folder: str) -> str:
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, f"{trace['task']}.json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(trace, fh, indent=1)
    return path


# ---------------------------------------------------------------- analysis
_NORM = [(re.compile(r"/home/\S+|[A-Za-z]:\\\S+"), "<path>"), (re.compile(r"\b[0-9a-f]{12}\b"), "<id>"), (re.compile(r"\d+"), "<n>")]


def _norm(s: str) -> str:
    s = str(s or "").lower()
    for rx, rep in _NORM:
        s = rx.sub(rep, s)
    return " ".join(s.split())[:90]


def analyze(db, days: float = 7, public: bool = False) -> dict:
    """Recurring failures across recent tasks, and suggestions. Read-only: it changes nothing."""
    since = time.time() - days * 86400
    ids = [r[0] for r in db.execute("SELECT id FROM tasks WHERE created>=? ORDER BY created", (since,)).fetchall()]
    traces = [x for x in (build(db, i, public=False) for i in ids) if x]
    out = collections.Counter(tr["outcome"]["state"] for tr in traces)
    lab = [tr for tr in traces if tr["environment"]["lab_agent"]]
    first_pass = sum(1 for tr in lab if tr["outcome"]["verified"] and tr["outcome"]["attempts"] == 1)
    problems, provider_fail, refusals, actions = collections.Counter(), collections.Counter(), collections.Counter(), collections.Counter()
    for tr in traces:
        for p in tr["verification"]["problems"] or []:
            problems[_norm(p)] += 1
        for r in tr["repairs"]:
            for p in str(r["problems"]).split(";"):
                if p.strip():
                    problems[_norm(p)] += 1
        for f in tr["models"]["failures"]:
            provider_fail[(f["provider"], f["error"], f["class"] or "?")] += f["n"]
        for k, n in tr["models"]["gateway_refusals"].items():
            refusals[k] += n
        for a in tr["actions"]:
            if a["status"] not in ("done", "queued", "allowed"):
                actions[(a["action"], a["status"])] += a["n"]
    corrected = [tr["task"] for tr in traces if tr["outcome"]["corrected"]]
    suggestions = []
    total_fail = sum(provider_fail.values())
    for (prov, err, cls), n in provider_fail.most_common(5):
        if n >= 5 and (cls == "too_large" or err in ("HTTP 413", "HTTP 400")):
            suggestions.append(f"routing: {prov} refused {n} requests as too large ({err}); keep its measured limit in providers.json so "
                               "the gateway skips it, and compact the agent's history earlier")
        elif n >= 5 and (cls == "rate_limited" or err == "HTTP 429"):
            suggestions.append(f"routing: {prov} was rate limited {n} times; a used-up free quota needs a cooldown, or another free "
                               "provider in the same lane")
        elif n >= 5 and str(err).startswith("HTTP 5"):
            suggestions.append(f"reliability: {prov} failed {n} times with {err} (provider outage); check that the lane has a working "
                               "fallback and that the runner survives it")
    for p, n in problems.most_common(8):
        if n < 2:
            continue
        if "deliver" in p:
            suggestions.append(f"prompt: '{p}' x{n} -> the worker prompt must make ./deliver/ unmissable")
        elif "verify.json" in p or "verifier" in p:
            suggestions.append(f"verifier: '{p}' x{n} -> retry the verifier once before counting the attempt as failed")
        elif "lab path" in p or "fresh folder" in p:
            suggestions.append(f"skill/prompt: '{p}' x{n} -> delivered tests must not depend on the lab workspace")
        else:
            suggestions.append(f"plan: recurring problem '{p}' x{n} -> add it to the acceptance criteria up front")
    if corrected:
        suggestions.append(f"verification gap: {len(corrected)} task(s) needed a correction after 'completed' ({', '.join(corrected)}); "
                           "add an owner-side check of the delivered files to the eval set")
    for (a, s), n in actions.most_common(5):
        if n >= 2:
            suggestions.append(f"planner: {a} was {s} {n} times (rate limits, guards or bad input: check the audit for the reasons)")
    return {
        "window_days": days, "tasks": len(traces), "outcomes": dict(out),
        "lab_tasks": len(lab), "first_attempt_verified": first_pass,
        "avg_attempts": round(sum(tr["outcome"]["attempts"] for tr in lab) / len(lab), 2) if lab else None,
        "avg_duration_s": round(sum(tr["timing"]["duration_s"] for tr in traces) / len(traces)) if traces else None,
        "recurring_problems": [{"problem": p if not public else None, "n": n} for p, n in problems.most_common(10)],
        "provider_failures": [{"provider": p, "error": e, "class": c, "n": n} for (p, e, c), n in provider_fail.most_common(10)],
        "provider_failure_total": total_fail,
        "gateway_refusals": dict(refusals), "refused_actions": [{"action": a, "status": s, "n": n} for (a, s), n in actions.most_common(10)],
        "corrected_tasks": corrected,
        "suggestions": suggestions if not public else len(suggestions),
        "note": "read-only report: nothing was changed; suggestions need evidence from the eval suite before any change",
    }
