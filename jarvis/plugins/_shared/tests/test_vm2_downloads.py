"""Files downloaded on VM2: inspected there, copied to the PC quarantine only on the owner's click, with the sha256 checked, and never opened, run or unpacked (D79)."""
import hashlib
import io
import os
import subprocess
import webbrowser
import zipfile

import pytest

from jarvis.plugins._shared import vm2_downloads as DL
from jarvis.plugins._shared.tests.vm2_rig import Rig

PDF = b"%PDF-1.7\nhello from a site\n"
PE = b"MZ\x90\x00" + b"\x00" * 300
ZIP = io.BytesIO()
with zipfile.ZipFile(ZIP, "w") as _z:
    _z.writestr("evil.exe", "MZ")
ZIPB = ZIP.getvalue()


@pytest.fixture
def rig(tmp_path):
    r = Rig(tmp_path)
    r.out = tmp_path / "outputs"
    yield r
    r.close()


@pytest.fixture(autouse=True)
def nothing_is_ever_opened(monkeypatch):
    """The module has no code that opens, runs or unpacks; these make any such call fail the test."""
    def boom(*a, **k):
        raise AssertionError("a downloaded file was opened, run or unpacked")
    for obj, name in ((os, "startfile"), (subprocess, "Popen"), (subprocess, "run"), (subprocess, "call"), (webbrowser, "open"), (zipfile.ZipFile, "extractall"), (zipfile.ZipFile, "extract")):
        monkeypatch.setattr(obj, name, boom, raising=False)


def copy_all(rig, host="files.example.test"):
    return DL.review(rig.port, rig.owner, host, str(rig.out))


def test_the_card_shows_name_size_type_and_source_host(rig):
    rig.lab.download("report.pdf", PDF)
    rig.owner.script = ["skip"]
    copy_all(rig)
    rows = dict(map(tuple, rig.owner.forms[0]["fields"][0]["rows"]))
    assert rows["File"] == "report.pdf" and rows["Size"] == f"{len(PDF)} bytes" and rows["Type (by its content)"] == "PDF document" and rows["From (your request)"] == "files.example.test"
    assert "no virus scanner installed" in rows["Checked on VM2"] and rows["sha256"].startswith(hashlib.sha256(PDF).hexdigest()[:16])
    assert [b["id"] for b in rig.owner.forms[0]["buttons"]] == ["copy", "skip"] and rig.owner.forms[0]["buttons"][0]["style"] == "primary"


def test_nothing_crosses_without_the_click_and_a_no_leaves_the_pc_folder_empty(rig):
    rig.lab.download("report.pdf", PDF)
    rig.owner.script = ["skip"]
    got = copy_all(rig)
    assert got["copied"] == [] and got["skipped"][0]["name"] == "report.pdf" and not rig.out.exists()
    rig.owner.script = [None]
    assert copy_all(rig)["copied"] == [] and not rig.out.exists(), "an unanswered card is a no"


def test_an_approved_copy_is_verified_and_lands_in_the_host_folder_of_the_quarantine(rig):
    rig.lab.download("report.pdf", PDF)
    rig.owner.script = ["copy"]
    got = copy_all(rig)
    c = got["copied"][0]
    assert c["ok"] and c["sha256"] == hashlib.sha256(PDF).hexdigest() and c["size"] == len(PDF) and c["opened"] is False and c["extracted"] is False and "path" not in c
    files = list((rig.out / "downloads" / "files.example.test").iterdir())
    assert len(files) == 1 and files[0].read_bytes() == PDF and files[0].name.endswith("report.pdf")


def test_a_program_gets_a_warning_a_cautious_default_and_a_name_that_cannot_run(rig):
    rig.lab.download("setup.exe", PE)
    rig.owner.script = ["copy"]
    got = copy_all(rig)
    form = rig.owner.forms[0]
    assert [b["id"] for b in form["buttons"] if b["style"] == "primary"] == ["skip"], "for a program the safe button is the default"
    assert any(f["type"] == "callout" and "never open, run or unpack" in f["text"] for f in form["fields"])
    saved = next((rig.out / "downloads" / "files.example.test").iterdir())
    assert saved.name.endswith("setup.exe.quarantined") and got["copied"][0]["risky"] is True
    assert not saved.name.lower().endswith((".exe", ".msi", ".bat", ".cmd", ".ps1")), "the name cannot run"
    if os.name != "nt":                                 # on Windows os.access(X_OK) is True for every file: the extension is the whole guard there
        assert not os.access(saved, os.X_OK), "no execute bit"


def test_an_archive_is_copied_as_it_is_and_never_extracted(rig):
    rig.lab.download("pack.zip", ZIPB)
    rig.owner.script = ["copy"]
    got = copy_all(rig)
    saved = next((rig.out / "downloads" / "files.example.test").iterdir())
    assert got["copied"][0]["type"] == "ZIP archive" and saved.name.endswith("pack.zip.quarantined") and saved.read_bytes() == ZIPB
    assert [p.name for p in saved.parent.iterdir()] == [saved.name], "nothing was unpacked next to it"


def test_a_file_whose_name_lies_is_warned_about(rig):
    rig.lab.download("invoice.pdf", PE)
    rig.owner.script = ["skip"]
    copy_all(rig)
    assert any(f["type"] == "callout" and "name says one thing" in f["text"] for f in rig.owner.forms[0]["fields"])


def test_a_file_that_changes_in_transit_is_not_written(rig):
    rig.lab.download("report.pdf", PDF)
    rig.owner.script = ["copy"]
    real = rig.port.fetch
    rig.port.fetch = lambda fid, cap=None: (b"%PDF-1.7\nTAMPERED\n", hashlib.sha256(PDF).hexdigest())
    got = copy_all(rig)
    assert got["copied"] == [] and "sha256 differs" in got["refused"][0]["why"] and not rig.out.exists()
    rig.port.fetch = lambda fid, cap=None: (PDF, "0" * 64)                         # the server states another hash than the card showed
    rig.owner.script = ["copy"]
    assert copy_all(rig)["copied"] == [] and not rig.out.exists()
    rig.port.fetch = real


def test_a_file_swapped_on_vm2_after_the_card_cannot_be_copied(rig):
    rig.lab.download("report.pdf", PDF)

    def swap(spec):
        rig.lab.files["report.pdf"] = b"%PDF-1.7\nSWAPPED\n"
        return "copy"
    rig.owner.script = [swap]
    got = copy_all(rig)
    assert got["copied"] == [] and got["refused"] and not rig.out.exists()


def test_an_infected_or_oversized_file_is_never_offered(rig, monkeypatch):
    rig.lab.download("virus.pdf", PDF)
    rig.lab.download("huge.iso", b"\x00" * 6000)
    real = rig.lab.listing

    def listing():
        d = real()
        for f in d["files"]:
            if f["name"] == "virus.pdf":
                f["scan"] = {"tool": "clamscan", "result": "infected"}
            if f["name"] == "huge.iso":
                f.update(too_big=True, id="", sha256="")
        return d
    rig.lab.listing = listing
    rig.owner.script = ["copy", "copy"]
    got = copy_all(rig)
    assert rig.owner.forms == [] and got["copied"] == [] and {r["name"] for r in got["refused"]} == {"virus.pdf", "huge.iso"}


def test_only_new_files_are_offered_and_an_unreachable_vm2_is_said(rig):
    rig.lab.download("old.pdf", PDF)
    before = {r["id"] for r in rig.port.quarantine()}
    rig.lab.download("new.pdf", PDF + b"x")
    rig.owner.script = ["skip"]
    DL.review(rig.port, rig.owner, "x.example", str(rig.out), only=lambda it: it.id not in before)
    assert len(rig.owner.forms) == 1 and dict(map(tuple, rig.owner.forms[0]["fields"][0]["rows"]))["File"] == "new.pdf"
    rig.lab.close()
    assert "error" in DL.review(rig.port, rig.owner, "x.example", str(rig.out))


def test_a_name_with_tricks_is_cleaned_and_an_existing_file_is_never_overwritten(rig):
    rig.lab.download("../../evil‮gpj.pdf", PDF)
    rig.owner.script = ["copy"]
    copy_all(rig)
    rig.owner.script = ["copy"]
    copy_all(rig)
    names = [p.name for p in (rig.out / "downloads" / "files.example.test").iterdir()]
    assert len(names) == 2 and all("/" not in n and "‮" not in n for n in names)


def test_an_unknown_source_host_uses_a_safe_folder_name(rig):
    assert DL.pc_dir("../../Windows", str(rig.out)).name == DL.FALLBACK_HOST and DL.pc_dir("a.example", str(rig.out)).parent.name == "downloads"
