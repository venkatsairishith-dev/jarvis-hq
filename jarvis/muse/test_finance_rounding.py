"""Review addition: totals are reported to the cent, not as float noise."""
from jarvis.muse.finance import parse_csv, spent_on, summarize


def rows(n):
    body = "date,description,amount,currency,category\n" + "".join(f"2026-09-0{i % 9 + 1},Swiggy,0.10,INR,Food\n" for i in range(n))
    return parse_csv(body)


def test_ten_rows_of_ten_paise_total_exactly_one_rupee():
    r = rows(10)
    assert sum([0.1] * 10) != 1.0                                # plain float addition drifts (0.9999999999999999); the report must not
    assert summarize(r)["INR"]["Food"]["expense"] == 1.0
    assert spent_on(r, ["swiggy"], "2026-09") == {"INR": 1.0}
