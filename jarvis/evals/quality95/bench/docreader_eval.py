r"""Synthetic documents (drawn here with made-up text, a made-up invoice with the public test card number and the public example IBAN; nothing of the owner's)
through doc_reader.core.read_document with the real local model (llama-swap alias `docreader` on 127.0.0.1:8080) -> seconds per page, word recall, character error rate,
whether a table survives, and whether the cleaner removed the fake secrets (kinds reported, values never). Numbers only; results go to results/docreader-<time>.json.

    python docreader_eval.py [folder for the generated files]      default: D:\jarvis\cache\docreader-test
"""
import json
import os
import re
import sys
import time
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[3]))                   # the repo root: jarvis.plugins...
sys.path.insert(0, str(HERE))
from jarvis.plugins.doc_reader import core  # noqa: E402
from lightonocr_eval import edit_distance, norm  # noqa: E402

OUT = Path(sys.argv[1] if len(sys.argv) > 1 else r"D:\jarvis\cache\docreader-test")
OUT.mkdir(parents=True, exist_ok=True)
FONTS = os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts")


def font(size, name="arial.ttf"):
    return ImageFont.truetype(os.path.join(FONTS, name), size)


FAKE_CARD = "4111 1111 1111 1111"            # the public test card number
FAKE_PASSWORD_LINE = "password: hunter2"
FAKE_IBAN = "GB82 WEST 1234 5698 7654 32"    # the public example IBAN
ROWS = [("Item", "Qty", "Unit price", "Total"), ("Blue notebook set", "4", "12.50", "50.00"), ("Desk lamp", "2", "30.00", "60.00"),
        ("Shipping", "1", "15.00", "15.00")]


def invoice():
    img = Image.new("RGB", (1240, 1000), "white")
    d = ImageDraw.Draw(img)
    f, fb = font(30), font(40, "arialbd.ttf")
    d.text((60, 50), "INVOICE 2026-0417", font=fb, fill="black")
    d.text((60, 120), "Bill to: Example Trading Ltd, 12 Sample Road, Testville", font=f, fill="black")
    x0, y0, widths, rh = 60, 200, (520, 140, 240, 240), 64
    for r, row in enumerate(ROWS):
        x = x0
        for c, cell in enumerate(row):
            d.rectangle([x, y0 + r * rh, x + widths[c], y0 + (r + 1) * rh], outline="black", width=2)
            d.text((x + 14, y0 + r * rh + 16), cell, font=fb if r == 0 else f, fill="black")
            x += widths[c]
    y = y0 + len(ROWS) * rh + 40
    clean_lines = ["Total due: 125.00 USD", "Payment terms: 30 days"]
    secret_lines = [f"Card on file: {FAKE_CARD}", FAKE_PASSWORD_LINE, f"Pay by transfer to IBAN {FAKE_IBAN}"]
    for line in clean_lines + secret_lines:
        d.text((60, y), line, font=f, fill="black")
        y += 52
    img.save(OUT / "invoice-table.png")
    ref = [" ".join(r) for r in ROWS] + ["INVOICE 2026-0417", "Bill to: Example Trading Ltd, 12 Sample Road, Testville"] + clean_lines
    return ref


PAGES = [
    ["Quarterly summary", "", "Sales rose in the third quarter because the new blue notebook set sold well.", "The warehouse in Testville shipped 4,200 parcels.",
     "Returns stayed below two percent of all orders.", "The team plans to hire two people in spring."],
    ["Operations", "", "The Monday meeting moves to ten o'clock from next week.", "Please send the signed forms to the front desk by Friday.",
     "The printer on the second floor needs new toner.", "Lunch is provided on the last day of the month."],
    ["Closing notes", "", "Thank you to everyone who helped with the October stocktake.", "The next review is on the first of December.",
     "Questions can be sent to the Example Shop team.", "Kind regards, The Example Shop team"],
]


def pdf3():
    pages = []
    for lines in PAGES:
        img = Image.new("RGB", (1240, 1754), "white")
        d = ImageDraw.Draw(img)
        y = 120
        for i, line in enumerate(lines):
            d.text((90, y), line, font=font(44 if i == 0 else 30, "arialbd.ttf" if i == 0 else "arial.ttf"), fill="black")
            y += 90 if i == 0 else 60
        pages.append(img)
    pages[0].save(OUT / "report-3pages.pdf", save_all=True, append_images=pages[1:], resolution=150.0)
    return [ln for p in PAGES for ln in p if ln]


def rotated(deg):
    img = Image.new("L", (1240, 900), 255)
    d = ImageDraw.Draw(img)
    lines = ["Dear Sample Customer,", "Thank you for your order of the blue notebook set.", "It will ship on Monday and arrive within five working days.",
             "If anything is wrong, reply to this letter and we will fix it.", "Kind regards,", "The Example Shop team"]
    y = 120
    for line in lines:
        d.text((90, y), line, font=font(34), fill=0)
        y += 80
    img = img.rotate(deg, expand=True, fillcolor=235, resample=Image.BICUBIC)
    import random
    random.seed(7)
    px = img.load()
    for _ in range(25000):                                         # scanner noise
        x, y2 = random.randrange(img.width), random.randrange(img.height)
        px[x, y2] = max(0, px[x, y2] - random.randrange(60))
    name = f"letter-scan-rot{deg}.png"
    img.convert("RGB").save(OUT / name)
    return name, lines


def score(ref_lines, text):
    body = re.sub(r"(?m)^--- page \d+ of \d+ ---$", "", text)
    ref, got = norm("\n".join(ref_lines)), norm(body)
    words = set(re.findall(r"\w+", ref.lower()))
    rec = len(words & set(re.findall(r"\w+", got.lower()))) / max(1, len(words))
    cer = edit_distance(ref, got) / max(1, len(ref))
    return round(rec, 3), round(cer, 3)


def table_survives(text):
    """Every cell present and in reading order row by row; also whether the text uses table markup."""
    low = text.lower()
    pos, ok = 0, True
    for row in ROWS:
        for cell in row:
            i = low.find(cell.lower(), pos)
            if i < 0:
                ok = False
                break
            pos = i + len(cell)
        if not ok:
            break
    return ok, bool(re.search(r"\|\s*-{2,}|<table|<tr", text, re.I))


ROWS_OUT = []


def run(name, ref, **kw):
    p = OUT / name
    t0 = time.time()
    res = core.read_document(str(p), **kw)
    wall = time.time() - t0
    rec, cer = score(ref, res["text"])
    per = round(res["took_s"] / max(1, res["pages"]), 1)
    leaked = [v for v in (FAKE_CARD.replace(" ", ""), "4111", "hunter2", "WEST 1234", "GB82") if v.lower() in res["text"].lower().replace(" ", "") or v.lower() in res["text"].lower()]
    row = ({"file": name, "pages": res["pages"], "of": res["pages_total"], "seconds_total": res["took_s"], "seconds_per_page": per, "chars": res["chars"],
                      "word_recall": rec, "cer": cer, "removed_kinds": res["removed"], "leaked_values": leaked, "notes": res["notes"]})
    ROWS_OUT.append(row)
    print(json.dumps(row), flush=True)
    return res


if __name__ == "__main__":
    ref_inv = invoice()
    ref_pdf = pdf3()
    n4, ref_l = rotated(4)
    n90, _ = rotated(90)
    n_4 = rotated(-6)[0]
    res = run("invoice-table.png", ref_inv)
    ok, markup = table_survives(res["text"])
    print("TABLE: all 16 cells present in reading order:", ok, "| table markup (markdown/HTML):", markup)
    print("INVOICE TEXT (cleaned, from the model, made-up data):\n" + res["text"][:900])
    run("report-3pages.pdf", ref_pdf)
    run(n4, ref_l)
    run(n_4, ref_l)
    run(n90, ref_l)
    out = HERE.parent / "results" / f"docreader-{time.strftime('%Y%m%d-%H%M%S')}.json"
    out.write_text(json.dumps({"time": time.strftime("%Y-%m-%d %H:%M:%S"), "table_cells_in_order": ok, "table_markup": markup, "rows": ROWS_OUT}, indent=1), encoding="utf-8")
    print("wrote", out)
