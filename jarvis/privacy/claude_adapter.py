"""Claude as a planner provider behind the privacy gateway (owner exception, expires 2026-10-31).

The gateway speaks the OpenAI chat format; Claude is reached through the official Anthropic SDK's Messages API (not a compatibility
shim). Redaction has already run on the body before anything here sees it, and placeholders in the answer are restored by the gateway
afterwards, exactly as for the other providers.

Scope on purpose: plain chat calls (text in, text out). A request that streams, uses tools or functions, sends images or non-text parts,
asks for more than one choice, or sets a response format is NOT sent to Claude: `unsupported()` says why and the gateway moves on to the
next provider (Dahl). Sampling parameters (temperature, top_p, ...) are dropped because current Claude models reject non-default values.
"""
from __future__ import annotations

import datetime as dt
import time
from typing import Any

DEFAULT_TOKEN_CAP = 4096
UNSUPPORTED_KEYS = ("tools", "functions", "tool_choice", "function_call", "response_format", "logit_bias", "audio", "modalities")
DROPPED_KEYS = ("temperature", "top_p", "top_k", "presence_penalty", "frequency_penalty", "seed", "stop", "user", "metadata", "logprobs",
                "top_logprobs", "max_completion_tokens", "max_tokens", "stream_options", "reasoning_effort", "store")


class ClaudeUnavailable(Exception):
    """The provider cannot serve this request; the gateway falls through to the next provider. The message never holds a body or key."""


def today_utc() -> dt.date:
    return dt.datetime.now(dt.timezone.utc).date()


def expired(prov: dict, today: dt.date | None = None) -> bool:
    """A provider with an "expires" date (YYYY-MM-DD) is refused after that date; an unreadable date counts as expired."""
    raw = prov.get("expires")
    if not raw:
        return False
    try:
        return (today or today_utc()) > dt.date.fromisoformat(str(raw))
    except ValueError:
        return True


def unsupported(body: dict) -> str | None:
    """Why this request must not go to Claude, or None."""
    if body.get("stream"):
        return "streaming request"
    for k in UNSUPPORTED_KEYS:
        if body.get(k):
            return f"{k} not supported"
    if int(body.get("n") or 1) != 1:
        return "n > 1"
    for msg in body.get("messages") or []:
        if msg.get("role") not in ("system", "developer", "user", "assistant"):
            return f"role {msg.get('role')!r} not supported"
        c = msg.get("content")
        if isinstance(c, list):
            if any(not (isinstance(p, dict) and p.get("type") == "text") for p in c):
                return "non-text content"
        elif c is not None and not isinstance(c, str):
            return "odd content"
    return None


def _text(content: Any) -> str:
    if isinstance(content, list):
        return "".join(p.get("text", "") for p in content)
    return content or ""


def build_request(body: dict, prov: dict) -> dict:
    """Anthropic Messages parameters for an OpenAI-format body (already redacted, already checked with unsupported())."""
    system, msgs = [], []
    for m in body.get("messages") or []:
        text = _text(m.get("content"))
        if m["role"] in ("system", "developer"):
            if text:
                system.append(text)
            continue
        if msgs and msgs[-1]["role"] == m["role"]:                      # the API wants alternating turns; merge neighbours
            msgs[-1]["content"] += "\n\n" + text
        else:
            msgs.append({"role": m["role"], "content": text})
    if not msgs or msgs[0]["role"] != "user":
        msgs.insert(0, {"role": "user", "content": "(continue)"})
    cap = int(prov.get("max_tokens_cap") or DEFAULT_TOKEN_CAP)
    asked = body.get("max_completion_tokens") or body.get("max_tokens")
    req: dict = {"model": prov["model"], "max_tokens": max(16, min(int(asked), cap)) if asked else cap, "messages": msgs}
    if system:
        req["system"] = "\n\n".join(system)
    if prov.get("effort"):
        req["output_config"] = {"effort": prov["effort"]}
    return req


def to_openai(msg: Any, model: str) -> dict:
    """OpenAI chat.completion JSON from an Anthropic Message (object or dict). Refusals raise so the gateway falls back."""
    get = (lambda o, k, d=None: o.get(k, d)) if isinstance(msg, dict) else (lambda o, k, d=None: getattr(o, k, d))
    stop = get(msg, "stop_reason")
    if stop == "refusal":
        raise ClaudeUnavailable("claude declined the request")
    text = "".join(get(b, "text", "") or "" for b in (get(msg, "content") or []) if get(b, "type") == "text")
    usage = get(msg, "usage")
    pin, pout = (get(usage, "input_tokens", 0) or 0, get(usage, "output_tokens", 0) or 0) if usage is not None else (0, 0)
    return {"id": f"chatcmpl-{get(msg, 'id', 'claude')}", "object": "chat.completion", "created": int(time.time()), "model": model,
            "choices": [{"index": 0, "message": {"role": "assistant", "content": text},
                         "finish_reason": "length" if stop == "max_tokens" else "stop"}],
            "usage": {"prompt_tokens": pin, "completion_tokens": pout, "total_tokens": pin + pout}}


def call(prov: dict, key: str, body: dict, timeout: float = 120.0, client: Any = None) -> dict:
    """Send one redacted chat body to Claude and return an OpenAI-format answer. Raises ClaudeUnavailable on every failure."""
    if expired(prov):
        raise ClaudeUnavailable("owner exception expired")
    why = unsupported(body)
    if why:
        raise ClaudeUnavailable(why)
    if client is None:
        try:
            import anthropic
        except ImportError as e:
            raise ClaudeUnavailable("anthropic SDK not installed") from e
        headers = {"anthropic-workspace-id": prov["workspace_id"]} if prov.get("workspace_id") else None
        client = anthropic.Anthropic(api_key=key, timeout=timeout, max_retries=1, default_headers=headers)
    try:
        msg = client.messages.create(**build_request(body, prov))
    except ClaudeUnavailable:
        raise
    except Exception as e:                                              # noqa: BLE001 - report the kind only, never the text
        raise ClaudeUnavailable(f"claude call failed: {type(e).__name__}") from None
    return to_openai(msg, prov["model"])
