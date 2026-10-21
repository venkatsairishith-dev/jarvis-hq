"""`jarvis check` crashed with UnboundLocalError: main() imported Action again inside the `analyze` branch, which made
`Action` a local name for the whole function (static analysis F823, 2026-10-19)."""
import contextlib
import io
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from jarvis_kernel import cli  # noqa: E402


class CliCheckTest(unittest.TestCase):
    def test_check_classifies_without_crashing(self):
        with tempfile.TemporaryDirectory() as d:
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                rc = cli.main(["--data", d, "check", "files", "read", "CANARY-STATIC-x"])
            self.assertEqual(rc or 0, 0)
            res = json.loads(out.getvalue())
            self.assertIn("level", res)
            self.assertIn("reason", res)

    def test_analyze_refuses_or_reports_without_crashing(self):
        with tempfile.TemporaryDirectory() as d:
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                rc = cli.main(["--data", d, "analyze"])
            self.assertIn(rc, (0, 1, 3))

    @unittest.skipIf(os.name == "nt", "POSIX file modes")
    def test_agent_token_file_is_0600_even_when_it_already_existed(self):
        """An existing 0644 target kept its loose mode: O_CREAT's mode only applies to a new file (static pass, 2026-10-19)."""
        with tempfile.TemporaryDirectory() as d:
            out_path = os.path.join(d, "tok")
            with open(out_path, "w") as fh:
                fh.write("old")
            os.chmod(out_path, 0o644)
            with contextlib.redirect_stdout(io.StringIO()):
                cli.main(["--data", d, "agent-token", "CANARY-STATIC-agent", "--out", out_path])
            self.assertEqual(os.stat(out_path).st_mode & 0o777, 0o600)
            self.assertNotEqual(open(out_path).read(), "old")


if __name__ == "__main__":
    unittest.main()
