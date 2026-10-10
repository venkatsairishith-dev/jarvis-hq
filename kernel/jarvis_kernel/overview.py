"""Read-only overview for the owner's hub (the Agent OS dashboard): counts, states, names and times only.

No prompt text, no tool arguments, no file contents and no secrets: every field copied out of the audit log or the job
table is whitelisted here. Served at GET /v1/jarvis/overview, only to a dashboard-lane token (which can never call a
model or act). Custom code, justified in docs/01_RESEARCH_AUDIT.md: the kernel's approvals, jobs and audit are the
authority, so their view comes from the kernel itself, not from a second dashboard store.
"""
import json
import time

SAFE_KEYS = ("agent", "lane", "provider", "tool", "op", "status", "id", "node", "error", "level", "owner", "attempt", "name", "ok", "cmd")
RESULT_KEYS = ("ok", "error", "saved", "shown", "opened", "done", "folder", "count")


def _load(payload):
    try:
        d = json.loads(payload)
        return d if isinstance(d, dict) else {}
    except (TypeError, ValueError):
        return {}


def _short(d: dict) -> dict:
    out = {k: d[k] for k in SAFE_KEYS if isinstance(d.get(k), (str, int, float, bool))}
    if isinstance(d.get("kind"), str):          # a job's kind: kept apart from the audit event's own kind
        out["what"] = d["kind"]
    a = d.get("action")
    if isinstance(a, dict):
        out.update({k: a.get(k) for k in ("tool", "op", "lane") if isinstance(a.get(k), str)})
    return {k: (v[:60] if isinstance(v, str) else v) for k, v in out.items()}


def overview(kernel, providers, now=None) -> dict:
    now = time.time() if now is None else now
    since = now - 86400
    db = kernel.db
    agents, prov, actions = {}, {}, {}
    calls = failed_requests = 0
    series = {"calls": [0] * 24, "failed": [0] * 24, "actions": [0] * 24}     # per hour, oldest first, last = this hour

    def hour(ts):
        return min(23, max(0, 23 - int((now - ts) // 3600)))
    for ts, kind, payload in db.execute("SELECT ts, kind, payload FROM audit WHERE ts >= ? ORDER BY seq", (since,)):
        if kind == "agent_model_call":
            series["calls"][hour(ts)] += 1
        elif kind == "agent_model_request_failed":
            series["failed"][hour(ts)] += 1
        elif kind == "agent_action":
            series["actions"][hour(ts)] += 1
        if kind in ("agent_model_call", "agent_model_call_failed", "agent_model_request_failed"):
            d = _load(payload)
            name = str(d.get("agent") or "?")[:40]
            a = agents.setdefault(name, {"name": name, "lane": d.get("lane"), "calls": 0, "failed_requests": 0,
                                         "last_call": None, "provider": None, "prompt_chars": 0})
            if kind == "agent_model_call":
                calls += 1
                a.update(calls=a["calls"] + 1, last_call=ts, provider=d.get("provider"), lane=d.get("lane") or a["lane"])
                a["prompt_chars"] += int(d.get("prompt_chars") or 0)
                p = prov.setdefault(str(d.get("provider")), {"ok": 0, "failed": 0, "last_ok": None, "last_fail": None, "last_error": None})
                p.update(ok=p["ok"] + 1, last_ok=ts)
            elif kind == "agent_model_call_failed":
                p = prov.setdefault(str(d.get("provider")), {"ok": 0, "failed": 0, "last_ok": None, "last_fail": None, "last_error": None})
                p.update(failed=p["failed"] + 1, last_fail=ts, last_error=str(d.get("error", ""))[:40])
            else:
                failed_requests += 1
                a["failed_requests"] += 1
        elif kind == "agent_action":
            d = _load(payload)
            key = f"{str(d.get('tool'))[:20]}.{str(d.get('op'))[:20]}"
            actions[key] = actions.get(key, 0) + 1

    jobs, durations = [], []
    for jid, kind, state, owner, created, started, finished, result in db.execute(
            "SELECT id, kind, state, owner, created, started, finished, result FROM jobs ORDER BY created DESC LIMIT 25"):
        r = _load(result)
        jobs.append({"id": jid, "kind": kind, "state": state, "owner": owner or "owner", "created": created, "started": started,
                     "finished": finished, "result": {k: (str(r[k])[:80] if isinstance(r[k], str) else r[k])
                                                      for k in RESULT_KEYS if isinstance(r.get(k), (str, int, float, bool))}})
        if created and created >= since and started and finished:
            durations.append(finished - started)
    jobs_24h = db.execute("SELECT COUNT(*) FROM jobs WHERE created >= ?", (since,)).fetchone()[0]

    pending = []
    for aid, level, expires, action in db.execute(
            "SELECT id, level, expires, action FROM approvals WHERE state='pending' AND expires >= ? ORDER BY expires", (now,)):
        a = _load(action)
        pending.append({"id": aid, "level": level, "expires": expires, "tool": str(a.get("tool", ""))[:20],
                        "op": str(a.get("op", ""))[:20], "target": str(a.get("target", ""))[:60], "lane": a.get("lane")})

    from .nodes import Nodes
    full = Nodes(kernel).list()
    nodes = [{k: n[k] for k in ("name", "online", "seconds_since_report")} for n in full]
    outputs = []          # the laptop's outputs folder as its node last reported it: names, sizes, times
    for n in full:
        for o in ((n.get("state") or {}).get("outputs") or [])[:40]:
            if isinstance(o, dict):
                outputs.append({"node": n["name"], **{k: o[k] for k in ("agent", "name", "bytes", "modified")
                                                      if isinstance(o.get(k), (str, int, float))}})
    try:
        prefs = dict(db.execute("SELECT lane, provider FROM lane_prefs").fetchall())
    except Exception:
        prefs = {}
    providers_out = []
    for p in providers:
        if not p.enabled:
            continue
        s = prov.get(p.name, {})
        providers_out.append({"name": p.name, "lane": p.lane, "model": p.default_model, "available": p.available(),
                              "preferred": prefs.get(p.lane) == p.name, **{k: s.get(k) for k in ("ok", "failed", "last_ok", "last_fail", "last_error")}})
    from .tasks import Tasks                     # the owner's own outcome tasks: request, goal, plan, criteria, state, evidence
    T = Tasks(kernel)
    T.expire(now)
    task_list = [{k: t.get(k) for k in ("id", "created", "updated", "owner", "lane", "source", "request", "goal", "plan", "criteria", "state",
                                        "step", "attempts", "max_attempts", "deadline", "result", "evidence", "verification", "events")}
                 for t in T.recent(15)]
    tail = [{**_short(_load(payload)), "seq": seq, "ts": ts, "kind": kind}
            for seq, ts, kind, payload in db.execute("SELECT seq, ts, kind, payload FROM audit ORDER BY seq DESC LIMIT 40")]
    return {
        "now": now,
        "killed": kernel.killed(),
        "kpis": {"model_calls_24h": calls, "failed_requests_24h": failed_requests,
                 "success_rate_24h": round(100 * calls / (calls + failed_requests), 1) if calls + failed_requests else None,
                 "actions_24h": sum(actions.values()), "jobs_24h": jobs_24h,
                 "avg_job_seconds_24h": round(sum(durations) / len(durations), 1) if durations else None,
                 "approvals_pending": len(pending), "est_tokens_in_24h": sum(a["prompt_chars"] for a in agents.values()) // 4},
        "series_24h": series,
        "agents": sorted(agents.values(), key=lambda a: -(a["last_call"] or 0)),
        "actions_24h": actions,
        "providers": providers_out,
        "nodes": nodes,
        "outputs": outputs,
        "tasks": task_list,
        "approvals": pending,
        "jobs": jobs,
        "audit": tail,
    }
