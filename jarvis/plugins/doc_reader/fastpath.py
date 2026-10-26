"""Fast path for DIGITAL PDFs: read the text layer with LiteParse (local, no model) before any page is sent to the OCR model (D67).

LiteParse (run-llama/liteparse, Apache-2.0, PDFium text extraction, `lit` command from `pip install liteparse`) is used through its command
line, one short process per page, in its own venv on D:. Nothing is imported into JARVIS and nothing here needs it to exist:

  * not installed, switched off (`DOC_READER_FASTPATH=0`), a crash, a timeout, an empty or garbled answer  ->  the page goes to the
    LightOnOCR path exactly as before (scans, photos and pages with only a stamp of text have no real text layer);
  * the PDF reaches the process on standard input (the bytes `core.read_regular` already checked), so the process never opens a path the
    owner named and the link / junction rules of `core.check_path` cannot be sidestepped;
  * `--no-ocr` always: LiteParse's bundled Tesseract is not used (English data only is the default, Hindi / Telugu data not found on its
    page), a page without text is the OCR model's job;
  * a hard timeout per page and for the whole fast path, an output cap in bytes (the process is killed when it writes more), a clean
    environment, no window, no shell; the command is a fixed list, nothing from the document or the owner is in it except a page number;
  * what comes back is untrusted document text. `core.clean_page` (invisible characters out, instruction-shaped text defused, secrets,
    card numbers and real IBANs replaced) is applied to it exactly as to a model's transcription. A text layer can hold text that the eye
    cannot see (white on white); the OCR path never saw that, this one can, so the cleaner is not optional here.

The default command is the `lit` file from the venv on D: (`LITEPARSE_EXE` can name another absolute path). It is never looked up on PATH.
Class A: read-only, no network (nothing in the command line names a URL or an OCR server).
"""
from __future__ import annotations

import os
import re
import subprocess
import threading
import time
from pathlib import Path
from typing import Callable, Sequence

DEFAULT_EXE = r"D:\jarvis\tools\liteparse-venv\Scripts\lit.exe"
PAGE_TIMEOUT_S = 20.0                      # one page; PDFium needs milliseconds, so a longer wait means a rogue file
TOTAL_BUDGET_S = 90.0                      # all fast pages of one read
MAX_OUT_BYTES = 200_000                    # more than this from one page is cut (and the process killed); the cleaner cuts to 12,000 characters anyway
MIN_REAL_CHARS = 40                        # fewer non-space characters than this on a page: no real text layer, OCR it
MAX_BAD_SHARE = 0.20                       # replacement characters / controls / private-use characters above this share: garbled layer, OCR it
GIVE_UP_AFTER = 2                          # failures in a row (crash, timeout, no process): the rest of the pages go to OCR without trying

FastParse = Callable[[bytes, int], str]    # (pdf bytes, 1-based page number) -> that page's text; raises FastPathError when it cannot

_BAD = re.compile(r"[\ufffd\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f\ue000-\uf8ff]")


class FastPathError(Exception):
    """The fast path could not give this page's text. Never shown to the owner: the page is read by the OCR model instead."""


def enabled() -> bool:
    return os.environ.get("DOC_READER_FASTPATH", "1").strip().lower() not in ("0", "off", "false", "no")


def exe_path() -> str:
    """The absolute path of `lit`, or '' when it is absent (the normal case on a machine that did not install it)."""
    raw = (os.environ.get("LITEPARSE_EXE", "").strip().strip('"') or DEFAULT_EXE)
    p = Path(raw)
    if not p.is_absolute() or raw.startswith(("\\\\", "//")):
        return ""
    try:
        return str(p) if p.is_file() else ""
    except OSError:
        return ""


def _clean_env() -> dict[str, str]:
    keep = ("SystemRoot", "SYSTEMROOT", "windir", "PATH", "PATHEXT", "TEMP", "TMP", "TMPDIR", "LANG", "LC_ALL")
    return {k: os.environ[k] for k in keep if k in os.environ}


class LiteParseCli:
    """The real adapter: `lit parse - --format text --no-ocr --quiet --target-pages N`, the PDF on standard input.
    `cmd` is the command prefix (default `[exe]`); tests give a small Python script instead."""

    def __init__(self, cmd: Sequence[str] | None = None, *, timeout: float = PAGE_TIMEOUT_S, max_out: int = MAX_OUT_BYTES):
        self.cmd = list(cmd) if cmd else [exe_path()]
        self.timeout, self.max_out = timeout, max_out

    def available(self) -> bool:
        return bool(self.cmd and self.cmd[0])

    def __call__(self, pdf: bytes, page: int) -> str:
        if not self.available():
            raise FastPathError("not installed")
        argv = self.cmd + ["parse", "-", "--format", "text", "--no-ocr", "--quiet", "--target-pages", str(int(page))]
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
        try:
            proc = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, env=_clean_env(),
                                    shell=False, creationflags=flags, cwd=_neutral_cwd())
        except (OSError, ValueError):
            raise FastPathError("could not start") from None
        timed_out = threading.Event()

        def kill() -> None:
            timed_out.set()
            try:
                proc.kill()
            except OSError:
                pass
        timer = threading.Timer(self.timeout, kill)
        timer.daemon = True
        timer.start()

        def feed() -> None:
            try:
                proc.stdin.write(pdf)
            except (OSError, ValueError):
                pass
            finally:
                try:
                    proc.stdin.close()
                except (OSError, ValueError):
                    pass
        writer = threading.Thread(target=feed, daemon=True)
        writer.start()
        try:
            out = proc.stdout.read(self.max_out + 1)
            capped = len(out) > self.max_out
            if capped:
                proc.kill()
                out = out[:self.max_out]
        except (OSError, ValueError):
            out, capped = b"", False
        finally:
            timer.cancel()
            try:
                proc.stdout.close()
            except (OSError, ValueError):
                pass
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
            writer.join(timeout=2)
        if timed_out.is_set():
            raise FastPathError("timed out")
        if proc.returncode != 0 and not capped:
            raise FastPathError("failed")
        return out.decode("utf-8", "replace")


def _neutral_cwd() -> str | None:
    """A folder that holds nothing of the owner's (the process needs no files of its own)."""
    import tempfile
    d = tempfile.gettempdir()
    return d if os.path.isdir(d) else None


def default() -> FastParse | None:
    """The real adapter when `lit` is installed and the fast path is on, else None (every page then goes to the OCR model)."""
    if not enabled():
        return None
    cli = LiteParseCli()
    return cli if cli.available() else None


def is_real_text(text: str) -> bool:
    """True when a page's text layer is worth using: enough characters that are not blanks, and not mostly garbage."""
    t = text or ""
    solid = re.sub(r"\s", "", t)
    if len(solid) < MIN_REAL_CHARS:
        return False
    return len(_BAD.findall(solid)) <= MAX_BAD_SHARE * len(solid)


def read_pages(parse: FastParse, pdf: bytes, pages: Sequence[int], *, clock: Callable[[], float] = time.monotonic,
               budget_s: float = TOTAL_BUDGET_S) -> dict[int, str]:
    """{1-based page: raw text} for the pages whose text layer is real. A page that is missing from the answer is for the OCR model.
    Stops trying after GIVE_UP_AFTER failures in a row or when the budget is used up."""
    out: dict[int, str] = {}
    t0, fails = clock(), 0
    for n in pages:
        if clock() - t0 > budget_s or fails >= GIVE_UP_AFTER:
            break
        try:
            text = parse(pdf, n)
        except FastPathError:
            fails += 1
            continue
        except Exception:                                     # noqa: BLE001 - an adapter bug is "no fast path", never a failed read
            fails += 1
            continue
        fails = 0
        if is_real_text(str(text or "")):
            out[n] = str(text)
    return out
