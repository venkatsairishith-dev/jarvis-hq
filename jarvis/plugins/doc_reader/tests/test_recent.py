"""Recent files: names and sizes of the newest readable files of one ordinary folder, so 'read this invoice' can ask which (2026-10-25)."""
from __future__ import annotations

import os
import time

import pytest

from jarvis.pluginapi import Err, Ok
from jarvis.plugins.doc_reader import DocReaderPlugin, core
from jarvis.plugins.doc_reader.core import DocReaderError


@pytest.fixture()
def home(tmp_path_factory):
    h = tmp_path_factory.mktemp("home").resolve()
    for folder in ("Downloads", "Documents", "Desktop", "Pictures"):
        (h / folder).mkdir()
    now = time.time()

    def make(folder, name, size, age_days):
        p = h / folder / name
        p.write_bytes(b"x" * size)
        t = now - age_days * 86400
        os.utime(p, (t, t))
    make("Downloads", "invoice-march.pdf", 120_000, 1)
    make("Downloads", "scan-0042.PNG", 2_048, 3)
    make("Downloads", "old-receipt.jpg", 50_000, 30)
    make("Downloads", "notes.txt", 100, 0)                      # not a readable type
    make("Downloads", "passwords.pdf", 100, 0)                  # looks like it holds credentials
    make("Downloads", "my-token-export.png", 100, 0)
    make("Documents", "contract.pdf", 10, 5)
    return h


def test_the_newest_readable_files_with_sizes_and_age(home):
    out = core.recent_files("Downloads", 10, home=home)
    assert out["folder"] == str(home / "Downloads")
    assert [(f["name"], f["kb"], f["days_ago"]) for f in out["files"]] == [("invoice-march.pdf", 117, 1), ("scan-0042.PNG", 2, 3), ("old-receipt.jpg", 49, 30)]
    assert out["total_seen"] == 3


def test_other_types_and_credential_looking_names_are_never_listed(home):
    names = [f["name"] for f in core.recent_files("Downloads", 15, home=home)["files"]]
    assert "notes.txt" not in names and "passwords.pdf" not in names and "my-token-export.png" not in names


def test_the_limit_and_the_folder_names(home):
    assert len(core.recent_files("downloads", 2, home=home)["files"]) == 2
    assert core.recent_files("Documents", 5, home=home)["files"][0]["name"] == "contract.pdf"
    for bad in ("AppData", "..", "C:/Windows", "", "Downloads/x"):
        with pytest.raises(DocReaderError):
            core.recent_files(bad, 5, home=home)


def test_a_folder_that_cannot_be_opened_is_a_plain_refusal(tmp_path_factory):
    with pytest.raises(DocReaderError, match="cannot open"):
        core.recent_files("Pictures", 5, home=tmp_path_factory.mktemp("empty").resolve())


async def test_the_entry_puts_the_names_in_the_plugins_own_message_and_nothing_from_inside_a_file(home, monkeypatch):
    monkeypatch.setattr(core.Path, "home", classmethod(lambda cls: home))
    plugin = object.__new__(DocReaderPlugin)
    res = await plugin.recent_files(folder="Downloads", limit=5)
    assert isinstance(res, Ok)
    msg = res.value["message"]
    assert "invoice-march.pdf (117 KB, 1 d ago)" in msg and "Ask which one to read" in msg
    assert "text" not in res.value
    bad = await plugin.recent_files(folder="AppData")
    assert isinstance(bad, Err)
