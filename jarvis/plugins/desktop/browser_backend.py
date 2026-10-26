"""The browser agent's real backend: every action is a JARVIS kernel job (D27), run on the node by browserops.py on the owner's real Chrome through the
Playwright extension (D64, 2026-10-26). Also the client for the one-time per-site grants (the kernel's `site_grant/*` routes).

The engine (browser_agent.py) never imports this: it gets a `Backend`. Here:
  observe()   one `observe` job; the page (accessibility snapshot, tabs, address) comes back as a LOCAL result file on this laptop, read once and deleted
  act(a)      one browser job; the kernel decides first (policy.py: ordinary / grant / the owner's PIN), the node checks the live page and the element again
  a job held for the owner comes back as Res(kind="approval") and the engine reports it (the PIN is the owner's, in the console)
"""
from __future__ import annotations

import re
import secrets
import time

from . import core
from .browser_agent import Act, Backend, BackendLost, Res

NODE_KIND = [  # (substring of the node's / kernel's message, Res.kind): the engine reacts to the kind, never to the words
    ("the page changed", "stale"), ("is not on the page now", "stale"), ("hint no longer finds", "stale"), ("observe the page again", "stale"),
    ("not on ", "host"), ("the page is on ", "host"), ("not an https page", "host"),
    ("human check", "challenge"), ("captcha", "challenge"),
    ("intercepts pointer events", "blocked"), ("is not visible", "blocked"), ("Timeout", "blocked"), ("element is not enabled", "blocked"),
    ("more than one element matches", "ambiguous"), ("no button", "not_found"), ("no link", "not_found"), ("no textbox", "not_found"), ("no checkbox", "not_found"),
    ("kill switch", "stopped"), ("cancelled", "stopped"),
    ("browser connection failed", "lost"), ("extension is not installed", "lost"), ("needs", "lost"),
    ("refused by policy", "refused"), ("bank, wallet", "refused"), ("terms forbid", "refused"),
]


def classify(error: str) -> str:
    low = (error or "").lower()
    for part, kind in NODE_KIND:
        if part.lower() in low:
            return kind
    return ""


class KernelBackend(Backend):
    """`pc_run(body, timeout, what) -> {"state", "result", ...}` is the desktop plugin's own job runner (it holds the PC lock's fence, the journal's job notes
    and the owner's stop), so a browser task is stopped by `stop_pc_task` like any other PC work."""

    def __init__(self, pc_run, profile: str = "default", task: str = None, take_local=core.take_local, timeout: float = 120.0):
        self.pc_run, self.profile = pc_run, profile
        self.task = task or secrets.token_hex(6)
        self.take_local = take_local
        self.timeout = timeout
        self.ended = False

    # ------------------------------------------------------------------------------------------------------------
    def _body(self, a: Act) -> dict:
        body = {"op": "browser", "action": a.kind, "profile": self.profile, "task": self.task, "url": a.url or "", "target": a.label or "", "text": a.value or ""}
        if a.kind == "xy_click" and a.x is not None:
            body["target"], body["text"] = f"{int(a.x)},{int(a.y or 0)}", a.label or a.value or ""
        if a.kind == "scroll" and a.value != "into_view":
            body["target"] = ""
        if a.kind in ("tab_select", "tab_close", "dialog", "wait", "press", "goto", "reload", "back", "session_end", "observe", "download", "cred_new"):
            body["target"] = ""
        if a.kind == "drag":
            body["text"] = a.value
        if a.host:
            body["host"] = a.host
        for k, f in (("via", "via"), ("ref", "ref"), ("ctx", "ctx"), ("ext", "ext"), ("sel", "sel")):
            if getattr(a, f, ""):
                body[k] = getattr(a, f)
        return body

    def _run(self, body: dict, what: str) -> dict:
        try:
            return self.pc_run(body, self.timeout, what)
        except core.DesktopError as e:
            msg = str(e)
            return {"state": "refused", "result": {"ok": False, "error": msg[:240]}}

    def observe(self) -> dict:
        r = self._run({"op": "browser", "action": "observe", "profile": self.profile, "task": self.task, "url": "", "target": "", "text": ""}, "look at the page")
        res = r.get("result") or {}
        if r.get("state") == "waiting_for_permission":
            raise BackendLost("the page could not be read: " + str(r.get("how") or "held for the owner")[:120])
        if r.get("state") != "done" or not res.get("ok"):
            raise BackendLost(str(res.get("error") or r.get("state") or "the page could not be read")[:200])
        doc = self.take_local(res.get("local_result")) if res.get("local_result") else None
        if not isinstance(doc, dict):
            raise BackendLost("the page text did not come back")
        doc.setdefault("auth_required", res.get("auth_required"))
        doc.setdefault("challenge_required", res.get("challenge_required"))
        return doc

    def act(self, a: Act) -> Res:
        r = self._run(self._body(a), f"{a.kind} {a.label}"[:100])
        state, result = r.get("state"), dict(r.get("result") or {})
        if state == "waiting_for_permission":
            return Res(False, "waiting for the owner's approval", "approval", result, str(r.get("approval_id") or ""), str(r.get("how") or ""))
        if state in ("cancelled",) or r.get("stopped"):
            return Res(False, "stopped", "stopped")
        if state == "denied":
            return Res(False, "the owner did not approve it", "refused", result, str(r.get("approval_id") or ""))
        if state == "refused":
            return Res(False, str(result.get("error") or "refused"), classify(str(result.get("error"))) or "refused", result)
        if state == "done" and result.get("ok") is True:
            return Res(True, "", "", result)
        err = str(result.get("error") or state or "failed")
        return Res(False, err, classify(err) or ("lost" if result.get("needs") else ""), result)

    def local(self, res: Res, key: str = "local_result"):
        """The local document a job left on this laptop (a downloaded file's path), read once."""
        ref = res.data.get(key)
        return self.take_local(ref) if ref else None

    def end(self) -> None:
        if self.ended:
            return
        self.ended = True
        try:
            self._run({"op": "browser", "action": "session_end", "profile": self.profile, "task": self.task, "url": "", "target": "", "text": ""}, "close the browser session")
        except Exception:  # noqa: BLE001 - the node closes an idle session by itself after three minutes
            pass


class GrantsClient:
    """The kernel's per-site grants as the plugin sees them: which abilities are granted now (a cached read), and the request / apply calls. The plugin never
    writes a grant; it asks for a card, and the owner confirms it with the PIN in the console."""

    def __init__(self, owner=None, clock=time.time, sleep=time.sleep, ttl: float = 15.0):
        self.owner = owner or core.owner
        self.clock, self.sleep, self.ttl = clock, sleep, ttl
        self._cache = (0.0, {})

    def _read(self) -> dict:
        t, got = self._cache
        if self.clock() - t < self.ttl:
            return got
        try:
            code, body = self.owner("GET", "site_grants")
        except Exception:  # noqa: BLE001 - the console is down: no grants known
            code, body = 0, {}
        out: dict = {}
        if code == 200:
            for s in body.get("sites") or []:
                if isinstance(s, dict) and s.get("host") and float(s.get("expires", 0)) > self.clock():
                    out[str(s["host"])] = set(s.get("allowed") or [])
        self._cache = (self.clock(), out)
        return out

    def has(self, host: str, ability: str) -> bool:
        return ability in self._read().get(str(host or "").lower(), set())

    def missing(self, host: str, abilities) -> list:
        return [a for a in abilities if not self.has(host, a)]

    def invalidate(self) -> None:
        self._cache = (0.0, {})

    def request(self, hosts, abilities, days: int = 30, note: str = "") -> dict:
        """Ask the kernel for the approval card. Returns {approval_id, card, request} or {error}."""
        code, body = self.owner("POST", "site_grant/request", {"hosts": list(hosts), "abilities": list(abilities), "days": days, "note": note})
        if code >= 400:
            return {"error": str(body.get("error") or f"refused (HTTP {code})")[:200]}
        return body

    def apply(self, req: dict, approval_id: str) -> dict:
        """After the owner confirmed the card with the PIN: write the grant. {granted: [...]} or {error, pending: True} while it is not approved yet."""
        code, body = self.owner("POST", "site_grant/apply", dict(req, approval_id=approval_id))
        self.invalidate()
        if code == 200:
            return body
        return {"error": str(body.get("error") or f"HTTP {code}")[:200], "pending": code == 403}

    def wait_and_apply(self, req: dict, approval_id: str, wait_s: float = 300.0, stop=lambda: False, poll_s: float = 3.0) -> dict:
        end = self.clock() + wait_s
        while True:
            got = self.apply(req, approval_id)
            if "granted" in got:
                return got
            if not got.get("pending") or stop() or self.clock() >= end:
                return got
            self.sleep(poll_s)
