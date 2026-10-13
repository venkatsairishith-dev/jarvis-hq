"""Lab runner delivery: what leaves ./deliver/ and when. Runs inside the WSL lab box as root (it needs the agent user):
    sudo python3 -m unittest test_lab_runner   (from hq/lab/tests, with lab_runner.py next to it or on PYTHONPATH)"""
import json
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))
try:
    import lab_runner as R
except (ImportError, KeyError):            # Windows (no pwd) or no agent user: this suite is for the lab box
    R = None


@unittest.skipUnless(R is not None and os.geteuid() == 0, "lab box only, as root")
class Deliverables(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.folder = self.tmp.name
        self.d = os.path.join(self.folder, R.DELIVER)

    def tearDown(self):
        self.tmp.cleanup()

    def write(self, name, text="x\n", own=True):
        p = os.path.join(self.d, name)
        with open(p, "w") as fh:
            fh.write(text)
        if own:
            os.chown(p, R.PW.pw_uid, R.PW.pw_gid)
        return p

    def mkdir(self):
        os.mkdir(self.d); os.chown(self.d, R.PW.pw_uid, R.PW.pw_gid)

    def test_required_but_missing_or_empty_fails(self):
        self.assertEqual(R.deliverables(self.folder, False), ([], []))
        self.assertIn("nothing to deliver", R.deliverables(self.folder, True)[1][0])
        self.mkdir()
        self.assertIn("nothing to deliver", R.deliverables(self.folder, True)[1][0])

    def test_only_the_agents_own_plain_text_files_go(self):
        self.mkdir()
        self.write("tool.py", "print(1)\n"); self.write("README.md", "# x\n")
        self.write("evil.sh"); self.write("root.py", own=False)
        os.symlink("/etc/shadow", os.path.join(self.d, "shadow.txt"))
        os.mkdir(os.path.join(self.d, "sub")); os.mkdir(os.path.join(self.d, "__pycache__"))
        files, bad = R.deliverables(self.folder, True)
        self.assertEqual([n for n, _ in files], ["README.md", "tool.py"])
        self.assertEqual(dict(files)["tool.py"], "print(1)\n")
        for name in ("evil.sh", "root.py", "shadow.txt", "sub"):
            self.assertTrue(any(name in b for b in bad), name)

    def test_a_linked_deliver_folder_is_refused(self):
        os.symlink("/etc", self.d)
        self.assertEqual(R.deliverables(self.folder, True)[0], [])
        self.assertIn("plain folder", R.deliverables(self.folder, True)[1][0])

    def test_too_many_or_too_big(self):
        self.mkdir()
        for i in range(R.DELIVER_MAX_FILES + 1):
            self.write(f"f{i}.txt")
        self.assertIn("at most", R.deliverables(self.folder, False)[1][-1])
        self.write("big.txt", "x" * (R.DELIVER_MAX_CHARS + 1))
        self.assertTrue(any("big.txt" in b for b in R.deliverables(self.folder, False)[1]))

    def test_files_that_point_into_the_lab_are_not_delivered(self):
        self.mkdir()
        self.write("test_x.py", 'cwd="/home/agent/work/jobs/abc"\n')
        self.assertIn("lab path", R.deliverables(self.folder, True)[1][0])

    def test_delivered_tests_must_pass_from_a_fresh_folder(self):
        good = [("calc.py", "def add(a, b):\n    return a + b\n"),
                ("test_calc.py", "import unittest, calc\nclass T(unittest.TestCase):\n    def test_add(self):\n        self.assertEqual(calc.add(1, 2), 3)\n")]
        self.assertEqual(R.portable_check(self.folder, good), [])
        needs_workspace = [("test_calc.py", "import unittest, calc\nclass T(unittest.TestCase):\n    def test_add(self):\n        self.assertEqual(calc.add(1, 2), 3)\n")]
        bad = R.portable_check(self.folder, needs_workspace)                 # calc.py stayed behind in the workspace
        self.assertTrue(bad and "fresh folder" in bad[0], bad)
        self.assertEqual(R.portable_check(self.folder, [("notes.md", "# hi\n")]), [])   # no tests: nothing to run

    def test_skills_used_come_from_hermes_usage_counts(self):
        usage = os.path.join(self.folder, "usage.json")
        old = R.USAGE
        try:
            R.USAGE = usage
            self.assertEqual(R.skill_counts(), {})                                   # no file yet
            with open(usage, "w") as fh:
                json.dump({"grill-me": {"view_count": 1}, "code-wiki": {"use_count": 2}}, fh)
            os.chown(usage, R.PW.pw_uid, R.PW.pw_gid)
            before = R.skill_counts()
            with open(usage, "w") as fh:
                json.dump({"grill-me": {"view_count": 2}, "code-wiki": {"use_count": 2}, "adversarial-ux-test": {"view_count": 1}}, fh)
            self.assertEqual(R.skills_used(before), ["adversarial-ux-test", "grill-me"])
        finally:
            R.USAGE = old

    def test_the_delivery_loop_waits_for_the_laptop_and_reports_the_real_path(self):
        calls, polls = [], {"n": 0}

        def api(path, body=None):
            calls.append(path)
            if path.endswith("/deliver"):
                return 200, {"status": "queued", "job": "out" + body["name"][0]}
            polls["n"] += 1
            if polls["n"] == 1:
                return 0, {"error": "TimeoutError"}                       # a blip: keep waiting
            if polls["n"] == 2:
                return 200, {"state": "queued"}
            return 200, {"state": "done", "result": {"ok": True, "saved": "a.py", "folder": "D:\\jarvis\\outputs\\task-x", "chars": 3}}
        old = R.api, R.POLL_S
        R.api, R.POLL_S = api, 0
        try:
            lines, blocker = R.deliver("j", [("a.py", "abc")])
            self.assertEqual(blocker, "")
            self.assertEqual(lines, ["delivered: D:\\jarvis\\outputs\\task-x\\a.py (3 chars) job:outa"])
            R.api = lambda path, body=None: ((200, {"status": "queued", "job": "o"}) if path.endswith("/deliver")
                                             else (200, {"state": "expired", "result": None}))
            lines, blocker = R.deliver("j", [("a.py", "abc")])
            self.assertIn("expired", blocker)
            R.api = lambda path, body=None: (409, {"reason": "deliver only after verification"})
            self.assertIn("refused", R.deliver("j", [("a.py", "abc")])[1])
        finally:
            R.api, R.POLL_S = old


if __name__ == "__main__":
    unittest.main()


@unittest.skipUnless(R is not None and os.geteuid() == 0, "lab box only, as root")
class Integrity(unittest.TestCase):
    """A task must not change the agent's toolchain or start-up files; if it does, it is put back and the attempt fails."""
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); t = self.tmp.name
        self.bin = os.path.join(t, "bin"); os.mkdir(self.bin)
        self.rc = os.path.join(t, ".bashrc")
        with open(self.rc, "w") as fh:
            fh.write("export A=1\n")
        with open(os.path.join(self.bin, "tool"), "w") as fh:
            fh.write("#!/bin/sh\necho real\n")
        self.saved = (R.GUARDED_DIRS, R.GUARDED_FILES, R.QUARANTINE)
        R.GUARDED_DIRS, R.GUARDED_FILES, R.QUARANTINE = [self.bin], [self.rc], os.path.join(t, "q")

    def tearDown(self):
        R.GUARDED_DIRS, R.GUARDED_FILES, R.QUARANTINE = self.saved
        self.tmp.cleanup()

    def test_a_planted_python3_and_an_edited_startup_file_are_put_back(self):
        before = R._fingerprint()
        with open(os.path.join(self.bin, "python3"), "w") as fh:          # what a worker really did
            fh.write("#!/usr/bin/env python3\n# patches print(1+1) to 3\n")
        with open(self.rc, "a") as fh:
            fh.write("alias python3=~/bin/python3\n")
        os.remove(os.path.join(self.bin, "tool"))
        problems = R.guard_restore(before, "t1")
        self.assertFalse(os.path.exists(os.path.join(self.bin, "python3")))
        self.assertEqual(open(self.rc).read(), "export A=1\n")
        self.assertEqual(open(os.path.join(self.bin, "tool")).read(), "#!/bin/sh\necho real\n")
        joined = " ".join(problems)
        for word in ("added", "changed", "deleted"):
            self.assertIn(word, joined)
        self.assertTrue(os.listdir(R.QUARANTINE))                         # the evidence is kept, out of reach

    def test_no_change_no_problem_even_if_rewritten_identically(self):
        before = R._fingerprint()
        with open(self.rc, "w") as fh:
            fh.write("export A=1\n")
        self.assertEqual(R.guard_restore(before, "t2"), [])
