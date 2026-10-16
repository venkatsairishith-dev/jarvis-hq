"""Search providers for research, in order, with timeouts, one retry, de-duplication and source tracking.

1. N.E.K.O's own web_search plugin (AnySearch, then DuckDuckGo): free, no key. Always first (owner's choice, 2026-10-16).
2. Tavily (optional): used only when D:\\jarvis-secrets\\providers\\tavily.key exists. It is asked when the first provider
   fails or returns fewer than half of the wanted results. Basic search = 1 credit (Tavily docs, 2026-10-16).
Every result keeps "found_by". The key is read from its file, kept in memory only, never logged or returned.
`compare()` runs both providers on the same queries so the owner can decide which should be the default.
"""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from typing import Callable

SECRET_ROOT = os.environ.get("NEKO_SECRET_REF_ROOTS", r"D:\jarvis-secrets").split(os.pathsep)[0]
TAVILY_URL = "https://api.tavily.com/search"
TIMEOUT = 15.0
_key_cache: dict[str, str] = {}


class SearchError(Exception):
    pass


def tavily_key() -> str:
    if "tavily" in _key_cache:
        return _key_cache["tavily"]
    path = os.path.join(SECRET_ROOT, "providers", "tavily.key")
    if not os.path.isfile(path):
        return ""
    with open(path, encoding="utf-8") as f:
        _key_cache["tavily"] = f.read().strip()
    return _key_cache["tavily"]


def tavily_search(query: str, n: int, opener=urllib.request.urlopen) -> list[dict]:
    key = tavily_key()
    if not key:
        raise SearchError("no Tavily key file")
    body = json.dumps({"query": query, "max_results": max(1, min(int(n), 20)), "search_depth": "basic"}).encode()
    req = urllib.request.Request(TAVILY_URL, data=body, headers={"Content-Type": "application/json", "Authorization": f"Bearer {key}",
                                                                 "User-Agent": "neko-research/1.0"})
    try:
        with opener(req, timeout=TIMEOUT) as r:
            data = json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        raise SearchError(f"Tavily HTTP {e.code}") from None
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as e:
        raise SearchError(f"Tavily {type(e).__name__}") from None
    return [{"title": r.get("title") or "", "url": r.get("url") or "", "snippet": (r.get("content") or "")[:400],
             "date": (r.get("published_date") or "")[:10]} for r in data.get("results") or [] if r.get("url")]


def with_retry(fn: Callable[[], list[dict]], retries: int = 1, pause: float = 1.0) -> list[dict]:
    """One retry on a timeout or server error; a refusal (4xx, no key) is not retried."""
    for attempt in range(retries + 1):
        try:
            return fn()
        except SearchError as e:
            msg = str(e)
            if attempt >= retries or any(code in msg for code in ("HTTP 4", "no Tavily key", "no key")):
                raise
            time.sleep(pause)
    return []


def norm(url: str) -> str:
    from urllib.parse import urlsplit, urlunsplit
    p = urlsplit(url or "")
    return urlunsplit((p.scheme.lower(), p.netloc.lower().removeprefix("www."), p.path.rstrip("/"), p.query, ""))


def merged_search(query: str, n: int, primary: Callable[[str, int], list[dict]],
                  secondary: Callable[[str, int], list[dict]] | None = None, notes: list | None = None) -> list[dict]:
    """Primary first; the secondary only when the primary fails or finds too little. Results de-duplicated, tagged."""
    notes = notes if notes is not None else []
    out, seen = [], set()

    def add(results, who):
        for r in results:
            u = norm(r.get("url", ""))
            if u and u not in seen:
                seen.add(u)
                out.append({**r, "found_by": who})

    try:
        add(with_retry(lambda: primary(query, n)), "web_search")
    except (SearchError, Exception) as e:                     # noqa: BLE001 - one provider failing is not the end
        notes.append(f"web_search failed ({str(e)[:60] or type(e).__name__})")
    if secondary and len(out) < max(1, n // 2):
        try:
            add(with_retry(lambda: secondary(query, n)), "tavily")
        except SearchError as e:
            if "no Tavily key" not in str(e):
                notes.append(f"tavily failed ({e})")
    return out[:n]


def compare(queries: list[str], primary: Callable[[str, int], list[dict]], n: int = 6) -> dict:
    """Both providers on the same queries: result counts, distinct domains, overlap, authority share and time."""
    from .rank import authority
    rows = []
    for q in queries:
        row = {"query": q}
        for name, fn in (("web_search", primary), ("tavily", tavily_search)):
            t0 = time.time()
            try:
                res = fn(q, n)
                doms = {norm(r["url"]).split("/")[2] if "//" in norm(r["url"]) else "" for r in res}
                row[name] = {"results": len(res), "domains": len(doms), "authoritative": sum(authority(r["url"]) > 0 for r in res),
                             "seconds": round(time.time() - t0, 1), "urls": [norm(r["url"]) for r in res]}
            except Exception as e:                            # noqa: BLE001
                row[name] = {"error": f"{type(e).__name__}: {str(e)[:60]}", "seconds": round(time.time() - t0, 1)}
        a, b = row["web_search"].get("urls") or [], row["tavily"].get("urls") or []
        row["overlap"] = len(set(a) & set(b))
        for name in ("web_search", "tavily"):
            row[name].pop("urls", None)
        rows.append(row)
    return {"rows": rows, "tavily_key": bool(tavily_key())}
