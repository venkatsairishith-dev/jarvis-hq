"""AI chat websites are opened for the owner, never driven (plan P9 step 6, 2026-10-24).

chatgpt.com, chat.openai.com, gemini.google.com and claude.ai forbid automated use of the chat and programmatic extraction of
its answers; JARVIS asks those models through their APIs. The kernel refuses every browser or UI action there except opening
the page, and the node refuses the same on the live page address (the kernel does not see the address of an open tab)."""
import os
import sys
import unittest

HERE = os.path.dirname(__file__)
sys.path.insert(0, os.path.join(HERE, ".."))
sys.path.insert(0, os.path.join(HERE, "..", "..", "node"))
from jarvis_kernel.policy import DENY, YELLOW_APPROVAL, Action, ai_chat_host, ai_chat_target, decide  # noqa: E402
from jarvis_node import browser, browserext, sensitive  # noqa: E402

CHAT_URLS = ("https://chatgpt.com/", "https://chatgpt.com/c/abc", "chatgpt.com", "https://chat.openai.com/",
             "https://gemini.google.com/app", "https://claude.ai/new", "HTTPS://CHATGPT.COM/", "https://chatgpt。com/",
             "https://user@chatgpt.com:443/", "https://www.chatgpt.com./")
NOT_CHAT = ("https://openai.com/research", "https://platform.openai.com/docs", "https://ai.google.dev/gemini-api/docs",
            "https://www.google.com/search?q=chatgpt+alternatives", "https://notchatgpt.com/", "https://chatgpt.com.example.org/",
            "https://en.wikipedia.org/wiki/ChatGPT", "https://docs.anthropic.com/", "https://www.gemini.com/")


class Kernel(unittest.TestCase):
    def test_hosts(self):
        for u in CHAT_URLS:
            with self.subTest(u=u):
                self.assertTrue(ai_chat_target([u]), u)
        for u in NOT_CHAT:
            with self.subTest(u=u):
                self.assertEqual(ai_chat_target([u]), "", u)
        self.assertEqual(ai_chat_host("www.gemini.google.com"), "gemini.google.com")
        self.assertEqual(ai_chat_host("google.com"), "")

    def test_opening_stays_allowed(self):
        for u in CHAT_URLS[:6]:
            with self.subTest(u=u):
                self.assertLess(decide(Action("browser", "open", u)).level, DENY)

    def test_driving_is_denied(self):
        for op in ("snapshot", "extract", "click", "type", "select", "press"):
            for u in ("https://chatgpt.com/", "https://gemini.google.com/app", "https://claude.ai/new"):
                with self.subTest(op=op, u=u):
                    d = decide(Action("browser", op, u, {"text": "summarise my plan"}))
                    self.assertEqual(d.level, DENY, d.reason)
                    self.assertIn("against its terms", d.reason)

    def test_the_chatgpt_app_is_not_typed_into(self):
        d = decide(Action("ui", "type", "ChatGPT|Message ChatGPT", {"text": "hello"}))
        self.assertEqual(d.level, DENY, d.reason)
        d = decide(Action("ui", "type", "chrome|Message ChatGPT", {"text": "hello"}))
        self.assertEqual(d.level, DENY, d.reason)

    def test_the_chatgpt_app_can_still_be_started_and_arranged(self):
        for op in ("launch", "focus", "minimize", "maximize", "restore"):
            with self.subTest(op=op):
                self.assertLess(decide(Action("ui", op, "ChatGPT", {})).level, DENY)
        for op in ("snapshot", "click", "key", "scroll", "click_at"):
            with self.subTest(op=op):
                self.assertEqual(decide(Action("ui", op, "ChatGPT|Send", {})).level, DENY)

    def test_typing_the_word_elsewhere_is_fine(self):
        d = decide(Action("browser", "type", "https://www.google.com/", {"text": "chatgpt alternatives"}))
        self.assertLess(d.level, DENY, d.reason)
        d = decide(Action("ui", "type", "notepad|Text editor", {"text": "notes about ChatGPT"}))
        self.assertLess(d.level, DENY, d.reason)

    def test_other_ordinary_sites_keep_their_rules(self):
        self.assertEqual(decide(Action("browser", "click", "https://example.com/", {})).level, YELLOW_APPROVAL)


class FakeSession:
    def __init__(self, url):
        self.url, self.calls = url, []

    def call(self, tool, args, timeout=45.0):
        self.calls.append(tool)
        return f"- Page URL: {self.url}\n- Page Title: Page\n- button \"Send\" [ref=e1]\n- textbox \"Message\" [ref=e2]"


class Node(unittest.TestCase):
    def test_floor_and_kernel_agree(self):
        for u in CHAT_URLS:
            with self.subTest(u=u):
                self.assertTrue(sensitive.ai_chat_site(u), u)
        for u in NOT_CHAT:
            with self.subTest(u=u):
                self.assertEqual(sensitive.ai_chat_site(u), "", u)

    def test_owner_profile_never_reads_clicks_types_or_presses_on_a_chat_page(self):
        prof = {"dir": "Default", "account": "x"}
        for op, target in (("read", ""), ("find", "Send"), ("click", "Send"), ("type", "Message"), ("press", "")):
            with self.subTest(op=op):
                s = FakeSession("https://chatgpt.com/c/123")
                with self.assertRaises(browserext.BrowserError) as e:
                    browserext.act(s, op, "", target, "hi", prof)
                self.assertIn("its terms forbid automated use", str(e.exception))
                self.assertNotIn("browser_click", s.calls)
                self.assertNotIn("browser_type", s.calls)
                self.assertNotIn("browser_press_key", s.calls)

    def test_owner_profile_still_opens_it_and_works_elsewhere(self):
        prof = {"dir": "Default", "account": "x"}
        r = browserext.act(FakeSession("https://chatgpt.com/"), "open", "https://chatgpt.com/", "", "", prof)
        self.assertTrue(r["ok"])
        s = FakeSession("https://example.org/")
        r = browserext.act(s, "click", "", "Send", "", prof)
        self.assertTrue(r["ok"])
        self.assertIn("browser_click", s.calls)

    def test_agent_browser_never_opens_one(self):
        for u in ("https://chatgpt.com/", "https://gemini.google.com/app", "https://claude.ai/"):
            with self.subTest(u=u):
                with self.assertRaises(browser.BrowserError):
                    browser.check_url(u)


if __name__ == "__main__":
    unittest.main()
