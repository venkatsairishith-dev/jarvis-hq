"""Automation scheduler fault injection (completion J, 2026-10-19): crashes between the steps of a run, duplicate delivery, clock
jumps, damaged stored definitions, unavailable dependencies. The rules under test: a slot fires at most once; a run whose fire
was cut off is surfaced as `unknown` and never re-run; one damaged definition never stops the scheduler for the others; failure
limits pause the automation. Real sqlite, real Automations/Jobs/Tasks; a fake clock; no sleeps."""
import datetime as dt
import os
import sys
import tempfile
import threading
import unittest

HERE = os.path.dirname(__file__)
sys.path.insert(0, os.path.join(HERE, ".."))
from jarvis_kernel import automations as au
from jarvis_kernel.automations import Automations
from jarvis_kernel.jobs import Jobs
from jarvis_kernel.store import Kernel
from jarvis_kernel.tasks import Tasks


def utc(y, mo, d, h=0, mi=0):
    return dt.datetime(y, mo, d, h, mi, tzinfo=dt.timezone.utc).timestamp()


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.k = Kernel(os.path.join(self.tmp.name, "d"))
        self.T, self.J = Tasks(self.k), Jobs(self.k)
        self.now = [utc(2026, 10, 11, 0, 0)]
        self.A = self.new_engine()

    def new_engine(self, **kw):
        kw.setdefault("dispatch", lambda agent, lane, tid, p: (200, {"status": "queued"}))
        return Automations(self.k, self.T, self.J, owner=None, clock=lambda: self.now[0], **kw)

    def tearDown(self):
        self.k.db.close()
        self.tmp.cleanup()

    def job_def(self, **kw):
        d = {"name": "repo check", "action_kind": "job", "trigger_kind": "interval", "schedule": "600", "tz": "UTC",
             "action": {"kind": "project_check", "params": {"project": "jarvis-hq"}, "node": "laptop"}, "overlap": "queue",
             "max_runs_per_day": 100}
        d.update(kw)
        return d

    def jobs(self):
        return self.k.db.execute("SELECT COUNT(*) FROM jobs WHERE kind='project_check'").fetchone()[0]

    def statuses(self, aid):
        return [r["status"] for r in self.A.runs(aid, 50)]


class CrashPoints(Base):
    def test_a_crash_after_the_run_is_claimed_but_before_it_fires_is_surfaced_and_never_re_run(self):
        a = self.A.create(self.job_def())
        self.now[0] += 610
        due = a["next_run"]
        # the process died right after the claim: advance done, run row 'claimed', nothing fired
        self.k.db.execute("UPDATE automations SET next_run=? WHERE id=?", (due + 600, a["id"]))
        self.assertTrue(self.A._claim(a, f"{a['id']}:{au.iso_utc(due)}", due, "schedule"))
        restarted = self.new_engine()                                 # a restart: a new object over the same database
        restarted.tick()
        self.assertEqual(self.jobs(), 0, "a slot claimed before the crash must not fire at once on restart")
        self.now[0] += au.CLAIM_GRACE + 10
        restarted.tick()
        self.assertIn("unknown", self.statuses(a["id"]))
        self.assertEqual([s for s in self.statuses(a["id"]) if s == "fired"].count("fired"), self.jobs())
        self.assertNotIn("claimed", self.statuses(a["id"]))

    def test_a_crash_inside_fire_leaves_the_run_unknown_and_the_job_is_not_created_twice(self):
        a = self.A.create(self.job_def())
        self.now[0] += 610

        class Crash(BaseException):
            pass
        real = self.J.create
        self.J.create = lambda *x, **y: (real(*x, **y), (_ for _ in ()).throw(Crash()))[0]     # the job exists; the process dies
        with self.assertRaises(Crash):
            self.A.tick()
        self.J.create = real
        self.assertEqual(self.jobs(), 1)
        self.assertEqual(self.statuses(a["id"]).count("firing"), 1)
        self.A.tick()                                                  # restart, same slot: it is not fired again
        self.assertEqual(self.jobs(), 1)
        self.now[0] += au.CLAIM_GRACE + 10
        self.A.tick()
        st = self.statuses(a["id"])
        self.assertIn("unknown", st)
        self.assertNotIn("firing", st)

    def test_a_crash_between_the_schedule_advance_and_the_claim_loses_the_slot_but_never_doubles_it(self):
        a = self.A.create(self.job_def())
        self.now[0] += 610
        self.k.db.execute("UPDATE automations SET next_run=? WHERE id=?", (a["next_run"] + 600, a["id"]))   # advanced, then died
        for _ in range(3):
            self.new_engine().tick()
        self.assertEqual(self.jobs(), 0)

    def test_a_run_started_by_the_fence_cannot_be_fired_by_a_second_caller(self):
        a = self.A.create(self.job_def())
        key = f"{a['id']}:manual:x"
        self.A._claim(a, key, self.now[0], "manual")
        results = []
        ts = [threading.Thread(target=lambda: results.append(self.new_engine()._fire(a, key))) for _ in range(6)]
        [t.start() for t in ts]
        [t.join() for t in ts]
        self.assertEqual(results.count("fired"), 1)
        self.assertEqual(self.jobs(), 1)


class Duplicates(Base):
    def test_the_same_file_event_reported_twice_runs_once(self):
        a = self.A.create({"name": "inbox", "action_kind": "job", "trigger_kind": "event", "tz": "UTC", "event_spec": {"watch": "downloads", "glob": "*.pdf"},
                           "action": {"kind": "project_check", "params": {"project": "jarvis-hq"}, "node": "laptop"}, "overlap": "queue",
                           "max_runs_per_day": 50})
        self.now[0] += 5
        for _ in range(2):                                              # the node reported it, then (a retried POST) reported it again
            self.k.db.execute("INSERT OR IGNORE INTO watch_events(id,node,watch,rel,size,mtime_ns,ts,handled) VALUES ('ev1','laptop','downloads','a.pdf',1,1,?,0)",
                              (self.now[0],))
            self.A.tick()
            self.k.db.execute("UPDATE watch_events SET handled=0 WHERE id='ev1'")   # a redelivery re-opens it
        self.assertEqual(self.jobs(), 1)

    def test_two_schedulers_overlapping_on_one_database_fire_each_slot_once(self):
        a = self.A.create(self.job_def())
        self.now[0] += 610
        engines = [self.new_engine() for _ in range(5)]
        ts = [threading.Thread(target=e.tick) for e in engines]
        [t.start() for t in ts]
        [t.join() for t in ts]
        self.assertEqual(self.jobs(), 1)
        self.assertEqual(len(self.A.runs(a["id"])), 1)


class ClockJumps(Base):
    def test_a_clock_that_jumps_back_fires_nothing_extra_and_nothing_twice(self):
        a = self.A.create(self.job_def())
        self.now[0] += 610
        self.A.tick()
        self.assertEqual(self.jobs(), 1)
        self.now[0] -= 3600                                             # NTP / a manual change: an hour back
        self.A.tick()
        self.assertEqual(self.jobs(), 1)
        self.now[0] += 3600 + 600                                       # forward again: the next slot, once
        self.A.tick()
        self.assertEqual(self.jobs(), 2)

    def test_a_jump_forward_over_many_slots_runs_the_latest_only_by_default(self):
        a = self.A.create(self.job_def(catchup="one"))
        self.now[0] += 600 * 50 + 30
        self.A.tick()
        self.assertEqual(self.jobs(), 1)
        self.assertIn("skipped_missed", self.statuses(a["id"]))

    def test_a_jump_forward_cannot_blow_through_the_daily_limit(self):
        a = self.A.create(self.job_def(catchup="all", max_runs_per_day=3))
        self.now[0] += 600 * 20 + 30
        self.A.tick()
        self.assertEqual(self.jobs(), 3)
        self.assertIn("skipped_limit", self.statuses(a["id"]))


class Damage(Base):
    def test_one_damaged_definition_does_not_stop_the_scheduler_for_the_others(self):
        bad = self.A.create(self.job_def(name="bad"))
        good = self.A.create(self.job_def(name="good"))
        self.k.db.execute("UPDATE automations SET action=? WHERE id=?", ('{"kind": "project_ch', bad["id"]))     # truncated write
        self.now[0] += 610
        out = self.A.tick()
        self.assertEqual(len(out["fired"]), 1)
        self.assertEqual(self.statuses(good["id"]), ["fired"])
        row = self.k.db.execute("SELECT enabled, state, paused_reason FROM automations WHERE id=?", (bad["id"],)).fetchone()
        self.assertEqual((row[0], row[1]), (0, "paused"))
        self.assertIn("damaged", row[2])
        self.now[0] += 600
        self.assertEqual(len(self.A.tick()["fired"]), 1)                # still working next time; the damaged one stays off

    def test_a_damaged_definition_with_an_open_run_does_not_stop_retry_and_settle(self):
        bad = self.A.create(self.job_def(name="bad"))
        self.A._claim(bad, f"{bad['id']}:k", self.now[0], "schedule", "claimed", "waiting: JARVIS is busy")
        self.k.db.execute("UPDATE automations SET action='{' WHERE id=?", (bad["id"],))
        self.now[0] += 5
        self.A.tick()                                                   # must not raise
        self.assertEqual(self.k.db.execute("SELECT enabled FROM automations WHERE id=?", (bad["id"],)).fetchone()[0], 0)

    def test_the_owner_tick_goes_on_when_the_scheduler_itself_fails(self):
        from jarvis_kernel.owner import Owner
        o = Owner(self.k, self.T, self.J)

        class Broken:
            def tick(self):
                raise RuntimeError("scheduler fault")
        o.automations = Broken()
        out = o.tick()
        self.assertEqual(out["automations"], {"error": "RuntimeError"})
        self.assertIn("announced", out)                                 # the rest of the tick ran


class Dependencies(Base):
    def test_an_unavailable_dispatch_refuses_the_run_counts_a_failure_and_pauses_at_the_limit(self):
        A = Automations(self.k, self.T, self.J, owner=None, dispatch=None, clock=lambda: self.now[0])
        a = A.create({"name": "lab", "action_kind": "lab_task", "trigger_kind": "interval", "schedule": "600", "tz": "UTC", "overlap": "queue",
                      "max_runs_per_day": 50, "pause_after_failures": 2,
                      "action": {"goal": "Write the weekly summary", "instructions": "Summarise the notes.", "criteria": ["a summary file exists"], "agent": "hermes"}})
        for _ in range(2):
            self.now[0] += 610
            A.tick()
        cur = A.get(a["id"])
        self.assertEqual((cur["enabled"], cur["state"]), (0, "paused"))
        self.assertTrue(all(r["status"] == "refused" for r in A.runs(a["id"])))
        self.assertNotIn("Traceback", str(A.runs(a["id"])))

    def test_the_kill_switch_stops_firing(self):
        a = self.A.create(self.job_def())
        self.now[0] += 610
        self.k.kill("test")
        self.assertEqual(self.A.tick()["fired"], [])
        self.assertEqual(self.jobs(), 0)


if __name__ == "__main__":
    unittest.main()
