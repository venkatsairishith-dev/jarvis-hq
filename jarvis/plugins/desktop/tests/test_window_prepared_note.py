"""The line that tells the owner JARVIS restored or enlarged his window to read it."""
from jarvis.plugins.desktop import core


def test_nothing_done_says_nothing():
    assert core.window_prepared_note(None, "notepad") == ""
    assert core.window_prepared_note({"changed": False}, "notepad") == ""
    assert core.window_prepared_note("x", "notepad") == ""


def test_enlarged_says_both_sizes():
    n = core.window_prepared_note({"changed": True, "enlarged": True, "was": {"w": 320, "h": 200}, "now": {"w": 960, "h": 640}}, "notepad")
    assert "notepad window was small" in n and "320x200 to 960x640" in n and "stays that way" in n


def test_restored_from_minimised():
    n = core.window_prepared_note({"changed": True, "restored": True}, "notepad")
    assert "minimised" in n and "brought it back" in n
