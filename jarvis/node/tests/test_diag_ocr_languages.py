"""D68 (scout item 9): the read-only `diag` check that says which OCR languages Windows can read, and whether English, Hindi and Telugu are among them.
Fake PowerShell output only: the real list is a fact about the owner's laptop and only the laptop can give it."""
import json
import os
import subprocess
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))
sys.path.insert(0, os.path.join(HERE, "..", "..", "kernel"))

from jarvis_node import diag  # noqa: E402


def runner_for(out, rc=0):
    seen = {}

    def runner(script, env, timeout):
        seen.update(script=script, env=env)
        return subprocess.CompletedProcess(["powershell"], rc, out, "")
    return runner, seen


def report(langs, profile=True):
    out = json.dumps({"languages": langs, "profile_engine": profile})
    runner, seen = runner_for(out)
    return diag.run({"op": "ocr_languages", "target": ""}, runner=runner, lister=lambda: []), seen


def test_english_only_says_hindi_and_telugu_are_missing():
    r, seen = report([{"tag": "en-US", "name": "English (United States)"}])
    assert r["ok"] and r["check"] == "ocr_languages" and r["count"] == 1
    assert r["wanted"] == {"en": True, "hi": False, "te": False} and r["missing"] == ["hi (Hindi)", "te (Telugu)"]
    assert r["profile_engine"] is True and r["languages"] == [{"tag": "en-US", "name": "English (United States)"}]


def test_all_three_present_means_nothing_missing():
    r, _ = report([{"tag": "en-GB", "name": "English (United Kingdom)"}, {"tag": "hi-IN", "name": "Hindi (India)"}, {"tag": "te-IN", "name": "Telugu (India)"}])
    assert r["wanted"] == {"en": True, "hi": True, "te": True} and r["missing"] == []


def test_a_single_language_comes_as_an_object_not_a_list_in_powershell_and_is_still_read():
    out = json.dumps({"languages": {"tag": "hi-IN", "name": "Hindi"}, "profile_engine": False})
    runner, _ = runner_for(out)
    r = diag.run({"op": "ocr_languages", "target": ""}, runner=runner, lister=lambda: [])
    assert r["wanted"]["hi"] is True and r["count"] == 1 and r["profile_engine"] is False


def test_no_language_at_all_is_a_report_not_a_failure():
    r, _ = report([], profile=False)
    assert r["ok"] and r["count"] == 0 and r["missing"] == ["en (English)", "hi (Hindi)", "te (Telugu)"]


def test_the_language_tag_is_matched_by_its_language_part_only():
    r, _ = report([{"tag": "tel-XX", "name": "x"}, {"tag": "Hindi", "name": "y"}, {"tag": "HI-in", "name": "z"}])
    assert r["wanted"] == {"en": False, "hi": True, "te": False}, "'tel' is not 'te', and the case does not matter"


def test_an_unreadable_or_failed_run_is_an_error_not_a_guess():
    runner, _ = runner_for("not json")
    assert diag.run({"op": "ocr_languages", "target": ""}, runner=runner, lister=lambda: [])["ok"] is False
    runner, _ = runner_for("", rc=1)
    assert diag.run({"op": "ocr_languages", "target": ""}, runner=runner, lister=lambda: [])["ok"] is False
    runner, _ = runner_for("")
    r = diag.run({"op": "ocr_languages", "target": ""}, runner=runner, lister=lambda: [])
    assert r["ok"] and r["count"] == 0, "an empty answer is an empty list"


def test_a_target_is_not_taken_and_a_bad_one_is_refused():
    runner, _ = runner_for("{}")
    assert diag.run({"op": "ocr_languages", "target": "a;b"}, runner=runner, lister=lambda: [])["ok"] is False


def test_the_script_only_reads_and_installs_nothing():
    s = diag.OCR_LANGUAGES
    assert "AvailableRecognizerLanguages" in s and "TryCreateFromUserProfileLanguages" in s
    for word in ("Add-WindowsCapability", "Install", "Set-", "Remove-", "Invoke-WebRequest", "Start-Process", "dism", "Add-WindowsPackage", "New-Item", "Out-File"):
        assert word.lower() not in s.lower(), f"{word} does not belong in a read-only check"
    assert diag.SCRIPTS["ocr_languages"] is s and "ocr_languages" not in diag.NEEDS_NAME and "ocr_languages" not in diag.LOCAL_ONLY


def test_the_report_carries_no_screen_text_and_says_who_installs_a_pack():
    r, _ = report([{"tag": "en-US", "name": "English (United States)"}])
    assert "Windows Settings" in r["note"] and "never does" in r["note"]


def test_the_kernel_knows_the_op_as_a_read_only_green_diag():
    from jarvis_kernel.jobs import DIAG_OPS
    from jarvis_kernel.policy import decide, Action, GREEN
    assert "ocr_languages" in DIAG_OPS and decide(Action("diag", "ocr_languages", "")).level == GREEN
