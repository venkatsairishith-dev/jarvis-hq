"""Tests for jarvis/tools/make_notices.py. Run: python -m pytest jarvis/tools/test_make_notices.py -q -p no:cacheprovider"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location("make_notices", HERE / "make_notices.py")
mk = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mk)

SHIPPED = {"name": "alpha", "upstream_url": "https://example.com/alpha", "pinned_version": "1.2.3", "shipped": True,
           "licence_spdx": "MIT", "licence_file": "alpha.txt", "notice_file": "alpha.notice"}
HIDDEN = {"name": "beta", "upstream_url": "https://example.com/beta", "pinned_version": "9", "licence_spdx": "MIT",
          "licence_file": "beta.txt"}
BROKEN = {"name": "gamma", "upstream_url": "https://example.com/gamma", "pinned_version": "2", "shipped": True,
          "licence_spdx": "BSD-3-Clause", "licence_file": "gamma.txt", "notice_file": "alpha.notice"}


def _setup(tmp_path, entries):
    lic = tmp_path / "lic"
    lic.mkdir()
    (lic / "alpha.txt").write_text("ALPHA LICENCE TEXT\n", encoding="utf-8")
    (lic / "alpha.notice").write_text("ALPHA NOTICE TEXT\n", encoding="utf-8")
    (lic / "beta.txt").write_text("BETA LICENCE TEXT\n", encoding="utf-8")
    reg = tmp_path / "reg.json"
    reg.write_text(json.dumps(entries), encoding="utf-8")
    out = tmp_path / "NOTICES.md"
    return ["--registry", str(reg), "--licenses-dir", str(lic), "--out", str(out)], out


def test_only_shipped_entries_are_included(tmp_path):
    args, out = _setup(tmp_path, [SHIPPED, HIDDEN])
    assert mk.main(args) == 0
    text = out.read_text(encoding="utf-8")
    assert "## alpha" in text and "1.2.3" in text and "MIT" in text and "https://example.com/alpha" in text
    assert "ALPHA LICENCE TEXT" in text and "ALPHA NOTICE TEXT" in text
    assert "beta" not in text.lower()


def test_missing_file_is_listed_and_only_fails_with_strict(tmp_path):
    args, out = _setup(tmp_path, [SHIPPED, BROKEN])
    assert mk.main(args) == 0
    assert "MISSING:" in out.read_text(encoding="utf-8") and "gamma.txt" in out.read_text(encoding="utf-8")
    assert mk.main(args + ["--strict"]) == 1
    assert out.is_file()


def test_strict_passes_when_nothing_missing(tmp_path):
    args, _ = _setup(tmp_path, [SHIPPED])
    assert mk.main(args + ["--strict"]) == 0


def test_check_passes_when_current_and_fails_when_stale(tmp_path):
    args, out = _setup(tmp_path, [SHIPPED])
    assert mk.main(args + ["--check"]) == 1  # no file yet
    assert mk.main(args) == 0
    assert mk.main(args + ["--check"]) == 0
    out.write_text(out.read_text(encoding="utf-8") + "\nedited\n", encoding="utf-8")
    assert mk.main(args + ["--check"]) == 1
    # --check never rewrites the file
    assert out.read_text(encoding="utf-8").endswith("edited\n")


def test_check_fails_when_registry_changes(tmp_path):
    args, _ = _setup(tmp_path, [SHIPPED])
    assert mk.main(args) == 0
    Path(args[1]).write_text(json.dumps([SHIPPED, dict(SHIPPED, name="delta")]), encoding="utf-8")
    assert mk.main(args + ["--check"]) == 1
