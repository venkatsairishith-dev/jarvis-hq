"""Deterministic decider rules found missing by the v2 benchmark (area A, 2026-10-16). Each test fails on the code before
the fix. Model plans are hand-written in the shape the local decision model returns. Windows or any OS."""
import json

from plugin.plugins.desktop import decide as D

PROFILES = [{"profile": "Default", "name": "Venkat", "account": "ven***", "default": True, "say": ["default", "venkat"]},
            {"profile": "Profile 1", "name": "Venkat", "account": "ris***", "default": False, "say": ["venkat", "venkatamv"]},
            {"profile": "Profile 2", "name": "Satish", "account": "sat***", "default": False, "say": ["satish", "satishk"]}]


def v(req, *steps):
    return D.validate(json.dumps({"steps": list(steps)}), PROFILES, req)


def test_an_account_alias_without_the_word_account_is_not_a_one_command_request():
    # before: "play despacito as VenkatAMV" was planned by rules and searched "despacito as venkatamv" on YouTube
    for req in ("play despacito as VenkatAMV", "play lofi beats in satish", "play kalyani with venkatamv"):
        assert D.fast_path(req, PROFILES) is None, req
    assert D.fast_path("play despacito", PROFILES) is not None


def test_a_corrected_or_negated_account_is_not_the_one_used():
    p = v("open youtube in VenkatAMV, no wait, in Satish's profile", {"tool": "open_website", "site": "youtube", "account": "Satish"})
    assert [s.get("profile") for s in p.steps] == ["Profile 2", "Profile 2"]
    p = v("open gmail in Satish's profile, not VenkatAMV", {"tool": "open_website", "site": "gmail", "account": "Satish"})
    assert [s.get("profile") for s in p.steps] == ["Profile 2", "Profile 2"]
    assert D.account_from_text("use satish instead of venkatamv", PROFILES) == "satish"
    # aliases of one profile are not a conflict, and the longer name still wins
    assert D.account_from_text("open my Venkat AMV account and go to youtube", PROFILES) == "venkatamv"


def test_two_accounts_in_one_request_ask_instead_of_opening_one_in_the_wrong_profile():
    # before: both pages were planned in Profile 1, so gmail opened in VenkatAMV's profile although the owner said Satish
    p = v("open gmail in Satish's profile and youtube in VenkatAMV",
          {"tool": "open_website", "site": "gmail", "account": "Satish"}, {"tool": "open_website", "site": "youtube", "account": "VenkatAMV"})
    assert p.status == "clarify" and "satish" in p.question.lower() and "venkatamv" in p.question.lower()


def test_a_pronoun_app_in_a_later_step_is_the_app_this_plan_opened():
    p = v("open calculator then minimize it", {"tool": "open_app", "app": "calculator"}, {"tool": "window", "action": "minimize", "app": "it"})
    assert p.status == "ok" and p.steps[1]["app"] == "calculator"
    p = v("open notepad and save it", {"tool": "open_app", "app": "notepad"}, {"tool": "press_key", "key": "ctrl+s"})
    assert p.status == "ok" and p.steps[1]["app"] == "notepad"
    assert v("save it", {"tool": "press_key", "key": "ctrl+s"}).status == "clarify"          # nothing earlier: still ask


def test_typing_rules_keep_the_app_name_clean_and_the_order_right():
    for req, app, text in (("write dear sir into wordpad please", "wordpad", "dear sir"), ("write dear sir into the wordpad app", "wordpad", "dear sir"),
                           ("please write dear sir into wordpad.", "wordpad", "dear sir"), ("type hello into notepad for me", "notepad", "hello")):
        p = v(req, {"tool": "open_app", "app": app})
        assert [s["tool"] for s in p.steps] == ["open_app", "type_text"] and p.steps[1]["app"] == app and p.steps[1]["text"] == text, (req, p.steps)
    p = v("open wordpad and write dear sir", {"tool": "open_app", "app": "wordpad"})
    assert [s["tool"] for s in p.steps] == ["open_app", "type_text"] and p.steps[1]["text"] == "dear sir"
    p = v("write dear sir into wordpad and save it", {"tool": "open_app", "app": "wordpad"}, {"tool": "press_key", "app": "wordpad", "key": "ctrl+s"})
    assert [s["tool"] for s in p.steps] == ["open_app", "type_text", "press_key"]
    assert [s["tool"] for s in v("put on some Ilaiyaraaja music on youtube", {"tool": "play_media", "query": "Ilaiyaraaja music"}).steps] == ["play_media"]


def test_a_time_is_not_a_topic_and_a_topic_after_an_article_still_is():
    assert v("generate a deck for tomorrow", {"tool": "research", "query": "tomorrow"}).status == "clarify"
    assert v("make a report for monday", {"tool": "research", "query": "monday"}).status == "clarify"
    assert v("make slides about the moon", {"tool": "research", "query": "the moon"}).status == "ok"


def test_malformed_or_too_long_model_plans_are_reported_not_crashed_on():
    assert D.validate('[{"tool": "open_app", "app": "notepad"}]', PROFILES, "x").status == "fallback"      # before: AttributeError
    p = D.validate(json.dumps({"steps": [{"tool": "open_app", "app": a} for a in ("notepad", "calc", "paint", "vlc", "excel", "word")]}), PROFILES, "x")
    assert len(p.steps) == 4 and any("6 steps" in x for x in p.problems)
    p = D.validate(json.dumps({"steps": ["open notepad", {"tool": "open_app", "app": "notepad"}]}), PROFILES, "open notepad")
    assert [s["tool"] for s in p.steps] == ["open_app"] and p.problems
