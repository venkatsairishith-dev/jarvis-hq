import json

from jarvis.plugins._shared import ask_form as af


def test_build_form_cleans_and_limits():
    f = af.build_form("  Which   one?  ", ["Notepad", "notepad", "WordPad", "", "A", "B", "C", "D", "E"], reason="r", source="x")
    assert f["kind"] == "jarvis_ask" and f["question"] == "Which one?"
    assert [o["label"] for o in f["options"]] == ["Notepad", "WordPad", "A", "B", "C", "D"]      # duplicate dropped, six at most
    assert [o["id"] for o in f["options"]] == ["o1", "o2", "o3", "o4", "o5", "o6"]
    assert af.is_form(f, 2)


def test_form_with_too_few_options_always_allows_free_text():
    f = af.build_form("What?", [], allow_other=False)
    assert f["options"] == [] and f["allow_other"] is True and not af.is_form(f, 2) and af.is_form(f)


def test_is_form_rejects_lookalikes():
    assert not af.is_form({"kind": "other", "question": "x", "options": [1, 2]})
    assert not af.is_form({"kind": "jarvis_ask", "question": " ", "options": [1, 2]})
    assert not af.is_form("jarvis_ask")


def test_find_form_in_nested_results_and_json_strings():
    f = af.did_you_mean(["Open Notepad"])
    assert af.find_form({"a": [{"b": f}]}) == f
    assert af.find_form({"content": json.dumps(f)}) == f
    assert af.find_form("not json jarvis_ask") is None


def test_did_you_mean_wording():
    one = af.did_you_mean(["Delete the file report.txt"])
    assert one["question"] == "Did you mean: Delete the file report.txt?"
    two = af.did_you_mean(["Open Notepad", "Open WordPad"])
    assert two["question"] == "Which did you mean?" and len(two["options"]) == 2
