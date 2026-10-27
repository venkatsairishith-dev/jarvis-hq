"""D65: the owner talks to JARVIS about the task while it works (the hidden hook behind desktop__quick_command), and steers it by voice."""
from __future__ import annotations

import asyncio
import json

import pytest

from jarvis.plugins.desktop import DesktopPlugin, lessons as L, taskstate as T, taskvoice as V


@pytest.fixture
def paths(tmp_path):
    return {"state_path": tmp_path / "s.json", "control_path": tmp_path / "c.json"}


def write_state(paths, **kw):
    ts = T.TaskState(paths["state_path"], paths["control_path"])
    ts.begin("t1", "notepad")
    ts.update(**kw)
    return ts


@pytest.mark.parametrize("text,kind", [
    ("what are you doing", "doing"), ("What are you doing right now?", "doing"), ("Jarvis, why did you stop?", "why"), ("why did you stop", "why"), ("what went wrong", "wrong"),
    ("what's the problem", "wrong"), ("try another way", "try_another"), ("try a different way", "try_another"), ("skip this step", "skip"), ("stop", "stop"), ("please stop it", "stop"),
    ("what have you learned about Excel", "learned"), ("what have you learned", "learned"), ("forget that", "forget_last"), ("forget everything about excel", "forget_app"),
    ("forget all lessons", "forget_all")])
def test_the_owners_phrases_are_understood_as_whole_phrases(text, kind):
    assert T.parse(text)[0] == kind


@pytest.mark.parametrize("text", ["I will not stop the music", "why did the empire stop expanding", "forget the milk", "stop by the shop later", "what is on my screen", "open notepad", "try another way to cook rice please now and then", ""])
def test_a_sentence_that_only_contains_a_phrase_is_not_a_control(text):
    assert T.parse(text)[0] == ""


def test_what_are_you_doing_is_answered_from_the_live_state_without_touching_the_task(paths):
    ts = write_state(paths, act="type", step=3, phase="act")
    r = V.handle("what are you doing", **paths)
    assert r["handled"] and r["reply"].startswith("I am typing into notepad") and "step 3" in r["reply"]
    assert T.read(paths["state_path"])["state"] == "running" and not T.CONTROLS == (), "nothing was changed or stopped"
    assert ts.controls() == []


def test_why_did_you_stop_and_what_went_wrong_use_the_problem_and_what_was_tried(paths):
    ts = write_state(paths, problem="element_missing", phase="recover")
    ts.tried("wait", "failed")
    ts.tried("scroll", "failed")
    ts.update(state="asking", why="no_progress")
    why = V.handle("why did you stop", **paths)["reply"]
    wrong = V.handle("what went wrong", **paths)["reply"]
    assert "nothing I tried changed anything" in why and "the control I needed" in why
    assert "could not find the control" in wrong and "wait (it did not)" in wrong and "scroll (it did not)" in wrong


def test_the_state_file_and_the_answers_hold_no_typed_text_or_file_names(paths):
    ts = write_state(paths, act="save_as")
    ts.d["secret"] = "x"
    ts.update(app="C:\\Users\\me\\Documents\\bank statement.xlsx")        # not a process name: dropped
    ts.update(act="type password hunter2", problem="a dialog saying delete report.docx")      # not words of the closed lists: dropped
    raw = paths["state_path"].read_text()
    assert "bank" not in raw and "hunter2" not in raw and "report.docx" not in raw and "Users" not in raw
    assert json.loads(raw)["act"] == "" and json.loads(raw)["app"] == ""


def test_try_another_skip_and_stop_are_controls_only_for_a_live_task(paths):
    assert V.handle("try another way", **paths) is None and V.handle("skip this step", **paths) is None and V.handle("stop", **paths) is None, "no task: the message goes on as any other"
    ts = write_state(paths)
    stopped = []
    assert V.handle("try another way", **paths)["reply"] == "Trying another way."
    assert V.handle("skip this step", **paths)["reply"] == "Skipping this step."
    r = V.handle("stop", stop=lambda: stopped.append(1), **paths)
    assert r["reply"] == "Stopping." and stopped == [1]
    assert ts.controls() == ["try_another", "skip", "stop"] and ts.controls() == []


def test_a_task_that_ended_long_ago_is_not_talked_about(paths):
    write_state(paths)
    d = T.read(paths["state_path"])
    assert V.handle("what went wrong", now=d["updated"] + T.RECENT_S + 5, **paths) is None
    assert V.handle("what are you doing", now=d["updated"] + 5, **paths)["handled"]


def test_no_pin_by_voice_a_secret_in_the_message_is_dropped_and_never_kept(paths):
    write_state(paths)
    for text in ("my pin is 4821", "approve it", "the password is hunter2", "otp 123456"):
        r = V.handle(text, **paths)
        assert r["handled"] and "only given in the JARVIS console" in r["reply"] and "4821" not in json.dumps(r)
    assert V.handle("my pin is 4821", state_path=paths["state_path"].parent / "none.json") is None, "with no task, an ordinary chat message is left alone"
    assert not paths["control_path"].exists() or json.loads(paths["control_path"].read_text())["controls"] == []


def test_lessons_by_voice(tmp_path, paths):
    les = L.Lessons(tmp_path / "l.json")
    les.record("excel", "element_missing", "keyboard", True)
    assert "keyboard worked" in V.handle("what have you learned about Excel", lessons=les, **paths)["reply"]
    assert V.handle("forget that", lessons=les, **paths) is None, "no PC task behind it: that is about something else"
    write_state(paths)
    assert V.handle("forget that", lessons=les, **paths)["reply"] == "Forgot the last thing I learned." and les.about() == []
    les.record("excel", "dialog", "answer", True)
    assert "Forgot what I had learned about excel" in V.handle("forget everything about excel", lessons=les, **paths)["reply"]
    assert V.handle("forget everything about Priya", lessons=les, **paths) is None, "a person's name belongs to the names store"


def test_the_hook_is_reached_only_through_the_hidden_tool_and_the_chat_list_stays_small():
    from jarvis.bridge import face_tools as F
    assert "desktop__quick_command" not in F.CHAT_TOOLS
    size = len(json.dumps([{"name": t} for t in F.CHAT_TOOLS]))
    assert size < 7600
    from pathlib import Path
    card = json.loads((Path(F.__file__).resolve().parents[1] / "persona" / "jarvis_card.json").read_text(encoding="utf-8"))
    assert len(json.dumps(card)) > 0


def test_quick_command_answers_the_task_questions_before_anything_else(tmp_path, monkeypatch):
    monkeypatch.setattr(T, "STATE_FILE", tmp_path / "s.json")
    monkeypatch.setattr(T, "CONTROL_FILE", tmp_path / "c.json")
    ts = T.TaskState()
    ts.begin("t", "excel")
    ts.update(act="click", step=2)

    class Host:
        pass
    r = asyncio.run(DesktopPlugin.quick_command(Host(), text="what are you doing"))
    assert r.value["handled"] and "clicking in excel" in r.value["reply"]
    r = asyncio.run(DesktopPlugin.quick_command(Host(), text="what is the capital of France"))
    assert r.value == {"handled": False}
