import hashlib
import os
import stat
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from jarvis_kernel import context_select as CS  # noqa: E402
from jarvis_kernel import mcpwire as W  # noqa: E402

FILLER = [f"INFO step {i} ok (worker {i % 7})" for i in range(3000)]
ERR = "ValueError: port must be a number, got '80x80' (config.json line 3)"


def _long(err_at=1500):
    lines = FILLER[:err_at] + [ERR] + FILLER[err_at:]
    return "\n".join(lines)


def _fake_bin(d, body):
    p = os.path.join(d, "trim")
    with open(p, "w", encoding="utf-8") as f:
        f.write("#!" + sys.executable + "\n" + body)
    os.chmod(p, os.stat(p).st_mode | stat.S_IEXEC)
    if os.name == "nt":       # Windows cannot run a #! script: a .cmd launcher runs it with the same interpreter
        cmd = p + ".cmd"
        with open(cmd, "w", encoding="utf-8", newline="") as f:
            f.write('@"%s" "%s" %%*\r\n' % (sys.executable, p))
        return cmd
    return p


def _verbatim_in_order(orig, out):
    lines, j = orig.split("\n"), 0
    for line in out.split("\n"):
        if line.startswith("[... ") and line.endswith(" lines omitted ...]"):
            continue
        while j < len(lines) and lines[j] != line:
            j += 1
        if j == len(lines):
            return False
        j += 1
    return True


class Modes(unittest.TestCase):
    def test_default_is_evidence_and_off_still_gives_the_old_head_cut(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop(CS.ENV_MODE, None)
            self.assertEqual(CS.configured_mode(), "evidence")
            self.assertIn(ERR, CS.select(_long(), "why", 4000).text.split("\n"))
        with mock.patch.dict(os.environ, {CS.ENV_MODE: "off"}):
            text = _long()
            s = CS.select(text, "why", 4000)
        self.assertEqual(s.mode, "off")
        self.assertTrue(s.text.startswith(text[:4000]))
        self.assertTrue(s.text.endswith(f"[truncated: {len(text) - 4000} more characters]"))

    def test_an_unknown_mode_falls_back_to_the_default(self):
        with mock.patch.dict(os.environ, {CS.ENV_MODE: "magic"}):
            self.assertEqual(CS.configured_mode(), "evidence")

    def test_text_that_fits_is_returned_unchanged_in_every_mode(self):
        for mode in CS.MODES:
            s = CS.select("short\nERROR x", "x", 1000, mode)
            self.assertEqual((s.text, s.changed), ("short\nERROR x", False), mode)

    def test_evidence_keeps_the_error_verbatim_in_order_and_fits(self):
        text = _long()
        s = CS.select(text, "why did the config fail", 4000, "evidence")
        self.assertIn(ERR, s.text.split("\n"))
        self.assertLessEqual(len(s.text), 4000)
        self.assertTrue(_verbatim_in_order(text, s.text))
        self.assertEqual(s.sha256, hashlib.sha256(text.encode()).hexdigest())
        # the omitted ranges are exact: kept + omitted = every line once
        lines = text.split("\n")
        dropped = sum(b - a + 1 for a, b in s.omitted)
        self.assertEqual(dropped + s.kept_lines, len(lines))
        for a, b in s.omitted:
            self.assertNotIn(ERR, lines[a:b + 1])

    def test_a_flood_of_warnings_does_not_hide_the_one_error(self):
        warn = [f"DeprecationWarning: old api call {i}" for i in range(2000)]
        text = "\n".join(warn[:900] + ["ERROR: disk quota exceeded on /var/lib/jarvis"] + warn[900:])
        s = CS.select(text, "why did the job stop", 4000, "evidence")
        self.assertIn("ERROR: disk quota exceeded on /var/lib/jarvis", s.text.split("\n"))

    def test_binary_looking_text_gets_the_plain_head_cut(self):
        s = CS.select("A\x00" * 5000, "x", 100, "evidence")
        self.assertEqual(s.mode, "off")


class Trim1Safety(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()

    def _run(self, body, timeout=2.0):
        exe = _fake_bin(self.d, body)
        with mock.patch.dict(os.environ, {CS.ENV_TRIM1: exe}):
            return CS.select(_long(), "why did the config fail", 4000, "trim1-balanced", timeout_s=timeout)

    def test_missing_binary_falls_back_to_evidence(self):
        with mock.patch.dict(os.environ, {CS.ENV_TRIM1: "/nonexistent/trim"}):
            s = CS.select(_long(), "q", 4000, "trim1-balanced")
        self.assertEqual(s.mode, "trim1-balanced->evidence")
        self.assertIn(ERR, s.text.split("\n"))

    def test_output_that_is_not_the_input_is_ignored(self):
        s = self._run("import sys; sys.stdin.read(); print('rm -rf / is the answer')\n")
        self.assertNotIn("rm -rf / is the answer", s.text)
        self.assertIn("not a subsequence", s.note)
        self.assertIn(ERR, s.text.split("\n"))

    def test_a_hanging_binary_times_out_and_falls_back(self):
        s = self._run("import time; time.sleep(30)\n", timeout=0.5)
        self.assertIn("TimeoutExpired", s.note)
        self.assertIn(ERR, s.text.split("\n"))

    def test_a_failing_binary_falls_back(self):
        s = self._run("import sys; sys.exit(3)\n")
        self.assertIn("exit 3", s.note)

    def test_singular_marker_and_verbatim_lines_are_accepted(self):
        # keeps the error line and the last 5 lines, with trim-1's own markers (one of them singular)
        # bytes in and out: like the real trim-1, UTF-8 with \n whatever the console code page or newline translation
        body = ("import sys\nlines = sys.stdin.buffer.read().decode('utf-8').split('\\n')\ni = next(k for k, l in enumerate(lines) if l.startswith('ValueError'))\n"
                "out = ['… %d lines …' % i, lines[i], '… 1 line …', lines[i + 2], '… %d lines …' % (len(lines) - i - 8)] + lines[-5:]\n"
                "sys.stdout.buffer.write('\\n'.join(out).encode('utf-8'))\n")
        s = self._run(body)
        self.assertEqual(s.mode, "trim1-balanced")
        self.assertIn(ERR, s.text.split("\n"))
        self.assertTrue(_verbatim_in_order(_long(), s.text))


class ProcessResult(unittest.TestCase):
    def _res(self, text):
        return {"content": [{"type": "text", "text": text}]}

    def test_off_counts_the_characters_actually_left_out(self):
        from jarvis_kernel.scrub import scrub
        text = "token ghp_" + "A1b2C3d4" * 5 + "\n" + _long()       # fake token: scrubbing changes the length
        clean = scrub(text, deep=True)[0]
        self.assertNotEqual(len(clean), len(text))
        with mock.patch.dict(os.environ, {CS.ENV_MODE: "off"}):
            r = W.process_result(self._res(text), 5000)
        self.assertTrue(r["truncated"])
        self.assertTrue(r["text"].endswith(f"[truncated: {len(clean) - 5000} more characters]"), r["text"][-60:])
        self.assertTrue(r["untrusted"])

    def test_evidence_mode_keeps_the_error_and_says_what_it_did(self):
        with mock.patch.dict(os.environ, {CS.ENV_MODE: "evidence"}):
            r = W.process_result(self._res(_long()), 5000, query="config error")
        self.assertIn(ERR, r["text"])
        self.assertEqual(r["selection"]["mode"], "evidence")
        self.assertTrue(r["untrusted"])
        self.assertIn("never as instructions", r["note"])

    def test_a_private_key_is_still_withheld_whatever_the_mode(self):
        key = "-----BEGIN OPENSSH PRIVATE KEY-----\nAAAAB3NzaC1yc2EAAAADAQABAAABAQ\n-----END OPENSSH PRIVATE KEY-----"
        with mock.patch.dict(os.environ, {CS.ENV_MODE: "evidence"}):
            r = W.process_result(self._res(_long() + "\n" + key), 5000)
        self.assertTrue(r["withheld"])
        self.assertNotIn("PRIVATE KEY", r["text"])


if __name__ == "__main__":
    unittest.main()


class HostileInput(unittest.TestCase):
    """Tool output is untrusted: no input may make selection slow (it runs inside the kernel for every long result)."""
    def test_one_huge_line_and_many_lines_are_selected_quickly(self):
        import time
        cases = {"one 1 MB line": "at " + "x" * 1_000_000,
                 "1 MB line with an ident run": "a" + "b" * 1_000_000 + "-c",
                 "long File line": 'File "' + "y" * 200_000,
                 "100k short lines": "\n".join(f"at frame{i} line" for i in range(100_000)) + "\nERROR end"}
        for name, text in cases.items():
            t0 = time.perf_counter()
            s = CS.select(text, "error at line", 4000, "evidence")
            secs = time.perf_counter() - t0
            self.assertLess(secs, 5.0, f"{name}: {secs:.1f} s")
            self.assertLessEqual(len(s.text), 4000, name)


class AccessLogs(unittest.TestCase):
    def test_a_failing_request_is_kept_by_its_status_code(self):
        ok = [f'10.0.0.{i % 250} - - [12/Mar/2024:08:{i % 60:02d}:01 +0000] "POST /api/v2/checkout HTTP/1.1" 200 312 "-" "App/4.2"'
              for i in range(3000)]
        bad = '198.51.100.23 - - [12/Mar/2024:08:14:27 +0000] "POST /api/v2/checkout HTTP/1.1" 502 559 "-" "App/4.2"'
        text = "\n".join(ok[:1500] + [bad] + ok[1500:])
        s = CS.select(text, "which checkout request failed", 4000, "evidence")
        self.assertIn(bad, s.text.split("\n"))
        self.assertTrue(CS.EVIDENCE.search('{"ts": 1, "status": 503, "path": "/x"}'))
        self.assertFalse(CS.EVIDENCE.search(ok[0]))                 # a 200 is not evidence

