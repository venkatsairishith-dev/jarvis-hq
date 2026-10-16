"""Neko's node side (2026-10-16): cancellation kills what a job started, read-only diagnostics keep screen text local,
repairs back up first and can be undone, the Chrome profile registry, the YouTube flow and the browser tool allowlist."""
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest

HERE = os.path.dirname(__file__)
sys.path.insert(0, os.path.join(HERE, "..", "..", "node"))
from jarvis_node import browserext, diag, jobs, repair, runtime, winbackend  # noqa: E402


class Cancellation(unittest.TestCase):
    def test_cancel_kills_the_running_process_and_the_job_says_cancelled(self):
        out = {}

        def work():
            out["r"] = jobs.run_job({"id": "j1", "kind": "diag", "params": {"op": "processes", "target": ""}})
        orig = runtime.powershell                        # a long check, the same on Windows and on the Linux server
        runtime.powershell = lambda script, env=None, timeout=30: runtime.run([sys.executable, "-c", "import time; time.sleep(60)"], timeout=timeout)
        try:
            t = threading.Thread(target=work)
            t0 = time.time()
            t.start()
            time.sleep(2.0)
            self.assertEqual(runtime.current(), "j1")
            self.assertFalse(runtime.cancel("other-job"))                # only the job that is running
            self.assertTrue(runtime.cancel("j1"))
            t.join(20)
        finally:
            runtime.powershell = orig
        self.assertLess(time.time() - t0, 15)
        self.assertEqual((out["r"]["ok"], out["r"]["cancelled"]), (False, True))
        self.assertIsNone(runtime.current())

    def test_a_cancelled_job_starts_nothing_more(self):
        runtime.begin("j2")
        runtime.cancel("j2")
        with self.assertRaises(runtime.Cancelled):
            runtime.run([sys.executable, "-c", "print(1)"])
        runtime.end()


class Diagnostics(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["JARVIS_OUTPUTS"] = self.tmp.name

    def tearDown(self):
        os.environ.pop("JARVIS_OUTPUTS", None)
        self.tmp.cleanup()

    def fake(self, rows):
        return lambda script, env, timeout: subprocess.CompletedProcess([], 0, json.dumps(rows), "")

    def test_window_text_stays_on_the_laptop(self):
        rows = [{"title": "Sample App", "class": "#32770", "dialog": True, "texts": ["Config error: line 3"], "buttons": ["OK"]}]
        r = diag.run({"op": "window_text", "target": "sampleapp"}, runner=self.fake(rows))
        self.assertTrue(r["ok"])
        self.assertEqual(r["summary"], {"windows": 1, "dialogs": 1})
        self.assertNotIn("Config error", json.dumps(r))                  # nothing of the screen text in the result
        self.assertEqual(json.load(open(r["local_result"], encoding="utf-8"))[0]["texts"], ["Config error: line 3"])
        self.assertTrue(r["local_result"].startswith(os.path.realpath(self.tmp.name)))

    def test_plain_checks_and_refusals(self):
        r = diag.run({"op": "disk", "target": ""}, runner=self.fake([{"drive": "C", "free_gb": 20.0, "total_gb": 100.0}]))
        self.assertEqual(r["rows"][0]["drive"], "C")
        self.assertFalse(diag.run({"op": "services", "target": ""}, runner=self.fake([]))["ok"])
        self.assertFalse(diag.run({"op": "app_info", "target": "KeePass"}, runner=self.fake([]))["ok"])
        self.assertFalse(diag.run({"op": "app_info", "target": "a;b"}, runner=self.fake([]))["ok"])
        self.assertFalse(diag.run({"op": "shell", "target": ""}, runner=self.fake([]))["ok"])


@unittest.skipUnless(os.name == "nt", "Windows paths (the repairs run on the laptop)")
class Repairs(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()                         # under the user profile: a writable place
        self.old = repair.BACKUPS
        repair.BACKUPS = os.path.join(self.tmp.name, "backups")
        self.f = os.path.join(self.tmp.name, "app", "config.json")
        os.makedirs(os.path.dirname(self.f))
        open(self.f, "w", encoding="utf-8").write('{"port": 80x80, "name": "sample"}\n')

    def tearDown(self):
        repair.BACKUPS = self.old
        self.tmp.cleanup()

    def test_edit_backs_up_first_and_restore_undoes_it(self):
        r = repair.edit_file(self.f, '"port": 80x80', '"port": 8080')
        self.assertTrue(r["ok"] and r["verified_on_disk"])
        self.assertEqual(json.load(open(self.f, encoding="utf-8"))["port"], 8080)
        back = repair.restore(r["backup"])
        self.assertTrue(back["ok"])
        self.assertIn("80x80", open(self.f, encoding="utf-8").read())
        again = repair.restore(back["undo"].split()[-1])                 # a restore can itself be undone
        self.assertTrue(again["ok"])
        self.assertEqual(json.load(open(self.f, encoding="utf-8"))["port"], 8080)

    def test_refusals_change_nothing(self):
        self.assertIn("6 times", repair.edit_file(self.f, '"', "'")["error"])
        self.assertFalse(repair.edit_file(r"C:\Windows\win.ini", "a", "b")["ok"])
        self.assertFalse(repair.edit_file(r"D:\jarvis-secrets\x.txt", "a", "b")["ok"])
        self.assertFalse(repair.restore("../../x")["ok"])
        self.assertFalse(repair.restart_app("lsass")["ok"])
        self.assertFalse(repair.restart_app("KeePass")["ok"])
        self.assertFalse(repair.install("x && calc")["ok"])
        self.assertIn("80x80", open(self.f, encoding="utf-8").read())
        self.assertFalse(os.path.isdir(repair.BACKUPS) and os.listdir(repair.BACKUPS))

    def test_a_tampered_backup_is_not_restored(self):
        r = repair.edit_file(self.f, '"port": 80x80', '"port": 8080')
        open(os.path.join(repair.BACKUPS, r["backup"], "file.bak"), "w").write("evil")
        self.assertIn("does not match", repair.restore(r["backup"])["error"])


LOCAL_STATE = {"os_crypt": {"encrypted_key": "NOT-READ"}, "profile": {"last_used": "Default", "info_cache": {
    "Default": {"name": "Venkat", "gaia_name": "Venkat K", "gaia_given_name": "Venkat", "user_name": "venkatgaminggem17@gmail.com"},
    "Profile 1": {"name": "Venkat", "gaia_name": "Venkat AMV", "gaia_given_name": "Venkat", "user_name": "rising.star@gmail.com"},
    "Profile 2": {"name": "Satish", "gaia_name": "Satish", "user_name": "satish@gmail.com"}}}}


class Profiles(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        ls = os.path.join(self.tmp.name, "Local State")
        json.dump(LOCAL_STATE, open(ls, "w"))
        self.reg = browserext.build_registry(ls, os.path.join(self.tmp.name, "reg.json"))

    def tearDown(self):
        self.tmp.cleanup()

    def test_registry_keeps_aliases_and_masked_accounts_only(self):
        text = open(os.path.join(self.tmp.name, "reg.json")).read()
        self.assertNotIn("NOT-READ", text)
        self.assertNotIn("venkatgaminggem17@gmail.com", text)
        self.assertIn("ven***@gmail.com", text)
        d = [p for p in self.reg["profiles"] if p["dir"] == "Default"][0]
        self.assertTrue(d["default"] and "default" in d["aliases"])

    def test_resolve(self):
        self.assertEqual(browserext.resolve("default", self.reg)["dir"], "Default")
        self.assertEqual(browserext.resolve("", self.reg)["dir"], "Default")
        self.assertEqual(browserext.resolve("VenkatAMV", self.reg)["dir"], "Profile 1")
        self.assertEqual(browserext.resolve("satish", self.reg)["dir"], "Profile 2")
        with self.assertRaises(browserext.BrowserError) as e:
            browserext.resolve("Venkat", self.reg)                      # two profiles are called Venkat: ask once
        self.assertIn("ambiguous", str(e.exception))
        with self.assertRaises(browserext.BrowserError):
            browserext.resolve("nobody", self.reg)


SNAPSHOT = """- Page URL: https://www.youtube.com/results?search_query=kalyani+song
- Page Title: kalyani song - YouTube
- link "Sponsored: Buy headphones" [ref=e10]:
  - /url: /watch?v=AAAAAAAAAAA
- link "Shorts" [ref=e11]:
  - /url: /shorts/xyz
- link "Kalyani Raga Song - Full Video by Music Co 2 years ago 5 minutes" [ref=e12]:
  - /url: /watch?v=abcdefghijk
- link "Another Kalyani song" [ref=e13]:
  - /url: "/watch?v=zzzzzzzzzzz&pp=x"
"""


class FakeMcp:
    """Stands in for the Playwright MCP server: a page with one video that starts playing after the play key."""
    calls = []

    def __init__(self, argv, env=None):
        self.argv, self.env, self.pos, self.paused = argv, env, 0.0, True

    def start(self):
        pass

    def handshake(self, timeout=0):
        pass

    def close(self):
        pass

    def call(self, name, args, timeout=0):
        FakeMcp.calls.append(name)
        if name == "browser_snapshot":
            return {"content": [{"type": "text", "text": SNAPSHOT}]}
        if name == "browser_press_key":
            self.paused = False
        if name == "browser_wait_for" and not self.paused:
            self.pos += args.get("time", 1)
        if name == "browser_evaluate":
            v = {"paused": self.paused, "position_s": self.pos, "title": "Kalyani Raga Song - YouTube", "url": "https://www.youtube.com/watch?v=abcdefghijk"}
            return {"content": [{"type": "text", "text": "### Result\n" + json.dumps(v)}]}
        return {"content": [{"type": "text", "text": "- Page URL: https://example.org/\n- Page Title: Example"}]}


class Browser(unittest.TestCase):
    def test_watch_links_skip_ads_and_shorts(self):
        links = browserext.watch_links(SNAPSHOT)
        self.assertEqual([u for _, _, u in links], ["/watch?v=abcdefghijk", "/watch?v=zzzzzzzzzzz"])

    def test_play_is_verified_by_the_moving_video_position(self):
        FakeMcp.calls = []
        prof = {"dir": "Default", "name": "Venkat", "account": "ven***@gmail.com"}
        with browserext.Session(prof, FakeMcp) as s:
            r = browserext.play(s, "Kalyani song")
        self.assertTrue(r["ok"] and r["playing"])
        self.assertIn("Kalyani Raga Song", r["chose"])
        self.assertIn("browser_press_key", FakeMcp.calls)               # the page did not autoplay: YouTube's own play key

    def test_only_allowlisted_tools_and_the_fixed_script(self):
        prof = {"dir": "Default", "name": "Venkat", "account": ""}
        with browserext.Session(prof, FakeMcp) as s:
            for tool in ("browser_cookie_list", "browser_run_code_unsafe", "browser_storage_state", "browser_network_requests"):
                with self.assertRaises(browserext.BrowserError):
                    s.call(tool, {})
            with self.assertRaises(browserext.BrowserError):
                s.call("browser_evaluate", {"function": "() => document.cookie"})

    def test_session_argv_has_no_extra_capabilities(self):
        s = browserext.Session({"dir": "Profile 1", "name": "x", "account": ""}, FakeMcp)
        self.assertIn("--extension", s.s.argv)
        self.assertEqual(s.s.argv[s.s.argv.index("--profile-dir-name") + 1], "Profile 1")
        self.assertFalse(any(a.startswith("--caps") for a in s.s.argv))


class Keys(unittest.TestCase):
    def test_sendkeys_mapping_refuses_raw_syntax(self):
        self.assertEqual(winbackend.sendkeys("ctrl+s"), "^s")
        self.assertEqual(winbackend.sendkeys("alt+f4"), "%{F4}")
        self.assertEqual(winbackend.sendkeys("enter"), "{ENTER}")
        for bad in ("{ENTER}", "ctrl+ctrl+s", "win+r", "^s", "ctrl+"):
            with self.assertRaises(ValueError):
                winbackend.sendkeys(bad)


if __name__ == "__main__":
    unittest.main()
