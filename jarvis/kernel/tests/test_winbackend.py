import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(__file__)
sys.path.insert(0, os.path.join(HERE, ".."))
sys.path.insert(0, os.path.join(HERE, "..", "..", "node"))
from jarvis_kernel.policy import Action
from jarvis_kernel.store import Kernel


class AuditScrub(unittest.TestCase):
    def test_typed_secrets_never_reach_the_audit_log(self):
        tmp = tempfile.TemporaryDirectory(); k = Kernel(os.path.join(tmp.name, "d"))
        a = Action("ui", "type", "notepad|edit", {"text": "my token gho_abcdefghijklmnopqrstuvwxyz0123 and API_KEY=hunter2hunter2"})
        k.request(a); k.authorize(a)
        rows = " ".join(r[0] for r in k.db.execute("SELECT payload FROM audit"))
        self.assertNotIn("gho_abc", rows); self.assertNotIn("hunter2", rows)
        k.db.close(); tmp.cleanup()

    def test_long_text_truncated_and_private_key_blocked_in_log(self):
        d = Action("ui", "type", "notepad|edit", {"text": "A" * 5000}).describe()
        self.assertLess(len(d["params"]["text"]), 260)
        d2 = Action("ui", "type", "x|y", {"text": "-----BEGIN OPENSSH PRIVATE KEY-----"}).describe()
        self.assertIn("BLOCKED", d2["params"]["text"])


@unittest.skipUnless(os.name == "nt", "Windows only")
class Backend(unittest.TestCase):
    def test_injection_attempts_rejected_before_powershell(self):
        from jarvis_node import winbackend as w
        for bad in ("notepad", "notepad|", "no pad;calc|x", "$(whoami)|x", "a|b\nc", "x" * 100 + "|y", "a`b|c", "|x"):
            with self.assertRaises(ValueError, msg=bad):
                w.click(bad, {})
        with self.assertRaises(ValueError):
            w.type_text("notepad|edit", {"text": "x" * 3000})

    def test_launch_only_from_allowlist(self):
        from jarvis_node import winbackend as w
        for bad in ("cmd", "powershell", "C:\\Windows\\System32\\cmd.exe", "notepad.exe /c calc", "", "calc; cmd"):
            with self.assertRaises(ValueError, msg=bad):
                w.launch(bad, {})

    def test_backend_ops_cover_only_known_actions(self):
        from jarvis_node import winbackend as w
        from jarvis_kernel.jobs import UI_OPS
        # exactly what the kernel's job validator accepts (focus, key, scroll and click_at added 2026-10-16), nothing more
        self.assertEqual(set(w.BACKEND), UI_OPS)
        self.assertEqual(UI_OPS, {"launch", "click", "type", "focus", "key", "scroll", "click_at"})


if __name__ == "__main__":
    unittest.main()
