"""Router core: pure logic (no network) for choosing a provider/model target per request.

Hard rules: a provider is in exactly one lane; a public-lane token never reaches a private-lane provider or combo;
a paid provider is never used; every budget defaults to $0; key material never appears in errors, reprs or snapshots.
"""
from .anthropic_client import AnthropicClient, ClientError, UpstreamHTTP
from .breaker import CircuitBreaker
from .budget import Budget
from .cache import ResponseCache, cached_call
from .combos import Combo, ComboError, RouteDenied, Target, load_combos, resolve
from .keypool import KeyPool, NoKey
from .passthrough import Passthrough, PassthroughError, forward_count_tokens, forward_messages
from .quota import Quota
from .router import NoRoute, Pick, Router

__all__ = ["AnthropicClient", "ClientError", "UpstreamHTTP", "CircuitBreaker", "Budget", "Combo", "ComboError", "RouteDenied", "Target",
           "load_combos", "resolve", "KeyPool", "NoKey", "Passthrough", "PassthroughError", "forward_count_tokens", "forward_messages",
           "Quota", "NoRoute", "Pick", "Router", "ResponseCache", "cached_call"]
