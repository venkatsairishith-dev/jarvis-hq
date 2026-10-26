"""D54 (2026-10-26), simple tasks, second round: "undo the last sort" puts back the whole sort; the Windows shortcuts go to the desktop only.
Fakes only (the same fake bin and folders as test_taskops.py)."""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from jarvis_node import winbackend  # noqa: E402

from test_taskops import job, old, w  # noqa: E402,F401  (w is the fixture)


def test_undo_last_after_a_sort_puts_the_whole_sort_back_and_after_anything_else_only_that_thing(w):
    for n in ("a.png", "b.pdf", "c.mp3"):
        (w.dl / n).write_text("x", encoding="utf-8")
        old(w.dl / n, 5)
    r = job(w, "sort_downloads", "Downloads")
    assert r["moved"] == 3 and not (w.dl / "a.png").exists()
    u = job(w, "undo", "Downloads", "last")
    assert u["ok"] and u["undone"] >= 3 and (w.dl / "a.png").exists() and (w.dl / "b.pdf").exists() and (w.dl / "c.mp3").exists() and not (w.dl / "Images").exists()
    (w.docs / "x.txt").write_text("x", encoding="utf-8")
    job(w, "copy", "Documents", "x.txt", "Desktop")
    assert (w.desk / "x.txt").exists()
    job(w, "undo", "Documents", "last")
    assert not (w.desk / "x.txt").exists() and (w.docs / "x.txt").exists() and (w.dl / "a.png").exists()


def test_windows_shortcuts_go_to_the_desktop_only_and_only_from_a_fixed_list(monkeypatch):
    sent = []
    monkeypatch.setattr(winbackend, "_run", lambda script, env, *a, **k: sent.append((script, dict(env))) or "")
    winbackend.key("desktop|", {"text": "win+d"})
    winbackend.key("desktop|", {"text": "win+left"})
    assert [e["JARVIS_UI_VKS"] for _, e in sent] == ["91:0,68:0", "91:0,37:1"] and all(s is winbackend.GLOBAL_KEY for s, _ in sent)
    for bad_target, chord in (("notepad|", "win+d"), ("desktop|", "win+r"), ("desktop|", "win+l"), ("chrome|", "win+left")):
        with pytest.raises(ValueError):
            winbackend.key(bad_target, {"text": chord})
    assert len(sent) == 2
    assert set(winbackend.GLOBAL_KEYS) == {"win+d", "win+m", "win+left", "win+right", "win+up", "win+down"}
