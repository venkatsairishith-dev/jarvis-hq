"""Playwright MCP hardening (2026-10-17, evidence 08): from 0.0.82 Playwright MCP lists the tools a web PAGE registers
(WebMCP) as MCP tools named webmcp_*. Their names, descriptions and schemas come from whatever site is open, so the kernel
refuses them: in policy (every path that asks the kernel), when the owner tries to enable one, and on a call, audited.
Ordinary browser tools are unaffected. The node starts only the pinned @playwright/mcp version."""
import json
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))
sys.path.insert(0, os.path.join(HERE, "..", "..", "node"))
sys.path.insert(0, HERE)
import test_connectors as TC                 # its Base: a fake stdio MCP server behind the real node connector code
from jarvis_kernel.connectors import ConnectorError
from jarvis_kernel.policy import Action, DENY, GREEN, YELLOW_AUTO, decide
from jarvis_kernel.store import Kernel


def call_action(tool, risk="read", lane="private"):
    return Action("connector", "call", f"playwright.{tool}", {"risk": risk, "public_ok": False, "digest": "d" * 64, "args": {}}, lane, True)


class Policy(unittest.TestCase):
    def test_a_page_tool_is_refused_whatever_its_risk_class(self):
        for risk in ("read", "write", "send"):
            d = decide(call_action("webmcp_add_to_cart", risk))
            self.assertEqual(d.level, DENY, risk); self.assertIn("WebMCP", d.reason)
        self.assertEqual(decide(call_action("WebMCP_Checkout")).level, DENY)                      # case does not matter
        self.assertEqual(decide(call_action("\uff57ebmcp_checkout")).level, DENY)                 # nor a full-width letter

    def test_a_page_tool_named_as_the_action_itself_is_refused(self):
        # a node or client asking the kernel directly (/request, /authorize) with the tool's own name
        self.assertEqual(decide(Action("webmcp_add_to_cart", "call", "https://shop.example")).level, DENY)
        self.assertEqual(decide(Action("browser", "webmcp_add_to_cart", "https://shop.example")).level, DENY)

    def test_ordinary_browser_tools_are_unaffected(self):
        self.assertEqual(decide(call_action("browser_snapshot")).level, YELLOW_AUTO)
        self.assertEqual(decide(Action("browser", "snapshot", "https://example.com")).level, GREEN)
        self.assertEqual(decide(Action("fs", "read", "C:/work/webmcp_notes.md")).level, GREEN)   # a file name is not a tool

    def test_the_refusal_is_audited(self):
        with tempfile.TemporaryDirectory() as t:
            k = Kernel(os.path.join(t, "d"))
            d = k.authorize(call_action("webmcp_add_to_cart"))
            self.assertFalse(d.allowed)
            row = k.db.execute("SELECT payload FROM audit WHERE kind='authorize' ORDER BY seq DESC LIMIT 1").fetchone()
            p = json.loads(row[0])
            self.assertEqual((p["allowed"], p["level"]), (False, "DENY")); self.assertIn("webmcp_add_to_cart", p["action"]["target"])
            self.assertTrue(k.audit.verify()[0])
            k.db.close()


class Connector(TC.Base):
    """The same server lists an ordinary browser tool and a page's WebMCP tool."""
    flags = ["--webmcp"]

    def test_the_owner_cannot_enable_a_page_tool(self):
        self.ready((("browser_snapshot", "read"),))
        with self.assertRaises(ConnectorError) as e:
            self.enable("webmcp_add_to_cart", "read")
        self.assertIn("WebMCP", str(e.exception))
        self.assertFalse(next(t for t in self.svc.tools("fake") if t["tool"] == "webmcp_add_to_cart")["enabled"])

    def test_a_page_tool_enabled_in_an_older_registry_is_still_refused_and_never_reaches_the_server(self):
        self.ready((("browser_snapshot", "read"),))
        self.k.db.execute("UPDATE connector_tools SET risk='read', enabled=1, approved_digest=digest WHERE connector='fake' AND tool='webmcp_add_to_cart'")
        code, r = self.call("webmcp_add_to_cart", {"item": "laptop"})
        self.assertEqual(code, 403); self.assertIn("WebMCP", r["reason"])
        req = [json.loads(p) for (p,) in self.k.db.execute("SELECT payload FROM audit WHERE kind='request'").fetchall()]
        self.assertTrue(any(x["level"] == "DENY" and "webmcp_add_to_cart" in x["action"]["target"] for x in req))
        with open(self.state, encoding="utf-8") as fh:
            sent = [json.loads(line) for line in fh]
        self.assertFalse([m for m in sent if m.get("method") == "tools/call"])
        # the ordinary browser tool of the same server still works
        code, r = self.call("browser_snapshot")
        self.assertEqual((code, r["status"]), (200, "done")); self.assertIn("Shop", r["result"]["text"])


class Pin(unittest.TestCase):
    """The node starts only the pinned @playwright/mcp (browserext.MCP_VERSION)."""
    def setUp(self):
        from jarvis_node import browserext
        self.X = browserext
        self.tmp = tempfile.TemporaryDirectory()
        self.cli = os.path.join(self.tmp.name, "node_modules", "@playwright", "mcp", "cli.js")
        os.makedirs(os.path.dirname(self.cli))
        self.old = os.environ.get("JARVIS_OUTPUTS")
        os.environ["JARVIS_OUTPUTS"] = self.tmp.name

    def tearDown(self):
        if self.old is None:
            os.environ.pop("JARVIS_OUTPUTS", None)
        else:
            os.environ["JARVIS_OUTPUTS"] = self.old
        self.tmp.cleanup()

    def package(self, version, name="@playwright/mcp"):
        with open(os.path.join(os.path.dirname(self.cli), "package.json"), "w", encoding="utf-8") as fh:
            json.dump({"name": name, "version": version}, fh)

    def test_the_pin_is_the_documented_release(self):
        self.assertEqual(self.X.MCP_VERSION, "0.0.83")

    def test_only_the_pinned_version_passes(self):
        self.package("0.0.83")
        self.assertEqual(self.X.check_mcp_version(self.cli), "0.0.83")
        for v, name in (("0.0.84", "@playwright/mcp"), ("0.0.82", "@playwright/mcp"), ("0.0.83", "some-other-package")):
            self.package(v, name)
            with self.assertRaises(self.X.BrowserError) as e:
                self.X.check_mcp_version(self.cli)
            self.assertIn("@playwright/mcp@0.0.83", str(e.exception))
        os.remove(os.path.join(os.path.dirname(self.cli), "package.json"))
        with self.assertRaises(self.X.BrowserError):
            self.X.check_mcp_version(self.cli)

    def test_a_wrong_version_is_never_started(self):
        self.package("0.0.84")
        old = self.X.MCP_CLI
        self.X.MCP_CLI = self.cli
        try:
            with self.assertRaises(self.X.BrowserError):
                self.X.Session({"dir": "Default", "name": "Owner", "account": ""})      # the real server path: no factory
        finally:
            self.X.MCP_CLI = old


if __name__ == "__main__":
    unittest.main()
