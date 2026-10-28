"""D75: plugins/_shared/direct_answer.py answers plain questions (maths, conversions, dates, money) exactly, locally, and says "not mine" (None) for anything else.

Every expected number below is worked out HERE with fractions.Fraction / decimal.Decimal and plain Python, never with the module's own helpers, so a mistake in the
module cannot hide in a test that shares it. Rounded answers are compared with an independent ROUND_HALF_UP.
"""
import datetime as dt
import random
import socket
import subprocess
import time
from decimal import ROUND_HALF_UP, Decimal
from fractions import Fraction as F

import pytest

from jarvis.plugins._shared import direct_answer as D

TZ = dt.timezone(dt.timedelta(hours=5, minutes=30), "India Standard Time")
NOW = dt.datetime(2026, 10, 27, 15, 30, tzinfo=TZ)                      # Tuesday 27 October 2026, 3:30 PM


def ask(text, now=NOW):
    return D.answer(text, now)


def val(text):
    got = ask(text)
    assert got is not None, f"no answer for {text!r}"
    return got


def rounded(x, places):
    """Independent ROUND_HALF_UP of a Fraction to `places` decimals, as the text a person would read (trailing zeros trimmed)."""
    q = (Decimal(x.numerator) / Decimal(x.denominator)).quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_UP)
    s = format(q, "f")
    return s.rstrip("0").rstrip(".") if "." in s else s


def exact_text(x):
    """A terminating Fraction as its decimal text."""
    d = Decimal(x.numerator) / Decimal(x.denominator)
    s = format(d, "f")
    return s.rstrip("0").rstrip(".") if "." in s else s


# --------------------------------------------------------------------------------------------------------------------- arithmetic: the exact answer
EXACT = [
    ("23 times 47", F(23) * 47),
    ("what is 2+3", F(5)),
    ("what is 2 + 3", F(5)),
    ("2+3", F(5)),
    ("12 x 12", F(144)),
    ("12x12", F(144)),
    ("12 * 12", F(144)),
    ("12 × 12", F(144)),
    ("100 ÷ 8", F(100) / 8),
    ("what is 100 divided by 8", F(100) / 8),
    ("how much is 7 times 8", F(56)),
    ("please calculate 9 times 9", F(81)),
    ("hey jarvis, what's 15 plus 27?", F(42)),
    ("Jarvis, 12 into 12", F(144)),
    ("compute 1000 minus 1", F(999)),
    ("work out 3 + 4 * 5", F(23)),
    ("(3 + 4) * 5", F(35)),
    ("(3+4)*(5-2)", F(21)),
    ("10 plus 5 minus 3", F(12)),
    ("2 plus 3 times 4", F(14)),
    ("100 minus 30 minus 20", F(50)),
    ("100 divided by 5 divided by 4", F(5)),
    ("2 to the power 10", F(1024)),
    ("2^10", F(1024)),
    ("2**10", F(1024)),
    ("2 ^ 3 ^ 2", F(512)),
    ("10 squared", F(100)),
    ("5 cubed", F(125)),
    ("square of 12", F(144)),
    ("cube of 3", F(27)),
    ("square root of 144", F(12)),
    ("sqrt(81)", F(9)),
    ("square root of 0.25", F(1, 2)),
    ("square root of 2.25", F(3, 2)),
    ("cube root of 27", F(3)),
    ("cube root of 0.125", F(1, 2)),
    ("5!", F(120)),
    ("factorial of 6", F(720)),
    ("factorial of 0", F(1)),
    ("7 mod 3", F(1)),
    ("remainder when 17 is divided by 5", F(2)),
    ("absolute value of -7", F(7)),
    ("abs(-5)", F(5)),
    ("0.1 + 0.2", F(3, 10)),
    ("0.3 - 0.1", F(1, 5)),
    ("1.1 * 1.1", F(121, 100)),
    ("3/4 + 1/8", F(7, 8)),
    ("1/2 + 1/3 + 1/6", F(1)),
    ("-5 + 3", F(-2)),
    ("minus 5 plus 3", F(-2)),
    ("2^-1", F(1, 2)),
    ("(-2)^2", F(4)),
    ("(-2)^3", F(-8)),
    ("2 x 3 x 4", F(24)),
    ("one hundred and five plus two", F(107)),
    ("twenty three times forty seven", F(23) * 47),
    ("three point five times two", F(7)),
    ("a hundred plus a thousand", F(1100)),
    ("two thousand five hundred minus one thousand", F(1500)),
    ("12345678901234567890 * 98765432109876543210", F(12345678901234567890) * 98765432109876543210),
    ("999999999999 + 1", F(10 ** 12)),
    ("half of 90", F(45)),
    ("quarter of 200", F(50)),
    ("double 45", F(90)),
    ("twice 7", F(14)),
    ("triple 12", F(36)),
    ("multiply 6 by 7", F(42)),
    ("subtract 3 from 10", F(7)),
    ("divide 10 by 4", F(5, 2)),
    ("add 5 and 3", F(8)),
    ("sum of 10, 20 and 30", F(60)),
    ("add 5, 10 and 15", F(30)),
    ("average of 4, 8, 15, 16, 23, 42", F(4 + 8 + 15 + 16 + 23 + 42, 6)),
    ("largest of 3, 9 and 4", F(9)),
    ("smallest of 3, 9 and 4", F(3)),
    ("gcd of 12 and 18", F(6)),
    ("lcm of 4 and 6", F(12)),
    ("hcf of 36 and 48", F(12)),
]


@pytest.mark.parametrize("text,want", EXACT, ids=[t for t, _ in EXACT])
def test_exact_arithmetic(text, want):
    got = val(text)
    assert not got.approx, got
    assert F(got.value) == want, (got.text, want)


APPROX = [                                           # (question, exact value, places the sentence must round to)
    ("what is 1200 divided by 7", F(1200, 7), 4),
    ("what is 1/3", F(1, 3), 4),
    ("what is 2/3", F(2, 3), 4),
    ("22 / 7", F(22, 7), 4),
    ("1 / 7000", F(1, 7000), 7),
    ("100 / 3", F(100, 3), 4),
    ("average of 10, 20 and 35", F(65, 3), 4),
    ("1 divided by 6", F(1, 6), 4),
    ("5 / 9", F(5, 9), 4),
]


@pytest.mark.parametrize("text,want,places", APPROX, ids=[t for t, *_ in APPROX])
def test_non_terminating_results_say_about_and_how(text, want, places):
    got = val(text)
    assert got.approx and "about" in got.text and "rounded to" in got.text, got.text
    assert got.value == rounded(want, places), (got.value, rounded(want, places))


def test_a_division_that_is_not_exact_also_shows_the_exact_fraction():
    assert "exactly 1200/7" in val("1200 divided by 7").text


def test_roots_that_are_not_exact_are_rounded_and_said_so():
    r2 = val("square root of 2")
    assert r2.approx and r2.value == "1.4142" and "rounded to 4 decimal places" in r2.text
    assert val("cube root of 10").value == "2.1544"
    assert val("square root of 3").value == "1.7321"
    assert val("square root of 1000000").value == "1000"


def test_no_float_artefacts_in_any_sum():
    assert "0.30000000000000004" not in val("0.1 + 0.2").text
    assert val("0.1 + 0.2").value == "0.3"
    assert val("1.15 * 100").value == "115"
    assert val("4.35 * 100").value == "435"


def test_a_bare_sign_between_digits_is_a_sum_only_when_it_is_asked_or_spaced():
    for text, want in (("what is 12-5", 7), ("calculate 10-3", 7), ("10 - 3", 7), ("10-3?", 7), ("what is 3/4", F(3, 4)), ("1 / 4", F(1, 4)), ("2+3", 5)):
        assert F(val(text).value) == want, text
    for text in ("12-5", "3/4", "100/8"):
        assert ask(text) is None


def test_the_sentence_has_the_number_and_the_working():
    assert val("23 times 47").text == "23 times 47 is 1,081."
    assert val("what is 2+3").text == "2 plus 3 is 5."
    assert val("$5 + $3").text == "$5 plus $3 is $8."
    assert val("(3+4)*5").text == "(3 plus 4) times 5 is 35."
    assert val("square root of 144").text == "The square root of 144 is 12."


def test_big_results_are_grouped_or_given_in_scientific_form_and_never_cut():
    assert val("999999 * 999999").text.endswith("is 999,998,000,001.")
    big = val("2^200")
    assert big.approx and "x 10^60" in big.text and "6 significant digits" in big.text
    assert F(val("2^120").value) == 2 ** 120                        # 37 digits: still shown exactly


# --------------------------------------------------------------------------------------------------------------------- Indian numbers
INDIAN = [
    ("what is 1,00,000 plus 50,000", F(150000)),
    ("5 lakh plus 2 lakh", F(700000)),
    ("two lakh fifty thousand plus one lakh", F(350000)),
    ("2.5 lakh times 2", F(500000)),
    ("3 crore minus 50 lakh", F(25000000)),
    ("1 crore divided by 4", F(2500000)),
    ("10,00,000 + 1", F(1000001)),
    ("1,23,45,678 + 2", F(12345680)),
    ("5 lakh minus 1 lakh", F(400000)),
    ("2 lakh guna 3", F(600000)),
]


@pytest.mark.parametrize("text,want", INDIAN, ids=[t for t, _ in INDIAN])
def test_indian_number_words_and_grouping(text, want):
    got = val(text)
    assert F(got.value) == want


def test_indian_questions_are_answered_with_indian_grouping_and_a_lakh_gloss():
    got = val("5 lakh plus 2 lakh")
    assert got.text == "5,00,000 plus 2,00,000 is 7,00,000 (7 lakh)."
    assert "12,34,567" in val("1234567 in indian format").text


def test_western_questions_keep_western_grouping():
    assert "1,234,567" in val("1234567 with commas").text
    assert val("1000000 + 1").text == "1,000,000 plus 1 is 1,000,001."


# --------------------------------------------------------------------------------------------------------------------- percentages and money
PERCENT = [
    ("15% of 2400", F(15) * 2400 / 100),
    ("what is 15 percent of 2400", F(360)),
    ("what is 12.5% of 80", F(10)),
    ("7.5 percent of 200", F(15)),
    ("fifteen percent of 2400", F(360)),
    ("20 percent of 50", F(10)),
    ("33% of 100", F(33)),
    ("100% of 7", F(7)),
    ("0.5% of 1000", F(5)),
    ("200 plus 10%", F(220)),
    ("increase 200 by 10%", F(220)),
    ("500 minus 20%", F(400)),
    ("decrease 200 by 25%", F(150)),
    ("30 out of 240 as a percentage", F(30, 240) * 100),
    ("15 is what percent of 60", F(25)),
    ("what percent of 240 is 30", F(30, 240) * 100),
]


@pytest.mark.parametrize("text,want", PERCENT, ids=[t for t, _ in PERCENT])
def test_percentages(text, want):
    got = val(text)
    assert F(got.value) == want and not got.approx


def test_percentage_change_both_ways():
    assert F(val("percentage change from 80 to 100").value) == 25
    got = val("percentage change from 100 to 80")
    assert F(got.value) == 20 and "decrease" in got.text
    assert "no change" in val("percentage change from 5 to 5").text


MONEY = [                                            # (question, the number in the answer's `value`)
    ("20% discount on 1500", F(1200)),
    ("price after 30% discount on 1999", F(1999) * 70 / 100),
    ("1999 with 30% off", F(1999) * 70 / 100),
    ("30% off 1999", F(1999) * 70 / 100),
    ("add 18% gst to 2500", F(2500) * 118 / 100),
    ("18% gst on 2500", F(2500) * 118 / 100),
    ("gst on 2500 at 18%", F(2500) * 118 / 100),
    ("2500 plus 18% gst", F(2500) * 118 / 100),
    ("remove 18% gst from 2950", F(2950) / F(118, 100)),
    ("2950 including 18% gst", F(2950) / F(118, 100)),
    ("tip 15% on 840", F(840) * 15 / 100),
    ("15% tip on a bill of 1250", F(1250) * 15 / 100),
    ("split 1250 among 4", F(1250, 4)),
    ("split a bill of 1500 between 4 people with 10% tip", F(1500) * F(11, 10) / 4),
    ("split 1000 by 3", F(1000, 3)),
    ("1250 split 3 ways", F(1250, 3)),
    ("simple interest on 10000 at 8% for 3 years", F(10000) * 8 * 3 / 100),
    ("compound interest on 10000 at 8% for 3 years", F(10000) * F(108, 100) ** 3 - 10000),
    ("rs 5000 split among 3", F(5000, 3)),
    ("₹2400 plus 18% gst", F(2400) * 118 / 100),
]


@pytest.mark.parametrize("text,want", MONEY, ids=[t for t, _ in MONEY])
def test_money_questions_are_exact_to_the_paisa(text, want):
    got = val(text)
    cents = (want * 100)
    if cents.denominator == 1:
        assert not got.approx and F(got.value) == want, got.text
    else:
        assert got.approx and "rounded to 2 decimal places" in got.text, got.text
        assert Decimal(got.value) == Decimal(rounded(want, 2)).quantize(Decimal("0.01")), (got.value, rounded(want, 2))


def test_gst_on_an_amount_with_paise_rounds_and_says_so():
    got = val("add 5% vat to 99.99")
    assert got.approx and "rounded to 2 decimal places" in got.text
    assert "104.99" in got.text and "5.00" in got.text


def test_the_currency_symbol_follows_what_the_owner_said():
    assert val("what is 15% of rs 2400").text == "15% of ₹2,400 is ₹360."
    assert "₹" not in val("what is 15% of 2400").text
    assert val("$100 plus 8% tax").text.startswith("Tax at 8% on $100")


def _emi(principal, annual_percent, months):
    """A loan paid off month by month with Decimal: the EMI is the payment that leaves nothing owed (bisection), independent of the closed formula."""
    r = Decimal(annual_percent) / 1200
    lo, hi = Decimal(0), Decimal(principal)
    for _ in range(200):
        mid = (lo + hi) / 2
        bal = Decimal(principal)
        for _ in range(months):
            bal = bal * (1 + r) - mid
        lo, hi = (mid, hi) if bal > 0 else (lo, mid)
    return ((lo + hi) / 2).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


@pytest.mark.parametrize("text,principal,rate,months", [
    ("EMI of 5 lakh at 9% for 5 years", 500000, 9, 60),
    ("what is the EMI for a loan of 500000 at 9 percent for 60 months", 500000, 9, 60),
    ("emi on 10 lakh at 8.5% for 20 years", 1000000, Decimal("8.5"), 240),
    ("monthly payment on 250000 at 12% for 3 years", 250000, 12, 36),
    ("emi for 1200000 at 10.25% for 15 years", 1200000, Decimal("10.25"), 180),
])
def test_emi_matches_a_month_by_month_loan(text, principal, rate, months):
    got = val(text)
    assert got.approx and "a month" in got.text and "rounded to 2 decimal places" in got.text
    assert Decimal(got.value) == _emi(principal, rate, months), (got.value, _emi(principal, rate, months))
    assert "(1+r)^n" in got.text                                            # the formula is shown in the same line


def test_emi_with_no_interest_is_plain_division():
    got = val("emi for 100000 at 0% for 10 months")
    assert F(got.value) == 10000 and not got.approx and "P / n" in got.text


# --------------------------------------------------------------------------------------------------------------------- conversions
CONVERT = [                                          # (question, exact value in the target unit)
    ("convert 5 km to miles", F(5) * 1000 / F(1609344, 1000)),
    ("5 km in miles", F(5) * 1000 / F(1609344, 1000)),
    ("how many miles in 5 km", F(5) * 1000 / F(1609344, 1000)),
    ("how many cm in 2 inches", F(2) * F(254, 10000) * 100),
    ("convert 5 ft to cm", F(5) * F(3048, 10000) * 100),
    ("10 lb in kg", F(10) * F(45359237, 10 ** 8)),
    ("convert 10 kg to pounds", F(10) / F(45359237, 10 ** 8)),
    ("1 gallon in liters", F(3785411784, 10 ** 9)),
    ("2 liters in cups", F(2) / F(2365882365, 10 ** 10)),
    ("60 mph to kmph", F(60) * F(44704, 100000) * F(36, 10)),
    ("100 km/h in mph", F(100) * F(10, 36) / F(44704, 100000)),
    ("2 gb in mb", F(2048)),
    ("1 tb in gb", F(1024)),
    ("500 mb to gb", F(500, 1024)),
    ("1024 kb in mb", F(1)),
    ("8 bits in bytes", F(1)),
    ("90 minutes in hours", F(3, 2)),
    ("how many minutes in 3 hours", F(180)),
    ("how many days in 3 weeks", F(21)),
    ("how many seconds in a day", F(86400)),
    ("how many seconds in an hour", F(3600)),
    ("how many months in 2 years", F(24)),
    ("1 acre in sq ft", F(40468564224, 10 ** 7) / F(9290304, 10 ** 8)),
    ("1 hectare in sq m", F(10000)),
    ("convert 3 cups to ml", F(3) * F(2365882365, 10 ** 10) * 1000),
]


@pytest.mark.parametrize("text,want", CONVERT, ids=[t for t, _ in CONVERT])
def test_unit_conversions(text, want):
    got = val(text)
    d = want.denominator
    for p in (2, 5):
        while d % p == 0:
            d //= p
    terminating = d == 1
    exact_places = len(exact_text(want).partition(".")[2])
    if terminating and exact_places <= 12:
        assert F(got.value) == want and not got.approx, got.text
    else:
        assert got.approx and "rounded to" in got.text, got.text
        places = 4 if abs(want) >= F(1, 10) else 4 + len(str(int(1 / abs(want)))) - 1
        assert got.value == rounded(want, places), (got.value, rounded(want, places))


TEMPERATURE = [
    ("100 celsius to fahrenheit", F(212)),
    ("32 f to c", F(0)),
    ("0 c in k", F(27315, 100)),
    ("-40 c to f", F(-40)),
    ("300 kelvin to celsius", F(2685, 100)),
    ("what is 30 degrees celsius in fahrenheit", F(86)),
    ("98.6 °F in °C", F(37)),
    ("convert 37 c to f", F(986, 10)),
    ("72 f to c", F(72 - 32) * 5 / 9),
    ("20 °C to °F", F(68)),
    ("0 k in c", F(-27315, 100)),
]


@pytest.mark.parametrize("text,want", TEMPERATURE, ids=[t for t, _ in TEMPERATURE])
def test_temperatures(text, want):
    got = val(text)
    if want.denominator in (1, 2, 4, 5, 10, 20, 25, 50, 100):
        assert F(got.value) == want and not got.approx
    else:
        assert got.approx and got.value == rounded(want, 4)
    assert "°" in got.text or "K" in got.text


def test_conversion_sentence_shows_the_working_in_one_clause():
    assert val("5 km in miles").text == "5 km is about 3.1069 miles (1 mile = 1.609344 km; rounded to 4 decimal places)."
    assert val("how many cm in 2 inches").text == "2 inches is 5.08 cm (1 inch = 2.54 cm)."
    assert val("100 celsius to fahrenheit").text == "100 °C is 212 °F (°F = °C x 9/5 + 32)."
    assert "1,024 per step as Windows does" in val("2 gb in mb").text
    assert val("90 minutes in hours").text == "90 minutes is 1.5 hours (1 hour = 60 minutes)."


def test_big_magnitude_words_convert_between_lakh_crore_million():
    assert F(val("5 lakh in millions").value) == F(1, 2)
    assert F(val("1500000 in lakhs").value) == 15
    assert F(val("2 crore in lakhs").value) == 200
    assert F(val("50 thousand in lakh").value) == F(1, 2)
    assert F(val("1 million to crore").value) == F(1, 10)


# --------------------------------------------------------------------------------------------------------------------- number formatting
def test_number_formatting_questions():
    assert val("1234567 with commas").value == "1234567" and "1,234,567" in val("1234567 with commas").text
    assert "12,34,567" in val("1234567 in indian format").text
    assert val("1234567.891 with commas").text.endswith("1,234,567.891.")
    assert "one million two hundred thirty-four thousand five hundred sixty-seven" in val("1234567 in words").text
    assert "twelve lakh thirty-four thousand five hundred sixty-seven" in val("1234567 in indian words").text
    assert "one hundred" in val("100 in words").text
    assert val("round 3.14159 to 2 decimal places").value == "3.14"
    assert val("round 2.675 to 2 decimals").value == "2.68"                 # halves round up (a float would say 2.67)
    assert val("round 1234 to the nearest hundred").value == "1200"
    assert val("round 1250 to the nearest hundred").value == "1300"


# --------------------------------------------------------------------------------------------------------------------- dates and times (the PC clock)
def test_the_time_names_the_pc_time_zone():
    got = val("what time is it")
    assert got.text == "It is 3:30 PM (India Standard Time)."
    assert val("tell me the time").text == got.text and val("current time").text == got.text


def test_the_time_without_a_zone_is_plain():
    assert D.answer("what time is it", dt.datetime(2026, 10, 27, 0, 7)).text == "It is 12:07 AM."
    assert D.answer("what time is it", dt.datetime(2026, 10, 27, 12, 30)).text == "It is 12:30 PM."


def test_a_time_a_few_hours_from_now_and_before():
    assert val("what time will it be in 2 hours").value == "5:30 PM"
    assert val("what time will it be in 90 minutes").value == "5:00 PM"
    assert val("what time was it 3 hours ago").value == "12:30 PM"
    assert "tomorrow" in val("what time will it be in 10 hours").text and val("what time will it be in 10 hours").value == "1:30 AM"


DATES = [
    ("what is the date", "Today is Tuesday, 27 October 2026."),
    ("what's the date today", "Today is Tuesday, 27 October 2026."),
    ("today's date", "Today is Tuesday, 27 October 2026."),
    ("what day is it today", "Today is Tuesday, 27 October 2026."),
    ("what month is it", "It is October 2026."),
    ("what year is it", "It is 2026."),
    ("what is tomorrow's date", "Tomorrow is Wednesday, 28 October 2026."),
    ("what day is it tomorrow", "Tomorrow is Wednesday, 28 October 2026."),
    ("what was the date yesterday", "Yesterday was Monday, 26 October 2026."),
    ("day after tomorrow", "The day after tomorrow is Thursday, 29 October 2026."),
]


@pytest.mark.parametrize("text,want", DATES, ids=[t for t, _ in DATES])
def test_date_questions(text, want):
    assert val(text).text == want


OFFSETS = [                                          # (question, expected date computed with datetime / dateutil-free arithmetic)
    ("what is the date after 45 days", dt.date(2026, 10, 27) + dt.timedelta(days=45)),
    ("what is the date 45 days from now", dt.date(2026, 10, 27) + dt.timedelta(days=45)),
    ("45 days from today", dt.date(2026, 10, 27) + dt.timedelta(days=45)),
    ("date after 100 days", dt.date(2026, 10, 27) + dt.timedelta(days=100)),
    ("what will be the date after 3 weeks", dt.date(2026, 10, 27) + dt.timedelta(weeks=3)),
    ("what is the date in 2 months", dt.date(2026, 12, 27)),
    ("what date will it be in 1 year", dt.date(2027, 10, 27)),
    ("what was the date 30 days ago", dt.date(2026, 10, 27) - dt.timedelta(days=30)),
    ("30 days ago", dt.date(2026, 10, 27) - dt.timedelta(days=30)),
    ("what is the date after 365 days", dt.date(2026, 10, 27) + dt.timedelta(days=365)),
]


@pytest.mark.parametrize("text,want", OFFSETS, ids=[t for t, _ in OFFSETS])
def test_a_date_n_days_from_now(text, want):
    got = val(text)
    assert got.value == want.isoformat()
    assert f"{want.strftime('%A')}, {want.day} {want.strftime('%B %Y')}" in got.text


def test_adding_a_month_to_the_31st_says_what_it_did():
    got = D.answer("what is the date in 1 month", dt.datetime(2026, 1, 31, 9, 0))
    assert got.value == "2026-02-28" and "last day of that month" in got.text


UNTIL = [
    ("days until 25 December", (dt.date(2026, 12, 25) - dt.date(2026, 10, 27)).days),
    ("how many days to christmas", (dt.date(2026, 12, 25) - dt.date(2026, 10, 27)).days),
    ("how many days are left for new year", (dt.date(2027, 1, 1) - dt.date(2026, 10, 27)).days),
    ("days until 1 January 2027", (dt.date(2027, 1, 1) - dt.date(2026, 10, 27)).days),
    ("how many days left until 2026-12-31", (dt.date(2026, 12, 31) - dt.date(2026, 10, 27)).days),
    ("days until 31 october", 4),
    ("days since 1 January", (dt.date(2026, 10, 27) - dt.date(2026, 1, 1)).days),
    ("how many days since 15 august 2025", (dt.date(2026, 10, 27) - dt.date(2025, 8, 15)).days),
    ("days between 1 January 2026 and 15 March 2026", (dt.date(2026, 3, 15) - dt.date(2026, 1, 1)).days),
    ("how many days between 01/01/2026 and 15/03/2026", (dt.date(2026, 3, 15) - dt.date(2026, 1, 1)).days),
    ("days until 25 december 2027", (dt.date(2027, 12, 25) - dt.date(2026, 10, 27)).days),
    ("days until 28 february 2028", (dt.date(2028, 2, 28) - dt.date(2026, 10, 27)).days),
]


@pytest.mark.parametrize("text,days", UNTIL, ids=[t for t, _ in UNTIL])
def test_days_until_since_and_between(text, days):
    got = val(text)
    assert got.value == str(days), got.text


def test_a_date_that_is_today_and_weeks_form():
    assert "today" in val("days until 27 october").text
    assert val("weeks until 25 december").value == "8w3d"


def test_the_weekday_of_a_date_and_leap_years():
    assert val("what day is 15 august 2026").value == "Saturday"
    assert val("what day was 15 august 1947").value == "Friday"
    assert val("what day of the week is 25 december 2026").value == "Friday"
    assert val("is 2028 a leap year").value == "yes" and val("is 2026 a leap year").value == "no" and val("is 1900 a leap year").value == "no" and val("is 2000 a leap year").value == "yes"
    assert val("how many days in february 2028").value == "29" and val("how many days in february 2027").value == "28"
    assert val("how many days in february").text == "February 2026 has 28 days."          # no year said: this year, from the PC clock


# --------------------------------------------------------------------------------------------------------------------- Tenglish and Hinglish operator words
TENGLISH = [
    ("23 guna 47", F(1081)), ("23 guna 47 entha", F(1081)), ("23 guna 47 entha avutundi", F(1081)), ("23 ni 47 tho guna cheyyi", F(1081)),
    ("23 gunaa 47 kitna hota hai", F(1081)), ("23 ko 47 se guna karo", F(1081)), ("kitna hota hai 12 guna 12", F(144)), ("batao 6 guna 7", F(42)),
    ("cheppu 6 guna 7", F(42)), ("1200 ko 7 se bhaag karo", F(1200, 7)), ("1200 bhaag 7", F(1200, 7)), ("1200 bhag 7 batao", F(1200, 7)),
    ("1200 ni 7 tho bhaginchu", F(1200, 7)), ("45 aur 55 jodo", F(100)), ("45 jodo 55", F(100)), ("45 jod 55 kya hoga", F(100)),
    ("45 ni 55 tho kalupu", F(100)), ("45 kalupu 55 entha", F(100)), ("100 taggindi 40", F(60)), ("100 taggindi 40 entha", F(60)),
    ("100 nundi 40 tagginchu", F(60)), ("100 mein se 40 ghatao", F(60)), ("100 mein se 40 kam karo", F(60)), ("100 minus 40 kitna", F(60)),
    ("2 lakh aur 3 lakh jodo", F(500000)), ("2 lakh guna 3", F(600000)), ("5 into 6", F(30)),
]


@pytest.mark.parametrize("text,want", TENGLISH, ids=[t for t, _ in TENGLISH])
def test_tenglish_and_hinglish_operator_words(text, want):
    got = val(text)
    if got.approx:
        assert got.value == rounded(want, 4)
    else:
        assert F(got.value) == want


def test_hinglish_and_tenglish_percent_forms():
    assert F(val("2400 ka 15 percent kitna").value) == 360
    assert F(val("2400 lo 15 percent entha").value) == 360
    assert F(val("15 percent of 2400 entha").value) == 360


def test_the_answer_is_still_in_english_for_every_language():
    assert val("23 guna 47").text == "23 times 47 is 1,081."


# --------------------------------------------------------------------------------------------------------------------- random expressions against plain Python
def _random_expr(rng, depth=0):
    """(english text, python expression over Fractions) of the same random arithmetic."""
    if depth >= 3 or (depth > 0 and rng.random() < 0.3):
        n = rng.choice([rng.randint(1, 99), rng.randint(1, 9999), round(rng.uniform(0.1, 99), 1)])
        return str(n), f"F('{n}')"
    a, pa = _random_expr(rng, depth + 1)
    b, pb = _random_expr(rng, depth + 1)
    op, word, py = rng.choice([("+", "plus", "+"), ("-", "minus", "-"), ("*", "times", "*"), ("/", "divided by", "/")])
    if py == "/" and eval(pb, {"F": F}) == 0:
        py, op, word = "+", "+", "plus"
    sym = rng.choice([op, word]) if op != "*" else rng.choice(["*", "times", "x"])
    return f"({a} {sym} {b})", f"({pa} {py} {pb})"


def test_random_expressions_agree_with_python_fractions():
    rng = random.Random(75)
    checked = 0
    for _ in range(300):
        text, py = _random_expr(rng)
        want = eval(py, {"F": F})
        got = D.answer("what is " + text, NOW)
        assert got is not None, text
        if got.approx:
            if abs(want) >= F(1, 10) and abs(want) < 10 ** 40:
                assert got.value == rounded(want, 4), (text, got.text, want)          # the same ROUND_HALF_UP an independent Decimal gives
            else:
                assert abs(F(got.value) - want) <= abs(want) / 1000 + F(1, 10 ** 9), (text, got.text, want)
        else:
            assert F(got.value) == want, (text, got.text, want)
        checked += 1
    assert checked == 300


# --------------------------------------------------------------------------------------------------------------------- division by zero: said, not guessed
@pytest.mark.parametrize("text", ["5 / 0", "what is 10 divided by 0", "0 / 0", "10 mod 0", "1/(2-2)", "0^-1", "100 guna 0 bhaag 0", "5 divided by 0.0", "7 over 0"])
def test_division_by_zero_is_said(text):
    got = ask(text)
    if got is None:                                  # a form this module does not read (the last two) is simply not its question
        assert text in ("100 guna 0 bhaag 0",)
        return
    assert "zero" in got.text.lower() and got.value == ""


# --------------------------------------------------------------------------------------------------------------------- refusals: None, the normal path runs
REFUSED = [
    # not maths at all
    "what is the capital of France", "how are you today", "open notepad", "tell me a joke", "what is on my screen", "what is the weather in Paris", "who is the president",
    "what is love", "what is 5 apples plus 3", "what is your name", "", "   ", "hello",
    # unit-less / mismatched units / unknown units
    "5 km plus 3 km", "convert 5 to 7", "convert 5 km to kg", "how many days in a year", "how many days in 2 years", "1 kg in km", "5 apples to oranges", "100 usd to inr",
    "5 feet 3 inches in cm", "how many in 5 km", "what is 5 km",
    # the owner named the app or the screen: the app is used
    "calculate 12 times 7 in calculator", "open calculator and do 2+3", "open calculator", "show me 23 times 47 on screen", "use the calculator to work out 15 plus 27",
    "type 2+3 in notepad", "put 5 plus 3 in excel", "write 23 times 47 in a file", "calculate 5 plus 3 on the screen", "do 2+3 in calc", "open the calculator and add 4 and 5",
    "what is 5 plus 3 in the calculator app", "launch calculator and compute 10 times 10", "save 5 plus 5 to a file",
    # ambiguous
    "5 % 3", "what is 10%", "2(3+4)", "2 3", "x plus y minus z", "x + 2", "5 plus", "plus 5 times", "1,5 + 2", "12,34 + 1", "1.000,50 + 1", "10,20,30 + 1", "03/04/2026 days until",
    "days until 03/04/2026", "-2^2", "sqrt 16 plus 5", "square root of -4", "0^0", "1e5 + 1", "20% of 50 plus 5", "2 +* 3", "(2+3", "2+3)", "()", "5 ++ 3 **", "what time is it in london",
    "days until diwali", "what is the date of the meeting", "remind me in 5 minutes", "set a timer for 10 minutes", "call me in 2 hours",
    "it is 5 plus", "don't calculate 2+2", "do not add 2 and 2", "never multiply 3 by 4",
    # a bare string of digits and one sign is a phone number, a date or a fraction before it is a sum
    "555-1234", "12-05-2026", "3/4", "1/3", "10:30", "192.168.1.1", "3.11.2", "call 9876543210", "my pin is 1234", "room 101 plus 1", "I think 2+2 is 5", "5 plus 3 is 8, right?",
    # negative or huge
    "2**99999", "10**10**10", "9^9^9", "factorial of 5000", "factorial of 1001", "99999 ^ 99999", "2^1001", "(1000000^1000)^1000", "7 ^ 0.5 ^ 0.5 ^ 12345",
    "99999999999999999999999999999999 + 1", "1" * 40 + " + 1",
    # injection and syntax that is not maths
    "__import__('os').system('ls')", "__import__('os')", "import os", "exec('1+1')", "eval('2+2')", "open('/etc/passwd')", "2+2; rm -rf /", "2+2 && echo hi", "$(whoami)", "`id`",
    "{{7*7}}", "${7*7}", "<script>alert(1)</script>", "lambda: 1", "[1,2,3]", "'a' * 5", "print(2+2)", "1 if 1 else 2", "2 ** 3 ** 99999999", "chr(65)", "(1).real", "1 or 2", "not 5",
    "sqrt(__import__('math').pi)", "9" * 31, "0x1F + 1", "0b101 + 1", "1_000 + 1", "1e3 * 2", "2 ** 0.5 ** 0.5 ** 99999",
]


@pytest.mark.parametrize("text", REFUSED, ids=[t[:40] or "empty" for t in REFUSED])
def test_everything_not_plain_is_refused_silently(text):
    assert ask(text) is None, ask(text)


def test_very_long_input_is_refused_quickly():
    t0 = time.perf_counter()
    for text in ("1+" * 5000 + "1", "what is " + "9" * 5000, "a" * 100000, "(" * 3000 + "1" + ")" * 3000, "2" + "*2" * 400, " ".join(["12"] * 200), "5 plus " * 100 + "5"):
        assert ask(text) is None
    assert time.perf_counter() - t0 < 1.0


def test_deep_brackets_and_long_chains_are_capped_not_crashed():
    assert ask("(" * 40 + "1" + ")" * 40 + " + 1") is None
    assert val("(" * 10 + "1" + ")" * 10 + " + 1").value == "2"
    assert ask("+".join(["1"] * 130)) is None
    assert val("+".join(["1"] * 50)).value == "50"


UNICODE = [
    ("٢ + ٣", F(5)), ("२ + ३", F(5)), ("２＋３", F(5)), ("٢٣ × ٤٧", F(1081)), ("２３ × ４７", F(1081)), ("12 ÷ 4", F(3)), ("5 − 3", F(2)), ("5 – 3", F(2)),
    ("10 × 10", F(100)), ("5²", F(25)), ("2³", F(8)), ("√144", F(12)), ("√ 81", F(9)), ("1,00,000 + 1", F(100001)),
]


@pytest.mark.parametrize("text,want", UNICODE, ids=[t for t, _ in UNICODE])
def test_unicode_digits_and_operators_that_mean_what_they_look_like(text, want):
    assert F(val(text).value) == want


SPOOFED = [
    "2 + ٣٤5",                       # one number made of digits from two scripts
    "ԁ + 1", "20 рlus 3", "2 plυs 3", "5 tіmes 3",       # Cyrillic and Greek letters inside the English words, mixed digits
    "2​+​3", "2 +‮ 3", "2­+3", "﻿2+3", "2+3\x00", "2⁢+3",   # invisible and control characters
    "٢٣ guna ٤٧ plus १",                                               # fine digits but a Devanagari one in the same sum is accepted only as its own number
    "two ᱑", "𝟐 + 𝟑 ✓",
]


@pytest.mark.parametrize("text", SPOOFED, ids=[repr(t)[:30] for t in SPOOFED])
def test_lookalikes_and_invisible_characters_are_refused(text):
    got = ask(text)
    assert got is None or text in ("٢٣ guna ٤٧ plus १", "𝟐 + 𝟑 ✓"), got


def test_locale_separators():
    assert val("1,000 + 1").value == "1001"
    assert val("10,000,000 + 1").value == "10000001"
    assert val("10,00,000 + 1").value == "1000001"
    assert val("1,00,00,000 + 1").value == "10000001"
    assert val("1,234.5 + 1").value == "1235.5"
    assert val("0,5 + 1".replace(",", ".")).value == "1.5"
    for bad in ("1,5 + 2", "1.234,5 + 1", "12,34 + 1", "1,2,3 + 4", "1,000,00 + 1", "1 000 + 1"):
        assert ask(bad) is None, bad


# --------------------------------------------------------------------------------------------------------------------- guarantees: no network, no subprocess, never raises
def _boom(*a, **k):
    raise AssertionError("a direct answer must not touch the network or start a process")


def test_no_network_and_no_subprocess(monkeypatch):
    monkeypatch.setattr(socket, "socket", _boom)
    monkeypatch.setattr(socket, "create_connection", _boom)
    monkeypatch.setattr(socket, "getaddrinfo", _boom)
    monkeypatch.setattr(subprocess, "Popen", _boom)
    monkeypatch.setattr(subprocess, "run", _boom)
    import os
    monkeypatch.setattr(os, "system", _boom)
    for text, _ in EXACT[:25]:
        assert ask(text) is not None
    for text in ("what time is it", "days until 25 december", "convert 5 km to miles", "emi of 5 lakh at 9% for 5 years", "23 guna 47", "add 18% gst to 2500"):
        assert ask(text) is not None


def test_the_module_imports_only_the_standard_library_and_tenglish():
    import ast
    import pathlib
    tree = ast.parse(pathlib.Path(D.__file__).read_text(encoding="utf-8"))
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names |= {a.name.split(".")[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom):
            names.add(("." * node.level) + (node.module or ""))
    assert names <= {"__future__", "calendar", "datetime", "math", "re", "unicodedata", "dataclasses", "decimal", "fractions", "os", "sys", ".", ".tenglish"} | {"."}, names
    calls = {n.func.id for n in ast.walk(tree) if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}
    assert not calls & {"eval", "exec", "compile", "__import__", "open", "input", "getattr", "setattr"}, calls
    attrs = {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
    assert not attrs & {"system", "popen", "Popen", "urlopen", "connect", "literal_eval"}, attrs


def test_answer_never_raises_on_garbage():
    rng = random.Random(7)
    alphabet = "0123456789+-*/^()%!.,= abcxyz_$\"'\\\n\t√²٣ ԁ​"
    for _ in range(2000):
        text = "".join(rng.choice(alphabet) for _ in range(rng.randint(0, 60)))
        D.answer(text, NOW)
    for bad in (None, 5, b"2+2", ["2+2"], {}):
        assert D.answer(bad) is None


def test_the_switch(monkeypatch):
    assert D.enabled({}) and D.enabled({"JARVIS_DIRECT_ANSWERS": "1"})
    for off in ("0", "off", "false", "no"):
        assert not D.enabled({"JARVIS_DIRECT_ANSWERS": off})


def test_explicit_app_words():
    for t in ("in Calculator", "open calculator", "show me on screen", "use calc", "type it in notepad", "on the screen", "open the calculator app"):
        assert D.explicit_app(t), t
    for t in ("23 times 47", "what is 15% of 2400", "what time is it", "convert 5 km to miles"):
        assert not D.explicit_app(t), t


ANCHORED = [                                         # a date N units AFTER / BEFORE a date the owner NAMED (laptop 2026-10-28: "what is the date 45 days after 1 January 2027" went to the model)
    ("what is the date 45 days after 1 January 2027", dt.date(2027, 1, 1) + dt.timedelta(days=45)),
    ("30 days after 1 March 2027", dt.date(2027, 3, 31)),
    ("what date is 45 days after 1 january 2027", dt.date(2027, 2, 15)),
    ("60 days before 15 march 2027", dt.date(2027, 1, 14)),
    ("what is 100 days before 1 jan 2027", dt.date(2027, 1, 1) - dt.timedelta(days=100)),
    ("what date is 3 weeks after 5 november 2026", dt.date(2026, 11, 26)),
    ("date 90 days after 3 march 2026", dt.date(2026, 6, 1)),
    ("what is the date 2 months after 31 january 2027", dt.date(2027, 3, 31)),
    ("1 year after 29 february 2028", dt.date(2029, 2, 28)),
    ("10 days after tomorrow", dt.date(2026, 11, 7)),
]


@pytest.mark.parametrize("text,want", ANCHORED, ids=[t for t, _ in ANCHORED])
def test_a_date_n_units_after_or_before_a_named_date(text, want):
    got = val(text)
    assert got.value == want.isoformat()
    assert f"{want.strftime('%A')}, {want.day} {want.strftime('%B %Y')}" in got.text


@pytest.mark.parametrize("text", ["45 days after 1 march", "2 weeks after 12/05/2026", "open calculator and 45 days after 1 january 2027", "45 days after the meeting"])
def test_an_anchor_with_no_year_an_ambiguous_date_or_an_app_is_left_alone(text):
    assert D.answer(text, now=NOW) is None
