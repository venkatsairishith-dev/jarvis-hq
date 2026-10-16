"""Search provider chain: primary first, secondary only when needed, retry rules, de-duplication, tracking (no network)."""
import pytest

from plugin.plugins.research import search


def res(*urls):
    return [{"title": u, "url": u, "snippet": ""} for u in urls]


def test_primary_enough_secondary_not_asked():
    asked = []
    out = search.merged_search("q", 4, lambda q, n: res("https://a.org/x", "https://b.org/y", "https://c.org"),
                               lambda q, n: asked.append(q) or res("https://d.org"))
    assert len(out) == 3 and asked == [] and {r["found_by"] for r in out} == {"web_search"}


def test_secondary_fills_in_and_duplicates_are_dropped():
    out = search.merged_search("q", 6, lambda q, n: res("https://www.a.org/x/"),
                               lambda q, n: res("https://a.org/x", "https://e.org/1"))
    assert [r["url"] for r in out] == ["https://www.a.org/x/", "https://e.org/1"]
    assert [r["found_by"] for r in out] == ["web_search", "tavily"]


def test_primary_failure_is_noted_and_secondary_used():
    notes = []

    def broken(q, n):
        raise search.SearchError("web_search failed: busy")
    out = search.merged_search("q", 4, broken, lambda q, n: res("https://f.org"), notes)
    assert out[0]["found_by"] == "tavily" and any("web_search failed" in x for x in notes)


def test_retry_once_on_timeout_but_not_on_refusal():
    calls = []

    def flaky():
        calls.append(1)
        if len(calls) == 1:
            raise search.SearchError("Tavily TimeoutError")
        return res("https://g.org")
    assert search.with_retry(flaky, pause=0)[0]["url"] == "https://g.org" and len(calls) == 2
    calls.clear()

    def refused():
        calls.append(1)
        raise search.SearchError("Tavily HTTP 401")
    with pytest.raises(search.SearchError):
        search.with_retry(refused, pause=0)
    assert len(calls) == 1


def test_tavily_without_key_is_skipped_quietly(monkeypatch):
    monkeypatch.setattr(search, "tavily_key", lambda: "")
    with pytest.raises(search.SearchError, match="no Tavily key"):
        search.tavily_search("q", 3)
    notes = []
    out = search.merged_search("q", 4, lambda q, n: [], search.tavily_search, notes)
    assert out == [] and notes == []                 # a missing optional key is not an error
