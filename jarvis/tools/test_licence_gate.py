"""Tests for jarvis/tools/licence_gate.py. Run: python -m pytest jarvis/tools/test_licence_gate.py -q -p no:cacheprovider"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location("licence_gate", HERE / "licence_gate.py")
gate = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(gate)

GOOD = {"name": "good", "shipped": True, "licence_spdx": "Apache-2.0", "licence_file": "licenses/good.txt", "notice_file": "NOTICE"}


def test_real_registry_passes_in_both_profiles():
    entries = json.loads((HERE / "registry.json").read_text(encoding="utf-8"))
    assert gate.check_registry(entries, "personal") == []
    assert gate.check_registry(entries, "shared") == []


def test_injected_gpl_entry_fails_in_shared_and_passes_in_personal():
    bad = dict(GOOD, name="gpl-thing", licence_spdx="GPL-3.0-only")
    assert any("GPL-3.0-only" in p for p in gate.check_registry([GOOD, bad], "shared"))
    assert gate.check_registry([GOOD, bad], "personal") == []


def test_shipped_entry_needs_spdx_licence_file_and_notice():
    assert gate.check_entry(GOOD, "shared") == []
    assert gate.check_entry({k: v for k, v in GOOD.items() if k != "licence_spdx"}, "personal")
    assert gate.check_entry(dict(GOOD, licence_spdx="unknown"), "personal")
    assert gate.check_entry(dict(GOOD, notice_file=""), "personal")
    assert gate.check_entry(dict(GOOD, licence_file="  "), "personal")


def test_unshipped_entry_is_not_gated_but_is_reported():
    e = {"name": "model", "licence": "unknown", "licence_spdx": "GPL-3.0-only"}
    assert gate.check_entry(e, "shared") == []
    assert any("model" in line for line in gate.report([e]))


def test_expression_with_forbidden_part_fails_in_shared():
    e = dict(GOOD, licence_spdx="(MIT OR AGPL-3.0-or-later)")
    assert gate.check_entry(e, "shared")


def test_main_exit_codes(tmp_path):
    reg = tmp_path / "r.json"
    reg.write_text(json.dumps([GOOD, dict(GOOD, name="x", licence_spdx="AGPL-3.0-only")]), encoding="utf-8")
    assert gate.main(["--registry", str(reg), "--profile", "shared"]) == 1
    assert gate.main(["--registry", str(reg), "--profile", "personal"]) == 0


def test_populated_spdx_ids_are_well_formed_and_agree_with_the_free_text_licence():
    entries = json.loads((HERE / "registry.json").read_text(encoding="utf-8"))
    populated = [e for e in entries if e.get("licence_spdx")]
    assert len(populated) >= 30
    for e in populated:
        ids = gate._spdx_ids(e["licence_spdx"])
        assert ids and all(gate.SPDX_TOKEN.match(i) for i in ids), e["name"]
        assert e["licence_spdx"].lower() == str(e["licence"]).strip().lower(), e["name"]    # only copied from an already-clean value
