"""D54 (2026-10-26): "a save is part of the request". Save As and VS Code, for the owner's own write-and-save goals.

Owner: "When I told JARVIS to open Notepad and type so and so, I gave it permission to save, so it can control my mouse and click the Save
button. Or: open VS Code in a certain folder, type the code." Three pieces live here, each a SINGLE verified step the kernel authorizes first
(ui.save_as, pc.open_code; ui.type_focused is in winbackend.py):

  save_as      Save As (ctrl+shift+s) in the target window, the file name box set to a FULL path, the dialog's Save button pressed (the
               PowerShell is winbackend.SAVE_AS), then checked: the file is on disk, the window title names it, and when the caller sent
               a digest of the typed text, the saved file's text is that text.
  open_code    start VS Code on a folder (Code.exe found in its usual places; the only arguments are "-n" and the folder).
  guards       the folder allowlist, the file name rules, "never overwrite", and a refusal to click Yes on a replace prompt.

Rules that hold for every save (tests: node/tests/test_savefile.py):
  * only the known folders Documents, Desktop, Downloads, the outputs folder and the project folders of config/projects.json, plus folders that
    ALREADY exist inside them; a path, a drive letter, "..", a hidden folder, a link out of the root, a system or secrets folder is refused;
  * a file name that exists is never used: the name gets " (2)", " (3)" ... (the PowerShell refuses a second time if the path exists);
  * if a Replace / Confirm Save As prompt shows anyway, the script presses No and cancels; this module never presses Yes, and ui_action
    (jobs.py) refuses a click or Enter on such a prompt from anyone;
  * programs and scripts are never saved (.exe .bat .cmd .ps1 .vbs ...).
"""
from __future__ import annotations

import hashlib
import os
import re
import subprocess

from . import sensitive

KNOWN_ROOTS = ("documents", "desktop", "downloads", "pictures", "videos", "music")      # Windows known folders (pcopen.KNOWN_FOLDERS)
SAVE_TYPES = {".txt", ".md", ".rtf", ".csv", ".json", ".xml", ".yaml", ".yml", ".toml", ".ini", ".log", ".html", ".htm", ".css",
              ".py", ".js", ".ts", ".tsx", ".jsx", ".java", ".c", ".h", ".cpp", ".hpp", ".cs", ".go", ".rs", ".rb", ".php", ".sql", ".sh",
              ".png", ".jpg", ".jpeg", ".bmp", ".gif"}                  # new pictures too (a drawing saved from Paint)
BLOCKED_TYPES = {".exe", ".dll", ".msi", ".bat", ".cmd", ".ps1", ".psm1", ".vbs", ".vbe", ".wsf", ".wsh", ".hta", ".scr", ".lnk", ".reg",
                 ".com", ".pif", ".jar", ".url", ".cpl", ".msc", ".sys"}
NAME_RX = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 _.()\-]{0,79}$")
RESERVED = re.compile(r"(?i)^(con|prn|aux|nul|com[1-9]|lpt[1-9])(\..*)?$")
HIDDEN_OR_SYSTEM = re.compile(r"(?i)^(\.|\$|startup|windows|system32|syswow64|program files.*|programdata|appdata|\$recycle\.bin)")
CODE_FAMILY = "Code.exe"
MAX_COUNTER = 99
WAIT_FILE_S = 5.0
WAIT_TITLE_S = 4.0

ROOTS_HELP = "Documents, Desktop, Downloads, outputs or one of the projects JARVIS may use"


class SafeError(Exception):
    """A refusal or a failure whose wording is fixed here (never screen text), so the node may report it in full. `extra` holds short fixed
    facts (the saved name, a flag) that go into the result as they are."""

    def __init__(self, code: str, public: str, **extra):
        super().__init__(code)
        self.code, self.public, self.extra = code, public, extra


# ---------------------------------------------------------------------------------------------------------------- folders
def folder_roots(known: dict | None = None, projects: dict | None = None, outputs: str | None = None) -> dict:
    """{lower-case key: real path} of every folder a save or a VS Code launch may start from. `known`, `projects` and `outputs` are the test seams."""
    from . import pcopen
    out = {}
    for name in KNOWN_ROOTS:
        try:
            p = pcopen.known_folder(name, known)
        except Exception:                                   # no Windows (or no such folder): this root does not exist here
            p = None
        if p and os.path.isdir(p):
            out[name] = p
    if outputs is None:
        try:
            from .jobs import outputs_root
            outputs = outputs_root()
        except Exception:
            outputs = None
    if outputs and os.path.isdir(outputs):
        out["outputs"] = os.path.realpath(outputs)
    try:
        if projects is None:
            from .jobs import load_projects
            projects = load_projects()
    except Exception:
        projects = {}
    for name, spec in (projects or {}).items():
        path = ((spec or {}).get("path") or {}).get(os.name)
        if isinstance(name, str) and re.fullmatch(r"[A-Za-z0-9_.-]{1,40}", name) and path and os.path.isdir(path):
            out.setdefault(name.lower(), os.path.realpath(path))
    return out


def _inside(path: str, root: str) -> bool:
    p, r = os.path.normcase(os.path.realpath(path)), os.path.normcase(os.path.realpath(root))
    return p == r or p.startswith(r.rstrip(os.sep) + os.sep)


def resolve_folder(spec: str, roots: dict) -> tuple[str, str]:
    """(real folder path, label) for "Documents", "Documents/notes", "jarvis-hq/kernel" ... The first part must be a root; every further part must be
    a folder that exists already (case does not matter); nothing may lead out of the root. Raises SafeError."""
    text = str(spec or "").strip()
    refuse = SafeError("folder", f"I can only use {ROOTS_HELP}, and folders that already exist inside them")
    if not text or re.match(r"^[A-Za-z]:", text) or text.startswith(("\\\\", "//", "/", "~")) or re.search(r"[\r\n\0:*?\"<>|]", text):
        raise refuse
    parts = [p.strip() for p in re.split(r"[\\/]+", text) if p.strip()]
    first = re.sub(r"(?i)^(my|the)\s+", "", parts[0]).strip().lower()
    root = roots.get(first)
    if not root:
        raise refuse
    cur, label = root, [first.capitalize() if first in KNOWN_ROOTS else first]
    for comp in parts[1:]:
        if comp in (".", "..") or HIDDEN_OR_SYSTEM.match(comp):
            raise refuse
        try:
            hit = next((e for e in os.listdir(cur) if e.lower() == comp.lower() and os.path.isdir(os.path.join(cur, e))), None)
        except OSError:
            hit = None
        if hit is None:
            raise SafeError("no_folder", "that folder does not exist inside the allowed folders; I do not create folders")
        cur = os.path.realpath(os.path.join(cur, hit))
        if not _inside(cur, root):
            raise refuse
        label.append(hit)
    if not _inside(cur, root):
        raise refuse
    from . import filesearch
    if filesearch.is_secret_path(cur) or sensitive.why(cur):
        raise SafeError("secret", "that folder is a secrets or sensitive place; I do not write there")
    return cur, "/".join(label)


# ---------------------------------------------------------------------------------------------------------------- file names
def safe_name(name: str) -> str:
    """The file name, checked: a plain name with an allowed extension; programs and scripts are refused. Raises SafeError."""
    n = str(name or "").strip()
    if not NAME_RX.match(n) or n.endswith((".", " ")) or ".." in n or RESERVED.match(n):
        raise SafeError("name", "that is not a plain file name (letters, digits, spaces, dots, dashes; at most 80 characters)")
    ext = os.path.splitext(n)[1].lower()
    if ext in BLOCKED_TYPES:
        raise SafeError("type", "I do not save programs or scripts")
    if ext not in SAVE_TYPES:
        raise SafeError("type", "that file type is not one I save (text, notes and code files only)")
    return n


def free_name(folder: str, name: str) -> tuple[str, bool]:
    """(name that does not exist in the folder, whether it had to change). "a.txt" -> "a (2).txt" -> "a (3).txt". Never an existing name."""
    if not os.path.lexists(os.path.join(folder, name)):
        return name, False
    stem, ext = os.path.splitext(name)
    for n in range(2, MAX_COUNTER + 1):
        cand = f"{stem} ({n}){ext}"
        if not os.path.lexists(os.path.join(folder, cand)):
            return cand, True
    raise SafeError("full", "too many files with that name already exist")


# ---------------------------------------------------------------------------------------------------------------- the typed text
def normalize_text(t: str) -> str:
    """Text as comparable across editors: line ends and a byte-order mark do not matter, nor trailing spaces or blank ends."""
    t = str(t or "").replace("\r\n", "\n").replace("\r", "\n").lstrip("﻿")
    return "\n".join(line.rstrip() for line in t.split("\n")).strip()


def text_digest(t: str) -> str:
    """12 hex characters of the normalized text. The desktop plugin has the same function (decide.text_digest); a test keeps them equal."""
    return hashlib.sha256(normalize_text(t).encode("utf-8")).hexdigest()[:12]


def read_text(path: str) -> str | None:
    """The saved file's text, read with the node's own helper (repair._read), or None when it is not readable text."""
    from .repair import _read
    try:
        data = _read(path)
    except OSError:
        return None
    if len(data) > 2_000_000:
        return None
    for enc in ("utf-8-sig", "utf-16"):
        try:
            return data.decode(enc)
        except UnicodeError:
            continue
    return None


# ---------------------------------------------------------------------------------------------------------------- save as
FAILURES = {
    "path exists": ("exists", "a file with that name already exists; nothing was saved over it"),
    "no save dialog": ("no_dialog", "the Save As dialog did not open; nothing was saved"),
    "no file name box": ("no_box", "the Save As dialog had no file name box I could use; nothing was saved"),
    "no save button": ("no_button", "the Save As dialog had no Save button I could press; nothing was saved"),
    "replace prompt": ("replace", "a Replace prompt appeared: I pressed No and cancelled, so nothing was replaced"),
    "save dialog error": ("dialog_error", "the Save As dialog reported a problem; I cancelled it and nothing was saved"),
    "target is not the foreground window": ("not_front", "the window could not be brought to the front, so nothing was sent to it"),
}


def _failure(e: Exception) -> SafeError:
    text = str(e)
    for needle, (code, public) in FAILURES.items():
        if needle in text:
            return SafeError(code, public)
    return SafeError("failed", "the Save As step failed")


def _sleep(s: float) -> None:
    import time
    time.sleep(s)


def save_as(target: str, params: dict, *, run=None, lister=None, roots: dict | None = None, wait=_sleep, timeout_s: float = WAIT_FILE_S) -> dict:
    """The winbackend 'save_as' action. target "process|"; params["text"] = "<folder>|<file name>[|<digest of the typed text>]".
    Returns what was observed (see ui_action for the keys that leave the node); raises SafeError when it did not happen."""
    proc, _, _elem = str(target).partition("|")
    parts = str(params.get("text", "")).split("|")
    if len(parts) not in (2, 3) or not re.fullmatch(r"[A-Za-z0-9_.\- ]{1,80}", proc or ""):
        raise SafeError("bad_request", "the save request was not in the expected form")
    folder_spec, wanted, digest = parts[0], parts[1], (parts[2] if len(parts) == 3 else "")
    if digest and not re.fullmatch(r"[0-9a-f]{12}", digest):
        raise SafeError("bad_request", "the save request was not in the expected form")
    folder, label = resolve_folder(folder_spec, roots if roots is not None else folder_roots())
    name, renamed = free_name(folder, safe_name(wanted))
    full = os.path.join(folder, name)
    if os.path.lexists(full):                                        # belt and braces: free_name just said it does not exist
        raise SafeError("exists", FAILURES["path exists"][1])
    if run is None:
        from . import winbackend

        def run(env):
            return winbackend._run(winbackend.SAVE_AS, env)
    from .windowpick import SeveralWindowsSayWhich
    try:
        run({"JARVIS_UI_PROC": proc, "JARVIS_SAVE_PATH": full})
    except SeveralWindowsSayWhich:
        raise                                                        # ui_action reports its class name, as for every other action
    except RuntimeError as e:                                        # "ui-action-failed:<code>" (winbackend._run): a fixed reason code
        raise _failure(e) from None
    # --- verify: the file is on disk, the window names it, the text is what was typed
    import time
    end = time.monotonic() + timeout_s
    while not os.path.isfile(full):
        if time.monotonic() >= end:
            raise SafeError("not_on_disk", "I pressed Save but the file is not on disk", folder=label)
        wait(0.25)
    out = {"observed": "the file was found on disk after Save", "saved": name, "folder": label, "file_ok": True, "renamed": renamed}
    out["title_ok"] = _title_names(proc, name, lister, wait, min(WAIT_TITLE_S, timeout_s))
    if digest:
        text = read_text(full)
        if text is None:
            out["content_ok"] = "unreadable"
        elif text_digest(text) == digest:
            out["content_ok"] = True
            out["observed"] = "the file was found on disk after Save and its text is what was typed"
        else:
            raise SafeError("content", "the file was saved, but its text is not what was typed; check it", saved=name, folder=label, file_ok=True, content_ok=False)
    return out


def _title_names(proc: str, name: str, lister, wait, timeout_s: float = WAIT_TITLE_S) -> bool:
    """Does a window of the process show the saved file's name in its title? (Notepad: "name.txt - Notepad"; VS Code: "name.py - folder - Visual Studio Code".)"""
    import time
    end = time.monotonic() + timeout_s
    stem = os.path.splitext(name)[0].lower()
    while True:
        for w in sensitive.windows_of(proc, lister):
            t = str(w.get("title") or "").lower()
            if name.lower() in t or (stem and re.search(r"(?<!\w)" + re.escape(stem) + r"(?!\w)", t)):
                return True
        if time.monotonic() >= end:
            return False
        wait(0.4)


# ---------------------------------------------------------------------------------------------------------------- a replace prompt is never answered Yes
CONFIRM_TITLE = re.compile(r"(?i)confirm\s+save\s+as|^\s*(replace|overwrite)\b|\b(replace|overwrite)\s+(the\s+)?file")
YES_ELEMENT = re.compile(r"(?i)^&?(yes|replace|overwrite|ok)\b")


def refuse_replace_answer(op: str, target: str, text: str, lister=None) -> str:
    """A reason ("" when fine) for refusing a click or key aimed at a window of this process while a Replace / Confirm Save As prompt is open:
    the Yes there overwrites a file, and that is the owner's own choice, never JARVIS's (D54)."""
    if op not in ("click", "key", "click_at"):
        return ""
    proc, _, elem = str(target).partition("|")
    elem = elem.strip()
    said = str(text or "").strip().lower()
    if not ((op == "click" and YES_ELEMENT.match(elem)) or op == "click_at" or (op == "key" and said in ("enter", "y", "space"))):
        return ""                                            # not an answer to a prompt: no need to look at the windows
    if any(CONFIRM_TITLE.search(str(w.get("title") or "")) for w in sensitive.windows_of(proc, lister)):
        return "refused: a Replace prompt is open, and answering Yes would overwrite a file; that is for the owner to do"
    return ""


# ---------------------------------------------------------------------------------------------------------------- VS Code
def find_code_exe(env: dict | None = None, exists=os.path.isfile) -> str | None:
    """Code.exe in its three usual places (per-user install, Program Files, Program Files (x86)). Nothing from PATH, nothing the caller names."""
    env = os.environ if env is None else env
    bases = [(env.get("LOCALAPPDATA"), ("Programs", "Microsoft VS Code")), (env.get("ProgramFiles"), ("Microsoft VS Code",)),
             (env.get("ProgramFiles(x86)"), ("Microsoft VS Code",))]
    for base, rel in bases:
        if base:
            p = os.path.join(base, *rel, CODE_FAMILY)
            if exists(p):
                return p
    return None


def open_code(root: str, rel: str, *, roots: dict | None = None, exe: str | None = None, launcher=None, real: bool | None = None) -> dict:
    """pc_open op open_code: start VS Code on <root>[/<rel>] (a known folder or project, and a folder already inside it). The only program is
    Code.exe, the only arguments are -n (a new window, so the one JARVIS opened is its own) and the folder."""
    spec = str(root or "").strip() + (("/" + str(rel).strip().strip("\\/")) if str(rel or "").strip() else "")
    folder, label = resolve_folder(spec, roots if roots is not None else folder_roots())
    code = exe or find_code_exe()
    if not code or os.path.basename(code).lower() != CODE_FAMILY.lower():
        raise SafeError("no_code", "VS Code (Code.exe) was not found in its usual places")
    args = [code, "-n", folder]
    real = (launcher is None) if real is None else real

    def go():
        (launcher or (lambda a: subprocess.Popen(a, close_fds=True)))(args)
    if real and os.name == "nt":
        from . import windowpick
        windowpick.launch_and_record_any(go)             # remember the new window: it is the one JARVIS may type into
    else:
        go()
    return {"ok": True, "opened": label}
