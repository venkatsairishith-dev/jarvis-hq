"""Research pipeline with fakes for search, fetch, models and the verifier: no network, no model."""
import json

import pytest

from plugin.plugins.deliverables import spec as dspec
from plugin.plugins.research import core, fetch, rank

PAGES = {
    "https://owasp.org/top10": {"url": "https://owasp.org/top10", "title": "OWASP Top 10", "date": "2025-09-01",
                                "text": "The OWASP Top 10 is a standard awareness document for developers and web application security. "
                                        "It represents a broad consensus about the most critical security risks to web applications. " * 3},
    "https://portswigger.net/web-security": {"url": "https://portswigger.net/web-security", "title": "Web Security Academy", "date": "",
                                             "text": "The Web Security Academy is a free online training center for web application security. "
                                                     "It includes interactive labs where learners practise legally. " * 3},
    "https://spam.example/x": {"url": "https://spam.example/x", "title": "Spam", "date": "", "text": "Buy now. " * 50},
}


def fake_search(q, n):
    return [{"title": p["title"], "url": u, "snippet": ""} for u, p in PAGES.items()] + [{"title": "dup", "url": "https://www.owasp.org/top10/"}]


def fake_fetch(url):
    if url not in PAGES:
        raise fetch.FetchError("nope")
    return PAGES[url]


DRAFT = {"summary": "The OWASP Top 10 lists critical web application risks [S1]. Practice should use free legal labs [S2].",
         "sections": [
             {"heading": "What the OWASP Top 10 is", "kind": "fact",
              "paragraphs": ["The OWASP Top 10 is an awareness document about critical web risks [S1]. It is a law in every country [S1]."],
              "bullets": ["Made by aliens", "Learners can practise in interactive labs [S2]"]},
             {"heading": "Advice", "kind": "recommendation", "paragraphs": ["Start with the free labs."]}]}


def fake_verifier(sentence, passages):
    ok = "law" not in sentence and "aliens" not in sentence
    return ok, 0.99 if ok else 0.01


def run(**kw):
    args = dict(search=fake_search, fetch=fake_fetch, plan_llm=lambda s, u: json.dumps({"queries": ["owasp top 10", "legal web security labs"]}),
                write_llm=lambda s, u: "```json\n" + json.dumps(DRAFT) + "\n```", verifier=fake_verifier)
    args.update(kw)
    return core.run("How should a beginner learn web security?", depth="quick", **args)


def test_full_run_keeps_supported_and_moves_the_rest():
    out = run()
    spec = out["spec"]
    texts = " ".join(t for s in spec["sections"] for t in s["paragraphs"] + s["bullets"])
    assert "awareness document" in texts
    moved = spec["sections"][-1]
    assert moved["kind"] == "uncertain" and any("law" in b for b in moved["bullets"]) and any("aliens" in b for b in moved["bullets"])
    assert out["stats"]["unsupported"] == 1 and out["stats"]["uncited_facts"] == 1
    assert "Start with the free labs." in texts                      # uncited advice may stand
    dspec.report(spec)                                                # valid for the PDF maker
    dspec.deck(core.to_deck(spec))                                    # and for the deck maker


def test_structured_citations_become_markers_and_are_checked():
    draft = {"summary": [{"text": "The OWASP Top 10 lists critical web risks.", "sources": ["S1"]}],
             "sections": [{"heading": "Facts", "kind": "fact", "points": [
                 {"text": "The OWASP Top 10 is an awareness document.", "sources": ["S1"]},
                 {"text": "It is a law everywhere.", "sources": ["S1"]},
                 {"text": "Nobody cited this.", "sources": []}]},
                 {"heading": "Advice", "kind": "recommendation", "points": [{"text": "Use the free labs.", "sources": []}]}]}
    out = run(write_llm=lambda s, u: json.dumps(draft))
    spec = out["spec"]
    assert spec["summary"].endswith("[S1]")
    assert spec["sections"][0]["paragraphs"] == ["The OWASP Top 10 is an awareness document. [S1]"]
    assert spec["sections"][1]["bullets"] == ["Use the free labs."]
    assert out["stats"]["unsupported"] == 1 and out["stats"]["uncited_facts"] == 1
    dspec.report(spec)


def test_text_after_the_json_and_a_broken_writer():
    assert core.parse_json('{"a": 1}\n\nHope this helps!') == {"a": 1}
    out = run(write_llm=lambda s, u: "Sorry, I cannot", fallback_llm=lambda s, u: json.dumps(DRAFT))
    assert any("local model wrote" in n for n in out["notes"])
    with pytest.raises(core.ResearchError, match="readable report"):
        run(write_llm=lambda s, u: "Sorry, I cannot")


def test_duplicate_urls_and_unreadable_pages():
    out = run()
    assert out["counts"]["results"] == 3                              # www./trailing-slash duplicate dropped
    assert out["counts"]["pages_read"] >= 2


def test_private_values_are_left_out_of_searches():
    seen, searched = [], []
    out = run(redact=lambda q: q.replace("beginner", "[PERSON_1]"), plan_llm=lambda s, u: seen.append(u) or json.dumps({"queries": ["q1"]}),
              search=lambda q, n: searched.append(q) or fake_search(q, n))
    assert "beginner" not in seen[0] and "[PERSON_1]" not in seen[0]
    assert all("beginner" not in q and "[PERSON_1]" not in q for q in searched)
    assert any("left out of the web searches" in n for n in out["notes"])


def test_planner_failure_falls_back_to_the_question():
    out = run(plan_llm=lambda s, u: "not json")
    assert out["queries"] == ["How should a beginner learn web security?"]
    assert any("planning skipped" in n for n in out["notes"])


def test_no_results_and_bad_input():
    with pytest.raises(core.ResearchError, match="found nothing"):
        run(search=lambda q, n: [])
    with pytest.raises(core.ResearchError):
        core.run("", search=fake_search, fetch=fake_fetch, plan_llm=None, write_llm=lambda s, u: "{}", verifier=None)


def test_unknown_source_markers_are_not_trusted():
    bad = {"summary": "", "sections": [{"heading": "H", "kind": "fact", "paragraphs": ["Claim with a made-up source [S9]."]}]}
    out = run(write_llm=lambda s, u: json.dumps(bad))
    assert out["stats"]["unknown_sources"] == 1
    assert "[S9]" not in json.dumps(out["spec"])


def test_bm25_and_chunks():
    cs = rank.chunks("First sentence about OWASP risks. " * 30)
    assert cs and all(len(c) <= 760 for c in cs)
    s = rank.bm25("owasp risks", ["owasp risks and more owasp", "cooking pasta"])
    assert s[0] > s[1] == 0


def test_fetch_refuses_private_and_odd_urls():
    for url in ("http://127.0.0.1:8799/api", "file:///C:/Windows/win.ini", "http://localhost/", "https://example.com:8443/"):
        with pytest.raises(fetch.FetchError):
            fetch.check_url(url)


def test_extract_drops_scripts_and_reads_date():
    html = (b"<html><head><title>T</title><meta property='article:published_time' content='2026-01-05T10:00'></head>"
            b"<body><nav>menu menu menu menu menu menu</nav><script>evil()</script><article><p>" + b"Real readable content here. " * 10 +
            b"</p></article></body></html>")
    page = fetch.extract(html, "text/html", "https://x.example/a")
    assert page["date"] == "2026-01-05" and "evil" not in page["text"] and "menu" not in page["text"]
