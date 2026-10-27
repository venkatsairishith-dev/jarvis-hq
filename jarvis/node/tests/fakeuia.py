"""A fake Windows desktop behind uia_worker.Backend, for the UI Automation worker's tests (D68). Not a test file.

It has processes, top-level windows and a tree of controls with the patterns real controls have (Invoke, Value, Text), a foreground window, a keyboard that
types into the focused control, and a clipboard. `log` records what was done to it, so a test can say "nothing was typed". The same desktop is driven by the
worker's operations (through uia_worker.Backend) and, in test_uia_parity.py, by FakePowerShell, which plays the OLD scripts on it: the node jobs must return the
same shapes either way.
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "jarvis_node"))

from uia_worker import Backend, Elem  # noqa: E402


def node(name="", auto_id="", cls="", ctype="Pane", hwnd=0, value=None, text=None, invoke=False, password=False, children=(), enabled=True, wm_text=None):
    """One control. value is not None = it has a Value pattern; text = a Text pattern (document); invoke = an Invoke pattern; wm_text = what WM_GETTEXT returns
    (a classic Edit control with no patterns)."""
    return {"name": name, "id": auto_id, "cls": cls, "ctype": ctype, "hwnd": hwnd, "value": value, "text": text, "invoke": invoke, "password": password,
            "children": list(children), "enabled": enabled, "wm_text": wm_text, "invoked": 0}


def default_desktop():
    """Notepad (a Document with a value), Calculator (buttons), a login form with a password box, a classic Win32 message box, and a hung-looking app."""
    d = FakeDesktop()
    d.add_window(1001, 100, "notepad", "Untitled - Notepad", "Notepad", node("Untitled - Notepad", cls="Notepad", ctype="Window", hwnd=1001, children=[
        node("Text editor", auto_id="15", cls="RichEditD2DPT", ctype="Document", hwnd=1002, value="", text=""),
        node("File", auto_id="MenuFile", ctype="MenuItem", invoke=True),
        node("Status bar", ctype="Text")]))
    d.add_window(2001, 200, "calc", "Calculator", "CalcFrame", node("Calculator", cls="CalcFrame", ctype="Window", hwnd=2001, children=[
        node("Seven", auto_id="num7Button", ctype="Button", invoke=True), node("Plus", auto_id="plusButton", ctype="Button", invoke=True),
        node("Display is 0", auto_id="CalculatorResults", ctype="Text")]))
    d.add_window(3001, 300, "loginapp", "Sign in", "LoginWnd", node("Sign in", cls="LoginWnd", ctype="Window", hwnd=3001, children=[
        node("User name", auto_id="user", ctype="Edit", value=""), node("Password", auto_id="pw", ctype="Edit", value="", password=True)]))
    d.add_window(4001, 400, "oldtool", "Old tool", "OldWnd", node("Old tool", cls="OldWnd", ctype="Window", hwnd=4001, children=[
        node("", auto_id="1001", cls="Edit", ctype="Pane", hwnd=4002, wm_text=""),                           # a classic Edit: no patterns, typed into with keys
        node("Saved?", cls="#32770", ctype="Pane", hwnd=4010, children=[
            node("Save changes to Untitled?", cls="Static", ctype="Text"), node("Save", auto_id="CommandButton_6", cls="CCPushButton", ctype="Pane", hwnd=4011),
            node("Don't save", cls="Button", ctype="Button", invoke=True)])]))
    d.foreground = 1001
    return d


class FakeDesktop:
    def __init__(self):
        self.windows = {}              # hwnd -> {pid, proc, title, cls, root, minimized, maximized}
        self.foreground = 0
        self.focused = None            # the node that has keyboard focus
        self.clipboard = None
        self.log = []
        self.refuse_foreground = False  # SetForegroundWindow does nothing (the foreground lock)
        self.paste_lands = True
        self.fail_set_value = False

    def add_window(self, hwnd, pid, proc, title, cls, root):
        self.windows[hwnd] = {"hwnd": hwnd, "pid": pid, "proc": proc, "title": title, "cls": cls, "root": root, "minimized": False, "maximized": False,
                              "start": 132_000_000_000_000_000 + hwnd}


class FakeBackend(Backend):
    name = "fake"

    def __init__(self, desktop=None):
        self.d = desktop or default_desktop()
        self.alt_taps = 0
        self.attach_tries = []

    # ---- helpers
    def _elem(self, n, pid=0):
        return Elem(raw=n, name=n["name"], auto_id=n["id"], cls=n["cls"], ctype=n["ctype"], enabled=n["enabled"], password=n["password"], hwnd=n["hwnd"], pid=pid)

    def _pid_of_node(self, root_node):
        for w in self.d.windows.values():
            if w["root"] is root_node:
                return w["pid"]
        return 0

    def _walk(self, n):
        for c in n["children"]:
            yield c
            yield from self._walk(c)

    def _owner_pid(self, n):
        for w in self.d.windows.values():
            if n is w["root"] or any(n is x for x in self._walk(w["root"])):
                return w["pid"]
        return 0

    # ---- processes and windows
    def list_windows(self, proc):
        ws = [{"hwnd": w["hwnd"], "pid": w["pid"], "start": w["start"], "name": w["proc"]} for w in self.d.windows.values()
              if not proc or w["proc"] == proc.lower()]
        return ws, self.d.foreground

    def pids_named(self, name):
        return sorted({w["pid"] for w in self.d.windows.values() if w["proc"] == name.lower()})

    def process_name(self, pid):
        return next((w["proc"] for w in self.d.windows.values() if w["pid"] == pid), "")

    def foreground_hwnd(self):
        return self.d.foreground

    def foreground_pid(self):
        w = self.d.windows.get(self.d.foreground)
        return w["pid"] if w else 0

    def pid_of(self, hwnd):
        return self.d.windows[hwnd]["pid"] if hwnd in self.d.windows else 0

    # ---- tree
    def top_level(self):
        return [Elem(raw=w["root"], name=w["title"], cls=w["cls"], ctype="Window", hwnd=w["hwnd"], pid=w["pid"]) for w in self.d.windows.values()]

    def root_for(self, hwnd):
        w = self.d.windows.get(hwnd)
        return Elem(raw=w["root"], name=w["title"], cls=w["cls"], ctype="Window", hwnd=hwnd, pid=w["pid"]) if w else None

    def children(self, el):
        return [self._elem(c, el.pid) for c in el.raw["children"]]

    def descendants(self, el, limit):
        out = []
        for c in self._walk(el.raw):
            if len(out) >= limit:
                break
            out.append(self._elem(c, el.pid))
        return out

    def find_first(self, root, ident):
        for c in self._walk(root.raw):
            if c["id"] == ident or c["name"] == ident:
                return self._elem(c, root.pid)
        return None

    def find_text_fields(self, root):
        return [self._elem(c, root.pid) for c in self._walk(root.raw) if c["ctype"] in ("Edit", "Document") or c["cls"] in ("Edit", "RichEdit20W", "RICHEDIT50W")]

    # ---- patterns
    def invoke(self, el):
        if not el.raw["invoke"]:
            return False
        el.raw["invoked"] += 1
        self.d.log.append(("invoke", el.raw["name"]))
        return True

    def has_value(self, el):
        return el.raw["value"] is not None

    def set_value(self, el, text):
        if self.d.fail_set_value:
            raise OSError("COM error with screen text: secret-in-the-error")
        el.raw["value"] = text
        self.d.log.append(("set_value", el.raw["name"], text))

    def get_value(self, el):
        return el.raw["value"]

    def get_text(self, el, limit):
        t = el.raw["text"]
        return None if t is None else (t if limit < 0 else t[:limit])

    def focused_element(self):
        return self._elem(self.d.focused) if self.d.focused else None

    def set_focus(self, el):
        self.d.focused = el.raw
        self.d.log.append(("focus_element", el.raw["name"]))
        return True

    # ---- win32
    def post_click(self, hwnd):
        self.d.log.append(("bm_click", hwnd))

    def wm_gettext(self, hwnd):
        for w in self.d.windows.values():
            for c in self._walk(w["root"]):
                if c["hwnd"] == hwnd and c["wm_text"] is not None:
                    return c["wm_text"]
        return ""

    def show_window(self, hwnd, cmd):
        w = self.d.windows[hwnd]
        w["minimized"], w["maximized"] = cmd == 6, cmd == 3
        self.d.log.append(("show", hwnd, cmd))

    def is_iconic(self, hwnd):
        return self.d.windows[hwnd]["minimized"]

    def is_zoomed(self, hwnd):
        return self.d.windows[hwnd]["maximized"]

    def tap_alt(self):
        self.alt_taps += 1

    def bring_to_front(self, hwnd, attach):
        self.attach_tries.append(attach)
        if not self.d.refuse_foreground:
            self.d.foreground = hwnd

    def send_text(self, text):
        n = self.d.focused
        self.d.log.append(("keys", text))
        if n is None:
            return
        if n["value"] is not None:
            n["value"] += text
        elif n["wm_text"] is not None:
            n["wm_text"] += text

    def send_ctrl_v(self):
        self.d.log.append(("ctrl+v",))
        n = self.d.focused
        if self.d.paste_lands and n is not None and self.d.clipboard is not None:
            if n["value"] is not None:
                n["value"] += self.d.clipboard
            if n["text"] is not None:
                n["text"] += self.d.clipboard

    def clipboard_get(self):
        return self.d.clipboard

    def clipboard_set(self, text):
        self.d.clipboard = text
        self.d.log.append(("clipboard_set", len(text)))
        return True


def desktop_state(d):
    """Everything a job can change on the fake desktop, as plain data (compared between the worker path and the PowerShell path)."""
    def walk(n, out):
        out.append([n["name"], n["id"], n["value"], n["text"], n["wm_text"], n["invoked"]])
        for c in n["children"]:
            walk(c, out)
        return out
    return {"windows": {str(h): {"min": w["minimized"], "max": w["maximized"], "tree": walk(w["root"], [])} for h, w in sorted(d.windows.items())},
            "foreground": d.foreground, "clipboard": d.clipboard}


def apply_setup(d, name, value=None):
    """Change the fake desktop the same way on both sides of a parity test (the worker child runs this on request, the test runs it on its own copy)."""
    if name == "refuse_foreground":
        d.refuse_foreground = bool(value)
    elif name == "paste_lands":
        d.paste_lands = bool(value)
    elif name == "focus_document":
        d.focused = d.windows[int(value)]["root"]["children"][0]
    elif name == "second_notepad":
        d.add_window(1101, 101, "notepad", "Other - Notepad", "Notepad", node("Other - Notepad", cls="Notepad", ctype="Window", hwnd=1101, children=[
            node("Text editor", auto_id="15", cls="RichEditD2DPT", ctype="Document", hwnd=1102, value="", text="")]))
    elif name == "add_bank":
        d.add_window(6001, 600, "chrome", "ICICI Bank - Google Chrome", "Chrome_WidgetWin_1", node("ICICI Bank - Google Chrome", cls="Chrome_WidgetWin_1", ctype="Window", hwnd=6001, children=[
            node("Account balance", ctype="Text")]))
    elif name == "remove_window":
        d.windows.pop(int(value), None)
    elif name == "set_value":
        hwnd, idx, text = value
        d.windows[hwnd]["root"]["children"][idx]["value"] = text
    else:
        raise KeyError(name)
