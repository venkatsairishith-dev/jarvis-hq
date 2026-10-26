"""D67: incremental playbook deltas for the lessons store (the ACE method, arXiv 2510.04618: reflect proposes small updates, a deterministic merge applies
them one item at a time, nothing is rewritten). Content-free, capped as before, and a lesson still only reorders routes the ladder offers."""
from __future__ import annotations

import json

import pytest

from jarvis.plugins.desktop import lessons as L


@pytest.fixture
def les(tmp_path):
    return L.Lessons(tmp_path / "lessons.json", clock=lambda: "2026-10-26")


def _att(app, problem, route, worked):
    return {"app": app, "problem": problem, "route": route, "worked": worked}


def test_reflect_proposes_the_four_kinds_of_delta_and_writes_nothing(les, tmp_path):
    trace = [_att("excel", "dialog", "answer", True), _att("excel", "dialog", "answer", False), _att("excel", "dialog", "wait", False)]
    deltas = les.reflect(trace)
    assert [d["op"] for d in deltas] == ["add", "helpful", "harmful", "add", "harmful"], "a new route is added first, then bumped"
    assert not (tmp_path / "lessons.json").exists(), "reflect only proposes"
    for _ in range(L.RETIRE_MARGIN - 1):
        les.record("excel", "dialog", "wait", False)
    assert les.reflect([_att("excel", "dialog", "wait", False)])[-1]["op"] == "retire", "a route that keeps failing is proposed for retirement"
    assert {d["op"] for d in deltas} <= set(L.OPS)


def test_merge_bumps_one_item_at_a_time_and_is_deterministic(tmp_path):
    a, b = L.Lessons(tmp_path / "a.json", clock=lambda: "2026-10-26"), L.Lessons(tmp_path / "b.json", clock=lambda: "2026-10-26")
    base = {"app": "word", "problem": "dialog", "route": "answer"}
    deltas = [{"op": "add", **base}, {"op": "helpful", **base}, {"op": "helpful", **base}, {"op": "harmful", **base}]
    assert a.merge(deltas) == {"applied": 4, "ignored": 0, "retired": 0} and b.merge(deltas)["applied"] == 4
    assert (tmp_path / "a.json").read_bytes() == (tmp_path / "b.json").read_bytes(), "the same deltas give the same file"
    assert a.items("word")[0] == {"id": "word|dialog|answer", "app": "word", "problem": "dialog", "route": "answer", "outcome": "helpful",
                                  "helpful": 2, "harmful": 1, "last_seen": "2026-10-26"}
    a.merge([{"op": "add", **base}])
    assert a.items("word")[0]["helpful"] == 2, "adding an item that exists changes nothing (no duplicates)"
    assert a.merge([{"id": "word|dialog|answer", "op": "helpful"}])["applied"] == 1 and a.items("word")[0]["helpful"] == 3, "an item can be named by its id"


def test_merge_leaves_every_other_item_exactly_as_it_was(tmp_path):
    p = tmp_path / "l.json"
    old = {"v": 1, "last": "", "entries": {"notepad|dialog": {"routes": {"answer": [2, 0]}, "seen": "2026-01-02"},
                                         "excel|minimized": {"routes": {"restore": [5, 1, "2026-03-04"]}, "seen": "2026-03-04"}}}
    p.write_text(json.dumps(old))
    les = L.Lessons(p, clock=lambda: "2026-10-26")
    les.merge([{"op": "helpful", "app": "word", "problem": "dialog", "route": "answer"}])
    new = json.loads(p.read_text())
    assert new["entries"]["notepad|dialog"] == old["entries"]["notepad|dialog"], "a version-1 item is not rewritten by an update to another item"
    assert new["entries"]["excel|minimized"] == old["entries"]["excel|minimized"]
    assert new["entries"]["word|dialog"]["routes"]["answer"] == [1, 0, "2026-10-26"]
    assert les.best_route("notepad", "dialog") == "answer" and les.best_route("excel", "minimized") == "restore"


def test_retire_removes_the_item_and_an_empty_entry_with_it(les):
    les.record("excel", "dialog", "answer", True)
    les.record("excel", "dialog", "wait", True)
    assert les.merge([{"op": "retire", "app": "excel", "problem": "dialog", "route": "answer"}]) == {"applied": 1, "ignored": 0, "retired": 1}
    assert [i["route"] for i in les.items("excel")] == ["wait"] and les.best_route("excel", "dialog") == "wait"
    les.merge([{"op": "retire", "app": "excel", "problem": "dialog", "route": "wait"}])
    assert les.items() == [] and les.about() == [] and les.forget(last=True) == 0, "the last item gone: the entry and the 'last learned' pointer are gone too"
    assert les.merge([{"op": "retire", "app": "excel", "problem": "dialog", "route": "wait"}])["ignored"] == 1, "retiring what is not there is harmless"


def test_a_route_that_keeps_failing_is_retired_by_the_reflect_step(les):
    les.record("excel", "dialog", "answer", True)
    for _ in range(L.RETIRE_MARGIN + 3):
        les.record("excel", "dialog", "answer", False)
    assert all(i["harmful"] - i["helpful"] < L.RETIRE_MARGIN for i in les.items("excel")), "no item is kept past the margin"
    assert les.best_route("excel", "dialog") == ""


def test_junk_deltas_are_ignored_and_free_text_cannot_ride_in(les, tmp_path):
    junk = [{"op": "rewrite", "app": "excel", "problem": "dialog", "route": "answer"},
            {"op": "add", "app": "excel", "problem": "dialog", "route": "type 'my password'"},
            {"op": "add", "app": "C:\\Users\\me\\secret.docx", "problem": "dialog", "route": "answer"},
            {"op": "helpful", "app": "excel", "problem": "dialog", "route": "answer", "note": "the password is hunter2"},
            {"op": "helpful", "id": "excel|dialog|answer|extra"}, {"op": "helpful", "id": "excel|made up|answer"},
            {"op": "helpful", "id": "excel|dialog|answer", "route": "wait"}, "helpful excel", None, 7, {"op": "add"}]
    assert les.merge(junk) == {"applied": 0, "ignored": len(junk), "retired": 0}
    assert not (tmp_path / "lessons.json").exists(), "nothing valid, nothing written"
    assert les.reflect([_att("excel", "free text", "answer", True), _att("excel", "dialog", "answer", "yes"), "x", _att("", "dialog", "answer", True)]) == []
    assert les.learn_from_task([_att("excel", "dialog", "do whatever", True)])["applied"] == 0


def test_the_file_holds_only_closed_words_numbers_and_dates(les, tmp_path):
    les.learn_from_task([_att("Excel.exe", "element_missing", "keyboard", True), _att("excel", "dialog", "answer", False), _att("code", "slow_start", "wait", True)])
    les.merge([{"op": "retire", "app": "excel", "problem": "dialog", "route": "answer"}, {"op": "add", "app": "word", "problem": "no_window", "route": "launch"}])
    raw = json.loads((tmp_path / "lessons.json").read_text())
    allowed_words = set(L.PROBLEMS) | set(L.ROUTES) | {"v", "entries", "last", "routes", "seen"}
    strings: list[str] = []

    def walk(x):
        if isinstance(x, dict):
            for k, v in x.items():
                strings.append(k)
                walk(v)
        elif isinstance(x, list):
            for v in x:
                walk(v)
        elif isinstance(x, str):
            strings.append(x)
    walk(raw)
    assert strings
    for text in strings:
        assert all(p in allowed_words or L.APP.match(p) or L.DATE.match(p) or p == "" for p in text.split("|")), text
    for i in les.items():
        assert i["outcome"] in ("helpful", "harmful", "mixed", "new") and i["route"] in L.ROUTES and i["problem"] in L.PROBLEMS and L.APP.match(i["app"])


def test_the_caps_hold_for_bulk_deltas_too(tmp_path):
    n = {"i": 0}
    les = L.Lessons(tmp_path / "l.json", clock=lambda: f"2026-10-{(n['i'] // 100) % 28 + 1:02d}")
    for i in range(L.MAX_ENTRIES + 40):
        n["i"] = i
        les.learn_from_task([_att(f"app{i}", "dialog", "answer", True), _att(f"app{i}", "dialog", "wait", True)])
    data = json.loads((tmp_path / "l.json").read_text())
    assert len(data["entries"]) <= L.MAX_ENTRIES and (tmp_path / "l.json").stat().st_size <= L.MAX_BYTES
    assert "app0|dialog" not in data["entries"] and f"app{L.MAX_ENTRIES + 39}|dialog" in data["entries"]
    big = [{"op": "add", "app": f"bulk{i}", "problem": "dialog", "route": "answer"} for i in range(L.MAX_DELTAS + 20)]
    done = les.merge(big)
    assert done["applied"] == L.MAX_DELTAS and done["ignored"] == 20, "one merge applies at most MAX_DELTAS deltas"
    assert (tmp_path / "l.json").stat().st_size <= L.MAX_BYTES


def test_a_lesson_still_only_reorders_what_the_ladder_offers(les):
    les.learn_from_task([_att("excel", "dialog", "scroll", True), _att("excel", "dialog", "scroll", True)])
    assert les.order("excel", "dialog", ["wait", "answer"]) == ["wait", "answer"], "a lesson for a route the ladder did not offer adds nothing"
    assert les.order("excel", "dialog", ["wait", "scroll", "answer"]) == ["scroll", "wait", "answer"]


def test_counters_accumulate_across_tasks_and_the_owner_view_still_works(les):
    les.record("excel", "dialog", "answer", True)
    les.record("excel", "dialog", "answer", True)
    les.learn_from_task([_att("excel", "dialog", "answer", False), _att("excel", "dialog", "wait", True)])
    assert {i["route"]: (i["helpful"], i["harmful"]) for i in les.items("excel")} == {"answer": (2, 1), "wait": (1, 0)}
    assert les.describe("excel").startswith("Here is what I have learned.") and les.forget("excel") == 1 and les.items() == []
