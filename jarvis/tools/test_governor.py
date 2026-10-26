"""Tests for jarvis/tools/governor.py with fakes: the hard-limit enforcer (priority, EcoQoS, job, honest refusals) and the soft ladder
(unload, fewer threads, duty-cycle), protected processes, logging. Windows calls are replaced by fakes; the real ones are
checked on the laptop."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from jarvis.supervisor import budget as B  # noqa: E402
from jarvis.tools import governor as G  # noqa: E402

BUD = B.Budget()          # cpu 20 %, ram 6554 MB, vram 2048 MB, grace 20 s, threads 3


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


class FakeActions:
    def __init__(self, running=None):
        self.calls = []
        self.running = [] if running is None else running

    def unload_models(self):
        self.calls.append("unload")
        self.running = []
        return "POST /api/models/unload -> 200"

    def running_models(self):
        return self.running

    def restart_llama_swap(self, threads):
        self.calls.append(("restart", threads))
        return "ok"

    def duty_cycle(self, pids, seconds):
        self.calls.append(("duty", tuple(pids), seconds))
        return "suspended"


def m(cpu=5.0, ram=3000, vram=500, pids=(431,), top=()):
    return {"cpu": cpu, "ram": ram, "vram": vram, "llama_pids": list(pids), "top": list(top)}


def make(running=None, **kw):
    clock = Clock()
    logs = []
    act = FakeActions(running)
    gov = G.Governor(kw.pop("bud", BUD), act, lambda kind, **d: logs.append((kind, d)) or {"kind": kind, **d}, clock, tick_s=5.0)
    return gov, act, clock, logs


def run(gov, clock, metrics, seconds, step=5.0):
    out = []
    end = clock.t + seconds
    while clock.t < end:
        clock.t += step
        out += gov.step(metrics)
    return out


def test_nothing_happens_while_under_budget_or_for_the_first_20_seconds():
    gov, act, clock, logs = make()
    assert run(gov, clock, m(cpu=19), 120) == [] and act.calls == []
    assert run(gov, clock, m(cpu=25), 20) == [] and act.calls == []          # over, but not for longer than 20 s yet
    clock.t += 10
    assert gov.step(m(cpu=25)) and act.calls == ["unload"]                   # now it has been over for more than 20 s


def test_a_short_spike_resets_the_clock():
    gov, act, clock, _ = make()
    run(gov, clock, m(cpu=30), 15)
    run(gov, clock, m(cpu=5), 5)                                             # back under (below 90 %)
    run(gov, clock, m(cpu=30), 15)
    assert act.calls == []


def test_between_90_and_100_percent_holds_the_state():
    gov, act, clock, _ = make()
    run(gov, clock, m(cpu=30), 15)
    run(gov, clock, m(cpu=19), 10)                                           # 95 %: neither over nor clear
    run(gov, clock, m(cpu=30), 5)
    assert act.calls == ["unload"]                                           # the over-clock was not reset


@pytest.mark.parametrize("metric,kw", [("cpu", {"cpu": 21}), ("ram", {"ram": 6555}), ("vram", {"vram": 2049})])
def test_each_metric_triggers_the_ladder(metric, kw):
    gov, act, clock, logs = make()
    run(gov, clock, m(**kw), 30)
    assert act.calls[0] == "unload" and metric in logs[0][1]["over"][0]


def test_the_ladder_unload_then_fewer_threads_then_duty_cycle():
    gov, act, clock, logs = make()
    run(gov, clock, m(cpu=40), 30)
    assert act.calls == ["unload"]
    run(gov, clock, m(cpu=40), 30)
    assert ("restart", 2) in act.calls                                       # 3 -> 2 threads for the next load, nothing was loaded
    run(gov, clock, m(cpu=40), 60)
    assert any(c[0] == "duty" for c in act.calls if isinstance(c, tuple))
    duty = [c for c in act.calls if isinstance(c, tuple) and c[0] == "duty"][0]
    assert duty[1] == (431,) and 0.5 <= duty[2] <= 3.0
    kinds = [k for k, _ in logs]
    assert kinds.index("unload-models") < kinds.index("lower-threads") < kinds.index("duty-cycle")      # every action is logged


def test_threads_are_never_lowered_below_two_and_only_with_nothing_loaded():
    gov, act, clock, _ = make(running=[{"model": "ling-tiny"}], bud=B.Budget(threads=2))
    act.unload_models = lambda: act.calls.append("unload") or "no effect"        # the model stays loaded
    run(gov, clock, m(cpu=40), 120)
    assert not any(isinstance(c, tuple) and c[0] == "restart" for c in act.calls)


def test_back_under_budget_resets_the_ladder_and_is_logged():
    gov, act, clock, logs = make()
    run(gov, clock, m(cpu=40), 40)
    assert gov.rung >= 1
    run(gov, clock, m(cpu=2, ram=1000), 10)
    assert gov.rung == 0 and gov.over_since is None and any(k == "back-under-budget" for k, _ in logs)
    n = len(act.calls)
    run(gov, clock, m(cpu=40), 120)                                         # a new episode starts from rung 1 again (the cooldown has long passed)
    assert act.calls[n:] == ["unload"] or act.calls[n:n + 1] == ["unload"]


def test_unload_has_a_one_minute_cooldown():
    gov, act, clock, _ = make()
    run(gov, clock, m(cpu=40), 50)
    assert act.calls.count("unload") == 1


def test_protected_offenders_are_logged_not_touched():
    """The face / kernel client / supervisor are the offenders and no llama process exists: nothing is stopped, suspended or unloaded."""
    gov, act, clock, logs = make()
    top = ["face* cpu 35 ws 800"]
    run(gov, clock, m(cpu=35, pids=(), top=top), 600)
    assert act.calls == []
    notes = [d for k, d in logs if k == "over-budget-no-action"]
    assert 1 <= len(notes) <= 3 and notes[0]["top"] == top             # logged at most every 5 minutes


def test_duty_cycle_is_only_offered_llama_server_pids():
    comps = [{"name": "face", "protected": True, "pids": [300, 301], "cpu_pct": 30, "ws_mb": 1, "vram_mb": 0},
             {"name": "llama-server", "protected": False, "pids": [431], "cpu_pct": 15, "ws_mb": 1, "vram_mb": 0},
             {"name": "supervisor", "protected": True, "pids": [700], "cpu_pct": 1, "ws_mb": 1, "vram_mb": 0}]
    rep = {"components": comps, "total": {"cpu_pct": 46, "ws_mb": 3, "vram_mb": None}}
    x = G.measure(rep)
    assert x["llama_pids"] == [431] and x["vram"] is None and x["top"][0].startswith("face*")
    assert G.measure({"components": comps[:1], "total": {"cpu_pct": 1, "ws_mb": 1, "vram_mb": 0}})["llama_pids"] == []


# ---- hard limits -----------------------------------------------------------------------------------------------------------------------

class FakeWin:
    def __init__(self, refuse=()):
        self.refuse = set(refuse)
        self.throttled, self.adopted, self.jobs = [], [], []
        self.members = set()

    def throttle(self, pid):
        self.throttled.append(pid)
        return {"priority": True, "eco": True, "error": None}

    def adopt(self, job, pid):
        if pid in self.refuse:
            return False, 5
        self.adopted.append(pid)
        self.members.add(pid)
        return True, 0

    def in_job(self, job, pid):
        return pid in self.members

    def make_job(self, limits, name=None):
        self.jobs.append((limits, name))
        return object(), []


def enforcer(win, **kw):
    logs = []
    e = G.Enforcer(BUD, lambda kind, **d: logs.append((kind, d)), throttle=win.throttle, adopt=win.adopt, in_job=win.in_job, make_job=win.make_job, windows=True, **kw)
    return e, logs


def test_every_background_jarvis_process_gets_low_priority_ecoqos_and_the_job():
    win = FakeWin()
    e, logs = enforcer(win)
    res = e.apply({300: "node-agent", 431: "llama-server", 700: "supervisor"})
    assert res == {"new": 3, "throttled": 3, "in_job": 3, "job_refused": {}}
    assert win.throttled == [300, 431, 700] and win.adopted == [300, 431, 700]
    limits, name = win.jobs[0]
    assert len(win.jobs) == 1 and name.startswith("Local\\")
    assert limits.cpu_percent == 20.0 and limits.kill_on_close is False and limits.throttle         # closing the governor's handle must not kill JARVIS
    assert not limits.job_memory_bytes                                                            # no hard memory ceiling by default
    assert e.apply({300: "node-agent", 431: "llama-server"})["new"] == 0                                # each pid once
    assert sum(1 for k, _ in logs if k == "enforce") == 3


def test_a_process_in_another_job_is_reported_honestly_and_still_throttled():
    win = FakeWin(refuse={431})
    e, logs = enforcer(win)
    res = e.apply({300: "node-agent", 431: "llama-server"})
    assert res["job_refused"] == {431: 5} and res["in_job"] == 1 and res["throttled"] == 2
    rec = [d for k, d in logs if k == "enforce" and d["pid"] == 431][0]
    assert rec["job"] is False and rec["job_error"] == 5 and rec["priority"] and rec["eco"]


def test_memory_cap_can_be_left_off():
    win = FakeWin()
    e, _ = enforcer(win, mem_cap=False)
    e.apply({1: "node-agent"})
    assert win.jobs[0][0].job_memory_bytes is None


def test_dry_run_changes_nothing():
    win = FakeWin()
    logs = []
    e = G.Enforcer(BUD, lambda kind, **d: logs.append(kind), dry_run=True, throttle=win.throttle, adopt=win.adopt, in_job=win.in_job, make_job=win.make_job, windows=True)
    e.apply({1: "node-agent"})
    assert win.throttled == [] and win.jobs == [] and logs == ["would-enforce"]


def test_off_windows_enforcer_does_not_touch_anything():
    win = FakeWin()
    e = G.Enforcer(BUD, lambda *a, **k: None, throttle=win.throttle, adopt=win.adopt, in_job=win.in_job, make_job=win.make_job, windows=False)
    assert e.apply({1: "node-agent"})["throttled"] == 0 and win.throttled == [] and win.jobs == []


def test_forget_lets_a_reused_pid_be_enforced_again():
    win = FakeWin()
    e, _ = enforcer(win)
    e.apply({1: "node-agent"})
    e.forget(set())
    assert e.apply({1: "node-agent"})["new"] == 1



def test_the_face_window_is_never_throttled_or_capped():
    """2026-10-26 (owner: 'the Windows widget is very non responsive'): EcoQoS + BELOW_NORMAL + the CPU-capped job starved the face while a model ran."""
    win = FakeWin()
    e, logs = enforcer(win)
    res = e.apply({300: "face", 301: "face", 431: "llama-server"})
    assert win.throttled == [431] and win.adopted == [431]
    assert res["new"] == 3 and res["throttled"] == 1 and res["in_job"] == 1
    assert [d.get("interactive") for k, d in logs if k == "enforce" and d["pid"] in (300, 301)] == [True, True]
    assert e.apply({300: "face"})["new"] == 0


# ---- threads file, log, llama-swap -----------------------------------------------------------------------------------------------------

def test_threads_file_round_trip_and_expiry(tmp_path):
    f = str(tmp_path / "b" / "llama_threads.json")
    assert G.read_threads(3, f) == 3
    G.write_threads(2, f)
    assert G.read_threads(3, f) == 2
    assert G.read_threads(3, f, now=json.load(open(f))["set"] + 7200) == 3        # older than an hour: back to the default
    G.write_threads(1, f)
    assert G.read_threads(3, f) == 3                                              # below the minimum: ignored
    Path(f).write_text("garbage")
    assert G.read_threads(3, f) == 3


def test_local_models_script_follows_the_same_thread_rules():
    ps1 = (Path(G.ROOT) / "jarvis" / "scripts" / "local-models.ps1").read_text(encoding="utf-8-sig")
    assert "[int]$Threads" in ps1 and "llama_threads.json" in ps1 and "-le 3600" in ps1 and "-ge 2" in ps1 and "-t \\d+" in ps1
    assert str(G.MIN_THREADS) == "2"


def test_jsonl_log_writes_one_json_per_line(tmp_path):
    log = G.JsonLog(str(tmp_path / "logs" / "governor.jsonl"), echo=False)
    log("unload-models", result="ok")
    log("duty-cycle", pids=[1])
    lines = (tmp_path / "logs" / "governor.jsonl").read_text(encoding="utf-8").splitlines()
    assert [json.loads(x)["kind"] for x in lines] == ["unload-models", "duty-cycle"]


def test_real_actions_unload_tries_both_endpoints_and_dry_run_does_nothing():
    a = G.RealActions(lambda *x, **k: None, dry_run=True)
    assert a.unload_models() == "dry-run" and a.restart_llama_swap(2) == "dry-run" and a.duty_cycle([1], 1) == "dry-run"
    b = G.RealActions(lambda *x, **k: None)
    seen = []
    codes = iter([(404, ""), (200, "")])
    b._call = lambda method, path, timeout=10.0: seen.append((method, path)) or next(codes)
    assert b.unload_models() == "GET /unload -> 200" and seen == [("POST", "/api/models/unload"), ("GET", "/unload")]
    c = G.RealActions(lambda *x, **k: None)
    c._call = lambda *a, **k: (0, "refused")
    assert c.unload_models() == "no unload endpoint answered" and c.running_models() is None
    c._call = lambda *a, **k: (200, json.dumps({"running": [{"model": "x"}]}))
    assert c.running_models() == [{"model": "x"}]


def test_enforce_once_is_callable_from_a_launcher():
    from jarvis.tools import footprint as F
    procs = [F.Proc(300, 100, "jarvis.exe", r"D:\APPS\jarvis\jarvis.exe", r"D:\APPS\jarvis\jarvis.exe"), F.Proc(900, 100, "chrome.exe", r"C:\chrome.exe", "chrome.exe")]
    s = F.Sampler(procs=lambda: procs, gpu=lambda: None, own_pid=1)
    win = FakeWin()
    e, _ = enforcer(win)
    res = G.enforce_once(BUD, log=lambda *a, **k: None, sampler=s, enforcer=e)
    assert res["components"] == {300: "face"} and win.throttled == []               # the owner's Chrome is never touched, and the face window is exempt (interactive)
