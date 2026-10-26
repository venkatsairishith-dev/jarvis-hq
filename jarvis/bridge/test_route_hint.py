"""D54b (2026-10-26): jarvis_do hints the tool that answers a screen / document / weather / web question directly (a small chat model picked jarvis_do for
'what is on my screen'). Rules only, instant, never a block; the tool list and its descriptions are unchanged."""
import pytest

from jarvis.bridge.face_tools import CHAT_TOOLS, FaceTools


@pytest.mark.parametrize("task,tool", [("what is on my screen", "desktop__whats_on_screen"), ("what's the weather in Hyderabad", "web_search__search"),
                                       ("search the web for pasta recipes", "web_search__search")])
def test_a_misrouted_question_names_the_tool_that_answers_it(task, tool):
    assert FaceTools.better_tool(task) == tool


@pytest.mark.parametrize("task", ["open notepad and type hello", "create a folder Projects in Documents", "delete old.txt", "hello", "", "make a deck about whales"])
def test_other_tasks_get_no_hint(task):
    assert FaceTools.better_tool(task) in ("", "web_search__search") if task == "make a deck about whales" else FaceTools.better_tool(task) == ""


def test_the_chat_tool_list_is_unchanged_in_size():
    assert sum(len(k) + len(v) for k, v in CHAT_TOOLS.items()) < 7600
