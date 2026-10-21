"""Regression tests added after mutation testing of policy.py (2026-10-19). Each pins a decide() / caretaker rule that a deliberate
code mutation survived: lane-dependent levels for diag/media/ui, skill and connector rules, risky keys, the WebMCP deny on a
connector target, and two parts of the caretaker text check."""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from jarvis_kernel.policy import Action, DENY, GREEN, YELLOW_APPROVAL, YELLOW_AUTO, decide
from jarvis_kernel.policy import _ct_denylist, _ct_structure


def lvl(tool, op, target="", **kw):
    return decide(Action(tool, op, target, **kw))


class LaneRules(unittest.TestCase):
    def test_diag_and_media_reads_need_approval_only_from_the_public_lane(self):
        for tool, op in (("diag", "processes"), ("diag", "window_text"), ("media", "state")):
            with self.subTest(tool=tool, op=op):
                self.assertEqual(lvl(tool, op, lane="private").level, GREEN)
                d = lvl(tool, op, lane="public")
                self.assertEqual(d.level, YELLOW_APPROVAL)
                self.assertIn("lab agent", d.reason)

    def test_ui_desktop_views_need_approval_only_from_the_public_lane(self):
        for op in ("snapshot", "list_windows", "screenshot"):
            with self.subTest(op=op):
                self.assertLess(lvl("ui", op, lane="private").level, YELLOW_APPROVAL)
                d = lvl("ui", op, lane="public")
                self.assertEqual(d.level, YELLOW_APPROVAL)
                self.assertIn("desktop", d.reason)

    def test_skill_activation_rules(self):
        act = lambda lane="private", **p: lvl("skill", "activate", lane=lane, params=p)
        self.assertEqual(act(risk="low", where="lab").level, YELLOW_AUTO)
        self.assertEqual(act(risk="low", where="owner").level, YELLOW_APPROVAL)
        self.assertEqual(act(risk="medium", where="lab").level, YELLOW_APPROVAL)
        for risk in ("high", "", "critical", "LOW-ish"):
            with self.subTest(risk=risk):
                d = act(risk=risk, where="lab")
                self.assertEqual(d.level, DENY)
                self.assertIn("never activated", d.reason)
        self.assertEqual(act(lane="public", risk="low", where="lab").level, DENY)
        self.assertIn("lab agent may not grant itself", act(lane="public", risk="low", where="lab").reason)

    def test_connector_call_rules(self):
        call = lambda risk, lane="private", **p: lvl("connector", "call", "mail.list", lane=lane, params=dict(p, risk=risk))
        self.assertEqual(call("read").level, YELLOW_AUTO)
        for risk in ("write", "send"):
            self.assertEqual(call(risk).level, YELLOW_APPROVAL)
        for risk in ("", "none", "readwrite", "x"):
            with self.subTest(risk=risk):
                self.assertEqual(call(risk).level, DENY)
        self.assertEqual(call("read", lane="public").level, DENY)                       # public lane needs public_ok is True
        self.assertEqual(call("read", lane="public", public_ok="yes").level, DENY)
        self.assertEqual(call("read", lane="public", public_ok=True).level, YELLOW_AUTO)

    def test_webmcp_tool_on_a_connector_target_is_denied(self):
        d = lvl("connector", "call", "mail.webmcp_send", params={"risk": "read"})
        self.assertEqual(d.level, DENY)
        self.assertIn("WebMCP", d.reason)
        self.assertEqual(lvl("connector", "call", "mail.webmcp_send", params={"risk": "read"}, lane="public").level, DENY)
        # the same word in the target of a non-connector tool is not a tool name
        self.assertNotIn("WebMCP", lvl("fs", "read", "a.webmcp_x").reason)


class OwnerDirectKeys(unittest.TestCase):
    def key(self, tool, op, text, target=""):
        return lvl(tool, op, target, params={"text": text}, owner_direct=True)

    def test_risky_keys_need_approval_and_ordinary_keys_do_not(self):
        for k in ("alt+f4", "ctrl+w", "ctrl+shift+w", "ctrl+d"):
            with self.subTest(key=k):
                d = self.key("ui", "key", k)
                self.assertEqual(d.level, YELLOW_APPROVAL)
                self.assertIn("this key can close", d.reason)
        for k in ("delete", "shift+delete", "ctrl+shift+delete"):        # the word "delete" is also caught as consequential text
            with self.subTest(key=k):
                self.assertEqual(self.key("ui", "key", k).level, YELLOW_APPROVAL)
        self.assertEqual(self.key("ui", "key", "a").level, YELLOW_AUTO)
        self.assertEqual(self.key("ui", "key", "tab").level, YELLOW_AUTO)

    def test_enter_in_a_browser_needs_approval_unless_it_is_a_search(self):
        self.assertEqual(self.key("browser", "press", "enter", "address bar").level, YELLOW_APPROVAL)
        self.assertEqual(self.key("browser", "press", "enter", "search box").level, YELLOW_AUTO)
        self.assertEqual(self.key("browser", "press", "enter", "SEARCH box").level, YELLOW_AUTO)
        self.assertEqual(self.key("browser", "press", "tab", "address bar").level, YELLOW_AUTO)
        # the owner's direct flag is what lowers the level: without it the same key is not auto
        self.assertEqual(lvl("ui", "key", params={"text": "a"}).level, YELLOW_APPROVAL)


class CaretakerText(unittest.TestCase):
    def test_every_control_character_outside_quoted_data_is_refused(self):
        base = "Caretaker fix for finding cf-00000000000000000000.\n"
        for ch in ("\x00", "\x01", "\t", "\r", "\x1f", "\x7f", "\x80", "​", "é"):
            with self.subTest(ch=repr(ch)):
                self.assertIn("text outside the quoted data has characters other than plain ASCII", _ct_structure(base + "x" + ch + "y", []))
        # a plain space and a newline are not control characters
        self.assertNotIn("text outside the quoted data has characters other than plain ASCII", _ct_structure(base + "x y\nz", []))

    def test_git_option_value_is_not_read_as_the_subcommand(self):
        alias = "uses a git command the caretaker template does not use (an alias or an expansion)"
        for text in ("run git -C repo status", "run git -C repo log", "run git --git-dir=.git --work-tree=. status",
                     "run git --git-dir .git status"):
            with self.subTest(text=text):
                self.assertNotIn(alias, _ct_denylist(text))
        self.assertIn(alias, _ct_denylist("run git frobnicate"))
        self.assertIn("a clause asks for a push to main", _ct_denylist("run git -C repo push origin main"))


class Describe(unittest.TestCase):
    def test_non_text_values_are_kept_as_they_are(self):
        d = Action("fs", "write", "a.txt", params={"n": 5, "f": 1.5, "off": False, "none": None, "list": [1, None, True]}).describe()
        self.assertEqual(d["params"], {"n": 5, "f": 1.5, "off": False, "none": None, "list": [1, None, True]})

    def test_audit_record_hides_pc_names_but_names_the_kind(self):
        for op, kind in (("open_app", "app"), ("open_folder", "folder"), ("open_file", "file"), ("list_folder", "folder")):
            with self.subTest(op=op):
                d = Action("pc", op, "CANARY-Priyanka-SYNTH", params={"path": "CANARY-path-SYNTH", "n": 3}).describe(200)
                self.assertEqual(d["target"], f"[{kind} name hidden, {len('CANARY-Priyanka-SYNTH')} characters]")
                self.assertEqual(d["params"], {"path": f"[path hidden, {len('CANARY-path-SYNTH')} characters]", "n": 3})
        # the approval card (a larger limit) shows the real names
        self.assertEqual(Action("pc", "open_app", "CANARY-Priyanka-SYNTH").describe(150_000)["target"], "CANARY-Priyanka-SYNTH")


if __name__ == "__main__":
    unittest.main()
