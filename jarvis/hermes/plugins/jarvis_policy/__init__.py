"""JARVIS policy for Hermes (plan P2 step 4): a pre_tool_call hook that asks the JARVIS kernel what to do with every tool call.

Hermes logs and IGNORES an exception inside a hook (the tool would still run), so this plugin catches everything itself and answers
"block" whenever it cannot get a clear answer: kernel unreachable, bad token, odd reply, unknown level. Hermes itself blocks a hook that
times out; this plugin's own HTTP timeout is shorter, so the reason shown is ours.

The kernel is the judge (POST /v1/jarvis/policy on the model gateway, with the agent's own token; lane and requester come from the token):
    GREEN, YELLOW_AUTO      -> allow (no directive)
    YELLOW_APPROVAL         -> {"action": "approve"}: Hermes asks the owner in its own approval flow
    RED, DENY, anything else -> {"action": "block"}
Tools that only touch Hermes's own bookkeeping are allowed here without asking; every other tool is mapped onto the kernel's
vocabulary, and a tool this file does not know is sent as ("hermes", <name>), which the kernel has no rule for, so it is RED.
After a tool that reads from outside (web, browser) the session is marked untrusted, and the kernel is told.
Token: <HERMES_HOME>/jarvis-memory.token if present, else ~/.jarvis-llm-token. Endpoint: $JARVIS_ENDPOINT or http://127.0.0.1:8790.
"""
from __future__ import annotations

import json
import logging
import os
import threading
import urllib.error
import urllib.request

log = logging.getLogger(__name__)
TIMEOUT = 4
MAX_TARGET = 400

# Hermes's own bookkeeping: no file, network or process effect outside Hermes's home.
LOCAL_SAFE = frozenset({"todo_list", "clarify", "skill_view", "skills_list", "session_search", "memory", "show_tip", "set_theme",
                        "setup_beat", "focus_pane", "read_terminal", "read_window_below", "annotate_preview", "jarvis_memory_list",
                        "jarvis_memory_add", "jarvis_memory_update", "jarvis_memory_forget"})
READS = {"read_file": ("fs", "read"), "search_files": ("fs", "read")}
WRITES = {"write_file": ("fs", "write"), "patch": ("fs", "write"), "skill_manage": ("config", "write")}
WEB = {"web_search": ("browser", "open"), "web_extract": ("browser", "extract"), "x_search": ("browser", "open")}
BROWSER = {"browser_cdp": ("browser", "click"), "browser_exec": ("browser", "click"), "browser_dialog": ("browser", "click"),
           "computer_use": ("browser", "click"), "browser_vault": ("browser", "upload")}
SHELL = frozenset({"terminal", "execute_code", "process_manage", "close_terminal", "cronjob_manage", "delegate_task"})

_untrusted: set[str] = set()
_lock = threading.Lock()


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None                        # never re-send the bearer token to another address


_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect())


def _target(args: dict) -> str:
    for k in ("path", "file_path", "command", "url", "query", "name", "code"):
        v = args.get(k)
        if isinstance(v, str) and v:
            return v[:MAX_TARGET]
    return ""


def classify(tool_name: str, args: dict) -> tuple[str, str, str] | None:
    """(kernel tool, op, target) for a Hermes tool call, or None when it needs no ruling."""
    if tool_name in LOCAL_SAFE:
        return None
    args = args if isinstance(args, dict) else {}
    for table in (READS, WRITES, WEB, BROWSER):
        if tool_name in table:
            tool, op = table[tool_name]
            return tool, op, _target(args)
    if tool_name in SHELL:
        return "shell", "run", _target(args)
    return "hermes", str(tool_name)[:40], _target(args)


def _token() -> str:
    home = os.environ.get("HERMES_HOME") or os.path.expanduser("~/.hermes")
    for p in (os.path.join(home, "jarvis-memory.token"), os.path.expanduser("~/.jarvis-llm-token")):
        try:
            with open(p, encoding="utf-8") as f:
                t = f.read().strip()
            if t:
                return t
        except OSError:
            continue
    return ""


def ask_kernel(tool: str, op: str, target: str, untrusted: bool) -> dict | None:
    tok = _token()
    if not tok:
        return None
    url = os.environ.get("JARVIS_ENDPOINT", "http://127.0.0.1:8790").rstrip("/") + "/v1/jarvis/policy"
    data = json.dumps({"tool": tool, "op": op, "target": target, "untrusted": untrusted}).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST", headers={"Content-Type": "application/json", "Authorization": "Bearer " + tok})
    try:
        with _OPENER.open(req, timeout=TIMEOUT) as r:
            out = json.loads(r.read())
    except (OSError, ValueError, urllib.error.URLError):
        return None
    return out if isinstance(out, dict) and out.get("status") == "ok" and isinstance(out.get("level"), str) else None


def decide(tool_name: str, args: dict, task_id: str = "") -> dict | None:
    """The pre_tool_call directive for one call (None = allow). Never raises."""
    try:
        c = classify(tool_name, args)
        if c is None:
            return None
        tool, op, target = c
        with _lock:
            untrusted = task_id in _untrusted
        out = ask_kernel(tool, op, target, untrusted)
        if out is None:
            return {"action": "block", "message": "JARVIS policy could not be reached, so nothing is allowed to run (fail closed)."}
        level, reason = out["level"], str(out.get("reason", ""))[:200]
        if tool == "browser" and op in ("open", "extract") and level in ("GREEN", "YELLOW_AUTO"):
            with _lock:
                _untrusted.add(task_id)                        # what comes back from the web is untrusted from now on
        if level in ("GREEN", "YELLOW_AUTO"):
            return None
        if level == "YELLOW_APPROVAL":
            return {"action": "approve", "message": f"JARVIS asks the owner to approve {tool}.{op}: {reason}", "rule_key": f"jarvis:{tool}.{op}"}
        return {"action": "block", "message": f"JARVIS policy refuses {tool}.{op} ({level}): {reason}"}
    except Exception:                                          # noqa: BLE001 - a hook error would let the tool run, so block instead
        log.exception("jarvis_policy failed; blocking")
        return {"action": "block", "message": "JARVIS policy failed, so nothing is allowed to run (fail closed)."}


def pre_tool_call(tool_name: str, args: dict, task_id: str = "", **kwargs):
    return decide(tool_name, args, task_id)


def register(ctx) -> None:
    ctx.register_hook("pre_tool_call", pre_tool_call)
