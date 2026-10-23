"""Browser control over the DOM (ladder rung above the accessibility tree and pixels). Playwright + the installed Chrome.

Safety properties (each has a test):
- throwaway profile per session, deleted afterwards: no cookies, logins or history from the user's own browser
- no downloads, no permissions (camera, location, notifications), no extra windows or popups
- EVERY request (top level, redirects, images, scripts) must pass the network guard: http/https only, no embedded
  credentials, and no localhost, private, link-local or Tailscale addresses (so a web page cannot make this
  browser reach the user's own network). Hostnames are resolved and every returned address is checked.
- page text and element names are scrubbed for secrets and are UNTRUSTED input (the caller marks the session)
- hidden elements and hidden text (display/visibility, opacity 0, off-page, tiny or transparent, clipped) are not
  shown to the model; password fields can be seen but never typed into
- clicks and typing must name the element they expect (name must match), so a stale id cannot hit another element
- an action budget per session; one page only
- open() reports the HTTP status, so an error page is never taken for the page asked for
"""
import json
import os
import shutil
import socket
import sys
import tempfile
import urllib.parse

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "kernel"))
from jarvis_kernel.policy import ai_chat_host, internal_host  # noqa: E402
from jarvis_kernel.scrub import scrub  # noqa: E402

KEYS = {"Enter", "Tab", "Escape"}
MAX_TEXT = 8000
MAX_ELEMENTS = 80


class BrowserError(Exception):
    pass


def _public_ips(host: str) -> bool:
    try:
        infos = socket.getaddrinfo(host, None)
    except OSError:
        return False
    addrs = {i[4][0] for i in infos}
    return bool(addrs) and not any(internal_host(a) for a in addrs)


def check_url(url: str, allow_hosts=()) -> str:
    """Return the URL if it may be fetched, else raise BrowserError. allow_hosts exempts exact host names (tests only)."""
    parts = urllib.parse.urlsplit(url.strip())
    if parts.scheme.lower() not in ("http", "https"):
        raise BrowserError("only http and https")
    if parts.username or parts.password:
        raise BrowserError("embedded credentials are not allowed")
    host = parts.hostname or ""
    if not host:
        raise BrowserError("no host")
    site = ai_chat_host(host)
    if site:                                       # plan P9 step 6: agents ask those models through their APIs, never their chat sites
        raise BrowserError(f"{site} is not automated (its terms forbid it); ask that model through its API")
    if host not in allow_hosts:
        if internal_host(host):
            raise BrowserError("internal address")
        if not _public_ips(host):
            raise BrowserError("host does not resolve to public addresses")
    return url


def load_egress_proxy() -> str:
    url = os.environ.get("JARVIS_EGRESS_PROXY")
    if not url:
        try:
            url = json.load(open(os.path.join(os.path.dirname(__file__), "..", "..", "config", "egress.json"), encoding="utf-8-sig")).get("proxy")
        except (OSError, ValueError):
            url = None
    if not url:
        raise BrowserError("no egress checkpoint configured; refusing to browse")
    return url


def verify_egress(proxy_url: str):
    """The browser only runs through a checkpoint that is reachable and demonstrably refuses internal addresses."""
    import http.client
    parts = urllib.parse.urlsplit(proxy_url)
    try:
        c = http.client.HTTPConnection(parts.hostname, parts.port or 80, timeout=8)
        c.request("GET", "http://169.254.169.254/latest/meta-data/", headers={"Host": "169.254.169.254"})
        status = c.getresponse().status
        c.close()
    except Exception:
        raise BrowserError("egress checkpoint unreachable; refusing to browse") from None
    if status not in (403, 407):
        raise BrowserError(f"egress checkpoint failed its self-test (status {status}); refusing to browse")


JS_ELEMENTS = """
(max) => {
  const sel = 'a[href],button,input,select,textarea,[role=button],[role=link]';
  const out = []; let n = 0;
  const de = document.documentElement, W = Math.max(de.scrollWidth, innerWidth), H = Math.max(de.scrollHeight, innerHeight);
  for (const el of document.querySelectorAll(sel)) {
    const r = el.getBoundingClientRect(); const st = getComputedStyle(el);
    if (r.width < 2 || r.height < 2 || st.visibility === 'hidden' || st.display === 'none') continue;
    if (el.checkVisibility && !el.checkVisibility({checkOpacity: true, opacityProperty: true, checkVisibilityCSS: true, visibilityProperty: true})) continue;
    if (el.closest('[aria-hidden=true]')) continue;
    if (r.right + scrollX <= 0 || r.bottom + scrollY <= 0 || r.left + scrollX >= W || r.top + scrollY >= H) continue;   // off-page
    if (n >= max) break;
    el.setAttribute('data-jarvis-id', String(n));
    const type = (el.getAttribute('type') || '').toLowerCase();
    const name = (el.getAttribute('aria-label') || el.innerText || el.value || el.getAttribute('placeholder') || el.getAttribute('name') || '').trim().slice(0, 80);
    out.push({id: n, tag: el.tagName.toLowerCase(), type: type, name: name, href: el.tagName === 'A' ? el.href.split('?')[0].slice(0, 160) : null,
              disabled: !!el.disabled, secret: type === 'password'});
    n++;
  }
  return out;
}
"""


# Visible text of the page. body.innerText already leaves out display:none and visibility:hidden, but not text that is
# hidden in other ways (opacity 0, off the page, font-size ~0, transparent colour, clipped to nothing by an ancestor):
# all common ways to plant instructions for a model. Those text nodes are blanked while innerText is read, then restored.
JS_TEXT = r"""
() => {
  const body = document.body;
  if (!body) return '';
  const de = document.documentElement, W = Math.max(de.scrollWidth, innerWidth), H = Math.max(de.scrollHeight, innerHeight);
  const cache = new Map();
  const css = e => { let s = cache.get(e); if (!s) { s = getComputedStyle(e); cache.set(e, s); } return s; };
  const alpha0 = c => { const m = /rgba?\(([^)]*)\)/.exec(c || ''); if (!m) return c === 'transparent';
                        const p = m[1].split(/[\s,\/]+/).filter(Boolean); return p.length >= 4 && parseFloat(p[3]) === 0; };
  const area = (l, t, r, b) => Math.max(0, r - l) * Math.max(0, b - t);
  const hidden = node => {
    const p = node.parentElement;
    if (!p) return false;
    const cs = css(p);
    if (p.checkVisibility && !p.checkVisibility({checkOpacity: true, opacityProperty: true})) return true;
    if (!p.checkVisibility && parseFloat(cs.opacity) === 0) return true;
    if (parseFloat(cs.fontSize) < 2 || alpha0(cs.color)) return true;
    const rg = document.createRange(); rg.selectNodeContents(node);
    const rs = Array.from(rg.getClientRects()).filter(r => r.width > 0 && r.height > 0);
    if (!rs.length) return false;                       // not laid out: innerText decides
    let total = 0, onPage = 0;
    for (const r of rs) {
      total += area(r.left, r.top, r.right, r.bottom);
      onPage += area(Math.max(r.left + scrollX, 0), Math.max(r.top + scrollY, 0), Math.min(r.right + scrollX, W), Math.min(r.bottom + scrollY, H));
    }
    if (onPage < total * 0.25) return true;             // positioned off the page
    for (let a = p; a && a !== de; a = a.parentElement) {
      const s = css(a);
      if (/^rect\(0px,? 0px,? 0px,? 0px\)$/.test(s.clip || '')) return true;
      if (!/hidden|clip/.test(s.overflowX + ' ' + s.overflowY)) continue;
      const b = a.getBoundingClientRect();
      let vis = 0;
      for (const r of rs) vis += area(Math.max(r.left, b.left), Math.max(r.top, b.top), Math.min(r.right, b.right), Math.min(r.bottom, b.bottom));
      if (vis < total * 0.25) return true;              // clipped away by an ancestor (zero height, 1px boxes)
    }
    return false;
  };
  const walker = document.createTreeWalker(body, NodeFilter.SHOW_TEXT);
  const blank = [];
  let n = 0;
  for (let t = walker.nextNode(); t && n < 20000; t = walker.nextNode()) {
    if (!t.data.trim()) continue;
    n++;
    const tag = t.parentElement ? t.parentElement.tagName : '';
    if (tag === 'SCRIPT' || tag === 'STYLE' || tag === 'NOSCRIPT') continue;
    if (hidden(t)) blank.push([t, t.data]);
  }
  for (const [t] of blank) t.data = '';
  try { return body.innerText; } finally { for (const [t, d] of blank) t.data = d; }
}
"""

class BrowserSession:
    def __init__(self, channel="chrome", headless=True, allow_hosts=(), max_actions=30, timeout_ms=20000, egress_proxy=None, local_guard=False):
        """egress_proxy: URL of the Smokescreen checkpoint (default: config/egress.json or $JARVIS_EGRESS_PROXY).
        local_guard=True uses the built-in local filtering proxy instead; it exists for tests and is never a silent fallback."""
        self.channel, self.headless, self.allow_hosts = channel, headless, tuple(allow_hosts)
        self.local_guard, self.egress_proxy = local_guard, egress_proxy
        self.max_actions, self.timeout_ms, self.used = max_actions, timeout_ms, 0
        self._host_ok = {}
        self._blocked = []       # hosts the early check refused (for audit/inspection; never page content)

    # ---------- lifecycle ----------
    def __enter__(self):
        from playwright.sync_api import sync_playwright
        self.proxy = None
        if self.local_guard:
            from .guardproxy import GuardProxy
            self.proxy = GuardProxy(self.allow_hosts).start()
            proxy_url = f"http://127.0.0.1:{self.proxy.port}"
        else:
            proxy_url = self.egress_proxy or load_egress_proxy()
            verify_egress(proxy_url)        # refuses to run unless the checkpoint is reachable AND denies the metadata address
        self.profile = tempfile.mkdtemp(prefix="jarvis-prof-")
        self._pw, self.ctx, self.page = None, None, None
        try:
            self._pw = sync_playwright().start()
            self.ctx = self._pw.chromium.launch_persistent_context(
                self.profile, channel=self.channel, headless=self.headless, accept_downloads=False, permissions=[],
                service_workers="block", proxy={"server": proxy_url, "bypass": "<-loopback>"},
                args=["--disable-extensions", "--disable-sync", "--no-first-run", "--force-webrtc-ip-handling-policy=disable_non_proxied_udp"])
            self.ctx.route("**/*", self._route)       # early refusal; the proxy is what actually guarantees it
            self.page = self.ctx.new_page()
            self.page.set_default_timeout(self.timeout_ms)
            self.ctx.on("page", lambda p: self._close_extra())   # no popups or extra windows
        except BaseException:
            # A failed launch (e.g. Chrome missing) must not leave Playwright's loop running: that loop would make every
            # later session in this process fail with "Sync API inside the asyncio loop". Profile and proxy go too.
            self.__exit__(None, None, None)
            raise
        return self

    def _close_extra(self):
        for p in list(self.ctx.pages):
            if self.page is not None and p != self.page:
                try:
                    p.close()
                except Exception:
                    pass

    def __exit__(self, *exc):
        try:
            if self.ctx is not None:
                self.ctx.close()
        finally:
            try:
                if self._pw is not None:
                    self._pw.stop()       # always, even if closing the browser failed (else the loop stays running)
                    self._pw = None
            finally:
                if self.proxy:
                    self.proxy.stop()
                shutil.rmtree(self.profile, ignore_errors=True)

    @property
    def blocked(self):
        return sorted(set(self._blocked) | set(getattr(getattr(self, "proxy", None), "blocked", [])))

    # ---------- network guard ----------
    def _allowed(self, url: str) -> bool:
        scheme = urllib.parse.urlsplit(url).scheme.lower()
        if scheme in ("data", "blob", "about"):
            return True
        if scheme not in ("http", "https"):
            return False
        host = urllib.parse.urlsplit(url).hostname or ""
        if host not in self._host_ok:
            try:
                check_url(url, self.allow_hosts)
                self._host_ok[host] = True
            except BrowserError:
                self._host_ok[host] = False
                self._blocked.append(host[:80])
        return self._host_ok[host]

    def _route(self, route, request):
        if self._allowed(request.url):
            route.continue_()
        else:
            route.abort("blockedbyclient")

    # ---------- actions ----------
    def _spend(self):
        if self.used >= self.max_actions:
            raise BrowserError("action budget used up")
        self.used += 1

    def _reset(self):
        """Leave a failed navigation (Chrome error page) and return to a clean blank page."""
        try:
            self.page.goto("about:blank")
        except Exception:
            try:
                self.page.wait_for_load_state("domcontentloaded", timeout=2000)
                self.page.goto("about:blank")
            except Exception:
                pass

    def open(self, url: str):
        self._spend()
        try:
            check_url(url, self.allow_hosts)
        except BrowserError:
            self._blocked.append((urllib.parse.urlsplit(url).hostname or "?")[:80])
            raise
        if self.page.url.startswith("chrome-error://"):
            self._reset()
        try:
            resp = self.page.goto(url, wait_until="domcontentloaded")
        except Exception as e:
            self._reset()
            raise BrowserError("navigation failed: " + type(e).__name__) from None
        if not self._allowed(self.page.url):          # a redirect landed somewhere forbidden
            self._reset()
            raise BrowserError("redirected to a blocked address")
        # The HTTP status of the page that loaded: a 404 or 500 page can look like a normal page ("Welcome back"),
        # so the caller must be able to tell it apart. None when the browser reports no response (same-page jump).
        status = resp.status if resp is not None else None
        return {"url": scrub(self.page.url.split("#")[0])[0][:200], "status": status, "ok": status is None or status < 400}

    def snapshot(self, max_text=MAX_TEXT, max_elements=MAX_ELEMENTS) -> dict:
        els = self.page.evaluate(JS_ELEMENTS, max_elements)
        for e in els:
            e["name"] = scrub(e["name"])[0]
            e["href"] = scrub(e["href"])[0] if e["href"] else None
        text = scrub(self.page.evaluate(JS_TEXT) or "")[0][:max_text]     # visible text only (see JS_TEXT)
        return {"url": scrub(self.page.url.split("#")[0])[0][:200], "title": scrub(self.page.title())[0][:120], "text": text,
                "elements": els, "untrusted": True, "truncated": len(text) >= max_text}

    def _element(self, el_id, expect_name):
        if not expect_name:
            raise BrowserError("the expected element name is required")
        loc = self.page.locator(f'[data-jarvis-id="{int(el_id)}"]')
        if loc.count() != 1:
            raise BrowserError("no such element (take a new snapshot)")
        info = loc.evaluate("el => ({type: (el.getAttribute('type')||'').toLowerCase(), "
                            "name: (el.getAttribute('aria-label')||el.innerText||el.value||el.getAttribute('placeholder')||el.getAttribute('name')||'').trim().slice(0,80)})")
        if scrub(info["name"])[0] != expect_name:
            raise BrowserError("element changed (take a new snapshot)")
        return loc, info

    def click(self, el_id, expect_name):
        self._spend()
        loc, info = self._element(el_id, expect_name)
        loc.click()

    def type(self, el_id, expect_name, text):
        self._spend()
        loc, info = self._element(el_id, expect_name)
        if info["type"] == "password":
            raise BrowserError("password fields are never typed into")
        clean, findings, blocked = scrub(text)
        if blocked or findings:
            raise BrowserError("text looks like a secret: refused")
        loc.fill(text)

    def select(self, el_id, expect_name, value):
        self._spend()
        loc, info = self._element(el_id, expect_name)
        loc.select_option(value)

    def press(self, key):
        self._spend()
        if key not in KEYS:
            raise BrowserError("key not allowed")
        self.page.keyboard.press(key)


def controller_for(ask, session: BrowserSession, max_actions=30):
    """A Controller wired to a browser session: authorize with the kernel first, then act, then verify."""
    from .control import Controller

    def open_(target, params): session.open(params["url"])
    def click(target, params): session.click(target.split("|", 1)[1], params.get("name", ""))
    def type_(target, params): session.type(target.split("|", 1)[1], params.get("name", ""), params.get("text", ""))
    def select(target, params): session.select(target.split("|", 1)[1], params.get("name", ""), params.get("value", ""))
    def press(target, params): session.press(params.get("key", ""))
    return Controller(ask, {"open": open_, "click": click, "type": type_, "select": select, "press": press},
                      lambda process, **kw: session.snapshot(), max_actions=max_actions, tool="browser")
