"""lab_control: which processes 'Take control' freezes, and what the hub is told. Synthetic process tables, no real agents.
Run: python -m unittest discover -s hq/lab/tests (the freeze/thaw/_tail tests need Linux, e.g. the WSL lab box)."""
import json
import os
import signal
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import lab_control as lc  # noqa: E402

A, OTHER = 1001, 0
POSIX = hasattr(signal, "SIGSTOP") and hasattr(os, "O_NOFOLLOW")


def P(ppid, argv, uid=A, state="S", start=100):
    return lc.P(uid, ppid, state, start, argv)


def table():
    return {
        1: P(0, ["/sbin/init"], uid=OTHER),
        10: P(1, ["python3", "demo.py"]),
        11: P(10, ["/venv/bin/python3", "/home/agent/.local/bin/hermes", "chat", "-q", "Fix the tests", "--oneshot"], start=1000),
        12: P(11, ["/venv/bin/python", "/home/agent/lab/jarvis_mcp.py"]),
        13: P(11, ["bash", "-c", "python3 -m unittest"]),                   # Hermes runs commands in their own session
        14: P(13, ["python3", "-m", "unittest"]),
        15: P(12, ["firefox", "--new-window", "https://example.org"]),      # a window the agent opened
        16: P(15, ["/usr/lib/firefox/firefox-bin", "-contentproc"]),
        17: P(11, ["/home/agent/.local/bin/opencode", "run", "--dir", "/w", "write primes.py"]),   # sub-agent
        20: P(1, ["/home/agent/.local/bin/goose", "run", "--no-session", "-t", "Summarise sales.csv"], state="T", start=2000),
        21: P(20, ["/home/agent/.local/bin/goose", "mcp", "computercontroller"]),
        30: P(1, ["xfce4-session"]),
        31: P(30, ["xfce4-terminal"]),
        32: P(31, ["bash"]),
        33: P(32, ["/home/agent/.local/bin/hermes", "chat", "-q", "owner's own run"]),          # owner started it
        34: P(32, ["xdotool", "type", "hello"]),                                                 # owner's xdotool
        40: P(1, ["/usr/bin/cua-driver", "serve"]),                                              # drives the screen
        41: P(1, ["xdotool", "key", "Return"], state="T"),
        50: P(1, ["/root/hermes", "chat", "-q", "root's run"], uid=OTHER),
        60: P(1, ["/home/agent/.local/bin/opencode", "run", "hermes chat about goose run"], start=3000),
        70: P(1, ["sh", "-c", "cd w && /home/agent/.local/bin/hermes chat -q x; ps | grep 'goose run'"]),  # a shell
        71: P(1, ["python3", "-u", "/home/agent/.local/bin/hermes", "status"]),                           # not a task
    }


class Tasks(unittest.TestCase):
    def setUp(self):
        self.uid, lc.UID = lc.UID, A

    def tearDown(self):
        lc.UID = self.uid

    def test_tasks_goals_and_trees(self):
        ts = lc.tasks(table(), boot=0.0, hz=100, now=100.0)
        self.assertEqual([(t["pid"], t["agent"]) for t in ts], [(11, "hermes"), (20, "goose"), (60, "opencode")])
        hermes, goose, opencode = ts
        self.assertEqual(hermes["goal"], "Fix the tests")
        self.assertEqual(goose["goal"], "Summarise sales.csv")
        self.assertEqual(opencode["goal"], "hermes chat about goose run")     # matched on the program, not the prompt
        self.assertEqual(hermes["tree"][0], 11)                               # root first
        self.assertEqual(sorted(hermes["tree"]), [11, 12, 13, 14, 17])        # helpers and sub-agent, no Firefox
        self.assertEqual(hermes["seconds"], 90)
        self.assertTrue(goose["paused"])
        self.assertFalse(hermes["paused"])

    def test_owner_runs_other_users_and_shells_are_not_tasks(self):
        pids = {t["pid"] for t in lc.tasks(table())}
        for pid in (33, 50, 70, 71):
            self.assertNotIn(pid, pids)

    def test_interpreter_flags(self):
        self.assertEqual(lc._kind(P(1, ["python3", "-u", "/x/hermes", "chat", "-q", "hi"])), "hermes")
        self.assertEqual(lc._kind(P(1, ["node", "/x/opencode", "run", "hi"])), "opencode")
        self.assertIsNone(lc._kind(P(1, ["/x/goose", "session"])))

    def test_screen_drivers(self):
        self.assertEqual(sorted(lc.screen_drivers(table())), [21, 40, 41])  # not the owner's xdotool (34)

    @unittest.skipUnless(POSIX, "needs POSIX signals")
    def test_freeze_skips_windows_stopped_and_owner(self):
        sent = []
        done = lc.freeze(table(), kill=lambda pid, sig: sent.append((pid, sig)))
        self.assertEqual(done, [11, 12, 13, 17, 14, 21, 60, 40])              # root first; 20 and 41 already stopped
        self.assertTrue(all(sig == signal.SIGSTOP for _, sig in sent))
        for pid in (15, 16, 33, 34, 50):
            self.assertNotIn(pid, done)

    @unittest.skipUnless(POSIX, "needs POSIX signals")
    def test_thaw_children_first_and_only_agent_processes(self):
        sent = []
        lc.thaw(table(), [11, 12, 50, 999], kill=lambda pid, sig: sent.append((pid, sig)))
        self.assertEqual(sent, [(12, signal.SIGCONT), (11, signal.SIGCONT)])


class Steps(unittest.TestCase):
    def setUp(self):
        self.uid, lc.UID = lc.UID, A

    def tearDown(self):
        lc.UID = self.uid

    def test_activity_ignores_bad_lines(self):
        lines = ['{"ts": 5, "time": "10:00:05", "agent": "hermes", "tool": "desktop_open", "detail": "https://a", "ppid": 11}',
                 "not json", "[1, 2]", '{"tool": "save_output", "ppid": "x"}']
        acts = lc.activity(lines)
        self.assertEqual(len(acts), 1)
        self.assertEqual(acts[0]["action"], "desktop_open")

    def test_hermes_steps_skip_jarvis_tools(self):
        lines = ["2026-10-09 17:34:37,840 INFO [20261009_173034_b18192] agent.tool_executor: tool terminal completed (0.07s, 63 chars)",
                 "2026-10-09 17:34:40,000 INFO [20261009_173034_b18192] agent.tool_executor: tool mcp__jarvis__save_output completed (1s)",
                 "2026-10-09 17:34:41,000 INFO [x] something else"]
        steps = lc.hermes_steps(lines)
        self.assertEqual([(s["time"], s["action"], s["session"]) for s in steps], [("17:34:37", "terminal", "20261009_173034_b18192")])

    def test_last_step_by_process_and_hermes_session(self):
        started = time.mktime(time.strptime("20261009_173030", "%Y%m%d_%H%M%S"))
        task = {"agent": "hermes", "started": started, "tree": [11, 12]}
        acts = [{"ts": started + 10, "time": "17:30:40", "action": "desktop_open", "detail": "x", "ppid": 11},
                {"ts": started + 99, "time": "17:32:09", "action": "save_output", "detail": "other task", "ppid": 77}]
        hsteps = lc.hermes_steps([
            "2026-10-09 17:31:00,000 INFO [20261009_173034_b18192] agent.tool_executor: tool terminal completed (1s)",
            "2026-10-09 17:33:00,000 INFO [20261009_120000_aaaaaa] agent.tool_executor: tool read_file completed (1s)"])
        self.assertEqual(lc.last_step(task, acts, hsteps), {"time": "17:31:00", "action": "terminal", "detail": ""})
        self.assertIsNone(lc.last_step({"agent": "goose", "started": started, "tree": [20]}, acts, hsteps))


@unittest.skipUnless(POSIX, "needs O_NOFOLLOW")
class Tail(unittest.TestCase):
    def setUp(self):
        self.uid, lc.UID = lc.UID, os.getuid()
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = tmp.name

    def tearDown(self):
        lc.UID = self.uid

    def test_reads_own_file_refuses_links_and_other_owners(self):
        path = os.path.join(self.dir, "a.jsonl")
        with open(path, "w") as fh:
            fh.write("".join(json.dumps({"n": i}) + "\n" for i in range(50)))
        self.assertEqual(len(lc._tail(path)), 50)
        cut = lc._tail(path, limit=40)
        self.assertTrue(all(line.startswith("{") and line.endswith("}") for line in cut))   # partial first line dropped
        link = os.path.join(self.dir, "link.jsonl")
        os.symlink(path, link)
        self.assertEqual(lc._tail(link), [])
        lc.UID = os.getuid() + 1
        self.assertEqual(lc._tail(path), [])


if __name__ == "__main__":
    unittest.main()
