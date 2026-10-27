"""Predict a category or a number from a table with NVIDIA Kumo Tabular, only where it beats a plain baseline (D66). Local only.

    python jarvis/tools/tabular_predict.py check --csv rows.csv --target total_ms --name route-total   held-out comparison, writes the gate
    python jarvis/tools/tabular_predict.py predict --csv rows.csv --query q.csv --target total_ms --name route-total
    python jarvis/tools/tabular_predict.py route-forecast [--since 2d]                                  gateway timing log -> expected total_ms per provider

What it is: a pretrained in-context model (28M to 215M parameters, weights OpenMDW-1.1, code Apache-2.0). It gets rows that have the answer and rows
that do not, and predicts in one pass; there is no training step. NVIDIA's top ranking is its own test, so JARVIS never trusts it blindly:

  * `check` splits the rows, scores Kumo and a plain baseline (nearest neighbours, standard library) on the held-out part and writes
    <data>/state/tabular_gate.json {name: {"use": "kumo"|"baseline", ...}}. Kumo is used for that table name only if it wins by a margin
    (accuracy +0.02, or mean absolute error 5% lower). Anything else, an error included, falls back to the baseline.
  * `predict` reads that gate. No gate for the name = baseline.
  * Numbers and categories only: a column of free text is refused (no words of the owner go through it). At most 2000 context rows and 64 columns.
  * Local only: no network call is made here. The weights are fetched ONCE by the installer step (owner-approved) into the Hugging Face cache of the
    tabular venv; at run time HF_HUB_OFFLINE=1 is set. Not installed = the baseline answers and the result says so.
  * Not an interactive part: it runs from the nightly self-test or by hand, on the CPU, at BelowNormal priority (see docs). It never sits in the chat path.

The Kumo call follows the library's documented in-context pattern (TabICLv2 page of nvidia.github.io/structured-data-models/icl.html; KumoTabular is
documented with the same unified interface, constructor KumoTabular(task=None, size='large', pretrained=True, device=None)). The exact KumoTabular
call has NOT been run in this session (no GPU, no weights): `_kumo_predict` is the only place to adjust after the first run on the laptop.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import os
import random
import sys
import time
from pathlib import Path

MAX_CONTEXT_ROWS = 2000
MAX_COLUMNS = 64
MIN_ROWS = 30                        # fewer rows than this: nothing to learn from, the gate stays on the baseline
CLASS_MARGIN = 0.02                  # accuracy points Kumo must win by
REGRESSION_MARGIN = 0.05             # share by which its mean absolute error must be lower
KNN_K = 5
KUMO_SIZE = os.environ.get("JARVIS_TABULAR_SIZE", "small")       # 'small' (~28M) | 'medium' | 'large' (~215M)


def data_dir() -> Path:
    base = os.environ.get("JARVIS_DATA_DIR", "").strip() or (r"D:\jarvis\data" if os.name == "nt" else str(Path.home() / ".jarvis" / "data"))
    return Path(base)


def gate_path() -> Path:
    return data_dir() / "state" / "tabular_gate.json"


# ---------- table checks ----------

def _is_number(v) -> bool:
    if isinstance(v, bool):
        return False
    if isinstance(v, (int, float)):
        return math.isfinite(v)
    try:
        return math.isfinite(float(str(v).strip()))
    except (TypeError, ValueError):
        return False


def _to_number(v) -> float:
    return float(str(v).strip()) if not isinstance(v, (int, float)) else float(v)


def column_kinds(rows: list[dict], columns: list[str]) -> dict[str, str]:
    """'numeric' when every non-empty value is a number, else 'category'. A category column with more than 50 distinct values per 100 rows,
    or a value longer than 40 characters, looks like free text and is refused (ValueError): these tables hold numbers and short labels only."""
    kinds: dict[str, str] = {}
    for c in columns:
        vals = [r.get(c) for r in rows if r.get(c) not in (None, "")]
        if vals and all(_is_number(v) for v in vals):
            kinds[c] = "numeric"
            continue
        if any(len(str(v)) > 40 for v in vals) or (len(rows) >= 30 and len({str(v) for v in vals}) > 0.5 * len(rows)):
            raise ValueError(f"column {c!r} looks like free text; only numbers and short labels are accepted")
        kinds[c] = "category"
    return kinds


def check_shape(rows: list[dict], target: str) -> list[str]:
    if not rows:
        raise ValueError("no rows")
    columns = list(rows[0].keys())
    if target not in columns:
        raise ValueError(f"target column {target!r} is not in the table")
    if len(columns) > MAX_COLUMNS:
        raise ValueError(f"{len(columns)} columns; at most {MAX_COLUMNS}")
    return columns


# ---------- the plain baseline: k nearest neighbours, standard library ----------

class Baseline:
    """Mixed distance: numbers scaled by their range, labels 0 if equal else 1. Classification = majority of the k nearest, regression = their mean.
    With fewer rows than k it is the majority class / the median."""

    def __init__(self, context: list[dict], target: str, kinds: dict[str, str]):
        self.target = target
        self.features = [c for c in kinds if c != target]
        self.kinds = kinds
        self.regression = kinds[target] == "numeric"
        self.rows = [r for r in context if r.get(target) not in (None, "")]
        self.span: dict[str, float] = {}
        for c in self.features:
            if kinds[c] == "numeric":
                vals = [_to_number(r[c]) for r in self.rows if _is_number(r.get(c))]
                self.span[c] = (max(vals) - min(vals)) or 1.0 if vals else 1.0
        ys = [r[target] for r in self.rows]
        if self.regression:
            ys = sorted(_to_number(y) for y in ys)
            self.fallback = ys[len(ys) // 2] if ys else 0.0
        else:
            counts: dict[str, int] = {}
            for y in ys:
                counts[str(y)] = counts.get(str(y), 0) + 1
            self.fallback = max(counts, key=lambda k: (counts[k], k)) if counts else ""

    def _dist(self, a: dict, b: dict) -> float:
        d = 0.0
        for c in self.features:
            x, y = a.get(c), b.get(c)
            if x in (None, "") or y in (None, ""):
                d += 1.0
            elif self.kinds[c] == "numeric":
                d += min(1.0, abs(_to_number(x) - _to_number(y)) / self.span.get(c, 1.0)) if _is_number(x) and _is_number(y) else 1.0
            else:
                d += 0.0 if str(x) == str(y) else 1.0
        return d

    def predict_one(self, row: dict):
        if len(self.rows) < KNN_K:
            return self.fallback
        near = sorted(self.rows, key=lambda r: self._dist(row, r))[:KNN_K]
        if self.regression:
            return sum(_to_number(r[self.target]) for r in near) / len(near)
        votes: dict[str, int] = {}
        for r in near:
            votes[str(r[self.target])] = votes.get(str(r[self.target]), 0) + 1
        return max(votes, key=lambda k: (votes[k], k))

    def predict(self, query: list[dict]) -> list:
        return [self.predict_one(q) for q in query]


# ---------- Kumo Tabular (lazy, optional, local) ----------

def kumo_available() -> tuple[bool, str]:
    try:
        import sdm  # noqa: F401  (structured-data-models)
        import pandas  # noqa: F401
        import torch  # noqa: F401
    except Exception as e:          # ImportError, or a broken CUDA/DLL load
        return False, f"structured-data-models is not installed here ({type(e).__name__})"
    return True, ""


def _kumo_predict(context: list[dict], query: list[dict], target: str, kinds: dict[str, str]) -> list:
    """One in-context call (context rows with the answer + query rows without it) -> one prediction per query row.
    Adjust here after the first run on the laptop: this is the one function that touches the library."""
    os.environ.setdefault("HF_HUB_OFFLINE", "1")        # weights come from the cache the installer filled; no network at run time
    import pandas as pd
    import sdm
    device = os.environ.get("JARVIS_TABULAR_DEVICE", "cpu")
    cols = list(kinds)
    frame = pd.DataFrame([{c: (_to_number(r[c]) if kinds[c] == "numeric" and _is_number(r.get(c)) else r.get(c)) for c in cols} for r in context + query])
    for c in cols:
        frame[c] = pd.to_numeric(frame[c], errors="coerce") if kinds[c] == "numeric" else frame[c].astype("string")
    regression = kinds[target] == "numeric"
    stypes = sdm.infer_stypes(frame, overrides={target: "numerical" if regression else "categorical"})
    n = len(context)
    # the library wants the unknown answers present but unused: the query part keeps its column, the call drops it
    table = sdm.TableTensor.from_pandas(df=frame, stypes=stypes, device=device)
    model = sdm.models.KumoTabular(size=KUMO_SIZE, device=device)
    out = model(x_context=table[:n].drop_columns(target), y_context=table[:n, target], x_query=table[n:].drop_columns(target))
    if regression:
        values = out.numerical.mean(dim=-1) if hasattr(out, "numerical") else out
        return [float(v) for v in values.reshape(-1).tolist()]
    probs = out.numerical                                   # one column per category
    labels = sorted({str(r[target]) for r in context if r.get(target) not in (None, "")})     # assumed order of the category columns
    return [labels[int(i)] for i in probs.argmax(dim=-1).reshape(-1).tolist()]


# ---------- scoring, gate, predict ----------

def score(truth: list, predicted: list, regression: bool) -> float:
    """Higher is better: accuracy, or minus the mean absolute error."""
    if not truth:
        return 0.0
    if regression:
        return -sum(abs(_to_number(t) - float(p)) for t, p in zip(truth, predicted)) / len(truth)
    return sum(1 for t, p in zip(truth, predicted) if str(t) == str(p)) / len(truth)


def check(rows: list[dict], target: str, name: str, seed: int = 7, test_frac: float = 0.3, kumo=None) -> dict:
    """Held-out comparison. `kumo` (tests) replaces the library call: kumo(context, query, target, kinds) -> predictions."""
    columns = check_shape(rows, target)
    kinds = column_kinds(rows, columns)
    labelled = [r for r in rows if r.get(target) not in (None, "")]
    result = {"name": name, "target": target, "rows": len(labelled), "t": time.time(), "use": "baseline", "kumo_size": KUMO_SIZE}
    if len(labelled) < MIN_ROWS:
        result["why"] = f"only {len(labelled)} labelled rows (need {MIN_ROWS}): the baseline stays"
        return _save(name, result)
    rnd = random.Random(seed)
    shuffled = labelled[:]
    rnd.shuffle(shuffled)
    cut = max(1, int(len(shuffled) * (1 - test_frac)))
    train, test = shuffled[:cut][:MAX_CONTEXT_ROWS], shuffled[cut:]
    regression = kinds[target] == "numeric"
    truth = [r[target] for r in test]
    blind = [{k: v for k, v in r.items() if k != target} for r in test]               # the held-out answers are never shown to either model
    base = score(truth, Baseline(train, target, kinds).predict(blind), regression)
    result.update(task="regression" if regression else "classification", baseline=round(base, 4), held_out=len(test))
    runner = kumo
    if runner is None:
        ok, why = kumo_available()
        if not ok:
            result["why"] = why + ": the baseline stays"
            return _save(name, result)
        runner = _kumo_predict
    try:
        got = runner(train, blind, target, kinds)
        if len(got) != len(test):
            raise ValueError(f"{len(got)} predictions for {len(test)} rows")
        kum = score(truth, got, regression)
    except Exception as e:
        result["why"] = f"Kumo failed ({type(e).__name__}: {str(e)[:160]}): the baseline stays"
        return _save(name, result)
    result["kumo"] = round(kum, 4)
    win = (kum - base) >= (REGRESSION_MARGIN * abs(base) if regression else CLASS_MARGIN)
    result["use"] = "kumo" if win else "baseline"
    result["why"] = "Kumo beat the baseline on held-out rows by the margin" if win else "Kumo did not beat the baseline by the margin: the baseline stays"
    return _save(name, result)


def _save(name: str, result: dict) -> dict:
    p = gate_path()
    try:
        gate = json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
        gate = gate if isinstance(gate, dict) else {}
    except (OSError, ValueError):
        gate = {}
    gate[name] = result
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(gate, indent=1), encoding="utf-8")
        os.replace(tmp, p)
    except OSError:
        pass                                                  # the result is still returned; a missing gate means the baseline next time
    return result


def predict(context: list[dict], query: list[dict], target: str, name: str, kumo=None) -> dict:
    columns = check_shape(context, target)
    kinds = column_kinds(context, columns)
    context = [r for r in context if r.get(target) not in (None, "")][-MAX_CONTEXT_ROWS:]
    try:
        gate = json.loads(gate_path().read_text(encoding="utf-8")).get(name, {})
    except (OSError, ValueError, AttributeError):
        gate = {}
    used, note = "baseline", gate.get("why", "no held-out check for this table yet: the baseline answers")
    preds = None
    if gate.get("use") == "kumo":
        runner = kumo or (_kumo_predict if kumo_available()[0] else None)
        if runner is not None:
            try:
                preds = runner(context, query, target, kinds)
                if len(preds) != len(query):
                    raise ValueError("wrong number of predictions")
                used = "kumo"
            except Exception as e:
                preds, note = None, f"Kumo failed ({type(e).__name__}): the baseline answered"
        else:
            note = "Kumo is not installed here: the baseline answered"
    if preds is None:
        preds = Baseline(context, target, kinds).predict(query)
    return {"used": used, "predictions": preds, "note": note}


# ---------- route forecast from the content-free gateway log ----------

def route_rows(log_rows: list[dict]) -> list[dict]:
    """Gateway lines (src 'gateway') -> table rows: provider, after, tools (0/1), req_kb (rounded), total_ms. No text; numbers and provider names."""
    out = []
    for r in log_rows:
        if r.get("src") != "gateway" or not isinstance(r.get("total_ms"), (int, float)) or not r.get("provider"):
            continue
        out.append({"provider": str(r["provider"]), "after": str(r.get("after") or "user"), "tools": 1 if r.get("tools") else 0,
                    "req_kb": round((r.get("req_bytes") or 0) / 1024), "total_ms": round(r["total_ms"])})
    return out


def route_forecast(since: str | None = None, log: Path | None = None, kumo=None) -> dict:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import latency_report as L
    rows = route_rows(L.read_lines(log or L.default_log(), L.parse_since(since)))
    if len(rows) < MIN_ROWS:
        return {"ok": False, "why": f"only {len(rows)} gateway lines (need {MIN_ROWS}); nothing to forecast yet"}
    verdict = check(rows, "total_ms", "route-total", kumo=kumo)
    providers = sorted({r["provider"] for r in rows})
    typical = {"after": "user", "tools": 1, "req_kb": sorted(r["req_kb"] for r in rows)[len(rows) // 2]}
    query = [dict(typical, provider=p) for p in providers]
    got = predict(rows, query, "total_ms", "route-total", kumo=kumo)
    return {"ok": True, "used": got["used"], "expected_total_ms": {p: round(float(v)) for p, v in zip(providers, got["predictions"])},
            "gate": {k: verdict.get(k) for k in ("use", "baseline", "kumo", "why")}, "note": "advisory: nothing in the gateway reads this yet"}


# ---------- command line ----------

def _read_csv(path: str) -> list[dict]:
    with open(path, newline="", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("check")
    c.add_argument("--csv", required=True)
    c.add_argument("--target", required=True)
    c.add_argument("--name", required=True)
    p = sub.add_parser("predict")
    p.add_argument("--csv", required=True)
    p.add_argument("--query", required=True)
    p.add_argument("--target", required=True)
    p.add_argument("--name", required=True)
    f = sub.add_parser("route-forecast")
    f.add_argument("--since", default="2d")
    a = ap.parse_args(argv)
    try:
        if a.cmd == "check":
            out = check(_read_csv(a.csv), a.target, a.name)
        elif a.cmd == "predict":
            out = predict(_read_csv(a.csv), _read_csv(a.query), a.target, a.name)
        else:
            out = route_forecast(a.since)
    except (ValueError, OSError) as e:
        print(json.dumps({"ok": False, "why": str(e)}))
        return 2
    print(json.dumps(out, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
