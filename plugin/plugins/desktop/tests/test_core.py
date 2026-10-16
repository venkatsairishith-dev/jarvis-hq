"""Desktop plugin logic without servers: the approval round trip, waiting and cancelling jobs, local results read once
and deleted, and a diagnosis that is verified only against the file the error names."""
import json
import os
import socket
import subprocess
import sys

import pytest

from plugin.plugins.desktop import core


class FakeConsole:
    def __init__(self, approve_after=2, deny=False, finish_after=2):
        self.calls, self.t, self.pending = [], 0, set()
        self.approve_after, self.deny, self.finish_after = approve_after, deny, finish_after
        self.polls = 0

    def __call__(self, method, name, body=None, timeout=0):
        self.calls.append((method, name, body))
        if name == "pc":
            if body.get("action") == "edit_file" and not body.get("approval_id"):
                self.pending.add("a1")
                return 202, {"status": "needs_approval", "approval_id": "a1", "level": "YELLOW_APPROVAL", "reason": "repairs need the owner"}
            if body.get("approval_id"):
                return (403, {"error": "refused by policy: approval denied"}) if self.deny else (202, {"job": "j2", "status": "queued"})
            return 202, {"job": "j1", "status": "queued"}
        if name == "state":
            self.polls += 1
            if self.polls >= self.approve_after:
                self.pending.clear()
            return 200, {"approvals": [{"id": a} for a in self.pending]}
        if name.startswith("job/") and method == "GET":
            self.t += 1
            done = self.t >= self.finish_after
            return 200, {"id": name[4:], "state": "done" if done else "running", "result": {"ok": True, "backup": "b1"} if done else None}
        if name == "job/cancel":
            return 200, {"id": body["id"], "state": "cancelling"}
        return 404, {}


def pc(console, **kw):
    clock = [0.0]
    return core.Pc(call=console, sleep=lambda s: clock.__setitem__(0, clock[0] + s), clock=lambda: clock[0], **kw)


def test_ordinary_action_waits_for_the_job():
    c = FakeConsole()
    r = pc(c).run({"op": "diag", "check": "disk"}, 30)
    assert r["state"] == "done" and r["result"]["ok"]


def test_approval_is_waited_for_then_the_same_request_continues_with_its_id():
    c, states = FakeConsole(approve_after=3), []
    r = pc(c, on_state=lambda s, d: states.append(s)).run({"op": "repair", "action": "edit_file", "target": "x", "args": {}}, 30, "edit x")
    assert r["state"] == "done" and "waiting_for_permission" in states
    retried = [b for m, n, b in c.calls if n == "pc" and b.get("approval_id") == "a1"]
    assert retried and retried[0]["target"] == "x"


def test_denied_and_never_answered():
    r = pc(FakeConsole(approve_after=1, deny=True)).run({"op": "repair", "action": "edit_file", "target": "x", "args": {}}, 30)
    assert r["state"] == "denied"
    r = pc(FakeConsole(approve_after=10 ** 6), approval_wait_s=10).run({"op": "repair", "action": "edit_file", "target": "x", "args": {}}, 30)
    assert r["state"] == "waiting_for_permission" and r["approval_id"] == "a1" and "PIN" in r["how"]


def test_stop_while_an_approval_is_pending_is_cancelled():
    """Bench B-024 (2026-10-16): the owner's stop during the approval wait came back as 'waiting_for_permission'."""
    c = FakeConsole(approve_after=10 ** 6)
    p = pc(c)
    orig = c.__call__

    def call(method, name, body=None, timeout=0):
        if name == "state" and c.polls >= 1:
            p.stopped = True                               # the owner pressed stop while it waited
        return orig(method, name, body, timeout)
    p.call = call
    r = p.run({"op": "repair", "action": "edit_file", "target": "x", "args": {}}, 30)
    assert r["state"] == "cancelled" and not [b for m, n, b in c.calls if n == "pc" and b.get("approval_id")]


def test_a_dll_that_is_present_is_only_a_hypothesis(tmp_path):
    """Bench E-012 (2026-10-16): with the DLL in the program folder the cause still read 'a required library is missing'."""
    (tmp_path / "NekoRuntime140.dll").write_bytes(b"MZ")
    d = core.diagnose([{"dialog": True, "title": "x", "texts": ["NekoRuntime140.dll was not found."]}], [], [], app_dir=str(tmp_path))
    assert not d["verified"] and "hypothesis" in d["cause"]
    d = core.diagnose([{"dialog": True, "title": "x", "texts": ["NekoRuntime140.dll was not found."]}], [], [], app_dir=str(tmp_path / "none"))
    assert d["verified"] and "hypothesis" not in d["cause"]


def test_a_job_that_never_finishes_is_cancelled():
    c = FakeConsole(finish_after=10 ** 6)
    r = pc(c).run({"op": "diag", "check": "processes"}, 5)
    assert r["state"] == "failed" and ("POST", "job/cancel", {"id": "j1"}) in c.calls


def test_local_results_are_read_once_and_only_from_neko_local(tmp_path, monkeypatch):
    monkeypatch.setattr(core, "LOCAL_RESULTS", tmp_path / "neko-local")
    (tmp_path / "neko-local").mkdir()
    f = tmp_path / "neko-local" / "window_text-1.json"
    f.write_text(json.dumps([{"title": "x"}]), encoding="utf-8")
    assert core.take_local(str(f)) == [{"title": "x"}] and not f.exists()
    other = tmp_path / "elsewhere.json"
    other.write_text("[]", encoding="utf-8")
    with pytest.raises(core.DesktopError):
        core.take_local(str(other))
    assert other.exists()


def test_diagnosis_is_verified_against_the_named_file_and_proposes_a_safe_fix(tmp_path):
    cfg = tmp_path / "config.json"
    cfg.write_text('{\n  "name": "neko sample",\n  "port": 80x80\n}\n', encoding="utf-8")
    windows = [{"title": "Neko Sample App - configuration error", "dialog": True, "buttons": ["OK"],
                "texts": ["Neko Sample App could not start.", f"File: {cfg}"]}]
    d = core.diagnose(windows, [], [])
    assert d["verified"] and "line 3" in d["cause"]
    assert d["fix"]["args"] == {"find": '"port": 80x80', "replace": '"port": 8080'}
    cfg.write_text('{"port": 8080}', encoding="utf-8")                  # once fixed: no longer a verified cause
    d2 = core.diagnose(windows, [], [])
    assert not d2["verified"] and d2["fix"] is None and "Neko Sample App could not start" not in d2["cause"]


def test_unknown_errors_stay_hypotheses():
    d = core.diagnose([], [], [{"app": "x.exe", "kind": "crash", "module": "y.dll", "code": "c0000005"}])
    assert not d["verified"] and "hypothesis" in d["cause"]


def test_port_in_use_is_verified_against_the_real_listener_and_a_free_port_is_proposed(tmp_path):
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    s.listen(1)
    port = s.getsockname()[1]
    cfg = tmp_path / "config.json"
    cfg.write_text(json.dumps({"name": "x", "port": port, "listen": True}), encoding="utf-8")
    try:
        d = core.diagnose([{"dialog": True, "title": "x", "texts": [f"Port {port} is already in use by another program.", f"File: {cfg}"]}], [], [])
        assert d["verified"] and f"port {port}" in d["cause"] and d["fix"]["args"]["find"] == f'"port": {port}'
        assert d["fix"]["args"]["replace"] != d["fix"]["args"]["find"]
    finally:
        s.close()
    d = core.diagnose([{"dialog": True, "title": "x", "texts": [f"Port {port} is already in use by another program."]}], [], [])
    assert not d["verified"] and "hypothesis" in d["cause"]          # released since: not claimed


# The same disk checks on the drive this OS really has: C: on Windows, / elsewhere (the Linux test image).
DRIVE = "C:" if os.name == "nt" else "/"


def test_disk_space_and_service_down_are_checked_not_assumed():
    d = core.diagnose([{"dialog": True, "title": "x", "texts": [f"There is not enough space on the disk: it needs 999999999 MB on {DRIVE}, which has 1 MB free."]}], [], [])
    assert d["verified"] and d["fix"] is None and d["needs_mb"] == 999999999 and d["drive"] == DRIVE
    d = core.diagnose([{"dialog": True, "title": "x", "texts": [f"There is not enough space on the disk: it needs 1 MB on {DRIVE}, which has 0 MB free."]}], [], [])
    assert not d["verified"] and "hypothesis" in d["cause"]          # the drive really has space: the claim is not trusted
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    d = core.diagnose([{"dialog": True, "title": "x", "texts": [f"Could not connect to the service at 127.0.0.1:{port} (connection refused)."]}], [], [])
    assert d["verified"] and "nothing is listening" in d["cause"] and d["fix"] is None


def test_named_file_is_found_in_windows_and_posix_paths_but_not_in_urls():
    find = lambda t: (m.group(1) if (m := core.FILE_IN_TEXT.search(t)) else None)
    assert find(r"File: C:\Users\me\app\config.json.") == r"C:\Users\me\app\config.json"
    assert find("File: /home/me/app/config.json.") == "/home/me/app/config.json"
    assert find("see https://example.com/app/config.json for help") is None
    assert find("relative/config.json or ~/x.json") is None


def test_low_space_on_a_drive_that_does_not_exist_is_not_verified():
    drive = "Q:" if os.name != "nt" else "/no/such/mount"      # absent on this OS either way
    d = core.diagnose([{"dialog": True, "title": "x", "texts": [f"There is not enough space on the disk: it needs 5 MB on {drive}"]}], [], [])
    assert not d["verified"] and "hypothesis" in d["cause"] and "None" not in d["cause"] and "could not be checked" in d["cause"]


def test_a_dead_owner_is_not_alive_but_a_running_one_is():
    from plugin.plugins.desktop import coordinator as C
    p = subprocess.Popen([sys.executable, "-c", "pass"])
    p.wait()
    assert C._alive(os.getpid()) and not C._alive(p.pid) and not C._alive(0)


def test_a_crashed_tasks_lock_is_taken_over_but_a_live_ones_is_not(tmp_path):
    from plugin.plugins.desktop import coordinator as C
    lock = tmp_path / "pc_task.lock"
    p = subprocess.Popen([sys.executable, "-c", "pass"])
    p.wait()
    lock.write_text(json.dumps({"task": "crashed", "pid": p.pid, "at": __import__("time").time()}), encoding="utf-8")
    C.acquire("next", lock)                                         # owner is gone: taken over
    assert C.current_task(lock) == "next"
    with pytest.raises(C.Busy):
        C.acquire("third", lock)                                    # owner (this process) is alive: refused
    C.release("next", lock)
    assert not lock.exists()
