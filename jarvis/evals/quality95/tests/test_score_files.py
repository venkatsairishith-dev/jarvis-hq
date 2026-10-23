"""score.py picks result files by the run time in their names, so a fresh checkout (every mtime equal) scores the same
saved results as the laptop. Run: python -m pytest jarvis/evals/quality95/tests -q"""
import importlib.util
import json
import os
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("quality95_score", HERE / "score.py")
score = importlib.util.module_from_spec(spec)
spec.loader.exec_module(score)


def _write(path: Path, data, mtime: float):
    path.write_text(json.dumps(data), encoding="utf-8")
    os.utime(path, (mtime, mtime))


def test_live_runs_after_the_suite_count_even_when_every_mtime_is_equal(tmp_path, monkeypatch):
    same = 1_792_153_587
    _write(tmp_path / "suite-20261016-144424.json", [], same)
    _write(tmp_path / "desktop-20261016-125413.json", {"cases": [{"id": "W-old", "ok": False}]}, same)
    _write(tmp_path / "desktop-20261016-155621.json", {"cases": [{"id": "W-uwp-app", "ok": True}]}, same)
    _write(tmp_path / "diagnosis-20261016-130737.json", {"cases": [{"id": "T-port-conflict", "ok": True}]}, same)  # before the suite
    monkeypatch.setattr(score, "PC", tmp_path)
    rows = score.rows_from_live_runs(str(tmp_path / "suite-20261016-144424.json"))
    assert [(r["id"], r["status"]) for r in rows] == [("W-uwp-app", "pass")]     # newest desktop run; older diagnosis run not used
    assert score.newest(tmp_path / "desktop-*.json").endswith("desktop-20261016-155621.json")


def test_a_name_without_a_stamp_falls_back_to_mtime(tmp_path):
    a, b = tmp_path / "suite-a.json", tmp_path / "suite-b.json"
    _write(a, [], 2_000_000_000)
    _write(b, [], 1_000_000_000)
    assert score.newest(tmp_path / "suite-*.json") == str(a)
