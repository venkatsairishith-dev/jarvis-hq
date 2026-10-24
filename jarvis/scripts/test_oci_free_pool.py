"""The free-pool check: the situation of 2026-10-24 must come out as 'over', a 2 OCPU / 12 GB total as sustainable, and stale figures must refuse."""
from __future__ import annotations

import sys
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import oci_free_pool as F  # noqa: E402

NOW = datetime(2026, 10, 24, 16, 30)


def test_the_2026_10_24_situation_is_over_and_runs_out_within_a_day():
    r = F.project(1542.4, 9254.6, 3, 18, NOW)
    assert r["over"] and r["pool_runs_out_in_h"] < 0                      # already used up
    assert r["over_ocpu_h"] > 300 and r["est_cost"] > 5


def test_two_ocpu_twelve_gb_all_month_is_sustainable_but_three_eighteen_is_not():
    assert F.steady_state_ok(2, 12) and not F.steady_state_ok(3, 18) and F.steady_state_ok(1, 8) and F.steady_state_ok(1, 4)


def test_a_fresh_month_at_two_twelve_ends_inside_the_pool():
    r = F.project(0, 0, 2, 12, datetime(2026, 11, 1, 0, 0))
    assert not r["over"] and r["month_end_ocpu_h"] <= 1500 and r["month_end_gb_h"] <= 9000


def test_old_pool_figures_refuse_a_verdict(monkeypatch, capsys):
    assert F.stale(date(2026, 12, 1)) and not F.stale(date(2026, 11, 20))
    monkeypatch.setattr(F, "date", type("D", (), {"today": staticmethod(lambda: date(2027, 3, 1)), "fromisoformat": date.fromisoformat}))
    assert F.main(["--used-ocpu-h", "1", "--used-gb-h", "1", "--ocpus", "2", "--gb", "12"]) == 3
    assert "STALE" in capsys.readouterr().out


def test_exit_codes_and_storage_limits(capsys):
    assert F.main(["--used-ocpu-h", "100", "--used-gb-h", "600", "--ocpus", "2", "--gb", "12", "--storage-gb", "200", "--backups", "2"]) == 0
    assert F.main(["--used-ocpu-h", "100", "--used-gb-h", "600", "--ocpus", "2", "--gb", "12", "--storage-gb", "201"]) == 1
    assert F.main(["--used-ocpu-h", "-1", "--used-gb-h", "1", "--ocpus", "2", "--gb", "12"]) == 2
