"""Request sizing for the model gateway: estimate a chat request's input tokens, decide which providers can take it,
and classify a provider's refusal (too large / rate limited / other) so the gateway answers the agent correctly.

Why: a long Hermes session (~55k tokens) was sent unchanged to every provider in the lane; Groq answered 413, the others
400, and the agent got a 502 "all providers failed", which it retried for 15 minutes. Now a request that no provider
in the lane can take gets one fast 413 "context length exceeded" (the agent compresses its history), and providers
that are too small are skipped instead of called.

CONTEXT_LENGTH_MARKERS and looks_like_context_length_error() are reused from OpenJarvis
(https://github.com/open-jarvis/OpenJarvis, src/openjarvis/engine/_base.py at ecbde4b32091e2a8b02e8acfa41cb5f95e4da560),
Apache License 2.0, Copyright the OpenJarvis authors. Modified: markers added for HTTP 413 / Groq "request too large".
The token estimate adapts OpenJarvis's estimate_prompt_tokens (characters / 4 + 4 per message); JARVIS uses 3.5
characters per token because its requests carry JSON tool schemas, which tokenize denser than prose.
"""
import json

CONTEXT_LENGTH_MARKERS = (
    "context length",
    "maximum context",
    "context window",
    "maximum_context",
    "context_length_exceeded",
    # added for JARVIS: size refusals that do not say "context" (Groq 413, proxies)
    "request too large",
    "request_too_large",
    "payload too large",
    "too many tokens",
    "prompt is too long",
    "input is too long",
    "reduce the length",
    "context size",            # llama.cpp: "exceeds the available context size (4096 tokens)"
    "exceed_context_size",
)


def looks_like_context_length_error(text: str) -> bool:
    """True when *text* reads like a context-window overflow error (OpenJarvis heuristic, markers extended)."""
    low = (text or "").lower()
    return any(marker in low for marker in CONTEXT_LENGTH_MARKERS)


CHARS_PER_TOKEN = 3.5


def estimate_tokens(body: dict) -> int:
    """Input tokens of an OpenAI-style chat request: messages (content, tool calls, reasoning replays) and tool schemas."""
    msgs = body.get("messages") or []
    chars = len(json.dumps(msgs, ensure_ascii=False)) + len(json.dumps(body.get("tools") or [], ensure_ascii=False))
    return max(1, int(chars / CHARS_PER_TOKEN) + 4 * len(msgs))


MIN_OUTPUT = 1024        # a provider is only used if at least this much answer still fits
SUMMARY_MIN_OUTPUT = 4096   # a context-compression summary needs room for a whole checkpoint (Hermes asks ~2-7k)
SUMMARY_MAX_OUTPUT = 8192
SUMMARY_PREFIX = "You are a summarization agent creating a context checkpoint"   # Hermes context_compressor.py


def is_summary(body: dict) -> bool:
    """A Hermes context-compression call: one user message with the summarizer preamble and no tools."""
    msgs = body.get("messages") or []
    if body.get("tools") or not msgs:
        return False
    first = msgs[0].get("content") if isinstance(msgs[0], dict) else None
    if isinstance(first, list):
        first = " ".join(str(p.get("text", "")) for p in first if isinstance(p, dict))
    return isinstance(first, str) and first.lstrip().startswith(SUMMARY_PREFIX)


def estimate_fixed(body: dict) -> int:
    """The part of a request that compressing the conversation cannot shrink: system messages and tool schemas."""
    sys_msgs = [m for m in body.get("messages") or [] if isinstance(m, dict) and m.get("role") == "system"]
    chars = len(json.dumps(sys_msgs, ensure_ascii=False)) + len(json.dumps(body.get("tools") or [], ensure_ascii=False))
    return max(1, int(chars / CHARS_PER_TOKEN) + 4 * len(sys_msgs))


def fits_with_output(provider, tokens: int, out: int) -> bool:
    limit, total = getattr(provider, "max_input_tokens", None), getattr(provider, "max_total_tokens", None)
    return (not limit or tokens <= limit) and (not total or tokens + out <= total)


def summary_ok(reply: dict) -> tuple:
    """(ok, why) for a non-streamed summary reply: complete (not cut at the output cap) and with real content."""
    try:
        ch = reply["choices"][0]
    except (KeyError, IndexError, TypeError):
        return False, "no choices"
    if ch.get("finish_reason") == "length":
        return False, "truncated"
    text = (ch.get("message") or {}).get("content") or ""
    if len(text.strip()) < 200:
        return False, "empty"
    return True, ""


def fits(provider, tokens: int) -> bool:
    """A provider without a known limit is tried (as before); one with a limit only when the request fits:
    max_input_tokens bounds the input; max_total_tokens bounds input + output (measured: Groq counts the reserved
    max_tokens against its 8000 tokens-per-minute limit, llama.cpp against n_ctx)."""
    limit, total = getattr(provider, "max_input_tokens", None), getattr(provider, "max_total_tokens", None)
    return (not limit or tokens <= limit) and (not total or tokens + MIN_OUTPUT <= total)


def output_budget(provider, tokens: int, requested: int) -> int:
    """The answer budget to send this provider: what the agent asked for, shrunk to what still fits its total limit."""
    total = getattr(provider, "max_total_tokens", None)
    return min(requested, total - tokens) if total else requested


def classify(code: int, text: str) -> str:
    """"too_large", "rate_limited" or "other" for a provider's HTTP refusal."""
    if code == 413 or (code == 400 and looks_like_context_length_error(text)):
        return "too_large"
    if code == 429:
        return "too_large" if looks_like_context_length_error(text) else "rate_limited"
    return "other"


def cooldown_s(retry_after, text: str) -> int:
    """How long a rate-limited provider rests: its Retry-After (seconds), an hour for a used-up daily quota
    (e.g. OpenRouter "free-models-per-day"), else a minute. Capped at 6 h."""
    try:
        if retry_after:
            return max(1, min(int(float(retry_after)), 6 * 3600))
    except ValueError:
        pass
    low = (text or "").lower()
    if "per-day" in low or "per day" in low or "daily" in low:
        return 3600
    return 60


AGENT_MIN_CONTEXT = 64000   # Hermes refuses to run (and to summarize) with a window below 64K (agent_init, CONV:2108)


def reported_context_length(providers) -> int | None:
    """What /v1/models tells agents: the lane's real largest window, raised to Hermes's 64K minimum. Safe because the
    gateway never forwards a request a provider cannot fit: anything between the real window and 64K gets a fast 413,
    which makes the agent compress (its compaction trigger is set lower, by threshold_tokens)."""
    real = lane_context_length(providers)
    return max(real, AGENT_MIN_CONTEXT) if real else None


def lane_context_length(providers) -> int | None:
    """The largest known input limit among the lane's providers (smaller providers are skipped per request)."""
    limits = []
    for p in providers:
        a, t = getattr(p, "max_input_tokens", None), getattr(p, "max_total_tokens", None)
        if a or t:
            limits.append(min(x for x in (a, (t - MIN_OUTPUT) if t else None) if x))
    return max(limits) if limits else None
