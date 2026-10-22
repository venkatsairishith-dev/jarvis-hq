"""Finance CSV analysis (pure Python, standard library only).

Sign convention (ported from the source product): a POSITIVE amount is an
EXPENSE and a NEGATIVE amount is INCOME. Currencies are never converted;
all totals are reported per currency. Raw descriptions are never logged and
errors never echo file contents.

Provenance: behaviour ported from openmuse (github.com/CopilotKit/openmuse, MIT, Copyright (c) 2026 OpenMuse contributors), commit 1ac68f3, apps/server/src/engine/finance.ts; reimplemented in Python.
"""
from __future__ import annotations

import csv
import io
import re
from datetime import datetime
from typing import Any

MAX_BYTES = 500 * 1024

Row = dict[str, Any]


class FileTooLarge(ValueError):
    """Input exceeds the 500 KB cap."""


class BadCsv(ValueError):
    """CSV could not be parsed; message never contains file text."""


_SYNONYMS = {
    "date": ["date", "transaction date", "posted", "posted date", "txn date", "value date"],
    "description": ["description", "details", "narration", "memo", "payee"],
    "amount": ["amount", "value"],
    "debit": ["debit", "withdrawal", "debit amount"],
    "credit": ["credit", "deposit", "credit amount"],
    "currency": ["currency", "ccy"],
    "category": ["category"],
}

_CURRENCY_TOKENS = [
    ("INR", "INR"), ("EUR", "EUR"), ("USD", "USD"), ("RS", "INR"),
    ("₹", "INR"), ("€", "EUR"), ("$", "USD"),
]

_DATE_FORMATS = ["%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%Y/%m/%d", "%d.%m.%Y",
                 "%d %b %Y", "%d-%b-%Y", "%d/%m/%y", "%d-%m-%y"]


def _norm(name: str) -> str:
    return re.sub(r"\s+", " ", name.strip().lstrip("﻿").lower())


def _parse_date(s: str) -> str:
    s = s.strip()
    if re.match(r"^\d{4}-\d{2}-\d{2}T", s):
        s = s.split("T")[0]
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(s, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return ""


def parse_amount(text: str) -> tuple[float, str]:
    """Parse an amount string into (value, currency-or-""). Raises ValueError."""
    s = text.strip()
    if not s:
        raise ValueError("empty")
    currency = ""
    negative = False
    if s.startswith("(") and s.endswith(")"):
        negative, s = True, s[1:-1].strip()
    up = s.upper()
    for token, code in _CURRENCY_TOKENS:
        t = token.upper()
        if up.startswith(t):
            currency, s = code, s[len(token):].strip()
            break
        if up.endswith(t):
            currency, s = code, s[: len(s) - len(token)].strip()
            break
    if s.startswith("-"):
        negative, s = not negative, s[1:].strip()
    elif s.startswith("+"):
        s = s[1:].strip()
    if s.endswith("-"):
        negative, s = not negative, s[:-1].strip()
    s = s.replace(" ", "")
    if not re.fullmatch(r"[\d.,]+", s) or not re.search(r"\d", s):
        raise ValueError("bad")
    if "," in s and "." in s:
        if s.rfind(",") > s.rfind("."):
            s = s.replace(".", "").replace(",", ".")
        else:
            s = s.replace(",", "")
    elif "," in s:
        if re.fullmatch(r"\d{1,3}(,\d{2})*,\d{3}", s):
            s = s.replace(",", "")  # grouping, incl. Indian 1,23,456
        elif re.fullmatch(r"\d*,\d{1,2}", s):
            s = s.replace(",", ".")
        else:
            raise ValueError("bad")
    elif s.count(".") > 1:
        if not re.fullmatch(r"\d{1,3}(\.\d{3})+", s):
            raise ValueError("bad")
        s = s.replace(".", "")
    try:
        value = float(s)
    except ValueError:
        raise ValueError("bad") from None
    return (-value if negative else value), currency


def _decode(data: str | bytes) -> str:
    if isinstance(data, bytes):
        if len(data) > MAX_BYTES:
            raise FileTooLarge("file exceeds 500 KB limit")
        try:
            return data.decode("utf-8-sig")
        except UnicodeDecodeError:
            return data.decode("latin-1")
    if len(data.encode("utf-8")) > MAX_BYTES:
        raise FileTooLarge("file exceeds 500 KB limit")
    return data.lstrip("﻿")


def _sniff(text: str) -> str:
    first = next((ln for ln in text.splitlines() if ln.strip()), "")
    counts = {d: first.count(d) for d in (",", ";", "\t")}
    best = max(counts, key=lambda d: counts[d])
    return best if counts[best] else ","


def parse_csv(text_or_bytes: str | bytes, filename: str = "") -> list[Row]:
    """Parse a statement CSV into Rows. Raises FileTooLarge / BadCsv."""
    text = _decode(text_or_bytes)
    if not text.strip():
        raise BadCsv("empty CSV")
    reader = csv.reader(io.StringIO(text), delimiter=_sniff(text))
    try:
        records = [r for r in reader if any(c.strip() for c in r)]
    except csv.Error:
        raise BadCsv("malformed CSV") from None
    headers = [_norm(h) for h in records[0]]
    idx: dict[str, int] = {}
    for key, names in _SYNONYMS.items():
        for n in names:
            if n in headers:
                idx[key] = headers.index(n)
                break
    if not ({"amount", "debit", "credit"} & set(idx)):
        raise BadCsv("no amount column found")

    def cell(rec: list[str], key: str) -> str:
        i = idx.get(key)
        return rec[i].strip() if i is not None and i < len(rec) else ""

    rows: list[Row] = []
    for n, rec in enumerate(records[1:], start=1):
        currency = cell(rec, "currency").upper()
        try:
            found = False
            amount = 0.0
            if cell(rec, "amount"):
                amount, cur = parse_amount(cell(rec, "amount"))
                found = True
                currency = currency or cur
            else:
                d, c = cell(rec, "debit"), cell(rec, "credit")
                if d:
                    v, cur = parse_amount(d)
                    amount, found, currency = abs(v), True, currency or cur
                elif c:
                    v, cur = parse_amount(c)
                    amount, found, currency = -abs(v), True, currency or cur
            if not found:
                raise ValueError("missing")
        except ValueError:
            raise BadCsv(f"unparseable amount in row {n}") from None
        raw = {records[0][i]: (rec[i] if i < len(rec) else "") for i in range(len(records[0]))}
        rows.append({
            "date": _parse_date(cell(rec, "date")),
            "description": cell(rec, "description"),
            "amount": amount,
            "currency": currency,
            "category": cell(rec, "category"),
            "raw": raw,
        })
    return rows


def is_expense(row: Row) -> bool:
    """Positive amounts are expenses."""
    return row["amount"] > 0


def is_income(row: Row) -> bool:
    """Negative amounts are income."""
    return row["amount"] < 0


def _cur(row: Row, default_currency: str) -> str:
    return row.get("currency") or default_currency or "UNKNOWN"


def summarize(rows: list[Row], by: str = "category", period: str | None = None,
              default_currency: str = "UNKNOWN") -> dict[str, dict[str, dict[str, float]]]:
    """Totals per currency: {currency: {key: {expense, income, net}}}.

    `net` is income minus expense. `period` ("YYYY-MM") filters by month.
    """
    if by not in ("category", "month", "currency"):
        raise ValueError("by must be category, month or currency")
    out: dict[str, dict[str, dict[str, float]]] = {}
    for r in rows:
        if period and r["date"][:7] != period:
            continue
        cur = _cur(r, default_currency)
        if by == "category":
            key = r["category"] or "Uncategorized"
        elif by == "month":
            key = r["date"][:7] or "UNKNOWN"
        else:
            key = cur
        t = out.setdefault(cur, {}).setdefault(key, {"expense": 0.0, "income": 0.0, "net": 0.0})
        if is_expense(r):
            t["expense"] += r["amount"]
        elif is_income(r):
            t["income"] += -r["amount"]
        t["net"] = t["income"] - t["expense"]
    for by_key in out.values():                    # float sums drift (ten rows of 0.10 are not exactly 1.00): report to the cent
        for t in by_key.values():
            for k in t:
                t[k] = round(t[k], 2)
    return out


def spent_on(rows: list[Row], words: list[str], month: str,
             default_currency: str = "UNKNOWN") -> dict[str, float]:
    """Sum expenses in `month` whose description/category contain any word."""
    ws = [w.lower() for w in words if w]
    out: dict[str, float] = {}
    for r in rows:
        if not is_expense(r) or r["date"][:7] != month:
            continue
        hay = f"{r['description']} {r['category']}".lower()
        if any(w in hay for w in ws):
            cur = _cur(r, default_currency)
            out[cur] = out.get(cur, 0.0) + r["amount"]
    return {cur: round(v, 2) for cur, v in out.items()}
