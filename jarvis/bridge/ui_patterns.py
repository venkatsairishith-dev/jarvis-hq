"""The shapes JARVIS builds for common tasks (D55): `build(pattern, data)` turns a few facts into a declarative form (ask_spec.py).

The guide that says which pattern fits which task is jarvis/config/ui_patterns.json; every id there has a builder here (a test checks it).
A builder never fails on odd data: it uses what is usable and leaves the rest out. `cleanup` is not built here (bridge/cleaner_ui.py owns
it, it needs the scan) and `job` is built here but shown without waiting.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Callable

from jarvis.bridge.ask_spec import AskError, plain, save_folders  # noqa: F401

GUIDE_FILE = Path(__file__).resolve().parents[1] / "config" / "ui_patterns.json"
OK_CANCEL = [{"id": "ok", "label": "Continue", "style": "primary"}, {"id": "cancel", "label": "Cancel", "style": "secondary"}]
CONSOLE_NOTE = "This only records your choice. If a step is risky, JARVIS still asks for your PIN in the JARVIS console."
CHOICE_Q = re.compile(r"(?is)^\s*which one\s*[:?]\s*(.{1,60}?)\s+or\s+(.{1,60}?)\s*\??\s*$")
FILE_Q = re.compile(r"(?i)^\s*which (file|document|pdf|one)\b")


def guide() -> dict:
    return json.loads(GUIDE_FILE.read_text(encoding="utf-8"))["patterns"]


def _d(data: Any) -> dict:
    return data if isinstance(data, dict) else {}


def _list(value: Any) -> list:
    return value if isinstance(value, list) else []


def _options(items: Any, prefix: str = "o") -> list[dict]:
    out = []
    for i, o in enumerate(_list(items)[:30]):
        o = o if isinstance(o, dict) else {"label": o}
        out.append({**{k: v for k, v in o.items() if k in ("label", "detail", "pros", "cons", "badge", "size", "checked", "body")},
                    "id": str(o.get("id") or f"{prefix}{i + 1}"), "label": o.get("label") or o.get("id") or f"Option {i + 1}"})
    return out


def clarify(data: dict, **_) -> dict:
    """The decider's clarification questions: 'Which one: notepad or wordpad?' is a choice, 'Which file?' lists the newest files, else a text box."""
    q = plain(data.get("question") or data.get("text"), 300) or "Could you say a bit more?"
    m = CHOICE_Q.match(q)
    if m:
        return {"title": "Which one?", "intro": q, "fields": [
            {"id": "choice", "type": "radio", "variant": "segmented", "label": "Choose one", "required": True,
             "options": [{"id": "a", "label": plain(m.group(1), 60), "checked": True}, {"id": "b", "label": plain(m.group(2), 60)}]}], "buttons": OK_CANCEL}
    if FILE_Q.match(q):
        return pick_file({"question": "Pick the one you mean."})
    return {"title": "JARVIS asks", "intro": q, "fields": [{"id": "answer", "type": "text", "label": "Your answer", "max_len": 200, "required": True}], "buttons": OK_CANCEL}


def pick_file(data: dict, **_) -> dict:
    source = str(data.get("source") or "recent")
    gallery = data.get("view") == "gallery" or source == "pictures"
    node = {"id": "file", "type": "gallery" if gallery else "file_choice", "source": source, "required": True,
            "label": "Pictures" if gallery else "Newest files (Downloads, Documents, Desktop)" if source == "recent" else "Files"}
    return {"title": "Which picture?" if gallery else "Which file?", "intro": plain(data.get("question"), 300) or "Pick one.", "fields": [node], "buttons": OK_CANCEL}


def save(data: dict, **_) -> dict:
    what = plain(data.get("what") or data.get("text"), 80) or "this"
    fields: list[dict] = []
    if data.get("file_name") is not None:                             # D63: the file name is an editable heading (typed or dictated)
        fields.append({"id": "name", "type": "text", "heading": True, "label": "File name", "value": plain(data.get("file_name"), 120), "max_len": 120, "required": True})
    fields.append({"id": "folder", "type": "file_choice", "source": "folders", "label": "Folder", "required": True})
    return {"title": "Where to save?", "intro": f"Choose a folder for {what}.", "fields": fields,
            "buttons": [{"id": "ok", "label": "Save here", "style": "primary"}, {"id": "cancel", "label": "Cancel", "style": "secondary"}]}


def choose(data: dict, **_) -> dict:
    options = _options(data.get("options"))
    q = plain(data.get("question"), 300) or "Choose one."
    if not options:
        return clarify({"question": q})
    if any(o.get("pros") or o.get("cons") for o in options):
        node = {"id": "choice", "type": "cards", "label": "Options", "options": options, "required": True}
    elif len(options) <= 5 and not any(o.get("detail") for o in options) and all(len(str(o["label"])) <= 24 for o in options):
        node = {"id": "choice", "type": "radio", "variant": "segmented", "label": "Choose one", "options": options, "required": True}
    else:
        node = {"id": "choice", "type": "radio", "label": "Choose one", "options": options, "required": True}
    return {"title": plain(data.get("title"), 80) or "Which one?", "intro": q, "fields": [node], "buttons": OK_CANCEL}


def settings(data: dict, **_) -> dict:
    fields = []
    for i, it in enumerate(_list(data.get("items"))[:12]):
        if not isinstance(it, dict):
            continue
        kind, fid = str(it.get("kind") or "toggle"), str(it.get("id") or f"s{i + 1}")
        base = {"id": fid, "label": it.get("label") or fid, "value": it.get("value")}
        if kind == "choice":
            opts = _options(it.get("options"), "c")
            for o in opts:
                o["checked"] = o["id"] == str(it.get("value"))
            fields.append({**base, "type": "select", "options": opts})
        elif kind in ("slider", "number"):
            fields.append({**base, "type": kind, "min": it.get("min", 0), "max": it.get("max", 100), "step": it.get("step", 1), "unit": it.get("unit")})
        elif kind == "text":
            fields.append({**base, "type": "text"})
        else:
            fields.append({**base, "type": "toggle", "value": it.get("value") is True})
    return {"title": plain(data.get("title"), 80) or "Settings", "intro": plain(data.get("intro"), 300), "fields": fields,
            "buttons": [{"id": "apply", "label": "Apply", "style": "primary"}, {"id": "undo", "label": "Undo changes", "style": "secondary"},
                        {"id": "cancel", "label": "Cancel", "style": "quiet"}]}


def code(data: dict, **_) -> dict:
    text, lang = str(data.get("code") or ""), plain(data.get("language") or "text", 20)
    explanation = str(data.get("explanation") or "")
    view = {"id": "code", "type": "code_view", "label": "", "text": text, "language": lang}
    tabs = [{"id": "code", "label": "Code", "children": [view]}]
    if explanation:
        tabs.append({"id": "why", "label": "Explanation", "children": [{"type": "callout", "text": explanation}]})
    buttons = ([{"id": "type_in_editor", "label": "Type it into the editor", "style": "primary"}] if data.get("can_type") else []) + [
        {"id": "close", "label": "Done", "style": "secondary" if data.get("can_type") else "primary"}]
    return {"title": plain(data.get("title"), 80) or "Here is the code", "intro": "", "fields": [{"type": "tabs", "tabs": tabs}], "buttons": buttons}


def compare(data: dict, **_) -> dict:
    title = plain(data.get("title"), 80) or "Compare"
    if data.get("options"):
        return {**choose({"options": data["options"], "question": data.get("question") or "Pick the one you want."}), "title": title}
    return {"title": title, "intro": plain(data.get("question"), 300), "fields": [
        {"id": "pick", "type": "table", "label": "", "columns": _list(data.get("columns")), "rows": _list(data.get("rows")), "select": "one", "required": True}],
        "buttons": [{"id": "ok", "label": "Choose this", "style": "primary"}, {"id": "cancel", "label": "Cancel", "style": "secondary"}]}


def job(data: dict, **_) -> dict:
    items = [s for s in _list(data.get("steps")) if isinstance(s, dict)]
    fields: list[dict] = [{"type": "steps", "label": "", "items": items}]
    value = data.get("value")
    if value is not None or data.get("indeterminate") is not False:
        fields.append({"type": "progress", "label": plain(data.get("label"), 80), "value": value, "max": data.get("max", 100)})
    return {"title": plain(data.get("title"), 80) or "Working on it", "intro": plain(data.get("intro"), 300), "fields": fields,
            "buttons": [{"id": "cancel", "label": "Cancel job", "style": "secondary"}]}


def data_view(data: dict, **_) -> dict:
    fields: list[dict] = [{"type": "chart", "kind": data.get("kind"), "label": plain(data.get("label"), 80), "labels": data.get("labels"),
                           "series": data.get("series"), "unit": data.get("unit")}]
    labels, series = _list(data.get("labels")), [s for s in _list(data.get("series")) if isinstance(s, dict)]
    if labels and series:
        cols = [{"id": "label", "label": plain(data.get("x_label"), 30) or "Item"}] + [{"id": f"s{i}", "label": s.get("name") or f"Series {i + 1}", "align": "right"} for i, s in enumerate(series[:5])]
        rows = [{"id": f"r{j}", "cells": {"label": lab, **{f"s{i}": (s.get("values") or [""] * (j + 1))[j] if j < len(s.get("values") or []) else "" for i, s in enumerate(series[:5])}}}
                for j, lab in enumerate(labels[:60])]
        fields.append({"type": "table", "id": "numbers", "label": "The numbers", "columns": cols, "rows": rows, "select": "none"})
    return {"title": plain(data.get("title"), 80) or "The numbers", "intro": plain(data.get("intro"), 300), "fields": fields,
            "buttons": [{"id": "close", "label": "Done", "style": "primary"}]}


def confirm(data: dict, **_) -> dict:
    rows = [r for r in _list(data.get("rows")) if isinstance(r, (list, tuple)) and len(r) >= 2]
    fields: list[dict] = [{"type": "info_table", "label": "What will happen", "rows": rows}]
    if data.get("warn"):
        fields.append({"type": "callout", "level": "warn", "text": plain(data["warn"], 400)})
    fields.append({"type": "callout", "level": "info", "text": CONSOLE_NOTE})
    return {"title": plain(data.get("title"), 80) or "Please check", "intro": plain(data.get("intro"), 300), "fields": fields,
            "buttons": [{"id": "approve", "label": plain(data.get("approve_label"), 30) or "Go ahead", "style": "primary"},
                        {"id": "cancel", "label": "Cancel", "style": "secondary"}]}


def schedule(data: dict, **_) -> dict:
    repeat = [str(r) for r in _list(data.get("repeat"))[:6]] or ["none", "daily", "weekly", "monthly"]
    fields: list[dict] = []
    if data.get("what") is not None:                                  # D63: the reminder text is an editable heading (typed or dictated)
        fields.append({"id": "what", "type": "text", "heading": True, "label": "Reminder", "value": plain(data.get("what"), 200), "max_len": 200, "required": True})
    fields += [
        {"id": "day", "type": "date", "mode": "date", "label": "Day", "required": True},
        {"id": "at", "type": "date", "mode": "time", "label": "Time", "required": True},
        {"id": "repeat", "type": "select", "label": "Repeat", "options": [{"id": r, "label": r.capitalize(), "checked": i == 0} for i, r in enumerate(repeat)]},
        {"id": "note", "type": "text", "label": "Note", "max_len": 200}]
    return {"title": plain(data.get("title"), 80) or "When?", "intro": plain(data.get("note"), 300), "fields": fields,
            "buttons": [{"id": "ok", "label": "Set it", "style": "primary"}, {"id": "cancel", "label": "Cancel", "style": "secondary"}]}


def text(data: dict, **_) -> dict:
    return {"title": plain(data.get("title"), 80) or "JARVIS asks", "intro": plain(data.get("question"), 300), "fields": [
        {"id": "answer", "type": "text", "label": "Your answer", "placeholder": data.get("placeholder"), "multiline": data.get("multiline") is True,
         "heading": data.get("heading") is True, "max_len": 1000 if data.get("multiline") else 300, "required": True}], "buttons": OK_CANCEL}


BUILDERS: dict[str, Callable[..., dict]] = {
    "pick_file": pick_file, "choose": choose, "settings": settings, "code": code, "compare": compare, "job": job, "data_view": data_view,
    "confirm": confirm, "schedule": schedule, "text": text, "save": save, "clarify": clarify,
}
PATTERN_IDS = ("cleanup",) + tuple(BUILDERS)                 # cleanup is built by cleaner_ui.py
NO_WAIT = {"job"}                                             # shown and left open; a call with update=true moves it forward


def build(pattern: str, data: Any) -> dict:
    if pattern not in BUILDERS:
        raise AskError(f"pattern is one of {', '.join(PATTERN_IDS)}")
    return BUILDERS[pattern](_d(data))


def id_line() -> str:
    return "|".join(PATTERN_IDS)
