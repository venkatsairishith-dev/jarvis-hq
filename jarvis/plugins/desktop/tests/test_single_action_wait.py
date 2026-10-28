"""Two single actions in one breath (laptop 2026-10-28: the chat model fired three ui_click_element calls in the same second and two were refused with "another PC task is
running (single-run_pc_action-..., running 0 s)"). A single action waits for another single action; a task or a mission still refuses at once."""
import asyncio
import threading
import time

import pytest

from jarvis.plugins import desktop as DP
from jarvis.plugins.desktop import coordinator as C


@pytest.fixture(autouse=True)
def private_lock(tmp_path, monkeypatch):
    monkeypatch.setattr(C, "LOCK", tmp_path / "pc.lock")
    monkeypatch.setattr(C, "OPS_FILE", tmp_path / "ops.json", raising=False)
    monkeypatch.setattr(C, "HEARTBEAT_S", 0.05, raising=False)


def test_a_second_single_action_waits_for_the_first_instead_of_failing():
    first = DP._PcLock("run_pc_action", 30)
    first.__enter__()
    threading.Timer(0.8, lambda: first.__exit__(None, None, None)).start()

    async def second():
        t0 = time.monotonic()
        async with DP._PcLock("run_pc_action", 30):
            return time.monotonic() - t0
    waited = asyncio.run(second())
    assert 0.5 < waited < 10


def test_a_single_action_does_not_wait_for_a_task_or_a_mission():
    hold = C.acquire("task-abc123", C.LOCK, 30, kind="task")
    try:
        async def second():
            t0 = time.monotonic()
            with pytest.raises(C.Busy):
                async with DP._PcLock("run_pc_action", 30):
                    pass
            return time.monotonic() - t0
        assert asyncio.run(second()) < 2
    finally:
        C.release(hold)


def test_the_wait_has_a_limit(monkeypatch):
    monkeypatch.setattr(DP._PcLock, "SINGLE_WAIT_S", 0.6)
    first = DP._PcLock("run_pc_action", 30)
    first.__enter__()
    try:
        async def second():
            t0 = time.monotonic()
            with pytest.raises(C.Busy):
                async with DP._PcLock("run_pc_action", 30):
                    pass
            return time.monotonic() - t0
        assert 0.5 < asyncio.run(second()) < 5
    finally:
        first.__exit__(None, None, None)
