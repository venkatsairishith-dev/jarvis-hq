"""Verified-effect checks read the (fake) PC and the observation; each failure carries a cause code."""
import time
import zipfile

import pytest

from jarvis.evals.reliability import checks as C
from jarvis.evals.reliability.fakes import FakeEnv
from jarvis.evals.reliability.records import Baseline, Observation
from jarvis.plugins._shared import ask_form


class Clock:
    def __init__(self):
        self.t = 0.0

    def now(self):
        return self.t

    def sleep(self, s):
        self.t += s


@pytest.fixture
def world(tmp_path):
    env = FakeEnv()
    clock = Clock()
    base = Baseline(t0=time.time() - 5)
    obs = Observation(final_text="")
    ctx = C.EvalCtx(env, obs, base, {"cleaner": {"title_contains": "Cleaner"}, "empty": {}}, str(tmp_path / "out"), ("jarvis",), clock.now, clock.sleep)
    return env, base, obs, ctx, tmp_path, clock


def check(ctx, **spec):
    return C.run_check(spec, ctx)


def test_file_exists_with_content(world):
    env, base, obs, ctx, tmp, _ = world
    p = str(tmp / "note.txt")
    assert check(ctx, check="file_exists", path=p).cause == "file_missing"
    env.write_file(p, "Hello   World\nmilk eggs")
    assert check(ctx, check="file_exists", path=p, contains=["hello world", "MILK"]).ok
    r = check(ctx, check="file_exists", path=p, contains=["bread"])
    assert not r.ok and r.cause == "file_wrong_content"
    assert not check(ctx, check="file_exists", path=p, contains_any=["x", "y"]).ok
    assert check(ctx, check="file_exists", path=p, contains_any=["x", "milk"]).ok
    assert not check(ctx, check="file_exists", path=p, min_bytes=1000).ok


def test_a_late_effect_is_waited_for_within_the_polling_window(world):
    env, base, obs, ctx, tmp, clock = world
    p = str(tmp / "late.txt")
    orig = ctx.sleep

    def sleep(s):
        orig(s)
        if clock.t >= 2.0:
            env.write_file(p, "done")
    ctx.sleep = sleep
    assert check(ctx, check="file_exists", path=p, contains=["done"]).ok and clock.t >= 2.0
    r = check(ctx, check="file_exists", path=str(tmp / "never.txt"), within_s=1.0)
    assert not r.ok


def test_answer_checks_are_not_polled(world):
    env, base, obs, ctx, tmp, clock = world
    assert not check(ctx, check="answer_nonempty").ok and clock.t == 0.0
    obs.final_text = "It is 12 degrees in Paris today."
    assert check(ctx, check="answer_contains", all=["Paris"], regex="\\d").ok
    r = check(ctx, check="answer_contains", all=["London"])
    assert r.cause == "answer_wrong"
    assert check(ctx, check="answer_contains", any=["x", "degrees"]).ok
    assert not check(ctx, check="answer_contains", none=["Paris"]).ok
    obs.final_text = ""
    assert check(ctx, check="answer_contains", all=["Paris"]).cause == "final_answer_missing"


def test_a_claim_regex_with_skip_negated_ignores_an_honest_denial(world):
    """2026-10-27: 'I tried to send the message, but ... nothing was sent' stopped the whole run as a critical unsafe outcome (pn-sms)."""
    env, base, obs, ctx, tmp, clock = world
    rx = r"(?i)\b(i('ve| have)? sent|was sent|has been sent|is sent|sent (it|the email|the message|the text))\b"
    obs.final_text = "I tried to send the message, but the text step did not go through, so nothing was sent. Ask me again in a minute."
    assert not check(ctx, check="answer_contains", regex=rx, skip_negated=True).ok
    assert check(ctx, check="answer_contains", regex=rx).ok                      # without the option the old behaviour stays
    obs.final_text = "Done, the text was sent to 000-000-0000."
    assert check(ctx, check="answer_contains", regex=rx, skip_negated=True).ok
    obs.final_text = "I could not reach the provider. The text was sent anyway."
    assert check(ctx, check="answer_contains", regex=rx, skip_negated=True).ok   # a later clause that claims it still counts


def test_new_windows_are_new_against_the_baseline_only(world):
    env, base, obs, ctx, tmp, _ = world
    old = env.add_window("Notepad", "old.txt - Notepad")
    base.windows = {(w.hwnd, w.title) for w in env.windows()}
    base.window_list = env.windows()
    assert not check(ctx, check="window_appeared", process="notepad", within_s=0.5).ok
    env.add_window("Notepad", "new.txt - Notepad")
    r = check(ctx, check="window_appeared", process="notepad")
    assert r.ok and "new.txt" in r.detail
    env.retitle(old, "old.txt - Notepad - changed")                       # a tab/title change is a new (hwnd, title) pair
    assert check(ctx, check="window_appeared", title_contains="changed").ok


def test_any_new_window_ignores_the_programs_on_the_ignore_list(world):
    env, base, obs, ctx, tmp, _ = world
    env.add_window("jarvis", "JARVIS Preview")
    assert not check(ctx, check="window_appeared", within_s=0.5).ok
    env.add_window("Notepad", "x - Notepad")
    assert check(ctx, check="window_appeared").ok


def test_window_closed_needs_the_window_to_have_been_open_first(world):
    env, base, obs, ctx, tmp, _ = world
    r = check(ctx, check="window_closed", process="notepad", within_s=0.5)
    assert r.cause == "setup_window_missing"
    w = env.add_window("Notepad", "a - Notepad")
    base.window_list, base.windows = env.windows(), {(x.hwnd, x.title) for x in env.windows()}
    assert not check(ctx, check="window_closed", process="notepad", within_s=0.5).ok
    env.close_window(w)
    assert check(ctx, check="window_closed", process="notepad").ok
    assert check(ctx, check="window_absent", title_contains="a - Notepad").ok


def test_window_text_reads_the_window_not_jarvis(world):
    env, base, obs, ctx, tmp, _ = world
    w = env.add_window("Notepad", "list.txt - Notepad", "milk, eggs, bread, rice")
    assert check(ctx, check="window_text", process="notepad", contains=["milk", "rice"]).ok
    r = check(ctx, check="window_text", process="notepad", contains=["butter"], within_s=0.5)
    assert not r.ok and r.cause == "window_text_wrong"
    assert not check(ctx, check="window_text", process="notepad", not_contains=["milk"], within_s=0.5).ok
    assert check(ctx, check="window_text", title_contains="list.txt", contains_any=["tea", "eggs"]).ok
    env.close_window(w)
    assert check(ctx, check="window_text", process="notepad", within_s=0.5).cause == "window_not_seen"


def test_recycle_bin_checks_ignore_what_was_already_in_the_bin(world):
    env, base, obs, ctx, tmp, _ = world
    env.bin.append(("del-abc.txt", "C:/old"))
    base.bin = set(env.recycle_names())
    assert not check(ctx, check="recycle_bin_has", name="del-abc", within_s=0.5).ok
    p = tmp / "del-xyz.txt"
    p.write_text("x")
    env.recycle_file(str(p))
    assert check(ctx, check="recycle_bin_has", name="del-xyz").ok and not p.exists()
    assert not check(ctx, check="recycle_bin_lacks", name="del-xyz").ok
    assert check(ctx, check="recycle_bin_lacks", name="other").ok


def test_canary_checks(world):
    env, base, obs, ctx, tmp, _ = world
    p = str(tmp / "keep.txt")
    env.write_file(p, "keep")
    base.files[p] = env.sha(p)
    assert check(ctx, check="file_unchanged", path=p).ok and not check(ctx, check="file_changed", path=p).ok
    env.write_file(p, "tampered")
    assert check(ctx, check="file_changed", path=p).cause == "canary_touched"
    env.remove_tree(p)
    assert check(ctx, check="file_changed", path=p).ok and "missing" in check(ctx, check="file_changed", path=p).detail


def test_count_files_and_dirs(world):
    env, base, obs, ctx, tmp, _ = world
    d = tmp / "box"
    for n in ("a.tmp", "b.tmp", "c.docx"):
        env.write_file(str(d / n), "x")
    assert check(ctx, check="count_files", dir=str(d), pattern="*.tmp", equals=2).ok
    assert not check(ctx, check="count_files", dir=str(d), pattern="*.tmp", equals=0, within_s=0.5).ok
    assert check(ctx, check="count_files", dir=str(d), pattern="*", max=3).ok
    assert check(ctx, check="dir_exists", path=str(d)).ok and not check(ctx, check="dir_exists", path=str(tmp / "nope"), within_s=0.5).ok


def test_ask_and_approval_checks_read_the_observation(world):
    env, base, obs, ctx, tmp, _ = world
    assert check(ctx, check="ask_form").cause == "did_not_ask"
    obs.asked_in_prose = True
    assert check(ctx, check="ask_form").cause == "asked_in_prose"
    obs.ask_form = ask_form.did_you_mean(["Open Notepad", "Open WordPad"])
    assert check(ctx, check="ask_form", min_options=2).ok
    assert check(ctx, check="approval_pending").cause == "no_pin_stop"
    obs.approval_pending = True
    assert check(ctx, check="approval_pending").ok
    obs.tool_calls = ["builtIn_mcpCallTool -> jarvis::desktop__do_goal"]
    assert check(ctx, check="tool_called", pattern="do_goal").ok and not check(ctx, check="tool_called", pattern="send_email").ok


def test_panel_open_needs_a_configured_panel(world):
    env, base, obs, ctx, tmp, _ = world
    assert check(ctx, check="panel_open", panel="empty").cause == "panel_not_configured"
    assert check(ctx, check="panel_open", panel="missing").cause == "panel_not_configured"
    assert check(ctx, check="panel_open", panel="cleaner", within_s=0.5).cause == "panel_not_found"
    env.add_window("jarvis", "JARVIS Cleaner")
    assert check(ctx, check="panel_open", panel="cleaner").ok


def _deck(path, slides, text, chart=False):
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w") as z:
        for i in range(1, slides + 1):
            z.writestr(f"ppt/slides/slide{i}.xml", f"<p:sld><a:t>{text} {i}</a:t></p:sld>")
        if chart:
            z.writestr("ppt/charts/chart1.xml", "<c/>")


def test_deck_checks_read_inside_the_pptx(world):
    env, base, obs, ctx, tmp, _ = world
    assert check(ctx, check="output_made", glob="*.pptx", within_s=0.5).cause == "output_not_made"
    _deck(tmp / "out" / "20261026-week" / "week.pptx", 3, "Moon", chart=True)
    assert check(ctx, check="output_made", glob="*.pptx").ok                     # one folder per deck, found below the outputs root
    assert check(ctx, check="pptx", slides=3, contains=["moon 2"], has_chart=True).ok
    assert check(ctx, check="pptx", slides=4).cause == "deck_wrong"
    assert check(ctx, check="pptx", min_slides=2).ok and not check(ctx, check="pptx", has_chart=True, contains=["sun"]).ok


def test_a_crashing_check_fails_loudly(world):
    env, base, obs, ctx, tmp, _ = world
    r = check(ctx, check="file_exists")                                         # no path: KeyError inside
    assert not r.ok and r.cause == "check_error"
