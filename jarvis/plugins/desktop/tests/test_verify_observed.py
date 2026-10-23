"""verify() no longer lets a step look checked when the node only said ok (audit PC-D11, 2026-10-18). Browser click, type and
press, window actions, press_key and check_pc used to pass with an empty uncertainty. They still pass (bench B/L depend on
it) but say "reported ok, not observed: ..." unless the node supplied an observation: the text read back (type_text), the
window state read back or the foreground window confirmed (window), typed_chars (browser type, compared with the request).
The final summary then does not call the task plainly "Done"."""
import pytest

from jarvis.plugins.desktop import coordinator as C

NOT_OBSERVED = C.UNOBSERVED


def v(step, result):
    return C.verify(step, dict({"ok": True, "state": "done"}, **result))


# ------------------------------------------------------------------ browser
def test_a_browser_click_is_not_observed():
    ok, checked, unc = v({"tool": "browser", "action": "click", "target": "Play"}, {"clicked": "Play"})
    assert ok and unc.startswith(NOT_OBSERVED) and "reaction" in unc and "click" in checked


def test_browser_type_with_the_requested_character_count_still_says_the_field_was_not_read_back():
    ok, _checked, unc = v({"tool": "browser", "action": "type", "target": "Search", "text": "hello"}, {"typed_chars": 5})
    assert ok and unc.startswith(NOT_OBSERVED) and "not read back" in unc


def test_browser_type_with_a_different_count_fails():
    ok, checked, unc = v({"tool": "browser", "action": "type", "target": "Search", "text": "hello"}, {"typed_chars": 3})
    assert not ok and "3 of 5" in checked and "does not match" in unc


def test_browser_type_without_a_count_is_unobserved_and_says_so():
    ok, _c, unc = v({"tool": "browser", "action": "type", "target": "Search", "text": "hello"}, {})
    assert ok and NOT_OBSERVED in unc and "did not say how many" in unc


def test_browser_press_compares_the_key_and_is_otherwise_unobserved():
    ok, _c, unc = v({"tool": "browser", "action": "press", "text": "Enter"}, {"pressed": "Enter"})
    assert ok and unc.startswith(NOT_OBSERVED)
    ok, checked, _u = v({"tool": "browser", "action": "press", "text": "Enter"}, {"pressed": "Escape"})
    assert not ok and "not Enter" in checked
    ok, _c, unc = v({"tool": "browser", "action": "press", "text": ""}, {"pressed": "Enter"})       # no key given: Enter
    assert ok and NOT_OBSERVED in unc


@pytest.mark.parametrize("act", ["read", "find", "tabs"])
def test_browser_reads_are_observations(act):
    ok, _c, unc = v({"tool": "browser", "action": act, "target": "x"}, {"chars": 120})
    assert ok and unc == ""


def test_browser_whoami_is_unchanged():
    assert C.verify({"tool": "browser", "action": "whoami"}, {"ok": True, "matches": True, "seen": ["a***"], "expected": "a***"})[0]
    assert not C.verify({"tool": "browser", "action": "whoami"}, {"ok": True, "matches": False})[0]


# ------------------------------------------------------------------ window
@pytest.mark.parametrize("act,state", [("minimize", "minimized"), ("maximize", "maximized"), ("restore", "normal")])
def test_a_window_state_the_node_read_back_is_observed(act, state):
    ok, checked, unc = v({"tool": "window", "action": act, "app": "notepad"}, {"window_state": state, "observed": "window state read back"})
    assert ok and unc == "" and state in checked


def test_a_window_state_that_differs_from_the_request_fails():
    ok, checked, unc = v({"tool": "window", "action": "minimize", "app": "notepad"}, {"window_state": "normal"})
    assert not ok and "normal, not minimized" in checked and "normal" in unc


@pytest.mark.parametrize("act", ["minimize", "maximize", "restore", "focus"])
def test_a_window_action_without_an_observation_is_reported_not_observed(act):
    ok, _c, unc = v({"tool": "window", "action": act, "app": "notepad"}, {"process": "notepad"})
    assert ok and unc.startswith(NOT_OBSERVED) and act in unc


def test_focus_confirmed_as_the_foreground_window_is_observed():
    ok, checked, unc = v({"tool": "window", "action": "focus", "app": "notepad"}, {"foreground": True})
    assert ok and unc == "" and "in front" in checked


def test_inspect_is_a_read():
    ok, _c, unc = v({"tool": "window", "action": "inspect", "app": "notepad"}, {"inspected": "notepad"})
    assert ok and unc == ""


# ------------------------------------------------------------------ keys and checks
def test_a_key_press_is_reported_not_observed():
    ok, checked, unc = v({"tool": "press_key", "key": "ctrl+s", "app": "notepad"}, {"done": "key"})
    assert ok and unc.startswith(NOT_OBSERVED) and "ctrl+s" in checked


def test_a_media_toggle_that_read_the_media_state_first_is_observed():
    ok, _c, unc = v({"tool": "press_key", "key": "playpause", "intent": "pause"},
                    {"done": "nothing to do", "note": "nothing was playing"})
    assert ok and unc == ""


def test_check_pc_with_data_is_observed_and_without_data_is_not():
    assert v({"tool": "check_pc", "check": "disk"}, {"count": 2, "rows": [{"drive": "C:"}]})[2] == ""
    assert v({"tool": "check_pc", "check": "media"}, {"sessions": []})[2] == ""
    ok, _c, unc = v({"tool": "check_pc", "check": "disk"}, {})
    assert ok and unc.startswith(NOT_OBSERVED) and "no data" in unc


def test_type_text_is_still_an_observation():
    ok, checked, unc = v({"tool": "type_text", "app": "notepad", "text": "hi"}, {"observed": "text read back from the field"})
    assert ok and unc == "" and "read back" in checked


def test_a_failed_step_is_still_a_failure():
    assert not C.verify({"tool": "browser", "action": "click", "target": "x"}, {"ok": False, "error": "no element"})[0]
    assert not C.verify({"tool": "window", "action": "minimize", "app": "x"}, {"ok": "true"})[0]       # only a real True passes


# ------------------------------------------------------------------ what the owner is told
def _rec(*steps):
    return {"status": "completed", "steps": list(steps), "plan": {}, "uncertain": [
        f"step {s['n']}: {s['uncertainty']}" for s in steps if s.get("uncertainty")]}


def test_the_summary_does_not_say_plain_done_when_a_step_was_only_reported():
    ok, checked, unc = v({"tool": "browser", "action": "click", "target": "Play"}, {"clicked": "Play"})
    s = C.summary(_rec({"n": 1, "passed": ok, "checked": checked, "uncertainty": unc}))
    assert s.startswith("Done, but not everything was observed:") and "reported ok, not observed" in s
    assert "verified" not in s.lower()


def test_the_summary_is_plain_done_when_everything_was_observed():
    ok, checked, unc = v({"tool": "window", "action": "minimize", "app": "notepad"}, {"window_state": "minimized"})
    s = C.summary(_rec({"n": 1, "passed": ok, "checked": checked, "uncertainty": unc}))
    assert s.startswith("Done: ") and "not certain" not in s and "not observed" not in s


def test_a_different_kind_of_uncertainty_keeps_the_old_wording():
    s = C.summary(_rec({"n": 1, "passed": True, "checked": "a window appeared", "uncertainty": "the profile button shows only a display name"}))
    assert s.startswith("Done: ") and "(not certain: step 1: the profile button" in s


def test_the_coordinator_records_the_uncertainty_of_an_unobserved_step(tmp_path):
    from jarvis.plugins.desktop import decide as D
    plan = D.Plan([{"tool": "press_key", "key": "ctrl+s", "app": "notepad"}], "ok")
    co = C.Coordinator({"press_key": lambda s: {"ok": True, "done": "key"}}, [], decide_fn=lambda r: plan,
                       tasks_dir=tmp_path / "t", lock_path=tmp_path / "l")
    rec = co.run("save")
    assert rec["status"] == "completed" and rec["steps"][0]["uncertainty"].startswith(NOT_OBSERVED)
    assert rec["uncertain"] and C.summary(rec).startswith("Done, but not everything was observed")


def test_a_mission_explain_does_not_say_checked_for_a_reported_step():
    from jarvis.plugins.desktop import missions
    rec = {"id": "m1", "status": "completed", "steps": [
        {"n": 1, "tool": "press_key", "passed": True, "uncertainty": C.unobserved("the key press has no result to read")},
        {"n": 2, "tool": "window", "passed": True, "uncertainty": ""}]}
    text = missions.explain(rec)
    assert "2 of 2 steps done (1 of them only reported ok, not observed)" in text and "done and checked" not in text
    rec["steps"][0]["uncertainty"] = ""
    assert "2 of 2 steps done and checked" in missions.explain(rec)
