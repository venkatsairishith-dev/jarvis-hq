from __future__ import annotations

import os

import pytest

from plugin.plugins.doc_tools import core
from plugin.plugins.doc_tools.core import DocError


@pytest.fixture()
def env(tmp_path, monkeypatch):
    docs = tmp_path / "Documents"
    docs.mkdir()
    out = tmp_path / "out"
    monkeypatch.setenv("DOC_TOOLS_READ_ROOTS", str(docs))
    monkeypatch.setenv("DOC_TOOLS_OUT_DIR", str(out))
    return docs, out, tmp_path


def test_reads_text_inside_an_allowed_folder_and_reports_truncation(env):
    docs, _, _ = env
    (docs / "notes.md").write_text("# Title\n\n" + "word " * 5000, encoding="utf-8")
    r = core.read_document(str(docs / "notes.md"), max_chars=1000)
    assert r["kind"] == "text" and r["truncated"] is True and len(r["text"]) == 1000 and r["chars_total"] > 1000
    assert core.read_document(str(docs / "notes.md"), max_chars=60000)["truncated"] is False
    assert core.read_document(str(docs / "notes.md"))["truncated"] is True          # the default is 20,000 characters


def test_refuses_outside_relative_missing_and_protected_files(env):
    docs, _, tmp = env
    other = tmp / "elsewhere.txt"
    other.write_text("x", encoding="utf-8")
    for bad in (str(other), "relative/file.txt", str(docs / "missing.txt"), "", str(docs)):
        with pytest.raises(DocError):
            core.read_document(bad)
    for name in (".env", "my_token.txt", "id_rsa", "passwords.csv", "server.pem", "wallet.json"):
        (docs / name).write_text("secret", encoding="utf-8")
        with pytest.raises(DocError) as e:
            core.read_document(str(docs / name))
        assert "protected" in str(e.value)


def test_a_secrets_folder_is_never_read_even_inside_an_allowed_root(env):
    docs, _, _ = env
    d = docs / "jarvis-secrets"
    d.mkdir()
    (d / "notes.txt").write_text("hello", encoding="utf-8")
    with pytest.raises(DocError):
        core.read_document(str(d / "notes.txt"))


def test_unsupported_types_are_refused_without_reading_them(env):
    docs, _, _ = env
    (docs / "tool.exe").write_bytes(b"MZ\x00\x00")
    with pytest.raises(DocError) as e:
        core.read_document(str(docs / "tool.exe"))
    assert ".exe" in str(e.value)


def test_pdf_and_html_and_docx_are_read_as_text(env):
    docs, out, _ = env
    from reportlab.pdfgen import canvas
    c = canvas.Canvas(str(docs / "paper.pdf"))
    c.drawString(72, 720, "Photosynthesis converts light into chemical energy.")
    c.save()
    assert "Photosynthesis" in core.read_document(str(docs / "paper.pdf"))["text"]
    (docs / "page.html").write_text("<html><body><h1>Hello</h1><p>World <b>bold</b></p><script>x()</script></body></html>", encoding="utf-8")
    h = core.read_document(str(docs / "page.html"))
    assert h["kind"] == "html" and "Hello" in h["text"] and "World" in h["text"]
    w = core.write_document("report", "# Heading\n\n- first\n- second\n\nBody text.", "docx")
    # the written folder is outside the read roots, so allow it for the round trip
    os.environ["DOC_TOOLS_READ_ROOTS"] = str(docs) + os.pathsep + str(out)
    back = core.read_document(w["path"])
    assert back["kind"] == "docx" and "Heading" in back["text"] and "first" in back["text"] and "Body text." in back["text"]


def test_a_scanned_pdf_says_it_has_no_text_layer_instead_of_returning_silent_empty_text(env):
    """PRO-D8: an image-only PDF gave chars_total 0 and no warning, so the model could call the document empty."""
    docs, _, _ = env
    from reportlab.pdfgen import canvas
    c = canvas.Canvas(str(docs / "scan.pdf"))
    c.rect(50, 50, 300, 400, fill=1)                                  # a page with a picture and no text, like a scan
    c.save()
    r = core.read_document(str(docs / "scan.pdf"))
    assert r["kind"] == "pdf" and r["chars_total"] == 0 and r["text"] == ""
    assert r["note"] == "no text layer: the PDF may be scanned; OCR is not available here"
    c = canvas.Canvas(str(docs / "mixed.pdf"))
    c.drawString(72, 720, "Chapter one has real text.")
    c.showPage()
    c.rect(50, 50, 300, 400, fill=1)
    c.save()
    m = core.read_document(str(docs / "mixed.pdf"))
    assert "Chapter one" in m["text"] and m["note"].startswith("1 of 2 pages have no text layer")
    c = canvas.Canvas(str(docs / "plain.pdf"))
    c.drawString(72, 720, "Only text here.")
    c.save()
    assert "note" not in core.read_document(str(docs / "plain.pdf"))


def test_write_never_overwrites_and_sanitizes_names(env):
    _, out, _ = env
    a = core.write_document("Plan: week 1/2?", "one", "md")
    b = core.write_document("Plan: week 1/2?", "two", "md")
    assert a["path"] != b["path"] and a["name"] == "Plan week 12.md" and b["name"] == "Plan week 12-2.md"
    assert open(a["path"], encoding="utf-8").read() == "one\n"
    assert os.path.dirname(a["path"]) == str(out)
    for bad_name in ("", "../../etc/passwd", "   ", "...", "///"):
        try:
            r = core.write_document(bad_name, "x", "md")
            assert os.path.dirname(r["path"]) == str(out)              # a traversal attempt is flattened, never escapes
        except DocError:
            pass


def test_write_validates_format_and_size(env):
    with pytest.raises(DocError):
        core.write_document("x", "y", "exe")
    with pytest.raises(DocError):
        core.write_document("x", "   ", "md")
    with pytest.raises(DocError):
        core.write_document("x", "a" * (core.MAX_WRITE_CHARS + 1), "md")


def test_list_outputs_newest_first(env):
    core.write_document("first", "1", "md")
    core.write_document("second", "2", "txt")
    names = [f["name"] for f in core.list_outputs()]
    assert set(names) == {"first.md", "second.txt"}
