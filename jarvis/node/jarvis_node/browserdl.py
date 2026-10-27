"""Downloads for the browser agent: only from a granted host, only into the quarantine folder, checked, never run (D64, 2026-10-26).

What happens: the owner's Chrome saves a file into its own downloads folder when the page's export button is clicked (the Playwright extension
relay does not hand the file over). `collect` waits for that file to be COMPLETE (no .crdownload, size stable), then:
  * the kernel already decided that the click was allowed (a granted download_to_quarantine on the page's host; otherwise the PIN);
  * the type must be one of pptx, pdf, png, jpg (the first bytes must agree with the name; a pptx must be a zip holding ppt/presentation.xml);
    nothing else (no archive, no program, no script) is ever taken, and a refused file is left where Chrome put it, untouched;
  * the size is capped (default 150 MB);
  * on Windows the file's own "Zone.Identifier" stream says which page led to it: when it names a host other than the granted one the file is
    refused ("downloads allowed only from that host"); when it cannot be read the file is taken but marked source_unverified;
  * the file MOVES to <outputs>\\downloads\\<host>\\<yyyymmdd-hhmmss>-<safe name> (never over a file that exists) and is never opened, run or
    executed by the node. The plugin verifies it with the document readers and shows it.
The result carries the name, type, size and sha256; the full path goes into a local result only.
"""
import hashlib
import os
import re
import shutil
import time
import urllib.parse
import zipfile

from . import runtime
from .paths import jarvis_home

ALLOWED_EXT = (".pptx", ".pdf", ".png", ".jpg", ".jpeg")
MAX_BYTES = int(os.environ.get("JARVIS_DOWNLOAD_MAX_MB", "150")) * 1024 * 1024
PARTIAL = (".crdownload", ".tmp", ".part", ".download")
SAFE_NAME = re.compile(r"[^A-Za-z0-9 ._()\-]")
MAGIC = {".pdf": (b"%PDF-",), ".png": (b"\x89PNG\r\n\x1a\n",), ".jpg": (b"\xff\xd8\xff",), ".jpeg": (b"\xff\xd8\xff",), ".pptx": (b"PK\x03\x04",)}


class DownloadError(Exception):
    pass


def chrome_downloads() -> str:
    return os.environ.get("JARVIS_CHROME_DOWNLOADS") or os.path.join(os.environ.get("USERPROFILE") or os.path.expanduser("~"), "Downloads")


def outputs_root() -> str:
    return os.path.realpath(os.environ.get("JARVIS_OUTPUTS") or os.path.join(jarvis_home(), "outputs"))


def quarantine_dir(host: str) -> str:
    if not re.fullmatch(r"(?:[a-z0-9-]{1,63}\.)+[a-z0-9-]{2,63}", host or ""):
        raise DownloadError("bad host")
    return os.path.join(outputs_root(), "downloads", host)


def safe_name(name: str) -> str:
    base = os.path.basename(str(name or "").replace("\\", "/"))
    stem, ext = os.path.splitext(base)
    stem = re.sub(r"\s+", " ", SAFE_NAME.sub("_", stem)).strip(" ._")[:60] or "download"
    return stem + ext.lower()


def _wanted_ext(exts) -> tuple:
    got = tuple("." + e.strip().lower().lstrip(".") for e in (exts or ()) if e.strip())
    return tuple(e for e in got if e in ALLOWED_EXT) or ALLOWED_EXT


def baseline(folder: str = None) -> dict:
    """{name: (mtime, size)} of the downloads folder now: what was there before the click."""
    out = {}
    try:
        for e in os.scandir(folder or chrome_downloads()):
            try:
                if e.is_file(follow_symlinks=False):
                    st = e.stat(follow_symlinks=False)
                    out[e.name] = (st.st_mtime, st.st_size)
            except OSError:
                pass
    except OSError:
        pass
    return out


def new_files(before: dict, folder: str = None, since: float = 0.0) -> list:
    """Complete files that are new or changed since `before` (and not older than `since`), newest first. (path, size, mtime)."""
    folder = folder or chrome_downloads()
    found = []
    try:
        for e in os.scandir(folder):
            if not e.is_file(follow_symlinks=False) or e.name.lower().endswith(PARTIAL) or e.name.startswith("."):
                continue
            st = e.stat(follow_symlinks=False)
            if before.get(e.name) == (st.st_mtime, st.st_size) or st.st_mtime < since - 2:
                continue
            found.append((e.path, st.st_size, st.st_mtime))
    except OSError:
        return []
    return sorted(found, key=lambda t: -t[2])


def referrer_host(path: str, reader=None) -> str:
    """The host of the page that led to this download, from the Windows Zone.Identifier stream ("" when it cannot be read)."""
    try:
        text = reader(path) if reader else open(path + ":Zone.Identifier", encoding="utf-8", errors="replace").read()
    except OSError:
        return ""
    m = re.search(r"(?mi)^ReferrerUrl=(\S+)", text or "")
    if not m:
        return ""
    return (urllib.parse.urlsplit(m.group(1)).hostname or "").lower().rstrip(".")


def check_content(path: str, ext: str) -> None:
    """The first bytes agree with the name; a pptx is a zip with ppt/presentation.xml. Raises DownloadError."""
    try:
        with open(path, "rb") as f:
            head = f.read(16)
    except OSError:
        raise DownloadError("the file cannot be read") from None
    if not any(head.startswith(m) for m in MAGIC.get(ext, ())):
        raise DownloadError(f"the file does not look like a {ext[1:]} (its first bytes disagree with its name)")
    if ext == ".pptx":
        try:
            with zipfile.ZipFile(path) as z:
                names = set(z.namelist())
        except (zipfile.BadZipFile, OSError):
            raise DownloadError("the pptx is not a valid zip file") from None
        if "ppt/presentation.xml" not in names or "[Content_Types].xml" not in names:
            raise DownloadError("the file is a zip but not a PowerPoint deck")


def _sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _free_name(folder: str, name: str) -> str:
    stem, ext = os.path.splitext(name)
    p, n = os.path.join(folder, name), 1
    while os.path.exists(p):
        n += 1
        p = os.path.join(folder, f"{stem} ({n}){ext}")
    return p


def collect(host: str, before: dict, exts=(), wait_s: float = 90.0, folder: str = None, quarantine: str = None, since: float = 0.0,
            clock=time.time, sleep=time.sleep, zone_reader=None, poll_s: float = 1.0) -> dict:
    """Wait for the file the last click started, check it, move it into quarantine. Returns a result dict (ok False with `error` and `refused`
    when something is wrong; the browser job still ends normally so the engine can recover)."""
    allowed = _wanted_ext(exts)
    deadline, last, stable = clock() + wait_s, None, 0
    pick = None
    while True:
        runtime.check()
        cands = new_files(before, folder, since)
        if cands:
            path, size, mtime = cands[0]
            sig = (path, size, mtime)
            stable = stable + 1 if sig == last else 0
            last = sig
            if stable >= 1:                       # the same size and time on two looks in a row: Chrome has finished
                pick = path
                break
        if clock() >= deadline:
            return {"ok": False, "error": "no completed download appeared in time", "waited_s": int(wait_s)}
        sleep(poll_s)
    ext = os.path.splitext(pick)[1].lower()
    size = os.path.getsize(pick)
    if ext not in allowed:
        return {"ok": False, "refused": "type", "error": f"a {ext or 'file without a type'} file arrived; only {', '.join(e[1:] for e in allowed)} are taken. "
                                                         "It stays where Chrome put it, untouched", "ext": ext}
    if size > MAX_BYTES:
        return {"ok": False, "refused": "size", "error": f"the file is {size // (1024 * 1024)} MB; the cap is {MAX_BYTES // (1024 * 1024)} MB", "ext": ext}
    ref = referrer_host(pick, zone_reader)
    if ref and ref != host:
        return {"ok": False, "refused": "source", "error": f"the file came from a page on {ref}, not on {host}: not taken", "ext": ext}
    try:
        check_content(pick, ext)
    except DownloadError as e:
        return {"ok": False, "refused": "content", "error": str(e), "ext": ext}
    qdir = quarantine or quarantine_dir(host)
    os.makedirs(qdir, exist_ok=True)
    dest = _free_name(qdir, time.strftime("%Y%m%d-%H%M%S-", time.localtime(clock())) + safe_name(os.path.basename(pick)))
    shutil.move(pick, dest)                      # never opened, never run
    return {"ok": True, "name": os.path.basename(dest), "ext": ext[1:], "size": os.path.getsize(dest), "sha256": _sha256(dest),
            "source_verified": bool(ref), "source_host": ref or None, "quarantine": True, "local_result_file": dest}
