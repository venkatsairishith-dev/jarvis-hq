"""Lab isolation: independent red-team findings (LAB-RT, 2026-10-19) and their regression tests.

Every test here failed on the code as it was before the red-team pass (see the report: attack table). They are grouped by the question they answer:
  - root must never follow a link the code under test (or the worker) can place: logs, work folders, restored start-up files;
  - what the supervisor accepts as a test run: duplicate ids, non-finite numbers, file names with a newline, strict types of the identity;
  - what one test can leave behind for the next (shared world-writable folders, SysV / POSIX shared memory);
  - the boundary itself: capabilities, private folders, a verifier identity that is really separate, the verifier's umask.
Pure tests run anywhere. Tests marked NEEDS_REAL need root and the users agent, jarvis-sandbox, jarvis-verify (a throw-away container, see
test_isolation.py).
"""
import errno
import json
import os
import pwd
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))
sys.path.insert(0, os.path.join(HERE, "..", "..", "kernel"))
import lab_runner as R
import lab_isolation as ISO
from jarvis_kernel.tasks import isolation_problem

LINUX = os.path.isdir("/proc") and hasattr(os, "killpg")
REAL = LINUX and os.geteuid() == 0 and all(ISO._getpw(n) is not None for n in ("agent", ISO.SANDBOX_USER, ISO.VERIFY_USER))
NEEDS_REAL = unittest.skipUnless(REAL, "needs root and the users agent, jarvis-sandbox, jarvis-verify (a throw-away container)")
ME = pwd.getpwuid(os.getuid())


def plant(target, path):
    """A symlink as the worker (not root) would leave it: root-owned links are the administrator's and are followed by design."""
    os.symlink(target, path)
    if os.geteuid() == 0:
        os.lchown(path, 65534, 65534)


def mod(*tests, header="import os, sys, unittest\n"):
    out = header + "class T(unittest.TestCase):\n"
    for name, body in tests:
        out += f"    def {name}(self):\n" + "".join("        " + ln + "\n" for ln in body.strip("\n").split("\n"))
    return out


# ---------------------------------------------------------------------------------------------------------------- root and links
class RootNeverFollowsLinks(unittest.TestCase):
    """The worker owns its folder tree. Whatever root does inside it must not follow a link the worker planted (a symlink named like the next
    log file made the root runner overwrite any root-owned file, the runner's own module included)."""

    def setUp(self):
        self.base = tempfile.mkdtemp(prefix="rt-links-")
        self.target = os.path.join(self.base, "root-owned-target")
        with open(self.target, "w") as fh:
            fh.write("ORIGINAL\n")
        self.folder = os.path.join(self.base, "jobs", "task")
        os.makedirs(self.folder)

    def tearDown(self):
        shutil.rmtree(self.base, ignore_errors=True)

    def test_a_planted_symlink_log_is_not_followed(self):
        plant(self.target, os.path.join(self.folder, "work-2.log"))
        fh = R.open_log(self.folder, "work-2.log")
        fh.write("agent-chosen text\n")
        fh.close()
        self.assertEqual(open(self.target).read(), "ORIGINAL\n")
        self.assertFalse(os.path.islink(os.path.join(self.folder, "work-2.log")))        # replaced by a real file of the runner's own
        self.assertEqual(open(os.path.join(self.folder, "work-2.log")).read(), "agent-chosen text\n")

    def test_a_hardlinked_log_is_not_written_through(self):
        os.link(self.target, os.path.join(self.folder, "verify-1.log"))
        fh = R.open_log(self.folder, "verify-1.log")
        fh.write("x\n")
        fh.close()
        self.assertEqual(open(self.target).read(), "ORIGINAL\n")

    def test_a_symlink_in_the_path_of_the_folder_is_refused(self):
        real = os.path.join(self.base, "elsewhere")
        os.makedirs(real)
        link = os.path.join(self.base, "link-to-elsewhere")
        plant(real, link)
        with self.assertRaises(OSError):
            R.open_log(os.path.join(link, "task"), "work-1.log")
        with self.assertRaises(OSError):
            R.ensure_folder(os.path.join(link, "task"), ME)
        self.assertEqual(os.listdir(real), [])                                          # nothing was created through the link

    def test_ensure_folder_does_not_chown_through_a_symlinked_folder(self):
        victim = os.path.join(self.base, "victim-dir")
        os.makedirs(victim)
        os.chmod(victim, 0o700)
        before = os.stat(victim)
        plant(victim, os.path.join(self.base, "jobs", "linked-task"))
        with self.assertRaises(OSError):
            R.ensure_folder(os.path.join(self.base, "jobs", "linked-task"), ME)
        after = os.stat(victim)
        self.assertEqual((before.st_uid, before.st_gid, before.st_mode), (after.st_uid, after.st_gid, after.st_mode))

    def test_ensure_folder_makes_missing_folders_and_hands_them_over(self):
        path = os.path.join(self.base, "new", "deeper", "task")
        R.ensure_folder(path, ME)
        self.assertTrue(os.path.isdir(path))
        self.assertEqual(os.stat(path).st_uid, ME.pw_uid)
        R.ensure_folder(path, ME)                                                       # idempotent

    def test_a_restored_startup_file_is_not_written_through_a_symlink(self):
        victim = os.path.join(self.base, "victim")
        open(victim, "w").write("SECRET-ROOT-FILE\n")
        guarded = os.path.join(self.base, "guarded")
        os.makedirs(guarded)
        path = os.path.join(guarded, "bashrc")
        os.symlink(victim, path)                                                        # what a racing agent process leaves at the moment of the restore
        with self.assertRaises(OSError):
            R.write_file_nofollow(path, b"restored\n", 0o644, ME)
        self.assertEqual(open(victim).read(), "SECRET-ROOT-FILE\n")
        os.unlink(path)
        R.write_file_nofollow(path, b"restored\n", 0o644, ME)
        self.assertEqual(open(path, "rb").read(), b"restored\n")

    def test_the_fingerprint_reads_do_not_follow_a_swapped_in_symlink(self):
        victim = os.path.join(self.base, "victim")
        open(victim, "w").write("SECRET-ROOT-FILE\n")
        path = os.path.join(self.base, "guarded-file")
        plant(victim, path)
        with self.assertRaises(OSError):
            R.read_file_nofollow(path)
        open(path + "2", "w").write("plain")
        self.assertEqual(R.read_file_nofollow(path + "2"), b"plain")


@NEEDS_REAL
class RunAgentDoesNotFollowLinks(unittest.TestCase):
    """The real run_agent, as root, with the worker's symlink in place (the exact attack: ln -s <runner module> work-2.log)."""

    def test_the_worker_cannot_make_root_overwrite_a_file_through_the_log_name(self):
        base = tempfile.mkdtemp(prefix="rt-agent-")
        os.chmod(base, 0o755)
        target = os.path.join(base, "runner-module.py")
        open(target, "w").write("ORIGINAL RUNNER CODE\n")
        folder = os.path.join(base, "task")
        os.mkdir(folder)
        os.chown(folder, R.PW.pw_uid, R.PW.pw_gid)
        subprocess.run(["runuser", "-u", "agent", "--", "ln", "-s", target, os.path.join(folder, "work-2.log")], check=True)
        saved = (R.command, R.api)
        R.command = lambda kind, prompt, f, max_turns=60: ["sh", "-c", "echo 'attacker-chosen: PWNED'"]
        R.api = lambda path, body=None: (200, {"state": "running"})
        try:
            rc, _, _ = R.run_agent("job", "hermes", "p", folder, 30, "work-2.log")
        finally:
            R.command, R.api = saved
            content = open(target).read()
            shutil.rmtree(base, ignore_errors=True)
        self.assertEqual(rc, 0)
        self.assertEqual(content, "ORIGINAL RUNNER CODE\n")


# ---------------------------------------------------------------------------------------------------------------- what is accepted
class StrictInputs(unittest.TestCase):
    def test_non_finite_numbers_in_any_spelling_are_refused(self):
        for text in ('{"a": 1e999}', '{"a": -1e999}', '[1E400]', '{"a": {"b": 1e999}}', '{"a": NaN}', '{"a": Infinity}', '{"a": -Infinity}'):
            with self.subTest(text=text):
                with self.assertRaises(ISO.StrictJSONError):
                    ISO.strict_loads(text)
                with self.assertRaises(ISO.StrictJSONError):
                    ISO.strict_raw_decode(text)
        self.assertEqual(ISO.strict_loads('{"a": 1.5e3, "b": -0.0, "c": 10}'), {"a": 1500.0, "b": -0.0, "c": 10})

    def test_deeply_nested_json_inside_the_size_limit_is_a_refusal_not_a_crash(self):
        """RecursionError is not a ValueError: the verifier's output (or a record) nested a few thousand deep used to abort the whole attempt."""
        deep = "[" * 100000 + "]" * 100000
        deep_obj = '{"a":' * 50000 + "1" + "}" * 50000
        for text in (deep, deep_obj):
            with self.subTest(n=len(text)):
                with self.assertRaises(ISO.StrictJSONError):
                    ISO.strict_loads(text, 400_000)
                with self.assertRaises(ISO.StrictJSONError):
                    ISO.strict_raw_decode(text)
        want = {"nonce": "n", "task": "a" * 12, "job": "b" * 12, "attempt": 1}
        got, why, sha = R.parse_channel(("JARVIS-VERDICT: " + deep_obj).encode(), want)
        self.assertIsNone(got)
        self.assertIn("not valid JSON", why)

    def test_a_corrupt_usage_file_cannot_abort_a_job(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "usage.json")
            keep = (R.USAGE, R.safe_read)
            try:
                R.USAGE = path
                for body in ('{"a": {"view_count": "x"}, "b": {"use_count": [1]}, "c": {"view_count": 1e999}, "d": {"view_count": 2, "use_count": 3}}',
                             "[" * 100000, "[1,2]", "null"):
                    R.safe_read = lambda p, limit=0, _b=body: _b
                    with self.subTest(body=body[:30]):
                        out = R.skill_counts()
                        self.assertIsInstance(out, dict)
                R.safe_read = lambda p, limit=0: body0
                body0 = '{"d": {"view_count": 2, "use_count": 3}, "x": {"view_count": "x"}}'
                self.assertEqual(R.skill_counts(), {"d": 5})
            finally:
                R.USAGE, R.safe_read = keep

    def test_a_deliverable_name_may_not_end_in_a_newline_or_hide_a_second_line(self):
        for name in ("test_a.py\n", "a.md\n", "a\n.md", "test_a.py\r", "a\x00.py", "a/b.py", "../a.py", ".hidden.py", " a.py"):
            with self.subTest(name=name):
                self.assertIsNone(R.DELIVER_NAME.match(name), repr(name))
        for name in ("test_a.py", "notes 1.md", "data-2.json", "A_b.c.txt"):
            self.assertIsNotNone(R.DELIVER_NAME.match(name), name)
        self.assertTrue(".." in "a..py")                                      # the double dot is refused by deliverables() itself (".." in name)

    def test_the_identity_of_a_verdict_is_compared_by_type_as_well_as_value(self):
        want = {"nonce": "n" * 32, "task": "t", "job": "j", "attempt": 1}
        good = {"nonce": "n" * 32, "task": "t", "job": "j", "attempt": 1, "passed": True}
        self.assertEqual(R.check_verdict(good, want), "")
        for bad in ({"attempt": True}, {"attempt": 1.0}, {"attempt": "1"}, {"job": ["j"]}, {"task": 1}, {"nonce": ["x"]}):
            with self.subTest(bad=bad):
                self.assertNotEqual(R.check_verdict({**good, **bad}, want), "")

    def test_two_tests_that_report_the_same_id_void_the_collection(self):
        """A failing test could name itself like a passing one: the id was then run twice, the failing test never, and the run counted 'all passed'."""
        recs = [{"e": "c", "id": "m.T.test_easy", "seq": 0, "pid": 7}, {"e": "c", "id": "m.T.test_easy", "seq": 1, "pid": 7},
                {"e": "collected", "n": 2, "seq": 2, "pid": 7}]
        ids, why = R._collected(recs)
        self.assertIsNone(ids)
        self.assertIn("unique", why)
        step = {"records": recs, "bad": [], "exit": 0, "timed_out": False, "output": ""}
        probs, ids = R.judge_collect(dict(step, signal=0))
        self.assertTrue(probs)
        self.assertIsNone(ids)
        probs, facts = R.judge_harness(step, None)
        self.assertTrue(probs)


# ---------------------------------------------------------------------------------------------------------------- the boundary
class FullNeedsASeparateVerifier(unittest.TestCase):
    def setUp(self):
        self.saved = ISO.VERIFIER_TOOLS

    def tearDown(self):
        ISO.VERIFIER_TOOLS = self.saved

    def boundary(self, verify):
        sb = pwd.struct_passwd(("sb", "x", 995, 995, "", "/", "/"))
        agent = pwd.struct_passwd(("agent", "x", 1000, 1000, "", "/", "/"))
        b = ISO.Boundary("identity", [], sandbox=sb, verify=verify, agent=agent)
        b.self_test, b.verifier_tools = "ok", True
        return b

    def test_without_a_verify_user_the_owners_statement_does_not_make_the_mode_full(self):
        b = self.boundary(None)
        self.assertEqual(b.mode(), "identity")
        blk = b.describe(processes=3, granularity="id", cpu_seconds=0.1)
        blk["runner_uid"] = 0
        self.assertFalse(blk["separate_verifier_identity"])
        self.assertEqual(isolation_problem(blk), "")                  # a consistent 'identity' block ...
        self.assertEqual(blk["mode"], "identity")

    def test_with_a_verify_user_it_is_full(self):
        b = self.boundary(pwd.struct_passwd(("vf", "x", 1001, 1001, "", "/", "/")))
        self.assertEqual(b.mode(), "full")
        blk = b.describe(processes=3, granularity="id", cpu_seconds=0.1)
        blk["runner_uid"] = 0
        self.assertEqual(isolation_problem(blk), "")

    def test_the_kernel_refuses_a_full_block_whose_verifier_is_not_separate(self):
        b = self.boundary(pwd.struct_passwd(("vf", "x", 1001, 1001, "", "/", "/")))
        blk = b.describe(processes=3, granularity="id", cpu_seconds=0.1)
        blk["runner_uid"] = 0
        self.assertEqual(isolation_problem(blk), "")
        self.assertNotEqual(isolation_problem(dict(blk, separate_verifier_identity=False)), "")
        self.assertNotEqual(isolation_problem(dict(blk, verify_uid=blk["sandbox_uid"])), "")      # the verifier IS the sandbox identity


class PurgeHelpers(unittest.TestCase):
    def test_purge_removes_only_what_the_identity_owns(self):
        base = tempfile.mkdtemp(prefix="rt-purge-")
        try:
            mine = os.path.join(base, "mine")
            os.makedirs(os.path.join(mine, "sub"))
            open(os.path.join(mine, "sub", "f"), "w").write("x")
            os.symlink("/etc/passwd", os.path.join(mine, "link"))
            open(os.path.join(base, "file"), "w").write("y")
            uid = os.getuid() or 54321                                    # never root: a root run uses a stand-in identity
            if os.geteuid() == 0:
                for dp, dn, fn in os.walk(base):
                    for x in dn + fn:
                        os.lchown(os.path.join(dp, x), uid, uid)
            n = ISO.purge_uid_files(uid, dirs=[base])
            self.assertEqual(os.listdir(base), [])                      # everything here is owned by this uid
            self.assertGreaterEqual(n, 4)
            self.assertTrue(os.path.exists("/etc/passwd"))              # the link was removed, not followed
            other = uid + 12345
            os.makedirs(os.path.join(base, "d"))
            open(os.path.join(base, "d", "f"), "w").write("z")
            if os.geteuid() == 0:
                os.lchown(os.path.join(base, "d"), uid, uid)
                os.lchown(os.path.join(base, "d", "f"), uid, uid)
            self.assertEqual(ISO.purge_uid_files(other, dirs=[base]), 0)
            self.assertEqual(sorted(os.listdir(base)), ["d"])
            self.assertEqual(ISO.purge_uid_files(uid, dirs=[os.path.join(base, "nonexistent")]), 0)
        finally:
            shutil.rmtree(base, ignore_errors=True)

    def test_purge_never_touches_root_or_an_empty_uid_argument(self):
        base = tempfile.mkdtemp(prefix="rt-purge0-")
        try:
            open(os.path.join(base, "f"), "w").write("x")
            self.assertEqual(ISO.purge_uid_files(0, dirs=[base]), 0)
            self.assertEqual(ISO.purge_uid_files(None, dirs=[base]), 0)
            self.assertEqual(os.listdir(base), ["f"])
        finally:
            shutil.rmtree(base, ignore_errors=True)


class RealBase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.saved = (ISO.REQUEST, ISO.VERIFIER_TOOLS)
        ISO.REQUEST = "identity"
        cls.b = ISO.resolve(R.PW)
        ISO.run_self_test(cls.b)

    @classmethod
    def tearDownClass(cls):
        ISO.REQUEST, ISO.VERIFIER_TOOLS = cls.saved

    def setUp(self):
        self.assertEqual(self.b.self_test, "ok", self.b.self_test)
        R.CURRENT_BOUNDARY = self.b

    def tearDown(self):
        R.CURRENT_BOUNDARY = None

    def run_tests(self, *tests, name="test_a.py"):
        return R.portable_run("/tmp", [(name, mod(*tests))])


@NEEDS_REAL
class NothingSurvivesBetweenTests(RealBase):
    """One test must not prepare the ground for the next, nor leave anything on the lab box: every world-writable place and SysV segment is
    emptied of the sandbox identity's belongings after each step."""

    def test_files_in_shared_folders_and_shared_memory_do_not_reach_the_next_test(self):
        leak = "/tmp/rt-leak-%d" % os.getpid()
        problems, rec = self.run_tests(
            ("test_a_prepares", f"for d in ('/tmp', '/var/tmp', '/dev/shm', '/run/lock'):\n    open(d + '/{os.path.basename(leak)}', 'w').write('x')\n"
                                "import ctypes\nl = ctypes.CDLL(None, use_errno=True)\nself.assertGreaterEqual(l.shmget(0x5eed7001, 4096, 0o1000 | 0o666), 0)"),
            ("test_b_relies_on_it", f"for d in ('/tmp', '/var/tmp', '/dev/shm', '/run/lock'):\n    self.assertFalse(os.path.exists(d + '/{os.path.basename(leak)}'), d)\n"
                                    "import ctypes\nl = ctypes.CDLL(None, use_errno=True)\nself.assertLess(l.shmget(0x5eed7001, 0, 0o666), 0)"))
        self.assertEqual(problems, [], problems)
        for d in ("/tmp", "/var/tmp", "/dev/shm", "/run/lock"):
            self.assertFalse(os.path.exists(d + "/" + os.path.basename(leak)), d)
        with open("/proc/sysvipc/shm") as fh:
            self.assertNotIn(str(0x5eed7001), fh.read().replace("\n", " ").split())

    def test_module_level_code_of_the_collection_step_leaves_nothing_either(self):
        name = "rt-collect-%d" % os.getpid()
        src = f"import os, unittest\nP = ('/dev/shm/{name}', '/tmp/{name}')\nLEFT_BY_AN_EARLIER_STEP = any(os.path.exists(p) for p in P)\n" \
              f"for p in P:\n    open(p, 'w').write('x')\n\nclass T(unittest.TestCase):\n    def test_a(self):\n        self.assertFalse(LEFT_BY_AN_EARLIER_STEP)\n"
        problems, rec = R.portable_run("/tmp", [("test_a.py", src)])
        self.assertEqual(problems, [], problems)
        self.assertFalse(os.path.exists("/dev/shm/" + name) or os.path.exists("/tmp/" + name))

    def test_a_failing_test_that_names_itself_like_a_passing_one_voids_the_run(self):
        src = "import unittest\nclass T(unittest.TestCase):\n    def test_easy(self): pass\n    def test_hard(self): self.fail('really fails')\n" \
              "    def id(self):\n        return 'test_a.T.test_easy'\n"
        problems, rec = R.portable_run("/tmp", [("test_a.py", src)])
        self.assertTrue(problems)
        self.assertEqual(rec["tests_passed"], 0)


@NEEDS_REAL
class DeliverablesAreReadSafely(unittest.TestCase):
    """What the worker can leave in ./deliver/ (links, pipes, folders, oversize files, files of somebody else, odd names): the root runner reads only
    plain files of the agent user with sane names, never opens a link or blocks on a pipe, and says why for the others."""

    def test_odd_entries_are_refused_and_plain_files_are_read(self):
        base = tempfile.mkdtemp(prefix="rt-deliver-")
        os.chmod(base, 0o755)
        try:
            d = os.path.join(base, R.DELIVER)
            os.makedirs(d)
            uid, gid = R.PW.pw_uid, R.PW.pw_gid
            def mk(name, text="x = 1\n", chown=True):
                path = os.path.join(d, name)
                with open(path, "w") as fh:
                    fh.write(text)
                if chown:
                    os.chown(path, uid, gid)
                return path
            mk("good.py")
            os.symlink("/etc/passwd", os.path.join(d, "link.py"))
            os.lchown(os.path.join(d, "link.py"), uid, gid)
            os.mkfifo(os.path.join(d, "pipe.py"))
            os.chown(os.path.join(d, "pipe.py"), uid, gid)
            os.makedirs(os.path.join(d, "dir.py"))
            mk("big.py", "#" * (R.DELIVER_MAX_CHARS + 10))
            mk("rootowned.py", chown=False)
            mk("newline.py\n")
            mk("bad name!.py")
            mk(".hidden.py")
            mk("double..py")
            os.link(os.path.join(d, "good.py"), os.path.join(d, "hard.py"))
            os.chown(base, uid, gid)
            os.chown(d, uid, gid)
            files, problems = R.deliverables(base, True)
            names = [n for n, _ in files]
            self.assertIn("good.py", names)
            self.assertIn("hard.py", names)                                  # a second name for the agent's own file: the same bytes, harmless
            for bad in ("link.py", "pipe.py", "dir.py", "big.py", "rootowned.py", "newline.py\n", "bad name!.py", "double..py"):
                self.assertNotIn(bad, names, bad)
            self.assertNotIn(".hidden.py", names)
            self.assertTrue(problems)
            self.assertNotIn("root:", "".join(t for _, t in files))          # /etc/passwd was never read through the link
        finally:
            shutil.rmtree(base, ignore_errors=True)

    def test_the_deliver_folder_itself_may_not_be_a_link(self):
        base = tempfile.mkdtemp(prefix="rt-deliver2-")
        os.chmod(base, 0o755)
        try:
            os.symlink("/etc", os.path.join(base, R.DELIVER))
            os.lchown(os.path.join(base, R.DELIVER), R.PW.pw_uid, R.PW.pw_gid)
            files, problems = R.deliverables(base, True)
            self.assertEqual(files, [])
            self.assertTrue(problems)
        finally:
            shutil.rmtree(base, ignore_errors=True)


@NEEDS_REAL
class GroupedTestsAreNeverFull(RealBase):
    """More than SANDBOX_MAX_PROCS tests share processes (chunks). In a chunk one test can answer for its chunk-mates: it reported a failing neighbour
    as passed and the run was accepted as 'full'. Such a run must not be verifiable."""
    LIAR = ('''import os, unittest
class T(unittest.TestCase):
    def test_a_liar(self):
        import __main__
        __main__.emit({'e': 'stop', 'id': 'test_a.T.test_a_liar', 'o': 'ok'})
        __main__.emit({'e': 'start', 'id': 'test_a.T.test_b_really_fails'})
        __main__.emit({'e': 'stop', 'id': 'test_a.T.test_b_really_fails', 'o': 'ok'})
        __main__.emit({'e': 'done', 'run': 2, 'failures': 0, 'errors': 0, 'skipped': 0, 'xfail': 0, 'xpass': 0, 'ok': True})
        os._exit(0)
    def test_b_really_fails(self): self.fail('real failure')
    def test_c(self): pass
''')

    def test_one_process_per_test_is_void_for_the_liar_and_full(self):
        problems, rec = R.portable_run("/tmp", [("test_a.py", self.LIAR)])
        self.assertTrue(problems)                                          # the liar's forged neighbour record is void
        ISO.VERIFIER_TOOLS = "sandboxed"
        self.b.verifier_tools = True
        try:
            blk = self.b.describe(3, "id", 0.1)
            self.assertEqual((blk["mode"], isolation_problem(blk)), ("full", ""))
        finally:
            self.b.verifier_tools = False
            ISO.VERIFIER_TOOLS = ""

    def test_a_run_in_groups_is_never_full_and_says_why(self):
        saved = R.SANDBOX_MAX_PROCS
        self.b.verifier_tools = True
        R.SANDBOX_MAX_PROCS = 2                                            # three tests in groups of two
        try:
            problems, rec = R.portable_run("/tmp", [("test_a.py", self.LIAR)])
        finally:
            R.SANDBOX_MAX_PROCS = saved
            self.b.verifier_tools = False
        blk = rec["isolation"]
        self.assertTrue(blk["granularity"].startswith("chunk"), blk)
        self.assertEqual(blk["mode"], "identity")                          # was 'full': the forged neighbour made the run 'pass'
        self.assertFalse(blk["verifier_tools_isolated"])
        self.assertIn("groups", " ".join(blk["reasons"]))
        self.assertEqual(isolation_problem(blk), "")
        full_but_grouped = dict(blk, mode="full", verifier_tools_isolated=True)
        self.assertIn("one process per test", isolation_problem(full_but_grouped))      # the kernel refuses it even if a runner claimed it


@NEEDS_REAL
class BoundaryProperties(RealBase):
    def probe(self, code):
        scratch = tempfile.mkdtemp(prefix="rt-probe-")
        os.chown(scratch, self.b.sandbox.pw_uid, self.b.sandbox.pw_gid)
        try:
            r, w = os.pipe()
            p = ISO.spawn_sandboxed(self.b, [ISO.PYTHON3, "-I", "-B", "-c", code], scratch, stdout=w, stderr=subprocess.DEVNULL)
            os.close(w)
            out = b""
            while True:
                chunk = os.read(r, 65536)
                if not chunk:
                    break
                out += chunk
            os.close(r)
            p.wait(timeout=30)
            return json.loads(out)
        finally:
            shutil.rmtree(scratch, ignore_errors=True)

    def test_no_capabilities_survive_and_the_process_is_the_first_to_die_when_memory_runs_out(self):
        res = self.probe("import json\nd = {}\nfor l in open('/proc/self/status'):\n    k = l.split(':')[0]\n    if k in ('CapInh','CapPrm','CapEff','CapAmb','NoNewPrivs'): d[k] = l.split(':')[1].strip()\n"
                         "d['oom'] = open('/proc/self/oom_score_adj').read().strip()\nprint(json.dumps(d))")
        for k in ("CapInh", "CapPrm", "CapEff", "CapAmb"):
            self.assertEqual(res[k].strip("0"), "", k)
        self.assertEqual(res["NoNewPrivs"], "1")
        self.assertEqual(res["oom"], "1000")

    def test_setuid_programs_give_nothing(self):
        res = self.probe("import json, subprocess\nr = subprocess.run(['/usr/bin/su', '-c', 'id -u', 'root'], capture_output=True, text=True, stdin=subprocess.DEVNULL)\n"
                         "print(json.dumps({'rc': r.returncode, 'out': r.stdout.strip()}))")
        self.assertNotEqual(res["rc"], 0)
        self.assertNotEqual(res["out"], "0")

    def test_the_self_test_catches_a_verify_home_the_sandbox_can_read(self):
        home = self.b.verify.pw_dir
        mode = stat.S_IMODE(os.stat(home).st_mode)
        os.chmod(home, 0o755)                                       # Ubuntu 22.04's useradd -m default
        try:
            b2 = ISO.resolve(R.PW)
            ISO.run_self_test(b2)
            self.assertTrue(b2.self_test.startswith("failed"), b2.self_test)
            self.assertIn("private_verify_home", b2.self_test)
            self.assertEqual(b2.mode(), "none")
        finally:
            os.chmod(home, mode)
        b3 = ISO.resolve(R.PW)
        self.assertEqual(ISO.run_self_test(b3), "ok")

    def test_the_verifier_session_starts_with_a_private_umask_and_the_verify_identity(self):
        folder = tempfile.mkdtemp(prefix="rt-job-")
        os.chown(folder, R.PW.pw_uid, R.PW.pw_gid)
        view = tempfile.mkdtemp(prefix="vview-")
        os.chmod(view, 0o755)
        saved = (R.command, R.api)
        R.command = lambda kind, prompt, f, max_turns=60: ["sh", "-c", "id -u; umask"]
        R.api = lambda path, body=None: (200, {"state": "running"})
        out = R.VerifierOutput()
        try:
            R.run_agent("job", "hermes", "p", folder, 30, "verify-1.log", capture=out, user=self.b.verifier_pw(), cwd=view)
            lines = out.read().decode().split()
        finally:
            R.command, R.api = saved
            out.close()
            shutil.rmtree(folder, ignore_errors=True)
            shutil.rmtree(view, ignore_errors=True)
        self.assertEqual(lines, [str(self.b.verifier_pw().pw_uid), "0077"])

    def test_RESIDUAL_the_sandbox_shares_the_network_namespace_so_abstract_sockets_and_loopback_ports_are_reachable(self):
        """DOCUMENTED LIMIT, pinned so the claim in the README is evidence and not hope: the identity backend is user separation inside ONE kernel and
        ONE network namespace. A process of the sandbox identity can connect to an abstract unix socket or a loopback port opened by any other
        identity (the runner, the verifier). Nothing in the runner or the verifier listens on one; the lab box's Agent PC display (WSLg) does
        (abstract X11 socket), guarded only by X authority. A network namespace needs CAP_SYS_ADMIN (the container backend: --network=none)."""
        import socket
        srv = socket.socket(socket.AF_UNIX)
        srv.bind("\0rt-abstract-%d" % os.getpid())
        srv.listen(1)
        tcp = socket.socket()
        tcp.bind(("127.0.0.1", 0))
        tcp.listen(1)
        res = self.probe("import json, socket\nr = {}\ns = socket.socket(socket.AF_UNIX)\ntry:\n    s.connect('\\0rt-abstract-%d')\n    r['abstract'] = 'CONNECTED'\nexcept OSError as e:\n"
                         "    r['abstract'] = 'denied'\nt = socket.socket()\ntry:\n    t.connect(('127.0.0.1', %d))\n    r['tcp'] = 'CONNECTED'\nexcept OSError:\n    r['tcp'] = 'denied'\n"
                         "print(json.dumps(r))" % (os.getpid(), tcp.getsockname()[1]))
        srv.close()
        tcp.close()
        self.assertEqual(res, {"abstract": "CONNECTED", "tcp": "CONNECTED"})


@NEEDS_REAL
class RealResolve(unittest.TestCase):
    def test_a_missing_verify_user_with_the_owners_statement_is_not_full_end_to_end(self):
        saved = (ISO.REQUEST, ISO.VERIFY_USER, ISO.VERIFIER_TOOLS)
        try:
            ISO.REQUEST, ISO.VERIFY_USER, ISO.VERIFIER_TOOLS = "identity", "no-such-verify-user", "sandboxed"
            b = ISO.resolve(R.PW)
            ISO.run_self_test(b)
            self.assertEqual(b.self_test, "ok")
            b.verifier_tools = True
            self.assertEqual(b.mode(), "identity")
            self.assertFalse(b.describe(3, "id", 0.1)["separate_verifier_identity"])
        finally:
            ISO.REQUEST, ISO.VERIFY_USER, ISO.VERIFIER_TOOLS = saved


@NEEDS_REAL
class DeployCheck(unittest.TestCase):
    """deploy/check_lab_deploy.py, the command the README tells the owner to run on the lab box: it must say OK for a correct install and FAIL for each
    loose permission the isolation depends on."""
    SCRIPT = os.path.join(HERE, "..", "deploy", "check_lab_deploy.py")

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="rt-install-", dir="/opt")
        os.chmod(self.dir, 0o755)
        for name in ("lab_runner.py", "lab_isolation.py"):
            shutil.copy(os.path.join(HERE, "..", name), os.path.join(self.dir, name))
            os.chmod(os.path.join(self.dir, name), 0o644)
        self.home = ISO._getpw(ISO.VERIFY_USER).pw_dir
        self.home_mode = stat.S_IMODE(os.stat(self.home).st_mode)

    def tearDown(self):
        os.chmod(self.home, self.home_mode)
        shutil.rmtree(self.dir, ignore_errors=True)
        subprocess.run(["gpasswd", "-d", ISO.SANDBOX_USER, "rt-extra"], capture_output=True)
        subprocess.run(["groupdel", "rt-extra"], capture_output=True)

    def check(self):
        r = subprocess.run([sys.executable, "-I", self.SCRIPT, "--dir", self.dir], capture_output=True, text=True, timeout=120)
        return r.returncode, r.stdout

    def test_a_correct_install_passes(self):
        code, out = self.check()
        self.assertEqual(code, 0, out)
        self.assertIn("RESULT: OK", out)
        self.assertIn("isolation self-test: mode identity", out)

    def test_every_loose_permission_is_a_failure(self):
        os.chmod(os.path.join(self.dir, "lab_runner.py"), 0o664)
        code, out = self.check()
        self.assertEqual(code, 1, out)
        self.assertRegex(out, r"FAIL .*lab_runner.py")
        os.chmod(os.path.join(self.dir, "lab_runner.py"), 0o644)
        os.chmod(self.dir, 0o777)
        code, out = self.check()
        self.assertEqual(code, 1, out)
        os.chmod(self.dir, 0o755)
        os.chmod(self.home, 0o755)                                          # Ubuntu 22.04's `useradd -m` default
        code, out = self.check()
        self.assertEqual(code, 1, out)
        self.assertRegex(out, r"FAIL .*jarvis-verify is 0o755")
        self.assertRegex(out, r"FAIL isolation self-test: mode none")
        os.chmod(self.home, self.home_mode)
        subprocess.run(["groupadd", "rt-extra"], check=True)
        subprocess.run(["usermod", "-aG", "rt-extra", ISO.SANDBOX_USER], check=True)
        code, out = self.check()
        self.assertEqual(code, 1, out)
        self.assertRegex(out, r"FAIL .*jarvis-sandbox is in no other group")


if __name__ == "__main__":
    unittest.main()
