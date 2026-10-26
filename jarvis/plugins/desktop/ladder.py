"""The problem-solving ladder, pure part (D65): recognising a problem from what was SEEN, and the fixed tables the rungs use.

Nothing here touches a PC. `pcagent.py` runs the rungs; this file answers "what kind of problem is this" and "which button / key / menu path".
Everything it reads is screen text, so it is only ever used to RAISE care (find a dialog, find a risk word, find an app): it never lowers a class and it never
grants anything. The decision engine's `dialog_class` (raise-only) and the kernel's own policy decide what may be pressed.

Problem classes (lessons.PROBLEMS plus the ones that are never learned: uac, resource, owner_active, loop):
  a  wrong_window / no_window / minimized / other_desktop      focus by title or process, restore, switch desktop, alt-tab, launch
  b  dialog                                                    classify A / B / C; answer only A; ask on B / C; UAC is NEVER touched
  c  element_missing                                           wait by condition, scroll, expand menus, search / palette, keyboard, vision locate
  d  not_responding                                            wait up to a budget, look at CPU, offer to close and relaunch (class B)
  e  typed_not_landed                                          re-focus, paste route, read back
  f  no_effect (twice)                                         other control, other route (menu vs shortcut), re-plan, then ask
  g  loop                                                      the same signature three times: stop
  h  resource                                                  disk full, no network, app missing: say it, offer the next best (installing is class C: ask)
Standard library only.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

# ------------------------------------------------------------------------------------------------------------------------------ problems
NEVER_LEARNED = ("uac", "resource", "owner_active", "loop")


@dataclass
class Problem:
    kind: str                                   # one of lessons.PROBLEMS or NEVER_LEARNED
    app: str = ""                               # the process the task works in
    detail: str = ""                            # a fixed word ("disk_full", "app_missing", ...) or '' ; never screen text
    dialog: dict | None = None                  # the dialog involved (local use only: it is never put into a lesson, the state file or an ask form's words)


UAC_PROCS = {"consent", "credentialuibroker", "consentui", "securityhealthsystray"}
UAC_TEXT = re.compile(r"(?i)user account control|do you want to allow this app to make changes|make changes to your device|run as administrator|"
                      r"enter (?:the )?administrator|administrator password|windows security|credential")
NOT_RESPONDING = re.compile(r"(?i)\(not responding\)|is not responding|stopped working|has stopped responding|isn't responding")
NO_SPACE = re.compile(r"(?i)not enough (?:disk )?space|disk is full|no space left|insufficient disk space|low disk space")
NO_NET = re.compile(r"(?i)no internet|you(?:'| a)re offline|can(?:'|no)t connect to the (?:internet|server)|err_internet_disconnected|check your (?:internet )?connection|"
                    r"no network|dns_probe|unable to connect")
UNREADABLE_RX = re.compile(r"\(unreadable dialog\)")
SAVE_PROMPT = re.compile(r"(?i)save changes|do you want to save|want to save|unsaved")
HARD_NO = re.compile(r"(?i)replace|overwrite|don.?t save|discard|delete|remove|erase|format|install|update now|upgrade|buy|purchase|pay|send|sign ?in|log ?in|accept|agree|"
                     r"allow all|run as|yes to all|empty|uninstall|restart now|reboot|shut ?down|subscribe|continue anyway|trust")
# a class-A dialog is closed with the most ordinary button first
A_PREFER = ("ok", "got it", "continue", "next", "yes", "open", "save", "close", "done", "finish", "not now", "no thanks", "later", "skip", "dismiss", "cancel")
DISMISS = ("not now", "no thanks", "later", "remind me later", "skip", "dismiss", "close", "maybe later", "cancel")


def _n(s) -> str:
    return re.sub(r"[^a-z0-9 ]", "", str(s or "").lower().replace("&", "")).strip()


def is_uac(windows: list[dict], dialogs: list[dict]) -> bool:
    """Windows' administrator prompt (UAC) or a credential prompt is on screen. It is the owner's alone: JARVIS stops and says so. The secure desktop itself is not
    visible to UI Automation at all; this catches the cases where it is (consent.exe in the window list, a dialog saying so)."""
    for w in windows or []:
        if re.sub(r"[^a-z]", "", str(w.get("process") or "").lower()) in UAC_PROCS or UAC_TEXT.search(str(w.get("title") or "")):
            return True
    for d in dialogs or []:
        if UAC_TEXT.search(" ".join([str(d.get("title") or ""), *[str(t) for t in d.get("texts") or []]])):
            return True
    return False


def dialog_words(d: dict) -> str:
    """The dialog as ONE line of words, for the decision engine and for the kernel's ctx (local use; the caller scrubs it)."""
    parts = [str(d.get("title") or ""), *[str(t) for t in d.get("texts") or []], "buttons: " + ", ".join(str(b) for b in d.get("buttons") or [])]
    return " ".join(" ".join(parts).split())


def pick_button(d: dict, cls: str) -> str:
    """The button to press for a dialog of class A ('' when there is no safe one). B and C are never answered here: the caller asks the owner."""
    if cls != "A":
        return ""
    buttons = [str(b) for b in d.get("buttons") or [] if str(b).strip()]
    safe = [b for b in buttons if not HARD_NO.search(b)]
    for want in A_PREFER:
        for b in safe:
            if _n(b) == want:
                return b
    return safe[0] if len(safe) == 1 and len(buttons) == 1 else ""


def dismiss_button(d: dict) -> str:
    for want in DISMISS:
        for b in d.get("buttons") or []:
            if _n(b) == want:
                return str(b)
    return ""


def is_save_prompt(d: dict) -> bool:
    return bool(SAVE_PROMPT.search(dialog_words(d)))


def resource_from_text(text: str) -> str:
    if NO_SPACE.search(text or ""):
        return "disk_full"
    if NO_NET.search(text or ""):
        return "no_network"
    return ""


APP_ALTERNATIVES = {"winword": ["wordpad", "notepad"], "word": ["wordpad", "notepad"], "excel": ["notepad"], "powerpnt": ["notepad"], "powerpoint": ["notepad"],
                    "chrome": ["msedge"], "firefox": ["msedge", "chrome"], "code": ["notepad"], "vscode": ["notepad"], "mspaint": ["paint"], "paint": ["mspaint"]}


def alternatives_for(app: str) -> list[str]:
    return list(APP_ALTERNATIVES.get(re.sub(r"[^a-z]", "", str(app or "").lower()), []))


# ------------------------------------------------------------------------------------------------------------------------------ shortcuts <-> menus
SHORTCUT_MENU = {"ctrl+s": ("File", "Save"), "ctrl+shift+s": ("File", "Save as"), "ctrl+n": ("File", "New"), "ctrl+o": ("File", "Open"), "ctrl+p": ("File", "Print"),
                 "ctrl+z": ("Edit", "Undo"), "ctrl+y": ("Edit", "Redo"), "ctrl+f": ("Edit", "Find"), "ctrl+a": ("Edit", "Select all"), "ctrl+c": ("Edit", "Copy"),
                 "ctrl+v": ("Edit", "Paste"), "ctrl+x": ("Edit", "Cut"), "ctrl+h": ("Edit", "Replace")}
MENU_SHORTCUT = {(m.lower(), i.lower()): k for k, (m, i) in SHORTCUT_MENU.items()}
MENU_NAMES = ("file", "edit", "view", "insert", "format", "tools", "help", "window", "home", "design", "layout")
PALETTE_APPS = {"code": "ctrl+shift+p", "code - insiders": "ctrl+shift+p", "cursor": "ctrl+shift+p", "vscodium": "ctrl+shift+p"}


def menu_for_key(key: str) -> tuple[str, str] | None:
    """The File / Edit menu path of a common shortcut: the other way to do the same thing when the shortcut had no effect."""
    return SHORTCUT_MENU.get(str(key or "").lower())


def key_for_element(element: str) -> str:
    """The shortcut of a menu item JARVIS could not click, when it is a well-known one (Save, Copy, ...)."""
    e = _n(element)
    for (m, i), k in MENU_SHORTCUT.items():
        if _n(i) == e:
            return k
    return ""


def mnemonic(element: str, menubar: bool) -> str:
    """alt+<first letter> for a menu-bar item (File -> alt+f). Only for a menu bar: a dialog's buttons are never guessed by letter."""
    e = _n(element)
    return f"alt+{e[0]}" if menubar and e and e[0].isalnum() and e in MENU_NAMES else ""


# ------------------------------------------------------------------------------------------------------------------------------ signatures
def sig(*parts) -> str:
    """A short stable fingerprint of what the screen showed (titles, text lengths, hashes): used to see whether an action CHANGED anything, and to see a loop."""
    import hashlib
    return hashlib.sha256("\x1f".join(str(p) for p in parts).encode("utf-8", "replace")).hexdigest()[:16]
