"""File-arrival triggers: the node's watcher (debounce, baseline, restart reconcile) and the kernel's event-driven runs."""
import json
import os
import sys
import tempfile
import time
import unittest

HERE = os.path.dirname(__file__)
sys.path.insert(0, os.path.join(HERE, ".."))
sys.path.insert(0, os.path.join(HERE, "..", "..", "node"))
from jarvis_kernel.automations import Automations, AutomationError
from jarvis_kernel.jobs import Jobs
from jarvis_kernel.nodes import record_watch_events
from jarvis_kernel.store import Kernel
from jarvis_kernel.tasks import Tasks
from jarvis_node.watch import Watcher


class FakeClock:
    def __init__(self):
        self.t = 1_000_000.0

    def __call__(self):
        return self.t


class RepoConfig(unittest.TestCase):
    def test_the_shipped_allowlist_is_valid_json_and_names_downloads(self):
        from jarvis_node.watch import load_watches
        shipped = os.path.join(os.path.dirname(__file__), "..", "..", "config", "watches.json")
        if not os.path.exists(os.path.normpath(shipped)) and os.name != "nt":
            self.skipTest("this is the server's layout: watched folders are the laptop's config")
        w = load_watches()
        self.assertIn("downloads", w)
        self.assertIn("nt", w["downloads"]["path"])


class NodeWatcher(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = os.path.join(self.tmp.name, "dl")
        os.makedirs(self.dir)
        self.state = os.path.join(self.tmp.name, "state", "watch.json")
        self.clock = FakeClock()
        self.spec = {"downloads": {"path": {os.name: self.dir}, "ignore": [".crdownload", ".tmp"], "depth": 1}}

    def tearDown(self):
        self.tmp.cleanup()

    def mk(self):
        return Watcher(self.spec, self.state, clock=self.clock, settle=2.0)

    def write(self, name, text="x"):
        with open(os.path.join(self.dir, name), "w") as fh:
            fh.write(text)

    def test_first_run_is_a_baseline_and_a_new_file_is_reported_once_after_it_settles(self):
        self.write("old.pdf")
        w = self.mk()
        self.assertEqual(w.scan(), {})                                   # baseline: the existing file is not an arrival
        self.write("new.pdf", "hello")
        self.assertEqual(w.scan(), {})                                   # just seen: not settled yet
        self.clock.t += 3
        r = w.scan()
        self.assertEqual([f["rel"] for f in r["downloads"]], ["new.pdf"])
        self.assertEqual([f["rel"] for f in w.scan()["downloads"]], ["new.pdf"])   # not acknowledged yet: offered again, never lost
        w.ack(r)
        self.clock.t += 3
        self.assertEqual(w.scan(), {})                                   # never twice

    def test_a_file_still_being_written_waits_and_temp_names_are_ignored(self):
        w = self.mk()
        w.scan()
        self.write("big.zip", "a")
        self.write("big.zip.crdownload", "partial")
        w.scan()
        self.clock.t += 3
        self.write("big.zip", "ab")                                      # changed since the last scan: the clock restarts
        self.assertEqual(w.scan(), {})
        self.clock.t += 3
        r = w.scan()
        self.assertEqual([f["rel"] for f in r["downloads"]], ["big.zip"])
        w.ack(r)

    def test_files_that_arrived_while_the_node_was_off_are_found_at_start(self):
        w = self.mk()
        w.scan()                                                          # baseline saved to disk
        self.write("while-asleep.docx")
        w2 = self.mk()                                                    # a new process: reads the saved baseline
        w2.scan()
        self.clock.t += 3
        r = w2.scan()
        self.assertEqual([f["rel"] for f in r["downloads"]], ["while-asleep.docx"])

    def test_subfolders_beyond_depth_and_hidden_files_are_not_watched(self):
        w = self.mk()
        w.scan()
        os.makedirs(os.path.join(self.dir, "a", "b"))
        with open(os.path.join(self.dir, "a", "b", "deep.txt"), "w") as fh:
            fh.write("x")
        self.write(".hidden")
        self.clock.t += 3
        w.scan()
        self.clock.t += 3
        self.assertEqual(w.scan(), {})


class KernelEvents(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.k = Kernel(os.path.join(self.tmp.name, "d"))
        self.T, self.J = Tasks(self.k), Jobs(self.k)
        self.now = [time.time()]                      # events are stamped with the real clock, so the test clock starts there
        self.cmds = []

        class Owner:
            busy = type("L", (), {"locked": staticmethod(lambda: False)})()

            def command(inner, text, source="console"):
                self.cmds.append(text)
                return {"id": "c-" + str(len(self.cmds))}
        self.A = Automations(self.k, self.T, self.J, owner=Owner(), clock=lambda: self.now[0])

    def tearDown(self):
        self.k.db.close(); self.tmp.cleanup()

    def definition(self, **kw):
        d = {"name": "sort downloads", "action_kind": "command", "trigger_kind": "event", "event_spec": {"watch": "downloads", "glob": "*.pdf"},
             "action": {"text": "File the new document in the right folder"}}
        d.update(kw)
        return d

    def test_event_trigger_validates_and_previews_without_next_runs(self):
        p = self.A.preview(self.definition())
        self.assertEqual(p["next_runs"], [])
        self.assertIn("downloads", p["side_effects"][0])
        for bad in ({"watch": "bad name!", "glob": "*"}, {"watch": "downloads", "glob": "../*"}, {}):
            with self.assertRaises(AutomationError):
                self.A.create(self.definition(event_spec=bad))

    def test_a_matching_file_runs_once_even_if_reported_twice(self):
        a = self.A.create(self.definition())
        self.assertIsNone(a["next_run"])
        self.now[0] += 10
        ev = {"watch": "downloads", "files": [{"rel": "invoice.pdf", "size": 10, "mtime_ns": 5}, {"rel": "photo.png", "size": 3, "mtime_ns": 6}]}
        self.assertEqual(record_watch_events(self.k, "laptop", ev), 2)
        self.assertEqual(record_watch_events(self.k, "laptop", ev), 0)         # the same report again is a no-op
        out = self.A.tick()
        self.assertEqual(len(out["events"]), 1)                                # only the .pdf matches the glob
        self.assertEqual(len(self.cmds), 1)
        self.assertIn("invoice.pdf", self.cmds[0])
        self.assertEqual(self.A.tick()["events"], [])
        runs = self.A.runs(a["id"])
        self.assertEqual((len(runs), runs[0]["source"]), (1, "event"))

    def test_other_watches_and_files_from_before_the_automation_do_nothing(self):
        record_watch_events(self.k, "laptop", {"watch": "downloads", "files": [{"rel": "early.pdf", "size": 1, "mtime_ns": 1}]})
        self.now[0] += 5
        self.A.create(self.definition())                                       # created after the file arrived
        record_watch_events(self.k, "laptop", {"watch": "desktop", "files": [{"rel": "x.pdf", "size": 1, "mtime_ns": 2}]})
        self.now[0] += 5
        self.assertEqual(self.A.tick()["events"], [])
        self.assertEqual(self.cmds, [])

    def test_daily_limit_and_pause_stop_event_runs(self):
        a = self.A.create(self.definition(max_runs_per_day=1))
        self.now[0] += 5
        record_watch_events(self.k, "laptop", {"watch": "downloads", "files": [{"rel": "a.pdf", "size": 1, "mtime_ns": 1}, {"rel": "b.pdf", "size": 1, "mtime_ns": 2}]})
        self.A.tick()
        self.assertEqual(len(self.cmds), 1)
        self.assertIn("skipped_limit", [r["status"] for r in self.A.runs(a["id"])])
        self.A.set_enabled(a["id"], False)
        record_watch_events(self.k, "laptop", {"watch": "downloads", "files": [{"rel": "c.pdf", "size": 1, "mtime_ns": 3}]})
        self.A.tick()
        self.assertEqual(len(self.cmds), 1)

    def test_event_report_validation(self):
        bad = [{"watch": "x;y", "files": []}, {"watch": "downloads", "files": "no"},
               {"watch": "downloads", "files": [{"rel": "../../etc/passwd", "size": 1, "mtime_ns": 1}]},
               {"watch": "downloads", "files": [{"rel": "a", "size": "big", "mtime_ns": 1}]}]
        for ev in bad:
            self.assertEqual(record_watch_events(self.k, "laptop", ev), 0, str(ev))


if __name__ == "__main__":
    unittest.main()
