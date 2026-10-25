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
GEOCODE_URL = "https://geocoding-api.open-meteo.com/v1/search"
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
# WMO weather codes (Open-Meteo docs, https://open-meteo.com/en/docs#weathervariables)
WMO = {0: "clear sky", 1: "mostly clear", 2: "partly cloudy", 3: "overcast", 45: "fog", 48: "freezing fog", 51: "light drizzle", 53: "drizzle",
       55: "heavy drizzle", 56: "freezing drizzle", 57: "heavy freezing drizzle", 61: "light rain", 63: "rain", 65: "heavy rain", 66: "freezing rain",
       67: "heavy freezing rain", 71: "light snow", 73: "snow", 75: "heavy snow", 77: "snow grains", 80: "light showers", 81: "showers",
       82: "violent showers", 85: "snow showers", 86: "heavy snow showers", 95: "thunderstorm", 96: "thunderstorm with hail", 99: "heavy thunderstorm with hail"}


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

    @plugin_entry(
        id="weather",
        name="Weather",
        description="Current weather and today's forecast for a city (Open-Meteo, no key). Give the city in English, for example Paris.",
        llm_result_fields=["summary", "city", "country", "temperature_c", "condition", "high_c", "low_c", "rain_chance_pct", "wind_kmh"],
        timeout=30.0,
        input_schema={"type": "object", "properties": {"city": {"type": "string", "description": "City name in English, for example Paris."}}, "required": ["city"]},
    )
    async def weather(self, city: str = "", **_: Any):
        city = " ".join(str(city or "").split())[:80]
        if not city:
            return Err(SdkError("Which city?"))
        try:
            async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT, follow_redirects=True) as client:
                g = await client.get(GEOCODE_URL, params={"name": city, "count": 1, "language": "en", "format": "json"}, headers={"User-Agent": USER_AGENT})
                g.raise_for_status()
                hits = (g.json() or {}).get("results") or []
                if not hits:
                    return Err(SdkError(f"No place called '{city}' was found."))
                place = hits[0]
                f = await client.get(FORECAST_URL, headers={"User-Agent": USER_AGENT}, params={
                    "latitude": place["latitude"], "longitude": place["longitude"], "timezone": "auto", "forecast_days": 1,
                    "current": "temperature_2m,weather_code,wind_speed_10m",
                    "daily": "temperature_2m_max,temperature_2m_min,precipitation_probability_max"})
                f.raise_for_status()
                data = f.json() or {}
            cur, day = data.get("current") or {}, data.get("daily") or {}
            first = lambda k: (day.get(k) or [None])[0]       # noqa: E731
            out = {"city": str(place.get("name") or city), "country": str(place.get("country") or ""), "temperature_c": cur.get("temperature_2m"),
                   "condition": WMO.get(int(cur.get("weather_code") if cur.get("weather_code") is not None else -1), "unknown"),
                   "high_c": first("temperature_2m_max"), "low_c": first("temperature_2m_min"),
                   "rain_chance_pct": first("precipitation_probability_max"), "wind_kmh": cur.get("wind_speed_10m")}
        except (httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
            return Err(SdkError(f"The weather service could not be reached ({type(exc).__name__})."))
        out["summary"] = (f"{out['city']}, {out['country']}: {out['temperature_c']} C, {out['condition']}; today {out['low_c']} to {out['high_c']} C, "
                          f"rain chance {out['rain_chance_pct']} %, wind {out['wind_kmh']} km/h.")
        return Ok(out)
