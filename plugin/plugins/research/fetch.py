"""Fetch web pages safely and pull out their readable text, title and date.

Pages are untrusted. Only http(s) on ports 80/443; every address the name resolves to must be public (no loopback,
private, link-local or reserved ranges, checked again on each redirect); at most 3 redirects, 1.5 MB, 15 s. Scripts,
styles, navigation and forms are dropped. Results are cached on disk for a day so repeated research is cheaper.
"""
from __future__ import annotations

import hashlib
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

from lxml import html as lhtml

CACHE = Path(os.environ.get("RESEARCH_CACHE_DIR", r"D:\jarvis\cache\research"))
MAX_BYTES, TIMEOUT, MAX_REDIRECTS, CACHE_SECONDS = 1_500_000, 15, 3, 24 * 3600
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Neko-Research/0.1 (personal assistant; +local)"
DROP = ("script", "style", "noscript", "nav", "header", "footer", "aside", "form", "svg", "iframe", "button")


class FetchError(Exception):
    pass


def public_host(host: str) -> bool:
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror:
        return False
    for info in infos:
        ip = ipaddress.ip_address(info[4][0].split("%")[0])
        if not ip.is_global or ip.is_multicast:
            return False
    return bool(infos)


def check_url(url: str) -> str:
    p = urllib.parse.urlsplit(url)
    if p.scheme not in ("http", "https") or not p.hostname:
        raise FetchError("only http(s) links are fetched")
    if p.port not in (None, 80, 443):
        raise FetchError("unusual port refused")
    if not public_host(p.hostname):
        raise FetchError("address is not public")
    return url


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None


_OPENER = urllib.request.build_opener(_NoRedirect, urllib.request.ProxyHandler({}))


def _get(url: str) -> tuple[str, bytes, str]:
    for _ in range(MAX_REDIRECTS + 1):
        check_url(url)
        req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.5",
                                                   "Accept-Language": "en;q=0.9"})
        try:
            r = _OPENER.open(req, timeout=TIMEOUT)
        except urllib.error.HTTPError as e:
            if e.code in (301, 302, 303, 307, 308) and e.headers.get("Location"):
                url = urllib.parse.urljoin(url, e.headers["Location"])
                continue
            raise FetchError(f"HTTP {e.code}") from None
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            raise FetchError(type(e).__name__) from None
        with r:
            ctype = r.headers.get("Content-Type", "")
            if "html" not in ctype and "text/plain" not in ctype:
                raise FetchError(f"not a web page ({ctype.split(';')[0] or 'unknown type'})")
            body = r.read(MAX_BYTES + 1)
            if len(body) > MAX_BYTES:
                body = body[:MAX_BYTES]
            return r.geturl(), body, ctype
    raise FetchError("too many redirects")


_DATE_META = ("article:published_time", "og:published_time", "date", "dc.date", "dcterms.date", "pubdate",
              "article:modified_time", "last-modified")


def extract(body: bytes, ctype: str, url: str) -> dict:
    if "text/plain" in ctype:
        text = body.decode("utf-8", "replace")
        return {"url": url, "title": url, "date": "", "text": re.sub(r"\s+", " ", text)[:60000]}
    doc = lhtml.fromstring(body)
    for bad in doc.xpath("//" + " | //".join(DROP)):
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
