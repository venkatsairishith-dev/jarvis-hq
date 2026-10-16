"""Remote MCP over HTTPS: endpoint validation (SSRF, DNS rebinding, plain http), both protocol eras, SSE replies, redirects,
credentials, and the shape of results. No network: the resolver and the HTTP opener are injected."""
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from jarvis_kernel import mcphttp as H
from jarvis_kernel import mcpwire as W

PUBLIC = lambda host, port, **kw: [(2, 1, 6, "", ("93.184.216.34", port))]            # noqa: E731


def resolver_for(*ips):
    return lambda host, port, **kw: [(2, 1, 6, "", (ip, port)) for ip in ips]


class Endpoint(unittest.TestCase):
    def test_plain_http_and_embedded_credentials_are_refused(self):
        for url in ("http://example.com/mcp", "https://user:pw@example.com/mcp", "ftp://example.com/x", "//example.com/mcp", "https:///mcp"):
            with self.assertRaises(H.HttpMcpError, msg=url):
                H.check_endpoint(url, PUBLIC)

    def test_internal_and_private_addresses_are_refused_by_name_and_by_resolution(self):
        for url in ("https://localhost/mcp", "https://127.0.0.1/mcp", "https://10.1.2.3/mcp", "https://192.168.0.5/mcp", "https://169.254.169.254/latest",
                    "https://100.125.91.51/mcp", "https://[::1]/mcp", "https://2130706433/mcp", "https://0x7f.1/mcp", "https://app.internal/mcp",
                    "https://box.ts.net/mcp"):
            with self.assertRaises(H.HttpMcpError, msg=url) as cm:
                H.check_endpoint(url, PUBLIC)
            self.assertIn(cm.exception.kind, ("ssrf", "bad_endpoint"), url)
        # a public-looking name that resolves to a private address (DNS rebinding, split-horizon): refused
        with self.assertRaises(H.HttpMcpError) as cm:
            H.check_endpoint("https://innocent.example.org/mcp", resolver_for("10.0.0.7"))
        self.assertEqual(cm.exception.kind, "ssrf")
        # one bad address among good ones is enough
        with self.assertRaises(H.HttpMcpError):
            H.check_endpoint("https://mixed.example.org/mcp", resolver_for("93.184.216.34", "127.0.0.1"))

    def test_a_good_endpoint_is_pinned_to_the_address_that_was_checked(self):
        t = H.check_endpoint("https://mcp.example.org:8443/v1/mcp?x=1", resolver_for("93.184.216.34", "93.184.216.35"))
        self.assertEqual((t["host"], t["port"], t["ip"], t["path"]), ("mcp.example.org", 8443, "93.184.216.34", "/v1/mcp?x=1"))


class FakeServer:
    """An opener that plays a scripted server. `era` is "legacy" or "modern"."""

    def __init__(self, era="legacy", tools=None, redirect=False, status=200, sse=False, big=False):
        self.era, self.redirect, self.status, self.sse, self.big = era, redirect, status, sse, big
        self.requests = []
        self.tools = tools if tools is not None else [
            {"name": "lookup", "description": "Look something up", "inputSchema": {"type": "object", "properties": {"q": {"type": "string"}}, "required": ["q"]}},
            {"name": "post", "description": "Post a thing", "inputSchema": {"type": "object"}}]

    def __call__(self, target, method, headers, body, timeout):
        m = json.loads(body)
        self.requests.append((m, dict(headers)))
        if self.redirect:
            return 302, {"location": "https://elsewhere.example.org/"}, b""
        if self.status != 200:
            return self.status, {}, b"{}"
        method_name, rid = m.get("method"), m.get("id")
        if self.big:
            return 200, {"content-type": "application/json"}, b"x" * 10
        if method_name == "server/discover":
            if self.era == "modern":
                return self._ok(rid, {"resultType": "complete", "supportedVersions": ["2026-07-28"], "capabilities": {"tools": {}},
                                      "_meta": {"io.modelcontextprotocol/serverInfo": {"name": "remote", "version": "2"}}})
            return 400, {}, json.dumps({"jsonrpc": "2.0", "id": rid, "error": {"code": -32602, "message": "bad"}}).encode()
        if method_name == "initialize":
            return 200, {"content-type": "application/json", "mcp-session-id": "sess-1"}, json.dumps(
                {"jsonrpc": "2.0", "id": rid, "result": {"protocolVersion": "2025-06-18", "capabilities": {"tools": {}},
                                                         "serverInfo": {"name": "remote", "version": "1"}}}).encode()
        if method_name and method_name.startswith("notifications/"):
            return 202, {}, b""
        if method_name == "tools/list":
            return self._ok(rid, {"tools": self.tools})
        if method_name == "tools/call":
            return self._ok(rid, {"content": [{"type": "text", "text": "answer for " + json.dumps(m["params"]["arguments"])}], "isError": False})
        return 200, {"content-type": "application/json"}, json.dumps({"jsonrpc": "2.0", "id": rid, "error": {"code": -32601, "message": "no"}}).encode()

    def _ok(self, rid, result):
        payload = {"jsonrpc": "2.0", "id": rid, "result": result}
        if self.sse:
            return 200, {"content-type": "text/event-stream"}, ("event: message\ndata: " + json.dumps(payload) + "\n\n").encode()
        return 200, {"content-type": "application/json"}, json.dumps(payload).encode()


def session(srv, bearer=None):
    return H.HttpSession("https://mcp.example.org/mcp", bearer, resolver=PUBLIC, opener=srv)


class Eras(unittest.TestCase):
    def test_legacy_server_initialize_session_id_and_headers(self):
        srv = FakeServer("legacy")
        s = session(srv, "tok-123")
        hs = s.handshake()
        self.assertEqual((hs["era"], hs["server"]["name"]), ("legacy", "remote"))
        tools = s.list_tools()
        self.assertEqual([t["name"] for t in tools], ["lookup", "post"])
        last_headers = srv.requests[-1][1]
        self.assertEqual(last_headers["Mcp-Session-Id"], "sess-1")
        self.assertEqual(last_headers["Authorization"], "Bearer tok-123")
        self.assertEqual(srv.requests[-1][0]["method"], "tools/list")
        self.assertNotIn("_meta", srv.requests[-1][0].get("params", {}))

    def test_modern_server_needs_no_handshake_and_every_request_carries_its_metadata(self):
        srv = FakeServer("modern")
        s = session(srv)
        hs = s.handshake()
        self.assertEqual(hs["era"], "modern")
        s.list_tools()
        for m, headers in srv.requests:
            self.assertIn("io.modelcontextprotocol/protocolVersion", m["params"]["_meta"])
            self.assertEqual(headers["MCP-Protocol-Version"], "2026-07-28")
        self.assertNotIn("initialize", [m["method"] for m, _ in srv.requests])

    def test_sse_replies_are_read(self):
        srv = FakeServer("legacy", sse=True)
        s = session(srv)
        s.handshake()
        self.assertEqual(len(s.list_tools()), 2)


class Failures(unittest.TestCase):
    def test_a_redirect_is_never_followed(self):
        with self.assertRaises(H.HttpMcpError) as cm:
            session(FakeServer(redirect=True)).handshake()
        self.assertEqual(cm.exception.kind, "redirect")

    def test_a_rejected_credential_is_reported_as_auth_not_as_a_protocol_problem(self):
        with self.assertRaises(H.HttpMcpError) as cm:
            session(FakeServer(status=401), "bad").handshake()
        self.assertEqual(cm.exception.kind, "auth")

    def test_run_op_never_raises_and_is_honest(self):
        r = H.run_op("test", "http://example.com/mcp", None, resolver=PUBLIC)
        self.assertFalse(r["ok"])
        self.assertEqual(r["error_class"], "insecure")
        r = H.run_op("test", "https://mcp.example.org/mcp", None, resolver=resolver_for("10.0.0.1"))
        self.assertEqual(r["error_class"], "ssrf")
        r = H.run_op("test", "https://mcp.example.org/mcp", "tok", resolver=PUBLIC, opener=FakeServer(redirect=True))
        self.assertFalse(r["ok"])


class Operations(unittest.TestCase):
    def test_test_discovers_compact_tools_and_marks_nothing_as_safe(self):
        r = H.run_op("test", "https://mcp.example.org/mcp", None, resolver=PUBLIC, opener=FakeServer("legacy"))
        self.assertTrue(r["ok"])
        self.assertEqual(r["tool_count"], 2)
        self.assertEqual({t["name"] for t in r["tools"]}, {"lookup", "post"})
        self.assertTrue(all("digest" in t for t in r["tools"]))

    def test_call_returns_untrusted_scrubbed_data_and_checks_the_digest(self):
        srv = FakeServer("legacy")
        listed = H.run_op("test", "https://mcp.example.org/mcp", None, resolver=PUBLIC, opener=srv)
        digest = next(t["digest"] for t in listed["tools"] if t["name"] == "lookup")
        ok = H.run_op("call", "https://mcp.example.org/mcp", None, "lookup", {"q": "x"}, digest, resolver=PUBLIC, opener=FakeServer("legacy"))
        self.assertTrue(ok["ok"])
        self.assertTrue(ok["result"]["untrusted"])
        self.assertIn("answer for", ok["result"]["text"])
        changed = H.run_op("call", "https://mcp.example.org/mcp", None, "lookup", {"q": "x"}, "0" * 64, resolver=PUBLIC, opener=FakeServer("legacy"))
        self.assertFalse(changed["ok"])
        self.assertEqual(changed["error_class"], "tool_changed")
        unknown = H.run_op("call", "https://mcp.example.org/mcp", None, "nope", {}, "", resolver=PUBLIC, opener=FakeServer("legacy"))
        self.assertEqual(unknown["error_class"], "unknown_tool")


class ResultShapes(unittest.TestCase):
    def test_malformed_content_is_handled_as_untrusted_data(self):
        # bench N-027: content as a dict or a number raised TypeError inside process_result
        for res in ({"content": {"type": "text", "text": "x"}}, {"content": 5}, {"content": "hello"}, {"content": None},
                    {"content": [None, 3, {"type": "text", "text": "ok"}]}):
            out = W.process_result(res)
            self.assertTrue(out["untrusted"], res)
            json.dumps(out)
        self.assertEqual(W.process_result({"content": [None, {"type": "text", "text": "ok"}]})["text"], "ok")


if __name__ == "__main__":
    unittest.main()
