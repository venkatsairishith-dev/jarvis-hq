"""The form language (D55: generated UI = declarative forms only). A spec is JSON; the face's one trusted renderer draws it.

    spec   {title, intro, timeout_s, fields: [node], buttons: [{id, label, style}]}
    node   {type, id?, label?, hint?, ...}   see TYPES below

Nothing here runs, fetches or follows anything. Every text is plain text (control, invisible and direction characters removed, cut to a
limit); the face shows it as text, never as markup, and a link is not clickable. `clean_spec` is forgiving on purpose: an unknown component or
a bad shape becomes a plain list or a short note, never an error screen (`notes` tells the owner that a part was simplified). Only a spec that
is not an object at all, or that asks for a PIN / password / code, is refused. Size caps: nodes, depth, rows, characters.

Types
  layout   section{children, collapsed}  row{children}  column{children}  tabs{tabs:[{id,label,children}]}  grid{children}
           split{options:[{id,label,detail,body}]}  (a list on one side, the chosen item's text on the other; an input)
  input    checkbox_group  radio{variant: list|segmented}  select  text{multiline, heading}  number  slider{unit}  toggle  date{mode: date|time|datetime}
           file_choice{source}  gallery{source, multiple}  table{columns, rows, select: none|one|many}  cards{options with pros/cons, multiple}
  display  info_table{rows}  chart{kind: bar|line|pie, labels, series}  code_view{text, language}  diff_view{before, after}
           steps{items:[{label, status}]}  progress{value, max, label}  callout{level: info|warn, text}
"""
from __future__ import annotations

import os
import re
import stat
from pathlib import Path
from typing import Any

from jarvis.bridge.output_preview import INVISIBLE, SECRET_NAME

LAYOUT = ("section", "row", "column", "tabs", "grid")
INPUTS = ("checkbox_group", "radio", "select", "text", "number", "slider", "toggle", "date", "file_choice", "gallery", "table", "cards", "split")
DISPLAY = ("info_table", "chart", "code_view", "diff_view", "steps", "progress", "callout")
TYPES = LAYOUT + INPUTS + DISPLAY
BUTTON_STYLES = ("primary", "secondary", "quiet")
STEP_STATUS = ("pending", "running", "done", "failed", "skipped")
FILE_SOURCES = ("downloads", "documents", "desktop", "pictures", "recent", "folders")
KNOWN_FOLDERS = {"downloads": "Downloads", "documents": "Documents", "desktop": "Desktop", "pictures": "Pictures"}
IMAGE_EXT = (".png", ".jpg", ".jpeg", ".gif", ".webp")

MAX_NODES = 150
MAX_DEPTH = 4
MAX_CHARS = 60_000
MAX_OPTIONS = 500
MAX_ROWS = 500
MAX_COLS = 8
MAX_STEPS = 30
MAX_SERIES = 6
MAX_POINTS = 60
MAX_CODE = 20_000
MAX_BUTTONS = 4
MAX_SUBITEMS = 8
DEFAULT_TIMEOUT_S = 90
MAX_TIMEOUT_S = 150                       # the face gives a tool call 180 s
RECENT_FILE_LIMIT = 12
GALLERY_LIMIT = 24
THUMB_PX = 160
THUMB_RAW_MAX = 300_000
THUMBS_TOTAL_MAX = 1_200_000

ID_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,31}$")
OPTION_ID_RE = re.compile(r"^[\w.:-]{1,64}$")
DATE_RES = {"date": re.compile(r"^\d{4}-\d{2}-\d{2}$"), "time": re.compile(r"^\d{2}:\d{2}$"), "datetime": re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}$")}
# A form never collects what only the console may take.
SECRET_FIELD = re.compile(r"(?i)\b(pin|passw\w*|passcode|otp|one[- ]time|2fa|secret|token|api[ _-]?key|cvv|card number|seed phrase)\b")


class AskError(ValueError):
    """The spec or answer is refused, in words for the model or the owner."""


def plain(value: Any, limit: int) -> str:
    """Text that is shown to the owner: one line, no control, invisible or direction characters, cut to `limit`."""
    return " ".join(INVISIBLE.sub(" ", str(value if value is not None else "")).split())[:limit]


def block(value: Any, limit: int) -> str:
    """Text with line breaks kept (code, a diff, a long answer): control and direction characters other than newline and tab are removed."""
    text = str(value if value is not None else "").replace("\r\n", "\n").replace("\r", "\n")
    return INVISIBLE.sub(lambda m: m.group(0) if m.group(0) in "\n\t" else " ", text)[:limit]


def number(value: Any, default: float | None = None) -> float | None:
    if isinstance(value, bool):
        return default
    try:
        n = float(value)
    except (TypeError, ValueError):
        return default
    return n if n == n and abs(n) < 1e15 else default


# ---- known folders (the only source of file_choice and gallery) -------------------------------------------------------------------------

def known_folder(name: str, home: Path | None = None) -> Path:
    return (home or Path.home()) / KNOWN_FOLDERS[name]


def save_folders(home: Path | None = None) -> list[tuple[str, Path]]:
    """Where JARVIS may save to: the owner's four known folders and JARVIS's own deliverables folder (only those that exist)."""
    out = [(KNOWN_FOLDERS[k], known_folder(k, home)) for k in KNOWN_FOLDERS]
    try:
        from jarvis.bridge.output_preview import default_root
        out.append(("JARVIS outputs", default_root()))
    except Exception:  # noqa: BLE001 - no deliverables plugin: the four folders stand
        pass
    return [(label, p) for label, p in out if p.is_dir()]


def _thumb(path: Path, size: int) -> dict | None:
    """A small picture of an image file as {mime, data}: shrunk with Pillow when it is there, the file itself when it is tiny."""
    import base64
    import io
    ext = path.suffix.lower()
    if ext not in IMAGE_EXT or size > 12 * 1024 * 1024:
        return None
    try:
        from PIL import Image
        with Image.open(path) as im:
            im.thumbnail((THUMB_PX, THUMB_PX))
            buf = io.BytesIO()
            im.convert("RGB").save(buf, "JPEG", quality=70)
            return {"mime": "image/jpeg", "data": base64.b64encode(buf.getvalue()).decode("ascii")}
    except Exception:  # noqa: BLE001 - not decodable, or no Pillow
        if size <= THUMB_RAW_MAX and ext != ".webp":
            try:
                return {"mime": {".png": "image/png", ".gif": "image/gif"}.get(ext, "image/jpeg"), "data": base64.b64encode(path.read_bytes()).decode("ascii")}
            except OSError:
                return None
        return None


def _file_rows(folder: Path, limit: int, images_only: bool = False) -> list[tuple[float, str, int, Path]]:
    rows: list[tuple[float, str, int, Path]] = []
    try:
        names = os.listdir(folder)
    except OSError:
        return rows
    for name in names:
        if name.startswith(".") or SECRET_NAME.search(name) or (images_only and not name.lower().endswith(IMAGE_EXT)):
            continue
        p = folder / name
        try:
            st = os.lstat(p)
        except OSError:
            continue
        if not stat.S_ISREG(st.st_mode) or getattr(st, "st_file_attributes", 0) & 0x400:
            continue                                                  # a link, a junction, a folder or a device is not a file to pick
        rows.append((st.st_mtime, plain(name, 80), st.st_size, p))
    rows.sort(key=lambda r: r[0], reverse=True)
    return rows[:limit]


def _day_range(day: str, now: float | None = None) -> tuple[float, float] | None:
    """(start, end) epoch seconds of a local day: today, yesterday, last_week (the 7 days before today) or YYYY-MM-DD. None when it cannot be read."""
    import datetime as _dt
    today = _dt.datetime.fromtimestamp(now if now is not None else __import__("time").time()).replace(hour=0, minute=0, second=0, microsecond=0)
    day = str(day or "").strip().lower()
    if day == "today":
        a, b = today, today + _dt.timedelta(days=1)
    elif day == "yesterday":
        a, b = today - _dt.timedelta(days=1), today
    elif day == "last_week":
        a, b = today - _dt.timedelta(days=7), today
    elif re.fullmatch(r"\d{4}-\d{2}-\d{2}", day):
        try:
            a = _dt.datetime.strptime(day, "%Y-%m-%d")
        except ValueError:
            return None
        b = a + _dt.timedelta(days=1)
    else:
        return None
    return a.timestamp(), b.timestamp()


def _apply_filter(rows: list, flt: dict | None, now: float | None = None) -> list:
    """A BACKEND-side narrowing of a file list (D74): the caller may say which names, which day, which extensions, oldest first. It never adds a file: it only
    chooses among the ones the backend found in the known folders. rows: (mtime, name, size, path)."""
    if not flt:
        return rows
    words = [str(w).lower() for w in flt.get("name_like") or [] if str(w).strip()][:6]
    exts = [("." + str(e).lower().lstrip(".")) for e in flt.get("ext") or [] if str(e).strip()][:8]
    span = _day_range(flt["day"], now) if flt.get("day") else None
    if words:
        rows = [r for r in rows if any(w in r[1].lower() for w in words)]
    if exts:
        rows = [r for r in rows if r[1].lower().endswith(tuple(exts))]
    if span:
        rows = [r for r in rows if span[0] <= r[0] < span[1]]
    rows = sorted(rows, key=lambda r: r[0], reverse=not flt.get("oldest"))
    return rows


def file_options(source: str, home: Path | None = None, limit: int = RECENT_FILE_LIMIT, thumbs: bool = False, flt: dict | None = None) -> list[dict]:
    """What a file_choice or gallery lists: the newest plain files of a known folder (`recent` = Downloads, Documents and Desktop together,
    `folders` = the save folders). `path` stays on the server; the panel answers with the option id. `flt` (D74): name words, a day, extensions, oldest first."""
    if source == "folders":
        return [{"id": f"d{i}", "label": label, "detail": plain(p, 100), "path": str(p)} for i, (label, p) in enumerate(save_folders(home))]
    folders = [known_folder(k, home) for k in ("downloads", "documents", "desktop")] if source == "recent" else [known_folder(source, home)]
    rows: list[tuple[float, str, int, Path]] = []
    for folder in folders:
        rows += _file_rows(folder, 400 if flt else limit, images_only=thumbs and source != "recent")
    rows.sort(key=lambda r: r[0], reverse=True)
    rows = _apply_filter(rows, flt)
    out, budget = [], THUMBS_TOTAL_MAX
    for i, (_, name, size, p) in enumerate(rows[:limit]):
        o: dict[str, Any] = {"id": f"f{i}", "label": name, "detail": p.parent.name, "size": size, "path": str(p)}
        if thumbs and budget > 0:
            t = _thumb(p, size)
            if t:
                budget -= len(t["data"])
                o["thumb"] = t
        out.append(o)
    return out


# ---- cleaning one spec ----------------------------------------------------------------------------------------------------------------------

class _Ctx:
    def __init__(self, home: Path | None):
        self.home, self.ids, self.nodes, self.chars, self.notes = home, set(), 0, 0, []

    def text(self, value: Any, limit: int) -> str:
        t = plain(value, limit)
        self.chars += len(t)
        return t

    def note(self, text: str) -> None:
        if len(self.notes) < 6 and text not in self.notes:
            self.notes.append(text)


def _option(raw: Any, seen: set, ctx: _Ctx, backend: bool = False) -> dict | None:
    if not isinstance(raw, dict):
        ctx.note("An option was not in the right shape and was left out.")
        return None
    oid = str(raw.get("id", ""))
    if not OPTION_ID_RE.fullmatch(oid) or oid in seen:
        ctx.note("An option had a missing or repeated id and was left out.")
        return None
    seen.add(oid)
    out: dict[str, Any] = {"id": oid, "label": ctx.text(raw.get("label") or oid, 100)}
    for key, limit in (("detail", 160), ("badge", 24), ("group", 60)):
        if raw.get(key):
            out[key] = ctx.text(raw[key], limit)
    if raw.get("body"):
        out["body"] = block(raw["body"], 4000)
        ctx.chars += len(out["body"])
    size = raw.get("size")
    if isinstance(size, str):
        out["size"] = plain(size, 24)
    elif number(size) is not None and number(size) >= 0:
        out["size"] = int(number(size))
    if raw.get("checked") is True:
        out["checked"] = True
    for key in ("pros", "cons"):
        if isinstance(raw.get(key), list):
            out[key] = [ctx.text(x, 100) for x in raw[key][:5] if x]
    if isinstance(raw.get("items"), list):
        out["items"] = [{"label": ctx.text(s.get("label"), 100), **({"size": int(number(s.get("size"), 0) or 0)} if number(s.get("size")) is not None else {})}
                        for s in raw["items"][:MAX_SUBITEMS] if isinstance(s, dict) and s.get("label")]
    if backend:                                                       # only the backend sets these; a caller's spec cannot
        if raw.get("path"):
            out["path"] = str(raw["path"])
        if isinstance(raw.get("thumb"), dict):
            out["thumb"] = raw["thumb"]
    return out


def _options(raw: Any, ctx: _Ctx) -> list[dict]:
    seen: set = set()
    given = raw if isinstance(raw, list) else []
    if len(given) > MAX_OPTIONS:
        ctx.note(f"Only the first {MAX_OPTIONS} items are shown.")
    return [o for o in (_option(x, seen, ctx) for x in given[:MAX_OPTIONS]) if o]


def _fallback(raw: Any, ctx: _Ctx, why: str) -> dict:
    """A part that cannot be drawn as asked: its labels as a plain list, and a note."""
    ctx.note(why)
    rows: list[list[str]] = []
    if isinstance(raw, dict):
        if raw.get("label"):
            rows.append([ctx.text(raw["label"], 120), ""])
        for o in (raw.get("options") or raw.get("items") or [])[:20] if isinstance(raw.get("options") or raw.get("items"), list) else []:
            if isinstance(o, dict) and (o.get("label") or o.get("id")):
                rows.append([ctx.text(o.get("label") or o.get("id"), 100), ctx.text(o.get("detail"), 100)])
        if raw.get("text"):
            rows.append([ctx.text(raw["text"], 300), ""])
    return {"type": "info_table", "label": "", "rows": rows}


def _node_id(raw: dict, ctx: _Ctx, needed: bool) -> str | None:
    nid = str(raw.get("id", ""))
    if nid:
        if not ID_RE.fullmatch(nid) or nid in ctx.ids:
            return None
        ctx.ids.add(nid)
        return nid
    return None


def _children(raw: Any, ctx: _Ctx, depth: int) -> list[dict]:
    items = raw if isinstance(raw, list) else []
    return [n for n in (clean_node(x, ctx, depth) for x in items) if n]


def clean_node(raw: Any, ctx: _Ctx, depth: int = 0) -> dict | None:
    if ctx.nodes >= MAX_NODES:
        ctx.note("The form was cut because it was too big.")
        return None
    if not isinstance(raw, dict):
        ctx.note("A part of the form was not in the right shape and was left out.")
        return None
    ctx.nodes += 1
    ntype = str(raw.get("type", ""))
    if ntype not in TYPES:
        return _fallback(raw, ctx, f"A part ({plain(ntype, 20) or 'unnamed'}) is shown as a plain list.")
    if depth > MAX_DEPTH and ntype in LAYOUT:
        return _fallback(raw, ctx, "A part was nested too deeply and is shown as a plain list.")
    label = ctx.text(raw.get("label"), 120)
    if ntype in INPUTS and SECRET_FIELD.search(label + " " + plain(raw.get("placeholder"), 80)):
        raise AskError("a form never asks for a PIN, password or code; approvals happen in the JARVIS console")
    out: dict[str, Any] = {"type": ntype, "label": label}
    if ntype in INPUTS:
        nid = _node_id(raw, ctx, True)
        if nid is None:
            return _fallback(raw, ctx, "A choice had a missing or repeated id and is shown as a plain list.")
        out["id"] = nid
        if raw.get("required") is True:
            out["required"] = True
    elif raw.get("id"):
        out["id"] = plain(raw["id"], 32)
    if raw.get("hint"):
        out["hint"] = ctx.text(raw["hint"], 160)
    d = depth + 1
    t = ntype
    if t in ("section", "row", "column", "grid"):
        out["children"] = _children(raw.get("children"), ctx, d)
        if t == "section" and raw.get("collapsed") is True:
            out["collapsed"] = True
    elif t == "tabs":
        tabs, tseen = [], set()
        for tab in (raw.get("tabs") or [])[:6] if isinstance(raw.get("tabs"), list) else []:
            if isinstance(tab, dict) and OPTION_ID_RE.fullmatch(str(tab.get("id", ""))) and str(tab["id"]) not in tseen:
                tseen.add(str(tab["id"]))
                tabs.append({"id": str(tab["id"]), "label": ctx.text(tab.get("label") or tab["id"], 40), "children": _children(tab.get("children"), ctx, d)})
        if not tabs:
            return _fallback(raw, ctx, "Tabs without content are left out.")
        out["tabs"] = tabs
    elif t in ("checkbox_group", "radio", "select", "cards", "split"):
        options = _options(raw.get("options"), ctx)
        if not options:
            return _fallback(raw, ctx, "A choice without options is left out.")
        single = t != "checkbox_group" and not (t == "cards" and raw.get("multiple") is True)
        if single:                                                    # one choice: keep at most the first one that is ticked
            first = next((o["id"] for o in options if o.get("checked")), None)
            for o in options:
                o.pop("checked", None)
            if first:
                next(o for o in options if o["id"] == first)["checked"] = True
        out["options"] = options
        if t == "radio":
            out["variant"] = "segmented" if raw.get("variant") == "segmented" and len(options) <= 5 else "list"
        if t == "cards":
            out["multiple"] = raw.get("multiple") is True
        if t == "checkbox_group" and raw.get("collapsed") is True:
            out["collapsed"] = True
    elif t in ("file_choice", "gallery"):
        source = str(raw.get("source") or ("pictures" if t == "gallery" else "recent"))
        if source not in FILE_SOURCES:
            return _fallback(raw, ctx, "A file list asked for a place that is not allowed.")
        out["source"] = source
        flt = raw.get("filter") if isinstance(raw.get("filter"), dict) else None
        listed = file_options(source, ctx.home, GALLERY_LIMIT if t == "gallery" else RECENT_FILE_LIMIT, thumbs=True, flt=flt)    # the caller's own options are ignored
        if flt and not listed:                                        # D74: nothing matched the description: the newest files, said so (never an empty choice)
            listed = file_options(source, ctx.home, GALLERY_LIMIT if t == "gallery" else RECENT_FILE_LIMIT, thumbs=True)
            ctx.note("No file matched what was described, so these are the newest files.")
        out["options"] = [o for o in (_option(x, set(), ctx, backend=True) for x in listed) if o]
        out["multiple"] = t == "gallery" and raw.get("multiple") is True
    elif t == "text":
        out.update(max_len=int(min(max(number(raw.get("max_len"), 200) or 200, 1), 2000)), multiline=raw.get("multiline") is True)
        if raw.get("heading") is True and not out["multiline"]:     # D63: an editable heading (a deck title, a file name, a reminder text); still plain text
            out["heading"] = True
        if raw.get("placeholder"):
            out["placeholder"] = ctx.text(raw["placeholder"], 80)
        if raw.get("value"):
            out["value"] = block(raw["value"], out["max_len"]) if out["multiline"] else plain(raw["value"], out["max_len"])
    elif t in ("number", "slider"):
        lo, hi = number(raw.get("min"), 0), number(raw.get("max"), 100 if t == "slider" else 1_000_000)
        if lo > hi:
            lo, hi = hi, lo
        out.update(min=lo, max=hi, step=abs(number(raw.get("step"), 1) or 1), value=min(max(number(raw.get("value"), lo), lo), hi))
        if raw.get("unit"):
            out["unit"] = ctx.text(raw["unit"], 12)
    elif t == "toggle":
        out["value"] = raw.get("value") is True
    elif t == "date":
        mode = str(raw.get("mode") or "date")
        mode = mode if mode in DATE_RES else "date"
        out["mode"] = mode
        value = str(raw.get("value") or "")
        if value:
            if DATE_RES[mode].fullmatch(value):
                out["value"] = value
            else:
                ctx.note("A date had the wrong format and was left empty.")
    elif t == "table":
        cols = [{"id": str(c.get("id")), "label": ctx.text(c.get("label") or c.get("id"), 40), "align": "right" if c.get("align") == "right" else "left"}
                for c in (raw.get("columns") or [])[:MAX_COLS] if isinstance(c, dict) and OPTION_ID_RE.fullmatch(str(c.get("id", "")))]
        if not cols:
            return _fallback(raw, ctx, "A table without columns is left out.")
        seen: set = set()
        rows = []
        for r in (raw.get("rows") or [])[:MAX_ROWS] if isinstance(raw.get("rows"), list) else []:
            if isinstance(r, dict) and OPTION_ID_RE.fullmatch(str(r.get("id", ""))) and str(r["id"]) not in seen:
                seen.add(str(r["id"]))
                cells = r.get("cells") if isinstance(r.get("cells"), dict) else {}
                rows.append({"id": str(r["id"]), "cells": {c["id"]: ctx.text(cells.get(c["id"]), 100) for c in cols}})
        select = str(raw.get("select") or "none")
        out.update(columns=cols, rows=rows, select=select if select in ("none", "one", "many") else "none", sortable=raw.get("sortable") is not False)
    elif t == "info_table":
        rows = raw.get("rows") if isinstance(raw.get("rows"), list) else []
        out["rows"] = [[ctx.text(c, 160) for c in r[:3]] for r in rows[:MAX_ROWS] if isinstance(r, (list, tuple)) and len(r) >= 2]
    elif t == "chart":
        kind = str(raw.get("kind") or "bar")
        labels = [ctx.text(x, 30) for x in (raw.get("labels") or [])[:MAX_POINTS]] if isinstance(raw.get("labels"), list) else []
        series = []
        for s in (raw.get("series") or [])[:MAX_SERIES] if isinstance(raw.get("series"), list) else []:
            if isinstance(s, dict) and isinstance(s.get("values"), list):
                vals = [number(v, 0.0) for v in s["values"][:MAX_POINTS]]
                series.append({"name": ctx.text(s.get("name") or "", 40), "values": vals})
        if not series or not labels:
            return _fallback(raw, ctx, "A chart without numbers is left out.")
        out.update(kind=kind if kind in ("bar", "line", "pie") else "bar", labels=labels[:max(len(x["values"]) for x in series)], series=series)
        if raw.get("unit"):
            out["unit"] = ctx.text(raw["unit"], 12)
    elif t == "code_view":
        out.update(text=block(raw.get("text"), MAX_CODE), language=plain(raw.get("language") or "text", 20))
        ctx.chars += len(out["text"])
    elif t == "diff_view":
        out.update(before=block(raw.get("before"), MAX_CODE), after=block(raw.get("after"), MAX_CODE))
        ctx.chars += len(out["before"]) + len(out["after"])
    elif t == "steps":
        out["items"] = [{"label": ctx.text(s.get("label"), 100), "status": s.get("status") if s.get("status") in STEP_STATUS else "pending",
                         **({"detail": ctx.text(s["detail"], 120)} if s.get("detail") else {})}
                        for s in (raw.get("items") or [])[:MAX_STEPS] if isinstance(s, dict) and s.get("label")] if isinstance(raw.get("items"), list) else []
    elif t == "progress":
        mx = number(raw.get("max"), 100) or 100
        val = number(raw.get("value"))
        out.update(max=mx, value=None if val is None else min(max(val, 0), mx), indeterminate=val is None)
        if raw.get("unit"):
            out["unit"] = ctx.text(raw["unit"], 12)
    elif t == "callout":
        out.update(level="warn" if raw.get("level") == "warn" else "info", text=ctx.text(raw.get("text") or raw.get("label"), 2000))
    return out


def clean_spec(spec: Any, home: Path | None = None) -> dict:
    """The spec the panel may draw. Forgiving (see the module text); raises AskError only for a spec that is not an object, an empty one,
    or one that asks for a PIN, password or code."""
    if not isinstance(spec, dict):
        raise AskError("spec must be an object {title, intro, fields, buttons}")
    ctx = _Ctx(home)
    given = spec.get("fields") if isinstance(spec.get("fields"), list) else []
    fields = [n for n in (clean_node(f, ctx, 0) for f in given) if n]
    buttons, bseen = [], set()
    for b in (spec.get("buttons") or [])[:MAX_BUTTONS] if isinstance(spec.get("buttons"), list) else []:
        bid = str(b.get("id", "")) if isinstance(b, dict) else ""
        if not ID_RE.fullmatch(bid) or bid in bseen:
            ctx.note("A button had a missing or repeated id and was left out.")
            continue
        bseen.add(bid)
        style = str(b.get("style") or "secondary")
        buttons.append({"id": bid, "label": plain(b.get("label") or bid, 40), "style": style if style in BUTTON_STYLES else "secondary"})
    intro = plain(spec.get("intro"), 400)
    if not fields and not intro:
        raise AskError("a form needs at least a field or an intro")
    if not buttons:
        buttons = [{"id": "ok", "label": "OK", "style": "primary"}, {"id": "cancel", "label": "Cancel", "style": "secondary"}]
    timeout = number(spec.get("timeout_s"), DEFAULT_TIMEOUT_S)
    title = plain(spec.get("title") or "JARVIS asks", 80)
    if SECRET_FIELD.search(title + " " + intro) and any(n["type"] in ("text", "number") for n in iter_inputs(fields)):
        raise AskError("a form never asks for a PIN, password or code; approvals happen in the JARVIS console")
    if ctx.chars > MAX_CHARS:
        raise AskError(f"the form is too long ({ctx.chars} characters; the limit is {MAX_CHARS})")
    out: dict[str, Any] = {"title": title, "intro": intro, "fields": fields, "buttons": buttons, "timeout_s": int(min(max(timeout, 5), MAX_TIMEOUT_S))}
    if ctx.notes:
        out["notes"] = ctx.notes
    return out


def iter_nodes(nodes: list) -> Any:
    for n in nodes:
        yield n
        for c in n.get("children") or []:
            yield from iter_nodes([c])
        for tab in n.get("tabs") or []:
            yield from iter_nodes(tab.get("children") or [])


def iter_inputs(nodes: list) -> Any:
    return (n for n in iter_nodes(nodes) if n.get("type") in INPUTS)


def _multi(n: dict) -> bool:
    t = n["type"]
    return t == "checkbox_group" or (t in ("cards", "gallery") and n.get("multiple")) or (t == "table" and n.get("select") == "many")


def _ids(n: dict) -> set:
    return {r["id"] for r in n["rows"]} if n["type"] == "table" else {o["id"] for o in n.get("options") or []}


def _initial(n: dict) -> Any:
    t = n["type"]
    if t in ("checkbox_group", "radio", "select", "cards", "split", "file_choice", "gallery"):
        picked = [o["id"] for o in n["options"] if o.get("checked")]
        return picked if _multi(n) else (picked[0] if picked else "")
    if t == "table":
        return [] if n["select"] == "many" else ""
    return n.get("value", False if t == "toggle" else "")


def default_values(spec: dict) -> dict:
    return {n["id"]: _initial(n) for n in iter_inputs(spec["fields"])}


def validate_answer(spec: dict, button: str, values: Any) -> dict:
    """The owner's answer checked against the spec: ids must exist, numbers are clamped, text is cut. Returns {input id: value}; a file or
    gallery answer is still the option id (the broker turns it into the path). A primary button also needs every required input."""
    if button not in {b["id"] for b in spec["buttons"]}:
        raise AskError("that button is not on the form")
    values = values if isinstance(values, dict) else {}
    out: dict[str, Any] = {}
    strict = any(b["id"] == button and b["style"] == "primary" for b in spec["buttons"])
    for n in iter_inputs(spec["fields"]):
        nid, t = n["id"], n["type"]
        raw = values.get(nid, _initial(n))
        if t in ("checkbox_group", "radio", "select", "cards", "split", "file_choice", "gallery", "table"):
            ids = _ids(n)
            if _multi(n):
                picked = [str(x) for x in (raw if isinstance(raw, list) else [])]
                if any(x not in ids for x in picked):
                    raise AskError(f"{nid}: an item that is not on the form")
                order = [x for x in (n["rows"] if t == "table" else n["options"])]
                value: Any = [x["id"] for x in order if x["id"] in picked]
            else:
                value = str(raw or "")
                if value and value not in ids:
                    raise AskError(f"{nid}: an item that is not on the form")
        elif t == "text":
            value = block(raw, n["max_len"]) if n.get("multiline") else plain(raw, n["max_len"])
        elif t in ("number", "slider"):
            v = number(raw)
            if v is None and raw not in ("", None):
                raise AskError(f"{nid}: not a number")
            value = None if v is None else min(max(v, n["min"]), n["max"])
        elif t == "toggle":
            value = raw is True
        else:                                                         # date
            value = str(raw or "")
            if value and not DATE_RES[n["mode"]].fullmatch(value):
                raise AskError(f"{nid}: wrong date format")
        if strict and n.get("required") and value in ("", None, []):
            raise AskError(f"{n['label'] or nid} needs an answer")
        out[nid] = value
    return out


def paths_of(spec: dict) -> dict[str, dict[str, str]]:
    """{input id: {option id: path}} for the file_choice and gallery inputs (server side only)."""
    return {n["id"]: {o["id"]: o["path"] for o in n["options"] if "path" in o} for n in iter_inputs(spec["fields"]) if n["type"] in ("file_choice", "gallery")}


def for_panel(spec: dict) -> dict:
    """The spec for the panel: the same, without the server-side paths of file options (the panel answers with the option id)."""
    def strip(n: dict) -> dict:
        n = dict(n)
        if n.get("type") in ("file_choice", "gallery"):
            n["options"] = [{k: v for k, v in o.items() if k != "path"} for o in n["options"]]
        if n.get("children"):
            n["children"] = [strip(c) for c in n["children"]]
        if n.get("tabs"):
            n["tabs"] = [{**t, "children": [strip(c) for c in t["children"]]} for t in n["tabs"]]
        return n
    return {**spec, "fields": [strip(n) for n in spec["fields"]]}
