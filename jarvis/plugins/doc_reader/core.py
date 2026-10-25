"""Read a scanned document or a picture of a page, on this PC, with a local OCR model. English documents only.

The page images go to LightOnOCR-3-0.8B (Q4_K_M + mmproj-f16) through llama.cpp's own multimodal path: llama-swap (127.0.0.1:8080,
model alias `docreader`) in front of llama-server. The call shape is the one measured in jarvis/evals/quality95/bench/lightonocr_eval.py
(image as a data URL on /v1/chat/completions; the text sometimes comes back in `reasoning_content`). On the owner's laptop (CPU) a page took
4 to 11 s. Hindi was not tested and is not needed.

This file holds the logic and the safety rules; it is tested without a model (the server call is injectable):

  * only the one file the owner names: an absolute path, no `..`, no network (UNC) path, no stream (`:`), no link / junction / reparse point
    in any part of the path (checked part by part, then the file is opened without following a link and looked at again), no
    secret-looking name, nothing under jarvis-secrets / .ssh / .gnupg / .aws / .azure / AppData / Windows / Program Files;
  * a type allowlist (pdf, png, jpg, jpeg, tif, tiff, webp, bmp) and the first bytes must agree with the name; at most 25 MB;
  * PDF pages are drawn with pypdfium2 (Apache-2.0 / BSD-3, already in the agent venv; PDFium runs no script), at most 10 pages by default,
    never more than 30, the longer side 1540 px (about 140 dpi on a letter page, 300 dpi at most on a small one) so a page and its answer
    fit the model's 4096-token window;
  * what the model wrote has its reasoning removed, invisible and instruction-shaped text defused (`_shared/untrusted`), and secrets,
    card numbers and real IBANs replaced (`_shared/screen_text.scrub_text`, the cleaner used for screen text). The result says which kinds
    were removed, never the values.

Nothing here opens a socket except the one call to the loopback model server (`_shared/loopback_http`: no proxy, no redirect, 127.0.0.1
only). No telemetry. The text of a document is data for a model to read, never instructions.
"""
from __future__ import annotations

import base64
import io
import json
import os
import re
import stat
import time
import urllib.error
import urllib.request
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any, Callable

from jarvis.plugins._shared import loopback_http
from jarvis.plugins._shared.screen_text import scrub_text
from jarvis.plugins._shared.untrusted import clean_untrusted
from jarvis.plugins.doc_tools.core import BLOCKED_DIR_NAMES

MODEL = "docreader"                                        # llama-swap alias (jarvis/local_models/llama-swap.yaml)
DEFAULT_URL = "http://127.0.0.1:8080/v1"
PROMPT = "Transcribe all the text in this image exactly."   # the prompt measured in lightonocr_eval.py
MAX_BYTES = 25 * 1024 * 1024
DEFAULT_PAGES = 10
MAX_PAGES = 30
LONG_EDGE = 1540                                           # px; the context is 4096 tokens, an A4 page at this size is ~1.6-2.1k image tokens
MAX_SCALE = 300 / 72                                       # never draw a small page above 300 dpi
MAX_PIXELS = 120_000_000                                   # an image header that claims more is refused before it is decoded
MAX_TOKENS = 1800                                          # answer budget per page (4096 - image tokens)
PAGE_TIMEOUT_S = 240.0                                     # the first call may have to load the model
TOTAL_BUDGET_S = 840.0                                     # stop starting new pages after this (the plugin entry times out at 900)
PAGE_CHARS_MAX = 12_000

EXTENSIONS = (".pdf", ".png", ".jpg", ".jpeg", ".tif", ".tiff", ".webp", ".bmp")
SUPPORTED = "PDF, PNG, JPG, TIFF, WEBP or BMP"

# A name that looks like it holds credentials is never read, whatever is inside (same list as jarvis/bridge/output_preview.py, plus ppk,
# wallet, seed and the word "key" on its own: "monkey.png" is fine, "my-key.png" is not).
SECRET_NAME = re.compile(r"(?i)(secret|passw|credential|token|private|apikey|api[_-]?key|\.env|id_rsa|id_ed25519|\.pem$|\.key$|\.ppk$|"
                         r"\.pfx$|\.p12$|\.kdbx$|\.keystore$|\.npmrc$|\.netrc$|\.htpasswd$|\.jks$|wallet|seed[_ -]?phrase|"
                         r"(?<![a-z])keys?(?![a-z]))")
BLOCKED_DIRS = {n.lower() for n in BLOCKED_DIR_NAMES} | {"jarvis-secrets", ".ssh", ".gnupg", ".aws", ".azure", ".kube", ".jarvis"}
WIN_RESERVED = re.compile(r"(?i)^(con|prn|aux|nul|com[0-9]|lpt[0-9])(\..*)?$")
DRIVE = re.compile(r"^[A-Za-z]:[\\/]")
_CONTROL = re.compile(r"[\x00-\x1f\x7f-\x9f]")


class DocReaderError(Exception):
    """A refusal or a failure whose message is safe to show to the owner. It never repeats the path it was given."""


# ---------------------------------------------------------------- the path rules
def _is_link_like(st: os.stat_result) -> bool:
    if stat.S_ISLNK(st.st_mode):
        return True
    return bool(getattr(st, "st_file_attributes", 0) & 0x400)           # Windows reparse point (junction, symlink, mount point, cloud stub)


def check_path(path_str: Any) -> Path:
    """The path of a plain file the owner named, or a refusal. The text rules first, then the file system rules."""
    raw = str(path_str or "").strip().strip('"').strip("'")
    if not raw:
        raise DocReaderError("Say which file to read: give its full path.")
    if len(raw) > 400 or _CONTROL.search(raw):
        raise DocReaderError("That path cannot be used.")
    if raw.startswith(("\\\\", "//")):
        raise DocReaderError("Network paths are not read; copy the file to this PC first.")
    if not (DRIVE.match(raw) or PurePosixPath(raw).is_absolute() or PureWindowsPath(raw).is_absolute()):
        raise DocReaderError("Give the full path of the file.")
    if ":" in (raw[2:] if DRIVE.match(raw) else raw):
        raise DocReaderError("That path cannot be used.")                # a data stream ("file.pdf:stream") or a device path
    parts = [p for p in re.split(r"[\\/]+", raw) if p]
    if DRIVE.match(raw):
        parts = parts[1:]
    if not parts:
        raise DocReaderError("That path is a folder, not a file.")
    for p in parts:
        if p in (".", "..") or p != p.strip() or p.endswith(".") or WIN_RESERVED.match(p):
            raise DocReaderError("That path cannot be used.")
    if any(p.lower() in BLOCKED_DIRS for p in parts[:-1]) or parts[-1].lower() in BLOCKED_DIRS:
        raise DocReaderError("That file is in a protected folder and will not be read.")
    if any(SECRET_NAME.search(p) for p in parts):
        raise DocReaderError("That file looks like it holds credentials, so it will not be read.")
    ext = os.path.splitext(parts[-1])[1].lower()
    if ext not in EXTENSIONS:
        raise DocReaderError(f"I can read {SUPPORTED} files, not '{ext or 'no extension'}'.")
    path = Path(raw)
    cur = Path(path.anchor) if path.anchor else path
    try:
        for part in path.parts[len(Path(path.anchor).parts) if path.anchor else 0:]:
            cur = cur / part
            if _is_link_like(os.lstat(cur)):
                raise DocReaderError("Links and junctions are not followed; give the real path of the file.")
    except OSError:
        raise DocReaderError("I cannot find that file.") from None
    if not stat.S_ISREG(os.lstat(path).st_mode):
        raise DocReaderError("That is not a plain file.")
    return path


def read_regular(path: Path, cap: int | None = None) -> bytes:
    """The bytes of a regular file of at most `cap` bytes (default MAX_BYTES), opened without following a link and checked again after opening."""
    cap = MAX_BYTES if cap is None else cap
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0) | getattr(os, "O_CLOEXEC", 0)
    try:
        fd = os.open(path, flags)
    except OSError:
        raise DocReaderError("I cannot open that file.") from None
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode) or _is_link_like(st):
            raise DocReaderError("That is not a plain file.")
        if st.st_size > cap:
            raise DocReaderError(f"That file is larger than the {cap // (1024 * 1024)} MB limit.")
        if st.st_size == 0:
            raise DocReaderError("That file is empty.")
        with os.fdopen(fd, "rb", closefd=False) as fh:
            data = fh.read(cap + 1)
    finally:
        os.close(fd)
    if len(data) > cap:
        raise DocReaderError(f"That file is larger than the {cap // (1024 * 1024)} MB limit.")
    return data


def sniff(data: bytes, ext: str) -> str:
    """"pdf" or "image" when the first bytes agree with the extension, else a refusal."""
    ext = ext.lower()
    if ext == ".pdf":
        ok = data.startswith(b"%PDF-")
    elif ext == ".png":
        ok = data.startswith(b"\x89PNG\r\n\x1a\n")
    elif ext in (".jpg", ".jpeg"):
        ok = data.startswith(b"\xff\xd8\xff")
    elif ext in (".tif", ".tiff"):
        ok = data[:4] in (b"II*\x00", b"MM\x00*")
    elif ext == ".webp":
        ok = data[:4] == b"RIFF" and data[8:12] == b"WEBP"
    elif ext == ".bmp":
        ok = data.startswith(b"BM")
    else:
        ok = False
    if not ok:
        raise DocReaderError("The inside of that file does not match its name, so it will not be read.")
    return "pdf" if ext == ".pdf" else "image"


def clamp_pages(value: Any) -> int:
    try:
        n = int(value)
    except (TypeError, ValueError):
        return DEFAULT_PAGES
    return max(1, min(n, MAX_PAGES))


# ---------------------------------------------------------------- pages to images
def _png(img) -> bytes:
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


def _flat_rgb(img):
    """RGB on white (transparent and palette images, CMYK, 16-bit), turned upright by its EXIF orientation."""
    from PIL import Image, ImageOps
    img = ImageOps.exif_transpose(img)
    if img.mode in ("RGBA", "LA") or (img.mode == "P" and "transparency" in img.info):
        rgba = img.convert("RGBA")
        base = Image.new("RGB", rgba.size, "white")
        base.paste(rgba, mask=rgba.split()[-1])
        return base
    return img.convert("RGB")


def _fit(img):
    from PIL import Image
    if max(img.size) > LONG_EDGE:
        img = img.copy()
        img.thumbnail((LONG_EDGE, LONG_EDGE), Image.LANCZOS)
    return img


def render_pdf(data: bytes, max_pages: int) -> tuple[list[bytes], int]:
    """(PNG bytes of the first `max_pages` pages, the PDF's page count)."""
    try:
        import pypdfium2 as pdfium
    except ImportError:
        raise DocReaderError("The PDF page renderer (pypdfium2) is not installed.") from None
    try:
        doc = pdfium.PdfDocument(data)
    except Exception:  # noqa: BLE001 - a damaged or password-protected PDF is "cannot open", not a crash
        raise DocReaderError("That PDF could not be opened (it may be damaged or password protected).") from None
    out: list[bytes] = []
    try:
        total = len(doc)
        if total < 1:
            raise DocReaderError("That PDF has no pages.")
        for i in range(min(total, max_pages)):
            page = doc[i]
            try:
                w, h = page.get_size()
                if not (w > 0 and h > 0):
                    raise DocReaderError("A page of that PDF has no size.")
                scale = min(LONG_EDGE / max(w, h), MAX_SCALE)
                out.append(_png(_flat_rgb(page.render(scale=scale).to_pil())))
            except DocReaderError:
                raise
            except Exception:  # noqa: BLE001
                raise DocReaderError(f"Page {i + 1} of that PDF could not be drawn.") from None
            finally:
                page.close()
    finally:
        doc.close()
    return out, total


def render_image(data: bytes, ext: str, max_pages: int) -> tuple[list[bytes], int]:
    """(PNG bytes, count): one page for a picture, one per frame (up to `max_pages`) for a multi-page TIFF."""
    try:
        from PIL import Image
    except ImportError:
        raise DocReaderError("The image library (Pillow) is not installed.") from None
    try:
        img = Image.open(io.BytesIO(data))
        if img.width * img.height > MAX_PIXELS:
            raise DocReaderError("That picture is too large to read safely.")
        frames = max(1, int(getattr(img, "n_frames", 1))) if ext.lower() in (".tif", ".tiff") else 1
        out: list[bytes] = []
        for i in range(min(frames, max_pages)):
            img.seek(i)
            if img.width * img.height > MAX_PIXELS:
                raise DocReaderError("That picture is too large to read safely.")
            out.append(_png(_fit(_flat_rgb(img))))
        return out, frames
    except DocReaderError:
        raise
    except Exception:  # noqa: BLE001
        raise DocReaderError("That picture could not be decoded (it may be damaged).") from None


# ---------------------------------------------------------------- the model's answer
_THINK = re.compile(r"<think>.*?</think>", re.S | re.I)
_IMAGE_LINE = re.compile(r"(?m)^[ \t]*!\[[^\]\n]*\]\([^)\n]*\)[ \t]*\n?")        # "![image](image_1.png)": the picture itself, not text
_SPECIAL = re.compile(r"<\|[a-z_]{2,20}\|>|</?s>|<(?:bos|eos|pad)>", re.I)


def strip_reasoning(text: str) -> str:
    """The transcription without <think> blocks (closed, unclosed, or closed with no opening tag), special tokens and the image marker."""
    t = str(text or "")
    t = _THINK.sub("", t)
    if "</think>" in t.lower():                                   # a template that opened the block in the prompt: only the end tag is in the answer
        t = re.split(r"(?i)</think>", t)[-1]
    t = re.split(r"(?i)<think>", t)[0]                           # an unclosed block runs to the end: all of it is reasoning
    t = _SPECIAL.sub("", t)
    t = _IMAGE_LINE.sub("", t)
    return t.strip()


def answer_text(message: dict) -> str:
    """The transcription from a chat message: `content`, or `reasoning_content` when that is empty (this model's template puts it there)."""
    return str(message.get("content") or message.get("reasoning_content") or "")


def server_url() -> str:
    return loopback_http.check_loopback(os.environ.get("DOC_READER_URL", "").strip() or DEFAULT_URL).rstrip("/")


def transcribe_http(png: bytes, *, base_url: str | None = None, timeout: float = PAGE_TIMEOUT_S) -> tuple[str, str]:
    """(raw answer, finish_reason) for one page image, from the local llama-server through llama-swap. Loopback only."""
    try:
        url = (base_url or server_url()).rstrip("/") + "/chat/completions"
        loopback_http.check_loopback(url)
    except loopback_http.LoopbackError as exc:
        raise DocReaderError(str(exc)) from None
    data_url = "data:image/png;base64," + base64.b64encode(png).decode("ascii")
    body = {"model": MODEL, "max_tokens": MAX_TOKENS, "temperature": 0, "messages": [{"role": "user", "content": [
        {"type": "image_url", "image_url": {"url": data_url}}, {"type": "text", "text": PROMPT}]}]}
    req = urllib.request.Request(url, data=json.dumps(body).encode("utf-8"), headers={"Content-Type": "application/json"})
    try:
        with loopback_http.urlopen(req, timeout=timeout) as r:
            out = json.loads(r.read().decode("utf-8", "replace"))
        choice = out["choices"][0]
        return answer_text(choice["message"]), str(choice.get("finish_reason") or "")
    except urllib.error.HTTPError as exc:
        raise DocReaderError(f"The local model server refused the page (HTTP {exc.code}). Is the 'docreader' model in the llama-swap "
                             "config and its files on this PC? A very large page can also be too big for its window.") from None
    except TimeoutError:
        raise DocReaderError("The local document reader took too long on a page.") from None
    except (urllib.error.URLError, ConnectionError, OSError):
        raise DocReaderError("The local document reader is not running (nothing answered on the llama-swap port). "
                             "Start the local models and try again.") from None
    except (KeyError, IndexError, TypeError, ValueError):
        raise DocReaderError("The local document reader gave an answer I could not read.") from None



def clean_page(raw: str) -> tuple[str, list[str]]:
    """One page's transcription made safe to hand on: reasoning out, invisible and instruction-shaped text defused, then secrets, card
    numbers and real IBANs replaced. Returns (clean text, names of the rules that fired); a page holding a private key is withheld whole."""
    text = clean_untrusted(strip_reasoning(raw))[:PAGE_CHARS_MAX]
    clean, hits = scrub_text(text)
    if "private_key" in hits:
        return "[this page held a private key and was removed]", hits
    return re.sub(r"\n{3,}", "\n\n", clean).strip(), hits


REMOVED_WORDS = {"github_token": "a GitHub token", "provider_key": "an API key", "jwt": "a login token", "bearer": "a login token",
                 "pin_or_code": "a PIN or one-time code", "key_assignment": "a password or key", "private_key": "a private key",
                 "card_number": "a card number", "bank_account": "a bank account number"}


def removed_sentence(hits: list[str]) -> str:
    kinds = sorted({REMOVED_WORDS.get(h, h) for h in hits})
    return ("Removed from the text before anything read it: " + ", ".join(kinds) + ".") if kinds else ""


# ---------------------------------------------------------------- the whole read
Transcribe = Callable[[bytes], "str | tuple[str, str]"]


def read_document(path_str: Any, max_pages: Any = None, *, transcribe: Transcribe | None = None, clock: Callable[[], float] = time.monotonic,
                  budget_s: float = TOTAL_BUDGET_S) -> dict[str, Any]:
    """Read the file the owner named. `transcribe(png_bytes)` is the model call (an answer string, or (answer, finish_reason)); the default
    asks the local llama-server. Returns {pages, pages_total, chars, text, page_texts, took_s, removed, notes}; the text is already cleaned."""
    t0 = clock()
    limit = clamp_pages(DEFAULT_PAGES if max_pages is None else max_pages)
    path = check_path(path_str)
    data = read_regular(path)
    kind = sniff(data, path.suffix)
    images, total = render_pdf(data, limit) if kind == "pdf" else render_image(data, path.suffix, limit)
    ask = transcribe or transcribe_http
    notes: list[str] = []
    page_texts: list[str] = []
    hits_all: list[str] = []
    for i, png in enumerate(images, 1):
        if i > 1 and clock() - t0 > budget_s:
            notes.append(f"Stopped after page {i - 1}: the time limit for one read was reached.")
            break
        try:
            got = ask(png)
        except DocReaderError:
            if i == 1:
                raise
            notes.append(f"Stopped after page {i - 1}: the document reader stopped answering.")
            break
        raw, finish = (got, "") if isinstance(got, str) else (str(got[0]), str(got[1]))
        text, hits = clean_page(raw)
        hits_all += [h for h in hits if h not in hits_all]
        if finish == "length":
            notes.append(f"Page {i} is longer than the reader's answer limit, so its end may be cut off.")
        page_texts.append(text or "(no text found on this page)")
    read = len(page_texts)
    if total > read and not any(n.startswith("Stopped") for n in notes):
        notes.append(f"Read the first {read} of {total} pages; ask for more pages (at most {MAX_PAGES}) to read further.")
    marked = [f"--- page {i} of {total} ---\n{t}" for i, t in enumerate(page_texts, 1)]
    text = "\n\n".join(marked)
    out: dict[str, Any] = {"pages": read, "pages_total": total, "chars": len(text), "text": text, "page_texts": page_texts,
                           "took_s": round(clock() - t0, 1), "removed": hits_all, "notes": notes}
    sentence = removed_sentence(hits_all)
    if sentence:
        out["notes"] = [sentence] + notes
    return out


# ---------------------------------------------------------------- the total of an invoice, found here and shown on screen
# Asked "what is the total?" the model must not need the page text (it stays on this PC unless the owner allows screen text). So the reader finds the
# total itself, with fixed rules over the cleaned page text, draws it on a small card in the deliverables folder (the face's Preview panel opens it) and
# tells the model only THAT it is on screen. Never the amount, never any other value of the page.
TOTAL_LABELS = (          # (priority, label pattern): the highest priority found wins; the last one on the last page among equals
    (5, r"grand\s+total"),
    (4, r"total\s+(?:amount\s+)?due|amount\s+due|balance\s+due|total\s+payable|amount\s+payable|net\s+payable|pay\s+this\s+amount"),
    (3, r"invoice\s+total|total\s+amount|total\s+invoice|total\s+to\s+pay|total\s+charges?"),
    (2, r"total"),
)
NOT_A_TOTAL = re.compile(r"(?i)sub\s*-?\s*total|total\s+(?:before|excl|exclud|ex\b|net\b)|total\s+(?:tax|vat|gst|discount|qty|quantity|items?|weight|hours?)|tax\s+total|total\s+savings|previous\s+balance")
AMOUNT = re.compile(r"(?<![\w.,])(?:(?P<pre>[$€£₹¥]|USD|EUR|GBP|INR|Rs\.?)\s*)?(?P<num>\d{1,3}(?:[ ,.]\d{3})+(?:[.,]\d{1,2})?|\d+(?:[.,]\d{1,2})?)(?:\s*(?P<post>USD|EUR|GBP|INR|Rs\.?|[$€£₹¥]))?(?![\w%])")
CURRENCY_WORDS = {"$": "USD", "€": "EUR", "£": "GBP", "₹": "INR", "¥": "JPY", "rs": "INR", "rs.": "INR"}


def _table_text(page: str) -> str:
    """A page the model returned as an HTML table, as plain lines: cells joined by ' | ', one line per row, no tags."""
    page = re.sub(r"(?i)</t[dh]>\s*", " | ", page)
    page = re.sub(r"(?i)</tr>|<br\s*/?>|</p>|</div>", "\n", page)
    page = re.sub(r"<[^>\n]{1,200}>", " ", page)
    return page.replace("&amp;", "&").replace("&nbsp;", " ")


def _normalise_number(raw: str) -> str | None:
    """'1,234.56' / '1.234,56' / '1 234,56' / '325.89' -> '1234.56'; None when it is not a plain amount."""
    s = raw.strip()
    if not re.fullmatch(r"\d[\d ,.]*", s):
        return None
    seps = [m.start() for m in re.finditer(r"[,.]", s)]
    if not seps:
        return s.replace(" ", "")
    last = seps[-1]
    tail = s[last + 1:]
    if len(tail) in (1, 2):                                   # the last separator is the decimal point
        whole, frac = s[:last], tail
    else:                                                     # three digits after it: a thousands separator, no fraction
        whole, frac = s, ""
    whole = re.sub(r"[ ,.]", "", whole)
    return f"{whole}.{frac}" if frac else whole


def find_total(page_texts: list[str]) -> dict[str, str] | None:
    """The invoice total in the cleaned page texts: {"label", "amount", "currency"} or None. Only a line that names a total gives an amount; a sub-total, a tax
    line, a percentage or a quantity never does. Several candidates: the strongest label, then the last one in the document."""
    best: tuple[int, int, int, dict[str, str]] | None = None
    for page_no, page in enumerate(page_texts):
        for line_no, line in enumerate(_table_text(page).splitlines()):
            line = line.strip(" |\t")
            if not line or NOT_A_TOTAL.search(line):
                continue
            for priority, pattern in TOTAL_LABELS:
                m = re.search(r"(?i)\b(" + pattern + r")\b[^\d$€£₹¥\n]{0,25}", line)
                if not m:
                    continue
                rest = line[m.end(1):]
                amounts = list(AMOUNT.finditer(rest))
                if not amounts:
                    break
                a = amounts[-1] if "|" in rest else amounts[0]          # in a table row the amount is the last cell, in a sentence the first number
                number = _normalise_number(a.group("num"))
                if number is None or float(number) <= 0:
                    break
                cur_raw = (a.group("pre") or a.group("post") or "").strip().lower()
                currency = CURRENCY_WORDS.get(cur_raw, cur_raw.upper())
                label = re.sub(r"\s+", " ", m.group(1)).strip()
                cand = (priority, page_no, line_no, {"label": label[:1].upper() + label[1:], "amount": number, "currency": currency})
                if best is None or cand[:3] >= best[:3]:
                    best = cand
                break
    return best[3] if best else None


def total_card_png(total: dict[str, str]) -> bytes:
    """A small picture of the total for the face's Preview panel (drawn here, no network)."""
    from PIL import Image, ImageDraw, ImageFont
    fonts = os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts")

    def font(name: str, size: int):
        for candidate in (os.path.join(fonts, name), name, "DejaVuSans.ttf"):
            try:
                return ImageFont.truetype(candidate, size)
            except OSError:
                continue
        return ImageFont.load_default()
    img = Image.new("RGB", (720, 300), "#ffffff")
    d = ImageDraw.Draw(img)
    d.rectangle([0, 0, 719, 12], fill="#2563eb")
    d.text((40, 40), "Invoice total", font=font("arial.ttf", 30), fill="#475569")
    d.text((40, 100), f"{total['amount']} {total['currency']}".strip(), font=font("arialbd.ttf", 84), fill="#0f172a")
    d.text((40, 220), f"from the line \"{total['label']}\"", font=font("arial.ttf", 26), fill="#475569")
    d.text((40, 258), "Read on this PC. Nothing was sent to any model.", font=font("arial.ttf", 20), fill="#94a3b8")
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


def _deliverables_root() -> Path:
    try:
        from jarvis.plugins.deliverables import tools
        return Path(tools.OUT_ROOT)
    except Exception:                                          # noqa: BLE001 - the same setting the deliverables plugin reads
        return Path(os.environ.get("DELIVERABLES_OUT_DIR", r"D:\jarvis\outputs\deliverables"))


def write_total_card(total: dict[str, str], out_root: "str | os.PathLike | None" = None, clock: Callable[[], float] = time.time) -> Path:
    """Put the card in a new folder of the deliverables folder, the way the deliverables plugin leaves a finished piece (picture, spec.json with the title, qa.json
    last), so the Preview panel lists it and opens it by itself. Returns the folder."""
    root = Path(out_root) if out_root else _deliverables_root()
    folder = root / (time.strftime("%Y%m%d-%H%M%S", time.localtime(clock())) + "-invoice-total")
    folder.mkdir(parents=True, exist_ok=False)
    png = folder / "invoice-total.png"
    png.write_bytes(total_card_png(total))
    (folder / "spec.json").write_text(json.dumps({"title": "Invoice total"}), encoding="utf-8")
    (folder / "qa.json").write_text(json.dumps({"file": str(png), "folder": str(folder), "kind": "image",
                                                "qa": {"passed": True, "checks": {"drawn_locally": True}, "warnings": [], "counts": {}}}), encoding="utf-8")
    return folder
