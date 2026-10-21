"""Read-only source adapters beyond the web search (master integration pass, 2026-10-20).

Agent Reach (Panniantong/Agent-Reach, MIT) was evaluated: its web reading sends every URL to a third party (Jina), its
search to Exa, and its X / Reddit / LinkedIn channels need the owner's cookies or a logged-in browser and break those
platforms' terms. None of that is used. What is added here is the safe part of the idea, on JARVIS's own fetcher:

    github_repo(owner, repo)   repository facts from the public GitHub REST API: description, license, default branch,
                               last push, archived, stars, open issues, latest release, README excerpt
    github_search(query, n)    public repository search (unauthenticated: 10 searches a minute, 60 API calls an hour)

Every request goes through ``fetch._get`` (public addresses only, pinned socket, redirects re-checked, size and time
limits). No token, cookie or login is ever sent. Results are citation records:
    {"platform", "url", "title", "retrieved_at", "updated_at", "excerpt", "facts"}
with text cleaned of sentences addressed to an AI. A failure (rate limit, 404, network) is returned as
{"ok": False, "why": ...}; nothing raises past this module and nothing is invented.
"""
from __future__ import annotations

import base64
import json
import re
import time
from typing import Any, Callable

from . import fetch

API = "https://api.github.com"
_NAME = re.compile(r"^[A-Za-z0-9_.-]{1,100}$")
_REPO_URL = re.compile(r"https?://github\.com/([A-Za-z0-9_.-]{1,100})/([A-Za-z0-9_.-]{1,100})")
Getter = Callable[[str], tuple[str, bytes, str]]


def _clean(text: Any, limit: int) -> str:
    from plugin.plugins._shared.untrusted import clean_untrusted

    from .verify import strip_injected
    kept, _ = strip_injected(str(text or ""))
    return clean_untrusted(kept, limit)


def _json_get(url: str) -> tuple[str, bytes, str]:
    return fetch._get(url, accept_json=True)            # the same SSRF-safe fetcher, JSON allowed for API answers


def _json(url: str, get: Getter) -> Any:
    _final, body, _ctype = get(url)
    return json.loads(body.decode("utf-8", "replace"))


def repos_in(text: str) -> list[tuple[str, str]]:
    out = []
    for owner, repo in _REPO_URL.findall(text or ""):
        repo = re.sub(r"\.git$", "", repo)
        if (owner, repo) not in out:
            out.append((owner, repo))
    return out[:3]


def github_repo(owner: str, repo: str, get: Getter | None = None) -> dict[str, Any]:
    get = get or _json_get
    if not (_NAME.match(owner or "") and _NAME.match(repo or "")):
        return {"ok": False, "why": "not a GitHub owner/repo name"}
    now = round(time.time(), 3)
    try:
        info = _json(f"{API}/repos/{owner}/{repo}", get)
    except Exception as e:  # noqa: BLE001 - rate limit, 404, network: say which, never invent
        return {"ok": False, "why": f"GitHub repository lookup failed ({type(e).__name__}: {str(e)[:80]})",
                "url": f"https://github.com/{owner}/{repo}"}
    if not isinstance(info, dict) or "full_name" not in info:
        return {"ok": False, "why": "GitHub returned no repository", "url": f"https://github.com/{owner}/{repo}"}
    facts = {"license": ((info.get("license") or {}).get("spdx_id") or "none stated"), "default_branch": info.get("default_branch"),
             "pushed_at": info.get("pushed_at"), "archived": bool(info.get("archived")), "fork": bool(info.get("fork")),
             "stars": info.get("stargazers_count"), "open_issues": info.get("open_issues_count")}
    if info.get("fork") and isinstance(info.get("parent"), dict):
        facts["fork_of"] = info["parent"].get("full_name")
    release, readme = None, ""
    try:
        rel = _json(f"{API}/repos/{owner}/{repo}/releases/latest", get)
        if isinstance(rel, dict) and rel.get("tag_name"):
            release = {"tag": _clean(rel.get("tag_name"), 60), "published_at": rel.get("published_at")}
    except Exception:  # noqa: BLE001 - many repositories have no release
        release = None
    try:
        rd = _json(f"{API}/repos/{owner}/{repo}/readme", get)
        if isinstance(rd, dict) and rd.get("encoding") == "base64":
            readme = base64.b64decode(rd.get("content", "")).decode("utf-8", "replace")
    except Exception:  # noqa: BLE001
        readme = ""
    facts["latest_release"] = release
    return {"ok": True, "platform": "github", "url": info.get("html_url") or f"https://github.com/{owner}/{repo}",
            "title": _clean(info.get("full_name"), 120), "retrieved_at": now, "updated_at": info.get("pushed_at"),
            "excerpt": _clean(info.get("description") or "", 400), "readme_excerpt": _clean(readme, 3000), "facts": facts}


def github_search(query: str, n: int = 5, get: Getter | None = None) -> dict[str, Any]:
    get = get or _json_get
    q = re.sub(r"[^\w .:/+-]", " ", str(query or ""))[:200].strip()
    if not q:
        return {"ok": False, "why": "empty query", "results": []}
    from urllib.parse import quote
    try:
        data = _json(f"{API}/search/repositories?q={quote(q)}&per_page={max(1, min(int(n), 10))}", get)
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "why": f"GitHub search failed ({type(e).__name__}: {str(e)[:80]})", "results": []}
    now = round(time.time(), 3)
    rows = []
    for it in (data.get("items") or [])[:n] if isinstance(data, dict) else []:
        rows.append({"platform": "github", "url": it.get("html_url"), "title": _clean(it.get("full_name"), 120),
                     "retrieved_at": now, "updated_at": it.get("pushed_at"), "excerpt": _clean(it.get("description") or "", 300),
                     "facts": {"license": ((it.get("license") or {}).get("spdx_id") or "none stated"),
                               "stars": it.get("stargazers_count"), "archived": bool(it.get("archived"))}})
    return {"ok": True, "results": rows}
