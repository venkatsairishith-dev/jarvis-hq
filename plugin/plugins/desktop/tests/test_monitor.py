"""Rules of the proactive monitor (plugin/plugins/desktop/monitor.py) with fake collectors. Windows or any OS."""
from plugin.plugins.desktop import monitor as M


class Fake:
    def __init__(self):
        self.t, self.events, self.free, self.up, self.open = 1000.0, [], {"C:\\": 50.0, "D:\\": 20.0}, {}, []

    def mon(self, st, **kw):
        return M.Monitor(st, crashes=lambda after: [e for e in self.events if e["record"] > after], newest=lambda: 100,
                         disks=lambda: dict(self.free), port=lambda p: self.up.get(p, True), tasks=lambda: list(self.open),
                         services={"llama-swap": 8080}, clock=lambda: self.t, **kw)


def test_crashes_after_the_bookmark_only_and_once_each():
    f, st = Fake(), M.State()
    m = f.mon(st)
    f.events = [{"record": 90, "id": 1000, "app": "old.exe"}]
    assert m.check() == [] and st.bookmark == 100                    # first check: bookmark at the newest record
    f.events += [{"record": 101, "id": 1000, "app": "NekoCrashCanary.exe"}, {"record": 102, "id": 1026, "app": "NekoCrashCanary.exe"}]
    a = m.check()
    assert [x.kind for x in a] == ["crash"] and "NekoCrashCanary.exe crashed (2 times)" in a[0].text
    assert m.check() == []                                           # the same events are not announced again
    f.events.append({"record": 103, "id": 1002, "app": "hung.exe"})
    assert "stopped responding" in m.check()[0].text


def test_disk_floor_with_hysteresis():
    f, st = Fake(), M.State()
    m = f.mon(st)
    m.check()
    f.free["C:\\"] = 11.5
    a = m.check()
    assert [(x.kind, x.subject) for x in a] == [("disk", "C:\\")] and "11.5 GB" in a[0].text
    assert m.check() == []                                           # still low: quiet period
    f.free["C:\\"] = 12.5
    assert m.check() == []                                           # above the floor but inside the 1 GB band: no flap
    f.free["C:\\"] = 13.5
    m.check()
    assert "disk|C:\\" not in st.active                              # cleared


def test_service_down_needs_two_checks_and_a_previous_up():
    f, st = Fake(), M.State()
    m = f.mon(st)
    f.up[8080] = False
    assert m.check() == [] and m.check() == []                       # never seen up: not ours to report
    f.up[8080] = True
    m.check()
    f.up[8080] = False
    assert m.check() == []                                           # one failed check is a blip
    a = m.check()
    assert [x.kind for x in a] == ["service"] and "port 8080" in a[0].text
    assert m.check() == []
    f.up[8080] = True
    m.check()
    assert "service|llama-swap" not in st.active


def test_stuck_task_after_budget_plus_grace():
    f, st = Fake(), M.State()
    m = f.mon(st, budget_s=600)
    f.open = [{"id": "t1", "request": "play a song", "started": f.t - 650, "status": "running"}]
    assert m.check() == []
    f.t += 100
    a = m.check()
    assert [x.kind for x in a] == ["stuck"] and "play a song" in a[0].text


def test_state_round_trip(tmp_path):
    st = M.State(bookmark=5, seen_up={"x": True})
    st.save(tmp_path / "s.json")
    assert M.State.load(tmp_path / "s.json").bookmark == 5 and M.State.load(tmp_path / "missing.json").bookmark == 0


def test_a_crash_loop_speaks_once_per_quiet_period_and_then_says_how_often_it_repeated():
    """PRO-D2: a program crashing every minute was announced on every 60 s check. One fault speaks once (D28)."""
    f, st = Fake(), M.State()
    m = f.mon(st)
    m.check()                                                        # bookmark at 100
    per_check = []
    for i in range(5):
        f.t += 60
        f.events.append({"record": 101 + i, "id": 1000, "app": "Looper.exe"})
        per_check.append(len([a for a in m.check() if a.kind == "crash"]))
    assert per_check == [1, 0, 0, 0, 0]
    f.t += M.QUIET_S
    f.events.append({"record": 200, "id": 1000, "app": "Looper.exe"})
    a = m.check()
    assert [x.kind for x in a] == ["crash"] and "4 more times since the last alert" in a[0].text
    f.events.append({"record": 201, "id": 1000, "app": "Other.exe"})
    assert [x.subject for x in m.check()] == ["Other.exe"]           # a different program is a different fault
    f.events.append({"record": 202, "id": 1002, "app": "Looper.exe"})
    hang = m.check()                                                 # so is the same program hanging instead of crashing
    assert len(hang) == 1 and "Looper.exe stopped responding" in hang[0].text
    f.events.append({"record": 203, "id": 1002, "app": "Looper.exe"})
    assert m.check() == []                                           # and a hang loop is quiet too


def test_one_failing_collector_does_not_hide_the_other_checks():
    """PRO-D7: an Event Log error raised out of check(), so a low-disk alert in the same tick was never made."""
    f, st = Fake(), M.State(bookmark=1)
    calls = {"n": 0}

    def boom(after):
        calls["n"] += 1
        raise OSError("EvtQuery failed")
    m = M.Monitor(st, crashes=boom, newest=lambda: 1, disks=lambda: {"C:\\": 1.0}, port=lambda p: True, services={},
                  clock=lambda: f.t)
    a = m.check()
    kinds = {(x.kind, x.subject) for x in a}
    assert ("disk", "C:\\") in kinds and ("monitor", "crashes") in kinds
    err = next(x for x in a if x.kind == "monitor")
    assert "could not check for program crashes (OSError)" in err.text and "EvtQuery" not in err.text
    f.t += 60
    assert m.check() == []                                           # the same collector error is rate-limited
    f.t += M.QUIET_S
    again = [x for x in m.check() if x.kind == "monitor"]           # (the disk, still low, is reminded too: its own rule)
    assert len(again) == 1 and "1 more times" in again[0].text and calls["n"] == 3


def test_finished_stuck_tasks_and_old_crashes_are_forgotten():
    """PRO-D11: keys for every stuck task id stayed in the state file forever."""
    f, st = Fake(), M.State()
    m = f.mon(st, budget_s=10)
    m.check()
    for i in range(50):
        f.open = [{"id": f"t{i}", "request": "x", "started": f.t - 500, "status": "running"}]
        m.check()
        f.t += 60
    assert [k for k in st.pending if k.startswith("stuck|")] == ["stuck|t49"]   # only the task that is still open
    assert len([k for k in st.last_alert if k.startswith("stuck|")]) == 1
    f.open = []
    f.events.append({"record": 101, "id": 1000, "app": "Once.exe"})
    m.check()
    f.t += M.QUIET_S + 1
    m.check()
    keys = set(st.pending) | set(st.active) | set(st.last_alert) | set(st.suppressed)
    assert not [k for k in keys if k.startswith(("stuck|", "crash|"))], keys


def test_a_task_list_that_cannot_be_read_forgets_nothing():
    f, st = Fake(), M.State()
    m = f.mon(st, budget_s=10)
    m.check()
    f.open = [{"id": "t1", "request": "x", "started": f.t - 500, "status": "running"}]
    assert [x.kind for x in m.check()] == ["stuck"]

    def broken():
        raise OSError("disk gone")
    m.tasks = broken
    a = m.check()
    assert [x.subject for x in a] == ["tasks"] and "stuck|t1" in st.pending
