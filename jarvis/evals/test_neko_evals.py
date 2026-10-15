"""The regression comparison of neko_evals.py (no services needed)."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import neko_evals as ne  # noqa: E402


def test_compare_flags_only_real_drops():
    old = {"routing": {"accuracy": 0.9, "seconds_each": 0.5, "n": 16}, "pii": {"secrets_hidden": 1.0, "ordinary_flagged": 0.25}}
    same = {"routing": {"accuracy": 0.9, "seconds_each": 0.8, "n": 16}, "pii": {"secrets_hidden": 1.0, "ordinary_flagged": 0.25}}
    assert ne.compare(same, old) == []                                   # 0.3 s slower is within tolerance
    worse = {"routing": {"accuracy": 0.8, "seconds_each": 1.5}, "pii": {"secrets_hidden": 1.0, "ordinary_flagged": 0.4}}
    out = ne.compare(worse, old)
    assert "routing.accuracy: 0.9 -> 0.8" in out and "pii.ordinary_flagged: 0.25 -> 0.4" in out and any("seconds_each" in w for w in out)
    better = {"routing": {"accuracy": 1.0}, "pii": {"ordinary_flagged": 0.1}}
    assert ne.compare(better, old) == []


def test_sets_are_balanced_and_labelled():
    assert {w for _, w in ne.ROUTING} == {"general", "code", "math"}
    assert sum(w for _, w in ne.DEPTH) == len(ne.DEPTH) // 2
    assert {w for _, _, w in ne.CLAIMS} == {True, False}
