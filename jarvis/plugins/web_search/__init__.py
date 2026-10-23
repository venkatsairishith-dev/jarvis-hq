"""Web search for JARVIS: DuckDuckGo's HTML pages through httpx and BeautifulSoup, with no search library and no key.

The `research` plugin calls `web_search:search` after the question has been cleared by the privacy gateway. Results are
untrusted page text: titles and snippets are cleaned (see _parsing.py) and the caller still treats them as data.

If DuckDuckGo answers with a challenge page, the plugin rests for a minute and says so, instead of retrying in a burst.
"""
from __future__ import annotations

import asyncio
import time
from typing import Any

import httpx

from jarvis.pluginapi import Err, Ok, PluginBase, SdkError, jarvis_plugin, lifecycle, plugin_entry

from ._parsing import SearchBlockedError, is_ddg_blocked, is_ddg_no_results, parse_ddg_html, parse_ddg_lite_html

USER_AGENT = "JARVIS-WebSearch/0.1 (personal assistant; https://github.com/venkatsairishith-dev/jarvis-hq)"
HTML_URL = "https://html.duckduckgo.com/html/"
LITE_URL = "https://lite.duckduckgo.com/lite/"
REQUEST_TIMEOUT = 15.0
COOLDOWN_SECONDS = 60.0
MAX_RESULTS = 10
DEFAULT_RESULTS = 8
MAX_QUERY_CHARS = 400


@jarvis_plugin
class WebSearchPlugin(PluginBase):
    _rest_until = 0.0
    _gate: asyncio.Semaphore | None = None

    @lifecycle(id="startup")
    async def startup(self, **_):
        return Ok({"status": "ready"})

    @lifecycle(id="shutdown")
    async def shutdown(self, **_):
        return Ok({"status": "stopped"})

    async def _fetch(self, client: httpx.AsyncClient, url: str, query: str) -> str:
        resp = await client.post(url, data={"q": query, "kl": "wt-wt"}, headers={"User-Agent": USER_AGENT})
        if resp.status_code in (202, 403, 429):
            raise SearchBlockedError(f"DuckDuckGo answered {resp.status_code}")
        resp.raise_for_status()
        html = resp.text
        if is_ddg_blocked(html):
            raise SearchBlockedError("DuckDuckGo asked for a challenge")
        return html

    async def _search(self, query: str, limit: int) -> list[dict[str, str]]:
        async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT, follow_redirects=True) as client:
            html = await self._fetch(client, HTML_URL, query)
            results = parse_ddg_html(html, limit)
            if not results and not is_ddg_no_results(html):
                results = parse_ddg_lite_html(await self._fetch(client, LITE_URL, query), limit)
        return results

    @plugin_entry(
        id="search",
        name="Web search",
        description="Search the web with DuckDuckGo. Keep the question in the owner's own language; do not translate it.",
        llm_result_fields=["summary"],
        timeout=30.0,
        input_schema={
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "What to search for, in the owner's own language."},
                "max_results": {"type": "integer", "description": "How many results at most (default 8, up to 10)."},
            },
            "required": ["query"],
        },
    )
    async def search(self, query: str = "", max_results: int = 0, **_: Any):
        query = " ".join(str(query or "").split())[:MAX_QUERY_CHARS]
        if not query:
            return Err(SdkError("The search needs words to look for."))
        limit = max(1, min(int(max_results or DEFAULT_RESULTS), MAX_RESULTS))
        now = time.monotonic()
        if now < self._rest_until:
            return Err(SdkError(f"Web search is resting for {int(self._rest_until - now) + 1} s after DuckDuckGo asked for a challenge."))
        if self._gate is None:
            self._gate = asyncio.Semaphore(2)
        try:
            async with self._gate:
                results = await self._search(query, limit)
        except SearchBlockedError as exc:
            self._rest_until = time.monotonic() + COOLDOWN_SECONDS
            return Err(SdkError(f"Web search is blocked for now ({exc})."))
        except (httpx.HTTPError, ValueError) as exc:
            return Err(SdkError(f"Web search could not reach DuckDuckGo ({type(exc).__name__})."))
        summary = "\n".join(f"{i}. {r['title']} - {r['url']}\n   {r['snippet']}".rstrip() for i, r in enumerate(results, 1))
        return Ok({"query": query, "provider": "duckduckgo", "results": results,
                   "summary": summary or "No results were found."})
