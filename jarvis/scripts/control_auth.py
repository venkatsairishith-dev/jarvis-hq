"""Authentication for the local control surfaces (audit M15 2026-10-18; re-review M3 2026-10-19). Standard library only.

Two servers on this machine can read private state or change what the PC does: the JARVIS console (:8799) and the local speech
server (:48930). Loopback is not a person: any program on the
laptop, and any web page through DNS rebinding or a forged Origin, can reach it. Version 1 of this module (M15) put a per-launch
secret in the pages the servers serve; the re-review showed that was no barrier, because `GET /` handed that secret to ANY
loopback client. This version does not hand a credential to whoever asks.

Route classes (every route of each server is in one of these):
  public-harmless   /health-style liveness (no private data), static JS/CSS, the login page. No credential.
  sensitive-read    owner state, tasks, approvals, audit/events, memory, files, desktop state/screen, bench results. A credential is required.
  state-changing    everything that POSTs or DELETEs. A credential AND, for a browser, a per-session double-submit token.

Credentials (two kinds)
  * MASTER token. Each server makes a random one at every launch (`new_secret`) and publishes it to `<name>.token`, a 0600 file in
    a per-user runtime folder (`run_dir`): %LOCALAPPDATA%\\jarvis\\run on Windows (per-user by default), $XDG_RUNTIME_DIR/jarvis or
    ~/.jarvis/run elsewhere (0700 folder). First-party PROGRAMS (watchdog, desktop plugin, jarvis_bridge, the owner's
    scripts) read that file and send the token in the server's header (`X-Jarvis-Console` / `X-Jarvis-Control`). `read` refuses a
    token file that other users could read. The master token is never put in a page, a URL, a cookie or a log.
  * BROWSER SESSION. A page load alone proves nothing, so a bare `GET /` from an unauthenticated client gets a login page with no
    secret in it. The launcher (`jarvis/scripts/open_console.py`) holds the master token, asks the server for a
    ONE-TIME LAUNCH CODE (`POST /auth/code`, valid 60 s, used once) and opens the browser at `http://127.0.0.1:PORT/#code=...`
    (a URL fragment is never sent to a server and the login script removes it). The login script POSTs the code to
    `/auth/session`; the server answers with an HttpOnly, SameSite=Strict session cookie bound to the exact Host:port it was
    issued for. The signed-in page carries a per-session double-submit token (derived from the session, different from the master
    token) that its script echoes on every state-changing request. A used, expired or unknown code is refused.
  * Every request also needs an exact loopback Host (name AND port: DNS rebinding is refused) and, if a browser sends an Origin,
    one of the server's own. No CORS header is ever sent, and no secret goes in a URL or a log.

Cookie flags: HttpOnly (a script cannot read it), SameSite=Strict, Path=/, host-only, Max-Age 12 h; no `Secure`, because these servers
speak plain http on loopback (a Secure cookie would not be sent). A request that a browser marks `Sec-Fetch-Site: cross-site` or
`same-site` (another site, or a page on another loopback port) cannot use the session cookie. /auth answers 429 for a minute after ten
failed attempts in a minute (global: every caller is on loopback).

What this blocks, plainly: other OS users (the token file is 0600 in a per-user folder and `read` checks the mode and owner), web
pages on other sites (SameSite=Strict, no CORS, Origin and Host checks, nothing readable cross-origin), DNS rebinding, processes that
cannot read the token file or the browser's cookie jar, and a program that only knows the port. What it does NOT block: a process
running as the same OS user, which can read the 0600 token file or the browser profile and then do what the first-party client
does; and a web server on ANOTHER loopback port that the owner browses to, which receives the cookie too (cookies are not
port-scoped), although it still cannot satisfy the double-submit token or read a response. The real boundaries for anything that
matters are unchanged: the kernel's approvals with the owner's PIN, policy, lanes and the kill switch.
"""
from __future__ import annotations

import hashlib
import hmac
import http.cookies
import json
import os
import secrets
import tempfile
import threading
import time

HEADER = "X-Jarvis-Control"
LOCAL_NAMES = ("127.0.0.1", "localhost", "::1")


def run_dir() -> str:
    """The per-user folder that holds the token files (override: JARVIS_RUN_DIR, used by tests)."""
    override = os.environ.get("JARVIS_RUN_DIR")
    if override:
        return override
    if os.name == "nt":
        base = os.environ.get("LOCALAPPDATA") or os.path.join(os.path.expanduser("~"), "AppData", "Local")
        return os.path.join(base, "jarvis", "run")
    xdg = os.environ.get("XDG_RUNTIME_DIR")
    return os.path.join(xdg, "jarvis") if xdg else os.path.join(os.path.expanduser("~"), ".jarvis", "run")


def new_secret() -> str:
    return secrets.token_urlsafe(32)


def _token_path(name: str, directory: str | None) -> str:
    if not name or not name.replace("_", "").replace("-", "").isalnum():
        raise ValueError("bad token name")
    return os.path.join(directory or run_dir(), name + ".token")


def publish(name: str, secret: str, directory: str | None = None) -> str:
    """Write this launch's secret for first-party clients: atomic replace, 0600 file in a 0700 folder. Returns the path."""
    path = _token_path(name, directory)
    folder = os.path.dirname(path)
    os.makedirs(folder, mode=0o700, exist_ok=True)
    if os.name != "nt":
        os.chmod(folder, 0o700)
    fd, tmp = tempfile.mkstemp(prefix=name + ".", suffix=".tmp", dir=folder)    # mkstemp creates the file 0600
    try:
        with os.fdopen(fd, "w", encoding="ascii") as f:
            f.write(secret)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    return path


def unpublish(name: str, directory: str | None = None) -> None:
    try:
        os.unlink(_token_path(name, directory))
    except OSError:
        pass


def read(name: str, directory: str | None = None) -> str | None:
    """A server's current secret, or None when it is not running / the file is not private to this user."""
    try:
        path = _token_path(name, directory)
        st = os.stat(path)
        if os.name != "nt" and (st.st_mode & 0o077 or st.st_uid != os.getuid()):
            return None                                   # readable or writable by others, or planted by another user
        with open(path, encoding="ascii") as f:
            value = f.read(256).strip()
    except (OSError, ValueError):
        return None
    return value or None


HEADERS = {"console": "X-Jarvis-Console"}        # every other server uses HEADER


def header_for(name: str) -> str:
    return HEADERS.get(name, HEADER)


def headers_for(name: str, directory: str | None = None) -> dict:
    """The header a first-party client adds to a state-changing request to server `name` ({} if no token is published)."""
    value = read(name, directory)
    return {header_for(name): value} if value else {}


def secret_ok(given, secret: str) -> bool:
    """Constant-time comparison; an empty configured secret never matches."""
    if not secret or not isinstance(given, str):
        return False
    return hmac.compare_digest(given.encode("utf-8", "replace"), secret.encode("utf-8", "replace"))


def host_value(headers):
    """The request's Host header, or None when it is absent or given more than once (RFC 9112: a request with several Host headers is
    malformed; which one a proxy honoured is exactly the confusion a rebinding attack plays with, so such a request is refused)."""
    get_all = getattr(headers, "get_all", None)
    values = get_all("Host") if get_all else ([headers.get("Host")] if headers.get("Host") is not None else [])
    return values[0] if values and len(values) == 1 else None


def host_exact(host_header, port: int) -> bool:
    """The Host header names a loopback name AND this server's port (a rebound page would carry its own name)."""
    host = (host_header or "").strip().lower()
    if host.startswith("["):                               # [::1]:48940
        name, _, rest = host[1:].partition("]")
        given_port = rest[1:] if rest.startswith(":") else ""
    elif host.count(":") > 1:                              # an IPv6 literal must be bracketed to carry a port
        return False
    else:
        name, _, given_port = host.rpartition(":") if ":" in host else (host, "", "")
    return name in LOCAL_NAMES and given_port == str(port)


def origin_ok(origin, allowed) -> bool:
    """No Origin (a program, not a page) is fine; a page's Origin must be exactly one of `allowed` ("null" never is)."""
    if origin is None:
        return True
    return origin.strip().rstrip("/").lower() in {a.lower() for a in allowed}


def loopback_origins(port: int, extra_ports=()) -> tuple:
    ports = (port, *extra_ports)
    return tuple(f"http://{n}:{p}" for p in ports for n in ("127.0.0.1", "localhost"))


# ---------------------------------------------------------------- browser sessions (re-review M3)
LOGIN_JS = """(async () => {
  const msg = document.getElementById("msg");
  const m = /[#&]code=([^&]+)/.exec(location.hash);
  if (!m) { msg.textContent = "Open this from JARVIS (the launcher opens it with a one-time code); typing the address is not enough."; return; }
  const code = decodeURIComponent(m[1]);
  history.replaceState(null, "", location.pathname + location.search);   // the code is in the fragment: gone from the address bar
  try {
    const r = await fetch("/auth/session", { method: "POST", headers: { "Content-Type": "application/json" },
                                             body: JSON.stringify({ code }), credentials: "same-origin" });
    if (r.ok) { location.replace("/"); return; }
    msg.textContent = "That launch link has expired or was already used. Open it again from JARVIS.";
  } catch (e) { msg.textContent = "Could not reach the server."; }
})();
"""


def login_page(product: str) -> bytes:
    """What an unauthenticated `GET /` gets: no secret, no data, a way to sign in from the launcher's one-time code."""
    safe = "".join(ch for ch in product if ch.isalnum() or ch in " .-")[:40]
    return ("<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\"><meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">"
            f"<title>{safe}: sign in</title></head><body style=\"font:16px system-ui;padding:2rem;max-width:34rem\">"
            f"<h1>{safe}</h1><p id=\"msg\">Signing in...</p>"
            "<p>This window opens from JARVIS with a one-time link. If you see this, start it from the JARVIS launcher "
            "(<code>open_console.py</code>).</p>"
            "<script src=\"/auth/login.js\"></script></body></html>").encode("utf-8")


class Gate:
    """The credentials of one server (see the module docstring). Thread-safe. `master` is the token first-party programs send in
    `header`; browsers get a session via a one-time code. Nothing here is persisted: a restart invalidates every session and code."""

    FAIL_LIMIT, FAIL_WINDOW_S, LOCKOUT_S = 10, 60.0, 60.0     # failed /auth attempts per window before /auth answers 429 for a while

    def __init__(self, name: str, header: str, cookie: str, clock=time.time, session_ttl: float = 12 * 3600, code_ttl: float = 60.0,
                 max_codes: int = 8, max_sessions: int = 32):
        self.name, self.header, self.cookie = name, header, cookie
        self.clock, self.session_ttl, self.code_ttl, self.max_codes, self.max_sessions = clock, session_ttl, code_ttl, max_codes, max_sessions
        self.master = new_secret()
        self._codes: dict = {}               # sha256(code) -> expiry
        self._sessions: dict = {}            # session id -> (host, expiry)
        self._lock = threading.Lock()
        self._fails: list = []               # times of recent failed /auth attempts
        self._locked_until = 0.0

    # -- brute-force limit on /auth (the code and master token are 192/256-bit, so this is defence in depth, not the barrier)
    def locked(self) -> float:
        """Seconds /auth stays closed after too many failures (0 when open). Global on purpose: every caller is on loopback, so there
        is no source address to count; the cost is that a flood of bad attempts blocks NEW sign-ins for a minute (open sessions and
        master-token callers are unaffected)."""
        with self._lock:
            return max(0.0, self._locked_until - self.clock())

    def _fail(self) -> None:
        now = self.clock()
        with self._lock:
            self._fails = [t for t in self._fails if now - t < self.FAIL_WINDOW_S] + [now]
            if len(self._fails) >= self.FAIL_LIMIT:
                self._locked_until, self._fails = now + self.LOCKOUT_S, []

    # -- one-time launch codes
    def issue_code(self) -> str:
        code, now = secrets.token_urlsafe(24), self.clock()
        with self._lock:
            self._codes = {h: t for h, t in self._codes.items() if t > now}
            while len(self._codes) >= self.max_codes:                     # the oldest outstanding code makes room
                self._codes.pop(min(self._codes, key=self._codes.get))
            self._codes[hashlib.sha256(code.encode()).hexdigest()] = now + self.code_ttl
        return code

    def redeem(self, code, host) -> str | None:
        """A session id for a valid, unused, unexpired code (which is then spent), else None. The session is bound to `host`."""
        if not isinstance(code, str) or not 8 <= len(code) <= 200 or not host:
            return None
        h, now = hashlib.sha256(code.encode()).hexdigest(), self.clock()
        with self._lock:
            expiry = self._codes.pop(h, None)                             # pop: a second use finds nothing
            if expiry is None or expiry <= now:
                return None
            self._sessions = {k: v for k, v in self._sessions.items() if v[1] > now}
            while len(self._sessions) >= self.max_sessions:
                self._sessions.pop(min(self._sessions, key=lambda k: self._sessions[k][1]))
            sid = secrets.token_urlsafe(32)
            self._sessions[sid] = (str(host).lower(), now + self.session_ttl)
        return sid

    def cookie_header(self, sid: str) -> str:
        return f"{self.cookie}={sid}; HttpOnly; SameSite=Strict; Path=/; Max-Age={int(self.session_ttl)}"

    # -- who is asking
    def master_ok(self, headers) -> bool:
        return secret_ok(headers.get(self.header), self.master)

    def session_of(self, headers) -> str | None:
        """The live session id in the Cookie header whose bound Host equals this request's Host, else None."""
        raw = headers.get("Cookie")
        if not raw:
            return None
        try:
            jar = http.cookies.SimpleCookie()
            jar.load(raw)
        except http.cookies.CookieError:
            return None
        morsel = jar.get(self.cookie)
        if morsel is None:
            return None
        # A browser says where a request comes from: only the page itself (or a typed address) may use the session. A page on another
        # loopback port is "same-site", not "same-origin", so it cannot ride the cookie (the port-shared cookie jar, see the docstring).
        if headers.get("Sec-Fetch-Site") not in (None, "same-origin", "none"):
            return None
        host, now = (host_value(headers) or "").lower(), self.clock()
        with self._lock:
            rec = self._sessions.get(morsel.value)
        if rec is None or rec[1] <= now or not hmac.compare_digest(rec[0].encode(), host.encode()):
            return None
        return morsel.value

    def csrf(self, sid: str) -> str:
        """The per-session double-submit token the signed-in page echoes (derived from the master token; not the master token)."""
        return hmac.new(self.master.encode(), b"csrf|" + sid.encode(), hashlib.sha256).hexdigest()

    def read_ok(self, headers, port: int) -> bool:
        """May this request read private state? exact Host, a foreign Origin never, then the master token or a bound session."""
        if not host_exact(host_value(headers), port) or not origin_ok(headers.get("Origin"), loopback_origins(port)):
            return False
        return self.master_ok(headers) or self.session_of(headers) is not None

    def write_ok(self, headers, port: int) -> bool:
        """May this request change something? exact Host and Origin, then the master token, or a bound session AND its double-submit
        token in `header`."""
        if not host_exact(host_value(headers), port) or not origin_ok(headers.get("Origin"), loopback_origins(port)):
            return False
        if self.master_ok(headers):
            return True
        sid = self.session_of(headers)
        return sid is not None and secret_ok(headers.get(self.header), self.csrf(sid))

    def page_token(self, headers) -> str | None:
        """What a signed-in page may put in its <meta>: the session's double-submit token (never the master token); None if not signed in."""
        sid = self.session_of(headers)
        return self.csrf(sid) if sid else None

    # -- the /auth routes (shared by every server that has a browser page)
    def auth_route(self, method: str, path: str, headers, body: bytes, port: int):
        """(status, extra headers, JSON-able reply) for POST /auth/code and POST /auth/session, else None for another path."""
        if path not in ("/auth/code", "/auth/session"):
            return None
        if method != "POST":
            return 405, {}, {"error": "use POST"}
        if not host_exact(host_value(headers), port) or not origin_ok(headers.get("Origin"), loopback_origins(port)):
            return 403, {}, {"error": "refused: loopback host and own origin only"}
        wait = self.locked()
        if wait > 0:
            return 429, {"Retry-After": str(int(wait) + 1)}, {"error": "too many failed sign-in attempts; try again shortly"}
        if path == "/auth/code":
            if not self.master_ok(headers):
                self._fail()
                return 401, {}, {"error": "the master token is required"}
            return 200, {}, {"code": self.issue_code(), "expires_in": int(self.code_ttl)}
        try:
            code = (json.loads(body.decode("utf-8")) or {}).get("code") if body else None
        except (ValueError, UnicodeDecodeError, AttributeError):
            code = None
        sid = self.redeem(code, headers.get("Host"))
        if sid is None:
            self._fail()
            return 401, {}, {"error": "that launch code is unknown, expired or already used"}
        return 200, {"Set-Cookie": self.cookie_header(sid)}, {"ok": True}
