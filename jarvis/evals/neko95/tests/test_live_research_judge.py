"""MEM-M10: the live research pass rule (bench/live_research_dahl.py judge()). The script itself needs the network and a key;
its pass function is pure and is tested here with canned reports, plus reports made by the real core.check_report.
Run: python -m pytest jarvis/evals/neko95/tests/test_live_research_judge.py -q"""
import importlib.util
import re
import sys
from pathlib import Path

BENCH = Path(__file__).resolve().parents[1] / "bench"
REPO = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(REPO))
spec_ = importlib.util.spec_from_file_location("live_research_dahl_t", BENCH / "live_research_dahl.py")
lr = importlib.util.module_from_spec(spec_)
spec_.loader.exec_module(lr)                      # imports nothing heavy: httpx, the kernel and the plugin load in main()

WANT = r"1889"
SRC = [{"id": "S1", "title": "Eiffel Tower", "url": "https://example.org/eiffel", "date": "", "publisher": ""}]
READ = {"https://example.org/eiffel"}
GOOD_STATS = {"cited": 3, "supported": 2, "unsupported": 1, "checked": True}


def report(summary="", fact=(), uncertain=(), disagree=(), kind="fact", sources=SRC):
    sections = []
    if fact:
        sections.append({"heading": "Facts", "kind": kind, "paragraphs": [" ".join(fact)], "bullets": []})
    if disagree:
        sections.append({"heading": "Sources disagree", "kind": "uncertain", "paragraphs": ["x"], "bullets": list(disagree)})
    if uncertain:
        sections.append({"heading": "Statements that could not be confirmed from the sources", "kind": "uncertain",
                         "paragraphs": ["These came up while writing but the cited sources did not clearly support them."],
                         "bullets": list(uncertain)})
    return {"summary": summary, "sources": list(sources), "sections": sections}


def old_rule(spec, stats, read_urls, want):
    """The rule before 2026-10-18, as it was in the script."""
    text = spec["summary"] + " " + " ".join(p for s in spec["sections"] for p in s["paragraphs"] + s["bullets"])
    fabricated = [s["url"] for s in spec["sources"] if s["url"] not in read_urls]
    return bool(re.search(want, text, re.I)) and not fabricated and stats.get("cited", 0) > 0 and len(spec["sources"]) > 0


def test_answer_in_a_confirmed_cited_sentence_passes_and_the_share_is_reported():
    spec = report(summary="The Eiffel Tower was completed in 1889. [S1]", fact=["It is about 330 m tall. [S1]"])
    j = lr.judge(spec, GOOD_STATS, READ, WANT)
    assert j["ok"] and j["confirmed_hits"] == 1 and j["supported_share"] == round(2 / 3, 3) and j["reasons"] == []


def test_answer_in_a_confirmed_fact_section_passes():
    spec = report(summary="A tower in Paris.", fact=["It was completed in 1889 for the World's Fair. [S1] It is made of iron. [S1]"])
    assert lr.judge(spec, GOOD_STATS, READ, WANT)["ok"]


def test_answer_only_in_the_could_not_be_confirmed_section_fails_though_the_old_rule_passed_it():
    spec = report(summary="A famous tower in Paris.", fact=["It stands on the Champ de Mars. [S1]"],
                  uncertain=["The tower was completed in 1889. [S1]"])
    assert old_rule(spec, GOOD_STATS, READ, WANT) is True                      # the lax rule: the word appears somewhere
    j = lr.judge(spec, GOOD_STATS, READ, WANT)
    assert not j["ok"] and j["answer_anywhere"] is True and j["confirmed_hits"] == 0
    assert "outside the confirmed parts" in j["reasons"][0]


def test_answer_only_in_sources_disagree_fails():
    spec = report(summary="x", fact=["It is in Paris. [S1]"], disagree=["Completed in 1889. [S1]", "Completed in 1887. [S1]"])
    assert not lr.judge(spec, GOOD_STATS, READ, WANT)["ok"]


def test_a_confirmed_sentence_without_a_citation_does_not_count():
    spec = report(summary="The Eiffel Tower was completed in 1889.", fact=["It is made of iron. [S1]"])
    j = lr.judge(spec, GOOD_STATS, READ, WANT)
    assert not j["ok"] and j["confirmed_hits"] == 0


def test_a_citation_to_a_source_the_report_does_not_list_does_not_count():
    spec = report(summary="Completed in 1889. [S9]", fact=["Iron. [S1]"])
    assert not lr.judge(spec, GOOD_STATS, READ, WANT)["ok"]


def test_the_claim_checker_must_have_run_and_supported_something():
    spec = report(summary="Completed in 1889. [S1]")
    j = lr.judge(spec, {"cited": 2, "supported": 2, "checked": False}, READ, WANT)
    assert not j["ok"] and any("claim checker was not available" in r for r in j["reasons"])
    j = lr.judge(spec, {"cited": 2, "supported": 0, "unsupported": 2, "checked": True}, READ, WANT)
    assert not j["ok"] and any("no cited sentence was supported" in r for r in j["reasons"]) and j["supported_share"] == 0.0


def test_without_a_claim_checker_no_section_is_labelled_fact_so_nothing_is_confirmed():
    spec = report(fact=["Completed in 1889. [S1]"], kind="")                    # core.check_report blanks the kind then
    assert lr.confirmed_sentences(spec) == []
    assert not lr.judge(spec, {"cited": 1, "supported": 0, "checked": False}, READ, WANT)["ok"]


def test_an_invented_source_fails():
    spec = report(summary="Completed in 1889. [S1]")
    j = lr.judge(spec, GOOD_STATS, set(), WANT)
    assert not j["ok"] and j["fabricated"] == ["https://example.org/eiffel"]


def test_no_citations_at_all_fails():
    assert not lr.judge(report(summary="Completed in 1889."), {"cited": 0, "supported": 0, "checked": True}, READ, WANT)["ok"]
    assert lr.judge(report(summary="Completed in 1889. [S1]"), {"cited": 0, "supported": 0, "checked": True}, READ, WANT)["supported_share"] is None


def test_sentence_split_keeps_each_citation_with_its_sentence():
    got = lr.confirmed_sentences(report(summary="Built in 1887. [S1] Opened in 1889. [S1]"))
    assert got == ["Built in 1887. [S1]", "Opened in 1889. [S1]"]
    spec = report(summary="Built in 1887. [S1] Opened in 1889.")
    assert not lr.judge(spec, GOOD_STATS, READ, WANT)["ok"]                      # the sentence with the answer has no marker


def test_with_reports_made_by_the_real_report_checker():
    """core.check_report moves a sentence its source does not support into the 'could not be confirmed' section."""
    from plugin.plugins.research import core
    passages = [{"sid": "S1", "text": "The tower is made of wrought iron and stands in Paris."}]
    draft = {"summary": [{"text": "A wrought iron tower in Paris.", "sources": ["S1"]}],
             "sections": [{"heading": "Facts", "kind": "fact", "points": [
                 {"text": "The tower was completed in 1889.", "sources": ["S1"]},
                 {"text": "It is made of wrought iron.", "sources": ["S1"]}]}]}

    def verifier(claim, texts):
        return ("1889" not in claim), 0.9
    rep, stats = core.check_report(draft, passages, verifier)
    spec = {"summary": rep["summary"], "sections": rep["sections"], "sources": SRC}
    assert any("1889" in b for s in spec["sections"] if s["kind"] == "uncertain" for b in s["bullets"])
    j = lr.judge(spec, stats, READ, WANT)
    assert old_rule(spec, stats, READ, WANT) and not j["ok"]                    # lax rule passes it, the new one does not
    rep, stats = core.check_report(draft, passages, lambda c, t: (True, 0.9))   # now the checker supports it
    spec = {"summary": rep["summary"], "sections": rep["sections"], "sources": SRC}
    assert lr.judge(spec, stats, READ, WANT)["ok"]
    rep, stats = core.check_report(draft, passages, None)                       # no checker: nothing confirmed
    spec = {"summary": rep["summary"], "sections": rep["sections"], "sources": SRC}
    assert not lr.judge(spec, stats, READ, WANT)["ok"]
