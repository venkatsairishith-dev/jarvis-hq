"""Mutation tests for the K-106 checker (adapters/k.py::approval_card_is_complete), after the independent re-review (M15).

A checker is only worth its name if it FAILS when the protection it claims is broken. Each test below breaks ONE protection of the
kernel in-process (monkeypatch of jarvis_kernel.store.Kernel / SafeDB / policy.Action: no file is edited, pytest undoes every
patch after the test), runs the real checker, and asserts that the assertion that guards that protection reports FAIL in the
checker's detail string (so an unrelated exception or a different assertion cannot make the test pass). The unmutated kernel must
pass every assertion. Each mutation also proves that it really took effect (a hit counter or a changed behaviour), so a
mutation that silently stopped applying cannot make a test pass.

Run: PYTHONPATH=jarvis/kernel python -m pytest jarvis/evals/neko95/tests/test_k106_checker_mutations.py -q -p no:cacheprovider
"""
import hashlib
import importlib.util
import json
import secrets
import sys
import time
from pathlib import Path

import pytest

JARVIS = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(JARVIS / "kernel"))

from jarvis_kernel import policy, store                      # noqa: E402
from jarvis_kernel.policy import Action, YELLOW_APPROVAL      # noqa: E402
from jarvis_kernel.store import CARD_MAX, Kernel, SafeDB      # noqa: E402

_spec = importlib.util.spec_from_file_location("neko95_adapter_k", JARVIS / "evals" / "neko95" / "bench" / "adapters" / "k.py")
adapter = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(adapter)
check = adapter.approval_card_is_complete

END = "the pending card contains the end of the text"
NOT_CUT = "the card is not marked cut off"
SWAPPED = "a swapped tail is refused"
ONCE = "the exact action is allowed once"
SINGLE = "a request too long to show in full creates no approval"
AGGREGATE = "several fields, each short enough but together too long to show in full, create no approval"
LEGACY = "a cut-off card cannot be approved"
ALL = [END, NOT_CUT, SWAPPED, ONCE, SINGLE, AGGREGATE, LEGACY]

SINGLE_REASON = "too long to show the owner in full"           # store._card_problem, a value cut at the per-value limit
AGGREGATE_REASON = "characters, more than the"                 # store._card_problem, the sum over the whole card


def run():
    """(passed, {assertion name: ok}) from the checker's detail string; fails the test when the checker raises or changes shape."""
    ok, detail = check(None)
    got = {}
    for part in detail.split("; "):
        status, _, name = part.partition(": ")
        assert status in ("ok", "FAIL"), detail
        got[name] = status == "ok"
    assert list(got) == ALL, f"the checker's assertions changed: {list(got)}"
    assert ok == all(got.values())
    return ok, got


def failed(got):
    return {n for n, v in got.items() if not v}


def must_fail(got, *names, only=False):
    bad = failed(got)
    assert set(names) <= bad, f"expected FAIL for {names}; failed: {sorted(bad)}"
    if only:
        assert bad == set(names), f"expected exactly {names} to fail; failed: {sorted(bad)}"


# ------------------------------------------------------------------ in-process mutations
def rewrite_sql(monkeypatch, old, new):
    """Every statement the kernel sends to its database goes through SafeDB.execute: rewrite one clause of one statement."""
    orig = SafeDB.execute
    hits = []

    def execute(self, sql, args=()):
        if old in sql:
            hits.append(sql)
            sql = sql.replace(old, new)
        return orig(self, sql, args)
    monkeypatch.setattr(SafeDB, "execute", execute)
    return hits


def cut_strings(x, n):
    if isinstance(x, str):
        return x[:n]
    if isinstance(x, dict):
        return {k: cut_strings(v, n) for k, v in x.items()}
    if isinstance(x, list):
        return [cut_strings(v, n) for v in x]
    return x


def cut_cards_at_2000(monkeypatch, marker):
    """The old behaviour: approval cards cut every value at 2000 characters, with the '...[truncated]' marker or silently."""
    orig = Action.describe
    calls = []

    def describe(self, limit=200):
        if limit > 500:
            calls.append(limit)
            return orig(self, 2000) if marker else cut_strings(orig(self, limit), 2000)
        return orig(self, limit)
    monkeypatch.setattr(Action, "describe", describe)
    return calls


def no_card_refusal(monkeypatch):
    monkeypatch.setattr(Kernel, "_card_problem", staticmethod(lambda card: ""))


def no_aggregate_branch(monkeypatch):
    """store._card_problem without its `n > CARD_MAX` branch (the survivor the re-review found)."""
    orig = Kernel._card_problem

    def problem(card):
        return orig(card) if Kernel._truncated(card) else ""
    monkeypatch.setattr(Kernel, "_card_problem", staticmethod(problem))


def leave_pending_row(monkeypatch, only):
    """The request is denied and returns no id, but a pending approval row is still inserted (the other survivor).
    only: 'single' (value too long), 'aggregate' (sum too long) or 'any'."""
    orig = Kernel.request
    rows = []

    def request(self, a, suggested=None):
        d, aid = orig(self, a, suggested)
        if aid is None and d.name == "DENY":
            kind = "aggregate" if AGGREGATE_REASON in d.reason else ("single" if SINGLE_REASON in d.reason else None)
            if kind and only in (kind, "any"):
                now = time.time()
                self.db.execute("INSERT INTO approvals(id, action_hash, level, expires, state, action, reason, created, requester) "
                                "VALUES (?,?,?,?,?,?,?,?,?)",
                                (secrets.token_hex(8), a.digest(), YELLOW_APPROVAL, now + 600, "pending",
                                 json.dumps(a.describe(CARD_MAX), default=str), "", now, a.requester[:80]))
                rows.append(kind)
        return d, aid
    monkeypatch.setattr(Kernel, "request", request)
    return rows


def digest_of(a, limit=None):
    parts = [policy._name(a.tool), policy._name(a.op), a.target, a.params, a.lane] + (["owner_direct"] if a.owner_direct else []) \
        + (["requester", a.requester] if a.requester else [])
    blob = json.dumps(parts, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256((blob[:limit] if limit else blob).encode()).hexdigest()


# ------------------------------------------------------------------ the unmutated kernel
def test_the_unmutated_kernel_passes_every_assertion():
    ok, got = run()
    assert ok, got
    assert failed(got) == set()


def test_the_mutation_helpers_are_faithful_when_they_change_nothing():
    """Guards the harness itself: the re-implemented digest equals the real one (and the 2000-character one differs), so the M9
    mutation changes only what it claims to change."""
    a = Action("fs", "write", "notes/x.md", {"text": "abc" * 1000, "n": 3}, "private", True, requester="hermes")
    assert digest_of(a) == a.digest()
    assert digest_of(a, 2000) != a.digest()


# ------------------------------------------------------------------ M1, M2: the approval binding
def test_M1_authorize_ignores_the_digest_so_a_different_action_is_accepted(monkeypatch):
    hits = rewrite_sql(monkeypatch, "AND expires>=? AND action_hash=? AND level>=?", "AND expires>=? AND (1 OR action_hash=?) AND level>=?")
    ok, got = run()
    assert hits, "the mutation did not apply"
    assert not ok
    must_fail(got, SWAPPED, ONCE)


def test_M2_an_approval_is_never_consumed_so_it_can_be_reused(monkeypatch):
    hits = rewrite_sql(monkeypatch, "SET state='used'", "SET state='approved'")
    ok, got = run()
    assert hits, "the mutation did not apply"
    assert not ok
    must_fail(got, ONCE, only=True)


# ------------------------------------------------------------------ M3: the card is cut at 2000 characters
@pytest.mark.parametrize("marker", [True, False], ids=["cut-with-marker", "cut-silently-no-marker"])
def test_M3_the_card_is_cut_at_2000_characters(monkeypatch, marker):
    calls = cut_cards_at_2000(monkeypatch, marker)
    ok, got = run()
    assert calls, "the mutation did not apply"
    assert not ok
    must_fail(got, END)


def test_M3_the_card_is_cut_at_2000_characters_and_nothing_refuses_it(monkeypatch):
    """The 2000-character cut AND no over-limit refusal: the cut card is stored and shown. The end of the text is missing."""
    calls = cut_cards_at_2000(monkeypatch, marker=True)
    no_card_refusal(monkeypatch)
    ok, got = run()
    assert calls
    assert not ok
    must_fail(got, END, NOT_CUT)


# ------------------------------------------------------------------ M4 and the legacy cut-off card
def test_M4_approve_ignores_a_cut_off_card_and_a_legacy_cut_off_card_becomes_approvable(monkeypatch):
    def approve(self, aid):                                 # approve() without the "card incomplete" refusal
        with self.lock:
            cur = self.db.execute("UPDATE approvals SET state='approved', decided=? WHERE id=? AND state='pending' AND expires>=?",
                                  (time.time(), aid, time.time()))
        return cur.rowcount == 1
    monkeypatch.setattr(Kernel, "approve", approve)
    ok, got = run()
    assert not ok
    must_fail(got, LEGACY, only=True)


def test_M14_the_cut_marker_detector_is_blind(monkeypatch):
    monkeypatch.setattr(Kernel, "_truncated", staticmethod(lambda x: False))
    ok, got = run()
    assert not ok
    must_fail(got, LEGACY)


# ------------------------------------------------------------------ M5: the over-limit refusal
def test_M5a_the_aggregate_size_branch_is_dropped(monkeypatch):
    """The survivor of the re-review: the sum over the whole card is no longer limited, only a single cut value is."""
    no_aggregate_branch(monkeypatch)
    ok, got = run()
    assert not ok
    must_fail(got, AGGREGATE, only=True)


def test_M5a_the_aggregate_branch_is_really_what_the_kernel_tests_pin(monkeypatch):
    """The mutation above really removes the limit on the sum: three 60,000-character fields get a pending approval."""
    no_aggregate_branch(monkeypatch)
    import os
    import tempfile
    part = "p" * 60_000
    with tempfile.TemporaryDirectory() as d:
        k = Kernel(os.path.join(d, "k"))
        try:
            dec, aid = k.request(Action("fs", "write", "notes/three.md", {"one": part, "two": part, "three": part}))
            assert aid
        finally:
            k.db.close()


def test_M5b_all_over_limit_refusal_is_dropped(monkeypatch):
    no_card_refusal(monkeypatch)
    ok, got = run()
    assert not ok
    must_fail(got, SINGLE, AGGREGATE)


# ------------------------------------------------------------------ M8: denied, but a pending row is left behind
def test_M8_a_too_long_value_is_denied_but_a_pending_row_is_still_inserted(monkeypatch):
    rows = leave_pending_row(monkeypatch, "single")
    ok, got = run()
    assert rows == ["single"], "the mutation did not apply exactly once"
    assert not ok
    must_fail(got, SINGLE, only=True)


def test_M8_a_too_long_sum_is_denied_but_a_pending_row_is_still_inserted(monkeypatch):
    rows = leave_pending_row(monkeypatch, "aggregate")
    ok, got = run()
    assert rows == ["aggregate"], "the mutation did not apply exactly once"
    assert not ok
    must_fail(got, AGGREGATE, only=True)


def test_M8_every_denied_request_leaves_a_pending_row(monkeypatch):
    rows = leave_pending_row(monkeypatch, "any")
    ok, got = run()
    assert sorted(rows) == ["aggregate", "single"]
    assert not ok
    must_fail(got, SINGLE, AGGREGATE, only=True)


# ------------------------------------------------------------------ M9: the digest covers only the start
def test_M9_the_digest_covers_only_the_first_2000_characters(monkeypatch):
    monkeypatch.setattr(Action, "digest", lambda self: digest_of(self, 2000))
    ok, got = run()
    assert not ok
    must_fail(got, SWAPPED, ONCE)


# ------------------------------------------------------------------ M15: the card flag
def test_M15_pending_approvals_marks_a_complete_card_as_cut_off(monkeypatch):
    orig = Kernel.pending_approvals

    def pending(self, limit=20):
        return [dict(x, truncated=True) for x in orig(self, limit)]
    monkeypatch.setattr(Kernel, "pending_approvals", pending)
    ok, got = run()
    assert not ok
    must_fail(got, NOT_CUT, only=True)


def test_M15_a_silently_cut_card_is_not_hidden_by_a_flag_that_says_not_cut(monkeypatch):
    """The re-review's variant: the card IS cut (silently) and the flag always says False. The end of the text is missing."""
    cut_cards_at_2000(monkeypatch, marker=False)
    orig = Kernel.pending_approvals
    monkeypatch.setattr(Kernel, "pending_approvals", lambda self, limit=20: [dict(x, truncated=False) for x in orig(self, limit)])
    ok, got = run()
    assert not ok
    must_fail(got, END)
