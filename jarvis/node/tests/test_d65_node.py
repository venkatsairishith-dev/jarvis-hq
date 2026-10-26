"""D65, the node side of the human-like PC control loop: what the loop needs to SEE (hung / other-desktop windows, the owner's input state), the fixed reason code of a failed
action, the vision rung of the screen cascade, the three new desktop-only keys, and the human feel of the pointer and the typing (checked in the scripts themselves, which
only Windows can run)."""
import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))
sys.path.insert(0, os.path.join(HERE, "..", "..", "kernel"))

from jarvis_node import diag, jobs, vision, winbackend, windows  # noqa: E402


# ------------------------------------------------------------------------------------------------------------------------------ what the loop sees
def lister():
    return [{"hwnd": 11, "pid": 1, "process": "notepad", "title": "a - Notepad", "foreground": True, "minimized": False, "w": 800, "h": 600, "hung": False, "cloaked": False},
            {"hwnd": 12, "pid": 2, "process": "excel", "title": "b - Excel (Not Responding)", "foreground": False, "minimized": False, "w": 800, "h": 600, "hung": True, "cloaked": False},
            {"hwnd": 13, "pid": 3, "process": "winword", "title": "c - Word", "foreground": False, "minimized": False, "w": 800, "h": 600, "cloaked": True},
            {"hwnd": 14, "pid": 4, "process": "calc", "title": "Calculator", "foreground": False, "minimized": True, "w": 800, "h": 600}]


def test_the_window_list_says_which_window_is_hung_or_on_another_desktop_and_when_the_owner_last_touched_the_pc():
    out = jobs.ui_windows({}, lister=lister, input_state=lambda: {"idle_ms": 1500, "cursor": [30, 40]})
    by = {w["process"]: w for w in out["windows"]}
    assert by["excel"]["hung"] is True and by["notepad"]["hung"] is False and by["winword"]["cloaked"] is True and by["winword"].get("hung") is None
    assert "hung" not in by["calc"] and "cloaked" not in by["calc"], "an older lister that does not know them adds nothing"
    assert out["idle_ms"] == 1500 and out["cursor"] == [30, 40]


def test_an_unreadable_input_state_is_not_a_failed_listing():
    for bad in (lambda: {}, lambda: (_ for _ in ()).throw(OSError("x")), lambda: {"idle_ms": "soon", "cursor": "here"}):
        out = jobs.ui_windows({}, lister=lister, input_state=bad)
        assert out["ok"] and "idle_ms" not in out and "cursor" not in out


def test_the_input_state_is_empty_off_windows():
    if os.name != "nt":
        assert windows.input_state() == {}


# ------------------------------------------------------------------------------------------------------------------------------ the reason code
class Boom:
    def __init__(self, exc):
        self.exc = exc

    def __getitem__(self, k):
        def f(target, params):
            raise self.exc
        return f

    def __contains__(self, k):
        return True


@pytest.mark.parametrize("message,reason", [("ui-action-failed:no element", "no element"), ("ui-action-failed:no window", "no window"),
                                            ("ui-action-failed:target is not the foreground window", "target is not the foreground window"),
                                            ("ui-action-failed:text not in field", "text not in field")])
def test_a_failed_action_carries_the_fixed_reason_code_of_our_own_script(message, reason):
    r = jobs.ui_action({"op": "click", "target": "notepad|Save", "text": ""}, backend=Boom(RuntimeError(message)), lister=lambda: [])
    assert r == {"ok": False, "error": "RuntimeError", "reason": reason}


@pytest.mark.parametrize("exc", [RuntimeError("ui-action-failed:C:\\Users\\me\\secret.docx"), RuntimeError("some screen text: password hunter2"), ValueError("bad target"),
                                 RuntimeError("ui-action-failed:" + "x" * 90), RuntimeError("ui-action-failed:no element; text on screen = 4111 1111")])
def test_anything_that_could_hold_screen_text_gives_the_class_name_only(exc):
    r = jobs.ui_action({"op": "click", "target": "notepad|Save", "text": ""}, backend=Boom(exc), lister=lambda: [])
    assert r == {"ok": False, "error": type(exc).__name__}


# ------------------------------------------------------------------------------------------------------------------------------ the vision rung
def test_window_vision_reads_the_window_into_a_local_file_and_sends_only_counts(tmp_path, monkeypatch):
    monkeypatch.setattr(vision, "see", lambda proc, label="*": {"ok": True, "lines": 3, "local_result": str(tmp_path / "v.json"), "chars": 40})
    r = diag.run({"op": "window_vision", "target": "notepad|*"}, lister=lambda: [])
    assert r["ok"] and r["check"] == "window_vision" and r["local_result"].endswith("v.json") and "text" not in r


def test_window_vision_locates_with_two_separate_points(monkeypatch):
    monkeypatch.setattr(vision, "see", lambda proc, label="*": {"ok": True, "lines": 3, "ocr_point": {"x": 10, "y": 20}, "vision_point": {"x": 12, "y": 22}})
    r = diag.run({"op": "window_vision", "target": "notepad|Save"}, lister=lambda: [])
    assert r["ocr_point"] == {"x": 10, "y": 20} and r["vision_point"] == {"x": 12, "y": 22}


@pytest.mark.parametrize("target", ["notepad", "notepad|", "a;b|x", "notepad|a\nb", "x" * 100 + "|y"])
def test_window_vision_refuses_a_bad_target_before_looking(target, monkeypatch):
    monkeypatch.setattr(vision, "see", lambda *a, **k: pytest.fail("looked at a window for a bad target"))
    assert diag.run({"op": "window_vision", "target": target}, lister=lambda: [])["ok"] is False


def test_window_vision_never_looks_at_a_bank_or_a_password_manager(monkeypatch):
    monkeypatch.setattr(vision, "see", lambda *a, **k: pytest.fail("looked"))
    r = diag.run({"op": "window_vision", "target": "KeePass|*"}, lister=lambda: [])
    assert r["ok"] is False and "refused" in r["error"]


def test_see_gives_the_ocr_point_and_the_vision_point_apart(tmp_path, monkeypatch):
    png = tmp_path / "w.png"
    png.write_bytes(b"x")
    monkeypatch.setattr(vision, "capture", lambda proc, scale=2: (str(png), {"width": 800, "height": 600, "scale": 1}))
    monkeypatch.setattr(vision, "ocr", lambda p, scale=1: [{"text": "Save", "cx": 100, "cy": 50, "words": []}])
    monkeypatch.setattr(vision, "find_text", lambda lines, label: {"cx": 100, "cy": 50})
    monkeypatch.setattr(vision, "ground_vision", lambda p, size, target: {"cx": 104, "cy": 52})
    got = vision.see("notepad", "Save")
    assert got["ocr_point"] == {"x": 100, "y": 50} and got["vision_point"] == {"x": 104, "y": 52} and not png.exists(), "the capture is deleted"
    monkeypatch.setattr(vision, "find_text", lambda lines, label: None)
    monkeypatch.setattr(vision, "ground_vision", lambda p, size, target: None)
    png.write_bytes(b"x")
    assert "ocr_point" not in vision.see("notepad", "Nope") and "vision_point" not in vision.see("notepad", "Nope")


# ------------------------------------------------------------------------------------------------------------------------------ keys for the ladder
def test_the_three_new_desktop_keys_go_to_the_desktop_only(monkeypatch):
    sent = []
    monkeypatch.setattr(winbackend, "_run", lambda script, env, *a, **k: sent.append((script, dict(env))) or "")
    for chord in ("alt+tab", "ctrl+win+right", "ctrl+win+left", "win+ctrl+left"):
        winbackend.key("desktop|", {"text": chord})
    assert [e["JARVIS_UI_VKS"] for _, e in sent] == ["18:0,9:0", "17:0,91:0,39:1", "17:0,91:0,37:1", "17:0,91:0,37:1"] and all(s is winbackend.GLOBAL_KEY for s, _ in sent)
    for bad_target in ("notepad|", "chrome|"):
        for chord in ("alt+tab", "ctrl+win+right"):
            with pytest.raises(ValueError):
                winbackend.key(bad_target, {"text": chord})
    for chord in ("win+tab", "win+l", "win+ctrl+up", "win+r"):
        with pytest.raises(ValueError):
            winbackend.key("desktop|", {"text": chord})
    assert len(sent) == 4


# ------------------------------------------------------------------------------------------------------------------------------ the human feel (script text; Windows runs it)
def test_the_pointer_travels_in_a_short_eased_curve_for_click_at_and_drag():
    for script in (winbackend.CLICK_AT, winbackend.DRAG):
        assert "function Move-Smooth" in script and "Move-Smooth $" in script and "JARVIS_UI_SMOOTH" in script
    assert script.count("SetCursorPos($x1") == 0, "no instant jump to the start point any more"
    assert "[W32]::SetCursorPos($x, $y)\n[W32]::mouse_event" not in winbackend.CLICK_AT


def test_typing_is_paced_in_small_pieces_up_to_a_cap_and_pasted_in_one_beyond_it():
    t = winbackend.TYPE
    assert "JARVIS_UI_PACE_CAP" in t and "JARVIS_UI_PACE_MS" in t and "Substring($i, [Math]::Min(8" in t
    assert t.count("SendWait") == 2, "the paced path and the single-piece path"
    assert t.index("Substring($i") < t.index("SendWait([regex]::Replace($piece"), "each piece is escaped AFTER the cut, so {+} is never split"
