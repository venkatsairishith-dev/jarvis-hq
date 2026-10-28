"""D75: jarvis_do (the face's door to the JARVIS console) ANSWERS a plain question on this PC instead of handing the console a task. The chat tool list and the persona do not
change: only what the existing tool does with such a question."""
import asyncio

import pytest

from jarvis.bridge import face_tools as F

from .test_face_tools import CONSOLE, FakeClient


def tools(client):
    return F.FaceTools(client_factory=lambda: client, console=CONSOLE)


@pytest.mark.parametrize("task,said", [
    ("What is 23 times 47?", "23 times 47 is 1,081."),
    ("15% of 2400", "15% of 2,400 is 360."),
    ("convert 5 km to miles", "5 km is about 3.1069 miles"),
    ("23 guna 47", "23 times 47 is 1,081."),
])
def test_a_plain_question_is_answered_and_nothing_goes_to_the_console(task, said):
    c = FakeClient()
    out = asyncio.run(tools(c).do(task))
    assert out["ok"] is True and out["answered"] is True and said in out["message"] and "nothing was started" in out["message"]
    assert c.calls == [], "no console task, so no kernel job"


@pytest.mark.parametrize("task", ["Open Calculator and work out 15 plus 27", "calculate 12 times 7 in Calculator", "show me 23 times 47 on screen", "open notepad", "what is the capital of France"])
def test_anything_else_still_goes_to_the_console(task):
    c = FakeClient()
    out = asyncio.run(tools(c).do(task))
    assert out.get("answered") is None
    assert c.calls, task


def test_the_switches(monkeypatch):
    monkeypatch.setenv("JARVIS_DIRECT_ANSWERS", "0")
    assert F.FaceTools.direct_answer("23 times 47") == ""
    monkeypatch.delenv("JARVIS_DIRECT_ANSWERS")
    monkeypatch.setenv("JARVIS_QUICK_PATH", "0")
    assert F.FaceTools.direct_answer("23 times 47") == ""
    monkeypatch.delenv("JARVIS_QUICK_PATH")
    assert F.FaceTools.direct_answer("23 times 47") == "23 times 47 is 1,081."
