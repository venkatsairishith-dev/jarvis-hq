"""D54 (2026-10-26): the owner's write-and-save goals, read from his words. Pure functions, no I/O beyond one small config file.

    "open notepad and type hello, save it as notes.txt in Documents"          -> open_app, type_text, save_as
    "open VS Code in the jarvis-hq folder, create hello.py and type print(1)"  -> open_in_vscode, type_in_editor, save_as

decide.py asks this module for the parts the small model tends to leave out (the save, the file name, the folder, the VS Code launch) exactly as it
already does for "write X into WordPad" (decide.complete_plan). Nothing here authorizes anything: the node re-checks every folder and file name,
and the kernel decides each action (D27).
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path

CONFIG_DIR = Path(__file__).resolve().parents[2] / "config"
KNOWN = {"documents": "Documents", "docs": "Documents", "document": "Documents", "desktop": "Desktop", "downloads": "Downloads",
         "download": "Downloads", "outputs": "outputs", "output": "outputs", "pics": "Pictures", "photos": "Pictures", "picture": "Pictures",
         "video": "Videos", "movies": "Videos", "songs": "Music"}
ROOTS_TEXT = "Documents, Desktop, Downloads, Pictures, Videos, Music, outputs or one of your projects"
NAME_RX = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 _.()\-]{0,79}$")       # = savefile.NAME_RX on the node
CODE_APPS = {"vscode", "code", "visualstudiocode"}
BLOCKED_TYPES = {".exe", ".dll", ".msi", ".bat", ".cmd", ".ps1", ".psm1", ".vbs", ".vbe", ".wsf", ".wsh", ".hta", ".scr", ".lnk", ".reg",
                 ".com", ".pif", ".jar", ".url", ".cpl", ".msc", ".sys"}
CODE_TYPES = {".py", ".js", ".ts", ".tsx", ".jsx", ".java", ".c", ".h", ".cpp", ".hpp", ".cs", ".go", ".rs", ".rb", ".php", ".sql", ".sh", ".html",
              ".htm", ".css", ".json", ".md", ".txt", ".csv", ".xml", ".yaml", ".yml", ".toml", ".ini", ".log", ".rtf", ".png", ".jpg", ".jpeg", ".bmp", ".gif"}
MAX_TYPED = 3900                                     # one type_focused job (the kernel allows 4000)
MAX_TEXT = 20000                                     # the longest text a plan carries (typed in pieces of MAX_TYPED)


# ---------------------------------------------------------------------------------------------------------------- the typed text
def normalize_text(t: str) -> str:
    """Same as the node's savefile.normalize_text (a test keeps them equal)."""
    t = str(t or "").replace("\r\n", "\n").replace("\r", "\n").lstrip("﻿")
    return "\n".join(line.rstrip() for line in t.split("\n")).strip()


def text_digest(t: str) -> str:
    return hashlib.sha256(normalize_text(t).encode("utf-8")).hexdigest()[:12]


def chunks(text: str, size: int = MAX_TYPED) -> list[str]:
    """The text in pieces of at most `size` characters, cut at line ends when it can be."""
    out, cur = [], ""
    for line in str(text).splitlines(keepends=True):
        while len(line) > size:
            if cur:
                out.append(cur)
                cur = ""
            out.append(line[:size])
            line = line[size:]
        if len(cur) + len(line) > size:
            out.append(cur)
            cur = ""
        cur += line
    if cur:
        out.append(cur)
    return out


# ---------------------------------------------------------------------------------------------------------------- folders
def known_roots() -> dict:
    """{lower-case word: canonical root} for the four known folders and every project in config/projects.json (names only; the paths are the node's)."""
    roots = {"documents": "Documents", "desktop": "Desktop", "downloads": "Downloads", "pictures": "Pictures", "videos": "Videos", "music": "Music",
             "outputs": "outputs"}
    path = Path(os.environ.get("JARVIS_PROJECTS_FILE") or CONFIG_DIR / "projects.json")
    try:
        names = json.loads(path.read_text(encoding="utf-8-sig")).get("projects") or {}
    except (OSError, ValueError, AttributeError):
        names = {}
    for n in names:
        if isinstance(n, str) and re.fullmatch(r"[A-Za-z0-9_.-]{1,40}", n):
            roots.setdefault(n.lower(), n)
    return roots


def folder_spec(raw: str) -> tuple[str, str]:
    """(canonical folder like "Documents/notes", "") or ("", why not). Only a known folder or a project, and folders inside it: a drive letter, a
    network path, "..", or any other place is refused here (the node checks again, and that the inner folders exist)."""
    t = " ".join(str(raw or "").strip().strip("\"'").split())
    if re.search(r"(?<![A-Za-z0-9])[A-Za-z]:|^\\\\|^//|^/|^~|\.\.|[*?<>|\0]", t):
        return "", "outside"
    parts = [p.strip() for p in re.split(r"[\\/]+", t) if p.strip()]
    if parts:                                          # filler words belong to the owner's phrase for the root ("the jarvis-hq project folder"), not to inner folder names
        parts[0] = " ".join(re.sub(r"(?i)(?<![\w.-])(?:my|the|a|folder|directory|dir|project|drive)(?![\w.-])", " ", parts[0]).split())
        parts = [p for p in parts if p]
    if not parts:
        return "", "empty"
    roots = known_roots()
    first = parts[0].lower()
    first = KNOWN.get(first, first)
    canon = roots.get(first.lower())
    if canon is None:
        return "", "outside"
    return "/".join([canon, *parts[1:]]), ""


def folder_question(raw: str = "") -> str:
    return f"I can only save into {ROOTS_TEXT}. Which of them should it be?"


# ---------------------------------------------------------------------------------------------------------------- file names
def default_name(app: str = "") -> str:
    import time
    return time.strftime("JARVIS note %Y-%m-%d %H%M.txt")


def file_name(raw: str, default_ext: str = ".txt") -> tuple[str, str]:
    """(plain file name with an extension, "") or ("", why not). Programs and scripts are refused; so is anything that is not a plain name."""
    n = " ".join(str(raw or "").strip().strip("\"'").split())
    if not n:
        return "", "empty"
    if not os.path.splitext(n)[1]:
        n += default_ext
    ext = os.path.splitext(n)[1].lower()
    if ext in BLOCKED_TYPES:
        return "", "program"
    if not NAME_RX.match(n) or n.endswith((".", " ")) or ".." in n or ext not in CODE_TYPES:
        return "", "name"
    return n, ""


def is_code_app(app: str) -> bool:
    return re.sub(r"[^a-z]", "", str(app or "").lower()) in CODE_APPS


# ---------------------------------------------------------------------------------------------------------------- reading the request
SAVE_TAIL = re.compile(r"(?is)(?:(?:,|;|\.)\s*|\s+(?:and|then)\s+)(?:please\s+)?save\b.*$")
SAVE_WORD = re.compile(r"(?i)\b(?:and\s+|then\s+)?save\b")
Q = r"[\"'“‘]"
QE = r"[\"'”’]"
NAME_AFTER = re.compile(rf"(?i)\b(?:as|named|called|under(?:\s+the\s+name)?)\s+(?:{Q}([^\"'”’]{{1,80}}){QE}|([\w()-][\w .()-]{{0,79}}?\.[A-Za-z0-9]{{1,6}})\b|([\w()-]+))")
FOLDER_AFTER = re.compile(rf"(?i)\b(?:in|into|to|on|inside|under)\s+(?:{Q}([^\"'”’]{{1,120}}){QE}|((?:my\s+|the\s+)?[\w:./\\~-]+(?:\s+(?:folder|directory|project)){{0,2}}))"
                          r"(?=\s*(?:,|;|\.\s|\.$|!|\?|$|\band\b|\bthen\b|\bas\b|\bnamed\b|\bcalled\b))")


def parse_save(request: str) -> dict | None:
    """What the owner said about saving: {"name", "folder"} (each "" when not said), or None when he did not say to save at all."""
    r = " ".join(str(request or "").split())
    m = SAVE_WORD.search(r)
    if not m:
        return None
    tail = r[m.end():]
    name = folder = ""
    nm = NAME_AFTER.search(tail)
    if nm:
        name = (nm.group(1) or nm.group(2) or nm.group(3) or "").strip()
        tail_wo_name = tail[:nm.start()] + " " + tail[nm.end():]
    else:
        tail_wo_name = tail
    fm = FOLDER_AFTER.search(tail_wo_name)
    if fm:
        folder = (fm.group(1) or fm.group(2) or "").strip()
        if name and folder.lower() == name.lower():
            folder = ""
    return {"name": name, "folder": folder}


VSCODE_NAME = r"(?:vs\s?code|visual\s+studio\s+code)"
VSCODE_OPEN = re.compile(rf"(?i)\b(?:open|launch|start)\s+(?:up\s+)?{VSCODE_NAME}\b")
VSCODE_IN = re.compile(rf"(?i)\b(?:open|launch|start)\s+(?:up\s+)?{VSCODE_NAME}\s+(?:app\s+)?(?:in|on|at|with|inside|from|to)\s+"
                       # "on a new folder C:\x\y", "in the folder D:\work\app", "on folder ~/code/app": the words before the path are skipped, but "the folder" is skipped only when a real
                       # path follows ("in the project folder" keeps meaning that phrase). Before this, the path was never taken and VS Code asked "which folder?" (laptop 2026-10-28)
                       r"(?:(?:(?:a|an|the|my)\s+)?(?:folder|directory)\s+(?=[\w.:~\\/-])|(?:(?:a|an|the)\s+)?new\s+(?:folder|directory|project)\s+)?"
                       rf"(?:{Q}([^\"'”’]{{1,120}}){QE}|((?:my\s+|the\s+)?[\w:./\\~-]+(?:\s+(?:folder|directory|project))?(?:\s+folder)?))"
                       r"(?=\s*(?:,|;|\.\s|\.$|!|\?|$|\band\b|\bthen\b))")
VSCODE_FILE = re.compile(rf"(?i)\b(?:create|make|add|new)\s+(?:a\s+|an\s+|the\s+)?(?:new\s+)?(?:file\s+)?(?:called\s+|named\s+)?(?:{Q}([^\"'”’]{{1,80}}){QE}|([\w()-][\w .()-]{{0,60}}?\.[A-Za-z0-9]{{1,6}})\b)")
TYPE_VERB = re.compile(r"(?i)\b(?:and\s+|then\s+|,\s*)?(?:type|write|enter|put|paste)\s+(?:in\s+|into\s+)?(?:it\s+)?(?:the\s+following\s+)?[:\-]?\s*")
FENCE = re.compile(r"```(?:[A-Za-z0-9_+-]*[ \t]*\n)?(.*?)```", re.S)


def said_text(request: str, after: int = 0) -> str:
    """The text to type, as the owner said it: a fenced block, else a quoted string after type/write, else the rest of the sentence after the verb.
    The save clause is not part of it."""
    r = str(request or "")
    f = FENCE.search(r)
    if f:
        return f.group(1).strip("\n")
    r = SAVE_TAIL.sub("", r[after:]) if SAVE_WORD.search(r[after:]) else r[after:]
    m = TYPE_VERB.search(r)
    if not m:
        return ""
    rest = r[m.end():].strip()
    q = re.match(rf"{Q}(.+){QE}\s*[.!]?$", rest, re.S)
    if q:
        return q.group(1).strip()
    return rest.rstrip(" .!").strip() if rest else ""


def parse_vscode(request: str) -> dict | None:
    """A VS Code goal: {"folder" (raw, "" if none said), "file" (raw), "text", "wants_file"} or None when the request is not about VS Code."""
    r = " ".join(str(request or "").split()) if "```" not in str(request or "") else str(request)
    if not VSCODE_OPEN.search(r):
        return None
    fm = VSCODE_IN.search(r)
    folder = (fm.group(1) or fm.group(2) or "").strip() if fm else ""
    after = fm.end() if fm else VSCODE_OPEN.search(r).end()
    filem = VSCODE_FILE.search(r, after)
    file = (filem.group(1) or filem.group(2) or "").strip() if filem else ""
    text = said_text(r, filem.end() if filem else after)
    return {"folder": folder, "file": file, "text": text, "wants_file": bool(filem)}
