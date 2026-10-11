"""A small MCP client for local stdio servers. Standard library only.

It speaks both protocol eras (probe with `server/discover`, fall back to `initialize`), never answers a server's request with
anything but "not supported" (a connector cannot reach a model or the owner through sampling or elicitation), bounds every line
it reads, drains stderr so a chatty server cannot stall, and shuts a server down the way the specification asks: close its
input, wait, then terminate, then kill.

It starts only the argument vector it is given. Where that vector comes from, and what is allowed to run, is decided by
connectors.py from this machine's own allowlist; nothing a server or a model says reaches this module as a command.
"""
import collections
import json
import os
import queue
import subprocess
import sys
import threading
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "kernel"))
from jarvis_kernel import mcpwire as W  # noqa: E402

NOWIN = 0x08000000 if os.name == "nt" else 0
LINE_LIMIT = 1_048_576          # one JSON-RPC message; a longer line ends the session
DISCOVER_WAIT = 6.0             # how long a server may take to answer the modern probe before it is treated as legacy


class McpError(Exception):
    def __init__(self, kind: str, message: str):
        super().__init__(message)
        self.kind = kind          # start | handshake | protocol | timeout | crash | oversize | input_required | server


class StdioSession:
    def __init__(self, argv, env=None, cwd=None, line_limit=LINE_LIMIT, popen=subprocess.Popen):
        self.argv, self.env, self.cwd, self.line_limit, self._popen = list(argv), env, cwd, line_limit, popen
        self.proc = None
        self.era = None               # "modern" | "legacy"
        self.server = {}
        self.protocol = None
        self.capabilities = {}
        self._ids = iter(range(1, 10**9))
        self._inbox = queue.Queue()
        self._stderr = collections.deque(maxlen=40)
        self._dead = None             # reason the reader stopped
        self.notifications = 0
        self.refused_requests = 0
        self.last_used = time.time()

    # ---- process ----
    def start(self):
        try:
            self.proc = self._popen(self.argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=self.env,
                                    cwd=self.cwd, creationflags=NOWIN, close_fds=True)
        except (OSError, ValueError) as e:
            raise McpError("start", f"could not start the server ({type(e).__name__})") from None
        threading.Thread(target=self._read_out, daemon=True).start()
        threading.Thread(target=self._read_err, daemon=True).start()

    def _read_out(self):
        out = self.proc.stdout
        try:
            while True:
                line = out.readline(self.line_limit + 1)
                if not line:
                    self._dead = self._dead or "the server closed its output"
                    break
                if len(line) > self.line_limit:
                    self._dead = "oversize"
                    self.kill()
                    break
                try:
                    msg = json.loads(line)
                except ValueError:
                    continue                    # a stray non-JSON line: ignored, never executed or shown
                if isinstance(msg, dict):
                    self._inbox.put(msg)
        except (OSError, ValueError):
            self._dead = self._dead or "the server's output broke"
        finally:
            self._inbox.put(None)               # wakes a waiter

    def _read_err(self):
        try:
            for line in iter(self.proc.stderr.readline, b""):
                self._stderr.append(line.decode("utf-8", "replace").rstrip()[:300])
        except (OSError, ValueError):
            pass

    def stderr_tail(self, limit: int = 400) -> str:
        from jarvis_kernel.scrub import scrub
        return scrub(" | ".join(list(self._stderr)[-4:]))[0][-limit:]

    def alive(self) -> bool:
        return self.proc is not None and self.proc.poll() is None and self._dead is None

    def _send(self, msg: dict):
        if not self.alive():
            raise McpError("crash", "the server is not running")
        try:
            self.proc.stdin.write((json.dumps(msg, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8"))
            self.proc.stdin.flush()
        except (OSError, ValueError):
            raise McpError("crash", "the server stopped accepting input") from None

    # ---- requests ----
    def _rpc(self, method: str, params: dict = None, timeout: float = 20.0) -> dict:
        rid = next(self._ids)
        self._send(W.request(rid, method, params, modern=self.era == "modern"))
        deadline = time.time() + timeout
        while True:
            left = deadline - time.time()
            if left <= 0:
                try:
                    self._send(W.notification("notifications/cancelled", {"requestId": rid, "reason": "timeout"}))
                except McpError:
                    pass
                raise McpError("timeout", f"no answer to {method} within {int(timeout)} s")
            try:
                msg = self._inbox.get(timeout=min(left, 0.5))
            except queue.Empty:
                if not self.alive() and self._inbox.empty():
                    raise McpError("oversize" if self._dead == "oversize" else "crash", "oversized reply" if self._dead == "oversize" else "the server stopped")
                continue
            if msg is None:
                if self._dead == "oversize":
                    raise McpError("oversize", "the server sent a message larger than the limit")
                raise McpError("crash", "the server stopped")
            if "method" in msg:
                if "id" in msg:                  # a request from the server: always refused
                    self.refused_requests += 1
                    try:
                        self._send(W.refuse_server_request(msg))
                    except McpError:
                        pass
                else:
                    self.notifications += 1
                continue
            if msg.get("id") != rid:
                continue                         # a late answer to a request already abandoned
            self.last_used = time.time()
            if isinstance(msg.get("error"), dict):
                e = msg["error"]
                raise McpError("server", f"{e.get('code')}: {str(e.get('message', ''))[:200]}")
            res = msg.get("result")
            return res if isinstance(res, dict) else {}

    def handshake(self, timeout: float = 15.0) -> dict:
        """Dual-era connect. Returns {era, protocol, server, capabilities}."""
        rid = next(self._ids)
        self._send(W.request(rid, "server/discover", modern=True))
        deadline = time.time() + min(DISCOVER_WAIT, timeout)
        modern = None
        while time.time() < deadline:
            try:
                msg = self._inbox.get(timeout=0.25)
            except queue.Empty:
                if not self.alive():
                    raise McpError("crash", "the server stopped during the handshake")
                continue
            if msg is None:
                raise McpError("crash", "the server stopped during the handshake")
            if "method" in msg:
                if "id" in msg:
                    self.refused_requests += 1
                    self._send(W.refuse_server_request(msg))
                continue
            if msg.get("id") != rid:
                continue
            if isinstance(msg.get("result"), dict):
                modern = msg["result"]
            elif W.is_modern_error(msg.get("error")):
                sup = (msg["error"].get("data") or {}).get("supported") if isinstance(msg["error"].get("data"), dict) else None
                raise McpError("protocol", f"the server speaks MCP but not {W.PROTOCOL_MODERN} (it supports {sup})")
            break                                # any other error: a legacy server
        if modern is not None:
            versions = modern.get("supportedVersions") or []
            if versions and W.PROTOCOL_MODERN not in versions:
                raise McpError("protocol", f"no common protocol version (server: {versions[:5]})")
            self.era, self.protocol = "modern", W.PROTOCOL_MODERN
            meta = modern.get("_meta") if isinstance(modern.get("_meta"), dict) else {}
            self.server = meta.get("io.modelcontextprotocol/serverInfo") if isinstance(meta.get("io.modelcontextprotocol/serverInfo"), dict) else {}
            self.capabilities = modern.get("capabilities") if isinstance(modern.get("capabilities"), dict) else {}
        else:
            self.era = "legacy"
            res = self._rpc("initialize", W.initialize_params(), timeout=max(5.0, timeout - DISCOVER_WAIT))
            self.protocol = str(res.get("protocolVersion", ""))[:20]
            self.server = res.get("serverInfo") if isinstance(res.get("serverInfo"), dict) else {}
            self.capabilities = res.get("capabilities") if isinstance(res.get("capabilities"), dict) else {}
            self._send(W.notification("notifications/initialized"))
        info = {k: str(v)[:80] for k, v in self.server.items() if k in ("name", "version", "title")}
        return {"era": self.era, "protocol": self.protocol, "server": info, "capabilities": sorted(self.capabilities)[:12]}

    def list_tools(self, timeout: float = 20.0) -> list:
        tools, cursor = [], None
        for _ in range(10):                      # pagination, bounded
            res = self._rpc("tools/list", {"cursor": cursor} if cursor else {}, timeout=timeout)
            for t in res.get("tools") or []:
                tools.append(t)
            cursor = res.get("nextCursor")
            if not cursor or len(tools) >= W.MAX_TOOLS:
                break
        return tools[:W.MAX_TOOLS]

    def call(self, name: str, arguments: dict, timeout: float = 30.0) -> dict:
        res = self._rpc("tools/call", {"name": name, "arguments": arguments or {}}, timeout=timeout)
        if res.get("resultType") == "input_required":
            raise McpError("input_required", "the server asked for more input (elicitation or sampling), which JARVIS does not provide")
        return res

    # ---- shutdown ----
    def kill(self):
        p = self.proc
        if p is None or p.poll() is not None:
            return
        try:
            if os.name == "nt":                  # a server started through a launcher has children: take the tree
                subprocess.run(["taskkill", "/PID", str(p.pid), "/T", "/F"], capture_output=True, timeout=10, creationflags=NOWIN)
            p.kill()
        except Exception:
            pass

    def close(self, grace: float = 3.0):
        p = self.proc
        if p is None:
            return
        try:
            if p.stdin:
                p.stdin.close()                  # the portable "please stop"
        except OSError:
            pass
        end = time.time() + grace
        while p.poll() is None and time.time() < end:
            time.sleep(0.05)
        if p.poll() is None:
            try:
                p.terminate()
            except OSError:
                pass
            end = time.time() + 2
            while p.poll() is None and time.time() < end:
                time.sleep(0.05)
        if p.poll() is None:
            self.kill()
        self._dead = self._dead or "closed"
