"""Tests for jarvis.muse.pdfforms using reportlab-built forms."""
from __future__ import annotations

import dataclasses
import hashlib

import pytest
from reportlab.pdfgen import canvas

from jarvis.muse import pdfforms as pf


@pytest.fixture
def form(tmp_path):
    p = tmp_path / "form.pdf"
    c = canvas.Canvas(str(p))
    af = c.acroForm
    af.textfield(name="name", x=50, y=700, width=200, height=20, maxlen=10)
    af.textfield(name="city", x=50, y=660, width=200, height=20,
                 fieldFlags="required")
    af.checkbox(name="agree", x=50, y=620, size=20)
    af.radio(name="size", value="S", x=50, y=580, selected=False)
    af.radio(name="size", value="L", x=100, y=580, selected=False)
    af.choice(name="color", value="red", options=["red", "green"], x=50, y=540,
              width=100, height=20)
    c.showPage()
    c.save()
    return p


def test_inspect_types(form):
    f = {x.name: x for x in pf.inspect(form)}
    assert f["name"].type == "text" and f["name"].max_length == 10
    assert f["city"].required and not f["name"].required
    assert f["agree"].type == "checkbox"
    assert f["size"].type == "radio" and set(f["size"].options) == {"S", "L"}
    assert f["color"].type == "choice" and f["color"].options == ["red", "green"]
    assert f["name"].page == 1


def test_no_form(tmp_path):
    p = tmp_path / "n.pdf"
    c = canvas.Canvas(str(p))
    c.drawString(10, 10, "hi")
    c.save()
    assert pf.inspect(p) == []


def test_non_pdf_and_corrupt():
    with pytest.raises(pf.BadPdf) as e:
        pf.inspect(b"secret text here")
    assert "secret" not in str(e.value)
    with pytest.raises(pf.BadPdf):
        pf.inspect(b"%PDF-1.4\ngarbage garbage")


def test_too_large():
    with pytest.raises(pf.BadPdf):
        pf.inspect(b"%PDF-" + b"0" * (pf.MAX_BYTES + 1))


def test_plan_errors(form):
    fields = pf.inspect(form)
    plan = pf.plan_fill(fields, {"agree": "banana", "size": "XL",
                                 "name": "x" * 50, "city": {"a": 1}, "nope": 1})
    assert not plan.ok
    assert {e["field"] for e in plan.errors} == {"agree", "size", "name", "city"}
    assert plan.unknown == ["nope"]


def test_plan_ok_and_required(form):
    fields = pf.inspect(form)
    plan = pf.plan_fill(fields, {"name": 42, "agree": True, "size": "L"})
    assert plan.ok and plan.preview["name"] == "42"
    assert plan.missing_required == ["city"]


def test_fill_requires_approval(form, tmp_path):
    with pytest.raises(pf.NotApproved):
        pf.fill(form, {"city": "x"}, tmp_path / "o.pdf", approved=False)
    assert not (tmp_path / "o.pdf").exists()


def test_fill_success(form, tmp_path):
    before = hashlib.sha256(form.read_bytes()).hexdigest()
    out = tmp_path / "o.pdf"
    r = pf.fill(form, {"city": "Oslo", "name": "Bob", "agree": True,
                       "size": "L", "color": "green"}, out, approved=True)
    assert hashlib.sha256(form.read_bytes()).hexdigest() == before
    assert r.sha256_in == before
    assert r.sha256_out == hashlib.sha256(out.read_bytes()).hexdigest()
    got = {f.name: f.value for f in pf.inspect(out)}
    assert got["city"] == "Oslo" and got["name"] == "Bob"
    assert got["size"] == "L" and got["color"] == "green"
    assert got["agree"] not in (None, "", "Off")
    blob = repr(dataclasses.asdict(r))
    assert "Oslo" not in blob and "Bob" not in blob
    assert sorted(r.fields_written) == ["agree", "city", "color", "name", "size"]


def test_fill_refusals(form, tmp_path):
    out = tmp_path / "o.pdf"
    with pytest.raises(pf.FillRefused):
        pf.fill(form, {"name": "Bob"}, out, approved=True)  # missing required
    pf.fill(form, {"name": "Bob"}, out, approved=True, allow_missing=True)
    with pytest.raises(pf.FillRefused):
        pf.fill(form, {"city": "x"}, out, approved=True)  # exists
    with pytest.raises(pf.FillRefused):
        pf.fill(form, {"city": "x", "name": "y" * 50}, tmp_path / "p.pdf",
                approved=True)
    with pytest.raises(pf.FillRefused):
        pf.fill(form, {"city": "x"}, form, approved=True)  # input never overwritten
