"""Long PC work stays visible to the widget and the wake listener (completion I, 2026-10-19). A fake clock drives the real
Operation registry (coordinator.snapshot) and the real widget reader (widget_server.pc_busy): busy at 5 s, 130 s and 1000 s of
silence, and 'stale' only when the plugin stops refreshing the snapshot (20 s), i.e. when the plugin process is gone."""
import importlib.util
import json
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from jarvis.plugins.desktop import coordinator as C

ROOT = Path(__file__).resolve().parents[4]
spec = importlib.util.spec_from_file_location("widget_server_vis", ROOT / "jarvis" / "widget" / "widget_server.py")
ws = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ws)


class Clock:
    def __init__(self):
        self.t = 1_000_000.0

    def __call__(self):
        return self.t


@pytest.fixture
def clock(monkeypatch):
    c = Clock()
    monkeypatch.setattr(ws, "time", SimpleNamespace(time=c, strftime=time.strftime, monotonic=time.monotonic))   # the widget module's own clock
    return c


def publish(tmp_path, wall):
    p = tmp_path / "pc_operations.json"
    p.write_text(json.dumps(C.snapshot(wall)), encoding="utf-8")
    return str(p)


@pytest.mark.parametrize("silent_s", [5, 130, 1000])
def test_an_operation_silent_for_this_long_is_still_busy_while_the_plugin_refreshes_the_snapshot(tmp_path, clock, silent_s):
    mono = Clock()
    op = C.Operation("task", "task-fake", clock=mono, wall=clock)
    mono.t += silent_s
    clock.t += silent_s
    ops = publish(tmp_path, clock)                       # the publisher rewrites the snapshot every 5 s whatever the step does
    got = ws.pc_busy(str(tmp_path / "none.json"), ops)
    assert got["busy"] is True and got["source"] == "registry", got
    assert got["operations"][0]["idle_s"] == silent_s
    op.abandon("test over")


def test_when_the_plugin_dies_the_snapshot_goes_stale_after_20_seconds(tmp_path, clock):
    op = C.Operation("task", "task-fake")
    ops = publish(tmp_path, clock)
    assert ws.pc_busy(str(tmp_path / "none.json"), ops)["busy"] is True
    clock.t += 19.9
    assert ws.pc_busy(str(tmp_path / "none.json"), ops)["busy"] is True
    clock.t += 0.2                                       # 20.1 s without a refresh: the plugin process is gone
    assert ws.pc_busy(str(tmp_path / "none.json"), ops)["busy"] is False
    op.abandon("test over")


def test_a_finished_operation_is_not_busy_and_a_damaged_snapshot_is_not_busy(tmp_path, clock):
    op = C.Operation("task", "task-fake")
    op.abandon("done")
    assert ws.pc_busy(str(tmp_path / "none.json"), publish(tmp_path, clock))["busy"] is False
    bad = tmp_path / "bad.json"
    bad.write_text('{"at": 1, "busy": tr', encoding="utf-8")              # a truncated write
    assert ws.pc_busy(str(tmp_path / "none.json"), str(bad))["busy"] is False


def test_a_stop_request_is_visible_in_the_status_as_stopping_not_as_stopped(tmp_path, clock):
    op = C.Operation("task", "task-fake", wall=clock)
    op.request_stop("the owner said stop")
    clock.t += 3
    got = ws.pc_busy(str(tmp_path / "none.json"), publish(tmp_path, clock))
    assert got["busy"] is True and got["stopping"] == 1 and got["state"] in ("stopping", "stuck"), got
    op.abandon("test over")
