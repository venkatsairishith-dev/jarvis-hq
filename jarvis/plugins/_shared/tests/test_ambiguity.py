"""D74 (2026-10-27): ASK, DO NOT GUESS. The deterministic gate (plugins/_shared/ambiguity.py over config/ambiguity.json).

Measured on the real laptop (Phase 7): 0 of 15 ambiguous requests were asked about. These tests hold the two numbers that matter:
  * the FALSE-ASK budget is zero: every clear request (114 written here, every example of the simple-task catalogue and every act / pin goal of the reliability gate: about
    300 in all) passes untouched, the same text;
  * the TRUE-ASK rate is every one of 61 ambiguous requests (English, Telugu and Hindi in English letters), each with the right kind of question.
"""
import json
import os
import sys
from pathlib import Path

import pytest

from jarvis.plugins._shared import ambiguity as A

ROOT = Path(__file__).resolve().parents[3]
CASES = json.loads((Path(__file__).with_name("ambiguity_cases.json")).read_text(encoding="utf-8"))
WINDOWS = [{"process": "jarvis", "title": "JARVIS", "foreground": True}, {"process": "notepad", "title": "screen-1.txt - Notepad"},
           {"process": "CalculatorApp", "title": "Calculator"}]


def ctx(**kw):
    base = dict(recent_files=[("a.txt", 10), ("b.pdf", 2048)], windows=WINDOWS, people=["Ravi", "Priya"])
    base.update(kw)
    return A.Context(**base)


def ans(choice=None, other="", button="ok"):
    return {"answered": True, "button": button, "values": {"choice": choice, "other": other}}


def catalogue_and_goal_requests():
    out = []
    for t in json.loads((ROOT / "config" / "simple_tasks.json").read_text(encoding="utf-8"))["tasks"]:
        out += [("catalogue " + t["id"], e["say"]) for e in t.get("examples", [])]
    goals = json.loads((ROOT / "evals" / "reliability" / "goals.json").read_text(encoding="utf-8"))
    for cid, cap in goals["capabilities"].items():
        for g in cap["goals"]:
            if g.get("kind") in ("act", "act_or_ask", "pin"):
                text = g["text"].replace("{work}", "D:\\work").replace("{sep}", "\\").replace("{tok}", "ab12cd").replace("{num}", "12345")
                out.append((f"goal {g['id']}", text))
    return out


# ------------------------------------------------------------------------------------------------------------------------ the two numbers
def test_no_clear_request_is_asked_about():
    asked = [(t, A.check(t, ctx()).rule) for t in CASES["clear"] if A.check(t, ctx())]
    assert len(CASES["clear"]) >= 40
    assert not asked, asked


def test_every_catalogue_example_and_every_act_goal_passes_untouched():
    reqs = [(src, t) for src, t in catalogue_and_goal_requests() if t not in CASES["clear_excluded"]]
    assert len(reqs) >= 250
    asked = [(src, t, A.check(t, ctx()).rule) for src, t in reqs if A.check(t, ctx())]
    assert not asked, asked


def test_the_one_excluded_catalogue_example_is_really_ambiguous():
    for t in CASES["clear_excluded"]:
        v = A.check(t, ctx())
        assert v is not None and v.kind == "reference"


def test_every_ambiguous_request_is_asked_about_with_the_right_kind_of_question():
    amb = CASES["ambiguous"]
    assert len(amb) >= 25 and {c["lang"] for c in amb} == {"en", "te", "hi"}
    wrong = []
    for c in amb:
        v = A.check(c["say"], ctx())
        if v is None or v.kind != c["kind"]:
            wrong.append((c["say"], c["kind"], v and v.kind))
    assert not wrong, wrong
    per = {lang: sum(1 for c in amb if c["lang"] == lang) for lang in ("en", "te", "hi")}
    assert min(per.values()) >= 10, per


def test_the_reliability_goals_that_failed_on_the_laptop_are_asked_now():
    goals = json.loads((ROOT / "evals" / "reliability" / "goals.json").read_text(encoding="utf-8"))["capabilities"]["ambiguous_asks"]["goals"]
    assert len(goals) == 15
    not_asked = [g["text"] for g in goals if A.check(g["text"].replace("{work}", "D:\\work").replace("{sep}", "\\").replace("{tok}", "ab12cd"), ctx()) is None]
    assert not_asked == []


def test_asking_does_not_depend_on_the_context_being_there():
    """No windows, no files, no people known: still a question (a text box), never a guess."""
    empty = A.Context(recent_files=[], windows=None, people=[])
    for c in CASES["ambiguous"]:
        assert A.check(c["say"], empty) is not None, c["say"]


def test_a_broken_context_never_stops_an_ordinary_request():
    class Boom(A.Context):
        @property
        def windows(self):  # noqa: D401
            raise RuntimeError("no")

        @windows.setter
        def windows(self, v):
            pass
    assert A.check("open notepad", Boom()) is None
    assert A.check(None) is None and A.check("") is None and A.check("   ") is None


# ------------------------------------------------------------------------------------------------------------------------ what each rule does
def test_either_or_names_the_two_and_replaces_exactly_them():
    v = A.check("Open notepad or wordpad.", ctx())
    assert v.kind == "either_or" and v.question == "Which one: Notepad or WordPad?" and [o.label for o in v.options] == ["Notepad", "WordPad"]
    assert A.resolve(v, ans("o2")) == ["Open WordPad."]
    assert A.resolve(v, ans("", "Paint")) == ["Open Paint."]                  # the typed 'Something else' wins over the buttons
    assert A.resolve(A.check("Notepad leda Wordpad teruvu", ctx()), ans("o1")) == ["Notepad teruvu"]


def test_an_or_inside_content_or_a_question_is_not_a_choice():
    for t in ("search for cats or dogs", "type yes or no in notepad", "open notepad and type 'a or b'", "is it raining or sunny in paris?", "answer yes or no",
              "who is the president or prime minister of India?", "I like tea or coffee"):
        assert A.check(t, ctx()) is None, t


def test_a_delete_needs_a_named_object_and_a_named_one_passes():
    for t in ("delete the old file", "delete this", "delete everything", "delete the old stuff", "delete the file", "purani files delete karo"):
        assert A.check(t, ctx()).kind == "destructive", t
    for t in ("delete test.txt in Downloads", "delete the file report.docx", "remove the folder Old Notes from Desktop", "get rid of draft.docx", "delete everything in D:\\work\\x",
              "delete the oldest file in D:\\work\\age-1", "delete C:\\Users\\me\\a.txt"):
        assert A.check(t, ctx()) is None, t


def test_a_destructive_request_is_never_settled_by_the_last_task():
    last = {"file": "D:\\out\\deck.pptx", "status": "completed"}
    assert A.check("open it", ctx(last=last)) is None                       # an 'open it' after a task that made a file: decide.follow_up resolves it
    for t in ("delete it", "send it to him", "rename it", "move it to the folder", "save it"):
        assert A.check(t, ctx(last=last)) is not None, t


def test_a_pronoun_is_settled_by_what_the_request_itself_names_or_makes():
    for t in ("make a deck about tea and show it to me", "type hello in notepad and save it", "open notepad and close it", "take a screenshot and save it",
              "write a note: buy milk and save it as milk.txt"):
        assert A.check(t, ctx()) is None, t
    # but a name that is only the PLACE or the tool is not what 'it' stands for
    for t in ("copy it to the desktop", "open it in notepad", "move that to documents", "delete it from downloads"):
        assert A.check(t, ctx()) is not None, t


def test_a_vague_reference_in_time_is_asked_with_the_files_of_that_day():
    v = A.check("Open the file I was working on yesterday.", ctx())
    assert v.kind == "reference" and v.file_filter == {"day": "yesterday"} and v.source == "files"
    assert A.resolve(v, ans("C:\\Users\\me\\Documents\\p.docx")) == ["Open C:\\Users\\me\\Documents\\p.docx."]      # the whole description is replaced, not left behind
    assert A.check("Open yesterday's file", ctx()) is not None
    assert A.check("kal wali file kholo", ctx()).file_filter == {"day": "yesterday"}


def test_the_missing_part_of_a_request_is_what_is_asked():
    cases = {"send the report": "Who should I send it to?", "email the report": "Who should I send it to?", "move report.docx": "Which folder should it go to?",
             "rename report.docx": "What should it be called?", "make a pdf": "What should it be about?", "remind me to drink water": "When should I remind you?",
             "play something": "What should I play?", "kuch bajao": "What should I play?", "pdf banao": "What should it be about?"}
    for t, q in cases.items():
        v = A.check(t, ctx())
        assert v is not None and v.kind == "slot" and v.question == q, (t, v and v.question)
    assert A.resolve(A.check("Send the report", ctx()), ans("o1")) == ["Send the report to Ravi"]
    assert A.resolve(A.check("Move report.docx", ctx()), ans("o2")) == ["Move report.docx to Desktop"]
    assert A.resolve(A.check("Remind me to drink water", ctx()), ans("", "in 20 minutes")) == ["Remind me in 20 minutes to drink water"]
    assert A.resolve(A.check("Make a pdf.", ctx()), ans("", "tea")) == ["Make a pdf about tea"]
    assert A.resolve(A.check("Play something.", ctx()), ans("", "despacito")) == ["Play despacito."]
    for t in ("send hello to ravi", "send it to ravi@example.com", "move a.txt to documents", "rename a.txt to b.txt", "make a pdf about tea", "remind me at 5 pm to call mom",
              "play a relaxing song", "play despacito", "play music on spotify"):
        assert A.check(t, ctx()) is None or A.check(t, ctx()).kind == "reference", t


def test_typed_text_is_content_and_never_read_for_words():
    assert A.check("open notepad and type 'delete it or close it'", ctx()) is None
    assert A.check('write "send it to him" in notepad', ctx()) is None


# ------------------------------------------------------------------------------------------------------------------------ closing windows
def test_close_the_window_never_offers_jarvis_own_window():
    v = A.check("Close the window.", ctx())
    assert v.kind == "close" and [o.label for o in v.options] == ["Notepad", "Calculator"]
    assert all("jarvis" not in o.label.lower() and "jarvis" not in o.detail.lower() for o in v.options)
    assert A.resolve(v, ans("o1")) == ["Close Notepad"]


def test_when_the_only_window_is_jarvis_own_the_question_is_a_text_box():
    only_mine = [{"process": "jarvis", "title": "JARVIS", "foreground": True}, {"process": "chrome", "title": "JARVIS - Google Chrome"}]
    v = A.check("close this", ctx(windows=only_mine))
    assert v.kind == "close" and v.options == [] and v.text_required
    spec = A.spec(v)
    assert [f["id"] for f in spec["fields"]] == ["other"] and spec["fields"][0]["required"] is True


def test_close_everything_is_a_tick_list_and_runs_one_close_per_ticked_window():
    v = A.check("close everything", ctx())
    assert v.multiple and A.spec(v)["fields"][0]["type"] == "checkbox_group"
    assert A.resolve(v, ans(["o1", "o2"])) == ["Close Notepad", "Close Calculator"]
    assert A.resolve(v, ans([])) == []


def test_a_window_jarvis_opened_is_what_close_it_means():
    v = A.check("close it", ctx(launched=["notepad"]))
    assert v.auto_text == "Close Notepad" and v.rule == "close_opened"
    v2 = A.check("close it", ctx(launched=["notepad", "calculatorapp"]))
    assert not v2.auto_text and v2.options                                    # two of them: ask
    assert not A.check("close everything", ctx(launched=["notepad"])).auto_text         # 'everything' is never decided for him


def test_naming_the_app_is_a_plain_close():
    for t in ("close notepad", "close the calculator", "Notepad band karo", "Notepad ni muyyi", "open notepad and close it"):
        assert A.check(t, ctx()) is None, t


def test_own_window_parity_with_the_kernel_list():
    sys.path.insert(0, str(ROOT / "kernel"))
    try:
        from jarvis_kernel import policy
    except Exception:
        pytest.skip("the kernel package is not importable here")
    for proc, title in (("jarvis", ""), ("Jarvis.exe", ""), ("electron", ""), ("chrome", "JARVIS - Google Chrome"), ("notepad", "JARVIS notes.txt - Notepad"), ("chrome", "Gmail"), ("notepad", "")):
        assert bool(A.own_window(proc, title)) == bool(policy.own_window(proc, title)), (proc, title)


# ------------------------------------------------------------------------------------------------------------------------ the forms
def test_the_form_for_the_chat_model_has_the_question_and_generic_labels_only():
    v = A.check("Delete the old file.", ctx())
    f = A.form(v)
    assert f["kind"] == "jarvis_ask" and f["question"] == "Which one should I delete?" and f["allow_other"] is True
    assert "a.txt" not in json.dumps(f) and "b.pdf" not in json.dumps(f)
    v = A.check("Close the window.", ctx())
    assert "screen-1" not in json.dumps(A.form(v)) and "Calculator" not in json.dumps(A.form(v)["options"])         # titles and apps of the owner's screen stay on his panel
    v = A.check("Send the report", ctx())
    assert "Ravi" not in json.dumps(A.form(v))                                                                      # people's names too
    v = A.check("Open notepad or wordpad", ctx())
    assert [o["label"] for o in A.form(v)["options"]] == ["Notepad", "WordPad"] and all("value" not in o for o in A.form(v)["options"])      # his own words are fine


def test_the_panel_form_is_a_valid_jarvis_ask_spec_with_a_something_else_box():
    from jarvis.bridge import ask_spec
    for c in CASES["ambiguous"]:
        v = A.check(c["say"], ctx())
        spec = ask_spec.clean_spec(A.spec(v))
        ids = [f["id"] for f in spec["fields"]]
        assert "other" in ids, c
        assert [b["id"] for b in spec["buttons"]] == ["ok", "cancel"]
        assert not spec.get("notes") or all("simplified" not in n for n in spec["notes"]), (c, spec.get("notes"))
    spec = A.spec(A.check("Open the file I was working on yesterday.", ctx()))
    assert spec["fields"][0]["type"] == "file_choice" and spec["fields"][0]["filter"] == {"day": "yesterday"}


def test_a_file_list_is_filtered_by_the_backend_never_by_the_caller(tmp_path):
    from jarvis.bridge import ask_spec
    import time
    for d in ("Downloads", "Documents", "Desktop"):
        (tmp_path / d).mkdir()
    (tmp_path / "Downloads" / "old-notes.txt").write_text("a")
    (tmp_path / "Downloads" / "fresh.txt").write_text("b")
    yest = time.time() - 86400
    os.utime(tmp_path / "Downloads" / "old-notes.txt", (yest, yest))
    spec = {"title": "t", "fields": [{"id": "f", "type": "file_choice", "source": "recent", "filter": {"name_like": ["old"]}}], "buttons": [{"id": "ok", "label": "ok", "style": "primary"}]}
    c = ask_spec.clean_spec(spec, tmp_path)
    assert [o["label"] for o in c["fields"][0]["options"]] == ["old-notes.txt"]
    spec["fields"][0]["filter"] = {"day": "yesterday"}
    assert [o["label"] for o in ask_spec.clean_spec(spec, tmp_path)["fields"][0]["options"]] == ["old-notes.txt"]
    spec["fields"][0]["filter"] = {"name_like": ["nothing-like-this"]}
    c = ask_spec.clean_spec(spec, tmp_path)
    assert {o["label"] for o in c["fields"][0]["options"]} == {"old-notes.txt", "fresh.txt"} and c["notes"]       # nothing matched: the newest, and it says so
    spec["fields"][0]["options"] = [{"id": "x", "label": "C:\\Windows\\system32\\cmd.exe"}]                         # a caller's own options are still ignored
    assert "cmd.exe" not in json.dumps(ask_spec.clean_spec(spec, tmp_path))


def test_recent_files_are_names_and_sizes_only_and_skip_secret_looking_names(tmp_path):
    for d in ("Downloads", "Documents", "Desktop"):
        (tmp_path / d).mkdir()
    (tmp_path / "Downloads" / "report.pdf").write_text("x" * 10)
    (tmp_path / "Downloads" / "passwords.txt").write_text("x")
    (tmp_path / "Downloads" / ".hidden").write_text("x")
    (tmp_path / "Desktop" / "notes.txt").write_text("x" * 3)
    got = A.recent_files(tmp_path)
    assert sorted(got) == [("notes.txt", 3), ("report.pdf", 10)]


# ------------------------------------------------------------------------------------------------------------------------ the decider's ask
def test_the_deciders_question_is_the_same_kind_of_form():
    v = A.clarify_verdict("Which one: notepad or wordpad?", "open that")
    assert v.kind == "clarify" and [o.label for o in v.options] == ["Notepad", "WordPad"]
    assert A.resolve(v, ans("o1")) == ["open that\n(The owner's clarification: Notepad)"]
    f = A.form(A.clarify_verdict("Which file?", "open that", ctx()))
    assert f["kind"] == "jarvis_ask" and f["question"] == "Which file?"
    t = A.clarify_verdict("What should the deck be about?", "make a deck")
    assert A.resolve(t, ans("", "solar power")) == ["make a deck\n(The owner's clarification: solar power)"] and t.text_required
    assert A.clarify_verdict("", "x").question                                  # never an empty question


def test_no_answer_means_no_request():
    v = A.check("Open notepad or wordpad.", ctx())
    for a in (None, {}, {"answered": False, "reason": "timeout"}, ans("o1", button="cancel"), ans("", ""), ans(None, ""), ans("zzz")):
        assert A.resolve(v, a) == [], a


# ------------------------------------------------------------------------------------------------------------------------ the rules are data
def test_the_words_are_data_and_a_new_word_needs_no_code(tmp_path):
    data = json.loads((ROOT / "config" / "ambiguity.json").read_text(encoding="utf-8"))
    data["or_words"].append("ellaa")
    data["verbs"]["open"].append("tereyiri")
    f = tmp_path / "ambiguity.json"
    f.write_text(json.dumps(data), encoding="utf-8")
    assert A.check("Notepad ellaa Wordpad tereyiri", ctx()) is None
    r = A.load(f)
    assert r.source == "file" and A.check("Notepad ellaa Wordpad tereyiri", ctx(), r).kind == "either_or"


def test_a_missing_or_broken_file_falls_back_to_the_built_in_floor_not_to_guessing(tmp_path):
    for path in (tmp_path / "missing.json", ):
        r = A.load(path)
        assert r.source == "floor" and A.check("open notepad or wordpad", ctx(), r).kind == "either_or" and A.check("delete this", ctx(), r) is not None
    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    r = A.load(bad)
    assert r.source == "floor" and A.check("open it", ctx(), r) is not None
    odd = tmp_path / "odd.json"
    odd.write_text(json.dumps({"or_words": "not a list", "verbs": 5, "slots": [{"id": "x"}, 7], "pronouns": []}), encoding="utf-8")
    r = A.load(odd)
    assert A.check("open notepad or wordpad", ctx(), r).kind == "either_or"


def test_the_owner_can_turn_the_gate_off_in_the_file_only(tmp_path, monkeypatch):
    data = json.loads((ROOT / "config" / "ambiguity.json").read_text(encoding="utf-8"))
    data["enabled"] = False
    f = tmp_path / "ambiguity.json"
    f.write_text(json.dumps(data), encoding="utf-8")
    assert A.check("open it", ctx(), A.load(f)) is None


def test_needs_asks_only_for_what_the_request_can_use():
    assert A.needs("close the window") >= {"windows", "launched"}
    assert A.needs("open it") == {"last"}
    assert A.needs("open notepad") == set()


def test_the_shipped_file_is_valid_and_complete():
    r = A.load()
    assert r.source == "file" and r.enabled
    d = json.loads((ROOT / "config" / "ambiguity.json").read_text(encoding="utf-8"))
    for key in ("or_words", "verbs", "pronouns", "slots", "questions", "vague_nouns", "temporal", "past_reference", "close"):
        assert d[key], key
    for lang_word in ("leda", "ya", "kholo", "teruvu", "pampu", "bhejo", "ninna", "kal"):
        assert any(lang_word in json.dumps(d[k]) for k in ("or_words", "verbs", "temporal")), lang_word
    for s in d["slots"]:
        assert s["question"] and s["verbs"] and set(s["verbs"]) <= set(d["verbs"]), s["id"]
