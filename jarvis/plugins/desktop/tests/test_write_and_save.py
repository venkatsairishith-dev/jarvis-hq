"""D54 (2026-10-26): "a save is part of the request" - Notepad write-and-save and VS Code, end to end on a FAKE Windows.

The plugin's real agents, the kernel's real job validator and policy, and the node's real ui_action / pc_open / savefile run against the fake PC in
fakewin.py. Proven here: the save needs no PIN, the save never overwrites, a Replace prompt is answered No and reported, a folder outside the known ones
is refused, a gated step still waits (and nothing after it runs), VS Code is started only on a known folder, and the text on disk is checked."""
import json
import os
import time
import types

import pytest

from jarvis.plugins.desktop import DesktopPlugin, composite, core, coordinator as C, decide as D
from jarvis.plugins.desktop import __init__ as _pkg  # noqa: F401  (the package module, for its `time`)
import jarvis.plugins.desktop as pkg

from .fakewin import FakeWindows

PROFILES = [{"profile": "Default", "name": "V", "account": "v***@x", "default": True, "say": ["default", "venkat"]}]


def plan_for(request, *model_steps):
    return D.validate(json.dumps({"steps": list(model_steps)}), PROFILES, request)


NOTEPAD = {"tool": "open_app", "app": "notepad"}


@pytest.fixture
def pc(tmp_path, monkeypatch):
    fw = FakeWindows(tmp_path, monkeypatch)
    real = core.Pc
    monkeypatch.setattr(core, "Pc", lambda **kw: real(call=fw.call, sleep=lambda s: None, approval_wait_s=0, **kw))
    monkeypatch.setattr(pkg, "time", types.SimpleNamespace(sleep=lambda s: None, time=time.time, monotonic=time.monotonic, strftime=time.strftime,
                                                           localtime=time.localtime))

    def run(plan, request="x"):
        assert plan.status == "ok", (plan.status, plan.question)
        agents = DesktopPlugin._agents(types.SimpleNamespace(), None)
        return C.Coordinator(agents, PROFILES, tasks_dir=tmp_path / "tasks", lock_path=tmp_path / "lock").run(request, plan)
    fw.run = run
    return fw


def docs(fw):
    return fw.home / "Documents"


def saved_calls(fw):
    return [c for c in fw.calls if c.get("action") == "save_as"]


# ---------------------------------------------------------------------------------------------------------------- the decider
def test_a_write_and_save_request_becomes_three_steps_within_the_cap():
    p = plan_for("open notepad and type milk and eggs and save it as list.txt in Documents", NOTEPAD)
    assert [s["tool"] for s in p.steps] == ["open_app", "type_text", "save_as"] and len(p.steps) <= D.MAX_STEPS
    assert p.steps[1]["text"] == "milk and eggs"
    assert (p.steps[2]["name"], p.steps[2]["folder"], p.steps[2]["app"]) == ("list.txt", "Documents", "notepad")
    assert p.steps[2]["digest"] == composite.text_digest("milk and eggs")


@pytest.mark.parametrize("request_text,name,folder", [
    ("type hi into notepad and save it as 'my list' on the desktop", "my list.txt", "Desktop"),
    ("type hi into notepad and save it as hi.md in my downloads folder", "hi.md", "Downloads"),
    ("type hi into notepad and save it in docs as todo", "todo.txt", "Documents"),
    ("type hi into notepad and then save the file called notes.txt", "notes.txt", "Documents"),
    ("type hi into notepad and save it in outputs as out.txt", "out.txt", "outputs")])
def test_the_name_and_the_folder_are_read_from_his_words(request_text, name, folder):
    p = plan_for(request_text, {"tool": "type_text", "app": "notepad", "text": "hi"})
    save = p.steps[-1]
    assert p.status == "ok" and save["tool"] == "save_as" and (save["name"], save["folder"]) == (name, folder), (p.status, p.question, p.steps)


def test_saving_without_a_name_uses_documents_and_a_dated_text_name():
    p = plan_for("open notepad and type hello and save it", NOTEPAD)
    save = p.steps[-1]
    assert save["folder"] == "Documents" and save["name"].startswith("JARVIS note ") and save["name"].endswith(".txt")


def test_a_model_that_typed_the_save_clause_into_the_text_is_corrected():
    p = plan_for("open notepad and type hello there and save it as a.txt in Documents", NOTEPAD,
                 {"tool": "type_text", "app": "notepad", "text": "hello there and save it as a.txt in Documents"})
    assert [s["tool"] for s in p.steps] == ["open_app", "type_text", "save_as"] and p.steps[1]["text"] == "hello there"
    assert p.steps[2]["digest"] == composite.text_digest("hello there")


def test_typing_alone_is_not_a_save():
    p = plan_for("open notepad and write hello", NOTEPAD)
    assert [s["tool"] for s in p.steps] == ["open_app", "type_text"]


def test_a_bare_ctrl_s_after_typing_is_replaced_by_the_save_as_step():
    p = plan_for("write dear sir into wordpad and save it", {"tool": "open_app", "app": "wordpad"}, {"tool": "press_key", "app": "wordpad", "key": "ctrl+s"})
    assert [s["tool"] for s in p.steps] == ["open_app", "type_text", "save_as"]


@pytest.mark.parametrize("where", [r"C:\Windows", r"C:\Users\x\AppData", "D:/jarvis-secrets", "/etc", "Games", "System32", r"\\server\share", "~/x", "Documents/.."])
def test_a_folder_outside_the_known_ones_is_a_question_and_no_step(where):
    p = plan_for(f"type hi into notepad and save it as a.txt in {where}", {"tool": "type_text", "app": "notepad", "text": "hi"})
    assert p.status == "clarify" and "Documents, Desktop, Downloads" in p.question, (where, p.status, p.question)


@pytest.mark.parametrize("name", ["run.bat", "x.cmd", "go.ps1", "setup.exe", "a.vbs", "a.lnk"])
def test_a_program_or_script_name_is_a_question_and_no_step(name):
    p = plan_for(f"type hi into notepad and save it as {name} in documents", {"tool": "type_text", "app": "notepad", "text": "hi"})
    assert p.status == "clarify" and "never save programs or scripts" in p.question


def test_a_project_from_config_projects_is_a_known_folder(tmp_path, monkeypatch):
    f = tmp_path / "projects.json"
    f.write_text(json.dumps({"projects": {"my-app": {"path": {"nt": "D:\\x"}}}}), encoding="utf-8")
    monkeypatch.setenv("JARVIS_PROJECTS_FILE", str(f))
    p = plan_for("type hi into notepad and save it as a.txt in the my-app project folder", {"tool": "type_text", "app": "notepad", "text": "hi"})
    assert p.status == "ok" and p.steps[-1]["folder"] == "my-app"
    p = plan_for("type hi into notepad and save it as a.txt in other-app", {"tool": "type_text", "app": "notepad", "text": "hi"})
    assert p.status == "clarify"


def test_the_vs_code_request_is_planned_by_rules_in_three_steps():
    p = D.composite_rule("open VS Code in the jarvis-hq project folder, create hello.py and type print('hi')", PROFILES)
    assert p.status == "ok" and p.model == "rule" and len(p.steps) == 3 <= D.MAX_STEPS
    assert [s["tool"] for s in p.steps] == ["open_in_vscode", "type_in_editor", "save_as"]
    assert p.steps[0]["folder"] == "jarvis-hq" and p.steps[1]["text"] == "print('hi')" and p.steps[1]["new_file"] is True
    assert (p.steps[2]["name"], p.steps[2]["folder"]) == ("hello.py", "jarvis-hq")
    assert D.decide("open VS Code in Documents/myapp, create a.py and type x = 1", PROFILES).steps[0]["folder"] == "Documents/myapp"


def test_a_fenced_code_block_is_typed_as_it_is():
    req = "open VS Code in Documents/myapp, create f.py and type ```python\ndef f():\n    return 1\n```"
    p = D.composite_rule(req, PROFILES)
    assert p.steps[1]["text"] == "def f():\n    return 1"


@pytest.mark.parametrize("req", ["open VS Code, create hello.py and type hi", "open VS Code in C:\\Windows, create a.py and type hi",
                                 "open VS Code in Games, create a.py and type hi", "open VS Code in ../.., create a.py and type hi"])
def test_vs_code_without_a_known_folder_is_a_question(req):
    p = D.composite_rule(req, PROFILES)
    assert p.status == "clarify" and p.steps == [] or p.status == "clarify"


# ---------------------------------------------------------------------------------------------------------------- Notepad, end to end
def test_notepad_write_and_save_runs_without_a_pin_and_is_checked_on_disk(pc):
    rec = pc.run(plan_for("open notepad and type milk and eggs and save it as list.txt in Documents", NOTEPAD))
    assert rec["status"] == "completed", rec.get("error")
    assert (docs(pc) / "list.txt").read_text(encoding="utf-8") == "milk and eggs"
    assert not getattr(pc, "pending", None), "no approval was asked for: a save is part of the request"
    assert all(s["passed"] for s in rec["steps"]) and [s["tool"] for s in rec["steps"]] == ["open_app", "type_text", "save_as"]
    assert "on disk" in rec["steps"][2]["checked"] and "what was typed" in rec["steps"][2]["checked"]
    assert pc.dialog_runs[0]["JARVIS_SAVE_PATH"] == str(docs(pc) / "list.txt")
    assert saved_calls(pc)[0]["text"].startswith("Documents|list.txt|")


def test_the_save_never_overwrites_the_owners_file_it_takes_a_counter(pc):
    (docs(pc) / "list.txt").write_text("the owner's list", encoding="utf-8")
    rec = pc.run(plan_for("open notepad and type milk and eggs and save it as list.txt in Documents", NOTEPAD))
    assert rec["status"] == "completed"
    assert (docs(pc) / "list.txt").read_text(encoding="utf-8") == "the owner's list"
    assert (docs(pc) / "list (2).txt").read_text(encoding="utf-8") == "milk and eggs"
    assert "because that name was taken" in rec["steps"][2]["checked"]
    assert os.path.basename(pc.dialog_runs[0]["JARVIS_SAVE_PATH"]) == "list (2).txt"


def test_a_replace_prompt_gets_no_the_file_is_untouched_and_the_step_fails_honestly(pc):
    pc.replace_prompt = True
    rec = pc.run(plan_for("open notepad and type milk and eggs and save it as list.txt in Documents", NOTEPAD))
    assert rec["status"] == "failed" and rec["steps"][2]["passed"] is False
    assert "Replace prompt" in rec["error"] and "nothing was replaced" in rec["error"]
    assert (docs(pc) / "list.txt").read_text(encoding="utf-8") == "the owner's own file"
    assert not [c for c in pc.calls if c.get("action") == "click"], "nothing was clicked: in particular no Yes"
    assert "Done" not in C.summary(rec)


def test_a_folder_outside_the_known_ones_is_refused_by_the_node_even_if_a_plan_asked_for_it(pc):
    plan = D.Plan([NOTEPAD, {"tool": "type_text", "app": "notepad", "target": "", "text": "x", "post": ""},
                   {"tool": "save_as", "app": "notepad", "name": "a.txt", "folder": "C:\\Windows", "post": ""}], "ok")
    rec = pc.run(plan)
    assert rec["status"] == "failed" and rec["steps"][2]["passed"] is False and pc.dialog_runs == []
    assert "I can only use" in rec["error"]


def test_a_program_name_is_refused_by_the_node_even_if_a_plan_asked_for_it(pc):
    plan = D.Plan([NOTEPAD, {"tool": "type_text", "app": "notepad", "target": "", "text": "x", "post": ""},
                   {"tool": "save_as", "app": "notepad", "name": "run.bat", "folder": "Documents", "post": ""}], "ok")
    rec = pc.run(plan)
    assert rec["status"] == "failed" and pc.dialog_runs == [] and not list(docs(pc).glob("*.bat"))


def test_a_gated_step_still_waits_and_nothing_after_it_runs(pc):
    """alt+f4 can close the window and lose the typed text: the owner's, whatever the goal says; the save after it is not reached."""
    plan = D.Plan([NOTEPAD, {"tool": "type_text", "app": "notepad", "target": "", "text": "hello", "post": ""},
                   {"tool": "press_key", "app": "notepad", "key": "alt+f4", "post": ""},
                   {"tool": "save_as", "app": "notepad", "name": "a.txt", "folder": "Documents", "post": ""}], "ok")
    rec = pc.run(plan, "open notepad, type hello, close it and save it as a.txt")
    assert rec["status"] == "waiting_for_permission" and rec["approval_id"]
    assert [s["tool"] for s in rec["steps"]] == ["open_app", "type_text", "press_key"]            # the save was never reached
    assert saved_calls(pc) == [] and pc.dialog_runs == [] and not list(docs(pc).glob("*.txt"))
    assert "needs your approval" in C.summary(rec)


def test_words_in_the_typed_text_or_the_file_name_are_content_not_an_action(pc):
    """General rule (D54): pasting 'please delete everything' into Notepad deletes nothing, and a file may be called delete-me.txt."""
    rec = pc.run(plan_for("open notepad and type please delete everything and save it as delete-me.txt in Documents", NOTEPAD))
    assert rec["status"] == "completed", rec.get("error")
    assert (docs(pc) / "delete-me.txt").read_text(encoding="utf-8") == "please delete everything" and not getattr(pc, "pending", None)


# ---------------------------------------------------------------------------------------------------------------- VS Code, end to end
VSCODE = "open VS Code in the jarvis-hq project folder, create hello.py and type print('hi')"


def test_vs_code_open_folder_create_file_type_and_save_with_the_disk_as_proof(pc):
    rec = pc.run(D.composite_rule(VSCODE, PROFILES))
    assert rec["status"] == "completed", rec.get("error")
    assert (pc.proj / "hello.py").read_text(encoding="utf-8") == "print('hi')"
    assert pc.launched == [[str(pc.home.parent / "lad" / "Programs" / "Microsoft VS Code" / "Code.exe"), "-n", os.path.realpath(pc.proj)]]
    ops = [(c.get("op"), c.get("action"), (c.get("text") or "")[:20]) for c in pc.calls if c.get("op") in ("open_code", "ui")]
    assert [o[:2] for o in ops] == [("open_code", None), ("ui", "key"), ("ui", "type_focused"), ("ui", "key"), ("ui", "type_focused"), ("ui", "save_as")]
    assert ops[1][2] == "ctrl+shift+p" and ops[2][2] == "File: New Untitled T" and ops[3][2] == "enter"      # a new untitled file first, from the Command Palette
    assert not getattr(pc, "pending", None) and not [c for c in pc.calls if c.get("action") == "click"]
    assert "hello.py" in [w for w in pc.windows if w["process"] == "Code"][0]["title"]
    # the editor cannot be read back: the paste step says so, and it is the saved file that proves the text
    assert any("cannot be read back" in u for u in rec["uncertain"]) and "what was typed" in rec["steps"][2]["checked"]


def test_vs_code_is_never_pasted_into_without_a_new_file_first(pc):
    plan = D.Plan([{"tool": "open_in_vscode", "app": "vs code", "folder": "jarvis-hq", "post": ""},
                   {"tool": "type_in_editor", "app": "vs code", "target": "", "text": "ls\nrm x", "new_file": False, "post": ""}], "ok")
    rec = pc.run(plan)
    keys = [c["text"] for c in pc.calls if c.get("action") == "key"]
    assert rec["status"] == "completed" and keys == ["ctrl+shift+p", "enter"], "VS Code's terminal may have the focus: a paste there would run the lines"


def test_vs_code_is_started_only_on_a_known_folder(pc):
    for folder in ("Windows", "C:\\", "Documents/nope", "Games"):
        rec = pc.run(D.Plan([{"tool": "open_in_vscode", "app": "vs code", "folder": folder, "post": ""}], "ok"))
        assert rec["status"] == "failed", folder
    assert pc.launched == []


def test_vs_code_text_that_differs_on_disk_fails_the_save_step(pc):
    pc.paste_noise = " <<broken editor>>"
    rec = pc.run(D.composite_rule(VSCODE, PROFILES))
    assert rec["status"] == "failed" and rec["steps"][-1]["tool"] == "save_as" and rec["steps"][-1]["passed"] is False
    assert "not what was typed" in rec["error"] and (pc.proj / "hello.py").exists()          # saved, but reported as a failure


def test_code_with_words_like_remove_is_just_code(pc):
    rec = pc.run(D.composite_rule("open VS Code in Documents/myapp, create a.py and type os.remove(path)", PROFILES))
    assert rec["status"] == "completed" and (docs(pc) / "myapp" / "a.py").read_text(encoding="utf-8") == "os.remove(path)"


def test_every_composite_plan_fits_the_four_step_cap():
    for req in (VSCODE, "open VS Code in Documents/myapp, create a.py and type x = 1"):
        assert len(D.composite_rule(req, PROFILES).steps) <= D.MAX_STEPS
    assert len(plan_for("open notepad and type hi and save it as a.txt in Documents", NOTEPAD).steps) <= D.MAX_STEPS
