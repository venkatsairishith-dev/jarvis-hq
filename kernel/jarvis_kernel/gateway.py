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
import re
import urllib.request
from dataclasses import dataclass, field

from .scrub import scrub

LANES = {"local", "private", "public"}


class RouteError(Exception):
    pass


def secrets_dir() -> str:
    """Where this machine keeps secret files: $JARVIS_SECRETS, else D:\\jarvis-secrets (Windows) or ~/.jarvis/secrets."""
    return os.environ.get("JARVIS_SECRETS") or (r"D:\jarvis-secrets" if os.name == "nt" else os.path.expanduser("~/.jarvis/secrets"))


@dataclass
class Provider:
    name: str
    lane: str
    base_url: str | None
    key_file: str | None
    models: list
    paid: bool = False
    enabled: bool = True
    default_model: str = "default"
    model_pattern: str | None = None      # a model name must match this (e.g. OpenRouter ':free$'), else the call is refused
    extra_body: dict = field(default_factory=dict)  # e.g. {"provider": {"data_collection": "deny"}}
    os_only: str | None = None            # "nt" or "posix": a provider that exists on one machine type only (e.g. the laptop's local model)
    priority: int = 50                    # lower is tried first, within the lane rules (a slow local fallback gets a high number)
    timeout_s: int | None = None          # per-provider request timeout (a slow CPU fallback needs longer than a cloud API)

    def key_path(self):
        if not self.key_file:
            return None
        return self.key_file if os.path.isabs(self.key_file) else os.path.join(secrets_dir(), *self.key_file.replace("\\", "/").split("/"))

    def problems(self):
        out = []
        if self.lane not in LANES:
            out.append("unknown lane")
        if self.paid:
            out.append("paid provider")
        return out

    def model_ok(self, model: str) -> bool:
        return self.model_pattern is None or re.search(self.model_pattern, model) is not None

    def available(self) -> bool:
        if not self.enabled or self.problems() or not self.base_url:
            return False
        if self.os_only and self.os_only != os.name:
            return False
        kp = self.key_path()
        return kp is None or os.path.isfile(kp)


def allowed(task_lane: str, provider_lane: str) -> bool:
    if task_lane not in ("private", "public"):
        return False
    return provider_lane == "local" or provider_lane == task_lane


def load(path: str):
    cfg = json.load(open(path, encoding="utf-8-sig"))  # tolerate a BOM (Windows editors add one)
    return [Provider(**p) for p in cfg["providers"]]


def plan(providers, task_lane: str, purpose: str | None = None, prefer_local: bool = True):
    """Ordered fallback chain for a task. Raises RouteError if nothing may serve it (never widens the lane)."""
    ok = [p for p in providers if p.available() and allowed(task_lane, p.lane)]
    if purpose:
        ok = [p for p in ok if purpose in p.models or "any" in p.models]
    ok.sort(key=lambda p: (p.priority, (p.lane != "local") if prefer_local else (p.lane == "local")))   # stable: config order breaks ties
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
        use_model = p.default_model if model == "default" else model
        if not p.model_ok(use_model):  # e.g. a paid OpenRouter model: refuse before any request is made
            kernel.audit.append("model_refused", {"provider": p.name, "model": use_model[:80], "why": "model_pattern"})
            last = "ModelRefused"
            continue
        headers = {"Content-Type": "application/json", "User-Agent": "jarvis-gateway/0.1"}
        if p.key_file:
            headers["Authorization"] = "Bearer " + open(p.key_path(), encoding="utf-8-sig").read().strip()
        payload = {"model": use_model, "max_tokens": int(max_tokens), "messages": [{"role": "user", "content": clean}]}
        payload.update(p.extra_body)
        body = json.dumps(payload).encode()
        try:
            req = urllib.request.Request(p.base_url.rstrip("/") + "/chat/completions", data=body, headers=headers, method="POST")
            with urllib.request.urlopen(req, timeout=p.timeout_s or timeout) as r:
                out = json.loads(r.read())["choices"][0]["message"].get("content") or ""
            if not out.strip():
                raise ValueError("empty reply")  # reasoning models can spend the whole budget thinking: try the next provider
            kernel.audit.append("model_call", {"provider": p.name, "lane": task_lane, "scrubbed": findings, "chars_in": len(clean), "chars_out": len(out)})
            return out
        except Exception as e:  # try the next provider in the SAME lane
            last = type(e).__name__
            kernel.audit.append("model_call_failed", {"provider": p.name, "lane": task_lane, "error": last})
    raise RouteError(f"all providers failed ({last})")
