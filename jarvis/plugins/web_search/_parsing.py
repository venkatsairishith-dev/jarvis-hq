"""Parsing of DuckDuckGo result pages: pure functions with no network and no plugin API, so they are easy to test.

Safety rules:
- Every title and snippet goes through `sanitize_text` before it can reach a model or the speech engine. Text from a web page is
  untrusted: control, format (zero-width and bidi override), private-use and surrogate characters are removed, because they
  are used to hide instructions.
- A result address must be an absolute http(s) URL. `javascript:` links and relative links are refused.

The page parsers are adapted from N.E.K.O's web_search plugin (Apache-2.0, https://github.com/Project-N-E-K-O/N.E.K.O); the
Baidu parsers were dropped.
"""
from __future__ import annotations

import re
import unicodedata
from urllib.parse import unquote, urlparse

from bs4 import BeautifulSoup  # type: ignore[import-untyped]

MAX_TITLE_LEN = 200
MAX_SNIPPET_LEN = 320

_WS_RE = re.compile(r"\s+")


class SearchBlockedError(RuntimeError):
    """The search engine answered with a challenge page instead of results."""


def sanitize_text(text: str) -> str:
    """Untrusted page text made safe: control/format/private-use/surrogate characters and U+FFFD removed, whitespace squeezed."""
    if not text:
        return ""
    kept: list[str] = []
    for ch in text:
        if ch.isspace():
            kept.append(" ")
            continue
        if ord(ch) == 0xFFFD:
            continue
        if unicodedata.category(ch) in ("Cc", "Cf", "Co", "Cs"):
            continue
        kept.append(ch)
    return _WS_RE.sub(" ", "".join(kept)).strip()


def is_http_url(url: str) -> bool:
    # check the hostname, not the netloc: "https://@" and "http://:80" have a netloc but no hostname
    try:
        parsed = urlparse(url)
        return parsed.scheme in ("http", "https") and bool(parsed.hostname)
    except ValueError:
        return False


def _clip(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[:limit].rstrip() + "…"


def extract_real_url(href: str) -> str:
    """DuckDuckGo wraps result links as /l/?uddg=<encoded address>; this returns the address."""
    if "uddg=" in href:
        match = re.search(r"uddg=([^&]+)", href)
        if match:
            return unquote(match.group(1))
    return href


def is_ddg_ad_url(url: str) -> bool:
    # Ads are wrapped by duckduckgo.com/y.js. Do not look for "ad_provider=" in the target: a normal result's own query string
    # can carry the same parameter.
    try:
        parsed = urlparse(url)
    except ValueError:
        return True
    host = (parsed.hostname or "").lower()
    return (host == "duckduckgo.com" or host.endswith(".duckduckgo.com")) and parsed.path == "/y.js"


def is_ddg_blocked(html: str) -> bool:
    """Known DuckDuckGo challenge pages, which can come back with a 2xx status."""
    soup = BeautifulSoup(html[:20000], "html.parser")
    if soup.select_one("form#anomaly-modal") is not None:
        return True
    for tag, attribute in (("form", "action"), ("script", "src")):
        for node in soup.find_all(tag):
            url = str(node.get(attribute, "")).strip()
            if not url:
                continue
            parsed = urlparse(url)
            host = (parsed.hostname or "").lower()
            if parsed.path == "/anomaly.js" and (not host or host == "duckduckgo.com" or host.endswith(".duckduckgo.com")):
                return True
    return False


def is_ddg_no_results(html: str) -> bool:
    """DuckDuckGo's explicit empty-results container."""
    return BeautifulSoup(html, "html.parser").select_one(".no-results, #no-results") is not None


def parse_ddg_html(html: str, max_results: int = 8) -> list[dict[str, str]]:
    soup = BeautifulSoup(html, "html.parser")
    results: list[dict[str, str]] = []
    for link_tag in soup.select("a.result__a"):
        if link_tag.find_parent("div", class_=re.compile(r"result--ad")):
            continue
        title = sanitize_text(link_tag.get_text(" ", strip=True))
        real_url = extract_real_url(str(link_tag.get("href", "")))
        if not title or not is_http_url(real_url) or is_ddg_ad_url(real_url):
            continue
        snippet = ""
        parent = link_tag.find_parent("div", class_="result")
        if parent is not None:
            sn = parent.select_one("a.result__snippet")
            if sn is not None:
                snippet = sanitize_text(sn.get_text(" ", strip=True))
        results.append({"title": _clip(title, MAX_TITLE_LEN), "url": real_url, "snippet": _clip(snippet, MAX_SNIPPET_LEN)})
        if len(results) >= max_results:
            break
    return results


def parse_ddg_lite_html(html: str, max_results: int = 8) -> list[dict[str, str]]:
    soup = BeautifulSoup(html, "html.parser")
    results: list[dict[str, str]] = []
    for row in soup.find_all("tr"):
        link = row.find("a", href=True)
        if link is None:
            continue
        href = str(link.get("href", ""))
        if href.startswith("//"):                       # the Lite endpoint may use protocol-relative redirects
            href = "https:" + href
        if not href.startswith(("http://", "https://")):
            continue
        title = sanitize_text(link.get_text(" ", strip=True))
        real_url = extract_real_url(href)
        if not title or not is_http_url(real_url) or is_ddg_ad_url(real_url):
            continue
        snippet = ""
        next_row = row.find_next_sibling("tr")
        if next_row is not None:
            cell = next_row.find("td", class_="result-snippet")
            if cell is not None:
                snippet = sanitize_text(cell.get_text(" ", strip=True))
        results.append({"title": _clip(title, MAX_TITLE_LEN), "url": real_url, "snippet": _clip(snippet, MAX_SNIPPET_LEN)})
        if len(results) >= max_results:
            break
    return results
