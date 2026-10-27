"""D76: smaller guarantees of the task path: owner-written paths are not guessed out of other words, the brief is written by the LOCAL model first (the pages' text stays on this PC),
the online route needs the owner's switch, and safe mode turns the whole path off."""
import re

import pytest

from jarvis.plugins.desktop import taskplan as TP
from jarvis.plugins.desktop import taskroute
from jarvis.plugins.desktop.tests.taskharness import TaskHarness
from jarvis.plugins.desktop.tests.test_task_flows import _results, all_passed, good_brief


@pytest.fixture
def th(tmp_path, monkeypatch):
    h = TaskHarness(tmp_path, monkeypatch)
    yield h
    h.close()


def test_a_slash_between_words_is_not_a_path():
    assert TP.path_in("make a CSV of vendor/date/total") == ""
    assert TP.path_in("read /home/u/Documents/inv now") == "/home/u/Documents/inv"
    assert TP.path_in('read "C:\\Users\\x\\Documents\\my files" please').startswith("C:\\Users")
    assert TP.path_in(r"read C:\Users\x\Documents\inv.") == r"C:\Users\x\Documents\inv"


class _Rel:
    def __init__(self, text, allowed=True):
        self.text, self.allowed = text, allowed


def test_the_local_model_writes_the_brief_first_and_the_online_model_is_not_asked(th):
    th.search_results = _results()
    online_calls = []
    seen = []

    def local(messages):
        seen.append(messages[-1]["content"])
        return good_brief(messages), "local"
    got = th.run_plan("look up three sources on solar power for schools and write a one-page brief and show it",
                      runner=th.runner(release_local=lambda t: _Rel(t), write_local=local, ask_reason=lambda m: online_calls.append(m) or ("x", "online"),
                                       release_text=lambda t: _Rel(t)))
    assert all_passed(got) and "the local model on this PC" in got[-1]["res"]["facts"][0] and not online_calls
    assert seen and "solar power" in seen[0]


def test_when_the_local_brief_breaks_the_rules_the_online_route_is_tried_only_if_cleared_and_then_the_short_phrases(th):
    th.search_results = _results()
    bad = lambda m: ("# Brief\n\nNo sources cited at all.", "local")
    online = []
    got = th.run_plan("look up three sources on solar power for schools and write a one-page brief and show it",
                      runner=th.runner(release_local=lambda t: _Rel(t), write_local=bad, release_text=lambda t: _Rel("", allowed=False),
                                       ask_reason=lambda m: online.append(m) or ("x", "online")))
    assert all_passed(got) and not online                                       # an excerpt the gateway or the switch did not clear never goes online
    assert "no model" in got[-1]["res"]["facts"][0]


def test_safe_mode_turns_the_task_path_off(monkeypatch):
    assert taskroute.enabled() is True
    monkeypatch.setenv("JARVIS_SAFE_MODE", "1")
    assert taskroute.enabled() is False
    monkeypatch.delenv("JARVIS_SAFE_MODE")
    monkeypatch.setenv("JARVIS_TASK_PATH", "0")
    assert taskroute.enabled() is False
