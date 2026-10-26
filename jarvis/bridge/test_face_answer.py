"""D59 lever b, the hook: jarvis_task_result carries `summary`, a sentence made from the console's row (never from a model), so the chat model
(or the face, when the model's own last message is empty) has the true final sentence to show."""
from __future__ import annotations

import asyncio

from jarvis.bridge.test_face_tools import FakeClient, tools


def result(rows, cid=""):
    return asyncio.run(tools(FakeClient(rows)).task_result(cid))


def test_answer_is_generic_while_the_reply_is_private(monkeypatch):
    monkeypatch.delenv("JARVIS_BRIDGE_SPEAK_REPLY", raising=False)
    out = result([{"id": "c1", "source": "face", "state": "done", "reply": "Your 3pm with Dr. Rao is moved."}])
    assert out["summary"] == "Done. JARVIS reports it finished." and "Rao" not in str(out["summary"])


def test_answer_carries_the_reply_when_the_owner_allows_speaking_it(monkeypatch):
    monkeypatch.setenv("JARVIS_BRIDGE_SPEAK_REPLY", "1")
    out = result([{"id": "c1", "source": "face", "state": "done", "reply": "Done: a Calculator window appeared."}])
    assert out["summary"] == "Done: a Calculator window appeared."


def test_answer_for_a_running_and_a_failed_task(monkeypatch):
    monkeypatch.delenv("JARVIS_BRIDGE_SPEAK_REPLY", raising=False)
    assert result([{"id": "c1", "source": "face", "state": "running"}])["summary"].startswith("JARVIS is still working")
    assert result([{"id": "c2", "source": "face", "state": "failed", "error": "approval expired"}])["summary"] == "I could not finish that."


def test_a_broken_hook_never_breaks_the_result(monkeypatch):
    import jarvis.plugins._shared.final_answer as fa
    monkeypatch.setattr(fa, "answer_for_task", lambda *a, **k: 1 / 0)
    out = result([{"id": "c1", "source": "face", "state": "done", "reply": "x"}])
    assert out["ok"] is True and out["summary"] == ""


# ---- the language gate hook in jarvis_do (D59 lever c): off unless JARVIS_LANGUAGE_GATE=1
def do(task, client=None):
    c = client or FakeClient()
    return asyncio.run(tools(c).do(task)), c


def test_language_gate_is_off_by_default(monkeypatch):
    monkeypatch.delenv("JARVIS_LANGUAGE_GATE", raising=False)
    out, c = do("Notepad open chesi milk rayandi")
    assert c.calls == [("delegate", "Notepad open chesi milk rayandi", "face")] and out["ok"] is True


def test_confident_tenglish_is_handed_over_as_plain_english(monkeypatch):
    monkeypatch.setenv("JARVIS_LANGUAGE_GATE", "1")
    out, c = do("Notepad open chesi milk, eggs, bread, rice rayandi")
    assert c.calls == [("delegate", "Open Notepad and type: milk, eggs, bread, rice.", "face")] and out["command_id"] == "c1"


def test_english_is_never_touched_by_the_gate(monkeypatch):
    monkeypatch.setenv("JARVIS_LANGUAGE_GATE", "1")
    out, c = do("Open Notepad and write a list")
    assert c.calls == [("delegate", "Open Notepad and write a list", "face")]


def test_a_doubtful_reading_starts_nothing_and_tells_the_model_what_to_ask(monkeypatch):
    monkeypatch.setenv("JARVIS_LANGUAGE_GATE", "1")
    out, c = do("ee file ni tegu")                       # the owner's uncertain word: only ever a question
    assert c.calls == [] and out["asked"] is True and "Nothing was started" in out["message"] and "Did you mean: Delete this file" in out["message"]


def test_a_gate_that_breaks_never_blocks_the_task(monkeypatch):
    monkeypatch.setenv("JARVIS_LANGUAGE_GATE", "1")
    import jarvis.plugins._shared.language_gate as lg
    monkeypatch.setattr(lg, "decide", lambda *a, **k: 1 / 0)
    out, c = do("Notepad open chesi milk rayandi")
    assert len(c.calls) == 1 and out["ok"] is True
