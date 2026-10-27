"""The browser agent: a reusable engine that drives the owner's REAL Chrome (his profiles and sessions) through the Playwright-extension relay
(D64, 2026-10-26, owner: "a GRAND MASTER in browser automation").

    observe   accessibility snapshot first (browser_snapshot.py), the local vision model only when the snapshot is not enough (a canvas, an icon button);
              page text never goes to an online model by itself, only the one tab, only through the privacy gateway for an explicit reasoning step
    act       click, fill, select, press, scroll, hover, drag, upload (from the owner's known folders, by name), new tabs and pop-ups, iframes, shadow DOM
              (the snapshot pierces both), cookie banners and "Continue / Skip / Not now" interstitials, infinite scroll, lazy loading, SPA route changes;
              waits are CONDITIONS (a text appeared, the page stopped changing), never a fixed sleep; before a state-changing action there is only a short
              natural pause (a person does not click twice in a millisecond; this is pacing, not evasion)
    verify    every action is checked by its EFFECT (the field holds the text, the url or the page changed, a text appeared, the file arrived and has its
              slides), never by "the click returned ok"
    recover   a ladder: retry once -> alternate locators (role+name, text, label, nearby text, XPath hint, coordinates from the local vision model) ->
              scroll into view -> close the overlay -> reload -> go back -> re-plan the sub-goal with the decider -> (the caller tries the next site) ->
              only then ask the owner, with a form that shows what blocked it
    walls     a CAPTCHA, a bot check, a 2FA or e-mail code, a payment form, a password box: NEVER bypassed, solved or worked around. The engine stops, shows the
              owner exactly the page state and asks him to do that ONE step, then continues from the live page
    safety    page text is data: it cannot change the goal, the grant, the host list or the plan (the planner only picks one control of the current page by
              number; the typed text and every address come from the goal and the playbook, never from the page); a host list per task (off-list navigation
              asks); step and time budgets; a stop flag the owner's "stop" reaches; a content-free journal line per action (D61)

The engine knows nothing about Windows or the kernel: it talks to a `Backend` (browser_backend.py is the real one, the tests have a fake browser).
The kernel still decides every action (policy.py) and the node checks the live page and the element again (browserops.py).
"""
from __future__ import annotations

import dataclasses
import random
import re
import time
import urllib.parse
from dataclasses import dataclass, field
from typing import Callable

from . import browser_snapshot as S
from .browser_snapshot import Locator, Page

STATE_CHANGING = {"click", "type", "select", "press", "drag", "upload", "check", "xy_click", "goto", "download", "cred_new", "dialog"}
JOB_KIND = {"click": "click", "fill": "type", "select": "select", "press": "press", "hover": "hover", "drag": "drag", "upload": "upload", "check": "check",
            "scroll": "scroll", "goto": "goto", "back": "back", "reload": "reload", "wait": "wait", "tab_select": "tab_select", "tab_close": "tab_close",
            "dialog": "dialog", "download": "download", "cred_new": "cred_new", "xy_click": "xy_click"}
SSO_HOSTS = ("accounts.google.com", "login.microsoftonline.com", "login.live.com")
MAX_GRANT_HOSTS = 4
# controls a planner or an overlay-dismisser must never choose, whatever the page says (the kernel gates them as well)
NEVER_PROPOSE = re.compile(r"(?i)\b(pay|payment|buy|purchase|order|checkout|subscribe|upgrade|go pro|get pro|donate|billing|credit card|delete|remove|erase|trash|"
                           r"uninstall|install|unsubscribe|transfer|withdraw|send|post|publish|share|invite|sign ?out|log ?out|close account|deactivate|"
                           r"change password|password|authori[sz]e|grant|admin|buy credits|add credits|top up)\b")
WALL_WORDS = {"captcha": "a CAPTCHA or bot check", "two_factor": "a two-step verification code", "email_code": "an e-mail verification",
              "payment": "a payment form", "signin": "a sign-in", "error": "an error page", "paywall": "a limit that needs an upgrade"}


class AgentError(Exception):
    pass


class Stopped(AgentError):
    """The owner said stop (stop_pc_task), or the engine's kill flag is set."""


class BudgetExceeded(AgentError):
    pass


class BackendLost(AgentError):
    """The browser connection is gone (the extension was not allowed, Chrome closed)."""


class NeedOwner(AgentError):
    """Only the owner can go on: a wall, an off-list address, a grant card, an unanswered question. `state` is what to show him."""

    def __init__(self, kind: str, detail: str, state: dict = None):
        super().__init__(f"{kind}: {detail}")
        self.kind, self.detail, self.state = kind, detail, state or {}


class NeedApproval(AgentError):
    """A step is held for the owner's PIN in the console (the kernel said so)."""

    def __init__(self, res: "Res", act: "Act"):
        super().__init__(res.error or "waiting for the owner's approval")
        self.res, self.act = res, act


class StepFailed(AgentError):
    def __init__(self, step_id: str, why: str, rungs: list, page_state: dict = None):
        super().__init__(f"step {step_id} failed: {why}")
        self.step_id, self.why, self.rungs, self.page_state = step_id, why, rungs, page_state or {}


# ------------------------------------------------------------------------------------------------------------------------------ data
@dataclass
class Act:
    kind: str                       # a JOB_KIND value or observe/locate/end
    label: str = ""                 # the element's accessible name: what the kernel reads
    role: str = ""
    ref: str = ""
    sel: str = ""                   # an xpath= / css= hint the node resolves and checks by name
    value: str = ""                 # typed text, an option, a key, a wait condition, a file NAME, a url
    url: str = ""
    x: "int | None" = None
    y: "int | None" = None
    host: str = ""                  # the host of the page this acts on (the node checks the live page)
    via: str = ""                   # at a sign-in provider: the granted site this belongs to
    ctx: str = ""                   # visible text of the dialog / provider screen: evidence for the kernel
    ext: str = ""                   # download types


@dataclass
class Res:
    ok: bool = False
    error: str = ""
    kind: str = ""                  # "" | refused | stale | blocked | host | challenge | approval | stopped | lost | timeout
    data: dict = field(default_factory=dict)
    approval_id: str = ""
    how: str = ""


class Backend:
    """What the engine needs from a browser. browser_backend.KernelBackend is the real one; tests have a fake."""

    def observe(self) -> dict:
        raise NotImplementedError

    def act(self, a: Act) -> Res:
        raise NotImplementedError

    def take_local(self, ref) -> "dict | str | None":
        return None

    def end(self) -> None:
        pass


@dataclass
class Budget:
    max_actions: int = 150
    max_seconds: float = 1500.0
    max_attempts_per_step: int = 14
    max_replans: int = 4
    max_owner_asks: int = 5
    patience_s: float = 8.0         # how long an effect may take to show before the step counts as failed (polled, not slept)
    poll_s: float = 0.4


@dataclass
class Step:
    id: str
    do: str                         # click fill select press scroll hover drag upload check goto back reload wait tab_select tab_close download cred_new expect
    loc: Locator = field(default_factory=Locator)
    value: str = ""                 # {param} placeholders, @cred:/@identity: references, or a short constant
    expect: dict = field(default_factory=dict)
    until: dict = field(default_factory=dict)         # for wait: the condition (text, text_any, gone, element) and fail_if
    skip_if: dict = field(default_factory=dict)       # already true -> nothing to do
    optional: bool = False
    intent: str = ""
    timeout_s: float = 0.0          # for wait steps
    safe_reload: bool = False
    password_ok: bool = False       # a password field is expected here (a sign-up form); never for a sign-in
    note: str = ""
    to: str = ""                    # for goto: the address
    new_tab: bool = False           # for goto: open it in a tab of its own (the first page of a task)

    @classmethod
    def from_dict(cls, d: dict) -> "Step":
        d = d or {}
        return cls(id=str(d.get("id") or d.get("do") or "step")[:40], do=str(d.get("do", "click")), loc=Locator.from_dict(d.get("loc") or {}), value=str(d.get("value", ""))[:300],
                   expect=dict(d.get("expect") or {}), until=dict(d.get("until") or {}), skip_if=dict(d.get("skip_if") or {}), optional=bool(d.get("optional")),
                   intent=str(d.get("intent", ""))[:120], timeout_s=float(d.get("timeout_s", 0) or 0), safe_reload=bool(d.get("safe_reload")),
                   password_ok=bool(d.get("password_ok")), note=str(d.get("note", ""))[:200], to=str(d.get("to", ""))[:300],
                   new_tab=bool(d.get("new_tab")))

    def to_dict(self) -> dict:
        out = {"id": self.id, "do": self.do}
        for k, v in (("loc", self.loc.to_dict()), ("value", self.value), ("expect", self.expect), ("until", self.until), ("skip_if", self.skip_if),
                     ("optional", self.optional), ("intent", self.intent), ("timeout_s", self.timeout_s), ("safe_reload", self.safe_reload),
                     ("password_ok", self.password_ok), ("note", self.note), ("to", self.to), ("new_tab", self.new_tab)):
            if v:
                out[k] = v
        return out


@dataclass
class Try:
    result: "StepResult | None"
    why: str
    state: str                      # ok | not_executed | no_effect | progress | wrong_effect | error_page


@dataclass
class StepResult:
    id: str
    ok: bool
    how: str = "primary"
    facts: list = field(default_factory=list)       # what was OBSERVED to be true after the step
    rungs: list = field(default_factory=list)
    error: str = ""
    repair: "dict | None" = None                    # the locator that worked when it was not the playbook's own
    skipped: bool = False
    data: dict = field(default_factory=dict)        # e.g. the downloaded file's name, size, sha256 (never its path)


def owner_wall(pg: Page, ok_password: bool = False):
    """The walls that are the owner's. An error page is not one: a reload or the next site may fix it, and only when nothing does is the owner told."""
    w = S.walls(pg, have_signin_ability=ok_password)
    return None if w is None or w.kind == "error" else w


FIELD_WORDS = {"topic": {"topic", "about", "prompt", "subject", "idea", "describe", "description", "title", "text", "content"},
               "title": {"title", "name", "heading"}, "slides": {"slides", "number", "count", "pages", "length", "cards"}, "theme": {"theme", "style", "template", "design"}}


def fill_name_fits(param: str, field_name: str) -> bool:
    """Does a field's name fit a parameter of the goal (the topic goes into a field called 'What is your presentation about?')?"""
    want = {param.lower()} | FIELD_WORDS.get(param.lower(), set())
    return bool(want & set(re.findall(r"[a-z0-9]+", str(field_name or "").lower())))


def fill(value: str, params: dict) -> str:
    """{name} placeholders from the task's parameters. An unknown placeholder is an error, never an empty string typed into a page."""
    def sub(m):
        k = m.group(1)
        if k not in (params or {}):
            raise AgentError(f"the step needs the value '{k}' and the task has none")
        return str(params[k])
    return re.sub(r"\{([a-z_][a-z0-9_]*)\}", sub, value or "")


def fill_loc(loc: Locator, params: dict) -> Locator:
    """A locator whose words hold {parameters} (the theme the owner named): the words are filled in; an unknown parameter is an error."""
    syn = tuple(x for x in (fill(a, params) for a in loc.any_of) if x)
    return dataclasses.replace(loc, name=fill(loc.name, params), text=fill(loc.text, params), label=fill(loc.label, params), near=fill(loc.near, params), any_of=syn)


# ------------------------------------------------------------------------------------------------------------------------------ the agent
class Agent:
    def __init__(self, backend: Backend, *, goal: str = "", hosts=(), planner=None, ask: Callable = None, journal: Callable = None, stop: Callable = None,
                 budget: Budget = None, banners: dict = None, grants=None, sleep=time.sleep, clock=time.monotonic, rng=None, online: Callable = None,
                 params: dict = None, take_local: Callable = None, human: bool = True):
        self.backend = backend
        self.goal = goal
        self.allowed = {h.lower() for h in hosts if h}
        self.planner = planner
        self._ask = ask
        self._journal = journal
        self._stop = stop or (lambda: False)
        self.budget = budget or Budget()
        self.banners = banners or {"reject": [], "accept": []}
        self.grants = grants                  # .has(host, ability) -> bool  (the kernel's list; None = no grants)
        self.sleep, self.clock = sleep, clock
        self.rng = rng or random.Random()
        self.online = online                  # an EXPLICIT reasoning step: online(question, redacted_text) -> str; never called by the engine on its own
        self.params = params or {}
        self.human = human
        self.t0 = clock()
        self.actions = 0
        self.asks = 0
        self.replans = 0
        self.page: "Page | None" = None
        self.sig_seen: set = set()
        self.injections = 0
        self.expect_password = False
        self.signin_site = ""                 # the granted site a step at a sign-in provider belongs to (the kernel reads it as `via`)
        self.events: list = []                # content-free log of what happened (also the journal's input)
        self.site_hint = ""
        self.owner_allowed: set = set()

    # ------------------------------------------------------------------------------------------------------------ budget, stop, journal
    def _tick(self, acting: bool = False) -> None:
        if self._stop():
            raise Stopped("the owner stopped the browser task")
        if self.clock() - self.t0 > self.budget.max_seconds:
            raise BudgetExceeded(f"the task used its {int(self.budget.max_seconds)} s")
        if acting:
            self.actions += 1
            if self.actions > self.budget.max_actions:
                raise BudgetExceeded(f"the task used its {self.budget.max_actions} actions")

    def _log(self, kind: str, **kw) -> None:
        ev = {"kind": kind, "host": (self.page.host if self.page else ""), **kw}
        self.events.append(ev)
        if self._journal:
            try:
                self._journal(ev)
            except Exception:  # noqa: BLE001 - the recorder never breaks an action
                pass

    def pause(self) -> None:
        """A short, natural pause before a state-changing action: a person does not click twice in a millisecond. Pacing, not evasion."""
        if self.human:
            self.sleep(self.rng.uniform(0.25, 0.8))

    # ------------------------------------------------------------------------------------------------------------ observing
    def observe(self) -> Page:
        self._tick()
        try:
            doc = self.backend.observe()
        except BackendLost:
            raise
        except Exception as e:  # noqa: BLE001
            if isinstance(e, AgentError):
                raise
            raise BackendLost(f"the browser could not be read ({type(e).__name__})") from None
        pg = S.parse(doc)
        if isinstance(doc, dict):
            pg._auth = bool(doc.get("auth_required"))           # type: ignore[attr-defined]
        self.page = pg
        self.injections += S.injection_count(pg)
        return pg

    def state(self, limit_names: int = 8) -> dict:
        """What the owner is shown when the engine is stuck: the site, what is on the page, what was tried. Names of controls only, never typed text."""
        pg = self.page
        if pg is None:
            return {"site": "", "title": "", "buttons": [], "events": self.events[-6:]}
        return {"site": pg.host, "title": pg.title[:80], "buttons": [n for _i, role, n in S.safe_names(pg, 40) if role in ("button", "link")][:limit_names],
                "tabs": len(pg.tabs), "events": [str(e.get("kind")) + ":" + str(e.get("act", e.get("wall", ""))) for e in self.events[-6:]]}

    # ------------------------------------------------------------------------------------------------------------ acting
    def act(self, a: Act) -> Res:
        self._tick(acting=True)
        if not a.host and self.page is not None and a.kind not in ("goto",):
            a.host = self.page.host
        if a.kind in STATE_CHANGING:
            self.pause()
        t = self.clock()
        res = self.backend.act(a)
        ms = int((self.clock() - t) * 1000)
        self._log("action", act=a.kind, role=a.role, ok=res.ok, res=res.kind, ms=ms, cls=("C" if res.kind == "approval" else "B" if a.kind in STATE_CHANGING else "A"))
        if res.kind == "stopped":
            raise Stopped("stopped")
        if res.kind == "lost":
            raise BackendLost(res.error or "the browser connection was lost")
        if res.kind == "approval":
            raise NeedApproval(res, a)
        return res

    def allow_host(self, host: str) -> None:
        if host:
            self.allowed.add(host.lower())

    # ------------------------------------------------------------------------------------------------------------ the owner
    def ask_owner(self, title: str, intro: str, rows: list, buttons: list, warn: str = "") -> str:
        """Show a form, wait for the owner's button. Returns the button id, or '' when nobody answered. Never carries a PIN, a password or a code."""
        self.asks += 1
        if self.asks > self.budget.max_owner_asks:
            return ""
        spec = {"title": title[:80], "intro": intro[:300], "fields": [{"type": "info_table", "label": "What JARVIS sees", "rows": [[str(k)[:40], str(v)[:200]] for k, v in rows]}]
                + ([{"type": "callout", "level": "warn", "text": warn[:300]}] if warn else []),
                "buttons": [{"id": i, "label": l, "style": s} for i, l, s in buttons[:4]]}
        self._log("ask", wall=title[:30])
        if not self._ask:
            return ""
        try:
            ans = self._ask(spec)
        except Exception:  # noqa: BLE001
            return ""
        return str(ans.get("button") or "") if isinstance(ans, dict) and ans.get("answered") else ""

    def hand_to_owner(self, wall: S.Wall, pg: Page) -> None:
        """A wall: stop, show exactly where the page is, ask for that one step, and go on from the live page. Never clicks, types or 'solves' anything here."""
        for _ in range(3):
            what = WALL_WORDS.get(wall.kind, "a step only you can do")
            names = [n for _i, role, n in S.safe_names(pg, 30) if role in ("button", "link")][:6]
            btn = self.ask_owner("JARVIS needs you for one step",
                                 f"{pg.host or 'The page'} shows {what}. JARVIS does not solve or skip this. Please do it in Chrome, then press Done.",
                                 [("Site", pg.host), ("What it is", wall.why), ("Page title", pg.title[:80] or "-"), ("Buttons seen", ", ".join(names) or "-")],
                                 [("done", "Done, continue", "primary"), ("stop", "Stop", "secondary")])
            self._log("wall", wall=wall.kind)
            if btn == "stop":
                raise Stopped("the owner chose to stop at " + wall.kind)
            if btn != "done":
                raise NeedOwner(wall.kind, f"{pg.host}: {wall.why}", self.state())
            pg = self.observe()
            again = owner_wall(pg, self.expect_password)
            if again is None:
                return
            wall = again
        raise NeedOwner(wall.kind, "the page still shows the same wall after three tries", self.state())

    # ------------------------------------------------------------------------------------------------------------ grants (the kernel decides; the engine asks)
    def has_grant(self, host: str, ability: str) -> bool:
        return bool(self.grants and self.grants.has(host, ability))

    # ------------------------------------------------------------------------------------------------------------ page hygiene
    def _check_host(self, pg: Page) -> None:
        """Off-list navigation asks. The addresses a task may visit: those the goal names, the playbook lists, the shortlist allows; a sign-in provider only
        while a granted sign-in is in progress. A page that opened somewhere else (a pop-up, an advert, a redirect) is closed or left, and the owner is asked."""
        h = pg.host
        if not h or h in self.allowed or h in self.owner_allowed:
            return
        if h in SSO_HOSTS and any(self.has_grant(a, "signin_existing_session") for a in self.allowed):
            return
        if not pg.https and h:
            pass
        self._log("offlist", wall="host")
        try:
            self.act(Act("back"))
        except NeedApproval:
            pass
        btn = self.ask_owner("This leaves the sites JARVIS was given",
                             f"The page moved to {h}, which is not on this task's list. JARVIS went back. Allow {h} for this task only?",
                             [("From", ", ".join(sorted(self.allowed))[:80]), ("To", h)], [("allow", "Allow this task", "secondary"), ("stay", "Stay on my sites", "primary")])
        if btn == "allow":
            self.owner_allowed.add(h)
            return
        raise NeedOwner("off_list", f"the page went to {h}, which this task may not visit", self.state())

    def _tabs_hygiene(self, pg: Page) -> Page:
        """Pop-ups: a new tab on an address the task may not visit is closed (and the first tab selected); an allowed one (a sign-in window, the next step of the
        flow) is kept and followed."""
        if len(pg.tabs) < 2:
            return pg
        cur = next((t for t in pg.tabs if t.get("current")), None)
        if cur is None:
            return pg
        host = (urllib.parse.urlsplit(str(cur.get("url", ""))).hostname or "").lower()
        if host and host not in self.allowed and host not in self.owner_allowed and host not in SSO_HOSTS:
            try:
                self.act(Act("tab_close", value=str(cur.get("index", ""))))
            except NeedApproval:
                pass
            self._log("popup_closed", wall="popup")
            return self.observe()
        return pg

    def dismiss_banners(self, pg: Page, force: bool = False) -> Page:
        """Cookie banners and 'Continue / Skip / Not now' interstitials, by a known list. Rejecting or 'necessary only' is preferred; 'accept' only on a granted host
        (accept_terms) because the kernel treats it as accepting terms."""
        for _ in range(3):
            if not (S.has_overlay(pg) or force):
                return pg
            el, why = S.overlay_dismiss(pg, self.banners.get("reject", []))
            if el is None and self.has_grant(pg.host, "accept_terms"):
                for want in self.banners.get("accept", []):
                    el = next((e for e in pg.els if e.role in ("button", "link") and e.ref and S._fold(e.shown) == S._fold(want)), None)
                    if el:
                        why = "known cookie banner button (accept, granted)"
                        break
            if el is None or NEVER_PROPOSE.search(el.shown or ""):
                return pg
            before = pg.sig()
            res = self.act(Act("click", label=el.name or el.child_text or el.role, role=el.role, ref=el.ref, host=pg.host, ctx=self.ctx_for(el, pg)))
            if not res.ok:
                return pg
            self._log("banner", wall=why[:30])
            pg = self.observe()
            if pg.sig() == before:
                return pg
        return pg

    def ctx_for(self, el, pg: Page) -> str:
        """The visible text of the dialog the element is in, or of a sign-in provider page: evidence for the kernel (it can only add risk). Cleaned."""
        if el is not None and el.in_dialog:
            # the dialog's MESSAGE (headings, paragraphs, text): not the names of its other buttons, which would make a "Cancel" next to a "Publish" look risky
            parts = [e.name or e.text for e in pg.els if e.in_dialog and e.role in ("dialog", "alertdialog", "heading", "paragraph", "text", "alert", "status", "generic")
                     and (e.name or e.text)]
            if not parts:
                parts = [e.name or e.text for e in pg.els if e.in_dialog and e.role == "dialog" and (e.name or e.text)]
        elif pg.host in SSO_HOSTS:
            parts = pg.lines[:30]
        else:
            return ""
        return re.sub(r"\s+", " ", " ".join(parts))[:560]

    # ------------------------------------------------------------------------------------------------------------ the unit: one step
    def run_steps(self, steps: list, params: dict = None) -> list:
        out = []
        for st in steps:
            r = self.do(st, params)
            out.append(r)
        return out

    def _holds(self, exp: dict, pg: Page) -> bool:
        return not self._unmet(exp, None, pg, Res(ok=True), None)

    def _unmet(self, exp: dict, before: "Page | None", after: Page, res: Res, act: "Act | None") -> list:
        """Which parts of an expectation are NOT true on the page now. Empty = verified. Each key is a fact about the page, a tab, or a file."""
        bad = []
        for k, v in exp.items():
            if k == "url_contains" and str(v).lower() not in after.url.lower():
                bad.append(f"the address does not contain '{v}'")
            elif k == "host_is" and after.host != str(v).lower():
                bad.append(f"the page is on {after.host or 'nothing'}, not {v}")
            elif k == "url_changed" and v and before is not None and after.url == before.url:
                bad.append("the address did not change")
            elif k == "changed" and v and before is not None and after.sig() == before.sig() and not res.data.get("new_tab"):
                bad.append("nothing on the page changed")
            elif k == "text" and not after.has_text(str(v)):
                bad.append(f"'{str(v)[:40]}' did not appear")
            elif k == "text_any" and not any(after.has_text(str(t)) for t in v):
                bad.append("none of the expected texts appeared")
            elif k == "text_gone" and after.has_text(str(v)):
                bad.append(f"'{str(v)[:40]}' is still there")
            elif k == "gone_all" and any(after.has_text(str(t)) for t in v):
                bad.append("something that should have gone is still there")
            elif k == "element":
                if not self._element_ok(v, after):
                    bad.append("the element is not in the expected state")
            elif k == "elements_gone":
                if S.find(after, Locator.from_dict(v), 0.7)[0] is not None:
                    bad.append("an element that should have gone is still there")
            elif k == "new_tab" and v and not (res.data.get("new_tab") or len(after.tabs) > (len(before.tabs) if before else 1)):
                bad.append("no new tab opened")
            elif k == "count_at_least":
                role, n = v.get("role", "heading"), int(v.get("n", 1))
                if sum(1 for e in after.els if e.role == role) < n:
                    bad.append(f"fewer than {n} {role} elements")
            elif k == "download":
                if not res.data.get("name") or (v.get("ext") and res.data.get("ext") not in str(v["ext"]).split(",")):
                    bad.append("no file of the expected type arrived")
            elif k == "any":
                if all(self._unmet(sub, before, after, res, act) for sub in v):
                    bad.append("none of the alternative effects happened")
            elif k == "noop_ok":
                continue                         # the effect of this step is checked by the NEXT step (a download starts; the file arrives and is read there)
            elif k == "typed":
                n = res.data.get("typed_chars")
                if n is not None and n != int(v):
                    bad.append(f"{n} of {v} characters were typed")
        return bad

    def _element_ok(self, spec: dict, pg: Page) -> bool:
        loc = Locator.from_dict(spec)
        el, score, _ = S.find(pg, loc, 0.6)
        if spec.get("present") is False:
            return el is None
        if el is None:
            return False
        if "checked" in spec and bool(spec["checked"]) != el.checked:
            return False
        if "disabled" in spec and bool(spec["disabled"]) != el.disabled:
            return False
        if "value" in spec and str(spec["value"]).strip().lower() != (el.text or "").strip().lower():
            return False
        if "value_contains" in spec and str(spec["value_contains"]).lower() not in (el.text or "").lower():
            return False
        if "expanded" in spec and bool(spec["expanded"]) != ("expanded" in el.attrs and el.attrs.get("expanded") != "false"):
            return False
        return True

    def default_expect(self, st: Step, value: str, el) -> dict:
        if st.expect:
            return st.expect
        if st.do in ("click", "check", "hover") and st.do != "hover":
            return {"changed": True}
        if st.do == "fill" and not value.startswith("@"):
            return {"element": {"role": el.role if el else "textbox", "name": (el.name if el else st.loc.name) or st.loc.label, "value": value}} if value and len(value) < 120 else {}
        if st.do == "select":
            return {"element": {"role": el.role if el else "combobox", "name": (el.name if el else st.loc.name), "value_contains": value.split("|")[0]}} if el else {}
        if st.do == "goto":
            return {"host_is": (urllib.parse.urlsplit(value).hostname or "").lower()} if value else {}
        return {}

    def do(self, st: Step, params: dict = None) -> StepResult:
        """One step with its whole recovery ladder. Returns a StepResult (ok False only for an optional step that could not be done); raises StepFailed when a
        required step cannot be done by any rung (the caller then tries the next site, and only then asks the owner).

        A state-changing action is never repeated blindly: it is repeated only when it evidently did nothing (it was not executed, or the page is exactly as it
        was). A click that led somewhere else is undone with Back first; a click after which the page is changing but the expected result has not shown is NOT
        clicked again (it may be working)."""
        params = {**self.params, **(params or {})}
        value = fill(st.value, params)
        to = fill(st.to, params)
        loc_text = " ".join(x for x in (st.loc.name, st.loc.text, st.loc.label, st.loc.near) if x)
        if "{" in loc_text:
            st = dataclasses.replace(st, loc=fill_loc(st.loc, params))
        if st.optional and ((st.value and not value.strip()) or ("{" in loc_text and not (st.loc.name or st.loc.text or st.loc.label or st.loc.near))):
            return StepResult(st.id, True, "skipped", ["no value was given for it"], [], skipped=True)       # e.g. a theme the owner did not name: nothing to pick
        rungs: list = []
        if st.do == "goto" and st.new_tab:           # the first page of a task: the tab Chrome shares may be one of the owner's own pages, so it is not even looked at
            pg = None
        else:
            pg = self.observe()
            pg = self._prepare(pg, st)
        if st.skip_if and pg is not None and self._holds(st.skip_if, pg):
            return StepResult(st.id, True, "skipped", ["already so"], rungs, skipped=True)
        if st.do == "expect":
            return self._expect_only(st, pg or self.observe())
        if st.do == "wait":
            return self.wait_for(st, params)
        start_url = pg.url if pg is not None else ""
        memo = {"attempts": 0, "why": "not tried", "first": "", "wrong": 0, "error_page": False}

        def attempt(how: str, loc: Locator) -> "StepResult | None":
            memo["attempts"] += 1
            if memo["attempts"] > self.budget.max_attempts_per_step:
                raise StepFailed(st.id, memo["first"] or "too many attempts", rungs, self.state())
            rungs.append(how)
            t = self._try(st, loc, value, to, how)
            if t.result is not None:
                t.result.rungs = list(rungs)
                return t.result
            memo["why"] = t.why
            memo["error_page"] = memo["error_page"] or t.state == "error_page"
            if t.state != "not_executed" and not memo["first"]:
                memo["first"] = t.why                  # the first thing that was really seen is the best reason to give
            if t.state == "refused":
                raise StepFailed(st.id, t.why, rungs, self.state())
            if t.state == "progress":
                raise StepFailed(st.id, t.why + " (the page was still changing, so the step was not repeated)", rungs, self.state())
            if t.state == "wrong_effect":
                memo["wrong"] += 1
                if memo["wrong"] >= 2 or not self._come_back(start_url, rungs):
                    raise StepFailed(st.id, t.why, rungs, self.state())
            return None

        why = lambda: memo["first"] or memo["why"]       # noqa: E731
        try:
            # 1. the step as written, 2. once more on a fresh look (the page may have moved on under us)
            for how in ("primary", "retry"):
                r = attempt(how, st.loc)
                if r:
                    return r
            if st.do not in ("goto", "press", "back", "reload", "scroll", "tab_select", "tab_close", "download", "cred_new", "dialog"):
                # 3. the same element by other means: name in any role, text, label, nearby text, XPath hint
                for how, loc in self._alternates(st, self.page):
                    r = attempt("alt:" + how, loc)
                    if r:
                        r.repair = {"step": st.id, "from": st.loc.to_dict(), "to": loc.to_dict(), "how": how}
                        return r
                # 3b. coordinates from the local vision model (an icon button, a canvas, a custom widget)
                r = self._vision_try(st, value, rungs)
                if r:
                    r.repair = {"step": st.id, "from": st.loc.to_dict(), "to": {"vision": st.loc.words()}, "how": "vision"}
                    return r
                # 4. scroll it into view / down the page (infinite scroll and lazy loading load more as we go)
                r = self._scroll_find(st, attempt, rungs)
                if r:
                    return r
            # 5. close what covers the page
            pg = self.observe()
            pg2 = self.dismiss_banners(pg, force=True)
            closed = pg2.sig() != pg.sig()
            if not closed and S.has_overlay(pg):
                try:
                    self.act(Act("press", value="Escape"))
                    closed = self.observe().sig() != pg.sig()
                except NeedApproval:
                    pass
            if closed:
                rungs.append("overlay")
                r = attempt("after_overlay", st.loc)
                if r:
                    return r
            # 6. reload (only where that cannot lose work), 7. go back when the page is somewhere the task did not mean to be
            pg = self.observe()
            if st.safe_reload or memo["error_page"] or S.ERROR_PAGE.search(pg.title):
                self.act(Act("reload"))
                rungs.append("reload")
                r = attempt("after_reload", st.loc)
                if r:
                    return r
            pg = self.observe()
            if start_url and pg.url != start_url and self._come_back(start_url, rungs):
                r = attempt("after_back", st.loc)
                if r:
                    return r
            # 8. re-plan the sub-goal with the decider (it picks ONE control of this page by number; text and addresses stay the step's own)
            r = self._replan(st, value, to, attempt, rungs)
            if r:
                return r
        except StepFailed as e:
            if st.optional:
                return StepResult(st.id, False, "gave_up", [], rungs, e.why)
            raise
        if st.optional:
            return StepResult(st.id, False, "gave_up", [], rungs, why())
        raise StepFailed(st.id, why(), rungs, self.state())

    def _come_back(self, start_url: str, rungs: list) -> bool:
        """Back to the page a step started on, after a click led somewhere else."""
        try:
            self.act(Act("back"))
        except NeedApproval:
            return False
        rungs.append("back")
        pg = self.observe()
        return not start_url or pg.url == start_url

    SSO_CLICK = re.compile(r"(?i)\b(?:google|microsoft|apple|sso|continue with|sign in with|log in with)\b")

    def _pw_ok(self, st: Step) -> bool:
        """A password field on the page is not the owner's wall when this step does not type into it: a sign-up form (password_ok) or the click on a
        'Continue with Google' button of a page that also shows an e-mail and password form."""
        if self.expect_password or st.password_ok:
            return True
        return st.do == "click" and bool(self.SSO_CLICK.search(" ".join([st.loc.name, *st.loc.any_of])))

    def _prepare(self, pg: Page, st: Step) -> Page:
        """Before any step: a wall is the owner's, a cookie banner or interstitial is dismissed, a pop-up or off-list address is dealt with."""
        w = owner_wall(pg, self._pw_ok(st))
        if w is not None:
            self.hand_to_owner(w, pg)
            pg = self.page
        pg = self._tabs_hygiene(pg)
        self._check_host(pg)
        if st.do in ("click", "fill", "select", "check", "hover", "upload", "drag") and (st.loc.name or st.loc.text or st.loc.label or st.loc.any_of):
            el, _s, _h = S.find(pg, st.loc, 0.7)
            if el is not None and el.in_dialog:
                return pg                          # the step's own control is IN the dialog that is open (an export dialog): closing it would undo the last step
        pg = self.dismiss_banners(pg)
        return pg

    def _alternates(self, st: Step, pg: Page) -> list:
        """The same element by other means, from what the step knows about it: its name, other names, its text, its label, the text near it, an XPath hint, then the vision."""
        loc, out = st.loc, []
        seen = {loc.to_dict().__repr__()}

        def add(how, l):
            k = l.to_dict().__repr__()
            if k not in seen and (l.name or l.text or l.label or l.near or l.xpath or l.any_of):
                seen.add(k)
                out.append((how, l))
        words = loc.name or (loc.any_of[0] if loc.any_of else "") or loc.text or loc.label
        add("name_any_role", Locator(name=loc.name, any_of=loc.any_of, frame=loc.frame))
        if loc.text:
            add("text", Locator(text=loc.text, frame=loc.frame))
            add("text_role", Locator(role=loc.role, text=loc.text, frame=loc.frame))
        if words:
            add("text", Locator(text=words, frame=loc.frame))
            add("words", Locator(role=loc.role, name=" ".join(words.split()[:2]), frame=loc.frame))
        if loc.label:
            add("label", Locator(role="textbox", label=loc.label))
        if loc.near:
            add("near", Locator(role=loc.role, near=loc.near, nth=loc.nth))
        if loc.xpath:
            add("xpath", Locator(role=loc.role, name=loc.name, xpath=loc.xpath))
        return out

    def _scroll_find(self, st, attempt, rungs):
        """Infinite scroll and lazy loading: go down the page a few times, looking after each; stop when the page stops growing."""
        last = None
        for i in range(4):
            try:
                self.act(Act("scroll", value="down"))
            except NeedApproval:
                break
            pg = self.observe()
            rungs.append(f"scroll{i + 1}")
            if self._find_el(st, st.loc, pg) is not None:
                r = attempt("after_scroll", st.loc)
                if r:
                    return r
            if pg.sig() == last:
                break
            last = pg.sig()
        return None

    def _vision_try(self, st: Step, value: str, rungs: list):
        """The element has no usable name (an icon button, a canvas): the LOCAL vision model looks at a picture of the tab and gives a point; the point is a guess
        (INFERRED), the node refuses a click where the page has a control with a different name, and the effect is verified like any other."""
        if st.do not in ("click", "check", "hover") or not st.loc.words():
            return None
        try:
            res = self.act(Act("locate", label=st.loc.words()))
        except NeedApproval:
            return None
        pt = (res.data or {}).get("point") if res.ok else None
        if not pt:
            return None
        rungs.append("vision")
        pg = self.observe()
        a = Act("xy_click", label=st.loc.words(), x=int(pt["x"]), y=int(pt["y"]), host=pg.host)
        res = self.act(a)
        if not res.ok:
            return None
        after = self._patient_observe(st, pg, res, a, value, None)
        if self._unmet(self.default_expect(st, value, None), pg, after, res, a):
            return None
        return StepResult(st.id, True, "vision", [f"clicked '{st.loc.words()[:40]}' where the local vision model saw it (inferred), and the effect was verified"], list(rungs))

    def _find_el(self, st: Step, loc: Locator, pg: Page):
        el, score, how = S.find(pg, loc, 0.6)
        return el

    def _replan(self, st: Step, value: str, to: str, attempt, rungs):
        """The step's own locator found nothing and no other means did: the sub-goal is handed to the planner (the local decision engine), which may only name
        ONE control of the page that is on the list we show it. The text, the address and the key stay the step's own; the answer is checked here again."""
        if self.planner is None or self.replans >= self.budget.max_replans:
            return None
        for _ in range(2):
            self.replans += 1
            pg = self.observe()
            opts = [(i, role, n) for i, role, n in S.safe_names(pg, 40) if not NEVER_PROPOSE.search(n)]
            if not opts:
                return None
            sub = st.intent or st.note or f"{st.do} {st.loc.words()}"
            try:
                prop = self.planner.propose(self.goal, sub, pg, opts, st)
            except Exception:  # noqa: BLE001 - a planner that is down is no planner
                return None
            if not isinstance(prop, dict) or prop.get("do") not in ("click", "fill", "select", "check"):
                return None
            idx = prop.get("idx")
            if not isinstance(idx, int) or idx not in {o[0] for o in opts}:
                self._log("planner_refused", wall="not on the list")
                return None
            el = pg.els[idx]
            if st.do != prop["do"] and not (st.do == "click" and prop["do"] in ("click", "check")):
                return None                                    # the planner may choose the control, not change what is done to it
            rungs.append("replan")
            loc = Locator(role=el.role, name=el.name or el.child_text)
            r = attempt("replan:" + re.sub(r"[^a-z ]", "", str(prop.get("why", "")).lower())[:30], loc)
            if r:
                r.repair = {"step": st.id, "from": st.loc.to_dict(), "to": loc.to_dict(), "how": "replan"}
                return r
        return None

    # ------------------------------------------------------------------------------------------------------------ one attempt
    def _to_act(self, st: Step, loc: Locator, value: str, to: str, pg: Page):
        kind = st.do
        if kind == "goto":
            return Act("goto", url=to or value, value="new_tab" if st.new_tab else "", host=""), None
        if kind in ("press",):
            return Act("press", value=value or "Enter", host=pg.host), None
        if kind in ("back", "reload"):
            return Act(kind, host=pg.host), None
        if kind == "scroll":
            if value == "into_view":
                el, _s, _h = S.find(pg, loc, 0.5)
                if el is None:
                    return None, "not_found"
                return Act("scroll", label=el.name or el.child_text or el.role, ref=el.ref, value="into_view", host=pg.host), el
            return Act("scroll", value=value or "down", host=pg.host), None
        if kind in ("tab_select", "tab_close"):
            return Act(kind, value=value, host=""), None
        if kind == "download":
            return Act("download", ext=value or st.expect.get("download", {}).get("ext", "pptx,pdf"), host=pg.host), None
        if kind == "cred_new":
            return Act("cred_new", host=pg.host), None
        el, score, how = S.find(pg, loc, 0.5)
        if el is None and loc.xpath:
            return Act(JOB_KIND[kind], label=loc.name or loc.words(), role=loc.role, sel=loc.xpath, value=value, host=pg.host), None
        if el is None:
            return None, "not_found"
        if kind == "fill" and el.role not in S.FIELDS and el.role not in ("generic", "paragraph", "text"):
            return None, f"not_a_field:{el.role}"
        if el.disabled and kind in ("click", "check", "fill", "select"):
            return None, "disabled"
        label = el.name or el.child_text or el.role
        a = Act(JOB_KIND[kind], label=label, role=el.role, ref=el.ref, value=value, host=pg.host, ctx=self.ctx_for(el, pg),
                via=(self.signin_site if pg.host in SSO_HOSTS else ""))
        if kind == "drag":
            a.value = fill(st.to or value, self.params)
        return a, el

    def _try(self, st: Step, loc: Locator, value: str, to: str, how: str) -> "Try":
        """Look, act, check the effect. A Try says what happened: ok / not_executed (nothing was done) / no_effect (done, the page is as it was) / progress
        (done, the page is changing, the expected result has not shown) / wrong_effect (done, and the address changed to somewhere else)."""
        first_page = st.do == "goto" and st.new_tab
        pg = None if first_page else self.observe()
        w = owner_wall(pg, self._pw_ok(st)) if pg is not None else None
        if w is not None:
            self.hand_to_owner(w, pg)
            pg = self.page
        a, el = self._to_act(st, loc, value, to, pg)
        if a is None:
            return Try(None, str(el or "not_found"), "not_executed")
        if a.kind == "goto":
            h = (urllib.parse.urlsplit(a.url).hostname or "").lower()
            if h and h not in self.allowed and h not in self.owner_allowed:
                btn = self.ask_owner("This address is not on the task's list", f"The step wants to open {h}. Allow it for this task?",
                                     [("Address", h)], [("allow", "Allow this task", "secondary"), ("stay", "No", "primary")])
                if btn != "allow":
                    raise NeedOwner("off_list", f"navigation to {h} is not on this task's list", self.state())
                self.owner_allowed.add(h)
        before = pg
        res = self.act(a)
        if not res.ok:
            if a.kind == "download" and (res.data or {}).get("refused"):
                return Try(None, "the file was not taken: " + str(res.error)[:120], "refused")           # a refused file is not fetched again
            return Try(None, res.kind or res.error[:80] or "failed", "not_executed")
        after = self._patient_observe(st, before, res, a, value, el)
        bad = self._unmet(self.default_expect(st, value, el), before, after, res, a)
        if bad:
            self._log("verify", act=a.kind, ok=False)
            why = "; ".join(bad)[:160]
            if S.ERROR_PAGE.search(after.title + "\n" + "\n".join(after.lines[:8])) and len(after.els) < 12:
                return Try(None, "the page shows an error (" + why + ")", "error_page")      # nothing to undo and nothing to lose: a reload is the answer
            if before is None or after.url != before.url:
                return Try(None, why, "wrong_effect" if before is not None else "no_effect")
            return Try(None, why, "progress" if after.sig() != before.sig() else "no_effect")
        facts = self._facts(st, a, after, res, value)
        self._log("verify", act=a.kind, ok=True)
        self._after_effects(after)
        return Try(StepResult(st.id, True, how, facts, [how], data=dict(res.data)), "", "ok")

    def _after_effects(self, pg: Page) -> Page:
        try:
            pg = self._tabs_hygiene(pg)
            self._check_host(pg)
            return pg
        except NeedApproval:
            return pg

    def _patient_observe(self, st: Step, before: Page, res: Res, a: Act, value: str, el) -> Page:
        """The effect of an action may take a moment to show. Look, and look again until it shows or the patience runs out: a poll, not a fixed sleep."""
        end = self.clock() + self.budget.patience_s
        exp = self.default_expect(st, value, el)
        pg = self.observe()
        while self.clock() < end and self._unmet(exp, before, pg, res, a):
            self._tick()
            self.sleep(self.budget.poll_s)
            pg = self.observe()
        return pg

    def _facts(self, st: Step, a: Act, after: Page, res: Res, value: str) -> list:
        facts = []
        if st.do in ("click", "check"):
            facts.append(f"clicked '{a.label[:40]}'" if a.label else "clicked")
        elif st.do == "fill":
            facts.append(f"typed {res.data.get('typed_chars', len(value))} characters into '{a.label[:30]}'")
        elif st.do == "goto":
            facts.append(f"opened {after.host}")
        elif st.do == "download":
            facts.append(f"a {res.data.get('ext')} file of {res.data.get('size')} bytes arrived in quarantine")
        else:
            facts.append(st.do)
        if st.expect:
            facts.append("checked: " + ", ".join(st.expect)[:80])
        return facts

    def _expect_only(self, st: Step, pg: Page) -> StepResult:
        bad = self._unmet(st.expect, None, pg, Res(ok=True), None)
        if bad:
            if st.optional:
                return StepResult(st.id, False, "gave_up", [], [], "; ".join(bad))
            raise StepFailed(st.id, "; ".join(bad), [], self.state())
        return StepResult(st.id, True, "primary", ["checked: " + ", ".join(st.expect)[:80]])

    # ------------------------------------------------------------------------------------------------------------ waiting by condition
    def wait_for(self, st: Step, params: dict = None) -> StepResult:
        """Wait until a CONDITION holds (st.until), polling the live page, while watching for what ends the wait badly (st.until['fail_if']), a wall, a free-limit
        message. Never a fixed sleep. Raises StepFailed on timeout."""
        cond = dict(st.until or st.expect)
        fail_if = cond.pop("fail_if", None)
        limit = st.timeout_s or 120.0
        end = self.clock() + limit
        last_sig, still = None, 0
        while True:
            pg = self.observe()
            w = owner_wall(pg, self.expect_password)
            if w is not None:
                self.hand_to_owner(w, pg)
                pg = self.page
            if S.paywall(pg) and not cond.get("allow_paywall"):
                raise StepFailed(st.id, "the site says the free limit is reached", ["paywall"], self.state())
            if fail_if and any(pg.has_text(t) for t in fail_if):
                raise StepFailed(st.id, "the site reported a failure", ["fail_if"], self.state())
            pg = self.dismiss_banners(pg)
            if not self._unmet({k: v for k, v in cond.items() if k != "allow_paywall"}, None, pg, Res(ok=True), None):
                return StepResult(st.id, True, "primary", ["the page reached: " + ", ".join(k for k in cond)[:80]])
            if self.clock() >= end:
                raise StepFailed(st.id, "the condition did not hold in time", ["timeout"], self.state())
            sig = pg.sig()
            still = still + 1 if sig == last_sig else 0
            last_sig = sig
            if still >= 12 and not cond.get("patient"):
                raise StepFailed(st.id, "the page stopped changing and the condition does not hold", ["stuck"], self.state())
            kind = "text:" + str(cond["text"])[:150] if cond.get("text") else "idle"
            res = self.act(Act("wait", value=kind))        # the node polls the page for up to 25 s for this one condition (or until it stops changing)
            if not res.ok and res.kind in ("lost",):
                raise BackendLost(res.error)
            self.sleep(self.budget.poll_s)
