"""PDF form inspect / plan / fill with type checks and an owner-approval gate.

inspect() lists AcroForm fields, plan_fill() validates values without touching
any file, and fill() writes a filled copy only when explicitly approved.
Receipts and error messages never contain field values or file text.
"""
from __future__ import annotations

import hashlib
import io
import os
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from pypdf import PdfReader, PdfWriter
from pypdf.generic import BooleanObject, NameObject

MAX_BYTES = 20 * 1024 * 1024


class BadPdf(Exception):
    """Input is too large, not a PDF, or unreadable."""


class NotApproved(Exception):
    """fill() was called without owner approval."""


class FillRefused(Exception):
    """Plan is not ok, or output path problem."""


@dataclass
class Field:
    name: str
    type: str
    value: Any = None
    options: list[str] = field(default_factory=list)
    required: bool = False
    max_length: int | None = None
    page: int | None = None


@dataclass
class FillPlan:
    ok: bool
    errors: list[dict] = field(default_factory=list)
    unknown: list[str] = field(default_factory=list)
    missing_required: list[str] = field(default_factory=list)
    preview: dict = field(default_factory=dict)


@dataclass
class Receipt:
    path: str
    sha256_in: str
    sha256_out: str
    fields_written: list[str]
    when: str


def _load_bytes(src: "str | os.PathLike | bytes") -> bytes:
    if isinstance(src, (bytes, bytearray)):
        data = bytes(src)
    else:
        try:
            if os.path.getsize(src) > MAX_BYTES:
                raise BadPdf("file too large")
            with open(src, "rb") as f:
                data = f.read(MAX_BYTES + 1)
        except OSError:
            raise BadPdf("cannot read file") from None
    if len(data) > MAX_BYTES:
        raise BadPdf("file too large")
    if b"%PDF-" not in data[:1024]:
        raise BadPdf("not a PDF")
    return data


def _reader(data: bytes) -> PdfReader:
    try:
        return PdfReader(io.BytesIO(data))
    except Exception:
        raise BadPdf("unreadable PDF") from None


def _clean(v: Any) -> Any:
    if v is None:
        return None
    if isinstance(v, list):
        return [_clean(x) for x in v]
    s = str(v)
    return s[1:] if s.startswith("/") else s


def _inherited(node: Any, key: str) -> Any:
    while node is not None:
        if key in node:
            return node[key]
        node = node.get("/Parent")
        node = node.get_object() if node is not None else None
    return None


def _fq_name(node: Any) -> str:
    parts = []
    while node is not None:
        if "/T" in node:
            parts.append(str(node["/T"]))
        p = node.get("/Parent")
        node = p.get_object() if p is not None else None
    return ".".join(reversed(parts))


def _ap_states(node: Any) -> list[str]:
    try:
        return [str(k)[1:] for k in node["/AP"]["/N"].keys()]
    except Exception:
        return []


def _walk(node: Any, out: list) -> None:
    kids = [k.get_object() for k in node.get("/Kids", [])]
    if any("/T" in k for k in kids):
        for k in kids:
            _walk(k, out)
    elif "/T" in node:
        out.append((node, kids))


def inspect(src: "str | os.PathLike | bytes") -> list[Field]:
    data = _load_bytes(src)
    reader = _reader(data)
    try:
        form = reader.trailer["/Root"].get("/AcroForm")
        if form is None:
            return []
        form = form.get_object()
        leaves: list = []
        for f in form.get("/Fields", []):
            _walk(f.get_object(), leaves)
        pages: dict[str, int] = {}
        for i, page in enumerate(reader.pages):
            for a in page.get("/Annots", []) or []:
                pages.setdefault(_fq_name(a.get_object()), i + 1)
    except Exception:
        raise BadPdf("unreadable form") from None
    result = []
    for node, kids in leaves:
        name = _fq_name(node)
        ft = str(_inherited(node, "/FT") or "")
        ff = int(_inherited(node, "/Ff") or 0)
        options: list[str] = []
        if ft == "/Tx":
            typ = "text"
        elif ft == "/Btn":
            if ff & (1 << 16):
                typ = "button"
            elif ff & (1 << 15):
                typ = "radio"
                for w in kids or [node]:
                    options += [s for s in _ap_states(w)
                                if s != "Off" and s not in options]
            else:
                typ = "checkbox"
                options = [s for s in _ap_states(node) if s != "Off"]
        elif ft == "/Ch":
            typ = "choice"
            for o in _inherited(node, "/Opt") or []:
                o = o.get_object() if hasattr(o, "get_object") else o
                if isinstance(o, list):
                    o = o[0]
                options.append(str(o))
        elif ft == "/Sig":
            typ = "signature"
        else:
            typ = "unknown"
        ml = _inherited(node, "/MaxLen")
        result.append(Field(
            name=name, type=typ, value=_clean(_inherited(node, "/V")),
            options=options, required=bool(ff & 2),
            max_length=int(ml) if ml is not None else None,
            page=pages.get(name)))
    return result


def _empty(v: Any) -> bool:
    return v is None or v == "" or v == "Off"


def plan_fill(fields: list[Field], values: dict) -> FillPlan:
    by_name = {f.name: f for f in fields}
    plan = FillPlan(ok=True)
    for name, val in values.items():
        f = by_name.get(name)
        if f is None:
            plan.unknown.append(name)
            continue
        reason = None
        new = val
        if f.type == "text":
            if isinstance(val, bool) or not isinstance(val, (str, int, float)):
                reason = "text field takes a string or number"
            else:
                new = str(val)
                if f.max_length is not None and len(new) > f.max_length:
                    reason = f"value longer than max_length {f.max_length}"
        elif f.type == "checkbox":
            on = f.options[0] if f.options else "Yes"
            if isinstance(val, bool):
                new = on if val else "Off"
            elif isinstance(val, str) and val in (f.options + ["Off"]):
                new = val
            else:
                reason = "checkbox takes a bool or its on/off value"
        elif f.type in ("radio", "choice"):
            if isinstance(val, str) and val in f.options:
                new = val
            else:
                reason = f"{f.type} takes one of its options"
        else:
            reason = f"{f.type} field cannot be filled"
        if reason:
            plan.errors.append({"field": name, "reason": reason})
        else:
            plan.preview[name] = new
    for f in fields:
        if f.required and f.name not in values and _empty(f.value):
            plan.missing_required.append(f.name)
    plan.ok = not plan.errors and not plan.unknown
    return plan


def _sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def fill(src: "str | os.PathLike | bytes", values: dict,
         out_path: "str | os.PathLike", approved: bool,
         allow_missing: bool = False) -> Receipt:
    if approved is not True:
        raise NotApproved("owner approval required")
    data = _load_bytes(src)
    fields = inspect(data)
    plan = plan_fill(fields, values)
    if not plan.ok:
        raise FillRefused("plan not ok: %d error(s), %d unknown field(s)"
                          % (len(plan.errors), len(plan.unknown)))
    if plan.missing_required and not allow_missing:
        raise FillRefused("missing required fields: "
                          + ", ".join(plan.missing_required))
    out_path = os.fspath(out_path)
    if os.path.exists(out_path):
        raise FillRefused("output path already exists")

    writer = PdfWriter(clone_from=_reader(data))
    types = {f.name: f.type for f in fields}
    pagenum = {f.name: (f.page or 1) for f in fields}
    for name, val in plan.preview.items():
        v = ("/" + val) if types[name] in ("checkbox", "radio") else val
        writer.update_page_form_field_values(
            writer.pages[pagenum[name] - 1], {name: v}, auto_regenerate=False)
    writer._root_object["/AcroForm"][NameObject("/NeedAppearances")] = \
        BooleanObject(True)
    buf = io.BytesIO()
    writer.write(buf)
    out = buf.getvalue()

    got = {f.name: f.value for f in inspect(out)}
    for name, val in plan.preview.items():
        ok = _empty(got.get(name)) if (types[name] == "checkbox"
                                        and val == "Off") else got.get(name) == val
        if not ok:
            raise FillRefused("verification failed for field " + name)

    d = os.path.dirname(os.path.abspath(out_path))
    fd, tmp = tempfile.mkstemp(dir=d, suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(out)
        os.replace(tmp, out_path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise
    return Receipt(path=out_path, sha256_in=_sha(data), sha256_out=_sha(out),
                   fields_written=list(plan.preview),
                   when=datetime.now(timezone.utc).isoformat())
