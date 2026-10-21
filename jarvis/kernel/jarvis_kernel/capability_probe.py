"""Measure whether a LOCAL model really makes tool calls, and record the result for model_select.py.

    python -m jarvis_kernel.capability_probe --provider vm-llama-cpu [--runs 3] [--write]

Why: models.dev says nothing about our local llama.cpp servers, so the selector treats their tool support as
"unverified". This replaces the guess with a measurement: send a small request that offers one tool and check that the
reply is a well-formed call to that tool with valid JSON arguments.

Safety:
- Only providers in the local lane whose base_url is a loopback address are probed. Cloud providers are refused: no
  request leaves this machine, no key is read, nothing can cost money.
- The probe prompt is fixed, contains no user data, and the tool is a fake `get_weather` that is never executed.
- Without --write nothing is saved. With --write, one JSON file (jarvis/config/measured_capabilities.json) is replaced
  atomically; it records pass/fail counts, the model name the server reported, and the date. It changes no routing by
  itself: the selector only reads it when asked for a recommendation.
- It runs a model on this machine: on the shared server use nice and the usual CPU cap. Run it on the laptop for the
  laptop's models.
"""
from __future__ import annotations

import argparse
import datetime
import ipaddress
import json
import os
import urllib.parse
import urllib.request

from . import gateway
from .model_select import PROBES, PROVIDERS

TOOL = {"type": "function", "function": {
    "name": "get_weather", "description": "Current weather for a city.",
    "parameters": {"type": "object", "properties": {"city": {"type": "string"}}, "required": ["city"]}}}
PROMPT = "What is the weather in Paris right now? Use the tool."


def is_loopback(url: str) -> bool:
    host = urllib.parse.urlparse(url).hostname or ""
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def check_reply(reply: dict) -> bool:
    """True only for a well-formed call to the offered tool with a non-empty string city."""
    try:
        calls = reply["choices"][0]["message"].get("tool_calls") or []
        if len(calls) < 1:
            return False
        fn = calls[0]["function"]
        args = json.loads(fn["arguments"]) if isinstance(fn["arguments"], str) else fn["arguments"]
        return fn["name"] == "get_weather" and isinstance(args, dict) and isinstance(args.get("city"), str) and bool(args["city"].strip())
    except (KeyError, IndexError, TypeError, ValueError, AttributeError):
        return False


def probe_once(p: gateway.Provider, timeout: float = 120.0, model: str | None = None) -> tuple[bool, str | None]:
    body = {"model": model or (p.default_model if p.default_model != "default" else "any"), "temperature": 0, "max_tokens": 200,
            "messages": [{"role": "user", "content": PROMPT}], "tools": [TOOL]}
    req = urllib.request.Request(p.base_url.rstrip("/") + "/chat/completions", data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json", **p.auth_headers()})
    with urllib.request.urlopen(req, timeout=timeout) as r:                    # loopback only (checked by the caller)
        reply = json.loads(r.read(1 << 20))
    return check_reply(reply), reply.get("model") if isinstance(reply.get("model"), str) else None


def error_kind(e: Exception) -> str:
    """What went wrong, without any server text: HTTP status or exception class only."""
    code = getattr(e, "code", None)
    return f"HTTP {code}" if isinstance(code, int) else type(e).__name__


def run(p: gateway.Provider, runs: int = 3, timeout: float = 120.0, model: str | None = None) -> dict:
    if p.lane != "local" or not p.base_url or not is_loopback(p.base_url):
        raise gateway.RouteError(f"{p.name}: only local, loopback providers are probed (nothing leaves this machine)")
    if p.paid:
        raise gateway.RouteError(f"{p.name}: paid provider")
    passed, served, errors, kinds = 0, None, 0, []
    for _ in range(runs):
        try:
            ok, served_name = probe_once(p, timeout, model)
        except Exception as e:                                                  # server down / timeout / unknown model: a failed probe, not a crash
            errors += 1
            kinds.append(error_kind(e))
            continue
        passed += ok
        served = served_name or served
    return {"tools": {"ok": passed == runs and runs > 0, "passed": passed, "runs": runs, "errors": errors,
                      "model": model or p.default_model, "served_model": served, "error_kinds": sorted(set(kinds)),
                      "when": datetime.date.today().isoformat(), "method": "fixed get_weather prompt, temperature 0"}}


def save(provider: str, result: dict, path: str = PROBES) -> None:
    try:
        with open(path, encoding="utf-8") as f:
            doc = json.load(f)
    except (FileNotFoundError, ValueError):
        doc = {}
    doc.setdefault("_note", "Measured by jarvis_kernel/capability_probe.py. Read by model_select.py; changes no route by itself.")
    doc.setdefault("providers", {})[provider] = result
    tmp = path + ".part"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(doc, f, indent=1, sort_keys=True)
        f.write("\n")
    os.replace(tmp, path)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--provider", required=True)
    ap.add_argument("--runs", type=int, default=3)
    ap.add_argument("--model", help="the model or llama-swap alias to probe (e.g. decider, general, code); a local server needs a real name")
    ap.add_argument("--timeout", type=float, default=120.0)
    ap.add_argument("--write", action="store_true", help="save the result to measured_capabilities.json")
    ap.add_argument("--providers", default=PROVIDERS)
    a = ap.parse_args(argv)
    by = {p.name: p for p in gateway.load(a.providers)}
    if a.provider not in by:
        ap.error("unknown provider")
    try:
        res = run(by[a.provider], a.runs, a.timeout, a.model)
    except gateway.RouteError as e:
        print(f"refused: {e}")
        return 2
    print(json.dumps({a.provider: res}, indent=1))
    if a.write:
        save(a.provider, res)
    return 0 if res["tools"]["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
