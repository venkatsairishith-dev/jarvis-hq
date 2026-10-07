"""Model gateway: decides which provider a task may use. Lanes come from the kernel, never from a model.

Rules (all deterministic):
- Provider lanes: local, private, public. A private task may use local or private providers only; a public task
  may use local or public providers only. Fallbacks never cross lanes.
- A provider marked paid, or missing a lane, is invalid and never used. Default budget for every provider is $0.
- A provider with no key file present is skipped (unavailable), not an error.
- Prompts pass the secret scrubber first. Private keys block the call; tokens are redacted.
"""
import json
import os
import urllib.request
from dataclasses import dataclass

from .scrub import scrub

LANES = {"local", "private", "public"}


class RouteError(Exception):
    pass


@dataclass
class Provider:
    name: str
    lane: str
    base_url: str | None
    key_file: str | None
    models: list
    paid: bool = False
    enabled: bool = True

    def problems(self):
        out = []
        if self.lane not in LANES:
            out.append("unknown lane")
        if self.paid:
            out.append("paid provider")
        return out

    def available(self) -> bool:
        if not self.enabled or self.problems() or not self.base_url:
            return False
        return self.key_file is None or os.path.isfile(self.key_file)


def allowed(task_lane: str, provider_lane: str) -> bool:
    if task_lane not in ("private", "public"):
        return False
    return provider_lane == "local" or provider_lane == task_lane


def load(path: str):
    cfg = json.load(open(path, encoding="utf-8"))
    return [Provider(**p) for p in cfg["providers"]]


def plan(providers, task_lane: str, purpose: str | None = None, prefer_local: bool = True):
    """Ordered fallback chain for a task. Raises RouteError if nothing may serve it (never widens the lane)."""
    ok = [p for p in providers if p.available() and allowed(task_lane, p.lane)]
    if purpose:
        ok = [p for p in ok if purpose in p.models or "any" in p.models]
    ok.sort(key=lambda p: (p.lane != "local") if prefer_local else (p.lane == "local"))
    if not ok:
        raise RouteError(f"no available provider for a {task_lane} task")
    return ok


def call(kernel, providers, task_lane: str, prompt: str, purpose: str | None = None, model: str = "default", timeout=60, max_tokens=256):
    """Run a prompt through the first provider in the chain that works. Audited. Keys are read at call time only.
    max_tokens is a hard output budget for the call."""
    clean, findings, blocked = scrub(prompt)
    if blocked:
        kernel.audit.append("model_call_blocked", {"lane": task_lane, "findings": findings})
        raise RouteError("blocked: prompt contains a private key")
    chain = plan(providers, task_lane, purpose)
    last = None
    for p in chain:
        headers = {"Content-Type": "application/json"}
        if p.key_file:
            headers["Authorization"] = "Bearer " + open(p.key_file).read().strip()
        body = json.dumps({"model": model, "max_tokens": int(max_tokens), "messages": [{"role": "user", "content": clean}]}).encode()
        try:
            req = urllib.request.Request(p.base_url.rstrip("/") + "/chat/completions", data=body, headers=headers, method="POST")
            with urllib.request.urlopen(req, timeout=timeout) as r:
                out = json.loads(r.read())["choices"][0]["message"]["content"]
            kernel.audit.append("model_call", {"provider": p.name, "lane": task_lane, "scrubbed": findings, "chars_in": len(clean), "chars_out": len(out)})
            return out
        except Exception as e:  # try the next provider in the SAME lane
            last = type(e).__name__
            kernel.audit.append("model_call_failed", {"provider": p.name, "lane": task_lane, "error": last})
    raise RouteError(f"all providers failed ({last})")
