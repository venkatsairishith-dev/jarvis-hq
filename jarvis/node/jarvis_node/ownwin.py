"""JARVIS's own windows (D74, 2026-10-27): the node never closes one of them for an action, whatever the target says.

Found on the real laptop (Phase 7): the goal "Close the window" closed JARVIS's own chat window. The kernel refuses a close aimed at the face's process
(policy.own_window); this is the second layer, at the place where the window is really picked: the node knows each window's process AND title, so it also
refuses a close that would land on the console's browser tab, and it refuses when the kernel package is not importable (the same pattern as sensitive.py:
the kernel's list as given, plus the node's own floor).

Only the CLOSE family is refused here (the `close` op, and the close chords alt+f4 / ctrl+w / ctrl+q when they are aimed at an own window). Typing into, reading
and minimising the avatar are other questions with other rules (windowpick.py: only a window JARVIS opened is changed when there are several).
"""
import os
import re
import sys

FLOOR_PROCESSES = {"jarvis", "jarvisface", "jarvis-face", "jarvis_face", "jarvis.face", "electron"}
BROWSERS = {"chrome", "msedge", "firefox", "brave", "opera", "vivaldi", "arc"}
FLOOR_TITLE = re.compile(r"^jarvis(?:\s*[-–|:]\s*|\s+(?:console|dashboard|editor|chat)\b|$)", re.I)
CLOSE_CHORDS = {"alt+f4", "ctrl+w", "ctrl+shift+w", "ctrl+q", "ctrl+shift+q"}
REFUSAL = "refused: that is one of JARVIS's own windows; JARVIS does not close its own windows"

try:
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "kernel"))
    from jarvis_kernel.policy import own_window as _kernel_check   # noqa: E402
except Exception:                       # a node without the kernel package: the floor alone still refuses
    _kernel_check = None


def _proc(name) -> str:
    return re.sub(r"\.exe$", "", str(name or "").strip().lower())


def why(process, title="") -> str:
    """Why this process / window title is one of JARVIS's own windows ("" when it is not): the kernel's reading as given, and the node's floor."""
    if _kernel_check is not None:
        try:
            got = _kernel_check(process, title)
        except Exception:               # noqa: BLE001 - a broken list must not become "nothing is JARVIS's"
            got = ""
        if got:
            return got
    p = _proc(process)
    if p in FLOOR_PROCESSES:
        return "JARVIS's own window"
    if title and (not p or p in BROWSERS) and FLOOR_TITLE.search(str(title).strip()):
        return "JARVIS's own console window"
    return ""


def is_close(op: str, text: str = "") -> bool:
    """Is this ui action a close: the `close` op, or a key chord that closes the window in front."""
    op = str(op or "").strip().lower()
    return op == "close" or (op == "key" and str(text or "").strip().lower() in CLOSE_CHORDS)


def windows_of(proc: str, lister=None) -> list:
    from . import sensitive
    return sensitive.windows_of(proc, lister)


def refuse_close(op: str, target: str, text: str = "", lister=None) -> str:
    """The node's refusal for a close of an own window ("" when the close may go on). `target` is "process|element". A process that is JARVIS's own is
    refused outright; a browser is refused only when EVERY one of its windows is the console's (with several, windowpick.py already refuses a guess)."""
    if not is_close(op, text):
        return ""
    proc = str(target or "").partition("|")[0].strip()
    if why(proc):
        return REFUSAL
    wins = windows_of(proc, lister)
    if wins and all(why(w.get("process") or proc, w.get("title") or "") for w in wins):
        return REFUSAL
    return ""
