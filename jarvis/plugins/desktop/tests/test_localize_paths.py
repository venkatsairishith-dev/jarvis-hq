r"""A full path to something directly inside the owner's own known folders is the same request as "<name> in <Folder>" (laptop 2026-10-28: "Delete C:\Users\me\Documents\a.txt"
was answered "I can only save into Documents, Desktop, ..." in 9 of 40 tasks of one sweep). Anything else is still refused or asked about."""
import pytest

from jarvis.plugins.desktop import composite, simple_tasks as S

HOME = r"C:\Users\tester"


@pytest.fixture(autouse=True)
def home(monkeypatch):
    monkeypatch.setenv("USERPROFILE", HOME)
    monkeypatch.setenv("HOME", HOME)
    monkeypatch.setattr(composite, "_HOME_ITEM", None)


@pytest.mark.parametrize("text,want", [
    (rf"Delete {HOME}\Documents\a.txt", "Delete a.txt in Documents"),
    (rf"Delete the file {HOME}\Documents\del-r1.txt.", "Delete the file del-r1.txt in Documents."),
    (rf"Delete {HOME}\Documents\a.txt and {HOME}\Documents\b.txt please", "Delete a.txt in Documents and b.txt in Documents please"),
    (rf"Delete all .tmp files in {HOME}\Documents\tmpdir-x but keep the rest", "Delete all .tmp files in tmpdir-x in Documents but keep the rest"),
    (rf"copy {HOME}/Downloads/x.pdf to the Desktop", "copy x.pdf in Downloads to the Desktop"),
    (rf"open {HOME}\Downloads", "open Downloads folder"),
])
def test_a_path_inside_one_of_his_known_folders_becomes_name_in_folder(text, want):
    assert composite.localize_paths(text) == want


@pytest.mark.parametrize("text", [
    rf"delete {HOME}\Documents\sub\deep.txt",                  # deeper than directly inside
    r"Delete C:\Windows\notepad.exe", r"Delete D:\data\a.txt",
    r"Delete C:\Users\someoneelse\Documents\a.txt",            # another profile
    rf"delete {HOME}\AppData\Roaming\x.txt", "delete a.txt in Documents",
])
def test_everything_else_is_left_exactly_as_it_was(text):
    assert composite.localize_paths(text) == text


def test_the_catalogue_acts_on_the_inside_path_and_still_asks_about_the_others():
    inside = S.plan_for(rf"Delete the file {HOME}\Documents\ghost.txt")
    assert inside and inside["status"] == "ok" and inside["task"] == "trash"
    for outside in (r"Delete C:\Windows\notepad.exe", rf"delete {HOME}\Documents\sub\deep.txt", r"Delete D:\data\a.txt"):
        got = S.plan_for(outside)
        assert got and got["status"] == "clarify" and "I can only" in got["question"], outside
