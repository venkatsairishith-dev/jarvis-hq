"""D54 (2026-10-26): Save As and VS Code on the node. Fakes only: fake Windows dialogs (a callable standing in for the PowerShell that drives the Save As
dialog), a fake window lister, a fake launcher, real temporary folders. No Windows, no PowerShell, no VS Code.

What must hold: the save never overwrites, a folder outside the known ones is refused, programs and scripts are never saved, a Replace prompt is answered No
(and a Yes is never pressed by anyone), the file is checked on disk, VS Code is the only program started and only on a folder inside the known ones."""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from jarvis_node import jobs, pcopen, savefile as sf, winbackend  # noqa: E402

NOW = lambda s: None  # noqa: E731  (the tests never wait)


@pytest.fixture
def world(tmp_path):
    home = tmp_path / "home"
    folders = {n: home / n.title() for n in ("documents", "desktop", "downloads")}
    for f in folders.values():
        f.mkdir(parents=True)
    out = tmp_path / "outputs"
    out.mkdir()
    proj = tmp_path / "proj"
    (proj / "src").mkdir(parents=True)
    (folders["documents"] / "notes").mkdir()
    (folders["documents"] / ".hidden").mkdir()
    known = {k: str(v) for k, v in folders.items()}
    projects = {"jarvis-hq": {"path": {os.name: str(proj)}}}
    roots = sf.folder_roots(known=known, projects=projects, outputs=str(out))
    return {"roots": roots, "docs": folders["documents"], "desk": folders["desktop"], "out": out, "proj": proj, "tmp": tmp_path}


def saver(content=None, created=None):
    """The fake PowerShell: 'presses Save' by writing the file at the path the script was given (what the real dialog does)."""
    seen = []

    def run(env):
        seen.append(dict(env))
        if content is not None:
            with open(env["JARVIS_SAVE_PATH"], "w", encoding="utf-8", newline="") as fh:
                fh.write(content)
    run.seen = seen
    return run


def windows(*titles, proc="notepad"):
    return lambda: [{"title": t, "process": proc, "hwnd": i + 1} for i, t in enumerate(titles)]


def do(world, text, run, lister=None, proc="notepad"):
    return sf.save_as(f"{proc}|", {"text": text}, run=run, lister=lister or windows("hello.txt - Notepad"), roots=world["roots"], wait=NOW, timeout_s=0.01)


# ---------------------------------------------------------------------------------------------------------------- folders
def test_the_roots_are_the_known_folders_the_outputs_folder_and_the_projects(world):
    assert set(world["roots"]) == {"documents", "desktop", "downloads", "outputs", "jarvis-hq"}


@pytest.mark.parametrize("spec", [r"C:\Windows", r"C:\Windows\System32", "D:/jarvis-secrets", "/etc", "~", r"\\server\share", "Documents/../x", "../x",
                                  "System32", "Windows", "Documents/.hidden", "Documents/nope", "", "Program Files", "AppData/Roaming/Microsoft/Windows/Start Menu/Programs/Startup"])
def test_a_folder_outside_the_known_ones_is_refused(world, spec):
    with pytest.raises(sf.SafeError):
        sf.resolve_folder(spec, world["roots"])


def test_the_known_folders_projects_and_folders_inside_them_are_allowed(world):
    assert sf.resolve_folder("Documents", world["roots"])[0] == os.path.realpath(world["docs"])
    assert sf.resolve_folder("my documents", world["roots"])[1] == "Documents"
    assert sf.resolve_folder("documents/NOTES", world["roots"])[1] == "Documents/notes"                     # case does not matter, the real name is used
    assert sf.resolve_folder("outputs", world["roots"])[0] == os.path.realpath(world["out"])
    assert sf.resolve_folder("jarvis-hq/src", world["roots"])[0] == os.path.realpath(world["proj"] / "src")


@pytest.mark.skipif(os.name == "nt", reason="symlinks need a privilege on Windows")
def test_a_link_that_leaves_the_root_is_refused(world):
    (world["docs"] / "escape").symlink_to(world["tmp"])
    with pytest.raises(sf.SafeError):
        sf.resolve_folder("Documents/escape", world["roots"])


def test_a_secrets_folder_inside_a_root_is_refused(world):
    (world["docs"] / "jarvis-secrets").mkdir()
    with pytest.raises(sf.SafeError):
        sf.resolve_folder("Documents/jarvis-secrets", world["roots"])


# ---------------------------------------------------------------------------------------------------------------- names
@pytest.mark.parametrize("name", ["run.bat", "x.cmd", "x.ps1", "setup.exe", "a.vbs", "a.lnk", "a.msi", "a.reg", "a.js.exe", "a.hta", "a.scr"])
def test_programs_and_scripts_are_never_saved(name):
    with pytest.raises(sf.SafeError):
        sf.safe_name(name)


@pytest.mark.parametrize("name", ["", ".txt", "..\\a.txt", "a/b.txt", "a:b.txt", "con.txt", "NUL", "a.txt.", "x" * 90 + ".txt", "a|b.txt", "../a.txt", "notes.unknown", "secrets.env"])
def test_a_name_that_is_not_plain_is_refused(name):
    with pytest.raises(sf.SafeError):
        sf.safe_name(name)


def test_plain_text_and_code_names_pass():
    for n in ("notes.txt", "my list.md", "hello.py", "App (2).js", "a-b_c.json", "index.html", "Main.java", "x.cpp"):
        assert sf.safe_name(n) == n


def test_a_name_that_exists_gets_a_counter_never_the_same_name(world):
    (world["docs"] / "hello.txt").write_text("mine", encoding="utf-8")
    (world["docs"] / "hello (2).txt").write_text("mine too", encoding="utf-8")
    assert sf.free_name(str(world["docs"]), "hello.txt") == ("hello (3).txt", True)
    assert sf.free_name(str(world["docs"]), "fresh.txt") == ("fresh.txt", False)


# ---------------------------------------------------------------------------------------------------------------- the save
def test_save_as_checks_the_disk_the_window_and_the_text(world):
    typed = "line one\r\nline two  \r\n"
    run = saver(content=typed)
    digest = sf.text_digest("line one\nline two")          # outside the f-string: Python 3.11 refuses a backslash inside one
    out = do(world, f"Documents|hello.txt|{digest}", run)
    assert out["saved"] == "hello.txt" and out["folder"] == "Documents" and out["file_ok"] is True and out["title_ok"] is True and out["content_ok"] is True
    assert run.seen[0]["JARVIS_SAVE_PATH"] == str(world["docs"] / "hello.txt") or os.path.samefile(run.seen[0]["JARVIS_SAVE_PATH"], world["docs"] / "hello.txt")
    assert run.seen[0]["JARVIS_UI_PROC"] == "notepad"


def test_a_save_never_overwrites_an_existing_file(world):
    mine = world["docs"] / "hello.txt"
    mine.write_text("the owner's own text", encoding="utf-8")
    run = saver(content="typed")
    out = do(world, "Documents|hello.txt", run, lister=windows("hello (2).txt - Notepad"))
    assert out["saved"] == "hello (2).txt" and out["renamed"] is True
    assert mine.read_text(encoding="utf-8") == "the owner's own text"                                          # untouched
    assert (world["docs"] / "hello (2).txt").read_text(encoding="utf-8") == "typed"
    assert os.path.basename(run.seen[0]["JARVIS_SAVE_PATH"]) == "hello (2).txt"


def test_the_script_is_never_given_a_path_that_exists(world):
    (world["docs"] / "a.txt").write_text("x", encoding="utf-8")
    seen_exists = []

    def run(env):
        seen_exists.append(os.path.exists(env["JARVIS_SAVE_PATH"]))
        open(env["JARVIS_SAVE_PATH"], "w").close()
    do(world, "Documents|a.txt", run, lister=windows("a (2).txt - Notepad"))
    assert seen_exists == [False]


def test_a_replace_prompt_means_no_and_nothing_replaced_and_it_is_reported(world):
    target = world["docs"] / "race.txt"

    def run(env):                                              # the fake dialog: the file appears between our check and the Save, then the prompt shows
        target.write_text("owner's", encoding="utf-8")
        raise RuntimeError("ui-action-failed:replace prompt")
    with pytest.raises(sf.SafeError) as e:
        do(world, "Documents|race.txt", run)
    assert e.value.code == "replace" and "pressed No" in e.value.public and "nothing was replaced" in e.value.public
    assert target.read_text(encoding="utf-8") == "owner's"


@pytest.mark.parametrize("reason,code", [("path exists", "exists"), ("no save dialog", "no_dialog"), ("save dialog error", "dialog_error"),
                                         ("no file name box", "no_box"), ("something else entirely", "failed")])
def test_dialog_failures_are_reported_in_fixed_words(world, reason, code):
    def run(env):
        raise RuntimeError("ui-action-failed:" + reason)
    with pytest.raises(sf.SafeError) as e:
        do(world, "Documents|a.txt", run)
    assert e.value.code == code
    assert not (world["docs"] / "a.txt").exists()


def test_a_folder_outside_the_known_ones_is_refused_before_any_dialog_is_touched(world):
    called = []
    for text in (r"C:\Windows|a.txt", "Documents/../..|a.txt", "System32|a.txt", "Documents|run.bat", "Documents|a.txt|nothex12"):
        with pytest.raises(sf.SafeError):
            do(world, text, lambda env: called.append(env))
    assert called == []


def test_pressing_save_that_leaves_no_file_is_a_failure_not_a_success(world):
    with pytest.raises(sf.SafeError) as e:
        do(world, "Documents|ghost.txt", saver(content=None))
    assert e.value.code == "not_on_disk"


def test_text_that_differs_from_what_was_typed_fails_after_the_save_and_says_so(world):
    with pytest.raises(sf.SafeError) as e:
        do(world, f"Documents|a.txt|{sf.text_digest('what I typed')}", saver(content="something else"))
    assert e.value.code == "content" and e.value.extra["saved"] == "a.txt" and e.value.extra["content_ok"] is False


def test_an_unreadable_file_is_not_compared_and_says_so(world):
    def run(env):
        with open(env["JARVIS_SAVE_PATH"], "wb") as fh:
            fh.write(b"\x80\x81\x82\x83\x84")
    out = do(world, f"Documents|a.txt|{sf.text_digest('x')}", run)
    assert out["file_ok"] is True and out["content_ok"] == "unreadable"


def test_a_window_that_does_not_show_the_name_is_reported_not_hidden(world):
    out = do(world, "Documents|a.txt", saver(content="x"), lister=windows("Untitled - Notepad"))
    assert out["file_ok"] is True and out["title_ok"] is False


def test_the_text_digest_is_the_same_on_the_node_and_in_the_plugin():
    from jarvis.plugins.desktop import composite
    for t in ("print('hi')\n", "a\r\nb  \r\n\r\n", "\ufeffx", "tab\tand space ", ""):
        assert sf.text_digest(t) == composite.text_digest(t)
        assert sf.normalize_text(t) == composite.normalize_text(t)


# ---------------------------------------------------------------------------------------------------------------- a Yes on a replace prompt
CONFIRM = windows("Confirm Save As", "Untitled - Notepad")


def test_a_click_or_enter_that_would_answer_yes_to_a_replace_prompt_is_refused(world):
    assert "Replace prompt" in sf.refuse_replace_answer("click", "notepad|Yes", "", CONFIRM)
    assert "Replace prompt" in sf.refuse_replace_answer("click", "notepad|&Yes", "", CONFIRM)
    assert "Replace prompt" in sf.refuse_replace_answer("click", "notepad|Replace", "", CONFIRM)
    assert "Replace prompt" in sf.refuse_replace_answer("key", "notepad|", "enter", CONFIRM)
    assert "Replace prompt" in sf.refuse_replace_answer("click_at", "notepad|10,10", "", CONFIRM)
    assert sf.refuse_replace_answer("click", "notepad|No", "", CONFIRM) == ""                                    # No and Cancel are fine
    assert sf.refuse_replace_answer("click", "notepad|Cancel", "", CONFIRM) == ""
    assert sf.refuse_replace_answer("click", "notepad|Yes", "", windows("Untitled - Notepad")) == ""            # no such prompt open: an ordinary Yes
    assert sf.refuse_replace_answer("type", "notepad|*", "Yes", CONFIRM) == ""


def test_ui_action_refuses_it_before_the_backend_runs(world):
    ran = []
    backend = {"click": lambda t, p: ran.append(t), "key": lambda t, p: ran.append(t)}
    out = jobs.ui_action({"op": "click", "target": "notepad|Yes", "text": ""}, backend=backend, lister=CONFIRM)
    assert out["ok"] is False and "Replace prompt" in out["error"] and ran == []
    out = jobs.ui_action({"op": "key", "target": "notepad|", "text": "enter"}, backend=backend, lister=CONFIRM)
    assert out["ok"] is False and ran == []
    assert jobs.ui_action({"op": "click", "target": "notepad|No", "text": ""}, backend=backend, lister=CONFIRM)["ok"] is True and ran == ["notepad|No"]


def test_ui_action_reports_a_save_as_refusal_in_the_nodes_own_words_and_keeps_the_observed_facts(world, monkeypatch):
    def refuse(target, params):
        raise sf.SafeError("replace", "a Replace prompt appeared: I pressed No and cancelled, so nothing was replaced")
    out = jobs.ui_action({"op": "save_as", "target": "notepad|", "text": "Documents|a.txt"}, backend={"save_as": refuse}, lister=windows("Untitled - Notepad"))
    assert out == {"ok": False, "error": "a Replace prompt appeared: I pressed No and cancelled, so nothing was replaced"}
    good = lambda t, p: {"observed": "x", "saved": "a.txt", "folder": "Documents", "file_ok": True, "title_ok": True, "content_ok": True, "renamed": False, "secret": "no"}  # noqa: E731
    out = jobs.ui_action({"op": "save_as", "target": "notepad|", "text": "Documents|a.txt"}, backend={"save_as": good}, lister=windows("Untitled - Notepad"))
    assert out["ok"] is True and out["saved"] == "a.txt" and out["file_ok"] is True and "secret" not in out


def test_the_save_script_never_presses_yes_and_uses_save_as_not_save():
    script = winbackend.SAVE_AS
    assert "'^+s'" in script and "'^s'" not in script                       # Save As (never a plain ctrl+s that overwrites a named document)
    assert "'Yes'" not in script and "'Replace'" not in script and "'Overwrite'" not in script
    assert "'No', 'Cancel'" in script and "path exists" in script and "replace prompt" in script
    assert "__PICK__" not in script and "__PICK__" not in winbackend.TYPE_FOCUSED
    assert set(winbackend.BACKEND) == {"launch", "click", "type", "focus", "key", "scroll", "click_at", "minimize", "maximize", "restore", "save_as", "type_focused", "drag", "close"}


# ---------------------------------------------------------------------------------------------------------------- VS Code
@pytest.fixture
def code_exe(tmp_path, monkeypatch):
    base = tmp_path / "lad" / "Programs" / "Microsoft VS Code"
    base.mkdir(parents=True)
    exe = base / "Code.exe"
    exe.write_bytes(b"")
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "lad"))
    monkeypatch.delenv("ProgramFiles", raising=False)
    monkeypatch.delenv("ProgramFiles(x86)", raising=False)
    return str(exe)


def test_vs_code_is_started_on_a_folder_inside_the_known_ones_and_nothing_else(world, code_exe):
    launched = []
    out = sf.open_code("jarvis-hq", "src", roots=world["roots"], launcher=launched.append)
    assert out == {"ok": True, "opened": "jarvis-hq/src"}
    assert launched == [[code_exe, "-n", os.path.realpath(world["proj"] / "src")]]                    # the program and the only two arguments


@pytest.mark.parametrize("root,rel", [("C:\\", ""), ("Windows", ""), ("Documents", "../.."), ("Documents", "nope"), ("", ""), ("D:/jarvis-secrets", ""), ("Documents", ".hidden")])
def test_vs_code_is_not_started_on_a_folder_outside_the_known_ones(world, code_exe, root, rel):
    launched = []
    with pytest.raises(sf.SafeError):
        sf.open_code(root, rel, roots=world["roots"], launcher=launched.append)
    assert launched == []


def test_vs_code_that_is_not_installed_is_reported(world, monkeypatch, tmp_path):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "nothing"))
    monkeypatch.delenv("ProgramFiles", raising=False)
    monkeypatch.delenv("ProgramFiles(x86)", raising=False)
    with pytest.raises(sf.SafeError) as e:
        sf.open_code("Documents", "", roots=world["roots"], launcher=lambda a: None)
    assert e.value.code == "no_code"


def test_only_code_exe_is_ever_the_program(world, code_exe, tmp_path):
    other = tmp_path / "evil.exe"
    other.write_bytes(b"")
    with pytest.raises(sf.SafeError):
        sf.open_code("Documents", "", roots=world["roots"], exe=str(other), launcher=lambda a: None)


def test_pc_open_open_code_goes_through_the_same_path(world, code_exe):
    launched = []
    known = {"documents": str(world["docs"]), "desktop": str(world["desk"]), "downloads": str(world["tmp"])}
    out = pcopen.pc_open({"op": "open_code", "target": "Documents", "path": "notes"}, roots=known, launcher=launched.append)
    assert out["ok"] is True and out["opened"] == "Documents/notes" and launched and launched[0][1] == "-n"
    bad = pcopen.pc_open({"op": "open_code", "target": "Windows", "path": ""}, roots=known, launcher=launched.append)
    assert bad["ok"] is False and len(launched) == 1
    pw = pcopen.pc_open({"op": "open_code", "target": "Documents", "path": "keepass"}, roots=known, launcher=launched.append)       # the sensitive-word check still comes first
    assert pw["ok"] is False and len(launched) == 1
