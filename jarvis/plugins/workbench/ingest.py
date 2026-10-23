"""Turn a client's files into segments: small pieces of text that each say exactly where they came from.

Segment: {"id": "S12", "source": "F3", "ref": {"line": 4} | {"message": 7, "time": "12/10/2026 14:05"} | {"time": "00:01:02"}
          | {"page": 2}, "speaker": "Ravi" | "", "text": "...", "lang": "en|hi|te|hi-Latn|te-Latn|mixed|other",
          "to_ai": bool}

Formats: WhatsApp exports (Android and iOS lines), Telegram JSON exports, generic JSON message lists, "Name: text" chat
logs, SRT / VTT subtitles, "[00:01:02] Speaker: text" transcripts, PDF, Word, Markdown, text, CSV / TSV. Audio goes
through ``transcribe``; images and spreadsheets without a reader are kept as reference files with what is known about
them (no OCR here). Text is cleaned (``safety.clean``) and every segment that reads as an instruction to an AI is marked
``to_ai``: it stays visible as evidence and is never treated as a client request.

Adding files obeys Document Tools' rules: only from the folders the owner allows, never credential-looking names or
system folders (``doc_tools.core``'s own lists), and a copy goes into the project; the original is not touched.
"""
from __future__ import annotations

import csv
import io
import json
import re
from pathlib import Path
from typing import Any

from . import safety
from .projects import Project, ProjectError, read_json, write_json

AUDIO = {".wav", ".mp3", ".m4a", ".ogg", ".opus", ".flac", ".aac", ".webm", ".amr", ".3gp"}
SUBTITLE = {".srt", ".vtt"}
IMAGE = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".tif", ".tiff", ".heic"}
SHEET = {".xlsx", ".xls", ".ods"}
DOC = {".pdf", ".docx", ".md", ".markdown", ".html", ".htm", ".rtf"}
TEXTISH = {".txt", ".log", ".json", ".csv", ".tsv", ""}
MAX_SEGMENT = 2000
MAX_SEGMENTS_PER_FILE = 5000


def check_readable(path_str: str) -> Path:
    """Document Tools' read rules (allowed folders, protected names and folders); audio may be larger than 25 MB."""
    from jarvis.plugins.doc_tools import core as dt

    raw = str(path_str or "").strip().strip('"')
    if not raw:
        raise ProjectError("No file path was given.")
    p = Path(raw)
    if not p.is_absolute():
        raise ProjectError("Give the full path of the file.")
    if p.is_symlink():
        raise ProjectError("That path is a link; give the file itself.")
    try:
        real = p.resolve(strict=True)
    except OSError:
        raise ProjectError("That file does not exist.")
    if not real.is_file():
        raise ProjectError("That path is not a file.")
    if {x.lower() for x in real.parts} & dt.BLOCKED_DIR_NAMES or dt.BLOCKED_NAME.search(real.name):
        raise ProjectError("That file is protected and will not be read.")
    if not any(real == r or r in real.parents for r in dt.read_roots()):
        raise ProjectError("That file is outside the folders JARVIS may read (Documents, Downloads, Desktop, outputs, "
                           "the study vault, or DOC_TOOLS_READ_ROOTS).")
    return real


def kind_of(path: Path, head: bytes = b"") -> str:
    ext = path.suffix.lower()
    if ext in AUDIO:
        return "audio"
    if ext in SUBTITLE:
        return "transcript"
    if ext in IMAGE:
        return "image"
    if ext in SHEET:
        return "spreadsheet"
    if ext in (".csv", ".tsv"):
        return "table"
    if ext in DOC:
        return "document"
    if ext == ".json":
        return "chat" if _looks_like_chat_json(head) else "document"
    if ext in TEXTISH:
        text = head.decode("utf-8", "replace")
        if _TIME_LINE.search(text) and not _CHAT_LINE.search(text):
            return "transcript"
        if _CHAT_LINE.search(text) or _WA_ANDROID.search(text) or _WA_IOS.search(text):
            return "chat"
        return "document"
    return "other"


def _looks_like_chat_json(head: bytes) -> bool:
    t = head.decode("utf-8", "replace")
    return bool(re.search(r'"(messages|from|sender|author)"\s*:', t))


# ---------------------------------------------------------------- language (by script; a hint, not a translation)
_TELUGU = re.compile("[ఀ-౿]")
_DEVANAGARI = re.compile("[ऀ-ॿ]")
_LATIN = re.compile("[A-Za-z]")
_HI_LATN = re.compile(r"(?i)\b(hai|hain|nahi|nahin|chahiye|karna|kariye|kijiye|bhai|accha|achha|jaldi|kal|aaj|matlab|"
                      r"theek|thik|zaroor|bilkul|kyunki|lekin|abhi|hoga|hogi|dena|dijiye)\b")
_TE_LATN = re.compile(r"(?i)\b(kavali|kaavali|cheyyandi|cheyandi|ledu|undi|ayindi|chala|bagundi|ivvandi|pampandi|"
                      r"repu|ippudu|kuda|kooda|emi|enti|ela|cheppandi|meeru|manam|anna|garu)\b")


def detect_language(text: str) -> str:
    t = str(text or "")
    te, hi, la = len(_TELUGU.findall(t)), len(_DEVANAGARI.findall(t)), len(_LATIN.findall(t))
    total = te + hi + la
    if not total:
        return "other"
    if te / total > 0.6:
        return "te"
    if hi / total > 0.6:
        return "hi"
    if la / total > 0.6:
        te_l, hi_l = len(_TE_LATN.findall(t)), len(_HI_LATN.findall(t))
        if te_l >= 2 and te_l > hi_l:
            return "te-Latn"
        if hi_l >= 2 and hi_l >= te_l:
            return "hi-Latn"
        return "en"
    return "mixed"


# ---------------------------------------------------------------- line formats
_WA_ANDROID = re.compile(r"^(\d{1,2}[/.-]\d{1,2}[/.-]\d{2,4}),?\s+(\d{1,2}:\d{2}(?::\d{2})?\s?(?:[AaPp]\.?[Mm]\.?)?)\s+[-–]\s+"
                         r"([^:]{1,60}):\s?(.*)$", re.M)
_WA_IOS = re.compile(r"^‎?\[(\d{1,2}[/.-]\d{1,2}[/.-]\d{2,4}),?\s+(\d{1,2}:\d{2}(?::\d{2})?\s?(?:[AaPp]\.?[Mm]\.?)?)\]\s+"
                     r"([^:]{1,60}):\s?(.*)$", re.M)
_TIME_LINE = re.compile(r"^\s*\[?(\d{1,2}:\d{2}(?::\d{2})?(?:[.,]\d{1,3})?)\]?\s+(?:([^:\]]{1,40}):\s+)?(.+)$", re.M)
_SPEAKER_TIME = re.compile(r"^\s*([^:()\[\]]{1,40})\s*[(\[](\d{1,2}:\d{2}(?::\d{2})?)[)\]]\s*:\s*(.+)$", re.M)
_CHAT_LINE = re.compile(r"^\s*([A-Za-zऀ-ॿఀ-౿][\w .'ऀ-ॿఀ-౿-]{0,38}):\s+(.+)$", re.M)
_SRT_TIME = re.compile(r"(\d{1,2}:\d{2}:\d{2})[.,]\d{1,3}\s*-->\s*\d{1,2}:\d{2}:\d{2}[.,]\d{1,3}")
_VTT_TIME = re.compile(r"((?:\d{1,2}:)?\d{2}:\d{2})\.\d{3}\s*-->\s*(?:\d{1,2}:)?\d{2}:\d{2}\.\d{3}")
_VTT_VOICE = re.compile(r"^<v\s+([^>]{1,40})>(.*?)(?:</v>)?$")


def _seg(source: str, ref: dict[str, Any], speaker: str, text: str) -> dict[str, Any] | None:
    raw = " ".join(str(text or "").split())
    if not raw:
        return None
    flagged = safety.addressed_to_ai(raw)
    cleaned = safety.clean(raw, MAX_SEGMENT)
    return {"source": source, "ref": ref, "speaker": safety.clean(speaker, 60).strip(), "text": cleaned,
            "lang": detect_language(cleaned), "to_ai": flagged}


def parse_chat_text(source: str, text: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    n = 0
    for lineno, line in enumerate(text.splitlines(), 1):
        m = _WA_IOS.match(line) or _WA_ANDROID.match(line)
        if m:
            n += 1
            current = {"ref": {"message": n, "time": f"{m.group(1)} {m.group(2)}".strip(), "line": lineno},
                       "speaker": m.group(3), "text": m.group(4)}
            out.append(current)
            continue
        m2 = _CHAT_LINE.match(line)
        if m2 and not re.match(r"(?i)^\s*(https?|note|ps|re|subject|to|from|date|cc)\s*:", line):
            n += 1
            current = {"ref": {"message": n, "line": lineno}, "speaker": m2.group(1), "text": m2.group(2)}
            out.append(current)
            continue
        if line.strip() and current is not None:          # a message that runs over several lines
            current["text"] += " " + line.strip()
        elif line.strip():
            n += 1
            current = {"ref": {"message": n, "line": lineno}, "speaker": "", "text": line.strip()}
            out.append(current)
    return [s for s in (_seg(source, c["ref"], c["speaker"], c["text"]) for c in out) if s]


def _json_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list):                            # Telegram: text can be a list of strings and {"text": ...}
        return "".join(_json_text(v.get("text", "")) if isinstance(v, dict) else _json_text(v) for v in value)
    return "" if value is None else str(value)


def parse_chat_json(source: str, raw: str) -> list[dict[str, Any]]:
    try:
        data = json.loads(raw)
    except ValueError:
        return parse_chat_text(source, raw)
    rows = data.get("messages") if isinstance(data, dict) else data
    if not isinstance(rows, list):
        return []
    out = []
    for i, r in enumerate(rows[:MAX_SEGMENTS_PER_FILE], 1):
        if not isinstance(r, dict):
            continue
        speaker = _json_text(r.get("from") or r.get("sender") or r.get("author") or r.get("name") or "")
        text = _json_text(r.get("text") if "text" in r else r.get("message") or r.get("content") or r.get("body") or "")
        when = _json_text(r.get("date") or r.get("time") or r.get("ts") or r.get("timestamp") or "")
        ref = {"message": i}
        if when:
            ref["time"] = when[:40]
        s = _seg(source, ref, speaker, text)
        if s:
            out.append(s)
    return out


def parse_transcript(source: str, text: str, suffix: str = "") -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    if suffix in (".srt", ".vtt") or _SRT_TIME.search(text) or text.lstrip().startswith("WEBVTT"):
        blocks = re.split(r"\n\s*\n", text.replace("\r\n", "\n"))
        for b in blocks:
            lines = [x for x in b.strip().splitlines() if x.strip()]
            stamp = None
            body: list[str] = []
            for ln in lines:
                m = _SRT_TIME.search(ln) or _VTT_TIME.search(ln)
                if m:
                    stamp = m.group(1)
                elif stamp is not None and not ln.strip().isdigit():
                    body.append(ln.strip())
            if stamp is None or not body:
                continue
            joined = " ".join(body)
            speaker = ""
            vm = _VTT_VOICE.match(joined)
            if vm:
                speaker, joined = vm.group(1), vm.group(2)
            else:
                cm = re.match(r"^([^:]{1,40}):\s+(.+)$", joined)
                if cm and len(cm.group(1).split()) <= 4:
                    speaker, joined = cm.group(1), cm.group(2)
            s = _seg(source, {"time": stamp}, speaker, joined)
            if s:
                out.append(s)
        return out
    for lineno, line in enumerate(text.splitlines(), 1):
        m = _SPEAKER_TIME.match(line)
        if m:
            s = _seg(source, {"time": m.group(2), "line": lineno}, m.group(1), m.group(3))
        else:
            m = _TIME_LINE.match(line)
            if m:
                s = _seg(source, {"time": m.group(1), "line": lineno}, m.group(2) or "", m.group(3))
            elif out and line.strip():
                out[-1]["text"] = safety.clean(out[-1]["text"] + " " + line.strip(), MAX_SEGMENT)
                out[-1]["to_ai"] = out[-1]["to_ai"] or safety.addressed_to_ai(line)
                continue
            else:
                s = _seg(source, {"line": lineno}, "", line) if line.strip() else None
        if s:
            out.append(s)
    return out


def parse_document_text(source: str, text: str, page_texts: list[str] | None = None) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    if page_texts:
        for pno, ptext in enumerate(page_texts, 1):
            for para in re.split(r"\n\s*\n", ptext):
                s = _seg(source, {"page": pno}, "", para)
                if s:
                    out.append(s)
        return out
    line = 1
    for para in re.split(r"(\n\s*\n)", text):
        if para.strip() and not re.fullmatch(r"\n\s*\n", para):
            s = _seg(source, {"line": line}, "", para)
            if s:
                out.append(s)
        line += para.count("\n")
    return out


def parse_table(source: str, text: str, delimiter: str = ",") -> list[dict[str, Any]]:
    out = []
    reader = csv.reader(io.StringIO(text), delimiter=delimiter)
    header: list[str] | None = None
    for i, row in enumerate(reader, 1):
        if i > MAX_SEGMENTS_PER_FILE:
            break
        if header is None:
            header = row
            continue
        pairs = "; ".join(f"{h}: {v}" for h, v in zip(header, row) if str(v).strip())
        s = _seg(source, {"row": i}, "", pairs)
        if s:
            out.append(s)
    return out


def _pdf_pages(path: Path) -> list[str]:
    from pypdf import PdfReader

    from jarvis.plugins.doc_tools.core import MAX_PDF_PAGES
    reader = PdfReader(str(path))
    if reader.is_encrypted:
        raise ProjectError("That PDF is password-protected; it was not read.")
    return [(p.extract_text() or "") for p in reader.pages[:MAX_PDF_PAGES]]


def extract(project: Project, row: dict[str, Any], transcriber=None) -> dict[str, Any]:
    """Segments for one source, plus a note when something could not be read. Never raises for a bad file."""
    path = project.path("sources", str(row["file"]))
    kind, sid, suffix = row.get("kind"), row["id"], path.suffix.lower()
    note = ""
    segs: list[dict[str, Any]] = []
    try:
        if kind == "audio":
            from . import transcribe
            got = transcribe.transcribe(path, transcriber=transcriber, language=row.get("language", ""))
            note = got.get("note", "")
            if got.get("text") or got.get("segments"):
                segs = transcribe.to_segments(sid, got)
            row["transcription"] = {k: got.get(k) for k in ("status", "provider", "language") if got.get(k)}
        elif kind == "transcript":
            segs = parse_transcript(sid, path.read_text(encoding="utf-8", errors="replace"), suffix)
        elif kind == "chat":
            raw = path.read_text(encoding="utf-8", errors="replace")
            segs = parse_chat_json(sid, raw) if suffix == ".json" else parse_chat_text(sid, raw)
        elif kind == "table":
            segs = parse_table(sid, path.read_text(encoding="utf-8", errors="replace"), "\t" if suffix == ".tsv" else ",")
        elif kind == "document":
            if suffix == ".pdf":
                pages = _pdf_pages(path)
                segs = parse_document_text(sid, "", pages)
                if pages and not any(p.strip() for p in pages):
                    note = "no text layer: the PDF may be scanned; OCR is not available here"
            elif suffix == ".docx":
                from jarvis.plugins.doc_tools.core import _read_docx
                segs = parse_document_text(sid, _read_docx(path))
            elif suffix in (".html", ".htm"):
                from bs4 import BeautifulSoup
                segs = parse_document_text(sid, BeautifulSoup(path.read_text(encoding="utf-8", errors="replace"),
                                                               "html.parser").get_text("\n"))
            elif suffix == ".json":
                segs = parse_document_text(sid, path.read_text(encoding="utf-8", errors="replace"))
            else:
                segs = parse_document_text(sid, path.read_text(encoding="utf-8", errors="replace"))
        elif kind == "spreadsheet":
            segs, note = _sheet(sid, path)
        elif kind == "image":
            note = "kept as a reference image; its text is not read here (no OCR in this workflow)"
            row["image"] = _image_meta(path)
        else:
            note = "kept as a reference file; its content is not read here"
    except ProjectError as e:
        note = str(e)
    except Exception as e:  # noqa: BLE001 - a broken file: say so, never echo parser internals
        note = f"could not be read ({type(e).__name__})"
    segs = segs[:MAX_SEGMENTS_PER_FILE]
    return {"source": sid, "segments": segs, "note": note}


def _sheet(sid: str, path: Path) -> tuple[list[dict[str, Any]], str]:
    try:
        import openpyxl
    except ImportError:
        return [], "spreadsheet kept as a reference file: openpyxl is not installed here, so its cells were not read"
    wb = openpyxl.load_workbook(str(path), read_only=True, data_only=True)
    segs: list[dict[str, Any]] = []
    for ws in wb.worksheets[:20]:
        header = None
        for i, row in enumerate(ws.iter_rows(values_only=True), 1):
            if i > 2000:
                break
            vals = ["" if v is None else str(v) for v in row]
            if header is None:
                header = vals
                continue
            pairs = "; ".join(f"{h}: {v}" for h, v in zip(header, vals) if v.strip())
            s = _seg(sid, {"sheet": ws.title[:40], "row": i}, "", pairs)
            if s:
                segs.append(s)
    return segs, ""


def _image_meta(path: Path) -> dict[str, Any]:
    try:
        from PIL import Image
        with Image.open(path) as im:
            return {"format": im.format, "width": im.width, "height": im.height}
    except Exception:  # noqa: BLE001
        return {}


def add_files(project: Project, paths: list[str], roles: dict[str, str] | None = None,
              languages: dict[str, str] | None = None) -> dict[str, Any]:
    """Copy the files into the project. Each path is checked on its own; one refusal does not stop the others."""
    added, refused = [], []
    for p in paths:
        try:
            real = check_readable(p)
            with real.open("rb") as f:
                head = f.read(4096)
            row = project.add_source(real, kind_of(real, head), (roles or {}).get(p, ""))
            if languages and languages.get(p):
                lang = str(languages[p]).lower()
                if lang in ("en", "hi", "te"):
                    manifest = project.manifest()
                    for r in manifest:
                        if r["id"] == row["id"]:
                            r["language"] = lang
                    write_json(project.path("sources", "manifest.json"), manifest)
            added.append({k: row.get(k) for k in ("id", "original_name", "kind", "duplicate") if row.get(k) is not None})
        except ProjectError as e:
            refused.append({"file": Path(str(p)).name[:80], "why": str(e)})
    return {"added": added, "refused": refused}


def extract_all(project: Project, transcriber=None) -> dict[str, Any]:
    """Extract every source, number the segments S1..Sn and save extracted/segments.json."""
    manifest = project.manifest()
    all_segs: list[dict[str, Any]] = []
    notes = []
    for row in manifest:
        got = extract(project, row, transcriber=transcriber)
        all_segs.extend(got["segments"])
        if got["note"]:
            notes.append({"source": row["id"], "note": got["note"]})
    write_json(project.path("sources", "manifest.json"), manifest)        # transcription status, image meta
    for i, s in enumerate(all_segs, 1):
        s["id"] = f"S{i}"
    write_json(project.path("extracted", "segments.json"), all_segs)
    langs: dict[str, int] = {}
    for s in all_segs:
        langs[s["lang"]] = langs.get(s["lang"], 0) + 1
    project.log("extracted", segments=len(all_segs), flagged=sum(1 for s in all_segs if s["to_ai"]))
    return {"segments": len(all_segs), "languages": langs, "notes": notes,
            "flagged_instructions": sum(1 for s in all_segs if s["to_ai"])}


def segments(project: Project) -> list[dict[str, Any]]:
    data = read_json(project.path("extracted", "segments.json"), [])
    return data if isinstance(data, list) else []
