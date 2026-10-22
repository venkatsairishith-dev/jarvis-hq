"""Review additions for the watch engine: bounded records, no secrets in failure records, the paused notice names its watch."""
from __future__ import annotations

import json

from jarvis.muse import watch as W
from jarvis.muse.watch import Scheduler, Watch


def make(cond=None):
    return Watch(id="w1", url="https://shop.example/item?token=SECRET123", condition=cond or {"type": "changed"}, interval_s=60)


def test_history_is_capped_and_the_saved_file_stays_small(tmp_path):
    w, s = make(), Scheduler()
    for i in range(W.MAX_HISTORY + 30):
        s.run_check(w, lambda url, i=i: f"line {i}\nstable", now=1000.0 + i)
    assert len(w.history) == W.MAX_HISTORY
    W.save(tmp_path / "w.json", [w])
    assert len(json.loads((tmp_path / "w.json").read_text(encoding="utf-8"))[0]["history"]) == W.MAX_HISTORY


def test_a_huge_diff_is_truncated_with_a_note():
    w, s = make(), Scheduler()
    s.run_check(w, lambda url: "\n".join(f"old {i}" for i in range(1000)), now=1.0)
    s.run_check(w, lambda url: "\n".join(f"new {i}" for i in range(1000)), now=2.0)
    diff = w.history[-1]["diff"]
    assert len(diff) == W.MAX_DIFF_LINES + 1 and diff[-1]["op"] == "note" and "more changed" in diff[-1]["text"]


def test_failure_records_hold_the_error_kind_not_the_message():
    w, s = make(), Scheduler()

    def boom(url):
        raise RuntimeError("GET " + url + " failed with token SECRET123")

    s.run_check(w, boom, now=1.0)
    assert w.history[-1] == {"at": 1.0, "ok": False, "error": "RuntimeError"}
    assert "SECRET123" not in json.dumps(w.history)        # the URL itself lives in the watch; the failure record must not repeat it


def test_paused_notice_names_the_watch():
    w, s = make(), Scheduler()
    notices = []
    for i in range(W.MAX_FAILURES):
        notices = s.run_check(w, lambda url: (_ for _ in ()).throw(OSError("x")), now=float(i))
    assert w.state == "paused" and notices == [{"kind": "paused", "watch": "w1", "reason": "5 failed checks"}]


def test_hit_marker_is_a_hash_not_a_copy_of_the_page_and_still_notifies_once():
    w, s = make({"type": "contains", "text": "in stock"}), Scheduler()
    page = "Samsung T7 - in stock - " + "x" * 5000
    assert len(s.run_check(w, lambda url: page, now=1.0)) == 1
    assert len(s.run_check(w, lambda url: page, now=2.0)) == 0
    assert len(w.last_hit_snapshot) == 64 and "in stock" not in w.last_hit_snapshot
    assert len(s.run_check(w, lambda url: page + " now", now=3.0)) == 1
