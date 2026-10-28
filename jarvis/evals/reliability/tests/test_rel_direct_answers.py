"""D75: the `direct_answers` capability of goals.json. A plain question must be ANSWERED (the right number in the final answer), with NO app window, no PC-task tool and no model
round trip; the ten goals that NAME the Calculator must still open it.

The expected numbers are worked out in this file with fractions.Fraction / decimal.Decimal and plain Python (never with direct_answer.py), then
  1. every goal's regex must accept the right number written plainly, with western commas and with Indian commas, and must reject its neighbours (+1, x10, a digit more);
  2. the real quick path (plugins/desktop/quick.py) is run on every goal's text through the harness' own runner on the pretend PC, and the accuracy is counted.
"""
import calendar
import datetime
import re
from decimal import ROUND_HALF_UP, Decimal
from fractions import Fraction as F

import pytest

from jarvis.evals.reliability import classify as K
from jarvis.evals.reliability import model, runner
from jarvis.evals.reliability.drivers import ScriptedDriver
from jarvis.evals.reliability.fakes import FakeEnv
from jarvis.evals.reliability.records import Observation
from jarvis.plugins.desktop import quick as Q

CAT = model.load()
CAP = CAT.capabilities["direct_answers"]
DIRECT = [g for g in CAP.goals if "direct" in g.tags]
EXPLICIT = [g for g in CAP.goals if "explicit_app" in g.tags]
BY = {g.id: g for g in CAP.goals}


def dec(x, places):
    """Independent ROUND_HALF_UP of a Fraction, as plain text with its zeros kept."""
    return str((Decimal(x.numerator) / Decimal(x.denominator)).quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_UP))


def emi(principal, annual_percent, months):
    """The payment that leaves nothing owed after `months` months of interest, found by bisection on a month-by-month loan (not the closed formula)."""
    r = Decimal(annual_percent) / 1200
    lo, hi = Decimal(0), Decimal(principal)
    for _ in range(200):
        mid = (lo + hi) / 2
        bal = Decimal(principal)
        for _ in range(months):
            bal = bal * (1 + r) - mid
        lo, hi = (mid, hi) if bal > 0 else (lo, mid)
    return str(((lo + hi) / 2).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def leap(y):
    return y % 4 == 0 and (y % 100 != 0 or y % 400 == 0)


F_ = F
ORACLE = {                                     # goal id -> the number the answer must hold, as text
    "da-mul": str(23 * 47),
    "da-add": str(2 + 3),
    "da-div-exact": str(F(1200, 8).numerator),
    "da-div-repeating": dec(F(1200, 7), 4),
    "da-sqrt": str(int(144 ** 0.5 + 0.5)),
    "da-power": str(2 ** 10),
    "da-brackets": str((3 + 4) * 5),
    "da-chain": str(100 + 25 - 40),
    "da-decimal": str(Decimal("0.1") + Decimal("0.2")),
    "da-big-multiply": str(12345 * 6789),
    "da-words": str(23 * 47),
    "da-factorial": str(1 * 2 * 3 * 4 * 5 * 6),
    "da-negative": str(15 - 40),
    "da-lakh": str(5 * 100000 + 2 * 100000),
    "da-indian-grouping": str(100000 + 50000),
    "da-percent": str(F(15, 100) * 2400),
    "da-percent-decimal": str(F(125, 1000) * 80),
    "da-percent-change": str((F(100) - 80) / 80 * 100),
    "da-discount": str(Decimal("1999") * (1 - Decimal("0.30"))),
    "da-gst": str(Decimal("2500") * Decimal("1.18")),
    "da-tip": str(Decimal("840") * Decimal("0.15")),
    "da-split": str(Decimal("1250") / 4),
    "da-emi-5-lakh": emi(500000, 9, 60),
    "da-emi-10-lakh": emi(1000000, Decimal("8.5"), 240),
    "da-simple-interest": str(10000 * 8 * 3 // 100),
    "da-km-miles": dec(F(5 * 1000) / F(1609344, 1000), 4),
    "da-c-to-f": str(100 * 9 // 5 + 32),
    "da-f-to-c": str((F("98.6") - 32) * 5 / 9),
    "da-kg-pounds": dec(F(10) / F(45359237, 10 ** 8), 4),
    "da-gb-mb": str(2 * 1024),
    "da-minutes-hours": str(540 // 60),
    "da-seconds-day": str(24 * 60 * 60),
    "da-mph-kmph": str(Decimal("60") * Decimal("1.609344")),
    "da-inches-cm": str(Decimal("5") * Decimal("2.54")),
    "da-days-between": str((datetime.date(2026, 3, 15) - datetime.date(2026, 1, 1)).days),
    "da-february-days": str(calendar.monthrange(2028, 2)[1]),
    "da-te-multiply": str(23 * 47),
    "da-te-add": str(45 + 55),
    "da-te-subtract": str(100 - 40),
    "da-te-divide": dec(F(1200, 7), 4),
    "da-te-percent": str(F(15, 100) * 2400),
    "da-hi-multiply": str(23 * 47),
    "da-hi-divide": str(1200 // 8),
    "da-hi-add": str(50 + 70),
    "da-hi-subtract": str(100 - 30),
    "da-hi-percent": str(F(15, 100) * 2400),
    "da-hi-lakh": str(2 * 100000 + 3 * 100000),
}
# goals whose answer is a word, a date or a clock, checked on the answer text itself
WORDS = {"da-weekday-1947": "Friday", "da-leap-year": "Yes" if leap(2028) else "No", "da-today-weekday": None, "da-today-date": None, "da-date-plus-45": None,
         "da-days-to-christmas": None, "da-time-now": None}
EXPLICIT_EXPECTED = {"da-app-open-and-work-out": str(15 + 27), "da-app-in-calculator": str(12 * 7), "da-app-open-and-do": str(2 + 3), "da-app-use-the-app": str(9 * 9),
                     "da-app-show-on-screen": str(23 * 47), "da-app-calc-division": str(144 // 12), "da-app-launch": str(250 + 750)}


def plain(v):
    v = str(v)
    if "." in v:
        v = v.rstrip("0").rstrip(".") if v.count(".") == 1 and set(v.split(".")[1]) <= {"0"} else v
    return v


def western(v):
    ip, _, fp = v.lstrip("-").partition(".")
    return ("-" if v.startswith("-") else "") + f"{int(ip):,}" + ("." + fp if fp else "")


def indian(v):
    sign = "-" if v.startswith("-") else ""
    ip, _, fp = v.lstrip("-").partition(".")
    head, tail = ip[:-3], ip[-3:]
    groups = []
    while len(head) > 2:
        groups.insert(0, head[-2:])
        head = head[:-2]
    if head:
        groups.insert(0, head)
    return sign + ",".join(groups + [tail] if len(ip) > 3 else [ip]) + ("." + fp if fp else "")


def number_regex(goal):
    for c in goal.verify:
        if c["check"] == "answer_contains" and "regex" in c and not c["regex"].startswith(r"\b"):
            return c["regex"]
    return None


def sentence_with(v):
    return f"The answer is {v}."


# --------------------------------------------------------------------------------------------------------------------- the corpus is what the owner asked for
def test_the_capability_is_there_with_enough_goals_in_three_languages():
    assert len(DIRECT) >= 40 and len(DIRECT) == 54
    assert len(EXPLICIT) == 10 and len(CAP.goals) == 64
    assert {g.lang for g in DIRECT} == {"en", "te-rom", "hi-rom"}
    assert sum(g.lang == "te-rom" for g in DIRECT) >= 5 and sum(g.lang == "hi-rom" for g in DIRECT) >= 5
    assert CAP.needs_driver == "face" and model.validate(CAT) == []
    assert all(g.kind == "act" for g in CAP.goals)


def test_every_direct_goal_wants_the_answer_no_app_no_task_and_no_model_call():
    for g in DIRECT:
        checks = {c["check"] for c in g.verify}
        assert "answer_contains" in checks and "model_requests" in checks, g.id
        assert next(c for c in g.verify if c["check"] == "model_requests")["max"] == 0
        forbids = {c["check"] for c in g.forbid}
        assert {"window_appeared", "tool_called"} <= forbids, g.id
        assert any(c.get("title_contains") == "Calculator" and c["severity"] == 3 for c in g.forbid if c["check"] == "window_appeared")
        assert re.search(g.forbid[1]["pattern"], "jarvis::desktop__do_goal") and re.search(g.forbid[1]["pattern"], "jarvis_do")
        assert not re.search(g.forbid[1]["pattern"], "desktop__quick_command"), "the quick hook is the RIGHT door"


def test_every_explicit_app_goal_wants_the_calculator_window():
    for g in EXPLICIT:
        assert any(c["check"] == "window_appeared" and c["title_contains"] == "Calculator" for c in g.verify), g.id
        assert not g.forbid
        assert re.search(r"(?i)calculator|screen|calc", g.text), g.id


# --------------------------------------------------------------------------------------------------------------------- the expected numbers, independently
def test_the_oracle_covers_every_numeric_goal_and_every_other_goal_is_listed_as_words():
    numeric = {g.id for g in DIRECT if number_regex(g)}
    assert numeric == set(ORACLE), (numeric ^ set(ORACLE))
    assert {g.id for g in DIRECT} - numeric == set(WORDS)


@pytest.mark.parametrize("gid", sorted(ORACLE))
def test_the_goal_accepts_the_right_number_in_every_notation_and_rejects_its_neighbours(gid):
    rx = re.compile(number_regex(BY[gid]), re.I | re.S)
    v = ORACLE[gid]
    for form in {plain(v), western(plain(v)), indian(plain(v)), v}:
        assert rx.search(sentence_with(form)), (gid, form, rx.pattern)
        assert rx.search(f"{form} is the result"), (gid, form)
    d = Decimal(plain(v))
    neighbours = {str(d + 1), str(d - 1), str(d * 10), "1" + plain(v).lstrip("-"), str(d + Decimal("0.01"))} - {plain(v)}
    if "." not in plain(v):
        neighbours.add(plain(v) + "0")
    for wrong in neighbours:
        assert not rx.search(sentence_with(wrong)), (gid, wrong)
    assert not rx.search("I could not work that out.")


@pytest.mark.parametrize("gid", sorted(ORACLE))
def test_the_answer_is_never_just_a_number_already_in_the_question(gid):
    asked = set(re.findall(r"\d+(?:\.\d+)?", BY[gid].text.replace(",", "")))
    want = plain(ORACLE[gid]).lstrip("-")
    assert want not in asked, f"{gid}: the expected {want} is in the question, so an echo of it would pass"


# --------------------------------------------------------------------------------------------------------------------- the real quick path, through the harness' runner
def run_goals(tmp_path, goals, jarvis):
    class Clock:
        def __init__(self):
            self.t = 0.0

        def now(self):
            return self.t

        def sleep(self, s):
            self.t += s

    clk, env = Clock(), FakeEnv()
    drv = ScriptedDriver(lambda text: jarvis(env, text))
    drv.name = "face"
    cat = model.load()
    r = runner.Runner(cat, env, drv, work_root=str(tmp_path / "w"), outputs=str(tmp_path / "out"), results_path=tmp_path / "r.json", repeats=1,
                      sleep=clk.sleep, clock=clk.now, log=lambda s: None, stop_on_critical=False, pin=runner.PinSwitch(str(tmp_path / "pin.txt")))
    return {g.id: r.run_one(g, 1) for g in goals}


def the_quick_path(env, text):
    """JARVIS as the face + rules-first hook make it: the hook answers a question; for anything else the hook says handled=false and (here) the Calculator opens."""
    cmd = Q.match(text, [], datetime.datetime.now())
    if cmd is not None and cmd.kind == "answer":
        return Observation(final_text=cmd.reply, tool_calls=["desktop__quick_command"], seconds=0.1, requests=0)
    env.open_app("calc")
    return Observation(final_text="Opened Calculator.", tool_calls=["jarvis::desktop__do_goal"], seconds=9.0, requests=2)


def test_the_real_quick_path_answers_every_direct_goal_and_never_opens_an_app(tmp_path):
    rows = run_goals(tmp_path, DIRECT, the_quick_path)
    wrong = {gid: (r["outcome"], r.get("cause"), r.get("answer")) for gid, r in rows.items() if r["outcome"] != K.PASS}
    accuracy = (len(rows) - len(wrong)) / len(rows)
    assert not wrong, f"accuracy {accuracy:.1%}: {wrong}"
    assert len(rows) == 54 and accuracy == 1.0
    assert all(r["requests"] == 0 for r in rows.values())


def test_an_explicit_calculator_request_is_not_answered_by_the_rules(tmp_path):
    for g in EXPLICIT:
        cmd = Q.match(g.text, [], datetime.datetime.now())
        assert cmd is None or cmd.kind != "answer", f"{g.id}: {g.text!r} was answered although the owner named the app"


def calculator_jarvis(env, text):
    env.open_app("calc")
    for gid, want in EXPLICIT_EXPECTED.items():
        if BY[gid].text == text:
            return Observation(final_text=f"Opened Calculator. {text.split('.')[0]} comes to {want}.", tool_calls=["jarvis::desktop__do_goal"], seconds=9.0, requests=2)
    return Observation(final_text="Opened Calculator.", tool_calls=["jarvis::desktop__do_goal"], seconds=9.0, requests=2)


def test_the_explicit_calculator_goals_pass_only_when_the_window_appears(tmp_path):
    rows = run_goals(tmp_path, EXPLICIT, calculator_jarvis)
    assert {gid: r["outcome"] for gid, r in rows.items()} == {g.id: K.PASS for g in EXPLICIT}
    answered = run_goals(tmp_path / "b", EXPLICIT, lambda env, text: Observation(final_text="It is 84.", tool_calls=["desktop__quick_command"], requests=0, seconds=0.1))
    assert all(r["outcome"] == K.FAIL and r["cause"] == "window_not_seen" for r in answered.values()), "answering instead of opening the app fails these ten"


def test_expected_numbers_of_the_explicit_goals_are_right():
    for gid, want in EXPLICIT_EXPECTED.items():
        rx = re.compile(number_regex(BY[gid]))
        assert rx.search(f"it is {want}.") and not rx.search(f"it is {int(want) + 1}."), gid


# --------------------------------------------------------------------------------------------------------------------- what the gate does with the wrong behaviours
def test_opening_the_calculator_for_a_question_is_unsafe_minor_and_fails_the_capability(tmp_path):
    def opens_app(env, text):
        env.open_app("calc")
        return Observation(final_text="The answer is 1,081.", tool_calls=["jarvis::desktop__do_goal"], seconds=9.0, requests=2)
    rows = run_goals(tmp_path, [BY["da-mul"]], opens_app)
    r = rows["da-mul"]
    assert r["outcome"] == K.UNSAFE and r["severity"] == 3


def test_a_wrong_number_a_missing_answer_and_a_model_round_trip_each_fail(tmp_path):
    cases = {
        "wrong number": Observation(final_text="23 times 47 is 1,082.", tool_calls=["desktop__quick_command"], requests=0, seconds=0.1),
        "no answer": Observation(final_text="", tool_calls=[], requests=0, seconds=0.1),
        "echo only": Observation(final_text="You asked what 23 times 47 is.", tool_calls=[], requests=0, seconds=0.1),
    }
    for name, obs in cases.items():
        row = run_goals(tmp_path / name.replace(" ", "_"), [BY["da-mul"]], lambda env, text, o=obs: o)["da-mul"]
        assert row["outcome"] == K.FAIL, name
    asked_a_model = run_goals(tmp_path / "model", [BY["da-mul"]], lambda env, text: Observation(final_text="23 times 47 is 1,081.", tool_calls=[], requests=1, seconds=7.0))["da-mul"]
    assert asked_a_model["outcome"] == K.FAIL and asked_a_model["cause"] == "model_called"


def test_a_task_tool_call_without_a_window_is_also_caught(tmp_path):
    row = run_goals(tmp_path, [BY["da-mul"]], lambda env, text: Observation(final_text="1,081.", tool_calls=["jarvis_do"], requests=0, seconds=3.0))["da-mul"]
    assert row["outcome"] == K.UNSAFE and row["severity"] == 3


def test_doing_nothing_passes_no_direct_goal(tmp_path):
    rows = run_goals(tmp_path, CAP.goals, lambda env, text: Observation(final_text="", tool_calls=[], requests=0, seconds=0.1))
    assert [gid for gid, r in rows.items() if r["outcome"] in (K.PASS, K.GRACEFUL, K.UNSAFE)] == []


# --------------------------------------------------------------------------------------------------------------------- the dates and the clock goals use the harness' own fields
def test_the_date_fields_are_filled_for_the_goals_that_need_them():
    ctx = model.make_context("W", "\\", "O", "seed", now=datetime.datetime(2026, 10, 27, 12, 0).timestamp())
    assert ctx.plus45 == "11 December 2026" and ctx.xmas_days == "59" and ctx.weekday == "Tuesday" and ctx.day == "27" and ctx.month == "October" and ctx.year == "2026"
    after = model.make_context("W", "\\", "O", "seed", now=datetime.datetime(2026, 12, 26, 12, 0).timestamp())
    assert after.xmas_days == str((datetime.date(2027, 12, 25) - datetime.date(2026, 12, 26)).days)
    rendered = model.render(BY["da-date-plus-45"], ctx)
    assert rendered.verify[0]["all"] == ["11 December 2026"]
    assert model.render(BY["da-days-to-christmas"], ctx).verify[0]["any"][0] == "59 days"


def test_the_new_check_and_cause_are_registered():
    assert "model_requests" in model.CHECKS and "model_called" in K.CAUSES
    assert "model_called" in CAT.levers["by_cause"]
