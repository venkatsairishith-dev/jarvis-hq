"""What the real Windows 11 Notepad Save As dialog needed (checked live 2026-10-26, jarvis/evals/reliability/results/STATUS.md): the file name box is
AutomationId 1001 with class Edit, ControlType Pane and no patterns (so no ValuePattern: it is written with WM_SETTEXT and read back with WM_GETTEXT); the
second element with id 1001 is the address bar and must not be taken; the dialog's Save / Cancel are Panes of class Button and the Replace prompt's Yes / No are
CCPushButton, none with an Invoke pattern (they are clicked with a POSTED BM_CLICK: a sent one waits for the modal Replace prompt it opens and never returns)."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from jarvis_node import winbackend  # noqa: E402


def test_the_file_name_box_is_the_1001_element_of_class_edit_and_is_written_by_message_when_it_has_no_value_pattern():
    s = winbackend.SAVE_AS
    assert "$_.Current.ClassName -eq 'Edit'" in s and "'1001'" in s
    assert "0x000C" in s and "0x000D" in s                  # WM_SETTEXT, then WM_GETTEXT to read back what the box holds
    assert "if ($sb.ToString() -ne $path) { throw 'no file name box' }" in s


def test_buttons_without_an_invoke_pattern_are_found_by_class_and_clicked_by_a_posted_message():
    s = winbackend.SAVE_AS
    assert "'CCPushButton'" in s and "($c.ClassName -eq 'Button')" in s
    assert "PostMessage([IntPtr]$bh, 0x00F5" in s            # BM_CLICK posted, never sent
    assert "SendMessage([IntPtr]$bh, 0x00F5" not in s


def test_the_replace_prompt_is_still_answered_no_and_never_yes():
    s = winbackend.SAVE_AS
    assert "Find-Button $q @() @('No', 'Cancel')" in s and "throw 'replace prompt'" in s
    assert "'Yes'" not in s
