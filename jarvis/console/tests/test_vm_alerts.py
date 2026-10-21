"""VM-side owner notices with Telegram off: the nightly regression (scripts/nightly.py) and Monit's alert script
(scripts/alert.py). A temporary kernel store; no network, no Telegram, nothing on the VM is touched."""
import contextlib
import importlib.util
import io
import os
import re
import sqlite3
import stat
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
JARVIS = os.path.join(HERE, "..", "..")
sys.path.insert(0, os.path.join(JARVIS, "kernel"))


def load(name, rel):
    spec = importlib.util.spec_from_file_location(name, os.path.join(JARVIS, rel))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def console_failure_filter():
    """The JARVIS console's own rule for its "recent failures" list (Settings screen), read from its source."""
    src = open(os.path.join(JARVIS, "ui", "src", "screens", "Settings.tsx"), encoding="utf-8").read()
    return re.compile(re.search(r"events\.filter\(\(e: AuditEvent\) => /(.+?)/\.test\(e\.kind\)\)", src).group(1))


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.env = {k: os.environ.get(k) for k in ("JARVIS_DATA", "JARVIS_TELEGRAM", "MONIT_SERVICE", "MONIT_EVENT",
                                                   "MONIT_DESCRIPTION")}
        os.environ["JARVIS_DATA"] = self.kdir = os.path.join(self.tmp.name, "kernel")
        os.environ["JARVIS_TELEGRAM"] = "off"                    # the owner's setting since 2026-10-10
        self.argv = sys.argv

    def tearDown(self):
        sys.argv = self.argv
        for k, v in self.env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        for root, dirs, _ in os.walk(self.tmp.name):
            for d in dirs:
                os.chmod(os.path.join(root, d), stat.S_IRWXU)
        self.tmp.cleanup()

    def audit(self):
        path = os.path.join(self.kdir, "state.db")
        if not os.path.exists(path):
            return []
        db = sqlite3.connect(path)
        try:
            import json
            return [(k, json.loads(p)) for k, p in db.execute("SELECT kind, payload FROM audit ORDER BY seq")]
        finally:
            db.close()


class Nightly(Base):
    """PRO-D3: with Telegram off the flags went only to a JSON file on the VM; no audit row, nothing in the console."""

    def run_nightly(self, false_allow):
        nightly = load("nightly_t", os.path.join("scripts", "nightly.py"))
        nightly.OUT = os.path.join(self.tmp.name, "bench-results")
        nightly.run_set = lambda name, ood: {"set": name, "ok": True, "accuracy": {"union": 90}, "false_allow": {"union": false_allow},
                                             "p95_ms": {"union": 5.0}, "tail": ""}
        sys.argv = ["nightly.py"]
        with contextlib.redirect_stdout(io.StringIO()):
            nightly.main()

    def test_flags_are_audited_and_listed_by_the_console_with_telegram_off(self):
        self.run_nightly((9, 100))                               # 9% of high-risk requests let through: a regression
        rows = self.audit()
        kinds = [k for k, _ in rows]
        self.assertIn("nightly_run", kinds)
        run = dict(rows)["nightly_run"]
        self.assertEqual(run["flags"], 4)
        self.assertIn("union let 9 of 100 high-risk requests through", run["flagged"][0])
        failed = [p for k, p in rows if k == "nightly_regression_failed"]
        self.assertEqual(len(failed), 1)
        self.assertEqual(failed[0]["flags"], 4)
        shown = [k for k in kinds if console_failure_filter().search(k)]
        self.assertEqual(shown, ["nightly_regression_failed"])  # the console's failure list shows it
        from jarvis_kernel import owner
        self.assertNotIn("nightly_regression_failed", owner.NOISE)   # and the live event feed does not hide it

    def test_a_clean_run_is_audited_without_a_failure_row(self):
        self.run_nightly((0, 100))
        kinds = [k for k, _ in self.audit()]
        self.assertEqual(kinds.count("nightly_run"), 1)
        self.assertNotIn("nightly_regression_failed", kinds)


class MonitAlert(Base):
    """PRO-D1: Monit's sandbox (deploy/monit-override.conf) leaves only ~/.jarvis-kernel writable; the Telegram-off
    fallback wrote to ~/jarvis/alerts.log and failed there."""

    def test_the_fallback_log_is_written_in_the_kernel_data_folder_after_the_audit_row(self):
        alert = load("alert_t", os.path.join("scripts", "alert.py"))
        ro = os.path.join(self.tmp.name, "jarvis-readonly")
        os.makedirs(ro)
        os.chmod(ro, stat.S_IRUSR | stat.S_IXUSR)               # /home/ubuntu/jarvis as Monit's children see it
        alert.ROOT = ro
        os.environ.update(MONIT_SERVICE="jarvis_embed", MONIT_EVENT="Does not exist", MONIT_DESCRIPTION="process is not running")
        sys.argv = ["alert.py", "fail"]
        self.assertEqual(alert.main(), 0)
        self.assertEqual([k for k, _ in self.audit()], ["monit_alert"])
        with open(os.path.join(self.kdir, "alerts.log"), encoding="utf-8") as fh:
            lines = fh.read().splitlines()
        self.assertEqual(len(lines), 1)
        self.assertIn("ALERT: jarvis_embed - Does not exist. process is not running", lines[0])
        self.assertEqual(os.listdir(ro), [])

    def test_a_log_that_cannot_be_written_is_audited_not_lost(self):
        alert = load("alert_t2", os.path.join("scripts", "alert.py"))
        alert.alerts_log = lambda k: os.path.join(self.tmp.name, "missing-dir", "alerts.log")
        os.environ.update(MONIT_SERVICE="rootfs", MONIT_EVENT="Resource limit matched", MONIT_DESCRIPTION="space usage 90%")
        sys.argv = ["alert.py", "fail"]
        self.assertEqual(alert.main(), 1)
        self.assertEqual([k for k, _ in self.audit()], ["monit_alert", "monit_alert_log_failed"])


if __name__ == "__main__":
    unittest.main()
