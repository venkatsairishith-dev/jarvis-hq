"""JARVIS egress proxy (plan P4; the Smokescreen port). An asyncio forward proxy for the agent browser and agent tools.

For every request it decides with ssrf_guard.check_url (public addresses only, resolved once), refuses bank/wallet/password-manager
hosts with the kernel's sensitive_host, then connects to the ONE pinned address the guard returned (the name is never resolved again,
which defeats DNS rebinding). CONNECT tunnels are allowed to port 443 only; plain http is forwarded in origin form with Proxy-* and
hop-by-hop headers removed. Refusals answer 403 (the browser's self-test in jarvis_node/browser.py expects exactly that for
169.254.169.254), a wrong role token 407, a full proxy 503, an unreachable upstream 502.

The decision log is one JSON line per request: time, role, method, host, port, pinned ip, allow, reason. It never holds paths, queries,
headers or bodies.

Roles (optional, egress_acl.json): {"roles": {"research": {"token_file": "...", "deny_hosts": ["..."], "allow_only": null}}}.
A client names its role with Proxy-Authorization: Basic base64(role:token); the token file is read by this process only. Without a
roles file every client is role "default" with the built-in rules.

    python jarvis/egress/proxy.py --listen 127.0.0.1:4750 [--allow-bind 100.125.91.51] [--acl FILE] [--log FILE]
"""
from __future__ import annotations

import argparse
import asyncio
import base64
import hmac
import json
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Awaitable, Callable
from urllib.parse import urlsplit

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "supervisor"))
sys.path.insert(0, str(HERE.parent / "kernel"))
import ssrf_guard  # noqa: E402
from binds import assert_bind  # noqa: E402

try:                                                    # the kernel's bank/wallet/password-manager floor (+ config/sensitive_sites.json)
    from jarvis_kernel.policy import sensitive_host as _kernel_sensitive_host  # noqa: E402
except Exception:                                       # pragma: no cover - the proxy must not run without it
    _kernel_sensitive_host = None

MAX_HEAD = 16 * 1024
HEAD_TIMEOUT = 10.0
CONNECT_TIMEOUT = 10.0
IDLE_TIMEOUT = 60.0
MAX_CONNECTIONS = 256
CONNECT_PORTS = (443,)
HTTP_PORTS = (80,)
HOP_BY_HOP = {b"connection", b"keep-alive", b"proxy-connection", b"proxy-authorization", b"proxy-authenticate", b"te", b"trailer",
              b"transfer-encoding", b"upgrade"}

Connector = Callable[[str, int], Awaitable[tuple[asyncio.StreamReader, asyncio.StreamWriter]]]


@dataclass
class Role:
    name: str
    token: str = ""                                     # empty = no token required (only the implicit "default" role)
    deny_hosts: tuple[str, ...] = ()
    allow_only: tuple[str, ...] | None = None           # when set, only these hosts (and their subdomains) are reachable


@dataclass
class Acl:
    roles: dict[str, Role] = field(default_factory=lambda: {"default": Role("default")})

    @property
    def auth_required(self) -> bool:
        return "default" not in self.roles

    @classmethod
    def load(cls, path: str | Path) -> "Acl":
        data = json.loads(Path(path).read_text(encoding="utf-8-sig"))
        roles = {}
        for name, spec in (data.get("roles") or {}).items():
            token = ""
            if spec.get("token_file"):
                token = Path(spec["token_file"]).read_text(encoding="utf-8").strip()
                if not token:
                    raise ValueError(f"role {name!r}: token file is empty")
            elif name != "default":
                raise ValueError(f"role {name!r}: needs a token_file")
            allow_only = spec.get("allow_only")
            roles[name] = Role(name, token, tuple(h.lower() for h in spec.get("deny_hosts", ())),
                               tuple(h.lower() for h in allow_only) if allow_only is not None else None)
        if not roles:
            raise ValueError("acl has no roles")
        return cls(roles)


def _host_matches(host: str, patterns: tuple[str, ...]) -> bool:
    return any(host == p or host.endswith("." + p) for p in patterns)


@dataclass(frozen=True)
class Decision:
    allow: bool
    status: int = 403
    reason: str = ""
    host: str = ""
    port: int = 0
    ip: str = ""


def decide(url: str, role: Role, resolver=None, ports: tuple[int, ...] = CONNECT_PORTS + HTTP_PORTS,
           sensitive=None) -> Decision:
    """The whole policy for one request. Pure apart from the resolver, so it is tested without sockets."""
    v = ssrf_guard.check_url(url, resolver=resolver, allowed_ports=ports)
    host = v.host or (urlsplit(url).hostname or "")
    if not v.ok:
        return Decision(False, 403, v.reason, host, v.port)
    check = sensitive if sensitive is not None else _kernel_sensitive_host
    if check is None:
        return Decision(False, 403, "sensitive-site rules unavailable (fail closed)", host, v.port)
    why = check(host)
    if why:
        return Decision(False, 403, "sensitive site", host, v.port)
    if _host_matches(host, role.deny_hosts):
        return Decision(False, 403, "denied for role", host, v.port)
    if role.allow_only is not None and not _host_matches(host, role.allow_only):
        return Decision(False, 403, "not in role allow list", host, v.port)
    return Decision(True, 200, "", host, v.port, v.ip)


async def _default_connector(ip: str, port: int):
    return await asyncio.wait_for(asyncio.open_connection(ip, port), CONNECT_TIMEOUT)


class EgressProxy:
    def __init__(self, host: str = "127.0.0.1", port: int = 0, acl: Acl | None = None, allowed_binds: tuple[str, ...] = (),
                 resolver=None, connector: Connector | None = None, log: Callable[[dict], None] | None = None,
                 sensitive=None, max_connections: int = MAX_CONNECTIONS, idle_timeout: float = IDLE_TIMEOUT):
        self.host = assert_bind(host, allowed_binds)
        self.port = port
        self.acl = acl or Acl()
        self.resolver = resolver
        self.connector = connector or _default_connector
        self.log = log or (lambda rec: print(json.dumps(rec, separators=(",", ":")), file=sys.stderr, flush=True))
        self.sensitive = sensitive
        self.idle_timeout = idle_timeout
        self._slots = asyncio.Semaphore(max_connections)
        self._server: asyncio.base_events.Server | None = None

    async def start(self) -> "EgressProxy":
        self._server = await asyncio.start_server(self._handle, self.host, self.port, limit=MAX_HEAD)
        self.port = self._server.sockets[0].getsockname()[1]
        return self

    async def close(self):
        if self._server:
            self._server.close()
            await self._server.wait_closed()

    def _record(self, role: str, method: str, d: Decision):
        self.log({"ts": round(time.time(), 3), "role": role, "method": method, "host": d.host[:255], "port": d.port, "ip": d.ip,
                  "allow": d.allow, "status": d.status, "reason": d.reason})

    def _role_for(self, headers: dict[bytes, bytes]) -> Role | None:
        if not self.acl.auth_required:                  # no roles file: every client is "default"; a stray header changes nothing
            return self.acl.roles["default"]
        raw = headers.get(b"proxy-authorization", b"")
        if not raw:
            return None
        try:
            scheme, _, value = raw.decode("latin-1").partition(" ")
            if scheme.lower() != "basic":
                return None
            name, _, token = base64.b64decode(value.strip(), validate=True).decode("utf-8").partition(":")
        except (ValueError, UnicodeDecodeError):
            return None
        role = self.acl.roles.get(name)
        if role is None or not role.token or not hmac.compare_digest(role.token.encode(), token.encode()):
            return None
        return role

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        if self._slots.locked():
            try:                                        # take the request off the socket first, or closing it sends a reset
                await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), 1.0)
            except Exception:
                pass
            await _reply(writer, 503, "Service Unavailable")
            writer.close()
            return
        async with self._slots:
            try:
                await self._serve(reader, writer)
            except Exception:
                pass
            finally:
                writer.close()

    async def _serve(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        try:
            head = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), HEAD_TIMEOUT)
        except asyncio.LimitOverrunError:
            await _reply(writer, 431, "Request Header Fields Too Large")
            return
        except (asyncio.IncompleteReadError, asyncio.TimeoutError):
            return
        if len(head) > MAX_HEAD:
            await _reply(writer, 431, "Request Header Fields Too Large")
            return
        lines = head.split(b"\r\n")
        try:
            method, target, _version = lines[0].decode("latin-1").split(" ", 2)
        except ValueError:
            await _reply(writer, 400, "Bad Request")
            return
        headers: dict[bytes, bytes] = {}
        raw_headers: list[tuple[bytes, bytes]] = []
        for ln in lines[1:]:
            if not ln:
                continue
            k, sep, v = ln.partition(b":")
            if not sep:
                await _reply(writer, 400, "Bad Request")
                return
            raw_headers.append((k.strip(), v.strip()))
            headers[k.strip().lower()] = v.strip()
        role = self._role_for(headers)
        if role is None:
            self._record("?", method, Decision(False, 407, "proxy authentication failed"))
            await _reply(writer, 407, "Proxy Authentication Required", extra=b'Proxy-Authenticate: Basic realm="jarvis-egress"\r\n')
            return
        if method.upper() == "CONNECT":
            hostport = target.strip()
            host, sep, port_s = hostport.rpartition(":")
            if not sep or not port_s.isdigit():
                await _reply(writer, 400, "Bad Request")
                return
            d = decide(f"https://{host}:{port_s}/", role, self.resolver, CONNECT_PORTS, self.sensitive)
            self._record(role.name, "CONNECT", d)
            if not d.allow:
                await _reply(writer, d.status, "Forbidden")
                return
            upstream = await self._open(d, writer)
            if upstream is None:
                return
            writer.write(b"HTTP/1.1 200 Connection Established\r\n\r\n")
            await writer.drain()
            await self._pipe(reader, writer, *upstream)
            return
        parts = urlsplit(target)
        if parts.scheme.lower() != "http" or not parts.hostname:
            self._record(role.name, method, Decision(False, 403, "only absolute http:// targets or CONNECT", parts.hostname or ""))
            await _reply(writer, 403, "Forbidden")
            return
        d = decide(target, role, self.resolver, HTTP_PORTS, self.sensitive)
        self._record(role.name, method, d)
        if not d.allow:
            await _reply(writer, d.status, "Forbidden")
            return
        upstream = await self._open(d, writer)
        if upstream is None:
            return
        up_reader, up_writer = upstream
        path = (parts.path or "/") + ("?" + parts.query if parts.query else "")
        out = [f"{method} {path} HTTP/1.1".encode("latin-1")]
        has_host = False
        for k, v in raw_headers:
            kl = k.lower()
            if kl in HOP_BY_HOP:
                continue
            if kl == b"host":
                has_host = True
            out.append(k + b": " + v)
        if not has_host:
            out.append(b"Host: " + parts.netloc.rpartition("@")[2].encode("latin-1"))
        out.append(b"Connection: close")
        up_writer.write(b"\r\n".join(out) + b"\r\n\r\n")
        await up_writer.drain()
        await self._pipe(reader, writer, up_reader, up_writer)

    async def _open(self, d: Decision, writer):
        try:
            return await self.connector(d.ip, d.port)
        except (OSError, asyncio.TimeoutError):
            self._record("-", "UPSTREAM", Decision(False, 502, "upstream unreachable", d.host, d.port, d.ip))
            await _reply(writer, 502, "Bad Gateway")
            return None

    async def _pipe(self, c_reader, c_writer, u_reader, u_writer):
        async def copy(src: asyncio.StreamReader, dst: asyncio.StreamWriter):
            try:
                while True:
                    data = await asyncio.wait_for(src.read(65536), self.idle_timeout)
                    if not data:
                        break
                    dst.write(data)
                    await dst.drain()
            except (OSError, asyncio.TimeoutError, asyncio.IncompleteReadError):
                pass
            finally:
                try:
                    dst.close()
                except Exception:
                    pass
        await asyncio.gather(copy(c_reader, u_writer), copy(u_reader, c_writer))


async def _reply(writer: asyncio.StreamWriter, status: int, text: str, extra: bytes = b""):
    try:
        writer.write(f"HTTP/1.1 {status} {text}\r\n".encode() + extra + b"Content-Length: 0\r\nConnection: close\r\n\r\n")
        await writer.drain()
    except Exception:
        pass


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--listen", default="127.0.0.1:4750", help="host:port; only loopback or an --allow-bind address")
    ap.add_argument("--allow-bind", action="append", default=[], help="extra address the proxy may bind (e.g. the Tailscale IP)")
    ap.add_argument("--acl", help="egress_acl.json with roles (optional)")
    ap.add_argument("--log", help="append the JSON decision log here instead of stderr")
    args = ap.parse_args(argv)
    host, _, port = args.listen.rpartition(":")
    acl = Acl.load(args.acl) if args.acl else Acl()
    log = None
    if args.log:
        fh = open(args.log, "a", encoding="utf-8")

        def log(rec):
            fh.write(json.dumps(rec, separators=(",", ":")) + "\n")
            fh.flush()
    if _kernel_sensitive_host is None:
        print("refusing to start: the kernel's sensitive-site rules could not be imported", file=sys.stderr)
        return 2

    async def run():
        proxy = await EgressProxy(host, int(port), acl, tuple(args.allow_bind), log=log).start()
        print(f"egress proxy listening on {proxy.host}:{proxy.port}", file=sys.stderr, flush=True)
        await asyncio.Event().wait()

    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
