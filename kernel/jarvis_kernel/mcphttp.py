"""MCP over Streamable HTTP for remote servers, called by the kernel itself. Standard library only.

Rules, all enforced here and not by configuration:
- https only (a credential or any data over plain http is refused);
- the host must not be, and must not resolve to, a loopback, private, link-local, CGNAT (Tailscale), reserved or multicast
  address, and the connection goes to the address that was checked (no second lookup: DNS rebinding cannot swap it);
- redirects are never followed; a response is read up to a fixed size;
- the credential, if any, is a bearer token read from a file by reference, sent only to this endpoint, never returned.
Both protocol eras are spoken: a modern per-request attempt first, then the `initialize` handshake.
"""
import http.client
import json
import socket
import ssl
import time
import urllib.parse

from . import mcpwire as W
from .policy import internal_host

MAX_RESPONSE = 1_048_576


class HttpMcpError(Exception):
    def __init__(self, kind: str, message: str):
        super().__init__(message)
        self.kind = kind


def check_endpoint(url: str, resolver=socket.getaddrinfo) -> dict:
    """Validate an endpoint and resolve it once. Returns {host, port, path, ip}. Raises HttpMcpError."""
    u = urllib.parse.urlsplit(str(url))
    if u.scheme.lower() != "https":
        raise HttpMcpError("insecure", "the endpoint must be https")
    host = (u.hostname or "").strip().lower()
    if not host or u.username or u.password:
        raise HttpMcpError("bad_endpoint", "the endpoint needs a host name and no embedded credentials")
    try:
        port = u.port or 443
    except ValueError:
        raise HttpMcpError("bad_endpoint", "bad port") from None
    if not 1 <= port <= 65535:
        raise HttpMcpError("bad_endpoint", "bad port")
    if internal_host(host):
        raise HttpMcpError("ssrf", "the endpoint is an internal or private address")
    try:
        infos = resolver(host, port, type=socket.SOCK_STREAM)
    except OSError:
        raise HttpMcpError("unreachable", "the host name does not resolve") from None
    ips = []
    for info in infos:
        ip = info[4][0]
        if internal_host(ip):
            raise HttpMcpError("ssrf", "the host name resolves to an internal or private address")
        if ip not in ips:
            ips.append(ip)
    if not ips:
        raise HttpMcpError("unreachable", "the host name does not resolve")
    path = (u.path or "/") + (("?" + u.query) if u.query else "")
    return {"host": host, "port": port, "path": path, "ip": ips[0]}


class _PinnedHTTPS(http.client.HTTPSConnection):
    """Connects to an address that was already checked, and verifies the certificate against the real host name."""

    def __init__(self, host, port, ip, timeout):
        super().__init__(host, port, timeout=timeout, context=ssl.create_default_context())
        self._ip = ip

    def connect(self):
        raw = socket.create_connection((self._ip, self.port), self.timeout)
        self.sock = self._context.wrap_socket(raw, server_hostname=self.host)


def _default_opener(target: dict, method: str, headers: dict, body: bytes, timeout: float):
    conn = _PinnedHTTPS(target["host"], target["port"], target["ip"], timeout)
    try:
        conn.request(method, target["path"], body=body, headers=headers)
        r = conn.getresponse()
        data = r.read(MAX_RESPONSE + 1)
        if len(data) > MAX_RESPONSE:
            raise HttpMcpError("oversize", "the response is larger than the limit")
        return r.status, {k.lower(): v for k, v in r.getheaders()}, data
    finally:
        conn.close()


def _events(data: bytes) -> list:
    """The JSON messages in a text/event-stream body."""
    out = []
    for block in data.decode("utf-8", "replace").replace("\r\n", "\n").split("\n\n"):
        payload = "\n".join(l[5:].lstrip() for l in block.split("\n") if l.startswith("data:"))
        if payload.strip():
            try:
                m = json.loads(payload)
            except ValueError:
                continue
            if isinstance(m, dict):
                out.append(m)
    return out


class HttpSession:
    def __init__(self, url: str, bearer: str = None, resolver=socket.getaddrinfo, opener=_default_opener, timeout: float = 20.0):
        self.target = check_endpoint(url, resolver)
        self.bearer, self.opener, self.timeout = bearer, opener, timeout
        self.era = None
        self.protocol = None
        self.server = {}
        self.capabilities = {}
        self.session_id = None
        self._n = 0
        self.refused_requests = 0

    def _rpc(self, method, params=None, notify=False, modern=None):
        self._n += 1
        rid = self._n
        modern = (self.era == "modern") if modern is None else modern
        msg = W.notification(method, params) if notify else W.request(rid, method, params, modern=modern)
        headers = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream", "User-Agent": "jarvis-connector/1"}
        if self.bearer:
            headers["Authorization"] = "Bearer " + self.bearer
        if self.era == "modern" or modern:
            headers["MCP-Protocol-Version"] = W.PROTOCOL_MODERN
        elif self.protocol:
            headers["MCP-Protocol-Version"] = self.protocol
        if self.session_id:
            headers["Mcp-Session-Id"] = self.session_id
        try:
            status, rh, data = self.opener(self.target, "POST", headers, json.dumps(msg).encode("utf-8"), self.timeout)
        except HttpMcpError:
            raise
        except (OSError, ssl.SSLError, http.client.HTTPException) as e:
            raise HttpMcpError("unreachable", f"could not reach the server ({type(e).__name__})") from None
        if 300 <= status < 400:
            raise HttpMcpError("redirect", "the server redirected the request; redirects are not followed")
        if status in (401, 403):
            raise HttpMcpError("auth", f"the server refused the credential (HTTP {status})")
        if notify:
            return {}
        if status >= 400:
            try:
                err = json.loads(data).get("error") if data else None
            except ValueError:
                err = None
            if isinstance(err, dict):
                raise _Rpc(err)
            raise HttpMcpError("server", f"HTTP {status}")
        if "mcp-session-id" in rh and not self.session_id:
            self.session_id = rh["mcp-session-id"][:200]
        ctype = rh.get("content-type", "")
        if "text/event-stream" in ctype:
            msgs = _events(data)
        else:
            try:
                m = json.loads(data)
            except ValueError:
                raise HttpMcpError("protocol", "the server's reply was not JSON") from None
            msgs = m if isinstance(m, list) else [m]
        for m in msgs:
            if isinstance(m, dict) and "method" in m and "id" in m:
                self.refused_requests += 1          # the server asked the client for something: not answered, never granted
        for m in msgs:
            if isinstance(m, dict) and m.get("id") == rid:
                if isinstance(m.get("error"), dict):
                    raise _Rpc(m["error"])
                return m.get("result") if isinstance(m.get("result"), dict) else {}
        raise HttpMcpError("protocol", "the server did not answer the request")

    def handshake(self) -> dict:
        try:
            res = self._rpc("server/discover", modern=True)
            versions = res.get("supportedVersions") or []
            if versions and W.PROTOCOL_MODERN not in versions:
                raise HttpMcpError("protocol", f"no common protocol version (server: {versions[:5]})")
            self.era, self.protocol = "modern", W.PROTOCOL_MODERN
            meta = res.get("_meta") if isinstance(res.get("_meta"), dict) else {}
            self.server = meta.get("io.modelcontextprotocol/serverInfo") if isinstance(meta.get("io.modelcontextprotocol/serverInfo"), dict) else {}
            self.capabilities = res.get("capabilities") if isinstance(res.get("capabilities"), dict) else {}
        except _Rpc as e:
            if W.is_modern_error(e.err):
                raise HttpMcpError("protocol", "the server speaks MCP but not " + W.PROTOCOL_MODERN) from None
            self._legacy()
        except HttpMcpError as e:
            if e.kind in ("protocol", "server"):
                self._legacy()
            else:
                raise
        info = {k: str(v)[:80] for k, v in self.server.items() if k in ("name", "version", "title")}
        return {"era": self.era, "protocol": self.protocol, "server": info, "capabilities": sorted(self.capabilities)[:12]}

    def _legacy(self):
        self.era = "legacy"
        self.session_id = None
        try:
            res = self._rpc("initialize", W.initialize_params(), modern=False)
        except _Rpc as e:
            raise HttpMcpError("handshake", f"the server rejected the handshake ({str(e)[:120]})") from None
        self.protocol = str(res.get("protocolVersion", W.PROTOCOL_LEGACY))[:20]
        self.server = res.get("serverInfo") if isinstance(res.get("serverInfo"), dict) else {}
        self.capabilities = res.get("capabilities") if isinstance(res.get("capabilities"), dict) else {}
        self._rpc("notifications/initialized", notify=True)

    def list_tools(self) -> list:
        tools, cursor = [], None
        for _ in range(10):
            res = self._call("tools/list", {"cursor": cursor} if cursor else {})
            tools.extend(t for t in (res.get("tools") or []) if isinstance(t, dict))
            cursor = res.get("nextCursor")
            if not cursor or len(tools) >= W.MAX_TOOLS:
                break
        return tools[:W.MAX_TOOLS]

    def call(self, name: str, arguments: dict) -> dict:
        res = self._call("tools/call", {"name": name, "arguments": arguments or {}})
        if res.get("resultType") == "input_required":
            raise HttpMcpError("input_required", "the server asked for more input, which JARVIS does not provide")
        return res

    def _call(self, method, params):
        try:
            return self._rpc(method, params)
        except _Rpc as e:
            raise HttpMcpError("server", str(e)) from None

    def close(self):
        pass                                        # requests are independent; an HTTP session ends when it is abandoned


class _Rpc(Exception):
    def __init__(self, err: dict):
        super().__init__(f"{err.get('code')}: {str(err.get('message', ''))[:200]}")
        self.err = err


def run_op(op: str, endpoint: str, bearer: str, tool: str = "", args: dict = None, digest: str = "", timeout: float = 20.0,
           max_output: int = W.DEFAULT_MAX_OUTPUT, resolver=socket.getaddrinfo, opener=_default_opener) -> dict:
    """One discover/test/call against a remote endpoint, shaped like the node's connector results. Never raises."""
    t0 = time.time()

    def fail(kind, msg, **extra):
        return {"ok": False, "op": op, "error_class": kind, "error": str(msg)[:300], "ms": int((time.time() - t0) * 1000), **extra}
    try:
        s = HttpSession(endpoint, bearer, resolver=resolver, opener=opener, timeout=timeout)
        hs = s.handshake()
        raw = s.list_tools()
        listed = {}
        for t in raw:
            st = W.sanitize_tool(t)
            if st:
                listed[st["name"]] = st
        if op in ("test", "discover", "connect"):
            return {"ok": True, "op": op, "installed": True, "state": "connected", **hs, "tool_count": len(listed),
                    "tools": [W.compact_tool(t) for t in listed.values()], "refused_server_requests": s.refused_requests,
                    "ms": int((time.time() - t0) * 1000)}
        cur = listed.get(tool)
        if cur is None:
            return fail("unknown_tool", "the server does not offer this tool")
        if digest and digest != cur["digest"]:
            return fail("tool_changed", "the server changed this tool since the owner reviewed it; it must be reviewed again")
        out = W.process_result(s.call(tool, args or {}), max_output)
        ok = not out["is_error"]
        return {"ok": ok, "op": op, "tool": tool, "result": out, "ms": int((time.time() - t0) * 1000),
                **({} if ok else {"error_class": "tool_error", "error": out["text"][:200]})}
    except HttpMcpError as e:
        return fail(e.kind, e)
    except Exception as e:
        return fail("internal", type(e).__name__)
