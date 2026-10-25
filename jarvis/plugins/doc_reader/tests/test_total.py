"""The total of an invoice, found locally and shown on a card, never sent to a model (2026-10-25). Fake invoices only."""
from __future__ import annotations

import json

import pytest

from jarvis.pluginapi import Ok
from jarvis.plugins._shared import screen_text
from jarvis.plugins.doc_reader import DocReaderPlugin, core

HTML_TABLE = """<table><thead><tr><th>Item</th><th>Qty</th><th>Unit price</th><th>Amount</th></tr></thead><tbody>
<tr><td>Design hours</td><td>6</td><td>45.00</td><td>270.00</td></tr><tr><td>Hosting</td><td>1</td><td>19.50</td><td>19.50</td></tr></tbody></table>

Subtotal: 289.50 USD

Tax (8%): 23.16 USD

Total due: 312.66 USD

Payment terms: 14 days"""


def total(*pages):
    return core.find_total(list(pages))


def test_a_total_due_line_under_an_html_table():
    assert total(HTML_TABLE) == {"label": "Total due", "amount": "312.66", "currency": "USD"}


@pytest.mark.parametrize("text, label, amount, currency", [
    ("Subtotal 100.00\nVAT 20.00\nTotal 120.00", "Total", "120.00", ""),
    ("Grand Total: $1,234.56", "Grand Total", "1234.56", "USD"),
    ("Amount due   EUR 1.234,56", "Amount due", "1234.56", "EUR"),
    ("Total amount payable: ₹ 12,345.00", "Amount payable", "12345.00", "INR"),
    ("| Total | | | 99.90 € |", "Total", "99.90", "EUR"),
    ("Balance due: 1 234,50 GBP", "Balance due", "1234.50", "GBP"),
    ("Invoice total: Rs. 5,000", "Invoice total", "5000", "INR"),
    ("<tr><td>Total</td><td>£48.00</td></tr>", "Total", "48.00", "GBP"),
])
def test_labels_and_number_formats(text, label, amount, currency):
    got = total(text)
    assert got is not None and got["label"].lower() == label.lower() and got["amount"] == amount and got["currency"] == currency


def test_a_subtotal_a_tax_line_a_percentage_or_a_quantity_never_gives_a_total():
    for text in ("Subtotal: 100.00", "Sub-total 100.00", "Total tax: 8.00", "Total before tax 90.00", "Total items: 12", "Tax (8%): 24.14", "Total discount 5.00",
                 "Total hours 40", "Previous balance 100.00", "Invoice 2026-0417", "no amounts here"):
        assert total(text) is None, text


def test_the_strongest_label_wins_and_among_equals_the_last_one():
    assert total("Total 10.00\nGrand total 99.00\nTotal 20.00")["amount"] == "99.00"
    assert total("Total due 10.00", "Total due 15.00")["amount"] == "15.00"
    assert total("Total 50.00\nAmount due 40.00")["amount"] == "40.00"           # a due line beats a plain total


def test_a_zero_or_a_garbled_amount_is_not_a_total():
    assert total("Total due: 0.00") is None
    assert total("Total due: see below") is None


def test_the_card_is_written_where_the_preview_panel_lists_it(tmp_path_factory):
    from jarvis.bridge.output_preview import OutputPreview
    tmp_path = tmp_path_factory.mktemp("panel")
    folder = core.write_total_card({"label": "Total due", "amount": "312.66", "currency": "USD"}, tmp_path)
    assert (folder / "invoice-total.png").read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"
    qa = json.loads((folder / "qa.json").read_text(encoding="utf-8"))
    assert qa["qa"]["passed"] is True and qa["kind"] == "image"
    items = OutputPreview(tmp_path, auto=False).list(5)["items"]
    assert [(i["title"], i["kind"], i["passed"]) for i in items] == [("Invoice total", "image", True)]


async def test_the_reply_says_the_total_is_on_screen_and_never_carries_the_amount(scan_png, _cards_go_to_a_temp_folder, monkeypatch):
    monkeypatch.delenv(screen_text.FLAG, raising=False)
    plugin = object.__new__(DocReaderPlugin)
    plugin.transcribe = lambda png: HTML_TABLE
    res = await plugin.read_document(path=scan_png)
    assert isinstance(res, Ok)
    v = res.value
    assert "on the owner's screen" in v["note"]
    blob = json.dumps(v)
    for value in ("312.66", "289.50", "23.16", "270.00", "Design hours"):
        assert value not in blob, value
    assert "text" not in v and "stayed on this PC" in v["text_note"]
    cards = [p for p in _cards_go_to_a_temp_folder.iterdir() if p.name.endswith("invoice-total")]
    assert len(cards) == 1


async def test_a_page_without_a_total_makes_no_card(scan_png, _cards_go_to_a_temp_folder, monkeypatch):
    monkeypatch.delenv(screen_text.FLAG, raising=False)
    plugin = object.__new__(DocReaderPlugin)
    plugin.transcribe = lambda png: "Dear Sample Customer, thank you for your order."
    res = await plugin.read_document(path=scan_png)
    assert "owner's screen" not in res.value.get("note", "")
    assert list(_cards_go_to_a_temp_folder.iterdir()) == []


@pytest.fixture()
def scan_png(tmp_path_factory):
    from PIL import Image
    d = tmp_path_factory.mktemp("d").resolve()
    Image.new("RGB", (300, 200), "white").save(d / "invoice.png")
    return str(d / "invoice.png")
