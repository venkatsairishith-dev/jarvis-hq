"""D76 (2026-10-27): the WEB milestone of an open-ended task. The generalisation of web_task beyond decks: instead of a site playbook, a PILOT that browses the owner's real Chrome
by himself, from the milestone the planner checked, with the same engine underneath (browser_agent.Agent: snapshot, act, verify by effect, the recovery ladder, walls, the host
list, the kernel on every job).

    mode     what the pilot does                                                                                  verified by
    open     goes to the site the owner named (a new tab of his Chrome)                                           the page is on that site
    search   uses the site's search page with the owner's words (a URL, or the search box and its button)        the results page names the words
    follow   presses the link whose words best fit what is sought (or the best search result), up to 3 hops      the landing page names what was sought
    read     reads the values asked for from the page, locally                                                    each value is IN the page text
    lookup   search, follow, read in one go (a product on a shop)                                                 as above
    download presses the download link, takes the file from Chrome into quarantine, reads it                      the file exists, its type and pages
    fill     types the values the OWNER wrote into the form's fields, never ticks, never submits               each field reads back what was typed
    sources  searches the web (via the privacy gateway), reads up to N pages on DISTINCT sites                    N excerpts from N domains

READ-ONLY by default. The pilot reads, searches, scrolls, opens links, copies values and downloads into quarantine (with the site's one-time grant). It NEVER presses a control
whose name is a consequential word (pay, buy, send, post, delete, sign in, accept, submit, book, ...: config/web_task.json), and the kernel judges every click as well.
Page text is DATA: it can name a link the pilot may choose, it can never add a step, change the goal, type something or widen the host list. Links are chosen by NUMBER from the
page's own control names (browser_snapshot.safe_names drops instruction-like names); every typed word comes from the owner's goal.
"""
from __future__ import annotations

import math
import re
import urllib.parse
from dataclasses import dataclass, field

from . import browser_agent as BA
from . import browser_snapshot as S
from . import taskplan as TP
from .taskboard import Board, BudgetExceeded
from .taskextract import Extractor, Found, in_text, norm, page_text, registrable
from .browser_snapshot import Locator, Page

SCHEMES_OK = ("https://",)
FILE_LINK_EXT = {"pdf", "zip", "doc", "docx", "xls", "xlsx", "csv", "ppt", "pptx", "exe", "msi", "rar", "7z", "dmg", "apk", "jpg", "jpeg", "png", "gif", "mp3", "mp4"}


@dataclass
class Outcome:
    ok: bool = False
    reason: str = "none"                          # one of taskplan.REASONS
    detail: str = ""                              # a plain sentence about what stopped it (names a site, never page text)
    facts: list = field(default_factory=list)     # sentences that were VERIFIED
    unverified: list = field(default_factory=list)
    data: dict = field(default_factory=dict)
    needs_owner: "dict | None" = None
    approval_id: str = ""
    how: str = ""


class WebGoal:
    """`make_agent(hosts) -> BA.Agent` builds the engine for this milestone (the real one has the kernel behind it, the tests a fake Chrome behind the same kernel)."""

    def __init__(self, make_agent, extractor: Extractor, board: Board, cfg: dict, goal: str, *, stop=lambda: False, ask=None, grants=None, take_local=None, search=None,
                 grant_wait_s: float = 300.0, line=lambda act, app="": None, pause_for_owner=lambda: True, sleep=lambda s: None, planner=None, verify_file=None):
        self.make_agent, self.ex, self.board, self.cfg, self.goal = make_agent, extractor, board, cfg, goal
        self.stop, self.ask, self.grants, self.take_local, self.search = stop, ask, grants, take_local, search
        self.grant_wait_s, self.line, self.pause_for_owner, self.sleep, self.planner = grant_wait_s, line, pause_for_owner, sleep, planner
        self.verify_file = verify_file
        self.agent: "BA.Agent | None" = None
        self.injections = 0                          # places on pages that spoke to the assistant (counted, never obeyed)
        self.hard = [w for w in cfg["hard_words"]]

    # ------------------------------------------------------------------------------------------------------------------------------- helpers
    def family(self, host: str) -> list:
        """The owner's site and the one-word variants of it (a site and its www form): what the engine's host list is made of. Never a sibling subdomain."""
        h = str(host or "").lower().strip(".")
        if not h:
            return []
        bare = h[4:] if h.startswith("www.") else h
        return list(dict.fromkeys([h, bare, "www." + bare]))

    def hosts_for(self, ms) -> list:
        hs = []
        for h in ([ms.site] if ms.site else []) + list(self.board.hosts):
            hs += self.family(h)
        return list(dict.fromkeys(hs))[:12]

    def is_hard(self, name: str) -> bool:
        low = " " + re.sub(r"[^a-z0-9 ]+", " ", str(name or "").lower()) + " "
        return any((" " + w + " ") in low for w in self.hard) or bool(BA.NEVER_PROPOSE.search(str(name or "")))

    def _budget(self, *keys) -> None:
        self.board.check(*keys)

    def _new_agent(self, ms) -> "BA.Agent":
        self.agent = self.make_agent(self.hosts_for(ms))
        self.agent.goal = (self.goal or "")[:200]
        left = max(1, int(self.board.left("actions")))
        self.agent.budget.max_actions = min(self.agent.budget.max_actions, left)
        self.agent.budget.max_seconds = max(30.0, min(self.agent.budget.max_seconds, float(self.board.left("seconds"))))
        return self.agent

    def _done_agent(self) -> None:
        ag = self.agent
        if ag is None:
            return
        self.board.spend("actions", ag.actions)
        self.injections += ag.injections
        self.board.d["asked"] = self.board.d.get("asked", 0) + ag.asks
        ag.actions = 0
        ag.injections = 0
        ag.asks = 0

    def look(self) -> Page:
        """The page now, after the engine's own hygiene: a wall is handed to the owner (CAPTCHA, 2FA, payment, sign-in: never passed), a cookie banner is dismissed, a pop-up to a
        site the task may not visit is closed, an off-list address asks."""
        ag = self.agent
        pg = ag.observe()
        pg = ag._prepare(pg, BA.Step(id="look", do="expect"))
        return ag.page or pg

    def settled(self) -> Page:
        """The page once it has stopped changing (up to three looks): a page that loads in pieces is read when it is whole."""
        pg = self.look()
        for _ in range(2):
            sig = pg.sig()
            self.sleep(0.2)
            nxt = self.agent.observe()
            if nxt.sig() == sig:
                return nxt
            pg = nxt
        return pg

    def _goto(self, url: str, new_tab: bool = False) -> Page:
        host = (urllib.parse.urlsplit(url).hostname or "").lower()
        fam = self.family(host) + [h for h in self.board.hosts]
        self.agent.allow_host(host)
        step = BA.Step(id="goto", do="goto", to=url, new_tab=new_tab, expect={"any": [{"host_is": h} for h in dict.fromkeys(fam)]})
        self._budget("pages", "actions", "seconds")
        self.agent.do(step)
        self.board.spend("pages", 1)
        pg = self.settled()
        self.board.add_host(pg.host)
        self.board.set_url(pg.url)
        return pg

    def ensure_page(self, ms) -> Page:
        """The page the milestone starts on: the live tab when it is on the task's site, else the last address of the board (in a new tab), else the site's front page."""
        try:
            pg = self.agent.observe()
            on_site = pg.host in self.family(ms.site) if ms.site else (pg.host in self.hosts_for(ms) or pg.host in self.board.hosts)
            if pg.host and on_site and pg.url.startswith("https://"):
                return self.look()
        except BA.BackendLost:
            pass
        url = (self.owner_url(ms.site) if ms.site and not self._board_url_on(ms.site) else self.board.d.get("url")) or ""
        if not url:
            raise BA.NeedOwner("no_site", "there is no page to start from", {})
        return self._goto(url, new_tab=True)

    def _board_url_on(self, site: str) -> bool:
        u = self.board.d.get("url") or ""
        return (urllib.parse.urlsplit(u).hostname or "").lower() in self.family(site)

    # ------------------------------------------------------------------------------------------------------------------------------- links
    def link_candidates(self, pg: Page, seek: str, query: str, hint: str, allowed: set) -> list:
        """[(score, El, absolute url)] best first: links whose words (name + address path) fit what is sought. A link to a site the task may not visit, a script or mail link, a link
        whose name is a consequential word, and a sponsored result are never candidates."""
        want = [t for t in TP.tokens(seek) if t not in TP.STOP] if seek and seek != "*best*" else []
        q = [t for t in TP.tokens(query) if t not in TP.STOP]
        out = []
        skip = set(self.cfg.get("result_skip", []))
        for e in pg.els:
            if e.role != "link" or not e.ref or not e.url:
                continue
            raw = e.url.strip()
            if raw.startswith(("#", "javascript:", "mailto:", "tel:", "data:")):
                continue
            url = urllib.parse.urljoin(pg.url, raw)
            u = urllib.parse.urlsplit(url)
            if u.scheme != "https" or (u.hostname or "").lower() not in allowed:
                continue
            leaf = u.path.rsplit("/", 1)[-1]
            if "." in leaf and leaf.rsplit(".", 1)[-1].lower() in FILE_LINK_EXT:
                continue                                         # a file is DOWNLOADED (with the site's grant), never "followed"
            name = (e.name or e.child_text or e.text or "").strip()
            if not name or len(name) > 200 or S.INJECTION.search(name) or self.is_hard(name) or norm(name) in skip or re.match(r"(?i)^(sponsored|ad)\b", name):
                continue
            ntok = set(TP.tokens(name)) | set(TP.tokens(urllib.parse.unquote(u.path).replace("-", " ").replace("_", " ")))
            if seek == "*best*":
                score = (len(set(q) & ntok) / max(1, len(q))) if q else 0.0
                if hint and hint in url:
                    score += 0.35
                if any(a.role in ("main", "article", "list", "listitem") for a in self._ancestors(pg, e)):
                    score += 0.1
            else:
                score = len(set(want) & ntok) / max(1, len(want)) if want else 0.0
                if want and norm(" ".join(want)) in norm(name):
                    score += 0.2
            if score > 0:
                out.append((round(score, 3), e, url))
        out.sort(key=lambda t: (-t[0], t[1].i))
        return out

    def _ancestors(self, pg: Page, e):
        i, n = e.parent, 0
        while 0 <= i < len(pg.els) and n < 12:
            yield pg.els[i]
            i, n = pg.els[i].parent, n + 1

    def body_has(self, pg: Page, seek: str) -> bool:
        want = {t for t in TP.tokens(seek) if t not in TP.STOP}
        have = set(TP.tokens(" ".join(pg.lines[:300])))
        return bool(want) and sum(1 for t in want if self._like(t, have)) >= max(1, math.ceil(0.8 * len(want)))

    @staticmethod
    def _like(t: str, have: set) -> bool:
        return t in have or any(len(t) >= 4 and len(h) >= 4 and (h.startswith(t) or t.startswith(h)) for h in have)

    def matches(self, pg: Page, seek: str, query: str) -> bool:
        """Is this page ABOUT what was sought? Its title and headings (not the links and text on it) hold all of a short phrase's words, or 60% of a long one's. A page that merely
        has a link to the thing is on the way, not there."""
        want = {t for t in TP.tokens(query if seek == "*best*" else seek) if t not in TP.STOP}
        if not want:
            return True
        head = set(TP.tokens(" ".join([pg.title] + [e.name or e.text for e in pg.els if e.role == "heading"])))
        hit = sum(1 for t in want if self._like(t, head))
        return hit >= (len(want) if len(want) <= 2 else math.ceil(0.6 * len(want)))

    # ------------------------------------------------------------------------------------------------------------------------------- the modes
    def m_open(self, ms) -> Outcome:
        site = ms.site
        if not site:
            return Outcome(False, "not_found", "the owner named no site to open")
        url = self.owner_url(site)
        pg = self._goto(url, new_tab=True)
        if pg.host not in self.family(site) and pg.host not in self.board.hosts:
            return Outcome(False, "host", f"the page is on {pg.host}, not on {site}")
        title = pg.title[:80]
        return Outcome(True, "none", facts=[f"opened {pg.host}" + (f" (page titled '{title}')" if title else "")])

    def owner_url(self, site: str) -> str:
        """The address the OWNER wrote for this site (with its path), else the site's front page."""
        return TP.url_for(site, self.goal)

    def m_search(self, ms) -> Outcome:
        if not ms.query:
            return Outcome(False, "not_found", "there is nothing to search for")
        site = ms.site or (self.board.hosts[0] if self.board.hosts else "")
        tpl = TP.search_template(site, self.cfg)
        if not tpl and not ms.site:
            for h in self.board.hosts:
                tpl = TP.search_template(h, self.cfg)
                if tpl:
                    site = h
                    break
        q = urllib.parse.quote_plus(ms.query)
        if tpl:
            pg = self._goto(tpl.replace("{q}", q), new_tab=not bool(self.board.d.get("url")))
        else:
            pg = self.ensure_page(ms)
            pg = self._search_box(ms, pg)
        wants = {t for t in TP.tokens(ms.query) if t not in TP.STOP}
        text = page_text(pg)
        if wants and not (wants & (set(TP.tokens(text)) | set(TP.tokens(urllib.parse.unquote_plus(pg.url))))):
            return Outcome(False, "no_effect", f"the results page on {pg.host} does not mention what was searched for")
        return Outcome(True, "none", facts=[f"searched {pg.host} for the owner's words and the results page loaded"])

    def _search_box(self, ms, pg: Page) -> Page:
        """No search page known for the site: the search box and its button (never the Enter key, which the kernel asks the owner about in a browser)."""
        box = Locator(role="searchbox", name="Search", any_of=("Search", "Search for", "Find", "Query", "q"))
        el, _s, _h = S.find(pg, box, 0.5)
        if el is None:
            box = Locator(role="textbox", name="Search", any_of=("Search", "Search for", "Find", "Query"))
            el, _s, _h = S.find(pg, box, 0.5)
        if el is None and ms.site:                                   # a page without a search box (a product page): the site's front page has one
            pg = self._goto(self.owner_url(ms.site))
        self.agent.do(BA.Step(id="search_fill", do="fill", loc=box, value=ms.query[:120].replace("{", "").replace("}", ""), intent="type the search words"))
        btn = Locator(role="button", name="Search", any_of=("Search", "Go", "Find", "Submit search"))
        self.agent.do(BA.Step(id="search_go", do="click", loc=btn, intent="press the search button", expect={"changed": True}))
        self.board.spend("pages", 1)
        return self.settled()

    def m_follow(self, ms) -> Outcome:
        """Press the link whose words fit what is sought, then look where it led: the page counts only when its title or headings are about it (`matches`); a page that is on the
        way (it has links that fit) is followed one more hop; a dead end is left with Back and the next best link is tried. At most `hops` deep, a few tries in all."""
        pg = self.ensure_page(ms)
        allowed = set(self.hosts_for(ms)) | set(self.board.hosts)
        tried: set = set()
        row = TP.site_row(ms.site or pg.host, self.cfg)
        hint = row.get("result_hint", "")
        hops = int(self.cfg["limits"]["hops"])
        if ms.seek and ms.seek != "*best*" and self.matches(pg, ms.seek, ms.query):
            return Outcome(True, "none", facts=[f"the page on {pg.host} is already about {ms.seek[:60]} ('{pg.title[:70]}')"])
        depth = 0
        for attempt in range(hops + 3):
            self._budget("pages", "actions", "seconds")
            self._tick()
            cands = [c for c in self.link_candidates(pg, ms.seek, ms.query, hint, allowed) if c[2] not in tried]
            if depth >= hops:
                cands = []
            if not cands or cands[0][0] < 0.34:
                prop = self._propose(pg, ms, tried, allowed) if cands or depth < hops else None
                if prop is not None:
                    cands = [prop] + [c for c in cands if c[2] != prop[2]]
            if not cands:
                if depth == 0:
                    if ms.seek and ms.seek != "*best*" and self.body_has(pg, ms.seek):
                        return Outcome(True, "none", facts=[f"the page on {pg.host} already shows what was sought ('{pg.title[:70]}')"])
                    break
                try:
                    self.agent.act(BA.Act("back"))                       # a dead end: one step back, and the next best link there
                except BA.NeedApproval:
                    break
                depth -= 1
                pg = self.settled()
                continue
            score, el, url = cands[0]
            tried.add(url)
            before = pg.url
            name = (el.name or el.child_text or el.text).strip()
            same = [e for e in pg.els if e.role == "link" and (e.name or e.child_text or e.text).strip() == name and e.ref]
            nth = next((k for k, e in enumerate(same) if e is el), 0)
            step = BA.Step(id=f"follow{attempt + 1}", do="click", loc=Locator(role="link", name=name, nth=nth), expect={"url_changed": True},
                           intent=f"open the link about {ms.seek or ms.query}"[:100])
            try:
                self.agent.do(step)
            except BA.StepFailed:
                try:
                    self._goto(url)                                  # the press found nothing to press: the link's own address, on a site the task may visit
                except BA.StepFailed:
                    continue
            self.board.spend("pages", 1)
            pg = self.settled()
            self.board.add_host(pg.host)
            self.board.set_url(pg.url)
            if pg.url == before:
                continue
            depth += 1
            if self.matches(pg, ms.seek, ms.query):
                return Outcome(True, "none", facts=[f"opened a page on {pg.host} about {ms.seek if ms.seek != '*best*' else 'the search words'}" + (f" ('{pg.title[:70]}')" if pg.title else "")])
        return Outcome(False, "not_found", f"no link on {pg.host or ms.site} led to a page about {ms.seek if ms.seek != '*best*' else 'what was searched'}")

    def _propose(self, pg: Page, ms, tried: set, allowed: set):
        """No link fits well by its words: the local decision engine picks ONE link by its number from the page's own link names (browser_planner.LocalPlanner)."""
        if self.planner is None or not (ms.seek or ms.query):
            return None
        opts = [(i, role, n) for i, role, n in S.safe_names(pg, 60) if role == "link" and not self.is_hard(n)]
        if not opts:
            return None
        try:
            prop = self.planner.propose(self.goal, ms.seek if ms.seek != "*best*" else ms.query, pg, opts, BA.Step(id="p", do="click"))
        except Exception:  # noqa: BLE001
            return None
        if not isinstance(prop, dict) or prop.get("do") != "click" or prop.get("idx") not in {o[0] for o in opts}:
            return None
        e = pg.els[prop["idx"]]
        url = urllib.parse.urljoin(pg.url, e.url or "")
        u = urllib.parse.urlsplit(url)
        if u.scheme != "https" or (u.hostname or "").lower() not in allowed or url in tried:
            return None
        return (0.3, e, url)

    def _tick(self) -> None:
        if self.stop():
            raise BA.Stopped("the owner stopped the task")
        if not self.pause_for_owner():
            raise BA.Stopped("the owner stopped the task")

    def m_read(self, ms) -> Outcome:
        pg = self.ensure_page(ms)
        pg = self.settled()
        text = page_text(pg)
        row = TP.site_row(pg.host, self.cfg)
        ex = Extractor(self.ex.llm, row.get("title_strip", ""))
        names = ms.extract or ["answer"]
        subject = ms.subject or ms.seek if ms.seek != "*best*" else ms.subject or ms.query
        found, missing = ex.read(names, text, title=pg.title, subject=subject, question=ms.goal or subject)
        if not found:
            return Outcome(False, "no_values", f"none of {', '.join(names)} could be read from the page on {pg.host}")
        rec = {"host": pg.host, "url": pg.url, "title": pg.title[:160], "subject": subject[:100]}
        for n, f in found.items():
            rec[n] = f.value
            if f.many:
                rec[n + "_all"] = list(f.many)
        self.board.add_record(rec)
        facts = [f"read {n}: {f.value}" + ("" if f.how == "rule" else f" ({'the best matching line' if f.how == 'best_line' else 'found by the local model'}, in the page text)") for n, f in found.items()]
        out = Outcome(True, "none", facts=[f"on {pg.host}: " + "; ".join(facts)], data={"values": {n: f.value for n, f in found.items()}})
        if missing:
            out.ok, out.reason = False, "no_values"
            out.detail = f"could not read {', '.join(missing)} from the page on {pg.host}"
            out.unverified = [out.detail]
        return out

    def m_lookup(self, ms) -> Outcome:
        facts = []
        for sub, fn in (("search", self.m_search), ("follow", self.m_follow), ("read", self.m_read)):
            ms2 = _with_mode(ms, sub)
            out = fn(ms2)
            if not out.ok:
                out.facts = facts + out.facts
                return out
            facts += out.facts
        out.facts = facts[-1:] if facts else []
        return out

    # ---- download
    def ensure_grant(self, host: str, abilities=("download_to_quarantine",)) -> "dict | None":
        """The one-time per-site grant card for a download, if the site lacks it. The owner confirms it with the PIN in the console; no grant, no download. None = granted."""
        if self.grants is None:
            return {"kind": "grant", "site": host, "detail": "downloads need the owner's one-time grant for the site, and none can be asked for here"}
        lacking = self.grants.missing(host, abilities)
        if not lacking:
            return None
        got = self.grants.request([host], list(abilities), 30, "JARVIS wants to download one file from this site into the quarantine folder (read, never opened or run).")
        if "approval_id" not in got:
            return {"kind": "grant", "site": host, "detail": str(got.get("error") or "the grant card could not be made")[:160]}
        done = self.grants.wait_and_apply(got["request"], got["approval_id"], self.grant_wait_s, self.stop)
        if "granted" in done:
            return None
        return {"kind": "grant", "site": host, "approval_id": got["approval_id"],
                "detail": f"waiting for your PIN in the JARVIS console to let JARVIS download from {host}; approve the card, then ask me to continue"}

    def download_candidates(self, pg: Page, ms, ext: str, allowed: set) -> list:
        """Links and buttons that are plausibly THE download: the address ends in the file type, or the name says the type or 'download'; ranked by the words sought."""
        want = {t for t in TP.tokens(ms.seek) if t not in TP.STOP}
        cands = []
        for e in pg.els:
            if e.role not in ("link", "button") or not e.ref:
                continue
            name = (e.name or e.child_text or e.text).strip()
            if not name or self.is_hard(name) or S.INJECTION.search(name):
                continue
            url = urllib.parse.urljoin(pg.url, e.url or "") if e.url else ""
            if url and ((urllib.parse.urlsplit(url).hostname or "").lower() not in allowed or urllib.parse.urlsplit(url).scheme != "https"):
                continue
            ntok = set(TP.tokens(name)) | set(TP.tokens(urllib.parse.unquote(urllib.parse.urlsplit(url).path).replace("-", " ").replace("_", " ")))
            by_url = url.lower().split("?")[0].endswith("." + ext)
            if not (by_url or ext in ntok or "download" in ntok):
                continue
            score = (len(want & ntok) / max(1, len(want)) if want else 0.0) + (0.5 if by_url else 0.0) + (0.2 if ext in ntok else 0.0) + (0.1 if "download" in ntok else 0.0)
            cands.append((score, e, name))
        cands.sort(key=lambda t: (-t[0], t[1].i))
        return cands

    def m_download(self, ms) -> Outcome:
        pg = self.ensure_page(ms)
        ext = (ms.ext or "pdf").lower()
        host = pg.host
        need = self.ensure_grant(host)
        if need is not None:
            return Outcome(False, "no_grant", need["detail"], needs_owner=need, approval_id=need.get("approval_id", ""))
        allowed = set(self.hosts_for(ms)) | set(self.board.hosts) | {host}
        cands = self.download_candidates(pg, ms, ext, allowed)
        if not cands and ms.seek:                                   # the page does not have the file: the page about it may
            led = self.m_follow(_with_mode(ms, "follow"))
            if led.ok:
                pg = self.settled()
                host = pg.host
                need = self.ensure_grant(host)
                if need is not None:
                    return Outcome(False, "no_grant", need["detail"], needs_owner=need, approval_id=need.get("approval_id", ""))
                allowed |= {host}
                cands = self.download_candidates(pg, ms, ext, allowed)
        if not cands:
            return Outcome(False, "not_found", f"no download link for a {ext} file on the page on {host}")
        _s, el, name = cands[0]
        role = el.role
        same = [e for e in pg.els if e.role == role and (e.name or e.child_text or e.text).strip() == name and e.ref]
        nth = next((k for k, e in enumerate(same) if e is el), 0)
        self.agent.do(BA.Step(id="dl_click", do="click", loc=Locator(role=role, name=name, nth=nth), expect={"noop_ok": True}, intent="press the download link"))
        res = self.agent.do(BA.Step(id="dl_file", do="download", value=ext, expect={"download": {"ext": ext}}))
        path = ""
        ref = (res.data or {}).get("local_result")
        if ref and self.take_local:
            doc = self.take_local(ref)
            if isinstance(doc, dict):
                path = str(doc.get("file") or "")
        if not path:
            return Outcome(False, "no_file", f"the download from {host} gave no file")
        try:
            info = (self.verify_file or _verify_file)(path)
        except Exception as e:  # noqa: BLE001 - a file that cannot be read as what its name says is not a download
            return Outcome(False, "no_file", f"the file from {host} could not be read as a {ext} file ({str(e)[:80]})")
        self.board.add_file({**info, "path": path, "host": host})
        bits = [f"{info['ext']} file", f"{info['size']} bytes"] + ([f"{info['pages']} pages"] if info.get("pages") else [])
        return Outcome(True, "none", facts=[f"downloaded from {host} into quarantine and read: " + ", ".join(bits)], data={"file": info.get("name", "")})

    # ---- fill
    def m_fill(self, ms) -> Outcome:
        fills = TP.fills_from_goal(self.goal)
        if not fills:
            return Outcome(False, "no_values", "the owner gave no values for the form")
        pg = self.ensure_page(ms)
        personal = [k for k in fills if TP.is_personal(k)]
        if personal:
            ok = self._confirm_fill(pg.host, fills)
            if ok is None:
                return Outcome(False, "asked", "the form's values were not typed: there is no way to ask you from here", needs_owner={"kind": "confirm_fill", "site": pg.host, "detail": "personal details are typed only after you say so"})
            if not ok:
                return Outcome(False, "asked", "you did not allow the personal details to be typed, so nothing was entered", needs_owner={"kind": "declined", "site": pg.host, "detail": "declined"})
        typed, missing = [], []
        for label, value in fills.items():
            if re.search(r"[{}]|^@(?:cred|identity):", value):
                missing.append(label)
                continue
            loc = Locator(role="textbox", name=label, label=label, any_of=tuple(TP_FIELD_SYN.get(label, ())))
            el, _s, _h = S.find(pg, loc, 0.55)
            if el is None:
                missing.append(label)
                continue
            try:
                self.agent.do(BA.Step(id="fill_" + re.sub(r"\W", "", label)[:20], do="fill", loc=Locator(role=el.role, name=el.name or label), value=value, intent=f"type the {label}"))
                typed.append(label)
            except BA.StepFailed:
                missing.append(label)
            pg = self.look()
        pg = self.look()
        read_back = []
        for label in typed:
            el, _s, _h = S.find(pg, Locator(role="textbox", name=label, label=label, any_of=tuple(TP_FIELD_SYN.get(label, ()))), 0.55)
            if el is not None and norm(el.text) == norm(fills[label]):
                read_back.append(label)
        submit = next((e for e in pg.els if e.role == "button" and re.search(r"(?i)\b(submit|send|register|sign up|apply|book|confirm|continue)\b", e.name or "")), None)
        if submit is not None:
            self.board.d["submit"] = {"label": (submit.name or "")[:60], "host": pg.host}
        self.board.d["filled"] = {"host": pg.host, "labels": read_back}
        out = Outcome(bool(read_back) and len(read_back) == len(fills), "none" if read_back and not missing else "no_values", data={"labels": read_back})
        if read_back:
            out.facts = [f"typed and read back {len(read_back)} field(s) on {pg.host}: {', '.join(read_back)}; nothing was submitted"]
        if missing or len(read_back) < len(typed):
            out.unverified = [f"could not fill or read back: {', '.join(missing + [t for t in typed if t not in read_back])}"]
            out.detail = out.unverified[0]
        return out

    def _confirm_fill(self, host: str, fills: dict):
        """Entering personal details is the owner's decision: a form on his panel lists the fields and the values HE gave. True / False, or None when there is no panel."""
        if self.ask is None:
            return None
        self.line("ask_owner", host)
        spec = {"title": "Type your details into this form?", "intro": f"JARVIS will type these into the form on {host}. Nothing is submitted.",
                "fields": [{"type": "info_table", "label": "What will be typed", "rows": [[k, v] for k, v in fills.items()][:8]}],
                "buttons": [{"id": "go", "label": "Type them", "style": "primary"}, {"id": "no", "label": "Do not type", "style": "secondary"}]}
        try:
            ans = self.ask(spec)
        except Exception:  # noqa: BLE001
            return None
        return bool(isinstance(ans, dict) and ans.get("answered") and ans.get("button") == "go")

    # ---- several sources on a topic
    def m_sources(self, ms) -> Outcome:
        want = max(1, min(int(ms.count or self.cfg["research"]["min_domains"]), 6))
        if self.search is None:
            return Outcome(False, "failed", "web search is not available here")
        try:
            results = self.search(ms.query, want * 3)
        except Exception as e:  # noqa: BLE001 - a search that could not be made: the reason, never the query
            return Outcome(False, "failed", f"the web search could not be made ({str(e)[:100]})")
        seen_dom = {registrable(s["host"]) for s in self.board.sources}
        n_before = len(self.board.sources)
        first = True
        for r in results:
            if len(seen_dom) >= want:
                break
            self._tick()
            try:
                self._budget("pages", "actions", "seconds")
            except BudgetExceeded:
                break
            url = str(r.get("url") or "")
            u = urllib.parse.urlsplit(url)
            host = (u.hostname or "").lower()
            if u.scheme != "https" or not host or registrable(host) in seen_dom or host.endswith((".test.invalid",)):
                continue
            self.line("browse", host)
            self.agent.allow_host(host)
            self.board.add_host(host)
            try:
                pg = self._goto(url, new_tab=first)
            except (BA.StepFailed, BA.NeedOwner):
                continue
            first = False
            text = page_text(pg, 20000)
            if len(text.strip()) < 200:
                continue
            if self.board.add_source(pg.title or r.get("title") or host, pg.url, pg.host, text[: int(self.cfg["research"]["excerpt_chars"]) * 3]):
                seen_dom.add(registrable(pg.host))
        got = len(self.board.sources) - n_before
        doms = len({registrable(s["host"]) for s in self.board.sources})
        out = Outcome(doms >= want, "none" if doms >= want else "not_found", data={"sources": got, "domains": doms})
        out.facts = [f"read {len(self.board.sources)} page(s) from {doms} distinct site(s)"] if self.board.sources else []
        if doms < want:
            out.detail = f"only {doms} distinct site(s) could be read, {want} were asked for"
            out.unverified = [out.detail]
        return out

    # ------------------------------------------------------------------------------------------------------------------------------- entry
    def run(self, ms) -> Outcome:
        """One web milestone. Never raises for what the web does: a wall that the owner did not clear, a step no rung can do, a held click, a stop and a used-up budget each come
        back as an Outcome that says which."""
        self.agent = None
        try:
            self._tick()
            self._new_agent(ms)
            self.line("browse", ms.site or (self.board.hosts[0] if self.board.hosts else ""))
            fn = {"open": self.m_open, "search": self.m_search, "follow": self.m_follow, "read": self.m_read, "lookup": self.m_lookup, "download": self.m_download,
                  "fill": self.m_fill, "sources": self.m_sources}[ms.mode]
            return fn(ms)
        except BA.Stopped:
            return Outcome(False, "stopped", "the owner stopped the task")
        except BudgetExceeded as e:
            return Outcome(False, "budget", f"the task's {e.what} budget is used up")
        except BA.BudgetExceeded as e:
            return Outcome(False, "budget", f"the browser's own limit was reached ({str(e)[:80]})")
        except BA.NeedApproval as e:
            return Outcome(False, "approval", f"a step is held for your PIN in the console ({str(e)[:100]})", approval_id=getattr(e.res, "approval_id", ""),
                           how=getattr(e.res, "how", ""), needs_owner={"kind": "approval", "detail": str(e)[:160]})
        except BA.NeedOwner as e:
            return Outcome(False, "wall" if e.kind in ("captcha", "two_factor", "email_code", "payment", "signin", "paywall") else "host" if e.kind == "off_list" else "asked",
                           f"{e.kind}: {e.detail}"[:200], needs_owner={"kind": e.kind, "detail": e.detail[:160], **{k: v for k, v in (e.state or {}).items() if k == "site"}})
        except BA.StepFailed as e:
            return Outcome(False, "not_found" if "not_found" in e.why or "no element" in e.why else "no_effect", f"step {e.step_id}: {e.why}"[:200])
        except BA.BackendLost as e:
            return Outcome(False, "failed", f"the browser could not be reached ({str(e)[:100]})")
        except BA.AgentError as e:
            return Outcome(False, "failed", str(e)[:160])
        finally:
            self._done_agent()
            try:
                self.board.save()
            except Exception:  # noqa: BLE001
                pass


TP_FIELD_SYN = {"name": ("Name", "Full name", "Your name"), "full name": ("Full name", "Name", "Your name"), "first name": ("First name", "Given name"),
                "last name": ("Last name", "Surname", "Family name"), "email": ("Email", "E-mail", "Email address"), "e-mail": ("Email", "E-mail", "Email address"),
                "email address": ("Email", "E-mail", "Email address"), "phone": ("Phone", "Telephone", "Mobile", "Phone number"), "phone number": ("Phone", "Phone number", "Mobile"),
                "mobile": ("Mobile", "Phone", "Mobile number"), "city": ("City", "Town"), "address": ("Address", "Street address"), "message": ("Message", "Comments", "Your message"),
                "comment": ("Comment", "Comments", "Message"), "subject": ("Subject", "Topic"), "company": ("Company", "Organization", "Organisation"),
                "roll": ("Roll number", "Roll no", "Roll"), "student id": ("Student ID", "Roll number"), "quantity": ("Quantity", "Qty"), "country": ("Country",), "age": ("Age",)}


def _with_mode(ms, mode: str):
    import dataclasses
    return dataclasses.replace(ms, mode=mode, seek=(ms.seek if mode != "search" else ""), extract=(ms.extract if mode == "read" else []))


def _verify_file(path: str) -> dict:
    from . import ppt_flow
    return ppt_flow.verify_file(path)
