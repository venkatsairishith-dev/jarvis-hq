"""Fixtures for scripts/preview-check.mjs: a small deliverables folder (a deck with slide images, a PDF, a PNG, markdown, code and hostile
HTML), opened through the real jarvis.bridge.output_preview, written out as the tool results the face receives.

    python make_fixtures.py OUT.json BEACON_URL

BEACON_URL is a local address the check watches: nothing in the page may ever request it.
"""
from __future__ import annotations

import io
import json
import sys
import tempfile
from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[6]
sys.path.insert(0, str(ROOT))

from jarvis.bridge.output_preview import OutputPreview, decode_id  # noqa: E402


def page_png(title: str, color: tuple, size=(640, 360)) -> bytes:
    im = Image.new("RGB", size, color)
    d = ImageDraw.Draw(im)
    d.rectangle((24, 24, size[0] - 24, 90), fill=(255, 255, 255))
    d.text((40, 48), title, fill=(20, 20, 40))
    for i in range(4):
        d.rectangle((40, 130 + i * 44, size[0] - 120 - i * 40, 150 + i * 44), fill=(255, 255, 255))
    buf = io.BytesIO()
    im.save(buf, "PNG")
    return buf.getvalue()


def make(root: Path, beacon: str) -> None:
    deck = root / "20261024-101500-q3-board-deck"
    (deck / "previews").mkdir(parents=True)
    (deck / "q3-board-deck.pptx").write_bytes(b"PK\x03\x04" + b"\0" * 60)
    for i, color in enumerate([(70, 80, 200), (40, 140, 120), (200, 110, 60), (150, 70, 160)], 1):
        (deck / "previews" / f"slide-{i:02d}.png").write_bytes(page_png(f"Q3 board deck - slide {i}", color))
    (deck / "spec.json").write_text(json.dumps({"title": "Q3 board deck"}), encoding="utf-8")
    (deck / "qa.json").write_text(json.dumps({"file": "q3-board-deck.pptx", "kind": "pptx", "qa": {"passed": True}}), encoding="utf-8")

    rep = root / "20261024-111500-flights-report"
    rep.mkdir()
    pages = [Image.open(io.BytesIO(page_png(f"Hyderabad flights - page {i}", (245, 245, 250), (420, 560)))).convert("RGB") for i in (1, 2)]
    pages[0].save(rep / "flights-report.pdf", "PDF", save_all=True, append_images=pages[1:])
    (rep / "spec.json").write_text(json.dumps({"title": "Hyderabad flights"}), encoding="utf-8")
    (rep / "qa.json").write_text(json.dumps({"file": "flights-report.pdf", "kind": "pdf", "qa": {"passed": False}}), encoding="utf-8")

    (root / "chart.png").write_bytes(page_png("Sample chart", (30, 120, 200), (720, 400)))
    (root / "notes.md").write_text(
        "# Trip notes\n\nThe **flight** leaves at 07:10; see [the airline](" + beacon + "/click-md) for the booking.\n\n"
        "![remote picture](" + beacon + "/beacon-md.png)\n\n- pack passport\n- charge laptop\n\n| day | plan |\n|---|---|\n| Mon | arrive |\n| Tue | meetings |\n\n"
        "```python\ndef total(items):\n    return sum(i.price for i in items)\n```\n", encoding="utf-8")
    (root / "tools.py").write_text("import json\n\n\ndef load(path):\n    \"\"\"Read a JSON file.\"\"\"\n    with open(path, encoding=\"utf-8\") as fh:\n        return json.load(fh)\n", encoding="utf-8")
    (root / "hostile.html").write_text(
        '<meta http-equiv="refresh" content="0;url=' + beacon + '/refresh"><base href="' + beacon + '/base/">'
        '<link rel="stylesheet" href="' + beacon + '/style.css"><style>@import url(' + beacon + '/import.css);'
        'body{background:url(' + beacon + '/bg.png)} h1{color:#2b46c9}</style>'
        '<h1>Sample page</h1><p>Plain <b>HTML</b> from a report.</p>'
        '<script>window.__pwned = 1; fetch("' + beacon + '/script")</script>'
        '<img src="' + beacon + '/beacon.png" onerror="window.__pwned = 2" alt="remote image">'
        '<img src="x:y" onerror="window.__pwned = 3"><a id="evil" href="' + beacon + '/click">a link</a>'
        '<form action="' + beacon + '/form" method="post"><input name="q"><button>go</button></form>'
        '<iframe src="' + beacon + '/frame"></iframe><svg><use href="' + beacon + '/sprite.svg#a"/></svg>'
        '<p onclick="window.__pwned = 4">click me</p>', encoding="utf-8")


def main() -> int:
    out_path, beacon = Path(sys.argv[1]), sys.argv[2]
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "deliverables"
        make(root, beacon)
        pv = OutputPreview(root)
        listing = pv.list(20)
        gets = {}
        for item in listing["items"]:
            rel = decode_id(item["id"])
            first = pv.get(item["id"], 0, True)
            gets[item["id"]] = {"0": first}
            if first.get("view") == "pages":
                for p in range(1, first["page_count"]):
                    gets[item["id"]][str(p)] = pv.get(item["id"], p, False)
            first["open_path"] = "FIXTURE/" + rel                       # the real path is a temp folder that is gone after this
            gets[item["id"]]["0"] = first
        out_path.write_text(json.dumps({"outputs": listing, "get": gets}), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
