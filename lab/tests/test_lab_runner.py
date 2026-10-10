"""Lab runner delivery: what leaves ./deliver/ and when. Runs inside the WSL lab box as root (it needs the agent user):
    sudo python3 -m unittest test_lab_runner   (from hq/lab/tests, with lab_runner.py next to it or on PYTHONPATH)"""
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
