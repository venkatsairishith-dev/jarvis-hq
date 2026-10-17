"""Model gateway: decides which provider a task may use. Lanes come from the kernel, never from a model.

Rules (all deterministic):
- Provider lanes: local, private, public. A private task may use local or private providers only; a public task
  may use local or public providers only. Fallbacks never cross lanes.
- A provider marked paid, or missing a lane, is invalid and never used. Default budget for every provider is $0.
- A provider with no key file present is skipped (unavailable), not an error. Exception: key_optional=true (a local
  server that may or may not be started with --api-key-file): it is called without a key until the file exists.
- A key is read from its file at call time and only ever goes into the Authorization header: never into the audit log,
  an error message or a return value.
- Prompts pass the secret scrubber first. Private keys block the call; tokens are redacted.
"""
import json
import os
import re
import time
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
    max_input_tokens: int | None = None   # measured input limit; larger requests skip this provider (sizing.py)
    max_total_tokens: int | None = None   # measured limit on input + reserved output (Groq TPM, llama.cpp n_ctx)
    key_optional: bool = False            # the key file may be absent: then requests go without a key (local llama-server)

    def key_path(self):
        if not self.key_file:
            return None
        return self.key_file if os.path.isabs(self.key_file) else os.path.join(secrets_dir(), *self.key_file.replace("\\", "/").split("/"))

    def auth_headers(self) -> dict:
        """{"Authorization": "Bearer <key>"} from the key file, or {} when there is no key file (or an optional one is absent).
        The file may hold comment lines starting with '#', as llama-server's --api-key-file allows; the first other
        non-empty line is the key. Raises OSError/ValueError for an unreadable or empty required key; the message never
        contains the key."""
        kp = self.key_path()
        if kp is None or (self.key_optional and not os.path.isfile(kp)):
            return {}
        with open(kp, encoding="utf-8-sig") as fh:
            keys = [ln.strip() for ln in fh.read().splitlines() if ln.strip() and not ln.strip().startswith("#")]
        if not keys:
            raise ValueError("key file is empty")
        return {"Authorization": "Bearer " + keys[0]}

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
        return kp is None or self.key_optional or os.path.isfile(kp)


def allowed(task_lane: str, provider_lane: str) -> bool:
    if task_lane not in ("private", "public"):
        return False
    return provider_lane == "local" or provider_lane == task_lane


def load(path: str):
    cfg = json.load(open(path, encoding="utf-8-sig"))  # tolerate a BOM (Windows editors add one)
    return [Provider(**{k: v for k, v in p.items() if not k.startswith("_")}) for p in cfg["providers"]]   # "_note" etc. are comments


def plan(providers, task_lane: str, purpose: str | None = None, prefer_local: bool = True):
    """Ordered fallback chain for a task. Raises RouteError if nothing may serve it (never widens the lane)."""
    ok = [p for p in providers if p.available() and allowed(task_lane, p.lane)]
    if purpose:
        ok = [p for p in ok if purpose in p.models or "any" in p.models]
    ok.sort(key=lambda p: (p.priority, (p.lane != "local") if prefer_local else (p.lane == "local")))   # stable: config order breaks ties
    if not ok:
        raise RouteError(f"no available provider for a {task_lane} task")
    return ok


def explain(providers, task_lane: str, purpose: str | None = None, est_tokens: int | None = None, rest: dict | None = None,
            preferred: str | None = None) -> dict:
    """Why each provider is, or is not, in the chain for a request: the same rules as plan(), with a reason code per
    provider (and the order of the eligible ones). `rest` maps a provider name to seconds it is resting after errors.
    Nothing here is a guess: a code is only reported when its rule actually fired."""
    rest = rest or {}
    cands, eligible = [], []
    for p in providers:
        codes = []
        if not p.enabled:
            codes.append("PROVIDER_DISABLED")
        if p.paid:
            codes.append("PAID_REFUSED")
        if p.lane not in LANES:
            codes.append("UNKNOWN_LANE")
        if not p.base_url:
            codes.append("NO_ENDPOINT")
        if p.os_only and p.os_only != os.name:
            codes.append("OTHER_MACHINE_ONLY")
        kp = p.key_path()
        if kp is not None and not p.key_optional and not os.path.isfile(kp):
            codes.append("KEY_MISSING")
        if not allowed(task_lane, p.lane):
            codes.append("LANE_MISMATCH")
        if purpose and purpose not in p.models and "any" not in p.models:
            codes.append("PURPOSE_MISMATCH")
        limit = p.max_input_tokens or p.max_total_tokens
        if est_tokens and limit and est_tokens > limit:
            codes.append("CONTEXT_TOO_SMALL")
        if rest.get(p.name, 0) > 0:
            codes.append("RESTING_AFTER_ERRORS")
        row = {"provider": p.name, "lane": p.lane, "model": p.default_model, "priority": p.priority, "context_limit": limit,
               "verdict": "excluded" if codes else "eligible", "codes": codes}
        cands.append(row)
        if not codes:
            eligible.append((p, row))
    eligible.sort(key=lambda t: (t[0].name != preferred, t[0].priority, t[0].lane != "local"))
    for rank, (p, row) in enumerate(eligible, 1):
        row["rank"] = rank
        row["codes"] = (["OWNER_PREFERRED"] if p.name == preferred else []) + ["LOCAL_FIRST" if p.lane == "local" else f"PRIORITY_{p.priority}"]
    return {"lane": task_lane, "purpose": purpose, "est_tokens": est_tokens, "chain": [p.name for p, _ in eligible],
            "chosen": eligible[0][0].name if eligible else None, "candidates": cands,
            "policy": {"never_crosses_lanes": True, "paid_providers_refused": True, "order": "owner preference, then priority, then local first"},
            "note": None if eligible else "no provider can serve this request; the kernel would answer with an error, not widen the lane"}


PROBE_HEADERS = ("retry-after", "x-ratelimit-limit-requests", "x-ratelimit-remaining-requests", "x-ratelimit-limit-tokens",
                 "x-ratelimit-remaining-tokens", "x-ratelimit-reset-requests", "x-ratelimit-reset-tokens", "x-request-id")


def probe(p: Provider, chat: bool = False, timeout: float = 20.0) -> dict:
    """What a provider actually is and does, measured with the real key (read here, inside the kernel, never returned):
    its model list, whether the configured model is in it, rate-limit headers it sends, and (chat=True) one tiny real
    request with the token counts it reports. Fixed prompt: no private data ever goes in. Errors are reported by class."""
    out = {"provider": p.name, "lane": p.lane, "base_url_host": re.sub(r"^https?://([^/]+).*$", r"\1", p.base_url or ""),
           "configured_model": p.default_model, "ok": False}
    if not p.available():
        out["why"] = "not available here (disabled, no endpoint, key file missing, or another machine's provider)"
        return out
    headers = {"Content-Type": "application/json", "User-Agent": "jarvis-probe/0.1"}
    try:
        headers.update(p.auth_headers())
    except (OSError, ValueError):
        out["why"] = "key file unreadable"
        return out
    base = p.base_url.rstrip("/")
    t0 = time.time()
    try:
        with urllib.request.urlopen(urllib.request.Request(base + "/models", headers=headers), timeout=timeout) as r:
            out["status"] = r.status
            out["headers"] = {h: r.headers.get(h) for h in PROBE_HEADERS if r.headers.get(h)}
            data = json.loads(r.read() or b"{}")
        ids = [m.get("id") for m in (data.get("data") or []) if isinstance(m, dict)]
        out.update(models_count=len(ids), model_listed=p.default_model in ids, sample_models=[str(i)[:60] for i in ids[:8]], ok=True)
    except Exception as e:
        out["models_error"] = type(e).__name__ + (f" {getattr(e, 'code', '')}" if hasattr(e, "code") else "")
    out["ms"] = round((time.time() - t0) * 1000)
    if chat:
        body = {"model": p.default_model, "max_tokens": 8, "temperature": 0,
                "messages": [{"role": "user", "content": "Reply with the single word: ok"}]}
        body.update(p.extra_body)
        t1 = time.time()
        try:
            req = urllib.request.Request(base + "/chat/completions", data=json.dumps(body).encode(), headers=headers, method="POST")
            with urllib.request.urlopen(req, timeout=max(timeout, p.timeout_s or 0)) as r:
                rep = json.loads(r.read())
                hdrs = {h: r.headers.get(h) for h in PROBE_HEADERS if r.headers.get(h)}
            u = rep.get("usage") or {}
            out["chat"] = {"ok": True, "ms": round((time.time() - t1) * 1000), "model": str(rep.get("model", ""))[:80],
                           "finish_reason": ((rep.get("choices") or [{}])[0]).get("finish_reason"),
                           "usage": {k: u[k] for k in ("prompt_tokens", "completion_tokens", "total_tokens") if isinstance(u.get(k), (int, float))},
                           "headers": hdrs}
            out["usage"] = out["chat"]["usage"]
            out["ok"] = True
        except Exception as e:
            out["chat"] = {"ok": False, "error": type(e).__name__ + (f" {getattr(e, 'code', '')}" if hasattr(e, "code") else ""),
                           "ms": round((time.time() - t1) * 1000)}
            out["ok"] = False
    return out


def call(kernel, providers, task_lane: str, prompt: str, purpose: str | None = None, model: str = "default", timeout=60, max_tokens=256):
    """Run a prompt through the first provider in the chain that works. Audited. Keys are read at call time only.
    max_tokens is a hard output budget for the call."""
    clean, findings, blocked = scrub(prompt, deep=True)
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
        try:                                      # withdrawn or unreadable key: this provider fails, try the next one
            headers.update(p.auth_headers())
        except (OSError, ValueError) as e:
            last = f"key unreadable ({type(e).__name__})"
            kernel.audit.append("model_call_failed", {"provider": p.name, "lane": task_lane, "error": last})
            continue
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
