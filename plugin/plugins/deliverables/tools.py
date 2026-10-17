"""Where the external tools and the output folder are, and how they are run (bounded, no shell, no network for OfficeCLI).

Windows paths by default; every one can be changed by an environment variable.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import time
from pathlib import Path

OUT_ROOT = Path(os.environ.get("DELIVERABLES_OUT_DIR", r"D:\jarvis\outputs\neko\deliverables"))
OFFICECLI = os.environ.get("DELIVERABLES_OFFICECLI", r"D:\jarvis\tools\officecli\v1.0.152\officecli.exe")
SOFFICE = os.environ.get("DELIVERABLES_SOFFICE", r"D:\jarvis\tools\libreoffice\26.2.6\program\soffice.exe")
LO_PROFILE = os.environ.get("DELIVERABLES_LO_PROFILE", r"D:\jarvis\cache\lo-profile")
OPENABLE = (".pdf", ".pptx", ".png", ".html", ".md", ".docx")


class ToolError(RuntimeError):
    pass


def slug(text: str, limit: int = 48) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", (text or "output").lower()).strip("-")
    return (s[:limit].rstrip("-") or "output")


def new_job_dir(title: str) -> Path:
    """A fresh folder per deliverable: <root>/<YYYYMMDD-HHMMSS>-<slug>; never reuses or overwrites one."""
    OUT_ROOT.mkdir(parents=True, exist_ok=True)
    base = OUT_ROOT / f"{time.strftime('%Y%m%d-%H%M%S')}-{slug(title)}"
    d, n = base, 2
    while True:                         # mkdir is the check: two deliverables started at once never get the same folder
        try:
            d.mkdir()
            return d
        except FileExistsError:
            d, n = Path(f"{base}-{n}"), n + 1


def inside_outputs(path: str) -> Path:
    """Resolve a path and refuse anything that is not an existing deliverable file under the output root."""
    p = Path(path).resolve()
    root = OUT_ROOT.resolve()
    if root != p and root not in p.parents:
        raise ToolError("only files made by the deliverables tool can be opened here")
    if not p.is_file() or p.suffix.lower() not in OPENABLE:
        raise ToolError("that file does not exist or is not a document this tool makes")
    return p


def status() -> dict:
    return {"officecli": os.path.isfile(OFFICECLI), "libreoffice": os.path.isfile(SOFFICE),
            "edge": bool(_edge_path()), "output_dir": str(OUT_ROOT)}


def _edge_path() -> str | None:
    for p in (r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe", r"C:\Program Files\Microsoft\Edge\Application\msedge.exe"):
        if os.path.isfile(p):
            return p
    return shutil.which("msedge")


def run(cmd: list[str], timeout: float, cwd: str | None = None, env_extra: dict | None = None) -> subprocess.CompletedProcess:
    env = dict(os.environ, **(env_extra or {}))
    try:
        return subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace",
                              timeout=timeout, cwd=cwd, env=env, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except subprocess.TimeoutExpired:
        raise ToolError(f"{Path(cmd[0]).name} did not finish within {int(timeout)} s") from None
    except OSError as e:
        raise ToolError(f"could not start {Path(cmd[0]).name} ({type(e).__name__})") from None


def officecli(args: list[str], cwd: str, timeout: float = 180) -> subprocess.CompletedProcess:
    if not os.path.isfile(OFFICECLI):
        raise ToolError("OfficeCLI is not installed (see jarvis/docs/05_OPERATIONS.md)")
    return run([OFFICECLI, *args], timeout, cwd=cwd, env_extra={"OFFICECLI_SKIP_UPDATE": "1"})


def soffice_to_pdf(src: Path, outdir: Path, timeout: float = 300) -> Path:
    """Render with LibreOffice in its own profile on D: (headless, no first-start wizard)."""
    if not os.path.isfile(SOFFICE):
        raise ToolError("LibreOffice is not installed (see jarvis/docs/05_OPERATIONS.md)")
    profile = "file:///" + LO_PROFILE.replace("\\", "/")
    r = run([SOFFICE, f"-env:UserInstallation={profile}", "--headless", "--norestore", "--nolockcheck",
             "--convert-to", "pdf", "--outdir", str(outdir), str(src)], timeout)
    pdf = outdir / (src.stem + ".pdf")
    if r.returncode != 0 or not pdf.is_file():
        raise ToolError(f"LibreOffice could not render the deck (exit {r.returncode})")
    return pdf


FIT_APPS = {".pptx": ("powerpnt", "soffice", "simpress", "pptview"), ".docx": ("winword", "soffice", "swriter", "wordpad"),
            ".pdf": ("msedge", "acrord", "acrobat", "sumatra", "chrome", "firefox")}


def _has_app(ext: str) -> bool:
    """Is the program Windows would use for this type one that can show it? On this PC .pptx was mapped to Notepad:
    "open the deck" showed it as binary text (2026-10-16). A type with no fitting program goes to the fallback."""
    if os.name != "nt":
        return False
    try:
        import ctypes
        n = ctypes.c_uint(1024)
        buf = ctypes.create_unicode_buffer(1024)
        hr = ctypes.windll.shlwapi.AssocQueryStringW(0, 2, ext, "open", buf, ctypes.byref(n))   # ASSOCSTR_EXECUTABLE
        if hr != 0 or not buf.value:
            return False
        exe = os.path.basename(buf.value).lower()
        return any(a in exe for a in FIT_APPS.get(ext, (exe,)))
    except Exception:  # noqa: BLE001
        return True                                                                          # unknown: let the shell try


def open_file(p: Path) -> None:
    """The owner's default app; if Windows has none for this type (this PC had no .pptx app: decks "opened" into
    nothing, 2026-10-16), the tools that are installed here: LibreOffice for decks/documents, Edge for PDF/HTML."""
    ext = p.suffix.lower()
    if _has_app(ext):
        try:
            os.startfile(str(p))  # noqa: S606 - a path checked by inside_outputs()
            return
        except OSError:
            pass
    if ext in (".pptx", ".docx") and os.path.isfile(SOFFICE):
        quiet_profile()
        profile = "file:///" + LO_PROFILE.replace("\\", "/")
        subprocess.Popen([SOFFICE, f"-env:UserInstallation={profile}", "--norestore", str(p)],
                         creationflags=getattr(subprocess, "DETACHED_PROCESS", 0))
        return
    edge = _edge_path()
    if ext in (".pdf", ".html", ".png", ".md") and edge:
        subprocess.Popen([edge, str(p)], creationflags=getattr(subprocess, "DETACHED_PROCESS", 0))
        return
    raise ToolError(f"no program on this PC opens {ext} files")


def show_in_folder(p: Path) -> None:
    subprocess.Popen(["explorer.exe", f"/select,{p}"], creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))


_QUIET_KEYS = (  # (path, name, value): first-run, tip and what's-new dialogs would otherwise block a visible start (seen)
    ("/org.openoffice.Setup/Office", "FirstStartWizardCompleted", "true"),
    ("/org.openoffice.Setup/Product", "WhatsNew", "false"),
    ("/org.openoffice.Setup/Product", "WhatsNewDialog", "false"),
    ("/org.openoffice.Office.Common/Misc", "ShowTipOfTheDay", "false"),
    ("/org.openoffice.Office.Common/Misc", "FirstRun", "false"),
    ("/org.openoffice.Office.Common/Misc", "ShowWelcomeDialog", "false"),
)


def quiet_profile() -> None:
    """Pre-set the private LibreOffice profile so no first-start dialog appears (only adds missing keys)."""
    user = Path(LO_PROFILE) / "user"
    user.mkdir(parents=True, exist_ok=True)
    xcu = user / "registrymodifications.xcu"
    text = xcu.read_text(encoding="utf-8") if xcu.is_file() else (
        '<?xml version="1.0" encoding="UTF-8"?>\n<oor:items xmlns:oor="http://openoffice.org/2001/registry" '
        'xmlns:xs="http://www.w3.org/2001/XMLSchema" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">\n</oor:items>\n')
    add = "".join(f'<item oor:path="{p}"><prop oor:name="{n}" oor:op="fuse"><value>{v}</value></prop></item>\n'
                  for p, n, v in _QUIET_KEYS if f'oor:name="{n}"' not in text)
    if add:
        xcu.write_text(text.replace("</oor:items>", add + "</oor:items>"), encoding="utf-8")


def start_slideshow(p: Path) -> None:
    if not os.path.isfile(SOFFICE):
        raise ToolError("LibreOffice is not installed, so the slideshow cannot start")
    quiet_profile()
    profile = "file:///" + LO_PROFILE.replace("\\", "/")
    subprocess.Popen([SOFFICE, f"-env:UserInstallation={profile}", "--norestore", "--show", str(p)],
                     creationflags=getattr(subprocess, "DETACHED_PROCESS", 0))
