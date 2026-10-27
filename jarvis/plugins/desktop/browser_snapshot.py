"""Reading a page the way the browser agent needs (D64, 2026-10-26): the Playwright MCP accessibility snapshot -> elements, visible text, overlays,
walls (CAPTCHA, sign-in, 2FA, payment) and semantic locators. Pure Python, no I/O. The page text it works on is read from a local file on this
laptop and is DATA: nothing here ever treats a word on a page as an instruction.

Snapshot format (`@playwright/mcp` 0.0.83, read from its package on 2026-10-26):
    ### Page
    - Page URL: https://example.com/
    - Page Title: Example
    ### Snapshot
    ```yaml
    - generic [ref=e1]:
      - heading "Example" [level=1] [ref=e2]
      - button "Generate" [ref=e3] [cursor=pointer] [box=10,20,80,32]
      - textbox "Topic" [ref=e4]: current value
      - link "More" [ref=e5]:
        - /url: https://example.com/more
      - checkbox "Remember me" [checked] [ref=e6]
      - iframe [ref=e7]:
        - button "Play" [ref=f7e1]
Elements inside frames carry refs such as f7e1; shadow DOM is pierced by the snapshot itself.
"""
from __future__ import annotations

import re
import urllib.parse
from dataclasses import dataclass, field

CLICKABLE = {"button", "link", "checkbox", "radio", "menuitem", "menuitemcheckbox", "menuitemradio", "tab", "option", "switch", "treeitem", "combobox"}
FIELDS = {"textbox", "searchbox", "combobox", "spinbutton", "slider"}
INTERACTIVE = CLICKABLE | FIELDS | {"listbox"}
LINE = re.compile(r"^(?P<indent>\s*)-\s+(?P<body>.*)$")
ATTR = re.compile(r"\[([a-z-]+)(?:=([^\]]*))?\]")
HEAD = re.compile(r"""^'?(?P<role>[A-Za-z][\w-]*)(?:\s+"(?P<name>(?:[^"\\]|\\.)*)")?(?P<rest>.*)$""")

# a wall: something only the owner may pass. Never clicked, never typed into, never "solved".
CAPTCHA = re.compile(r"(?i)\b(?:re|h)?captcha\b|not a robot|verify (?:that )?you(?:'re| are) (?:a )?human|are you (?:a )?(?:robot|human)|security check|"
                     r"human verification|cloudflare|checking (?:if|that) you are human|just a moment|press (?:and hold|&amp; hold)|unusual traffic|"
                     r"turnstile|bot (?:detection|check)|prove you(?:'re| are) (?:not a )?(?:robot|human)")
CAPTCHA_HOSTS = ("google.com/recaptcha", "recaptcha.net", "hcaptcha.com", "challenges.cloudflare.com", "arkoselabs.com", "funcaptcha.com")
TWO_FACTOR = re.compile(r"(?i)\b(two[- ]?(?:step|factor)|2[- ]?step|2fa|authenticator app|enter (?:the |your )?(?:6[- ]digit |verification |security |one[- ]time |sms )?code|"
                        r"verification code|security code|one[- ]time (?:code|password)|otp|we (?:sent|texted|emailed) (?:you )?a code|check your (?:phone|e-?mail)|"
                        r"confirm (?:it'?s|that it is) you|passkey|use your security key)\b")
PAYMENT = re.compile(r"(?i)\b(card number|credit card|debit card|cvv|cvc|security code|expir(?:y|ation) date|billing (?:address|details|information)|payment method|"
                     r"add (?:a )?payment|enter payment|complete (?:your )?(?:purchase|order)|place (?:your )?order|pay now|checkout|start (?:your )?(?:free )?trial)\b")
PAYWALL = re.compile(r"(?i)\b(upgrade (?:to|your plan)|you(?:'ve| have) (?:run out|used (?:all|up))|out of (?:free )?credits|no (?:more )?credits|credit limit|"
                     r"limit reached|reached (?:your|the) (?:free )?limit|free (?:plan )?limit|subscribe to continue|unlock (?:this|more)|go pro|get pro|"
                     r"daily limit|monthly limit|upgrade to export|upgrade to download)\b")
SIGNIN_PAGE = re.compile(r"(?i)^(?:sign|log) ?in\b|^(?:sign|log) ?in to\b|^welcome back\b|^continue with\b")
ERROR_PAGE = re.compile(r"(?i)\b(this page isn'?t working|something went wrong|page not found|404|500 internal|502 bad gateway|503 service|try again later|"
                        r"err_[a-z_]+|can'?t reach this page|site can'?t be reached|temporarily unavailable)\b")
EMAIL_CODE = re.compile(r"(?i)\b(verify your e-?mail|check your inbox|we sent (?:you )?(?:an )?e-?mail|confirmation e-?mail|enter the code (?:we|sent)|e-?mail verification|"
                        r"verification link|check your e-?mail|confirm your e-?mail|confirmation link|activation link|verify your (?:account|e-?mail address)|"
                        r"we(?:'ve| have)? sent (?:you )?a (?:confirmation |verification )?(?:link|message))\b")
# page text that talks TO the agent: noted (and counted), never obeyed, never given to a planner
INJECTION = re.compile(r"(?i)\b(ignore (?:all |any )?(?:previous|prior|above) (?:instructions|rules)|you are (?:now )?(?:allowed|authori[sz]ed|permitted)|"
                       r"system (?:prompt|message|override)|new instructions?|assistant,|as an ai|you must (?:now )?(?:go|navigate|open|enter|type|click|delete|send)|"
                       r"enter (?:your|the) password|navigate to http|go to https?://|open https?://|do not tell the user|the owner (?:already )?(?:approved|allowed)|"
                       r"pre-?approved|disregard)\b")
OVERLAY_ROLES = {"dialog", "alertdialog"}
DISMISS_NAME = re.compile(r"(?i)^(?:close|dismiss|x|×|✕|✖|not now|maybe later|no thanks?|no,? thanks?|skip(?: for now| this| tour)?|later|remind me later|cancel|got it|"
                          r"ok(?:ay)?|continue without|i'?ll do it later|done|finish|reject(?: all)?|decline|deny|necessary only|only necessary|essential only|"
                          r"use necessary cookies only|continue with (?:necessary|essential) cookies|not interested|no,? i'?m good)$")
CONTINUE_NAME = re.compile(r"(?i)^(?:continue|next|skip|not now|maybe later|no thanks?|got it|ok(?:ay)?|let'?s go|start|get started|done|close|dismiss)$")


@dataclass
class El:
    i: int                      # order in the snapshot
    ref: str
    role: str
    name: str                   # the accessible name ("" when none)
    depth: int
    attrs: dict = field(default_factory=dict)
    text: str = ""              # inline text after the colon (a textbox's value, a paragraph's text)
    url: str = ""               # a link's address (the /url child)
    parent: int = -1
    in_dialog: bool = False
    frame: str = ""             # "f7" for an element inside iframe e7
    label: str = ""             # the nearest preceding text/heading/label before it (for "near" locators)
    child_text: str = ""        # the first text inside it (a clickable <div> has no name, its text is below)

    @property
    def shown(self) -> str:
        return self.name or self.child_text or self.text

    @property
    def box(self):
        b = self.attrs.get("box")
        try:
            x, y, w, h = (float(v) for v in str(b).split(","))
            return x, y, w, h
        except (TypeError, ValueError):
            return None

    def state(self, key: str):
        return self.attrs.get(key)

    @property
    def disabled(self) -> bool:
        return "disabled" in self.attrs

    @property
    def checked(self) -> bool:
        return "checked" in self.attrs and self.attrs.get("checked") != "false"


@dataclass
class Page:
    url: str = ""
    title: str = ""
    host: str = ""
    https: bool = False
    els: list = field(default_factory=list)
    lines: list = field(default_factory=list)          # visible text lines (headings, paragraphs, texts, names of controls)
    tabs: list = field(default_factory=list)           # [{"index", "current", "title", "url"}]
    modal: str = ""                                    # a JavaScript dialog or file chooser the server reports
    raw: str = ""

    @property
    def text(self) -> str:
        return "\n".join(self.lines)

    def has_text(self, needle: str) -> bool:
        n = _fold(needle)
        return bool(n) and (n in _fold(self.text) or any(n in _fold(e.shown) for e in self.els))

    @property
    def path(self) -> str:
        return urllib.parse.urlsplit(self.url).path or "/"

    def sig(self) -> str:
        """A short fingerprint of what the page shows: used to see that an action changed something."""
        import hashlib
        body = self.url + "|" + "|".join(f"{e.role}:{e.name}:{e.text[:40]}:{sorted(e.attrs)}" for e in self.els if e.role != "generic" or e.name) + "|" + self.text[:4000]
        return hashlib.sha1(body.encode("utf-8", "replace")).hexdigest()[:16]


def _fold(s) -> str:
    return " ".join(str(s or "").lower().replace("’", "'").split())


def _unq(s: str) -> str:
    return re.sub(r"\\(.)", r"\1", s or "")


def parse(doc, snapshot: str = None) -> Page:
    """A Page from the node's local result document ({url, title, host, https, snapshot, tabs}) or from raw snapshot text."""
    if isinstance(doc, dict):
        raw = str(doc.get("snapshot") if snapshot is None else snapshot or "")
        pg = Page(url=str(doc.get("url", "")), title=str(doc.get("title", "")), host=str(doc.get("host", "")), https=bool(doc.get("https")),
                  tabs=[t for t in (doc.get("tabs") or []) if isinstance(t, dict)], raw=raw)
    else:
        raw = str(doc if snapshot is None else snapshot)
        pg = Page(raw=raw)
    m = re.search(r"Page URL:\s*(\S+)", raw)
    if m and not pg.url:
        pg.url = m.group(1)
    m = re.search(r"Page Title:\s*(.+)", raw)
    if m and not pg.title:
        pg.title = m.group(1).strip()
    u = urllib.parse.urlsplit(pg.url)
    pg.host = pg.host or (u.hostname or "").lower().rstrip(".")
    pg.https = pg.https or u.scheme == "https"
    ms = re.search(r"### Modal state\n((?:- .*\n?)+)", raw)
    if ms:
        pg.modal = " ".join(ms.group(1).split())[:200]
    body = raw.split("### Snapshot", 1)[-1] if "### Snapshot" in raw else raw
    stack: list = []              # (depth, index) of open ancestors
    last_text = ""
    for line in body.splitlines():
        m = LINE.match(line)
        if not m:
            continue
        depth = len(m.group("indent")) // 2
        item = m.group("body").rstrip()
        if item.startswith("/url:"):
            if pg.els:
                for e in reversed(pg.els):
                    if e.role == "link" and not e.url and e.depth < depth:
                        e.url = item[5:].strip().strip("'\"")
                        break
            continue
        if item.startswith("/"):
            continue
        if item.startswith("text:") or item.startswith("'text:"):
            t = item.split(":", 1)[1].strip().strip("'")
            if t:
                pg.lines.append(t)
                last_text = t
                if stack:
                    parent = pg.els[stack[-1][1]] if stack[-1][1] < len(pg.els) else None
                    if parent is not None and not parent.child_text:
                        parent.child_text = t[:100]
            continue
        h = HEAD.match(item)
        if not h:
            continue
        role = h.group("role").lower()
        name = _unq(h.group("name") or "")
        rest = h.group("rest") or ""
        attrs = {}
        for am in ATTR.finditer(rest):
            attrs[am.group(1)] = am.group(2) if am.group(2) is not None else True
        inline = ""
        if ":" in rest:
            tail = rest.split("]")[-1] if "]" in rest else rest
            if ":" in tail:
                inline = tail.split(":", 1)[1].strip().strip("'")
        while stack and stack[-1][0] >= depth:
            stack.pop()
        parent = stack[-1][1] if stack else -1
        ref = str(attrs.get("ref", "")) if attrs.get("ref") not in (None, True) else ""
        in_dialog = role in OVERLAY_ROLES or (parent >= 0 and pg.els[parent].in_dialog)
        fm = re.match(r"^(f\d+)e\d+$", ref)
        el = El(i=len(pg.els), ref=ref, role=role, name=name, depth=depth, attrs=attrs, text=inline, parent=parent, in_dialog=in_dialog,
                frame=fm.group(1) if fm else "", label=last_text[:100])
        if "url" in attrs and attrs["url"] is not True:
            el.url = str(attrs["url"])
        pg.els.append(el)
        if role == "option" and "selected" in attrs and parent >= 0 and pg.els[parent].role in ("combobox", "listbox") and not pg.els[parent].text:
            pg.els[parent].text = name                       # the value of a drop-down is its selected option
        stack.append((depth, el.i))
        if role in ("heading", "paragraph", "cell", "listitem", "text", "term", "definition", "caption", "blockquote", "status", "alert") and (name or inline):
            pg.lines.append(name or inline)
            last_text = name or inline
        elif role in INTERACTIVE and (name or inline):
            pg.lines.append(name or inline) if role in ("button", "link", "tab", "menuitem", "option") else None
            if role in FIELDS or role in ("checkbox", "radio", "switch"):
                last_text = name or last_text
        elif role in ("generic", "group", "region", "banner", "main", "navigation", "form", "dialog", "alertdialog", "article") and name:
            last_text = name
            pg.lines.append(name)
        if inline and name and inline != name and role not in ("textbox", "searchbox", "combobox", "spinbutton", "slider"):
            pg.lines.append(inline)
    return pg


# ------------------------------------------------------------------------------------------------------------- locators
@dataclass
class Locator:
    """A SEMANTIC way to find one element: by what it is and what it says, not by a brittle selector. Any subset of the fields."""
    role: str = ""
    name: str = ""             # accessible name; matched exactly first, then loosely
    text: str = ""             # visible text anywhere in the element
    label: str = ""            # the label or placeholder of a field
    near: str = ""             # a text that sits right before the element (a heading, a caption)
    xpath: str = ""            # a last-resort hint the node validates by name
    nth: int = 0               # which one of several identical matches (0 = the first)
    frame: str = ""
    any_of: tuple = ()         # other names that mean the same ("Generate", "Create", "Make it")

    @classmethod
    def from_dict(cls, d: dict) -> "Locator":
        d = d or {}
        return cls(role=str(d.get("role", ""))[:30].lower(), name=str(d.get("name", ""))[:120], text=str(d.get("text", ""))[:120], label=str(d.get("label", ""))[:120],
                   near=str(d.get("near", ""))[:120], xpath=str(d.get("xpath", ""))[:280], nth=int(d.get("nth", 0) or 0), frame=str(d.get("frame", ""))[:12],
                   any_of=tuple(str(x)[:120] for x in (d.get("any_of") or [])[:8]))

    def to_dict(self) -> dict:
        out = {k: v for k, v in (("role", self.role), ("name", self.name), ("text", self.text), ("label", self.label), ("near", self.near),
                                 ("xpath", self.xpath), ("nth", self.nth or None), ("frame", self.frame), ("any_of", list(self.any_of) or None)) if v}
        return out

    def words(self) -> str:
        return self.name or (self.any_of[0] if self.any_of else "") or self.text or self.label or self.near or self.role


ROLE_FAMILY = {"button": {"button", "link", "menuitem", "tab", "generic"}, "link": {"link", "button", "menuitem", "tab", "generic"},
               "textbox": {"textbox", "searchbox", "combobox"}, "searchbox": {"searchbox", "textbox"}, "checkbox": {"checkbox", "switch", "radio", "menuitemcheckbox"},
               "combobox": {"combobox", "listbox", "button", "textbox"}, "option": {"option", "menuitem", "radio", "generic", "button"},
               "tab": {"tab", "button", "link"}, "menuitem": {"menuitem", "button", "link", "option"}, "switch": {"switch", "checkbox"}, "radio": {"radio", "option"}}


def _tokens(s: str) -> set:
    return set(re.findall(r"[a-z0-9]+", _fold(s)))


def _name_score(el: El, want: str) -> float:
    """1.0 = same name; 0.9 = it starts with the wanted words or they start with its; 0.75 = contains; 0.5.. = shared words. 0 = unrelated."""
    w = _fold(want)
    if not w:
        return 0.0
    for cand in (el.name, el.child_text, el.text):
        c = _fold(cand)
        if not c:
            continue
        if c == w:
            return 1.0
        if len(w) >= 3 and (c.startswith(w) or (len(c) >= 3 and w.startswith(c))):
            return 0.9
        if (len(w) >= 3 or w == "@") and w in c:          # "@" = an account chooser entry (it shows an e-mail address)
            return 0.75
        if len(c) >= 4 and c in w:
            return 0.6
        a, b = _tokens(c), _tokens(w)
        if a and b and len(a & b) / len(b) >= 0.6 and len(a & b) >= 1:
            return 0.5 + 0.2 * len(a & b) / len(a | b)
    return 0.0


def candidates(pg: Page, loc: Locator, visible_only: bool = True, in_dialog: "bool | None" = None) -> list:
    """Elements that fit the locator, best first: [(score, El)]. Several equally good ones are returned all; the caller decides (nth, or ask)."""
    out = []
    fam = ROLE_FAMILY.get(loc.role, {loc.role}) if loc.role else None
    names = [n for n in ([loc.name] + list(loc.any_of)) if n]
    for e in pg.els:
        if loc.frame and e.frame != loc.frame:
            continue
        if in_dialog is not None and e.in_dialog != in_dialog:
            continue
        if fam and e.role not in fam:
            continue
        if not loc.role and e.role not in INTERACTIVE and not (e.attrs.get("cursor") == "pointer"):
            continue
        if not e.ref:
            continue
        score = 0.0
        role_bonus = 0.05 if (loc.role and e.role == loc.role) else 0.0
        # ONE strategy decides the score: the name (and its synonyms) when there is one, else the text, else the label, else the nearby text.
        # The others only break ties. That keeps "find it by its name" and "find it by its text" different rungs of the recovery ladder.
        if names:
            for n in names:
                score = max(score, _name_score(e, n))
        elif loc.text:
            score = _name_score(e, loc.text) * 0.95
        elif loc.label:
            score = _name_score(e, loc.label) * 0.9 if e.role in FIELDS else 0.0
        elif loc.near:
            score = 0.55 if _fold(loc.near) in _fold(e.label) and e.role in INTERACTIVE else 0.0
        if loc.near and score > 0 and (names or loc.text or loc.label):
            score = score + (0.04 if _fold(loc.near) in _fold(e.label) else -0.04)
        if score <= 0:
            continue
        if visible_only and e.attrs.get("hidden"):
            continue
        out.append((round(min(1.0, score + role_bonus), 3), e))
    out.sort(key=lambda t: (-t[0], t[1].i))
    return out


def find(pg: Page, loc: Locator, min_score: float = 0.5):
    """(El, score, how) for the best match, or (None, 0, reason). Two elements tied for best: the nth one (default the first) of the tie, so a
    page with two 'Next' buttons is still answered, but `ambiguous` says it."""
    cands = candidates(pg, loc)
    cands = [c for c in cands if c[0] >= min_score]
    if not cands:
        return None, 0.0, "no element fits"
    top = cands[0][0]
    tied = [e for s, e in cands if s >= top - 0.01]
    # an element inside an open dialog wins over the same name behind it
    if any(e.in_dialog for e in tied) and not all(e.in_dialog for e in tied):
        tied = [e for e in tied if e.in_dialog]
    idx = min(max(loc.nth, 0), len(tied) - 1)
    return tied[idx], top, ("ambiguous" if len(tied) > 1 else "ok")


# ------------------------------------------------------------------------------------------------------------- walls and overlays
@dataclass
class Wall:
    kind: str          # captcha | two_factor | email_code | payment | signin | paywall | error
    why: str


def walls(pg: Page, have_signin_ability: bool = False) -> "Wall | None":
    """What only the owner may pass on this page, or None. CAPTCHA, a bot check, a 2FA or e-mail code, a payment form are never bypassed or solved;
    a sign-in page is a wall unless the engine has a grant for it (it decides, via have_signin_ability)."""
    url = pg.url.lower()
    if any(h in url for h in CAPTCHA_HOSTS) or any(h in pg.raw for h in CAPTCHA_HOSTS):
        return Wall("captcha", "a CAPTCHA or bot check is on this page")
    shown = "\n".join([pg.title] + pg.lines + [e.name for e in pg.els if e.role in ("button", "checkbox", "heading", "iframe", "textbox")])
    if CAPTCHA.search(shown):
        return Wall("captcha", "the page asks to prove you are not a robot")
    if any(e.role == "textbox" and re.search(r"(?i)verification code|security code|one[- ]time|otp|6[- ]digit|authenticator", e.name + " " + e.text) for e in pg.els) \
            or (TWO_FACTOR.search(shown) and any(e.role == "textbox" for e in pg.els)):
        return Wall("email_code" if EMAIL_CODE.search(shown) else "two_factor", "the page asks for a verification code")
    if EMAIL_CODE.search(shown) and not any(e.role == "textbox" and re.search(r"(?i)search|topic|prompt|title", e.name) for e in pg.els):
        return Wall("email_code", "the site wants the e-mail address confirmed")
    if any(e.role in FIELDS and re.search(r"(?i)card number|cvv|cvc|expir", e.name) for e in pg.els) or (PAYMENT.search(shown) and any(e.role in FIELDS for e in pg.els)):
        return Wall("payment", "the page is a payment form")
    pw = any(e.role == "textbox" and re.search(r"(?i)pass(?:word|code)", e.name) for e in pg.els)
    if pw and not have_signin_ability:
        return Wall("signin", "the page asks for a password")
    if pw and any(h in pg.host for h in ("accounts.google.com", "login.microsoftonline.com", "login.live.com")):
        return Wall("signin", "the sign-in provider asks for a password (the browser is not signed in there)")
    if ERROR_PAGE.search(pg.title + "\n" + "\n".join(pg.lines[:12])) and len(pg.els) < 12:
        return Wall("error", "the page is an error page")
    return None


def paywall(pg: Page) -> bool:
    """Did the site stop at its free limit (credits used up, upgrade needed)? Looked for in the headings, dialogs and alerts only."""
    shown = "\n".join(e.name + " " + e.text for e in pg.els if e.role in ("heading", "alert", "alertdialog", "dialog", "status", "paragraph", "text") or e.in_dialog)
    return bool(PAYWALL.search(shown + "\n" + "\n".join(pg.lines[:40])))


def injection_count(pg: Page) -> int:
    """How many places in the page speak to the agent (counted for the journal; the words themselves are never kept or followed)."""
    return sum(1 for ln in pg.lines + [e.name for e in pg.els] if INJECTION.search(ln or ""))


def safe_names(pg: Page, limit: int = 40) -> list:
    """Names of controls fit to show a planner: short, no instruction-like text, no secrets. Page text never reaches a planner as free text."""
    out = []
    for e in pg.els:
        if e.role in INTERACTIVE and e.ref:
            n = (e.shown or "").strip()
            if n and len(n) <= 80 and not INJECTION.search(n) and not re.search(r"https?://", n):
                out.append((e.i, e.role, n))
        if len(out) >= limit:
            break
    return out


def dialog_els(pg: Page) -> list:
    return [e for e in pg.els if e.in_dialog and e.role in INTERACTIVE and e.ref]


def overlay_dismiss(pg: Page, banners: list = None):
    """The safest element that closes what covers the page: a known cookie-banner button (reject / necessary only first), then a dismiss-looking button in a
    dialog, then a 'Skip / Not now' button. Never an 'Accept / Allow / Upgrade' button here: those are decisions, not dismissals. (El or None, why)"""
    names = [b for b in (banners or []) if b]
    for want in names:
        for e in pg.els:
            if e.role in ("button", "link") and e.ref and _fold(e.shown) == _fold(want):
                return e, "known cookie banner button"
    inside = [e for e in dialog_els(pg) if e.role in ("button", "link")]
    pool = inside or [e for e in pg.els if e.role in ("button", "link") and e.ref and not e.in_dialog]
    for e in pool:
        n = _fold(e.shown)
        if DISMISS_NAME.match(n) and not re.search(r"(?i)accept|allow|agree|upgrade|buy|pay|subscribe|delete|remove|publish|share", n):
            return e, "a dismiss button"
    return None, ""


def has_overlay(pg: Page) -> bool:
    return any(e.role in OVERLAY_ROLES for e in pg.els) or bool(pg.modal)
