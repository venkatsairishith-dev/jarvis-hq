"""Every desktop test gets its own PC-lock file. The single-action entries (run_pc_action, run_browser_action, open_website,
play_youtube, run_troubleshooter) now take the PC lock, whose default path is the plugin's data folder (D:\\jarvis\\... on the
laptop, a relative junk folder on this server): a test must never touch that, and two tests must never share a lock."""
import threading

import pytest

from jarvis.plugins.desktop import coordinator as C


@pytest.fixture(autouse=True)
def _private_pc_lock(tmp_path, monkeypatch):
    monkeypatch.setattr(C, "LOCK", tmp_path / "pc_task.lock")
    # the registry snapshot (the widget reads it): beside the test's folder, not in it (tests list that folder's content)
    monkeypatch.setattr(C, "OPS_FILE", tmp_path.parent / f"{tmp_path.name}-registry" / "pc_operations.json")
    yield


@pytest.fixture(autouse=True)
def _no_pc_state_survives_a_test():
    """Operations, lock holds and heartbeat threads are process-global (coordinator._OPS / _HOLDS). A test that fails half-way
    (an assertion in the middle of a race) must not leave a live `pc-lock-heartbeat` thread, a registered operation or a held
    lock behind: the next test's Stop would stop it, its heartbeat would keep a deleted lock path busy, and one failure would
    become dozens. Teardown ends every operation, releases every hold, and joins the heartbeat threads."""
    with C._APPROVALS_LOCK:
        C._APPROVALS.clear()                               # tracked pending approvals are process-global too
    yield
    for op in C.operations():
        try:
            op.abandon("test teardown")
        except Exception:                                  # noqa: BLE001 - cleaning up must never mask the test's own result
            pass
    with C._HOLDS_LOCK:
        left = list(C._HOLDS.values())
    for hold in left:
        try:
            C.release(hold)
        except Exception:                                  # noqa: BLE001
            pass
    with C._APPROVALS_LOCK:
        C._APPROVALS.clear()
    C._PUB_WAKE.set()
    for t in [t for t in threading.enumerate() if t.name.startswith(("pc-lock-heartbeat", "pc-ops-publisher"))]:
        t.join(5)


@pytest.fixture(autouse=True)
def _quick_path_off_and_progress_in_tmp(tmp_path, monkeypatch):
    """D60: the rules-first router answers 'open notepad' without the online plan. The older goal tests are about the online plan, so they run with
    the router off (test_quick.py turns it on); and the goal-progress file for the face goes to the test's own folder, never to the data dir."""
    from jarvis.plugins.desktop import core
    monkeypatch.setenv("JARVIS_QUICK_PATH", "0")
    monkeypatch.setattr(core, "GOAL_PROGRESS", tmp_path.parent / f"{tmp_path.name}-progress" / "goal_progress.json")
    yield


@pytest.fixture(autouse=True)
def _task_state_and_lessons_in_tmp(tmp_path, monkeypatch):
    """D65: the control loop writes a live state file, reads a controls file and keeps lessons. Their default place is the data dir (D:\\jarvis\\... on the laptop, a junk
    folder on this server): a test never touches it, and a state one test left must never be read by the next."""
    from jarvis.plugins.desktop import lessons, taskstate
    monkeypatch.setattr(taskstate, "STATE_FILE", tmp_path.parent / f"{tmp_path.name}-state" / "pc_task_state.json")
    monkeypatch.setattr(taskstate, "CONTROL_FILE", tmp_path.parent / f"{tmp_path.name}-state" / "pc_task_control.json")
    monkeypatch.setattr(lessons, "FILE", tmp_path.parent / f"{tmp_path.name}-state" / "lessons.json")
    monkeypatch.setattr(lessons, "_DEFAULT", None)
    yield
