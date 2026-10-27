"""D66: Kumo Tabular is used only where it beats the plain baseline on held-out rows; every failure falls back to the baseline."""
import json
import random
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import tabular_predict as T  # noqa: E402


@pytest.fixture(autouse=True)
def data(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path))
    return tmp_path


def class_rows(n=120, seed=1):
    r = random.Random(seed)
    rows = []
    for _ in range(n):
        x = r.uniform(0, 10)
        rows.append({"x": round(x, 2), "kind": r.choice(["a", "b"]), "label": "hi" if x > 5 else "lo"})
    return rows


def reg_rows(n=120, seed=2):
    r = random.Random(seed)
    return [{"p": r.choice(["g", "h"]), "kb": k, "ms": 1000 + 10 * k + (500 if (p := 0) else 0)} for k in [r.randint(1, 50) for _ in range(n)]]


def test_baseline_learns_a_simple_rule():
    rows = class_rows()
    kinds = T.column_kinds(rows, list(rows[0]))
    b = T.Baseline(rows[:90], "label", kinds)
    got = b.predict([{k: v for k, v in r.items() if k != "label"} for r in rows[90:]])
    assert T.score([r["label"] for r in rows[90:]], got, False) > 0.85


def test_baseline_regression_and_tiny_context():
    rows = reg_rows()
    kinds = T.column_kinds(rows, list(rows[0]))
    b = T.Baseline(rows[:90], "ms", kinds)
    assert abs(b.predict_one({"p": "g", "kb": 20}) - 1200) < 150
    tiny = T.Baseline(rows[:2], "ms", kinds)
    assert tiny.predict_one({"p": "g", "kb": 1}) in (rows[0]["ms"], rows[1]["ms"])


def test_free_text_columns_are_refused():
    rows = [{"note": f"please open my bank statement number {i} and read it", "y": 1} for i in range(40)]
    with pytest.raises(ValueError, match="free text"):
        T.column_kinds(rows, ["note", "y"])
    with pytest.raises(ValueError):
        T.check_shape(rows, "missing")
    with pytest.raises(ValueError):
        T.check_shape([{f"c{i}": 1 for i in range(65)}], "c0")


def test_too_few_rows_keeps_the_baseline():
    out = T.check(class_rows(10), "label", "tiny", kumo=lambda *a: (_ for _ in ()).throw(AssertionError("must not run")))
    assert out["use"] == "baseline" and "only 10" in out["why"]


def test_kumo_that_wins_is_chosen_and_the_held_out_answers_stay_hidden():
    seen = {}

    rows = class_rows(200)
    for r in rows[::7]:                      # noisy labels: the baseline smooths them away, the oracle below knows them
        r["label"] = "lo" if r["label"] == "hi" else "hi"
    truth = {(r["x"], r["kind"]): r["label"] for r in rows}

    def perfect(context, query, target, kinds):
        seen["query_cols"] = set(query[0])
        return [truth[(q["x"], q["kind"])] for q in query]

    out = T.check(rows, "label", "cls", kumo=perfect)
    assert "label" not in seen["query_cols"]
    assert out["kumo"] > out["baseline"] and out["use"] == "kumo"
    gate = json.loads(T.gate_path().read_text())
    assert gate["cls"]["use"] == "kumo"


def test_kumo_that_does_not_beat_the_margin_loses():
    rows = class_rows(200)
    def same_as_baseline(context, query, target, kinds):
        return T.Baseline(context, target, kinds).predict(query)
    out = T.check(rows, "label", "same", kumo=same_as_baseline)
    assert out["use"] == "baseline"


def test_kumo_error_and_wrong_count_fall_back():
    rows = class_rows(100)
    def boom(*a):
        raise RuntimeError("cuda out of memory")
    assert T.check(rows, "label", "e1", kumo=boom)["use"] == "baseline"
    assert "Kumo failed" in T.check(rows, "label", "e1", kumo=boom)["why"]
    assert T.check(rows, "label", "e2", kumo=lambda c, q, t, k: ["lo"])["use"] == "baseline"


def test_regression_margin_is_relative_to_the_error():
    rows = reg_rows(150)
    kinds = T.column_kinds(rows, list(rows[0]))
    exact = lambda c, q, t, k: [1000 + 10 * x["kb"] for x in q]
    out = T.check(rows, "ms", "reg", kumo=exact)
    assert out["task"] == "regression" and out["kumo"] >= out["baseline"]
    assert out["use"] in ("kumo", "baseline")
    assert kinds["ms"] == "numeric"


def test_not_installed_means_baseline(monkeypatch):
    monkeypatch.setattr(T, "kumo_available", lambda: (False, "structured-data-models is not installed here (ImportError)"))
    out = T.check(class_rows(100), "label", "ni")
    assert out["use"] == "baseline" and "not installed" in out["why"]


def test_predict_follows_the_gate():
    rows = class_rows(100)
    q = [{"x": 9.0, "kind": "a"}, {"x": 1.0, "kind": "b"}]
    no_gate = T.predict(rows, q, "label", "nogate", kumo=lambda *a: ["WRONG"] * 2)
    assert no_gate["used"] == "baseline" and no_gate["predictions"] == ["hi", "lo"]
    T._save("g", {"use": "kumo"})
    yes = T.predict(rows, q, "label", "g", kumo=lambda *a: ["K1", "K2"])
    assert yes["used"] == "kumo" and yes["predictions"] == ["K1", "K2"]
    broke = T.predict(rows, q, "label", "g", kumo=lambda *a: (_ for _ in ()).throw(RuntimeError("x")))
    assert broke["used"] == "baseline" and "failed" in broke["note"]


def test_context_is_capped_and_unlabelled_rows_dropped():
    rows = [{"x": i, "y": "a" if i % 2 else "b"} for i in range(T.MAX_CONTEXT_ROWS + 50)] + [{"x": 0, "y": ""}]
    T._save("cap", {"use": "kumo"})
    got = {}
    def spy(context, query, target, kinds):
        got["n"] = len(context)
        return ["a"] * len(query)
    T.predict(rows, [{"x": 3}], "y", "cap", kumo=spy)
    assert got["n"] == T.MAX_CONTEXT_ROWS


def test_route_forecast_reads_only_content_free_gateway_lines(tmp_path):
    log = tmp_path / "latency.jsonl"
    lines = []
    for i in range(80):
        prov = "dahl" if i % 2 else "groq"
        lines.append({"t": 1000 + i, "src": "gateway", "provider": prov, "after": "user", "tools": 1, "req_bytes": 8000 + i * 10,
                      "total_ms": 8000 if prov == "dahl" else 2000})
    lines.append({"t": 5000, "src": "face", "phase": "sent", "turn": 1})
    log.write_text("\n".join(json.dumps(x) for x in lines), encoding="utf-8")
    rows = T.route_rows([json.loads(x) for x in log.read_text().splitlines()])
    assert len(rows) == 80 and set(rows[0]) == {"provider", "after", "tools", "req_kb", "total_ms"}
    out = T.route_forecast(log=log, kumo=lambda c, q, t, k: [8000.0 if x["provider"] == "dahl" else 2000.0 for x in q])
    assert out["ok"] and out["expected_total_ms"] == {"dahl": 8000, "groq": 2000} or out["used"] == "baseline"
    assert abs(out["expected_total_ms"]["dahl"] - 8000) < 1 and abs(out["expected_total_ms"]["groq"] - 2000) < 1
    short = T.route_forecast(log=tmp_path / "none.jsonl")
    assert not short["ok"]


def test_cli_check(tmp_path, capsys):
    import csv
    p = tmp_path / "t.csv"
    rows = class_rows(80)
    with open(p, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0])); w.writeheader(); w.writerows(rows)
    assert T.main(["check", "--csv", str(p), "--target", "label", "--name", "cli"]) == 0
    assert json.loads(capsys.readouterr().out)["use"] == "baseline"
    assert T.main(["check", "--csv", str(p), "--target", "nope", "--name", "cli"]) == 2
