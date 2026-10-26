"""D54 (2026-10-26): the catalogue of everyday simple tasks (config/simple_tasks.json), end to end on the FAKE Windows of fakewin.py.

The data file is the contract: every entry's own examples must match it, plan without the model, and every handler runs the real plugin agents, the
kernel's real policy (the grant comes from the owner's words) and the node's real file/settings code against fake folders, a fake Recycle Bin, a fake
PowerShell and a fake scheduler. Class C entries never become a simple_task step: they go to the loop, where the kernel holds the step for the PIN.
Entries marked needs_laptop are the ones whose real behaviour (Windows itself) only the laptop can show; the fake proves their class, their request and
their verification."""
import datetime as dt
import json
import os
import time
import types
from pathlib import Path

import pytest

from jarvis.plugins.desktop import DesktopPlugin, core, coordinator as C, decide as D, drive as drive_mod, simple_tasks as ST
import jarvis.plugins.desktop as pkg

from .fakewin import FakeWindows

PROFILES = [{"profile": "Default", "name": "V", "account": "v***@x", "default": True, "say": ["default", "venkat"]}]
ENTRIES = ST.load()


def no_model(body):
    raise AssertionError("the decision model must not be asked for a catalogue task")


def plan_of(text):
    return D.decide(text, PROFILES, post=no_model)


@pytest.fixture
def pc(tmp_path, monkeypatch):
    fw = FakeWindows(tmp_path, monkeypatch)
    real = core.Pc
    monkeypatch.setattr(core, "Pc", lambda **kw: real(call=fw.call, sleep=lambda s: None, approval_wait_s=0, **kw))
    monkeypatch.setattr(core, "owner", lambda method, name, body=None, timeout=20.0: fw.call(method, name, body))
    monkeypatch.setattr(pkg, "time", types.SimpleNamespace(sleep=lambda s: None, time=time.time, monotonic=time.monotonic, strftime=time.strftime,
                                                           localtime=time.localtime))
    monkeypatch.setenv("JARVIS_VAULT_DIR", str(tmp_path / "vault"))
    fw.tmp = tmp_path

    def ask(text, plan=None):
        """One owner request, as the plugin runs it: planned without the model, the goal = his words, every step through the kernel."""
        plan = plan or plan_of(text)
        if plan.status != "ok":
            return {"plan": plan, "status": "plan_" + plan.status}
        op = C.begin("task", "task-t")
        op.goal = text
        tok = C.bind(op)
        try:
            agents = DesktopPlugin._agents(types.SimpleNamespace(), None)
            return C.Coordinator(agents, PROFILES, tasks_dir=tmp_path / "tasks", lock_path=tmp_path / "lock", op=op).run(text, plan)
        finally:
            C.unbind(tok)
            op.entry_exit()
    fw.ask = ask
    return fw


def docs(fw):
    return fw.home / "Documents"


def said(rec):
    return C.summary(rec)


def done(rec):
    return rec["status"] == "completed"


# ================================================================================================================ the file itself
def test_the_catalogue_file_is_valid_and_complete():
    assert ST.problems() == []
    assert len(ENTRIES) >= 70
    assert len({t["id"] for t in ENTRIES}) == len(ENTRIES)
    assert {t["group"] for t in ENTRIES} >= {"files", "apps", "settings", "daily"}
    assert {t["class"] for t in ENTRIES} == {"A", "B", "C", "ask"}
    for t in ENTRIES:
        assert t["phrases"] and t["examples"] and t["verify"] and isinstance(t["needs_laptop"], bool), t["id"]
        assert t["how"]["handler"] in ST.HANDLERS, t["id"]


@pytest.mark.parametrize("entry,example", [(t["id"], e) for t in ENTRIES for e in t["examples"]])
def test_every_example_matches_its_own_entry_with_its_arguments(entry, example):
    hit = ST.match(example["say"])
    assert hit is not None and hit["id"] == entry, (example["say"], hit and hit["id"])
    assert hit["args"] == example["args"], example["say"]


@pytest.mark.parametrize("entry,example", [(t["id"], t["examples"][0]) for t in ENTRIES])
def test_every_entry_plans_without_the_decision_model_and_by_its_class(entry, example):
    t = ST.get(entry)
    p = plan_of(example["say"])
    h = t["how"]["handler"]
    if h == "ask":
        assert p.status == "clarify" and p.question == t["how"]["say"]
    elif h == "unavailable":
        assert p.status == "fallback" and p.problems == [t["how"]["say"]]
    elif t["class"] == "C":
        assert p.status == "ok" and [s["tool"] for s in p.steps] == ["drive"], (p.status, p.steps)         # the loop does the preparing; the kernel holds the step that matters
    elif h in ("window", "media_key", "open_file", "open_site"):
        assert p.status == "ok" and p.steps[0]["tool"] in ("window", "press_key", "open_file", "open_website", "open_app")
    else:
        assert p.status in ("ok", "clarify"), (p.status, p.problems)
        if p.status == "ok":
            assert p.steps[0]["tool"] in ("simple_task", "drive", "check_pc", "open_website") and p.steps[0].get("task", entry) == entry


def test_the_laptop_flags_say_which_entries_only_windows_can_prove():
    flagged = {t["id"] for t in ENTRIES if t["needs_laptop"]}
    for t in ENTRIES:
        h = t["how"]["handler"]
        if h in ("setting", "keys", "snap", "arrange", "screenshot", "copy_screen_text", "paste", "type_active", "drive", "close_app", "window", "media_key"):
            assert t["needs_laptop"], t["id"]
        if h in ("convert", "reminder", "vault_note", "quick_list", "file_op", "status") and t["id"] not in ("battery",):
            assert not t["needs_laptop"], t["id"]
    assert {"dark_mode", "calculate", "snap_left", "show_desktop"} <= flagged and "mkdir" not in flagged


def test_a_task_is_added_by_adding_an_entry_with_an_existing_handler(tmp_path):
    data = json.loads(ST.catalogue_path().read_text(encoding="utf-8"))
    data["tasks"].append({"id": "make_notes_folder", "name": "A folder named Notes", "group": "files", "class": "A", "phrases": ["make my notes folder"],
                          "examples": [{"say": "make my notes folder", "args": {}}],
                          "how": {"handler": "file_op", "steps": [{"op": "mkdir", "target": "Documents", "path": "Notes"}]}, "verify": "the folder exists", "needs_laptop": False})
    path = tmp_path / "extra.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    assert ST.problems_in(data) == [] and ST.match("make my notes folder", path)["id"] == "make_notes_folder"
    assert ST.match("make my notes folder") is None                         # the real file is untouched


def test_a_bad_entry_is_reported_and_skipped_never_fatal(tmp_path):
    data = json.loads(ST.catalogue_path().read_text(encoding="utf-8"))
    data["tasks"] += [{"id": "broken", "class": "Z", "phrases": ["(unclosed"], "how": {"handler": "nope"}}, {"id": "mkdir"}, "not an entry"]
    path = tmp_path / "bad.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    bad = ST.problems_in(data)
    assert any(s.startswith("broken:") for s in bad) and any("used twice" in s for s in bad) and any("no usable id" in s for s in bad)
    tasks = ST.load(path)
    assert "broken" not in {t["id"] for t in tasks} and [t["id"] for t in tasks].count("mkdir") == 1       # the first of two equal ids stays
    assert ST.match("create a folder X in Documents", path)["id"] == "mkdir"
    assert ST.load() and ST.problems() == []                                  # the real file loads again afterwards


# ================================================================================================================ how requests are read
@pytest.mark.parametrize("text,want", [
    ("delete old.txt", "trash"), ("delete old.txt for good", "delete_permanent"), ("permanently delete old.txt in Documents", "delete_permanent"),
    ("delete old.txt from Downloads for good", "delete_permanent"), ("erase draft.docx", "delete_permanent"),
    ("find tax2025.pdf and show it in Explorer", "find_and_show"), ("find tax2025.pdf", "find_by_name"), ("search for best pasta recipe", "search_web"),
    ("search for the file budget in Documents", "find_by_name"), ("open budget.xlsx", "open_file"), ("open the website example.com", "open_site"),
    ("find files bigger than 100 mb in Downloads", "find_filtered"), ("tidy up the downloads folder", "sort_downloads"), ("clean up my PC", "cleanup"),
    ("empty the recycle bin", "empty_bin"), ("show the desktop", "show_desktop"), ("send an email to the bank", "send_message"),
    ("please, can you create a folder Projects in Documents?", "mkdir"), ("Jarvis, dark mode", "dark_mode")])
def test_conflicts_between_phrases_are_settled_by_class_then_by_the_more_specific_words(text, want):
    assert ST.match(text)["id"] == want


@pytest.mark.parametrize("text", ["delete everything", "delete those", "delete old stuff", "delete them", "remove the rest", "move those into Documents", "delete all files",
                                  "rename it to final"])
def test_a_vague_item_is_a_question_not_a_guess(text):
    p = plan_of(text)
    assert p.status == "clarify" and "Which one" in p.question, (text, p.status, p.steps)


@pytest.mark.parametrize("text", ["delete C:\\Windows", "delete ..\\x.txt", "create a folder Projects in D:", "copy a.txt into C:\\Windows", "move a.txt to Windows\\System32"])
def test_a_place_outside_the_known_folders_is_asked_about_before_anything_runs(text):
    p = plan_of(text)
    assert p.status in ("clarify", "fallback") and not p.steps, (text, p.status, p.steps)


def test_a_folder_the_catalogue_cannot_place_keeps_the_old_message():
    p = plan_of("make a folder test-live on D:")
    assert p.status == "fallback" and "Documents, Desktop" in p.problems[0]


def test_unmatched_requests_still_reach_the_model():
    asked = []
    D.decide("what do you think about the new moon landing plans", PROFILES, post=lambda body: asked.append(1) or '{"steps":[{"tool":"answer"}]}')
    assert asked


def test_a_broken_catalogue_never_stops_the_decider(monkeypatch):
    monkeypatch.setattr(ST, "plan_for", lambda r: 1 / 0)
    asked = []
    D.decide("rename report.docx to final", PROFILES, post=lambda body: asked.append(1) or '{"steps":[{"tool":"answer"}]}')
    assert asked
