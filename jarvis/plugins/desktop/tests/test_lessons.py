"""D65: the lessons store. Local, size-capped, content-free; consulted before the ladder; viewable and clearable by the owner."""
from __future__ import annotations

import json

import pytest

from jarvis.plugins.desktop import lessons as L


@pytest.fixture
def les(tmp_path):
    return L.Lessons(tmp_path / "lessons.json", clock=lambda: "2026-10-26")


def test_a_route_that_worked_is_remembered_and_comes_first_next_time(les):
    assert les.best_route("excel", "dialog") == ""
    les.record("Excel.exe", "element_missing", "keyboard", True)
    les.record("excel", "element_missing", "keyboard", True)
    les.record("excel", "element_missing", "scroll", False)
    assert les.best_route("excel", "element_missing") == "keyboard"
    assert les.order("excel", "element_missing", ["wait", "scroll", "keyboard"]) == ["keyboard", "wait", "scroll"]
    assert les.order("word", "element_missing", ["wait", "scroll"]) == ["wait", "scroll"], "another app learned nothing"
    assert les.order("excel", "element_missing", ["wait", "scroll"]) == ["wait", "scroll"], "a lesson only REORDERS what the ladder offers: it never adds a route"


def test_a_route_that_fails_more_than_it_works_is_not_recommended(les):
    les.record("excel", "dialog", "answer", True)
    for _ in range(3):
        les.record("excel", "dialog", "answer", False)
    assert les.best_route("excel", "dialog") == ""


def test_a_lesson_cannot_carry_text_only_closed_words(les, tmp_path):
    assert not les.record("notepad", "free text about a file report.docx", "keyboard", True)
    assert not les.record("notepad", "dialog", "type 'my password' then enter", True)
    assert not les.record("C:\\Users\\me\\secret.docx", "dialog", "answer", True)
    assert not les.record("a window title: Bank - Chrome", "dialog", "answer", True)
    assert not (tmp_path / "lessons.json").exists()
    les.record("notepad", "dialog", "answer", True)
    raw = (tmp_path / "lessons.json").read_text()
    assert set(json.loads(raw)) == {"v", "entries", "last"} and "notepad|dialog" in raw


def test_a_hand_edited_file_cannot_smuggle_text_in(tmp_path):
    p = tmp_path / "lessons.json"
    p.write_text(json.dumps({"v": 1, "last": "x", "entries": {
        "notepad|dialog": {"routes": {"answer": [2, 0], "type the password hunter2": [9, 0], "wait": ["a", "b"]}, "seen": "2026-10-26"},
        "C:\\secret.docx|dialog": {"routes": {"answer": [1, 0]}}, "excel|made up problem": {"routes": {"answer": [1, 0]}}}}))
    rows = L.Lessons(p).about()
    assert [(r["app"], r["problem"], r["route"]) for r in rows] == [("notepad", "dialog", "answer")]


def test_the_file_is_capped_and_the_oldest_lesson_goes_first(tmp_path):
    n = {"i": 0}
    les = L.Lessons(tmp_path / "l.json", clock=lambda: f"2026-10-{(n['i'] // 100) % 28 + 1:02d}")
    for i in range(L.MAX_ENTRIES + 40):
        n["i"] = i
        assert les.record(f"app{i}", "dialog", "answer", True)
    data = json.loads((tmp_path / "l.json").read_text())
    assert len(data["entries"]) <= L.MAX_ENTRIES and (tmp_path / "l.json").stat().st_size <= L.MAX_BYTES
    assert "app0|dialog" not in data["entries"] and f"app{L.MAX_ENTRIES + 39}|dialog" in data["entries"]


def test_the_owner_can_see_what_was_learned_and_clear_it(les):
    les.record("excel", "element_missing", "keyboard", True)
    les.record("excel", "dialog", "answer", True)
    les.record("word", "minimized", "restore", True)
    said = les.describe("excel")
    assert "excel" in said and "keyboard" in said and "word" not in said and "forget" in said.lower()
    assert "I have not learned anything about powerpoint yet." == les.describe("powerpoint")
    assert les.forget(last=True) == 1 and les.best_route("word", "minimized") == "" and les.best_route("excel", "dialog") == "answer"
    assert les.forget("excel") == 2 and les.about("excel") == []
    les.record("word", "minimized", "restore", True)
    assert les.forget(everything=True) == 1 and les.about() == []


def test_a_broken_or_unwritable_file_is_never_a_reason_to_fail(tmp_path):
    p = tmp_path / "l.json"
    p.write_text("{not json")
    les = L.Lessons(p)
    assert les.best_route("excel", "dialog") == "" and les.about() == []
    assert les.record("excel", "dialog", "answer", True)
    blocked = L.Lessons(tmp_path / "file" / "sub" / "l.json")
    (tmp_path / "file").write_text("a file, not a folder")
    assert blocked.record("excel", "dialog", "answer", True) is True and blocked.best_route("excel", "dialog") == ""
