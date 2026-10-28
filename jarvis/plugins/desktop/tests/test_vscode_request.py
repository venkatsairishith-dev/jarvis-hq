"""The VS Code request parser (composite.parse_vscode). On the laptop (2026-10-28) "Open VS Code on a new folder <path> and create hello.py" never took the path, so the task stopped
to ask "Which folder should VS Code open?" (otsc-vscode-new-folder, 2 of 2 runs)."""
import pytest

from jarvis.plugins.desktop import composite


@pytest.mark.parametrize("text,folder,file", [
    (r"Open VS Code on a new folder C:\Users\venkat\Documents\proj-ab12 and create a file hello.py in it that contains print('hi').", r"C:\Users\venkat\Documents\proj-ab12", "hello.py"),
    ("Open VS Code on a new folder proj-ab12 and create a file hello.py in it", "proj-ab12", "hello.py"),
    (r"open vs code in the folder D:\work\app", r"D:\work\app", ""),
    ("open VS Code on folder ~/code/app and create main.py", "~/code/app", "main.py"),
    ("open vs code in the folder my-app", "my-app", ""),
    (r"open VS Code in C:\Users\venkat\Documents\proj", r"C:\Users\venkat\Documents\proj", ""),
])
def test_the_folder_the_owner_named_is_taken(text, folder, file):
    got = composite.parse_vscode(text)
    assert got["folder"] == folder and got["file"] == file


@pytest.mark.parametrize("text,folder", [
    ("open vscode in the project folder", "the project folder"),
    ("open vs code in my projects folder", "my projects folder"),
    ("open vs code in the folder and create a.py", "the folder"),
])
def test_a_phrase_that_is_not_a_path_keeps_its_old_meaning(text, folder):
    assert composite.parse_vscode(text)["folder"] == folder
