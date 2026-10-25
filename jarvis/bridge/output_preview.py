"""Show the owner's things: what the face's Preview panel may list and open from the deliverables folder.

The folder is the output root of jarvis/plugins/deliverables (`DELIVERABLES_OUT_DIR`, default D:\\jarvis\\outputs\\deliverables). Nothing
outside it is ever read or served. A request names an item by its id (the path inside the folder, URL-safe base64), and the id is
checked the same strict way every time:

  - no `..`, no absolute path, no drive letter or stream (`:`), no backslash, no hidden or secret-looking name;
  - no part of the path may be a symbolic link, a junction or a reparse point (checked part by part, then the file is opened with
    O_NOFOLLOW and looked at again after opening, so a swap between the check and the read gains nothing);
  - the file type must be on the allowlist below AND its first bytes must agree with its name (a `.png` that is not a PNG is refused);
  - a size cap per type; text is cut at 200,000 characters; text that holds a private key is refused, other key-like strings in the text
    are replaced (`plugins/_shared/screen_text.scrub_text`).

What it returns is data for the face's own panel (images as base64, text, HTML source). The panel shows HTML in a sandboxed frame
without scripts and without network. The files in this folder were written by JARVIS's own tools from a spec, but the model must not
read them through these tools, so the descriptions say the three panel tools are for the panel; the model-facing one is `show`, which
returns one short sentence.

The panel opens by itself in two cases: the model called `show`, or a deliverable finished while this was running (its qa.json is
written last, and only a fresh one counts, so what was made while the face was closed is not thrown at the owner).

Pages of a PDF or a deck come from the page images the deliverables checks already saved (`previews/page-NN.png`, `slide-NN.png`);
a PDF without them is rendered here with pypdfium2, the library the deliverables checks use.
"""
from __future__ import annotations

import base64
import binascii
import io
import json
import os
import re
import stat
import threading
import time
from collections import deque
from pathlib import Path
from typing import Any, Callable

IMAGE_MAX = 4 * 1024 * 1024        # a tool result carries it twice (text and structured part), so keep it modest
PAGE_IMAGE_MAX = 3 * 1024 * 1024
TEXT_MAX = 2 * 1024 * 1024
TEXT_CHARS = 200_000
HTML_MAX = 1024 * 1024
PDF_MAX = 50 * 1024 * 1024
SPEC_MAX = 64 * 1024
THUMB_WIDTH = 200
THUMB_PAGES = 24
THUMBS_TOTAL_MAX = 1_500_000
MAX_DEPTH = 4
LIST_LIMIT = 30
SCAN_DIRS = 300

IMAGE_TYPES = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".gif": "image/gif", ".webp": "image/webp"}
# source files: shown as text with their language (svg is shown as source, never rendered: it can carry script)
TEXT_TYPES = {
    ".md": ("text/markdown", "markdown"), ".markdown": ("text/markdown", "markdown"), ".txt": ("text/plain", "text"),
    ".log": ("text/plain", "text"), ".csv": ("text/csv", "text"), ".json": ("application/json", "json"),
    ".yaml": ("application/yaml", "yaml"), ".yml": ("application/yaml", "yaml"), ".toml": ("application/toml", "toml"),
    ".ini": ("text/plain", "ini"), ".xml": ("application/xml", "xml"), ".svg": ("image/svg+xml", "xml"),
    ".py": ("text/x-python", "python"), ".js": ("text/javascript", "javascript"), ".mjs": ("text/javascript", "javascript"),
    ".ts": ("text/typescript", "typescript"), ".tsx": ("text/typescript", "tsx"), ".jsx": ("text/javascript", "jsx"),
    ".css": ("text/css", "css"), ".sh": ("text/x-shellscript", "bash"), ".ps1": ("text/x-powershell", "powershell"),
    ".sql": ("application/sql", "sql"), ".rs": ("text/x-rust", "rust"), ".go": ("text/x-go", "go"),
    ".java": ("text/x-java", "java"), ".c": ("text/x-c", "c"), ".h": ("text/x-c", "c"), ".cpp": ("text/x-c++", "cpp"),
    ".cs": ("text/x-csharp", "csharp"), ".vue": ("text/x-vue", "vue"),
}
HTML_TYPES = {".html": "text/html", ".htm": "text/html"}
PDF_TYPE = "application/pdf"
PPTX_TYPE = "application/vnd.openxmlformats-officedocument.presentationml.presentation"
DOCX_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
MAGIC = {".png": (b"\x89PNG\r\n\x1a\n",), ".jpg": (b"\xff\xd8\xff",), ".jpeg": (b"\xff\xd8\xff",), ".gif": (b"GIF87a", b"GIF89a"),
         ".webp": (b"RIFF",), ".pdf": (b"%PDF-",), ".pptx": (b"PK\x03\x04",), ".docx": (b"PK\x03\x04",)}

# A name that looks like it holds credentials is neither listed nor opened, whatever is inside.
SECRET_NAME = re.compile(r"(?i)(secret|passw|credential|token|private|apikey|api[_-]?key|\.env|id_rsa|id_ed25519|\.pem$|\.key$|"
                         r"\.pfx$|\.p12$|\.kdbx$|\.keystore$|\.npmrc$|\.netrc$|\.htpasswd$|\.jks$)")
STOP = {"the", "a", "an", "my", "me", "show", "open", "display", "view", "see", "please", "preview", "panel", "that", "this", "of",
        "to", "in", "on", "for", "and", "it", "up", "you", "can", "could", "latest", "last", "newest", "recent", "latest", "new", "one", "file", "things"}
KIND_WORDS = {"deck": "deck", "decks": "deck", "slides": "deck", "slide": "deck", "presentation": "deck", "pptx": "deck",
              "powerpoint": "deck", "report": "pdf", "reports": "pdf", "pdf": "pdf", "image": "image", "picture": "image",
              "photo": "image", "png": "image", "chart": "image", "page": "html", "html": "html", "code": "text", "text": "text", "notes": "text"}


class PreviewError(RuntimeError):
    """A refusal, in words for the owner. It never repeats the path it was given."""


def default_root() -> Path:
    try:
        from jarvis.plugins.deliverables import tools
        return Path(tools.OUT_ROOT)
    except Exception:  # noqa: BLE001 - the plugin cannot load (missing dependency): fall back to the same setting it reads
        return Path(os.environ.get("DELIVERABLES_OUT_DIR", r"D:\jarvis\outputs\deliverables"))


def encode_id(rel: str) -> str:
    return base64.urlsafe_b64encode(rel.encode("utf-8")).decode("ascii").rstrip("=")


def decode_id(item_id: Any) -> str:
    if not isinstance(item_id, str) or not item_id or len(item_id) > 600 or not re.fullmatch(r"[A-Za-z0-9_-]+", item_id):
        raise PreviewError("that is not an id of something I can show")
    try:
        rel = base64.urlsafe_b64decode(item_id + "=" * (-len(item_id) % 4)).decode("utf-8")
    except (binascii.Error, UnicodeDecodeError):
        raise PreviewError("that is not an id of something I can show") from None
    return rel


def classify(name: str) -> tuple[str, str, str]:
    """(family, mime, language) for a file name, or raise PreviewError when the type is not on the allowlist."""
    ext = os.path.splitext(name)[1].lower()
    if ext in IMAGE_TYPES:
        return "image", IMAGE_TYPES[ext], ""
    if ext == ".pdf":
        return "pdf", PDF_TYPE, ""
    if ext == ".pptx":
        return "deck", PPTX_TYPE, ""
    if ext == ".docx":
        return "document", DOCX_TYPE, ""
    if ext in HTML_TYPES:
        return "html", HTML_TYPES[ext], ""
    if ext in TEXT_TYPES:
        mime, lang = TEXT_TYPES[ext]
        return "text", mime, lang
    raise PreviewError("that kind of file cannot be shown here")


def _is_link_like(st: os.stat_result) -> bool:
    if stat.S_ISLNK(st.st_mode):
        return True
    return bool(getattr(st, "st_file_attributes", 0) & 0x400)           # Windows reparse point (junction, symlink, mount point)


def check_rel(rel: str) -> list[str]:
    """The parts of a relative path, or a refusal. Pure text rules; the file system rules come after."""
    if not isinstance(rel, str) or not rel or len(rel) > 400 or "\x00" in rel or "\\" in rel or ":" in rel or rel.startswith("/"):
        raise PreviewError("that path is not inside the deliverables folder")
    parts = rel.split("/")
    if len(parts) > MAX_DEPTH or any(p in ("", ".", "..") or p.startswith(".") or p != p.strip() or p.endswith(".") for p in parts):
        raise PreviewError("that path is not inside the deliverables folder")
    if any(SECRET_NAME.search(p) for p in parts):
        raise PreviewError("that file looks like it holds credentials, so it is not shown")
    return parts


def _read_regular(path: Path, cap: int) -> bytes:
    """The bytes of a regular file of at most `cap` bytes, opened without following a link, checked again after opening."""
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0) | getattr(os, "O_CLOEXEC", 0)
    try:
        fd = os.open(path, flags)
    except OSError:
        raise PreviewError("I cannot open that file") from None
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode) or _is_link_like(st):
            raise PreviewError("that is not a plain file")
        if st.st_size > cap:
            raise PreviewError(f"that file is larger than the {cap // (1024 * 1024) or 1} MB limit for showing it here")
        with os.fdopen(fd, "rb", closefd=False) as fh:
            data = fh.read(cap + 1)
    finally:
        os.close(fd)
    if len(data) > cap:
        raise PreviewError("that file is larger than the limit for showing it here")
    return data


def _scrub(text: str) -> tuple[str, list[str]]:
    from jarvis.plugins._shared.screen_text import scrub_text
    return scrub_text(text)


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def _decode_text(data: bytes) -> str:
    if b"\x00" in data[:8192]:
        raise PreviewError("that file is binary, not text")
    return data.decode("utf-8-sig", errors="replace")


class OutputPreview:
    """List and open deliverables for the face's Preview panel. `root` is the deliverables folder."""

    def __init__(self, root: str | Path | None = None, clock: Callable[[], float] = time.time, auto: bool | None = None):
        self.root = Path(root) if root is not None else default_root()
        self.clock = clock
        # auto: a deliverable that appears while this runs is queued for the panel by itself (JARVIS_PREVIEW_AUTO=0 turns it off)
        self.auto = (os.environ.get("JARVIS_PREVIEW_AUTO", "1").strip() != "0") if auto is None else auto
        self._wanted: deque[str] = deque(maxlen=5)
        self._announced: set[str] = set()
        self._lock = threading.Lock()
        self._baseline = self._newest_mtime()

    # ------------------------------------------------------------------ resolving
    def _root_real(self) -> Path:
        try:
            real = self.root.resolve(strict=True)
        except OSError:
            raise PreviewError("the deliverables folder does not exist yet") from None
        if not real.is_dir():
            raise PreviewError("the deliverables folder does not exist yet")
        return real

    def resolve(self, rel: str) -> Path:
        """The path of a regular file inside the folder, or a refusal."""
        parts = check_rel(rel)
        real = self._root_real()
        cur = real
        for part in parts:
            cur = cur / part
            try:
                st = os.lstat(cur)
            except OSError:
                raise PreviewError("I cannot find that file") from None
            if _is_link_like(st):
                raise PreviewError("links are not followed; that file is not shown")
        if not stat.S_ISREG(st.st_mode):
            raise PreviewError("that is not a plain file")
        try:
            inside = cur.resolve(strict=True)
            inside.relative_to(real)
        except (OSError, ValueError):
            raise PreviewError("that path is not inside the deliverables folder") from None
        return cur

    # ------------------------------------------------------------------ listing
    def _spec_title(self, folder: Path) -> str | None:
        try:
            data = json.loads(_read_regular(folder / "spec.json", SPEC_MAX).decode("utf-8"))
            title = str(data.get("title") or "").strip()
            return " ".join(title.split())[:120] or None
        except (PreviewError, ValueError, AttributeError, UnicodeDecodeError):
            return None

    def _qa(self, folder: Path) -> dict | None:
        try:
            data = json.loads(_read_regular(folder / "qa.json", SPEC_MAX * 8).decode("utf-8"))
            return data if isinstance(data, dict) else None
        except (PreviewError, ValueError, UnicodeDecodeError):
            return None

    def _page_files(self, folder: Path, prefix: str) -> list[str]:
        d = folder / "previews"
        try:
            if _is_link_like(os.lstat(d)):
                return []
            names = os.listdir(d)
        except OSError:
            return []
        found = sorted(n for n in names if re.fullmatch(prefix + r"-\d{2,3}\.png", n))
        return found

    def _item(self, rel: str, path: Path, st: os.stat_result, main: bool, qa: dict | None, title: str | None) -> dict | None:
        try:
            family, mime, _ = classify(path.name)
        except PreviewError:
            return None
        item: dict[str, Any] = {"id": encode_id(rel), "title": title or path.stem.replace("-", " ").replace("_", " ").strip().capitalize() or path.name,
                                "kind": family, "mime": mime, "size": st.st_size, "modified": int(st.st_mtime), "passed": None, "pages": None}
        if main and qa is not None:
            try:
                item["passed"] = bool(qa["qa"]["passed"])
            except (KeyError, TypeError):
                pass
        if family in ("pdf", "deck"):
            n = len(self._page_files(path.parent, "slide" if family == "deck" else "page"))
            item["pages"] = n or None
        item["preview"] = {"image": "image", "pdf": "pages", "deck": "pages", "html": "html", "text": "text"}.get(family, "none")
        return item

    def list(self, limit: int = 12) -> dict:
        """The newest deliverables first: the main file of each deliverable folder, plus loose files that can be shown."""
        try:
            limit = max(1, min(int(limit), LIST_LIMIT))
        except (TypeError, ValueError):
            limit = 12
        try:
            real = self._root_real()
        except PreviewError as exc:
            return {"ok": True, "count": 0, "items": [], "message": str(exc)}
        entries: list[tuple[float, str, Path, os.stat_result]] = []
        try:
            with os.scandir(real) as it:
                for e in it:
                    try:
                        st = e.stat(follow_symlinks=False)
                    except OSError:
                        continue
                    if _is_link_like(st) or e.name.startswith(".") or SECRET_NAME.search(e.name):
                        continue
                    entries.append((st.st_mtime, e.name, Path(e.path), st))
        except OSError:
            return {"ok": True, "count": 0, "items": [], "message": "the deliverables folder cannot be read"}
        entries.sort(key=lambda t: (t[0], t[1]), reverse=True)
        items: list[dict] = []
        for _, name, path, st in entries[:SCAN_DIRS]:
            if len(items) >= limit:
                break
            if stat.S_ISREG(st.st_mode):
                it = self._item(name, path, st, False, None, None)
                if it:
                    items.append(it)
                continue
            if not stat.S_ISDIR(st.st_mode):
                continue
            qa = self._qa(path)
            if qa is None and (path / "spec.json").is_file():
                continue                                       # a deliverable folder without its qa.json is still being made, or failed
            main_name = None
            if qa is not None:
                main_name = Path(str(qa.get("file") or "").replace("\\", "/")).name or None
            title = self._spec_title(path) if qa is not None else None
            try:
                children = sorted(os.scandir(path), key=lambda e: e.name)
            except OSError:
                continue
            for child in children:
                try:
                    cst = child.stat(follow_symlinks=False)
                except OSError:
                    continue
                if not stat.S_ISREG(cst.st_mode) or _is_link_like(cst) or child.name.startswith(".") or SECRET_NAME.search(child.name):
                    continue
                is_main = main_name is not None and child.name == main_name
                if qa is not None and not is_main:
                    continue                                   # a deliverable folder lists its deliverable, not its spec.json or report.html
                it = self._item(f"{name}/{child.name}", Path(child.path), cst, is_main, qa, title if is_main else None)
                if it:
                    items.append(it)
        items.sort(key=lambda i: i["modified"], reverse=True)
        items = items[:limit]
        return {"ok": True, "count": len(items), "items": items}

    # ------------------------------------------------------------------ one item
    def get(self, item_id: str, page: int = 0, thumbs: bool = False) -> dict:
        try:
            rel = decode_id(item_id)
            path = self.resolve(rel)
            return self._payload(rel, path, page, thumbs)
        except PreviewError as exc:
            return {"ok": False, "message": str(exc)}

    def _head(self, rel: str, path: Path, family: str, mime: str, size: int) -> dict:
        title = None
        if "/" in rel:
            qa = self._qa(path.parent)
            if qa is not None and Path(str(qa.get("file") or "").replace("\\", "/")).name == path.name:
                title = self._spec_title(path.parent)
        return {"ok": True, "id": encode_id(rel), "title": title or path.stem.replace("-", " ").replace("_", " ").strip().capitalize() or path.name,
                "kind": family, "mime": mime, "size": size, "open_path": str(path)}

    def _payload(self, rel: str, path: Path, page: int, thumbs: bool) -> dict:
        family, mime, language = classify(path.name)
        ext = path.suffix.lower()
        if family == "image":
            data = _read_regular(path, IMAGE_MAX)
            self._check_magic(ext, data)
            if ext == ".webp" and data[8:12] != b"WEBP":
                raise PreviewError("that file is not what its name says")
            return {**self._head(rel, path, family, mime, len(data)), "view": "image", "image": {"mime": mime, "data": _b64(data)}}
        if family in ("pdf", "deck"):
            return self._pages_payload(rel, path, family, mime, page, thumbs)
        if family == "document":
            data = _read_regular(path, PDF_MAX)
            self._check_magic(ext, data)
            return {**self._head(rel, path, family, mime, len(data)), "view": "none", "message": "There is no preview for this kind of document here; open it in its app."}
        if family == "html":
            data = _read_regular(path, HTML_MAX)
            text, hits = _scrub(_decode_text(data))
            if not text and hits:
                raise PreviewError("that file holds a private key, so it is not shown")
            return {**self._head(rel, path, family, mime, len(data)), "view": "html", "html": text, "redacted": hits}
        data = _read_regular(path, TEXT_MAX)
        text, hits = _scrub(_decode_text(data))
        if not text and hits:
            raise PreviewError("that file holds a private key, so it is not shown")
        cut = len(text) > TEXT_CHARS
        return {**self._head(rel, path, family, mime, len(data)), "view": "text", "language": language, "text": text[:TEXT_CHARS],
                "truncated": cut, "redacted": hits}

    @staticmethod
    def _check_magic(ext: str, data: bytes) -> None:
        heads = MAGIC.get(ext)
        if heads and not any(data.startswith(h) for h in heads):
            raise PreviewError("that file is not what its name says")

    def _pages_payload(self, rel: str, path: Path, family: str, mime: str, page: int, thumbs: bool) -> dict:
        try:
            page = max(0, int(page))
        except (TypeError, ValueError):
            page = 0
        prefix = "slide" if family == "deck" else "page"
        head = self._read_head(path)
        self._check_magic(path.suffix.lower(), head)
        previews = self._page_files(path.parent, prefix)
        size = os.stat(path).st_size
        out = {**self._head(rel, path, family, mime, size), "view": "pages", "page": 0, "page_count": 0}
        if previews:
            count = len(previews)
            page = min(page, count - 1)
            out.update(page=page, page_count=count,
                       image={"mime": "image/png", "data": _b64(self._preview_png(path.parent, previews[page]))})
            if thumbs:
                out["thumbs"] = self._thumbs(path.parent, previews)
            return out
        if family == "pdf":
            rendered = self._render_pdf(path, page, thumbs)
            if rendered:
                out.update(rendered)
                return out
        return {**self._head(rel, path, family, mime, size), "view": "none",
                "message": "No page images were saved for this one; open it in the app." if family == "deck" else "I cannot render this PDF here; open it in the app."}

    @staticmethod
    def _read_head(path: Path) -> bytes:
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0)
        try:
            fd = os.open(path, flags)
        except OSError:
            raise PreviewError("I cannot open that file") from None
        try:
            if not stat.S_ISREG(os.fstat(fd).st_mode):
                raise PreviewError("that is not a plain file")
            return os.read(fd, 16)
        finally:
            os.close(fd)

    def _preview_png(self, folder: Path, name: str) -> bytes:
        p = folder / "previews" / name
        if _is_link_like(os.lstat(p)):
            raise PreviewError("links are not followed; that file is not shown")
        data = _read_regular(p, PAGE_IMAGE_MAX)
        self._check_magic(".png", data)
        return data

    def _thumbs(self, folder: Path, previews: list[str]) -> list[dict]:
        try:
            from PIL import Image
        except ImportError:
            return []
        out, total = [], 0
        for i, name in enumerate(previews[:THUMB_PAGES]):
            try:
                with Image.open(io.BytesIO(self._preview_png(folder, name))) as im:
                    im = im.convert("RGB")
                    im = im.resize((THUMB_WIDTH, max(1, int(im.height * THUMB_WIDTH / max(1, im.width)))))
                    buf = io.BytesIO()
                    im.save(buf, "PNG", optimize=True)
            except (PreviewError, OSError, ValueError):
                continue
            raw = buf.getvalue()
            total += len(raw)
            if total > THUMBS_TOTAL_MAX:
                break
            out.append({"page": i, "mime": "image/png", "data": _b64(raw)})
        return out

    def _render_pdf(self, path: Path, page: int, thumbs: bool) -> dict | None:
        try:
            import pypdfium2 as pdfium
        except ImportError:
            return None
        data = _read_regular(path, PDF_MAX)
        try:
            doc = pdfium.PdfDocument(data)
        except Exception:  # noqa: BLE001 - a damaged PDF is "cannot render", not a crash
            return None
        try:
            count = len(doc)
            if count < 1:
                return None
            page = min(page, count - 1)

            def png(i: int, scale: float) -> bytes:
                buf = io.BytesIO()
                doc[i].render(scale=scale).to_pil().convert("RGB").save(buf, "PNG", optimize=True)
                return buf.getvalue()
            image = png(page, 1.2)
            if len(image) > PAGE_IMAGE_MAX:
                image = png(page, 0.7)
            res: dict[str, Any] = {"page": page, "page_count": count, "image": {"mime": "image/png", "data": _b64(image)}}
            if thumbs:
                small, total = [], 0
                for i in range(min(count, THUMB_PAGES)):
                    raw = png(i, 0.3)
                    total += len(raw)
                    if total > THUMBS_TOTAL_MAX:
                        break
                    small.append({"page": i, "mime": "image/png", "data": _b64(raw)})
                res["thumbs"] = small
            return res
        except Exception:  # noqa: BLE001
            return None
        finally:
            doc.close()

    # ------------------------------------------------------------------ "show me X"
    def find(self, query: str) -> dict | None:
        """The item that best matches the words, or the newest one when the words name nothing in particular."""
        items = self.list(LIST_LIMIT)["items"]
        if not items:
            return None
        words = [w for w in re.findall(r"[a-z0-9]+", str(query or "").lower()) if w not in STOP and len(w) > 1]
        kinds = {KIND_WORDS[w] for w in words if w in KIND_WORDS}
        words = [w for w in words if w not in KIND_WORDS]
        best, best_score = None, 0
        for it in items:                                              # newest first, so a tie goes to the newer one
            hay = (it["title"] + " " + it["kind"]).lower()
            score = sum(2 for w in words if w in hay)
            if kinds and (it["kind"] in kinds or (it["kind"] == "deck" and "deck" in kinds) or (it["mime"] == PDF_TYPE and "pdf" in kinds)):
                score += 1
            if score > best_score:
                best, best_score = it, score
        if best is not None:
            return best
        if words or kinds:
            return None
        return items[0]

    def show(self, query: str) -> dict:
        """The tool for the model: queue one item for the panel and say so in a sentence. Returns no file content."""
        item = self.find(query)
        if item is None:
            return {"ok": False, "message": "I could not find anything I made that matches that."}
        with self._lock:
            self._wanted.append(item["id"])
        return {"ok": True, "message": f"Showing {item['title']} in the preview panel."}

    def _newest_mtime(self) -> float:
        newest = 0.0
        try:
            with os.scandir(self.root) as it:
                for e in it:
                    try:
                        newest = max(newest, e.stat(follow_symlinks=False).st_mtime)
                    except OSError:
                        continue
        except OSError:
            pass
        return newest

    def _fresh(self) -> list[str]:
        """Ids of deliverables that finished while this has been running (a folder counts once its qa.json is written), at most
        two minutes old: what was made while the face was closed is not thrown at the owner when it opens."""
        if not self.auto:
            return []
        now = self.clock()
        out: list[str] = []
        for it in self.list(8)["items"]:
            if it["id"] in self._announced or it["modified"] <= int(self._baseline):
                continue
            self._announced.add(it["id"])
            if now - it["modified"] <= 120:
                out.append(it["id"])
        return out

    def take_wanted(self) -> list[str]:
        """What the panel should open: what `show` queued, then anything new that finished. Each id is returned once."""
        with self._lock:
            fresh = self._fresh()
            out = list(self._wanted) + [i for i in fresh if i not in self._wanted]
            self._wanted.clear()
        return out[-5:]
