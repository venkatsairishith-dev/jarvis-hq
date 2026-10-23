"""open_website says exactly what was and was not observed (audit PC-D7 remainder, 2026-10-18). An unproven profile still
completes with a warning (bench L-004 expects that and is not changed); the warning now names what the node saw: a Chrome
window changed after the launch (yes/no), the window title names the site (yes/no/not reported), the profile (unproven, or
the button showed the expected name)."""
from jarvis.plugins.desktop import coordinator as C

STEP = {"tool": "open_website", "profile": "Default", "url": "https://example.com/"}


def verify(**result):
    return C.verify(STEP, dict({"ok": True, "state": "done", "profile": "Default", "verified": "window"}, **result))


def test_the_l004_shape_still_completes_and_now_lists_what_was_observed():
    ok, _checked, unc = verify(profile_name_matches=False)                    # exactly the fixture of bench L-004
    assert ok
    assert "display name" in unc and "unproven" in unc                          # what L-004 requires, unchanged
    assert "a Chrome window changed after the launch: yes" in unc
    assert "the window title names the site: not reported" in unc


def test_a_title_that_does_not_name_the_site_is_listed_as_no():
    ok, _c, unc = verify(profile_name_matches=False, site_in_title=False, host="example.com")
    assert ok and "the window title names example.com: no" in unc and "address itself was not read" in unc
    assert "unproven" in unc


def test_a_title_that_names_the_site_is_listed_as_yes_with_the_profile_still_unproven():
    ok, _c, unc = verify(profile_name_matches=False, site_in_title=True, host="example.com")
    assert ok and "the window title names example.com: yes" in unc and "unproven" in unc
    assert "address itself was not read" not in unc


def test_a_matching_profile_button_and_a_titled_site_leave_nothing_uncertain():
    assert verify(profile_name_matches=True, site_in_title=True, host="example.com") == (
        True, "Chrome window appeared in profile Default", "")
    assert verify(profile_name_matches=True)[2] == ""                         # no title information: nothing to doubt


def test_a_matching_profile_button_with_a_wrong_title_names_the_profile_as_matched():
    ok, _c, unc = verify(profile_name_matches=True, site_in_title=False, host="example.com")
    assert ok and "the profile button shows the expected name" in unc and "names example.com: no" in unc
    assert "unproven" not in unc


def test_no_window_is_still_a_failure_with_its_reason():
    ok, _c, unc = C.verify(STEP, {"ok": True, "state": "done", "profile": "Default", "verified": "unconfirmed"})
    assert not ok and unc == "no Chrome window was seen"
    ok, _c, _u = C.verify(STEP, {"ok": True, "state": "done", "profile": "Profile 1", "verified": "window"})
    assert not ok                                                              # a window, but in another profile


def test_the_summary_carries_the_observation(tmp_path):
    from jarvis.plugins.desktop import decide as D
    plan = D.Plan([dict(STEP)], "ok")
    co = C.Coordinator({"open_website": lambda s: {"ok": True, "profile": "Default", "verified": "window", "profile_name_matches": False}},
                       [], decide_fn=lambda r: plan, tasks_dir=tmp_path / "t", lock_path=tmp_path / "l")
    text = C.summary(co.run("open example"))
    assert text.startswith("Done: ") and "not certain" in text and "display name" in text
    assert "a Chrome window changed after the launch: yes" in text
