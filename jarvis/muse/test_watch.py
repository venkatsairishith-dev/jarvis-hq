"""Tests for jarvis.muse.watch: one group per numbered rule of the spec."""
from __future__ import annotations

import json

import pytest

from jarvis.muse.watch import Scheduler, Watch, compute_diff, condition_met, find_prices, load, save


def mk(cond=None, **kw) -> Watch:
    return Watch(id="w1", url="http://x", condition=cond or {"type": "changed"}, interval_s=100, **kw)


def failing(url):
    raise RuntimeError("boom")


def test_watch_fields_and_defaults():
    w = mk()
    assert (w.state, w.failures, w.last_snapshot, w.history) == ("active", 0, None, [])


def test_backoff_sequence_then_pause():
    w, s, now = mk(), Scheduler(), 1000.0
    waits, notices = [], []
    for _ in range(5):
        notices = s.run_check(w, failing, now)
        waits.append((w.next_check - now) / 60)
    assert waits[:4] == [2, 4, 8, 16]
    assert w.state == "paused" and w.failures == 5
    assert notices == [{"kind": "paused", "watch": w.id, "reason": "5 failed checks"}]
    assert s.due(10**9) == []
    w.resume()
    assert (w.state, w.failures) == ("active", 0)


def test_backoff_capped_at_60():
    w = mk(failures=9)
    Scheduler().run_check(w, failing, 0.0)
    assert w.next_check == 3600


def test_success_resets_failures():
    w, s = mk(), Scheduler()
    s.run_check(w, failing, 0.0)
    s.run_check(w, lambda u: "ok", 500.0)
    assert w.failures == 0 and w.next_check == 600


def test_diff_first_none_then_lines():
    w, s = mk(), Scheduler()
    s.run_check(w, lambda u: "a\nb", 0.0)
    assert w.history[-1]["diff"] == []
    s.run_check(w, lambda u: "a\nc", 1.0)
    h = w.history[-1]
    assert h["diff"] == [{"op": "del", "text": "b"}, {"op": "add", "text": "c"}]
    assert isinstance(h["summary"], str) and h["summary"]


def test_diff_ignores_whitespace_only_changes():
    assert compute_diff("a  b\nc", "a b\n\n  c ") == []


def test_contains_case_insensitive():
    assert condition_met({"type": "contains", "text": "SALE"}, "big sale now", [])


def test_contains_with_trailing_space_matches():
    assert condition_met({"type": "contains", "text": "in  stock "}, "Item is IN\n stock today", [])
    assert not condition_met({"type": "contains", "text": "sold out"}, "in stock", [])


@pytest.mark.parametrize("text,cur,amt", [
    ("Rs. 12,499", "INR", 12499), ("₹12,499.00", "INR", 12499), ("INR 12499", "INR", 12499),
    ("$1,299.99", "USD", 1299.99), ("12,499 INR", "INR", 12499), ("Rs 1,24,999", "INR", 124999),
])
def test_price_formats(text, cur, amt):
    assert find_prices(text) == [(cur, amt)]


def test_price_below_rules():
    c = {"type": "price_below", "amount": 13000, "currency": "INR"}
    assert condition_met(c, "now Rs. 12,499", [])
    assert not condition_met(c, "now Rs. 14,000", [])
    assert not condition_met(c, "now $5.00", [])  # other currency ignored
    assert condition_met(c, "Rs. 99,999 or ₹12,000", [])  # any price


def test_changed_condition_and_notify_once():
    w, s = mk(), Scheduler()
    assert s.run_check(w, lambda u: "a", 0.0) == []
    n = s.run_check(w, lambda u: "b", 1.0)
    assert len(n) == 1 and n[0]["kind"] == "hit" and n[0]["watch"] == "w1" and n[0]["diff"]
    assert s.run_check(w, lambda u: "b", 2.0) == []


def test_contains_notifies_once_per_snapshot():
    w, s = mk({"type": "contains", "text": "go"}), Scheduler()
    assert len(s.run_check(w, lambda u: "go", 0.0)) == 1
    assert s.run_check(w, lambda u: "go", 1.0) == []
    assert s.run_check(w, lambda u: "stop", 2.0) == []
    assert len(s.run_check(w, lambda u: "go go", 3.0)) == 1


def test_roundtrip_json_and_crash_resume(tmp_path):
    cond = {"type": "price_below", "amount": 1.5, "currency": "USD"}
    w = mk(cond)
    s = Scheduler([w])
    s.run_check(w, failing, 50.0)
    s.run_check(w, failing, 200.0)
    assert Watch.from_dict(json.loads(json.dumps(w.to_dict()))) == w
    p = tmp_path / "w.json"
    save(p, [w])
    # simulated crash mid-save: a half-written temp file is left behind
    (tmp_path / "w.json.tmp").write_text("{garbage", encoding="utf-8")
    (w2,) = load(p)
    assert (w2.failures, w2.next_check, w2.condition) == (2, w.next_check, cond)
    assert w2 == w
    assert load(tmp_path / "missing.json") == []


def test_due_ordering():
    a, b, c = mk(next_check=30.0), mk(next_check=10.0), mk(next_check=99.0)
    c.state = "paused"
    b.id, a.id = "b", "a"
    assert [w.id for w in Scheduler([a, b, c]).due(50.0)] == ["b", "a"]


def test_fetch_exception_is_failed_check():
    w = mk()
    assert Scheduler().run_check(w, failing, 0.0) == []
    assert w.failures == 1 and w.history[-1]["ok"] is False
