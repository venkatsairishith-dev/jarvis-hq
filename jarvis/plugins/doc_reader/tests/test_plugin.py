"""The plugin entry: manifest, the screen-text rule (flag, gateway), what the reply says was removed, and the face boundary."""
from __future__ import annotations

from pathlib import Path

import pytest
from PIL import Image

from jarvis.pluginapi import Err, Ok
from jarvis.plugins._shared import screen_text
from jarvis.plugins.doc_reader import DocReaderPlugin, release_pages

IBAN_OK = "GB82WEST12345698765432"


def make_plugin(*answers):
    p = object.__new__(DocReaderPlugin)
    it = iter(answers)
    p.transcribe = lambda png: next(it)
    return p


@pytest.fixture()
def scan(tmp_path_factory):
    d = tmp_path_factory.mktemp("d").resolve()
    Image.new("RGB", (300, 200), "white").save(d / "invoice.png")
    return str(d / "invoice.png")


def test_manifest():
    text = (Path(__file__).resolve().parents[1] / "plugin.toml").read_text(encoding="utf-8")
    assert 'id = "doc_reader"' in text and 'entry = "jarvis.plugins.doc_reader:DocReaderPlugin"' in text


def test_the_entry_is_registered_with_the_host():
    """The host finds the manifest in jarvis/plugins/doc_reader and the class has the one entry and both lifecycle hooks."""
    import importlib
    from jarvis.pluginapi.host import PLUGINS_DIR, inspect_class, read_manifest
    m = read_manifest(PLUGINS_DIR / "doc_reader")
    assert m and m["id"] == "doc_reader"
    mod, cls = m["entry"].split(":")
    entries, lifecycle, timers = inspect_class(getattr(importlib.import_module(mod), cls))
    assert set(entries) == {"read_document"} and set(lifecycle) == {"startup", "shutdown"} and not timers
    meta = entries["read_document"][0]
    assert "path" in meta.input_schema["required"] and meta.timeout >= 600


async def test_text_stays_on_the_pc_when_screen_text_is_not_allowed(scan, monkeypatch):
    monkeypatch.delenv(screen_text.FLAG, raising=False)
    res = await make_plugin("Total due 125.00").read_document(path=scan)
    assert isinstance(res, Ok)
    v = res.value
    assert "text" not in v and "stayed on this PC" in v["text_note"] and v["pages"] == 1 and v["chars"] > 0


async def test_with_the_flag_and_a_cleared_release_the_text_comes_back(scan, monkeypatch):
    monkeypatch.setenv(screen_text.FLAG, "1")
    seen = []

    def fake_release(text, **kw):
        seen.append(text)
        return screen_text.Released(text.upper(), True, "gateway", "names hidden by the gateway")
    monkeypatch.setattr(screen_text, "release_safely", fake_release)
    res = await make_plugin("Total due 125.00").read_document(path=scan)
    v = res.value
    assert v["text"] == "--- page 1 of 1 ---\nTOTAL DUE 125.00" and v[screen_text.SCREEN_KEY] is True
    assert v["text_note"] == "names hidden by the gateway" and seen == ["Total due 125.00"]


async def test_secrets_are_removed_before_the_release_and_the_reply_names_what_went(scan, monkeypatch):
    monkeypatch.setenv(screen_text.FLAG, "1")
    seen = []
    monkeypatch.setattr(screen_text, "release_safely", lambda t, **kw: (seen.append(t), screen_text.Released(t, True, "gateway", "ok"))[1])
    res = await make_plugin(f"IBAN {IBAN_OK} card 4111 1111 1111 1111 password: hunter2hunter2").read_document(path=scan)
    v = res.value
    assert all(x not in "".join(seen) + v["text"] for x in (IBAN_OK, "4111 1111", "hunter2"))
    assert set(v["removed"]) >= {"bank_account", "card_number", "key_assignment"}
    assert "a card number" in v["note"] and "a bank account number" in v["note"] and IBAN_OK not in v["note"]


async def test_a_gateway_that_is_down_withholds_all_of_it(scan, monkeypatch):
    monkeypatch.setenv(screen_text.FLAG, "1")
    monkeypatch.setattr(screen_text, "gateway_state", lambda: (False, "down"))
    monkeypatch.setattr(screen_text, "local_model_ok", lambda url: False)
    res = await make_plugin("Dear Sample Customer").read_document(path=scan)
    v = res.value
    assert "text" not in v and v["text_note"] == screen_text.WITHHELD and "Sample" not in str(v)


async def test_a_page_with_no_text_says_so_without_asking_for_release(scan, monkeypatch):
    monkeypatch.setenv(screen_text.FLAG, "1")
    monkeypatch.setattr(screen_text, "release_safely", lambda *a, **k: pytest.fail("nothing to release"))
    res = await make_plugin("").read_document(path=scan)
    assert "No text was found" in res.value["text_note"]


async def test_refusals_come_back_as_errors(tmp_path_factory):
    tmp_path = tmp_path_factory.mktemp('d')
    for bad in ("", "relative.png", str(tmp_path / "secret.png"), "../x.png", "C:\\Users\\me\\wallet.pdf"):
        res = await make_plugin("x").read_document(path=bad)
        assert isinstance(res, Err)
        assert bad not in str(res.error) or bad == ""


def test_release_pages_is_all_or_nothing():
    calls = []

    def rel(text, **kw):
        calls.append(text)
        return screen_text.Released(text, text != "two", "gateway" if text != "two" else "withheld", "n")
    text, note, via = release_pages(["one", "two", "three"], 3, release=rel)
    assert (text, via) == ("", "withheld") and calls == ["one", "two"]


async def test_what_the_face_boundary_does_with_the_result(scan, monkeypatch):
    """The face's tool server runs every result through clean_result; the reply must survive it with the page marker, the counts and
    the 'removed' rule names, and no path or secret in it."""
    monkeypatch.delenv(screen_text.FLAG, raising=False)
    res = await make_plugin(f"IBAN {IBAN_OK}").read_document(path=scan)
    out = screen_text.clean_result(res.value)
    assert out["pages"] == 1 and out["removed"] == ["bank_account"] and IBAN_OK not in str(out) and "invoice.png" not in str(out)
