"""D74 (2026-10-27): the node never closes JARVIS's own window. The layer where the window is really picked (process AND title are known here)."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from jarvis_node import jobs, ownwin  # noqa: E402


def lister(*rows):
    return lambda: [dict(process=p, title=t, foreground=False, minimized=False, w=800, h=600) for p, t in rows]


class Backend(dict):
    """A pretend backend: records what the node let through."""
    def __init__(self):
        super().__init__()
        self.ran = []
        self["close"] = lambda target, params: (self.ran.append(("close", target)), {"closed": True, "observed": "the window closed"})[1]
        self["key"] = lambda target, params: (self.ran.append(("key", target, params.get("text"))), None)[1]
        self["minimize"] = lambda target, params: (self.ran.append(("minimize", target)), None)[1]


def test_a_close_aimed_at_the_face_never_reaches_the_backend():
    be = Backend()
    for target in ("jarvis|", "jarvis.exe|", "Electron|", "jarvisface|"):
        out = jobs.ui_action({"op": "close", "target": target, "text": ""}, backend=be, lister=lister(("jarvis", "JARVIS")))
        assert out["ok"] is False and "own windows" in out["error"], (target, out)
    assert be.ran == []


def test_a_close_chord_aimed_at_the_face_is_refused_too():
    be = Backend()
    for chord in ("alt+f4", "ctrl+w", "ctrl+q"):
        out = jobs.ui_action({"op": "key", "target": "jarvis|", "text": chord}, backend=be, lister=lister(("jarvis", "JARVIS")))
        assert out["ok"] is False, chord
    assert be.ran == []


def test_the_consoles_browser_tab_is_refused_when_it_is_the_only_window_of_the_browser():
    be = Backend()
    out = jobs.ui_action({"op": "close", "target": "chrome|", "text": ""}, backend=be, lister=lister(("chrome", "JARVIS - Google Chrome")))
    assert out["ok"] is False and be.ran == []


def test_a_browser_with_other_windows_is_left_to_the_window_picker():
    be = Backend()
    lst = lister(("chrome", "JARVIS - Google Chrome"), ("chrome", "Gmail - Google Chrome"))
    out = jobs.ui_action({"op": "close", "target": "chrome|", "text": ""}, backend=be, lister=lst)
    assert out["ok"] is True and be.ran == [("close", "chrome|")]


def test_an_ordinary_close_and_every_other_op_are_unchanged():
    be = Backend()
    assert jobs.ui_action({"op": "close", "target": "notepad|", "text": ""}, backend=be, lister=lister(("notepad", "a.txt - Notepad")))["ok"] is True
    assert jobs.ui_action({"op": "minimize", "target": "jarvis|", "text": ""}, backend=be, lister=lister(("jarvis", "JARVIS")))["ok"] is True
    assert jobs.ui_action({"op": "key", "target": "notepad|", "text": "alt+f4"}, backend=be, lister=lister(("notepad", "a.txt")))["ok"] is True
    assert [r[0] for r in be.ran] == ["close", "minimize", "key"]


def test_a_notepad_document_that_starts_with_the_word_is_not_the_console():
    assert ownwin.why("notepad", "JARVIS notes.txt - Notepad") == ""
    assert ownwin.why("chrome", "Gmail - Google Chrome") == ""
    assert ownwin.why("jarvis", "anything")


def test_the_nodes_floor_still_refuses_without_the_kernel_package(monkeypatch):
    monkeypatch.setattr(ownwin, "_kernel_check", None)
    assert ownwin.why("jarvis")
    assert ownwin.why("chrome", "JARVIS console - Google Chrome")
    assert ownwin.refuse_close("close", "electron|", "", lister(("electron", "JARVIS")))
    assert not ownwin.refuse_close("close", "notepad|", "", lister(("notepad", "x")))
