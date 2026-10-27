"""The PC as the reliability gate sees it: files, windows, the Recycle Bin. Every verification reads THIS, never JARVIS's own report.

  LocalEnv     the parts that are plain Python (files, folders, deck files, output folders). Works anywhere; the tests use it.
  WindowsEnv   adds windows, window text (UI Automation), the Recycle Bin, message boxes and rendered pictures, through
               ps/envprobe.ps1 (PowerShell, so no extra install on the laptop). Written without a Windows machine at hand:
               `runner.py --selftest` exercises every primitive first, and a gate run is not to be trusted before it passes.

The independent reader is deliberate: JARVIS verifies its own steps (coordinator.verify); if the gate asked JARVIS "did it work?" a
wrong "yes" would be counted as a pass. The gate opens the file itself, lists the windows itself, looks in the bin itself.
"""
from __future__ import annotations

import fnmatch
import hashlib
import json
import os
import shutil
import subprocess
import time
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

PS_SCRIPT = Path(__file__).resolve().parent / "ps" / "envprobe.ps1"


@dataclass(frozen=True)
class Win:
    hwnd: int
    pid: int
    process: str
    title: str


class EnvError(RuntimeError):
    pass


class LocalEnv:
    """Files and folders. Subclasses add windows and the Recycle Bin; here those report 'nothing' so a file-only goal still runs."""
    sep = os.sep

    # ---- files
    def mkdir(self, path: str) -> None:
        Path(path).mkdir(parents=True, exist_ok=True)

    def write_file(self, path: str, text: str, age_days: float = 0) -> None:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
        if age_days:
            t = time.time() - age_days * 86400
            os.utime(p, (t, t))

    def write_pdf(self, path: str, lines: list, age_days: float = 0) -> None:
        """A small PDF with a real text layer (reportlab, already a dependency of the document tools): the fixture of a document goal."""
        import io
        from reportlab.lib.pagesizes import A4
        from reportlab.pdfgen import canvas
        buf = io.BytesIO()
        c = canvas.Canvas(buf, pagesize=A4)
        y = 800
        for ln in lines:
            c.drawString(60, y, str(ln))
            y -= 22
        c.save()
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(buf.getvalue())
        if age_days:
            t = time.time() - age_days * 86400
            os.utime(p, (t, t))

    def read_file(self, path: str) -> str | None:
        try:
            raw = Path(path).read_bytes()
        except OSError:
            return None
        for enc in ("utf-8-sig", "utf-16", "cp1252"):
            try:
                return raw.decode(enc)
            except UnicodeError:
                continue
        return raw.decode("utf-8", "replace")

    def exists(self, path: str) -> bool:
        return Path(path).exists()

    def is_dir(self, path: str) -> bool:
        return Path(path).is_dir()

    def size(self, path: str) -> int:
        try:
            return Path(path).stat().st_size
        except OSError:
            return -1

    def sha(self, path: str) -> str | None:
        try:
            return hashlib.sha256(Path(path).read_bytes()).hexdigest()
        except OSError:
            return None

    def list_dir(self, path: str, pattern: str = "*") -> list[str]:
        p = Path(path)
        if not p.is_dir():
            return []
        return sorted(str(x) for x in p.iterdir() if fnmatch.fnmatch(x.name.lower(), pattern.lower()))

    def remove_tree(self, path: str) -> None:
        p = Path(path)
        if p.is_dir():
            shutil.rmtree(p, ignore_errors=True)
        elif p.exists():
            try:
                p.unlink()
            except OSError:
                pass

    def newest_since(self, directory: str, pattern: str, since: float, depth: int = 3) -> list[str]:
        """Files under `directory` (down to `depth` folders; the deliverables tool makes one folder per deck) whose name matches `pattern`
        and that were modified at or after `since` (epoch seconds), newest first."""
        out: list[tuple[float, str]] = []
        root = Path(directory)
        if not root.is_dir():
            return []
        base_depth = len(root.parts)
        for dirpath, dirs, files in os.walk(root):
            if len(Path(dirpath).parts) - base_depth >= depth:
                dirs[:] = []
            for name in files:
                if fnmatch.fnmatch(name.lower(), pattern.lower()):
                    f = os.path.join(dirpath, name)
                    try:
                        m = os.path.getmtime(f)
                    except OSError:
                        continue
                    if m >= since - 1:
                        out.append((m, f))
        return [f for _, f in sorted(out, reverse=True)]

    def pptx_info(self, path: str) -> dict:
        """Slide count, all slide text and whether a chart part exists, from the .pptx zip itself (no library)."""
        import re
        try:
            with zipfile.ZipFile(path) as z:
                names = z.namelist()
                slides = sorted(n for n in names if re.fullmatch(r"ppt/slides/slide\d+\.xml", n))
                text = " ".join(re.sub(r"<[^>]+>", " ", z.read(n).decode("utf-8", "replace")) for n in slides)
                return {"slides": len(slides), "text": " ".join(text.split()), "has_chart": any(n.startswith("ppt/charts/") for n in names)}
        except (OSError, zipfile.BadZipFile, KeyError):
            return {"slides": 0, "text": "", "has_chart": False}

    # ---- windows and the bin: nothing here; WindowsEnv has them
    def windows(self) -> list[Win]:
        return []

    def window_text(self, win: Win) -> str:
        return ""

    def close_window(self, win: Win) -> None:
        return None

    def recycle_names(self) -> list[str]:
        return []

    def recycle_purge(self, name_contains: str) -> int:
        return 0

    def recycle_file(self, path: str) -> None:
        raise EnvError("no Recycle Bin on this environment")

    def open_app(self, name: str) -> None:
        raise EnvError("cannot open apps on this environment")

    def open_notepad_text(self, path: str, text: str) -> None:
        self.write_file(path, text)

    def open_image_text(self, path: str, text: str) -> None:
        raise EnvError("cannot render pictures on this environment")

    def render_image(self, path: str, text: str) -> None:
        raise EnvError("cannot render pictures on this environment")

    def message_box(self, title: str, text: str) -> None:
        raise EnvError("cannot show a message box on this environment")

    # ---- D65 (problem_recovery): setups that deliberately create a problem for the control loop to overcome
    def minimize_window(self, title_contains: str) -> None:
        raise EnvError("cannot minimise windows on this environment")

    def start_later(self, program: str, args: list, delay_s: float) -> None:
        raise EnvError("cannot start things later on this environment")

    def message_box_later(self, title: str, text: str, delay_s: float) -> None:
        raise EnvError("cannot show a message box later on this environment")

    def slow_form(self, title: str, marker: str, hang_s: float) -> None:
        raise EnvError("cannot show a window that hangs on this environment")

    def close_later(self, title_contains: str, delay_s: float) -> None:
        raise EnvError("cannot close windows later on this environment")

    def type_unsaved(self, text: str) -> None:
        raise EnvError("cannot type into Notepad on this environment")


class WindowsEnv(LocalEnv):
    sep = "\\"

    def __init__(self, script: Path | str = PS_SCRIPT, timeout_s: float = 60.0, runner: Any = None):
        self.script, self.timeout_s = str(script), timeout_s
        self._run = runner or subprocess.run

    def _ps(self, *args: str, timeout: float | None = None) -> Any:
        cmd = ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", self.script, *[str(a) for a in args]]
        try:
            proc = self._run(cmd, capture_output=True, text=True, timeout=timeout or self.timeout_s, encoding="utf-8", errors="replace")
        except subprocess.TimeoutExpired as e:
            raise EnvError(f"envprobe {args[0]} timed out") from e
        except OSError as e:
            raise EnvError(f"cannot run PowerShell ({type(e).__name__})") from e
        if proc.returncode != 0:
            raise EnvError(f"envprobe {args[0]} failed: {(proc.stderr or proc.stdout or '')[:200]}")
        out = (proc.stdout or "").strip()
        if not out:
            return None
        try:
            return json.loads(out.splitlines()[-1])
        except ValueError as e:
            raise EnvError(f"envprobe {args[0]} printed something that is not JSON") from e

    def windows(self) -> list[Win]:
        rows = self._ps("windows") or []
        return [Win(int(r["hwnd"]), int(r["pid"]), str(r.get("process", "")), str(r.get("title", ""))) for r in rows]

    def window_text(self, win: Win) -> str:
        out = self._ps("window-text", win.hwnd, timeout=90)
        return str((out or {}).get("text", "")) if isinstance(out, dict) else ""

    def close_window(self, win: Win) -> None:
        self._ps("close", win.hwnd, timeout=30)

    def recycle_names(self) -> list[str]:
        rows = self._ps("bin-list") or []
        return [str(r.get("name", "")) + " <" + str(r.get("from", "")) + ">" for r in rows]

    def recycle_purge(self, name_contains: str) -> int:
        out = self._ps("bin-purge", name_contains)
        return int((out or {}).get("removed", 0)) if isinstance(out, dict) else 0

    def recycle_file(self, path: str) -> None:
        self._ps("bin-recycle", path)

    def open_app(self, name: str) -> None:
        self._ps("start", name)

    def open_notepad_text(self, path: str, text: str) -> None:
        self.write_file(path, text)
        self._ps("start", "notepad.exe", path)

    def render_image(self, path: str, text: str) -> None:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._ps("render-image", path, text)

    def open_image_text(self, path: str, text: str) -> None:
        self.render_image(path, text)
        self._ps("start", "mspaint.exe", path)

    def message_box(self, title: str, text: str) -> None:
        self._ps("msgbox", title, text)

    # ---- D65 (problem_recovery): UNVERIFIED until `--selftest` has run on the laptop (the PowerShell is in ps/envprobe.ps1)
    def minimize_window(self, title_contains: str) -> None:
        self._ps("minimize", title_contains)

    def start_later(self, program: str, args: list, delay_s: float) -> None:
        self._ps("start-later", int(delay_s), program, *[str(a) for a in args])

    def message_box_later(self, title: str, text: str, delay_s: float) -> None:
        self._ps("msgbox-later", int(delay_s), title, text)

    def slow_form(self, title: str, marker: str, hang_s: float) -> None:
        self._ps("slow-form", title, marker, int(hang_s))

    def close_later(self, title_contains: str, delay_s: float) -> None:
        self._ps("close-later", int(delay_s), title_contains)

    def type_unsaved(self, text: str) -> None:
        self._ps("type-unsaved", text, timeout=40)
