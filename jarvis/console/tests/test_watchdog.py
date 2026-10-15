"""The watchdog's decisions (pure function; nothing is started)."""
import importlib.util
import os
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location("watchdog_t", os.path.join(HERE, "..", "..", "scripts", "watchdog.py"))
wd = importlib.util.module_from_spec(spec)
spec.loader.exec_module(wd)

NOW = 1_000_000.0
LONG_UP = 10 ** 6


class Decide(unittest.TestCase):
    def test_nothing_to_do_when_everything_is_up(self):
        self.assertEqual(wd.decide([], [], {}, NOW, LONG_UP), ([], []))

    def test_right_after_boot_it_waits_for_the_login_task(self):
        actions, gave_up = wd.decide(["main", "memory"], [], {}, NOW, 60)
        self.assertEqual([a[0] for a in actions], ["wait"])
        self.assertEqual(gave_up, [])

    def test_a_down_server_starts_the_stack_once_for_all_of_them(self):
        actions, _ = wd.decide(["speech", "memory"], [], {}, NOW, LONG_UP)
        self.assertEqual(actions, [("start_stack", "stack:memory,speech")])

    def test_the_plugin_runtime_being_down_is_a_stack_problem(self):
        actions, _ = wd.decide(["plugins"], ["doc_tools"], {}, NOW, LONG_UP)
        self.assertEqual([a[0] for a in actions], ["start_stack"])

    def test_a_stopped_plugin_alone_is_started_through_the_runtime(self):
        actions, _ = wd.decide([], ["doc_tools", "learn_vault"], {}, NOW, LONG_UP)
        self.assertEqual(actions, [("start_plugin", "doc_tools"), ("start_plugin", "learn_vault")])

    def test_after_three_attempts_in_an_hour_it_gives_up_instead_of_looping(self):
        heals = {"stack:speech": [NOW - 100, NOW - 900, NOW - 1800]}
        actions, gave_up = wd.decide(["speech"], [], heals, NOW, LONG_UP)
        self.assertEqual(actions, [])
        self.assertEqual(gave_up, ["stack:speech"])

    def test_attempts_older_than_an_hour_do_not_count(self):
        heals = {"stack:speech": [NOW - 4000, NOW - 5000, NOW - 6000, NOW - 100]}
        actions, gave_up = wd.decide(["speech"], [], heals, NOW, LONG_UP)
        self.assertEqual(actions, [("start_stack", "stack:speech")])
        self.assertEqual(gave_up, [])

    def test_a_different_problem_is_not_blocked_by_anothers_history(self):
        heals = {"stack:speech": [NOW - 10, NOW - 20, NOW - 30]}
        actions, _ = wd.decide(["memory"], [], heals, NOW, LONG_UP)
        self.assertEqual(actions, [("start_stack", "stack:memory")])


if __name__ == "__main__":
    unittest.main()
