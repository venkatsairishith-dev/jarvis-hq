import os
import sys
import unittest

HERE = os.path.dirname(__file__)
sys.path.insert(0, os.path.join(HERE, ".."))
sys.path.insert(0, os.path.join(HERE, "..", "..", "node"))
from jarvis_kernel.policy import Action, GREEN, YELLOW_APPROVAL, DENY, RED, decide


class UIPolicy(unittest.TestCase):
    def test_observe_is_green_acting_needs_approval(self):
        for op in ("snapshot", "list_windows"):
            self.assertEqual(decide(Action("ui", op, "notepad")).level, GREEN, op)
        for op in ("launch", "focus", "click", "type", "key", "scroll"):
            self.assertEqual(decide(Action("ui", op, "notepad")).level, YELLOW_APPROVAL, op)

    def test_unknown_ui_op_fails_closed(self):
        self.assertEqual(decide(Action("ui", "drag_and_drop_files", "notepad")).level, RED)

    def test_sensitive_apps_always_denied_even_to_look(self):
        for target in ("1Password", "Bitwarden - vault", "KeePassXC", "MetaMask Wallet", "PayPal - Chrome", "My Bank - Home", "Windows Credential Manager"):
            for op in ("snapshot", "click", "type"):
                self.assertEqual(decide(Action("ui", op, target)).level, DENY, (target, op))

    def test_no_false_positive_on_blank_or_bankruptcy_words(self):
        self.assertEqual(decide(Action("ui", "snapshot", "blank document")).level, GREEN)
        self.assertEqual(decide(Action("ui", "snapshot", "notes.txt - Notepad")).level, GREEN)

    def test_model_cannot_lower_ui_levels(self):
        self.assertEqual(decide(Action("ui", "click", "notepad"), suggested=GREEN).level, YELLOW_APPROVAL)

    def test_secret_file_open_in_editor_is_denied(self):
        self.assertEqual(decide(Action("ui", "snapshot", "keys.json - Notepad")).level, DENY)


@unittest.skipUnless(os.name == "nt", "Windows UI Automation only")
class UISnapshot(unittest.TestCase):
    def test_bad_process_names_rejected(self):
        from jarvis_node.ui import snapshot
        for bad in ("Code; calc", "x' ; Remove-Item", "a b", "$(whoami)", "x|y"):
            with self.assertRaises(ValueError, msg=bad):
                snapshot(bad)

    def test_snapshot_shape_and_limits(self):
        from jarvis_node.ui import snapshot
        s = snapshot("explorer", max_elements=15, max_depth=3)  # Explorer is always running on Windows
        if s.get("error"):
            self.skipTest("no explorer window with a handle")
        self.assertLessEqual(len(s["elements"]), 15)
        self.assertIn("window", s)
        for e in s["elements"]:
            self.assertEqual(set(e), {"depth", "type", "name", "id", "enabled", "offscreen", "value"})
            self.assertIsNone(e["value"])  # values are off by default


if __name__ == "__main__":
    unittest.main()
