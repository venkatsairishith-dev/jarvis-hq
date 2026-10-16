"""Neko's hands on this PC, through the JARVIS kernel (logic; no N.E.K.O imports, tested without servers).

Every action is one request to the JARVIS console's owner route (127.0.0.1:8799/api/owner/pc). The kernel decides: ordinary
actions run at once, consequential ones come back as "needs_approval" and wait for the owner's PIN in the console, refused
ones say why. The laptop node runs the job and reports what it actually observed. This module only asks and waits.

Privacy: screen text and page text are written by the node to outputs\\neko-local on this laptop; take_local() reads such a
file once and deletes it. What goes back to the main agent (and so to its model) is a diagnosis written here, not the
screen text, unless the owner set NEKO_SCREEN_TEXT_TO_MODEL=1.
"""
from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Callable

CONSOLE = os.environ.get("JARVIS_CONSOLE_URL", "http://127.0.0.1:8799").rstrip("/")
DATA = Path(os.environ.get("NEKO_STORAGE_SELECTED_ROOT", r"D:\jarvis\N.E.K.O-data"))
ACTIVITY = DATA / "neko" / "activity.json"
LOCAL_RESULTS = Path(os.environ.get("JARVIS_OUTPUTS", r"D:\jarvis\outputs")) / "neko-local"
PROFILES = DATA / "neko" / "browser_profiles.json"
FINAL = ("done", "failed", "cancelled", "expired")
STATES = ("inspecting", "diagnosing", "researching", "waiting_for_permission", "applying_fix", "verifying", "completed", "failed")


class DesktopError(Exception):
    pass


def screen_text_allowed() -> bool:
    return os.environ.get("NEKO_SCREEN_TEXT_TO_MODEL", "").strip().lower() in ("1", "true", "yes")


# ---------------------------------------------------------------- the console's owner route
def owner(method: str, name: str, body: dict | None = None, timeout: float = 20.0) -> tuple[int, dict]:
    if not re.match(r"^http://(127\.0\.0\.1|localhost)(:\d+)?$", CONSOLE):
        raise DesktopError("JARVIS_CONSOLE_URL must be plain http on a loopback address")
    data = json.dumps(body).encode() if body is not None else None
    # X-Jarvis-Console marks a local program (not a web page): the console refuses cross-site POSTs without it
    req = urllib.request.Request(f"{CONSOLE}/api/owner/{name}", data=data, method=method,
                                 headers={"X-Jarvis-Console": "1", **({"Content-Type": "application/json"} if data else {})})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read().decode("utf-8", "replace") or "{}")
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode("utf-8", "replace") or "{}")
        except ValueError:
            return e.code, {"error": f"HTTP {e.code}"}
    except (urllib.error.URLError, OSError, TimeoutError) as e:
        raise DesktopError(f"the JARVIS console is not answering ({type(e).__name__}); is it running?") from None


class Pc:
    """Ask, wait for approval when the kernel wants one, wait for the job, return what the node saw."""

    def __init__(self, call=owner, sleep=time.sleep, clock=time.time, on_state: Callable[[str, str], None] | None = None,
                 approval_wait_s: float = 300.0):
        self.call, self.sleep, self.clock = call, sleep, clock
        self.on_state = on_state or (lambda s, d: None)
        self.approval_wait_s = approval_wait_s
        self.current_job: str | None = None
        self.stopped = False

    def submit(self, body: dict) -> dict:
        code, r = self.call("POST", "pc", body)
        if code >= 400:
            raise DesktopError(str(r.get("error") or f"refused (HTTP {code})")[:300])
        return r

    def approval_pending(self, aid: str) -> bool:
        code, st = self.call("GET", "state", None)
        return code < 400 and any(a.get("id") == aid for a in st.get("approvals") or [])

    def wait_approval(self, aid: str, what: str) -> bool:
        """Wait (a few minutes at most) until the owner approved or denied it in the console. True = no longer pending."""
        self.on_state("waiting_for_permission", f"Approve in the JARVIS console with your PIN: {what}"[:200])
        end = self.clock() + self.approval_wait_s
        while self.clock() < end and not self.stopped:
            if not self.approval_pending(aid):
                return True
            self.sleep(3.0)
        return False

    def wait_job(self, jid: str, timeout: float) -> dict:
        self.current_job = jid
        end = self.clock() + timeout
        try:
            while self.clock() < end:
                if self.stopped:
                    self.cancel(jid)
                code, j = self.call("GET", f"job/{jid}", None)
                if code < 400 and j.get("state") in FINAL:
                    return j
                self.sleep(1.0)
            self.cancel(jid)
            return {"id": jid, "state": "failed", "result": {"ok": False, "error": f"no result within {int(timeout)} s; it was cancelled"}}
        finally:
            self.current_job = None

    def cancel(self, jid: str) -> dict:
        code, r = self.call("POST", "job/cancel", {"id": jid})
        return r if code < 400 else {"error": r.get("error")}

    def run(self, body: dict, timeout: float = 90.0, what: str = "") -> dict:
        """One action end to end. Returns {"state": done|failed|cancelled|waiting_for_permission|denied, "result": {...}}."""
        r = self.submit(body)
        if r.get("status") == "needs_approval":
            aid = r["approval_id"]
            if not self.wait_approval(aid, what or r.get("reason", "")):
                return {"state": "waiting_for_permission", "approval_id": aid, "level": r.get("level"), "reason": r.get("reason"),
                        "how": "Approve it in the JARVIS console (http://127.0.0.1:8799) with your PIN, then ask Neko to continue "
                               f"with approval_id {aid}."}
            try:
                r = self.submit(dict(body, approval_id=aid))
            except DesktopError as e:
                return {"state": "denied", "approval_id": aid, "result": {"ok": False, "error": str(e)}}
        j = self.wait_job(r["job"], timeout)
        return {"state": j.get("state"), "job": j.get("id"), "result": j.get("result") or {}}


# ---------------------------------------------------------------- local results and the icon
def take_local(path: str | None):
    """Read a node result that had to stay on this laptop, then delete it (not retained). Only inside neko-local."""
    if not path:
        return None
    p = Path(path).resolve()
    if LOCAL_RESULTS.resolve() not in p.parents:
        raise DesktopError("a local result outside outputs\\neko-local was refused")
    try:
        text = p.read_text(encoding="utf-8")
    finally:
        try:
            p.unlink()
        except OSError:
            pass
    try:
        return json.loads(text)
    except ValueError:
        return text


def set_activity(state: str, detail: str = "", path: Path = ACTIVITY):
    """What Neko is doing on the PC right now, for the icon (the widget reads it; old entries are ignored)."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"state": state, "detail": detail[:200], "at": time.time()}), encoding="utf-8")
    except OSError:
        pass


def profiles(path: Path = PROFILES) -> list[dict]:
    try:
        reg = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    return [{"profile": p["dir"], "name": p.get("name"), "account": p.get("account"), "default": p.get("default"),
             "say": sorted(a for a in p.get("aliases", []) if not a.startswith("profile"))[:4]} for p in reg.get("profiles", [])]


# ---------------------------------------------------------------- diagnosis (local)
FILE_IN_TEXT = re.compile(r"([A-Za-z]:\\[^\s\"'<>|*?]+\.(?:json|ini|cfg|conf|config|xml|ya?ml|toml|txt))", re.I)
NUMBER_TYPO = re.compile(r"^(\d+)[^\d\s,}\]]+(\d*)$")


def diagnose(windows: list, events: list, crashes: list, app_dir: str | None = None) -> dict:
    """What went wrong, from the error dialog, the event log and crash records, checked against the files it names.
    A cause is 'verified' only when the named file really has the fault; otherwise it is a hypothesis."""
    texts = [t for w in windows or [] for t in (w.get("texts") or []) + [w.get("title") or ""]]
    dialog = [w for w in windows or [] if w.get("dialog")]
    out = {"dialog_seen": bool(dialog), "dialog_title": (dialog[0].get("title") if dialog else "")[:120],
           "verified": False, "cause": "", "evidence": [], "fix": None, "app_dir": app_dir}
    joined = " ".join(texts)
    m = FILE_IN_TEXT.search(joined)
    if m:
        path = m.group(1)
        out["file"] = path
        if path.lower().endswith(".json") and os.path.isfile(path):
            raw = Path(path).read_text(encoding="utf-8-sig")
            try:
                json.loads(raw)
                out["evidence"].append("the file the dialog names is valid JSON now")
            except json.JSONDecodeError as e:
                line = raw.splitlines()[e.lineno - 1] if 0 < e.lineno <= len(raw.splitlines()) else ""
                out.update(verified=True, cause=f"{Path(path).name} is not valid JSON at line {e.lineno}, column {e.colno} ({e.msg})",
                           line=e.lineno)
                out["evidence"].append(f"JSON check of {Path(path).name}: {e.msg} at line {e.lineno}")
                fix = _number_typo_fix(line)
                if fix:
                    out["fix"] = {"action": "edit_file", "target": path, "args": {"find": fix[0], "replace": fix[1]},
                                  "why": "a value that must be a number has a typo"}
    dll = re.search(r"(?i)\b([\w.-]+\.dll)\b was not found|(?:missing|cannot find|could not load)\D{0,20}\b([\w.-]+\.dll)\b", joined)
    mod = re.search(r"No module named '([\w.]+)'", joined)
    if not out["cause"] and dll:
        name = dll.group(1) or dll.group(2)
        found = _dll_present(name, out.get("app_dir"))
        out.update(cause=f"a required library is missing: {name}", verified=not found, dependency=name)
        out["evidence"].append(f"{name} {'is present after all (the error may be about a different copy)' if found else 'is in neither the program folder nor the system path'}")
    elif not out["cause"] and mod:
        out.update(cause=f"a required Python module is missing: {mod.group(1)} (a hypothesis until its environment is checked)",
                   dependency=mod.group(1))
    if not out["cause"] and dialog:
        out["cause"] = "the program showed an error dialog: " + ("; ".join(texts)[:200] if screen_text_allowed() else
                                                                "its text stayed on this laptop")
    if crashes:
        c = crashes[0]
        out["evidence"].append(f"crash record: {c.get('app')} {c.get('kind')} in {c.get('module') or '?'} code {c.get('code') or '?'}")
        if not out["cause"]:
            out["cause"] = f"{c.get('app')} crashed in {c.get('module') or 'an unknown module'} (code {c.get('code') or '?'}): a hypothesis until reproduced"
    if events:
        out["evidence"].append(f"{len(events)} recent error event(s) from: " + ", ".join(sorted({str(e.get('provider')) for e in events}))[:160])
    if not out["cause"]:
        out["cause"] = "no error dialog, crash record or error event was found for it"
    return out


def _dll_present(name: str, app_dir: str | None = None) -> bool:
    dirs = ([app_dir] if app_dir else []) + [os.environ.get("SystemRoot", r"C:\Windows") + r"\System32"] + os.environ.get("PATH", "").split(os.pathsep)
    return any(d and os.path.isfile(os.path.join(d, name)) for d in dirs)


def _number_typo_fix(line: str):
    """'  "port": 80x80' -> ('"port": 80x80', '"port": 8080'): only when the value is digits with a stray character."""
    m = re.search(r'("[^"]+"\s*:\s*)([^,}\s]+)', line)
    if not m:
        return None
    t = NUMBER_TYPO.match(m.group(2))
    if not t:
        return None
    return m.group(0), m.group(1) + t.group(1) + t.group(2)
