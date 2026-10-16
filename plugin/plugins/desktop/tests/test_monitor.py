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
