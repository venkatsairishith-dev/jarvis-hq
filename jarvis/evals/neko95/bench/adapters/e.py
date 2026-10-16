"""E: error diagnosis and self-repair, against the real code: core.diagnose on real fixture files, real listeners and the
real disk; the node's repair.py on real files (backups in a temp folder; PowerShell/winget faked where they are Windows
programs); the kernel policy for repairs; the desktop troubleshooter end to end through a fake JARVIS console over real
local HTTP (the fake console stands in for the kernel and the laptop node, and runs the node's real repair code)."""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import shutil
import socket
import subprocess
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

_TMP = tempfile.mkdtemp(prefix="bench-e-")
os.environ.setdefault("NEKO_STORAGE_SELECTED_ROOT", _TMP)
os.environ.setdefault("JARVIS_OUTPUTS", os.path.join(_TMP, "outputs"))

from plugin.plugins.desktop import coordinator as C  # noqa: E402
from plugin.plugins.desktop import core  # noqa: E402
from plugin.plugins.desktop import decide as D  # noqa: E402
from plugin.plugins.desktop import monitor as MON  # noqa: E402
from jarvis_kernel.policy import NAMES, Action, decide  # noqa: E402
from jarvis_node import repair, runtime  # noqa: E402

DRIVE = "C:" if os.name == "nt" else "/"


def _dir() -> Path:
    return Path(tempfile.mkdtemp(dir=_TMP))


def _home_dir() -> Path:
    """repair.py only writes in the user's own folder (or D:): fixtures live under ~ (HOME=/tmp in the test image)."""
    return Path(tempfile.mkdtemp(prefix="bench-e-", dir=os.path.expanduser("~")))


def _dialog(*texts, title="Neko Sample App - error"):
    return [{"title": title, "dialog": True, "buttons": ["OK"], "texts": list(texts)}]


def _free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def _brief(d):
    return f"verified {d['verified']}, cause '{d['cause'][:140]}', fix {json.dumps(d.get('fix'))[:140]}"


# ---------------------------------------------------------------- diagnosis from real evidence
def diag_json(a):
    d = _dir()
    cfg = d / "config.json"
    cfg.write_text(a["content"], encoding="utf-8")
    path = str(cfg) if not a.get("missing") else str(d / "absent.json")
    if a.get("missing"):
        cfg.unlink()
    r = core.diagnose(_dialog("Neko Sample App could not start.", f"File: {path}"), [], [])
    leaked = "could not start" in r["cause"]
    w = a["want"]
    if w == "typo_fix":
        f = r.get("fix") or {}
        raw = a["content"]
        fixed = raw.replace(f.get("args", {}).get("find", "\0"), f.get("args", {}).get("replace", ""), 1)
        try:
            after = json.loads(fixed)
        except ValueError:
            after = None
        ok = r["verified"] and f"line {a['line']}" in r["cause"] and f.get("action") == "edit_file" and raw.count(f["args"]["find"]) == 1 \
            and after == a["after"] and f["target"] == str(cfg)
    elif w == "no_fix":
        ok = r["verified"] and f"line {a['line']}" in r["cause"] and r["fix"] is None
    elif w == "valid_now":
        ok = not r["verified"] and r["fix"] is None and any("valid JSON now" in e for e in r["evidence"]) and not leaked
    else:                                                     # the named file does not exist: nothing is claimed
        ok = not r["verified"] and r["fix"] is None and not leaked
    return ok, _brief(r) + f", dialog text in cause {leaked}"


def diag_port(a):
    d = _dir()
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    s.listen(1)
    port = s.getsockname()[1]
    cfg = d / "config.json"
    cfg.write_text(json.dumps({"name": "x", "port": port, "debug": False}, indent=1), encoding="utf-8")
    try:
        if a["listening"]:
            r = core.diagnose(_dialog(f"Port {port} is already in use by another program.", f"File: {cfg}"), [], [])
            f = r.get("fix") or {}
            new = int(f.get("args", {}).get("replace", '"port": 0').split(":")[1])
            probe = socket.socket()
            try:
                probe.bind(("127.0.0.1", new))
                free = True
            except OSError:
                free = False
            finally:
                probe.close()
            ok = r["verified"] and f"port {port}" in r["cause"] and str(os.getpid()) in r["cause"] and f["args"]["find"] == f'"port": {port}' \
                and new != port and free
            return ok, _brief(r) + f", proposed port {new} free {free}"
    finally:
        s.close()
    r = core.diagnose(_dialog(f"Port {port} is already in use by another program."), [], [])
    return not r["verified"] and "hypothesis" in r["cause"] and r["fix"] is None, _brief(r)


def diag_disk(a):
    need = a["need_mb"]
    r = core.diagnose(_dialog(f"There is not enough space on the disk: it needs {need} MB on {a.get('drive', DRIVE)}"), [], [])
    if a["want"] == "verified":
        ok = r["verified"] and r["fix"] is None and r["needs_mb"] == need and "owner decides" in r.get("advice", "")
    elif a["want"] == "unchecked":
        ok = not r["verified"] and "could not be checked" in r["cause"] and "None" not in r["cause"]
    else:
        ok = not r["verified"] and "hypothesis" in r["cause"] and r["fix"] is None
    return ok, _brief(r) + f", free {r.get('free_mb')} MB"


def diag_service_down(a):
    port = _free_port()
    r = core.diagnose(_dialog(f"Could not connect to the service at 127.0.0.1:{port} (connection refused)."), [], [])
    ok = r["verified"] and "nothing is listening" in r["cause"] and r["fix"] is None and "start" in r.get("advice", "")
    return ok, _brief(r) + f", advice '{r.get('advice')}'"


def diag_dependency(a):
    d = _dir()
    if a["kind"] == "dll_absent":
        r = core.diagnose(_dialog("The code execution cannot proceed because NekoRuntime140.dll was not found."), [], [], app_dir=str(d))
        ok = r["verified"] and r.get("dependency") == "NekoRuntime140.dll" and r["fix"] is None
    elif a["kind"] == "dll_present":
        (d / "NekoRuntime140.dll").write_bytes(b"MZ")
        r = core.diagnose(_dialog("The code execution cannot proceed because NekoRuntime140.dll was not found."), [], [], app_dir=str(d))
        ok = not r["verified"] and any("present after all" in e for e in r["evidence"]) and "hypothesis" in r["cause"]
    else:
        r = core.diagnose(_dialog("Traceback: ModuleNotFoundError: No module named 'neko_sample_lib'"), [], [])
        ok = not r["verified"] and "hypothesis" in r["cause"] and r.get("dependency") == "neko_sample_lib"
    return ok, _brief(r) + f", evidence {r['evidence'][:2]}"


def diag_crash_only(a):
    if a["kind"] == "crash":
        r = core.diagnose([], [{"provider": "Application Error"}], [{"app": "NekoSampleApp.exe", "kind": "crash", "module": "ucrtbase.dll", "code": "c0000409"}])
        ok = not r["verified"] and "hypothesis" in r["cause"] and any("crash record" in e for e in r["evidence"]) \
            and any("1 recent error event" in e for e in r["evidence"])
    else:
        r = core.diagnose([{"title": "Neko Sample App", "dialog": False, "texts": ["Ready"]}], [], [])
        ok = not r["verified"] and r["cause"].startswith("no error dialog") and r["fix"] is None
    return ok, _brief(r)


# ---------------------------------------------------------------- repairs (node): backup first, verify after
class _Backups:
    def __enter__(self):
        self.old, self.dir = repair.BACKUPS, _home_dir()
        repair.BACKUPS = str(self.dir / "backups")
        return self

    def __exit__(self, *exc):
        repair.BACKUPS = self.old
        shutil.rmtree(self.dir, ignore_errors=True)

    def count(self):
        return len(os.listdir(repair.BACKUPS)) if os.path.isdir(repair.BACKUPS) else 0


def _cfg(b, text='{\n  "name": "sample",\n  "port": 80x80\n}\n'):
    p = b.dir / "app" / "config.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")
    return p


def repair_edit(a):
    with _Backups() as b:
        p = _cfg(b)
        before = p.read_bytes()
        r = repair.edit_file(str(p), '"port": 80x80', '"port": 8080')
        bdir = Path(repair.BACKUPS) / r.get("backup", "?")
        m = json.loads((bdir / "manifest.json").read_text(encoding="utf-8")) if bdir.is_dir() else {}
        ok = r["ok"] and r["verified_on_disk"] and (bdir / "file.bak").read_bytes() == before \
            and m.get("sha256") == hashlib.sha256(before).hexdigest() and json.loads(p.read_text())["port"] == 8080 \
            and r["undo"] == f"repair restore {r['backup']}"
        return ok, f"ok {r['ok']}, backup equals the original {bdir.is_dir() and (bdir / 'file.bak').read_bytes() == before}, undo '{r.get('undo')}'"


def repair_restore(a):
    with _Backups() as b:
        p = _cfg(b)
        r = repair.edit_file(str(p), '"port": 80x80', '"port": 8080')
        back = repair.restore(r["backup"])
        restored = "80x80" in p.read_text()
        again = repair.restore(back["undo"].split()[-1])
        ok = back["ok"] and back["verified_on_disk"] and restored and again["ok"] and json.loads(p.read_text())["port"] == 8080
        return ok, f"restore {back['ok']} (original back {restored}), undo of the restore {again['ok']}"


def repair_refusals(a):
    with _Backups() as b:
        p = _cfg(b, '{"a": "x", "b": "x"}\n')
        before = p.read_bytes()
        amb = repair.edit_file(str(p), '"x"', '"y"')
        empty = repair.edit_file(str(p), "", "z")
        outside = repair.edit_file("/etc/hostname", "a", "b")
        os.makedirs(repair.BACKUPS, exist_ok=True)
        inside_backups = Path(repair.BACKUPS) / "x.json"
        inside_backups.write_text("{}", encoding="utf-8")
        bk = repair.edit_file(str(inside_backups), "{}", "[]")
        bad_id = repair.restore("../../etc")
        ok = not any(x["ok"] for x in (amb, empty, outside, bk, bad_id)) and "2 times" in amb["error"] and p.read_bytes() == before \
            and b.count() == 1                                    # only the file this test put there; no backup was written
        return ok, f"ambiguous: {amb['error'][:60]}; empty find: {empty['error'][:40]}; /etc: {outside['error'][:40]}; backups dir: {bk['error'][:30]}; bad id: {bad_id['error']}; file unchanged {p.read_bytes() == before}"


def repair_tampered(a):
    with _Backups() as b:
        p = _cfg(b)
        r = repair.edit_file(str(p), '"port": 80x80', '"port": 8080')
        (Path(repair.BACKUPS) / r["backup"] / "file.bak").write_text("evil", encoding="utf-8")
        t = repair.restore(r["backup"])
        return not t["ok"] and "does not match" in t["error"] and json.loads(p.read_text())["port"] == 8080, f"restore: {t}"


class _FakeProc:
    """Stands in for PowerShell and process start (Windows programs) around repair.restart_app."""

    def __init__(self, close, check):
        self.close, self.check, self.ps, self.started = close, check, [], []

    def __enter__(self):
        self.o = (runtime.powershell, runtime.popen)
        runtime.powershell = lambda script, env=None, timeout=30: (self.ps.append(script), subprocess.CompletedProcess(
            [], 0, json.dumps(self.close if script == repair.CLOSE else self.check), ""))[1]
        runtime.popen = lambda args, **kw: self.started.append(args)
        return self

    def __exit__(self, *exc):
        runtime.powershell, runtime.popen = self.o


def repair_restart(a):
    exe = _home_dir() / "NekoSampleApp.exe"
    exe.write_bytes(b"MZ")
    try:
        m = a["mode"]
        if m == "protected":
            with _FakeProc({}, {}) as f:
                rs = [repair.restart_app(n) for n in ("lsass", "KeePass", "jarvis_node", "a;b")]
            return not any(r["ok"] for r in rs) and not f.ps and not f.started, f"refused {sum(not r['ok'] for r in rs)}/4, scripts run {len(f.ps)}"
        if m == "will_not_close":
            with _FakeProc({"running": True, "path": str(exe), "closed": False, "left": 1}, {}) as f:
                r = repair.restart_app("NekoSampleApp")
            return not r["ok"] and "NOT forced" in r["error"] and not f.started, f"{r}, started {len(f.started)}"
        if m == "not_responding":
            with _FakeProc({"running": True, "path": str(exe), "closed": True, "left": 0}, {"running": True, "responding": False}) as f:
                r = repair.restart_app("NekoSampleApp")
            return not r["ok"] and r["running"] and not r["responding"] and "not running and answering" in r["error"] and f.started == [[str(exe)]], f"{r}"
        with _FakeProc({"running": True, "path": str(exe), "closed": True, "left": 0}, {"running": True, "responding": True}) as f:
            r = repair.restart_app("NekoSampleApp")
        return r["ok"] and f.started == [[str(exe)]] and repair.CHECK in f.ps, f"{r}"
    finally:
        shutil.rmtree(exe.parent, ignore_errors=True)


def repair_install(a):
    o_which, o_run = repair.shutil.which, runtime.run
    calls = []
    repair.shutil.which = lambda n: "/fake/winget" if n == "winget" else o_which(n)
    runtime.run = lambda args, timeout=30, **kw: (calls.append(args[1]), subprocess.CompletedProcess(
        args, 0, "No installed package found matching input criteria." if args[1] == "list" else "Installer cancelled", ""))[1]
    try:
        r = repair.install("Neko.SampleApp")
        bad = repair.install("x && calc")
    finally:
        repair.shutil.which, runtime.run = o_which, o_run
    ok = not r["ok"] and not r["listed_after"] and calls == ["install", "list"] and not bad["ok"]
    return ok, f"install ok {r['ok']} (listed after {r['listed_after']}), winget calls {calls}, injected id refused {not bad['ok']}"


def repair_policy(a):
    want = [("edit_file", "/home/owner/app/config.json", "YELLOW_APPROVAL"), ("restart_app", "NekoSampleApp", "YELLOW_APPROVAL"),
            ("install", "Neko.SampleApp", "YELLOW_APPROVAL"), ("restore", "20261016-101500-abcdef", "YELLOW_AUTO"),
            ("edit_file", r"D:\apps\sample\settings.json", "RED"), ("edit_file", r"D:\jarvis-secrets\providers\x.key", "DENY"),
            ("uninstall", "Neko.SampleApp", "RED")]
    got = []
    for op, tgt, w in want:
        d = decide(Action("repair", op, tgt, {"find": "a", "replace": "b"}))
        got.append(NAMES[d.level] if isinstance(d.level, int) else str(d.level))
    bad = [f"{op} {tgt}: {g} (want {w})" for (op, tgt, w), g in zip(want, got) if g != w]
    return not bad, "; ".join(bad) or "all 7 repair actions at the expected level: " + ", ".join(got)


# ---------------------------------------------------------------- the troubleshooter, end to end
class _SampleApp:
    """The fake program: its main window shows only when its config parses; otherwise an error dialog names the file."""

    def __init__(self, cfg):
        self.cfg, self.up = cfg, True

    def windows(self):
        if not self.up:
            return []
        try:
            json.loads(self.cfg.read_text(encoding="utf-8"))
            return [{"title": "Neko Sample App", "class": "NekoMain", "dialog": False, "texts": ["Ready"], "buttons": []}]
        except ValueError:
            return [{"title": "Neko Sample App - configuration error", "class": "#32770", "dialog": True, "buttons": ["OK"],
                     "texts": ["Neko Sample App could not start.", f"File: {self.cfg}"]}]


class _Console:
    def __init__(self, app, local, deny):
        self.app, self.local, self.deny = app, local, deny
        self.jobs, self.approvals, self.repairs, self.n = {}, 0, 0, 0

    def handle(self, method, path, body):
        name = path.split("/api/owner/", 1)[-1]
        if name == "state":
            return 200, {"approvals": []}                     # the owner has answered by the first look
        if name.startswith("job/") and method == "GET":
            return 200, {"id": name[4:], "state": "done", "result": self.jobs.get(name[4:], {"ok": False})}
        if name == "job/cancel":
            return 200, {"state": "cancelling"}
        if name != "pc":
            return 404, {}
        op = body.get("op")
        if op == "repair" and not body.get("approval_id"):
            self.approvals += 1
            return 202, {"status": "needs_approval", "approval_id": "a1", "level": "YELLOW_APPROVAL", "reason": "repairs need the owner"}
        if op == "repair":
            if self.deny:
                return 403, {"error": "refused by policy: approval denied"}
            self.repairs += 1
            res = repair.run({"op": body["action"], "target": body["target"], "args": body["args"]})     # the node's real repair
        elif op == "diag" and body.get("check") in ("window_text", "app_errors"):
            self.n += 1
            f = self.local / f"{body['check']}-{self.n}.json"
            f.write_text(json.dumps(self.app.windows() if body["check"] == "window_text" else []), encoding="utf-8")
            res = {"ok": True, "local_result": str(f)}
        elif op == "diag":
            res = {"ok": True, "rows": []}
        elif op == "ui" and body.get("action") == "click":
            self.app.up = False                               # OK on the error dialog: the program exits
            res = {"ok": True}
        elif op == "open_app":
            self.app.up = True
            res = {"ok": True, "opened": body.get("target"), "verified": "verified"}
        else:
            res = {"ok": False, "error": "not in this fixture"}
        jid = f"j{len(self.jobs) + 1}"
        self.jobs[jid] = res
        return 202, {"job": jid, "status": "queued"}


def _serve(console):
    class H(BaseHTTPRequestHandler):
        def _go(self, method):
            n = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(n) or b"{}") if n else {}
            code, out = console.handle(method, self.path, body)
            data = json.dumps(out).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            self._go("GET")

        def do_POST(self):
            self._go("POST")

        def log_message(self, *a):
            pass
    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def troubleshooter(a):
    from plugin.plugins.desktop import DesktopPlugin
    with _Backups() as b:
        cfg = _cfg(b, a["config"])
        before = cfg.read_bytes()
        local = _dir() / "neko-local"
        local.mkdir()
        console = _Console(_SampleApp(cfg), local, a.get("deny", False))
        srv = _serve(console)
        saved = core.CONSOLE, core.LOCAL_RESULTS, core.set_activity
        core.CONSOLE, core.LOCAL_RESULTS, core.set_activity = f"http://127.0.0.1:{srv.server_address[1]}", local, (lambda *x, **k: None)

        class Host:
            pass
        h = Host()
        h._run = lambda pc, body, timeout, what="": DesktopPlugin._run(h, pc, body, timeout, what)
        try:
            res = asyncio.run(DesktopPlugin.run_troubleshooter(h, app="NekoSampleApp", start_name="Neko Sample App"))
        finally:
            core.CONSOLE, core.LOCAL_RESULTS, core.set_activity = saved
            srv.shutdown()
            srv.server_close()
        rep = getattr(res, "value", None) or {}
        states = [s["state"] for s in rep.get("steps", [])]
        left = list(local.iterdir())
        w = a["want"]
        if w == "fixed":
            ok = rep.get("outcome") == "fixed and verified" and rep.get("verified") and rep.get("backup") and console.approvals == 1 \
                and console.repairs == 1 and json.loads(cfg.read_text())["port"] == 8080 and rep["verification"]["error_dialogs_now"] == 0 \
                and (Path(repair.BACKUPS) / rep["backup"] / "file.bak").read_bytes() == before
        elif w == "denied":
            ok = rep.get("outcome", "").startswith("not approved") and cfg.read_bytes() == before and console.repairs == 0 and b.count() == 0
        else:
            ok = rep.get("outcome", "").startswith("fix applied, but the check failed") and console.repairs == 1 \
                and rep["verification"]["error_dialogs_now"] == 1 and rep.get("undo")
        ok = ok and "waiting_for_permission" in states and not left            # screen text read once and deleted
        return ok, f"outcome '{rep.get('outcome')}', approvals asked {console.approvals}, repairs run {console.repairs}, " \
                   f"states {states}, local files left {len(left)}, verification {rep.get('verification')}"


def troubleshoot_not_retried(a):
    """A troubleshoot step whose fix did not pass its check (and reports a timeout) is neither retried nor called done."""
    calls = []
    res = {"ok": False, "outcome": "fix applied, but the check failed (undo with the backup)", "verified": True,
           "error": "timed out waiting for the program to answer"}
    d = _dir()
    co = C.Coordinator({"troubleshoot": lambda s: calls.append(1) or dict(res)}, [], tasks_dir=d / "t", lock_path=d / "l")
    rec = co.run("fix the sample app", D.Plan([{"tool": "troubleshoot", "app": "NekoSampleApp"}], "ok", model="bench"))
    ok = len(calls) == 1 and rec["status"] == "failed" and not C.summary(rec).startswith("Done")
    return ok, f"troubleshooter runs {len(calls)}, status {rec['status']}, summary '{C.summary(rec)[:100]}'"


# ---------------------------------------------------------------- the proactive monitor (detection)
def monitor_case(a):
    t = [1000.0]
    events, up = [], {8080: True}
    path = _dir() / "monitor.json"

    def mon(st):
        return MON.Monitor(st, crashes=lambda after: [e for e in events if e["record"] > after], newest=lambda: 100,
                           disks=lambda: {}, port=lambda p: up[p], tasks=lambda: [], services={"llama-swap": 8080}, clock=lambda: t[0])
    if a["kind"] == "crash_restart":
        st = MON.State()
        m = mon(st)
        m.check()                                              # first look: bookmark at the newest record
        events.append({"record": 101, "id": 1000, "app": "NekoSampleApp.exe"})
        first = m.check()
        st.save(path)
        m2 = mon(MON.State.load(path))                         # the plugin restarts
        again = m2.check()
        events.append({"record": 102, "id": 1002, "app": "NekoSampleApp.exe"})
        t[0] += 60
        new = m2.check()
        ok = len(first) == 1 and "crashed" in first[0].text and again == [] and len(new) == 1 and "stopped responding" in new[0].text
        return ok, f"first {[x.text for x in first]}, after restart {len(again)}, new hang {[x.text for x in new]}"
    st = MON.State()
    m = mon(st)
    m.check()
    up[8080] = False
    blip = m.check()
    down = m.check()
    quiet = m.check()
    ok = blip == [] and len(down) == 1 and "port 8080" in down[0].text and quiet == []
    return ok, f"one failed check {len(blip)}, two {len(down)}, three {len(quiet)}"
