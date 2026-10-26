"""Deterministic decider rules found missing by the v2 benchmark (area A, 2026-10-16). Each test fails on the code before
the fix. Model plans are hand-written in the shape the local decision model returns. Windows or any OS."""
import json

from jarvis.plugins.desktop import decide as D

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
    # D54 (2026-10-26): "save it" after typing is save_as (Save As + file name + Save, verified on disk), not a bare ctrl+s that only opens a dialog
    assert [s["tool"] for s in p.steps] == ["open_app", "type_text", "save_as"] and p.steps[2]["folder"] == "Documents"
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


# ---- bench area A, 2026-10-17: each test fails on the code before the fix (A-0xx = the bench case it guards)
def test_a_research_topic_that_is_only_a_pronoun_asks(): # A-070
    for req, q in (("research it", "it"), ("look that up", "that"), ("dig into this for me", "this")):
        p = v(req, {"tool": "research", "query": q})
        assert p.status == "clarify" and "research" in p.question.lower() and not p.steps, (req, p)
    assert v("research tidal energy", {"tool": "research", "query": "tidal energy"}).status == "ok"


def test_the_other_one_is_vague_not_a_file_or_app_name():  # A-071
    for req, step in (("no, the other file", {"tool": "open_file", "name": "the other file", "folder": "Downloads"}),
                      ("open the other one", {"tool": "open_file", "name": "the other one"}),
                      ("open that one", {"tool": "open_app", "app": "that one"})):
        p = v(req, step)
        assert p.status == "clarify" and not p.steps, (req, p)
    assert v("open the budget file", {"tool": "open_file", "name": "budget"}).status == "ok"


def test_a_choice_offered_with_or_asks_which_one():  # A-072
    p = v("open notepad or wordpad", {"tool": "open_app", "app": "notepad"}, {"tool": "open_app", "app": "wordpad"})
    assert p.status == "clarify" and not p.steps and "notepad" in p.question and "wordpad" in p.question
    p = v("open youtube or maybe gmail", {"tool": "open_website", "site": "youtube"}, {"tool": "open_website", "site": "gmail"})
    assert p.status == "clarify" and not p.steps
    p = v("open notepad and wordpad", {"tool": "open_app", "app": "notepad"}, {"tool": "open_app", "app": "wordpad"})
    assert [s["app"] for s in p.steps] == ["notepad", "wordpad"]                 # "and" still means both


def test_a_negated_action_is_never_planned():  # A-073
    for req, step in (("don't open youtube", {"tool": "open_website", "site": "youtube"}),
                      ("do not play despacito", {"tool": "play_media", "query": "despacito"}),
                      ("never launch paint", {"tool": "open_app", "app": "paint"})):
        p = v(req, step)
        assert p.status != "ok" and not p.steps, (req, p)
    p = v("open gmail but don't open youtube", {"tool": "open_website", "site": "gmail"}, {"tool": "open_website", "site": "youtube"})
    assert p.status == "ok" and [s["url"] for s in p.steps] == [D.SITES["gmail"]] and p.problems


def test_in_sentence_corrections_leave_the_fast_path():  # A-074
    for req in ("play despacito no wait believer", "play shape of you sorry I mean perfect", "play anything except jazz",
                "open notepad no wait wordpad", "play rock or jazz", "don't open youtube"):
        assert D.fast_path(req, PROFILES) is None, req
    assert D.fast_path("play despacito", PROFILES) is not None and D.fast_path("open notepad", PROFILES) is not None


def test_an_app_corrected_in_the_same_sentence_is_dropped():  # A-075
    p = v("open notepad, no wait, open wordpad", {"tool": "open_app", "app": "notepad"}, {"tool": "open_app", "app": "wordpad"})
    assert [s["app"] for s in p.steps] == ["wordpad"]
    p = v("open wordpad instead of notepad", {"tool": "open_app", "app": "wordpad"}, {"tool": "open_app", "app": "notepad"})
    assert [s["app"] for s in p.steps] == ["wordpad"]
    # a correction of another detail never removes the step it does not replace
    p = v("open notepad and type hello, actually make it hi", {"tool": "open_app", "app": "notepad"},
          {"tool": "type_text", "app": "notepad", "text": "hi"})
    assert [s["tool"] for s in p.steps] == ["open_app", "type_text"]


def test_a_negated_account_is_never_used_even_from_the_model():  # A-076
    p = v("open gmail, but not in Satish's profile", {"tool": "open_website", "site": "gmail", "account": "Satish"})
    assert not any(s.get("profile") == "Profile 2" for s in p.steps) and p.status == "clarify"
    p = v("play lofi on youtube, not with VenkatAMV", {"tool": "play_media", "query": "lofi", "account": "VenkatAMV"})
    assert not any(s.get("profile") == "Profile 1" for s in p.steps)


def test_the_owners_document_word_beats_the_models():  # A-083
    p = v("make a report about electric buses", {"tool": "research", "query": "electric buses", "deliverable": "presentation"})
    assert p.steps[0]["deliverable"] == "report"
    p = v("make slides about tides", {"tool": "research", "query": "tides", "deliverable": "report"})
    assert p.steps[0]["deliverable"] == "presentation"


def test_writing_a_report_on_a_topic_is_not_typing_into_an_app():  # A-084
    for req in ("write a report on electric buses", "write an essay on monsoon rains"):
        p = v(req, {"tool": "research", "query": req.split(" on ", 1)[1], "deliverable": "report"})
        assert [s["tool"] for s in p.steps] == ["research"], (req, p.steps)
    p = v("write dear sir on notepad", {"tool": "open_app", "app": "notepad"})      # typing into a real app still works
    assert [s["tool"] for s in p.steps] == ["open_app", "type_text"]


def test_the_owners_resume_beats_the_models_pause():  # A-086
    for req in ("resume the song on youtube", "continue the video", "unpause the music"):
        p = v(req, {"tool": "press_key", "key": "pause"})
        assert p.steps[0]["key"] == "playpause" and p.steps[0]["intent"] == "play", (req, p.steps)
    assert v("pause the song", {"tool": "press_key", "key": "play"}).steps[0]["intent"] == "pause"


def test_wrong_typed_model_fields_fall_back_and_never_crash():  # A-089
    for req, step in (("open notepad", {"tool": "open_app", "app": 42}), ("open notepad", {"tool": ["open_app"], "app": "notepad"}),
                      ("play lofi", {"tool": "play_media", "query": ["lofi"]}),
                      ("open gmail", {"tool": "open_website", "site": "gmail", "account": 7}),
                      ("open notepad", {"tool": {"x": 1}}), ("research tides", {"tool": "research", "query": {"q": "tides"}})):
        p = v(req, step)
        assert p.status in ("fallback", "clarify") and not p.steps, (step, p)


def test_a_request_to_create_a_folder_is_refused_plainly_and_never_asks_the_model():
    """2026-10-21: "make a folder test-live on D:" was mapped to open_file, so the owner heard "no single file matches" for a thing the agent
    cannot do at all. No model call is made (post would raise), the plan has no steps and says what is missing."""
    def post(*a, **k):
        raise AssertionError("the model must not be asked")
    for req in ("make a folder test-live on D:", "Create the folder D:\\test-live", "create a new directory called notes", "mkdir D:\\x",
                "please make me a new folder"):
        p = D.decide(req, PROFILES, post=post)
        assert p.status == "fallback" and not p.steps and "cannot create" in p.problems[0], (req, p)
    # near misses stay ordinary requests (the rule is about creating a folder, not opening or finding one)
    for req in ("open my downloads folder", "open the folder test-live on D:", "find the report folder", "make a presentation about folders"):
        assert not D.MAKE_FOLDER.search(req), req

