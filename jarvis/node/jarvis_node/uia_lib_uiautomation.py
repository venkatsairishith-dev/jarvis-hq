"""The UI Automation backend over the `uiautomation` library (D68): the ONE file that knows the library. To change to pywinauto (or anything else) write another
class with the same methods as uia_worker.Backend and name it in uia_worker.load_backend; no other file mentions a library.

Library: uiautomation 2.0.29 (yinkaisheng, Apache-2.0, depends on comtypes only; pinned with hashes in tools/requirements/uia-venv.txt). It is used for the COM
bootstrap, the pattern wrappers (Invoke, Value, Text) and the clipboard; searches and walks go straight to the UI Automation COM interface the library opens
(`IUIAutomation`), exactly as the PowerShell scripts did through .NET, so a search means the same thing in both paths. Three things the library does that
matter here: its patterns SLEEP half a second after Invoke/SetValue unless told `waitTime=0` (always passed below), its own tree walker is the RAW view
(the scripts walked the CONTROL view; `ControlViewWalker` is used below), and it prints warnings to stdout (the worker moved stdout away from the protocol).

Windows only; written without a Windows machine at hand: every call into Windows is in this file and listed in docs/03_DECISIONS.md D68 as "laptop verifies".
Win32 is called through ctypes with declared argument types (64-bit handles), not through the library's own global ctypes settings.
"""
from __future__ import annotations

import os

from uia_worker import Backend, Elem            # the worker's folder is on sys.path (uia_worker.load_backend)

TREE_CHILDREN, TREE_DESCENDANTS = 2, 4
WM_GETTEXT, BM_CLICK = 0x000D, 0x00F5
GW_OWNER, GWL_EXSTYLE, WS_EX_TOOLWINDOW = 4, -20, 0x80
KEYEVENTF_KEYUP, KEYEVENTF_UNICODE = 0x0002, 0x0004
VK_RETURN, VK_TAB, VK_CONTROL, VK_MENU, VK_V = 0x0D, 0x09, 0x11, 0x12, 0x56
CF_UNICODETEXT = 13
TEXT_CLASSES = ("Edit", "RichEdit20W", "RICHEDIT50W")


def unicode_events(text: str) -> list[tuple[int, int, int]]:
    """Text -> (virtual key, scan code, flags) key events for SendInput, nothing parsed as key syntax: a character is sent as itself (KEYEVENTF_UNICODE,
    a character above U+FFFF as its two UTF-16 halves), a line break as Enter, a tab as Tab. Pure, so it is tested without Windows."""
    ev: list[tuple[int, int, int]] = []
    i, text = 0, text.replace("\r\n", "\n").replace("\r", "\n")
    while i < len(text):
        ch = text[i]
        i += 1
        if ch == "\n":
            ev += [(VK_RETURN, 0, 0), (VK_RETURN, 0, KEYEVENTF_KEYUP)]
        elif ch == "\t":
            ev += [(VK_TAB, 0, 0), (VK_TAB, 0, KEYEVENTF_KEYUP)]
        else:
            data = ch.encode("utf-16-le")
            for k in range(0, len(data), 2):
                unit = int.from_bytes(data[k:k + 2], "little")
                ev += [(0, unit, KEYEVENTF_UNICODE), (0, unit, KEYEVENTF_UNICODE | KEYEVENTF_KEYUP)]
    return ev


class UiautomationBackend(Backend):
    name = "uiautomation"

    def __init__(self):
        import comtypes
        import ctypes
        import uiautomation as auto
        from ctypes import wintypes
        self.auto, self.comtypes, self.ct, self.wt = auto, comtypes, ctypes, wintypes
        self.com_error = comtypes.COMError
        self.iua = auto._AutomationClient.instance().IUIAutomation          # the library's one private door: its COM connection (a public wrapper for FindAll does not exist)
        self.true = self.iua.CreateTrueCondition()
        self.walker = self.iua.ControlViewWalker
        self._win32()

    # ---------------------------------------------------------------- Win32 (ctypes, declared types)
    def _win32(self):
        ct, wt = self.ct, self.wt
        u, k = ct.WinDLL("user32", use_last_error=True), ct.WinDLL("kernel32", use_last_error=True)
        H, P = ct.c_void_p, ct.c_void_p
        self.u, self.k = u, k
        self.WNDENUMPROC = ct.WINFUNCTYPE(wt.BOOL, H, wt.LPARAM)
        for fn, res, args in (
                (u.EnumWindows, wt.BOOL, [self.WNDENUMPROC, wt.LPARAM]), (u.GetWindowThreadProcessId, wt.DWORD, [H, ct.POINTER(wt.DWORD)]),
                (u.IsWindowVisible, wt.BOOL, [H]), (u.GetWindowTextLengthW, ct.c_int, [H]), (u.GetWindow, H, [H, wt.UINT]),
                (u.GetWindowLongW, ct.c_long, [H, ct.c_int]), (u.GetForegroundWindow, H, []), (u.SetForegroundWindow, wt.BOOL, [H]),
                (u.BringWindowToTop, wt.BOOL, [H]), (u.AttachThreadInput, wt.BOOL, [wt.DWORD, wt.DWORD, wt.BOOL]), (u.ShowWindow, wt.BOOL, [H, ct.c_int]),
                (u.IsIconic, wt.BOOL, [H]), (u.IsZoomed, wt.BOOL, [H]), (u.PostMessageW, wt.BOOL, [H, wt.UINT, wt.WPARAM, wt.LPARAM]),
                (u.SendMessageTimeoutW, ct.c_ssize_t, [H, wt.UINT, wt.WPARAM, P, wt.UINT, wt.UINT, ct.POINTER(ct.c_size_t)]),
                (u.keybd_event, None, [wt.BYTE, wt.BYTE, wt.DWORD, ct.c_size_t]), (u.IsClipboardFormatAvailable, wt.BOOL, [wt.UINT]),
                (k.OpenProcess, H, [wt.DWORD, wt.BOOL, wt.DWORD]), (k.CloseHandle, wt.BOOL, [H]), (k.GetCurrentThreadId, wt.DWORD, []),
                (k.QueryFullProcessImageNameW, wt.BOOL, [H, wt.DWORD, wt.LPWSTR, ct.POINTER(wt.DWORD)]),
                (k.GetProcessTimes, wt.BOOL, [H, ct.POINTER(wt.FILETIME), ct.POINTER(wt.FILETIME), ct.POINTER(wt.FILETIME), ct.POINTER(wt.FILETIME)]),
                (k.CreateToolhelp32Snapshot, H, [wt.DWORD, wt.DWORD]), (k.Process32FirstW, wt.BOOL, [H, P]), (k.Process32NextW, wt.BOOL, [H, P])):
            fn.restype, fn.argtypes = res, args

        class PROCESSENTRY32W(ct.Structure):
            _fields_ = [("dwSize", wt.DWORD), ("cntUsage", wt.DWORD), ("th32ProcessID", wt.DWORD), ("th32DefaultHeapID", ct.c_size_t),
                        ("th32ModuleID", wt.DWORD), ("cntThreads", wt.DWORD), ("th32ParentProcessID", wt.DWORD), ("pcPriClassBase", ct.c_long),
                        ("dwFlags", wt.DWORD), ("szExeFile", ct.c_wchar * 260)]

        class KEYBDINPUT(ct.Structure):
            _fields_ = [("wVk", wt.WORD), ("wScan", wt.WORD), ("dwFlags", wt.DWORD), ("time", wt.DWORD), ("dwExtraInfo", ct.c_size_t)]

        class MOUSEINPUT(ct.Structure):                       # only so the union has the real size (INPUT is 40 bytes on 64-bit Windows)
            _fields_ = [("dx", wt.LONG), ("dy", wt.LONG), ("mouseData", wt.DWORD), ("dwFlags", wt.DWORD), ("time", wt.DWORD), ("dwExtraInfo", ct.c_size_t)]

        class INPUT(ct.Structure):
            class _U(ct.Union):
                _fields_ = [("ki", KEYBDINPUT), ("mi", MOUSEINPUT)]
            _anonymous_ = ("u",)
            _fields_ = [("type", wt.DWORD), ("u", _U)]
        self.PE, self.INPUT, self.KEYBDINPUT = PROCESSENTRY32W, INPUT, KEYBDINPUT
        u.SendInput.restype, u.SendInput.argtypes = wt.UINT, [wt.UINT, ct.POINTER(INPUT), ct.c_int]

    def _image(self, pid: int) -> str:
        h = self.k.OpenProcess(0x1000, False, pid)           # PROCESS_QUERY_LIMITED_INFORMATION
        if not h:
            return ""
        try:
            buf, n = self.ct.create_unicode_buffer(520), self.wt.DWORD(520)
            return buf.value if self.k.QueryFullProcessImageNameW(h, 0, buf, self.ct.byref(n)) else ""
        finally:
            self.k.CloseHandle(h)

    def _start_time(self, pid: int) -> int:
        h = self.k.OpenProcess(0x1000, False, pid)
        if not h:
            return 0
        try:
            c, e, kt, ut = (self.wt.FILETIME() for _ in range(4))
            if self.k.GetProcessTimes(h, *(self.ct.byref(x) for x in (c, e, kt, ut))):
                return (c.dwHighDateTime << 32) | c.dwLowDateTime
            return 0
        finally:
            self.k.CloseHandle(h)

    def process_name(self, pid: int) -> str:
        return os.path.splitext(os.path.basename(self._image(pid)))[0]

    def pids_named(self, name: str) -> list[int]:
        out, want = [], name.lower()
        snap = self.k.CreateToolhelp32Snapshot(0x2, 0)           # TH32CS_SNAPPROCESS
        if not snap or snap == self.ct.c_void_p(-1).value:
            return out
        try:
            pe = self.PE()
            pe.dwSize = self.ct.sizeof(self.PE)
            ok = self.k.Process32FirstW(snap, self.ct.byref(pe))
            while ok:
                if os.path.splitext(pe.szExeFile)[0].lower() == want:
                    out.append(int(pe.th32ProcessID))
                ok = self.k.Process32NextW(snap, self.ct.byref(pe))
        finally:
            self.k.CloseHandle(snap)
        return out

    def list_windows(self, proc: str):
        """windowpick.LIST: visible, titled, unowned, non-tool top-level windows of the processes called `proc` ("" = of every process)."""
        want, found, names, starts = proc.lower(), [], {}, {}
        pid = self.wt.DWORD()

        def cb(hwnd, _lp):
            if not self.u.IsWindowVisible(hwnd) or self.u.GetWindowTextLengthW(hwnd) <= 0 or self.u.GetWindow(hwnd, GW_OWNER) \
                    or (self.u.GetWindowLongW(hwnd, GWL_EXSTYLE) & WS_EX_TOOLWINDOW):
                return True
            self.u.GetWindowThreadProcessId(hwnd, self.ct.byref(pid))
            p = int(pid.value)
            if p not in names:
                names[p] = self.process_name(p).lower()
                starts[p] = self._start_time(p)
            if not want or names[p] == want:
                found.append({"hwnd": int(hwnd), "pid": p, "start": starts[p], "name": names[p]})
            return True
        self.u.EnumWindows(self.WNDENUMPROC(cb), 0)
        return found, int(self.u.GetForegroundWindow() or 0)

    def foreground_hwnd(self) -> int:
        return int(self.u.GetForegroundWindow() or 0)

    def pid_of(self, hwnd: int) -> int:
        pid = self.wt.DWORD()
        self.u.GetWindowThreadProcessId(hwnd, self.ct.byref(pid))
        return int(pid.value)

    def foreground_pid(self) -> int:
        h = self.u.GetForegroundWindow()
        return self.pid_of(h) if h else 0

    def show_window(self, hwnd, cmd):
        self.u.ShowWindow(hwnd, cmd)

    def is_iconic(self, hwnd):
        return bool(self.u.IsIconic(hwnd))

    def is_zoomed(self, hwnd):
        return bool(self.u.IsZoomed(hwnd))

    def tap_alt(self):
        self.u.keybd_event(VK_MENU, 0, 0, 0)
        self.u.keybd_event(VK_MENU, 0, KEYEVENTF_KEYUP, 0)

    def bring_to_front(self, hwnd, attach):
        if attach:                                              # Windows' foreground lock: join the foreground thread's input for the hand-over
            fg = self.u.GetForegroundWindow()
            ft = self.u.GetWindowThreadProcessId(fg, None) if fg else 0
            me = self.k.GetCurrentThreadId()
            self.u.AttachThreadInput(me, ft, True)
            try:
                self.u.BringWindowToTop(hwnd)
                self.u.SetForegroundWindow(hwnd)
            finally:
                self.u.AttachThreadInput(me, ft, False)
        else:
            self.u.SetForegroundWindow(hwnd)

    def post_click(self, hwnd):
        self.u.PostMessageW(hwnd, BM_CLICK, 0, 0)               # posted, not sent: a SendMessage would wait for a prompt the click opens

    def wm_gettext(self, hwnd) -> str:
        buf, res = self.ct.create_unicode_buffer(65536), self.ct.c_size_t(0)
        ok = self.u.SendMessageTimeoutW(hwnd, WM_GETTEXT, 65536, self.ct.cast(buf, self.ct.c_void_p), 2, 2000, self.ct.byref(res))   # SMTO_ABORTIFHUNG, 2 s
        return buf.value if ok else ""

    def _send_events(self, events):
        arr = (self.INPUT * len(events))()
        for i, (vk, scan, flags) in enumerate(events):
            arr[i].type = 1
            arr[i].ki = self.KEYBDINPUT(vk, scan, flags, 0, 0)
        if self.u.SendInput(len(events), arr, self.ct.sizeof(self.INPUT)) != len(events):
            raise OSError("SendInput did not send every key")

    def send_text(self, text):
        ev = unicode_events(text)
        for i in range(0, len(ev), 200):
            self._send_events(ev[i:i + 200])

    def send_ctrl_v(self):
        self._send_events([(VK_CONTROL, 0, 0), (VK_V, 0, 0), (VK_V, 0, KEYEVENTF_KEYUP), (VK_CONTROL, 0, KEYEVENTF_KEYUP)])

    def clipboard_get(self):
        if not self.u.IsClipboardFormatAvailable(CF_UNICODETEXT):
            return None                                         # an image or files on the clipboard: nothing text to put back
        return self.auto.GetClipboardText()

    def clipboard_set(self, text):
        return bool(self.auto.SetClipboardText(text))

    # ---------------------------------------------------------------- UI Automation
    def _wrap(self, raw):
        if not raw:
            return None
        try:
            kind = self.auto.ControlTypeNames.get(raw.CurrentControlType, "")
            return Elem(raw=raw, name=raw.CurrentName, auto_id=raw.CurrentAutomationId, cls=raw.CurrentClassName,
                        ctype=kind[:-len("Control")] if kind.endswith("Control") else kind, enabled=raw.CurrentIsEnabled,
                        offscreen=raw.CurrentIsOffscreen, password=raw.CurrentIsPassword, hwnd=raw.CurrentNativeWindowHandle, pid=raw.CurrentProcessId)
        except self.com_error:
            return None                                         # the element went away while we looked

    def _all(self, raw, scope, cond, limit):
        out = []
        try:
            arr = raw.FindAll(scope, cond)
            for i in range(min(arr.Length, limit)):
                e = self._wrap(arr.GetElement(i))
                if e is not None:
                    out.append(e)
        except self.com_error:
            pass
        return out

    def top_level(self):
        return self._all(self.iua.GetRootElement(), TREE_CHILDREN, self.true, 5000)

    def root_for(self, hwnd):
        try:
            return self._wrap(self.iua.ElementFromHandle(hwnd))
        except self.com_error:
            return None

    def children(self, el):
        out = []
        try:
            c = self.walker.GetFirstChildElement(el.raw)
            while c:
                w = self._wrap(c)
                if w is not None:
                    out.append(w)
                c = self.walker.GetNextSiblingElement(c)
        except self.com_error:
            pass
        return out

    def descendants(self, el, limit):
        return self._all(el.raw, TREE_DESCENDANTS, self.true, limit)

    def find_first(self, root, ident):
        P = self.auto.PropertyId
        cond = self.iua.CreateOrCondition(self.iua.CreatePropertyCondition(P.AutomationIdProperty, ident),
                                          self.iua.CreatePropertyCondition(P.NameProperty, ident))
        try:
            return self._wrap(root.raw.FindFirst(TREE_DESCENDANTS, cond))
        except self.com_error:
            return None

    def find_text_fields(self, root):
        P, T = self.auto.PropertyId, self.auto.ControlType
        conds = [self.iua.CreatePropertyCondition(P.ControlTypeProperty, T.EditControl), self.iua.CreatePropertyCondition(P.ControlTypeProperty, T.DocumentControl)]
        conds += [self.iua.CreatePropertyCondition(P.ClassNameProperty, c) for c in TEXT_CLASSES]
        cond = conds[0]
        for c in conds[1:]:
            cond = self.iua.CreateOrCondition(cond, c)
        return self._all(root.raw, TREE_DESCENDANTS, cond, 200)

    def _pattern(self, el, pattern_id):
        return self.auto.Control.CreateControlFromElement(el.raw).GetPattern(pattern_id)

    def invoke(self, el):
        pat = self._pattern(el, self.auto.PatternId.InvokePattern)
        if not pat:
            return False
        pat.Invoke(waitTime=0)                                  # the library sleeps 0.5 s after Invoke unless told not to
        return True

    def has_value(self, el):
        return self._pattern(el, self.auto.PatternId.ValuePattern) is not None

    def set_value(self, el, text):
        self._pattern(el, self.auto.PatternId.ValuePattern).SetValue(text, waitTime=0)

    def get_value(self, el):
        pat = self._pattern(el, self.auto.PatternId.ValuePattern)
        return None if not pat else pat.Value

    def get_text(self, el, limit):
        pat = self._pattern(el, self.auto.PatternId.TextPattern)
        return None if not pat else pat.DocumentRange.GetText(limit)

    def focused_element(self):
        try:
            return self._wrap(self.iua.GetFocusedElement())
        except self.com_error:
            return None

    def set_focus(self, el):
        try:
            el.raw.SetFocus()
            return True
        except self.com_error:
            return False


def make():
    """(backend, "uiautomation <version>") for the worker. Raises ImportError when the library is not installed."""
    import importlib.metadata as md
    b = UiautomationBackend()
    try:
        ver = md.version("uiautomation")
    except md.PackageNotFoundError:
        ver = "?"
    return b, "uiautomation " + ver
