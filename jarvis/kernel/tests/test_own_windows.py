"""D74 (2026-10-27): JARVIS never closes its OWN windows by itself. Found on the real laptop (Phase 7): "Close the window" closed the chat window.
The kernel is the second layer (the first is the desktop plugin, which asks which window; the node is the third): a close aimed at the face, the avatar or the
console is DENY, unless the owner's own words name JARVIS, and then it is the owner's PIN. An ordinary 'close notepad' is unchanged."""
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from jarvis_kernel import policy  # noqa: E402


def act(target, op="close", goal="", text="", owner_direct=True):
    return policy.Action(tool="ui", op=op, target=target, params={"text": text} if text else {}, lane="private", owner_direct=owner_direct, goal=goal)


def test_closing_the_face_is_denied_whatever_the_goal_says_about_the_window():
    for target in ("jarvis|", "jarvis.exe|", "JARVIS|", "electron|", "jarvisface|", "jarvis-face|"):
        d = policy.decide(act(target, goal="Close the window"))
        assert d.level == policy.DENY, (target, d.reason)
    d = policy.decide(act("jarvis|", goal="close this"))
    assert "own" in d.reason.lower()


def test_closing_an_ordinary_app_is_unchanged():
    for goal in ("close notepad", "Close the window", ""):
        assert policy.decide(act("notepad|", goal=goal)).level == policy.YELLOW_AUTO
    assert policy.decide(act("calc|", goal="close calculator")).level == policy.YELLOW_AUTO


def test_naming_jarvis_turns_the_denial_into_the_owners_pin():
    d = policy.decide(act("jarvis|", goal="please close the JARVIS chat window"))
    assert d.level == policy.YELLOW_APPROVAL


def test_an_agent_or_the_public_lane_gets_no_softer_answer():
    a = policy.Action(tool="ui", op="close", target="jarvis|", params={}, lane="private", owner_direct=False)
    assert policy.decide(a).level == policy.DENY
    b = policy.Action(tool="ui", op="close", target="jarvis|", params={}, lane="public", owner_direct=False)
    assert policy.decide(b).level == policy.DENY


def test_the_close_chords_are_covered_too():
    for chord in ("alt+f4", "ctrl+w", "ctrl+q", "ALT+F4"):
        assert policy.decide(act("jarvis|", op="key", text=chord, goal="close it")).level == policy.DENY, chord
    assert policy.decide(act("jarvis|", op="key", text="enter", goal="press enter")).level != policy.DENY          # other keys are not this rule
    assert policy.decide(act("notepad|", op="key", text="alt+f4", goal="close it")).level == policy.YELLOW_APPROVAL   # RISKY_KEYS, as before


def test_other_ui_actions_on_jarvis_are_not_changed_by_this_rule():
    assert policy.decide(act("jarvis|", op="minimize", goal="minimize it")).level == policy.YELLOW_AUTO


def test_own_window_reads_process_and_a_browser_tab_title():
    assert policy.own_window("jarvis")
    assert policy.own_window("Jarvis.EXE")
    assert policy.own_window("chrome", "JARVIS - Google Chrome")
    assert policy.own_window("msedge", "JARVIS console - Microsoft Edge")
    assert not policy.own_window("chrome", "Gmail - Google Chrome")
    assert not policy.own_window("notepad", "JARVIS notes.txt - Notepad")        # a document that happens to start with the word
    assert not policy.own_window("notepad", "")
    assert not policy.own_window("", "")


def test_the_file_adds_to_the_floor_and_never_removes(tmp_path):
    f = tmp_path / "own_windows.json"
    f.write_text(json.dumps({"processes": ["myface.exe"], "title_regex": ["^mini hud$", "(unclosed"]}), encoding="utf-8")
    cfg = policy.load_own_windows(str(f))
    assert policy.own_window("myface", cfg=cfg)
    assert policy.own_window("notepad", "Mini HUD", cfg=cfg) == ""             # a title rule applies to browsers and JARVIS processes only
    assert policy.own_window("jarvis", cfg=cfg)                                 # the floor is still there
    broken = tmp_path / "broken.json"
    broken.write_text("{not json", encoding="utf-8")
    assert policy.own_window("jarvis", cfg=policy.load_own_windows(str(broken)))
    assert policy.own_window("jarvis", cfg=policy.load_own_windows(str(tmp_path / "missing.json")))


def test_the_real_file_loads_and_is_console_only():
    assert policy.own_window("jarvis")
    assert "own_windows.json" in policy.SAFETY_COMPONENTS and "ambiguity.json" in policy.SAFETY_COMPONENTS
    from jarvis_kernel import push_gate
    for f in ("jarvis/config/own_windows.json", "jarvis/config/ambiguity.json"):
        assert push_gate.PROTECTED.search(f), f
