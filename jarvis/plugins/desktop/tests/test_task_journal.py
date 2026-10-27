"""D76: what the owner's Timeline (D61) says about a milestone of an open-ended task: a fixed phrase and the site's host name, never a query, a seek or a value."""
from jarvis.plugins.desktop import journal_client as jc


def test_the_journal_names_a_milestone_by_a_fixed_phrase_and_the_site_only():
    cls, label, target = jc.classify("task_step", {"mode": "follow", "site": "shop-a.test", "seek": "iPhone 15 private words", "query": "my private query"}, {"ok": True})
    assert (cls, label, target) == ("A", "Follow a link", "shop-a.test")
    assert jc.classify("task_step", {"mode": "download", "site": "college.test"}, {"ok": True})[0] == "B"
    assert jc.classify("task_step", {"mode": "fill", "site": "x\nhostile.test"}, {"ok": True})[2] == "xhostile.test"
    assert "private" not in str(jc.classify("task_step", {"mode": "read", "seek": "private", "extract": ["total"]}, {"ok": True}))
    assert jc.classify("task_step", {"mode": "download", "site": "a.test"}, {"state": "waiting_for_permission"})[0] == "C"
