"""Checks a deck or report specification before anything is built.

Specs come from a model or from the research plugin, so they are untrusted: sizes are bounded, only known layouts and
fields are used, links must be http(s), and every citation marker must name a listed source.
"""
from __future__ import annotations

import re
from typing import Any

LAYOUTS = ("title", "section", "bullets", "two_column", "chart", "table", "quote", "timeline", "stats", "closing")
CHART_TYPES = ("bar", "column", "line", "pie")
CITE = re.compile(r"\[(S\d{1,3})\]")
MAX_SLIDES, MAX_SECTIONS, MAX_TEXT, MAX_ITEMS, MAX_SOURCES = 40, 30, 4000, 12, 60


class SpecError(ValueError):
    pass


def _s(v: Any, name: str, limit: int = MAX_TEXT, required: bool = False) -> str:
    if v is None or v == "":
        if required:
            raise SpecError(f"{name} is required")
        return ""
    if not isinstance(v, (str, int, float)):
        raise SpecError(f"{name} must be text")
    v = str(v).strip()
    if len(v) > limit:
        raise SpecError(f"{name} is too long ({len(v)} characters; at most {limit})")
    return v


def _list(v: Any, name: str, limit: int = MAX_ITEMS) -> list:
    if v is None:
        return []
    if not isinstance(v, list):
        raise SpecError(f"{name} must be a list")
    if len(v) > limit:
        raise SpecError(f"{name} has {len(v)} items; at most {limit}")
    return v


def _bullets(v: Any, name: str) -> list[dict]:
    """Bullets as plain strings or {"text", "level"}; a leading "- " or two spaces per level also works."""
    out = []
    for i, b in enumerate(_list(v, name)):
        if isinstance(b, dict):
            text, level = _s(b.get("text"), f"{name}[{i}].text", 400, True), int(b.get("level") or 0)
        else:
            raw = str(b)
            level = (len(raw) - len(raw.lstrip(" "))) // 2
            text = _s(raw.strip().lstrip("-•* ").strip(), f"{name}[{i}]", 400, True)
        out.append({"text": text, "level": max(0, min(level, 2))})
    return out


def sources(v: Any) -> list[dict]:
    out, seen = [], set()
    for i, s in enumerate(_list(v, "sources", MAX_SOURCES)):
        if not isinstance(s, dict):
            raise SpecError(f"sources[{i}] must be an object")
        sid = _s(s.get("id") or f"S{i + 1}", f"sources[{i}].id", 8)
        if not re.fullmatch(r"S\d{1,3}", sid) or sid in seen:
            raise SpecError(f"sources[{i}].id must be unique and look like S1, S2, ...")
        seen.add(sid)
        url = _s(s.get("url"), f"sources[{i}].url", 600)
        if url and not re.match(r"https?://", url, re.I):
            raise SpecError(f"sources[{i}].url must start with http:// or https://")
        out.append({"id": sid, "title": _s(s.get("title"), f"sources[{i}].title", 300, True), "url": url,
                    "date": _s(s.get("date"), f"sources[{i}].date", 40), "publisher": _s(s.get("publisher"), f"sources[{i}].publisher", 120)})
    return out


def chart(v: Any, name: str) -> dict | None:
    if not v:
        return None
    if not isinstance(v, dict):
        raise SpecError(f"{name} must be an object")
    kind = (v.get("type") or "column").lower()
    if kind not in CHART_TYPES:
        raise SpecError(f"{name}.type must be one of {', '.join(CHART_TYPES)}")
    labels = [_s(x, f"{name}.labels", 60, True) for x in _list(v.get("labels"), f"{name}.labels", 24)]
    series = []
    for j, s in enumerate(_list(v.get("series"), f"{name}.series", 6)):
        vals = _list(s.get("values") if isinstance(s, dict) else None, f"{name}.series[{j}].values", 24)
        try:
            vals = [float(x) for x in vals]
        except (TypeError, ValueError):
            raise SpecError(f"{name}.series[{j}].values must be numbers") from None
        if len(vals) != len(labels):
            raise SpecError(f"{name}.series[{j}] has {len(vals)} values for {len(labels)} labels")
        series.append({"name": _s(s.get("name") or f"Series {j + 1}", f"{name}.series[{j}].name", 60), "values": vals})
    if not labels or not series:
        raise SpecError(f"{name} needs labels and at least one series")
    if kind == "pie" and len(series) != 1:
        raise SpecError(f"{name}: a pie chart takes exactly one series")
    return {"type": kind, "title": _s(v.get("title"), f"{name}.title", 120), "labels": labels, "series": series,
            "caption": _s(v.get("caption"), f"{name}.caption", 300), "unit": _s(v.get("unit"), f"{name}.unit", 20)}


def table(v: Any, name: str) -> dict | None:
    if not v:
        return None
    if not isinstance(v, dict):
        raise SpecError(f"{name} must be an object")
    cols = [_s(c, f"{name}.columns", 60, True) for c in _list(v.get("columns"), f"{name}.columns", 8)]
    rows = []
    for r_i, r in enumerate(_list(v.get("rows"), f"{name}.rows", 20)):
        cells = [_s(c, f"{name}.rows[{r_i}]", 300) for c in _list(r, f"{name}.rows[{r_i}]", 8)]
        if len(cells) != len(cols):
            raise SpecError(f"{name}.rows[{r_i}] has {len(cells)} cells for {len(cols)} columns")
        rows.append(cells)
    if not cols or not rows:
        raise SpecError(f"{name} needs columns and rows")
    return {"columns": cols, "rows": rows, "caption": _s(v.get("caption"), f"{name}.caption", 300)}


def _check_citations(texts: list[str], ids: set[str]) -> None:
    missing = sorted({m for t in texts for m in CITE.findall(t or "")} - ids)
    if missing:
        raise SpecError(f"citation markers name sources that are not listed: {', '.join(missing)}")


def deck(spec: Any) -> dict:
    if not isinstance(spec, dict):
        raise SpecError("the presentation spec must be an object")
    out = {"title": _s(spec.get("title"), "title", 160, True), "subtitle": _s(spec.get("subtitle"), "subtitle", 240),
           "author": _s(spec.get("author"), "author", 80), "theme": _s(spec.get("theme"), "theme", 20),
           "topic": _s(spec.get("topic"), "topic", 400), "animate": spec.get("animate", True) is not False,
           "sources": sources(spec.get("sources")), "slides": []}
    texts = []
    for i, sl in enumerate(_list(spec.get("slides"), "slides", MAX_SLIDES)):
        if not isinstance(sl, dict):
            raise SpecError(f"slides[{i}] must be an object")
        layout = (sl.get("layout") or "bullets").lower()
        if layout not in LAYOUTS:
            raise SpecError(f"slides[{i}].layout must be one of {', '.join(LAYOUTS)}")
        n = f"slides[{i}]"
        s = {"layout": layout, "title": _s(sl.get("title"), f"{n}.title", 160, layout not in ("quote", "closing")),
             "subtitle": _s(sl.get("subtitle"), f"{n}.subtitle", 240), "notes": _s(sl.get("notes"), f"{n}.notes", 3000),
             "bullets": _bullets(sl.get("bullets"), f"{n}.bullets"),
             "left_title": _s(sl.get("left_title"), f"{n}.left_title", 80), "right_title": _s(sl.get("right_title"), f"{n}.right_title", 80),
             "left": _bullets(sl.get("left"), f"{n}.left"), "right": _bullets(sl.get("right"), f"{n}.right"),
             "chart": chart(sl.get("chart"), f"{n}.chart"), "table": table(sl.get("table"), f"{n}.table"),
             "quote": _s(sl.get("quote"), f"{n}.quote", 400), "attribution": _s(sl.get("attribution"), f"{n}.attribution", 120),
             "items": [], "sources": [str(x) for x in _list(sl.get("sources"), f"{n}.sources")]}
        for j, it in enumerate(_list(sl.get("items"), f"{n}.items", 8)):
            if not isinstance(it, dict):
                raise SpecError(f"{n}.items[{j}] must be an object")
            s["items"].append({"label": _s(it.get("label") or it.get("value"), f"{n}.items[{j}].label", 40, True),
                               "text": _s(it.get("text"), f"{n}.items[{j}].text", 200)})
        if layout == "chart" and not s["chart"]:
            raise SpecError(f"{n}: a chart slide needs a chart")
        if layout == "table" and not s["table"]:
            raise SpecError(f"{n}: a table slide needs a table")
        if layout in ("timeline", "stats") and not s["items"]:
            raise SpecError(f"{n}: a {layout} slide needs items")
        texts += [s["title"], s["subtitle"], s["quote"], s["notes"]] + [b["text"] for b in s["bullets"] + s["left"] + s["right"]] \
            + [it["text"] for it in s["items"]]
        out["slides"].append(s)
    if not out["slides"]:
        raise SpecError("the presentation needs at least one slide")
    ids = {x["id"] for x in out["sources"]}
    _check_citations(texts, ids)
    for s in out["slides"]:
        bad = [x for x in s["sources"] if x not in ids]
        if bad:
            raise SpecError(f"slide '{s['title']}' lists unknown sources: {', '.join(bad)}")
    return out


def report(spec: Any) -> dict:
    if not isinstance(spec, dict):
        raise SpecError("the report spec must be an object")
    out = {"title": _s(spec.get("title"), "title", 160, True), "subtitle": _s(spec.get("subtitle"), "subtitle", 240),
           "author": _s(spec.get("author"), "author", 80), "theme": _s(spec.get("theme"), "theme", 20),
           "topic": _s(spec.get("topic"), "topic", 400), "summary": _s(spec.get("summary"), "summary", 3000),
           "sources": sources(spec.get("sources")), "sections": []}
    texts = [out["summary"]]
    for i, se in enumerate(_list(spec.get("sections"), "sections", MAX_SECTIONS)):
        if not isinstance(se, dict):
            raise SpecError(f"sections[{i}] must be an object")
        n = f"sections[{i}]"
        kind = (se.get("kind") or "").lower()
        if kind and kind not in ("fact", "opinion", "uncertain", "recommendation"):
            raise SpecError(f"{n}.kind must be fact, opinion, uncertain or recommendation")
        s = {"heading": _s(se.get("heading"), f"{n}.heading", 160, True), "level": 3 if se.get("level") == 3 else 2,
             "kind": kind, "paragraphs": [_s(p, f"{n}.paragraphs", MAX_TEXT, True) for p in _list(se.get("paragraphs"), f"{n}.paragraphs", 20)],
             "bullets": _bullets(se.get("bullets"), f"{n}.bullets"), "callout": _s(se.get("callout"), f"{n}.callout", 600),
             "table": table(se.get("table"), f"{n}.table"), "chart": chart(se.get("chart"), f"{n}.chart")}
        texts += s["paragraphs"] + [b["text"] for b in s["bullets"]] + [s["callout"]]
        out["sections"].append(s)
    if not out["sections"]:
        raise SpecError("the report needs at least one section")
    _check_citations(texts, {x["id"] for x in out["sources"]})
    return out
