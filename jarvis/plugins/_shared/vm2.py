"""VM2, the second server's Linux desktop (the Agent PC), as JARVIS sees it (D79, 2026-10-27, owner: "VM2 should have a dedicated Linux desktop, like Muse").

Everything the JARVIS side needs to know about VM2 comes from ONE file, `jarvis/config/vm2.json` (console-protected: the kernel's SAFETY_COMPONENTS and the push gate
list it), so the real VM2 plugs in by configuration and nothing else changes. This module holds:

  Vm2Config   the file, validated. A viewer or control address must be a Tailscale address (100.64.0.0/10 or *.ts.net) or this machine, over http(s), with NO user name,
              password, token, key or session in it. A bad address is dropped with a reason; the rest of the file still works. The viewer address is the only address JARVIS ever
              opens for the owner, it comes from this file and from nowhere else (never from a page, a model or a message), and it is never written to a log, a journal, a card or
              a notice: the viewer's own password is the owner's, typed by him in the viewer, and JARVIS never reads it.
  Vm2Port     the lab's existing control protocol, spoken as it is (lab_control.py behind the hub's /control/ route): GET state, POST take (the agent user's processes are frozen),
              POST continue (thawed), plus two read-only routes of the same service for the download quarantine. A health probe with a short timeout says ready / busy / needs you
              / offline. Nothing is invented on the server side.
  status words and the HUD line  "VM2 ready", "VM2 busy", "VM2 needs you", "VM2 offline" (nothing at all when VM2 is not configured).

Standard library only; the HTTP transport is injected so every rule is tested without a network.
"""
from __future__ import annotations

import ipaddress
import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

JARVIS = Path(__file__).resolve().parents[2]
CONFIG_FILE = JARVIS / "config" / "vm2.json"
ENV_CONFIG = "JARVIS_VM2_CONFIG"
HUB_HEADER = {"X-Jarvis-Hub": "1"}
STATES = ("off", "offline", "ready", "busy", "needs_you")
HUD_WORDS = {"ready": "VM2 ready", "busy": "VM2 busy", "needs_you": "VM2 needs you", "offline": "VM2 offline"}
SECRETY = re.compile(r"(?i)pass|pwd|token|secret|key|auth|sid|session|cookie|credential|bearer|jwt|otp|code")
HOST_NAME = re.compile(r"^[a-z0-9]([a-z0-9.-]{0,78}[a-z0-9])?$")
DEFAULTS = {
    "enabled": False, "viewer_url": "", "view_only_param": "view_only", "control_url": "", "probe_timeout_s": 3.0, "probe_ttl_s": 60.0,
    "takeover_minutes": 30, "max_reasks": 3, "look_wait_s": 20.0, "grant_days": 14, "quarantine_max_bytes": 50 * 1024 * 1024,
    "poll_s": 4.0, "task_wait_minutes": 40,
}
DEFAULT_ROUTING = {
    "default": "pc",
    "explicit_vm2": ["on vm2", "on vm 2", "in vm2", "to vm2", "using vm2", "with vm2", "in the sandbox", "on the sandbox", "in a sandbox", "in the safe desktop",
                     "on the server desktop", "on the server's desktop", "on the agent pc", "on the agent computer", "in the agent pc"],
    "explicit_pc": ["on my pc", "on my computer", "on my laptop", "in my chrome", "in my browser", "on this pc"],
    "trusted_download_hosts": [],
    "risky_extensions": [".exe", ".msi", ".dll", ".bat", ".cmd", ".ps1", ".vbs", ".js", ".jar", ".scr", ".lnk", ".reg", ".sh", ".apk", ".dmg", ".pkg",
                         ".zip", ".rar", ".7z", ".tar", ".gz", ".tgz", ".iso", ".cab",
                         ".docm", ".xlsm", ".pptm", ".doc", ".xls", ".ppt"],
    "research_unknown_sites": True,
    "known_sites": [],
}


class Vm2Error(Exception):
    """A call to VM2 failed. The message never carries an address."""


# ---------------------------------------------------------------------------------------------------------------------- addresses
def host_class(host: str) -> str:
    """'loopback' | 'tailnet' | '' (anything else: refused)."""
    h = str(host or "").strip().lower().strip("[]")
    if h == "localhost" or h.endswith(".localhost"):
        return "loopback"
    if h.endswith(".ts.net") and HOST_NAME.match(h):
        return "tailnet"
    try:
        ip = ipaddress.ip_address(h)
    except ValueError:
        return ""
    if ip.is_loopback:
        return "loopback"
    if isinstance(ip, ipaddress.IPv4Address) and ip in ipaddress.ip_network("100.64.0.0/10"):
        return "tailnet"
    if isinstance(ip, ipaddress.IPv6Address) and ip in ipaddress.ip_network("fd7a:115c:a1e0::/48"):
        return "tailnet"
    return ""


def check_endpoint(url: str) -> tuple[bool, str]:
    """(ok, reason). The reason is a fixed sentence: it never repeats the address. Rules in the module docstring."""
    raw = str(url or "").strip()
    if not raw:
        return False, "empty"
    try:
        p = urllib.parse.urlsplit(raw)
        port = p.port
    except ValueError:
        return False, "the address cannot be read"
    if p.scheme not in ("http", "https"):
        return False, "only http or https"
    if not p.hostname:
        return False, "no host"
    if p.username is not None or p.password is not None or "@" in p.netloc:
        return False, "a user name or password must never be in the address"
    if not host_class(p.hostname):
        return False, "only a Tailscale address (100.64.0.0/10 or *.ts.net) or this machine is accepted"
    if port is not None and not 1 <= port <= 65535:
        return False, "bad port"
    for k, v in urllib.parse.parse_qsl(p.query, keep_blank_values=True):
        if SECRETY.search(k) or SECRETY.search(v):
            return False, "a password, token or key must never be in the address (the viewer asks the owner for its own password)"
    if SECRETY.search(urllib.parse.unquote(p.path)) and re.search(r"(?i)(token|secret|password|key)=?", p.path):
        return False, "a password, token or key must never be in the address"
    if p.fragment and SECRETY.search(p.fragment):
        return False, "a password, token or key must never be in the address"
    return True, ""


@dataclass
class Vm2Config:
    exists: bool = False
    enabled: bool = False
    viewer_url: str = ""
    view_only_param: str = "view_only"
    control_url: str = ""
    probe_timeout_s: float = 3.0
    probe_ttl_s: float = 60.0
    takeover_minutes: float = 30.0
    max_reasks: int = 3
    look_wait_s: float = 20.0
    grant_days: int = 14
    quarantine_max_bytes: int = 50 * 1024 * 1024
    poll_s: float = 4.0
    task_wait_minutes: float = 40.0
    routing: dict = field(default_factory=lambda: json.loads(json.dumps(DEFAULT_ROUTING)))
    problems: list = field(default_factory=list)
    path: str = ""

    # ---- what is usable
    @property
    def has_viewer(self) -> bool:
        return bool(self.enabled and self.viewer_url)

    @property
    def has_control(self) -> bool:
        return bool(self.enabled and self.control_url)

    def viewer_target(self) -> tuple[str, int] | None:
        """(host, port) of the viewer: the ONE address the face's network lock may add. None unless the file exists, is enabled, and holds a valid viewer address."""
        if not self.has_viewer:
            return None
        p = urllib.parse.urlsplit(self.viewer_url)
        return (p.hostname or "").lower(), p.port or (443 if p.scheme == "https" else 80)

    def viewer_open_url(self, view_only: bool = False) -> str:
        """The address to open for the owner (view_only: watching; otherwise he takes control). Built only from the file. Never log the result."""
        if not self.has_viewer:
            return ""
        p = urllib.parse.urlsplit(self.viewer_url)
        q = [(k, v) for k, v in urllib.parse.parse_qsl(p.query, keep_blank_values=True) if k != self.view_only_param]
        q.append((self.view_only_param, "1" if view_only else "0"))
        return urllib.parse.urlunsplit((p.scheme, p.netloc, p.path, urllib.parse.urlencode(q), p.fragment))


def config_path(path=None) -> Path:
    if path:
        return Path(path)
    env = os.environ.get(ENV_CONFIG, "").strip()
    return Path(env) if env else CONFIG_FILE


def load_config(path=None) -> Vm2Config:
    """The configuration, or a disabled one. Never raises. A bad address is dropped (with a reason in `problems`), the rest stays."""
    p = config_path(path)
    cfg = Vm2Config(path=str(p))
    try:
        data = json.loads(p.read_text(encoding="utf-8-sig"))
    except FileNotFoundError:
        return cfg
    except (OSError, ValueError):
        cfg.exists, cfg.problems = True, ["vm2.json cannot be read"]
        return cfg
    if not isinstance(data, dict):
        cfg.exists, cfg.problems = True, ["vm2.json is not an object"]
        return cfg
    cfg.exists = True
    cfg.enabled = data.get("enabled") is True
    for key in ("viewer_url", "control_url"):
        value = str(data.get(key) or "").strip().rstrip("/") if key == "control_url" else str(data.get(key) or "").strip()
        if value:
            ok, why = check_endpoint(value)
            if ok:
                setattr(cfg, key, value)
            else:
                cfg.problems.append(f"{key}: {why}")
    param = str(data.get("view_only_param") or "view_only")
    cfg.view_only_param = param if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,30}", param) and not SECRETY.search(param) else "view_only"

    def num(key, lo, hi, cast=float):
        try:
            v = cast(data.get(key, DEFAULTS[key]))
        except (TypeError, ValueError):
            return DEFAULTS[key]
        return min(max(v, lo), hi)
    cfg.probe_timeout_s = num("probe_timeout_s", 0.5, 15)
    cfg.probe_ttl_s = num("probe_ttl_s", 5, 600)
    cfg.takeover_minutes = num("takeover_minutes", 1, 24 * 60)
    cfg.max_reasks = int(num("max_reasks", 1, 5, int))
    cfg.look_wait_s = num("look_wait_s", 1, 120)
    cfg.grant_days = int(num("grant_days", 1, 30, int))
    cfg.quarantine_max_bytes = int(num("quarantine_max_bytes", 1024, 500 * 1024 * 1024, int))
    cfg.poll_s = num("poll_s", 0.5, 60)
    cfg.task_wait_minutes = num("task_wait_minutes", 1, 240)
    routing = data.get("routing")
    if isinstance(routing, dict):
        for k, base in DEFAULT_ROUTING.items():
            if k in routing and isinstance(routing[k], type(base)):
                cfg.routing[k] = routing[k]
        if cfg.routing.get("default") != "pc":                    # the default is the owner's PC; a file cannot flip it to VM2
            cfg.routing["default"] = "pc"
    return cfg


# ---------------------------------------------------------------------------------------------------------------------- transport
class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None


def default_http(method: str, url: str, headers: dict, body: bytes | None, timeout: float, max_bytes: int) -> tuple[int, bytes, dict]:
    """(status, body, headers) with no proxy and no redirect. Raises Vm2Error without the address."""
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect())
    req = urllib.request.Request(url, data=body, method=method, headers=headers)
    try:
        with opener.open(req, timeout=timeout) as r:
            data = r.read(max_bytes + 1)
            if len(data) > max_bytes:
                raise Vm2Error("the answer is bigger than the allowed size")
            return r.status, data, {k.lower(): v for k, v in r.headers.items()}
    except urllib.error.HTTPError as e:
        try:
            data = e.read(max_bytes + 1)
        except OSError:
            data = b""
        return e.code, data, {k.lower(): v for k, v in (e.headers or {}).items()}
    except (urllib.error.URLError, OSError, TimeoutError) as e:
        raise Vm2Error(f"VM2 did not answer ({type(e).__name__})") from None


Http = Callable[[str, str, dict, "bytes | None", float, int], "tuple[int, bytes, dict]"]


@dataclass
class Frozen:
    ok: bool
    tasks: int = 0
    detail: str = ""


class Vm2Port:
    """The lab's control protocol as the laptop speaks it. `http` is injectable."""

    def __init__(self, config: Vm2Config | None = None, http: Http | None = None, clock: Callable[[], float] = time.time):
        self.cfg = config or load_config()
        self.http = http or default_http
        self.clock = clock
        self._probe = (0.0, "off", {})

    # ---- low level
    def _call(self, method: str, path: str, timeout: float | None = None, max_bytes: int = 4 * 1024 * 1024) -> tuple[int, bytes, dict]:
        if not self.cfg.has_control:
            raise Vm2Error("VM2 is not set up")
        return self.http(method, self.cfg.control_url.rstrip("/") + path, dict(HUB_HEADER), b"" if method == "POST" else None,
                         timeout if timeout is not None else self.cfg.probe_timeout_s, max_bytes)

    def _json(self, method: str, path: str, timeout: float | None = None) -> dict:
        code, body, _h = self._call(method, path, timeout)
        if code != 200:
            raise Vm2Error(f"VM2 answered HTTP {code}")
        try:
            d = json.loads(body.decode("utf-8", "replace") or "{}")
        except ValueError:
            raise Vm2Error("VM2 answered something that is not JSON") from None
        if not isinstance(d, dict):
            raise Vm2Error("VM2 answered something unexpected")
        return d

    # ---- health
    def state(self) -> dict | None:
        """The control state, or None when VM2 does not answer (a short timeout, never a hang)."""
        try:
            return self._json("GET", "/control/state")
        except Vm2Error:
            return None

    @staticmethod
    def words(st: dict | None) -> str:
        """One of STATES from a control state."""
        if st is None:
            return "offline"
        if st.get("mode") == "owner":
            return "needs_you"
        tasks = [t for t in st.get("tasks") or [] if isinstance(t, dict)]
        return "busy" if any(not t.get("paused") for t in tasks) else "ready"

    def status(self, force: bool = False) -> str:
        """'off' (not configured), 'offline', 'ready', 'busy' or 'needs_you'. Cached for probe_ttl_s unless forced."""
        if not self.cfg.has_control:
            return "off"
        t, word, _ = self._probe
        if not force and self.clock() - t < min(self.cfg.probe_ttl_s, 5.0) and word != "off":
            return word
        st = self.state()
        self._probe = (self.clock(), self.words(st), st or {})
        return self._probe[1]

    def available(self) -> bool:
        """Can a task be sent to VM2 right now? (configured, answering; a VM2 the owner holds counts as unavailable for new work.)"""
        return self.status(force=True) in ("ready", "busy")

    # ---- the lab's own control protocol
    def take(self) -> Frozen:
        """POST /control/take, then CHECK what the server says: mode owner and every agent task paused. Frozen(ok=False) says plainly what is not so."""
        try:
            st = self._json("POST", "/control/take", timeout=max(self.cfg.probe_timeout_s, 8.0))
        except Vm2Error as e:
            return Frozen(False, 0, str(e)[:120])
        tasks = [t for t in st.get("tasks") or [] if isinstance(t, dict)]
        running = [t for t in tasks if not t.get("paused")]
        if st.get("mode") != "owner":
            return Frozen(False, len(tasks), "VM2 did not switch to the owner's control")
        if running:
            return Frozen(False, len(tasks) - len(running), f"{len(running)} agent task(s) are still running")
        return Frozen(True, len(tasks), "")

    def resume(self) -> bool:
        """POST /control/continue; True only when the server reports the agents have the desktop again."""
        try:
            st = self._json("POST", "/control/continue", timeout=max(self.cfg.probe_timeout_s, 8.0))
        except Vm2Error:
            return False
        return st.get("mode") == "agent"

    def reported_takeover(self, st: dict | None = None) -> dict | None:
        """The wall a lab agent reported with need_owner: {kind, host} (a closed kind and a plain host), or None. Free text from the agent is never read."""
        st = st if st is not None else self.state()
        t = (st or {}).get("takeover")
        if not isinstance(t, dict):
            return None
        kind = t.get("kind") if t.get("kind") in ("captcha", "bot_check", "two_factor", "email_code", "login", "payment", "consent", "other") else "other"
        host = str(t.get("host") or "").lower()
        return {"kind": kind, "host": host if HOST_NAME.match(host) else "", "at": float(t.get("at") or 0)}

    # ---- the download quarantine (read-only routes of the same service)
    def quarantine(self) -> list[dict]:
        """The inspection records of the files in VM2's quarantine (see lab/quarantine.py). Raises Vm2Error when VM2 does not answer."""
        d = self._json("GET", "/control/quarantine", timeout=max(self.cfg.probe_timeout_s, 20.0))
        files = [f for f in d.get("files") or [] if isinstance(f, dict)]
        return files[:200]

    def fetch(self, file_id: str, max_bytes: int | None = None) -> tuple[bytes, str]:
        """(bytes, the sha256 the server states). The caller re-hashes; nothing is trusted from the header."""
        if not re.fullmatch(r"[0-9a-f]{24}", str(file_id or "")):
            raise Vm2Error("bad file id")
        code, body, h = self._call("GET", "/control/quarantine/" + file_id, timeout=120.0, max_bytes=max_bytes or self.cfg.quarantine_max_bytes)
        if code != 200:
            raise Vm2Error(f"VM2 answered HTTP {code}")
        return body, h.get("x-jarvis-sha256", "")


# ---------------------------------------------------------------------------------------------------------------------- HUD
def state_file(data: Path | None = None) -> Path:
    if data is None:
        from jarvis.pluginapi.paths import data_dir
        data = data_dir()
    return Path(data) / "plugins" / "vm2_state.json"


def write_state(word: str, path: Path | None = None, now: float | None = None) -> None:
    try:
        p = Path(path) if path else state_file()
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps({"v": 1, "state": word if word in STATES else "offline", "at": time.time() if now is None else now}), encoding="utf-8")
        os.replace(tmp, p)
    except OSError:
        pass


def hud_fields(path: Path | None = None, now: float | None = None, port: Vm2Port | None = None, config: Vm2Config | None = None) -> dict:
    """{} when VM2 is not configured; else {"vm2": <state word>, "vm2_line": "VM2 ready"}. The state is read from a small file and re-probed (short timeout) when it
    is older than probe_ttl_s, so the HUD's frequent polling does not hit the network."""
    cfg = config or (port.cfg if port else load_config())
    if not cfg.has_control:
        return {}
    now = time.time() if now is None else now
    p = Path(path) if path else state_file()
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
        word, at = d["state"], float(d["at"])
    except (OSError, ValueError, KeyError, TypeError):
        word, at = "", 0.0
    if word not in STATES or now - at > cfg.probe_ttl_s:
        word = (port or Vm2Port(cfg)).status(force=True)
        write_state(word, p, now)
    return {"vm2": word, "vm2_line": HUD_WORDS.get(word, "")} if word in HUD_WORDS else {}


# ---------------------------------------------------------------------------------------------------------------------- grants for VM2
class Vm2Grants:
    """The kernel's per-site grants that are FOR VM2 (`target: vm2`, kernel/sitegrants.py). The PC client (browser_backend.GrantsClient) ignores them and this one ignores the
    PC's. The plugin only ever ASKS for a card; the owner confirms it with the PIN in the console, and nothing here can write a grant."""

    def __init__(self, owner: Callable[..., tuple], clock: Callable[[], float] = time.time, ttl: float = 15.0):
        self.owner, self.clock, self.ttl = owner, clock, ttl
        self._cache: tuple = (0.0, {})

    def _read(self) -> dict:
        t, got = self._cache
        if self.clock() - t < self.ttl:
            return got
        try:
            code, body = self.owner("GET", "site_grants")
        except Exception:  # noqa: BLE001 - the console is down: no grants known
            code, body = 0, {}
        out: dict = {}
        if code == 200 and isinstance(body, dict):
            for s in body.get("sites") or []:
                if isinstance(s, dict) and s.get("host") and s.get("target") == "vm2" and float(s.get("expires", 0)) > self.clock():
                    out[str(s["host"])] = set(s.get("allowed") or [])
        self._cache = (self.clock(), out)
        return out

    def has(self, host: str, ability: str) -> bool:
        return ability in self._read().get(str(host or "").lower(), set())

    def request(self, hosts, abilities, days: int = 14, note: str = "") -> dict:
        code, body = self.owner("POST", "site_grant/request", {"hosts": list(hosts), "abilities": list(abilities), "days": days, "note": note, "target": "vm2"})
        if code >= 400:
            return {"error": str((body or {}).get("error") or f"refused (HTTP {code})")[:200]}
        self._cache = (0.0, {})
        return body
