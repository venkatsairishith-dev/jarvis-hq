"""Connector jobs on this machine: start, test, connect, call and stop the local MCP servers the owner allowed here.

What may run is decided ONLY by this machine's allowlist (config/connectors.local.json, written by the console when the owner
adds a connector). A job names a connector; it never carries a command. Secrets are read from files under the secrets folder,
by reference, and passed to the server as environment variables; their values are never returned, logged or put in a result.

Every result leaving this module has been scrubbed and size-capped, and tool metadata is reduced to what is safe to show.
"""
import json
import os
import re
import subprocess
import sys
import threading
import time

from .mcpclient import McpError, StdioSession

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "kernel"))
from jarvis_kernel import mcpwire as W  # noqa: E402

SECRETS = r"D:\jarvis-secrets"
WORKROOT = os.path.join(os.path.dirname(__file__), "..", "..", "..", "cache", "connectors")
CONFIG = os.path.join(os.path.dirname(__file__), "..", "..", "config", "connectors.local.json")
ENV_NAME = re.compile(r"^[A-Z_][A-Z0-9_]{0,63}$")
BASE_ENV_WINDOWS = ("SystemRoot", "SystemDrive", "windir", "PATHEXT", "ComSpec", "NUMBER_OF_PROCESSORS", "PROCESSOR_ARCHITECTURE", "OS")
IDLE_SECONDS = 600
OPS = {"discover", "test", "connect", "disconnect", "call", "status"}

_sessions = {}
_lock = threading.Lock()
_stats = {}


def load_config(path=None) -> dict:
    try:
        with open(path or CONFIG, encoding="utf-8-sig") as fh:
            d = json.load(fh).get("connectors", {})
        return d if isinstance(d, dict) else {}
    except FileNotFoundError:
        return {}
    except (OSError, ValueError):
        print("connectors.local.json is not valid: no connector can start", file=sys.stderr)
        return {}


def _secret(ref: str, secrets_dir: str):
    """The value in a secrets file named by reference, or None. The reference must stay inside the secrets folder."""
    if not isinstance(ref, str) or not re.match(r"^[A-Za-z0-9][A-Za-z0-9_./-]{0,120}$", ref) or ".." in ref:
        return None
    root = os.path.realpath(secrets_dir)
    full = os.path.realpath(os.path.join(root, ref.replace("/", os.sep)))
    if os.path.commonpath([root, full]) != root or not os.path.isfile(full):
        return None
    try:
        with open(full, encoding="utf-8-sig") as fh:
            v = fh.read(4097).strip()
    except OSError:
        return None
    return v if 0 < len(v) <= 4096 else None


def build_env(spec: dict, workdir: str, secrets_dir: str = None):
    """(environment for the server, names of secret references that could not be read). Nothing from this process leaks in
    except a handful of system variables; the server's PATH is its own folder plus the system folder."""
    secrets_dir = secrets_dir or SECRETS
    env = {}
    if os.name == "nt":
        for k in BASE_ENV_WINDOWS:
            if os.environ.get(k):
                env[k] = os.environ[k]
        sysroot = os.environ.get("SystemRoot", r"C:\Windows")
        env["PATH"] = os.pathsep.join([os.path.dirname(spec["command"][0]), os.path.join(sysroot, "System32")])
    else:
        env["PATH"] = os.pathsep.join([os.path.dirname(spec["command"][0]), "/usr/bin", "/bin"])
    env["TEMP"] = env["TMP"] = env["TMPDIR"] = workdir
    env["PYTHONIOENCODING"] = "utf-8"
    for k, v in (spec.get("env_static") or {}).items():
        if isinstance(k, str) and ENV_NAME.match(k) and isinstance(v, str) and len(v) <= 500:
            env[k] = v
    missing = []
    for k, ref in (spec.get("env_refs") or {}).items():
        if not (isinstance(k, str) and ENV_NAME.match(k)):
            continue
        val = _secret(ref, secrets_dir)
        if val is None:
            missing.append(k)
        else:
            env[k] = val
    return env, missing


def _redact(text, values):
    """Remove the exact secret values this server was started with from anything about to leave: a server that echoes its own
    environment (or a log line that carries it) must not turn into a way to read the owner's credentials."""
    t = str(text)
    for v in values or ():
        if isinstance(v, str) and len(v) >= 4:
            t = t.replace(v, "[redacted]")
    return t


def _fail(op, name, kind, message, **extra):
    return {"ok": False, "op": op, "connector": name, "error_class": kind, "error": str(message)[:300], **extra}


def _close(name):
    s = _sessions.pop(name, None)
    if s is not None:
        try:
            s.close()
        except Exception:
            pass


def reap(now=None, idle=IDLE_SECONDS):
    """Stop servers nobody has used for a while."""
    now = now or time.time()
    with _lock:
        for n in [n for n, s in _sessions.items() if not s.alive() or now - s.last_used > idle]:
            _close(n)


def shutdown_all():
    with _lock:
        for n in list(_sessions):
            _close(n)


def _session(name, spec, secrets_dir, popen):
    """A live, handshaken session for the connector, started if needed. Returns (session, reconnected)."""
    s = _sessions.get(name)
    key = json.dumps([spec.get("command"), spec.get("env_refs"), spec.get("env_static")], sort_keys=True, default=str)
    if s is not None and s.alive() and getattr(s, "spec_key", None) == key and os.path.isfile(spec["command"][0]):
        return s, False
    if s is not None and s.alive():                 # the owner's allowlist entry changed (or the program is gone): start again from it
        _close(name)
        s = None
    reconnected = s is not None
    _close(name)
    exe = spec["command"][0]
    if not os.path.isfile(exe):
        raise McpError("start", "the server program is not installed at the configured path")
    work = os.path.realpath(os.path.join(WORKROOT, name))
    os.makedirs(work, exist_ok=True)
    env, missing = build_env(spec, work, secrets_dir)
    if missing:
        raise McpError("needs_setup", "missing secret file(s) for: " + ", ".join(sorted(missing)))
    s = StdioSession(spec["command"], env=env, cwd=work, popen=popen)
    s.secret_values = [env[k] for k in (spec.get("env_refs") or {}) if k in env]
    s.spec_key = key
    s.start()
    try:
        s.handshake(timeout=float(spec.get("timeout_s", 20)))
    except McpError as e:
        e.stderr = _redact(s.stderr_tail(), s.secret_values)     # why it would not start, scrubbed, for the owner's diagnostics
        s.close(grace=1.0)
        raise
    except Exception:
        s.close(grace=1.0)
        raise
    _sessions[name] = s
    _stats[name] = _stats.get(name, 0) + (1 if reconnected else 0)
    return s, reconnected


def handle(params: dict, cfg: dict = None, secrets_dir: str = None, popen=subprocess.Popen) -> dict:
    """Run one connector job. Never raises; every failure is a result with an honest class."""
    t0 = time.time()
    name, op = str(params.get("connector", "")), str(params.get("op", ""))
    if op not in OPS or not W.CONNECTOR_NAME.match(name):
        return _fail(op, name, "bad_request", "unknown connector operation")
    cfg = load_config() if cfg is None else cfg
    spec = cfg.get(name)
    if (not isinstance(spec, dict) or not isinstance(spec.get("command"), list) or not spec["command"]
            or not all(isinstance(a, str) for a in spec["command"])):
        return _fail(op, name, "not_configured", "this connector is not set up on this computer")
    secrets_dir = secrets_dir or SECRETS
    timeout = max(3.0, min(float(params.get("timeout_s") or spec.get("timeout_s") or 20), 120.0))
    max_output = int(max(500, min(int(params.get("max_output") or W.DEFAULT_MAX_OUTPUT), 60000)))
    with _lock:
        try:
            if op == "status":
                s = _sessions.get(name)
                installed = os.path.isfile(spec["command"][0])
                return {"ok": True, "op": op, "connector": name, "installed": installed, "state": "connected" if s and s.alive() else "stopped",
                        "reconnects": _stats.get(name, 0)}
            if op == "disconnect":
                _close(name)
                return {"ok": True, "op": op, "connector": name, "state": "stopped", "ms": int((time.time() - t0) * 1000)}
            was_connected = name in _sessions and _sessions[name].alive()
            s, reconnected = _session(name, spec, secrets_dir, popen)
            if op in ("discover", "test", "connect"):
                raw = s.list_tools(timeout=timeout)
                tools = [W.sanitize_tool(t) for t in raw]
                tools = [t for t in tools if t]
                for t in tools:
                    t["description"] = _redact(t["description"], s.secret_values)
                hs = {"era": s.era, "protocol": s.protocol, "server": {k: str(v)[:80] for k, v in s.server.items() if k in ("name", "version", "title")},
                      "capabilities": sorted(s.capabilities)[:12]}
                if op == "test" and not was_connected:
                    _close(name)                 # a test leaves nothing running
                state = "connected" if name in _sessions else "stopped"
                return {"ok": True, "op": op, "connector": name, "installed": True, "state": state, **hs, "tool_count": len(tools),
                        "tools": [W.compact_tool(t) for t in tools[:W.MAX_TOOLS]], "refused_server_requests": s.refused_requests,
                        "reconnected": reconnected, "ms": int((time.time() - t0) * 1000)}
            # call
            tool = str(params.get("tool", ""))
            args = params.get("args") if isinstance(params.get("args"), dict) else {}
            if not W.TOOL_NAME.match(tool):
                return _fail(op, name, "bad_request", "bad tool name")
            listed = {t["name"]: W.sanitize_tool(t) for t in s.list_tools(timeout=timeout) if isinstance(t, dict) and W.sanitize_tool(t)}
            cur = listed.get(tool)
            if cur is None:
                return _fail(op, name, "unknown_tool", "the server does not offer this tool", reconnected=reconnected)
            want = str(params.get("digest", ""))
            if want and want != cur["digest"]:
                return _fail(op, name, "tool_changed", "the server changed this tool since the owner reviewed it; it must be reviewed again",
                             reconnected=reconnected)
            res = s.call(tool, args, timeout=timeout)
            out = W.process_result(res, max_output)
            out["text"] = _redact(out["text"], s.secret_values)
            ok = not out["is_error"]
            return {"ok": ok, "op": op, "connector": name, "tool": tool, "result": out, "reconnected": reconnected,
                    **({} if ok else {"error_class": "tool_error", "error": out["text"][:200]}), "ms": int((time.time() - t0) * 1000)}
        except McpError as e:
            if e.kind in ("crash", "oversize", "timeout", "protocol", "handshake", "start"):
                _close(name)
            return _fail(op, name, e.kind, e, installed=os.path.isfile(spec["command"][0]), stderr=getattr(e, "stderr", "")[:300],
                         ms=int((time.time() - t0) * 1000))
        except Exception as e:
            _close(name)
            return _fail(op, name, "internal", type(e).__name__, ms=int((time.time() - t0) * 1000))
