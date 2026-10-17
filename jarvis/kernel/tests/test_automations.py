"""Automations (cron math, tick, catch-up, overlap, limits, auto-pause, preview) and agent definitions."""
import datetime as dt
import json
import os
import sys
import tempfile
import threading
import time
import unittest
from zoneinfo import ZoneInfo

HERE = os.path.dirname(__file__)
sys.path.insert(0, os.path.join(HERE, ".."))
from jarvis_kernel import automations as au
from jarvis_kernel.agents import AgentDefs, AgentError, draft_from_sentence
from jarvis_kernel.automations import Automations, AutomationError, next_cron
from jarvis_kernel.jobs import Jobs
from jarvis_kernel.store import Kernel
from jarvis_kernel.tasks import Tasks

try:
    import croniter
except ImportError:                                  # the oracle is optional
    croniter = None

try:
    ZoneInfo("Asia/Kolkata")
    HAVE_TZ = True
except Exception:
    HAVE_TZ = False


def utc(y, mo, d, h=0, mi=0):
    return dt.datetime(y, mo, d, h, mi, tzinfo=dt.timezone.utc).timestamp()


@unittest.skipUnless(HAVE_TZ, "no time zone database (pip install tzdata)")
class CronMath(unittest.TestCase):
    def local(self, ts, tz):
        return dt.datetime.fromtimestamp(ts, ZoneInfo(tz)).strftime("%Y-%m-%d %H:%M")

    def test_basic_expressions(self):
        base = utc(2026, 10, 11, 0, 0)
        self.assertEqual(self.local(next_cron("30 8 * * *", base, "Asia/Kolkata"), "Asia/Kolkata"), "2026-10-11 08:30")
        self.assertEqual(self.local(next_cron("*/15 * * * *", utc(2026, 10, 11, 10, 7), "UTC"), "UTC"), "2026-10-11 10:15")
        self.assertEqual(self.local(next_cron("0 9 * * 1-5", utc(2026, 10, 10, 12), "UTC"), "UTC"), "2026-10-12 09:00")   # Sat -> Mon
        self.assertEqual(self.local(next_cron("0 0 1 jan *", utc(2026, 10, 11), "UTC"), "UTC"), "2027-01-01 00:00")
        self.assertEqual(self.local(next_cron("5 4 * * sun", utc(2026, 10, 11, 5), "UTC"), "UTC"), "2026-10-18 04:05")  # strictly after

    def test_bad_expressions_are_refused(self):
        for bad in ("* * * *", "61 * * * *", "* 24 * * *", "*/0 * * * *", "a b c d e", "0 0 31 2 *"):
            with self.assertRaises(AutomationError, msg=bad):
                next_cron(bad, utc(2026, 1, 1), "UTC")

    def test_wall_clock_survives_dst(self):
        # Berlin: clocks go forward on 2026-03-29. "0 8 * * *" must stay 08:00 local on both sides.
        a = next_cron("0 8 * * *", utc(2026, 3, 28, 12), "Europe/Berlin")
        b = next_cron("0 8 * * *", a, "Europe/Berlin")
        self.assertEqual(self.local(a, "Europe/Berlin"), "2026-03-29 08:00")
        self.assertEqual(self.local(b, "Europe/Berlin"), "2026-03-30 08:00")
        self.assertEqual(round((b - a) / 3600), 24)            # both are after the switch (02:00 -> 03:00 happens before 08:00)
        c = next_cron("0 8 * * *", utc(2026, 3, 27, 12), "Europe/Berlin")      # 08:00 on the 28th (CET) to 08:00 on the 29th (CEST)
        d = next_cron("0 8 * * *", c, "Europe/Berlin")
        self.assertEqual(round((d - c) / 3600), 23)            # the wall clock is kept, so the UTC gap is 23 h

    def test_gap_and_fold_fire_once(self):
        # 02:30 does not exist in Berlin on 2026-03-29: it fires once, at the shifted time.
        t = next_cron("30 2 * * *", utc(2026, 3, 28, 12), "Europe/Berlin")
        self.assertEqual(self.local(t, "Europe/Berlin"), "2026-03-29 03:30")
        t2 = next_cron("30 2 * * *", t, "Europe/Berlin")
        self.assertEqual(self.local(t2, "Europe/Berlin"), "2026-03-30 02:30")
        # 02:30 happens twice on 2026-10-25: once.
        f = next_cron("30 2 * * *", utc(2026, 10, 24, 12), "Europe/Berlin")
        f2 = next_cron("30 2 * * *", f, "Europe/Berlin")
        self.assertEqual(self.local(f2, "Europe/Berlin"), "2026-10-26 02:30")

    @unittest.skipUnless(croniter, "croniter not installed")
    def test_agrees_with_croniter(self):
        exprs = ["*/7 * * * *", "0 */3 * * *", "15 10 * * 1,3,5", "0 8 1 * *", "30 6 * * mon-fri", "0 0 29 2 *", "45 23 31 * *",
                 "10 * * * 0", "0 12 15 jun *", "5 4 * * 7"]
        for tz in ("UTC", "Asia/Kolkata", "America/New_York"):
            for e in exprs:
                t = utc(2026, 10, 11, 3, 3)
                it = croniter.croniter(e, dt.datetime.fromtimestamp(t, ZoneInfo(tz)))
                for _ in range(5):
                    want = it.get_next(float)
                    got = next_cron(e, t, tz)
                    self.assertEqual(got, want, f"{e} {tz}")
                    t = got

    def test_phrases(self):
        f = Automations.from_phrase
        self.assertEqual(f("every morning at 7:30"), ("cron", "30 7 * * *"))
        self.assertEqual(f("Every day at 6pm"), ("cron", "0 18 * * *"))
        self.assertEqual(f("weekdays at 9"), ("cron", "0 9 * * 1-5"))
        self.assertEqual(f("every Monday at 8:15am"), ("cron", "15 8 * * 1"))
        self.assertEqual(f("every 30 minutes"), ("interval", "1800"))
        self.assertEqual(f("hourly"), ("interval", "3600"))
        self.assertIsNone(f("whenever the moon is full"))


@unittest.skipUnless(HAVE_TZ, "no time zone database (pip install tzdata)")
class Engine(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.k = Kernel(os.path.join(self.tmp.name, "d"))
        self.T, self.J = Tasks(self.k), Jobs(self.k)
        self.now = [utc(2026, 10, 11, 0, 0)]
        self.dispatched = []

        def dispatch(agent, lane, tid, p):
            self.dispatched.append((agent, lane, tid, p))
            return 200, {"status": "queued", "task": tid, "job": "j" + tid[:4]}
        self.A = Automations(self.k, self.T, self.J, owner=None, dispatch=dispatch, clock=lambda: self.now[0])

    def tearDown(self):
        self.k.db.close(); self.tmp.cleanup()

    def job_def(self, **kw):
        d = {"name": "repo check", "action_kind": "job", "trigger_kind": "cron", "schedule": "0 8 * * *", "tz": "UTC",
             "action": {"kind": "project_check", "params": {"project": "jarvis-hq"}, "node": "laptop"}}
        d.update(kw)
        return d

    def test_preview_creates_nothing_and_lists_side_effects(self):
        p = self.A.preview(self.job_def())
        self.assertEqual(len(p["next_runs"]), 3)
        self.assertTrue(p["next_runs"][0]["utc"].startswith("2026-10-11T08:00"))
        self.assertTrue(p["side_effects"])
        self.assertEqual(self.A.list(), [])

    def test_unattended_definitions_that_need_approval_are_refused(self):
        with self.assertRaises(AutomationError):
            self.A.create(self.job_def(action={"kind": "ui_action", "params": {"agent": "x", "op": "click", "target": "a|b", "text": ""}}))
        with self.assertRaises(AutomationError):
            self.A.create(self.job_def(tz="Mars/Olympus"))
        with self.assertRaises(AutomationError):
            self.A.create(self.job_def(schedule="every day"))
        with self.assertRaises(AutomationError):
            self.A.create(self.job_def(trigger_kind="interval", schedule="5"))

    def test_a_read_only_desktop_listing_may_run_unattended(self):
        a = self.A.create(self.job_def(name="windows", action={"kind": "ui_windows", "params": {}, "node": "laptop"}))
        r = self.A.run_now(a["id"])
        self.assertEqual(r["status"], "fired")
        self.assertEqual(self.k.db.execute("SELECT kind FROM jobs WHERE id=?", (r["run"]["job_id"],)).fetchone()[0], "ui_windows")

    def test_tick_fires_each_slot_once_even_when_called_twice_or_by_two_processes(self):
        a = self.A.create(self.job_def())
        self.now[0] = utc(2026, 10, 11, 8, 0, ) + 5
        B = Automations(self.k, self.T, self.J, clock=lambda: self.now[0])        # a second "process"
        out = [None, None]
        th = [threading.Thread(target=lambda i=i, X=X: out.__setitem__(i, X.tick())) for i, X in enumerate((self.A, B))]
        [t.start() for t in th]; [t.join() for t in th]
        runs = self.A.runs(a["id"])
        self.assertEqual(len(runs), 1)
        self.assertEqual(self.A.tick()["fired"], [])
        self.assertEqual(self.k.db.execute("SELECT COUNT(*) FROM jobs WHERE kind='project_check'").fetchone()[0], 1)
        self.assertGreater(self.A.get(a["id"])["next_run"], self.now[0])

    def test_catchup_policies(self):
        one = self.A.create(self.job_def(name="one", trigger_kind="interval", schedule="600", catchup="one"))
        skip = self.A.create(self.job_def(name="skip", trigger_kind="interval", schedule="600", catchup="skip", grace_s=60))
        every = self.A.create(self.job_def(name="all", trigger_kind="interval", schedule="600", catchup="all", max_runs_per_day=100,
                                           overlap="queue"))
        self.now[0] += 3 * 600 + 300                                 # laptop was off for 3 slots; the last one is 300 s old
        self.A.tick()
        fired = lambda a: [r for r in self.A.runs(a["id"]) if r["status"] not in ("skipped_missed",)]
        self.assertEqual(len(fired(one)), 1)                          # coalesced
        self.assertEqual(len(fired(skip)), 0)                         # 300 s late > 60 s grace: nothing runs, with a record
        self.assertTrue(any(r["status"] == "skipped_missed" for r in self.A.runs(skip["id"])))
        self.assertEqual(len(fired(every)), 3)                        # each missed slot ran

    def test_skip_policy_runs_the_latest_slot_when_it_is_within_grace(self):
        a = self.A.create(self.job_def(name="skip2", trigger_kind="interval", schedule="600", catchup="skip", grace_s=120))
        self.now[0] += 3 * 600 + 30
        self.A.tick()
        st = [r["status"] for r in self.A.runs(a["id"])]
        self.assertEqual(st.count("fired"), 1)
        self.assertIn("skipped_missed", st)

    def test_overlap_and_daily_limit(self):
        a = self.A.create(self.job_def(trigger_kind="interval", schedule="600", max_runs_per_day=2))
        self.now[0] += 610; self.A.tick()
        self.now[0] += 600; self.A.tick()                             # first run still 'fired' (no node finished it): overlap skip
        st = [r["status"] for r in self.A.runs(a["id"])]
        self.assertIn("skipped_overlap", st)
        self.assertEqual(st.count("fired"), 1)

    def test_a_cancelled_job_ends_its_run_so_later_slots_fire(self):
        """Bench D-078 (2026-10-17): settle() ignored 'cancelled'; the run stayed 'fired' and every later slot was skipped."""
        a = self.A.create(self.job_def(trigger_kind="interval", schedule="600", max_runs_per_day=50))
        self.now[0] += 610; self.A.tick()
        jid = self.A.runs(a["id"])[0]["job_id"]
        self.k.db.execute("UPDATE jobs SET state='cancelled' WHERE id=?", (jid,))
        self.A.settle()
        self.now[0] += 600; self.A.tick()
        st = [r["status"] for r in self.A.runs(a["id"])]
        self.assertNotIn("skipped_overlap", st)
        self.assertEqual(st.count("fired"), 1)                        # the new slot; the cancelled one is settled

    def test_failures_pause_the_automation(self):
        a = self.A.create(self.job_def(trigger_kind="interval", schedule="600", pause_after_failures=2, overlap="queue", max_runs_per_day=50))
        for _ in range(2):
            self.now[0] += 610
            self.A.tick()
            jid = self.A.runs(a["id"])[0]["job_id"]
            self.k.db.execute("UPDATE jobs SET state='failed' WHERE id=?", (jid,))
            self.A.settle()
        cur = self.A.get(a["id"])
        self.assertEqual((cur["enabled"], cur["state"]), (0, "paused"))
        self.assertIn("2 failed runs", cur["paused_reason"])
        self.now[0] += 5000
        self.assertEqual(self.A.tick()["fired"], [])                  # paused: nothing fires
        r = self.A.set_enabled(a["id"], True)                         # resuming does not replay what was missed
        self.assertGreater(r["next_run"], self.now[0])

    def test_run_now_is_independent_of_the_schedule(self):
        a = self.A.create(self.job_def())
        nxt = a["next_run"]
        r = self.A.run_now(a["id"])
        self.assertEqual(r["status"], "fired")
        self.assertEqual(self.A.get(a["id"])["next_run"], nxt)

    def test_one_time_runs_once_and_completes(self):
        when = self.now[0] + 3600
        a = self.A.create(self.job_def(trigger_kind="once", schedule=str(when)))
        self.now[0] = when + 2
        self.A.tick(); self.A.tick()
        self.assertEqual(len(self.A.runs(a["id"])), 1)
        self.assertEqual(self.A.get(a["id"])["state"], "completed")

    def test_lab_task_goes_through_the_dispatch_path(self):
        a = self.A.create({"name": "weekly summary", "action_kind": "lab_task", "trigger_kind": "cron", "schedule": "0 9 * * 1", "tz": "Asia/Kolkata",
                           "action": {"goal": "Write the weekly project summary", "instructions": "Summarise the week from the notes provided.",
                                      "criteria": ["a summary file exists"], "agent": "hermes"}})
        self.now[0] = utc(2026, 10, 12, 3, 31)                         # Monday 09:01 in Kolkata
        out = self.A.tick()
        self.assertEqual(len(out["fired"]), 1)
        self.assertEqual(len(self.dispatched), 1)
        ag, lane, tid, p = self.dispatched[0]
        self.assertEqual((ag, lane, p["public_ok"]), ("automation", "public", True))
        self.assertEqual(self.T.get(tid)["source"], "schedule")
        with self.assertRaises(AutomationError):                      # the lab cannot see D:\
            self.A.create(dict(a["action"], goal="x") and {"name": "bad", "action_kind": "lab_task", "trigger_kind": "cron",
                          "schedule": "0 9 * * 1", "action": {"goal": "g", "instructions": "read D:\\secret\\file", "criteria": ["c"]}})

    def test_kill_switch_stops_the_scheduler_and_delete_is_verified(self):
        a = self.A.create(self.job_def(trigger_kind="interval", schedule="600"))
        self.k.kill("test")
        self.now[0] += 700
        self.assertEqual(self.A.tick()["fired"], [])
        self.k.resume()
        self.assertEqual(len(self.A.tick()["fired"]), 1)
        d = self.A.delete(a["id"])
        self.assertTrue(d["verified"])
        self.assertEqual(self.k.db.execute("SELECT COUNT(*) FROM automation_runs").fetchone()[0], 0)


class Agents(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.k = Kernel(os.path.join(self.tmp.name, "d")); self.T = Tasks(self.k)
        self.calls = []

        def dispatch(agent, lane, tid, p):
            self.calls.append((agent, lane, tid, p))
            return 200, {"status": "queued", "task": tid, "job": "jjj"}
        self.G = AgentDefs(self.k, self.T, dispatch=dispatch)

    def tearDown(self):
        self.k.db.close(); self.tmp.cleanup()

    def test_sentence_becomes_a_reviewable_definition(self):
        d = draft_from_sentence("Create a Python software engineer agent that writes code, tests it and fixes failures.")
        self.assertEqual(d["role"], "software-engineer")
        self.assertIn("lab.code", d["tools"])
        self.assertTrue(d["criteria"] and d["permissions"] and d["verification"])
        desc = self.G.describe(d)
        self.assertIn("not_enforced_by_kernel", desc)
        self.assertEqual(self.G.list(), [])                           # reviewing created nothing
        with self.assertRaises(AgentError):
            draft_from_sentence("hi")

    def test_lifecycle_and_confirmation_rules(self):
        d = draft_from_sentence("Make a research analyst agent that compares options and writes a report")
        a = self.G.create(d)
        self.assertEqual(a["state"], "draft")
        with self.assertRaises(AgentError):                           # duplicate name
            self.G.create(d)
        with self.assertRaises(AgentError):                           # unknown tool
            self.G.update(a["id"], {"tools": ["root.shell"]})
        with self.assertRaises(AgentError):                           # private lane is not for specialists
            self.G.update(a["id"], {"lane": "private"})
        a = self.G.set_state(a["id"], "active")
        a = self.G.update(a["id"], {"budget_s": 600}, a["version"])
        self.assertEqual(a["state"], "draft")                         # an edited active agent is reviewed again
        with self.assertRaises(AgentError):
            self.G.update(a["id"], {"budget_s": 700}, expected_version=1)   # stale version
        c = self.G.clone(a["id"], "Research clone")
        self.assertEqual(c["state"], "draft")
        self.G.set_state(a["id"], "active"); self.G.set_state(a["id"], "paused")
        self.G.set_state(a["id"], "terminated")
        with self.assertRaises(AgentError):
            self.G.set_state(a["id"], "active")
        r = self.G.delete(a["id"])
        self.assertTrue(r["verified"])

    def test_test_run_uses_the_guarded_dispatch_with_the_definitions_limits(self):
        a = self.G.create(draft_from_sentence("Create a Python software engineer agent that writes code and tests it"))
        out = self.G.test(a["id"], "Write fizzbuzz in Python with a unit test")
        agent, lane, tid, p = self.calls[0]
        self.assertEqual((agent, lane), (f"agent:{a['name']}", "public"))
        self.assertIn("fizzbuzz", p["instructions"])
        self.assertIn("careful Python software engineer", p["instructions"])
        t = self.T.get(tid)
        self.assertEqual(t["owner"], f"agent:{a['name']}")
        self.assertLessEqual(t["max_attempts"], 2)
        self.assertEqual(self.G.get(a["id"])["tasks"][0]["id"], tid)   # its own task history
        self.assertEqual(out["task"], tid)


if __name__ == "__main__":
    unittest.main()
