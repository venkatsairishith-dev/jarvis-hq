"""D74: jarvis_do (the face's door to the JARVIS console) reads the owner's words with the ask-do-not-guess rules BEFORE the language gate rewrites them, and hands nothing
over when a question is due. Same rules and same file as the desktop plugin (plugins/_shared/ambiguity.py, config/ambiguity.json)."""
import asyncio

from jarvis.bridge import face_tools as F
from jarvis.plugins._shared import ambiguity, ask_form

from .test_face_tools import CONSOLE, FakeClient


def tools(client):
    return F.FaceTools(client_factory=lambda: client, console=CONSOLE)


def test_an_ambiguous_task_is_not_handed_to_the_console_and_comes_back_as_a_form():
    for task in ("Delete the old file.", "Open notepad or wordpad.", "Send it to him.", "Make a pdf."):
        c = FakeClient()
        out = asyncio.run(tools(c).do(task))
        assert out["ok"] is True and out["asked"] is True and c.calls == [], task
        assert ask_form.is_form(out["form"]) and "Nothing was started" in out["message"] and "jarvis_ask" in out["message"]


def test_the_form_for_the_chat_model_carries_no_file_names(tmp_path, monkeypatch):
    monkeypatch.setattr(ambiguity, "recent_files", lambda *a, **k: [("secret-plan.docx", 10)])
    out = asyncio.run(tools(FakeClient()).do("Delete the old file."))
    assert "secret-plan" not in str(out)


def test_the_choice_between_two_apps_names_them_for_the_owner():
    out = asyncio.run(tools(FakeClient()).do("Open notepad or wordpad."))
    assert "Notepad / WordPad" in out["message"] and [o["label"] for o in out["form"]["options"]] == ["Notepad", "WordPad"]


def test_a_clear_task_goes_through_untouched():
    c = FakeClient()
    out = asyncio.run(tools(c).do("open my calendar"))
    assert c.calls == [("delegate", "open my calendar", "face")] and out["ok"] and "asked" not in out


def test_the_rules_run_before_the_language_gate_rewrites_the_words(monkeypatch):
    """With the language gate ON, 'Notepad leda Wordpad teruvu' became 'Open Notepad.' (the second choice was dropped). The rules see the owner's own words first."""
    monkeypatch.setenv("JARVIS_LANGUAGE_GATE", "1")
    c = FakeClient()
    out = asyncio.run(tools(c).do("Notepad leda Wordpad teruvu"))
    assert out["asked"] is True and c.calls == []


def test_the_gate_can_be_switched_off_for_one_run(monkeypatch):
    monkeypatch.setenv("JARVIS_AMBIGUITY_GATE", "0")
    c = FakeClient()
    asyncio.run(tools(c).do("Delete the old file."))
    assert c.calls and c.calls[0][0] == "delegate"
