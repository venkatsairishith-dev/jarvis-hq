"""Learning layer 3: tasks that worked three times, observed on screen, become STAGED examples for the decider; nothing more, and never active."""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from jarvis.plugins.desktop import learn, skills

NOTEPAD = [{"tool": "open_app", "app": "notepad"}]
CALC = [{"tool": "open_app", "app": "calculator"}]


def write(tmp, name, request, steps, status="completed", passed=True, uncertainty="", model="qwen3-4b", plan_status="ok", ran=None):
    ran = ran if ran is not None else [{"tool": s["tool"], "passed": passed, "uncertainty": uncertainty} for s in steps]
    (tmp / f"{name}.json").write_text(json.dumps({"id": name, "request": request, "status": status,
                                                 "plan": {"status": plan_status, "steps": steps, "model": model}, "steps": ran}))


@pytest.fixture
def dirs(tmp_path):
    tasks, root = tmp_path / "tasks", tmp_path / "skills"
    tasks.mkdir()
    return tasks, root


def three(tasks, request="open the notepad app", steps=NOTEPAD, prefix="a", **kw):
    for i in range(3):
        write(tasks, f"{prefix}{i}", request, steps, **kw)


def test_a_plan_that_worked_three_times_observed_is_a_candidate(dirs):
    tasks, root = dirs
    write(tasks, "x0", "open the notepad app", NOTEPAD)
    write(tasks, "x1", "open the notepad app", NOTEPAD)
    write(tasks, "x2", "Open the Notepad app", NOTEPAD)
    got = learn.candidates(tasks, root)
    assert len(got) == 1 and got[0]["request"] == "open the notepad app" and got[0]["runs"] == 3
    assert got[0]["plan"] == {"steps": NOTEPAD}


def test_fewer_than_three_runs_is_not_enough(dirs):
    tasks, root = dirs
    for i in range(2):
        write(tasks, f"a{i}", "open the notepad app", NOTEPAD)
    assert learn.candidates(tasks, root) == []


@pytest.mark.parametrize("kw", [
    {"status": "failed"},                                         # it did not finish
    {"passed": False},                                            # a step did not pass its check
    {"uncertainty": "reported ok, not observed: no window seen"},  # passed on the node's word alone
    {"model": "rule"},                                            # a rule made the plan: nothing to teach
    {"plan_status": "clarify"},
])
def test_evidence_that_does_not_count(dirs, kw):
    tasks, root = dirs
    three(tasks, **kw)
    assert learn.candidates(tasks, root) == []


@pytest.mark.parametrize("steps", [
    [{"tool": "type_text", "app": "notepad", "text": "hello"}],     # typed text is content, not a skill
    [{"tool": "browser", "action": "click", "target": "ok"}],
    [{"tool": "troubleshoot", "app": "word"}],
    [{"tool": "research", "query": "solar"}],
    [{"tool": "stop"}],
])
def test_only_safe_to_teach_tools_are_mined(dirs, steps):
    tasks, root = dirs
    three(tasks, steps=steps)
    assert learn.candidates(tasks, root) == []


@pytest.mark.parametrize("request_text", [
    "open the notepad app and mail me@example.com",
    "open the file for account 123456789",
    "open my password manager",
    "open the notepad app " + "x" * 200,
    "open the notepad\napp",
])
def test_requests_that_look_private_are_never_taught(dirs, request_text):
    tasks, root = dirs
    three(tasks, request=request_text)
    assert learn.candidates(tasks, root) == []


def test_what_is_already_taught_or_already_proposed_is_not_proposed_again(dirs, monkeypatch):
    tasks, root = dirs
    three(tasks)
    monkeypatch.setattr(skills, "active", lambda r=root: {"sites": {}, "examples": [{"request": "Open the Notepad app", "plan": {"steps": NOTEPAD}}]})
    assert learn.candidates(tasks, root) == []
    monkeypatch.undo()
    first = learn.propose_mined(tasks, root, now=1_000_000.0)
    assert first["proposed"] == "v1"
    assert learn.candidates(tasks, root) == []                      # proposed once: not again


def test_staging_changes_nothing_that_is_active_and_is_limited(dirs):
    tasks, root = dirs
    three(tasks, request="open the notepad app", prefix="n")
    three(tasks, request="start the calculator please", steps=CALC, prefix="c")
    dry = learn.propose_mined(tasks, root, now=1_000_000.0, dry_run=True)
    assert dry["proposed"] is None and len(dry["would_propose"]) == 2 and not (root / "registry.json").exists()
    res = learn.propose_mined(tasks, root, now=1_000_000.0)
    assert res["proposed"] == "v1" and res["examples"] == 2 and "promote v1" in res["next"]
    reg = json.loads((root / "registry.json").read_text())
    assert reg["active"] is None and reg["versions"]["v1"]["status"] == "staged"        # never active by itself
    body = json.loads((root / "versions" / "v1.json").read_text())
    assert set(body["content"]) == {"sites", "examples"} and body["content"]["sites"] == {} and len(body["content"]["examples"]) == 2
    assert "observed on screen" in body["reason"]
    # one proposal a day
    three(tasks, request="open the documents folder", steps=[{"tool": "open_file", "name": "documents"}], prefix="d")
    again = learn.propose_mined(tasks, root, now=1_000_000.0 + 3600)
    assert again["proposed"] is None and "24 hours" in again["why"] and again["waiting"] == 1
    later = learn.propose_mined(tasks, root, now=1_000_000.0 + 90000)
    assert later["proposed"] == "v2"


def test_a_candidate_the_gate_would_refuse_never_appears(dirs):
    tasks, root = dirs
    three(tasks, steps=[{"tool": "open_app", "app": "notepad", "evil_field": "x"}])
    assert learn.candidates(tasks, root) == []


def test_damaged_records_are_skipped(dirs):
    tasks, root = dirs
    three(tasks)
    (tasks / "bad.json").write_text("{not json")
    (tasks / "list.json").write_text("[1,2]")
    assert len(learn.candidates(tasks, root)) == 1
    assert learn.candidates(Path(tasks) / "missing", root) == []


def test_the_owner_command_line_lists_and_stages(dirs, monkeypatch, capsys):
    tasks, root = dirs
    three(tasks)
    from jarvis.plugins.desktop import coordinator as coord
    monkeypatch.setattr(coord, "TASKS", tasks)
    monkeypatch.setattr(skills, "ROOT", root)
    monkeypatch.setattr(learn, "skills", skills)
    assert skills._cli(["skills.py", "candidates"]) == 0
    assert json.loads(capsys.readouterr().out)[0]["request"] == "open the notepad app"
    monkeypatch.setattr(learn.skills, "ROOT", root)
    out = learn.propose_mined(tasks, root, now=2_000_000.0)
    assert out["proposed"] == "v1"


def test_a_proposal_holds_at_most_three_examples_and_the_rest_wait_for_the_next_day(dirs):
    tasks, root = dirs
    apps = ["notepad", "calculator", "paint", "word", "excel"]
    for i, app in enumerate(apps):
        three(tasks, request=f"start the {app} program", steps=[{"tool": "open_app", "app": app}], prefix=f"p{i}")
    res = learn.propose_mined(tasks, root, now=1_000_000.0)
    assert res["examples"] == 3
    assert len(json.loads((root / "versions" / "v1.json").read_text())["content"]["examples"]) == 3
    assert len(learn.candidates(tasks, root)) == 2
