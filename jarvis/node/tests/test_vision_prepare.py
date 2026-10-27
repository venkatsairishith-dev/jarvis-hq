"""A small or minimised window is made readable before it is read (owner, 2026-10-27: "see what's on the notepad" with a small window)."""
import json
import os
import subprocess
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from jarvis_node import vision  # noqa: E402


def ps(stdout="", code=0):
    return lambda script, env, timeout: subprocess.CompletedProcess([], code, stdout, "")


def setup(monkeypatch, fake):
    monkeypatch.setattr(vision.runtime, "powershell", fake)
    monkeypatch.setattr(vision, "_pick", lambda: "")
    monkeypatch.setattr("jarvis_node.windowpick.pick_env", lambda proc, mutating: {})


def test_an_enlarged_window_is_reported_with_its_sizes(monkeypatch):
    setup(monkeypatch, ps(json.dumps({"changed": True, "enlarged": True, "restored": False, "was": {"w": 320, "h": 200}, "now": {"w": 960, "h": 640}, "x": "ignored"})))
    got = vision.prepare("notepad")
    assert got == {"changed": True, "restored": False, "enlarged": True, "was": {"w": 320, "h": 200}, "now": {"w": 960, "h": 640}}


def test_a_window_that_needs_nothing_is_left_alone(monkeypatch):
    setup(monkeypatch, ps(json.dumps({"changed": False, "restored": False, "enlarged": False})))
    assert vision.prepare("notepad") == {"changed": False}


def test_a_failing_or_garbled_prepare_never_stops_the_read(monkeypatch):
    setup(monkeypatch, ps("", 1))
    assert vision.prepare("notepad") == {"changed": False}
    setup(monkeypatch, ps("not json"))
    assert vision.prepare("notepad") == {"changed": False}

    def boom(*a):
        raise OSError("powershell missing")
    setup(monkeypatch, boom)
    assert vision.prepare("notepad") == {"changed": False}


def test_it_can_be_switched_off(monkeypatch):
    monkeypatch.setenv("JARVIS_AUTO_ENLARGE", "0")
    called = []
    setup(monkeypatch, lambda *a: called.append(a))
    assert vision.prepare("notepad") == {"changed": False} and not called


def test_the_script_only_enlarges_sizable_unzoomed_windows_inside_the_work_area():
    s = vision.PREPARE
    assert "0x00040000" in s and "IsZoomed" in s and "WorkingArea" in s        # sizing border, not maximised, stays on the monitor
    assert "SetForegroundWindow" not in s and "keybd_event" not in s            # no focus steal, no keys


def test_capture_carries_what_was_done_and_look_reports_it(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_OUTPUTS", str(tmp_path / "outputs"))

    def fake(script, env, timeout):
        if "JARVIS_PNG" not in env:
            return subprocess.CompletedProcess([], 0, json.dumps({"changed": True, "enlarged": True, "was": {"w": 300, "h": 200}, "now": {"w": 960, "h": 640}}) + "\n", "")
        with open(env["JARVIS_PNG"], "wb") as fh:
            fh.write(b"\x89PNG\r\n\x1a\n")
        return subprocess.CompletedProcess([], 0, json.dumps({"width": 960, "height": 640, "left": 0, "top": 0, "scale": 2}) + "\n", "")
    setup(monkeypatch, fake)
    monkeypatch.setattr(vision, "ocr", lambda png, scale=1: [{"text": "hello", "words": [{"text": "hello", "x": 1, "y": 1, "w": 5, "h": 5}]}])
    res = vision.look("notepad")
    assert res["prepared"]["enlarged"] is True and res["lines"] == 1
