"""Which window "notepad|..." means: one shared rule, never a guess for a change (found on the real laptop: with two Notepads JARVIS could type into the owner's)."""
import json
import os
import subprocess
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from jarvis_node import windowpick as wp  # noqa: E402
from jarvis_node import winbackend as wb  # noqa: E402

MINE, OWNERS, OLDER = 111, 222, 333
W = lambda hwnd, start, pid=1: {"hwnd": hwnd, "pid": pid, "start": start}


def test_one_window_is_used_for_reads_and_changes_alike():
    for mutating in (False, True):
        got = wp.choose([W(OWNERS, 5)], fg=0, launched=set(), mutating=mutating, proc="notepad")
        assert got[0]["hwnd"] == OWNERS


def test_no_window_means_none_so_the_old_modern_app_lookup_still_runs():
    assert wp.choose([], 0, set(), True, "notepad") is None


def test_with_two_windows_and_none_opened_by_jarvis_a_change_is_refused():
    wins = [W(MINE, 10), W(OWNERS, 5)]
    for fg in (0, MINE, OWNERS):                                  # even when one is in front: the front one may be the owner's
        with pytest.raises(wp.WindowPickError) as e:
            wp.choose(wins, fg, set(), True, "notepad")
        assert str(e.value) == "several notepad windows; say which one"


def test_a_window_jarvis_opened_is_the_target_even_when_the_owner_has_others():
    wins = [W(OWNERS, 5), W(MINE, 10), W(OLDER, 1)]
    w, note = wp.choose(wins, fg=OWNERS, launched={MINE}, mutating=True, proc="notepad")
    assert w["hwnd"] == MINE and "JARVIS opened" in note
    # several of its own: the one in front, else the newest
    assert wp.choose(wins, MINE, {MINE, OLDER}, True, "notepad")[0]["hwnd"] == MINE
    assert wp.choose(wins, OWNERS, {MINE, OLDER}, True, "notepad")[0]["hwnd"] == MINE      # newest start wins


def test_a_read_takes_the_front_window_else_the_newest_and_says_so():
    wins = [W(OWNERS, 5), W(MINE, 10)]
    w, note = wp.choose(wins, OWNERS, set(), False, "notepad")
    assert w["hwnd"] == OWNERS and note == "the one in front"
    w, note = wp.choose(wins, 999, set(), False, "notepad")
    assert w["hwnd"] == MINE and note == "picked the newest of 2"


def test_every_script_that_changes_something_is_a_change_and_only_find_and_readback_are_reads():
    assert wb.READ_ONLY == {wb.FIND, wb.READBACK}
    for s in (wb.CLICK, wb.TYPE, wb.FOCUS, wb.KEY, wb.SCROLL, wb.CLICK_AT, wb.WINDOW_STATE):
        assert s not in wb.READ_ONLY


def test_the_scripts_act_on_the_handle_they_are_given_not_on_the_first_process():
    assert "$env:JARVIS_UI_HWND" in wb.PICK
    for s in (wb.CLICK, wb.TYPE, wb.FOCUS, wb.KEY, wb.SCROLL, wb.CLICK_AT, wb.WINDOW_STATE):
        assert "$env:JARVIS_UI_HWND" in s


def _fake_windows(monkeypatch, windows, fg):
    monkeypatch.setattr(wp, "list_windows", lambda proc: (windows, fg))


def test_a_change_to_an_ambiguous_app_is_refused_before_any_script_runs(monkeypatch, tmp_path):
    monkeypatch.setenv("JARVIS_RUN_DIR", str(tmp_path))
    _fake_windows(monkeypatch, [W(MINE, 10), W(OWNERS, 5)], MINE)
    ran = []
    from jarvis_node import runtime
    monkeypatch.setattr(runtime, "run", lambda *a, **k: ran.append(a) or subprocess.CompletedProcess([], 0, "", ""))
    with pytest.raises(wp.SeveralWindowsSayWhich, match="several notepad windows; say which one"):
        wb._run(wb.TYPE, {"JARVIS_UI_PROC": "notepad", "JARVIS_UI_ELEM": "*", "JARVIS_UI_TEXT": "x"})
    assert not ran


def test_the_chosen_handle_reaches_the_script_through_the_environment(monkeypatch, tmp_path):
    monkeypatch.setenv("JARVIS_RUN_DIR", str(tmp_path))
    wp.record("notepad", [MINE], 7)
    _fake_windows(monkeypatch, [W(MINE, 10, 7), W(OWNERS, 5, 9)], OWNERS)
    seen = {}
    from jarvis_node import runtime

    def fake_run(argv, timeout=30, env=None):
        seen.update(env or {})
        return subprocess.CompletedProcess(argv, 0, "", "")
    monkeypatch.setattr(runtime, "run", fake_run)
    wb._run(wb.TYPE, {"JARVIS_UI_PROC": "notepad", "JARVIS_UI_ELEM": "*", "JARVIS_UI_TEXT": "x"})
    assert seen["JARVIS_UI_HWND"] == str(MINE) and seen["JARVIS_UI_PID"] == "7"


def test_a_launch_is_recorded_and_forgotten_after_twelve_hours(monkeypatch, tmp_path):
    monkeypatch.setenv("JARVIS_RUN_DIR", str(tmp_path))
    wp.record("Notepad", [MINE], 7)
    assert wp.launched_hwnds("notepad") == {MINE} and wp.launched_hwnds("calc") == set()
    data = json.loads((tmp_path / "launched-windows.json").read_text())
    data[0]["t"] -= wp.KEEP_S + 5
    (tmp_path / "launched-windows.json").write_text(json.dumps(data))
    assert wp.launched_hwnds("notepad") == set()


def test_launch_and_record_remembers_only_the_window_that_appeared(monkeypatch, tmp_path):
    monkeypatch.setenv("JARVIS_RUN_DIR", str(tmp_path))
    state = {"calls": 0}

    def fake_list(proc):
        state["calls"] += 1
        return ([W(OWNERS, 5)] if state["calls"] == 1 else [W(OWNERS, 5), W(MINE, 10)]), 0
    monkeypatch.setattr(wp, "list_windows", fake_list)
    monkeypatch.setattr(wp.time, "sleep", lambda s: None)
    started = []
    wp.launch_and_record("notepad", lambda: started.append(1))
    assert started == [1] and wp.launched_hwnds("notepad") == {MINE}


def test_a_bad_process_name_is_refused():
    with pytest.raises(ValueError):
        wp.list_windows("notepad; calc")


def test_the_class_name_is_the_reason_because_the_node_reports_only_class_names():
    from jarvis_node import jobs
    def backend_type(target, params):
        raise wp.SeveralWindowsSayWhich("several notepad windows; say which one")
    out = jobs.ui_action({"op": "type", "target": "notepad|*", "text": "x"}, backend={"type": backend_type}, lister=lambda proc: [])
    assert out == {"ok": False, "error": "SeveralWindowsSayWhich"}


def test_launch_and_record_any_remembers_every_new_window_by_its_app(monkeypatch, tmp_path):
    monkeypatch.setenv("JARVIS_RUN_DIR", str(tmp_path))
    state = {"calls": 0}

    def fake_list(proc):
        assert proc == ""                                          # all apps: the node does not know which one a Start entry or a folder opens in
        state["calls"] += 1
        old = [dict(W(OWNERS, 5), name="notepad")]
        return (old if state["calls"] == 1 else old + [dict(W(MINE, 10, 7), name="notepad"), dict(W(444, 11, 8), name="explorer")]), 0
    monkeypatch.setattr(wp, "list_windows", fake_list)
    monkeypatch.setattr(wp.time, "sleep", lambda s: None)
    assert sorted(wp.launch_and_record_any(lambda: None)) == ["explorer", "notepad"]
    assert wp.launched_hwnds("notepad") == {MINE} and wp.launched_hwnds("explorer") == {444}


def test_pc_open_records_the_window_only_for_the_real_launcher(monkeypatch, tmp_path):
    from jarvis_node import pcopen
    calls = []
    monkeypatch.setattr(wp, "launch_and_record_any", lambda go: (calls.append("recorded"), go()))
    ran = []
    pcopen._opened(lambda: ran.append(1), real=False)
    assert ran == [1] and calls == []                              # a test's fake opener is never recorded
    if os.name == "nt":
        pcopen._opened(lambda: ran.append(2), real=True)
        assert ran == [1, 2] and calls == ["recorded"]
