"""VM2 download quarantine: what a lab agent's Firefox saved is looked at HERE, on VM2, before anything crosses to the owner's PC (D79).

Runs inside lab_control.py (root, Unix socket behind the hub's /control/ route). It reads the quarantine folder of the agent user and says, for each file:
name, size, sha256, the type by its MAGIC BYTES (never by its name or what a page claimed), whether the name and the content disagree, whether the type is risky
(program, script, archive, Office file with macros, anything unknown), and the answer of an antivirus scanner IF one is already installed (clamscan; nothing is
installed or downloaded by this module). A file that is too big is listed but never read or served.

The folder belongs to the agent user, so everything in it is untrusted: the folder and each file are opened with O_NOFOLLOW from a directory descriptor, only regular
files owned by that user count, a link or a device is skipped, and bytes are read in chunks up to the cap. Nothing here executes, opens, extracts or renders a file.
Standard library only; the checks are pure functions so the tests run anywhere.
"""
import hashlib
import io
import os
import re
import shutil
import stat
import subprocess
import zipfile

QUARANTINE = os.environ.get("JARVIS_VM2_QUARANTINE", "/home/agent/quarantine")
MAX_BYTES = 50 * 1024 * 1024
MAX_LISTED = 200
CHUNK = 1 << 16
PARTIAL = re.compile(r"(?i)(\.part|\.crdownload|\.tmp|\.download)$")

# (prefix, type, kind)
MAGIC = (
    (b"MZ", "Windows program (PE)", "executable"),
    (b"\x7fELF", "Linux program (ELF)", "executable"),
    (b"\xca\xfe\xba\xbe", "Java class or Mach-O", "executable"),
    (b"\xcf\xfa\xed\xfe", "Mach-O program", "executable"),
    (b"%PDF-", "PDF document", "pdf"),
    (b"\x89PNG\r\n\x1a\n", "PNG picture", "image"),
    (b"\xff\xd8\xff", "JPEG picture", "image"),
    (b"GIF87a", "GIF picture", "image"),
    (b"GIF89a", "GIF picture", "image"),
    (b"\x1f\x8b", "gzip archive", "archive"),
    (b"Rar!\x1a\x07", "RAR archive", "archive"),
    (b"7z\xbc\xaf\x27\x1c", "7-Zip archive", "archive"),
    (b"BZh", "bzip2 archive", "archive"),
    (b"\xfd7zXZ\x00", "xz archive", "archive"),
    (b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1", "old Office or installer (OLE2)", "office_old"),
    (b"#!", "script (shebang)", "script"),
)
SCRIPT_EXT = {".sh", ".bash", ".ps1", ".bat", ".cmd", ".vbs", ".js", ".jse", ".wsf", ".py", ".pl", ".rb", ".hta", ".scr", ".lnk", ".reg"}
EXE_EXT = {".exe", ".dll", ".msi", ".com", ".sys", ".jar", ".apk", ".dmg", ".pkg", ".deb", ".rpm", ".appimage", ".so"}
ARCHIVE_EXT = {".zip", ".rar", ".7z", ".tar", ".gz", ".tgz", ".bz2", ".xz", ".iso", ".cab"}
OFFICE_EXT = {".docx", ".xlsx", ".pptx", ".docm", ".xlsm", ".pptm", ".doc", ".xls", ".ppt", ".rtf", ".odt", ".ods", ".odp"}
PDF_EXT, IMAGE_EXT, TEXT_EXT = {".pdf"}, {".png", ".jpg", ".jpeg", ".gif"}, {".txt", ".csv", ".md", ".json", ".xml", ".html", ".htm", ".log"}
SAFE_KINDS = {"pdf", "image", "text", "office"}            # everything else is risky: asked about with a warning, never opened by JARVIS on the PC
MAX_ZIP_NAMES = 4000


def clean_name(name):
    """A file name fit to show: no path, no control or direction-override characters, at most 120 characters."""
    base = os.path.basename(str(name or "").replace("\\", "/"))
    base = "".join(c for c in base if c.isprintable() and c not in "‪‫‬‭‮⁦⁧⁨⁩​‌‍﻿")
    return base[:120] or "file"


def _ext(name):
    return os.path.splitext(str(name).lower())[1]


def sniff_zip(head_bytes_reader):
    """For a ZIP-family file: (type, kind). Only the central directory's NAMES are read; nothing is extracted."""
    try:
        with zipfile.ZipFile(head_bytes_reader) as z:
            names = [i.filename for i in z.infolist()[:MAX_ZIP_NAMES]]
    except (zipfile.BadZipFile, OSError, ValueError, NotImplementedError, RuntimeError):
        return "ZIP archive (unreadable)", "archive"
    low = [n.lower() for n in names]
    if any(n.endswith("vbaproject.bin") for n in low):
        return "Office file with macros (VBA)", "office_macro"
    if "[content_types].xml" in low and any(n.startswith(("word/", "xl/", "ppt/")) for n in low):
        return "Office document (OOXML)", "office"
    if "meta-inf/manifest.mf" in low and any(n.endswith(".class") for n in low):
        return "Java archive (JAR)", "executable"
    if "androidmanifest.xml" in low:
        return "Android package (APK)", "executable"
    return "ZIP archive", "archive"


def classify(name, head, whole=None):
    """(type words, kind) from the first bytes (and, for a ZIP, the whole file when it is small enough). Content decides; the name only adds a mismatch flag."""
    for prefix, words, kind in MAGIC:
        if head.startswith(prefix):
            if kind == "executable" and words.startswith("Java class") and _ext(name) not in (".class",):
                return "Java class or Mach-O program", "executable"
            return words, kind
    if head.startswith((b"PK\x03\x04", b"PK\x05\x06")):
        return sniff_zip(io.BytesIO(whole)) if whole is not None else ("ZIP archive", "archive")
    if head[257:262] == b"ustar":
        return "tar archive", "archive"
    probe = head[:2048].lstrip(b"\xef\xbb\xbf \t\r\n").lower()
    if probe.startswith((b"<!doctype html", b"<html", b"<script")):
        return "web page (HTML)", "html"
    if head and all(c in b"\t\r\n" or 32 <= c < 127 or c >= 0xC2 for c in head[:1024]):
        return "plain text", "text"
    return "unknown binary", "unknown"


def mismatch(name, kind):
    """True when the name claims something the content is not (a .pdf that is a program, an .exe that is plain text...)."""
    ext = _ext(name)
    claimed = ("pdf" if ext in PDF_EXT else "image" if ext in IMAGE_EXT else "executable" if ext in EXE_EXT else "archive" if ext in ARCHIVE_EXT
               else "office" if ext in OFFICE_EXT else "script" if ext in SCRIPT_EXT else "text" if ext in TEXT_EXT else "")
    if not claimed:
        return False
    ok = {"pdf": {"pdf"}, "image": {"image"}, "executable": {"executable", "archive"}, "archive": {"archive"},
          "office": {"office", "office_old", "office_macro", "archive"}, "script": {"script", "text"}, "text": {"text", "html"}}[claimed]
    return kind not in ok


def scan(path, run=subprocess.run, which=shutil.which):
    """The antivirus answer if clamscan is already installed: {tool, result: clean|infected|error|not_installed}. Never installs, never downloads signatures."""
    exe = which("clamscan")
    if not exe:
        return {"tool": "", "result": "not_installed"}
    try:
        p = run([exe, "--no-summary", "--stdout", path], capture_output=True, timeout=120, stdin=subprocess.DEVNULL)
    except (OSError, subprocess.SubprocessError):
        return {"tool": "clamscan", "result": "error"}
    return {"tool": "clamscan", "result": {0: "clean", 1: "infected"}.get(p.returncode, "error")}


def _read_regular(dir_fd, name, uid, cap):
    """(bytes-or-None, size) of one regular file of `uid` opened without following a link; None bytes when it is too big. The file is read in chunks."""
    try:
        fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=dir_fd)
    except OSError:
        return None, -1
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode) or (uid is not None and st.st_uid != uid):
            return None, -1
        if st.st_size > cap:
            return None, st.st_size
        chunks, left = [], st.st_size + 1
        while left > 0:
            b = os.read(fd, min(CHUNK, left))
            if not b:
                break
            chunks.append(b)
            left -= len(b)
        data = b"".join(chunks)
        return (data if len(data) <= cap else None), len(data)
    finally:
        os.close(fd)


def inspect_bytes(name, data, size=None, scan_result=None):
    """The inspection record for one file's bytes. `scan_result` is the antivirus answer (or None: not run)."""
    clean = clean_name(name)
    kind_words, kind = classify(clean, data[:4096], data if len(data) <= 8 * 1024 * 1024 else None)
    sha = hashlib.sha256(data).hexdigest()
    return {"id": sha[:24], "name": clean, "size": len(data) if size is None else size, "sha256": sha, "type": kind_words, "kind": kind,
            "risky": kind not in SAFE_KINDS, "mismatch": mismatch(clean, kind), "too_big": False, "scan": scan_result or {"tool": "", "result": "not_run"}}


def open_dir(path):
    """The quarantine folder as a directory descriptor (never a link), or None."""
    try:
        fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    except OSError:
        return None
    return fd


def listing(path=QUARANTINE, uid=None, cap=MAX_BYTES, scanner=None):
    """Inspect every finished file in the folder: a list of records, newest first, at most MAX_LISTED. `scanner(path) -> dict` runs the antivirus (default: clamscan if present)."""
    fd = open_dir(path)
    if fd is None:
        return []
    out = []
    try:
        with os.scandir(fd) as it:
            entries = sorted((e for e in it if e.is_file(follow_symlinks=False) and not e.name.startswith(".") and not PARTIAL.search(e.name)),
                             key=lambda e: e.stat(follow_symlinks=False).st_mtime, reverse=True)[:MAX_LISTED]
        for e in entries:
            data, size = _read_regular(fd, e.name, uid, cap)
            if size < 0:
                continue
            if data is None:
                out.append({"id": "", "name": clean_name(e.name), "size": size, "sha256": "", "type": "too big to inspect", "kind": "unknown", "risky": True,
                            "mismatch": False, "too_big": True, "scan": {"tool": "", "result": "not_run"}})
                continue
            sc = (scanner or (lambda p: scan(p)))(os.path.join(path, e.name))
            rec = inspect_bytes(e.name, data, scan_result=sc)
            rec["_file"] = e.name
            out.append(rec)
    finally:
        os.close(fd)
    return out


def public(rec):
    """The record as the hub sends it: no internal field."""
    return {k: v for k, v in rec.items() if not k.startswith("_")}


def read_by_id(file_id, path=QUARANTINE, uid=None, cap=MAX_BYTES):
    """(record, bytes) of the file with this id (its sha256 prefix), re-hashed now, or (None, b""). The bytes are what the owner approved only if the hash still matches."""
    if not re.fullmatch(r"[0-9a-f]{24}", str(file_id or "")):
        return None, b""
    fd = open_dir(path)
    if fd is None:
        return None, b""
    try:
        with os.scandir(fd) as it:
            names = [e.name for e in it if e.is_file(follow_symlinks=False) and not e.name.startswith(".")]
        for n in names:
            data, size = _read_regular(fd, n, uid, cap)
            if data is None:
                continue
            if hashlib.sha256(data).hexdigest().startswith(file_id):
                return inspect_bytes(n, data), data
    finally:
        os.close(fd)
    return None, b""
