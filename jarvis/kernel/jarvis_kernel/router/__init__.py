"""Router core: pure logic (no network) for choosing a provider/model target per request.

Hard rules: a provider is in exactly one lane; a public-lane token never reaches a private-lane provider or combo;
a paid provider is never used; every budget defaults to $0; key material never appears in errors, reprs or snapshots.
"""
from .breaker import CircuitBreaker
from .budget import Budget
from .combos import Combo, ComboError, RouteDenied, Target, load_combos, resolve
from .keypool import KeyPool, NoKey
from .quota import Quota
from .router import NoRoute, Pick, Router

__all__ = ["CircuitBreaker", "Budget", "Combo", "ComboError", "RouteDenied", "Target", "load_combos", "resolve",
           "KeyPool", "NoKey", "Quota", "NoRoute", "Pick", "Router"]
