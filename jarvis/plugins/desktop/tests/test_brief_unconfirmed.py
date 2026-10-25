"""2026-10-25 (owner): JARVIS said "Calculator is open on screen" while it was not. A launch whose window the node did not see is not a success."""
from jarvis.plugins import desktop


def test_a_launch_with_no_window_seen_is_not_ok():
    got = desktop._brief({"state": "done", "result": {"ok": True, "opened": "Calculator", "verified": "unconfirmed",
                                                      "note": "opened, but no window titled like 'Calculator' appeared within 6 s"}})
    assert got["ok"] is False and "appeared" in got["error"] and got["verified"] == "unconfirmed"


def test_a_verified_launch_stays_ok():
    got = desktop._brief({"state": "done", "result": {"ok": True, "opened": "Calculator", "verified": "verified", "window": {"title": "Calculator"}}})
    assert got["ok"] is True and "error" not in got
