"""The persistent UI Automation worker (D68). ONE long-lived child process of the node that answers the node's UI jobs, instead of a new
PowerShell (about 235 to 343 ms to start, measured and noted in tools/registry.json) for every read or action.

This file is the protocol and the operations. It is standard library only and runs in two places:
  * as the child script (`python uia_worker.py`, in the worker's own venv on D:), where a Windows backend does the real UI Automation;
  * imported by the tests, with a fake backend, so that the protocol, the lifecycle and every operation's logic run on any OS.

What an operation DOES is a line-for-line port of the PowerShell script it replaces (winbackend.py FIND / CLICK / TYPE / READBACK / FOCUS /
WINDOW_STATE / TYPE_FOCUSED, ui.py PS, diag.py WINDOW_TEXT, windowpick.py LIST): same selectors, same refusals (a password field is never
typed into), same fixed reason codes. The kernel's policy, the effect classes, the PIN rules and "screen text is untrusted data" are NOT here:
this process only does what the node's job code, after the kernel said yes, tells it to do. It has no network (no socket, no HTTP library is
imported), starts no shell, and answers errors with a code from a closed list, never with text from the screen.

Protocol: one JSON object per line, UTF-8, both ways.
  worker -> node, once at start : {"ready": true, "proto": 1, "lib": "uiautomation 2.0.29", "pid": 123}  or  {"ready": false, "error": "library missing"}
  node -> worker                : {"id": 7, "op": "click", "args": {...}}
  worker -> node                : {"id": 7, "ok": true, "result": {...}}  or  {"id": 7, "ok": false, "code": "no element"}
A request line over MAX_REQUEST bytes is refused; an answer over MAX_RESPONSE bytes is replaced by {"ok": false, "code": "oversize"}.
End of input (the node closed the pipe, or died) ends the worker, even when it is stuck inside a hung application.
"""
from __future__ import annotations

import json
import os
import re
import sys
import threading
import time

PROTO = 1
MAX_REQUEST = 256 * 1024               # bytes of one request line
MAX_RESPONSE = 1024 * 1024             # bytes of one answer line (a 60-element tree is about 10 KB)
MAX_WALK = 4000                        # elements one window_text walk looks at, so a huge app cannot take minutes
# The fixed reason codes that may leave this process: exactly the ones winbackend.REASONS knows. Anything else is "other".
CODES = ("no window", "no element", "password field", "target is not the foreground window", "does not support", "pattern",
         "text not in field", "clipboard paste failed", "oversize", "bad request", "unsupported")
BUTTON_CLASSES = ("Button", "CCPushButton")
TEXT_CLASSES = ("Static", "Edit", "RichEdit20W")
EDITORS = ("Code", "Code - Insiders", "VSCodium", "Cursor")      # Monaco's hidden textarea holds a fragment, not the document (seen live 2026-10-26)
SAFE_PROC = re.compile(r"^[A-Za-z0-9_.\- ]{0,80}$")


class OpError(Exception):
    """A refusal or failure with one of the fixed codes (never screen text)."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code if code in CODES else "other"


class Elem:
    """A UI element as plain data. `raw` is whatever the backend needs to act on it again (a COM pointer in the real one, a dict in the fake)."""
    __slots__ = ("raw", "name", "auto_id", "cls", "ctype", "enabled", "offscreen", "password", "hwnd", "pid")

    def __init__(self, raw=None, name="", auto_id="", cls="", ctype="Pane", enabled=True, offscreen=False, password=False, hwnd=0, pid=0):
        self.raw, self.name, self.auto_id, self.cls, self.ctype = raw, name or "", auto_id or "", cls or "", ctype or ""
        self.enabled, self.offscreen, self.password, self.hwnd, self.pid = bool(enabled), bool(offscreen), bool(password), int(hwnd or 0), int(pid or 0)


class Backend:
    """What the operations need from a UI Automation library. A library is swapped by writing another subclass of this (the real one is
    uia_lib_uiautomation.py, about 300 lines); nothing else in the node knows which library is behind it."""
    name = "abstract"

    # --- windows and processes (Win32)
    def list_windows(self, proc: str) -> tuple[list[dict], int]:
        """([{hwnd, pid, start, name}], foreground hwnd): visible, titled, unowned, non-tool top-level windows of the processes called `proc`
        ("" = every process that has such a window). `start` is the process's creation time (FILETIME), `name` its lower-case image name."""
        raise NotImplementedError

    def pids_named(self, name: str) -> list[int]: raise NotImplementedError
    def process_name(self, pid: int) -> str: raise NotImplementedError
    def foreground_hwnd(self) -> int: raise NotImplementedError
    def foreground_pid(self) -> int: raise NotImplementedError
    def pid_of(self, hwnd: int) -> int: raise NotImplementedError

    # --- the UI Automation tree
    def top_level(self) -> list[Elem]: raise NotImplementedError                          # the desktop's children
    def root_for(self, hwnd: int) -> Elem | None: raise NotImplementedError
    def children(self, el: Elem) -> list[Elem]: raise NotImplementedError                 # control view
    def descendants(self, el: Elem, limit: int) -> list[Elem]: raise NotImplementedError  # every descendant, document order, at most `limit`
    def find_first(self, root: Elem, ident: str) -> Elem | None: raise NotImplementedError       # AutomationId == ident or Name == ident
    def find_text_fields(self, root: Elem) -> list[Elem]: raise NotImplementedError              # Edit or Document by type or class, document order

    # --- patterns and focus
    def invoke(self, el: Elem) -> bool: raise NotImplementedError                          # False when the element has no Invoke pattern
    def has_value(self, el: Elem) -> bool: raise NotImplementedError
    def set_value(self, el: Elem, text: str) -> None: raise NotImplementedError
    def get_value(self, el: Elem) -> str | None: raise NotImplementedError                 # ValuePattern.Value
    def get_text(self, el: Elem, limit: int) -> str | None: raise NotImplementedError      # TextPattern.DocumentRange.GetText(limit)
    def focused_element(self) -> Elem | None: raise NotImplementedError
    def set_focus(self, el: Elem) -> bool: raise NotImplementedError

    # --- input and window state (Win32)
    def post_click(self, hwnd: int) -> None: raise NotImplementedError                     # BM_CLICK posted to a classic button
    def wm_gettext(self, hwnd: int) -> str: raise NotImplementedError
    def show_window(self, hwnd: int, cmd: int) -> None: raise NotImplementedError
    def is_iconic(self, hwnd: int) -> bool: raise NotImplementedError
    def is_zoomed(self, hwnd: int) -> bool: raise NotImplementedError
    def tap_alt(self) -> None: raise NotImplementedError                                   # lets a background process take the foreground
    def bring_to_front(self, hwnd: int, attach: bool) -> None: raise NotImplementedError   # SetForegroundWindow, with the input-thread attach on a retry
    def send_text(self, text: str) -> None: raise NotImplementedError                      # literal text; "\n" is Enter, "\t" is Tab; nothing is parsed as key syntax
    def send_ctrl_v(self) -> None: raise NotImplementedError
    def clipboard_get(self) -> str | None: raise NotImplementedError                       # None when the clipboard holds no text
    def clipboard_set(self, text: str) -> bool: raise NotImplementedError


def _norm(t) -> str:
    return re.sub(r"\s+", " ", str(t or "")).strip()


class Ops:
    """The operations. `sleep` is injectable so a test does not wait for the real beats."""

    def __init__(self, backend: Backend, sleep=time.sleep, env=None):
        self.b, self.sleep, self.env = backend, sleep, (os.environ if env is None else env)
        self.table = {"ping": self.ping, "windows": self.windows, "find": self.find, "click": self.click, "focus": self.focus, "type": self.type,
                      "window_state": self.window_state, "paste": self.paste, "snapshot": self.snapshot, "window_text": self.window_text}

    # ---- which window
    def _window(self, a: dict) -> tuple[int, int]:
        """The window the node chose (windowpick.py sends hwnd and pid); else the PICK rule of winbackend.py: a window of the process, or, for a
        modern (UWP) app, the ApplicationFrameHost frame titled with the app's name (one only)."""
        if a.get("hwnd"):
            hwnd = int(a["hwnd"])
            return hwnd, int(a.get("pid") or 0) or self.b.pid_of(hwnd)
        proc = str(a.get("proc", ""))
        if not proc or not SAFE_PROC.match(proc):
            raise OpError("bad request")
        wins, _fg = self.b.list_windows(proc)
        if wins:
            return int(wins[0]["hwnd"]), int(wins[0]["pid"])
        hit = [f for f in self.b.top_level() if f.cls == "ApplicationFrameWindow"
               and (f.name == proc or f.name.lower().startswith(proc.lower() + " "))]
        if len(hit) == 1:
            return hit[0].hwnd, hit[0].pid
        raise OpError("no window")                       # none, or more than one match (the old script said the same)

    def _root(self, hwnd: int) -> Elem:
        root = self.b.root_for(hwnd)
        if root is None:
            raise OpError("no window")
        return root

    def _find(self, root: Elem, ident: str) -> Elem:
        ident = str(ident or "")
        if not ident or len(ident) > 120 or "\n" in ident:
            raise OpError("bad request")
        if ident == "*":                                  # "the window's first text field": Edit or Document, never a password field
            for el in self.b.find_text_fields(root):
                if not el.password:
                    return el
            raise OpError("no element")
        el = self.b.find_first(root, ident)
        if el is None:
            raise OpError("no element")
        return el

    # ---- operations
    def ping(self, a):
        return {"pong": True}

    def windows(self, a):
        proc = str(a.get("proc", ""))
        if not SAFE_PROC.match(proc):
            raise OpError("bad request")
        wins, fg = self.b.list_windows(proc)
        return {"fg": int(fg or 0), "windows": [{"hwnd": int(w["hwnd"]), "pid": int(w["pid"]), "start": int(w.get("start") or 0),
                                                 "name": str(w.get("name", ""))} for w in wins]}

    def find(self, a):
        hwnd, _pid = self._window(a)
        el = self._find(self._root(hwnd), a.get("elem"))
        return {"found": True, "type": el.ctype, "enabled": el.enabled, "password": el.password}

    def click(self, a):
        hwnd, _pid = self._window(a)
        el = self._find(self._root(hwnd), a.get("elem"))
        if self.b.invoke(el):
            return {}
        if el.cls in BUTTON_CLASSES and el.hwnd != 0:     # a classic Win32 button (MessageBox OK) without UIA's Invoke: its own click message, still by element
            self.b.post_click(el.hwnd)
            return {}
        raise OpError("does not support")

    def _focus(self, hwnd: int, pid: int) -> None:
        """winbackend.FOCUS: already in front and not minimised = do nothing (an Alt tap would close an open menu); else up to three tries."""
        b = self.b
        fpid = b.foreground_pid()
        for attempt in range(3):
            if not (fpid != pid or b.is_iconic(hwnd)):
                break
            if b.is_iconic(hwnd):
                b.show_window(hwnd, 9)                    # SW_RESTORE
            b.tap_alt()
            b.bring_to_front(hwnd, attach=attempt > 0)    # Windows' foreground lock: from the second try join the foreground thread's input
            self.sleep(0.3)
            fpid = b.foreground_pid()
        if b.foreground_pid() != pid:
            raise OpError("target is not the foreground window")

    def focus(self, a):
        hwnd, pid = self._window(a)
        self._focus(hwnd, pid)
        return {"foreground": True}

    def _type_keys(self, text: str) -> None:
        """D65 human feel: pieces of 8 characters with a short beat up to JARVIS_UI_PACE_CAP characters (default 240), one piece beyond it."""
        cap = int(self.env.get("JARVIS_UI_PACE_CAP") or 240)
        pace = int(self.env.get("JARVIS_UI_PACE_MS") or 55)
        if pace > 0 and len(text) <= cap:
            for i in range(0, len(text), 8):
                self.b.send_text(text[i:i + 8])
                self.sleep((pace - 15 + ((i // 8) % 3) * 15) / 1000.0)
        else:
            self.b.send_text(text)

    def type(self, a):
        """FOCUS, then TYPE (the value pattern; else the keyboard, only if the target really owns the foreground), then READBACK (the text must be in the field)."""
        text = str(a.get("text", ""))
        if len(text) > 2000:
            raise OpError("bad request")
        hwnd, pid = self._window(a)
        self._focus(hwnd, pid)
        b = self.b
        root = self._root(hwnd)
        el = self._find(root, a.get("elem"))
        if b.has_value(el) and not el.password:
            b.set_value(el, text)                         # best rung: structured value
        else:
            if el.password:
                raise OpError("password field")
            if not b.set_focus(el):
                b.bring_to_front(hwnd, attach=False)
            self.sleep(0.4)
            if b.foreground_pid() != pid:
                raise OpError("target is not the foreground window")
            self._type_keys(text)
        self.sleep(0.2)
        el = self._find(self._root(hwnd), a.get("elem"))   # READBACK looks the element up again, as the script did
        have = b.get_value(el) if b.has_value(el) else None
        if not have and el.hwnd != 0:
            have = b.wm_gettext(el.hwnd)
        if not have or text not in have:
            raise OpError("text not in field")
        return {"read_back": True}

    def window_state(self, a):
        hwnd, _pid = self._window(a)
        show, want = int(a.get("show", 9)), str(a.get("want", ""))
        if want not in ("minimized", "maximized", "normal") or show not in (3, 6, 9):
            raise OpError("bad request")
        self.b.show_window(hwnd, show)
        self.sleep(0.4)
        state = "minimized" if self.b.is_iconic(hwnd) else ("maximized" if self.b.is_zoomed(hwnd) else "normal")
        if state != want:
            raise OpError("pattern")
        return {"state": state}

    def paste(self, a):
        """winbackend.TYPE_FOCUSED: put the text into whatever has the keyboard focus, by pasting it; the clipboard's old text is put back; the focused
        control is read back when UI Automation can. Result {"typed": "read back" | "none"}."""
        text, proc = str(a.get("text", "")), str(a.get("proc", ""))
        if not text or len(text) > 4000:
            raise OpError("bad request")
        hwnd, pid = self._window(a)
        self._focus(hwnd, pid)
        b = self.b
        old = None
        try:
            old = b.clipboard_get()
        except Exception:  # noqa: BLE001 - an unreadable clipboard is "had nothing to put back"
            old = None
        try:
            try:
                if not b.clipboard_set(text):
                    raise OpError("clipboard paste failed")
                self.sleep(0.15)
                if b.foreground_pid() != pid:             # re-checked right before the keys go out
                    raise OpError("target is not the foreground window")
                b.send_ctrl_v()
                self.sleep(0.4)
            except OpError as e:
                if e.code == "target is not the foreground window":
                    raise
                raise OpError("clipboard paste failed") from None
            except Exception:  # noqa: BLE001
                raise OpError("clipboard paste failed") from None
        finally:
            if old is not None:
                try:
                    b.clipboard_set(old)
                except Exception:  # noqa: BLE001
                    pass
        seen = "none"
        try:
            fe = b.focused_element()
            have = None
            if fe is not None:
                have = b.get_text(fe, -1)
                if have is None:
                    have = b.get_value(fe)
            if have is not None:
                tail = _norm(text)[-60:]
                if tail in _norm(have):
                    seen = "read back"
                elif proc not in EDITORS:
                    raise OpError("text not in field")
        except OpError:
            raise
        except Exception:  # noqa: BLE001 - a control that cannot be read is "not observed"
            pass
        return {"typed": seen}

    def snapshot(self, a):
        """ui.py: the accessibility tree of one window (the process's, or the foreground window when proc is "")."""
        proc = str(a.get("proc", ""))
        max_n, max_d, values = max(1, min(int(a.get("max", 60)), 400)), max(1, min(int(a.get("depth", 4)), 12)), bool(a.get("values"))
        b = self.b
        if not proc:
            hwnd = b.foreground_hwnd()
        else:
            try:
                hwnd, _pid = self._window(a)
            except OpError:
                return {"error": "no window"}
        root = b.root_for(hwnd) if hwnd else None
        if root is None:
            return {"error": "no window"}
        out: list[dict] = []

        def walk(el: Elem, depth: int) -> None:
            if len(out) >= max_n or depth > max_d:
                return
            for c in b.children(el):
                if len(out) >= max_n:
                    return
                val = None
                if values and not c.password:
                    try:
                        val = b.get_value(c) if b.has_value(c) else b.get_text(c, 300)
                    except Exception:  # noqa: BLE001
                        val = None
                out.append({"depth": depth, "type": c.ctype, "name": c.name, "id": c.auto_id, "cls": c.cls, "enabled": c.enabled,
                            "offscreen": c.offscreen, "value": val})
                walk(c, depth + 1)
        walk(root, 1)
        return {"window": root.name, "process": b.process_name(root.pid), "class": root.cls, "elements": out, "truncated": len(out) >= max_n}

    def window_text(self, a):
        """diag.py WINDOW_TEXT: every top-level window of the process, dialogs included, with its texts and button names."""
        name = str(a.get("name", ""))
        if not name or not SAFE_PROC.match(name):
            raise OpError("bad request")
        b = self.b
        ids = set(b.pids_named(name))
        top = b.top_level()
        frames = {f.hwnd for f in top if f.cls == "ApplicationFrameWindow" and (f.name == name or f.name.lower().startswith(name.lower() + " "))}
        if not ids and not frames:
            return {"rows": []}
        rows: list[dict] = []
        budget = [MAX_WALK]
        for w in top:
            if w.pid not in ids and w.hwnd not in frames:
                continue
            texts, buttons = [], []
            desc = b.descendants(w, max(budget[0], 0))
            budget[0] -= len(desc)
            for e in desc:
                if e.password or not e.name:
                    continue
                if e.ctype == "Button" or e.cls in BUTTON_CLASSES:
                    buttons.append(e.name)
                elif e.ctype in ("Text", "Edit", "Document") or e.cls in TEXT_CLASSES:
                    texts.append(e.name)
                if len(texts) > 60:
                    break
            rows.append({"title": w.name, "class": w.cls, "dialog": w.cls == "#32770", "texts": texts, "buttons": buttons})
            # an app's own dialogs (Find, Save?, About) sit under its main window in the UIA tree: list each as its own dialog
            for d in desc:
                if d.cls != "#32770":
                    continue
                dt, db = [], []
                for e in b.descendants(d, 400):
                    if e.password or not e.name:
                        continue
                    if e.ctype == "Button" or e.cls in BUTTON_CLASSES:
                        db.append(e.name)
                    elif e.cls in ("Static", "Edit") or e.ctype == "Text":
                        dt.append(e.name)
                rows.append({"title": d.name, "class": "#32770", "dialog": True, "texts": dt, "buttons": db, "owner": w.name})
            if budget[0] <= 0:
                break
        return {"rows": rows}


# ---------------------------------------------------------------------------------------------------------------- the loop
def handle(ops: Ops, line: bytes | str, max_response: int = MAX_RESPONSE) -> bytes:
    """One request line in, one answer line out (always an answer, never an exception)."""
    rid = None
    try:
        if isinstance(line, (bytes, bytearray)) and len(line) > MAX_REQUEST:
            return _dump({"id": None, "ok": False, "code": "oversize"})
        try:
            req = json.loads(line)
        except ValueError:
            return _dump({"id": None, "ok": False, "code": "bad request"})
        rid = req.get("id") if isinstance(req, dict) else None
        if not isinstance(req, dict) or not isinstance(rid, int) or isinstance(rid, bool):
            return _dump({"id": rid if isinstance(rid, int) else None, "ok": False, "code": "bad request"})
        fn = ops.table.get(req.get("op")) if isinstance(req.get("op"), str) else None
        args = req.get("args", {})
        if fn is None or not isinstance(args, dict):
            return _dump({"id": rid, "ok": False, "code": "bad request"})
        out = _dump({"id": rid, "ok": True, "result": fn(args)})
        if len(out) > max_response:
            return _dump({"id": rid, "ok": False, "code": "oversize"})
        return out
    except OpError as e:
        return _dump({"id": rid, "ok": False, "code": e.code})
    except NotImplementedError:                      # this library's adapter lacks the primitive: nothing was done, the node uses its PowerShell script
        return _dump({"id": rid, "ok": False, "code": "unsupported"})
    except Exception as e:  # noqa: BLE001 - only the KIND of failure: its text could hold screen content
        return _dump({"id": rid, "ok": False, "code": "other", "etype": type(e).__name__[:40]})


def _dump(obj) -> bytes:
    return (json.dumps(obj, ensure_ascii=True, separators=(",", ":")) + "\n").encode("ascii")


def serve(ops: Ops, rin, rout, *, lib: str = "", max_response: int = MAX_RESPONSE, hard_exit=None, grace_s: float = 3.0) -> None:
    """Answer requests from `rin` (a binary file) on `rout` until the input ends or a quit arrives. A reader thread owns the input, so that the end of
    input ends the worker even while the main thread is stuck inside a hung application (`hard_exit(code)` is called `grace_s` later)."""
    import queue
    hard_exit = hard_exit or os._exit
    inbox: "queue.Queue[bytes | None]" = queue.Queue()

    def reader():
        while True:
            line = rin.readline(MAX_REQUEST + 1)
            if not line:
                inbox.put(None)
                t = threading.Timer(grace_s, lambda: hard_exit(0))
                t.daemon = True
                t.start()
                return
            if len(line) > MAX_REQUEST and not line.endswith(b"\n"):      # too long: say so once, drop the rest of that line
                while True:
                    rest = rin.readline(MAX_REQUEST)
                    if not rest or rest.endswith(b"\n"):
                        break
                inbox.put(b"\x00OVERSIZE")
                continue
            inbox.put(line)
    threading.Thread(target=reader, daemon=True).start()
    rout.write(_dump({"ready": True, "proto": PROTO, "lib": lib, "pid": os.getpid()}))
    rout.flush()
    while True:
        line = inbox.get()
        if line is None:
            return
        if line == b"\x00OVERSIZE":
            rout.write(_dump({"id": None, "ok": False, "code": "oversize"}))
            rout.flush()
            continue
        line = line.strip()
        if not line:
            continue
        quitting = False
        try:
            quitting = json.loads(line).get("op") == "quit"
        except Exception:  # noqa: BLE001
            pass
        if quitting:
            rid = json.loads(line).get("id")
            rout.write(_dump({"id": rid, "ok": True, "result": {"bye": True}}))
            rout.flush()
            return
        rout.write(handle(ops, line, max_response))
        rout.flush()


def load_backend(name: str) -> tuple[Backend, str]:
    """The adapter for the chosen library (JARVIS_UIA_LIB, default uiautomation). Raises ImportError when the library is not installed."""
    here = os.path.dirname(os.path.abspath(__file__))
    if here not in sys.path:
        sys.path.insert(0, here)
    if name == "uiautomation":
        import uia_lib_uiautomation as mod
    else:
        raise ImportError("unknown library")
    return mod.make()


def protect_stdout():
    """The protocol channel: whatever a library prints (it prints warnings to stdout) must never land in it. The real stdout is kept for us; fd 1 goes to the null device."""
    out = os.fdopen(os.dup(1), "wb", buffering=0)
    try:
        nul = os.open(os.devnull, os.O_WRONLY)
        os.dup2(nul, 1)
        sys.stdout = open(os.devnull, "w", encoding="utf-8")
    except OSError:
        pass
    return out


def main(loader=None, extra_ops=None, grace_s: float = 3.0) -> int:
    """The worker program. `loader() -> (backend, lib name)` and `extra_ops` are the tests' seams (a fake backend, fault injection); the real run uses neither."""
    sys.modules.setdefault("uia_worker", sys.modules[__name__])     # the adapter does `from uia_worker import Backend`: the same module, not a second copy
    out = protect_stdout()
    try:
        backend, lib = (loader or (lambda: load_backend(os.environ.get("JARVIS_UIA_LIB", "uiautomation"))))()
    except Exception as e:  # noqa: BLE001
        why = "library missing" if isinstance(e, ImportError) else "library failed to start"
        out.write(_dump({"ready": False, "error": why, "etype": type(e).__name__[:40]}))
        return 3
    ops = Ops(backend)
    ops.table.update(extra_ops or {})
    serve(ops, sys.stdin.buffer, out, lib=lib, grace_s=grace_s)
    return 0


if __name__ == "__main__":
    sys.exit(main())
