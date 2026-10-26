"""A fake Windows PC and a fake JARVIS console for the D54 tests (write-and-save, VS Code, solve what is on screen, and any other task).

Everything between the plugin and the pixels is REAL code: the plugin's agents (`DesktopPlugin._agents`, including the observe - decide - act driver), the
kernel's real `Owner.pc` with its job validator, policy and approvals (a held action really comes back as "needs_approval"), and the node's real
`jobs.ui_action`, `pcopen.pc_open`, `ui_windows` and `savefile` (folder allowlist, free file name, read-back from the real temporary folders).
Only three things are faked: the console's HTTP (`FakeWindows.call`), the PowerShell that drives the Save As dialog (`FakeWindows.dialog`), and
Windows itself: a scene of small `FakeApp`s (Notepad, VS Code, and whatever a test builds: Paint, Calculator, Explorer, a form in Chrome,
Settings, a mail client, an installer ...) with elements, keys, and dialogs that have a visible text and buttons. No Windows, no PowerShell, no network.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve()
JARVIS = HERE.parents[3]
for p in (JARVIS / "kernel", JARVIS / "node"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from jarvis_kernel.owner import Owner, OwnerError  # noqa: E402
from jarvis_kernel.jobs import Jobs  # noqa: E402
from jarvis_kernel.store import Kernel  # noqa: E402
from jarvis_kernel.tasks import Tasks  # noqa: E402
from jarvis_node import jobs as node_jobs, pcopen, savefile as sf, taskops  # noqa: E402


class FakeBin:
    """The Recycle Bin: a folder and a list (what the Shell would list)."""

    def __init__(self, folder: Path):
        self.folder, self.items = folder, []
        folder.mkdir(parents=True, exist_ok=True)

    def trasher(self, path):
        import shutil
        dst = self.folder / f"{len(self.items)}-{os.path.basename(path)}"
        self.items.append({"name": os.path.basename(path), "from": os.path.dirname(path), "size": 1})
        shutil.move(path, str(dst))

    def lister(self):
        return list(self.items)

    def emptier(self):
        import shutil
        for n in os.listdir(self.folder):
            p = self.folder / n
            shutil.rmtree(p) if p.is_dir() else p.unlink()
        self.items = []


class FakeShell:
    """The PowerShell of taskops (settings, clipboard): a small system state it reads and writes, and the lines a real one would print."""

    def __init__(self):
        self.state = {"theme": "1", "toast": "1", "brightness": "50", "wallpaper": "", "radios": {"WiFi": "On", "Bluetooth": "On"}, "locked": False, "clipboard": ""}
        self.scripts: list = []

    def run(self, script: str, env: dict) -> str:
        self.scripts.append(script)
        v = env.get("JARVIS_SET_VALUE", "")
        if "AppsUseLightTheme" in script:
            self.state["theme"] = v
            return f"JARVIS_SET:{v},{v}"
        if "ToastEnabled" in script:
            self.state["toast"] = v
            return f"JARVIS_SET:{v}"
        if "WmiSetBrightness" in script:
            self.state["brightness"] = v
            return f"JARVIS_SET:{v}"
        if "SystemParametersInfo" in script:
            self.state["wallpaper"] = v
            return f"JARVIS_SET:{v}"
        if "LockWorkStation" in script:
            self.state["locked"] = True
            return "JARVIS_SET:True"
        if "ms-settings:nightlight" in script:
            return "JARVIS_SET:page"
        if "GetRadiosAsync" in script:
            kind = env.get("JARVIS_SET_KIND", "")
            self.state["radios"][kind] = "On" if v == "1" else "Off"
            return "JARVIS_SET:" + self.state["radios"][kind]
        if "Set-Clipboard" in script:
            self.state["clipboard"] = env.get("JARVIS_CLIP_TEXT", "")
            return "JARVIS_SET:ok" if "-eq" not in script else "JARVIS_SET:" + str(self.state["clipboard"] == env.get("JARVIS_CLIP_TEXT", ""))
        if "Get-Clipboard" in script and "JARVIS_CLIP_TEXT" in script:
            return "JARVIS_SET:" + str(self.state["clipboard"] == env.get("JARVIS_CLIP_TEXT", ""))
        if "Get-Clipboard" in script:
            import base64
            return "JARVIS_CLIP:" + base64.b64encode(self.state["clipboard"].encode("utf-8")).decode()
        raise AssertionError("fake shell: an unknown script")


class FakeApp:
    """One open application of the fake PC."""

    def __init__(self, fw, process: str, title: str):
        self.fw, self.process, self.title = fw, process, title
        self.elements: dict = {}          # name -> fn(app): a control the task can click
        self.keys: dict = {}              # "ctrl+v" -> fn(app)
        self.dialogs: list = []           # open dialogs: {"title", "texts", "buttons": {name: fn(app)}}
        self.screen: list = []            # what the window shows (UI Automation text)
        self.fields: dict = {}            # element -> text typed into it
        self.doc = ""
        self.strokes: list = []
        self.log: list = []               # every effect that really happened in this app

    def dialog(self, title, texts, buttons):
        self.dialogs.append({"title": title, "texts": list(texts), "buttons": dict(buttons)})

    def content(self) -> str:
        return self.doc or f"[{self.process} drawing: {len(self.strokes)} strokes]" + "".join(f"{k}={v};" for k, v in self.fields.items())


class FakeWindows:
    def __init__(self, tmp_path: Path, monkeypatch=None):
        self.home = tmp_path / "home"
        self.known = {}
        for n in ("documents", "desktop", "downloads", "pictures"):
            (self.home / n.title()).mkdir(parents=True)
            self.known[n] = str(self.home / n.title())
        self.proj = tmp_path / "proj"
        (self.proj / "src").mkdir(parents=True)
        (self.home / "Documents" / "myapp").mkdir()
        self.out = tmp_path / "outputs"
        self.out.mkdir()
        self.roots = sf.folder_roots(known=self.known, projects={"jarvis-hq": {"path": {os.name: str(self.proj)}}}, outputs=str(self.out))
        self.windows: list[dict] = []
        self.apps: dict = {}                        # process (lower) -> FakeApp, for the general scene
        self.notepad_text = ""
        self.code_text = ""
        self.code_untitled = False
        self.calls: list[dict] = []                 # every body the console was asked to queue, in order (as the plugin sent them: goal and ctx included)
        self.n = 0
        self.replace_prompt = False                 # the next Save As raises the Replace prompt (the file "appears" just before)
        self.dialog_runs: list[dict] = []
        self.launched: list = []
        self.paste_noise = ""                       # appended by the "editor" on every paste (a broken editor), to prove the disk check
        self.pending: list = []
        self.factories: dict = {}                   # app name -> fn(fw) -> FakeApp, started by open_app
        self.global_keys: list = []                 # win+d, win+left ... sent to the desktop
        self.bin = FakeBin(tmp_path / "recycle")
        self.shell = FakeShell()
        self.scheduler: list = []                   # the automations the console was asked to create
        self.now = 1_800_000_000.0
        self._ctx = None
        self.sys_rows = {"disk": [{"drive": "C", "free_gb": 120.5, "total_gb": 476.0}],
                         "network": [{"adapters_up": 1, "adapters": ["Wi-Fi"], "dns_ok": True, "internet_tcp_ok": True}],
                         "battery": [{"percent": 64, "charging": False, "minutes_left": 190}]}
        self.kernel = Kernel(str(tmp_path / "kernel"))
        self.owner = Owner(self.kernel, Tasks(self.kernel), Jobs(self.kernel))
        self.owner.node = "laptop"
        if monkeypatch is not None:
            monkeypatch.chdir(tmp_path)                   # the kernel resolves a bare file name against its working directory: keep that out of any ".claude" checkout path
            monkeypatch.setattr(sf, "folder_roots", lambda *a, **k: self.roots)         # pc_open asks for the roots itself: it gets these
            from jarvis.plugins.desktop import core
            local = tmp_path / "outputs-local" / "local-only"
            local.mkdir(parents=True)
            monkeypatch.setattr(core, "LOCAL_RESULTS", local)
            self.local = local
            base = tmp_path / "lad" / "Programs" / "Microsoft VS Code"
            base.mkdir(parents=True)
            (base / "Code.exe").write_bytes(b"")
            monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "lad"))
            monkeypatch.setenv("JARVIS_OUTPUTS", str(tmp_path / "outputs-local"))         # the node's local-only folder = core.LOCAL_RESULTS
            monkeypatch.delenv("ProgramFiles", raising=False)
            monkeypatch.delenv("ProgramFiles(x86)", raising=False)

    # ---- Windows
    def add_window(self, title: str, process: str, foreground: bool = True):
        if foreground:
            for w in self.windows:
                w["foreground"] = False
        self.windows.insert(0, {"title": title, "process": process, "hwnd": 100 + len(self.windows), "foreground": foreground, "minimized": False,
                                "w": 800, "h": 600, "id": f"w{len(self.windows)}"})

    def add_app(self, process: str, title: str, foreground: bool = True) -> FakeApp:
        app = FakeApp(self, process, title)
        self.apps[process.lower()] = app
        self.add_window(title, process, foreground)
        return app

    def lister(self):
        out = []
        for w in self.windows:
            d = dict(w)
            app = self.apps.get(w["process"].lower())
            if app is not None and app.dialogs:                      # an open dialog is a window of its own, in front
                out.append({**w, "title": app.dialogs[-1]["title"], "hwnd": w["hwnd"] + 5000, "foreground": True})
                d["foreground"] = False
            out.append(d)
        return out

    def retitle(self, process: str, title: str):
        for w in self.windows:
            if w["process"].lower() == process.lower():
                w["title"] = title

    # ---- the PowerShell of winbackend.SAVE_AS, as the dialog would behave
    def dialog(self, env: dict):
        self.dialog_runs.append(dict(env))
        path = env["JARVIS_SAVE_PATH"]
        if os.path.exists(path):
            raise RuntimeError("ui-action-failed:path exists")
        proc = env["JARVIS_UI_PROC"].lower()
        if self.replace_prompt:
            Path(path).write_text("the owner's own file", encoding="utf-8")          # the file appeared meanwhile: the real dialog asks to replace it
            raise RuntimeError("ui-action-failed:replace prompt")
        app = self.apps.get(proc)
        text = app.content() if app is not None else (self.code_text if proc == "code" else self.notepad_text)
        Path(path).write_text(text, encoding="utf-8", newline="")
        name = os.path.basename(path)
        if app is not None:
            app.log.append(("saved", name))
        self.retitle(proc, f"{name} - Notepad" if proc == "notepad" else f"{name} - {Path(path).parent.name} - Visual Studio Code" if proc == "code" else f"{name} - {proc}")

    # ---- the node's UI backend (what winbackend would do), fake
    def backend(self):
        def need(proc):
            if not any(w["process"].lower() == proc.lower() for w in self.windows):
                raise RuntimeError("ui-action-failed:no window")
            return self.apps.get(proc.lower())

        def proc_of(target):
            return target.partition("|")[0]

        def elem_of(target):
            return target.partition("|")[2]

        def norm(s):
            return str(s).replace("&", "").strip().lower()

        def focus(t, p):
            need(proc_of(t))
            return {"observed": "the target is the foreground window", "foreground": True}

        def key(t, p):
            if proc_of(t).lower() == "desktop":                         # a Windows shortcut sent to the desktop
                self.global_keys.append(p.get("text", ""))
                if p.get("text") == "win+d":
                    for w in self.windows:
                        w["minimized"] = True
                return None
            app = need(proc_of(t))
            k = p.get("text", "")
            if proc_of(t).lower() == "code" and k == "ctrl+n":
                self.code_text, self.code_untitled = "", True
            if proc_of(t).lower() == "code" and k == "ctrl+shift+p":          # the Command Palette (D54, 2026-10-26: ctrl+n went to VS Code's Chat box)
                self.code_palette = ""
            elif proc_of(t).lower() == "code" and k == "enter" and getattr(self, "code_palette", None) == "File: New Untitled Text File":
                self.code_text, self.code_untitled, self.code_palette = "", True, None
            if app is not None:
                if app.dialogs and k == "enter":
                    dlg = app.dialogs.pop()
                    name = next(iter(dlg["buttons"]))
                    dlg["buttons"][name](app)
                elif k in app.keys:
                    app.keys[k](app)

        def click(t, p):
            app = need(proc_of(t))
            if app is None:
                return None
            el = norm(elem_of(t))
            if app.dialogs:
                dlg = app.dialogs[-1]
                for name, fn in dlg["buttons"].items():
                    if norm(name) == el:
                        app.dialogs.pop()
                        fn(app)
                        return None
            for name, fn in app.elements.items():
                if norm(name) == el:
                    fn(app)
                    return None
            raise RuntimeError("ui-action-failed:no element")

        def type_(t, p):
            app = need(proc_of(t))
            if app is not None:
                app.fields[elem_of(t)] = p.get("text", "")
                app.doc = p.get("text", "") if elem_of(t) in ("*", "") else app.doc
                app.log.append(("typed", elem_of(t), p.get("text", "")))
                return {"observed": "text read back from the field"}
            self.notepad_text = p.get("text", "")
            return {"observed": "text read back from the field"}

        def type_focused(t, p):
            app = need(proc_of(t))
            proc = proc_of(t).lower()
            if app is not None:
                app.doc += p.get("text", "")
                return {"observed": "text read back from the editor"}
            if proc == "code" and getattr(self, "code_palette", None) is not None:        # typed into the palette's box, not into the document
                self.code_palette = p.get("text", "")
                return {}
            if proc == "code":
                self.code_text += p.get("text", "") + self.paste_noise
                return {}
            self.notepad_text += p.get("text", "") + self.paste_noise
            return {"observed": "text read back from the editor"}

        def close(t, p):
            name = proc_of(t)
            app = need(name)
            if app is not None and getattr(app, "unsaved", False):         # the app asks to save: nothing is closed, the question stays open
                app.dialog("Save changes?", ["Do you want to save changes?"], {"Cancel": lambda a: None})
                return {"closed": False}
            self.windows = [w for w in self.windows if w["process"].lower() != name.lower()]
            self.apps.pop(name.lower(), None)
            return {"closed": True, "observed": "the window closed"}

        def state(word):
            def f(t, p):
                need(proc_of(t))
                for w in self.windows:
                    if w["process"].lower() == proc_of(t).lower():
                        w["minimized"] = word == "minimized"
                return {"window_state": word, "observed": "the window state was read back"}
            return f

        def stroke(t, p):
            app = need(proc_of(t))
            if app is not None:
                app.strokes.append(elem_of(t))

        def save_as(t, p):
            need(proc_of(t))
            return sf.save_as(t, p, run=self.dialog, lister=self.lister, roots=self.roots, wait=lambda s: None, timeout_s=0.01)
        return {"close": close, "minimize": state("minimized"), "maximize": state("maximized"), "restore": state("normal"),
                "focus": focus, "key": key, "type": type_, "type_focused": type_focused, "save_as": save_as, "click": click, "click_at": stroke,
                "drag": stroke, "scroll": lambda t, p: need(proc_of(t)) and None}

    def launcher(self, args):
        self.launched.append(list(args))
        self.add_window(f"{Path(args[-1]).name} - Visual Studio Code", "Code")

    # ---- the console (core.owner replacement): the kernel is the real one
    def call(self, method: str, name: str, body):
        if method == "GET" and name == "state":
            return 200, {"approvals": [{"id": a} for a in self.pending]}
        if method == "GET" and name.startswith("job/"):
            try:
                return 200, self.owner.job(name.split("/", 1)[1])
            except OwnerError as e:
                return e.code, {"error": str(e)}
        if method == "POST" and name in ("deny", "job/cancel"):
            return 200, {}
        if method == "POST" and name == "automations":
            d = dict(body.get("definition") or {})
            if not d.get("name") or d.get("trigger_kind") != "once":
                return 400, {"error": "fake scheduler: a name and a one-time trigger"}
            d["id"] = f"auto-{len(self.scheduler) + 1}"
            self.scheduler.append(d)
            return 201, d
        if method == "GET" and name == "automations":
            return 200, {"automations": list(self.scheduler)}
        if method == "POST" and name == "pc":
            return self._pc(dict(body))
        return 404, {"error": f"fake console: {method} {name}"}

    def _pc(self, body: dict):
        body.pop("holder", None)
        self.calls.append(dict(body))
        try:
            r = self.owner.pc(body)
        except OwnerError as e:
            return e.code, {"error": str(e)}
        if r.get("status") == "needs_approval":
            self.pending = [r["approval_id"]]
            return 200, r
        job = self.owner.jobs.next_for("laptop")
        assert job and job["id"] == r["job"], "the fake node takes the job that was just queued"
        self.owner.jobs.finish("laptop", job["id"], self._node(job))
        return 200, r

    def _node(self, job: dict) -> dict:
        kind, p = job["kind"], job["params"]
        if kind == "ui_windows":
            return node_jobs.ui_windows({}, lister=self.lister)
        if kind == "ui_action":
            return node_jobs.ui_action(p, backend=self.backend(), lister=self.lister)
        if kind == "diag":
            return self._diag(p)
        if kind == "pc_open":
            if p["op"] == "open_app":
                return self._open_app(p["target"])
            out = pcopen.pc_open(p, roots=self.known, launcher=self.launcher)
            if out.get("ok") and p["op"] == "open_code":
                out = dict(out, verified="verified")
            return out
        if kind in ("file_op", "setting", "clipboard"):
            return taskops.run(kind, p, self.ctx(), run=self.shell.run)
        if kind == "screenshot":
            def shooter(path):
                os.makedirs(os.path.dirname(path), exist_ok=True)
                Path(path).write_bytes(b"\x89PNG fake")
                return {"width": 1920, "height": 1080, "bytes": 9}
            return node_jobs.screenshot(p, root=str(self.out), shooter=shooter)
        return {"ok": False, "error": f"fake node: {kind}"}

    def ctx(self):
        """taskops' context: the fake PC's known folders, its bin, a temp-only environment, a plain directory walk instead of Everything."""
        if self._ctx is None:
            local = self.out.parent / "appdata"
            (local / "Temp").mkdir(parents=True, exist_ok=True)
            self.env = {"LOCALAPPDATA": str(local), "APPDATA": str(self.out.parent / "roaming"), "TEMP": str(local / "Temp"), "SystemRoot": str(self.out.parent / "WindowsDir")}
            self._ctx = taskops.Ctx(roots=self.roots, journal=str(self.out.parent / "ops" / "journal.jsonl"), trasher=self.bin.trasher, bin_lister=self.bin.lister,
                                    bin_emptier=self.bin.emptier, env=self.env, now=lambda: self.now, launcher=lambda a: self.launched.append(list(a)), scan_only=True)
        return self._ctx

    def _open_app(self, name: str) -> dict:
        key = name.strip().lower()
        if key in self.factories:
            self.factories[key](self)
        elif key == "notepad":
            self.add_window("Untitled - Notepad", "notepad")
        else:
            return {"ok": False, "error": "no such app in the Start list"}
        return {"ok": True, "opened": name, "verified": "verified"}

    def _diag(self, p: dict) -> dict:
        if p["op"] in self.sys_rows:
            rows = self.sys_rows[p["op"]]
            return {"ok": True, "check": p["op"], "count": len(rows), "rows": rows}
        proc = p["target"]
        app = self.apps.get(proc.lower())
        if p["op"] == "window_text":
            rows = []
            if app is not None:
                rows.append({"title": app.title, "class": "Window", "dialog": False, "texts": list(app.screen), "buttons": list(app.elements)})
                for d in app.dialogs:
                    rows.append({"title": d["title"], "class": "#32770", "dialog": True, "texts": list(d["texts"]), "buttons": list(d["buttons"])})
            elif proc.lower() == "notepad":
                rows.append({"title": "Untitled - Notepad", "dialog": False, "texts": [self.notepad_text], "buttons": []})
            data = rows
        elif p["op"] == "window_ocr":
            data = {"observed_lines": list(app.screen) if app is not None else []}
        else:
            return {"ok": False, "error": "fake node: diag"}
        path = self.local / f"{p['op']}-{self.n}.json"
        self.n += 1
        path.write_text(json.dumps(data), encoding="utf-8")
        return {"ok": True, "check": p["op"], "local_result": str(path), "summary": {"windows": len(data) if isinstance(data, list) else 0}}
