"""Tests for jarvis.muse.taskstate."""
from __future__ import annotations

import threading

import pytest

from jarvis.muse.taskstate import (ApprovalError, BadTransition, LeaseLost, RetryRefused,
                                   TaskStore)


class Clock:
    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t


@pytest.fixture
def clk():
    return Clock()


@pytest.fixture
def st(clk):
    return TaskStore(":memory:", clock=clk, lease_s=60)


def running(st, worker="w1", **kw):
    tid = st.enqueue("k", {"a": 1}, **kw)
    assert st.claim(worker)["id"] == tid
    return tid


def state(st, tid):
    return st.get(tid)["state"]


def test_legal_transitions(st, clk):
    t = running(st)
    assert st.get(t)["attempts"] == 1
    st.complete(t, "w1", {"ok": 1})
    assert state(st, t) == "succeeded" and st.get(t)["result"] == {"ok": 1}

    t = running(st)
    st.fail(t, "w1", "boom")
    assert state(st, t) == "failed"
    st.retry(t)
    assert state(st, t) == "queued"
    st.cancel(t)
    assert state(st, t) == "cancelled"

    t = running(st)
    st.pause(t)
    assert state(st, t) == "paused"
    st.resume(t)
    assert state(st, t) == "queued"
    st.claim("w1")
    st.cancel(t)
    assert state(st, t) == "cancelled"

    t = running(st)
    st.pause(t)
    st.cancel(t)
    assert state(st, t) == "cancelled"

    t = st.enqueue("k", run_at=clk.t + 10)
    assert state(st, t) == "scheduled"
    st.cancel(t)
    assert state(st, t) == "cancelled"


@pytest.mark.parametrize("setup,action", [
    ("queued", lambda st, t: st.pause(t)),
    ("queued", lambda st, t: st.resume(t)),
    ("queued", lambda st, t: st.retry(t)),
    ("succeeded", lambda st, t: st.cancel(t)),
    ("cancelled", lambda st, t: st.resume(t)),
    ("failed", lambda st, t: st.cancel(t)),
])
def test_illegal_transitions(st, setup, action):
    t = st.enqueue("k")
    if setup == "succeeded":
        st.claim("w")
        st.complete(t, "w")
    elif setup == "failed":
        st.claim("w")
        st.fail(t, "w", "e")
    elif setup == "cancelled":
        st.cancel(t)
    with pytest.raises(BadTransition):
        action(st, t)
    assert state(st, t) == setup


def test_idempotent_enqueue(st):
    a = st.enqueue("k", key="same")
    assert st.enqueue("k", {"x": 2}, key="same") == a
    assert st.enqueue("k") != a


def test_concurrent_claim_single_winner(st):
    st.enqueue("k")
    barrier = threading.Barrier(8)
    wins = []

    def go(i):
        barrier.wait()
        r = st.claim(f"w{i}")
        if r:
            wins.append(r["lease_owner"])

    ts = [threading.Thread(target=go, args=(i,)) for i in range(8)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    assert len(wins) == 1


def test_claim_oldest_first(st):
    a, b = st.enqueue("k"), st.enqueue("k")
    assert st.claim("w")["id"] == a
    assert st.claim("w")["id"] == b
    assert st.claim("w") is None


def test_lease_renewal_and_loss(st, clk):
    t = running(st)
    clk.t += 40
    st.renew(t, "w1")
    clk.t += 50  # 90 after claim but only 50 after renew
    st.renew(t, "w1")
    with pytest.raises(LeaseLost):
        st.renew(t, "other")
    clk.t += 61
    with pytest.raises(LeaseLost):
        st.renew(t, "w1")
    with pytest.raises(LeaseLost):
        st.complete(t, "w1")


def test_only_owner_completes(st):
    t = running(st)
    with pytest.raises(LeaseLost):
        st.complete(t, "intruder")
    with pytest.raises(LeaseLost):
        st.fail(t, "intruder", "x")


def test_recover(st, clk):
    safe = running(st, "w1")
    unsafe = running(st, "w2")
    st.mark_effect_started(unsafe, "w2")
    assert st.recover() == []
    clk.t += 61
    assert sorted(st.recover()) == sorted([safe, unsafe])
    assert state(st, safe) == "queued"
    u = st.get(unsafe)
    assert u["state"] == "failed" and u["error"] == "outcome uncertain: not retried"
    assert st.claim("w3")["id"] == safe
    assert st.claim("w3") is None


def test_effect_markers(st):
    t = running(st)
    st.mark_effect_started(t, "w1")
    assert st.get(t)["outcome_uncertain"] == 1
    st.mark_effect_done(t, "w1")
    assert st.get(t)["outcome_uncertain"] == 0
    with pytest.raises(LeaseLost):
        st.mark_effect_started(t, "nope")


def test_retry_refusal_and_override(st, clk):
    t = running(st)
    st.mark_effect_started(t, "w1")
    clk.t += 61
    st.recover()
    with pytest.raises(RetryRefused):
        st.retry(t)
    with pytest.raises(RetryRefused):
        st.retry(t, force=True)
    with pytest.raises(RetryRefused):
        st.retry(t, confirm_duplicate_risk=True)
    st.retry(t, force=True, confirm_duplicate_risk=True)
    g = st.get(t)
    assert g["state"] == "queued" and g["outcome_uncertain"] == 0


def test_approval_flow(st):
    t = running(st)
    st.request_approval(t, "w1", {"send": "hi"})
    g = st.get(t)
    assert g["state"] == "waiting_approval" and g["lease_owner"] is None
    st.approve(t, True, "alice")
    g = st.get(t)
    assert g["state"] == "queued" and g["approval"]["decision"] is True
    assert g["approval"]["by"] == "alice"
    with pytest.raises(ApprovalError):
        st.approve(t, False, "bob")
    assert st.claim("w2")["id"] == t

    t2 = running(st)
    st.request_approval(t2, "w1", {})
    st.approve(t2, False, "alice")
    assert state(st, t2) == "cancelled"
    with pytest.raises(ApprovalError):
        st.approve(t2, True, "alice")


def test_history_has_no_payload(st):
    t = running(st)
    st.complete(t, "w1", {"secret": "zzz"})
    h = st.history(t)
    assert [(f, to) for _, f, to, _ in h] == [(None, "queued"), ("queued", "running"),
                                              ("running", "succeeded")]
    assert "zzz" not in repr(h) and all(len(x) == 4 for x in h)


def test_persistence_restart_mid_run(tmp_path, clk):
    p = tmp_path / "t.db"
    s = TaskStore(p, clock=clk)
    t = running(s)
    before = s.get(t)
    s.close()
    s2 = TaskStore(p, clock=clk)
    assert s2.get(t) == before
    assert len(s2.history(t)) == 2
    clk.t += 61
    assert s2.recover() == [t]
    assert state(s2, t) == "queued"
    s2.close()


def test_scheduled_due_after_run_at(st, clk):
    t = st.enqueue("k", run_at=clk.t + 100)
    assert st.claim("w") is None
    assert state(st, t) == "scheduled"
    clk.t += 100
    assert st.claim("w")["id"] == t
    assert [to for _, _, to, _ in st.history(t)] == ["scheduled", "queued", "running"]
