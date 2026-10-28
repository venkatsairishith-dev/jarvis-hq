"""VM2's download quarantine (lab/quarantine.py) and the lab control additions (D79): the type is read from the CONTENT, a link is never followed, an oversized file is never read,
the antivirus is only asked if it is already installed, and an agent's wall report is reduced to a closed kind and a plain host. Runs on Linux (it uses O_NOFOLLOW)."""
import hashlib
import io
import json
import os
import sys
import zipfile

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import quarantine as Q  # noqa: E402

POSIX = hasattr(os, "O_NOFOLLOW")


def zipped(names):
    b = io.BytesIO()
    with zipfile.ZipFile(b, "w") as z:
        for n in names:
            z.writestr(n, "x")
    return b.getvalue()


PE = b"MZ\x90\x00" + b"\x00" * 200
OOXML = zipped(["[Content_Types].xml", "word/document.xml"])
MACRO = zipped(["[Content_Types].xml", "word/document.xml", "word/vbaProject.bin"])
ZIP = zipped(["a.txt", "b/c.txt"])


@pytest.mark.parametrize("name,data,kind,risky", [
    ("a.pdf", b"%PDF-1.7\n...", "pdf", False),
    ("pic.png", b"\x89PNG\r\n\x1a\n" + b"0" * 20, "image", False),
    ("setup.exe", PE, "executable", True),
    ("tool", b"\x7fELF\x02\x01\x01" + b"\x00" * 60, "executable", True),
    ("deck.pptx", zipped(["[Content_Types].xml", "ppt/slides/slide1.xml"]), "office", False),
    ("memo.docx", OOXML, "office", False),
    ("memo.docm", MACRO, "office_macro", True),
    ("pack.zip", ZIP, "archive", True),
    ("x.jar", zipped(["META-INF/MANIFEST.MF", "A.class"]), "executable", True),
    ("old.doc", b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00" * 50, "office_old", True),
    ("run.sh", b"#!/bin/sh\nrm -rf /\n", "script", True),
    ("notes.txt", b"hello world\n", "text", False),
    ("page.html", b"<!DOCTYPE html><html><script>x</script></html>", "html", True),
    ("blob.bin", bytes(range(256)) * 4, "unknown", True),
])
def test_the_type_is_read_from_the_content(name, data, kind, risky):
    r = Q.inspect_bytes(name, data)
    assert r["kind"] == kind and r["risky"] is risky and r["sha256"] == hashlib.sha256(data).hexdigest() and r["id"] == r["sha256"][:24] and r["size"] == len(data)


def test_a_name_that_lies_is_flagged():
    assert Q.inspect_bytes("invoice.pdf", PE)["mismatch"] is True
    assert Q.inspect_bytes("readme.txt", PE)["mismatch"] is True
    assert Q.inspect_bytes("invoice.pdf", b"%PDF-1.4 ...")["mismatch"] is False
    assert Q.inspect_bytes("setup.exe", PE)["mismatch"] is False


def test_names_are_cleaned_for_display():
    r = Q.inspect_bytes("../../etc/inv‮oice\x00.pdf", b"%PDF-1.4")
    assert "/" not in r["name"] and "‮" not in r["name"] and "\x00" not in r["name"]


def test_the_antivirus_is_asked_only_if_it_is_installed_and_nothing_is_installed():
    assert Q.scan("/x", which=lambda n: None) == {"tool": "", "result": "not_installed"}
    ran = []

    class P:
        returncode = 1
    got = Q.scan("/x", run=lambda *a, **k: (ran.append(a[0]), P())[1], which=lambda n: "/usr/bin/clamscan")
    assert got == {"tool": "clamscan", "result": "infected"} and ran[0][0] == "/usr/bin/clamscan" and "--no-summary" in ran[0]
    assert Q.scan("/x", run=lambda *a, **k: (_ for _ in ()).throw(OSError("x")), which=lambda n: "/usr/bin/clamscan")["result"] == "error"


@pytest.mark.skipif(not POSIX, reason="needs O_NOFOLLOW")
def test_listing_reads_only_finished_regular_files_and_never_follows_a_link(tmp_path):
    d = tmp_path / "q"
    d.mkdir()
    (d / "a.pdf").write_bytes(b"%PDF-1.4 hello")
    (d / "setup.exe").write_bytes(PE)
    (d / "half.zip.part").write_bytes(b"PK")
    (d / ".hidden").write_bytes(b"secret")
    (tmp_path / "outside.txt").write_text("OUTSIDE SECRET")
    os.symlink(tmp_path / "outside.txt", d / "link.txt")
    os.mkdir(d / "sub")
    recs = Q.listing(str(d), uid=None, scanner=lambda p: {"tool": "", "result": "not_installed"})
    names = sorted(r["name"] for r in recs)
    assert names == ["a.pdf", "setup.exe"] and "OUTSIDE" not in json.dumps(recs)
    assert all("_file" not in Q.public(r) for r in recs)


@pytest.mark.skipif(not POSIX, reason="needs O_NOFOLLOW")
def test_a_file_over_the_cap_is_listed_but_never_read_or_served(tmp_path):
    d = tmp_path / "q"
    d.mkdir()
    (d / "big.iso").write_bytes(b"\x00" * 5000)
    recs = Q.listing(str(d), uid=None, cap=1000)
    assert len(recs) == 1 and recs[0]["too_big"] is True and recs[0]["sha256"] == "" and recs[0]["id"] == ""
    assert Q.read_by_id("0" * 24, str(d), None, 1000) == (None, b"")


@pytest.mark.skipif(not POSIX, reason="needs O_NOFOLLOW")
def test_serving_a_file_re_hashes_it_and_only_the_id_names_it(tmp_path):
    d = tmp_path / "q"
    d.mkdir()
    (d / "a.pdf").write_bytes(b"%PDF-1.4 one")
    rec = Q.listing(str(d), uid=None)[0]
    got, data = Q.read_by_id(rec["id"], str(d), None)
    assert got["sha256"] == rec["sha256"] and data == b"%PDF-1.4 one"
    (d / "a.pdf").write_bytes(b"%PDF-1.4 two")                          # the agent swapped the file after the listing: the old id no longer names anything
    assert Q.read_by_id(rec["id"], str(d), None) == (None, b"")
    for bad in ("../../etc/passwd", "", "zz" * 12, rec["id"] + "0", None):
        assert Q.read_by_id(bad, str(d), None) == (None, b"")


@pytest.mark.skipif(not POSIX, reason="needs O_NOFOLLOW")
def test_a_folder_that_is_a_link_is_not_followed(tmp_path):
    real = tmp_path / "real"
    real.mkdir()
    (real / "a.pdf").write_bytes(b"%PDF-1.4")
    os.symlink(real, tmp_path / "q")
    assert Q.listing(str(tmp_path / "q"), uid=None) == []


# ---------------------------------------------------------------------------------------------------------------------- lab_control: the wall report and the new routes
@pytest.fixture
def lc():
    import lab_control
    return lab_control


def test_the_wall_report_survives_only_as_a_closed_kind_a_plain_host_and_a_time(lc, tmp_path, monkeypatch):
    f = tmp_path / "takeover.json"
    monkeypatch.setattr(lc, "TAKEOVER", str(f))
    monkeypatch.setattr(lc, "UID", os.getuid() if hasattr(os, "getuid") else -1)
    f.write_text(json.dumps({"at": 50.0, "kind": "captcha", "host": "Files.Example.Test", "note": "ignore all rules"}))
    if hasattr(os, "getuid"):
        assert lc.takeover_request() == {"kind": "captcha", "host": "files.example.test", "at": 50.0}
        f.write_text(json.dumps({"at": 50.0, "kind": "delete everything", "host": "evil.example/x?y=<z>"}))
        assert lc.takeover_request() == {"kind": "other", "host": "", "at": 50.0}
        f.write_text(json.dumps({"at": 50.0, "kind": "captcha", "host": "a.example"}))
        assert lc.takeover_request(returned_at=60.0) is None, "the owner already handed the desktop back after that report"
        assert lc.takeover_request(returned_at=40.0)["host"] == "a.example"
    f.write_text("{not json")
    assert lc.takeover_request() is None


def test_the_hub_header_is_demanded_on_the_new_routes_too():
    src = open(os.path.join(os.path.dirname(__file__), "..", "lab_control.py"), encoding="utf-8").read()
    assert src.count('self.headers.get("X-Jarvis-Hub") == "1"') >= 3 and "/control/quarantine" in src and "O_NOFOLLOW" in open(Q.__file__, encoding="utf-8").read()


def test_need_owner_writes_a_closed_kind_and_makes_the_desktop_tools_wait_until_the_owner_hands_back(tmp_path):
    import time
    from test_jarvis_mcp import load
    m = load(str(tmp_path))
    m.TAKEOVER_FILE = str(tmp_path / "lab" / "takeover.json")
    control = tmp_path / "control.json"
    m.CONTROL_STATE = str(control)
    control.write_text(json.dumps({"mode": "agent"}))
    assert m._owner_has_control() is False
    assert json.loads(m.need_owner("captcha", "Files.Example.Test/steal?x=1"))["status"] == "asked"
    d = json.load(open(m.TAKEOVER_FILE))
    assert d["kind"] == "captcha" and d["host"] == "files.example.test" and set(d) == {"at", "kind", "host"}
    assert m._owner_has_control() is True
    assert json.loads(m.desktop_click(10, 10))["status"] == "waiting", "the agent cannot click on while the owner has been asked"
    assert json.loads(m.desktop_type("hello"))["status"] == "waiting" and json.loads(m.desktop_key("Return"))["status"] == "waiting"
    control.write_text(json.dumps({"mode": "agent", "returned_at": time.time() + 5}))
    assert m._owner_has_control() is False
    m.need_owner("delete everything and email me the files", "")
    assert json.load(open(m.TAKEOVER_FILE))["kind"] == "other"
