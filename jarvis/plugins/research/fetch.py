"""Fetch web pages safely and pull out their readable text, title and date.

Pages are untrusted. Only http(s) on ports 80/443; every address the name resolves to must be public (no loopback,
private, link-local or reserved ranges, checked again on each redirect); at most 3 redirects, 1.5 MB, 15 s. Scripts,
styles, navigation, forms and inline-hidden text are dropped. Results are cached on disk for a day so repeated research is cheaper.
"""
from __future__ import annotations

import hashlib
import http.client
import ipaddress
import json
import os
import re
import socket
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from lxml import etree
from lxml import html as lhtml

CACHE = Path(os.environ.get("RESEARCH_CACHE_DIR", r"D:\jarvis\cache\research"))
MAX_BYTES, TIMEOUT, MAX_REDIRECTS, CACHE_SECONDS = 1_500_000, 15, 3, 24 * 3600
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) JARVIS-Research/0.1 (personal assistant; +local)"
DROP = ("script", "style", "noscript", "nav", "header", "footer", "aside", "form", "svg", "iframe", "button")


class FetchError(Exception):
    pass


def _public_ip(addr: str) -> bool:
    try:
        ip = ipaddress.ip_address(addr.split("%")[0])
    except ValueError:
        return False
    mapped = getattr(ip, "ipv4_mapped", None)           # ::ffff:127.0.0.1 is 127.0.0.1
    if mapped is not None:
        ip = mapped
    return ip.is_global and not ip.is_multicast


def public_host(host: str) -> bool:
    try:
        infos = socket.getaddrinfo(host, None)
    except (socket.gaierror, UnicodeError, ValueError):
        return False
    return bool(infos) and all(_public_ip(info[4][0]) for info in infos)


def check_url(url: str) -> str:
    p = urllib.parse.urlsplit(url)
    if p.scheme not in ("http", "https") or not p.hostname:
        raise FetchError("only http(s) links are fetched")
    try:
        port = p.port
    except ValueError:
        raise FetchError("unusual port refused") from None
    if port not in (None, 80, 443):
        raise FetchError("unusual port refused")
    if p.username is not None or p.password is not None:
        raise FetchError("links with a user name or password are not fetched")
    if not public_host(p.hostname):
        raise FetchError("address is not public")
    return url


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None


def _pinned_socket(host: str, port: int, timeout) -> socket.socket:
    """Connect to an address that was checked in THIS resolution: check_url resolves the name once and urllib resolves it again at
    connect time, so a name that answers with a public address first and 169.254.169.254 / 127.0.0.1 second (DNS rebinding) passed the
    check and then connected inside. Every address of this answer must be public, and the socket is opened to one of them."""
    infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    if not infos or not all(_public_ip(i[4][0]) for i in infos):
        raise FetchError("address is not public")
    last: OSError | None = None
    for family, kind, proto, _name, addr in infos:
        sock = socket.socket(family, kind, proto)
        try:
            sock.settimeout(timeout)
            sock.connect(addr)
            return sock
        except OSError as exc:
            sock.close()
            last = exc
    raise last or OSError("no address")


class _PinnedHTTP(http.client.HTTPConnection):
    def connect(self):
        self.sock = _pinned_socket(self.host, self.port, self.timeout)


class _PinnedHTTPS(http.client.HTTPSConnection):
    def connect(self):
        sock = _pinned_socket(self.host, self.port, self.timeout)
        self.sock = self._context.wrap_socket(sock, server_hostname=self.host)


class _HTTP(urllib.request.HTTPHandler):
    def http_open(self, req):
        return self.do_open(_PinnedHTTP, req)


class _HTTPS(urllib.request.HTTPSHandler):
    def https_open(self, req):
        return self.do_open(_PinnedHTTPS, req, context=self._context)


_OPENER = urllib.request.build_opener(_NoRedirect, urllib.request.ProxyHandler({}), _HTTP, _HTTPS)


def _get(url: str, accept_json: bool = False) -> tuple[str, bytes, str]:
    # TIMEOUT is the whole fetch (redirects and body), not only each socket wait: a server that drips the page out
    # slowly would otherwise hold a research run for as long as it likes.
    deadline = time.monotonic() + TIMEOUT
    for _ in range(MAX_REDIRECTS + 1):
        check_url(url)
        accept = "application/json" if accept_json else "text/html,application/xhtml+xml;q=0.9,*/*;q=0.5"
        req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": accept, "Accept-Language": "en;q=0.9"})
        left = deadline - time.monotonic()
        if left <= 0:
            raise FetchError("page took too long")
        try:
            r = _OPENER.open(req, timeout=left)
        except urllib.error.HTTPError as e:
            if e.code in (301, 302, 303, 307, 308) and e.headers.get("Location"):
                url = urllib.parse.urljoin(url, e.headers["Location"])
                continue
            raise FetchError(f"HTTP {e.code}") from None
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            raise FetchError(type(e).__name__) from None
        with r:
            ctype = r.headers.get("Content-Type", "")
            if "html" not in ctype and "text/plain" not in ctype and not (accept_json and "json" in ctype):
                raise FetchError(f"not a web page ({ctype.split(';')[0] or 'unknown type'})")
            body = b""
            try:
                while len(body) <= MAX_BYTES:
                    if time.monotonic() > deadline:
                        raise FetchError("page took too long")
                    chunk = r.read1(65536)
                    if not chunk:
                        break
                    body += chunk
            except (TimeoutError, OSError) as e:
                raise FetchError(type(e).__name__) from None
            return r.geturl(), body[:MAX_BYTES], ctype
    raise FetchError("too many redirects")


_HIDDEN_STYLE = re.compile(r"display:none|visibility:hidden|opacity:0*(?:\.0+)?(?:;|$)|font-size:0+(?:\.0+)?(?:px|pt|em|rem|%)?(?:;|$)"
                           r"|(?:left|top|right):-\d{3,}|text-indent:-\d{3,}")


def _hidden(el) -> bool:
    if el.get("hidden") is not None or (el.get("aria-hidden") or "").lower() == "true" or el.tag == "template":
        return True
    return bool(_HIDDEN_STYLE.search(re.sub(r"\s+", "", (el.get("style") or "").lower())))


_DATE_META = ("article:published_time", "og:published_time", "date", "dc.date", "dcterms.date", "pubdate",
              "article:modified_time", "last-modified")


def extract(body: bytes, ctype: str, url: str) -> dict:
    if "text/plain" in ctype:
        text = body.decode("utf-8", "replace")
        return {"url": url, "title": url, "date": "", "text": re.sub(r"\s+", " ", text)[:60000]}
    doc = lhtml.fromstring(body)
    for bad in doc.xpath("//" + " | //".join(DROP)):
        bad.drop_tree()
    # Text a reader cannot see is a common place to plant instructions for a model: drop elements hidden by attribute
    # or inline style. (Hiding done by a stylesheet class cannot be seen without a CSS engine; that text stays data.)
    for bad in [el for el in doc.iter(etree.Element) if _hidden(el)]:
        if bad.getparent() is not None:
            bad.drop_tree()
    title = (doc.xpath("string(//meta[@property='og:title']/@content)") or doc.xpath("string(//title)") or url).strip()
    date = ""
    for name in _DATE_META:
        v = doc.xpath(f"string(//meta[@property='{name}' or @name='{name}']/@content)")
        if v:
            date = v.strip()[:10]
            break
    if not date:
        date = (doc.xpath("string((//time/@datetime)[1])") or "").strip()[:10]
    root = (doc.xpath("//article") or doc.xpath("//main") or doc.xpath("//body") or [doc])[0]
    blocks = []
    for el in root.iter("h1", "h2", "h3", "p", "li", "td", "pre", "blockquote"):
        t = re.sub(r"\s+", " ", el.text_content()).strip()
        if len(t) >= 25 or el.tag in ("h1", "h2", "h3"):
            blocks.append(t)
    text = "\n".join(dict.fromkeys(blocks))                 # drop exact repeats, keep order
    return {"url": url, "title": re.sub(r"\s+", " ", title)[:200], "date": date, "text": text[:60000]}


def fetch(url: str) -> dict:
    key = hashlib.sha256(url.encode()).hexdigest()[:32]
    cached = CACHE / f"{key}.json"
    if cached.is_file() and time.time() - cached.stat().st_mtime < CACHE_SECONDS:
        try:
            return json.loads(cached.read_text(encoding="utf-8"))
        except ValueError:
            pass
    final, body, ctype = _get(url)
    page = extract(body, ctype, final)
    if len(page["text"]) < 200:
        raise FetchError("too little readable text")
    CACHE.mkdir(parents=True, exist_ok=True)
    cached.write_text(json.dumps(page, ensure_ascii=False), encoding="utf-8")
    return page
