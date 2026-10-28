"""D81: the external watchdog around one goal's driver call - fakes only (no real hang, no real PC). Covers: the normal (no-hang) path is untouched;
a driver that ignores its own timeout is still caught by the external deadline; window diagnosis and classification (hung_window, dialog_stuck,
no_window_model_silent, process_zombie, unknown); the never-close-what-you-didn't-open and never-close-a-non-matching-window safety rules; recovery
(close / cancel / terminate); the stuck-report file's shape and that it holds zero chat/owner text; and that `run_one`'s outcome after a recovered
hang is the same invalid/harness-timeout shape the rest of the pipeline already expects."""
from __future__ import annotations

import json
import time

import pytest

from jarvis.evals.reliability import model, runner, watchdog as W
from jarvis.evals.reliability import classify as K
from jarvis.evals.reliability.drivers import ScriptedDriver
from jarvis.evals.reliability.fakes import FakeEnv
from jarvis.evals.reliability.records import Observation


# ------------------------------------------------------------------------------------------------------------------------------- fake drivers
class QuickDriver:
    """Returns right away: the ordinary, no-hang case."""
    name = "quick"

    def __init__(self, obs: Observation | None = None):
        self.obs = obs or Observation(final_text="ok", seconds=1.0)
        self.cancelled = False

    def run(self, text, timeout_s, attended):
        return self.obs

    def cancel_all(self):
        self.cancelled = True


class HangingDriver:
    """Never returns (a real thread, blocked forever): proves the EXTERNAL deadline fires even with no cooperative cancellation at all."""
    name = "hanging"

    def __init__(self, current_proc=None):
        self.cancelled = False
        self.current_proc = current_proc

    def run(self, text, timeout_s, attended):
        time.sleep(10_000)
        return Observation(final_text="too late")  # pragma: no cover - never reached in a test

    def cancel_all(self):
        self.cancelled = True


class RaisingDriver:
    name = "raising"

    def run(self, text, timeout_s, attended):
        raise RuntimeError("the node script crashed")

    def cancel_all(self):
        pass


class FakeProc:
    """A stand-in for a tracked child process (`driver.current_proc`): alive until `kill()`."""

    def __init__(self, alive_for=1):
        self.alive_for = alive_for
        self.polls = 0
        self.killed = False

    def poll(self):
        self.polls += 1
        if self.killed:
            return 1
        return None if self.polls <= self.alive_for else 0

    def kill(self):
        self.killed = True


def closable():
    return [{"process": "notepad"}]


# ------------------------------------------------------------------------------------------------------------------------------- the normal path
def test_a_driver_that_returns_in_time_is_untouched():
    env = FakeEnv()
    obs = Observation(final_text="done", seconds=2.0, requests=3)
    out = W.run_with_recovery(QuickDriver(obs), env, "say hi", 5.0, False, pre_hwnds=set(), closable=closable(), grace_s=0.2)
    assert out is obs


def test_the_normal_path_never_touches_the_pc(tmp_path):
    env = FakeEnv()
    win = env.add_window("notepad", "Untitled - Notepad")
    out = W.run_with_recovery(QuickDriver(), env, "x", 1.0, False, pre_hwnds=set(), closable=closable(), grace_s=0.2, results_dir=tmp_path)
    assert out.status == "done" and win in env.windows() and env.closed == []
    assert list((tmp_path / W.STUCK_SUBDIR).glob("*")) == [] if (tmp_path / W.STUCK_SUBDIR).exists() else True


# ------------------------------------------------------------------------------------------------------------------------------- the external deadline itself
def test_a_hang_with_no_cooperative_cancellation_is_still_caught():
    env = FakeEnv()
    t0 = time.monotonic()
    out = W.run_with_recovery(HangingDriver(), env, "x", 0.05, False, pre_hwnds=set(), closable=closable(), grace_s=0.05)
    elapsed = time.monotonic() - t0
    assert out.status == "harness_error" and "stuck" in out.harness_error and elapsed < 5.0        # bounded, not 10000 s


def test_a_hang_with_no_evidence_either_way_defaults_to_no_window_model_silent():
    """No new window and nothing configured to check: the harness has no better evidence, so it reports the most common real cause rather than
    refusing to guess - still bounded, still logged, still lets the suite continue."""
    env = FakeEnv()
    out = W.run_with_recovery(HangingDriver(), env, "x", 0.02, False, pre_hwnds=set(), closable=closable(), grace_s=0.02)
    assert out.raw["stuck_cause"] == W.NO_WINDOW_MODEL_SILENT


def test_a_hang_is_classified_unknown_when_a_new_window_matches_no_known_pattern():
    env = FakeEnv()
    win = env.add_window("mspaint", "Untitled - Paint")

    def lister(env):
        return [{"hwnd": win.hwnd, "process": "mspaint", "title": win.title, "class": "Paint", "hung": False}]
    out = W.run_with_recovery(HangingDriver(), env, "x", 0.02, False, pre_hwnds=set(), closable=closable(), grace_s=0.02, window_lister=lister)
    assert out.raw["stuck_cause"] == W.UNKNOWN and "unknown" in out.harness_error
    assert env.closed == []            # unknown recovers nothing - never a guess


def test_a_driver_that_raises_instead_of_hanging_is_a_plain_harness_error_not_a_stuck_report(tmp_path):
    env = FakeEnv()
    out = W.run_with_recovery(RaisingDriver(), env, "x", 1.0, False, pre_hwnds=set(), closable=closable(), grace_s=0.2, results_dir=tmp_path)
    assert out.status == "harness_error" and "RuntimeError" in out.harness_error and "crashed" in out.harness_error
    assert not (tmp_path / W.STUCK_SUBDIR).exists()


def test_a_driver_that_returns_nothing_is_still_a_bounded_harness_error():
    class NoneDriver:
        def run(self, *a):
            return None

        def cancel_all(self):
            pass
    out = W.run_with_recovery(NoneDriver(), FakeEnv(), "x", 0.3, False, pre_hwnds=set(), closable=closable())
    assert out.status == "harness_error" and "nothing" in out.harness_error


# ------------------------------------------------------------------------------------------------------------------------------- window listing / normalization
def test_normalize_window_strips_the_title_but_keeps_the_safe_flags():
    row = {"hwnd": 7, "pid": 1, "process": "notepad.exe", "title": "my secret diary.txt - Notepad", "class": "Notepad",
           "hung": True, "cloaked": False, "minimized": False, "foreground": True}
    n = W._normalize_window(row)
    assert n == {"hwnd": 7, "process": "notepad.exe", "class": "Notepad", "has_title": True, "hung": True, "cloaked": False,
                "minimized": False, "foreground": True}
    assert "title" not in n and "diary" not in json.dumps(n)


def test_list_windows_falls_back_to_env_windows_when_no_lister_is_given():
    env = FakeEnv()
    env.add_window("chrome", "Some page - Chrome")
    rows = W.list_windows(env)
    assert rows and rows[0]["process"] == "chrome" and rows[0]["hung"] is False and "title" not in rows[0]


def test_list_windows_uses_an_injected_lister():
    rows = W.list_windows(FakeEnv(), window_lister=lambda env: [{"hwnd": 1, "process": "x", "title": "t", "hung": True}])
    assert rows == [{"hwnd": 1, "process": "x", "class": "", "has_title": True, "hung": True, "cloaked": False, "minimized": False, "foreground": False}]


# ------------------------------------------------------------------------------------------------------------------------------- classification
def test_classify_hung_window():
    c = W.classify_stuck([{"hwnd": 1, "hung": True, "class": ""}], log_quiet=False, process_alive=False)
    assert c.kind == W.HUNG_WINDOW and c.window["hwnd"] == 1


def test_classify_dialog_stuck():
    c = W.classify_stuck([{"hwnd": 2, "hung": False, "class": "#32770"}], log_quiet=False, process_alive=False)
    assert c.kind == W.DIALOG_STUCK and c.window["hwnd"] == 2


def test_hung_window_beats_dialog_stuck_when_both_are_present():
    c = W.classify_stuck([{"hwnd": 9, "hung": True, "class": "#32770"}], log_quiet=True, process_alive=True)
    assert c.kind == W.HUNG_WINDOW


def test_classify_no_window_model_silent():
    c = W.classify_stuck([], log_quiet=True, process_alive=False)
    assert c.kind == W.NO_WINDOW_MODEL_SILENT


def test_classify_process_zombie_takes_priority_over_log_silence():
    c = W.classify_stuck([], log_quiet=True, process_alive=True)
    assert c.kind == W.PROCESS_ZOMBIE


def test_classify_unknown_when_nothing_matches():
    c = W.classify_stuck([{"hwnd": 3, "hung": False, "class": "Notepad"}], log_quiet=False, process_alive=False)
    assert c.kind == W.UNKNOWN and "unknown" in c.detail


def test_classify_unknown_with_no_windows_and_no_log_evidence_either_way():
    c = W.classify_stuck([], log_quiet=False, process_alive=False)
    assert c.kind == W.UNKNOWN


# ------------------------------------------------------------------------------------------------------------------------------- recovery: closing
def test_a_hung_window_that_the_run_opened_and_that_matches_closable_is_closed():
    env = FakeEnv()
    win = env.add_window("notepad", "Untitled - Notepad")
    cause = W.StuckCause(W.HUNG_WINDOW, "hung", window={"hwnd": win.hwnd, "process": "notepad"})
    out = W.recover(env, QuickDriver(), cause, closable=closable(), pre_hwnds=set())
    assert out["closed_hwnd"] == win.hwnd and env.closed == [win]


def test_only_the_offending_window_is_closed_never_any_other_new_window():
    env = FakeEnv()
    hung_win = env.add_window("notepad", "Untitled - Notepad")
    other = env.add_window("chrome", "Some page - Chrome")        # another NEW window, not the one the cause names
    cause = W.StuckCause(W.HUNG_WINDOW, "hung", window={"hwnd": hung_win.hwnd, "process": "notepad"})
    W.recover(env, QuickDriver(), cause, closable=closable(), pre_hwnds=set())
    assert env.closed == [hung_win] and other in env.windows()


def test_a_window_the_run_did_not_open_is_never_closed_even_if_hung():
    env = FakeEnv()
    owners_win = env.add_window("notepad", "owners-own.txt - Notepad")           # open BEFORE the run
    cause = W.StuckCause(W.HUNG_WINDOW, "hung", window={"hwnd": owners_win.hwnd, "process": "notepad"})
    W.recover(env, QuickDriver(), cause, closable=closable(), pre_hwnds={owners_win.hwnd})
    assert env.closed == [] and owners_win in env.windows()


def test_a_new_window_of_the_wrong_app_is_never_closed_even_when_flagged():
    """A 'wrong app' window - new, but not matching the SAME closable rule runner.py uses - must not be closed."""
    env = FakeEnv()
    wrong = env.add_window("chrome", "Unrelated - Chrome")
    cause = W.StuckCause(W.DIALOG_STUCK, "dialog", window={"hwnd": wrong.hwnd, "process": "chrome"})
    W.recover(env, QuickDriver(), cause, closable=closable(), pre_hwnds=set())            # closable only matches "notepad"
    assert env.closed == [] and wrong in env.windows()


def test_a_window_matching_the_ignore_list_is_never_closed():
    env = FakeEnv()
    own = env.add_window("jarvis", "JARVIS")
    cause = W.StuckCause(W.HUNG_WINDOW, "hung", window={"hwnd": own.hwnd, "process": "jarvis"})
    W.recover(env, QuickDriver(), cause, closable=[{"process": "jarvis"}], ignore=("jarvis",), pre_hwnds=set())
    assert env.closed == []


def test_an_unknown_cause_closes_nothing_and_cancels_nothing():
    env = FakeEnv()
    win = env.add_window("notepad", "Untitled - Notepad")
    d = QuickDriver()
    cause = W.StuckCause(W.UNKNOWN, "stuck, cause unknown")
    out = W.recover(env, d, cause, closable=closable(), pre_hwnds=set())
    assert out == {"closed_hwnd": None, "cancelled": False, "terminated_process": False} and env.closed == [] and not d.cancelled


# ------------------------------------------------------------------------------------------------------------------------------- recovery: cancel / terminate
def test_no_window_model_silent_cancels_the_driver():
    d = QuickDriver()
    out = W.recover(FakeEnv(), d, W.StuckCause(W.NO_WINDOW_MODEL_SILENT, "quiet"), closable=closable(), pre_hwnds=set())
    assert d.cancelled and out["cancelled"] is True and out["terminated_process"] is False


def test_process_zombie_terminates_only_the_tracked_process_after_a_grace_period():
    d = QuickDriver()
    d.current_proc = FakeProc(alive_for=100)
    slept = []
    out = W.recover(FakeEnv(), d, W.StuckCause(W.PROCESS_ZOMBIE, "zombie"), closable=closable(), pre_hwnds=set(),
                    process_grace_s=3.0, sleep=slept.append)
    assert d.cancelled and out["terminated_process"] is True and d.current_proc.killed and slept == [3.0]


def test_a_process_that_already_exited_during_the_grace_period_is_left_alone():
    d = QuickDriver()
    d.current_proc = FakeProc(alive_for=0)           # already gone by the time poll() is checked
    out = W.recover(FakeEnv(), d, W.StuckCause(W.PROCESS_ZOMBIE, "zombie"), closable=closable(), pre_hwnds=set(), sleep=lambda s: None)
    assert out["terminated_process"] is False and not d.current_proc.killed


def test_no_tracked_process_means_nothing_is_terminated_but_cancel_still_happens():
    d = QuickDriver()        # no .current_proc at all
    out = W.recover(FakeEnv(), d, W.StuckCause(W.NO_WINDOW_MODEL_SILENT, "quiet"), closable=closable(), pre_hwnds=set())
    assert out["cancelled"] is True and out["terminated_process"] is False


def test_a_cancel_all_that_raises_does_not_stop_recovery():
    class BadCancel:
        current_proc = None

        def cancel_all(self):
            raise RuntimeError("tool server down")
    out = W.recover(FakeEnv(), BadCancel(), W.StuckCause(W.NO_WINDOW_MODEL_SILENT, "quiet"), closable=closable(), pre_hwnds=set())
    assert out["cancelled"] is False            # recorded honestly, and nothing raised out of recover()


# ------------------------------------------------------------------------------------------------------------------------------- log tails
def test_tail_log_keeps_only_content_free_fields_and_redacts_the_rest(tmp_path):
    p = tmp_path / "latency.jsonl"
    p.write_text('{"t": 1.0, "src": "gateway", "total_ms": 820, "provider": "dahl"}\n'
                 '{"t": 2.0, "src": "gateway", "message": "the owner said: please wire $500 to acct 1234"}\n'
                 'not even json\n', encoding="utf-8")
    rows = W.tail_log(str(p))
    assert rows[0] == {"t": 1.0, "src": "gateway", "total_ms": 820, "provider": "dahl"}
    assert "message" not in rows[1] and rows[1] == {"t": 2.0, "src": "gateway"}          # the free-text field is dropped, not kept
    assert rows[2] == {"raw_redacted": True, "chars": len("not even json")}
    assert "$500" not in json.dumps(rows) and "acct" not in json.dumps(rows)


def test_tail_log_is_empty_for_a_missing_or_unset_path():
    assert W.tail_log("") == [] and W.tail_log("/no/such/file-xyz.jsonl") == []


def test_tail_log_respects_n():
    p_rows = "\n".join(json.dumps({"t": float(i)}) for i in range(10))
    import tempfile, os
    with tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False) as f:
        f.write(p_rows + "\n")
        path = f.name
    try:
        assert len(W.tail_log(path, n=3)) == 3 and W.tail_log(path, n=3)[-1]["t"] == 9.0
    finally:
        os.unlink(path)


def test_log_tails_skips_node_and_tool_server_when_no_path_is_configured():
    out = W.log_tails(gateway="", node="", tool_server="")
    assert out == {}


def test_log_tails_includes_only_the_named_logs_that_have_a_path(tmp_path):
    gw = tmp_path / "gw.jsonl"
    gw.write_text('{"t": 5.0, "src": "gateway"}\n', encoding="utf-8")
    out = W.log_tails(gateway=str(gw), node="", tool_server="")
    assert list(out) == ["gateway"] and out["gateway"] == [{"t": 5.0, "src": "gateway"}]


def test__log_quiet_true_when_nothing_was_written_since_the_goal_started():
    logs = {"gateway": [{"t": 10.0}], "node": [], "tool_server": []}
    assert W._log_quiet(logs, since_wall=100.0) is True
    assert W._log_quiet(logs, since_wall=5.0) is False


# ------------------------------------------------------------------------------------------------------------------------------- the stuck-report file
def test_write_stuck_report_shape(tmp_path):
    cause = W.StuckCause(W.HUNG_WINDOW, "a new window is flagged not-responding by Windows", window={"hwnd": 5, "process": "notepad"})
    diag = W.Diagnosis(windows=[{"hwnd": 5, "process": "notepad"}], new_windows=[{"hwnd": 5, "process": "notepad"}],
                       screen_read={"source": "uia", "confidence": 0.8, "chars": 40}, logs={"gateway": []})
    path = W.write_stuck_report(tmp_path, "sm-folder", 3, cause, diag, clock=lambda: 1700000000.0)
    assert path
    data = json.loads(open(path, encoding="utf-8").read())
    assert data["goal"] == "sm-folder" and data["rep"] == 3 and data["cause"] == W.HUNG_WINDOW
    assert data["window"]["hwnd"] == 5 and data["screen_read"]["chars"] == 40
    assert path.startswith(str(tmp_path / W.STUCK_SUBDIR))


def test_write_stuck_report_returns_empty_string_with_no_results_dir():
    cause = W.StuckCause(W.UNKNOWN, "x")
    assert W.write_stuck_report(None, "g", 1, cause, W.Diagnosis()) == ""


def test_write_stuck_report_filename_is_safe_for_a_hostile_goal_id(tmp_path):
    cause = W.StuckCause(W.UNKNOWN, "x")
    path = W.write_stuck_report(tmp_path, "../../etc/passwd; rm -rf", 1, cause, W.Diagnosis())
    assert ".." not in path.split(W.STUCK_SUBDIR, 1)[1] and (tmp_path / W.STUCK_SUBDIR) in __import__("pathlib").Path(path).parents


def test_the_stuck_report_holds_zero_chat_or_owner_text(tmp_path):
    """The window's real title and the screen reading's own text must never reach the file: only counts and flags."""
    env = FakeEnv()
    secret_title = "Rahul's diary: my PIN is 4471, call me at 98765"
    win = env.add_window("notepad", secret_title)
    env.set_text(win, "the owner's private screen content, never to be logged")

    def lister(env):
        return [{"hwnd": win.hwnd, "process": "notepad", "title": secret_title, "class": "Notepad", "hung": True}]

    def reader(env, w):
        return {"source": "uia", "confidence": 0.8, "chars": 999}       # never the text itself

    out = W.run_with_recovery(HangingDriver(), env, "open the diary", 0.02, False, pre_hwnds=set(), closable=[{"process": "notepad"}],
                              grace_s=0.02, results_dir=tmp_path, goal_id="sm-diary", window_lister=lister, screen_reader=reader)
    assert out.raw["stuck_cause"] == W.HUNG_WINDOW
    raw = open(out.raw["stuck_report"], encoding="utf-8").read()
    assert secret_title not in raw and "4471" not in raw and "98765" not in raw and "private screen content" not in raw
    assert "Rahul" not in raw


def test_the_stuck_report_holds_zero_text_from_the_logs_either(tmp_path):
    env = FakeEnv()
    gw = tmp_path / "gw.jsonl"
    gw.write_text('{"t": 9999999999.0, "message": "the owner asked to wire money to a stranger"}\n', encoding="utf-8")
    out = W.run_with_recovery(HangingDriver(), env, "x", 0.02, False, pre_hwnds=set(), closable=closable(), grace_s=0.02,
                              results_dir=tmp_path, goal_id="g", log_paths={"gateway": str(gw)})
    raw = open(out.raw["stuck_report"], encoding="utf-8").read()
    assert "stranger" not in raw and "wire money" not in raw


# ------------------------------------------------------------------------------------------------------------------------------- the whole call, end to end
def test_run_with_recovery_closes_the_hung_window_and_reports_it(tmp_path):
    env = FakeEnv()
    win = env.add_window("notepad", "Untitled - Notepad")

    def lister(env):
        return [{"hwnd": win.hwnd, "process": "notepad", "title": win.title, "class": "Notepad", "hung": True}]
    out = W.run_with_recovery(HangingDriver(), env, "write a note", 0.02, False, pre_hwnds=set(), closable=[{"process": "notepad"}],
                              grace_s=0.02, results_dir=tmp_path, goal_id="sm-note", window_lister=lister)
    assert out.raw["stuck_cause"] == W.HUNG_WINDOW and env.closed == [win] and out.raw["stuck_report"]


def test_run_with_recovery_classifies_no_window_model_silent_and_cancels(tmp_path):
    env = FakeEnv()
    d = HangingDriver()
    gw = tmp_path / "gw.jsonl"
    gw.write_text("", encoding="utf-8")      # nothing written at all: quiet
    out = W.run_with_recovery(d, env, "x", 0.02, False, pre_hwnds=set(), closable=closable(), grace_s=0.02, results_dir=tmp_path, goal_id="g",
                              log_paths={"gateway": str(gw)})
    assert out.raw["stuck_cause"] == W.NO_WINDOW_MODEL_SILENT and d.cancelled


def test_run_with_recovery_classifies_process_zombie_and_terminates_the_tracked_process(tmp_path):
    env = FakeEnv()
    d = HangingDriver(current_proc=FakeProc(alive_for=100))
    out = W.run_with_recovery(d, env, "x", 0.02, False, pre_hwnds=set(), closable=closable(), grace_s=0.02, results_dir=tmp_path, goal_id="g",
                              process_grace_s=0.0, sleep=lambda s: None)
    assert out.raw["stuck_cause"] == W.PROCESS_ZOMBIE and d.current_proc.killed


def test_run_with_recovery_never_closes_a_window_that_was_open_before_the_run(tmp_path):
    env = FakeEnv()
    pre = env.add_window("notepad", "owners-own.txt - Notepad")

    def lister(env):
        return [{"hwnd": pre.hwnd, "process": "notepad", "title": pre.title, "class": "Notepad", "hung": True}]
    out = W.run_with_recovery(HangingDriver(), env, "x", 0.02, False, pre_hwnds={pre.hwnd}, closable=[{"process": "notepad"}],
                              grace_s=0.02, results_dir=tmp_path, goal_id="g", window_lister=lister)
    assert env.closed == [] and pre in env.windows()


# ------------------------------------------------------------------------------------------------------------------------------- wired into runner.run_one
S = "{work}{sep}"


class Clock:
    def __init__(self):
        self.t = 0.0

    def now(self):
        return self.t

    def sleep(self, s):
        self.t += s


def catalogue(goals_by_cap, **caps_kw):
    caps = {}
    for cid, gl in goals_by_cap.items():
        caps[cid] = model.Capability(cid, cid, gl, **caps_kw.get(cid, {}))
    return model.Catalog({"gate": {}, "ignore_processes": ["jarvis"], "cleanup": {"closable": [{"process": "notepad"}]}}, {}, caps, {}, {}, {}, {})


def test_run_one_after_a_recovered_hang_matches_the_normal_invalid_outcome_shape(tmp_path):
    """The rest of the pipeline (classify/report/gate) needs no change: a recovered hang must come back shaped exactly like any other harness trouble."""
    g = model.Goal(id="g1", cap="c1", text="Say ok.", verify=[{"check": "answer_nonempty"}], timeout_s=0.02)
    cat = catalogue({"c1": [g]})
    env = FakeEnv()
    clk = Clock()
    r = runner.Runner(cat, env, HangingDriver(), work_root=str(tmp_path / "w"), outputs="", results_path=tmp_path / "res" / "r.json", repeats=1,
                      sleep=clk.sleep, clock=clk.now, log=lambda s: None, watchdog_grace_s=0.02)
    row = r.run(repeats=1)["runs"][0]
    assert row["outcome"] == K.INVALID and row["harness"] is True and "stuck" in row["cause"]
    assert (tmp_path / "res" / W.STUCK_SUBDIR if False else tmp_path).exists()      # the run finished and wrote a results file (see below)
    saved = json.loads((tmp_path / "res" / "r.json").read_text(encoding="utf-8"))
    assert saved["runs"][0]["outcome"] == K.INVALID


def test_run_one_cleans_up_normally_after_a_recovered_hang(tmp_path):
    g = model.Goal(id="g1", cap="c1", text="Open stuff.", verify=[{"check": "answer_nonempty"}], timeout_s=0.02)
    cat = catalogue({"c1": [g]})
    env = FakeEnv()
    own = env.add_window("notepad", "owners-own.txt - Notepad")
    clk = Clock()
    r = runner.Runner(cat, env, HangingDriver(), work_root=str(tmp_path / "w"), outputs="", results_path=tmp_path / "res" / "r.json", repeats=1,
                      sleep=clk.sleep, clock=clk.now, log=lambda s: None, watchdog_grace_s=0.02)
    r.run(repeats=1)
    assert own in env.windows()               # the owner's own window was never touched by the hang/recovery path either


def test_a_recovered_hang_does_not_stop_the_suite_other_goals_still_run(tmp_path):
    hangs_once = {"n": 0}

    class FlakyDriver:
        name = "flaky"

        def run(self, text, timeout_s, attended):
            if hangs_once["n"] == 0:
                hangs_once["n"] += 1
                time.sleep(10_000)
            return Observation(final_text="ok", seconds=0.1)

        def cancel_all(self):
            pass
    gs = [model.Goal(id=f"g{i}", cap="c1", text="Say ok.", verify=[{"check": "answer_nonempty"}], timeout_s=0.02) for i in range(2)]
    cat = catalogue({"c1": gs})
    env = FakeEnv()
    clk = Clock()
    r = runner.Runner(cat, env, FlakyDriver(), work_root=str(tmp_path / "w"), outputs="", results_path=tmp_path / "res" / "r.json", repeats=1,
                      sleep=clk.sleep, clock=clk.now, log=lambda s: None, watchdog_grace_s=0.02)
    data = r.run(repeats=1)
    outcomes = {row["goal"]: row["outcome"] for row in data["runs"]}
    assert outcomes["g0"] == K.INVALID and outcomes["g1"] == K.PASS         # one goal hung and was recovered; the suite went on to the next


def test_the_watchdog_hook_does_not_fire_for_an_ordinary_quick_goal(tmp_path):
    g = model.Goal(id="g1", cap="c1", text="Say ok.", verify=[{"check": "answer_nonempty"}], timeout_s=5.0)
    cat = catalogue({"c1": [g]})
    env = FakeEnv()
    drv = ScriptedDriver(lambda t: Observation(final_text="ok", seconds=0.5))
    drv.name = "scripted"
    clk = Clock()
    r = runner.Runner(cat, env, drv, work_root=str(tmp_path / "w"), outputs="", results_path=tmp_path / "res" / "r.json", repeats=1,
                      sleep=clk.sleep, clock=clk.now, log=lambda s: None)
    row = r.run(repeats=1)["runs"][0]
    assert row["outcome"] == K.PASS and not row.get("harness")
