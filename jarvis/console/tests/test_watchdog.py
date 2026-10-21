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


class Supervision(unittest.TestCase):
    """PRO-D6 / VOI-D5: the desktop plugin (it hosts the PC monitor) and the wake listener (48945) were never watched."""

    def test_the_desktop_plugin_is_watched_and_started_through_the_runtime(self):
        self.assertIn("desktop", wd.PLUGINS)
        actions, _ = wd.decide([], ["desktop"], {}, NOW, LONG_UP)
        self.assertEqual(actions, [("start_plugin", "desktop")])
        heals = {"plugin:desktop": [NOW - 10, NOW - 20, NOW - 30]}
        self.assertEqual(wd.decide([], ["desktop"], heals, NOW, LONG_UP), ([], ["plugin:desktop"]))

    def test_a_wake_listener_that_should_run_is_restarted_with_the_same_heal_limit(self):
        self.assertEqual(wd.decide([], [], {}, NOW, LONG_UP, wake_down=True), ([("start_wake", "wake")], []))
        heals = {"wake": [NOW - 10, NOW - 20, NOW - 30]}
        self.assertEqual(wd.decide([], [], heals, NOW, LONG_UP, wake_down=True), ([], ["wake"]))
        actions, _ = wd.decide([], ["desktop"], {}, NOW, LONG_UP, wake_down=True)     # both heal in the same pass
        self.assertEqual(actions, [("start_plugin", "desktop"), ("start_wake", "wake")])
        actions, _ = wd.decide(["speech"], ["desktop"], {}, NOW, LONG_UP, wake_down=True)   # the stack restart covers it
        self.assertEqual(actions, [("start_stack", "stack:speech")])

    def test_the_wake_listener_is_wanted_only_when_the_owner_had_it_on(self):
        boot = NOW - LONG_UP
        seen = {"boot_at": boot + 3, "at": NOW - 600}
        self.assertTrue(wd.wake_wanted(seen, boot, True, False)[0])
        self.assertFalse(wd.wake_wanted(seen, boot, True, True)[0])                   # the owner's off switch
        self.assertFalse(wd.wake_wanted(seen, boot, False, False)[0])                 # no model: neko-start skips it too
        self.assertFalse(wd.wake_wanted(None, boot, True, False)[0])                  # never ran: -NoWake (or never on)
        self.assertFalse(wd.wake_wanted({"boot_at": boot - 86400}, boot, True, False)[0])   # ran before the last reboot


class OptionalServers(unittest.TestCase):
    """VOI-D11: llama-swap (8080) is optional; when it is not installed the watchdog must not count it as down, retry it, and
    finally give up (which turns the Neko icon to error for good)."""

    def observe(self, exists, dead=("local_models",)):
        saved = wd.http_ok
        wd.http_ok = lambda url, timeout=5.0, auth=None: (not any(wd.SERVERS[n][0] == url for n in dead), b'{"plugins": []}')
        try:
            return wd.observe(exists=exists)
        finally:
            wd.http_ok = saved

    def test_a_server_that_is_not_installed_is_not_down(self):
        down, *_ = self.observe(exists=lambda p: False)
        self.assertNotIn("local_models", down)
        self.assertEqual(wd.optional_missing(lambda p: False), ["local_models"])

    def test_an_installed_server_that_does_not_answer_is_still_down(self):
        down, *_ = self.observe(exists=lambda p: p == wd.OPTIONAL_SERVERS["local_models"])
        self.assertEqual(down, ["local_models"])
        self.assertEqual(wd.optional_missing(lambda p: True), [])

    def test_the_remote_only_profile_starts_no_local_models_so_none_is_expected(self):
        always = lambda p: True                                                   # llama-swap IS installed
        self.assertEqual(wd.optional_missing(always, profile="remote_only"), ["local_models"])
        self.assertEqual(wd.optional_missing(always, profile="vram4"), [])
        self.assertEqual(wd.optional_missing(always, profile=""), [])
        saved = wd.observe
        down, *_ = self.observe(exists=always)                                    # no hardware.json here: profile unknown
        self.assertEqual(down, ["local_models"])
        import json, tempfile
        keep = wd.DATA
        with tempfile.TemporaryDirectory() as d:
            os.makedirs(os.path.join(d, "neko"))
            json.dump({"profile": "remote_only"}, open(os.path.join(d, "neko", "hardware.json"), "w"))
            wd.DATA = d
            try:
                self.assertEqual(wd.hardware_profile(), "remote_only")
                down, *_ = self.observe(exists=always)
            finally:
                wd.DATA = keep
        self.assertEqual(down, [])
        self.assertIs(wd.observe, saved)

    def test_a_core_server_is_never_optional(self):
        down, *_ = self.observe(exists=lambda p: False, dead=("speech", "local_models"))
        self.assertEqual(down, ["speech"])
        self.assertTrue(set(wd.OPTIONAL_SERVERS) <= {"local_models"})              # the privacy gateway stays watched
        self.assertIn("privacy", wd.SERVERS)

    def test_it_checks_the_same_program_that_neko_start_starts(self):
        ps1 = open(os.path.join(os.path.dirname(wd.__file__), "neko-start.ps1"), encoding="utf-8").read()
        self.assertIn(wd.OPTIONAL_SERVERS["local_models"], ps1)

    def test_a_pass_without_the_program_stays_healthy_and_never_gives_up(self):
        import contextlib, io, json, sys, tempfile
        with tempfile.TemporaryDirectory() as d:
            os.makedirs(os.path.join(d, "logs"))
            keep = {k: getattr(wd, k) for k in ("DATA", "LOG", "STATE", "STATUS", "WAKE_OFF", "uptime_seconds", "wake_model_exists",
                                                "stack_script_running", "run_stack_start", "http_ok", "optional_missing")}
            argv, starts = sys.argv, []
            try:
                wd.DATA, wd.LOG = d, os.path.join(d, "logs", "w.log")
                wd.STATE, wd.STATUS, wd.WAKE_OFF = os.path.join(d, "logs", "s.json"), os.path.join(d, "logs", "st.json"), os.path.join(d, "x")
                wd.uptime_seconds, wd.wake_model_exists, wd.stack_script_running = (lambda: LONG_UP), (lambda: False), (lambda: False)
                wd.run_stack_start = lambda problem, no_wake: starts.append(problem)
                body = json.dumps({"plugins": [{"id": p, "status": "running"} for p in wd.PLUGINS],
                                   "kernel": {"reachable": True, "authorized": True}}).encode()
                wd.http_ok = lambda url, timeout=5.0, auth=None: (url != wd.SERVERS["local_models"][0], body)
                wd.optional_missing = lambda exists=None, profile=None: ["local_models"]
                sys.argv = ["watchdog.py"]
                observe_real = wd.observe
                wd.observe = lambda: observe_real(exists=lambda p: False)
                for _ in range(5):                                         # far more than the 3 heals an hour
                    with contextlib.redirect_stdout(io.StringIO()):
                        wd.main()
                st = json.load(open(wd.STATUS, encoding="utf-8"))
            finally:
                wd.observe = observe_real
                for k, v in keep.items():
                    setattr(wd, k, v)
                sys.argv = argv
        self.assertEqual(starts, [])
        self.assertEqual(st["gave_up_on"], [])
        self.assertEqual(st["servers_down"], [])
        self.assertEqual(st["optional_not_installed"], ["local_models"])


class MainPass(unittest.TestCase):
    """One full pass with the probes and the starter replaced: what the watchdog really starts."""

    def setUp(self):
        import tempfile
        self.tmp = tempfile.TemporaryDirectory()
        d = self.tmp.name
        os.makedirs(os.path.join(d, "logs"))
        self.saved = {k: getattr(wd, k) for k in ("DATA", "LOG", "STATE", "STATUS", "WAKE_OFF", "observe", "uptime_seconds",
                                                  "wake_model_exists", "stack_script_running", "run_stack_start")}
        wd.DATA, wd.LOG = d, os.path.join(d, "logs", "watchdog.log")
        wd.STATE, wd.STATUS = os.path.join(d, "logs", "state.json"), os.path.join(d, "logs", "status.json")
        wd.WAKE_OFF = os.path.join(d, "wake.off")
        wd.uptime_seconds, wd.wake_model_exists, wd.stack_script_running = (lambda: LONG_UP), (lambda: True), (lambda: False)
        self.starts = []
        wd.run_stack_start = lambda problem, no_wake: self.starts.append((problem, no_wake))
        self.argv = __import__("sys").argv
        __import__("sys").argv = ["watchdog.py"]

    def tearDown(self):
        for k, v in self.saved.items():
            setattr(wd, k, v)
        __import__("sys").argv = self.argv
        self.tmp.cleanup()

    def pass_with(self, down=(), plugins=(), wake_up=True):
        wd.observe = lambda: (list(down), list(plugins), {"reachable": True, "authorized": True, "killed": False}, wake_up)
        import contextlib, io
        with contextlib.redirect_stdout(io.StringIO()):
            wd.main()
        import json
        return json.load(open(wd.STATUS, encoding="utf-8"))

    def test_a_wake_listener_that_stopped_is_started_again(self):
        self.pass_with(wake_up=True)                                    # seen running since this boot
        st = self.pass_with(wake_up=False)
        self.assertEqual(self.starts, [("wake", False)])
        self.assertFalse(st["healthy"]) ; self.assertTrue(st["wake"]["wanted"])

    def test_a_listener_the_owner_never_started_is_left_off_and_stack_restarts_pass_nowake(self):
        st = self.pass_with(wake_up=False)                              # neko-start.ps1 -NoWake: it never ran
        self.assertEqual(self.starts, [])
        self.assertTrue(st["healthy"]) ; self.assertFalse(st["wake"]["wanted"])
        self.pass_with(down=["speech"], wake_up=False)
        self.assertEqual(self.starts, [("stack:speech", True)])         # -NoWake: the stack restart must not start it

    def test_the_owners_off_switch_wins_even_after_it_ran(self):
        self.pass_with(wake_up=True)
        open(wd.WAKE_OFF, "w").close()
        self.pass_with(wake_up=False)
        self.pass_with(down=["memory"], wake_up=False)
        self.assertEqual(self.starts, [("stack:memory", True)])


class Credentials(unittest.TestCase):
    """Re-review M3 (2026-10-19): liveness probes use the open endpoints; the kernel detail is a private read and sends the token."""

    def test_liveness_urls_are_the_open_ones(self):
        self.assertTrue(wd.SERVERS["console"][0].endswith("/api/health"))
        self.assertTrue(wd.WAKE_URL.endswith("/health"))

    def test_http_ok_sends_the_named_servers_token_from_its_private_file_and_nothing_otherwise(self):
        import tempfile
        import urllib.request
        seen = []

        class R:
            status = 200
            def __enter__(self): return self
            def __exit__(self, *a): return False
            def read(self): return b"{}"
        real = wd.loopback_net.urlopen       # the watchdog's loopback calls go through scripts/loopback_net.py since completion H
        wd.loopback_net.urlopen = lambda req, timeout=None: seen.append(dict(req.header_items())) or R()
        old = os.environ.get("JARVIS_RUN_DIR")
        with tempfile.TemporaryDirectory() as d:
            os.environ["JARVIS_RUN_DIR"] = d
            try:
                wd.control_auth.publish("console", "CONSOLE-TOKEN")
                wd.http_ok("http://127.0.0.1:8799/api/health", auth="console")
                wd.http_ok("http://127.0.0.1:8799/api/health")
                self.assertEqual(seen[0].get("X-jarvis-console"), "CONSOLE-TOKEN")
                self.assertNotIn("X-jarvis-console", seen[1])
            finally:
                wd.loopback_net.urlopen = real
                if old is None:
                    os.environ.pop("JARVIS_RUN_DIR", None)
                else:
                    os.environ["JARVIS_RUN_DIR"] = old


if __name__ == "__main__":
    unittest.main()


class StateFile(unittest.TestCase):
    """Completion J: the heal history survives a crash in the middle of a write and a damaged file."""

    def setUp(self):
        import tempfile
        self.tmp = tempfile.TemporaryDirectory()
        self.old = (wd.STATE, wd.STATUS)
        wd.STATE, wd.STATUS = os.path.join(self.tmp.name, "s", "state.json"), os.path.join(self.tmp.name, "s", "status.json")

    def tearDown(self):
        wd.STATE, wd.STATUS = self.old
        self.tmp.cleanup()

    def test_a_truncated_wrong_shaped_or_missing_state_reads_as_empty(self):
        self.assertEqual(wd.load_state(), {})
        os.makedirs(os.path.dirname(wd.STATE))
        for text in ('{"heals": {"stack": [1.0, 2', "[1, 2]", "null", '{"heals": [1]}', '{"heals": {"a": "x", "b": [1, "z", true, 2.5]}}'):
            with open(wd.STATE, "w") as f:
                f.write(text)
            st = wd.load_state()
            self.assertIsInstance(st.get("heals", {}), dict, text)
        self.assertEqual(wd.load_state()["heals"], {"b": [1, 2.5]})

    def test_a_crash_while_writing_keeps_the_old_history(self):
        wd.save_state({"stack:speech": [NOW - 10]}, None, NOW)
        real = wd.json.dump

        def dies(obj, f, **kw):
            f.write('{"heals": {"sta')
            raise KeyboardInterrupt()
        wd.json.dump = dies
        try:
            with self.assertRaises(KeyboardInterrupt):
                wd.save_state({"stack:speech": [NOW - 10, NOW]}, None, NOW)
        finally:
            wd.json.dump = real
        self.assertEqual(wd.load_state()["heals"], {"stack:speech": [NOW - 10]})

    def test_the_heal_limit_still_holds_after_a_damaged_state_file_is_replaced(self):
        wd.save_state({"stack:speech": [NOW - 100, NOW - 200, NOW - 300]}, None, NOW)
        actions, gave_up = wd.decide(["speech"], [], wd.load_state()["heals"], NOW, LONG_UP)
        self.assertEqual((actions, gave_up), ([], ["stack:speech"]))
