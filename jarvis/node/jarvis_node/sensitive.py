"""One check, on the node, for "is this a password manager, wallet, bank, broker, payment or crypto app, window or site"
(2026-10-18). jobs.py (window snapshot and UI actions), diag.py, repair.py and pcopen.py all use it.

The node had three copies of one regex that matched "banking" and "netbank" but not "bank" alone or any brand, so a window
titled "ICICI Bank - Chrome", or a Zerodha, PhonePe or Coinbase window, was snapshotted, typed into or read. The list is
the kernel's (policy.sensitive_target: jarvis/config/sensitive_sites.json on top of a built-in floor). FLOOR below is the
node's own floor, used as well and on its own if the kernel package cannot be imported, so the node keeps refusing.
"""
import os
import re
import sys
import unicodedata
import urllib.parse

FLOOR = re.compile(
    r"(?i)keepass|1password|bitwarden|lastpass|dashlane|enpass|wallet|metamask|exodus|ledger|banking|netbank|paypal"
    r"|\bbank|bank\b|\bupi\b|broker|zerodha|groww|upstox|paytm|phonepe|google\s*pay|\bgpay\b|coinbase|binance|robinhood"
    r"|\bicici|\bhdfc|\bsbi\b|\byono\b|\bkotak"
    r"|(?<![\w-])(?:[\w-]+\.)*chase\.com(?![\w-])(?!\.\w)|jpmorgan|\bhsbc")       # review M7: banks the floor lacked ("chase" alone is a common word: its site only; notchase.com is not it)


def _fold_fallback(s) -> str:
    """The kernel's policy.fold_text after policy._unquote, kept here so a node without the kernel package folds the same way
    (test_node_financial_windows pins that the two agree): %-decoding, NFKC,
    every Unicode dot as ".", no invisible or format characters, lower case. Dots the browser reads as "." ("chase。com")
    and zero-width or soft-hyphen characters inside a name must not hide a floor word."""
    s = str(s)
    for _ in range(3):
        u = urllib.parse.unquote(s)
        if u == s:
            break
        s = u
    dots = {0x3002: ".", 0xFF0E: ".", 0xFF61: ".", 0x2024: ".", 0xFE52: "."}
    odd = ((0x034F, 0x034F), (0x17B4, 0x17B5), (0x180B, 0x180F), (0xFE00, 0xFE0F), (0xE0100, 0xE01EF))
    s = unicodedata.normalize("NFKC", s).translate(dots)
    s = "".join(c for c in s if unicodedata.category(c) != "Cf" and not any(a <= ord(c) <= b for a, b in odd))
    return unicodedata.normalize("NFKC", s).translate(dots).lower()

try:
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "kernel"))
    from jarvis_kernel.policy import sensitive_target as _kernel_check   # noqa: E402
except Exception:                       # a node without the kernel package: the floor alone still refuses
    _kernel_check = None
try:
    from jarvis_kernel.policy import ai_chat_host as _kernel_ai_chat     # noqa: E402
except Exception:
    _kernel_ai_chat = None
AI_CHAT_FLOOR = ("chatgpt.com", "chat.openai.com", "gemini.google.com", "claude.ai")      # = policy.AI_CHAT_DOMAINS


def sensitive_target(texts) -> str:
    """Why these strings (an app or process name, a window title, a URL) point at a sensitive app or site ("" when not).
    The kernel's check as given, again with dots and underscores as spaces (a process "PhonePe.exe" is not a web host),
    and the node's floor."""
    t = [str(x) for x in (texts if isinstance(texts, (list, tuple)) else [texts]) if x not in (None, "")]
    if not t:
        return ""
    if _kernel_check is not None:
        why = _kernel_check(t) or _kernel_check([re.sub(r"[._]+", " ", x) for x in t])
        if why:
            return why
    m = FLOOR.search(" ".join(t)) or FLOOR.search(" ".join(_fold_fallback(x) for x in t))     # as written, and as a browser reads it
    return f"'{m.group(0)}' names a password manager, wallet, bank, broker or payment app" if m else ""


def ai_chat_site(url: str) -> str:
    """The AI chat site (chatgpt.com, gemini.google.com, ...) a page address is on, "" when none (plan P9 step 6: JARVIS may
    open one for the owner but never reads, clicks, types or presses keys there). The kernel's reading of the host when it is
    importable, and the node's own floor as well."""
    u = _fold_fallback(url).strip()
    host = (urllib.parse.urlsplit(u if "//" in u else "//" + u).hostname or "").rstrip(".")
    if not host:
        return ""
    if _kernel_ai_chat is not None:
        site = _kernel_ai_chat(host)
        if site:
            return site
    labels = host.split(".")
    return next((".".join(labels[i:]) for i in range(len(labels)) if ".".join(labels[i:]) in AI_CHAT_FLOOR), "")


def why(*texts) -> str:
    return sensitive_target(list(texts))


def _windows(lister=None) -> list:
    if lister is None:
        try:
            from . import windows
        except Exception:
            return []
        if not windows.ON_WINDOWS:
            return []
        lister = windows.list_windows
    try:
        return list(lister() or [])
    except Exception:
        return []


def windows_of(proc: str, lister=None) -> list:
    """The windows a "process" target can mean (winbackend's PICK): those of a process with that name (with or without
    .exe), and frame windows titled with it (a modern app's window belongs to ApplicationFrameHost)."""
    p = str(proc or "").strip().lower()
    if not p:
        return []
    base = p[:-4] if p.endswith(".exe") else p
    out = []
    for w in _windows(lister):
        name = str(w.get("process") or "").strip().lower()
        title = str(w.get("title") or "").strip().lower()
        if (name[:-4] if name.endswith(".exe") else name) == base or title == p or title.startswith(p + " "):
            out.append(w)
    return out


def sensitive_window(proc: str, lister=None) -> str:
    """Why acting on the window(s) of `proc` touches a sensitive app ("" when not): its name, or the title of any of its
    windows (a browser showing a bank)."""
    w = why(proc)
    if w:
        return w
    for win in windows_of(proc, lister):
        w = why(win.get("title"), win.get("process"))
        if w:
            return "one of its open windows is a password manager, wallet, bank, broker or payment app"   # never the title
    return ""
