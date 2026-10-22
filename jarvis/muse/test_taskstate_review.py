"""Review additions: resume obeys the uncertain-outcome rule, and a task that keeps losing its worker is failed."""
import pytest

from jarvis.muse.taskstate import RetryRefused, TaskStore


class Clock:
    t = 1000.0

    def __call__(self):
        return self.t


def test_pausing_mid_effect_then_resuming_is_refused_without_the_double_opt_in():
    c = Clock()
    s = TaskStore(clock=c, lease_s=60)
    tid = s.enqueue("send", {"to": "x"})
    s.claim("w1")
    s.mark_effect_started(tid, "w1")                  # an irreversible send may be half done
    s.pause(tid)
    with pytest.raises(RetryRefused):
        s.resume(tid)
    with pytest.raises(RetryRefused):
        s.resume(tid, force=True)                     # force alone is not enough
    s.resume(tid, force=True, confirm_duplicate_risk=True)
    assert s.get(tid)["state"] == "queued" and s.get(tid)["outcome_uncertain"] == 0


def test_resume_of_a_clean_paused_task_still_works():
    s = TaskStore(clock=Clock())
    tid = s.enqueue("k")
    s.claim("w1")
    s.pause(tid)
    s.resume(tid)
    assert s.get(tid)["state"] == "queued"


def test_a_task_that_keeps_losing_its_worker_is_failed_after_max_attempts():
    c = Clock()
    s = TaskStore(clock=c, lease_s=10, max_attempts=3)
    tid = s.enqueue("flaky")
    for attempt in range(3):
        assert s.claim("w")["id"] == tid
        c.t += 11                                     # the worker dies; its lease lapses
        s.recover()
    t = s.get(tid)
    assert t["state"] == "failed" and t["error"] == "max attempts reached" and t["attempts"] == 3
    assert s.claim("w") is None
