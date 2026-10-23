"""jarvis/scripts/preflight.py: what would put an error on the owner's screen, found first and said in plain words."""
import importlib.util
import os
import sys
import unittest

SCRIPTS = os.path.join(os.path.dirname(__file__), "..", "..", "scripts")
sys.path.insert(0, SCRIPTS)
spec = importlib.util.spec_from_file_location("preflight_t", os.path.join(SCRIPTS, "preflight.py"))
P = importlib.util.module_from_spec(spec)
spec.loader.exec_module(P)

HEADER = '"HostName","TaskName","Next Run Time","Status","Logon Mode","Last Run Time","Last Result","Author","Task To Run","Scheduled Task State"\n'


def row(name, result, status="Ready", run="powershell.exe x.ps1", state="Enabled"):
    return f'"PC","\\{name}","N/A","{status}","Interactive only","21-10-2026 18:59:29","{result}","me","{run}","{state}"\n'


class Tasks(unittest.TestCase):
    def test_only_jarvis_tasks_and_only_real_errors_are_red(self):
        csv_text = HEADER + row("JarvisNode", "267009", "Running") + row("JarvisLab", "2147942402", run="wscript.exe D:\\gone\\start.vbs") \
            + row("JarvisStack", "0") + row("JarvisFace", "267011") + row("Microsoft\\Windows\\X", "1") \
            + row("JarvisOld", "-2147024894") + row("JarvisOff", "2147942402", state="Disabled") + HEADER
        got = {r["task"]: r for r in P.audit_tasks(csv_text)}
        self.assertEqual(sorted(got), ["JarvisFace", "JarvisLab", "JarvisNode", "JarvisOff", "JarvisOld", "JarvisStack"])
        self.assertFalse(got["JarvisLab"]["ok"]); self.assertIn("a file the task starts is missing", got["JarvisLab"]["detail"])
        self.assertFalse(got["JarvisOld"]["ok"])                                      # the same code written as a signed number
        for fine in ("JarvisNode", "JarvisStack", "JarvisFace", "JarvisOff"):
            self.assertTrue(got[fine]["ok"], fine)                                    # running, success, never run, disabled
        self.assertEqual(got["JarvisLab"]["runs"], "wscript.exe D:\\gone\\start.vbs")

    def test_result_values(self):
        self.assertEqual(P.result_value("0x41301"), 0x41301)
        self.assertEqual(P.result_value("-2147024894"), 0x80070002)
        self.assertIsNone(P.result_value("N/A"))


class Rest(unittest.TestCase):
    def test_live_check_lines_are_parsed(self):
        out = "noise\n[PASS] kernel reachable -- ok\n[FAIL] Dahl slots -- no key\n[PASS] audit chain\n"
        self.assertEqual(P.parse_live_check(out), [("kernel reachable", True, "ok"), ("Dahl slots", False, "no key"), ("audit chain", True, "")])

    def test_the_report_is_green_only_when_everything_is(self):
        items = P.gather("[PASS] a\n[PASS] b\n", HEADER + row("JarvisNode", "0"), 40.0)
        text, code = P.report(items)
        self.assertEqual(code, 0); self.assertIn("ALL GREEN", text); self.assertIn("0 red", text)
        bad = P.gather("[PASS] a\n[FAIL] planner -- 429\n", HEADER + row("JarvisLab", "2147942402"), 2.0)
        text, code = P.report(bad)
        self.assertEqual(code, 1)
        self.assertIn("RED  [live check] planner -- 429", text); self.assertIn("RED  [scheduled task] JarvisLab", text)
        self.assertIn("RED  [disk] D: free space", text); self.assertIn("NOT READY: fix the 3 red", text)


if __name__ == "__main__":
    unittest.main()
