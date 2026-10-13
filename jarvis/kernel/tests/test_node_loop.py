"""The laptop node's loop: a queued job is picked up within the short poll interval, while the status report and the file
scan keep their own slower cadence, and the kill switch stops job pickup."""
import os
import sys
import tempfile
import types
import unittest
from unittest import mock

HERE = os.path.dirname(__file__)
sys.path.insert(0, os.path.join(HERE, "..", "..", "node"))
if "jarvis_node.collector" not in sys.modules:
    try:
        import jarvis_node.collector  # noqa: F401
    except Exception:                                      # psutil is not installed on the server: the loop does not need the real one
        import jarvis_node
        fake = types.ModuleType("jarvis_node.collector")
        fake.collect = lambda: {"host": "test"}
        sys.modules["jarvis_node.collector"] = fake
        jarvis_node.collector = fake
from jarvis_node import agent


class Stop(Exception):
    pass


class Loop(unittest.TestCase):
    def run_loop(self, ticks, queue=None, killed_after=None, **kw):
        """Drive agent.run() on a fake clock: one tick per sleep(). Returns the ordered list of endpoint calls with the time."""
        calls, t = [], [1000.0]
        queue = list(queue or [])

        def post(url, path, obj, token=None):
            calls.append((path, t[0]))
            if path == "/report":
                return {"killed": killed_after is not None and t[0] >= killed_after}
            if path == "/jobs/next":
                return {"job": queue.pop(0) if queue else None}
            return {}

        def sleep(s):
            t[0] += s
            if len(calls) and t[0] >= 1000.0 + ticks:
                raise Stop

        tmp = tempfile.mkdtemp()
        tok = os.path.join(tmp, "tok")
        open(tok, "w").write("t")
        started = []
        with mock.patch.object(agent, "_post", post), mock.patch.object(agent, "TOKEN_FILE", tok), \
                mock.patch.object(agent, "collect", lambda: {"host": "test"}), \
                mock.patch.object(agent, "_do_job", lambda url, token, job: started.append((job, t[0]))), \
                mock.patch.object(agent, "_watch_cycle", lambda *a, **k: None):
            try:
                agent.run("http://x", "laptop", clock=lambda: t[0], sleep=sleep, **kw)
            except Stop:
                pass
        return calls, started

    def test_a_job_is_picked_up_within_the_poll_interval_not_the_report_interval(self):
        calls, started = self.run_loop(40, queue=[None, None, {"id": "j1"}], beat=15, poll=3)
        reports = [t for p, t in calls if p == "/report"]
        polls = [t for p, t in calls if p == "/jobs/next"]
        self.assertEqual(reports[:3], [1000.0, 1015.0, 1030.0])        # the report cadence is unchanged
        self.assertGreater(len(polls), len(reports) * 3)               # jobs are asked for far more often
        self.assertEqual(started[0][0]["id"], "j1")
        self.assertLess(started[0][1] - 1000.0, 10)                    # the third ask, ~6 s in, not the next report at 15 s

    def test_nothing_is_picked_up_while_the_kill_switch_is_on(self):
        calls, started = self.run_loop(40, queue=[{"id": "j1"}], killed_after=1000.0, beat=15, poll=3)
        self.assertEqual(started, [])
        self.assertEqual([p for p, _ in calls if p == "/jobs/next"], [])
        self.assertLessEqual(len([1 for p, _ in calls if p == "/report"]), 3)     # and it does not hammer the report endpoint

    def test_once_reports_one_time_and_returns(self):
        calls, _ = self.run_loop(40, once=True)
        self.assertEqual([p for p, _ in calls], ["/report"])


if __name__ == "__main__":
    unittest.main()
