"""The node never looks at, types into or acts on a bank, broker, payment or crypto window (2026-10-18).

jobs.SENSITIVE_PROC, diag.SENSITIVE and repair.SENSITIVE were one regex that matched "banking" and "netbank" but not "bank"
alone or any brand: a window titled "ICICI Bank - Google Chrome", or a Zerodha, PhonePe or Coinbase window, was
snapshotted, typed into, minimized, OCR'd or restarted. All of them now use jarvis_node.sensitive (the kernel's list in
policy.sensitive_target, with the node's own regex as a floor), and the node looks at the titles of the target process's
windows (the kernel only sees "chrome|*"). The window list, the UI backend, the snapshot and PowerShell are fakes that
record what they were asked to do; nothing touches a real desktop.
"""
import os
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(__file__)
sys.path.insert(0, os.path.join(HERE, ".."))
sys.path.insert(0, os.path.join(HERE, "..", "..", "node"))
from jarvis_node import diag, repair, sensitive  # noqa: E402
from jarvis_node import jobs as NJ  # noqa: E402

FINANCIAL = ["ICICI Bank - Google Chrome", "Kite by Zerodha - Dashboard", "PhonePe", "Coinbase - Buy & sell crypto",
             "HDFC Bank NetBanking", "Groww - Stocks", "Login | State Bank of India"]


def windows(title, process="chrome"):
    return lambda: [{"hwnd": 11, "pid": 5, "process": process, "title": title, "foreground": True},
                    {"hwnd": 12, "pid": 6, "process": "notepad", "title": "notes.txt - Notepad", "foreground": False}]


class Backend(dict):
    def __init__(self):
        self.calls = []
        super().__init__({op: (lambda op: lambda t, p: self.calls.append((op, t)))(op)
                          for op in ("launch", "click", "type", "focus", "key", "scroll", "click_at", "minimize", "maximize", "restore")})


class SharedCheck(unittest.TestCase):
    def test_bank_alone_and_brands_are_sensitive(self):
        for title in FINANCIAL + ["PhonePe.exe", "zerodha_kite", "coinbase"]:
            self.assertTrue(sensitive.why(title), title)
        for title in ("notes.txt - Notepad", "Spotify Premium", "YouTube - Google Chrome", "calc", "Visual Studio Code"):
            self.assertEqual(sensitive.why(title), "", title)

    def test_the_floor_alone_still_refuses_without_the_kernel_package(self):
        real, sensitive._kernel_check = sensitive._kernel_check, None
        try:
            for title in FINANCIAL + ["PhonePe.exe"]:
                self.assertTrue(sensitive.why(title), title)
            self.assertEqual(sensitive.why("notes.txt - Notepad"), "")
        finally:
            sensitive._kernel_check = real
        # a node whose kernel package cannot be imported still starts and refuses (a fresh interpreter, kernel path hidden)
        code = ("import sys; sys.path[:] = [p for p in sys.path if 'kernel' not in p]; import builtins; real = builtins.__import__\n"
                "def imp(n, *a, **k):\n    if n.startswith('jarvis_kernel'): raise ImportError(n)\n    return real(n, *a, **k)\n"
                "builtins.__import__ = imp\nfrom jarvis_node import sensitive as s\n"
                "assert s._kernel_check is None\nassert s.why('ICICI Bank - Google Chrome') and s.why('Zerodha') and not s.why('Notepad')\n"
                "print('ok')")
        r = subprocess.run([sys.executable, "-B", "-c", code], cwd=os.path.join(HERE, "..", "..", "node"), capture_output=True, text=True)
        self.assertEqual((r.returncode, r.stdout.strip()), (0, "ok"), r.stderr[-800:])


class NodeRefuses(unittest.TestCase):
    def test_snapshot_of_a_financial_window_is_refused(self):
        for title in FINANCIAL:
            snap = lambda *a, t=title: {"window": t, "process": "chrome", "elements": [{"name": "Account balance"}]}   # noqa: E731
            r = NJ.ui_snapshot({"target": ""}, snap=snap)                       # the foreground window
            self.assertFalse(r["ok"], title); self.assertNotIn("elements", r)
        for target in ("zerodha", "PhonePe", "coinbase", "icicibank"):
            self.assertFalse(NJ.ui_snapshot({"target": target}, snap=lambda *a: {"window": "x", "process": "x"})["ok"], target)
        ok = NJ.ui_snapshot({"target": "notepad"}, snap=lambda *a: {"window": "notes.txt - Notepad", "process": "notepad", "elements": []})
        self.assertTrue(ok["ok"])

    def test_typing_and_window_actions_on_a_financial_window_are_refused(self):
        for title in FINANCIAL:
            for op, target, text in (("type", "chrome|*", "hello"), ("click", "chrome|Login", ""), ("focus", "chrome", ""),
                                     ("minimize", "chrome", ""), ("maximize", "chrome", ""), ("key", "chrome", "enter"),
                                     ("scroll", "chrome|", "down"), ("click_at", "chrome|10,10", "")):
                b = Backend()
                r = NJ.ui_action({"op": op, "target": target, "text": text}, backend=b, lister=windows(title))
                self.assertFalse(r["ok"], (title, op)); self.assertIn("refused", r["error"]); self.assertEqual(b.calls, [], (title, op))
        b = Backend()                                                            # named in the target itself: no window needed
        self.assertFalse(NJ.ui_action({"op": "type", "target": "PhonePe|Amount", "text": "500"}, backend=b, lister=lambda: [])["ok"])
        self.assertEqual(b.calls, [])
        # the same actions on an ordinary window still run
        b = Backend()
        self.assertTrue(NJ.ui_action({"op": "type", "target": "notepad|*", "text": "hello"}, backend=b, lister=windows("ICICI Bank - Google Chrome"))["ok"])
        self.assertTrue(NJ.ui_action({"op": "minimize", "target": "chrome", "text": ""}, backend=b, lister=windows("YouTube - Google Chrome"))["ok"])
        self.assertTrue(NJ.ui_action({"op": "key", "target": "", "text": "playpause"}, backend=b, lister=windows("ICICI Bank - Google Chrome"))["ok"])
        self.assertEqual([c[0] for c in b.calls], ["type", "minimize", "key"])

    def test_diagnostics_on_a_financial_window_are_refused_or_leave_it_out(self):
        ran = []

        def runner(script, env, timeout):
            ran.append(env["JARVIS_DIAG_NAME"])
            return subprocess.CompletedProcess([], 0, stdout=runner.out, stderr="")
        for title in FINANCIAL:                                                  # OCR / locate in a window: refused before capture
            for op, target in (("window_ocr", "chrome"), ("window_locate", "chrome|Transfer")):
                r = diag.run({"op": op, "target": target}, runner=runner, lister=windows(title))
                self.assertFalse(r["ok"], (title, op)); self.assertIn("refused", r["error"])
        for name in ("zerodha", "PhonePe", "coinbase", "ICICI Bank"):           # named directly: refused before PowerShell
            self.assertIn("refused", diag.run({"op": "app_info", "target": name}, runner=runner)["error"], name)
        self.assertEqual(ran, [])
        runner.out = '[{"id": 5, "window_title": "ICICI Bank - Google Chrome", "responding": true}, {"id": 6, "window_title": "YouTube"}]'
        r = diag.run({"op": "app_info", "target": "chrome"}, runner=runner)    # the title would reach the kernel: hidden
        self.assertTrue(r["ok"]); self.assertNotIn("ICICI", str(r["rows"])); self.assertIn("YouTube", str(r["rows"]))
        with tempfile.TemporaryDirectory() as tmp:
            old = os.environ.get("JARVIS_OUTPUTS"); os.environ["JARVIS_OUTPUTS"] = tmp
            try:
                runner.out = ('[{"title": "ICICI Bank - Google Chrome", "texts": ["Balance 12,000"], "buttons": []},'
                              ' {"title": "Confirm", "dialog": true, "owner": "Kite by Zerodha", "texts": ["Sell 10"], "buttons": ["OK"]},'
                              ' {"title": "YouTube - Google Chrome", "texts": ["Up next"], "buttons": []}]')
                r = diag.run({"op": "window_text", "target": "chrome"}, runner=runner)
                with open(r["local_result"], encoding="utf-8") as fh:
                    kept = fh.read()
            finally:
                os.environ.pop("JARVIS_OUTPUTS") if old is None else os.environ.__setitem__("JARVIS_OUTPUTS", old)
        self.assertEqual(r["summary"]["windows"], 1); self.assertNotIn("Balance", kept); self.assertNotIn("Sell 10", kept)

    def test_restarting_an_app_showing_a_financial_window_is_refused(self):
        ran, real = [], repair.runtime.powershell
        repair.runtime.powershell = lambda *a, **k: ran.append(a) or subprocess.CompletedProcess([], 0, stdout="{}", stderr="")
        try:
            for title in FINANCIAL:
                r = repair.restart_app("chrome", lister=windows(title))
                self.assertFalse(r["ok"], title); self.assertIn("sensitive", r["error"])
            for name in ("PhonePe", "zerodha", "coinbase"):
                self.assertFalse(repair.restart_app(name, lister=lambda: [])["ok"], name)
            self.assertEqual(ran, [])
        finally:
            repair.runtime.powershell = real


class SpellingsAndTheFloor(unittest.TestCase):
    """Review M7 (2026-10-18): the node's floor regex had no chase or hsbc, and read "chase。com" or "ICI<ZWSP>CI Bank" as other
    text. The node now folds text the way the kernel does (policy.fold_text) before the floor, and the floor works on its own
    when the kernel package cannot be imported."""
    SPELLINGS = ("https://chase\u3002com/", "https://chase\uff61com/", "chase\uff0ecom", "https://ch\u200base.com/", "ch\u00adase.com",
                 "https://www.hsbc.co.uk/", "HSBC UK - Personal Banking", "hsbc\u3002co\u3002uk", "ICI\u200bCI Bank - Chrome",
                 "https://%63hase.com/", "JPMorgan Chase - Chrome")

    def test_the_node_refuses_every_spelling(self):
        for text in self.SPELLINGS:
            with self.subTest(text=text):
                self.assertTrue(sensitive.sensitive_target(text), text)
                self.assertTrue(sensitive.why(text), text)

    def test_the_floor_alone_refuses_them_when_the_kernel_is_missing(self):
        """Run sensitive_target with the kernel check switched off: only FLOOR and the node's own folding remain."""
        real = sensitive._kernel_check
        sensitive._kernel_check = None
        try:
            for text in ("https://chase\u3002com/", "https://chase\uff61com/", "chase\uff0ecom", "https://www.hsbc.co.uk/",
                         "https://ch\u200base.com/".replace("ch\u200base", "chase"), "https://%63hase.com/", "ICI\u200bCI Bank - Chrome"):
                with self.subTest(text=text):
                    self.assertTrue(sensitive.sensitive_target(text), text)
            for text in ("https://example.org/", "https://en.wikipedia.org/wiki/Main_Page", "Notepad", "Paper Chase - Notepad"):
                with self.subTest(text=text):
                    self.assertEqual(sensitive.sensitive_target(text), "", text)
        finally:
            sensitive._kernel_check = real

    def test_the_nodes_fallback_folding_is_the_kernels(self):
        from jarvis_kernel.policy import _unquote, fold_text
        for text in self.SPELLINGS + ("A\uff0eB\u3002C\uff61D\u2024E\u200b\u00ad\ufeffF", "%E3%80%82", "\uff23\uff28\uff21\uff33\uff25", "plain text"):
            self.assertEqual(sensitive._fold_fallback(text), fold_text(_unquote(text)), text)

    def test_unrelated_names_stay_allowed(self):
        for text in ("https://example.org/", "https://en.wikipedia.org/wiki/Chase", "Notepad", "notes.txt - Notepad", "Paper Chase - Notepad",
                     "https://notchase.com/", "https://chase.com.evil.test/"):
            with self.subTest(text=text):
                self.assertEqual(sensitive.sensitive_target(text), "", text)


if __name__ == "__main__":
    unittest.main()
