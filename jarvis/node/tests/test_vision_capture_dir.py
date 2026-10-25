"""A window capture must work on a fresh layout where outputs\\local-only does not exist yet (found on the real laptop, 2026-10-25: read_window_text
answered "capture failed" because PowerShell could not save the PNG into a folder nothing had made)."""
import json
import os
import subprocess
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from jarvis_node import vision  # noqa: E402


def test_capture_makes_the_local_only_folder_before_the_screenshot_is_saved(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_OUTPUTS", str(tmp_path / "outputs"))
    assert not (tmp_path / "outputs" / "local-only").exists()
    seen = {}

    def fake_powershell(script, env, timeout):
        png = env["JARVIS_PNG"]
        seen["dir_existed"] = os.path.isdir(os.path.dirname(png))
        if not seen["dir_existed"]:                               # what the real Bitmap.Save does
            return subprocess.CompletedProcess([], 1, "", "Save failed: a generic error occurred in GDI+")
        with open(png, "wb") as fh:
            fh.write(b"\x89PNG\r\n\x1a\n")
        return subprocess.CompletedProcess([], 0, json.dumps({"width": 10, "height": 10, "left": 0, "top": 0, "scale": 2}) + "\n", "")

    monkeypatch.setattr(vision.runtime, "powershell", fake_powershell)
    monkeypatch.setattr(vision, "_pick", lambda: "")
    path, size = vision.capture("notepad")
    assert seen["dir_existed"] and os.path.isfile(path) and size["width"] == 10
    assert os.path.dirname(path) == str(tmp_path / "outputs" / "local-only") or os.path.realpath(os.path.dirname(path)) == os.path.realpath(tmp_path / "outputs" / "local-only")


def test_a_bad_process_name_is_still_refused_before_anything_is_made(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_OUTPUTS", str(tmp_path / "outputs"))
    with pytest.raises(vision.VisionError):
        vision.capture("notepad; calc")
    assert not (tmp_path / "outputs").exists()
