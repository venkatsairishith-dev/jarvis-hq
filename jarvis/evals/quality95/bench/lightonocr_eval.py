"""LightOnOCR-3-0.8B (GGUF, llama-server multimodal path) on THREE SYNTHETIC documents this script draws itself (never a real document).

    python lightonocr_eval.py --model <Q4_K_M.gguf> --mmproj <mmproj-f16.gguf> [--server <llama-server.exe>] [--port 8091]

Draws made-up documents with Pillow (an invoice with a table, a letter, a form with checkboxes), starts llama-server with `-m` and `--mmproj`, asks for the text of each
image through /v1/chat/completions (image as a data URL), and prints per document: seconds, characters, character error rate against the known text
(edit distance / reference length, after collapsing whitespace) and word recall. Numbers only; nothing is adopted. Results go to results/lightonocr-<time>.json.
"""
from __future__ import annotations

import argparse
import base64
import io
import json
import os
import re
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

HERE = Path(__file__).resolve().parent
DOCS = {
    "invoice": ["INVOICE 2026-0417", "Bill to: Example Trading Ltd, 12 Sample Road, Testville", "Item            Qty    Price    Total",
                "Widget A         4    12.50    50.00", "Gadget B         2    30.00    60.00", "Service fee       1    15.00    15.00",
                "Total due: 125.00 USD", "Payment terms: 30 days"],
    "letter": ["Dear Sample Customer,", "Thank you for your order of the blue notebook set.", "It will ship on Monday and arrive within five working days.",
               "If anything is wrong, reply to this letter and we will fix it.", "Kind regards,", "The Example Shop team"],
    "form": ["REGISTRATION FORM", "Name: Jane Q. Example", "Date of birth: 01/02/1990", "[x] I agree to the terms    [ ] Send me the newsletter",
             "Room: B-204    Floor: 2", "Signature: ______________"],
}


def font(size: int):
    for name in ("consola.ttf", "cour.ttf", "arial.ttf"):
        try:
            return ImageFont.truetype(os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts", name), size)
        except OSError:
            continue
    return ImageFont.load_default()


def draw(lines: list[str]) -> Image.Image:
    f = font(26)
    img = Image.new("RGB", (1100, 90 + 46 * len(lines)), "white")
    d = ImageDraw.Draw(img)
    for i, line in enumerate(lines):
        d.text((40, 40 + 46 * i), line, fill="black", font=f)
    return img


def norm(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip()


def edit_distance(a: str, b: str) -> int:
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def ask(port: int, img: Image.Image) -> tuple[str, float]:
    buf = io.BytesIO()
    img.save(buf, "PNG")
    url = "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()
    body = {"model": "x", "max_tokens": 700, "temperature": 0, "messages": [{"role": "user", "content": [
        {"type": "image_url", "image_url": {"url": url}}, {"type": "text", "text": "Transcribe all the text in this image exactly."}]}]}
    t0 = time.time()
    req = urllib.request.Request(f"http://127.0.0.1:{port}/v1/chat/completions", data=json.dumps(body).encode(), headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=300) as r:
        out = json.loads(r.read())
    return out["choices"][0]["message"].get("content") or "", time.time() - t0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", required=True)
    ap.add_argument("--mmproj", required=True)
    ap.add_argument("--server", default=r"D:\jarvis\tools\llama.cpp\llama-server.exe")
    ap.add_argument("--port", type=int, default=8091)
    ap.add_argument("--ngl", default="0", help="GPU layers (0 = CPU only, which keeps the 4 GB GPU for the other models)")
    a = ap.parse_args()
    proc = subprocess.Popen([a.server, "-m", a.model, "--mmproj", a.mmproj, "--port", str(a.port), "-ngl", a.ngl, "-c", "4096", "--host", "127.0.0.1"],
                            stdout=subprocess.DEVNULL, stderr=open(HERE / "results" / "lightonocr-server.log", "w"))
    try:
        for _ in range(120):
            try:
                urllib.request.urlopen(f"http://127.0.0.1:{a.port}/health", timeout=2).read()
                break
            except Exception:
                time.sleep(2)
        else:
            print("server did not become ready")
            return 2
        rows = {}
        for name, lines in DOCS.items():
            text, secs = ask(a.port, draw(lines))
            ref = norm("\n".join(lines))
            got = norm(text)
            cer = edit_distance(ref, got) / max(1, len(ref))
            words = set(re.findall(r"\w+", ref.lower()))
            rec = len(words & set(re.findall(r"\w+", got.lower()))) / max(1, len(words))
            rows[name] = {"seconds": round(secs, 1), "chars": len(got), "cer": round(cer, 3), "word_recall": round(rec, 3)}
            print(f"{name:8} {secs:6.1f}s  chars {len(got):4d}  CER {cer:.3f}  word recall {rec:.3f}", flush=True)
        out = HERE / "results" / f"lightonocr-{time.strftime('%Y%m%d-%H%M%S')}.json"
        out.write_text(json.dumps({"model": os.path.basename(a.model), "mmproj": os.path.basename(a.mmproj), "docs": rows}, indent=1), encoding="utf-8")
        print("wrote", out)
        return 0
    finally:
        proc.terminate()


if __name__ == "__main__":
    sys.exit(main())
