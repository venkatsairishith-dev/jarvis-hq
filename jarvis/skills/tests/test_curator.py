"""Tests for the skill curator lifecycle."""
import json
import os

import pytest

from skills import curator as cm
from skills.curator import Curator, CuratorError, Tampered

DAY = 86400.0


class Clock:
    def __init__(self, t=1_000_000.0):
        self.t = t

    def __call__(self):
        self.t += 1.0
        return self.t


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def cur(tmp_path, clock):
    return Curator(str(tmp_path / "curator.json"), clock=clock, max_active=3)


def active(cur, name, sha=None, source="owner"):
    cur.stage(name, sha or "sha-" + name, source)
    cur.promote(name, "audit-" + name)


def states(cur):
    return {n: r["state"] for n, r in cur.skills.items()}


def test_stage_promote_use_history(cur):
    cur.stage("a", "s1")
    with pytest.raises(CuratorError):
        cur.record_use("a")
    with pytest.raises(CuratorError):
        cur.promote("a", "")
    cur.promote("a", "rec-1")
    cur.record_use("a")
    assert cur.skills["a"]["use_count"] == 1 and cur.skills["a"]["last_used"]
    with pytest.raises(CuratorError):
        cur.promote("a", "rec-2")
    assert [(h["from"], h["to"]) for h in cur.history("a")] == [(None, "staged"), ("staged", "active")]
    assert cur.history("a")[1]["actor"] == "owner"


def test_stale_and_archive_moves_and_use_refused(cur, clock):
    active(cur, "a")
    now = clock.t
    assert cur.plan(now + 10 * DAY).moves == ()
    p = cur.plan(now + 31 * DAY)
    assert [(m.name, m.to_state) for m in p.moves] == [("a", "stale")]
    clock.t = now + 31 * DAY
    cur.apply(p, dry_run=False)
    with pytest.raises(CuratorError):
        cur.record_use("a")
    p = cur.plan(now + 95 * DAY)
    assert [(m.name, m.to_state) for m in p.moves] == [("a", "archived")]
    clock.t = now + 95 * DAY
    cur.apply(p, dry_run=False)
    assert cur.skills["a"]["state"] == "archived"
    assert cur.history("a")[-1]["actor"] == "curator"


def test_never_used_falls_back_to_promoted_at(cur, clock):
    active(cur, "a")
    assert cur.plan(clock.t + 29 * DAY).moves == ()
    assert cur.plan(clock.t + 31 * DAY).moves


def test_invariant_hand_written_store_rejected(tmp_path):
    rec = {"name": "x", "state": "active", "pinned": False, "created": 1, "promoted_at": 1, "last_used": None,
           "use_count": 0, "source": "agent", "content_sha256": "h", "promotion_record": None}
    p = tmp_path / "s.json"
    p.write_text(json.dumps({"skills": {"x": rec}}))
    with pytest.raises(CuratorError, match="promotion_record"):
        Curator(str(p))
    c = Curator(str(tmp_path / "other.json"))
    c.stage("y", "h")
    c.skills["y"]["state"] = "active"
    c._save()
    with pytest.raises(CuratorError):
        c.load()


def test_dry_run_default_changes_nothing_and_matches_real(cur, clock):
    active(cur, "a")
    active(cur, "b")
    cur.record_use("b")
    now = clock.t + 40 * DAY
    p = cur.plan(now)
    store = open(cur.store_path, "rb").read()
    n_backups = len(cur._backups())
    r = cur.apply(p)
    assert r.dry_run and open(cur.store_path, "rb").read() == store
    assert len(cur._backups()) == n_backups and states(cur)["a"] == "active"
    assert r.moves and r.after == {m.name: m.to_state for m in p.moves}
    clock.t = now
    real = cur.apply(p, dry_run=False)
    assert not real.dry_run and real.moves == r.moves and real.before == r.before and real.after == r.after
    assert all(states(cur)[m.name] == m.to_state for m in p.moves)
    assert real.backup and os.path.exists(real.backup)


def test_pinned_untouched(cur, clock):
    active(cur, "a")
    cur.pin("a")
    assert cur.plan(clock.t + 500 * DAY).moves == ()
    cur.unpin("a")
    assert cur.plan(clock.t + 500 * DAY).moves


def test_max_active_overflow_picks_lru(cur):
    for n in "abcde":
        active(cur, n)
    for n in "bcdea":          # least recently used first: b, c, d, e, a
        cur.record_use(n)
    cur.pin("b")               # pinned b is never chosen
    p = cur.plan()             # 5 active, max 3 -> two moves
    assert [m.name for m in p.moves] == ["c", "d"]
    assert all("max_active" in m.reason for m in p.moves)


def test_consolidation_listed_not_applied(cur):
    cur.max_active = 10
    active(cur, "Web-Search", "h1")
    active(cur, "web_search", "h2")
    active(cur, "other", "h1")
    active(cur, "unique", "h9")
    p = cur.plan()
    pairs = {(a, b) for a, b, _ in p.consolidation_candidates}
    assert ("Web-Search", "web_search") in pairs and ("Web-Search", "other") in pairs
    cur.apply(p, dry_run=False)
    assert set(states(cur).values()) == {"active"}


def test_backups_created_and_pruned(cur):
    for i in range(15):
        cur.stage(f"s{i}", "h")
    backups = cur._backups()
    assert len(backups) == 10
    assert backups == sorted(backups)


def test_atomic_write_crash_leaves_old_store(cur, monkeypatch):
    active(cur, "a")
    old = open(cur.store_path, "rb").read()

    def boom(*a, **k):
        raise OSError("crash")
    monkeypatch.setattr(cm.os, "replace", boom)
    with pytest.raises(OSError):
        cur.stage("zzz", "h")
    monkeypatch.undo()
    assert open(cur.store_path, "rb").read() == old
    assert not os.path.exists(cur.store_path + ".tmp")
    assert "zzz" not in Curator(cur.store_path).skills


def test_restore_only_to_staged(cur, clock):
    active(cur, "a")
    with pytest.raises(CuratorError):
        cur.restore("a")
    clock.t += 40 * DAY
    cur.apply(cur.plan(), dry_run=False)
    clock.t += 100 * DAY
    cur.apply(cur.plan(), dry_run=False)
    assert cur.skills["a"]["state"] == "archived"
    cur.restore("a")
    assert cur.skills["a"]["state"] == "staged" and cur.skills["a"]["promotion_record"] is None
    cur.promote("a", "new-record")
    assert cur.skills["a"]["promotion_record"] == "new-record"


def test_promote_tampered(cur):
    cur.stage("a", "orig")
    with pytest.raises(Tampered):
        cur.promote("a", "rec", content_sha256="changed")
    assert cur.skills["a"]["state"] == "staged"
    with pytest.raises(Tampered):
        cur.promote("a", "rec", content_sha256="changed", accept_new_sha256="other")
    cur.promote("a", "rec", content_sha256="changed", accept_new_sha256="changed")
    assert cur.skills["a"]["content_sha256"] == "changed"


def test_rollback_to_backs_up_current(cur):
    cur.stage("a", "h")
    cur.stage("b", "h")
    cur.stage("c", "h")
    target = cur._backups()[-1]    # taken before "c" was staged: holds a and b
    n = len(cur._backups())
    cur.rollback_to(target)
    assert set(cur.skills) == {"a", "b"}
    assert len(cur._backups()) == n + 1
    assert set(Curator(cur.store_path).skills) == {"a", "b"}


def test_rollback_rejects_bad_backup(cur, tmp_path):
    cur.stage("a", "h")
    bad = tmp_path / "bad.json"
    bad.write_text("not json SECRET")
    with pytest.raises(CuratorError) as e:
        cur.rollback_to(str(bad))
    assert "SECRET" not in str(e.value)
    assert set(cur.skills) == {"a"}


def test_corrupt_store_no_echo(tmp_path):
    p = tmp_path / "s.json"
    p.write_text('{"skills": {"x": SUPERSECRET')
    with pytest.raises(CuratorError) as e:
        Curator(str(p))
    assert "SUPERSECRET" not in str(e.value)
    p.write_text(json.dumps({"skills": {"x": {"name": "x", "state": "bogus-SECRET"}}}))
    with pytest.raises(CuratorError) as e:
        Curator(str(p))
    assert "SECRET" not in str(e.value)


def test_report_has_no_contents(cur, clock):
    active(cur, "a", "TOPSECRETSHA")
    r = cur.apply(cur.plan(clock.t + 40 * DAY))
    assert "TOPSECRETSHA" not in repr(r)
