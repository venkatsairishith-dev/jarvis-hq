"""JARVIS answers the Playwright extension's connect page itself, and only the right one (checked live 2026-10-26)."""
import os
import sys
import threading

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from jarvis_node import connectclick as cc  # noqa: E402


def test_the_script_presses_only_our_own_clients_row_for_this_launchs_relay_port():
    s = cc.SCRIPT
    assert "$c.Name -eq $says" in s and cc.CLIENT_SAYS == '"jarvis" is trying to connect to the Playwright Extension.'
    assert "chrome-extension://" in s and "connect.html" in s and "'%3A' + $_ + '%2F'" in s        # the relay port of the MCP server this launch started
    assert "$c.Name -eq $btnName" in s and cc.BUTTON == "Allow & select"
    assert "InvokePattern" in s and "SendKeys" not in s and "mouse" not in s.lower()               # a pattern, never a screen click
    assert "if ($ports.Count -eq 0) { 'none'; exit }" in s


def test_the_script_returns_nothing_of_the_page():
    out_lines = [ln for ln in cc.SCRIPT.splitlines() if ln.strip() in ("$done", "'none'; exit")]
    assert out_lines and not any("Write-Output" in ln or "Write-Host" in ln for ln in cc.SCRIPT.splitlines())


def test_press_once_reads_the_last_word_and_survives_errors():
    assert cc.press_once(run=lambda script, env: "noise\nclicked\n") == "clicked"
    assert cc.press_once(run=lambda script, env: "") == "none"
    assert cc.press_once(run=lambda script, env: "something else") == "error"

    def boom(script, env):
        raise OSError("no powershell")
    assert cc.press_once(run=boom) == "error"


def test_the_approver_polls_until_a_click_then_stops():
    seen = []

    def press():
        seen.append(1)
        return "clicked" if len(seen) == 3 else "none"
    a = cc.Approver(press=press, every=0.01, limit_s=5.0)
    with a:
        threading.Event().wait(0.3)
    assert len(seen) == 3 and a.result == "clicked"


def test_the_approver_gives_up_at_its_limit():
    a = cc.Approver(press=lambda: "none", every=0.01, limit_s=0.1)
    with a:
        threading.Event().wait(0.4)
    assert a.result == "none"
