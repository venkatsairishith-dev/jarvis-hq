"""Where does a web or download task run: the owner's PC or VM2? (D79, 2026-10-27)

Owner: "web downloads of unknown things should happen on VM2", and "if I specifically say so" a task runs on VM2. The rule is DATA (config/vm2.json `routing`) read by this
small pure module, in this order:

  1. the owner's own words name the PC ("on my PC", "in my Chrome")                          -> pc   (explicit_pc)
  2. the owner's own words name VM2 ("on VM2", "in the sandbox", "on the server desktop")   -> vm2  (explicit_vm2)
  3. the task needs the owner's real signed-in browser ("my account", a deck on a generator,
     a host the owner granted on the PC)                                                    -> pc   (logged_in)
  4. a download of a program, script, archive or Office file with macros (by the file name in the address, or the words "installer", "setup"), or ANY download from a host
     that is not on the owner's trusted list                                                -> vm2  (risky_download / untrusted_download)
  5. "research", "browse", "explore" ... a site JARVIS does not know                         -> vm2  (research_unknown)
  6. everything else                                                                       -> pc   (default)

Only the OWNER'S request text is read here (never a page, a window or a model's output), and nothing it says can pick a different VM2: the address of the viewer and the control
service come from the configuration file alone.

`resolve` adds the one rule that is not about data: a task meant for VM2 is NEVER quietly done on the PC. If VM2 is not set up, offline, or held by the owner, the answer is a
plain sentence and a question (wait / use the PC for this one task / cancel); the PC is used only when the owner chooses it.
"""
from __future__ import annotations

import re
import urllib.parse
from dataclasses import dataclass, field

from . import ask_form
from .vm2 import Vm2Config, Vm2Port

URL_RX = re.compile(r"(?i)\b((?:https?://)?(?:[a-z0-9-]+\.)+[a-z]{2,24}(?::\d+)?(?:/[^\s<>\"']*)?)")
DOWNLOAD_RX = re.compile(r"(?i)\b(download|downloading|fetch|grab|get me|save (?:the )?file)\b")
RESEARCH_RX = re.compile(r"(?i)\b(research|browse|explore|investigate|look into|check out|read up on|find out (?:what|about)|look at the site|visit)\b")
LOGGED_IN_RX = re.compile(r"(?i)\b(my (?:\w+ )?account|logged[- ]?in|signed[- ]?in|sign in|log in|my (?:gmail|drive|google|outlook|bank|inbox|profile|subscription)|with my login|"
                          r"slides?|deck|presentation|powerpoint)\b")
PROGRAM_WORDS = re.compile(r"(?i)\b(installer|setup\.exe|\.exe|\.msi|crack|keygen|patch|macro|macros)\b")
TLD_NOT_A_SITE = {"txt", "pdf", "doc", "docx", "xls", "xlsx", "ppt", "pptx", "zip", "rar", "exe", "msi", "png", "jpg", "jpeg", "gif", "csv", "json", "html", "py", "js", "md"}

REASONS = {
    "explicit_pc": "you said to do it on your PC", "explicit_vm2": "you said to do it on VM2", "logged_in": "it needs your own signed-in browser",
    "risky_download": "it downloads a program, script, archive or macro file", "untrusted_download": "it downloads from a site you have not marked as trusted",
    "research_unknown": "it browses a site JARVIS does not know", "default": "an ordinary task",
}


@dataclass
class Route:
    target: str = "pc"                    # pc | vm2
    reason: str = "default"
    hosts: list = field(default_factory=list)
    explicit: bool = False
    state: str = "ok"                     # ok | not_set_up | offline | held   (only for vm2)
    ask: dict | None = None               # the form to show when a vm2 task cannot run now
    say: str = ""

    @property
    def blocked(self) -> bool:
        return self.target == "vm2" and self.state != "ok"

    @property
    def why(self) -> str:
        return REASONS.get(self.reason, self.reason)


def hosts_in(text: str) -> list[tuple[str, str]]:
    """[(host, path)] of the web addresses in the owner's words (a bare 'file.zip' is not a site)."""
    out = []
    for m in URL_RX.finditer(text or ""):
        raw = m.group(1).rstrip(".,;:)!?")
        p = urllib.parse.urlsplit(raw if "://" in raw else "//" + raw)
        host = (p.hostname or "").lower()
        if not host or host.rsplit(".", 1)[-1] in TLD_NOT_A_SITE:
            continue
        out.append((host, p.path or ""))
    return out


def _ext(path: str, text: str) -> str:
    m = re.search(r"(\.[a-z0-9]{1,6})(?:$|[?#])", path.lower())
    if m:
        return m.group(1)
    m = re.search(r"(?i)\b[\w.-]+(\.[a-z0-9]{2,5})\b(?!\s*/)", text)
    return m.group(1).lower() if m and m.group(1).lower().lstrip(".") in TLD_NOT_A_SITE else ""


def _in(host: str, listed) -> bool:
    h = host.lower()
    return any(h == x or h.endswith("." + x) for x in (str(y).lower().lstrip("*.") for y in listed or []))


def classify(text: str, routing: dict, trusted_hosts=(), known_hosts=()) -> Route:
    """Pure: the target the data rules choose (availability is `resolve`'s business)."""
    t = " ".join(str(text or "").split())
    low = t.lower()
    pairs = hosts_in(t)
    hosts = [h for h, _ in pairs]
    has = lambda phrases: any(p in low for p in phrases or [])           # noqa: E731
    if has(routing.get("explicit_pc")):
        return Route("pc", "explicit_pc", hosts, True)
    if has(routing.get("explicit_vm2")):
        return Route("vm2", "explicit_vm2", hosts, True)
    trusted = list(routing.get("trusted_download_hosts") or []) + list(trusted_hosts or [])
    known = list(routing.get("known_sites") or []) + list(known_hosts or []) + trusted
    if LOGGED_IN_RX.search(t) or (hosts and all(_in(h, trusted_hosts) for h in hosts) and not DOWNLOAD_RX.search(t)):
        return Route("pc", "logged_in", hosts)
    risky = {str(x).lower() for x in routing.get("risky_extensions") or []}
    downloading = bool(DOWNLOAD_RX.search(t))
    ext = ""
    for _h, path in pairs:
        ext = ext or _ext(path, "")
    ext = ext or _ext("", t)
    if downloading and (ext in risky or PROGRAM_WORDS.search(t)):
        return Route("vm2", "risky_download", hosts)
    if downloading and (hosts and not all(_in(h, trusted) for h in hosts)):
        return Route("vm2", "untrusted_download", hosts)
    if routing.get("research_unknown_sites", True) and RESEARCH_RX.search(t) and hosts and not all(_in(h, known) for h in hosts):
        return Route("vm2", "research_unknown", hosts)
    return Route("pc", "default", hosts)


def _question(route: Route, state: str) -> tuple[str, dict]:
    say = {"not_set_up": "VM2 is not set up on this PC yet.", "offline": "VM2 is not answering right now.",
           "held": "You have the VM2 desktop at the moment."}[state]
    q = f"{say} This task was meant for VM2 ({route.why}). What should I do?"
    form = ask_form.build_form(q, ["Wait for VM2", "Use my PC for this one task", "Cancel"], reason="I never move a sandbox task to your PC by myself.", source="vm2_route", default="o3")
    return say, form


def resolve(text: str, cfg: Vm2Config, port: Vm2Port | None = None, trusted_hosts=(), known_hosts=()) -> Route:
    """The route with availability. A VM2 route that cannot run now carries `state`, a plain `say` and an ask form; it is never changed to pc here."""
    route = classify(text, cfg.routing, trusted_hosts, known_hosts)
    if route.target != "vm2":
        return route
    if not cfg.has_control:
        route.state = "not_set_up"
    else:
        word = (port or Vm2Port(cfg)).status(force=True)
        route.state = {"ready": "ok", "busy": "ok", "needs_you": "held"}.get(word, "offline")
    if route.state != "ok":
        route.say, route.ask = _question(route, route.state)
    return route
