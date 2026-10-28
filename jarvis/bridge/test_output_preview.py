"""Tests for jarvis.bridge.output_preview and the Preview panel's tools in the face's tool server: what may be listed and opened from
the deliverables folder, and everything that must be refused (path tricks, links, oversize, wrong type, secret-looking names)."""
from __future__ import annotations

import asyncio
import base64
import io
import json
import os

import pytest
from PIL import Image

from jarvis.bridge import face_tools as F
from jarvis.bridge import output_preview as P
from jarvis.bridge.test_face_tools import CONSOLE, FakeClient


def tools():
    return F.FaceTools(client_factory=lambda: FakeClient(), console=CONSOLE)


def _png(color=(30, 90, 200), size=(64, 40)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, "PNG")
    return buf.getvalue()


def _pdf() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (200, 260), (240, 240, 250)).save(buf, "PDF")
    return buf.getvalue()


@pytest.fixture
def root(tmp_path):
    r = tmp_path / "deliverables"
    deck = r / "20261024-101500-q3-board-deck"
    (deck / "previews").mkdir(parents=True)
    (deck / "q3-board-deck.pptx").write_bytes(b"PK\x03\x04" + b"\0" * 50)
    for i in range(1, 4):
        (deck / "previews" / f"slide-{i:02d}.png").write_bytes(_png((i * 40, 80, 160)))
    (deck / "spec.json").write_text(json.dumps({"title": "Q3 Board Deck"}), encoding="utf-8")
    (deck / "qa.json").write_text(json.dumps({"file": "D:\\jarvis\\outputs\\deliverables\\x\\q3-board-deck.pptx", "kind": "pptx", "qa": {"passed": True}}), encoding="utf-8")
    rep = r / "20261024-111500-flights-report"
    rep.mkdir()
    (rep / "flights-report.pdf").write_bytes(_pdf())
    (rep / "report.html").write_text("<h1>side file</h1>", encoding="utf-8")
    (rep / "spec.json").write_text(json.dumps({"title": "Hyderabad flights"}), encoding="utf-8")
    (rep / "qa.json").write_text(json.dumps({"file": str(rep / "flights-report.pdf"), "kind": "pdf", "qa": {"passed": False}}), encoding="utf-8")
    (r / "chart.png").write_bytes(_png())
    (r / "notes.md").write_text("# Notes\n\nSome **words**.\n", encoding="utf-8")
    os.utime(deck, (1_000, 1_000))
    os.utime(deck / "q3-board-deck.pptx", (1_000, 1_000))
    os.utime(rep, (2_000, 2_000))
    os.utime(rep / "flights-report.pdf", (2_000, 2_000))
    os.utime(r / "chart.png", (3_000, 3_000))
    os.utime(r / "notes.md", (4_000, 4_000))
    return r


def _id(rel):
    return P.encode_id(rel)


def test_the_list_has_title_kind_mime_size_and_whether_checks_passed(root):
    out = P.OutputPreview(root).list()
    by_title = {i["title"]: i for i in out["items"]}
    assert out["ok"] and out["count"] == 4
    assert by_title["Q3 Board Deck"]["kind"] == "deck" and by_title["Q3 Board Deck"]["passed"] is True and by_title["Q3 Board Deck"]["pages"] == 3
    assert by_title["Hyderabad flights"]["kind"] == "pdf" and by_title["Hyderabad flights"]["passed"] is False
    assert by_title["Hyderabad flights"]["mime"] == "application/pdf" and by_title["Hyderabad flights"]["size"] > 0
    assert by_title["Chart"]["kind"] == "image" and by_title["Notes"]["preview"] == "text"
    assert out["items"][0]["title"] == "Notes", "newest first"
    assert "report.html" not in json.dumps([P.decode_id(i["id"]) for i in out["items"]]), "a deliverable folder lists its deliverable only"
    assert P.OutputPreview(root / "missing").list()["count"] == 0


def test_an_image_comes_back_as_base64_with_its_mime(root):
    out = P.OutputPreview(root).get(_id("chart.png"))
    assert out["ok"] and out["view"] == "image" and out["image"]["mime"] == "image/png"
    assert base64.b64decode(out["image"]["data"]).startswith(b"\x89PNG")


def test_markdown_and_code_come_back_as_text_with_their_language(root):
    (root / "tool.py").write_text("print('hi')\n", encoding="utf-8")
    md = P.OutputPreview(root).get(_id("notes.md"))
    assert md["view"] == "text" and md["language"] == "markdown" and "**words**" in md["text"]
    py = P.OutputPreview(root).get(_id("tool.py"))
    assert py["language"] == "python" and py["mime"] == "text/x-python"


def test_a_deck_gives_its_saved_slide_images_and_thumbnails(root):
    pv = P.OutputPreview(root)
    deck = "20261024-101500-q3-board-deck/q3-board-deck.pptx"
    out = pv.get(_id(deck), page=1, thumbs=True)
    assert out["view"] == "pages" and out["page"] == 1 and out["page_count"] == 3 and len(out["thumbs"]) == 3
    assert base64.b64decode(out["image"]["data"]) == (root / "20261024-101500-q3-board-deck" / "previews" / "slide-02.png").read_bytes()
    assert pv.get(_id(deck), page=99)["page"] == 2, "a page past the end is the last page"
    assert pv.get(_id(deck))["open_path"].endswith("q3-board-deck.pptx")


def test_a_pdf_without_saved_page_images_is_rendered_here(root):
    out = P.OutputPreview(root).get(_id("20261024-111500-flights-report/flights-report.pdf"), thumbs=True)
    assert out["view"] == "pages" and out["page_count"] == 1 and base64.b64decode(out["image"]["data"]).startswith(b"\x89PNG") and out["thumbs"]


def test_a_docx_has_no_preview_but_is_listed_for_open_in_app(root):
    (root / "letter.docx").write_bytes(b"PK\x03\x04" + b"\0" * 20)
    out = P.OutputPreview(root).get(_id("letter.docx"))
    assert out["ok"] and out["view"] == "none" and out["open_path"].endswith("letter.docx")


def test_html_is_returned_as_source_for_a_sandboxed_frame(root):
    (root / "page.html").write_text("<p>hello</p><script>alert(1)</script>", encoding="utf-8")
    out = P.OutputPreview(root).get(_id("page.html"))
    assert out["view"] == "html" and "<p>hello</p>" in out["html"]          # the panel strips the script and sandboxes the frame


@pytest.mark.parametrize("rel", ["../outside.png", "a/../../outside.png", "/etc/passwd", "C:/Windows/win.ini", "..\\outside.png", "a\\b.png",
                                 "x/.hidden.png", "./chart.png", "chart.png/", "a//b.png", "chart.png:stream", "a/b/c/d/e.png"])
def test_paths_that_leave_the_folder_or_hide_are_refused(root, rel):
    (root.parent / "outside.png").write_bytes(_png())
    out = P.OutputPreview(root).get(_id(rel))
    assert out["ok"] is False and "outside" not in out["message"]


def test_a_symbolic_link_is_refused_to_a_file_and_to_a_folder(root, tmp_path):
    outside = tmp_path / "private"
    outside.mkdir()
    (outside / "plan.png").write_bytes(_png())
    os.symlink(outside / "plan.png", root / "link.png")
    os.symlink(outside, root / "linked-dir")
    pv = P.OutputPreview(root)
    assert pv.get(_id("link.png"))["ok"] is False
    assert pv.get(_id("linked-dir/plan.png"))["ok"] is False
    assert not any("link" in P.decode_id(i["id"]) for i in pv.list()["items"]), "links are not even listed"


def test_a_link_to_a_slide_image_inside_previews_is_not_followed(root, tmp_path):
    outside = tmp_path / "private.png"
    outside.write_bytes(_png((255, 0, 0)))
    slide = root / "20261024-101500-q3-board-deck" / "previews" / "slide-01.png"
    slide.unlink()
    os.symlink(outside, slide)
    out = P.OutputPreview(root).get(_id("20261024-101500-q3-board-deck/q3-board-deck.pptx"))
    assert out["ok"] is False or "image" not in out or base64.b64decode(out["image"]["data"]) != outside.read_bytes()


def test_a_root_reached_through_a_link_still_serves_only_inside_it(root, tmp_path):
    alias = tmp_path / "alias"
    os.symlink(root, alias)
    assert P.OutputPreview(alias).get(_id("chart.png"))["ok"] is True                   # the owner's configured root may be a link
    os.symlink(tmp_path, root / "up")
    assert P.OutputPreview(alias).get(_id("up/deliverables/chart.png"))["ok"] is False


def test_an_oversize_file_is_refused(root, monkeypatch):
    monkeypatch.setattr(P, "IMAGE_MAX", 100)
    (root / "big.png").write_bytes(_png(size=(300, 300)))
    out = P.OutputPreview(root).get(_id("big.png"))
    assert out["ok"] is False and "larger" in out["message"]
    monkeypatch.setattr(P, "TEXT_MAX", 10)
    (root / "big.txt").write_text("x" * 50, encoding="utf-8")
    assert P.OutputPreview(root).get(_id("big.txt"))["ok"] is False


def test_long_text_is_cut_not_refused(root, monkeypatch):
    monkeypatch.setattr(P, "TEXT_CHARS", 20)
    (root / "long.txt").write_text("y" * 100, encoding="utf-8")
    out = P.OutputPreview(root).get(_id("long.txt"))
    assert out["ok"] and out["truncated"] is True and len(out["text"]) == 20


def test_a_type_off_the_allowlist_or_a_name_that_lies_is_refused(root):
    (root / "run.exe").write_bytes(b"MZ\x90\x00")
    (root / "macro.xlsm").write_bytes(b"PK\x03\x04")
    (root / "fake.png").write_text("<html>not a png</html>", encoding="utf-8")
    (root / "fake.pdf").write_bytes(b"MZ not a pdf")
    (root / "fake.txt").write_bytes(b"\x00\x01\x02binary")
    (root / "fake.webp").write_bytes(b"RIFF1234WAVE")
    pv = P.OutputPreview(root)
    for name in ("run.exe", "macro.xlsm", "fake.png", "fake.pdf", "fake.txt", "fake.webp"):
        assert pv.get(_id(name))["ok"] is False, name
    assert not any(P.decode_id(i["id"]) in ("run.exe", "macro.xlsm") for i in pv.list()["items"])


SECRET_NAMES = (".env", "my-api-key.txt", "passwords.md", "id_rsa", "server.pem", "token.json", "credentials.json")


def test_a_secret_looking_name_is_refused_and_not_listed(root):
    for name in SECRET_NAMES:
        (root / name).write_text("x=1", encoding="utf-8")
    pv = P.OutputPreview(root)
    for name in SECRET_NAMES:
        assert pv.get(_id(name))["ok"] is False, name
    assert not {P.decode_id(i["id"]) for i in pv.list()["items"]} & set(SECRET_NAMES)
    assert pv.get(_id("a/secret/x.md"))["ok"] is False, "a secret-looking folder name counts too"


def test_secrets_inside_a_shown_file_are_removed_or_the_file_is_refused(root):
    (root / "cfg.txt").write_text("host=a\nOPENAI_API_KEY=sk-abcdefghijklmnopqrstuvwxyz0123\n", encoding="utf-8")
    out = P.OutputPreview(root).get(_id("cfg.txt"))
    assert out["ok"] and "sk-abcdef" not in out["text"] and out["redacted"]
    (root / "k.txt").write_text("-----BEGIN RSA PRIVATE KEY-----\nabc\n-----END RSA PRIVATE KEY-----\n", encoding="utf-8")
    assert P.OutputPreview(root).get(_id("k.txt"))["ok"] is False


def test_a_bad_id_is_a_refusal_not_a_crash(root):
    pv = P.OutputPreview(root)
    for bad in (None, 5, "", "***", "a" * 700, "AAAA"):
        assert pv.get(bad)["ok"] is False


def test_show_me_x_finds_the_item_queues_it_once_and_returns_no_content(root):
    pv = P.OutputPreview(root)
    out = pv.show("show me the board deck")
    assert out["ok"] and "Q3 Board Deck" in out["message"] and "base64" not in json.dumps(out)
    assert [P.decode_id(i) for i in pv.take_wanted()] == ["20261024-101500-q3-board-deck/q3-board-deck.pptx"] and pv.take_wanted() == []
    assert P.decode_id(pv.find("the flights report")["id"]).endswith("flights-report.pdf")
    assert P.decode_id(pv.find("show me my latest")["id"]) == "notes.md"
    assert P.decode_id(pv.find("the slides")["id"]).endswith(".pptx")
    assert pv.show("the quarterly zebra census")["ok"] is False and pv.take_wanted() == []


def test_the_preview_tools_exist_only_with_an_outputs_view_and_the_model_one_returns_a_sentence(root):
    server = F.build_server(tools(), outputs=P.OutputPreview(root))
    listed = asyncio.run(server.list_tools())
    names = {t.name for t in listed}
    assert {"jarvis_show", "jarvis_outputs", "jarvis_output_get", "jarvis_preview_wanted"} <= names
    assert "jarvis_show" not in {t.name for t in asyncio.run(F.build_server(tools()).list_tools())}
    by_name = {t.name: t for t in listed}
    assert all("PREVIEW PANEL ONLY" in by_name[n].description for n in ("jarvis_outputs", "jarvis_output_get", "jarvis_preview_wanted"))
    shown = str(asyncio.run(server.call_tool("jarvis_show", {"what": "flights report"})))
    assert "Showing" in shown and "iVBOR" not in shown
    assert "Hyderabad flights" in str(asyncio.run(server.call_tool("jarvis_outputs", {"limit": 5})))
    first = asyncio.run(server.call_tool("jarvis_preview_wanted", {}))
    assert "flights-report.pdf" not in str(first), "ids are opaque"
    assert not any(w in n for n in names for w in ("approve", "deny", "kill", "policy", "pin"))


def _make_report(root, name="20261025-090000-new-report", title="New report"):
    folder = root / name
    folder.mkdir()
    (folder / "new-report.pdf").write_bytes(_pdf())
    (folder / "spec.json").write_text(json.dumps({"title": title}), encoding="utf-8")
    return folder


def _finish(folder):
    (folder / "qa.json").write_text(json.dumps({"file": "new-report.pdf", "kind": "pdf", "qa": {"passed": True}}), encoding="utf-8")


def test_a_folder_still_being_made_is_not_listed_until_its_qa_json_exists(root):
    folder = _make_report(root)
    pv = P.OutputPreview(root)
    assert not any(i["title"] == "New report" for i in pv.list()["items"])
    _finish(folder)
    assert any(i["title"] == "New report" for i in pv.list()["items"])


def test_a_deliverable_that_finishes_while_running_opens_the_panel_once(root):
    pv = P.OutputPreview(root)
    assert pv.take_wanted() == [], "what was already there is not announced"
    folder = _make_report(root)
    assert pv.take_wanted() == [], "not before qa.json says it is finished"
    _finish(folder)
    ids = pv.take_wanted()
    assert [P.decode_id(i) for i in ids] == ["20261025-090000-new-report/new-report.pdf"]
    assert pv.take_wanted() == []


def test_what_was_made_long_ago_or_with_auto_off_is_not_announced(root):
    started = P.OutputPreview(root)
    folder = _make_report(root)
    _finish(folder)
    made = os.stat(folder / "new-report.pdf").st_mtime
    late = P.OutputPreview(root, clock=lambda: made + 3600)
    late._baseline = 0
    assert late.take_wanted() == [] and late.take_wanted() == [], "an old one is skipped quietly"
    off = P.OutputPreview(root, auto=False)
    off._baseline = 0
    assert off.take_wanted() == []
    assert started.take_wanted() != [], "the same folder is new for a view that started before it"


def test_a_title_has_no_direction_override_zero_width_or_control_characters(root):
    # found on the real laptop: a file named "invoice<RLO>gpj.exe.pdf" was listed with the override in its title (it would read as "invoiceexe.jpg")
    RLO, ZWSP, LRI, BEL = chr(0x202E), chr(0x200B), chr(0x2066), chr(7)
    hostile = root / "20261025-000000-hostile"
    hostile.mkdir()
    main = "invoice" + RLO + "gpj.exe.pdf"
    (hostile / main).write_bytes(_pdf())
    (hostile / "spec.json").write_text(json.dumps({"title": "Q4" + RLO + " plan" + ZWSP + BEL + " final" + LRI}), encoding="utf-8")
    (hostile / "qa.json").write_text(json.dumps({"file": str(hostile / main), "kind": "pdf", "qa": {"passed": True}}), encoding="utf-8")
    titles = [i["title"] for i in P.OutputPreview(root).list()["items"]]
    assert "Q4 plan final" in titles
    only = root / "20261025-000001-name-only"
    only.mkdir()
    (only / ("evil" + RLO + "gpj.exe.pdf")).write_bytes(_pdf())
    titles = [i["title"] for i in P.OutputPreview(root).list()["items"]]
    assert len(titles) >= 2 and not any(P.INVISIBLE.search(t) for t in titles), titles


# D76: a task that made a file queues it for the panel by its path
def test_show_path_queues_one_plain_file_of_the_folder_and_nothing_else(root, tmp_path):
    op = P.OutputPreview(root)
    (root / "20261027-120000-comparison.csv").write_text("a,b\n1,2\n", encoding="utf-8")
    assert op.show_path(str(root / "20261027-120000-comparison.csv")) is True
    assert op.take_wanted() == [P.encode_id("20261027-120000-comparison.csv")]
    outside = tmp_path / "elsewhere.csv"
    outside.write_text("a\n", encoding="utf-8")
    assert op.show_path(str(outside)) is False and op.show_path(str(root / "no-such.csv")) is False and op.show_path(str(root / ".." / "x.csv")) is False
    (root / "api-token.csv").write_text("a\n", encoding="utf-8")
    assert op.show_path(str(root / "api-token.csv")) is False                       # a secret-looking name is never shown
    assert op.take_wanted() == []
