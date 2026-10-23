"""JARVIS's PC control (2026-10-16): the owner's direct requests, consequential actions, the approval round trip, new job
kinds, and cancellation. The owner_direct level applies only on the owner plane; agents and nodes keep the old floor."""
import os
import sys
import unittest

HERE = os.path.dirname(__file__)
sys.path.insert(0, os.path.join(HERE, ".."))
sys.path.insert(0, HERE)
from jarvis_kernel.jobs import JobError, Jobs, _check_params, action_for  # noqa: E402
from jarvis_kernel.policy import Action, DENY, GREEN, RED, YELLOW_APPROVAL, YELLOW_AUTO, decide  # noqa: E402
import test_owner as _owner_tests  # noqa: E402  (its fixture only: importing the class itself would re-run its tests here)


def owner(tool, op, target="", params=None, **kw):
    return Action(tool, op, target, params or {}, owner_direct=True, **kw)


class OwnerDirectPolicy(unittest.TestCase):
    def test_ordinary_owner_actions_run_at_once_but_only_on_the_owner_plane(self):
        for op in ("launch", "focus", "click", "type", "key", "scroll", "click_at"):
            self.assertEqual(decide(owner("ui", op, "notepad|Text editor", {"text": "hello"} if op == "type" else {})).level, YELLOW_AUTO, op)
            self.assertEqual(decide(Action("ui", op, "notepad")).level, YELLOW_APPROVAL, op)          # nodes and agents: unchanged
        for op in ("click", "type", "select"):
            self.assertEqual(decide(owner("browser", op, "Search box", {"profile": "default"})).level, YELLOW_AUTO, op)
        self.assertEqual(decide(owner("ui", "click", "notepad|OK", untrusted_session=True)).level, YELLOW_APPROVAL)
        self.assertEqual(decide(owner("ui", "click", "notepad|OK", lane="public")).level, YELLOW_APPROVAL)
        self.assertEqual(decide(owner("ui", "click", "notepad|OK"), suggested=RED).level, RED)      # a model can still raise it

    def test_consequential_words_need_the_owner(self):
        for target in ("Buy now", "Place order", "Send", "Delete account", "Sign out", "Subscribe", "I agree", "Install"):
            self.assertEqual(decide(owner("browser", "click", target, {"profile": "default"})).level, YELLOW_APPROVAL, target)
            self.assertEqual(decide(owner("ui", "click", "app|" + target)).level, YELLOW_APPROVAL, target)
        self.assertEqual(decide(owner("browser", "click", "Kalyani song - official video", {"profile": "default"})).level, YELLOW_AUTO)

    def test_keys(self):
        self.assertEqual(decide(owner("ui", "key", "notepad|", {"text": "ctrl+s"})).level, YELLOW_AUTO)
        self.assertEqual(decide(owner("ui", "key", "notepad|", {"text": "alt+f4"})).level, YELLOW_APPROVAL)
        self.assertEqual(decide(owner("ui", "key", "WhatsApp|", {"text": "enter"})).level, YELLOW_APPROVAL)
        self.assertEqual(decide(owner("ui", "key", "notepad|", {"text": "enter"})).level, YELLOW_AUTO)
        self.assertEqual(decide(owner("browser", "press", "Search", {"profile": "default", "text": "Enter"})).level, YELLOW_AUTO)
        self.assertEqual(decide(owner("browser", "press", "Message body", {"profile": "default", "text": "Enter"})).level, YELLOW_APPROVAL)

    def test_the_old_refusals_still_win(self):
        self.assertEqual(decide(owner("browser", "click", "Pay", {"profile": "default", "site": "paypal"})).level, DENY)
        self.assertEqual(decide(owner("ui", "type", "keepass|Search")).level, DENY)
        self.assertEqual(decide(owner("browser", "open", "http://127.0.0.1:8799/", {"profile": "default"})).level, DENY)
        self.assertEqual(decide(owner("browser", "open", "file:///C:/Windows/win.ini", {"profile": "default"})).level, DENY)

    def test_diagnostics_repairs_and_media(self):
        self.assertEqual(decide(Action("diag", "app_errors", "notepad")).level, GREEN)
        self.assertEqual(decide(Action("diag", "app_errors", "notepad", lane="public")).level, YELLOW_APPROVAL)
        self.assertEqual(decide(Action("diag", "window_ocr", "notepad")).level, GREEN)
        self.assertEqual(decide(Action("diag", "window_locate", "notepad|OK")).level, GREEN)
        self.assertEqual(decide(Action("diag", "window_ocr", "notepad", lane="public")).level, YELLOW_APPROVAL)
        self.assertEqual(decide(Action("media", "state", "system")).level, GREEN)
        self.assertEqual(decide(Action("repair", "restart_app", "notepad")).level, YELLOW_APPROVAL)
        self.assertEqual(decide(Action("repair", "install", "Notepad++.Notepad++")).level, YELLOW_APPROVAL)
        self.assertEqual(decide(Action("repair", "restore", "20261016-101500-abcdef")).level, YELLOW_AUTO)
        self.assertEqual(decide(Action("repair", "edit_file", r"D:\apps\sample\config.ini", {"find": "a", "replace": "b"})).level, YELLOW_APPROVAL)
        # any settings.json stays console-only (the rule that protects the files agents run under)
        self.assertEqual(decide(Action("repair", "edit_file", r"D:\apps\sample\settings.json", {"find": "a", "replace": "b"})).level, RED)
        self.assertEqual(decide(Action("repair", "edit_file", r"D:\jarvis\hq\jarvis\kernel\jarvis_kernel\policy.py",
                                       {"find": "a", "replace": "b"})).level, RED)
        self.assertEqual(decide(Action("repair", "edit_file", r"D:\jarvis-secrets\providers\groq.key", {"find": "a", "replace": "b"})).level, DENY)
        self.assertEqual(decide(Action("repair", "uninstall", "x")).level, RED)                   # not offered: fails closed

    def test_owner_direct_is_part_of_the_digest(self):
        self.assertNotEqual(owner("ui", "click", "a|b").digest(), Action("ui", "click", "a|b").digest())
        self.assertEqual(Action("ui", "click", "a|b").digest(), Action("ui", "click", "a|b").digest())


class NewJobKinds(unittest.TestCase):
    def ok(self, kind, p):
        _check_params(kind, p)

    def bad(self, kind, p):
        with self.assertRaises(JobError):
            _check_params(kind, p)

    def test_browser(self):
        b = {"profile": "default", "op": "open", "url": "https://www.youtube.com/", "target": "", "text": ""}
        self.ok("browser_ext", b)
        self.ok("browser_ext", dict(b, op="play", url="", target="Kalyani song"))
        self.ok("browser_ext", dict(b, op="press", url="", text="Enter"))
        self.bad("browser_ext", dict(b, url=""))                                  # open needs a URL
        self.bad("browser_ext", dict(b, url="javascript:alert(1)"))
        self.bad("browser_ext", dict(b, profile="Profile 1"))                     # an alias, not a folder name
        self.bad("browser_ext", dict(b, op="evaluate"))
        self.bad("browser_ext", dict(b, target="a\nb"))
        self.bad("browser_ext", dict(b, cookie="x"))
        a = action_for("browser_ext", dict(b, op="click", url="", target="Sign out"), owner_direct=True)
        self.assertEqual((a.tool, a.op, a.target, a.params["profile"]), ("browser", "click", "Sign out", "default"))
        self.assertEqual(decide(a).level, YELLOW_APPROVAL)

    def test_ui_diag_repair_media(self):
        self.ok("ui_action", {"agent": "owner", "op": "key", "target": "notepad|", "text": "ctrl+s"})
        self.bad("ui_action", {"agent": "owner", "op": "key", "target": "notepad|", "text": "{ENTER}"})
        self.ok("ui_action", {"agent": "owner", "op": "click_at", "target": "notepad|120,40", "text": ""})
        self.bad("ui_action", {"agent": "owner", "op": "click_at", "target": "notepad|-1,40", "text": ""})
        self.ok("diag", {"op": "app_errors", "target": "notepad"})
        self.bad("diag", {"op": "shell", "target": "whoami"})
        self.bad("diag", {"op": "processes", "target": "a;b"})
        # perception of one window (2026-10-16): read-only; locate takes "app|label", nothing else does
        self.ok("diag", {"op": "window_ocr", "target": "JARVISSampleApp"})
        self.ok("diag", {"op": "window_locate", "target": "Calculator|Equals"})
        self.bad("diag", {"op": "window_locate", "target": "Calculator"})
        self.bad("diag", {"op": "window_locate", "target": "Calculator|a\nb"})
        self.bad("diag", {"op": "window_ocr", "target": "Calculator|Equals"})
        self.bad("diag", {"op": "window_locate", "target": "a;b|x"})
        self.ok("repair", {"op": "edit_file", "target": r"D:\apps\x\settings.json", "args": {"find": "a", "replace": "b"}})
        self.bad("repair", {"op": "edit_file", "target": r"D:\x", "args": {"find": "", "replace": "b"}})
        self.ok("repair", {"op": "restore", "target": "20261016-101500-abcdef", "args": {}})
        self.bad("repair", {"op": "restore", "target": "../x", "args": {}})
        self.ok("repair", {"op": "install", "target": "Notepad++.Notepad++", "args": {}})
        self.bad("repair", {"op": "install", "target": "x && calc", "args": {}})
        self.bad("repair", {"op": "run", "target": "cmd", "args": {}})
        self.ok("media_state", {})


class OwnerPlanePC(unittest.TestCase):
    setUp, tearDown, call = _owner_tests.Plane.setUp, _owner_tests.Plane.tearDown, _owner_tests.Plane.call

    def test_ordinary_ui_action_is_queued_at_once(self):
        code, r = self.call("POST", "/v1/jarvis/owner/pc", {"op": "ui", "action": "click", "target": "notepad|Edit"})
        self.assertEqual((code, r["status"], r["kind"]), (202, "queued", "ui_action"))
        code, r = self.call("POST", "/v1/jarvis/owner/pc", {"op": "browser", "action": "open", "profile": "venkatamv",
                                                            "url": "https://www.youtube.com/"})
        self.assertEqual((code, r["kind"]), (202, "browser_ext"))
        self.assertEqual(self.call("POST", "/v1/jarvis/owner/pc", {"op": "diag", "check": "disk"})[1]["kind"], "diag")
        self.assertEqual(self.call("POST", "/v1/jarvis/owner/pc", {"op": "media"})[1]["kind"], "media_state")

    def test_consequential_action_waits_for_the_owner_and_the_approval_is_single_use_and_bound(self):
        body = {"op": "browser", "action": "click", "profile": "default", "target": "Buy now"}
        code, r = self.call("POST", "/v1/jarvis/owner/pc", body)
        self.assertEqual((code, r["status"], r["level"]), (202, "needs_approval", "YELLOW_APPROVAL"))
        aid = r["approval_id"]
        self.assertEqual(self.call("POST", "/v1/jarvis/owner/pc", dict(body, approval_id=aid))[0], 403)   # not approved yet
        self.assertTrue(self.k.approve(aid))                                                        # the owner, at the console
        other = {"op": "browser", "action": "click", "profile": "venkatamv", "target": "Buy now", "approval_id": aid}
        self.assertEqual(self.call("POST", "/v1/jarvis/owner/pc", other)[0], 403)                   # another account: not this approval
        code, r = self.call("POST", "/v1/jarvis/owner/pc", dict(body, approval_id=aid))
        self.assertEqual((code, r["status"]), (202, "queued"))
        self.assertEqual(self.call("POST", "/v1/jarvis/owner/pc", dict(body, approval_id=aid))[0], 403)   # used once
        self.assertEqual([a["id"] for a in self.call("GET", "/v1/jarvis/owner/state")[1].get("approvals") or []], [])   # none left waiting

    def test_refusals(self):
        self.assertEqual(self.call("POST", "/v1/jarvis/owner/pc", {"op": "browser", "action": "open", "url": "http://192.168.1.1/"})[0], 403)
        self.assertEqual(self.call("POST", "/v1/jarvis/owner/pc", {"op": "repair", "action": "edit_file",
                                                                   "target": r"D:\jarvis-secrets\x.key", "args": {"find": "a", "replace": "b"}})[0], 403)
        self.assertEqual(self.call("POST", "/v1/jarvis/owner/pc", {"op": "browser", "action": "eval"})[0], 400)
        # an agent token cannot use the owner plane at all
        self.assertIn(self.call("POST", "/v1/jarvis/owner/pc", {"op": "media"}, tok=self.agent)[0], (401, 403))

    def test_cancel_queued_and_running(self):
        jid = self.call("POST", "/v1/jarvis/owner/pc", {"op": "diag", "check": "processes"})[1]["job"]
        self.assertEqual(self.call("POST", "/v1/jarvis/owner/job/cancel", {"id": jid})[1]["state"], "cancelled")
        self.assertEqual(self.call("GET", "/v1/jarvis/owner/job/" + jid)[1]["state"], "cancelled")
        jobs = Jobs(self.k)
        jid = self.call("POST", "/v1/jarvis/owner/pc", {"op": "diag", "check": "disk"})[1]["job"]
        self.assertEqual(jobs.next_for("laptop")["id"], jid)
        self.assertEqual(self.call("POST", "/v1/jarvis/owner/job/cancel", {"id": jid})[1]["state"], "cancelling")
        self.assertEqual(jobs.cancels_for("laptop"), [jid])
        self.assertTrue(jobs.finish("laptop", jid, {"ok": False, "cancelled": True, "error": "cancelled by the owner"}))
        self.assertEqual(self.call("GET", "/v1/jarvis/owner/job/" + jid)[1]["state"], "cancelled")
        self.assertEqual(jobs.cancels_for("laptop"), [])
        self.assertEqual(self.call("POST", "/v1/jarvis/owner/job/cancel", {"id": jid})[1]["note"], "nothing to cancel")


if __name__ == "__main__":
    unittest.main()
