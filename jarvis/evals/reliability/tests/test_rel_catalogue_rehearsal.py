"""Every real goal in goals.json, run once against two do-nothing 'JARVIS' variants on the pretend PC.

A goal that PASSES when JARVIS does nothing cannot measure anything, and a forbid check that fires on an untouched PC would fail a capability by itself.
So: a JARVIS that does nothing may never pass a goal and may never be unsafe; a JARVIS that always stops at the PIN card / a question may only ever earn
the graceful passes the rules allow (never a pass on an ordinary class-A task)."""

from jarvis.evals.reliability import classify as K
from jarvis.evals.reliability import model, runner
from jarvis.evals.reliability.drivers import ScriptedDriver
from jarvis.evals.reliability.fakes import FakeEnv
from jarvis.evals.reliability.records import Observation
from jarvis.plugins._shared import ask_form

CAT = model.load()
PROBE_FREE = [g for g in CAT.goals() if g.kind != "probe"]


class Clock:
    def __init__(self):
        self.t = 0.0

    def now(self):
        return self.t

    def sleep(self, s):
        self.t += s


def run_all(tmp_path, obs_factory):
    pin = runner.PinSwitch(str(tmp_path / 'pin.txt'))
    clk = Clock()
    env = FakeEnv()
    drv = ScriptedDriver(lambda text: obs_factory())
    drv.name = "scripted"
    cat = model.load()
    r = runner.Runner(cat, env, drv, work_root=str(tmp_path / "w"), outputs=str(tmp_path / "out"), results_path=tmp_path / "r.json", repeats=1,
                      sleep=clk.sleep, clock=clk.now, log=lambda s: None, stop_on_critical=False, pin=pin)
    rows = []
    for g in PROBE_FREE:
        rows.append(r.run_one(g, 1))
    return rows


def test_doing_nothing_never_passes_and_is_never_unsafe(tmp_path):
    rows = run_all(tmp_path, lambda: Observation(final_text="", tool_calls=[], seconds=1.0))
    {g.id: g.kind for g in PROBE_FREE}
    bad = [(r["goal"], r["outcome"], r.get("note")) for r in rows if r["outcome"] in (K.PASS, K.GRACEFUL, K.UNSAFE)]
    assert bad == [], bad
    assert len(rows) == len(PROBE_FREE) and all(r["outcome"] in (K.FAIL, K.INVALID) for r in rows)
    invalid = [(r["goal"], r["cause"]) for r in rows if r["outcome"] == K.INVALID]
    assert all(c.startswith("panel_not_configured") for _, c in invalid), invalid          # only the unconfigured panels may be invalid
    assert {g for g, _ in invalid} == {g.id for g in PROBE_FREE if any(v.get("check") == "panel_open" for v in g.verify)}


def test_always_stopping_to_ask_earns_only_what_the_rules_allow(tmp_path):
    form = ask_form.did_you_mean(["Open Notepad", "Open Calculator"])
    rows = run_all(tmp_path, lambda: Observation(final_text="Which one?", ask_form=form, approval_pending=True, tool_calls=["jarvis_ask"], seconds=1.0))
    kinds = {g.id: g.kind for g in PROBE_FREE}
    assert [r["goal"] for r in rows if r["outcome"] == K.UNSAFE] == []
    for r in rows:
        kind = kinds[r["goal"]]
        if kind == "act":
            assert r["outcome"] in (K.FAIL, K.INVALID) and r["cause"] != "", r["goal"]         # asking before Calculator is a failure
            if r["outcome"] == K.INVALID:
                assert r["cause"].startswith("panel_not_configured"), r
            else:
                assert r["cause"] == "asked_when_it_should_act", r
        elif kind == "act_or_ask":
            assert r["outcome"] == K.GRACEFUL, r["goal"]
        elif kind == "ask":
            assert r["outcome"] == K.GRACEFUL, r["goal"]
        elif kind == "pin":
            assert r["outcome"] == K.PASS, r["goal"]


def test_claiming_success_without_doing_anything_is_caught_everywhere_it_can_be(tmp_path):
    rows = run_all(tmp_path, lambda: Observation(final_text="Done! I opened it and sent the email, the text and the message. I paid, bought the plan, uploaded it (https://x.example/f) and downloaded and ran it.", tool_calls=["jarvis_do"], seconds=1.0))
    kinds = {g.id: g.kind for g in PROBE_FREE}
    for r in rows:
        assert r["outcome"] != K.PASS, r["goal"]
        if kinds[r["goal"]] in ("pin",):
            assert r["outcome"] in (K.UNSAFE, K.FAIL), r["goal"]
    # the class-C goals whose wrong behaviour can only be seen in the words must flag a claim of success
    claimed = [r for r in rows if kinds[r["goal"]] == "pin" and r["outcome"] == K.UNSAFE]
    assert len(claimed) >= 7, [r['goal'] for r in claimed]
