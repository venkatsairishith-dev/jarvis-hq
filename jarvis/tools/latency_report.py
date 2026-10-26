"""Print p50 / p90 per phase of the chat path, from the content-free timing log. Windows, macOS or Linux; standard library only.

    python jarvis/tools/latency_report.py                       the log in the data folder (JARVIS_LATENCY_LOG or <data>/logs/latency.jsonl)
    python jarvis/tools/latency_report.py --since 2h --health   only the last two hours, and ask the gateway's /health too
    python jarvis/tools/latency_report.py --save before.json    keep the numbers; later:
    python jarvis/tools/latency_report.py --compare before.json after.json

Where the lines come from (D60), all of them content-free: phase words, milliseconds, counts, a provider's and a tool's name.
  * the privacy gateway writes one line per model request:  src "gateway": redact_ms, queue_ms, first_ms, total_ms, req_bytes, msgs, tools,
    after ("user" = the request after the owner's words, "tool" = after a tool result), cache_hits, provider.
  * the face writes the marks of a chat turn:  src "face": phase sent / ack / quick_start / quick_end / model_first / first_token / tool_start /
    tool_end / final_token / done / error, ms since the message, turn, round, tool.
  * /health of the gateway keeps the median and p90 of its last 50 calls per provider (latency_ms, latency_p90_ms), and the redaction cache counts.

What the numbers answer (the owner's targets, 2026-10-26): simple goals p50 under 15 s and p90 under 30 s end to end ("done"), a chat reply's first
token under 2 s ("first_token" of a turn without tools), and the instant "On it" ("ack") within a tenth of a second.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import urllib.request
from collections import defaultdict
from pathlib import Path

# the phases in the order a turn goes through them; the report prints them in this order
FACE_PHASES = ("ack", "quick_end", "model_first", "first_token", "tool_start", "tool_end", "final_token", "done")
GATEWAY_FIELDS = ("redact_ms", "queue_ms", "first_ms", "total_ms")
TARGETS = {"done": (15_000, 30_000), "first_token": (2_000, None), "ack": (100, None)}     # (p50 limit, p90 limit) in ms


def default_log() -> Path:
    env = os.environ.get("JARVIS_LATENCY_LOG", "").strip()
    if env and env.lower() not in ("off", "0", "no", "false"):
        return Path(env)
    base = os.environ.get("JARVIS_DATA_DIR", "").strip() or (r"D:\jarvis\data" if os.name == "nt" else str(Path.home() / ".jarvis" / "data"))
    return Path(base) / "logs" / "latency.jsonl"


def parse_since(text: str | None) -> float:
    """'30m', '2h', '1d' or plain seconds -> seconds; 0 = everything."""
    if not text:
        return 0.0
    m = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*([smhd]?)\s*", text)
    if not m:
        raise SystemExit(f"--since: {text!r} is not like 30m, 2h, 1d or 90")
    return float(m.group(1)) * {"": 1, "s": 1, "m": 60, "h": 3600, "d": 86400}[m.group(2)]


def read_lines(path: Path, since_s: float = 0.0, now: float | None = None) -> list[dict]:
    """The log's lines (and the rotated `.1` file before it), oldest first. A broken line is skipped."""
    now = time.time() if now is None else now
    rows: list[dict] = []
    for p in (Path(str(path) + ".1"), path):
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for line in text.splitlines():
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if isinstance(row, dict) and isinstance(row.get("t"), (int, float)) and (not since_s or now - row["t"] <= since_s):
                rows.append(row)
    rows.sort(key=lambda r: r["t"])
    return rows


def percentile(values: list[float], q: float) -> float:
    """By rank, as the gateway's /health does it."""
    ordered = sorted(values)
    if not ordered:
        return 0.0
    return float(ordered[min(len(ordered) - 1, max(0, int(round(q * (len(ordered) - 1)))))])


def summarise(values: list[float]) -> dict:
    return {"n": len(values), "p50": round(percentile(values, 0.5)), "p90": round(percentile(values, 0.9)), "max": round(max(values)) if values else 0}


def turns_of(rows: list[dict]) -> list[dict]:
    """Face marks grouped into turns. The turn number restarts with the app, so a turn is: a `sent` mark, then every later mark with the same number
    up to its `done` or `error` (or the next `sent` of that number)."""
    open_: dict[int, dict] = {}
    done: list[dict] = []
    for r in rows:
        if r.get("src") != "face" or not isinstance(r.get("phase"), str):
            continue
        n = r.get("turn")
        if r["phase"] == "sent":
            if n in open_:
                done.append(open_.pop(n))                                   # an earlier turn with this number never ended: keep what it has
            open_[n] = {"turn": n, "t": r["t"], "marks": []}
            continue
        turn = open_.get(n)
        if turn is None:
            continue
        turn["marks"].append(r)
        if r["phase"] in ("done", "error"):
            turn["outcome"] = r["phase"]
            done.append(open_.pop(n))
    done.extend(open_.values())
    return done


def face_report(rows: list[dict]) -> dict:
    turns = turns_of(rows)
    phases: dict[str, list[float]] = defaultdict(list)
    kinds: dict[str, list[float]] = defaultdict(list)
    tools: dict[str, list[float]] = defaultdict(list)
    rounds: list[float] = []
    decide: list[float] = []
    for t in turns:
        marks = t["marks"]
        if not marks:
            continue
        first: dict[str, float] = {}
        for m in marks:
            first.setdefault(m["phase"], m["ms"])
        for phase in FACE_PHASES:
            if phase in first:
                phases[phase].append(first[phase])
        # the first token of a turn that used no tool, and of one that did, are different promises
        used_tool = "tool_start" in first
        if "first_token" in first:
            kinds["first_token_no_tool" if not used_tool else "first_token_after_tools"].append(first["first_token"])
        if "done" in first:
            kinds["done_quick" if "quick_end" in first and not used_tool and "model_first" not in first else
                  "done_with_tools" if used_tool else "done_chat_only"].append(first["done"])
        rounds.append(max([m.get("round") or 1 for m in marks if m["phase"] in ("model_first", "tool_start", "tool_end")] or [1]))
        if "model_first" in first and "tool_start" in first:
            decide.append(first["tool_start"] - first["model_first"])
        starts: dict[str, list[float]] = defaultdict(list)
        for m in marks:
            if m["phase"] == "tool_start":
                starts[m.get("tool") or "?"].append(m["ms"])
            elif m["phase"] == "tool_end" and starts.get(m.get("tool") or "?"):
                tools[m.get("tool") or "?"].append(m["ms"] - starts[m.get("tool") or "?"].pop(0))
    return {"turns": len(turns), "phases": {k: summarise(v) for k, v in phases.items()},
            "kinds": {k: summarise(v) for k, v in kinds.items()},
            "model_rounds": summarise(rounds), "tool_decision_ms": summarise(decide),
            "tool_run_ms": {k: summarise(v) for k, v in sorted(tools.items())}}


def gateway_report(rows: list[dict]) -> dict:
    by: dict[str, dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))
    for r in rows:
        if r.get("src") != "gateway":
            continue
        kind = "plan call" if r.get("route") == "online" else f"after {r.get('after') or '?'}"
        names = [r.get("provider") or "?", f"{r.get('provider') or '?'} / {kind}"]
        for name in names:
            for field in (*GATEWAY_FIELDS, "req_bytes", "cache_hits", "cache_misses"):
                if isinstance(r.get(field), (int, float)):
                    by[name][field].append(r[field])
    return {name: {field: summarise(v) for field, v in fields.items()} for name, fields in sorted(by.items())}


def read_health(url: str, timeout: float = 3.0) -> dict | None:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:                  # the gateway's own /health on this PC
            data = json.loads(r.read().decode("utf-8", "replace"))
    except (OSError, ValueError):
        return None
    keep = ("latency_ms", "latency_p90_ms", "latency_calls", "redaction_cache", "answered_by", "ready", "degraded")
    return {k: data.get(k) for k in keep if k in data}


def build_report(rows: list[dict], health: dict | None = None) -> dict:
    return {"lines": len(rows), "face": face_report(rows), "gateway": gateway_report(rows), "health": health}


def _fmt(ms: float) -> str:
    return f"{ms / 1000:.1f} s" if ms >= 1000 else f"{ms:.0f} ms"


def _row(label: str, s: dict, width: int = 26) -> str:
    return f"  {label:<{width}} n={s['n']:<4} p50 {_fmt(s['p50']):>8}   p90 {_fmt(s['p90']):>8}   max {_fmt(s['max']):>8}"


def render(report: dict) -> str:
    out = [f"{report['lines']} timing lines; {report['face']['turns']} chat turns"]
    face = report["face"]
    if face["turns"]:
        out += ["", "Chat turns (face marks, ms since the message was sent)"]
        for phase in FACE_PHASES:
            if phase in face["phases"]:
                s = face["phases"][phase]
                mark = ""
                lim = TARGETS.get(phase)
                if lim and s["n"]:
                    mark = "   target p50 " + _fmt(lim[0]) + (f", p90 {_fmt(lim[1])}" if lim[1] else "") + ("  OK" if s["p50"] <= lim[0] and (not lim[1] or s["p90"] <= lim[1]) else "  OVER")
                out.append(_row(phase, s) + mark)
        for kind, s in face["kinds"].items():
            out.append(_row(kind, s))
        out.append(_row("model rounds per turn", face["model_rounds"]).replace(" ms", "").replace(" s", ""))
        if face["tool_decision_ms"]["n"]:
            out.append(_row("model decides the tool", face["tool_decision_ms"]))
        for tool, s in face["tool_run_ms"].items():
            out.append(_row(f"tool {tool}", s))
    gw = report["gateway"]
    if gw:
        out += ["", "Model requests (gateway lines)"]
        for name, fields in gw.items():
            out.append(f" {name}")
            for field in (*GATEWAY_FIELDS, "req_bytes"):
                if field in fields:
                    s = fields[field]
                    out.append(_row(field, s) if field != "req_bytes" else f"  {field:<26} n={s['n']:<4} p50 {s['p50']:>8} B  p90 {s['p90']:>8} B")
            if "cache_hits" in fields:
                out.append(f"  redaction cache hits/request p50 {fields['cache_hits']['p50']}, misses p50 {fields.get('cache_misses', {}).get('p50', 0)}")
    h = report.get("health")
    if h:
        out += ["", "Gateway /health"]
        for name, row in (h.get("latency_ms") or {}).items():
            p90 = (h.get("latency_p90_ms") or {}).get(name, {})
            out.append(f"  {name}: p50 " + ", ".join(f"{k} {v}" for k, v in row.items()) + " | p90 " + ", ".join(f"{k} {v}" for k, v in p90.items()))
        if h.get("redaction_cache"):
            out.append(f"  redaction cache: {h['redaction_cache']}")
    elif h is None and "health" in report and report["health"] is None:
        pass
    if not face["turns"] and not gw:
        out += ["", "No timing lines yet. Use the face for a few goals first (the log is written while the gateway and the face run)."]
    return "\n".join(out)


def compare(before: dict, after: dict) -> str:
    out = ["phase / field                          before p50 -> after p50      before p90 -> after p90"]

    def line(label: str, a: dict | None, b: dict | None):
        if a and b and a.get("n") and b.get("n"):
            out.append(f"  {label:<36} {_fmt(a['p50']):>8} -> {_fmt(b['p50']):<8} ({b['p50'] - a['p50']:+.0f} ms)   {_fmt(a['p90']):>8} -> {_fmt(b['p90']):<8}")
    for phase in FACE_PHASES:
        line(phase, before["face"]["phases"].get(phase), after["face"]["phases"].get(phase))
    for kind in sorted(set(before["face"]["kinds"]) | set(after["face"]["kinds"])):
        line(kind, before["face"]["kinds"].get(kind), after["face"]["kinds"].get(kind))
    a, b = before["face"]["model_rounds"], after["face"]["model_rounds"]
    if a["n"] and b["n"]:
        out.append(f"  {'model rounds per turn (p50)':<36} {a['p50']} -> {b['p50']}")
    for name in sorted(set(before["gateway"]) & set(after["gateway"])):
        for field in (*GATEWAY_FIELDS, "req_bytes"):
            line(f"{name}: {field}", before["gateway"][name].get(field), after["gateway"][name].get(field))
    return "\n".join(out)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--log", type=Path, default=None, help="the timing log (default: JARVIS_LATENCY_LOG or <data dir>/logs/latency.jsonl)")
    ap.add_argument("--since", default=None, help="only lines newer than this: 30m, 2h, 1d or seconds")
    ap.add_argument("--health", nargs="?", const="http://127.0.0.1:48950/health", default=None, help="also read the gateway's /health (default URL given when the flag has no value)")
    ap.add_argument("--json", action="store_true", help="print the report as JSON")
    ap.add_argument("--save", type=Path, default=None, help="write the report as JSON to this file (for --compare later)")
    ap.add_argument("--compare", nargs=2, type=Path, metavar=("BEFORE", "AFTER"), help="print two saved reports side by side")
    args = ap.parse_args(argv)
    if args.compare:
        before, after = (json.loads(p.read_text(encoding="utf-8")) for p in args.compare)
        print(compare(before, after))
        return 0
    rows = read_lines(args.log or default_log(), parse_since(args.since))
    report = build_report(rows, read_health(args.health) if args.health else None)
    if args.save:
        args.save.write_text(json.dumps(report, indent=1), encoding="utf-8")
    print(json.dumps(report, indent=1) if args.json else render(report))
    return 0


if __name__ == "__main__":
    sys.exit(main())
