"""Model selector: which provider/model SHOULD serve a task, and why. Recommend-only.

    python -m jarvis_kernel.model_select "summarise this PDF" [--lane private] [--tools] [--vision] [--chars N]
                                         [--prefer NAME] [--evidence] [--allow-unverified]

What it does, in order:
1. The routing rules are gateway.explain() unchanged: lanes (a private task never reaches a public provider), paid
   providers refused, missing keys, purpose tags, measured context limits, rest after errors, owner preference.
2. What the task needs: tool calls, image input, and how many tokens (sizing.py's chars/4 estimate, labelled as an
   estimate) plus room for the answer (sizing.MIN_OUTPUT).
3. What each provider's model is DECLARED to support, from jarvis/config/model_catalog.json (models.dev, built by
   jarvis/scripts/refresh_model_catalog.py). A capability the catalog does not state is "unknown", never "yes": a task
   that needs it skips that provider unless the owner passes allow_unverified.
4. What was MEASURED: calls, failures, latency and the providers' own token counts from the kernel audit log
   (read-only connection). Nothing is inferred from a model's name.

What it never does: call a model, read a key, write any file or setting, or change the live route. gateway.call() and
llm_proxy keep routing exactly as before; making a recommendation the default is the existing owner-approved
`models.prefer` action, done by a person. Fallback safety is fallback_allowed() below.
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
import time

from . import gateway, sizing

HERE = os.path.dirname(os.path.abspath(__file__))
CONFIG = os.path.normpath(os.path.join(HERE, "..", "..", "config"))
PROVIDERS = os.path.join(CONFIG, "providers.json")
PROBES = os.path.join(CONFIG, "measured_capabilities.json")
CATALOG = os.path.join(CONFIG, "model_catalog.json")
FAILING_MIN_CALLS = 5          # measured failure share only counts once there are this many calls
FAILING_SHARE = 0.5


def load_catalog(path: str = CATALOG) -> dict:
    try:
        with open(path, encoding="utf-8-sig") as f:
            return json.load(f)
    except FileNotFoundError:
        return {"providers": {}, "source": None}


def requirements(task: str = "", *, body: dict | None = None, lane: str = "private", tools: bool | None = None,
                 vision: bool | None = None, out_tokens: int = sizing.MIN_OUTPUT, purpose: str | None = None) -> dict:
    """What a request needs. The lane is the caller's (the kernel decides it); it defaults to private, the safe side."""
    if lane not in ("private", "public"):
        raise ValueError("lane must be private or public")
    body = body or {"messages": [{"role": "user", "content": task}]}
    msgs = body.get("messages") or []
    has_image = any(isinstance(m, dict) and isinstance(m.get("content"), list)
                    and any(isinstance(p, dict) and p.get("type") in ("image_url", "input_image") for p in m["content"])
                    for m in msgs)
    return {"lane": lane, "purpose": purpose,
            "tools": bool(body.get("tools")) if tools is None else bool(tools),
            "vision": has_image if vision is None else bool(vision),
            "est_tokens": sizing.estimate_tokens(body), "est_method": "chars/4 (sizing.py), an estimate",
            "out_tokens": int(out_tokens)}


def declared(catalog: dict, provider: str, model: str) -> dict | None:
    """The catalog entry for this provider's model, or None when models.dev does not list it for this provider."""
    return ((catalog.get("providers") or {}).get(provider) or {}).get("models", {}).get(model)


def load_probes(path: str = PROBES) -> dict:
    """Measured capability probes written by capability_probe.py: {provider: {"tools": {"ok": bool, "model", "when", ...}}}."""
    try:
        with open(path, encoding="utf-8-sig") as f:
            return json.load(f).get("providers", {})
    except (FileNotFoundError, ValueError):
        return {}


def capability_codes(entry: dict | None, req: dict, allow_unverified: bool, probe: dict | None = None,
                     model: str | None = None) -> tuple[list, list]:
    """(blocking codes, notes) for one candidate. Unknown is reported as unknown. A measured probe of THIS model beats
    the catalog's declaration, in both directions."""
    block, notes = [], []
    tool_probe = (probe or {}).get("tools")
    if tool_probe and model and tool_probe.get("model") != model:
        tool_probe = None                       # measured on another model: says nothing about this one
    if entry is None:
        notes.append("NOT_IN_CATALOG")
    for need, key, tag in (("tools", "tool_call", "TOOLS"), ("vision", None, "VISION")):
        if not req.get(need):
            continue
        if need == "tools" and tool_probe is not None:
            (notes if tool_probe.get("ok") else block).append("TOOLS_MEASURED" if tool_probe.get("ok") else "TOOLS_MEASURED_FAILED")
            continue
        if entry is None:
            val = None
        elif key:
            val = entry.get(key)
        else:
            mods = (entry.get("modalities") or {}).get("input")
            val = None if mods is None else ("image" in mods)
        if val is True:
            notes.append(f"{tag}_DECLARED")
        elif val is False:
            block.append(f"{tag}_NOT_SUPPORTED")
        else:
            (notes if allow_unverified else block).append(f"{tag}_UNVERIFIED")
    ctx = ((entry or {}).get("limit") or {}).get("context")
    if isinstance(ctx, int) and req["est_tokens"] + req["out_tokens"] > ctx:
        block.append("CATALOG_CONTEXT_TOO_SMALL")
    cost = (entry or {}).get("cost") or {}
    if any(isinstance(v, (int, float)) and v > 0 for v in cost.values()):
        notes.append("LIST_PRICE_NONZERO")     # free use rests on the provider's free tier; providers.json says unpaid
    return block, notes


def evidence(conn, days: int = 7, now: float | None = None) -> dict:
    """Measured, per provider, from the audit log: calls, failures, latency, and token counts the provider reported."""
    since = (now or time.time()) - days * 86400
    out: dict = {}
    rows = conn.execute("SELECT kind, payload FROM audit WHERE ts >= ? AND kind IN ('agent_model_call', 'agent_model_call_failed')",
                        (since,)).fetchall()
    for kind, payload in rows:
        try:
            p = json.loads(payload)
        except (TypeError, ValueError):
            continue
        name = p.get("provider")
        if not name:
            continue
        e = out.setdefault(name, {"calls": 0, "failed": 0, "ms": [], "prompt_tokens": 0, "completion_tokens": 0, "with_usage": 0})
        if kind == "agent_model_call_failed":
            e["failed"] += 1
            continue
        e["calls"] += 1
        if isinstance(p.get("ms"), (int, float)):
            e["ms"].append(p["ms"])
        u = p.get("usage") or {}
        if "prompt_tokens" in u or "completion_tokens" in u:
            e["with_usage"] += 1
            e["prompt_tokens"] += int(u.get("prompt_tokens") or 0)
            e["completion_tokens"] += int(u.get("completion_tokens") or 0)
    for e in out.values():
        ms = e.pop("ms")
        e["p50_ms"] = int(statistics.median(ms)) if ms else None
        total = e["calls"] + e["failed"]
        e["failure_share"] = round(e["failed"] / total, 3) if total else None
        e["window_days"] = days
    return out


def select(providers, catalog: dict, req: dict, *, rest: dict | None = None, preferred: str | None = None,
           override: str | None = None, measured: dict | None = None, allow_unverified: bool = False,
           probes: dict | None = None) -> dict:
    """The recommendation. `preferred` is the stored owner preference (as gateway.explain); `override` is a one-off
    owner choice for this task, honoured only when that provider passes every rule and every declared need."""
    base = gateway.explain(providers, req["lane"], req.get("purpose"), req["est_tokens"], rest, preferred)
    by_name = {p.name: p for p in providers}
    measured = measured or {}
    rows, ok = [], []
    for row in base["candidates"]:
        p = by_name[row["provider"]]
        entry = declared(catalog, p.name, p.default_model)
        block, notes = capability_codes(entry, req, allow_unverified, (probes or {}).get(p.name), p.default_model)
        if not sizing.fits_with_output(p, req["est_tokens"], req["out_tokens"]):
            block.append("NO_ROOM_FOR_ANSWER")
        m = measured.get(p.name)
        if m and (m["calls"] + m["failed"]) >= FAILING_MIN_CALLS and (m["failure_share"] or 0) >= FAILING_SHARE:
            notes.append("MEASURED_FREQUENT_FAILURES")
        r = {"provider": p.name, "lane": p.lane, "model": p.default_model, "priority": p.priority,
             "route_codes": [c for c in row["codes"] if row["verdict"] == "excluded"],
             "capability_codes": block, "notes": notes,
             "declared": None if entry is None else {k: entry.get(k) for k in ("tool_call", "attachment", "reasoning", "limit", "cost", "modalities")},
             "measured": m}
        r["verdict"] = "eligible" if row["verdict"] == "eligible" and not block else "excluded"
        rows.append(r)
        if r["verdict"] == "eligible":
            ok.append((row.get("rank", 99), "MEASURED_FREQUENT_FAILURES" in notes, r))
    # gateway order (owner preference, priority, local first); measured frequent failures move to the end
    ok.sort(key=lambda t: (t[1], t[0]))
    chain = [r["provider"] for _, _, r in ok]
    override_result = None
    if override:
        if override in chain:
            chain.remove(override)
            chain.insert(0, override)
            override_result = {"provider": override, "honoured": True}
        else:
            hit = next((r for r in rows if r["provider"] == override), None)
            override_result = {"provider": override, "honoured": False,
                               "why": (hit["route_codes"] + hit["capability_codes"]) if hit else ["UNKNOWN_PROVIDER"]}
    live = base["chosen"]
    rec = chain[0] if chain else None
    return {"recommended": rec, "model": by_name[rec].default_model if rec else None, "chain": chain,
            "requirements": req, "override": override_result, "candidates": rows,
            "live_route_would_choose": live, "differs_from_live_route": rec != live,
            "catalog": {"source": catalog.get("source"), "built": catalog.get("built"), "sha256": catalog.get("source_sha256")},
            "applies": False, "calls_made": 0,
            "how_to_apply": "Nothing changed. To make a provider the default, the owner uses the kernel action models.prefer "
                            "(approval-gated); provider settings in providers.json change only with the owner's approval.",
            "note": None if rec else "No provider meets this task's rules and declared needs; nothing would be called."}


def shadow(conn, providers, catalog: dict, days: int = 7, probes: dict | None = None, now: float | None = None) -> dict:
    """Shadow mode, offline: for each real call in the audit log, what would the selector recommend TODAY (current
    providers.json, key files, catalog), compared with the provider that actually served it. Read-only; no call is made
    and nothing is recorded in the live path. Needs come from what the log kept: lane, tool count, estimated tokens.
    Because it uses today's availability, a provider that was down or has since changed shows up as a difference to look
    at, not as a fault of either router."""
    since = (now or time.time()) - days * 86400
    names = {p.name for p in providers}
    total = same = unknown = 0
    diffs: dict = {}
    for (payload,) in conn.execute("SELECT payload FROM audit WHERE ts >= ? AND kind = 'agent_model_call'", (since,)):
        try:
            p = json.loads(payload)
        except (TypeError, ValueError):
            continue
        actual, lane = p.get("provider"), p.get("lane")
        if lane not in ("private", "public") or actual not in names:
            unknown += 1
            continue
        req = requirements(body={"messages": [{"role": "user", "content": "x"}]}, lane=lane, tools=bool(p.get("tools")))
        req["est_tokens"] = int(p.get("est_tokens") or req["est_tokens"])
        rec = select(providers, catalog, req, probes=probes)
        total += 1
        if rec["recommended"] == actual:
            same += 1
        else:
            key = f"{actual} -> {rec['recommended']}"
            d = diffs.setdefault(key, {"calls": 0, "served_provider_codes": []})
            d["calls"] += 1
            row = next((r for r in rec["candidates"] if r["provider"] == actual), None)
            d["served_provider_codes"] = (row["route_codes"] + row["capability_codes"]) if row else ["UNKNOWN"]
    return {"window_days": days, "compared": total, "same": same, "different": total - same,
            "skipped_no_lane_or_provider": unknown, "agreement": round(same / total, 3) if total else None,
            "differences": diffs, "applies": False,
            "note": "Replay against today's configuration; nothing here changed any route."}


# Fallback safety: when may a failed call move on (to the same or the next provider)?
#   kind  "model"  - a chat completion with no side effects of its own
#         "action" - anything that changes the world (a tool call, a message, a payment, a file write)
#   stage "not_sent"        - the connection failed before the request left (refused, DNS, TLS)
#         "rejected"        - the provider answered an error before any output (HTTP 429/5xx with no body streamed)
#         "output_started"  - part of an answer was already passed on
#         "unknown"         - timed out or cut off with no way to know whether it was done
def fallback_allowed(kind: str, stage: str, idempotency_key: str | None = None) -> tuple[bool, str]:
    if stage == "output_started":
        return False, "part of the answer was already delivered; sending again could duplicate it"
    if kind == "model":
        if stage in ("not_sent", "rejected"):
            return True, "no output yet and a model call changes nothing by itself"
        return False, "outcome unknown; ask the owner rather than repeat"
    if kind == "action":
        if stage == "not_sent" and idempotency_key:
            return True, "the request never left, and the idempotency key stops a double run if it did"
        return False, "a consequential action is never repeated automatically; it is held for the owner"
    return False, "unknown kind of call: not repeated"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Recommend a provider/model for a task (changes nothing, calls nothing).")
    ap.add_argument("task", nargs="?", default="")
    ap.add_argument("--shadow", type=int, metavar="DAYS", help="compare past real calls (audit log, read-only) with what the selector would pick today")
    ap.add_argument("--lane", default="private", choices=("private", "public"))
    ap.add_argument("--purpose")
    ap.add_argument("--tools", action="store_true")
    ap.add_argument("--vision", action="store_true")
    ap.add_argument("--chars", type=int, help="size of the material the task will send, in characters")
    ap.add_argument("--prefer", help="one-off owner choice for this task")
    ap.add_argument("--allow-unverified", action="store_true")
    ap.add_argument("--evidence", action="store_true", help="read measured calls from the kernel audit log (read-only)")
    ap.add_argument("--providers", default=PROVIDERS)
    ap.add_argument("--catalog", default=CATALOG)
    a = ap.parse_args(argv)
    if a.shadow:
        from .traces import open_readonly
        conn = open_readonly()
        try:
            print(json.dumps(shadow(conn, gateway.load(a.providers), load_catalog(a.catalog), a.shadow, load_probes()), indent=1))
        finally:
            conn.close()
        return 0
    if not a.task:
        ap.error("a task is required unless --shadow is given")
    content = a.task + (" " + "x" * a.chars if a.chars else "")
    req = requirements(body={"messages": [{"role": "user", "content": content}]}, lane=a.lane, tools=a.tools,
                       vision=a.vision, purpose=a.purpose)
    measured = None
    if a.evidence:
        from .traces import open_readonly
        conn = open_readonly()
        try:
            measured = evidence(conn)
        finally:
            conn.close()
    out = select(gateway.load(a.providers), load_catalog(a.catalog), req, override=a.prefer, measured=measured,
                 allow_unverified=a.allow_unverified, probes=load_probes())
    print(json.dumps(out, indent=1))
    return 0 if out["recommended"] else 3


if __name__ == "__main__":
    raise SystemExit(main())
