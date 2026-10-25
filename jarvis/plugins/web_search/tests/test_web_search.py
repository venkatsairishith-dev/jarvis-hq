"""web_search: DuckDuckGo page parsing, untrusted-text cleaning, and the search entry against a fake DuckDuckGo."""
from __future__ import annotations

import asyncio

import httpx
import pytest

from jarvis.plugins import web_search as W
from jarvis.plugins.web_search import _parsing as P

REAL_CLIENT = httpx.AsyncClient
HTML = """
<div class="result results_links"><a class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.org%2Fa%3Fx%3D1&rut=1">Example <b>A</b></a>
  <a class="result__snippet">First   snippet​ with hidden ‮ characters</a></div>
<div class="result result--ad"><a class="result__a" href="//duckduckgo.com/y.js?ad_provider=x&u=1">An advertisement</a></div>
<div class="result"><a class="result__a" href="javascript:alert(1)">Bad link</a></div>
<div class="result"><a class="result__a" href="https://example.net/b">Example B</a><a class="result__snippet">Second</a></div>
"""
LITE = """<table><tr><td><a href="//duckduckgo.com/l/?uddg=https%3A%2F%2Flite.example%2F">Lite result</a></td></tr>
<tr><td class="result-snippet">Lite snippet</td></tr></table>"""
BLOCKED = '<form id="anomaly-modal" action="//duckduckgo.com/anomaly.js"></form>'
NONE = '<div class="no-results">No results.</div>'


def test_results_are_unwrapped_cleaned_and_ads_and_bad_links_dropped():
    got = P.parse_ddg_html(HTML)
    assert [r["url"] for r in got] == ["https://example.org/a?x=1", "https://example.net/b"]
    assert got[0]["title"] == "Example A" and got[0]["snippet"] == "First snippet with hidden characters"


def test_limit_and_lite_pages():
    assert len(P.parse_ddg_html(HTML, 1)) == 1
    assert P.parse_ddg_lite_html(LITE) == [{"title": "Lite result", "url": "https://lite.example/", "snippet": "Lite snippet"}]


def test_sanitize_removes_what_hides_instructions():
    assert P.sanitize_text("a​b‮cd�e  f\n\tg") == "abcde f g"
    assert P.is_http_url("https://a.b/c") and not P.is_http_url("javascript:alert(1)") and not P.is_http_url("https://@") and not P.is_http_url("/relative")


def test_challenge_and_empty_pages_are_recognised():
    assert P.is_ddg_blocked(BLOCKED) and not P.is_ddg_blocked(HTML)
    assert P.is_ddg_no_results(NONE) and not P.is_ddg_no_results(HTML)


class FakeDDG:
    def __init__(self, *pages):
        self.pages, self.calls = list(pages), []

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.calls.append((str(request.url), request.content.decode()))
        page = self.pages.pop(0) if self.pages else NONE
        return page if isinstance(page, httpx.Response) else httpx.Response(200, text=page)


@pytest.fixture
def plugin(monkeypatch):
    p = object.__new__(W.WebSearchPlugin)
    p._rest_until = 0.0
    p._gate = None
    return p


def with_fake(monkeypatch, fake):
    monkeypatch.setattr(W.httpx, "AsyncClient", lambda **kw: REAL_CLIENT(transport=httpx.MockTransport(fake.handler), **kw))


def search(p, **kw):
    return asyncio.run(W.WebSearchPlugin.search(p, **kw))


def test_the_entry_returns_results_and_a_summary_for_the_model(plugin, monkeypatch):
    fake = FakeDDG(HTML)
    with_fake(monkeypatch, fake)
    res = search(plugin, query="  best   tea in   hyderabad ", max_results=5)
    assert res.value["provider"] == "duckduckgo" and len(res.value["results"]) == 2 and res.value["summary"].startswith("1. Example A - https://example.org/a?x=1")
    assert fake.calls[0][0] == W.HTML_URL and "q=best+tea+in+hyderabad" in fake.calls[0][1]


def test_an_empty_html_page_without_the_empty_marker_falls_back_to_the_lite_page(plugin, monkeypatch):
    fake = FakeDDG("<html></html>", LITE)
    with_fake(monkeypatch, fake)
    assert search(plugin, query="x").value["results"][0]["url"] == "https://lite.example/"
    assert [c[0] for c in fake.calls] == [W.HTML_URL, W.LITE_URL]


def test_no_results_is_an_answer_not_an_error(plugin, monkeypatch):
    with_fake(monkeypatch, FakeDDG(NONE))
    assert search(plugin, query="zzzz").value["summary"] == "No results were found."


def test_a_challenge_makes_the_plugin_rest_instead_of_retrying(plugin, monkeypatch):
    fake = FakeDDG(BLOCKED)
    with_fake(monkeypatch, fake)
    assert "blocked for now" in search(plugin, query="x").error.message
    again = search(plugin, query="y")
    assert "resting" in again.error.message and len(fake.calls) == 1                         # no second request while it rests


def test_status_429_is_a_block_and_a_network_error_is_said_plainly(plugin, monkeypatch):
    with_fake(monkeypatch, FakeDDG(httpx.Response(429)))
    assert "blocked for now" in search(plugin, query="x").error.message
    plugin._rest_until = 0.0

    def boom(request):
        raise httpx.ConnectError("no route")
    monkeypatch.setattr(W.httpx, "AsyncClient", lambda **kw: REAL_CLIENT(transport=httpx.MockTransport(boom), **kw))
    assert search(plugin, query="x").error.message == "Web search could not reach DuckDuckGo (ConnectError)."


def test_an_empty_question_and_a_huge_one(plugin, monkeypatch):
    assert search(plugin, query="   ").error.message == "The search needs words to look for."
    fake = FakeDDG(HTML)
    with_fake(monkeypatch, fake)
    search(plugin, query="word " * 500)
    assert len(fake.calls[0][1]) < 700


def test_weather_entry_geocodes_then_reads_the_forecast(monkeypatch):
    def handler(request):
        if "geocoding" in request.url.host:
            return httpx.Response(200, json={"results": [{"name": "Paris", "country": "France", "latitude": 48.85, "longitude": 2.35}]})
        return httpx.Response(200, json={"current": {"temperature_2m": 14.2, "weather_code": 3, "wind_speed_10m": 11.0},
                                         "daily": {"temperature_2m_max": [16.0], "temperature_2m_min": [9.0], "precipitation_probability_max": [20]}})
    transport = httpx.MockTransport(handler)
    monkeypatch.setattr(W.httpx, "AsyncClient", lambda **kw: REAL_CLIENT(transport=transport, **kw))
    res = asyncio.run(W.WebSearchPlugin.weather(W.WebSearchPlugin(), city="Paris"))
    assert res.value["condition"] == "overcast" and res.value["high_c"] == 16.0 and "Paris, France" in res.value["summary"]


def test_weather_entry_reports_an_unknown_city_and_an_outage(monkeypatch):
    transport = httpx.MockTransport(lambda r: httpx.Response(200, json={"results": []}))
    monkeypatch.setattr(W.httpx, "AsyncClient", lambda **kw: REAL_CLIENT(transport=transport, **kw))
    assert isinstance(asyncio.run(W.WebSearchPlugin.weather(W.WebSearchPlugin(), city="Nowhereville")), W.Err)
    down = httpx.MockTransport(lambda r: httpx.Response(503))
    monkeypatch.setattr(W.httpx, "AsyncClient", lambda **kw: REAL_CLIENT(transport=down, **kw))
    assert isinstance(asyncio.run(W.WebSearchPlugin.weather(W.WebSearchPlugin(), city="Paris")), W.Err)
