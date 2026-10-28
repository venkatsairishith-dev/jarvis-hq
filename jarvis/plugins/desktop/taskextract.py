"""D76: reading values out of a page or a document, LOCALLY, and proving each one came from it.

A page's text and a document's text are read here by fixed rules first and, when a rule finds nothing, by the local model (grammar-forced JSON, the same loopback model
goal.py uses for its argument picks). Nothing here is sent anywhere. The rule that makes it trustworthy is `Found.evidence`: every value comes with the line of text it
was read from, and a value that is NOT in the text (a model that made it up) is thrown away. "Verified by effect" for a value means: it is on the page.

Also here: the page as text (`page_text`), the registrable domain of a host (for the "at least N distinct domains" rule), and the dates, prices and durations that the
rules know. Standard library only.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Callable

MAX_TEXT = 30000
MONTHS = "jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec|january|february|march|april|june|july|august|september|october|november|december"
DATE_RX = [
    re.compile(r"(?i)\b\d{1,2}(?:st|nd|rd|th)?[ -](?:" + MONTHS + r")[a-z]*\.?,? ?\d{4}\b"),
    re.compile(r"(?i)\b(?:" + MONTHS + r")[a-z]*\.? \d{1,2}(?:st|nd|rd|th)?,? \d{4}\b"),
    re.compile(r"\b\d{4}-\d{2}-\d{2}\b"),
    re.compile(r"\b\d{1,2}[/.-]\d{1,2}[/.-](?:\d{4}|\d{2})\b"),
]
PRICE_RX = re.compile(r"(?i)(?:[$€£₹¥]|\bRs\.?|\bINR|\bUSD|\bEUR|\bGBP)\s?\d[\d,]*(?:\.\d{1,2})?|\b\d[\d,]*(?:\.\d{1,2})?\s?(?:USD|EUR|GBP|INR|Rs\.?)\b")
DURATION_RX = re.compile(r"(?<![\d:])(\d{1,2}):([0-5]\d)(?::([0-5]\d))?(?![\d:])")
LABEL_RX = {"vendor": r"(?:from|vendor|seller|supplier|billed by|sold by|company|issued by|merchant|payee|store)",
            "invoice_number": r"(?:invoice\s*(?:no\.?|number|#)|inv\s*(?:no\.?|#)|bill\s*(?:no\.?|number))",
            "date": r"(?:invoice date|date|dated|issued|bill date)"}
NOT_VENDOR = re.compile(r"(?i)\b(invoice|receipt|bill|tax|page|date|total|amount|due|qty|quantity|description|item|price|subtotal|gst|vat|thank|www\.|http|@)\b|^\W*\d")


def norm(s) -> str:
    return " ".join(str(s or "").replace(" ", " ").replace("’", "'").split()).casefold()


def _digits(s: str) -> str:
    return re.sub(r"[^\d.]", "", s or "")


def in_text(value: str, text: str) -> bool:
    """Is the value really in the text (case, spacing and thousands separators do not matter)? A value the model invented is not."""
    v, t = norm(value), norm(text)
    if not v:
        return False
    if v in t:
        return True
    if re.search(r"\d", v):
        dv = re.sub(r"[,\s]", "", v)
        return dv in re.sub(r"[,\s]", "", t)
    return False


def registrable(host: str) -> str:
    """The site a host belongs to: 'www.example.co.uk' -> 'example.co.uk', 'en.wikipedia.org' -> 'wikipedia.org' (a short list of two-part suffixes; not the public suffix list)."""
    parts = [p for p in str(host or "").lower().strip(".").split(".") if p]
    if len(parts) <= 2:
        return ".".join(parts)
    if ".".join(parts[-2:]) in {"co.uk", "org.uk", "ac.uk", "gov.uk", "co.in", "ac.in", "edu.in", "org.in", "gov.in", "com.au", "co.jp", "co.nz", "com.br", "co.za", "edu.au"}:
        return ".".join(parts[-3:])
    return ".".join(parts[-2:])


def page_text(pg, limit: int = MAX_TEXT) -> str:
    """The visible text of a browser page (title first), as the lines the snapshot gave. Local use only."""
    head = [pg.title] if getattr(pg, "title", "") else []
    return "\n".join(head + list(getattr(pg, "lines", []) or []))[:limit]


@dataclass
class Found:
    name: str
    value: str
    evidence: str = ""             # the line it was read from
    how: str = "rule"              # rule | model | best_line
    many: list = None              # for names that can have several values (dates)

    def to_dict(self) -> dict:
        d = {"name": self.name, "value": self.value, "how": self.how}
        if self.many:
            d["many"] = list(self.many)
        return d


def _lines(text: str) -> list:
    return [ln.strip() for ln in str(text or "").splitlines() if ln.strip()]


def _tok(s: str) -> set:
    from .taskplan import STOP
    return {t for t in re.findall(r"[a-z0-9]+", norm(s)) if len(t) > 2 and t not in STOP and t not in {"page", "says", "say", "what", "does", "read"}}


def _near(lines: list, subject: str, idx: int, span: int = 4) -> bool:
    want = _tok(subject)
    if not want:
        return True
    ctx = " ".join(lines[max(0, idx - span): idx + span + 1])
    return len(want & _tok(ctx)) >= max(1, len(want) // 2)


def find_dates(text: str) -> list:
    """[(date text, its line)] in order of appearance, without repeats."""
    out, seen = [], set()
    for ln in _lines(text):
        hits = []
        for rx in DATE_RX:
            hits += [(m.start(), m.group(0)) for m in rx.finditer(ln)]
        for _pos, d in sorted(hits):
            k = norm(d)
            if k not in seen:
                seen.add(k)
                out.append((d.strip(), ln))
    return out


def find_price(text: str, subject: str = "") -> "tuple[str, str] | None":
    """(price, its line): the price on a line that mentions the subject (or one line after it), else after a 'price' label, else the first price on the page."""
    lines = _lines(text)
    hits = [(i, m.group(0).strip()) for i, ln in enumerate(lines) for m in PRICE_RX.finditer(ln)]
    if not hits:
        return None
    for i, p in hits:
        if subject and _tok(subject) & _tok(lines[i]):
            return p, lines[i]
    for i, p in hits:
        if re.search(r"(?i)\bprice\b|\bnow\b|\bour price\b", lines[max(0, i - 1)] + " " + lines[i]) and _near(lines, subject, i):
            return p, lines[i]
    for i, p in hits:
        if _near(lines, subject, i):
            return p, lines[i]
    return hits[0][1], lines[hits[0][0]]


def find_duration(text: str) -> "tuple[str, str] | None":
    """The longest clock-style duration on the page ('12:34', '1:02:03'): a video page also shows the time played so far, and the length is the largest."""
    best = None
    for ln in _lines(text):
        for m in DURATION_RX.finditer(ln):
            secs = (int(m.group(1)) * 3600 + int(m.group(2)) * 60 + int(m.group(3))) if m.group(3) else int(m.group(1)) * 60 + int(m.group(2))
            if best is None or secs > best[0]:
                best = (secs, m.group(0), ln)
    return (best[1], best[2]) if best else None


def find_labelled(text: str, name: str) -> "tuple[str, str] | None":
    rx = LABEL_RX.get(name)
    pat = rx or re.escape(name.replace("_", " "))
    for ln in _lines(text):
        m = re.match(r"(?i)^\W*" + pat + r"\s*[:\-–]\s*(.{2,100})$", ln)
        if m:
            return m.group(1).strip(), ln
    return None


def find_vendor(text: str) -> "tuple[str, str] | None":
    got = find_labelled(text, "vendor")
    if got:
        return got
    for ln in _lines(text)[:8]:
        if 3 <= len(ln) <= 60 and not NOT_VENDOR.search(ln):
            return ln, ln
    return None


def find_total(text: str) -> "tuple[str, str] | None":
    """(amount with its currency mark when the document has one, its line): the document reader's own total finder when it is installed, else the same idea in a few rules."""
    try:
        from jarvis.plugins.doc_reader import core as dr
        t = dr.find_total([text])
        if t:
            amount = t["amount"]
            for ln in _lines(text):
                for m in PRICE_RX.finditer(ln):
                    if _digits(m.group(0)).rstrip(".") == _digits(amount).rstrip(".") or re.sub(r"[ ,]", "", amount) in re.sub(r"[ ,]", "", m.group(0)):
                        if re.search(r"(?i)total|due|payable|amount", ln):
                            return m.group(0).strip(), ln
            return (amount + (" " + t["currency"] if t.get("currency") else "")).strip(), t["label"]
    except Exception:  # noqa: BLE001 - the reader is optional here
        pass
    for ln in reversed(_lines(text)):
        if re.search(r"(?i)\b(?:grand total|total due|amount due|total amount|total)\b", ln) and not re.search(r"(?i)sub-?total|tax|vat|gst", ln):
            m = PRICE_RX.search(ln) or re.search(r"\d[\d,]*\.\d{2}", ln)
            if m:
                return m.group(0).strip(), ln
    return None


def best_line(text: str, question: str) -> "tuple[str, str] | None":
    """No rule for the value and no model: the one line of the page that shares the most words with the question (at least two), cut to 220 characters. Said as such."""
    want = _tok(question)
    if len(want) < 1:
        return None
    from .browser_snapshot import INJECTION
    best = None
    for ln in _lines(text):
        if len(ln) < 12 or len(ln) > 300 or INJECTION.search(ln) or re.search(r"(?i)\b(?:delete|click|go to|you are (?:now )?allowed)\b.*\bhttps?://", ln):
            continue                                     # a page line that gives orders is never the answer
        have = _tok(ln)
        score = sum(1 for t in want if t in have or any(len(t) >= 4 and len(h) >= 4 and (h.startswith(t) or t.startswith(h)) for h in have))
        if score >= min(2, len(want)) and (best is None or score > best[0] or (score == best[0] and len(ln) < len(best[1]))):
            best = (score, ln)
    return (best[1][:220], best[1]) if best else None


class Extractor:
    """`llm(question, text, names) -> {name: value}` is the optional local model (None: rules only). Its answers are kept only when they are in the text."""

    def __init__(self, llm: "Callable[[str, str, list], dict] | None" = None, title_strip: str = ""):
        self.llm = llm
        self.title_strip = title_strip

    def title(self, title: str, text: str) -> "tuple[str, str] | None":
        t = " ".join(str(title or "").split())
        if self.title_strip and t.endswith(self.title_strip):
            t = t[: -len(self.title_strip)].strip()
        if not t:
            ln = _lines(text)
            t = ln[0] if ln else ""
        return (t, t) if t else None

    def one(self, name: str, text: str, *, title: str = "", subject: str = "", question: str = "") -> "Found | None":
        n = re.sub(r"[^a-z0-9_]", "_", name.strip().lower()).strip("_")
        got = None
        if n in ("title", "name", "product", "product_name", "heading"):
            lines = _lines(text)
            if n in ("name", "product", "product_name") and subject:
                got = next(((ln, ln) for ln in lines if _tok(subject) & _tok(ln) and 3 <= len(ln) <= 120), None) or self.title(title, text)
            else:
                got = self.title(title, text)
        elif n in ("price", "cost", "amount_price"):
            got = find_price(text, subject)
        elif n in ("length", "duration", "runtime", "time"):
            got = find_duration(text)
        elif n in ("date", "dates", "deadline", "when"):
            ds = find_dates(text)
            if ds:
                return Found(n, ds[0][0], ds[0][1], "rule", [d for d, _ in ds][:20])
        elif n in ("vendor", "seller", "supplier", "company"):
            got = find_vendor(text)
        elif n in ("total", "amount", "grand_total", "total_amount"):
            got = find_total(text)
        elif n in ("invoice_number", "number"):
            got = find_labelled(text, "invoice_number")
        elif n not in ("answer", "summary"):
            got = find_labelled(text, n)
        if got:
            return Found(n, got[0], got[1], "rule")
        if self.llm is not None:
            try:
                ans = (self.llm(question or subject or n.replace("_", " "), text[:6000], [n]) or {}).get(n)
            except Exception:  # noqa: BLE001 - a model that is down is no model
                ans = None
            if isinstance(ans, (str, int, float)) and str(ans).strip() and in_text(str(ans), text):
                v = " ".join(str(ans).split())[:200]
                ev = next((ln for ln in _lines(text) if in_text(v, ln)), v)
                return Found(n, v, ev, "model")
        if n in ("answer", "summary") or question:
            bl = best_line(text, question or subject or n)
            if bl:
                return Found(n, bl[0], bl[1], "best_line")
        return None

    def read(self, names: list, text: str, *, title: str = "", subject: str = "", question: str = "") -> "tuple[dict, list]":
        """({name: Found}, [names not found]). Every Found's value is in `text` (rule results are read from it; model results were checked)."""
        found, missing = {}, []
        text = f"{title}\n{text}" if title and title not in text else text          # the page's title is part of what the page says
        for name in names:
            f = self.one(name, text, title=title, subject=subject, question=question)
            from .browser_snapshot import INJECTION
            if f is not None and not INJECTION.search(f.value) and (in_text(f.value, text) or f.how == "rule" and f.name in ("total", "amount", "grand_total", "total_amount")):
                found[name] = f
            else:
                missing.append(name)
        return found, missing
