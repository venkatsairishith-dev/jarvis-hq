"""Tests for jarvis.muse.finance."""
import pytest

from jarvis.muse.finance import (BadCsv, FileTooLarge, is_expense, is_income,
                                 parse_amount, parse_csv, spent_on, summarize)

CSV = "Date,Description,Amount,Currency,Category\n"


def test_size_cap_boundary():
    line = "2024-01-01," + "x" * 88 + ",1,INR,a\n"  # 100 bytes
    total = 500 * 1024
    n, rem = divmod(total - len(CSV), len(line))
    ok = CSV + line * n + "2024-01-01," + "x" * (rem - len("2024-01-01,,1,INR,a\n")) + ",1,INR,a\n"
    assert len(ok.encode()) == 500 * 1024
    parse_csv(ok.encode())
    with pytest.raises(FileTooLarge):
        parse_csv((ok + "x").encode())


@pytest.mark.parametrize("d", [",", ";", "\t"])
def test_delimiters(d):
    text = d.join(["Transaction Date", "Narration", "Value"]) + "\n" + d.join(["2024-01-05", "Swiggy", "10.5"]) + "\n"
    r = parse_csv(text)[0]
    assert (r["date"], r["description"], r["amount"]) == ("2024-01-05", "Swiggy", 10.5)


@pytest.mark.parametrize("s,v,c", [
    ("1,234.50", 1234.5, ""), ("1.234,50", 1234.5, ""), ("(45.00)", -45.0, ""),
    ("Rs 100", 100.0, "INR"), ("INR 1,23,456.00", 123456.0, "INR"), ("₹99", 99.0, "INR"),
    ("$5.25", 5.25, "USD"), ("EUR 7,50", 7.5, "EUR"), ("€1.234,00", 1234.0, "EUR"),
    ("-20", -20.0, ""), ("1,23,456", 123456.0, ""),
])
def test_amounts(s, v, c):
    assert parse_amount(s) == (v, c)


def test_sign_convention():
    rows = parse_csv(CSV + "2024-01-01,a,10,INR,x\n2024-01-02,b,-5,INR,y\n")
    assert is_expense(rows[0]) and not is_income(rows[0])
    assert is_income(rows[1]) and not is_expense(rows[1])


def test_debit_credit_columns():
    rows = parse_csv("Date,Payee,Debit,Credit\n2024-01-01,a,50,\n2024-01-02,b,,70\n")
    assert rows[0]["amount"] == 50 and is_expense(rows[0])
    assert rows[1]["amount"] == -70 and is_income(rows[1])


def test_no_cross_currency():
    rows = parse_csv(CSV + "2024-01-01,a,10,INR,f\n2024-01-02,b,20,USD,f\n2024-01-03,c,5,,f\n")
    s = summarize(rows, by="category")
    assert set(s) == {"INR", "USD", "UNKNOWN"}
    assert s["INR"]["f"]["expense"] == 10 and s["USD"]["f"]["expense"] == 20
    assert set(summarize(rows, by="currency", default_currency="GBP")) == {"INR", "USD", "GBP"}
    assert summarize(rows, by="month", period="2024-02") == {}


def test_spent_on_month_filter_and_income():
    rows = parse_csv(CSV + "2024-01-01,Swiggy order,100,INR,\n2024-02-01,Zomato,50,INR,\n"
                     "2024-01-05,swiggy refund,-30,INR,\n2024-01-06,Cafe,20,INR,Food\n2024-01-07,Rent,9,USD,\n")
    assert spent_on(rows, ["food", "swiggy", "zomato"], "2024-01") == {"INR": 120.0}


def test_bad_csv_does_not_echo():
    with pytest.raises(BadCsv) as e:
        parse_csv(CSV + "2024-01-01,SECRETDESC,notanumber,INR,x\n")
    assert "row 1" in str(e.value) and "SECRETDESC" not in str(e.value) and "notanumber" not in str(e.value)
    with pytest.raises(BadCsv) as e:
        parse_csv("Date,Description\n2024-01-01,SECRETDESC\n")
    assert "SECRETDESC" not in str(e.value)
